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

By name (place_by_name, off by default): when SZ had APs in the wrong AP
Groups, their broadcast -- and so their derived group -- is wrong too, but
their names still say where they belong. An AP named after an R1 AP Group
("4-124"), or after the unit part of exactly one group's "unit@property"
SSID ("4-124@Site"), is found that group whether or not it is the SZ one. An AP with no group of
its own sitting in another AP's unit group (Novo: 223 APs fell back to the
AP Group's WLAN Group "3109") is found the default group. "3109-B" counts
as unit 3109's own.
APs whose name points elsewhere are counted either way (aps_name_differs)
so the operator can see it; only with place_by_name are they retargeted
(by_name, sz_group_name = what the SZ broadcast said). They broadcast
their name group's WLANs in R1, not their SZ ones: verify lists them apart.

Pure: snapshot + R1 inventory + layout choices in, plain dicts out. Every
phase of the stage re-plans against a fresh capture, so a re-run picks up
where the last one stopped.
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot
from services.r1_activation import state_from_network
from services.sz_migration.audit_matching import match_wlans
from services.sz_migration.broadcast_model import r1_ssids_by_group
from services.sz_migration.r1_layout import build_layout, r1_group_name, wlan_activations, without_wlans

DEFAULT_KEY = "__default__"


def _serial(v: Any) -> str:
    return str(v or "").strip().upper()


def is_dpsk_network(net: Optional[Dict[str, Any]]) -> bool:
    return str((net or {}).get("nwSubType") or (net or {}).get("type") or "").lower() == "dpsk"


def plan_ap_mapping(snapshot: SZMigrationSnapshot, inv: R1VenueInventory,
                    choices: Optional[Dict[str, Any]] = None, add_new_aps: bool = False,
                    selected: Optional[List[str]] = None, name_policy: str = "serial",
                    renames: Optional[Dict[str, str]] = None, place_by_name: bool = False,
                    overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
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
    snapshot = without_wlans(snapshot, (choices or {}).get("excluded_wlans"))   # as the plan saw the zone
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

    # ── By name ────────────────────────────────────────────────────
    by_name = _name_targets(groups, inv, default.get("id"), r1_group_ssids, all_ap_ssids)
    # A group named after an AP -- by its name or a "unit@..." SSID -- is that
    # AP's unit group. Another AP in it with no group of its own was put
    # there by mistake: by name it goes to the default group instead.
    fold_ = lambda n: (n or "").strip().casefold()
    ap_names = {fold_(t["ap"].get("name")) for t in target.values()} - {""}
    owners = {g["key"]: ({fold_(g["name"])} | {fold_(s.rsplit("@", 1)[0]) for s in g["ssids"] if "@" in s}) & ap_names
              for g in layout["groups"] + layout["staged"]["groups"]}
    name_differs = 0
    for key, t in list(target.items()):
        own = fold_(t["ap"].get("name"))
        hit = by_name(t["ap"].get("name"))
        # "3109-B" / "3109 Bedroom" is a second AP in unit 3109, not a stray
        mine = any(re.search(rf"(?<![0-9a-z]){re.escape(o)}(?![0-9a-z])", own) for o in owners.get(t["key"], ()))
        if hit is None and owners.get(t["key"]) and not mine:
            hit = {**dflt, "silent": False}
        if hit is None or hit["key"] == t["key"] or (hit["id"] and hit["id"] == t["id"]):
            continue
        name_differs += 1
        if place_by_name:
            target[key] = {**hit, "ap": t["ap"], "by_name": True, "sz_group_name": t["name"]}
        else:
            target[key] = {**t, "name_group": hit["name"]}

    # ── Manual ─────────────────────────────────────────────────────
    # {serial: AP Group name} typed by the operator: beats SZ and by-name.
    # An R1 group by that name (any case) "exists"; one this plan builds is
    # "planned"; anything else is "missing" -- its APs wait (group_missing)
    # until the group is created. "(default)" is the venue's default group.
    r1_by_name = {fold_(g.get("name")): g for g in inv.ap_groups if (g.get("name") or "").strip()}
    plan_by_name = {fold_(g["name"]): g for g in groups}
    manual_missing: List[str] = []
    overrides = {_serial(k): v for k, v in (overrides or {}).items()}
    for key, t in list(target.items()):
        want = (overrides.get(_serial(t["ap"].get("serial"))) or "").strip()
        if not want:
            continue
        f = fold_(want)
        # what SZ / by-name chose, kept for the table beside the manual choice
        auto = {"ap": t["ap"], "manual": True, "auto_group": t["name"],
                "sz_group_name": t.get("sz_group_name") or t["name"],
                "name_group": t["name"] if t.get("by_name") else t.get("name_group")}
        if f in ("(default)", "default") or (default and f == fold_(default.get("name"))):
            target[key] = {**dflt, **auto, "manual_status": "exists"}
        elif f in r1_by_name:
            g = r1_by_name[f]
            target[key] = {"key": f"r1:{g['id']}", "name": g["name"], "id": g["id"], "ready": True, "create": False,
                           "staged": False, **auto, "manual_status": "exists",
                           "broadcast": sorted(set(all_ap_ssids) | r1_group_ssids.get(g["id"], set()))}
        elif f in plan_by_name:
            g = plan_by_name[f]
            target[key] = {"key": g["key"], "name": g["name"], "id": g["r1_id"], "ready": True, "create": g["create"],
                           "staged": g["staged"], **auto, "manual_status": "planned",
                           "broadcast": sorted(set(all_ap_ssids) | (set() if g["staged"] else set(g["ssids"])))}
        else:
            name = r1_group_name(want)
            if name.casefold() not in {m.casefold() for m in manual_missing}:
                manual_missing.append(name)
            target[key] = {"key": f"manual:{name.casefold()}", "name": name, "id": None, "ready": True,
                           "create": True, "staged": False, **auto, "manual_status": "missing",
                           "broadcast": sorted(all_ap_ssids)}

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
               "by_name": bool(t.get("by_name")), "sz_group_name": t.get("sz_group_name"),
               "name_group": t.get("name_group"),
               "manual": bool(t.get("manual")), "manual_status": t.get("manual_status"),
               "auto_group": t.get("auto_group"),
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
        elif t["key"] == DEFAULT_KEY and not t.get("by_name") and not t.get("manual") and r1_group_ssids.get(cur.get("apGroupId")):
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
        "manual_missing": manual_missing,
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
            "aps_name_differs": name_differs,
            "aps_by_name": sum(1 for r in aps if r["by_name"]),
            "aps_manual": sum(1 for r in aps if r["manual"]),
            "groups": len(groups),
            "groups_to_create": sum(1 for g in groups if g["create"] and not g["exists"]),
            "groups_staged": sum(1 for g in groups if g["staged"]),
            "wlans": len(activations),
            **{f"wlans_{s}": count(activations, "status", s)
               for s in ("already", "needed", "no_network", "conflict")},
        },
    }


