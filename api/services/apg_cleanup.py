"""
APG Cleanup: one venue's AP Groups, what is in them, and what maps to them.

Membership comes from two sides that should agree: each AP's apGroupId
(/venues/aps/query) and each group's apSerialNumbers (/venues/apGroups/query).
APs are the authority; a group that lists serials the AP query puts
elsewhere is noted, not trusted.

WLAN mapping comes from each network's venueApGroups row for this venue:
isAllApGroups maps it to every group (including the default), otherwise
apGroupIds names the groups explicitly. The group row's wifiNetworkIds is
folded in as explicit too. Whether the mapping is live is the venue
activation's scheduler (GET /venues/{v}/wifiNetworks/{n}/settings):
ALWAYS_ON is active, ALWAYS_OFF is mapped but off, CUSTOM is scheduled.

Group status:
  default  -- the venue's implicit default group (isDefault, unnamed);
              never renamed or deleted here
  named_default -- a group NAMED "default" that is not R1's default: SZ
              import debris (SZ has a real "default" WLAN group; R1's is
              implicit, and "All AP Groups" covers it). Always a cleanup
              target: deletable once empty, its APs treated like
              default-group APs (each gets a suggested group)
  in_use   -- has APs; not deletable (move them first, e.g. AP Regroup)
  awaiting_ap -- no APs, but an AP sitting in the default group is named
              for it (AP "1222 NEW" -> group "1222@Site"); not deletable --
              the AP belongs in it, the group is not unused
  mapped   -- no APs, but WLANs are explicitly mapped to it; deletable,
              and the delete drops those mappings
  unused   -- no APs and no explicit mappings; the cleanup candidates

APs in the default group are listed on their own, each with the group its
name points at. Matching is by name key: a group's key is its name before
any "@" ("1222@Promenade" -> "1222"); an AP matches a group whose key is
its whole name, its first word, its name before "@", or its leading
digits ("708*", "2505New"). More than one
match is ambiguous and suggests nothing. Seen live 2026-10-22: 106 of a
venue's 121 default-group APs were unit replacements named for an empty
unit group.

AP names that carry the AP's own serial ("COM1_411839006684") are matched
by the name without it, and can be renamed to drop it (plan_ap_renames):
the serial, the delimiter joining it, and any brackets around it go.

APs are moved (plan_moves) by serial or name into a group by id or name,
creating a missing group -- the AP Regroup CSV, folded in here.

Renames are validated against R1's name rule and against every other group
in the venue (case-insensitively: the tools treat case variants as the same
group, see venues.find_ap_group_by_name). A rename onto a name another
rename is vacating is ordered after it; a cycle (A->B, B->A) is refused.

Pure: no I/O.
"""

import re
from typing import Any, Dict, List, Optional

DEFAULT = "default"
NAMED_DEFAULT = "named_default"
IN_USE = "in_use"
AWAITING_AP = "awaiting_ap"
MAPPED = "mapped"
UNUSED = "unused"
STATUSES = (DEFAULT, NAMED_DEFAULT, IN_USE, AWAITING_AP, MAPPED, UNUSED)

# R1's own pattern for an AP Group name (Wi-Fi_Services_VenueApGroup.name).
NAME_RE = re.compile(r"(?=^((?!(`|\$\()).){2,64}$)^(\S.*\S)$")

SSID_LIMIT = 15  # R1: 15 SSIDs per AP Group

# R1's pattern for an AP name (Wi-Fi_Services_ApV1.name): 2-32 printable
# ASCII characters, no backtick or $(, no leading/trailing space.
AP_NAME_RE = re.compile(r"(?=^((?!`|\$\()[ -_a-~]){2,32}$)^(\S.*\S)$")

_DELIMS = r"[\s_\-.:|/#]*"


