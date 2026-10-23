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
    """
    ("907-2.4") -> ("907", "2.4"); no band-looking suffix -> (unit, None).

    Only the SHAPE: "1400-5" also splits to ("1400", "5"), though at most
    properties that is unit 1400-5. Use band_units / network_band, which
    decide whether the suffix really is a band.
    """
    m = _BAND_SUFFIX.search(unit or "")
    if m and m.start() > 0:
        return unit[:m.start()], m.group(1)
    return unit, None


def band_units(raw_units: Iterable[str]) -> Dict[str, Tuple[str, Optional[str]]]:
    """
    raw unit -> (base unit, band or None), deciding which suffixes are bands.

    "-2.4" is always a band. "-5" / "-6" are only a band when the same base
    also has a "-2.4" variant (907-2.4 + 907-5); otherwise they are part of
    the unit's own name. At a property whose units run 1400-1 .. 1400-12,
    reading 1400-5 as "unit 1400, 5 GHz" would merge two units and pin one
    of them to a single radio.
    """
    raw = list(raw_units)
    split = {u: split_band(u) for u in raw}
    has_24 = {base for base, band in split.values() if band == "2.4"}
    return {
        u: (base, band) if band == "2.4" or (band and base in has_24) else (u, None)
        for u, (base, band) in split.items()
    }


def network_band(network: Dict[str, Any], ssids: Optional[Set[str]] = None) -> Optional[str]:
    """
    The band a network's SSID/name says it is for, from the part before "@".

    Same rule as band_units: "-2.4" always; "-5" / "-6" only when `ssids`
    (the tenant's SSIDs) holds the matching "-2.4" sibling.
    """
    for value in (network.get("ssid"), network.get("name")):
        left, at, right = (value or "").partition("@")
        base, band = split_band(left.strip())
        if band == "2.4":
            return band
        if band and ssids and any(
            split_band(s.partition("@")[0].strip()) == (base, "2.4") and s.partition("@")[2] == right
            for s in ssids
        ):
            return band
    return None


def radio_types_for(network: Dict[str, Any], ssids: Optional[Set[str]] = None) -> List[str]:
    """
    Radios to activate on: a band WLAN ("907-2.4") on that band only (see
    network_band); otherwise 2.4 + 5, plus 6 GHz for WPA3/OWE networks.
    """
    band = network_band(network, ssids)
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

_PLACEHOLDER = re.compile(r"\{(\w+)\}|\*")


def pattern_regex(pattern: str, property_name: str = "") -> Optional[re.Pattern]:
    """
    A WLAN pattern -> a full-match regex.

        {unit}        the unit (required)
        {property}    filled in from property_name
        {anything}    another captured field, e.g. {building}
        *             a wildcard, also captured

    "Manor_{building}-{unit}@{property}" and "Manor_*-{unit}@{property}" both
    read "Manor_East-221@Penn_state" as building East, unit 221. Extra
    fields are part of the unit's identity (see match_unit), so East-221 and
    West-221 stay two units. Literal parts match case-insensitively; returns
    None when the pattern has no {unit}.
    """
    pattern = (pattern or "").replace("{property}", property_name or "")
    if UNIT not in pattern:
        return None
    out, pos, seen, wild = ["^"], 0, set(), 0
    for m in _PLACEHOLDER.finditer(pattern):
        out.append(re.escape(pattern[pos:m.start()]))
        pos = m.end()
        if m.group(0) == "*":
            wild += 1
            out.append(f"(?P<w{wild}>.+?)")
            continue
        name = "unit" if m.group(1) == "unit" else f"f_{m.group(1)}"
        out.append(f"(?P={name})" if name in seen else f"(?P<{name}>.+?)")
        seen.add(name)
    out.append(re.escape(pattern[pos:]) + "$")
    return re.compile("".join(out), re.IGNORECASE)


def match_unit(m: re.Match) -> Tuple[str, Dict[str, str]]:
    """
    (unit key, fields) for a pattern match.

    With only {unit}, the key is the unit itself -- exactly as before. With
    extra fields, the key is the stretch of the SSID from the first field to
    the last ("East-221"), which keeps units unique when only a field tells
    them apart. fields maps "unit" and each extra field ("building", "*1",
    "*2" for wildcards) to its value.
    """
    fields: Dict[str, str] = {}
    spans = []
    for gname, value in m.groupdict().items():
        if value is None:
            continue
        key = "unit" if gname == "unit" else ("*" + gname[1:] if gname.startswith("w") else gname[2:])
        fields[key] = value.strip()
        spans.append(m.span(gname))
    if len(fields) == 1:
        return fields.get("unit", ""), fields
    start, end = min(a for a, _ in spans), max(b for _, b in spans)
    return m.string[start:end].strip(), fields


