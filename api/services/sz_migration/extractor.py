"""
SZ Migration Extractor — M0 Extraction Orchestrator

Ordered extraction with reference chasing:
1. Fetch zone detail
2. Fetch all WLANs (paginated, full detail each)
3. Fetch WLAN Groups (members come inline)
4. Fetch all AP Groups (full detail with radioConfig parsing)
5. Fetch all APs (paginated)
6. Scan all WLANs for foreign key references → deduplicate → fetch each once
7. Assemble SZMigrationSnapshot

Progress callback fires at each sub-step for SSE integration.
"""

import asyncio
import logging
import time
from typing import Callable, Optional, Dict, Any, List

from schemas.sz_migration import (
    SZMigrationSnapshot,
    SZZoneSnapshot,
    SZWLANFull,
    SZWLANGroup,
    SZWLANGroupMember,
    SZAPGroupEnriched,
    SZRadioConfig,
    SZReferencedObject,
    SZExtractionWarning,
)
from szapi.services.wlans import WlanService
from services.sz_migration.version_map import detect_zone_api_version

logger = logging.getLogger(__name__)

# Type alias for the progress callback
ProgressCallback = Optional[Callable[[str, str, Dict[str, Any]], None]]


async def extract_zone_snapshot(
    sz_client,
    zone_id: str,
    on_progress: ProgressCallback = None,
) -> SZMigrationSnapshot:
    """
    Perform a complete deep extraction of a single SZ zone.

    Args:
        sz_client: Authenticated SZClient instance (already in context manager)
        zone_id: Zone UUID to extract
        on_progress: Optional callback(phase, message, data) for SSE progress

    Returns:
        Complete SZMigrationSnapshot
    """
    start_time = time.time()
    warnings: List[SZExtractionWarning] = []

    def progress(phase: str, message: str, data: Optional[Dict] = None):
        logger.info(f"[extraction:{zone_id[:8]}] {phase}: {message}")
        if on_progress:
            on_progress(phase, message, data or {})

    # ── Step 1: Zone detail ──────────────────────────────────────────
    progress("zone", "Fetching zone details...")
    zone_raw = await sz_client.zones.get_zone_full_details(zone_id)

    zone_radio_config = None
    if zone_raw.get("radioConfig"):
        zone_radio_config = SZRadioConfig.from_sz_response(zone_raw["radioConfig"])

    zone_snapshot = SZZoneSnapshot(
        id=zone_raw["id"],
        name=zone_raw.get("name", ""),
        description=zone_raw.get("description"),
        country_code=zone_raw.get("countryCode"),
        radio_config=zone_radio_config,
        raw=zone_raw,
    )
    # ── Detect zone firmware → set API version for zone-specific calls ──
    controller_api_version = sz_client.api_version
    zone_firmware = zone_raw.get("version")
    zone_api_version = detect_zone_api_version(zone_firmware, fallback=controller_api_version)

    if zone_api_version and zone_api_version != controller_api_version:
        sz_client.api_version = zone_api_version
        progress("zone", f"Zone '{zone_snapshot.name}' firmware {zone_firmware} → using API {zone_api_version}", {
            "country_code": zone_snapshot.country_code,
            "zone_firmware": zone_firmware,
            "api_version": zone_api_version,
        })
    else:
        progress("zone", f"Zone '{zone_snapshot.name}' loaded (API {controller_api_version})", {
            "country_code": zone_snapshot.country_code,
            "zone_firmware": zone_firmware,
            "api_version": controller_api_version,
        })

    # ── Step 2: All WLANs (list then detail each) ────────────────────
    progress("wlans", "Fetching WLAN list...")
    wlan_list = await sz_client.wlans.get_all_wlans_paginated(zone_id)
    progress("wlans", f"Found {len(wlan_list)} WLANs, fetching full details...")

    wlans: List[SZWLANFull] = []
    for i, wlan_summary in enumerate(wlan_list):
        wlan_id = wlan_summary["id"]
        try:
            wlan_detail = await sz_client.wlans.get_wlan_details(zone_id, wlan_id)
            auth_type = WlanService.extract_auth_type(wlan_detail)
            wlan_full = SZWLANFull.from_sz_response(wlan_detail, auth_type)
            wlans.append(wlan_full)
        except Exception as e:
            logger.warning(f"Failed to fetch WLAN detail for {wlan_id}: {e}")
            warnings.append(SZExtractionWarning(
                phase="wlans",
                message=f"Failed to fetch WLAN '{wlan_summary.get('name', wlan_id)}': {e}",
                details={"wlan_id": wlan_id},
            ))

        if (i + 1) % 10 == 0 or i == len(wlan_list) - 1:
            progress("wlans", f"WLAN details: {i + 1}/{len(wlan_list)}", {
                "completed": i + 1,
                "total": len(wlan_list),
            })

    progress("wlans", f"Extracted {len(wlans)} WLANs")

    # ── Step 3: WLAN Groups ──────────────────────────────────────────
    progress("wlan_groups", "Fetching WLAN Groups...")
    wlan_group_list = await sz_client.wlans.get_all_wlan_groups_paginated(zone_id)

    wlan_groups: List[SZWLANGroup] = []
    for wg_raw in wlan_group_list:
        members = []
        for m in wg_raw.get("members", []):
            members.append(SZWLANGroupMember(
                id=m.get("id", ""),
                name=m.get("name"),
                ssid=m.get("ssid"),
                access_vlan=m.get("accessVlan"),
                nas_id=m.get("nasId"),
            ))
        wlan_groups.append(SZWLANGroup(
            id=wg_raw["id"],
            name=wg_raw.get("name", ""),
            description=wg_raw.get("description"),
            members=members,
            raw=wg_raw,
        ))

    progress("wlan_groups", f"Extracted {len(wlan_groups)} WLAN Groups")

    # ── Step 4: AP Groups (detail each for radioConfig) ──────────────
    progress("ap_groups", "Fetching AP Groups...")
    ap_group_list = await sz_client.apgroups.get_all_ap_groups_paginated(zone_id)
    progress("ap_groups", f"Found {len(ap_group_list)} AP Groups, fetching details...")

    # Pre-fetch all APs for counting (we'll reuse this in step 5)
    progress("aps", "Fetching AP list for counts...")
    aps_result = await sz_client.aps.get_aps_by_zone(zone_id)
    all_aps_raw = aps_result.get("list", []) if isinstance(aps_result, dict) else aps_result

    # Count APs per group
    ap_counts: Dict[str, int] = {}
    for ap in all_aps_raw:
        gid = ap.get("apGroupId")
        if gid:
            ap_counts[gid] = ap_counts.get(gid, 0) + 1

    ap_groups: List[SZAPGroupEnriched] = []
    for i, apg_summary in enumerate(ap_group_list):
        apg_id = apg_summary["id"]
        try:
            apg_detail = await sz_client.apgroups.get_ap_group_details(zone_id, apg_id)
            apg_radio_config = None
            if apg_detail.get("radioConfig"):
                apg_radio_config = SZRadioConfig.from_sz_response(apg_detail["radioConfig"])

            ap_groups.append(SZAPGroupEnriched(
                id=apg_detail["id"],
                name=apg_detail.get("name", ""),
                description=apg_detail.get("description"),
                radio_config=apg_radio_config,
                ap_count=ap_counts.get(apg_id, 0),
                raw=apg_detail,
            ))
        except Exception as e:
            logger.warning(f"Failed to fetch AP Group detail for {apg_id}: {e}")
            warnings.append(SZExtractionWarning(
                phase="ap_groups",
                message=f"Failed to fetch AP Group '{apg_summary.get('name', apg_id)}': {e}",
                details={"ap_group_id": apg_id},
            ))

    progress("ap_groups", f"Extracted {len(ap_groups)} AP Groups")

    # ── Step 5: APs (already fetched above) ──────────────────────────
    # Paginate through all APs if needed (the initial fetch may be truncated)
    total_ap_count = aps_result.get("totalCount", len(all_aps_raw)) if isinstance(aps_result, dict) else len(all_aps_raw)

    if len(all_aps_raw) < total_ap_count:
        progress("aps", f"Fetching remaining APs ({len(all_aps_raw)}/{total_ap_count})...")
        page = 1
        while len(all_aps_raw) < total_ap_count:
            more_result = await sz_client.aps.get_aps_by_zone(zone_id, page=page)
            more_aps = more_result.get("list", []) if isinstance(more_result, dict) else more_result
            if not more_aps:
                break
            all_aps_raw.extend(more_aps)
            page += 1
            if page > 100:
                break

    progress("aps", f"Extracted {len(all_aps_raw)} APs")

    # ── Step 6: Chase references ─────────────────────────────────────
    progress("references", "Scanning WLANs for foreign key references...")

    # Collect all unique references across all WLANs
    unique_refs: Dict[str, tuple] = {}  # key: "type:id" → (ref_type, ref_id)
    for wlan in wlans:
        for ref_type, ref_id in wlan.get_all_reference_ids():
            key = f"{ref_type}:{ref_id}"
            if key not in unique_refs:
                unique_refs[key] = (ref_type, ref_id)

    progress("references", f"Found {len(unique_refs)} unique references to chase")

    referenced_objects: Dict[str, SZReferencedObject] = {}

    if unique_refs:
        for i, (key, (ref_type, ref_id)) in enumerate(unique_refs.items()):
            try:
                if ref_type == "auth_service":
                    raw = await sz_client.aaa.get_auth_service(ref_id)
                elif ref_type == "accounting_service":
                    raw = await sz_client.aaa.get_accounting_service(ref_id)
                else:
                    raw = await sz_client.policies.get_referenced_object(zone_id, ref_type, ref_id)

                referenced_objects[key] = SZReferencedObject(
                    ref_type=ref_type,
                    id=ref_id,
                    name=raw.get("name"),
                    raw=raw,
                )
            except Exception as e:
                logger.warning(f"Failed to chase reference {key}: {e}")
                warnings.append(SZExtractionWarning(
                    phase="references",
                    message=f"Failed to fetch {ref_type} '{ref_id}': {e}",
                    details={"ref_type": ref_type, "ref_id": ref_id},
                ))
                # Store a stub so downstream knows this ref was attempted
                referenced_objects[key] = SZReferencedObject(
                    ref_type=ref_type,
                    id=ref_id,
                    name=None,
                    raw={"_error": str(e)},
                )

            if (i + 1) % 5 == 0 or i == len(unique_refs) - 1:
                progress("references", f"References: {i + 1}/{len(unique_refs)}", {
                    "completed": i + 1,
                    "total": len(unique_refs),
                })

    progress("references", f"Resolved {len(referenced_objects)} referenced objects")

    # ── Step 7: Assemble snapshot ────────────────────────────────────
    elapsed = round(time.time() - start_time, 2)

    extraction_metadata = {
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": elapsed,
        "zone_id": zone_id,
        "zone_firmware_version": zone_firmware,
        "api_version_used": sz_client.api_version,
        "controller_api_version": controller_api_version,
        "counts": {
            "wlans": len(wlans),
            "wlan_groups": len(wlan_groups),
            "ap_groups": len(ap_groups),
            "aps": len(all_aps_raw),
            "referenced_objects": len(referenced_objects),
            "warnings": len(warnings),
        },
        "api_stats": sz_client.get_api_stats(),
    }

    snapshot = SZMigrationSnapshot(
        zone=zone_snapshot,
        wlans=wlans,
        wlan_groups=wlan_groups,
        ap_groups=ap_groups,
        aps=all_aps_raw,
        referenced_objects=referenced_objects,
        extraction_metadata=extraction_metadata,
        warnings=warnings,
    )

    progress("complete", f"Extraction complete in {elapsed}s", snapshot.summary())

    return snapshot