def strip_serial(name: str, serial: str) -> Optional[str]:
    """
    The AP name without its serial: "COM1_411839006684" -> "COM1",
    "411839006684-1113" -> "1113", "Lobby (411839006684)" -> "Lobby",
    "A_411839006684_B" -> "A_B". None when the name does not carry the
    serial, or when nothing would be left of it.
    """
    name, serial = (name or "").strip(), (serial or "").strip()
    if len(serial) < 6 or serial.casefold() not in name.casefold():
        return None
    m = re.search(f"({_DELIMS})[\\(\\[]?\\s*{re.escape(serial)}\\s*[\\)\\]]?({_DELIMS})", name, re.I)
    left, right = name[:m.start()], name[m.end():]
    if left and right:
        # Keep one delimiter between the two halves: the one that was on the left, else the right.
        joiner = (m.group(1).strip() or m.group(2).strip() or " ")[:1]
        new = left + joiner + right
    else:
        new = left or right
    new = new.strip(" _-.:|/#")
    return new or None


def scheduler_state(settings: Optional[Dict[str, Any]]) -> str:
    """active / off / scheduled / unknown, from a venue-network settings object."""
    if settings is None:
        return "unknown"
    kind = ((settings.get("scheduler") or {}).get("type") or "ALWAYS_ON").upper()
    return {"ALWAYS_ON": "active", "ALWAYS_OFF": "off", "CUSTOM": "scheduled"}.get(kind, "unknown")


def is_named_default(group: Dict[str, Any]) -> bool:
    return not group.get("isDefault") and (group.get("name") or "").strip().casefold() == "default"


def group_key(name: str) -> str:
    return (name or "").split("@", 1)[0].strip().casefold()


def ap_keys(name: str) -> set:
    name = (name or "").strip()
    keys = {name, name.split("@", 1)[0].strip()}
    if name.split():
        keys.add(name.split()[0])
    digits = re.match(r"\d+", name)  # "708*", "2505New", "2524_new" -> the unit number
    if digits:
        keys.add(digits.group())
    return {k.casefold() for k in keys if k}


