"""
AP Mapping: put every AP in the R1 AP Group it belongs in, then check every
AP broadcasts in R1 what it broadcasts in SZ.

The configuration -- networks, every AP Group (SZ-derived and staged),
activations -- is Create WLANs' job; this is only the APs. Built on the same
placement (r1_layout), against the venue as R1 has it now:

  groups       the layout's groups that exist or are ticked to create
  aps          per SZ AP: target group, and add (not in the venue yet) /
               move (in another group) / already / unmapped (its group is
               neither in R1 nor ticked) / blocked (no serial)
  activations  per migrated WLAN, its R1 network (paired by audit_matching)
               and what's missing: All AP Groups, or each target group with
               that group's radios

APs whose SZ broadcast is exactly the All AP Groups set belong in the
venue's default group; so do APs that broadcast nothing in SZ (they get
the All AP Groups WLANs in R1 -- unavoidable, and noted). An AP SZ would
put in the default group that R1 has in a group carrying SSIDs of its own
is KEPT, not moved: that is the shape of a fix made by hand in R1 (a unit
AP SZ forgot to override), and moving it would take its unit SSID away.
It is reported, and verify lists it apart instead of failing on it.

Pure: snapshot + R1 inventory + layout choices in, plain dicts out. Every
phase of the stage re-plans against a fresh capture, so a re-run picks up
where the last one stopped.
"""

from collections import Counter
from typing import Any, Dict, List, Optional

from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot
from services.r1_activation import state_from_network
from services.sz_migration.audit_matching import match_wlans
from services.sz_migration.broadcast_model import r1_ssids_by_group
from services.sz_migration.r1_layout import build_layout, wlan_activations

DEFAULT_KEY = "__default__"


def _serial(v: Any) -> str:
    return str(v or "").strip().upper()


def is_dpsk_network(net: Optional[Dict[str, Any]]) -> bool:
    return str((net or {}).get("nwSubType") or (net or {}).get("type") or "").lower() == "dpsk"


