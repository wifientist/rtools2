"""
Maps Router — floor plan overlays for live client / RSSI data.

Three endpoints:
  GET  /maps/{controller_id}/venues/{venue_id}/floorplans
       List the venue's floor plans, with how many APs are placed on each.
  GET  /maps/{controller_id}/venues/{venue_id}/floorplans/{floorplan_id}/image
       Proxy the plan image (keeps R1's signed URL server-side).
  GET  /maps/{controller_id}/venues/{venue_id}/floorplans/{floorplan_id}/live
       The overlay payload: placed APs, their live clients, RSSI distributions,
       and an estimated coverage-cell radius per AP.

Everything positional about *clients* is estimated — see rf.estimate_distance_m.
APs are placed exactly, from the coordinates the venue admin set in R1.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.controller import Controller
from models.user import User

from .rf import (
    DEFAULT_CLIENT_TX_POWER_DBM,
    DEFAULT_PATH_LOSS_EXPONENT,
    RSSI_TIERS,
    band_to_freq_mhz,
    channel_to_freq_mhz,
    estimate_distance_m,
    normalize_signal,
    percentile,
    rssi_tier,
    stable_bearing_deg,
    summarize_rssi,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["Maps"])

# Floor plan images are static per plan; a short browser cache keeps the map
# from re-fetching several MB on every live poll.
IMAGE_CACHE_SECONDS = 3600
IMAGE_FETCH_TIMEOUT = 30


# ============================================================================
# Helpers
# ============================================================================


def _resolve_controller(
    controller_id: int,
    tenant_id: Optional[str],
    db: Session,
    current_user: User,
) -> tuple:
    """Load the caller's R1 controller and work out the effective tenant."""
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == current_user.id,
    ).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    effective_tenant_id = tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(
            status_code=400, detail="tenant_id required for MSP controllers"
        )

    return controller, effective_tenant_id


def _scale_metres_per_unit(scales: Optional[List[dict]]) -> Optional[Dict[str, Any]]:
    """
    Normalize R1's calibration segment into something the map can use.

    A scale is two points plus the real distance between them. The points share
    the coordinate space of AP positions (percent of image width/height), so the
    front end still has to fold in the rendered aspect ratio to get pixels — we
    just hand back the segment and its real length, plus a metres value derived
    from feet when only feet was recorded.
    """
    if not scales:
        return None

    scale = scales[0]
    metres = scale.get("distanceInMeters")
    feet = scale.get("distanceInFeet")
    if not metres and feet:
        metres = feet * 0.3048
    if not metres:
        return None

    x1, y1 = scale.get("x1"), scale.get("y1")
    x2, y2 = scale.get("x2"), scale.get("y2")
    if None in (x1, y1, x2, y2):
        return None

    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "distance_m": round(metres, 3),
        "distance_ft": round(metres / 0.3048, 2),
    }


def _ap_position(ap: dict) -> Optional[dict]:
    """floorPosition, but only when it actually carries coordinates."""
    position = ap.get("floorPosition") or {}
    if position.get("xPercent") is None or position.get("yPercent") is None:
        return None
    return position


