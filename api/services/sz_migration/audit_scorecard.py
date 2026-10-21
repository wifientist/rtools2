"""
End-of-run scorecard: SZ vs R1, category by category.

The detail sections answer "what exactly differs"; this answers "how aligned
is this site", in the order an operator walks a migration: zone/venue
settings, APs, AP Groups, AP membership, WLAN Groups (SZ-only), WLANs,
WLAN settings, passphrases.

Categories later audit checkpoints will fill are present from the start as
"not_checked" with a note, so the scorecard's shape doesn't change under you.

Pure: builds from pieces the auditor already computed.
"""

from typing import Any, Dict, List, Optional, Set

from schemas.migration_audit import (
    AlignmentCategory, APGroupActivationAudit, AuditMatching, AuthPath, AuthPathAudit,
    NetworkAuditItem, VenueSettingsAudit, BroadcastAudit,
)
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot

SZ_DEFAULT_WLAN_GROUP = "default"
# Key WLAN settings for the scorecard row; the detail table shows everything.
_KEY_FIELD_LABELS = {"VLAN ID": "VLAN", "Security Mode": "Security", "Mgmt Frame Protection": "MFP"}
_KEY_DIFF_FIELDS = {"ssid": "SSID", "network_type": "Type", "vlan_id": "VLAN", "passphrase": "Passphrase"}


def _status(aligned: int, total: int) -> str:
    if total == 0 or aligned == total:
        return "aligned"
    return "misaligned" if aligned == 0 else "partial"


def _venue_config(vs: Optional[VenueSettingsAudit]) -> AlignmentCategory:
    cat = AlignmentCategory(key="venue_config", label="Zone ↔ Venue settings")
    if vs is None:
        cat.details.append("Needs a live R1 connection for this run")
        return cat
    compared = [r for r in vs.rows if r.status in ("match", "differs", "unmappable")]
    ok = sum(1 for r in compared if r.status == "match")
    cat.sz_count = cat.r1_count = len(compared)
    cat.aligned_count = ok
    cat.status = _status(ok, len(compared))
    by_section: Dict[str, Dict[str, int]] = {}
    for r in vs.rows:
        s = by_section.setdefault(r.section, {"match": 0, "differs": 0, "other": 0})
        s["match" if r.status == "match" else "differs" if r.status in ("differs", "unmappable") else "other"] += 1
    cat.breakdown = [{"section": k, **v} for k, v in by_section.items()]
    for status, text in (("unmappable", "SZ value(s) R1 can't express"),
                         ("sz_only", "SZ setting(s) with no R1 equivalent"),
                         ("review", "setting(s) to confirm by eye"),
                         ("unavailable", "R1 setting(s) could not be read")):
        n = sum(1 for r in vs.rows if r.status == status)
        if n:
            cat.details.append(f"{n} {text}")
    return cat


def _aps(m: AuditMatching, inv: R1VenueInventory, snapshot: SZMigrationSnapshot) -> AlignmentCategory:
    c = m.counts
    cat = AlignmentCategory(
        key="aps", label="APs", sz_count=len(snapshot.aps), r1_count=len(inv.aps),
        aligned_count=c.get("aps_in_both", 0),
        breakdown=[
            {"label": "In both", "count": c.get("aps_in_both", 0)},
            {"label": "SZ only (not in the R1 venue)", "count": c.get("aps_sz_only", 0)},
            {"label": "R1 only (not in the SZ zone)", "count": c.get("aps_r1_only", 0)},
        ],
    )
    if not inv.aps and snapshot.aps:
        cat.status = "pending"
        cat.details.append("No APs in the R1 venue yet — expected until the APs are moved")
    else:
        cat.status = _status(cat.aligned_count, len(snapshot.aps))
        if c.get("aps_r1_only"):
            cat.details.append(f"{c['aps_r1_only']} AP(s) in the R1 venue that aren't in this SZ zone")
            if cat.status == "aligned":
                cat.status = "partial"
    return cat


