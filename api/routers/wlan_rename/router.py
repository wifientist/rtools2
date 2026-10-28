"""
WLAN Rename Router

Bulk-rename R1 WiFi networks (the network name, optionally the SSID):

- POST /wlan-rename/networks -> every network in the tenant: id, name, ssid
- POST /wlan-rename/apply    -> set name (and ssid, if given) on the chosen networks

The find/replace happens in the page; apply just writes the names it is sent.
Apply is GET -> set name -> PUT, as WLAN VLAN does: /wifiNetworks/{id} has no
PATCH and a PUT replaces the whole network.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_alpha
from dependencies import get_current_user, get_db
from models.user import User
from routers.tenant_scope import resolve_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wlan-rename", tags=["WLAN Rename"])

WRITE_CONCURRENCY = 5


# ==================== Models ====================

class NetworksRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None


class RenameChange(BaseModel):
    network_id: str
    name: str = Field(..., min_length=1)
    ssid: Optional[str] = Field(None, min_length=1, max_length=32)


class ApplyRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    changes: List[RenameChange] = Field(..., min_length=1)


# ==================== Helpers ====================

def _check_r1(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be RuckusONE, "
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

@router.post("/networks")
@require_alpha()
async def networks(
    request: NetworksRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every WiFi network in the tenant. Read-only."""
    r1_controller = _check_r1(request.r1_controller_id, current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    try:
        resp = await r1_client.networks.get_wifi_networks(tenant_id)
    except Exception as e:
        logger.exception("[WLAN Rename] R1 network read failed")
        raise HTTPException(status_code=502, detail=f"Failed to read networks from R1: {e}")

    rows = [
        {
            "id": n["id"],
            "name": n.get("name") or "",
            "ssid": n.get("ssid") or "",
            "venues": len({g.get("venueId") for g in (n.get("venueApGroups") or []) if g.get("venueId")}),
        }
        for n in (resp.get("data") or []) if n.get("id")
    ]
    return {"networks": rows}


@router.post("/apply")
@require_alpha()
async def apply(
    request: ApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set name (and ssid, if sent) on each network. One result per change."""
    r1_controller = _check_r1(request.r1_controller_id, current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    if len({c.network_id for c in request.changes}) != len(request.changes):
        raise HTTPException(status_code=400, detail="A network appears more than once")
    if len({c.name.lower() for c in request.changes}) != len(request.changes):
        # R1 names are unique tenant-wide and compared without case.
        raise HTTPException(status_code=400, detail="Two networks would get the same name")

    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def rename_one(change: RenameChange) -> Dict[str, Any]:
        out: Dict[str, Any] = {"network_id": change.network_id, "name": change.name}
        async with sem:
            try:
                # The PUT body, not a re-check: a PUT replaces the whole network.
                network = await r1_client.networks.get_wifi_network_by_id(change.network_id, tenant_id)
                if not network or not network.get("id"):
                    return {**out, "status": "failed", "error": "Network not found"}
                out["before"] = network.get("name")
                wlan = network.setdefault("wlan", {})
                ssid_same = change.ssid is None or wlan.get("ssid") == change.ssid
                if network.get("name") == change.name and ssid_same:
                    return {**out, "status": "unchanged"}

                network["name"] = change.name
                if change.ssid is not None:
                    wlan["ssid"] = change.ssid
                await r1_client.networks.update_wifi_network(
                    change.network_id, network, tenant_id=tenant_id, wait_for_completion=True,
                )
                logger.info(f"[WLAN Rename] {change.network_id}: '{out['before']}' -> '{change.name}'"
                            + (f" (ssid '{change.ssid}')" if change.ssid is not None else ""))
                return {**out, "status": "updated"}
            except Exception as e:
                logger.error(f"[WLAN Rename] Failed to rename {change.network_id}: {e}")
                return {**out, "status": "failed", "error": _api_error(e)}

    results = await asyncio.gather(*[rename_one(c) for c in request.changes])
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("updated", "unchanged", "failed")}
    return {"results": results, "counts": counts}
