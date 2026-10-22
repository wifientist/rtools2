"""
WLAN VLAN: copy each SZ WLAN's default VLAN onto its R1 network.

SZ keeps a WLAN's default VLAN on the WLAN (vlan.accessVlan). R1 keeps it on
the network (wlan.vlanId), which is tenant-wide: the venue only scopes which
networks are compared, never whether one may be set. A network active in
other venues too is noted (they get the VLAN as well); one using a VLAN pool
(advancedCustomization.vlanPool) is blocked -- a single VLAN is not a change
this tool makes.

Not reported: venue / AP Group VLAN overrides on the activation. GET
/wifiNetworks/{id} returns no `venues` and the query row's venueApGroups has
no VLANs, so seeing them costs a /venues/{v}/wifiNetworks/{n}/settings call
per network (plus one per AP Group).

WLANs pair with the networks active in the venue by the Migration Audit's
rules: exact name, then name ignoring case, then SSID. (A copy of
audit_matching.match_wlans, so this tool ships without the audit branch;
fold the two together once that lands.)

Pure: no I/O.
"""

from typing import Any, Dict, List, Optional, Tuple

IN_SYNC = "in_sync"    # R1 already carries the SZ VLAN
CHANGE = "change"      # set it; preselected
REVIEW = "review"      # can be set, but read the notes first (SZ VLAN pool)
BLOCKED = "blocked"    # nothing to set, or not this tool's change


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


MATCH_RULES = (
    ("name", lambda w, n: n.get("name") == w["name"]),
    ("name_case", lambda w, n: (n.get("name") or "").lower() == w["name"].lower()),
    ("ssid", lambda w, n: n.get("ssid") == w["ssid"]),
)