def _ap_groups(m: AuditMatching, inv: R1VenueInventory) -> AlignmentCategory:
    with_aps = [g for g in m.ap_groups if g.sz_ap_count > 0]
    matched = [g for g in with_aps if g.r1_ap_group_id]
    cat = AlignmentCategory(
        key="ap_groups", label="AP Groups", sz_count=len(with_aps), r1_count=len(inv.ap_groups),
        aligned_count=len(matched), status=_status(len(matched), len(with_aps)),
        breakdown=[
            {"sz": g.sz_name, "r1": g.r1_name, "match": g.method.value,
             "sz_aps": g.sz_ap_count, "r1_aps": g.r1_ap_count}
            for g in m.ap_groups
        ],
    )
    empty = len(m.ap_groups) - len(with_aps)
    if empty:
        cat.details.append(f"{empty} SZ AP Group(s) with no APs not counted (nothing broadcasts there)")
    naming = {"default": 0, "name": 0, "name_case": 0}
    for g in matched:
        naming[g.method.value] = naming.get(g.method.value, 0) + 1
    if naming["name_case"]:
        cat.details.append(f"{naming['name_case']} matched only ignoring case — names differ in capitalisation")
    if inv.aps:
        off = [g for g in matched if g.sz_ap_count != g.r1_ap_count]
        if off:
            cat.details.append("AP count differs: " + ", ".join(
                f"{g.sz_name} {g.sz_ap_count}→{g.r1_ap_count}" for g in off[:8]))
    if m.r1_only_ap_groups:
        cat.details.append(f"{len(m.r1_only_ap_groups)} R1 AP Group(s) with no SZ counterpart")
    return cat


def _ap_membership(m: AuditMatching) -> AlignmentCategory:
    both = [a for a in m.aps if a.presence == "in_both"]
    judged = [a for a in both if a.group_aligned is not None]
    ok = sum(1 for a in judged if a.group_aligned)
    names = {"exact": 0, "case": 0, "differs": 0}
    for a in both:
        if a.name_alignment:
            names[a.name_alignment] += 1
    cat = AlignmentCategory(
        key="ap_membership", label="AP → AP Group membership",
        sz_count=len(judged), r1_count=len(judged), aligned_count=ok,
        breakdown=[
            {"label": "In the expected R1 group", "count": ok},
            {"label": "In a different R1 group", "count": len(judged) - ok},
            {"label": "AP names identical", "count": names["exact"]},
            {"label": "AP names differ only in case", "count": names["case"]},
            {"label": "AP names differ", "count": names["differs"]},
        ],
    )
    if not both:
        cat.status = "pending"
        cat.details.append("No AP is in both yet — membership is checked once APs are in the R1 venue")
    else:
        cat.status = _status(ok, len(judged))
        unjudged = len(both) - len(judged)
        if unjudged:
            cat.details.append(f"{unjudged} AP(s) whose SZ group has no R1 counterpart — not judged")
        wrong = [a for a in judged if not a.group_aligned][:8]
        if wrong:
            cat.details.append("Wrong group: " + ", ".join(
                f"{a.name} ({a.r1_ap_group}, expected {a.expected_r1_ap_group})" for a in wrong))
    return cat


def _wlan_groups(snapshot: SZMigrationSnapshot, inv: R1VenueInventory, bc: BroadcastAudit) -> AlignmentCategory:
    """
    SZ WLAN Groups have no R1 counterpart; what they produce -- sets of APs
    broadcasting the same SSIDs -- maps onto R1 AP Groups. So the row counts
    WLAN Groups by where they're applied, and judges the broadcast groups.
    """
    apg_use: Dict[str, int] = {}
    for apg in snapshot.ap_groups:
        rc = apg.radio_config.model_dump() if apg.radio_config else {}
        for gid in {v for v in rc.values() if v}:
            apg_use[gid] = apg_use.get(gid, 0) + 1
    ap_use: Dict[str, int] = {}
    for v in snapshot.ap_radio_config.values():
        for gid in {r.get("wlan_group_id") for r in (v.get("radios") or {}).values() if r.get("wlan_group_id")}:
            ap_use[gid] = ap_use.get(gid, 0) + 1
    zone_rc = snapshot.zone.radio_config.model_dump() if snapshot.zone.radio_config else {}
    zone_ids = {v for v in zone_rc.values() if v}

    groups = bc.groups
    judged = [g for g in groups if g.status != "pending"]
    ok = sum(1 for g in judged if g.status in ("match", "split"))
    cat = AlignmentCategory(
        key="wlan_groups", label="WLAN Groups → R1 AP Groups",
        sz_count=len(snapshot.wlan_groups), r1_count=len(inv.ap_groups),
        aligned_count=ok, status="pending" if not judged else _status(ok, len(judged)),
        details=[
            "R1 has no WLAN Groups. What SZ's WLAN Groups and overrides produce is groups of APs "
            "broadcasting the same SSIDs; each should land on R1 AP Group(s) activated with those SSIDs.",
            f"{len(groups)} distinct SZ broadcast set(s); {ok} of {len(judged)} comparable "
            "match R1 AP by AP",
            f"WLAN Groups applied by AP override: {len(ap_use)} (on {bc.counts.get('sz_aps_with_ap_override', 0)} APs), "
            f"by AP Group: {len(apg_use)}, by zone: {len(zone_ids)}",
        ],
        breakdown=[
            {"wlan_group": g.name, "wlans": len(g.members),
             "aps_overridden_to_it": ap_use.get(g.id, 0), "ap_groups_using_it": apg_use.get(g.id, 0),
             "zone": "yes" if g.id in zone_ids or (g.name == SZ_DEFAULT_WLAN_GROUP and not zone_ids) else ""}
            for g in sorted(snapshot.wlan_groups, key=lambda g: -(ap_use.get(g.id, 0) + 100 * apg_use.get(g.id, 0)))
        ],
    )
    return cat


