"""
WLAN Wiz shaping -- raw R1 rows in, flat reference tables out.

Pure functions, no I/O. The page joins these itself, so each table is kept
flat and keyed by id: networks (tenant-wide), and for one venue its AP Groups,
APs and the venue activation of every network activated there.

What a network is "on" in a venue is read the way APG Cleanup reads it
(services/apg_cleanup.py): the network's venueApGroups entry for the venue
(isAllApGroups, apGroupIds) unioned with each group's own wifiNetworkIds --
R1 does not always report the mapping from both sides.
"""

from typing import Any, Dict, List, Optional

from services.apg_cleanup import scheduler_state
from services.pisr.shape import ap_views


def _vlan_pool(n: Dict[str, Any]) -> Optional[str]:
    pool = n.get("vlanPool")
    if isinstance(pool, dict):
        return pool.get("name") or pool.get("id")
    return pool or None


def shape_networks(networks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every network in the tenant, with every venue it is activated in."""
    rows = []
    for n in networks:
        if not n.get("id"):
            continue
        activations = [
            {
                "venue_id": va.get("venueId"),
                "all_groups": bool(va.get("isAllApGroups")),
                "group_ids": [g for g in (va.get("apGroupIds") or []) if g],
            }
            for va in (n.get("venueApGroups") or []) if va.get("venueId")
        ]
        rows.append({
            "id": n["id"],
            "name": n.get("name") or "",
            "ssid": n.get("ssid") or "",
            "description": n.get("description") or "",
            "type": n.get("nwSubType") or "",
            "security": n.get("securityProtocol") or "",
            "captive": n.get("captiveType") or "",
            "vlan": n.get("vlan"),
            "vlan_pool": _vlan_pool(n),
            "ap_count": n.get("apCount") or 0,
            "client_count": n.get("clientCount") or 0,
            "activations": activations,
        })
    return rows


def shape_venues(venues: Any) -> List[Dict[str, str]]:
    rows = venues.get("data", venues) if isinstance(venues, dict) else venues
    return [{"id": v["id"], "name": v.get("name") or v["id"]}
            for v in rows or [] if isinstance(v, dict) and v.get("id")]


def shape_venue(
    venue_id: str,
    groups: List[Dict[str, Any]],
    aps: List[Dict[str, Any]],
    networks: List[Dict[str, Any]],
    settings: Dict[str, Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    groups: /venues/apGroups/query rows; aps: /venues/aps/query rows;
    networks: /wifiNetworks/query rows filtered to the venue; settings: the
    venue settings per network id (None when the read failed).
    """
    group_ids = {g["id"] for g in groups if g.get("id")}
    default_id = next((g["id"] for g in groups if g.get("isDefault") and g.get("id")), None)

    # ── Activations in this venue ──
    acts: Dict[str, Dict[str, Any]] = {}
    for n in networks:
        nid = n.get("id")
        va = next((v for v in (n.get("venueApGroups") or []) if v.get("venueId") == venue_id), None)
        if not nid or va is None:
            continue
        acts[nid] = {
            "network_id": nid,
            "name": n.get("name") or "",
            "ssid": n.get("ssid") or "",
            "all_groups": bool(va.get("isAllApGroups")),
            "group_ids": {g for g in (va.get("apGroupIds") or []) if g in group_ids},
            "state": scheduler_state(settings.get(nid)),
        }
    for g in groups:
        for nid in g.get("wifiNetworkIds") or []:
            a = acts.get(nid)
            if a and not a["all_groups"] and g.get("id"):
                a["group_ids"].add(g["id"])

    # ── APs: the AP's own apGroupId is the truth; no group = the default group ──
    views = ap_views(aps, groups)
    ap_rows, stray = [], 0
    for v in views:
        gid = v.get("apGroupId")
        if gid not in group_ids:
            if not gid and default_id:
                gid = v["apGroupId"] = default_id
            else:
                stray += 1
        ap_rows.append({
            "serial": v.get("serial"),
            "name": v.get("name") or v.get("serial") or "",
            "model": v.get("model") or "",
            "status": v.get("status") or "",
            "state": v.get("state"),
            "mac": v.get("mac") or "",
            "ip": v.get("ip") or "",
            "firmware": v.get("firmware") or "",
            "group_id": gid if gid in group_ids else None,
            "clients": v.get("clients") or 0,
            "tags": v.get("tags") if isinstance(v.get("tags"), list) else [],
            "radios": [{"band": r.get("band"), "channel": r.get("channel"), "ssids": r.get("ssids") or []}
                       for r in v.get("radios") or []],
            "ssids_on_air": v.get("ssidsBroadcast") or [],
        })

    group_rows = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        group_rows.append({
            "id": gid,
            "name": g.get("name") or ("(default)" if g.get("isDefault") else gid),
            "description": g.get("description") or "",
            "is_default": bool(g.get("isDefault")),
            "client_count": g.get("clientCount") or 0,
        })

    return {
        "groups": group_rows,
        "aps": ap_rows,
        "activations": [{**a, "group_ids": sorted(a["group_ids"])} for a in acts.values()],
        "stray_aps": stray,
        "settings_failed": sum(1 for nid in acts if settings.get(nid) is None),
    }
