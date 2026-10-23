#!/usr/bin/env python3
"""
APG Cleanup: AP Group status, WLAN mapping, delete guard and rename plan.

WHAT THIS GUARDS

  1. Membership follows each AP's apGroupId; an AP with no group sits in
     the default group; a group listing an AP the AP query puts elsewhere
     is noted.
  2. WLANs: isAllApGroups maps to every group, apGroupIds and the group's
     wifiNetworkIds map explicitly; the venue scheduler gives active / off
     / scheduled. Over 15 mapped is noted.
  3. Status: default, in_use (APs), mapped (no APs, explicit WLANs),
     unused. Only mapped and unused may be deleted; deleting a network's
     only group is noted.
  4. Renames: R1's name rule, case-insensitive collisions, two renames onto
     one name, chains ordered into waves (A->B after B->C), cycles refused,
     the default group refused.
  5. APs in the default group: each is matched by name to the group it
     belongs in (AP "1222 NEW NEW" -> "1222@Site"); an empty group with
     such an AP waiting is awaiting_ap and may not be deleted; ambiguous
     and unmatched APs suggest nothing.
  6. A group NAMED "default" (SZ import debris; R1's default is implicit)
     is flagged, its APs get suggestions too, and it is deletable once
     empty.
  7. Moves (AP Regroup, inline): AP by serial or exact name (a name shared
     by two APs is refused), group by id or name, a case variant reused,
     a missing group created once, invalid names and a group named
     "default" refused as targets, an AP already there is "already".
  8. AP names carrying their serial: the serial, its delimiter and any
     brackets are stripped; matching uses the stripped name; renames obey
     R1's AP name rule (2-32) and never leave two APs with one name.

Usage:
    docker compose exec backend python scripts/test_apg_cleanup.py

Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.apg_cleanup import (build_inventory, check_delete, plan_ap_renames, plan_moves,
                                  plan_renames, strip_serial, suggest_group)

FAILS = 0
VENUE = "v1"


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


def group(gid, name, default=False, serials=(), nets=()):
    return {"id": gid, "name": name, "venueId": VENUE, "isDefault": default,
            "apSerialNumbers": list(serials), "wifiNetworkIds": list(nets)}


def ap(serial, gid, status="2_00_Operational"):
    return {"serialNumber": serial, "name": f"AP-{serial}", "apGroupId": gid, "status": status}


def net(nid, all_groups=False, group_ids=(), other_venue=False):
    va = [{"venueId": VENUE, "isAllApGroups": all_groups, "apGroupIds": list(group_ids)}]
    if other_venue:
        va.append({"venueId": "v2", "isAllApGroups": False, "apGroupIds": ["gX"]})
    return {"id": nid, "name": nid.upper(), "ssid": nid, "venueApGroups": va}


def sched(kind):
    return {"scheduler": {"type": kind}}


print("\n1-3. Inventory")
groups = [
    group("g0", "", default=True),
    group("g1", "Lobby", serials=["S1", "S9"]),
    group("g2", "Old-Wing", nets=["n3"]),
    group("g3", "Spare"),
    group("g4", "Empty-Mapped"),
]
aps = [ap("S1", "g1"), ap("S2", None), ap("S9", "g0", status="1_01_NeverContactedCloud"), ap("S5", "gZZ")]
nets = [net("n1", all_groups=True), net("n2", group_ids=["g1", "g4"]),
        net("n3", group_ids=["g2"], other_venue=True), net("n4", group_ids=["g4"])]
settings = {"n1": sched("ALWAYS_ON"), "n2": sched("ALWAYS_OFF"), "n3": sched("CUSTOM"), "n4": None}
inv = build_inventory(groups, aps, nets, settings, VENUE)
rows = {g["id"]: g for g in inv["groups"]}

check("default group listed first, named", inv["groups"][0]["id"] == "g0" and rows["g0"]["name"] == "(default)")
check("AP with no group lands in default", {a["serial"] for a in rows["g0"]["aps"]} == {"S2", "S9"})
check("AP in unknown group is stray", [a["serial"] for a in inv["stray_aps"]] == ["S5"])
check("membership from the AP, not the group list", [a["serial"] for a in rows["g1"]["aps"]] == ["S1"])
check("group listing an AP placed elsewhere is noted",
      any("another group" in n for n in rows["g1"]["notes"]), rows["g1"]["notes"])
check("online count", rows["g0"]["online_count"] == 1, rows["g0"]["online_count"])

w1 = {w["id"]: w for w in rows["g1"]["wlans"]}
check("all-groups network on every group", all(any(w["id"] == "n1" and w["via"] == "all_groups"
                                                   for w in r["wlans"]) for r in inv["groups"]))
check("explicit mapping from apGroupIds", w1.get("n2", {}).get("via") == "explicit")
check("scheduler states", (w1["n1"]["state"], w1["n2"]["state"]) == ("active", "off"))
w2 = {w["id"]: w for w in rows["g2"]["wlans"]}
check("explicit mapping from group wifiNetworkIds; CUSTOM is scheduled",
      w2.get("n3", {}).get("state") == "scheduled")
check("unreadable settings -> unknown", {w["id"]: w for w in rows["g4"]["wlans"]}["n4"]["state"] == "unknown")
check("other venue's apGroupIds ignored", "gX" not in rows)

check("statuses", {k: r["status"] for k, r in rows.items()}
      == {"g0": "default", "g1": "in_use", "g2": "mapped", "g3": "unused", "g4": "mapped"},
      {k: r["status"] for k, r in rows.items()})
check("counts", inv["counts"] == {"default": 1, "in_use": 1, "named_default": 0, "awaiting_ap": 0, "mapped": 2, "unused": 1}, inv["counts"])
check("summary counts networks off", inv["summary"]["networks_off"] == 1 and inv["summary"]["all_group_networks"] == 1)
check("sole group of a network is noted", any("Only AP Group for N3" in n for n in rows["g2"]["notes"]),
      rows["g2"]["notes"])
check("n4 sole on g4, n2 not (also on g1)",
      any("N4" in n and "N2" not in n for n in rows["g4"]["notes"]), rows["g4"]["notes"])

check("delete: default refused", check_delete(rows["g0"]) is not None)
check("delete: in use refused", "move them" in (check_delete(rows["g1"]) or ""))
check("delete: mapped and unused allowed", check_delete(rows["g2"]) is None and check_delete(rows["g3"]) is None)
check("delete: vanished group refused", check_delete(None) is not None)

many = [net(f"m{i}", group_ids=["g1"]) for i in range(16)]
inv2 = build_inventory(groups, aps, many, {}, VENUE)
check("over 15 SSIDs noted", any("R1 allows 15" in n for n in
                                 {g["id"]: g for g in inv2["groups"]}["g1"]["notes"]))


print("\n4. Rename plan")
G = [{"id": "a", "name": "AA"}, {"id": "b", "name": "BB"}, {"id": "c", "name": "CC"},
     {"id": "d", "name": "Default", "is_default": True}]


def plan(renames):
    out = plan_renames(G, [{"id": i, "new_name": n} for i, n in renames])
    return out, {r["id"]: r for r in out["rows"]}


out, r = plan([("a", "AA2")])
check("simple rename ok, one wave", r["a"]["status"] == "ok" and out["waves"] == [["a"]])
out, r = plan([("a", "AA")])
check("same name -> unchanged", r["a"]["status"] == "unchanged" and out["waves"] == [])
out, r = plan([("a", "bb")])
check("case-insensitive collision with an untouched group", r["a"]["status"] == "blocked", r["a"])
out, r = plan([("a", "aa")])
check("case-only change of itself is ok", r["a"]["status"] == "ok")
out, r = plan([("a", "ZZ"), ("b", "zz")])
check("two renames onto one name both blocked", r["a"]["status"] == r["b"]["status"] == "blocked")
out, r = plan([("a", "BB"), ("b", "XX")])
check("chain ordered: B vacated first", out["waves"] == [["b"], ["a"]], out["waves"])
out, r = plan([("a", "BB"), ("b", "CC"), ("c", "QQ")])
check("longer chain in three waves", out["waves"] == [["c"], ["b"], ["a"]], out["waves"])
out, r = plan([("a", "BB"), ("b", "AA")])
check("swap refused as a cycle", r["a"]["status"] == r["b"]["status"] == "blocked" and out["waves"] == [],
      r)
out, r = plan([("d", "Main")])
check("default group refused", r["d"]["status"] == "blocked")
for bad in ["x", " lead", "trail ", "has`tick", "cmd$(x)", "y" * 65]:
    out, r = plan([("a", bad)])
    check(f"invalid name refused: {bad[:12]!r}", r["a"]["status"] == "blocked")
out, r = plan([("zz", "New")])
check("unknown id refused", r["zz"]["status"] == "blocked")
out, r = plan([("a", "N1"), ("a", "N2")])
check("same group twice refused", out["rows"][1]["status"] == "blocked")


print("\n5. APs in the default group")
groups5 = [
    group("g0", "", default=True),
    group("u1222", "1222@Promenade"), group("u1402", "1402@Promenade"),
    group("u426", "426@Promenade"), group("u101", "101@Promenade"),
    group("lobby", "Lobby"), group("l2", "lobby@North"), group("spare", "Spare-Group"),
]
aps5 = [ap("S1", "g0"), ap("S2", "g0"), ap("S3", "g0"), ap("S4", "g0"), ap("S5", "g0"), ap("S6", "u101")]
names = {"S1": "1222 NEW NEW", "S2": "1402 New", "S3": "426", "S4": "Lobby", "S5": "01", "S6": "101"}
for a in aps5:
    a["name"] = names[a["serialNumber"]]
aps5.append({**ap("S7", "g0"), "name": "101 spare"})
nets5 = [net("n1", group_ids=["u1222", "u1402", "u426", "u101"])]
inv5 = build_inventory(groups5, aps5, nets5, {}, VENUE)
r5 = {g["id"]: g for g in inv5["groups"]}
d5 = {a["serial"]: a for a in inv5["default_aps"]}

check("first word matches the unit group", d5["S1"]["suggested_group_id"] == "u1222")
check("bare unit number matches", d5["S3"]["suggested_group_id"] == "u426")
check("two groups share the key -> ambiguous", d5["S4"]["match"] == "ambiguous"
      and d5["S4"]["candidates"] == ["Lobby", "lobby@North"], d5["S4"])
check("leading digits match through a suffix", suggest_group("426*", {"426": [{"id": "u426", "name": "426@P"}]})["match"] == "one")
check("letters first: no digit key", suggest_group("COM1", {"1": [{"id": "x", "name": "1@P"}]})["match"] == "none")
check("no group by that name -> none", d5["S5"]["match"] == "none")
check("match onto a group that already has an AP reports it",
      d5["S7"]["suggested_group_id"] == "u101" and d5["S7"]["suggested_group_aps"] == 1)
check("empty group with an AP waiting is awaiting_ap",
      r5["u1222"]["status"] == r5["u1402"]["status"] == r5["u426"]["status"] == "awaiting_ap")
check("awaiting note names the AP", any("1222 NEW NEW" in n for n in r5["u1222"]["notes"]))
check("in-use group stays in_use", r5["u101"]["status"] == "in_use")
check("unmatched empty group stays unused", r5["spare"]["status"] == "unused")
check("awaiting_ap may not be deleted", check_delete(r5["u1222"]) is not None)
check("summary counts default-group APs", (inv5["summary"]["default_aps"], inv5["summary"]["default_aps_matched"]) == (6, 4),
      inv5["summary"])


print("\n6. A group NAMED \"default\" (SZ import debris)")
groups6 = [group("g0", "", default=True), group("nd", "Default"), group("nd2", "default", nets=["n1"]),
           group("u12", "12@Site")]
aps6 = [{**ap("S1", "nd"), "name": "12 new"}, {**ap("S2", "u12"), "name": "12"}]
inv6 = build_inventory(groups6, aps6, [net("n1", group_ids=["nd2"])], {}, VENUE)
r6 = {g["id"]: g for g in inv6["groups"]}
d6 = {a["serial"]: a for a in inv6["default_aps"]}
check("named default flagged, real default untouched",
      (r6["nd"]["status"], r6["nd2"]["status"], r6["g0"]["status"]) == ("named_default", "named_default", "default"))
check("its APs are listed with suggestions", d6.get("S1", {}).get("suggested_group_id") == "u12"
      and d6["S1"]["group_name"] == "Default", d6.get("S1"))
check("named default with APs may not be deleted", check_delete(r6["nd"]) is not None)
check("empty named default may be deleted", check_delete(r6["nd2"]) is None)
check("mapped WLANs belong on All AP Groups", any("All AP Groups" in n for n in r6["nd2"]["notes"]))
check("never a match target", suggest_group("default", {})["match"] == "none"
      and all(a.get("suggested_group_id") not in ("nd", "nd2") for a in inv6["default_aps"]))


print("\n7. Moves")
groups7 = [group("g0", "", default=True), group("u1", "1222@Site"), group("nd", "default")]
aps7 = [{**ap("S1", "g0"), "name": "1222 NEW"}, {**ap("S2", "u1"), "name": "1222"},
        {**ap("S3", "g0"), "name": "Twin"}, {**ap("S4", "g0"), "name": "Twin"}, {**ap("S5", "g0"), "name": "COM1"}]


def mv(moves, allow_create=True):
    out = plan_moves(groups7, aps7, moves, allow_create)
    return out, out["rows"]


out, r = mv([{"ap": "S1", "group_id": "u1"}])
check("by serial into a group id", r[0]["status"] == "ok" and r[0]["from_group"] == "(default)"
      and r[0]["to_group_name"] == "1222@Site", r[0])
out, r = mv([{"ap": "1222 NEW", "group_name": "1222@site"}])
check("by name, group matched ignoring case", r[0]["status"] == "ok" and r[0]["to_group_id"] == "u1", r[0])
out, r = mv([{"ap": "S2", "group_id": "u1"}])
check("already there", r[0]["status"] == "already")
out, r = mv([{"ap": "Twin", "group_id": "u1"}])
check("name shared by two APs refused", r[0]["status"] == "blocked" and "serial" in r[0]["error"])
out, r = mv([{"ap": "nope", "group_id": "u1"}])
check("unknown AP refused", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S5", "group_name": "Common Area"}, {"ap": "S3", "group_name": "common area"}])
check("missing group created once, one spelling", out["groups_to_create"] == ["Common Area"]
      and all(x["create"] and x["to_group_name"] == "Common Area" for x in r), out)
out, r = mv([{"ap": "S5", "group_name": "Common Area"}], allow_create=False)
check("no create when not allowed", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S5", "group_name": "x"}])
check("invalid new name refused", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S5", "group_id": "nd"}])
check("group named default is not a target", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S5", "group_name": "Default"}])
check("...not even by name", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S1", "group_id": "u1"}, {"ap": "1222 NEW", "group_id": "g0"}])
check("same AP twice refused", r[1]["status"] == "blocked")
out, r = mv([{"ap": "S1", "group_id": "zzz"}])
check("unknown group id refused", r[0]["status"] == "blocked")
out, r = mv([{"ap": "S2", "group_id": "g0"}])
check("into R1's real default is allowed", r[0]["status"] == "ok")


print("\n8. AP names carrying their serial")
SER = "411839006684"
for name, want in [(f"COM1_{SER}", "COM1"), (f"8000-101_{SER}", "8000-101"), (f"{SER}-1113", "1113"),
                   (f"Lobby ({SER})", "Lobby"), (f"A_{SER}_B", "A_B"), (f"Hall - {SER}", "Hall"),
                   (f"com1_{SER.upper()}", "com1"), (SER, None), ("1222 NEW", None), ("Lobby_12", None)]:
    check(f"strip {name!r} -> {want!r}", strip_serial(name, SER if name != "Lobby_12" else "12") == want,
          strip_serial(name, SER))

groups8 = [group("g0", "", default=True), group("u1113", "1113@Site"), group("c1", "COM1@Site")]
aps8 = [{**ap(SER, "g0"), "name": f"1113_{SER}"}, {**ap("222222222222", "c1"), "name": "COM1_222222222222"},
        {**ap("333333333333", "g0"), "name": "333333333333"}, {**ap("444444444444", "c1"), "name": "COM1"}]
inv8 = build_inventory(groups8, aps8, [], {}, VENUE)
d8 = {a["serial"]: a for a in inv8["default_aps"]}
check("matching ignores the serial in the name", d8[SER]["suggested_group_id"] == "u1113", d8[SER])
sn = {r["serial"]: r for r in inv8["serial_named"]}
check("serial-named APs listed (not one named only its serial)", set(sn) == {SER, "222222222222"}, list(sn))
check("proposal ok", sn[SER]["status"] == "ok" and sn[SER]["new_name"] == "1113" and sn[SER]["group_name"] == "(default)")
check("proposal colliding with another AP's name blocked",
      sn["222222222222"]["status"] == "blocked" and "COM1" in sn["222222222222"]["error"], sn["222222222222"])
check("summary counts them", inv8["summary"]["serial_named"] == 2)

pr = lambda rs: {r["serial"]: r for r in plan_ap_renames(aps8, rs)["rows"]}
r = pr([{"serial": SER, "new_name": "x"}])
check("AP name too short refused", r[SER]["status"] == "blocked")
r = pr([{"serial": SER, "new_name": "y" * 33}])
check("AP name over 32 refused", r[SER]["status"] == "blocked")
r = pr([{"serial": SER, "new_name": f"1113_{SER}"}])
check("same name -> unchanged", r[SER]["status"] == "unchanged")
r = pr([{"serial": SER, "new_name": "Unit"}, {"serial": "333333333333", "new_name": "unit"}])
check("two APs onto one name both refused", r[SER]["status"] == r["333333333333"]["status"] == "blocked")
r = pr([{"serial": "444444444444", "new_name": "COM1-old"}, {"serial": "222222222222", "new_name": "COM1"}])
check("taking a name another AP is vacating is ok", r["222222222222"]["status"] == "ok", r)
r = pr([{"serial": "nope", "new_name": "Fine"}])
check("unknown serial refused", r["nope"]["status"] == "blocked")


print(f"\n{'FAILED: ' + str(FAILS) if FAILS else 'All passed'}")
sys.exit(1 if FAILS else 0)