def _ssid_broadcast(bc: BroadcastAudit) -> AlignmentCategory:
    c = bc.counts
    compared = c.get("aps_compared", 0)
    cat = AlignmentCategory(
        key="ssid_broadcast", label="SSIDs broadcast per AP",
        sz_count=compared, r1_count=compared, aligned_count=c.get("aps_match", 0),
        details=list(bc.notes),
        breakdown=[
            {"label": "APs broadcasting the same SSIDs", "count": c.get("aps_match", 0)},
            {"label": "APs with SSIDs missing or extra in R1", "count": c.get("aps_differs", 0)},
            {"label": "APs matching, but SZ AP overrides unreadable", "count": c.get("aps_unknown", 0)},
            {"label": "SZ APs not in the R1 venue yet", "count": c.get("aps_pending", 0)},
            {"label": "R1-only APs", "count": c.get("aps_r1_only", 0)},
            {"label": "SSIDs on the same APs both sides", "count": c.get("ssids_match", 0)},
            {"label": "SSIDs on SZ, broadcast nowhere in R1", "count": c.get("ssids_missing_in_r1", 0)},
            {"label": "SSIDs broadcast in R1, nowhere on SZ", "count": c.get("ssids_extra_in_r1", 0)},
        ],
    )
    if compared == 0:
        cat.status = "pending" if c.get("aps_pending") else "aligned"
    else:
        cat.status = _status(c.get("aps_match", 0), compared)
    worst = sorted((s for s in bc.ssids if s.status != "match"), key=lambda s: -(s.missing_in_r1 + s.extra_in_r1))[:5]
    for s in worst:
        parts = [p for p in (f"{s.missing_in_r1} missing" if s.missing_in_r1 else "",
                             f"{s.extra_in_r1} extra" if s.extra_in_r1 else "") if p]
        cat.details.append(
            f"{s.ssid}: SZ {s.sz_ap_count} APs, R1 {s.r1_ap_count} APs"
            + (f" ({', '.join(parts)})" if parts else "")
        )
    return cat


PROPERTY_WIDE_SHARE = 0.9


def wlan_scopes(snapshot: SZMigrationSnapshot, bc: BroadcastAudit) -> Dict[str, str]:
    """
    wlan_id -> property / unit / none, from where SZ actually broadcasts it:
    property-wide = on at least 90% of the zone's APs. Judged per AP because
    per-unit sites usually do it with AP-level overrides inside one AP Group.
    """
    total = sum(1 for a in bc.aps if a.presence in ("in_both", "sz_only"))
    on = {s.ssid: s.sz_ap_count for s in bc.ssids}
    out = {}
    for w in snapshot.wlans:
        n = on.get(w.ssid, 0)
        out[w.id] = "none" if n == 0 else ("property" if total and n >= PROPERTY_WIDE_SHARE * total else "unit")
    return out


def _wlans(snapshot: SZMigrationSnapshot, inv: R1VenueInventory, m: AuditMatching, scopes: Dict[str, str]) -> AlignmentCategory:
    pairs = {w.sz_wlan_id: w for w in m.wlans}
    ok = [w for w in m.wlans if w.r1_network_id and w.activated_in_venue]
    breakdown = []
    for scope, label in (("property", "Property-wide"), ("unit", "Per-unit"), ("none", "Not broadcast on SZ")):
        ids = [w.id for w in snapshot.wlans if scopes.get(w.id, "none") == scope]
        if ids:
            breakdown.append({"label": label, "sz": len(ids),
                              "matched": sum(1 for i in ids if pairs[i].r1_network_id and pairs[i].activated_in_venue)})
    cat = AlignmentCategory(
        key="wlans", label="WLANs → R1 networks", sz_count=len(snapshot.wlans),
        r1_count=len(inv.wifi_networks), aligned_count=len(ok),
        status=_status(len(ok), len(snapshot.wlans)), breakdown=breakdown,
    )
    c = m.counts
    cat.details.append(
        f"Property-wide = broadcast on at least {int(PROPERTY_WIDE_SHARE * 100)}% of the zone's APs on SZ; "
        "per-unit = fewer (worked out per AP, so AP-level overrides count)"
    )
    if c.get("wlans_not_activated"):
        cat.details.append(f"{c['wlans_not_activated']} exist in R1 but aren't activated in this venue")
    if c.get("wlans_unmatched"):
        cat.details.append(f"{c['wlans_unmatched']} have no R1 network")
    if c.get("wlans_fallback_match"):
        cat.details.append(f"{c['wlans_fallback_match']} matched by SSID or case-insensitive name — names differ")
    if c.get("r1_only_networks"):
        cat.details.append(f"{c['r1_only_networks']} R1 network(s) in the venue with no SZ WLAN")
    return cat