def plan_ap_mapping(snapshot: SZMigrationSnapshot, inv: R1VenueInventory,
                    choices: Optional[Dict[str, Any]] = None, add_new_aps: bool = False,
                    selected: Optional[List[str]] = None, name_policy: str = "serial",
                    renames: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    name_policy: R1 needs unique AP names; SZ doesn't ("Hallway" twice failed
    the second add). AP names are unique per VENUE: an AP whose name another
    AP being loaded -- or another AP already in this venue -- also has gets
    r1_name "<name>_<serial>" ("serial", default) unless the operator typed
    its name (renames: {serial: name});
    "keep" leaves the names (R1 will refuse those adds).
    selected: the serials the operator picked on the AP table. Only those are
    moved or added; an AP not in the venue is added only when picked --
    adding a serial claims it for this R1 tenant (a real AP pre-provisioned in
    a sandbox gets in the way of the customer's tenant). Unpicked actionable
    APs read "not_selected" (moves) / "not_in_r1" (adds).
    None (older callers): moves all, adds per add_new_aps.
    """
    layout = build_layout(snapshot, inv, choices or {})
    default = inv.default_ap_group() or {}
    group_name = {g.get("id"): (g.get("name") or "(default)") for g in inv.ap_groups}
    r1_ap = {_serial(a.get("serialNumber")): a for a in inv.aps if a.get("serialNumber")}
    r1_group_ssids, _ = r1_ssids_by_group(inv)
    picked = None if selected is None else {_serial(x) for x in selected}
    sz_raw = {_serial(a.get("serial")): a for a in snapshot.aps if a.get("serial")}
    sz_group_name = {g.id: g.name for g in snapshot.ap_groups}
    all_ap_ssids = sorted({w["ssid"] for w in layout["wlans"] if w["mode"] == "all_ap_groups"})

    # ── Groups ─────────────────────────────────────────────────────
    groups: List[Dict[str, Any]] = []
    target: Dict[str, Dict[str, Any]] = {}       # serial -> target group
    unmapped_groups: List[str] = []

    def add_targets(aps: List[Dict[str, Any]], g: Dict[str, Any]):
        for ap in aps:
            target[_serial(ap.get("serial")) or f"mac:{ap.get('mac')}"] = {**g, "ap": ap}

    # Staged groups (WLANs no AP broadcasts in SZ, created + grouped but not
    # activated) are built and filled like the others; they get no activation.
    for g in layout["groups"] + [x for x in layout["staged"]["groups"] if x["chosen"]]:
        row = {"key": g["key"], "name": g["name"], "r1_id": g["r1_id"], "exists": g["exists"],
               "create": g["create"], "ap_count": g["ap_count"], "ssids": g["ssids"],
               "staged": bool(g.get("staged"))}
        if g["exists"] or g["create"]:
            groups.append(row)
        else:
            unmapped_groups.append(g["name"])
        add_targets(g["aps"], {"key": g["key"], "name": g["name"], "id": g["r1_id"],
                               "ready": g["exists"] or g["create"], "create": g["create"],
                               "staged": bool(g.get("staged")),
                               # what an AP in it broadcasts: staged groups carry nothing active
                               "broadcast": sorted(set(all_ap_ssids) | (set() if g.get("staged") else set(g["ssids"])))})
    dflt = {"key": DEFAULT_KEY, "name": "(default)", "id": default.get("id"), "ready": bool(default.get("id")),
            "create": False, "staged": False, "broadcast": all_ap_ssids}
    add_targets(layout["default_group"]["aps"], dflt)
    add_targets(layout["silent_aps"], {**dflt, "silent": True})

    # ── APs ────────────────────────────────────────────────────────
    fold = lambda n: (n or "").strip().casefold()
    sz_name_count = Counter(fold(t["ap"].get("name")) for t in target.values() if t["ap"].get("name"))
    r1_name_owner = {fold(a.get("name")): _serial(a.get("serialNumber")) for a in inv.aps if a.get("name")}
    aps: List[Dict[str, Any]] = []
    for key, t in target.items():
        ap = t["ap"]
        serial = _serial(ap.get("serial"))
        cur = r1_ap.get(serial) if serial else None
        raw = sz_raw.get(serial) or {}
        n = fold(ap.get("name"))
        clash = bool(n) and (sz_name_count[n] > 1 or (n in r1_name_owner and r1_name_owner[n] != serial))
        row = {"name": ap.get("name"), "serial": serial or None, "mac": ap.get("mac"),
               "model": raw.get("model"), "sz_ap_group": sz_group_name.get(raw.get("apGroupId")),
               "group_key": t["key"], "group_name": t["name"], "group_id": t["id"], "staged": t["staged"],
               "broadcast": t["broadcast"],
               "current_group": group_name.get(cur.get("apGroupId")) if cur else None,
               "in_r1": cur is not None, "silent": bool(t.get("silent")), "error": None,
               "name_clash": clash,
               "r1_name": (((renames or {}).get(serial) or "").strip() or f"{ap.get('name')}_{serial}")
                          if clash and serial and name_policy == "serial" else ap.get("name")}
        if not serial:
            row.update(action="blocked", error="SZ has no serial for this AP")
        elif not t["ready"]:
            # Its AP Group wasn't ticked / built. Picked anyway, it goes into
            # the venue's default group -- the APs still come over; until the
            # group exists it broadcasts only the All AP Groups WLANs.
            row["action"] = "unmapped"
            if picked is not None and serial in picked and default.get("id"):
                row.update(fallback_default=True, wanted_group=t["name"],
                           group_key=DEFAULT_KEY, group_name="(default)", group_id=default["id"],
                           broadcast=dflt["broadcast"],
                           note=f"Its AP Group ({t['name']}) isn't built — in the default group it broadcasts only "
                                "the All AP Groups WLANs until it is")
                if cur is None:
                    row["action"] = "add"
                elif cur.get("apGroupId") == default["id"]:
                    row["action"] = "already"
                else:
                    row["action"] = "move"
        elif not t["id"]:
            # Ticked or staged, but not in R1: Create WLANs builds every group.
            row["action"] = "group_missing"
        elif cur is None:
            wanted = (serial in picked) if picked is not None else add_new_aps
            row["action"] = "add" if wanted else "not_in_r1"
        elif t["id"] and cur.get("apGroupId") == t["id"]:
            row["action"] = "already"
        elif t["key"] == DEFAULT_KEY and r1_group_ssids.get(cur.get("apGroupId")):
            extra = sorted(r1_group_ssids[cur["apGroupId"]])
            row.update(action="keep", extra_ssids=extra,
                       note=f"R1 has it in '{row['current_group']}', adding {', '.join(extra[:3])} — SZ broadcasts "
                            "only the All AP Groups WLANs here; kept as R1 has it")
        elif picked is not None and serial not in picked:
            row["action"] = "not_selected"
        else:
            row["action"] = "move"
        aps.append(row)
    aps.sort(key=lambda r: (r["group_name"] or "", r["name"] or ""))

    # ── Activations ────────────────────────────────────────────────
    matches = {m.sz_wlan_id: m for m in match_wlans(snapshot, inv)[0]}
    nets = {n.get("id"): n for n in inv.tenant_networks if n.get("id")}
    nets.update({n.get("id"): n for n in inv.wifi_networks if n.get("id")})   # venue records carry venueApGroups
    activations: List[Dict[str, Any]] = []
    acts = wlan_activations(layout)
    wlan_by_id = {w.id: w for w in snapshot.wlans}
    for wid, act in acts.items():
        if act["mode"] == "none":
            continue
        w = wlan_by_id[wid]
        m = matches.get(wid)
        nid = m.r1_network_id if m else None
        net = nets.get(nid) if nid else None
        row: Dict[str, Any] = {"wlan_id": wid, "name": w.name, "ssid": w.ssid, "mode": act["mode"],
                               "network_id": nid, "network_name": (net or {}).get("name"),
                               "dpsk": is_dpsk_network(net), "targets": [], "status": "ok", "error": None}
        if not nid:
            row.update(status="no_network", error="No R1 network for this WLAN yet — create it first")
            activations.append(row)
            continue
        state, bound = state_from_network(net, inv.venue_id)
        if act["mode"] == "all_ap_groups":
            row["radio_types"] = act["radio_types"]
            if state == "venue_wide":
                row["status"] = "already"
            elif state == "specific":
                row.update(status="conflict", error=f"On {len(bound)} specific AP Group(s) in this venue; "
                                                    "the plan puts it on All AP Groups")
            else:
                row["status"] = "needed"
        else:
            if state == "venue_wide":
                row.update(status="conflict", error="On All AP Groups in this venue; the plan puts it on "
                                                    "specific AP Groups")
            for t in act["targets"]:
                gid = t.get("r1_ap_group_id")
                row["targets"].append({
                    "group_id": gid, "group_name": t["r1_ap_group_name"], "radio_types": t["r1_radio_types"],
                    "status": "already" if gid and gid in bound else ("needed" if gid else "awaiting_group"),
                })
            if row["status"] != "conflict":
                todo = [t for t in row["targets"] if t["status"] != "already"]
                row["status"] = "needed" if todo else "already"
            row["skipped_groups"] = act.get("skipped_groups") or []
        activations.append(row)

    count = lambda rows, k, v: sum(1 for r in rows if r.get(k) == v)
    return {
        "layout_summary": layout["summary"],
        "warnings": layout["warnings"],
        "blocked": layout["blocked"],
        "default_group_id": default.get("id"),
        "groups": groups,
        "groups_to_create": [g["name"] for g in groups if g["create"] and not g["exists"]],
        "unmapped_groups": unmapped_groups,
        "staged": {"unpaired": layout["staged"]["unpaired"], "conflicts": layout["staged"]["conflicts"]},
        "aps": aps,
        "activations": activations,
        "counts": {
            "aps": len(aps),
            **{f"aps_{a}": count(aps, "action", a)
               for a in ("already", "move", "add", "not_in_r1", "not_selected", "group_missing", "keep",
                         "unmapped", "blocked")},
            "aps_fallback_default": sum(1 for r in aps if r.get("fallback_default")),
            "aps_name_clash": sum(1 for r in aps if r.get("name_clash")),
            "aps_silent": sum(1 for r in aps if r["silent"]),
            "groups": len(groups),
            "groups_to_create": sum(1 for g in groups if g["create"] and not g["exists"]),
            "groups_staged": sum(1 for g in groups if g["staged"]),
            "wlans": len(activations),
            **{f"wlans_{s}": count(activations, "status", s)
               for s in ("already", "needed", "no_network", "conflict")},
        },
    }
