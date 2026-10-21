"""
WLAN Activator: the pure parts.

Put APs into AP Groups and activate each group's WLAN directly on it. The
usual shape is one unit per group:

    AP "101-R550"  ->  AP Group "101"  ->  WLAN "101@Fieldhouse"

Nothing here talks to R1. The suggestion builder takes what the venue and
tenant already hold and guesses the mapping, so the operator starts from a
filled-in list instead of an empty one; the helpers read R1 records the way
the validation and activation phases need them.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from workflow.phases.ap_fields import ap_serial

UNIT = "{unit}"
DEFAULT_WLAN_PATTERN = "{unit}@{property}"
DEFAULT_GROUP_PATTERN = "{unit}"

# R1 allows only WPA3/OWE on 6 GHz (same rule as the SZ migration resolver).
_SIX_GHZ_SECURITY = {"WPA3", "OWE"}
BASE_RADIOS = ["2.4-GHz", "5-GHz"]


# ============================================================================
# Reading R1 records
# ============================================================================

def is_dpsk(network: Dict[str, Any]) -> bool:
    return str(network.get("nwSubType") or "").lower() == "dpsk"


# "907-2.4@Property", "907-5", "Unit 12 5G": a per-band variant of a unit WLAN.
_BAND_SUFFIX = re.compile(r"[-_ ](2\.4|5|6)(?:g|ghz)?$", re.IGNORECASE)
_BAND_RADIO = {"2.4": "2.4-GHz", "5": "5-GHz", "6": "6-GHz"}


def split_band(unit: str) -> Tuple[str, Optional[str]]:
    """("907-2.4") -> ("907", "2.4"); a unit with no band suffix -> (unit, None)."""
    m = _BAND_SUFFIX.search(unit or "")
    if m and m.start() > 0:
        return unit[:m.start()], m.group(1)
    return unit, None


def network_band(network: Dict[str, Any]) -> Optional[str]:
    """The band a network's name/SSID says it is for, before any "@suffix"."""
    for value in (network.get("ssid"), network.get("name")):
        base = (value or "").split("@", 1)[0].strip()
        band = split_band(base)[1]
        if band:
            return band
    return None


def radio_types_for(network: Dict[str, Any]) -> List[str]:
    """
    Radios to activate on: a band-named WLAN ("907-2.4") on that band only;
    otherwise 2.4 + 5, plus 6 GHz for WPA3/OWE networks.
    """
    band = network_band(network)
    if band:
        return [_BAND_RADIO[band]]
    security = str(network.get("securityProtocol") or "").upper()
    if security in _SIX_GHZ_SECURITY:
        return BASE_RADIOS + ["6-GHz"]
    return list(BASE_RADIOS)


def venue_state(network: Dict[str, Any], venue_id: str) -> Tuple[str, Set[str]]:
    """
    ('not_found' | 'specific' | 'venue_wide', bound AP Group ids) in a venue,
    from a /wifiNetworks/query row's venueApGroups.
    """
    for vag in (network or {}).get("venueApGroups") or []:
        if vag.get("venueId") != venue_id:
            continue
        if vag.get("isAllApGroups"):
            return "venue_wide", set()
        ids = set(vag.get("apGroupIds") or [])
        ids |= {g.get("apGroupId") for g in vag.get("apGroups") or [] if g.get("apGroupId")}
        return "specific", ids
    return "not_found", set()


def index_networks(networks: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Dict], Dict[str, List[Dict]]]:
    """(name -> network, ssid -> [networks]). Names are unique in R1; SSIDs are not."""
    by_name: Dict[str, Dict] = {}
    by_ssid: Dict[str, List[Dict]] = {}
    for n in networks:
        if n.get("name"):
            by_name[n["name"]] = n
        if n.get("ssid"):
            by_ssid.setdefault(n["ssid"], []).append(n)
    return by_name, by_ssid


def resolve_network(
    wlan: str,
    by_name: Dict[str, Dict],
    by_ssid: Dict[str, List[Dict]],
) -> Tuple[Optional[Dict], str]:
    """
    The network a CSV "wlan" value means: network name first, then SSID.

    Returns (network, "") or (None, reason). Two networks broadcasting the
    same SSID are not guessed between -- the name has to be used instead.
    """
    if wlan in by_name:
        return by_name[wlan], ""
    matches = by_ssid.get(wlan) or []
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        names = ", ".join(sorted(m.get("name") or m.get("id", "?") for m in matches))
        return None, f"SSID is used by {len(matches)} networks ({names}) - use the network name"
    return None, "no network with this name or SSID"


# ============================================================================
# Suggesting a mapping
# ============================================================================

