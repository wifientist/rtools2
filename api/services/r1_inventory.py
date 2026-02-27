"""
R1 Venue Inventory Service — standalone, reusable

Captures the complete state of a target R1 venue:
- WiFi networks (filtered to this venue)
- AP groups (filtered to this venue)
- APs
- DPSK pools (tenant-level)
- Identity groups (tenant-level)
- RADIUS attribute groups (tenant-level)

This is intentionally NOT under sz_migration/. Per-unit SSID workflows,
cleanup tools, and eventually V3 all benefit from a single way to
snapshot R1 venue state.
"""

import logging
import time
from typing import Optional, Callable, Dict, Any, List

from schemas.r1_inventory import R1VenueInventory

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str, str, Dict[str, Any]], None]]


async def capture_venue_inventory(
    r1_client,
    tenant_id: str,
    venue_id: str,
    on_progress: ProgressCallback = None,
) -> R1VenueInventory:
    """
    Capture the complete state of an R1 venue.

    Args:
        r1_client: Authenticated R1Client instance
        tenant_id: Tenant/EC ID
        venue_id: Venue UUID
        on_progress: Optional callback(phase, message, data) for progress

    Returns:
        R1VenueInventory with all entities captured
    """
    start_time = time.time()

    def progress(phase: str, message: str, data: Optional[Dict] = None):
        logger.info(f"[r1_inventory:{venue_id[:8]}] {phase}: {message}")
        if on_progress:
            on_progress(phase, message, data or {})

    # ── Venue details ────────────────────────────────────────────────
    progress("venue", "Fetching venue details...")
    try:
        venue_detail = await r1_client.venues.get_venue(tenant_id, venue_id)
    except Exception as e:
        logger.warning(f"Could not fetch venue details: {e}")
        venue_detail = {"id": venue_id}

    venue_name = venue_detail.get("name", "Unknown")
    progress("venue", f"Venue '{venue_name}' loaded")

    # ── WiFi networks ────────────────────────────────────────────────
    progress("networks", "Fetching WiFi networks...")
    try:
        networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = networks_response.get("data", [])

        # Filter to networks active in this venue via venueApGroups
        venue_networks = []
        for network in all_networks:
            venue_ap_groups = network.get("venueApGroups", [])
            if not venue_ap_groups:
                continue
            # Check if any venueApGroups entry references this venue
            for vag in venue_ap_groups:
                if vag.get("venueId") == venue_id:
                    venue_networks.append(network)
                    break

        progress("networks", f"Found {len(venue_networks)} networks in venue (of {len(all_networks)} total)")
    except Exception as e:
        logger.warning(f"Failed to fetch WiFi networks: {e}")
        venue_networks = []
        progress("networks", f"Failed to fetch networks: {e}")

    # ── AP groups ────────────────────────────────────────────────────
    progress("ap_groups", "Fetching AP groups...")
    try:
        ap_groups_response = await r1_client.venues.query_ap_groups(
            tenant_id,
            venue_id=venue_id,
        )
        ap_groups = ap_groups_response.get("data", []) if isinstance(ap_groups_response, dict) else []
        progress("ap_groups", f"Found {len(ap_groups)} AP groups")
    except Exception as e:
        logger.warning(f"Failed to fetch AP groups: {e}")
        ap_groups = []
        progress("ap_groups", f"Failed to fetch AP groups: {e}")

    # ── APs ──────────────────────────────────────────────────────────
    progress("aps", "Fetching APs...")
    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        aps = aps_response.get("data", []) if isinstance(aps_response, dict) else []
        progress("aps", f"Found {len(aps)} APs")
    except Exception as e:
        logger.warning(f"Failed to fetch APs: {e}")
        aps = []
        progress("aps", f"Failed to fetch APs: {e}")

    # ── DPSK pools (tenant-level) ────────────────────────────────────
    progress("dpsk_pools", "Fetching DPSK pools...")
    try:
        dpsk_response = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id)
        dpsk_pools = dpsk_response.get("data", []) if isinstance(dpsk_response, dict) else []
        progress("dpsk_pools", f"Found {len(dpsk_pools)} DPSK pools")
    except Exception as e:
        logger.warning(f"Failed to fetch DPSK pools: {e}")
        dpsk_pools = []
        progress("dpsk_pools", f"Failed to fetch DPSK pools: {e}")

    # ── Identity groups (tenant-level) ───────────────────────────────
    progress("identity_groups", "Fetching identity groups...")
    try:
        ig_response = await r1_client.identity.query_identity_groups(tenant_id=tenant_id)
        identity_groups = ig_response.get("data", []) if isinstance(ig_response, dict) else []
        progress("identity_groups", f"Found {len(identity_groups)} identity groups")
    except Exception as e:
        logger.warning(f"Failed to fetch identity groups: {e}")
        identity_groups = []
        progress("identity_groups", f"Failed to fetch identity groups: {e}")

    # ── RADIUS attribute groups (tenant-level) ───────────────────────
    progress("radius", "Fetching RADIUS attribute groups...")
    try:
        radius_response = await r1_client.radius_attributes.query_radius_attribute_groups(
            tenant_id=tenant_id,
        )
        radius_groups = radius_response.get("data", []) if isinstance(radius_response, dict) else []
        progress("radius", f"Found {len(radius_groups)} RADIUS attribute groups")
    except Exception as e:
        logger.warning(f"Failed to fetch RADIUS attribute groups: {e}")
        radius_groups = []
        progress("radius", f"Failed to fetch RADIUS attribute groups: {e}")

    # ── Assemble inventory ───────────────────────────────────────────
    elapsed = round(time.time() - start_time, 2)

    inventory = R1VenueInventory(
        venue_id=venue_id,
        venue_name=venue_name,
        tenant_id=tenant_id,
        venue=venue_detail,
        wifi_networks=venue_networks,
        ap_groups=ap_groups,
        aps=aps,
        dpsk_pools=dpsk_pools,
        identity_groups=identity_groups,
        radius_attribute_groups=radius_groups,
        snapshot_metadata={
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": elapsed,
            "counts": {
                "wifi_networks": len(venue_networks),
                "ap_groups": len(ap_groups),
                "aps": len(aps),
                "dpsk_pools": len(dpsk_pools),
                "identity_groups": len(identity_groups),
                "radius_attribute_groups": len(radius_groups),
            },
        },
    )

    progress("complete", f"R1 inventory captured in {elapsed}s", inventory.summary())

    return inventory