def _wlan_config(items: List[NetworkAuditItem]) -> AlignmentCategory:
    compared = [i for i in items if i.actual_r1_id]
    per_field: Dict[str, List[str]] = {}
    clean = 0
    for i in compared:
        bad: Set[str] = set()
        for d in i.diffs:
            if d.field in _KEY_DIFF_FIELDS:
                bad.add(_KEY_DIFF_FIELDS[d.field])
        for fc in i.field_comparisons:
            if fc.label in _KEY_FIELD_LABELS and fc.status == "differs":
                bad.add(_KEY_FIELD_LABELS[fc.label])
        for f in bad:
            per_field.setdefault(f, []).append(i.sz_wlan_name)
        clean += not bad
    pp = [i.passphrase_check for i in compared if i.passphrase_check]
    pp_details = []
    if pp:
        pp_details.append(
            f"PSK passphrases: {len(pp)} compared — {pp.count('match')} match, {pp.count('differs')} differ, "
            f"{pp.count('missing_in_r1') + pp.count('missing_in_sz')} set on one side only, "
            f"{pp.count('unknown')} not comparable (compared server-side; values never shown)"
        )
    return AlignmentCategory(
        key="wlan_config", label="WLAN settings (VLAN, type, security, passphrase)",
        sz_count=len(compared), r1_count=len(compared), aligned_count=clean,
        status=_status(clean, len(compared)),
        details=pp_details + ["Per-AP-Group VLAN overrides and the full security set are compared in checkpoint 3"],
        breakdown=[{"field": f, "mismatches": len(n), "wlans": n[:10]} for f, n in sorted(per_field.items())],
    )


def _passphrases(auth_items: Optional[List[AuthPathAudit]]) -> AlignmentCategory:
    cat = AlignmentCategory(key="passphrases", label="DPSK passphrases (from Cloudpath)", status="manual")
    if auth_items is None:
        cat.status = "not_checked"
        cat.details.append("Needs a live R1 connection for this run")
        return cat
    pools: Dict[str, Dict[str, Any]] = {}
    for a in auth_items:
        if a.path not in (AuthPath.INTERNAL, AuthPath.HYBRID):
            continue
        for p in a.pools:
            entry = pools.setdefault(p.id, {
                "pool": p.name or p.id, "passphrases": p.passphrase_count,
                "identities": sum(g.identity_count or 0 for g in p.identity_groups),
                "wlans": [],
            })
            entry["wlans"].append(a.sz_wlan_name)
    counts = [p["passphrases"] for p in pools.values()]
    cat.r1_count = sum(counts) if counts and None not in counts else None
    cat.breakdown = list(pools.values())
    cat.details.append("Passphrases come from Cloudpath, not SZ — compare these totals with the Cloudpath export")
    if not pools:
        cat.details.append("No WLAN authenticates against an R1 DPSK service")
    elif cat.r1_count is None:
        cat.details.append("Some pool counts could not be read")
    return cat


def build_scorecard(
    snapshot: SZMigrationSnapshot,
    inv: R1VenueInventory,
    matching: AuditMatching,
    venue_settings: Optional[VenueSettingsAudit],
    broadcast: BroadcastAudit,
    resolver_result,
    network_items: List[NetworkAuditItem],
    ap_group_audits: List[APGroupActivationAudit],
    auth_items: Optional[List[AuthPathAudit]],
) -> List[AlignmentCategory]:
    return [
        _venue_config(venue_settings),
        _aps(matching, inv, snapshot),
        _ap_groups(matching, inv),
        _ap_membership(matching),
        _wlan_groups(snapshot, inv, broadcast),
        _ssid_broadcast(broadcast),
        _wlans(snapshot, inv, matching, wlan_scopes(snapshot, broadcast)),
        _wlan_config(network_items),
        _passphrases(auth_items),
    ]