def match_networks(
    sz_wlans: List[Dict[str, Any]], networks: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pair each SZ WLAN with at most one network, each network with at most
    one WLAN. Returns (one match per WLAN, networks left unpaired)."""
    used: set = set()
    out = []
    for raw in sz_wlans:
        w = {"id": raw.get("id"), "name": raw.get("name") or "", "ssid": raw.get("ssid") or ""}
        m = {"sz_wlan_id": w["id"], "sz_name": w["name"], "sz_ssid": w["ssid"],
             "r1_network_id": None, "r1_name": None, "r1_ssid": None,
             "method": "none", "notes": []}
        for method, rule in MATCH_RULES:
            free = [n for n in networks if n.get("id") not in used and rule(w, n)]
            if not free:
                continue
            cand = free[0]
            if method == "ssid" and len(free) > 1:
                m["notes"].append(f"{len(free)} R1 networks broadcast SSID '{w['ssid']}' — matched the first")
            if method != "ssid" and cand.get("ssid") != w["ssid"]:
                m["notes"].append(f"SSID differs: SZ '{w['ssid']}', R1 '{cand.get('ssid')}'")
            used.add(cand["id"])
            m.update(r1_network_id=cand["id"], r1_name=cand.get("name"),
                     r1_ssid=cand.get("ssid"), method=method)
            break
        out.append(m)
    unpaired = [{k: n.get(k) for k in ("id", "name", "ssid", "nwSubType")}
                for n in networks if n.get("id") and n["id"] not in used]
    return out, unpaired


def _ref_name(ref: Any) -> Optional[str]:
    if isinstance(ref, dict):
        return ref.get("name") or ref.get("id")
    return ref or None


def sz_vlan_facts(wlan: Dict[str, Any], wlan_groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The default VLAN of one SZ WLAN (detail object), and what bends it."""
    vlan = wlan.get("vlan") or {}
    access = _int(vlan.get("accessVlan"))
    group_overrides = []
    for g in wlan_groups:
        for m in g.get("members") or []:
            if m.get("id") != wlan.get("id"):
                continue
            ov = _int(m.get("accessVlan"))
            if ov is not None and ov != access:
                group_overrides.append({"group": g.get("name") or g.get("id"), "vlan": ov})
    return {
        "vlan": access,
        "vlan_pool": _ref_name(vlan.get("vlanPooling")),
        "aaa_vlan_override": bool(vlan.get("aaaVlanOverride")),
        "group_overrides": group_overrides,
    }


def r1_vlan_facts(network: Dict[str, Any]) -> Dict[str, Any]:
    """The VLAN of one R1 network (full GET object)."""
    wlan = network.get("wlan") or {}
    adv = wlan.get("advancedCustomization") or {}
    return {"vlan": _int(wlan.get("vlanId")), "vlan_pool": _ref_name(adv.get("vlanPool"))}


def other_venue_count(query_row: Dict[str, Any], venue_id: str) -> int:
    """Venues besides this one the network is active in (query row; the
    venue filter does not trim venueApGroups -- verified live)."""
    return len({v.get("venueId") for v in query_row.get("venueApGroups") or []} - {venue_id, None})


def build_rows(
    sz_wlans: List[Dict[str, Any]],
    sz_wlan_groups: List[Dict[str, Any]],
    venue_networks: List[Dict[str, Any]],
    r1_full: Dict[str, Dict[str, Any]],
    venue_id: str,
) -> Dict[str, Any]:
    """
    One row per SZ WLAN: its VLAN, its R1 network's VLAN, and a status.

    sz_wlans are SZ WLAN detail objects; venue_networks the R1 query rows
    for the venue; r1_full the full GET of each, keyed by id.
    """
    matches, r1_only = match_networks(sz_wlans, venue_networks)
    by_id = {w.get("id"): w for w in sz_wlans}
    rows_by_id = {n.get("id"): n for n in venue_networks}

    rows = []
    for m in matches:
        sz = sz_vlan_facts(by_id[m["sz_wlan_id"]], sz_wlan_groups)
        row: Dict[str, Any] = {
            "sz_wlan_id": m["sz_wlan_id"], "sz_name": m["sz_name"], "sz_ssid": m["sz_ssid"],
            "sz": sz,
            "r1_network_id": m["r1_network_id"], "r1_name": m["r1_name"], "r1_ssid": m["r1_ssid"],
            "match_method": m["method"],
            "r1": None,
            "target_vlan": sz["vlan"],
            "notes": list(m["notes"]),
        }
        rows.append(row)
        notes = row["notes"]

        if sz["vlan_pool"]:
            notes.append(f"SZ uses VLAN pool '{sz['vlan_pool']}' — only its access VLAN is copied")
        if sz["aaa_vlan_override"]:
            notes.append("SZ allows AAA VLAN override — RADIUS can still move clients")
        for o in sz["group_overrides"]:
            notes.append(f"SZ WLAN group '{o['group']}' overrides it to VLAN {o['vlan']} — not copied")

        if not m["r1_network_id"]:
            row["status"] = BLOCKED
            notes.insert(0, "No R1 network in this venue matches by name or SSID")
            continue
        net = r1_full.get(m["r1_network_id"])
        if net is None:
            row["status"] = BLOCKED
            notes.insert(0, "Could not read the R1 network")
            continue

        r1 = {**r1_vlan_facts(net),
              "other_venues": other_venue_count(rows_by_id.get(m["r1_network_id"]) or {}, venue_id)}
        row["r1"] = r1
        target = sz["vlan"]
        if target is None:
            row["status"] = BLOCKED
            notes.insert(0, "SZ WLAN has no access VLAN")
            continue
        if r1["vlan_pool"]:
            row["status"] = BLOCKED
            notes.insert(0, f"R1 network uses VLAN pool '{r1['vlan_pool']}' — change it in R1")
            continue

        if r1["vlan"] == target:
            row["status"] = IN_SYNC
            continue
        row["status"] = REVIEW if sz["vlan_pool"] else CHANGE
        if r1["other_venues"]:
            n = r1["other_venues"]
            notes.insert(0, f"Also active in {n} other venue{'s' if n != 1 else ''} — "
                            f"the VLAN is tenant-wide, they get {target} too")

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (IN_SYNC, CHANGE, REVIEW, BLOCKED)}
    return {"rows": rows, "r1_only": r1_only, "counts": counts}
