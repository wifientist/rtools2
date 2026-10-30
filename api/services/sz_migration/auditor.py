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
from typing import Any, Dict, List, Optional, Set

from schemas.sz_migration import SZMigrationSnapshot
from schemas.r1_inventory import R1VenueInventory
from schemas.migration_audit import (
    MigrationAuditReport, AuditSummary, NetworkAuditItem, AuditStatus,
    FieldDiff, FieldComparisonItem, APGroupActivationAudit, ResourceCoverage,
    AuthPath, AuthIssue, AuthPathAudit, RadiusServerCheck, DpskPoolCheck,
    IdentityGroupCount,
)
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.mapper import map_all_wlans
from services.sz_migration.field_mappings import compare_fields
from services.sz_migration.audit_matching import build_matching
from services.sz_migration.audit_scorecard import build_overview, build_scorecard
from services.sz_migration.audit_portal import build_portal_audit, is_portal_wlan
from services.sz_migration.venue_settings import compare_venue_settings
from services.sz_migration.broadcast_model import build_broadcast_audit, r1_ssids_by_group
from services.sz_migration.wlan_assignment import SzAssignments, r1_activation, r1_activation_lines
from services.sz_migration.passphrase_check import PSK_AUTH_TYPES, compare_passphrases

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
    "GUEST": "wispr",   # R1 guest (WISPr captive portal) network
    "guest": "wispr",
}


