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
    all_aps_raw = await sz_client.aps.get_all_aps_by_zone(zone_id)

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
    progress("aps", f"Extracted {len(all_aps_raw)} APs")

    # ── Step 5b: Per-AP radio config (WLAN Group overrides) ──────────
    ap_radio_config = await _fetch_ap_radio_config(sz_client, all_aps_raw, progress, warnings)

    # ── Step 5c: Zone per-AP-model settings (PoE, LAN ports, LED...) ──
    zone_ap_models = await _fetch_zone_ap_models(sz_client, zone_id, all_aps_raw, progress, warnings)

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
    # A portal profile lives under /portals/hotspot (WISPr) or /portals/guest
    # depending on the WLAN that uses it; the generic lookup only tries guest,
    # so a Hotspot WLAN's portal came back as an error and migrated as plain Open.
    hotspot_portals = {
        ((w.raw or {}).get("portalServiceProfile") or {}).get("id")
        for w in wlans if (w.raw or {}).get("type") in HOTSPOT_WLAN_TYPES
    }

    if unique_refs:
        for i, (key, (ref_type, ref_id)) in enumerate(unique_refs.items()):
            try:
                if ref_type == "auth_service":
                    raw = await sz_client.aaa.get_auth_service(ref_id)
                elif ref_type == "accounting_service":
                    raw = await sz_client.aaa.get_accounting_service(ref_id)
                elif ref_type == "portal_service_profile":
                    raw = await _get_portal(sz_client, zone_id, ref_id, ref_id in hotspot_portals)
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
        ap_radio_config=ap_radio_config,
        zone_ap_models=zone_ap_models,
        referenced_objects=referenced_objects,
        extraction_metadata=extraction_metadata,
        warnings=warnings,
    )

    progress("complete", f"Extraction complete in {elapsed}s", snapshot.summary())

    return snapshot


_AP_BANDS = ("radio24g", "radio5g", "radio5gLower", "radio5gUpper", "radio6g")
AP_DETAIL_CONCURRENCY = 8


HOTSPOT_WLAN_TYPES = ("Hotspot", "Hotspot_MacByPass")


async def _get_portal(sz_client, zone_id: str, portal_id: str, hotspot: bool) -> Dict[str, Any]:
    """The portal profile from the endpoint its WLAN type implies, else the other one."""
    kinds = ("hotspot", "guest") if hotspot else ("guest", "hotspot")
    last: Optional[Exception] = None
    for kind in kinds:
        try:
            raw = await sz_client._request(
                "GET", f"/{sz_client.api_version}/rkszones/{zone_id}/portals/{kind}/{portal_id}")
            if raw:
                return {**raw, "_portal_kind": kind}
        except Exception as e:
            last = e
    raise last or RuntimeError(f"portal {portal_id} not found")


async def _fetch_ap_radio_config(sz_client, aps_raw, progress, warnings) -> Dict[str, Dict[str, Any]]:
    """
    Per-AP WLAN Group overrides, one GET /aps/{mac} per AP.

    Needed for every AP, not just flagged ones: on a live vSZ, query/ap
    returned null wlanGroup* and configOverride=False for an AP whose 5 GHz
    radio was overridden. Only the radio WLAN-group/service fields are kept.
    """
    macs = [ap.get("apMac") or ap.get("mac") for ap in aps_raw]
    macs = [m for m in macs if m]
    if not macs:
        return {}
    progress("ap_radio", f"Reading per-AP radio config for {len(macs)} APs...")
    sem = asyncio.Semaphore(AP_DETAIL_CONCURRENCY)
    out: Dict[str, Dict[str, Any]] = {}
    done = 0

    async def one(mac: str):
        nonlocal done
        async with sem:
            try:
                d = await sz_client.aps.get_ap_details(mac)
                rc = (d or {}).get("radioConfig") or {}
                out[mac] = {
                    "dual5g": rc.get("dual5gEnabled"),
                    "radios": {
                        b: {"wlan_group_id": (rc.get(b) or {}).get("wlanGroupId"),
                            "service_enabled": (rc.get(b) or {}).get("wlanServiceEnabled")}
                        for b in _AP_BANDS if rc.get(b) is not None
                    },
                }
            except Exception as e:
                out[mac] = {"error": f"{type(e).__name__}: {e}"[:200], "radios": {}}
        done += 1
        if done % 50 == 0 or done == len(macs):
            progress("ap_radio", f"Per-AP radio config: {done}/{len(macs)}")

    await asyncio.gather(*(one(m) for m in macs))
    failed = [m for m, v in out.items() if v.get("error")]
    if failed:
        warnings.append(SZExtractionWarning(
            phase="ap_radio",
            message=f"Could not read radio config for {len(failed)} AP(s); their per-AP overrides are unknown",
            details={"macs": failed[:20]},
        ))
    overridden = sum(1 for v in out.values() if any(r.get("wlan_group_id") for r in v.get("radios", {}).values()))
    progress("ap_radio", f"Per-AP radio config read: {overridden} AP(s) carry a WLAN Group override")
    return out


async def _fetch_zone_ap_models(sz_client, zone_id, aps_raw, progress, warnings) -> Dict[str, Dict[str, Any]]:
    """
    The zone's settings for each AP model it has (one GET per model), and the
    zone Ethernet Port Profiles their LAN ports point at (one GET per
    distinct profile). These are zone-level config -- they land on the R1
    venue's per-model settings.
    """
    models = sorted({ap.get("model") for ap in aps_raw if ap.get("model")})
    if not models:
        return {}
    progress("ap_models", f"Reading zone settings for {len(models)} AP model(s)...")
    base = f"/{sz_client.api_version}/rkszones/{zone_id}"
    out: Dict[str, Dict[str, Any]] = {}
    profiles: Dict[str, Dict[str, Any]] = {}
    for model in models:
        try:
            settings = await sz_client._request("GET", f"{base}/apmodel/{model}")
            out[model] = {"settings": settings or {}, "eth_profiles": {}}
        except Exception as e:
            out[model] = {"settings": {}, "eth_profiles": {}, "error": f"{type(e).__name__}: {e}"[:200]}
    for model, entry in out.items():
        for port in entry["settings"].get("lanPorts") or []:
            pid = (port.get("ethPortProfile") or {}).get("id")
            if pid is None or pid in profiles:
                continue
            try:
                profiles[pid] = await sz_client._request("GET", f"{base}/profile/ethernetPort/{pid}") or {}
            except Exception as e:
                profiles[pid] = {"error": f"{type(e).__name__}: {e}"[:200]}
    for entry in out.values():
        ids = {(p.get("ethPortProfile") or {}).get("id") for p in entry["settings"].get("lanPorts") or []}
        entry["eth_profiles"] = {i: profiles[i] for i in ids if i in profiles}
    failed = [m for m, e in out.items() if e.get("error")]
    if failed:
        warnings.append(SZExtractionWarning(
            phase="ap_models",
            message=f"Could not read zone settings for AP model(s): {', '.join(failed)}",
            details={"models": failed},
        ))
    progress("ap_models", f"Zone AP model settings read: {', '.join(models)}")
    return out