def suggest_group(ap_name: str, groups_by_key: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The group an AP's name points at: {match: one|ambiguous|none, group?, candidates?}."""
    found = {g["id"]: g for k in ap_keys(ap_name) for g in groups_by_key.get(k, [])}
    if len(found) == 1:
        return {"match": "one", "group": next(iter(found.values()))}
    if found:
        return {"match": "ambiguous", "candidates": sorted(g.get("name") or "" for g in found.values())}
    return {"match": "none"}


def _venue_activation(network: Dict[str, Any], venue_id: str) -> Optional[Dict[str, Any]]:
    for va in network.get("venueApGroups") or []:
        if va.get("venueId") == venue_id:
            return va
    return None


def build_inventory(
    groups: List[Dict[str, Any]],
    aps: List[Dict[str, Any]],
    networks: List[Dict[str, Any]],
    settings: Dict[str, Optional[Dict[str, Any]]],
    venue_id: str,
) -> Dict[str, Any]:
    """
    One row per AP Group in the venue.

    groups: /venues/apGroups/query rows; aps: /venues/aps/query rows;
    networks: /wifiNetworks/query rows for the venue; settings: venue
    settings per network id (None when the read failed).
    """
    group_ids = {g.get("id") for g in groups if g.get("id")}
    default_id = next((g["id"] for g in groups if g.get("isDefault") and g.get("id")), None)
    named_default_ids = [g["id"] for g in groups if g.get("id") and is_named_default(g)]
    # Groups whose APs are "not really placed": the implicit default and any group named "default".
    holding = ([default_id] if default_id else []) + named_default_ids
    group_name = {g.get("id"): g.get("name") or ("(default)" if g.get("isDefault") else g.get("id"))
                  for g in groups}

    # ── APs, by the AP's own apGroupId ──
    members: Dict[str, List[Dict[str, Any]]] = {gid: [] for gid in group_ids}
    stray_aps = []
    ap_group_of: Dict[str, str] = {}
    for ap in aps:
        serial = ap.get("serialNumber") or ap.get("serial")
        entry = {"serial": serial, "name": ap.get("name") or serial,
                 "clean_name": strip_serial(ap.get("name"), serial),
                 "status": ap.get("status"), "model": ap.get("model")}
        gid = ap.get("apGroupId")
        if gid in members:
            members[gid].append(entry)
            ap_group_of[serial] = gid
        elif not gid and default_id:
            # An AP with no group sits in the default group.
            members[default_id].append(entry)
            ap_group_of[serial] = default_id
        else:
            stray_aps.append(entry)

    # ── WLANs ──
    net_by_id = {n["id"]: n for n in networks if n.get("id")}
    explicit: Dict[str, set] = {gid: set() for gid in group_ids}
    all_groups: List[str] = []
    for nid, n in net_by_id.items():
        va = _venue_activation(n, venue_id)
        if va is None:
            continue
        if va.get("isAllApGroups"):
            all_groups.append(nid)
        for gid in va.get("apGroupIds") or []:
            if gid in explicit:
                explicit[gid].add(nid)
    for g in groups:
        for nid in g.get("wifiNetworkIds") or []:
            if g.get("id") in explicit and nid not in all_groups:
                explicit[g["id"]].add(nid)

    # Explicit groups per network in this venue -- deleting a network's only
    # group leaves its venue activation with nothing to activate on.
    groups_of_network: Dict[str, set] = {}
    for gid, nids in explicit.items():
        for nid in nids:
            groups_of_network.setdefault(nid, set()).add(gid)

    # ── APs in the default group, and the group each one's name points at ──
    groups_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for g in groups:
        if g.get("id") and g["id"] not in holding and group_key(g.get("name")):
            groups_by_key.setdefault(group_key(g.get("name")), []).append(g)
    default_aps = []
    awaiting: Dict[str, List[str]] = {}
    held = [(gid, a) for gid in holding for a in members.get(gid, [])]
    for held_in, a in sorted(held, key=lambda x: (x[1]["name"] or "").casefold()):
        # The name without its serial, when it carries one: "1113_312072009063" matches "1113@Site".
        s = suggest_group(a["clean_name"] or a["name"], groups_by_key)
        entry = {**a, "group_id": held_in, "group_name": group_name.get(held_in), "match": s["match"], "suggested_group_id": None, "suggested_group_name": None,
                 "suggested_group_aps": 0, "candidates": s.get("candidates") or []}
        if s["match"] == "one":
            gid = s["group"]["id"]
            entry.update(suggested_group_id=gid, suggested_group_name=s["group"].get("name"),
                         suggested_group_aps=len(members.get(gid, [])))
            if not members.get(gid):
                awaiting.setdefault(gid, []).append(a["name"])
        default_aps.append(entry)

    def wlan(nid: str, via: str) -> Dict[str, Any]:
        n = net_by_id.get(nid) or {}
        return {"id": nid, "name": n.get("name") or nid, "ssid": n.get("ssid") or "",
                "via": via, "state": scheduler_state(settings.get(nid)) if nid in net_by_id else "unknown"}

    rows = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        aps_in = sorted(members.get(gid, []), key=lambda a: (a["name"] or "").casefold())
        wlans = ([wlan(nid, "explicit") for nid in sorted(explicit.get(gid, set()))]
                 + [wlan(nid, "all_groups") for nid in all_groups])
        wlans.sort(key=lambda w: (w["via"] != "explicit", (w["name"] or "").casefold()))
        notes = []

        listed = set(g.get("apSerialNumbers") or [])
        elsewhere = sorted(s for s in listed if ap_group_of.get(s) not in (None, gid))
        if elsewhere:
            notes.append(f"Group lists {len(elsewhere)} AP{'s' if len(elsewhere) != 1 else ''} "
                         f"the AP query places in another group")

        n_active = sum(1 for w in wlans if w["state"] != "off")
        if len(wlans) > SSID_LIMIT:
            notes.append(f"{len(wlans)} WLANs mapped ({n_active} not off) — "
                         f"R1 allows {SSID_LIMIT} per AP Group")

        if g.get("isDefault"):
            status = DEFAULT
        elif is_named_default(g):
            status = NAMED_DEFAULT
            notes.append("Named \"default\" — SZ import debris; R1's default group is implicit"
                         + (", so move these APs to their unit groups (or R1's default) and delete it"
                            if aps_in else ", delete it"))
            if explicit.get(gid):
                notes.append("WLANs mapped here belong on \"All AP Groups\" in R1 instead")
        elif aps_in:
            status = IN_USE
        elif awaiting.get(gid):
            status = AWAITING_AP
            notes.append("AP " + ", ".join(awaiting[gid]) + " sits in the default group and is named for "
                         "this one — move it here (AP Regroup) rather than delete the group")
        elif explicit.get(gid):
            status = MAPPED
            sole = sorted(net_by_id.get(nid, {}).get("name") or nid
                          for nid in explicit[gid] if groups_of_network.get(nid) == {gid})
            if sole:
                notes.append("Only AP Group for " + ", ".join(sole)
                             + " in this venue — deleting it leaves "
                             + ("that network" if len(sole) == 1 else "those networks")
                             + " activated on no AP Group")
        else:
            status = UNUSED

        rows.append({
            "id": gid,
            "name": g.get("name") or ("(default)" if g.get("isDefault") else gid),
            "description": g.get("description") or "",
            "is_default": bool(g.get("isDefault")),
            "status": status,
            "aps": aps_in,
            "ap_count": len(aps_in),
            "online_count": sum(1 for a in aps_in if str(a.get("status") or "").startswith("2")),
            "client_count": g.get("clientCount") or 0,
            "wlans": wlans,
            "explicit_wlan_count": len(explicit.get(gid, set())),
            "notes": notes,
        })

    rows.sort(key=lambda r: (not r["is_default"], r["name"].casefold()))

    # ── AP names carrying the AP's serial, with the name it would become ──
    carrying = [ap for ap in aps if (ap.get("name") or "") != (ap.get("serialNumber") or "")
                and strip_serial(ap.get("name"), ap.get("serialNumber"))]
    checked = plan_ap_renames(aps, [{"serial": ap["serialNumber"],
                                     "new_name": strip_serial(ap.get("name"), ap["serialNumber"])}
                                    for ap in carrying])
    serial_named = [{**r, "group_name": group_name.get(ap_group_of.get(r["serial"]))}
                    for r in checked["rows"]]
    serial_named.sort(key=lambda r: (r["old_name"] or "").casefold())

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    return {
        "groups": rows,
        "counts": counts,
        "summary": {
            "groups": len(rows),
            "aps": len(aps),
            "networks": len(net_by_id),
            "all_group_networks": len(all_groups),
            "networks_off": sum(1 for nid in net_by_id if scheduler_state(settings.get(nid)) == "off"),
            "over_ssid_limit": sum(1 for r in rows if len(r["wlans"]) > SSID_LIMIT),
            "default_aps": len(default_aps),
            "default_aps_matched": sum(1 for a in default_aps if a["match"] == "one"),
            "serial_named": len(serial_named),
        },
        "default_aps": default_aps,
        "serial_named": serial_named,
        "stray_aps": stray_aps,
    }


def check_delete(row: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why a group may not be deleted (fresh inventory row), or None."""
    if row is None:
        return "AP Group not found in this venue"
    if row["status"] == DEFAULT:
        return "The venue's default AP Group cannot be deleted"
    if row["status"] == IN_USE:
        return f"Has {row['ap_count']} AP{'s' if row['ap_count'] != 1 else ''} — move them out first"
    if row["status"] == NAMED_DEFAULT and row["ap_count"]:
        return f"Named \"default\" but still has {row['ap_count']} AP{'s' if row['ap_count'] != 1 else ''} — move them out first"
    if row["status"] == AWAITING_AP:
        return "An AP in the default group is named for this group — it is waiting for that AP"
    return None


def plan_renames(groups: List[Dict[str, Any]], renames: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Validate and order renames against the venue's current groups.

    groups: inventory rows (id, name, is_default). renames: {id, new_name}.
    Returns {"rows": [...], "waves": [[id, ...], ...]} -- each row has
    status ok / unchanged / blocked; waves are the ok renames in an order
    that never collides (a wave runs only after the names it takes are
    vacated).
    """
    by_id = {g["id"]: g for g in groups}
    rows = []
    seen = set()
    for r in renames:
        gid, new = r.get("id"), r.get("new_name")
        g = by_id.get(gid)
        row = {"id": gid, "old_name": g["name"] if g else None, "new_name": new,
               "status": "ok", "error": None}
        rows.append(row)
        if gid in seen:
            row.update(status="blocked", error="Renamed more than once")
            continue
        seen.add(gid)
        if g is None:
            row.update(status="blocked", error="AP Group not found in this venue")
        elif g.get("is_default"):
            row.update(status="blocked", error="The default AP Group is not renamed here")
        elif new == g["name"]:
            row["status"] = "unchanged"
        elif not isinstance(new, str) or not NAME_RE.match(new):
            row.update(status="blocked", error="Not a valid R1 AP Group name (2–64 characters, "
                                               "no leading/trailing space, no ` or $( )")

    ok = [r for r in rows if r["status"] == "ok"]

    # Two renames onto one name.
    by_target: Dict[str, List[Dict[str, Any]]] = {}
    for r in ok:
        by_target.setdefault(r["new_name"].casefold(), []).append(r)
    for same in by_target.values():
        if len(same) > 1:
            for r in same:
                r.update(status="blocked", error="Another rename targets the same name")
    ok = [r for r in ok if r["status"] == "ok"]

    # Final names of every group once the ok renames are done.
    renaming = {r["id"]: r for r in ok}
    final_holders: Dict[str, List[str]] = {}
    for g in groups:
        final = renaming[g["id"]]["new_name"] if g["id"] in renaming else g["name"]
        final_holders.setdefault(final.casefold(), []).append(g["id"])
    for r in ok:
        others = [i for i in final_holders[r["new_name"].casefold()] if i != r["id"]]
        if others:
            r.update(status="blocked", error=f"'{by_id[others[0]]['name']}' already uses that name")
    ok = [r for r in ok if r["status"] == "ok"]

    # Order: a rename waits for the group currently holding its target name
    # (case-insensitively) to be renamed away.
    holder = {g["name"].casefold(): g["id"] for g in groups}
    pending = {r["id"]: r for r in ok}
    waves: List[List[str]] = []
    while pending:
        wave = [gid for gid, r in pending.items()
                if holder.get(r["new_name"].casefold()) in (None, gid)
                or holder.get(r["new_name"].casefold()) not in pending]
        if not wave:
            for r in pending.values():
                r.update(status="blocked",
                         error="Part of a rename cycle (e.g. a swap) — go through a temporary name")
            break
        waves.append(sorted(wave, key=lambda i: pending[i]["old_name"].casefold()))
        for gid in wave:
            pending.pop(gid)

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "unchanged", "blocked")}
    return {"rows": rows, "waves": waves, "counts": counts}