def run_audit(
    snapshot: SZMigrationSnapshot,
    r1_inventory: R1VenueInventory,
    sz_snapshot_job_id: str,
    r1_snapshot_job_id: str,
    r1_network_details: Optional[Dict[str, Dict]] = None,
    auth_facts: Optional[Dict] = None,
    r1_venue_settings: Optional[Dict] = None,
    saved_portal_key_fp: Optional[str] = None,
) -> MigrationAuditReport:
    """
    Run the full three-way audit comparison.

    1. Run resolver to get per-AP-Group WLAN activations
    2. Run mapper to get expected R1 network types
    3. For each SZ WLAN: find matching R1 network, compare fields
    4. Find R1 networks with no SZ counterpart (extra)
    5. Check AP Group activation coverage
    6. Check supporting resource coverage (DPSK pools, identity groups)
    7. For RADIUS/DPSK-backed WLANs: which auth path R1 took, and whether it
       is complete (auth_facts from auth_probe.collect_auth_facts)
    """
    warnings: List[str] = list((auth_facts or {}).get("errors", []))
    auth_items: List[AuthPathAudit] = []

    # Step 0: Pair SZ objects with R1 objects (shown on its own, used below)
    matching = build_matching(snapshot, r1_inventory)
    wlan_pairs = {m.sz_wlan_id: m for m in matching.wlans}
    # Step 0c: which SSIDs each AP broadcasts, resolved through each hierarchy
    broadcast = build_broadcast_audit(snapshot, r1_inventory, matching)
    venue_networks = {n.get("id"): n for n in r1_inventory.wifi_networks if n.get("id")}
    _annotate_assignments(snapshot, r1_inventory, matching, broadcast, venue_networks)

    # Step 0b: Zone vs venue settings (needs a live R1 read)
    venue_settings = (
        compare_venue_settings(snapshot.zone.raw, r1_inventory.venue, r1_venue_settings)
        if r1_venue_settings is not None else None
    )

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

        # The R1 network paired by the matching layer -- one pairing for the
        # whole report, so sections can't disagree about what matched.
        pair = wlan_pairs.get(wlan.id)
        actual = venue_networks.get(pair.r1_network_id) if pair and pair.activated_in_venue else None

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
                notes=(
                    f"R1 network '{pair.r1_name}' exists in the tenant but is not activated in this venue"
                    if pair and pair.r1_network_id else "Network not found in R1"
                ),
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

        # PSK passphrase: compared server-side as keyed fingerprints; only the
        # verdict leaves (never the value, never a fingerprint).
        pp_check: Optional[Dict[str, str]] = None
        if wlan.auth_type in PSK_AUTH_TYPES and actual_type == "psk":
            detail = (r1_network_details or {}).get(actual_id)
            pp_check = compare_passphrases(
                (wlan.raw or {}).get("encryption") or wlan.encryption,
                (detail or {}).get("wlan") if detail else None,
            )
            if pp_check["status"] in ("differs", "missing_in_r1", "missing_in_sz"):
                diffs.append(FieldDiff(
                    field="passphrase", expected="same as SZ",
                    actual={"differs": "different", "missing_in_r1": "none set in R1",
                            "missing_in_sz": "set in R1, none on SZ"}[pp_check["status"]],
                    severity="error",
                ))

        notes = mapping.notes if mapping and mapping.notes else ""
        auth = None
        if auth_facts is not None:
            auth = audit_auth_path(wlan, mapping, actual, snapshot, auth_facts)
            if auth is not None:
                auth_items.append(auth)
                if type_change_explained_by_path(sz_auth_backend(wlan, mapping), auth):
                    diffs = [d for d in diffs if d.field != "network_type"]
                    if actual_type != expected_type:
                        notes = (
                            f"Became {actual_type_raw} ({auth.path.value} auth path) — "
                            "a valid outcome for an externally-authenticated WLAN"
                        )

        status = AuditStatus.OK if not diffs else AuditStatus.WARNING
        if status == AuditStatus.OK and auth is not None and auth.status != AuditStatus.OK:
            status = AuditStatus.WARNING

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
                    status=fc.status,
                    sz_display=fc.sz_display,
                    r1_display=fc.r1_display,
                    note=fc.note,
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
            passphrase_check=pp_check["status"] if pp_check else None,
            passphrase_note=pp_check["note"] if pp_check else "",
            status=status,
            diffs=diffs,
            notes=notes,
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
    ap_group_audits = _audit_ap_group_activations(
        resolver_result, r1_inventory, {g.sz_name: g for g in matching.ap_groups},
    )

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
        auth_external_count=sum(1 for a in auth_items if a.path == AuthPath.EXTERNAL),
        auth_internal_count=sum(1 for a in auth_items if a.path == AuthPath.INTERNAL),
        auth_hybrid_count=sum(1 for a in auth_items if a.path == AuthPath.HYBRID),
        auth_none_count=sum(1 for a in auth_items if a.path == AuthPath.NONE),
        auth_error_count=sum(
            1 for a in auth_items for i in a.issues if i.severity == "error"
        ),
    )

    # Captive portal WLANs, setting by setting
    portals, unpaired_portals = build_portal_audit(snapshot, r1_inventory, matching, r1_network_details, auth_facts,
                                                     saved_portal_key_fp)

    # Captive portal WLANs first, then by name (the pairing view too)
    portal_ids = {w.id for w in snapshot.wlans if is_portal_wlan(w)}
    network_items.sort(key=lambda n: (n.sz_wlan_id not in portal_ids, (n.sz_wlan_name or "").lower()))
    matching.wlans.sort(key=lambda m: (m.sz_wlan_id not in portal_ids, (m.sz_name or "").lower()))

    return MigrationAuditReport(
        sz_zone_name=snapshot.zone.name,
        r1_venue_name=r1_inventory.venue_name,
        sz_snapshot_job_id=sz_snapshot_job_id,
        r1_snapshot_job_id=r1_snapshot_job_id,
        audit_timestamp=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        matching=matching,
        overview=build_overview(snapshot, r1_inventory, matching),
        venue_settings=venue_settings,
        broadcast=broadcast,
        portals=portals,
        unpaired_r1_portals=unpaired_portals,
        scorecard=build_scorecard(
            snapshot, r1_inventory, matching, venue_settings, broadcast, resolver_result, network_items,
            ap_group_audits, auth_items if auth_facts is not None else None,
        ),
        networks=network_items,
        extra_r1_networks=extra_networks,
        ap_group_activations=ap_group_audits,
        resource_coverage=resource_checks,
        auth_paths=auth_items,
        warnings=warnings + resolver_result.warnings,
    )


