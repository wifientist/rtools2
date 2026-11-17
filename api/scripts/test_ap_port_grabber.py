#!/usr/bin/env python3
"""
AP Port Grabber: each SZ AP's effective LAN ports against the same R1 AP.

WHAT THIS GUARDS

  1. SZ precedence: AP override > AP Group override > zone, per field; a
     group that overrides nothing (nulls) falls through to the zone.
  2. SZ's per-AP VLAN override (overwriteVlanEnabled) is carried.
  3. Statuses: match / update / not_in_r1 / blocked (R1 can't read the AP) /
     review (PoE mode, live uplink not the usual one). No "back to venue".
  3b. THE UPLINK: never in a plan, whatever SZ says; no R1 PoE-in flag ->
     blocked; live poePort is 0-based; the phase refuses an uplink port,
     verifies the uplink after coming off venue settings, undoes the flip
     and halts the job if it moved.
  4. R1 limits: a port that can't be disabled stays on (caution); a model
     needing one enabled trunk port blocks a plan that leaves none.
  5. SZ General == R1 SELECTIVE_TRUNK, and an access port's members are noise.
  6. Profile suggestion order: same name+VLANs, R1 default, same VLANs, create
     (clash -> "<name>_<venue>").
  7. The per-AP write: off venue settings writes every port, trunk first,
     activates the mapped profile and always sets the exact VLANs as the
     port's override; an AP already on its own settings writes only what
     differs; "venue" flips useVenueSettings back on.

Usage:
    docker compose exec backend python scripts/test_ap_port_grabber.py

Exits non-zero on failure.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ap_port_grabber import (  # noqa: E402
    BLOCKED, MATCH, NOT_IN_R1, REVIEW, UPDATE, build_rows, live_poe_port, profiles_to_map,
    r1_profile_summary, sig, suggest_profile, unit_spec, uplink_ports,
)

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


# ── fixtures ──────────────────────────────────────────────────────────

CAP = {
    "model": "H550", "canSupportPoeMode": True, "poeModeCapabilities": ["Auto", "802.3at"],
    "canSupportPoeOut": True, "requireOneEnabledTrunkPort": True,
    "lanPorts": [
        {"id": "1", "defaultType": "ACCESS", "untagId": 1, "vlanMembers": "1", "supportDisable": True},
        {"id": "2", "defaultType": "ACCESS", "untagId": 1, "vlanMembers": "1", "supportDisable": True},
        {"id": "5", "defaultType": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "isPoePort": True,
         "supportDisable": False},
    ],
}
SZP = {
    "A": {"id": "A", "name": "Default Access Port", "type": "AccessPort", "untagId": 1, "vlanMembers": "1"},
    "T": {"id": "T", "name": "Default Trunk Port", "type": "TrunkPort", "untagId": 1, "vlanMembers": "1-4094"},
    "U": {"id": "U", "name": "Unit 30", "type": "AccessPort", "untagId": 30, "vlanMembers": "30"},
    "G": {"id": "G", "name": "Gen", "type": "GeneralPort", "untagId": 5, "vlanMembers": "5,7",
          "_8021X": {"type": "PortBasedAuthenticator"}},
}


def lp(name, prof, enabled=True, **extra):
    return {"portName": name, "enabled": enabled, "ethPortProfile": {"id": prof, "name": SZP[prof]["name"]}, **extra}


ZONE = {"poeModeSetting": "Auto", "poeOutPortEnabled": False,
        "lanPorts": [lp("LAN1", "A"), lp("LAN2", "A"), lp("LAN5", "T")]}
VENUE_ENTRY = {"model": "H550", "poeMode": "Auto", "poeOut": False, "lanPorts": [
    {"portId": "1", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "enabled": True},
    {"portId": "2", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "enabled": True},
    {"portId": "5", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "enabled": True},
]}
ON_VENUE = {"specific": {"useVenueSettings": True, "poeMode": "Auto", "poeOut": False}, "ports": {}}


def sz_ap(n, group="g1", serial=None, mac=None):
    return {"mac": mac or f"AA:BB:CC:00:00:0{n}", "serial": serial if serial is not None else f"S{n}",
            "name": f"ap{n}", "model": "H550", "group_id": group, "group_name": group.upper()}


def r1_ap(n, serial=None, mac=None):
    return {"serialNumber": serial or f"S{n}", "macAddress": mac or f"aa:bb:cc:00:00:0{n}", "name": f"r1-ap{n}",
            "model": "H550", "status": "2_00_Operational", "poePort": "4"}   # 0-based: LAN5


aps = [sz_ap(i) for i in range(1, 9)] + [sz_ap(9, serial="", mac="AA:BB:CC:00:00:09")]
details = {a["mac"]: {} for a in aps}
details[aps[1]["mac"]] = {"specific": {"lanPorts": [
    lp("LAN1", "U"), lp("LAN2", "A", overwriteVlanEnabled=True, vlanUntagId=40, members="40"), lp("LAN5", "T")]}}
details[aps[6]["mac"]] = {"specific": {"lanPorts": [lp("LAN1", "A"), lp("LAN2", "A"), lp("LAN5", "U")]}}
details[aps[7]["mac"]] = {"specific": {"lanPorts": [
    lp("LAN1", "A"), lp("LAN2", "A"), lp("LAN5", "T", overwriteVlanEnabled=True, vlanUntagId=10, members="1-4094")]}}
ctx = {
    "sz_aps": [{**a, "group_id": "g2", "group_name": "G2"} if a["name"] == "ap3" else a for a in aps],
    "sz_details": details,
    "zone_models": {"H550": ZONE},
    "group_models": {
        "g1|H550": {"lanPorts": None, "poeModeSetting": None},       # overrides nothing
        "g2|H550": {"lanPorts": [lp("LAN1", "A"), lp("LAN2", "A", enabled=False), lp("LAN5", "T")]},
    },
    "sz_profiles": SZP,
    "r1_aps": [r1_ap(i) for i in (1, 2, 3, 4, 6, 7, 8)] + [r1_ap(9, serial="OTHER", mac="aa-bb-cc-00-00-09")],
    "caps": {"H550": CAP},
    "venue_entries": {"H550": VENUE_ENTRY},
    "ap_states": {
        "S1": ON_VENUE, "S2": ON_VENUE, "S3": ON_VENUE, "S7": ON_VENUE, "S8": ON_VENUE, "OTHER": ON_VENUE,
        "S4": {"specific": {"useVenueSettings": False, "poeMode": "Auto", "poeOut": False},
               "ports": {"1": {"enabled": True, "overwriteUntagId": 99, "overwriteVlanMembers": "99"},
                         "2": {"enabled": True}, "5": {"enabled": True}}},
        "S6": {"_error": "AP is disconnected"},
    },
    "activations": {},
}

print("\nRows")
res = build_rows(ctx)
rows = {r["sz_name"]: r for r in res["rows"]}
check("ap1: zone == venue -> match", rows["ap1"]["status"] == MATCH and rows["ap1"]["source"] == "Zone",
      rows["ap1"])
check("ap2: AP override -> update", rows["ap2"]["status"] == UPDATE, rows["ap2"]["status"])
check("ap2: source is the AP override", rows["ap2"]["source"] == "AP override")
plan2 = {p["portId"]: p for p in unit_spec(rows["ap2"])["ports"]}
check("ap2: LAN1 Access 30 from profile 'Unit 30'",
      plan2["1"]["untagId"] == 30 and plan2["1"]["sz_profile_id"] == "U" and plan2["1"]["changed"])
check("ap2: LAN2 SZ VLAN override 40 carried",
      plan2["2"]["untagId"] == 40 and plan2["2"]["vlan_override"] and plan2["2"]["sz_profile_id"] == "A")
check("ap2: two changes listed", len(rows["ap2"]["changes"]) == 2, rows["ap2"]["changes"])
check("ap3: AP Group override disables LAN2",
      rows["ap3"]["status"] == UPDATE and rows["ap3"]["source"] == "AP Group 'G2'"
      and any("LAN2 off" in c for c in rows["ap3"]["changes"]), rows["ap3"])
check("ap4: R1 AP has its own settings, SZ = venue -> update its ports (no 'back to venue')",
      rows["ap4"]["status"] == UPDATE and unit_spec(rows["ap4"])["r1_use_venue"] is False, rows["ap4"]["status"])
check("ap5: not in R1", rows["ap5"]["status"] == NOT_IN_R1)
check("ap6: R1 can't read the AP -> blocked",
      rows["ap6"]["status"] == BLOCKED and "disconnected" in rows["ap6"]["blockers"][0])
check("ap9: matched by MAC", rows["ap9"]["r1"] and rows["ap9"]["r1"]["serial"] == "OTHER"
      and rows["ap9"]["status"] == MATCH, rows["ap9"])

print("\nThe uplink")
check("live poePort is 0-based: '4' -> LAN5, '0' -> LAN1, junk -> none",
      live_poe_port({"poePort": "4"}) == "5" and live_poe_port({"poePort": "0"}) == "1"
      and live_poe_port({"poePort": None}) is None and live_poe_port({"poePort": "x"}) is None)
check("every row carries the uplink LAN5", all(r["uplink_ports"] == ["5"] for r in res["rows"]
                                               if r["status"] in (MATCH, UPDATE, REVIEW)))
check("no plan ever contains the uplink", all(p["portId"] != "5" for r in res["rows"]
                                              for p in unit_spec(r)["ports"]))
check("ap7: SZ makes the uplink Access -> ignored, nothing else differs -> match, noted",
      rows["ap7"]["status"] == MATCH and any("LAN5 is the uplink" in n for n in rows["ap7"]["notes"]), rows["ap7"])
check("ap8: SZ changes the uplink VLAN -> ignored, noted, no change listed",
      rows["ap8"]["status"] == MATCH and not rows["ap8"]["changes"]
      and any("never changed" in n for n in rows["ap8"]["notes"]), rows["ap8"])

c4 = json.loads(json.dumps(ctx))
c4["caps"]["H550"]["lanPorts"][2]["isPoePort"] = False
r4 = {x["sz_name"]: x for x in build_rows(c4)["rows"]}
check("R1 names no PoE-in port -> every AP blocked, nothing planned",
      all(r4[n]["status"] == BLOCKED and "uplink" in r4[n]["blockers"][0] for n in ("ap1", "ap2", "ap3")),
      r4["ap2"]["blockers"])

c5 = json.loads(json.dumps(ctx))
c5["r1_aps"][1]["poePort"] = "0"          # ap2 connected on LAN1 right now
r5 = {x["sz_name"]: x for x in build_rows(c5)["rows"]}["ap2"]
check("connected on another port -> both protected, review",
      r5["uplink_ports"] == ["1", "5"] and r5["status"] == REVIEW
      and all(p["portId"] not in ("1", "5") for p in unit_spec(r5)["ports"]), (r5["uplink_ports"], r5["status"]))

u = uplink_ports("R550", {"lanPorts": [{"id": "1"}, {"id": "2", "isPoePort": True}]}, {"poePort": "1"})
check("R550: R1, static map and live all say LAN2", u["ports"] == ["2"] and not u["caution"] and not u["blocker"], u)
u = uplink_ports("R550", {"lanPorts": [{"id": "1", "isPoePort": True}, {"id": "2"}]}, {})
check("R1 and the static map disagree -> both protected, caution", u["ports"] == ["1", "2"] and u["caution"], u)

print("\nProfile auth (the E-126 case)")
c7 = json.loads(json.dumps(ctx))
c7["sz_profiles"]["CV"] = {"id": "CV", "name": "Client Visibility", "type": "AccessPort", "untagId": 1,
                           "vlanMembers": "1", "_8021X": {"type": "RoleOpen"}}
SZP["CV"] = c7["sz_profiles"]["CV"]
c7["sz_details"][aps[0]["mac"]] = {"specific": {"lanPorts": [
    lp("LAN1", "CV", overwriteVlanEnabled=True, vlanUntagId=2232, members="2232"), lp("LAN2", "A"), lp("LAN5", "T")]}}
c7["ap_states"]["S1"] = {"specific": {"useVenueSettings": False, "poeMode": "Auto", "poeOut": False},
                         "ports": {"1": {"enabled": True, "overwriteUntagId": 2232, "overwriteVlanMembers": "2232"},
                                   "2": {"enabled": True}, "5": {"enabled": True}}}
c7["activations"]["S1"] = {"1": {"id": "t_ACCESS", "type": "ACCESS", "untagId": 1, "vlanMembers": "1",
                                 "authType": "DISABLED"}}
r7 = {x["sz_name"]: x for x in build_rows(c7)["rows"]}["ap1"]
check("same VLAN, but on Default Access instead of Client Visibility -> update",
      r7["status"] == UPDATE and any("client visibility" in c for c in r7["changes"]), (r7["status"], r7["changes"]))
c7["activations"]["S1"]["1"] = {"id": "cv", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "authType": "OPEN"}
r7 = {x["sz_name"]: x for x in build_rows(c7)["rows"]}["ap1"]
check("on R1's Client Visibility with VLAN 2232 -> matches", r7["status"] == MATCH, (r7["status"], r7["changes"]))

print("\nR1 limits & signatures")
c2 = json.loads(json.dumps(ctx))
c2["caps"]["H550"]["lanPorts"][1]["supportDisable"] = False
c2["sz_details"] = {a["mac"]: {} for a in aps}
c2["sz_details"][aps[0]["mac"]] = {"specific": {"lanPorts": [
    lp("LAN1", "A"), lp("LAN2", "A", enabled=False), lp("LAN5", "T")]}}
r = {x["sz_name"]: x for x in build_rows(c2)["rows"]}["ap1"]
check("port that can't be disabled stays on, as a caution",
      r["status"] == MATCH and any("can't be disabled" in c for c in r["cautions"]), r)
check("General == SELECTIVE_TRUNK", sig("GENERAL", 5, "5, 7") == sig("SELECTIVE_TRUNK", "5", "5,7"))
check("access members are noise", sig("ACCESS", 30, "1-4094") == sig("ACCESS", "30", "30"))
c6 = json.loads(json.dumps(ctx))
c6["zone_models"]["H550"] = {**ZONE, "poeModeSetting": "_802_3at"}
r6 = {x["sz_name"]: x for x in build_rows(c6)["rows"]}["ap1"]
check("PoE mode change -> review (may reboot)", r6["status"] == REVIEW
      and any("PoE mode" in c for c in r6["cautions"]), r6)

c3 = json.loads(json.dumps(ctx))
c3["zone_models"]["H550"] = {**ZONE, "lanPorts": [lp("LAN1", "G"), lp("LAN2", "A"), lp("LAN5", "T")]}
r3 = {x["sz_name"]: x for x in build_rows(c3)["rows"]}
check("zone-level difference on 2+ APs -> venue-level note", any("venue" in n for n in build_rows(c3)["model_notes"]))
check("802.1X is not listed as 'not carried' (auth goes with the profile mapping)",
      not any("802.1X" in e for e in r3["ap1"]["extras"]), r3["ap1"]["extras"])

print("\nProfile mapping (by name, 2026-11-17)")
venue = "Lakeside"
R1P_RAW = [
    {"id": "t_ACCESS", "name": "Default Access Port", "type": "ACCESS", "untagId": 1, "vlanMembers": "1",
     "isDefault": True, "authType": "DISABLED"},
    {"id": "t_TRUNK", "name": "Default Trunk Port (WAN)", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094",
     "isDefault": True, "authType": "DISABLED"},
    {"id": "cv", "name": "Client Visibility", "type": "ACCESS", "untagId": 1, "vlanMembers": "1", "authType": "OPEN"},
    {"id": "u40", "name": "Unit 40", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "authType": "DISABLED"},
]
r1p = [r1_profile_summary(p) for p in R1P_RAW]
szp = lambda n, u=1, t="ACCESS", auth="DISABLED": {"name": n, "type": t, "untagId": u,  # noqa: E731
                                                   "vlanMembers": str(u) if t == "ACCESS" else "1-4094", "auth": auth}
got = suggest_profile(szp("Client Visibility", 1, auth="OPEN"), r1p, venue)
check("SZ 'Client Visibility' -> R1 'Client Visibility' (the live case)",
      got["action"] == "existing" and got["r1_profile_id"] == "cv", got)
got = suggest_profile(szp("client visibility", 2232, auth="OPEN"), r1p, venue)
check("name match ignores case, and VLANs don't matter", got["r1_profile_id"] == "cv", got)
got = suggest_profile(szp("Client Visibility", 1, auth="DISABLED"), r1p, venue)
check("same name, other auth -> still reused, the reason says so",
      got["r1_profile_id"] == "cv" and "auth" in got["reason"], got)
got = suggest_profile(szp("Default Trunk Port(WAN)", 1, "TRUNK"), r1p, venue)
check("'Default Trunk Port(WAN)' == 'Default Trunk Port (WAN)' (spaces ignored)", got["r1_profile_id"] == "t_TRUNK", got)
got = suggest_profile(szp("Default Access Port", 30), r1p, venue)
check("SZ default -> R1 default of the same name", got["r1_profile_id"] == "t_ACCESS", got)
got = suggest_profile(szp("Unit 30", 30), r1p, venue)
check("not in R1 -> created under the SZ name (never a default stand-in)",
      got == {**got, "action": "create", "name": "Unit 30"}, got)
got = suggest_profile(szp("Lobby Visibility", 5, auth="OPEN"), r1p, venue)
check("not in R1, open auth -> created", got["action"] == "create" and got["name"] == "Lobby Visibility", got)
got = suggest_profile(szp("Secure", 5, auth="PORT_BASED_AUTHENTICATOR"), r1p, venue)
check("not in R1, 802.1X -> no create, must pick", got["action"] == "existing" and got["r1_profile_id"] is None, got)
got = suggest_profile(szp("Unit 40", 40), r1p, venue)
check("same name but another port type -> '<name>_<venue>' created",
      got["action"] == "create" and got["name"] == "Unit 40_Lakeside", got)
to_map = profiles_to_map(res["rows"], SZP)
check("profiles to map: never the uplink's (T)", set(to_map) == {"U", "A"}, set(to_map))
check("SZ auth carried on the mapping", to_map["A"]["auth"] == "DISABLED")
check("SZ RoleOpen -> R1 OPEN", __import__("services.ap_port_grabber", fromlist=["sz_auth"]).sz_auth(
    {"_8021X": {"type": "RoleOpen"}}) == "OPEN")


# ── the per-AP write, against a fake R1 ───────────────────────────────

class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body
        self.content = b"x" if body is not None else b""
        self.text = json.dumps(body)

    def json(self):
        return self._body


PROFILES = [
    {"id": "d-trunk", "name": "Default Trunk Port (WAN)", "type": "TRUNK", "untagId": 1, "vlanMembers": "1-4094",
     "isDefault": True, "authType": "DISABLED", "venueActivations": [{"venueId": "V", "apModel": "H550", "portId": 5}]},
    {"id": "d-acc", "name": "Default Access", "type": "ACCESS", "untagId": 1, "vlanMembers": "1",
     "isDefault": True, "authType": "DISABLED"},
    {"id": "u30", "name": "Unit 30", "type": "ACCESS", "untagId": 30, "vlanMembers": "30", "authType": "DISABLED"},
]


class FakeR1:
    ec_type = "REC"

    def __init__(self, specific, ports, serial="S2", poe_port="4", flip_breaks_uplink=False):
        self.specific, self.ports, self.calls = specific, ports, []
        self.serial, self.poe_port, self.breaks = serial, poe_port, flip_breaks_uplink
        self.activations = {}
        fake = self

        class Venues:
            async def get_aps_by_tenant_venue(self, tenant_id, venue_id):
                return {"data": [{"serialNumber": fake.serial, "model": "H550", "poePort": fake.poe_port}]}
        self.venues = Venues()

    def _pid(self, path):
        return path.split("/lanPorts/")[1].split("/")[0]

    def get(self, path, **kw):
        self.calls.append(("GET", path, None))
        if path.endswith("apModelCapabilities"):
            return Resp(200, {"apModels": [CAP]})
        if path.endswith("lanPortSpecificSettings"):
            return Resp(200, dict(self.specific))
        if "/apModels/" in path:
            return Resp(200, {"enabled": True})
        return Resp(200, dict(self.ports.setdefault(self._pid(path), {"enabled": True})))

    def post(self, path, payload=None, **kw):
        profs = [{**p, "apActivations": [{"apSerialNumber": self.serial, "portId": int(pid), "venueId": "V"}
                                         for pid, pr in self.activations.items() if pr == p["id"]]}
                 for p in PROFILES]
        return Resp(200, {"data": profs, "totalCount": len(profs)})

    def put(self, path, payload=None, **kw):
        self.calls.append(("PUT", path, payload))
        if path.endswith("lanPortSpecificSettings"):
            if self.breaks and payload.get("useVenueSettings") is False:
                self.ports.setdefault("5", {})["enabled"] = False      # R1 "moves" the uplink
            self.specific = {**self.specific, **payload}
        elif "/ethernetPortProfiles/" in path:
            pid = self._pid(path)
            self.activations[pid] = path.rsplit("/", 1)[1]
            self.ports[pid] = {k: v for k, v in self.ports.get(pid, {}).items() if not k.startswith("overwrite")}
        elif path.endswith("/settings"):
            self.ports[self._pid(path)] = payload
        return Resp(202, {"requestId": f"r{len(self.calls)}"})

    async def await_task_completion(self, request_id, override_tenant_id=None, **kw):
        return {"status": "SUCCESS"}

    def puts(self):
        return [c for c in self.calls if c[0] == "PUT"]


JOB = [0]


async def run_unit(spec, fake, profile_ids, job=None):
    from workflow.phases.ap_port_grabber import GrabberApplyApPortsPhase
    from workflow.phases.phase_executor import PhaseContext
    if job is None:
        JOB[0] += 1
        job = f"test-apg-{JOB[0]}"
    phase = GrabberApplyApPortsPhase(PhaseContext(job_id=job, r1_client=fake, venue_id="V", tenant_id="T",
                                                  unit_id="u"))
    return await phase.execute(phase.Inputs(unit_id="u", unit_number=spec["sz_name"], ap=spec,
                                            profile_ids=profile_ids))


def run(spec, fake, ids, job=None):
    return asyncio.get_event_loop().run_until_complete(run_unit(spec, fake, ids, job))


asyncio.set_event_loop(asyncio.new_event_loop())

print("\nPer-AP write")
ids = {"U": "u30", "A": "d-acc", "T": "d-trunk"}
fake = FakeR1({"useVenueSettings": True, "poeMode": "Auto", "poeOut": False}, {})
out = run(unit_spec(rows["ap2"]), fake, ids)
puts = [(c[1].split("/aps/S2")[1], c[2]) for c in fake.puts()]
check("comes off venue settings first, PoE kept",
      puts[0] == ("/lanPortSpecificSettings", {"poeMode": "Auto", "poeOut": False, "useVenueSettings": False}),
      puts[0])
check("LAN5 (the uplink) is never written", not any("/lanPorts/5/" in c[1] for c in fake.puts()), fake.puts())
check("LAN1 gets the mapped profile", ("/lanPorts/1/ethernetPortProfiles/u30", None) in puts)
check("LAN2 keeps SZ's VLAN 40 as the port override",
      fake.ports["2"].get("overwriteUntagId") == 40 and fake.ports["2"].get("overwriteVlanMembers") == "40",
      fake.ports["2"])
check("counted as one AP updated", out.aps_updated == 1 and out.ports_written == 4, out)

spec = unit_spec(rows["ap2"])
spec["r1_use_venue"] = False
for p in spec["ports"]:
    p["current_profile_id"] = ids.get(p["sz_profile_id"]) if p["portId"] != "1" else "v30"
fake = FakeR1({"useVenueSettings": False, "poeMode": "Auto", "poeOut": False},
              {"1": {"enabled": True, "overwriteUntagId": 30}, "2": {"enabled": True, "overwriteUntagId": 1},
               "5": {"enabled": True}})
run(spec, fake, ids)
touched = sorted({c[1].split("/lanPorts/")[1].split("/")[0] for c in fake.puts() if "/lanPorts/" in c[1]})
check("own settings already: no flip, only changed ports", touched == ["1", "2"]
      and not any(c[1].endswith("lanPortSpecificSettings") for c in fake.puts()), fake.puts())

print("\nPer-AP write: the uplink guards")
bad = unit_spec(rows["ap2"])
bad["ports"] = bad["ports"] + [{"portId": "5", "enabled": False, "type": "TRUNK", "untagId": 1,
                                "vlanMembers": "1-4094", "sz_profile_id": None, "changed": True}]
fake = FakeR1({"useVenueSettings": True}, {})
try:
    run(bad, fake, ids)
    check("a plan holding the uplink is refused", False)
except RuntimeError as e:
    check("a plan holding the uplink is refused, before ANY write", "uplink" in str(e) and not fake.puts(),
          (str(e), fake.puts()))

fake = FakeR1({"useVenueSettings": True}, {}, poe_port="0")    # now connected on LAN1
try:
    run(unit_spec(rows["ap2"]), fake, ids)
    check("AP now connected on a planned port is refused", False)
except RuntimeError as e:
    check("AP now connected on a planned port is refused, before ANY write",
          "LAN1" in str(e) and not fake.puts(), str(e))

no_up = unit_spec(rows["ap2"])
no_up["uplink_ports"] = []
fake = FakeR1({"useVenueSettings": True}, {})
try:
    run(no_up, fake, ids)
    check("a plan with no uplink is refused", False)
except RuntimeError as e:
    check("a plan with no uplink is refused, before ANY write", not fake.puts(), str(e))

job = "test-apg-halt"
fake = FakeR1({"useVenueSettings": True, "poeMode": "Auto", "poeOut": False}, {}, flip_breaks_uplink=True)
try:
    run(unit_spec(rows["ap2"]), fake, ids, job)
    check("uplink moved by the flip -> undo + halt", False)
except RuntimeError as e:
    check("uplink moved by the flip -> put back on venue settings, job halted",
          "halted" in str(e) and fake.puts()[-1][2].get("useVenueSettings") is True
          and not any("/lanPorts/" in c[1] for c in fake.puts()), (str(e), fake.puts()))
fake2 = FakeR1({"useVenueSettings": True}, {}, serial="S3")
try:
    run(unit_spec(rows["ap3"]), fake2, ids, job)
    check("next AP of a halted job writes nothing", False)
except RuntimeError as e:
    check("next AP of a halted job writes nothing", "halted" in str(e) and not fake2.puts(), str(e))


async def _clear():
    from redis_client import get_redis_client
    from workflow.phases.ap_port_grabber import HALT_KEY
    await (await get_redis_client()).delete(HALT_KEY.format(job_id=job))
asyncio.get_event_loop().run_until_complete(_clear())

fake = FakeR1({"useVenueSettings": True}, {})
try:
    run(unit_spec(rows["ap2"]), fake, {"A": "d-acc", "T": "d-trunk"})
    check("unmapped SZ profile fails the unit", False)
except RuntimeError as e:
    check("unmapped SZ profile fails the unit, naming it", "Unit 30" in str(e), str(e))

print("\nValidate")


async def run_validate(aps_in, decisions, profiles):
    from workflow.phases.ap_port_grabber import ValidateApPortGrabberPhase
    from workflow.phases.phase_executor import PhaseContext

    class FakeList(FakeR1):
        def post(self, path, payload=None, **kw):
            return Resp(200, {"data": profiles, "totalCount": len(profiles)})
    phase = ValidateApPortGrabberPhase(PhaseContext(job_id="j", r1_client=FakeList({}, {}), venue_id="V",
                                                    tenant_id="T"))
    return await phase.execute(phase.Inputs(aps=aps_in, profiles=decisions))

R1_RAW = [p for p in PROFILES]
dec = lambda sz_id, **kw: {**{"sz_name": SZP[sz_id]["name"], "auth": "DISABLED", "type": sig(  # noqa: E731
    {"AccessPort": "ACCESS", "TrunkPort": "TRUNK"}[SZP[sz_id]["type"]], SZP[sz_id]["untagId"],
    SZP[sz_id]["vlanMembers"])[0], "untagId": SZP[sz_id]["untagId"], "vlanMembers": SZP[sz_id]["vlanMembers"]}, **kw}
units_in = [unit_spec(rows["ap2"]), unit_spec(rows["ap3"])]
good = {"U": dec("U", action="create", name="Unit 30"), "A": dec("A", action="existing", r1_profile_id="d-acc")}
v = asyncio.get_event_loop().run_until_complete(run_validate(units_in, good, R1_RAW))
check("valid plan: two units", v.validation_result.valid and len(v.unit_mappings) == 2,
      [c.description for c in v.validation_result.conflicts])
check("create with a name that exists with the same VLANs -> reuse",
      v.profiles_resolved["U"] == {"r1_profile_id": "u30", "name": "Unit 30", "create": False},
      v.profiles_resolved.get("U"))
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "A": dec("A", action="existing", r1_profile_id="d-trunk")}, R1_RAW))
check("a Trunk profile for an SZ access profile is refused", not v.validation_result.valid)
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "U": dec("U", action="create", name="Default Trunk Port (WAN)")}, R1_RAW))
check("a new name taken by another port type is refused", not v.validation_result.valid)
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "U": dec("U", action="create", name="default access")}, R1_RAW))
check("a 'new' name already in R1 with the same type -> reused by name",
      v.validation_result.valid and v.profiles_resolved["U"]["r1_profile_id"] == "d-acc", v.profiles_resolved.get("U"))
cv_raw = R1_RAW + [{"id": "cv", "name": "Client Visibility", "type": "ACCESS", "untagId": 1, "vlanMembers": "1",
                    "authType": "OPEN"}]
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "A": dec("A", action="existing", r1_profile_id="cv", auth="OPEN")}, cv_raw))
check("an existing profile with auth (Client Visibility) is accepted",
      v.validation_result.valid and v.profiles_resolved["A"]["r1_profile_id"] == "cv",
      [c.description for c in v.validation_result.conflicts])
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "A": dec("A", action="existing", r1_profile_id="cv")}, cv_raw))
check("auth differing from SZ's -> warning, not a block",
      v.validation_result.valid and any(c.severity == "warning" and "auth" in c.description
                                        for c in v.validation_result.conflicts))
v = asyncio.get_event_loop().run_until_complete(
    run_validate(units_in, {**good, "U": dec("U", action="create", name="Unit 30", auth="PORT_BASED_AUTHENTICATOR")},
                 [p for p in R1_RAW if p["id"] != "u30"]))
check("creating an 802.1X profile is refused", not v.validation_result.valid)
v = asyncio.get_event_loop().run_until_complete(
    run_validate([{**units_in[0], "uplink_ports": []}], good, R1_RAW))
check("an AP planned without an uplink is refused", not v.validation_result.valid
      and any("uplink" in c.description for c in v.validation_result.conflicts))

print(f"\n{'FAILED: ' + str(FAILS) if FAILS else 'All passed'}\n")
sys.exit(1 if FAILS else 0)
