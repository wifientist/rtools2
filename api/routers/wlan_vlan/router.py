"""
WLAN VLAN Router

Copy each SZ WLAN's default VLAN (vlan.accessVlan) onto the matching R1
network's VLAN (wlan.vlanId), for one SZ zone and one R1 venue:

- POST /wlan-vlan/preview -> read both sides, one row per SZ WLAN (read-only)
- POST /wlan-vlan/apply   -> set wlan.vlanId on the chosen networks

Rows, statuses and what each note means: services/wlan_vlan.py.

Apply is GET -> set wlan.vlanId -> PUT, as Bulk WLAN Edit does. The GET is
not a re-check: /wifiNetworks/{id} has no PATCH, and a PUT replaces the whole
network, so the current object is the PUT body. The network is tenant-wide,
so the venue plays no part in the write.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from dependencies import get_current_user, get_db
from models.user import User
from routers.tenant_scope import resolve_tenant_id
from services.wlan_vlan import build_rows, r1_vlan_facts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wlan-vlan", tags=["WLAN VLAN"])

READ_CONCURRENCY = 10
WRITE_CONCURRENCY = 5


# ==================== Models ====================

class PreviewRequest(BaseModel):
    sz_controller_id: int
    zone_id: str
    r1_controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None


class VlanChange(BaseModel):
    network_id: str
    vlan_id: int = Field(..., ge=1, le=4094)


class ApplyRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    changes: List[VlanChange] = Field(..., min_length=1)


# ==================== Helpers ====================

def _check_controller(controller_id: int, kind: str, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != kind:
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be {kind}, "
                                                    f"got {controller.controller_type}")
    return controller


def _api_error(e: Exception) -> str:
    """The R1 error message, not the httpx wrapper around it."""
    response = getattr(e, "response", None)
    if response is not None:
        try:
            errors = response.json().get("errors") or []
            if errors:
                return "; ".join(err.get("message") or err.get("reason") or str(err) for err in errors)
        except Exception:
            pass
    return str(e)


# ==================== Endpoints ====================

@router.post("/preview")
async def preview(
    request: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the zone's WLANs and the venue's networks; one row per SZ WLAN. Read-only."""
    _check_controller(request.sz_controller_id, "SmartZone", current_user, db)
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)

    # ── SZ: the list carries no VLAN, so detail each WLAN ──
    sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    try:
        async with sz_client:
            zone, wlan_list, wlan_groups = await asyncio.gather(
                sz_client.zones.get_zone_details(request.zone_id),
                sz_client.wlans.get_all_wlans_paginated(request.zone_id),
                sz_client.wlans.get_all_wlan_groups_paginated(request.zone_id),
            )

            async def sz_detail(w):
                async with sem:
                    return await sz_client.wlans.get_wlan_details(request.zone_id, w["id"])

            sz_wlans = await asyncio.gather(*[sz_detail(w) for w in wlan_list if w.get("id")])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[WLAN VLAN] SZ read failed for zone {request.zone_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the zone from SmartZone: {e}")

    # ── R1: the query row's `vlan` is a summary; GET each network for wlan.vlanId and its pool ──
    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    try:
        venue = await r1_client.venues.get_venue(tenant_id, request.venue_id)
        resp = await r1_client.networks.get_wifi_networks(tenant_id, venue_id=request.venue_id)
        venue_networks = [n for n in (resp.get("data") or []) if n.get("id")]
    except Exception as e:
        logger.exception(f"[WLAN VLAN] R1 read failed for venue {request.venue_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the venue from R1: {e}")

    r1_full: Dict[str, Dict[str, Any]] = {}

    async def r1_detail(nid: str):
        async with sem:
            try:
                r1_full[nid] = await r1_client.networks.get_wifi_network_by_id(nid, tenant_id)
            except Exception as e:
                logger.warning(f"[WLAN VLAN] Could not read R1 network {nid}: {e}")

    await asyncio.gather(*[r1_detail(n["id"]) for n in venue_networks])

    result = build_rows(list(sz_wlans), wlan_groups, venue_networks, r1_full, request.venue_id)
    return {
        "zone_id": request.zone_id,
        "zone_name": (zone or {}).get("name") or "",
        "venue_id": request.venue_id,
        "venue_name": (venue or {}).get("name") or "",
        **result,
    }


@router.post("/apply")
async def apply(
    request: ApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set wlan.vlanId on each network. One result per change."""
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    if len({c.network_id for c in request.changes}) != len(request.changes):
        raise HTTPException(status_code=400, detail="A network appears more than once")

    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def set_one(change: VlanChange) -> Dict[str, Any]:
        out: Dict[str, Any] = {"network_id": change.network_id, "vlan_id": change.vlan_id}
        async with sem:
            try:
                # The PUT body, not a re-check: a PUT replaces the whole network.
                network = await r1_client.networks.get_wifi_network_by_id(change.network_id, tenant_id)
                if not network or not network.get("id"):
                    return {**out, "status": "failed", "error": "Network not found"}
                out["name"] = network.get("name")
                facts = r1_vlan_facts(network)
                out["before"] = facts["vlan"]
                if facts["vlan_pool"]:
                    # vlanId beside a pool is not the VLAN clients get; the preview blocks these too.
                    return {**out, "status": "failed",
                            "error": f"Network uses VLAN pool '{facts['vlan_pool']}'"}
                if facts["vlan"] == change.vlan_id:
                    return {**out, "status": "unchanged"}

                network.setdefault("wlan", {})["vlanId"] = change.vlan_id
                await r1_client.networks.update_wifi_network(
                    change.network_id, network, tenant_id=tenant_id, wait_for_completion=True,
                )
                logger.info(f"[WLAN VLAN] {out['name']} ({change.network_id}): "
                            f"VLAN {out['before']} -> {change.vlan_id}")
                return {**out, "status": "updated"}
            except Exception as e:
                logger.error(f"[WLAN VLAN] Failed to set VLAN on {change.network_id}: {e}")
                return {**out, "status": "failed", "error": _api_error(e)}

    results = await asyncio.gather(*[set_one(c) for c in request.changes])
    for r in results:
        if r["status"] == "failed":
            logger.warning(f"[WLAN VLAN] Not set: {r.get('name') or r['network_id']} -> "
                           f"VLAN {r['vlan_id']}: {r.get('error')}")
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("updated", "unchanged", "failed")}
    return {"results": results, "counts": counts}