def ssids_on_r1_group(r1_inventory: R1VenueInventory, r1_apg_id: str) -> Set[str]:
    """
    SSIDs a venue network activation puts on this R1 AP Group.

    venueApGroups comes in two shapes (apGroupIds, or apGroups[].apGroupId),
    and an All-AP-Groups activation covers every group in the venue.
    """
    out: Set[str] = set()
    for net in r1_inventory.wifi_networks:
        for vag in net.get("venueApGroups") or []:
            if vag.get("venueId") not in (None, r1_inventory.venue_id):
                continue
            ids = set(vag.get("apGroupIds") or [])
            ids |= {g.get("apGroupId") for g in vag.get("apGroups") or [] if g.get("apGroupId")}
            if vag.get("isAllApGroups") or r1_apg_id in ids:
                if net.get("ssid"):
                    out.add(net["ssid"])
    return out


def _audit_ap_group_activations(
    resolver_result,
    r1_inventory: R1VenueInventory,
    group_pairs: Dict[str, Any],
) -> List[APGroupActivationAudit]:
    """Check whether each AP Group has all expected SSIDs activated in R1."""
    audits = []

    for apg_summary in resolver_result.ap_group_summaries:
        expected_ssids = set(apg_summary.ssids)

        # The R1 group the matching layer paired (SZ "default" -> venue default)
        pair = group_pairs.get(apg_summary.ap_group_name)
        r1_apg_id = pair.r1_ap_group_id if pair else None

        actual_ssids = ssids_on_r1_group(r1_inventory, r1_apg_id) if r1_apg_id else set()
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
            r1_ap_group_found=r1_apg_id is not None,
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


# ═══════════════════════════════════════════════════════════════════════════
# Auth path: external RADIUS vs R1-internal DPSK
# ═══════════════════════════════════════════════════════════════════════════
#
# A WLAN that authenticated against an external server on SZ (usually
# Cloudpath) can land in R1 two ways: still pointing at that server through a
# RADIUS profile, or moved onto R1's own DPSK service with identities held in
# R1. Which one happened is read from what is linked to the R1 network, not
# from the migration plan, so a venue built by hand is audited the same way.
#
# Neither path exposes credentials here. The external server's identities are
# invisible to R1 entirely, so that path reports no counts; the internal path
# reports pool / identity / passphrase QUANTITIES only.

def sz_auth_backend(wlan, mapping) -> Optional[str]:
    """Where SZ sent this WLAN's authentication, or None if it is not RADIUS/DPSK."""
    if wlan.auth_type == "External DPSK":
        return "external_dpsk"
    if wlan.auth_type == "DPSK":
        return "external_dpsk" if (mapping and mapping.dpsk_type == "external") else "sz_dpsk"
    if wlan.auth_service_id or (mapping and mapping.r1_network_type == "aaa"):
        return "external_radius"
    return None


def _sz_server(raw: Dict, key: str) -> Optional[str]:
    """SZ auth/accounting service server as "ip:port" (primary or secondary)."""
    if not isinstance(raw, dict):
        return None
    server = raw.get(key)
    if isinstance(server, dict):
        ip = server.get("ip") or server.get("server") or server.get("hostname")
        port = server.get("port")
    elif key == "primary" and raw.get("ip"):
        ip, port = raw.get("ip"), raw.get("port")
    else:
        return None
    if not ip:
        return None
    return f"{ip}:{port}" if port else str(ip)


