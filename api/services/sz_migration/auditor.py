"""
Migration Audit Service

Three-way comparison: SZ Source → Expected R1 → Actual R1.
Pure function — no I/O. Receives pre-loaded snapshots, returns structured report.

Uses the existing resolver (WLAN Group → per-AP-Group activations) and
mapper (SZ auth type → R1 network type) to derive the expected R1 state,
then compares against the actual R1 venue inventory.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from schemas.sz_migration import SZMigrationSnapshot
from schemas.r1_inventory import R1VenueInventory
from schemas.migration_audit import (
    MigrationAuditReport, AuditSummary, NetworkAuditItem, AuditStatus,
    FieldDiff, FieldComparisonItem, APGroupActivationAudit, ResourceCoverage,
)
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.mapper import map_all_wlans
from services.sz_migration.field_mappings import compare_fields

logger = logging.getLogger(__name__)

# R1 nwSubType values → canonical lowercase types (matching mapper output)
R1_SUBTYPE_MAP: Dict[str, str] = {
    "PSK": "psk",
    "OPEN": "open",
    "AAA": "aaa",
    "DPSK": "dpsk",
    "psk": "psk",
    "open": "open",
    "aaa": "aaa",
    "dpsk": "dpsk",
}


def run_audit(
    snapshot: SZMigrationSnapshot,
    r1_inventory: R1VenueInventory,
    sz_snapshot_job_id: str,
    r1_snapshot_job_id: str,
    r1_network_details: Optional[Dict[str, Dict]] = None,
) -> MigrationAuditReport:
    """
    Run the full three-way audit comparison.

    1. Run resolver to get per-AP-Group WLAN activations
    2. Run mapper to get expected R1 network types
    3. For each SZ WLAN: find matching R1 network, compare fields
    4. Find R1 networks with no SZ counterpart (extra)
    5. Check AP Group activation coverage
    6. Check supporting resource coverage (DPSK pools, identity groups)
    """
    warnings: List[str] = []

    # Step 1: Resolve WLAN activations
    resolver_result = resolve_wlan_activations(snapshot)

    # Step 2: Map security types
    type_mappings = map_all_wlans(snapshot.wlans)

    # Step 3: Per-WLAN comparison
    network_items: List[NetworkAuditItem] = []
    matched_r1_ids: Set[str] = set()

    for wlan in snapshot.wlans:
        mapping = type_mappings.get(wlan.id)
        r1_type = mapping.r1_network_type if mapping else "unsupported"

        # Migration uses wlan.name as R1 network name, wlan.ssid as SSID
        expected_name = wlan.name
        expected_ssid = wlan.ssid
        expected_type = r1_type

        # Unsupported auth type — no R1 equivalent expected
        if r1_type == "unsupported":
            network_items.append(NetworkAuditItem(
                sz_wlan_id=wlan.id,
                sz_wlan_name=wlan.name,
                sz_ssid=wlan.ssid,
                sz_auth_type=wlan.auth_type,
                sz_vlan_id=wlan.vlan_id,
                sz_encryption_method=wlan.encryption_method,
                expected_r1_name=expected_name,
                expected_r1_type=expected_type,
                expected_r1_ssid=expected_ssid,
                status=AuditStatus.UNSUPPORTED,
                notes=mapping.notes if mapping else "Unsupported auth type",
            ))
            continue

        # Find actual R1 network — match by name first, then SSID
        actual = r1_inventory.network_by_name(wlan.name)
        if not actual:
            actual = r1_inventory.network_by_ssid(wlan.ssid)

        if not actual:
            # Missing — expected but not found in R1
            network_items.append(NetworkAuditItem(
                sz_wlan_id=wlan.id,
                sz_wlan_name=wlan.name,
                sz_ssid=wlan.ssid,
                sz_auth_type=wlan.auth_type,
                sz_vlan_id=wlan.vlan_id,
                sz_encryption_method=wlan.encryption_method,
                expected_r1_name=expected_name,
                expected_r1_type=expected_type,
                expected_r1_ssid=expected_ssid,
                status=AuditStatus.MISSING,
                notes="Network not found in R1 venue",
            ))
            continue

        # Found — compare fields
        actual_id = actual.get("id", "")
        matched_r1_ids.add(actual_id)

        actual_name = actual.get("name", "")
        actual_ssid = actual.get("ssid", "")
        actual_type_raw = actual.get("nwSubType", actual.get("type", ""))
        actual_type = R1_SUBTYPE_MAP.get(actual_type_raw, actual_type_raw.lower())
        actual_vlan = actual.get("vlan")

        diffs: List[FieldDiff] = []

        if actual_name != expected_name:
            diffs.append(FieldDiff(
                field="name", expected=expected_name,
                actual=actual_name, severity="warning",
            ))

        if actual_ssid != expected_ssid:
            diffs.append(FieldDiff(
                field="ssid", expected=expected_ssid,
                actual=actual_ssid, severity="error",
            ))

        if actual_type != expected_type:
            diffs.append(FieldDiff(
                field="network_type", expected=expected_type,
                actual=actual_type_raw, severity="error",
            ))

        if wlan.vlan_id is not None and actual_vlan is not None:
            try:
                if int(actual_vlan) != wlan.vlan_id:
                    diffs.append(FieldDiff(
                        field="vlan_id", expected=wlan.vlan_id,
                        actual=int(actual_vlan), severity="warning",
                    ))
            except (ValueError, TypeError):
                pass

        status = AuditStatus.OK if not diffs else AuditStatus.WARNING

        # Deep field comparison using field_mappings registry
        field_comps: List[FieldComparisonItem] = []
        if wlan.raw:
            # Use full R1 network detail if available, fall back to query result
            r1_full = (r1_network_details or {}).get(actual_id, actual)
            raw_comparisons = compare_fields(wlan.raw, r1_full)
            field_comps = [
                FieldComparisonItem(
                    section=fc.section,
                    label=fc.label,
                    sz_value=fc.sz_value,
                    r1_value=fc.r1_value,
                    match=fc.match,
                    sz_only=fc.sz_only,
                )
                for fc in raw_comparisons
            ]

        network_items.append(NetworkAuditItem(
            sz_wlan_id=wlan.id,
            sz_wlan_name=wlan.name,
            sz_ssid=wlan.ssid,
            sz_auth_type=wlan.auth_type,
            sz_vlan_id=wlan.vlan_id,
            sz_encryption_method=wlan.encryption_method,
            expected_r1_name=expected_name,
            expected_r1_type=expected_type,
            expected_r1_ssid=expected_ssid,
            actual_r1_id=actual_id,
            actual_r1_name=actual_name,
            actual_r1_ssid=actual_ssid,
            actual_r1_type=actual_type_raw,
            actual_r1_vlan=actual_vlan,
            field_comparisons=field_comps,
            status=status,
            diffs=diffs,
            notes=mapping.notes if mapping and mapping.notes else "",
        ))

    # Step 4: Find extra R1 networks (no SZ counterpart)
    extra_networks = []
    for net in r1_inventory.wifi_networks:
        net_id = net.get("id", "")
        if net_id not in matched_r1_ids:
            extra_networks.append({
                "id": net_id,
                "name": net.get("name", ""),
                "ssid": net.get("ssid", ""),
                "nwSubType": net.get("nwSubType", net.get("type", "")),
                "vlan": net.get("vlan"),
            })

    # Step 5: AP Group activation coverage
    ap_group_audits = _audit_ap_group_activations(resolver_result, r1_inventory)

    # Step 6: Resource coverage
    resource_checks = _audit_resource_coverage(snapshot, r1_inventory)

    # Build summary
    ok_count = sum(1 for n in network_items if n.status == AuditStatus.OK)
    warning_count = sum(1 for n in network_items if n.status == AuditStatus.WARNING)
    missing_count = sum(1 for n in network_items if n.status == AuditStatus.MISSING)
    unsupported_count = sum(1 for n in network_items if n.status == AuditStatus.UNSUPPORTED)
    total_diffs = sum(len(n.diffs) for n in network_items)

    covered_groups = sum(1 for a in ap_group_audits if not a.missing_ssids)
    total_groups = len(ap_group_audits) or 1
    ap_coverage = round((covered_groups / total_groups) * 100)

    summary = AuditSummary(
        total_sz_wlans=len(snapshot.wlans),
        ok_count=ok_count,
        warning_count=warning_count,
        missing_count=missing_count,
        extra_count=len(extra_networks),
        unsupported_count=unsupported_count,
        ap_group_coverage=ap_coverage,
        total_diffs=total_diffs,
    )

    return MigrationAuditReport(
        sz_zone_name=snapshot.zone.name,
        r1_venue_name=r1_inventory.venue_name,
        sz_snapshot_job_id=sz_snapshot_job_id,
        r1_snapshot_job_id=r1_snapshot_job_id,
        audit_timestamp=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        networks=network_items,
        extra_r1_networks=extra_networks,
        ap_group_activations=ap_group_audits,
        resource_coverage=resource_checks,
        warnings=warnings + resolver_result.warnings,
    )


def _audit_ap_group_activations(
    resolver_result,
    r1_inventory: R1VenueInventory,
) -> List[APGroupActivationAudit]:
    """Check whether each AP Group has all expected SSIDs activated in R1."""
    audits = []

    for apg_summary in resolver_result.ap_group_summaries:
        expected_ssids = set(apg_summary.ssids)

        # Find matching R1 AP Group
        r1_apg = r1_inventory.ap_group_by_name(apg_summary.ap_group_name)
        r1_apg_id = r1_apg.get("id") if r1_apg else None

        # Find actual SSIDs activated on this AP Group
        actual_ssids: Set[str] = set()
        if r1_apg_id:
            for net in r1_inventory.wifi_networks:
                for vag in net.get("venueApGroups", []):
                    for apg in vag.get("apGroups", []):
                        if apg.get("apGroupId") == r1_apg_id:
                            ssid = net.get("ssid", "")
                            if ssid:
                                actual_ssids.add(ssid)

        missing = sorted(expected_ssids - actual_ssids)
        extra = sorted(actual_ssids - expected_ssids)

        audits.append(APGroupActivationAudit(
            sz_ap_group_name=apg_summary.ap_group_name,
            sz_ssid_count=apg_summary.ssid_count,
            expected_ssids=sorted(expected_ssids),
            actual_ssids=sorted(actual_ssids),
            missing_ssids=missing,
            extra_ssids=extra,
            r1_ap_group_id=r1_apg_id,
            r1_ap_group_found=r1_apg is not None,
        ))

    return audits


def _audit_resource_coverage(
    snapshot: SZMigrationSnapshot,
    r1_inventory: R1VenueInventory,
) -> List[ResourceCoverage]:
    """Check supporting resource coverage (DPSK pools, identity groups)."""
    checks = []

    # DPSK pools — WLANs with DPSK type should have a matching pool
    dpsk_wlans = [w for w in snapshot.wlans if w.auth_type == "DPSK"]
    if dpsk_wlans:
        # Migration creates pools named "{wlan_name}-pool"
        expected_pool_names = [f"{w.name}-pool" for w in dpsk_wlans]
        matched = [n for n in expected_pool_names if r1_inventory.dpsk_pool_by_name(n)]
        missing = [n for n in expected_pool_names if not r1_inventory.dpsk_pool_by_name(n)]
        checks.append(ResourceCoverage(
            resource_type="dpsk_pools",
            expected_count=len(expected_pool_names),
            actual_count=len(r1_inventory.dpsk_pools),
            matched=matched,
            missing=missing,
        ))

        # Identity groups — migration creates "{wlan_name}-identities"
        expected_ig_names = [f"{w.name}-identities" for w in dpsk_wlans]
        matched_ig = [n for n in expected_ig_names if r1_inventory.identity_group_by_name(n)]
        missing_ig = [n for n in expected_ig_names if not r1_inventory.identity_group_by_name(n)]
        checks.append(ResourceCoverage(
            resource_type="identity_groups",
            expected_count=len(expected_ig_names),
            actual_count=len(r1_inventory.identity_groups),
            matched=matched_ig,
            missing=missing_ig,
        ))

    return checks
