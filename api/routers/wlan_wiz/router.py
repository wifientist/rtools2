"""
WLAN Wiz Router

A read-only reference: every WLAN in the tenant, and for one venue its AP
Groups, APs and which WLAN is on which group, in one place. The page holds it
all in memory and slices it; nothing here writes to R1.

- POST /wlan-wiz/networks -> every network in the tenant (with each venue it is
                             activated in) and the tenant's venue names
- POST /wlan-wiz/venue    -> one venue: AP Groups, APs (with the SSIDs each
                             radio is beaconing) and each activation there,
                             with its scheduler state

Two calls rather than one: the tenant list can be thousands of networks and
does not change with the venue, and each call has to fit under the proxy's 60s.
Shaping: services/wlan_wiz.py.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_alpha
from dependencies import get_current_user, get_db
from models.user import User
from routers.apg_cleanup.router import _list_groups
from routers.tenant_scope import resolve_tenant_id
from services import ap_group_ops
from services.wlan_wiz import shape_networks, shape_venue, shape_venues

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wlan-wiz", tags=["WLAN Wiz"])

READ_CONCURRENCY = 10


class TenantRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None


class VenueRequest(TenantRequest):
    venue_id: str


def _check_controller(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be RuckusONE, "
                                                    f"got {controller.controller_type}")
    return controller


@router.post("/networks")
@require_alpha()
async def networks(
    request: TenantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every WiFi network in the tenant, activated or not, plus venue names."""
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)

    async def venue_names():
        try:
            return shape_venues(await r1_client.venues.get_venues(tenant_id))
        except Exception as e:
            # Names only; the page falls back to ids.
            logger.warning(f"[WLAN Wiz] Could not read venue names: {e}")
            return []

    try:
        nets, venues = await asyncio.gather(r1_client.networks.get_wifi_networks(tenant_id), venue_names())
    except Exception as e:
        logger.exception("[WLAN Wiz] R1 network read failed")
        raise HTTPException(status_code=502,
                            detail=f"Failed to read networks from R1: {ap_group_ops.api_error(e)}")
    return {"networks": shape_networks(nets.get("data") or []), "venues": venues}


@router.post("/venue")
@require_alpha()
async def venue(
    request: VenueRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One venue's AP Groups, APs and activations. Read-only."""
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    venue_id = request.venue_id

    try:
        venue_obj, groups, aps_resp, nets_resp = await asyncio.gather(
            r1_client.venues.get_venue(tenant_id, venue_id),
            _list_groups(r1_client, tenant_id, venue_id),
            r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id),
            r1_client.networks.get_wifi_networks(tenant_id, venue_id=venue_id),
        )
    except Exception as e:
        logger.exception(f"[WLAN Wiz] R1 read failed for venue {venue_id}")
        raise HTTPException(status_code=502,
                            detail=f"Failed to read the venue from R1: {ap_group_ops.api_error(e)}")
    nets = [n for n in (nets_resp.get("data") or []) if n.get("id")]

    # Scheduler state (on / off / scheduled) lives only in the per-venue settings.
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    settings: Dict[str, Optional[Dict[str, Any]]] = {}

    async def read_settings(nid: str):
        async with sem:
            try:
                resp = await asyncio.to_thread(
                    ap_group_ops.r1_call, r1_client, "get",
                    f"/venues/{venue_id}/wifiNetworks/{nid}/settings", tenant_id)
                resp.raise_for_status()
                settings[nid] = resp.json() if resp.content else {}
            except Exception as e:
                logger.warning(f"[WLAN Wiz] Could not read venue settings of network {nid}: {e}")
                settings[nid] = None

    await asyncio.gather(*[read_settings(n["id"]) for n in nets])

    shaped = shape_venue(venue_id, groups, aps_resp.get("data") or [], nets, settings)
    return {"venue_id": venue_id, "venue_name": (venue_obj or {}).get("name") or "", **shaped}