def _same_server(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    """Compare "ip:port" strings; a missing port on either side compares host only."""
    if not a or not b:
        return None
    ha, _, pa = a.strip().lower().rpartition(":") if ":" in a else (a.strip().lower(), "", "")
    hb, _, pb = b.strip().lower().rpartition(":") if ":" in b else (b.strip().lower(), "", "")
    if ha != hb:
        return False
    return (pa == pb) if (pa and pb) else True


def _radius_check(role, sz_ref, r1_profile) -> RadiusServerCheck:
    raw = sz_ref.raw if sz_ref else {}
    sz_primary = _sz_server(raw, "primary")
    sz_secondary = _sz_server(raw, "secondary")
    r1_primary = (r1_profile or {}).get("primary")
    r1_secondary = (r1_profile or {}).get("secondary")
    return RadiusServerCheck(
        role=role,
        sz_service_name=(sz_ref.name or raw.get("name")) if sz_ref else None,
        sz_primary=sz_primary,
        sz_secondary=sz_secondary,
        r1_profile_id=(r1_profile or {}).get("id"),
        r1_profile_name=(r1_profile or {}).get("name"),
        r1_primary=r1_primary,
        r1_secondary=r1_secondary,
        primary_match=_same_server(sz_primary, r1_primary),
        # A secondary on only one side is a mismatch, not "nothing to compare".
        secondary_match=(
            _same_server(sz_secondary, r1_secondary) if (sz_secondary and r1_secondary)
            else False if sz_secondary
            else None
        ),
        placeholder_secret=(r1_profile or {}).get("placeholder_secret"),
    )


def audit_auth_path(
    wlan,
    mapping,
    actual: Dict,
    snapshot: SZMigrationSnapshot,
    facts: Dict,
) -> Optional[AuthPathAudit]:
    """Auth-path audit for one matched WLAN, or None if it is not RADIUS/DPSK-backed."""
    backend = sz_auth_backend(wlan, mapping)
    if backend is None:
        return None

    net_id = actual.get("id", "")
    issues: List[AuthIssue] = []

    def issue(severity: str, message: str) -> None:
        issues.append(AuthIssue(severity=severity, message=message))

    linked = (facts.get("radius_by_network") or {}).get(net_id, [])
    auth_profiles = [p for p in linked if p.get("type") != "ACCOUNTING"]
    acct_profiles = [p for p in linked if p.get("type") == "ACCOUNTING"]
    pool_info = (facts.get("pools_by_network") or {}).get(net_id) or {}
    pools = pool_info.get("pools") or []

    has_external = bool(auth_profiles)
    has_internal = bool(pools)
    if has_external and has_internal:
        path = AuthPath.HYBRID
    elif has_external:
        path = AuthPath.EXTERNAL
    elif has_internal:
        path = AuthPath.INTERNAL
    else:
        path = AuthPath.NONE

    settings = (facts.get("radius_settings") or {}).get(net_id) or {}
    item = AuthPathAudit(
        sz_wlan_id=wlan.id,
        sz_wlan_name=wlan.name,
        sz_ssid=wlan.ssid,
        sz_auth_type=wlan.auth_type,
        sz_backend=backend,
        r1_network_id=net_id or None,
        r1_network_name=actual.get("name"),
        r1_network_type=actual.get("nwSubType", actual.get("type")),
        path=path,
        auth_proxy=settings.get("enable_auth_proxy"),
        accounting_proxy=settings.get("enable_accounting_proxy"),
        status=AuditStatus.OK,
    )

    # ── External: does R1 point at the same server SZ did? ──────────
    if path in (AuthPath.EXTERNAL, AuthPath.HYBRID):
        auth_ref = snapshot.referenced_objects.get(f"auth_service:{wlan.auth_service_id}") \
            if wlan.auth_service_id else None
        acct_ref = snapshot.referenced_objects.get(
            f"accounting_service:{wlan.accounting_service_id}"
        ) if wlan.accounting_service_id else None

        if len(auth_profiles) > 1:
            issue("warning", f"{len(auth_profiles)} authentication profiles are linked; expected one")
        auth_check = _radius_check("authentication", auth_ref, auth_profiles[0])
        item.radius.append(auth_check)

        if backend == "sz_dpsk":
            issue("warning",
                  "SZ held these DPSK passphrases itself, but R1 now sends authentication "
                  "to an external RADIUS server — confirm that server has the identities")
        elif auth_ref is None:
            issue("info", "SZ auth service not in the snapshot — server address not compared")

        if auth_check.primary_match is False:
            issue("error", "Authentication server differs from SZ "
                           f"({auth_check.sz_primary} on SZ, {auth_check.r1_primary} in R1)")
        if auth_check.secondary_match is False:
            if auth_check.sz_secondary and not auth_check.r1_secondary:
                issue("warning", "SZ had a secondary authentication server; R1 has none")
            else:
                issue("warning", "Secondary authentication server differs from SZ")
        if auth_check.placeholder_secret:
            issue("error", "Shared secret is still the migration placeholder — "
                           "the RADIUS server will reject every request until it is set")
        elif auth_check.placeholder_secret is None:
            issue("info", "Could not read the profile to check for the placeholder shared secret")

        if wlan.accounting_service_id:
            if acct_profiles:
                acct_check = _radius_check("accounting", acct_ref, acct_profiles[0])
                item.radius.append(acct_check)
                if acct_check.primary_match is False:
                    issue("warning", "Accounting server differs from SZ "
                                     f"({acct_check.sz_primary} on SZ, {acct_check.r1_primary} in R1)")
                if acct_check.placeholder_secret:
                    issue("error", "Accounting shared secret is still the migration placeholder")
            else:
                issue("warning", "SZ sent accounting to a RADIUS server; R1 has no accounting profile linked")
        elif acct_profiles:
            item.radius.append(_radius_check("accounting", None, acct_profiles[0]))
            issue("info", "R1 has an accounting profile that SZ did not")

        if item.auth_proxy is True:
            issue("info", "RADIUS is proxied through R1: the server must accept R1's proxy "
                          "addresses as RADIUS clients")
        elif item.auth_proxy is False:
            issue("info", "APs talk to the RADIUS server directly: the server must accept "
                          "each AP's address (or its NAT) as a RADIUS client")

        if path == AuthPath.EXTERNAL:
            issue("info", "Identities live on the external server — R1 cannot count them")

    # ── Internal: is the DPSK service populated? ────────────────────
    if path in (AuthPath.INTERNAL, AuthPath.HYBRID):
        for p in pools:
            item.pools.append(DpskPoolCheck(
                id=p["id"],
                name=p.get("name"),
                passphrase_count=p.get("passphrase_count"),
                identity_groups=[IdentityGroupCount(**g) for g in p.get("identity_groups", [])],
            ))

        pp = [p.passphrase_count for p in item.pools]
        ids = [g.identity_count for p in item.pools for g in p.identity_groups]
        item.passphrase_total = sum(pp) if pp and None not in pp else None
        item.identity_total = sum(ids) if ids and None not in ids else None

        if any(not p.identity_groups for p in item.pools):
            issue("error", "DPSK service has no identity group — no identities can authenticate")
        if item.passphrase_total == 0:
            issue("error", "DPSK service holds no passphrases — nobody can connect yet")
        elif item.passphrase_total is None:
            issue("warning", "Could not count passphrases")
        if item.identity_total == 0:
            issue("warning", "Identity groups are empty")
        elif ids and item.identity_total is None:
            issue("warning", "Could not count identities")
        if (item.passphrase_total is not None and item.identity_total is not None
                and item.passphrase_total != item.identity_total):
            issue("info",
                  f"{item.passphrase_total} passphrases vs {item.identity_total} identities — "
                  "passphrases created without an existing identity get an auto-named one "
                  "(DPSK_User_…), so a gap can mean debris")

        if backend in ("external_radius", "external_dpsk"):
            issue("info",
                  "Moved from an external server to R1 DPSK. The source's identity count is "
                  "not visible here — compare these counts against the server's own export")

    if pool_info.get("error"):
        issue("warning", "Could not read this network's DPSK service link")

    if path == AuthPath.HYBRID:
        issue("warning", "Both an external RADIUS profile and a DPSK service are linked — "
                         "hybrid is reported but not audited in depth; confirm it is intended")
    elif path == AuthPath.NONE:
        issue("error", "No RADIUS profile and no DPSK service linked — nothing can authenticate")

    item.issues = issues
    severities = {i.severity for i in issues}
    item.status = (
        AuditStatus.MISSING if path == AuthPath.NONE
        else AuditStatus.WARNING if severities & {"error", "warning"}
        else AuditStatus.OK
    )
    return item


def type_change_explained_by_path(backend: Optional[str], auth: Optional[AuthPathAudit]) -> bool:
    """
    An externally-authenticated SZ WLAN may legitimately become either an AAA
    network (kept external) or a DPSK network (moved internal). The mapper can
    only suggest one, so a network_type mismatch that matches the path taken
    is a choice, not an error.
    """
    if auth is None or backend not in ("external_radius", "external_dpsk"):
        return False
    actual = str(auth.r1_network_type or "").lower()
    return (
        (auth.path == AuthPath.EXTERNAL and actual == "aaa")
        # external DPSK kept external: an R1 DPSK network asking the RADIUS server
        or (auth.path == AuthPath.EXTERNAL and actual == "dpsk" and backend == "external_dpsk")
        or (auth.path == AuthPath.INTERNAL and actual == "dpsk")
    )


def _annotate_assignments(snapshot, r1_inventory, matching, broadcast, venue_networks) -> None:
    """
    Fill the pairing view's "where assigned" columns: every SZ path that
    brings each WLAN in (zone / AP Group / AP override, with WLAN Group and
    radios), how R1 activates its network (All AP Groups or specific
    groups), and how many APs end up broadcasting it on each side.
    """
    sz = SzAssignments(snapshot)
    sz_count = {s.ssid: s.sz_ap_count for s in broadcast.ssids}
    r1_count = {s.ssid: s.r1_ap_count for s in broadcast.ssids}
    vlan_by_wlan = {w.id: w.vlan_id for w in snapshot.wlans}
    for m in matching.wlans:
        m.sz_assigned = sz.paths_for_wlan(m.sz_wlan_id)
        m.sz_ap_count = sz_count.get(m.sz_ssid, 0)
        m.sz_vlan = vlan_by_wlan.get(m.sz_wlan_id)
        net = venue_networks.get(m.r1_network_id) if m.r1_network_id else None
        if net is not None:
            act = r1_activation(r1_inventory, net)
            m.r1_activated = r1_activation_lines(act)
            m.r1_all_ap_groups = act["all"]
            m.r1_ap_count = r1_count.get(m.r1_ssid, 0)
            m.r1_vlan = _as_int(net.get("vlan"))
        elif m.r1_network_id:
            m.r1_activated = ["Not activated in this venue"]
            m.r1_ap_count = 0

    # WLAN Groups: which R1 AP Groups carry every SSID in each group
    by_group, everywhere = r1_ssids_by_group(r1_inventory)
    names = {g.get("id"): (g.get("name") or "(default)") for g in r1_inventory.ap_groups}
    carrying: Dict[str, List[str]] = {}
    # Members carry the WLAN's id and name, not its SSID
    ssid_of = {w.id: w.ssid for w in snapshot.wlans}
    for g in snapshot.wlan_groups:
        want = {ssid_of.get(m.id) or m.ssid for m in g.members} - {None, ""}
        if want:
            carrying[g.id] = sorted(names[gid] for gid in names
                                    if want <= (by_group.get(gid, set()) | everywhere))
    matching.wlan_groups = sz.usage(carrying)


def _as_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
