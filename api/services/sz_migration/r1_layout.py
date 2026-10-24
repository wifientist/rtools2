"""
R1 placement for a SmartZone zone: which WLANs go on All AP Groups, which
R1 AP Groups the rest need, and which WLANs each group carries -- so every
AP ends up broadcasting the same WLANs in R1 as it does in SZ.

Built from the outcome, not from SZ's AP Groups. SZ decides per AP and per
radio (AP override > AP Group override > zone > the "default" WLAN Group,
broadcast_model); R1 decides per AP Group, with "All AP Groups" on top. So:

  1. Work out each AP's SZ broadcast: WLAN -> radios.
  2. "Property-wide" WLANs: broadcast by at least PROPERTY_WIDE_SHARE of the
     APs that broadcast anything. Suggested for All AP Groups; the operator
     confirms (or picks others). The APs that would newly get one in R1 are
     listed.
  3. Strip those from every AP; APs left with the same WLAN set form one R1
     AP Group. An empty set means the venue's default AP Group -- nothing to
     activate there beyond All AP Groups.
  4. Match each group to an existing R1 AP Group: first by content -- an R1
     group whose specific activations are exactly this group's SSIDs is the
     same group, whatever it's called (venues already migrated name them
     "1326@Site" or "10101", SZ says "11011-1326") -- then by name.
     Otherwise it is a candidate the operator ticks to create, named after
     what SZ used for those APs: their shared AP-level WLAN Group (the
     per-unit pattern), else their shared SZ AP Group, else their single
     WLAN. A WLAN aimed at a group that neither exists nor is created is not
     activated there.
  5. 15 SSIDs per AP Group: group WLANs + All AP Groups WLANs (incl. ones
     already on All AP Groups in the R1 venue).

WLANs no AP broadcasts (unused WLAN Groups, Always Off schedules) are the
operator's call, per WLAN: skip (default), create (WLAN only), or create +
stage -- the WLAN is created and its AP Group built and filled with the APs
it pairs with, but nothing is activated. Pairing: an SZ WLAN Group holding
the WLAN whose name is an AP's name (sites where unit overrides were backed
out keep "12103" groups beside AP "12103"), or a name pattern. Only APs
that broadcast just the All AP Groups set (or nothing) can be staged -- an
AP in a group derived from its SZ broadcast already has a group -- so
staging never changes what an AP broadcasts.

AP membership is not done here: APs join their group later, by whatever
loads them. The layout carries each group's APs for that.

Pure: snapshot + R1 inventory + choices in, plain dicts out.
"""

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Dict, FrozenSet, List, Optional, Set

from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot
from services.sz_migration.ap_group_plan import r1_radio_types
from services.sz_migration.broadcast_model import _SzModel, r1_ssids_by_group

PROPERTY_WIDE_SHARE = 0.9
R1_SSID_LIMIT = 15
SAMPLE = 8

# broadcast_model radio key -> ap_group_plan band label
_BAND = {"radio24g": "2.4", "radio5g": "5", "radio5gLower": "5L", "radio5gUpper": "5U", "radio6g": "6"}
_SZ_DEFAULT_AP_GROUP = "default"


def group_key(wlan_ids: FrozenSet[str]) -> str:
    """Stable id for a WLAN set: survives re-extraction while the set is the same."""
    return hashlib.sha1("|".join(sorted(wlan_ids)).encode()).hexdigest()[:12]


def r1_group_name(name: str) -> str:
    """R1 AP Group names: 2-64 chars, no leading/trailing space, no ` or $(."""
    n = re.sub(r"`|\$\(", "", name or "").strip()[:64].strip()
    return n if len(n) >= 2 else f"{n or 'AP'}-group"


def _ap_label(ap: Dict[str, Any]) -> str:
    return ap.get("deviceName") or ap.get("serial") or ap.get("apMac") or "?"