def pattern_regex(pattern: str, property_name: str = "") -> Optional[re.Pattern]:
    """
    "{unit}@{property}" -> a full-match regex capturing the unit.

    Literal parts match case-insensitively; returns None when the pattern has
    no {unit} to capture.
    """
    pattern = (pattern or "").replace("{property}", property_name or "")
    if UNIT not in pattern:
        return None
    before, _, after = pattern.partition(UNIT)
    return re.compile(
        f"^{re.escape(before)}(?P<unit>.+?){re.escape(after)}$", re.IGNORECASE
    )


def units_from_networks(
    networks: Iterable[Dict[str, Any]],
    wlan_pattern: str,
    property_name: str = "",
) -> Tuple[Dict[str, Dict], Dict[str, List[str]]]:
    """
    unit -> network for every network whose name (or SSID) fits the pattern.

    Returns (units, ambiguous): a unit claimed by more than one network goes
    to `ambiguous` (unit -> network names) instead of being picked from.
    """
    rx = pattern_regex(wlan_pattern, property_name)
    if rx is None:
        return {}, {}
    claims: Dict[str, Dict[str, Dict]] = {}
    for n in networks:
        for value in (n.get("name"), n.get("ssid")):
            m = rx.match(value or "")
            if m and m.group("unit").strip():
                claims.setdefault(m.group("unit").strip(), {})[n.get("id")] = n
                break
    units: Dict[str, Dict] = {}
    ambiguous: Dict[str, List[str]] = {}
    for unit, nets in claims.items():
        if len(nets) == 1:
            units[unit] = next(iter(nets.values()))
        else:
            ambiguous[unit] = sorted(x.get("name") or "" for x in nets.values())
    return units, ambiguous


def _token_in(unit: str, text: str) -> bool:
    """
    `unit` appears in `text` as its own token.

    Neighbouring characters may not continue the unit's edge: a unit starting
    with a digit can't follow a digit, one starting with a letter can't follow
    a letter (same at the end). "101" is in "101-R550", "Unit101" and
    "R550 @101", but not in "1101" or "1015".
    """
    u, t = unit.lower(), text.lower()
    start = 0
    while True:
        i = t.find(u, start)
        if i < 0:
            return False
        j = i + len(u)
        before = t[i - 1] if i > 0 else ""
        after = t[j] if j < len(t) else ""
        ok_before = not before or not (
            (before.isdigit() and u[0].isdigit()) or (before.isalpha() and u[0].isalpha())
        )
        ok_after = not after or not (
            (after.isdigit() and u[-1].isdigit()) or (after.isalpha() and u[-1].isalpha())
        )
        if ok_before and ok_after:
            return True
        start = i + 1


def unit_for_name(name: str, units: Iterable[str]) -> Tuple[Optional[str], List[str]]:
    """
    The unit an AP name contains: the longest one found as a token.

    Returns (unit, []) or (None, tied_units) when two different units of the
    same length both appear -- reported, not guessed.
    """
    found = [u for u in units if _token_in(u, name or "")]
    if not found:
        return None, []
    longest = max(len(u) for u in found)
    top = sorted(u for u in found if len(u) == longest)
    if len(top) > 1:
        return None, top
    return top[0], []


def detect_property_suffix(networks: Iterable[Dict[str, Any]], venue_id: str) -> str:
    """
    The "@suffix" most of this venue's active WLANs share, e.g.
    "Promenade_jersey_village" -- the {property} the unit WLANs use, which
    need not be the venue's name. "" when fewer than two agree.
    """
    tally: Dict[str, int] = {}
    for n in networks:
        if venue_state(n, venue_id)[0] == "not_found":
            continue
        for value in (n.get("name"), n.get("ssid")):
            if value and "@" in value:
                suffix = value.rsplit("@", 1)[1].strip()
                if suffix:
                    tally[suffix] = tally.get(suffix, 0) + 1
                break
    if not tally:
        return ""
    best = max(tally, key=lambda k: tally[k])
    return best if tally[best] >= 2 else ""