def plan_moves(
    groups: List[Dict[str, Any]],
    aps: List[Dict[str, Any]],
    moves: List[Dict[str, Any]],
    allow_create: bool = True,
) -> Dict[str, Any]:
    """
    Resolve AP moves against the venue: the same job AP Regroup's CSV does.

    groups / aps are the raw query rows. Each move names an AP by serial or
    exact name (`ap`) and a target by `group_id` or `group_name`; a name
    with no group is created when allow_create (and valid), matched
    case-insensitively first so a case variant is reused, never duplicated.
    A group NAMED "default" is never a target -- it is debris to empty.

    Rows: status ok / already / blocked. groups_to_create: names, once each.
    """
    by_serial = {a.get("serialNumber"): a for a in aps if a.get("serialNumber")}
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for a in aps:
        if a.get("name"):
            by_name.setdefault(a["name"], []).append(a)
    group_by_id = {g["id"]: g for g in groups if g.get("id")}
    group_by_name = {g.get("name"): g for g in groups if g.get("name")}
    folded: Dict[str, List[Dict[str, Any]]] = {}
    for g in groups:
        if g.get("name"):
            folded.setdefault(g["name"].strip().casefold(), []).append(g)
    label = lambda g: g.get("name") or ("(default)" if g.get("isDefault") else g.get("id"))

    rows = []
    seen = set()
    to_create: Dict[str, str] = {}
    for m in moves:
        ident = str(m.get("ap") or "").strip()
        row: Dict[str, Any] = {"ap": ident, "serial": None, "ap_name": None, "from_group": None,
                               "to_group_id": None, "to_group_name": None, "create": False,
                               "status": "ok", "error": None}
        rows.append(row)

        ap = by_serial.get(ident)
        if ap is None:
            named = by_name.get(ident) or []
            if len(named) > 1:
                row.update(status="blocked", error=f"{len(named)} APs share that name — use the serial")
                continue
            ap = named[0] if named else None
        if ap is None:
            row.update(status="blocked", error="No AP in this venue by that serial or name")
            continue
        row.update(serial=ap["serialNumber"], ap_name=ap.get("name") or ap["serialNumber"])
        current = group_by_id.get(ap.get("apGroupId"))
        row["from_group"] = label(current) if current else None
        if row["serial"] in seen:
            row.update(status="blocked", error="AP appears more than once")
            continue
        seen.add(row["serial"])

        target = None
        if m.get("group_id"):
            target = group_by_id.get(m["group_id"])
            if target is None:
                row.update(status="blocked", error="Target AP Group not found in this venue")
                continue
        else:
            name = str(m.get("group_name") or "").strip()
            target = group_by_name.get(name)
            if target is None:
                variants = folded.get(name.casefold()) or []
                if len(variants) == 1:
                    target = variants[0]
                elif len(variants) > 1:
                    row.update(status="blocked", to_group_name=name,
                               error="Several AP Groups match that name ignoring case")
                    continue
            if target is None:
                row["to_group_name"] = name
                if not allow_create:
                    row.update(status="blocked", error="No AP Group by that name")
                elif not NAME_RE.match(name):
                    row.update(status="blocked", error="Not a valid R1 AP Group name")
                elif name.casefold() == "default":
                    row.update(status="blocked", error="Not creating a group named \"default\"")
                else:
                    row["create"] = True
                    to_create.setdefault(name.casefold(), name)
                continue

        row.update(to_group_id=target["id"], to_group_name=label(target))
        if is_named_default(target):
            row.update(status="blocked", error="A group named \"default\" is debris to empty, not fill")
        elif ap.get("apGroupId") == target["id"]:
            row["status"] = "already"

    # Rows creating the same group share one spelling.
    for r in rows:
        if r["create"]:
            r["to_group_name"] = to_create[r["to_group_name"].casefold()]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "already", "blocked")}
    return {"rows": rows, "groups_to_create": sorted(to_create.values()), "counts": counts}