def build_layout(snapshot: SZMigrationSnapshot, inv: Optional[R1VenueInventory],
                 choices: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    choices (all optional):
      all_ap_wlans: [wlan id]        None = the suggestion
      create_groups: [group key]     candidates to create
      group_names: {key: name}       renames
      include_unbroadcast: [wlan id] migrate (create, no activation)
      stage_unbroadcast: [wlan id]   create + build/fill its paired AP Group, no activation
      pairing: {mode: "wlan_group" | "pattern", wlan_pattern, ap_pattern}
                                     how a staged WLAN finds its APs ({unit} in both patterns)
    """
    choices = choices or {}
    sz = _SzModel(snapshot)
    wlan_by_id = {w.id: w for w in snapshot.wlans}
    wlg_name = {g.id: g.name for g in snapshot.wlan_groups}
    sz_apg_name = {g.id: g.name for g in snapshot.ap_groups}

    # ── 1. SZ broadcast per AP ─────────────────────────────────────
    aps: List[Dict[str, Any]] = []
    tiers: Counter = Counter()
    unknown = 0
    for raw in snapshot.aps:
        wlans, _, known, winners = sz.wlan_bands(raw)
        unknown += 0 if known else 1
        tiers.update(t for t, _ in winners)
        overrides = {gid for t, gid in winners if t == "AP override"}
        aps.append({
            "raw": raw,
            "wlans": {w: {_BAND[b] for b in bands} for w, bands in wlans.items()},
            "override": wlg_name.get(next(iter(overrides))) if len(overrides) == 1 else None,
            "sz_ap_group": sz_apg_name.get(raw.get("apGroupId")),
        })
    active = [a for a in aps if a["wlans"]]

    coverage: Dict[str, Set[int]] = defaultdict(set)
    bands_all: Dict[str, Set[str]] = defaultdict(set)
    for i, a in enumerate(active):
        for w, b in a["wlans"].items():
            coverage[w].add(i)
            bands_all[w] |= b

    # ── 2. Property-wide ───────────────────────────────────────────
    n_active = len(active)
    suggested = {w for w, s in coverage.items() if n_active and len(s) / n_active >= PROPERTY_WIDE_SHARE}
    chosen = choices.get("all_ap_wlans")
    all_ap = set(suggested if chosen is None else chosen) & set(coverage)

    # ── 3. Residual sets -> groups ────────────────────────────────
    clusters: Dict[FrozenSet[str], List[Dict[str, Any]]] = defaultdict(list)
    for a in active:
        clusters[frozenset(w for w in a["wlans"] if w not in all_ap)].append(a)
    default_aps = clusters.pop(frozenset(), [])

    existing_all_ap = set()
    if inv is not None:
        mig_ssids = {w.ssid for w in snapshot.wlans}
        for net in inv.wifi_networks:
            for vag in net.get("venueApGroups") or []:
                if vag.get("venueId") in (None, inv.venue_id) and vag.get("isAllApGroups") \
                        and net.get("ssid") not in mig_ssids:
                    existing_all_ap.add(net.get("ssid"))
    all_ap_ssids = {wlan_by_id[w].ssid for w in all_ap} | existing_all_ap

    # R1 groups by the SSIDs activated on them specifically (not All AP Groups)
    r1_by_ssids: Dict[FrozenSet[str], List[Dict[str, Any]]] = defaultdict(list)
    unlisted: Set[str] = set()
    if inv is not None:
        by_group, _ = r1_ssids_by_group(inv)
        # Networks activated on groups the inventory doesn't list: the AP
        # Group capture is incomplete (it once stopped at 25), and matching
        # against it would plan duplicates of groups that exist.
        unlisted = set(by_group) - {g.get("id") for g in inv.ap_groups}
        for g in inv.ap_groups:
            if g.get("id") in by_group and not g.get("isDefault"):
                r1_by_ssids[frozenset(by_group[g["id"]])].append(g)
    claimed: Set[str] = set()

    create = set(choices.get("create_groups") or [])
    renames = choices.get("group_names") or {}
    groups: List[Dict[str, Any]] = []
    used_names: Counter = Counter()
    for wset, members in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0]))):
        key = group_key(wset)
        overrides = {m["override"] for m in members}
        sz_groups = {m["sz_ap_group"] for m in members}
        if len(overrides) == 1 and None not in overrides:
            base, source = overrides.pop(), "SZ AP-level WLAN Group"
        elif len(sz_groups) == 1 and None not in sz_groups and _SZ_DEFAULT_AP_GROUP not in sz_groups:
            base, source = sz_groups.pop(), "SZ AP Group"
        elif len(wset) == 1:
            base, source = wlan_by_id[next(iter(wset))].name, "its only WLAN"
        else:
            base, source = "Migrated group", "generated"
        ssids = sorted({wlan_by_id[w].ssid for w in wset})
        r1, match = None, None
        same = [g for g in r1_by_ssids.get(frozenset(ssids), []) if g["id"] not in claimed]
        if len(same) == 1:
            r1, match = same[0], "SSIDs"
            name = r1.get("name") or base
        else:
            name = r1_group_name(renames.get(key) or base)
            used_names[name] += 1
            if used_names[name] > 1:
                name = r1_group_name(f"{name[:60]}-{used_names[name]}")
            r1 = inv.ap_group_by_name(name) if inv is not None else None
            if r1 is not None and r1["id"] in claimed:
                r1 = None
            match = "name" if r1 else None
        if r1 is not None:
            claimed.add(r1["id"])
        total = len(set(ssids) | all_ap_ssids)
        # Radios per WLAN for THIS group's APs: a WLAN on 5 GHz here and on
        # 2.4+5 elsewhere is activated with each group's own radios.
        radios = {}
        for w in wset:
            bands = set().union(*(m["wlans"].get(w, set()) for m in members))
            radios[w] = r1_radio_types(sorted(bands), wlan_by_id[w].encryption)[0]
        groups.append({
            "key": key, "name": name, "name_source": source, "renamed": key in renames,
            "wlan_ids": sorted(wset), "ssids": ssids,
            "ap_count": len(members),
            "aps": [{"name": _ap_label(m["raw"]), "serial": m["raw"].get("serial"),
                     "mac": m["raw"].get("apMac")} for m in members],
            "r1_id": r1.get("id") if r1 else None,
            "exists": r1 is not None,
            "matched_by": match,
            "create": r1 is None and key in create,
            "ssid_total": total, "over_limit": total > R1_SSID_LIMIT,
            "radio_types": radios,
        })

    # ── WLAN rows ──────────────────────────────────────────────────
    target_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for g in groups:
        for w in g["wlan_ids"]:
            target_groups[w].append(g)

    wlans_out: List[Dict[str, Any]] = []
    warnings: List[Dict[str, str]] = []

    def warn(message: str, severity: str = "warning"):
        warnings.append({"message": message, "severity": severity})

    include = set(choices.get("include_unbroadcast") or [])
    stage = set(choices.get("stage_unbroadcast") or [])
    unbroadcast: List[Dict[str, Any]] = []
    always_off = {w.id for w in snapshot.wlans if w.id not in sz.ssid_by_wlan}
    for w in snapshot.wlans:
        if w.id not in coverage:
            mode = "stage" if w.id in stage else "create" if w.id in include else "skip"
            unbroadcast.append({"id": w.id, "name": w.name, "ssid": w.ssid, "auth_type": w.auth_type,
                                "reason": "Scheduled Always Off" if w.id in always_off
                                else "In no WLAN Group any AP uses",
                                "mode": mode, "included": mode != "skip", "paired_aps": [],
                                "paired_group": None})
            continue
        radio_types, radio_notes = r1_radio_types(sorted(bands_all[w.id]), w.encryption)
        row = {
            "id": w.id, "name": w.name, "ssid": w.ssid, "auth_type": w.auth_type,
            "sz_ap_count": len(coverage[w.id]),
            "share": round(len(coverage[w.id]) / n_active, 4) if n_active else 0,
            "suggested_all_ap": w.id in suggested,
            "mode": "all_ap_groups" if w.id in all_ap else "ap_groups",
            "radio_types": radio_types, "radio_notes": radio_notes,
        }
        if w.id in all_ap:
            missing = [a for i, a in enumerate(active) if i not in coverage[w.id]]
            row["gains_in_r1"] = len(missing) + (len(aps) - n_active)
            row["gains_sample"] = [_ap_label(a["raw"]) for a in missing[:SAMPLE]]
        else:
            tg = target_groups[w.id]
            row["targets"] = [g["key"] for g in tg]
            row["targets_ready"] = sum(1 for g in tg if g["exists"] or g["create"])
            if row["targets_ready"] < len(tg):
                pending = len(tg) - row["targets_ready"]
                row["unready_note"] = (f"{pending} of {len(tg)} target AP Group(s) neither exist nor are "
                                       "marked to create; not activated there")
        wlans_out.append(row)
    wlans_out.sort(key=lambda r: (-r["share"], r["name"].lower()))

    # ── Staged WLANs: paired APs, one group per AP set ─────────────
    # Every WLAN no AP broadcasts gets its candidate per-unit group (so the
    # operator can tick it); only the ticked (stage) ones are built.
    staged = _stage(snapshot, unbroadcast, default_aps,
                    [a for a in aps if not a["wlans"]], choices.get("pairing") or {},
                    inv, claimed, renames, all_ap_ssids, r1_by_ssids)
    staged_serials = {ap["serial"] for g in staged["groups"] if g["chosen"] for ap in g["aps"]}
    default_aps = [a for a in default_aps if a["raw"].get("serial") not in staged_serials]

    default_total = len(all_ap_ssids)
    if default_total > R1_SSID_LIMIT:
        warn(f"{default_total} SSIDs on All AP Groups — over R1's {R1_SSID_LIMIT} per AP Group everywhere", "error")
    over = [g["name"] for g in groups if g["over_limit"]]
    if over:
        warn(f"{len(over)} AP Group(s) over {R1_SSID_LIMIT} SSIDs: {', '.join(over[:SAMPLE])}", "error")
    if unknown:
        warn(f"{unknown} AP(s) had no readable per-AP radio config: their AP-level overrides are "
                        "unknown, so their placement may be wrong — re-extract")
    if existing_all_ap:
        warn(f"Already on All AP Groups in R1 (not part of this migration, counted toward the limit): "
                        f"{', '.join(sorted(existing_all_ap))}")
    if unlisted:
        warn(f"The R1 snapshot lists {len(inv.ap_groups)} AP Groups, but networks are activated on "
             f"{len(unlisted)} more it doesn't list — recapture R1 state on Destination before planning, "
             "or existing groups would be created again", "error")
    if inv is None:
        warn("No R1 venue snapshot: existing AP Groups can't be matched — capture it on Destination")

    return {
        "summary": {
            "aps": len(aps), "aps_broadcasting": n_active, "aps_default_group": len(default_aps),
            "property_wide_share": PROPERTY_WIDE_SHARE,
            "sz_tiers": dict(tiers),
            "groups": len(groups),
            "groups_existing": sum(1 for g in groups if g["exists"]),
            "groups_to_create": sum(1 for g in groups if g["create"]),
            "groups_pending": sum(1 for g in groups if not g["exists"] and not g["create"]),
            "all_ap_wlans": len(all_ap),
            "staged_wlans": sum(1 for u in unbroadcast if u["mode"] == "stage"),
            "staged_groups": sum(1 for g in staged["groups"] if g["chosen"]),
            "staged_aps": len(staged_serials),
            "staged_candidates": len(staged["groups"]),
        },
        "default_group": {"ap_count": len(default_aps), "ssid_total": default_total,
                          "over_limit": default_total > R1_SSID_LIMIT,
                          # APs whose SZ broadcast is exactly the All AP Groups set
                          "aps": [{"name": _ap_label(a["raw"]), "serial": a["raw"].get("serial"),
                                   "mac": a["raw"].get("apMac")} for a in default_aps]},
        # APs that broadcast nothing in SZ (no WLAN Group reaches them)
        "silent_aps": [{"name": _ap_label(a["raw"]), "serial": a["raw"].get("serial"),
                        "mac": a["raw"].get("apMac")} for a in aps
                       if not a["wlans"] and a["raw"].get("serial") not in staged_serials],
        "staged": staged,
        "wlans": wlans_out,
        "groups": groups,
        "unbroadcast": unbroadcast,
        "warnings": warnings,
        "blocked": default_total > R1_SSID_LIMIT or bool(over) or bool(unlisted),
    }


def _pattern_regex(template: str) -> Optional["re.Pattern"]:
    """'{unit}@Site' -> ^(?P<unit>.+?)@Site$ ; None without exactly one {unit}."""
    if not template or template.count("{unit}") != 1:
        return None
    return re.compile("^" + re.escape(template).replace(re.escape("{unit}"), "(?P<unit>.+?)") + "$", re.I)


def _stage(snapshot: SZMigrationSnapshot, rows: List[Dict[str, Any]], default_aps: List[Dict[str, Any]],
           silent_aps: List[Dict[str, Any]], pairing: Dict[str, Any], inv: Optional[R1VenueInventory],
           claimed: Set[str], renames: Dict[str, str], all_ap_ssids: Set[str],
           r1_by_ssids: Dict[FrozenSet[str], List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Candidate per-unit groups for WLANs no AP broadcasts. Each pairs with APs
    by name; WLANs pairing with the same APs share a group. A group is
    "chosen" (built) when any of its WLANs is staged. Fills every row's
    paired_aps / paired_group, so the WLAN -> AP Group pairing shows whatever
    the choice. unpaired lists only staged WLANs that pair with no AP.
    """
    out: Dict[str, Any] = {"groups": [], "unpaired": [], "conflicts": [], "mode": pairing.get("mode") or "wlan_group"}
    if not rows:
        return out
    eligible = {}
    for a in default_aps + silent_aps:
        label = _ap_label(a["raw"]).strip().casefold()
        if a["raw"].get("serial"):
            eligible.setdefault(label, []).append(a)
    all_names = {_ap_label(a["raw"]).strip().casefold() for a in [*default_aps, *silent_aps]}
    sz_named = {_ap_label(r).strip().casefold() for r in snapshot.aps} - all_names   # APs with an SZ-derived group
    wlan_by_id = {w.id: w for w in snapshot.wlans}
    groups_of = defaultdict(list)
    for g in snapshot.wlan_groups:
        for m in g.members:
            groups_of[m.id].append(g.name)
    wlan_re, ap_tpl = _pattern_regex(pairing.get("wlan_pattern") or ""), pairing.get("ap_pattern") or "{unit}"

    row_by_id = {r["id"]: r for r in rows}
    pairs: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        w = wlan_by_id[row["id"]]
        if out["mode"] == "pattern":
            m = wlan_re.match(w.name) or wlan_re.match(w.ssid or "") if wlan_re else None
            names = [ap_tpl.replace("{unit}", m.group("unit"))] if m else []
        else:
            names = groups_of.get(w.id, [])
        found = [a for n in names for a in eligible.get(n.strip().casefold(), [])]
        blocked = [n for n in names if n.strip().casefold() in sz_named]
        if blocked and row["mode"] == "stage":
            out["conflicts"].append(f"{w.name}: AP {', '.join(blocked[:3])} already has an AP Group from its SZ "
                                    "broadcast — not staged there")
        if not found:
            if row["mode"] == "stage":
                out["unpaired"].append(w.name)
            continue
        pairs[w.id] = found

    # An AP pairing into two different AP sets can't be in both groups.
    sets: Dict[str, FrozenSet[str]] = {wid: frozenset(a["raw"]["serial"] for a in found) for wid, found in pairs.items()}
    holders: Dict[str, Set[FrozenSet[str]]] = defaultdict(set)
    for st in sets.values():
        for serial in st:
            holders[serial].add(st)
    torn = {serial for serial, h in holders.items() if len(h) > 1}
    if torn and any(r["mode"] == "stage" for r in rows if r["id"] in pairs):
        names = sorted({_ap_label(a["raw"]) for found in pairs.values() for a in found if a["raw"]["serial"] in torn})
        out["conflicts"].append(f"{len(torn)} AP(s) pair with WLANs that pair with different APs: "
                                f"{', '.join(names[:6])} — not staged")

    by_set: Dict[FrozenSet[str], List[str]] = defaultdict(list)
    for wid, st in sets.items():
        st = frozenset(st - torn)
        if st:
            by_set[st].append(wid)
        elif row_by_id[wid]["mode"] == "stage":
            out["unpaired"].append(wlan_by_id[wid].name)
    ap_by_serial = {a["raw"]["serial"]: a for found in pairs.values() for a in found}
    for st, wids in sorted(by_set.items(), key=lambda kv: sorted(wlan_by_id[w].name for w in kv[1])):
        key = "stage-" + group_key(st)
        first = wlan_by_id[sorted(wids, key=lambda w: wlan_by_id[w].name)[0]]
        shared = sorted(set.intersection(*(set(groups_of.get(w, [])) for w in wids))) if out["mode"] != "pattern" else []
        base = shared[0] if shared else first.name
        ssids = sorted({wlan_by_id[w].ssid for w in wids})
        chosen = any(row_by_id[w]["mode"] == "stage" for w in wids)
        r1 = None
        live = [g for g in r1_by_ssids.get(frozenset(ssids), []) if g["id"] not in claimed]
        if len(live) == 1:
            r1, match = live[0], "SSIDs"
            name = r1.get("name") or base
        else:
            name = r1_group_name(renames.get(key) or base)
            r1 = inv.ap_group_by_name(name) if inv is not None else None
            if r1 is not None and r1["id"] in claimed:
                r1 = None
            match = "name" if r1 else None
        if r1 is not None:
            claimed.add(r1["id"])
        members = [ap_by_serial[s] for s in sorted(st)]
        for w in wids:
            row_by_id[w]["paired_aps"] = [_ap_label(a["raw"]) for a in members]
            row_by_id[w]["paired_group"] = name
        out["groups"].append({
            "key": key, "name": name, "name_source": "SZ WLAN Group" if shared else "WLAN name",
            "renamed": key in renames, "staged": True,
            "wlan_ids": sorted(wids), "ssids": ssids, "ap_count": len(members),
            "aps": [{"name": _ap_label(a["raw"]), "serial": a["raw"].get("serial"), "mac": a["raw"].get("apMac")}
                    for a in members],
            "r1_id": r1.get("id") if r1 else None, "exists": r1 is not None, "matched_by": match,
            "chosen": chosen, "create": chosen and r1 is None,
            "ssid_total": len(all_ap_ssids), "over_limit": False, "radio_types": {},
        })
    return out


def wlan_activations(layout: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Per migrated WLAN, what activate_ssids does:
      {"mode": "all_ap_groups", "radio_types": [...]}
      {"mode": "ap_groups", "targets": [{r1_ap_group_id, r1_ap_group_name, r1_ap_group_create, r1_radio_types}],
       "skipped_groups": [name]}
      {"mode": "none"}  (included un-broadcast WLANs: created only)
    """
    groups = {g["key"]: g for g in layout["groups"]}
    out: Dict[str, Dict[str, Any]] = {}
    for w in layout["wlans"]:
        if w["mode"] == "all_ap_groups":
            out[w["id"]] = {"mode": "all_ap_groups", "radio_types": w["radio_types"]}
            continue
        targets, skipped = [], []
        for k in w.get("targets", []):
            g = groups[k]
            if g["exists"] or g["create"]:
                targets.append({"r1_ap_group_id": g["r1_id"], "r1_ap_group_name": g["name"],
                                "r1_ap_group_create": g["create"],
                                "r1_radio_types": (g.get("radio_types") or {}).get(w["id"]) or w["radio_types"]})
            else:
                skipped.append(g["name"])
        out[w["id"]] = {"mode": "ap_groups", "targets": targets, "skipped_groups": skipped}
    for u in layout["unbroadcast"]:
        if u["included"]:
            out[u["id"]] = {"mode": "none"}
    return out