def units_with_fields(
    networks: Iterable[Dict[str, Any]],
    wlan_pattern: str,
    property_name: str = "",
) -> Tuple[Dict[str, Dict], Dict[str, Dict[str, str]], Dict[str, List[str]]]:
    """
    (unit -> network, unit -> fields, ambiguous) for every network whose name
    (or SSID) fits the pattern. A unit claimed by more than one network goes
    to `ambiguous` (unit -> network names) instead of being picked from.
    """
    rx = pattern_regex(wlan_pattern, property_name)
    if rx is None:
        return {}, {}, {}
    claims: Dict[str, Dict[str, Dict]] = {}
    fields: Dict[str, Dict[str, str]] = {}
    for n in networks:
        for value in (n.get("name"), n.get("ssid")):
            m = rx.match(value or "")
            if m and m.group("unit").strip():
                key, f = match_unit(m)
                claims.setdefault(key, {})[n.get("id")] = n
                fields.setdefault(key, f)
                break
    units: Dict[str, Dict] = {}
    ambiguous: Dict[str, List[str]] = {}
    for unit, nets in claims.items():
        if len(nets) == 1:
            units[unit] = next(iter(nets.values()))
        else:
            ambiguous[unit] = sorted(x.get("name") or "" for x in nets.values())
    return units, {u: fields[u] for u in units}, ambiguous


def units_from_networks(
    networks: Iterable[Dict[str, Any]],
    wlan_pattern: str,
    property_name: str = "",
) -> Tuple[Dict[str, Dict], Dict[str, List[str]]]:
    """unit -> network (and ambiguous units), see units_with_fields."""
    units, _, ambiguous = units_with_fields(networks, wlan_pattern, property_name)
    return units, ambiguous


def _norm_sep(text: str) -> str:
    """Underscores and runs of whitespace as one space: SSIDs say Manor_East, AP Groups say Manor East."""
    return re.sub(r"[_\s]+", " ", text or "").strip()


def _token_in(unit: str, text: str) -> bool:
    """
    `unit` appears in `text` as its own token.

    Neighbouring characters may not continue the unit's edge: a unit starting
    with a digit can't follow a digit, one starting with a letter can't follow
    a letter (same at the end). "101" is in "101-R550", "Unit101" and
    "R550 @101", but not in "1101" or "1015".
    """
    u, t = _norm_sep(unit).lower(), _norm_sep(text).lower()
    if not u:
        return False
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


def _tokens_in(tokens: List[str], text: str) -> bool:
    return bool(tokens) and all(_token_in(t, text) for t in tokens)


def unit_for_name(name: str, units: Any) -> Tuple[Optional[str], List[str]]:
    """
    The unit an AP name contains: the longest one found as a token.

    `units` is either unit names, or unit -> tokens when a unit is more
    than its number ("East-221" -> ["East", "221"]): then every token must
    be in the name ("221 Manor East"), and the length is their total.

    Returns (unit, []) or (None, tied_units) when two different units of the
    same length both appear -- reported, not guessed.
    """
    tokens = units if isinstance(units, dict) else {u: [u] for u in units}
    found = {u: sum(len(t) for t in ts) for u, ts in tokens.items() if _tokens_in(ts, name or "")}
    if not found:
        return None, []
    longest = max(found.values())
    top = sorted(u for u, n in found.items() if n == longest)
    if len(top) > 1:
        return None, top
    return top[0], []


