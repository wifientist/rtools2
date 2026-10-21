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
    NetworkAuditItem,
)
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot

SZ_DEFAULT_WLAN_GROUP = "default"
# Key WLAN settings for the scorecard row; the detail table shows everything.
_KEY_FIELD_LABELS = {"VLAN ID": "VLAN", "Security Mode": "Security", "Mgmt Frame Protection": "MFP"}
_KEY_DIFF_FIELDS = {"ssid": "SSID", "network_type": "Type", "vlan_id": "VLAN"}


def _status(aligned: int, total: int) -> str:
    if total == 0 or aligned == total:
        return "aligned"
    return "misaligned" if aligned == 0 else "partial"


def _venue_config() -> AlignmentCategory:
    return AlignmentCategory(
        key="venue_config", label="Zone ↔ Venue settings",
        details=["Radio, channel, OFDM-only and other zone-level settings — arriving later in this checkpoint"],
    )


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


def _wlan_groups(snapshot: SZMigrationSnapshot, ap_group_audits: List[APGroupActivationAudit]) -> AlignmentCategory:
    # Where each WLAN Group is applied: AP Group radio overrides, else the zone default group.
    usage: Dict[str, Set[str]] = {g.id: set() for g in snapshot.wlan_groups}
    default_id = next((g.id for g in snapshot.wlan_groups if g.name == SZ_DEFAULT_WLAN_GROUP), None)
    for apg in snapshot.ap_groups:
        rc = apg.radio_config.model_dump() if apg.radio_config else {}
        for band, label in (("radio_24g", "2.4"), ("radio_5g", "5"), ("radio_6g", "6")):
            wg = rc.get(band) or default_id
            if wg in usage and (apg.ap_count or 0) > 0:
                usage[wg].add(f"{apg.name} ({label})")
    covered = sum(1 for a in ap_group_audits if a.r1_ap_group_found and not a.missing_ssids)
    judged = sum(1 for a in ap_group_audits if a.r1_ap_group_found)
    return AlignmentCategory(
        key="wlan_groups", label="WLAN Groups (SZ only)",
        sz_count=len(snapshot.wlan_groups), r1_count=None, aligned_count=covered,
        status="pending" if judged == 0 else _status(covered, judged),
        details=[
            "R1 has no WLAN Groups: each becomes SSID activations on the R1 AP Groups it applies to.",
            f"{covered} of {judged} paired AP Group(s) carry every SSID their SZ WLAN Groups put there",
            "Per-AP WLAN Group overrides are compared in checkpoint 2",
        ],
        breakdown=[
            {"wlan_group": g.name, "members": len(g.members),
             "applied_to": sorted(usage.get(g.id, set())) or ["(not applied to any AP Group with APs)"]}
            for g in snapshot.wlan_groups
        ],
    )


def wlan_scopes(resolver_result) -> Dict[str, str]:
    """wlan_id -> property (every AP Group with APs) / unit (some) / none."""
    groups_with_aps = {s.ap_group_name for s in resolver_result.ap_group_summaries if s.ap_count > 0}
    on: Dict[str, Set[str]] = {}
    for a in resolver_result.activations:
        if a.ap_count > 0:
            on.setdefault(a.wlan_id, set()).add(a.ap_group_name)
    out = {}
    for wid, groups in on.items():
        out[wid] = "property" if groups_with_aps and groups >= groups_with_aps else "unit"
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
            if fc.label in _KEY_FIELD_LABELS and not fc.match and not fc.sz_only:
                bad.add(_KEY_FIELD_LABELS[fc.label])
        for f in bad:
            per_field.setdefault(f, []).append(i.sz_wlan_name)
        clean += not bad
    return AlignmentCategory(
        key="wlan_config", label="WLAN settings (VLAN, type, security)",
        sz_count=len(compared), r1_count=len(compared), aligned_count=clean,
        status=_status(clean, len(compared)),
        details=["Per-AP-Group VLAN overrides and the full security set are compared in checkpoint 3"],
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
    resolver_result,
    network_items: List[NetworkAuditItem],
    ap_group_audits: List[APGroupActivationAudit],
    auth_items: Optional[List[AuthPathAudit]],
) -> List[AlignmentCategory]:
    return [
        _venue_config(),
        _aps(matching, inv, snapshot),
        _ap_groups(matching, inv),
        _ap_membership(matching),
        _wlan_groups(snapshot, ap_group_audits),
        _wlans(snapshot, inv, matching, wlan_scopes(resolver_result)),
        _wlan_config(network_items),
        _passphrases(auth_items),
    ]
