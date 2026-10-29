"""
Audit checkpoint 1: pair SZ objects with R1 objects, before comparing anything.

Every later comparison (settings, VLANs, per-AP broadcast) rests on these
pairings, so they are reported on their own -- with HOW each pair was made,
so a fallback match reads as one -- and the leftovers on BOTH sides are
listed rather than dropped:

  WLAN      -> R1 network   exact name, then name ignoring case, then a unique
                            SSID; venue networks first, then the rest of the
                            tenant ("exists, but not activated in this venue").
  AP Group  -> R1 AP Group  SZ "default" -> the venue's default group (unnamed,
                            isDefault); others by exact name, then ignoring case.
  AP        -> R1 AP        serial, then MAC (normalised). An SZ AP absent from
                            the venue is "sz_only" -- usually just not moved yet.

Pure: works on the two snapshots, no I/O.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from schemas.migration_audit import (
    ApGroupMatch, ApMatch, AuditMatching, MatchMethod, WlanMatch,
)
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot

SZ_DEFAULT_AP_GROUP = "default"


def norm_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    hexes = re.sub(r"[^0-9a-fA-F]", "", mac)
    return hexes.upper() if len(hexes) == 12 else None


def norm_serial(serial: Optional[str]) -> Optional[str]:
    s = (serial or "").strip().upper()
    return s or None


def _pick(candidates: List[Dict[str, Any]], used: set) -> Tuple[Optional[Dict[str, Any]], int]:
    """First unused candidate, and how many unused candidates there were."""
    free = [c for c in candidates if c.get("id") not in used]
    return (free[0] if free else None), len(free)


def match_wlans(snapshot: SZMigrationSnapshot, inv: R1VenueInventory) -> Tuple[List[WlanMatch], List[Dict[str, Any]]]:
    venue = [n for n in inv.wifi_networks if n.get("id")]
    venue_ids = {n["id"] for n in venue}
    rest = [n for n in inv.tenant_networks if n.get("id") and n["id"] not in venue_ids]
    used: set = set()
    out: List[WlanMatch] = []

    rules = (
        (MatchMethod.NAME, lambda w, n: (n.get("name") or "").strip() == w.name),
        (MatchMethod.NAME_CASE, lambda w, n: (n.get("name") or "").strip().lower() == w.name.lower()),
        (MatchMethod.SSID, lambda w, n: (n.get("ssid") or "").strip() == w.ssid),
    )

    for w in snapshot.wlans:
        m = WlanMatch(sz_wlan_id=w.id, sz_name=w.name, sz_ssid=w.ssid)
        found = None
        for pool, in_venue in ((venue, True), (rest, False)):
            for method, rule in rules:
                cand, count = _pick([n for n in pool if rule(w, n)], used)
                if cand is None:
                    continue
                if method == MatchMethod.SSID and count > 1:
                    m.notes.append(f"{count} R1 networks broadcast SSID '{w.ssid}' — matched the first")
                found = (cand, method, in_venue)
                break
            if found:
                break
        if found:
            cand, method, in_venue = found
            used.add(cand["id"])
            m.r1_network_id, m.r1_name, m.r1_ssid = cand["id"], cand.get("name"), cand.get("ssid")
            m.method, m.activated_in_venue = method, in_venue
            if not in_venue:
                m.notes.append("Exists in the tenant but is not activated in this venue")
            if method != MatchMethod.SSID and (cand.get("ssid") or "").strip() != w.ssid:
                m.notes.append(f"SSID differs: SZ '{w.ssid}', R1 '{cand.get('ssid')}'")
        out.append(m)

    r1_only = [
        {k: n.get(k) for k in ("id", "name", "ssid", "nwSubType")}
        for n in venue if n["id"] not in used
    ]
    return out, r1_only


def _r1_ap_count(group: Dict[str, Any], inv: R1VenueInventory) -> int:
    gid = group.get("id")
    return sum(1 for a in inv.aps if a.get("apGroupId") == gid)


def match_ap_groups(snapshot: SZMigrationSnapshot, inv: R1VenueInventory) -> Tuple[List[ApGroupMatch], List[Dict[str, Any]]]:
    used: set = set()
    out: List[ApGroupMatch] = []
    for g in snapshot.ap_groups:
        m = ApGroupMatch(sz_ap_group_id=g.id, sz_name=g.name, sz_ap_count=g.ap_count or 0)
        r1, method = None, MatchMethod.NONE
        if g.name == SZ_DEFAULT_AP_GROUP:
            r1, method = inv.default_ap_group(), MatchMethod.DEFAULT
        if r1 is None:
            r1 = next((x for x in inv.ap_groups if x.get("name") == g.name and x.get("id") not in used), None)
            method = MatchMethod.NAME if r1 else method
        if r1 is None:
            r1 = next((x for x in inv.ap_groups
                       if (x.get("name") or "").lower() == g.name.lower() and x.get("id") not in used), None)
            method = MatchMethod.NAME_CASE if r1 else MatchMethod.NONE
        if r1 is not None:
            used.add(r1.get("id"))
            m.r1_ap_group_id = r1.get("id")
            m.r1_name = r1.get("name") or "(default)"
            m.r1_ap_count = _r1_ap_count(r1, inv)
            m.method = method
        elif m.sz_ap_count == 0:
            m.notes.append("No APs on SZ — nothing broadcasts here, so R1 doesn't need it")
        out.append(m)

    r1_only = [
        {"id": x.get("id"), "name": x.get("name") or "(default)", "ap_count": _r1_ap_count(x, inv)}
        for x in inv.ap_groups if x.get("id") not in used
    ]
    return out, r1_only


def _sz_ap_fields(ap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": ap.get("deviceName") or ap.get("name"),
        "serial": norm_serial(ap.get("serial") or ap.get("serialNumber")),
        "mac": norm_mac(ap.get("apMac") or ap.get("mac")),
        "model": ap.get("model"),
        "group_id": ap.get("apGroupId"),
        "group_name": ap.get("apGroupName"),
    }


def match_aps(snapshot: SZMigrationSnapshot, inv: R1VenueInventory,
              group_matches: List[ApGroupMatch]) -> List[ApMatch]:
    sz_group_name = {g.id: g.name for g in snapshot.ap_groups}
    expected_by_sz_group = {gm.sz_ap_group_id: gm for gm in group_matches}
    r1_group_name = {x.get("id"): (x.get("name") or "(default)") for x in inv.ap_groups}

    r1_by_serial = {norm_serial(a.get("serialNumber")): a for a in inv.aps if norm_serial(a.get("serialNumber"))}
    r1_by_mac = {norm_mac(a.get("macAddress")): a for a in inv.aps if norm_mac(a.get("macAddress"))}
    used: set = set()
    out: List[ApMatch] = []

    for raw in snapshot.aps:
        s = _sz_ap_fields(raw)
        r1, method = None, MatchMethod.NONE
        if s["serial"] and s["serial"] in r1_by_serial:
            r1, method = r1_by_serial[s["serial"]], MatchMethod.SERIAL
        elif s["mac"] and s["mac"] in r1_by_mac:
            r1, method = r1_by_mac[s["mac"]], MatchMethod.MAC
        gm = expected_by_sz_group.get(s["group_id"])
        m = ApMatch(
            name=s["name"], serial=s["serial"], mac=s["mac"], model=s["model"],
            sz_ap_mac=raw.get("apMac") or raw.get("mac"),
            sz_ap_group=s["group_name"] or sz_group_name.get(s["group_id"]),
            expected_r1_ap_group=gm.r1_name if gm and gm.r1_ap_group_id else None,
            method=method,
        )
        if r1 is None:
            m.presence = "sz_only"
        else:
            used.add(id(r1))
            m.r1_ap_group = r1.get("apGroupName") or r1_group_name.get(r1.get("apGroupId"))
            m.r1_status = r1.get("status")
            m.sz_name, m.r1_name = s["name"], r1.get("name")
            m.r1_serial = r1.get("serialNumber")
            m.name_alignment = name_alignment(s["name"], r1.get("name"))
            m.model = m.model or r1.get("model")
            if gm and gm.r1_ap_group_id:
                m.group_aligned = r1.get("apGroupId") == gm.r1_ap_group_id
        out.append(m)

    for a in inv.aps:
        if id(a) in used:
            continue
        out.append(ApMatch(
            name=a.get("name"), serial=norm_serial(a.get("serialNumber")), mac=norm_mac(a.get("macAddress")),
            model=a.get("model"), r1_ap_group=a.get("apGroupName") or r1_group_name.get(a.get("apGroupId")),
            presence="r1_only", r1_status=a.get("status"), r1_serial=a.get("serialNumber"),
        ))
    return out


def build_matching(snapshot: SZMigrationSnapshot, inv: R1VenueInventory) -> AuditMatching:
    wlans, r1_only_networks = match_wlans(snapshot, inv)
    groups, r1_only_groups = match_ap_groups(snapshot, inv)
    aps = match_aps(snapshot, inv, groups)

    def n(items: Iterable[Any], pred) -> int:
        return sum(1 for i in items if pred(i))

    return AuditMatching(
        wlans=wlans,
        r1_only_networks=r1_only_networks,
        ap_groups=groups,
        r1_only_ap_groups=r1_only_groups,
        aps=aps,
        counts={
            "wlans_matched": n(wlans, lambda w: w.r1_network_id and w.activated_in_venue),
            "wlans_not_activated": n(wlans, lambda w: w.r1_network_id and not w.activated_in_venue),
            "wlans_unmatched": n(wlans, lambda w: not w.r1_network_id),
            "wlans_fallback_match": n(wlans, lambda w: w.method in (MatchMethod.NAME_CASE, MatchMethod.SSID)),
            "r1_only_networks": len(r1_only_networks),
            "ap_groups_matched": n(groups, lambda g: g.r1_ap_group_id),
            "ap_groups_unmatched": n(groups, lambda g: not g.r1_ap_group_id and g.sz_ap_count > 0),
            "r1_only_ap_groups": len(r1_only_groups),
            "aps_in_both": n(aps, lambda a: a.presence == "in_both"),
            "aps_sz_only": n(aps, lambda a: a.presence == "sz_only"),
            "aps_r1_only": n(aps, lambda a: a.presence == "r1_only"),
            "aps_wrong_group": n(aps, lambda a: a.group_aligned is False),
        },
    )


def name_alignment(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """exact / case (same ignoring case and surrounding space) / differs; None if either is missing."""
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if a.strip().lower() == b.strip().lower():
        return "case"
    return "differs"