def _ap_radio_summary(ap: dict) -> List[dict]:
    """Per-radio band/channel/tx-power, used for the AP detail panel."""
    radios = []
    for radio in ap.get("radioStatuses") or []:
        radios.append({
            "band": radio.get("band"),
            "channel": radio.get("channel"),
            "channel_bandwidth": radio.get("channelBandwidth"),
            "tx_power_dbm": radio.get("actualTxPower") or radio.get("transmitterPower"),
        })
    return radios


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/{controller_id}/venues/{venue_id}/floorplans")
async def list_floorplans(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Floor plans on a venue, annotated with how many APs sit on each.

    The AP counts matter for picking a plan: an uncalibrated plan with zero
    placed APs can be rendered but nothing can be overlaid on it, and the UI
    says so up front rather than showing an empty map.
    """
    controller, effective_tenant_id = _resolve_controller(
        controller_id, tenant_id, db, current_user
    )
    r1 = create_r1_client_from_controller(controller_id, db)

    plans, aps = await asyncio.gather(
        asyncio.to_thread(
            r1.floorplans.list_floorplans, effective_tenant_id, venue_id
        ),
        asyncio.to_thread(
            r1.floorplans.query_aps_with_positions, effective_tenant_id, venue_id
        ),
    )

    placed_by_plan: Dict[str, int] = {}
    unplaced = 0
    for ap in aps:
        position = _ap_position(ap)
        plan_id = (position or {}).get("floorplanId")
        if plan_id:
            placed_by_plan[plan_id] = placed_by_plan.get(plan_id, 0) + 1
        else:
            unplaced += 1

    results = []
    for plan in plans:
        plan_id = plan.get("id")
        scale = _scale_metres_per_unit(plan.get("scales"))
        results.append({
            "id": plan_id,
            "name": plan.get("name"),
            "floor_number": plan.get("floorNumber"),
            "image_id": plan.get("imageId"),
            "image_name": plan.get("imageName"),
            "ap_count": placed_by_plan.get(plan_id, 0),
            "scale": scale,
            "calibrated": scale is not None,
        })

    results.sort(key=lambda p: (p.get("floor_number") is None, p.get("floor_number"), p.get("name") or ""))

    return {
        "venue_id": venue_id,
        "floorplans": results,
        "total_aps": len(aps),
        "unplaced_ap_count": unplaced,
    }


@router.get("/{controller_id}/venues/{venue_id}/floorplans/{floorplan_id}/image")
async def get_floorplan_image(
    controller_id: int,
    venue_id: str,
    floorplan_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stream the floor plan image through the API.

    R1 hands out a short-lived signed URL on its object store. We fetch it here
    instead of redirecting the browser so the signed URL never leaves the
    server and the image is served same-origin (no CORS on the canvas).
    """
    controller, effective_tenant_id = _resolve_controller(
        controller_id, tenant_id, db, current_user
    )
    r1 = create_r1_client_from_controller(controller_id, db)

    plans = await asyncio.to_thread(
        r1.floorplans.list_floorplans, effective_tenant_id, venue_id
    )
    plan = next((p for p in plans if p.get("id") == floorplan_id), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Floor plan not found")

    image_id = plan.get("imageId")
    if not image_id:
        raise HTTPException(status_code=404, detail="Floor plan has no image")

    signed_url = await asyncio.to_thread(
        r1.floorplans.get_image_url, effective_tenant_id, venue_id, image_id
    )
    if not signed_url:
        raise HTTPException(
            status_code=502, detail="Could not obtain image URL from RUCKUS One"
        )

    try:
        upstream = await asyncio.to_thread(
            requests.get, signed_url, timeout=IMAGE_FETCH_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.warning(f"[maps] floor plan image fetch failed: {exc}")
        raise HTTPException(status_code=502, detail="Floor plan image fetch failed")

    if not upstream.ok:
        raise HTTPException(
            status_code=502,
            detail=f"Floor plan image fetch returned HTTP {upstream.status_code}",
        )

    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("Content-Type", "image/png"),
        headers={"Cache-Control": f"private, max-age={IMAGE_CACHE_SECONDS}"},
    )


@router.get("/{controller_id}/venues/{venue_id}/floorplans/{floorplan_id}/live")
async def get_live_overlay(
    controller_id: int,
    venue_id: str,
    floorplan_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    path_loss_exponent: float = Query(
        DEFAULT_PATH_LOSS_EXPONENT, ge=1.5, le=6.0,
        description="Log-distance model exponent: 2=free space, 3=typical indoor, 4+=obstructed",
    ),
    client_tx_power: float = Query(
        DEFAULT_CLIENT_TX_POWER_DBM, ge=0.0, le=30.0,
        description="Assumed client transmit power in dBm",
    ),
    cell_percentile: float = Query(
        90.0, ge=50.0, le=100.0,
        description="Percentile of client distances used as the AP's cell radius",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Live overlay for one floor plan.

    Returns the APs placed on the plan, every client currently associated to
    them, and per-AP RSSI statistics. Client distance is modelled from RSSI;
    the bearing is a stable fiction so dots don't stack (see rf.py). Treat the
    cell radius as "where this AP's clients actually are right now", not as a
    predicted propagation contour.
    """
    controller, effective_tenant_id = _resolve_controller(
        controller_id, tenant_id, db, current_user
    )
    r1 = create_r1_client_from_controller(controller_id, db)

    plans, aps, clients = await asyncio.gather(
        asyncio.to_thread(
            r1.floorplans.list_floorplans, effective_tenant_id, venue_id
        ),
        asyncio.to_thread(
            r1.floorplans.query_aps_with_positions, effective_tenant_id, venue_id
        ),
        asyncio.to_thread(
            r1.floorplans.query_live_clients, effective_tenant_id, venue_id
        ),
    )

    plan = next((p for p in plans if p.get("id") == floorplan_id), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Floor plan not found")

    warnings: List[str] = []

    # ── APs placed on this plan ──────────────────────────────
    placed_aps: Dict[str, dict] = {}
    for ap in aps:
        position = _ap_position(ap)
        if not position or position.get("floorplanId") != floorplan_id:
            continue
        serial = ap.get("serialNumber")
        if not serial:
            continue
        placed_aps[serial] = {
            "serial_number": serial,
            "name": ap.get("name") or serial,
            "model": ap.get("model"),
            "status": ap.get("status"),
            "ap_group_id": ap.get("apGroupId"),
            "x_percent": position.get("xPercent"),
            "y_percent": position.get("yPercent"),
            "reported_client_count": ap.get("clientCount"),
            "radios": _ap_radio_summary(ap),
            "clients": [],
        }

    if not placed_aps:
        warnings.append(
            "No APs are positioned on this floor plan in RUCKUS One — "
            "place them on the plan in R1 to see live client data here."
        )

    # ── Attach clients to their serving AP ───────────────────
    swapped_signal_count = 0
    clients_off_plan = 0
    clients_without_rssi = 0

    for client in clients:
        ap_info = client.get("apInformation") or {}
        serial = ap_info.get("serialNumber")
        ap_entry = placed_aps.get(serial) if serial else None
        if not ap_entry:
            clients_off_plan += 1
            continue

        rssi, snr, swapped = normalize_signal(client.get("signalStatus"))
        if swapped:
            swapped_signal_count += 1
        if rssi is None:
            clients_without_rssi += 1

        radio_status = client.get("radioStatus") or {}
        band = client.get("band")
        channel = radio_status.get("channel")
        freq_mhz = channel_to_freq_mhz(channel, band) or band_to_freq_mhz(band)

        distance_m = estimate_distance_m(
            rssi,
            freq_mhz,
            path_loss_exponent=path_loss_exponent,
            client_tx_power_dbm=client_tx_power,
        )

        mac = client.get("macAddress") or ""
        network = client.get("networkInformation") or {}
        traffic = client.get("trafficStatus") or {}

        ap_entry["clients"].append({
            "mac_address": mac,
            "hostname": client.get("hostname"),
            "ip_address": client.get("ipAddress"),
            "os_type": client.get("osType"),
            "device_type": client.get("deviceType"),
            "ssid": network.get("ssid"),
            "vlan": network.get("vlan"),
            "band": band,
            "channel": channel,
            "rssi": rssi,
            "snr": snr,
            "noise_floor": (client.get("signalStatus") or {}).get("noiseFloor"),
            "health": (client.get("signalStatus") or {}).get("health"),
            "tier": rssi_tier(rssi),
            "signal_fields_swapped": swapped,
            "connected_time": client.get("connectedTime"),
            "total_traffic": traffic.get("totalTraffic"),
            # Estimated ring placement — radius is modelled, bearing is stable
            # but arbitrary. See rf.estimate_distance_m.
            "estimated_distance_m": round(distance_m, 2) if distance_m is not None else None,
            "bearing_deg": stable_bearing_deg(mac or serial),
        })

    # ── Per-AP statistics and cell radius ────────────────────
    for ap_entry in placed_aps.values():
        rssi_values = [c["rssi"] for c in ap_entry["clients"] if c["rssi"] is not None]
        distances = [
            c["estimated_distance_m"]
            for c in ap_entry["clients"]
            if c["estimated_distance_m"] is not None
        ]

        bands: Dict[str, int] = {}
        ssids: Dict[str, int] = {}
        for client in ap_entry["clients"]:
            if client["band"]:
                bands[client["band"]] = bands.get(client["band"], 0) + 1
            if client["ssid"]:
                ssids[client["ssid"]] = ssids.get(client["ssid"], 0) + 1

        ap_entry["client_count"] = len(ap_entry["clients"])
        ap_entry["rssi_stats"] = summarize_rssi(rssi_values)
        ap_entry["bands"] = bands
        ap_entry["ssids"] = ssids
        ap_entry["cell_radius_m"] = (
            round(percentile(distances, cell_percentile), 2) if distances else None
        )
        ap_entry["median_distance_m"] = (
            round(percentile(distances, 50), 2) if distances else None
        )

    # ── Venue-level rollup ───────────────────────────────────
    all_rssi = [
        c["rssi"]
        for ap_entry in placed_aps.values()
        for c in ap_entry["clients"]
        if c["rssi"] is not None
    ]
    mapped_client_count = sum(a["client_count"] for a in placed_aps.values())

    if swapped_signal_count:
        warnings.append(
            f"{swapped_signal_count} client(s) reported RSSI and SNR in swapped "
            f"fields (known AP firmware bug) — values were corrected using the "
            f"noise floor."
        )
    if clients_without_rssi:
        warnings.append(
            f"{clients_without_rssi} client(s) on this plan reported no RSSI and "
            f"are drawn at their AP."
        )
    if clients_off_plan:
        warnings.append(
            f"{clients_off_plan} client(s) in this venue are served by APs that "
            f"are not on this floor plan."
        )
    if len(clients) == 10000:
        warnings.append(
            "Client query hit the 10,000-row ceiling — this venue may have more "
            "live clients than are shown."
        )

    scale = _scale_metres_per_unit(plan.get("scales"))
    if not scale:
        warnings.append(
            "This floor plan has no scale calibration in RUCKUS One, so nothing "
            "can be drawn to scale. Coverage cells are hidden and client dots "
            "are spread by relative distance only — set a scale on the plan in "
            "R1 to get real distances."
        )

    return {
        "venue_id": venue_id,
        "floorplan": {
            "id": plan.get("id"),
            "name": plan.get("name"),
            "floor_number": plan.get("floorNumber"),
            "image_id": plan.get("imageId"),
            "scale": scale,
            "calibrated": scale is not None,
        },
        "aps": sorted(placed_aps.values(), key=lambda a: a["name"]),
        "summary": {
            "ap_count": len(placed_aps),
            "client_count": mapped_client_count,
            "venue_client_count": len(clients),
            "rssi": summarize_rssi(all_rssi),
        },
        "model": {
            "path_loss_exponent": path_loss_exponent,
            "client_tx_power_dbm": client_tx_power,
            "cell_percentile": cell_percentile,
            "tiers": [
                {"name": name, "floor_dbm": floor} for name, floor in RSSI_TIERS
            ],
        },
        "warnings": warnings,
    }