def _name_targets(groups: List[Dict[str, Any]], inv: R1VenueInventory, default_id: Optional[str],
                  r1_group_ssids: Dict[str, Any], all_ap_ssids: List[str]):
    """
    AP name -> the group it is named after, or None. Candidates: the plan's
    groups (built or to be built) and every other non-default R1 group. An
    exact group name wins; else the one group carrying a "<name>@..." SSID.
    """
    fold = lambda n: (n or "").strip().casefold()
    unit = lambda ssid: fold(ssid.rsplit("@", 1)[0]) if "@" in (ssid or "") else None
    cands: List[Dict[str, Any]] = []
    seen_ids = set()
    for g in groups:
        cands.append({"key": g["key"], "name": g["name"], "id": g["r1_id"], "ready": True, "create": g["create"],
                      "staged": g["staged"],
                      "broadcast": sorted(set(all_ap_ssids) | (set() if g["staged"] else set(g["ssids"]))),
                      "ssids": g["ssids"]})
        if g["r1_id"]:
            seen_ids.add(g["r1_id"])
    for g in inv.ap_groups:
        gid = g.get("id")
        if not gid or gid == default_id or gid in seen_ids or not (g.get("name") or "").strip():
            continue
        ssids = sorted(r1_group_ssids.get(gid) or [])
        cands.append({"key": f"r1:{gid}", "name": g["name"], "id": gid, "ready": True, "create": False,
                      "staged": False, "broadcast": sorted(set(all_ap_ssids) | set(ssids)), "ssids": ssids})
    # Three indexes, tried in order: exact group name, exact unit ("1-1101" of
    # "1-1101@Site"), then the unit number alone -- the last alphanumeric
    # token of a group name or unit ("1101" of "1-1101" / "Unit 1101").
    idx: List[Dict[str, Dict[str, Dict[str, Any]]]] = [{}, {}, {}]
    for c in cands:
        units = {unit(s) for s in c.pop("ssids")} - {None, ""}
        idx[0].setdefault(fold(c["name"]), {})[c["key"]] = c
        for u in units:
            idx[1].setdefault(u, {})[c["key"]] = c
        for v in {fold(c["name"]), _last_token(fold(c["name"]))} | units | {_last_token(u) for u in units}:
            if v:
                idx[2].setdefault(v, {})[c["key"]] = c

    def lookup(ap_name: Optional[str]) -> Optional[Dict[str, Any]]:
        n = fold(ap_name)
        if not n:
            return None
        for i, keys in ((0, [n]), (1, [n]), (2, _variants(n))):
            for k in keys:
                hits = idx[i].get(k) or {}
                if len(hits) == 1:
                    return next(iter(hits.values()))
                if len(hits) > 1:
                    return None          # ambiguous: leave it to the operator
        return None
    return lookup


def _tokens(n: str) -> List[str]:
    return [t for t in re.split(r"[^0-9a-z]+", n) if t]


def _last_token(n: Optional[str]) -> Optional[str]:
    t = _tokens(n or "")
    return t[-1] if len(t) > 1 and len(t[-1]) >= 3 else None


def _variants(n: str) -> List[str]:
    """'1-1101' -> ['1101']; '2-2045 bedroom' -> ['2045', 'bedroom']: tokens of 3+ chars, the unit number first."""
    t = [x for x in _tokens(n) if len(x) >= 3]
    return sorted(t, key=lambda x: (not x.isdigit(), -len(x)))