def detect_property_wlans(
    networks: List[Dict[str, Any]],
    venue_id: str,
    named_group_ids: Set[str],
    exclude_ids: Set[str],
    share: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Networks bound group-by-group to most of the venue's AP Groups.

    These are the property-wide SSIDs every unit group also carries (the
    resident WiFi, the video SSID...). Networks on All AP Groups reach new
    groups by themselves, so only specific bindings count. Unit WLANs
    (exclude_ids) are left out.
    """
    if not named_group_ids:
        return []
    out = []
    for n in networks:
        if n.get("id") in exclude_ids:
            continue
        state, bound = venue_state(n, venue_id)
        hits = len(bound & named_group_ids)
        if state == "specific" and hits >= 2 and hits >= share * len(named_group_ids):
            out.append({
                "name": n.get("name") or "",
                "ssid": n.get("ssid") or "",
                "groups_bound": hits,
                "vlan": n.get("vlan"),
            })
    return sorted(out, key=lambda w: (-w["groups_bound"], w["name"]))


def build_suggestions(
    aps: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    networks: List[Dict[str, Any]],
    venue_id: str = "",
    wlan_pattern: str = DEFAULT_WLAN_PATTERN,
    group_pattern: str = DEFAULT_GROUP_PATTERN,
    property_name: str = "",
    extra_wlans: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Per UNIT: its WLANs, its AP Group, its APs, and what is still missing.

    Units come from the networks whose name or SSID fits wlan_pattern. A
    band variant ("907-2.4", "907-5") belongs to its base unit ("907"). A
    unit counts whether or not any AP in the venue is named for it -- most
    of a property's groups can be empty while APs are still going in, and
    an unactivated WLAN is work either way.

    A unit's AP Group(s) are, in order: every group its base WLAN is already
    active on (a unit can span several -- "2524_<serial>" x2); the group
    named by group_pattern if it exists; the one existing group named for
    the unit ("703_312072014820"); else a new group.

    WLANs are tenant-level, APs and groups are venue-level: a unit WLAN not
    active in this venue may be active in another venue of the tenant, and
    the row says so rather than calling it unused.

    APs are matched to the unit their name contains (longest token), then
    to the unit whose group they sit in. An AP only needs moving when its
    current group does not carry its unit's WLAN. APs tied to no unit are
    reported separately (an AP named exactly like an AP Group is placed in
    it; that group still gets the property-wide WLANs).

    Every group also carries `extra_wlans` (property-wide WLANs); when None,
    the ones bound to most of the venue's groups are detected and used.
    """
    raw_units, ambiguous_units = units_from_networks(networks, wlan_pattern, property_name)
    group_pattern = group_pattern or DEFAULT_GROUP_PATTERN
    if UNIT not in group_pattern:
        group_pattern = DEFAULT_GROUP_PATTERN

    def group_name(unit: str) -> str:
        return group_pattern.replace(UNIT, unit)

    group_names = {g.get("id"): g.get("name") or "" for g in groups}
    group_ids = {name: gid for gid, name in group_names.items() if name}
    group_by_fold = {name.casefold(): name for name in group_ids}

    # base unit -> its networks, the base WLAN first, then band variants
    unit_nets: Dict[str, List[Dict]] = {}
    for raw in sorted(raw_units, key=lambda u: (split_band(u)[1] is not None, u)):
        base, _ = split_band(raw)
        unit_nets.setdefault(base, []).append(raw_units[raw])

    unit_net_ids = {n.get("id") for nets in unit_nets.values() for n in nets}
    detected = detect_property_wlans(networks, venue_id, set(group_ids.values()), unit_net_ids)
    by_name, _ = index_networks(networks)
    extra_names = [w["name"] for w in detected] if extra_wlans is None else list(extra_wlans)
    extras = [by_name[n] for n in extra_names if n in by_name]

    def bound_groups(n: Dict) -> Set[str]:
        state, bound = venue_state(n, venue_id)
        return bound if state == "specific" else set()

    # ---- each unit's AP Group(s) ----------------------------------------
    unit_group: Dict[str, Tuple[List[str], str]] = {}
    for unit, nets in unit_nets.items():
        active = sorted({group_names.get(g, "") for g in bound_groups(nets[0])} - {""})
        wanted = group_name(unit)
        named = [g for g in group_ids if _token_in(unit, g)]
        if active:
            unit_group[unit] = (active, "" if active == [wanted] else "its WLAN is active there")
        elif wanted in group_ids:
            unit_group[unit] = ([wanted], "")
        elif len(named) == 1:
            unit_group[unit] = (named, "existing AP Group named for the unit")
        else:
            unit_group[unit] = ([wanted], "")
    unit_by_group = {g: u for u, (gs, _) in unit_group.items() for g in gs}

    # ---- APs -> units ---------------------------------------------------
    unit_aps: Dict[str, List[Dict[str, Any]]] = {u: [] for u in unit_nets}
    other_aps: List[Dict[str, Any]] = []
    for ap in sorted(aps, key=lambda a: (a.get("name") or "", ap_serial(a))):
        name = ap.get("name") or ""
        current_id = ap.get("apGroupId") or ap.get("ap_group_id") or ""
        current = group_names.get(current_id, "")
        entry = {"ap_serial": ap_serial(ap), "ap_name": name, "current_ap_group": current,
                 "current_ap_group_id": current_id}
        unit, tied = unit_for_name(name, unit_nets)
        if not unit and not tied and current in unit_by_group:
            unit = unit_by_group[current]
        if unit:
            unit_aps[unit].append(entry)
            continue
        if tied:
            entry.update(status="ambiguous", note=f"name fits units {', '.join(tied)}", ap_group="")
        elif name.strip().casefold() in group_by_fold:
            entry.update(status="group_only", note="AP name matches an AP Group; no unit WLAN",
                         ap_group=group_by_fold[name.strip().casefold()])
        else:
            entry.update(status="no_match", note="no unit found in the AP name", ap_group="")
        other_aps.append(entry)

    def wlan_todo(gids: List[Optional[str]], nets: List[Dict], where: str = "") -> List[str]:
        todo = []
        for n in nets:
            label = n.get("name") or n.get("ssid") or "?"
            state, bound = venue_state(n, venue_id)
            if state == "venue_wide":
                todo.append(f"{label} is on All AP Groups")
                continue
            missing = [g for g in gids if not g or g not in bound]
            if not missing:
                continue
            other_venues = {v.get("venueId") for v in n.get("venueApGroups") or []} - {venue_id, None}
            note = f" (active in {len(other_venues)} other venue(s))" if state == "not_found" and other_venues else ""
            on = f" on {len(missing)} of {len(gids)} groups" if len(gids) > 1 else ""
            todo.append(f"activate {label}{on}{note}")
        return todo

    # ---- unit rows ------------------------------------------------------
    unit_rows: List[Dict[str, Any]] = []
    for unit in sorted(unit_nets, key=lambda u: (len(u), u)):
        nets = unit_nets[unit]
        unit_groups, why = unit_group[unit]
        gids = [group_ids.get(g) for g in unit_groups]
        base_net_ids = {nets[0].get("id")}
        todo = [f"create AP Group {g}" for g, gid in zip(unit_groups, gids) if not gid]
        wlans = nets + [e for e in extras if e.get("id") not in {n.get("id") for n in nets}]
        todo += wlan_todo(gids, wlans)
        unit_ap_rows = []
        for ap in unit_aps[unit]:
            in_place = ap["current_ap_group"] in unit_groups
            # An AP in another group that already carries its unit WLAN is
            # serving the unit; moving it would only be churn.
            carried = any(ap["current_ap_group_id"] in bound_groups(n) for n in nets if n.get("id") in base_net_ids)
            move = not in_place and not carried
            if move:
                todo.append(f"move {ap['ap_name'] or ap['ap_serial']} (now in {ap['current_ap_group'] or 'default'})")
            unit_ap_rows.append({**ap, "move": move,
                                 "note": "" if in_place or move else f"stays in {ap['current_ap_group']} (carries the WLAN)"})
        unit_rows.append({
            "unit": unit,
            "ap_group": unit_groups[0],
            "ap_groups": unit_groups,
            "group_exists": all(gids),
            "group_note": why,
            "wlans": [n.get("name") or "" for n in nets],
            "network_vlan": nets[0].get("vlan"),
            "aps": unit_ap_rows,
            "todo": todo,
            "done": not todo,
        })

    # ---- APs tied to no unit --------------------------------------------
    for ap in other_aps:
        gid = group_ids.get(ap["ap_group"])
        todo = []
        if ap["ap_group"]:
            if ap["current_ap_group"] != ap["ap_group"]:
                todo.append(f"move AP (now in {ap['current_ap_group'] or 'default'})")
            todo += wlan_todo([gid], extras)
        ap["todo"] = todo
        ap["done"] = bool(ap["ap_group"]) and not todo

    counts = {
        "units": len(unit_rows),
        "units_done": sum(1 for u in unit_rows if u["done"]),
        "units_todo": sum(1 for u in unit_rows if not u["done"]),
        "units_without_aps": sum(1 for u in unit_rows if not u["aps"]),
        "aps": len(aps),
        "aps_in_units": sum(len(u["aps"]) for u in unit_rows),
        "other_aps": len(other_aps),
        "other_aps_todo": sum(1 for a in other_aps if not a["done"]),
    }

    return {
        "units": unit_rows,
        "other_aps": other_aps,
        "ambiguous_units": [
            {"unit": u, "networks": names} for u, names in sorted(ambiguous_units.items())
        ],
        "property_wlans": detected,
        "extra_wlans": [n.get("name") for n in extras],
        "unknown_extra_wlans": [n for n in extra_names if n not in by_name],
        "counts": counts,
    }