def detect_property_suffix(networks: Iterable[Dict[str, Any]], venue_id: str) -> str:
    """
    The "@suffix" most of this venue's active WLANs share, e.g.
    "Maple_court" -- the {property} the unit WLANs use, which
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


def units_by_property(networks: Iterable[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """Every "<unit>@<property>" SSID in the tenant: property -> its units."""
    out: Dict[str, Set[str]] = {}
    for n in networks:
        left, at, right = (n.get("ssid") or n.get("name") or "").partition("@")
        if at and left.strip() and right.strip():
            out.setdefault(right.strip(), set()).add(left.strip())
    return out


def owning_property(family_units: Set[str], by_property: Dict[str, Set[str]]) -> Optional[str]:
    """
    The property whose units best match a companion family's units (Jaccard),
    or None on a tie. A family of iot@<unit> WLANs active nowhere
    carries no property in its name; unit numbers repeat across a tenant's
    properties (1201 can exist at two of them), so it belongs
    to the property whose unit list it actually mirrors.
    """
    scores = sorted(
        (
            (len(family_units & us) / len(family_units | us), prop)
            for prop, us in by_property.items()
            if family_units & us
        ),
        reverse=True,
    )
    if not scores or (len(scores) > 1 and scores[0][0] == scores[1][0]):
        return None
    return scores[0][1]


def _family(networks: Iterable[Dict[str, Any]], rx: re.Pattern, exclude_ids: Set[str]) -> Dict[str, Dict[str, Dict]]:
    """unit -> {network id: network} for networks matching a companion regex (SSID first)."""
    claims: Dict[str, Dict[str, Dict]] = {}
    for n in networks:
        if n.get("id") in exclude_ids:
            continue
        for value in (n.get("ssid"), n.get("name")):
            m = rx.match(value or "")
            if m and m.group("unit").strip():
                claims.setdefault(match_unit(m)[0], {})[n.get("id")] = n
                break
    return claims


def companion_belongs_here(
    n: Dict[str, Any], venue_id: str, family_owner: Optional[str], property_name: str,
) -> bool:
    """Active here -> yes; active only elsewhere -> no; active nowhere -> if this property owns the family."""
    state, _ = venue_state(n, venue_id)
    if state != "not_found":
        return True
    if any(v.get("venueId") for v in n.get("venueApGroups") or []):
        return False
    return bool(family_owner) and family_owner == property_name


def detect_companion_patterns(
    networks: List[Dict[str, Any]],
    units: Set[str],
    exclude_ids: Set[str],
    venue_id: str = "",
    property_name: str = "",
    share: float = 0.25,
) -> List[Dict[str, Any]]:
    """
    "<prefix>@{unit}" patterns that this property's units also have, e.g.
    iot@1001 next to 1001@Maple_court.

    Only suggested, never applied: the page offers each one and the
    operator turns it on. A prefix counts when, of the WLANs that belong to
    THIS property (companion_belongs_here), it covers at least `share` of
    the units (and two or more).
    """
    if not units:
        return []
    prefixes: Set[str] = set()
    for n in networks:
        if n.get("id") in exclude_ids:
            continue
        left, at, right = (n.get("ssid") or n.get("name") or "").rpartition("@")
        if at and left.strip() and right.strip() in units and left.strip() not in units:
            prefixes.add(left.strip())
    by_property = units_by_property(networks)
    out = []
    for prefix in prefixes:
        pattern = f"{prefix}@{UNIT}"
        chosen, _ = companion_networks(networks, pattern, units, venue_id, property_name, exclude_ids, by_property)
        if len(chosen) >= 2 and len(chosen) >= share * len(units):
            out.append({"pattern": pattern, "units_matched": len(chosen), "units_total": len(units)})
    return sorted(out, key=lambda c: (-c["units_matched"], c["pattern"]))


def companion_networks(
    networks: Iterable[Dict[str, Any]],
    pattern: str,
    units: Set[str],
    venue_id: str,
    property_name: str = "",
    exclude_ids: Optional[Set[str]] = None,
    by_property: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[Dict[str, Dict], Dict[str, List[str]]]:
    """
    unit -> the network a companion pattern ("iot@{unit}") means
    for this property, for units the property actually has.

    The SSID is matched first, then the name. WLANs are tenant-level, so an
    SSID like iot@1400-10 can exist several times -- R1 keeps names
    unique by suffixing ("iot@1400-10_Maple"). And the unit
    number itself repeats across the tenant's properties. So a network
    only counts here when companion_belongs_here says so (active here, or
    active nowhere and this property owns the family), and of those the
    pick is: the one active in this venue; the only candidate; the one
    whose name mentions the property. Anything else is returned as
    ambiguous (unit -> candidate names), never guessed.
    """
    rx = pattern_regex(pattern, property_name)
    if rx is None:
        return {}, {}
    family = _family(networks, rx, exclude_ids or set())
    owner = owning_property(set(family), by_property if by_property is not None else units_by_property(networks))
    claims: Dict[str, Dict[str, Dict]] = {}
    for unit, nets in family.items():
        if unit not in units:
            continue
        mine = {nid: n for nid, n in nets.items() if companion_belongs_here(n, venue_id, owner, property_name)}
        if mine:
            claims[unit] = mine

    prop_tokens = [t for t in re.split(r"[\W_]+", (property_name or "").casefold()) if len(t) >= 3]
    chosen: Dict[str, Dict] = {}
    ambiguous: Dict[str, List[str]] = {}
    for unit, nets in claims.items():
        cands = list(nets.values())
        here = [n for n in cands if venue_state(n, venue_id)[0] != "not_found"]
        named = [n for n in cands if prop_tokens and prop_tokens[0] in (n.get("name") or "").casefold()]
        for pick in (here, cands, named):
            if len(pick) == 1:
                chosen[unit] = pick[0]
                break
        else:
            ambiguous[unit] = sorted(n.get("name") or n.get("id", "?") for n in cands)
    return chosen, ambiguous


def build_suggestions(
    aps: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    networks: List[Dict[str, Any]],
    venue_id: str = "",
    wlan_pattern: str = DEFAULT_WLAN_PATTERN,
    group_pattern: str = DEFAULT_GROUP_PATTERN,
    property_name: str = "",
    extra_wlans: Optional[List[str]] = None,
    companion_patterns: Optional[List[str]] = None,
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

    `companion_patterns` ("iot@{unit}") add more per-unit WLANs:
    each matching network joins its unit, for the units wlan_pattern found,
    and is expected on the unit's group like its own WLAN. Off unless given;
    patterns that would apply are only suggested (detected_companions).
    """
    raw_units, raw_fields, ambiguous_units = units_with_fields(networks, wlan_pattern, property_name)
    group_pattern = group_pattern or DEFAULT_GROUP_PATTERN
    if UNIT not in group_pattern:
        group_pattern = DEFAULT_GROUP_PATTERN
    unit_fields: Dict[str, Dict[str, str]] = {}

    def group_name(unit: str) -> str:
        """
        group_pattern filled from the unit's fields: {unit}, {building}...,
        {*} for the first wildcard. A pattern that leaves out a field the
        unit needs to be unique ("{unit}" for East-221 / West-221) names
        the group by the whole unit key instead, so two units never share
        a new group.
        """
        f = unit_fields.get(unit) or {"unit": unit}
        extra = [k for k in f if k != "unit"]
        rendered = group_pattern.replace("{*}", "{*1}")
        if any("{" + k + "}" not in rendered for k in extra):
            return group_pattern.replace(UNIT, unit)
        for k, v in f.items():
            # SSIDs say Manor_West, this venue's groups say Manor West: a
            # captured field follows the venue's own convention.
            if k != "unit" and groups_use_spaces:
                v = v.replace("_", " ")
            rendered = rendered.replace("{" + k + "}", v)
        return rendered

    def unit_tokens(unit: str) -> List[str]:
        """What an AP name must contain to be this unit's: every field."""
        return [v for v in (unit_fields.get(unit) or {"unit": unit}).values() if v]

    group_names = {g.get("id"): g.get("name") or "" for g in groups}
    group_ids = {name: gid for gid, name in group_names.items() if name}
    group_by_fold = {name.casefold(): name for name in group_ids}
    group_by_norm = {_norm_sep(name).casefold(): name for name in group_ids}
    groups_use_spaces = sum(" " in g for g in group_ids) > sum("_" in g for g in group_ids)

    # base unit -> its networks, the base WLAN first, then band variants
    bands = band_units(raw_units)
    unit_nets: Dict[str, List[Dict]] = {}
    for raw in sorted(raw_units, key=lambda u: (bands[u][1] is not None, u)):
        base, band = bands[raw]
        unit_nets.setdefault(base, []).append(raw_units[raw])
        if base not in unit_fields:
            f = dict(raw_fields.get(raw) or {"unit": raw})
            if band:
                f["unit"] = split_band(f.get("unit", ""))[0]
            unit_fields[base] = f

    unit_net_ids = {n.get("id") for nets in unit_nets.values() for n in nets}

    # Companion per-unit WLANs (iot@{unit}), only when asked for.
    companion_ambiguous: List[Dict[str, Any]] = []
    for pattern in companion_patterns or []:
        chosen, ambiguous = companion_networks(
            networks, pattern, set(unit_nets), venue_id, property_name, unit_net_ids)
        for unit, n in chosen.items():
            unit_nets[unit].append(n)
            unit_net_ids.add(n.get("id"))
        companion_ambiguous += [
            {"pattern": pattern, "unit": u, "networks": names} for u, names in sorted(ambiguous.items())
        ]
    detected_companions = [
        c for c in detect_companion_patterns(networks, set(unit_nets), unit_net_ids, venue_id, property_name)
        if c["pattern"] not in (companion_patterns or [])
    ]
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
        # "221 Manor_East" from the pattern is the existing "221 Manor East".
        wanted = group_by_norm.get(_norm_sep(wanted).casefold(), wanted)
        named = [g for g in group_ids if _tokens_in(unit_tokens(unit), g)]
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
        unit, tied = unit_for_name(name, {u: unit_tokens(u) for u in unit_nets})
        if not unit and current in unit_by_group and (not tied or unit_by_group[current] in tied):
            # No name match, or a tie the AP's own group settles.
            unit, tied = unit_by_group[current], []
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
        "companion_patterns": list(companion_patterns or []),
        "detected_companions": detected_companions,
        "companion_ambiguous": companion_ambiguous,
        "unknown_extra_wlans": [n for n in extra_names if n not in by_name],
        "counts": counts,
    }