def plan_ap_renames(aps: List[Dict[str, Any]], renames: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Check AP renames ({serial, new_name}) against the venue's APs.

    R1's AP name rule, and no two APs in the venue ending up with the same
    name (case-insensitively; R1 may also hold names unique more widely --
    a clash outside the venue comes back as a write error). Rows: ok /
    unchanged / blocked.
    """
    by_serial = {a.get("serialNumber"): a for a in aps if a.get("serialNumber")}
    rows = []
    seen = set()
    for r in renames:
        serial, new = r.get("serial"), r.get("new_name")
        ap = by_serial.get(serial)
        row = {"serial": serial, "old_name": (ap or {}).get("name"), "new_name": new,
               "status": "ok", "error": None}
        rows.append(row)
        if serial in seen:
            row.update(status="blocked", error="AP appears more than once")
        elif ap is None:
            row.update(status="blocked", error="No AP in this venue with that serial")
        elif not new:
            row.update(status="blocked", error="The name is only the serial — nothing would be left")
        elif new == ap.get("name"):
            row["status"] = "unchanged"
        elif not isinstance(new, str) or not AP_NAME_RE.match(new):
            row.update(status="blocked", error="Not a valid R1 AP name (2–32 printable characters, "
                                               "no leading/trailing space, no ` or $( )")
        seen.add(serial)

    renaming = {r["serial"]: r["new_name"] for r in rows if r["status"] == "ok"}
    final: Dict[str, List[str]] = {}
    for a in aps:
        if a.get("serialNumber"):
            name = renaming.get(a["serialNumber"], a.get("name") or a["serialNumber"])
            final.setdefault(name.casefold(), []).append(a["serialNumber"])
    for r in rows:
        if r["status"] != "ok":
            continue
        others = [x for x in final[r["new_name"].casefold()] if x != r["serial"]]
        if others:
            other = by_serial[others[0]]
            r.update(status="blocked", error=f"AP {other.get('name')} ({others[0]}) would have the same name")

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "unchanged", "blocked")}
    return {"rows": rows, "counts": counts}
