#!/usr/bin/env python3
"""
SZ migration, destination venue: create from the zone and carry its settings.

WHAT THIS GUARDS

  1. Radio: SZ encodings become R1 ones per band (method, width, power,
     channel lists as strings, ChannelFly frequency, scan interval, 6 GHz
     rates, AFC); dual 5 GHz carries lower/upper blocks.
  2. Values R1 can't take are left out of the body and noted, never sent
     (8080 width, -14 dB, Auto power on 6 GHz, 80 MHz on 2.4).
  3. The mesh passphrase never reaches the browser-facing plan, and is only
     merged into the PUT body at apply time.
  4. Apply overlays the zone values on R1's current body: fields the zone
     says nothing about keep R1's value.
  5. v1.1 sections are written with a v1.1 Content-Type; MSP writes go out
     as the EC tenant; one failing section doesn't stop the rest.
  6. Create refuses a name already in the tenant, waits on the 202, and
     returns the new venue id.
  7. Draft: non-region SZ time zones ("UTC", "PST") aren't passed to R1.
  8. 6 GHz outdoor channels are fitted to R1's own (country-limited) list.
  9. The stage's apply phase applies every section by default (a migration
     takes the config from SZ), none when all are unticked, and fails --
     with R1's message -- when R1 refuses one.
 10. Per-AP-model zone settings (real Lofts at Mercer H550/R510 data):
     PoE, LAN ports inline (type/untag/members, port id from LANn), LEDs;
     802.1X etc. on a port profile noted, LLDP/USB listed as not carried.
 11. Only the zone's models that R1 offers at the venue, and only where the
     zone differs from R1's default for the model, are written -- each as a
     complete entry. A beta model R1 doesn't offer (H375), unsupported PoE,
     undisableable ports and "no trunk left" are noted, never sent. Verify
     compares only what was written.
 12. Read-back: verify passes when R1 holds what was written, and names the
     field when R1 accepted a write but kept its own value. The plan phase
     marks sections R1 already matches as "reuse".

Usage:
    docker compose exec backend python scripts/test_venue_create.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sz_migration.venue_create import (
    apply_settings, build_settings_plan, create_venue, diff_section, fit_6g_outdoor, merge, public_plan,
    venue_draft_from_zone,
)
from services.sz_migration.venue_models import customise
import copy
from types import SimpleNamespace
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.apply_venue_settings as phase_mod
import workflow.phases.sz_migration.venue_config as venue_config_mod
from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot

MESH_PASS = "mesh-secret-passphrase"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


CF = {"channelSelectMode": "ChannelFly", "channelFlyChangeFrequency": 33}
ZONE = {
    "name": "Pinon Apartments",
    "countryCode": "us",
    "timezone": {"systemTimezone": "PST"},
    "radioConfig": {
        "dual5gEnabled": True,
        "radio24g": {"txPower": "Full", "channelWidth": 0, "channelRange": [11, 1, 6],
                     "autoChannelSelection": CF, "backgroundScanning": {"frequencyInSec": 20},
                     "protectionMode": "RTS_CTS"},
        "radio5g": {"txPower": "-3dB(1/2)", "channelWidth": 80, "channelRange": [36, 40, 149],
                    "channelRangeIndoor": [36, 40], "autoChannelSelection": CF, "channel": 0},
        "radio5gLower": {"txPower": "Full", "channelWidth": 40, "channelRange": [36, 40],
                         "channelRangeIndoor": [36], "autoChannelSelection": CF},
        "radio5gUpper": {"txPower": "Min", "channelWidth": 0, "channelRange": [149, 153],
                         "channelRangeIndoor": [149], "autoChannelSelection": CF},
        "radio6g": {"txPower": "-14dB", "channelWidth": 8080, "autoCellSizing": None,
                    "channelRange": [1, 5], "channelRangeIndoor": [1, 5, 9],
                    "bssMinRate": "HE-MCS0", "mgmtTxRate": "6 mbps", "afcEnabled": False,
                    "autoChannelSelection": {"channelSelectMode": "BackgroundScanning"}},
    },
    "apMgmtVlan": {"id": 10, "mode": "USER_DEFINED"},
    "mesh": {"ssid": "mesh-x", "passphrase": MESH_PASS, "meshRadioOption": "Radio5GOnly"},
    "loadBalancing": {"loadBalancingMethod": "BASED_ON_CLIENT_COUNT",
                      "bandBalancing": {"wifi24Percentage": 25}, "steeringMode": "BASIC"},
    "clientAdmissionControl24": {"maxRadioLoadPercent": 75, "minClientCount": 10, "minClientThroughputMbps": 0.0},
    "apRebootTimeout": {"gatewayLossTimeoutInSec": 1800, "serverLossTimeoutInSec": 7200},
    "bssColoringEnable": True,
    "dosBarringEnable": 1, "dosBarringThreshold": 5, "dosBarringCheckPeriod": 30, "dosBarringPeriod": 60,
    "rogue": {"reportType": "All"},
    "syslog": {"address": "10.0.0.1"},
    "channelEvaluationInterval": 600,
}


# Real zone per-model data (Lofts at Mercer), R510 given an 802.1X profile
# and LEDs off to exercise both paths.
ACCESS = {"name": "Default Access Port", "type": "AccessPort", "untagId": 1, "vlanMembers": "1",
          "_8021X": {"type": "Disable"}}
TRUNK = {"name": "Default Trunk Port(WAN)", "type": "TrunkPort", "untagId": 1, "vlanMembers": "1-4094",
         "_8021X": {"type": "Disable"}}
TRUNK_DOT1X = {**TRUNK, "name": "Trunk 802.1X", "_8021X": {"type": "PortBasedAuthenticator"}}
MODELS = {
    "H550": {"settings": {"ledStatusEnabled": True, "lldp": {"enabled": True}, "poeOutPortEnabled": False,
                          "usbPowerEnable": True, "poeModeSetting": "Auto",
                          "lanPorts": [{"portName": f"LAN{i}", "enabled": True, "ethPortProfile": {"id": "1"}}
                                       for i in range(1, 5)]
                          + [{"portName": "LAN5", "enabled": True, "ethPortProfile": {"id": "0"}}]},
             "eth_profiles": {"1": ACCESS, "0": TRUNK}},
    "R510": {"settings": {"ledStatusEnabled": False, "poeModeSetting": None,
                          "lanPorts": [{"portName": "LAN1", "enabled": True, "ethPortProfile": {"id": "2"}},
                                       {"portName": "LAN2", "enabled": True, "ethPortProfile": {"id": "0"}}]},
             "eth_profiles": {"2": TRUNK_DOT1X, "0": TRUNK}},
}


# R1's own defaults per model at the venue (GET apModelCapabilities), as
# R1 reports them for these models.
CAPS = [
    {"model": "H550", "ledOn": True, "canSupportPoeMode": True, "canSupportPoeOut": True,
     "poeModeCapabilities": ["Auto", "802.3af", "802.3at", "802.3bt-Class_5"],
     "lanPorts": [{"id": str(i), "defaultType": "ACCESS", "untagId": 1, "vlanMembers": "1", "supportDisable": True}
                  for i in range(1, 5)]
     + [{"id": "5", "defaultType": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "supportDisable": False}]},
    {"model": "R510", "ledOn": True, "canSupportPoeMode": True, "canSupportPoeOut": False,
     "requireOneEnabledTrunkPort": True, "poeModeCapabilities": ["Auto", "802.3af", "802.3at"],
     "lanPorts": [{"id": "1", "defaultType": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "supportDisable": True},
                  {"id": "2", "defaultType": "TRUNK", "untagId": 1, "vlanMembers": "1-4094", "supportDisable": False}]},
]
CAPS_BY_MODEL = {c["model"]: c for c in CAPS}

# The zone configured things R1 doesn't default to: H550 LAN3 on VLAN 30,
# plus a beta H375 R1 doesn't offer at the venue.
CUSTOM_ACCESS = {**ACCESS, "name": "Unit ports", "untagId": 30, "vlanMembers": "30"}
MODELS_CUSTOM = copy.deepcopy(MODELS)
MODELS_CUSTOM["H550"]["settings"]["lanPorts"][2]["ethPortProfile"] = {"id": "3"}
MODELS_CUSTOM["H550"]["eth_profiles"]["3"] = CUSTOM_ACCESS
MODELS_CUSTOM["H375"] = {"settings": {"ledStatusEnabled": False, "poeModeSetting": "Auto",
                                      "lanPorts": [{"portName": "LAN1", "enabled": True, "ethPortProfile": {"id": "0"}}]},
                         "eth_profiles": {"0": TRUNK}}


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeR1:
    ec_type = "MSP"

    def __init__(self, current=None, fail_paths=(), venues=(), ignore_paths=()):
        self.calls = []
        self.current = current or {}
        self.current.setdefault("apModelCapabilities", {"apModels": CAPS})
        self.fail_paths = fail_paths
        self.ignore_paths = ignore_paths
        self.venues = list(venues)

    def get(self, path, accept=None, override_tenant_id=None, params=None):
        self.calls.append(("get", path, accept, override_tenant_id, None))
        if path == "/venues":
            return Resp(200, self.venues)
        if path == "/venues/tags":
            return Resp(200, ["CID:1", "B", "CID:1"])
        return Resp(200, self.current.get(path.rsplit("/", 1)[1], {}))

    def put(self, path, payload=None, override_tenant_id=None, content_type=None):
        self.calls.append(("put", path, content_type, override_tenant_id, payload))
        if any(p in path for p in self.fail_paths):
            return Resp(400, {"errors": [{"message": "Invalid channel 149 for country"}]})
        if not any(p in path for p in self.ignore_paths):
            # R1 now holds it (passphrases aren't read back by R1 either)
            self.current[path.rsplit("/", 1)[1]] = payload
        return Resp(202, {"requestId": "r-" + path.rsplit("/", 1)[1]})

    def post(self, path, payload=None, override_tenant_id=None, params=None):
        self.calls.append(("post", path, None, override_tenant_id, payload))
        self.venues.append({"id": "new-venue-id", "name": payload["name"]})
        return Resp(202, {"requestId": "r-venue", "response": {"id": "new-venue-id", "name": payload["name"]}})

    async def await_task_completion(self, request_id, override_tenant_id=None, **kw):
        self.calls.append(("await", request_id, None, override_tenant_id, None))
        return {"status": "SUCCESS"}


def section(plan, key):
    return next((s for s in plan["sections"] if s.key == key), None)


def main() -> int:
    failures = 0
    plan = build_settings_plan(ZONE)
    radio = section(plan, "radio").body

    print("\n1. Radio encodings")
    b24, b5, b6, d5 = radio["radioParams24G"], radio["radioParams50G"], radio["radioParams6G"], radio["radioParamsDual5G"]
    failures += check("2.4: ChannelFly, AUTO, MAX, change freq 33, scan 20",
                      (b24["method"], b24["channelBandwidth"], b24["txPower"], b24["changeInterval"], b24["scanInterval"])
                      == ("CHANNELFLY", "AUTO", "MAX", 33, 20), str(b24))
    failures += check("2.4 channels sorted strings", b24["allowedChannels"] == ["1", "6", "11"], str(b24))
    failures += check("5: 80MHz, -3, indoor/outdoor lists",
                      (b5["channelBandwidth"], b5["txPower"], b5["allowedOutdoorChannels"], b5["allowedIndoorChannels"])
                      == ("80MHz", "-3", ["36", "40", "149"], ["36", "40"]), str(b5))
    failures += check("6: background scanning, rates, AFC; indoor list, no combined list",
                      (b6["method"], b6["bssMinRate6G"], b6["mgmtTxRate6G"], b6["enableAfc"],
                       b6.get("allowedIndoorChannels"), "allowedChannels" in b6)
                      == ("BACKGROUND_SCANNING", "HE_MCS_0", "6", False, ["1", "5", "9"], False), str(b6))

    # R1 reads back 6 GHz allowedChannels beside the indoor list and then
    # refuses both together; the write drops the combined list.
    from services.sz_migration.venue_create import drop_combined_channels
    rb = {"radioParams24G": {"allowedChannels": ["1", "6", "11"]},
          "radioParams6G": {"allowedChannels": ["1"], "allowedIndoorChannels": ["1"], "allowedOutdoorChannels": ["1"]}}
    drop_combined_channels(rb)
    failures += check("6 GHz combined list dropped, 2.4 GHz list kept",
                      "allowedChannels" not in rb["radioParams6G"] and rb["radioParams24G"]["allowedChannels"] == ["1", "6", "11"],
                      str(rb))

    failures += check("dual 5 GHz on, lower/upper carried, inherit off",
                      d5["enabled"] and d5["inheritParamsLower5G"] is False
                      and d5["radioParamsLower5G"]["channelBandwidth"] == "40MHz"
                      and d5["radioParamsUpper5G"]["txPower"] == "MIN"
                      and d5["radioParamsUpper5G"]["allowedOutdoorChannels"] == ["149", "153"], str(d5))
    off = build_settings_plan({**ZONE, "radioConfig": {**ZONE["radioConfig"], "dual5gEnabled": False}})
    d5off = section(off, "radio").body["radioParamsDual5G"]
    # R1 doesn't keep Lower/Upper lists while Dual 5 GHz is off: send its
    # defaults for the inherit flags, no lists, and note the zone's split plan.
    failures += check("dual 5 GHz off: inherit flags back to R1's defaults, no lower/upper lists sent",
                      d5off == {"enabled": False, "inheritParamsLower5G": True, "inheritParamsUpper5G": True}, str(d5off))
    failures += check("dual 5 GHz off: the zone's split plan is noted for whoever turns it on",
                      any("Lower 5 GHz 36, 40" in n and "Upper 5 GHz 149, 153" in n for n in section(off, "radio").notes),
                      str(section(off, "radio").notes))

    print("\n2. Unmappable values are noted, not sent")
    notes = " | ".join(section(plan, "radio").notes)
    failures += check("6 GHz 8080 width not sent", "channelBandwidth" not in b6, str(b6))
    failures += check("6 GHz -14dB not sent", "txPower" not in b6, str(b6))
    failures += check("notes name both + protection mode",
                      "8080" in notes and "-14dB" in notes and "RTS_CTS" in notes, notes)
    auto6 = build_settings_plan({"radioConfig": {"radio6g": {"autoCellSizing": True, "txPower": "Full"}}})
    failures += check("Auto power (auto cell sizing) not sent on 6 GHz",
                      "txPower" not in section(auto6, "radio").body.get("radioParams6G", {}))
    w24 = build_settings_plan({"radioConfig": {"radio24g": {"channelWidth": 80}}})
    failures += check("80 MHz not sent on 2.4 GHz",
                      "channelBandwidth" not in section(w24, "radio").body.get("radioParams24G", {}))
    nc = " | ".join(plan["not_carried"])
    failures += check("not-carried lists rogue, syslog, channel evaluation",
                      "Rogue" in nc and "Syslog" in nc and "600" in nc, nc)

    print("\n3. Other sections")
    failures += check("mgmt VLAN 10", section(plan, "mgmt_vlan").body == {"vlanId": 10})
    keep = build_settings_plan({"apMgmtVlan": {"id": 1, "mode": "KEEP"}})
    failures += check("KEEP mgmt VLAN: no section, noted",
                      section(keep, "mgmt_vlan") is None and "KEEP" in keep["not_carried"][0])
    lb = section(plan, "load_balancing").body
    failures += check("load balancing on, band balancing 25%",
                      lb == {"enabled": True, "loadBalancingMethod": "BASED_ON_CLIENT_COUNT",
                             "bandBalancingEnabled": True, "bandBalancingClientPercent24G": 25,
                             "steeringMode": "BASIC"}, str(lb))
    cac = section(plan, "cac").body
    failures += check("CAC 2.4 on with ints, 5 off",
                      cac == {"enable24G": True, "maxRadioLoad24G": 75, "minClientCount24G": 10,
                              "minClientThroughput24G": 0, "enable50G": False}, str(cac))
    dos = section(plan, "dos_protection").body
    failures += check("DoS protection", dos == {"enabled": True, "failThreshold": 5, "checkPeriod": 30,
                                                "blockingPeriod": 60}, str(dos))
    failures += check("no mesh section when SZ mesh is off", section(build_settings_plan({}), "mesh") is None)

    print("\n4. Mesh passphrase stays server-side")
    mesh = section(plan, "mesh")
    failures += check("mesh body has no passphrase", "passphrase" not in mesh.body, str(mesh.body))
    failures += check("public plan never contains it", MESH_PASS not in json.dumps(public_plan(plan)))
    failures += check("public plan has no request bodies",
                      all("body" not in s for s in public_plan(plan)["sections"]))

    print("\n5. Apply")
    failures += check("merge keeps unmentioned fields, replaces lists",
                      merge({"a": {"x": 1, "l": [1, 2]}, "b": 2}, {"a": {"l": [3]}})
                      == {"a": {"x": 1, "l": [3]}, "b": 2})
    current = {"apRadioSettings": {"radioParams24G": {"allowedChannels": ["1"], "txPower": "MIN",
                                                      "scanInterval": 5, "keepMe": True}},
               "apMeshSettings": {"enabled": False, "zeroTouchEnabled": False}}
    r1 = FakeR1(current=current, fail_paths=("apLoadBalancingSettings",))
    results = asyncio.run(apply_settings(r1, "ec-1", "v1", plan, ["radio", "mesh", "load_balancing"]))
    by = {r["key"]: r for r in results}
    puts = {c[1].rsplit("/", 1)[1]: c for c in r1.calls if c[0] == "put"}
    rp = puts["apRadioSettings"][4]["radioParams24G"]
    failures += check("radio overlaid on current (keepMe kept, power replaced)",
                      rp.get("keepMe") is True and rp["txPower"] == "MAX" and rp["allowedChannels"] == ["1", "6", "11"], str(rp))
    failures += check("radio + mesh written as v1.1",
                      puts["apRadioSettings"][2] == puts["apMeshSettings"][2] == "application/vnd.ruckus.v1.1+json")
    failures += check("mesh PUT carries the passphrase, current fields kept",
                      puts["apMeshSettings"][4].get("passphrase") == MESH_PASS
                      and puts["apMeshSettings"][4].get("zeroTouchEnabled") is False)
    failures += check("every call as the EC tenant", all(c[3] == "ec-1" for c in r1.calls), str(r1.calls))
    failures += check("failed section reported with R1's message, others applied",
                      by["load_balancing"]["status"] == "failed" and "Invalid channel" in by["load_balancing"]["error"]
                      and by["radio"]["status"] == by["mesh"]["status"] == "applied", str(results))
    failures += check("only chosen sections written", set(puts) == {"apRadioSettings", "apMeshSettings",
                                                                     "apLoadBalancingSettings"})

    print("\n6. Create")
    r1 = FakeR1(venues=[{"id": "x", "name": "Pinon Apartments"}])
    try:
        asyncio.run(create_venue(r1, "ec-1", {"name": " pinon apartments", "address": {}}))
        failures += check("duplicate name refused", False)
    except ValueError:
        failures += check("duplicate name refused (case/space-insensitive)", not any(c[0] == "post" for c in r1.calls))
    r1 = FakeR1()
    out = asyncio.run(create_venue(r1, "ec-1", {"name": "New", "address": {"city": "Houston", "country": "United States"},
                                                "tags": ["CID:1"]}))
    failures += check("created: id returned, 202 awaited",
                      out == {"id": "new-venue-id", "name": "New"} and any(c[0] == "await" for c in r1.calls), str(out))

    print("\n7. Draft")
    d = venue_draft_from_zone(ZONE)
    failures += check("PST not passed as time zone, noted", d["address"]["timezone"] is None and "PST" in d["notes"][0])
    failures += check("country code upper-cased", d["address"]["countryCode"] == "US")
    d = venue_draft_from_zone({"name": "x" * 70, "timezone": {"systemTimezone": "America/Chicago"}})
    failures += check("region time zone kept, long name shortened",
                      d["address"]["timezone"] == "America/Chicago" and len(d["name"]) == 63)

    print("\n8. 6 GHz outdoor fit")
    body = {"radioParams6G": {"allowedOutdoorChannels": ["1", "5", "97", "101", "129"]}}
    fit_6g_outdoor({"radioParams6G": {"allowedOutdoorChannels": ["1", "5", "93", "129", "157"]}}, body)
    failures += check("SZ channels limited to R1's outdoor list",
                      body["radioParams6G"]["allowedOutdoorChannels"] == ["1", "5", "129"], str(body))
    body = {"radioParams6G": {"allowedOutdoorChannels": ["97"]}}
    fit_6g_outdoor({"radioParams6G": {"allowedOutdoorChannels": ["1", "5"]}}, body)
    failures += check("none allowed -> R1's own list kept", body["radioParams6G"]["allowedOutdoorChannels"] == ["1", "5"])
    r1 = FakeR1(current={"apRadioSettings": {"radioParams6G": {"allowedOutdoorChannels": ["1", "5"]}}})
    asyncio.run(apply_settings(r1, "ec-1", "v1", plan, ["radio"]))
    sent = next(c[4] for c in r1.calls if c[0] == "put")
    failures += check("apply fits it before writing", sent["radioParams6G"]["allowedOutdoorChannels"] == ["1", "5"],
                      str(sent["radioParams6G"].get("allowedOutdoorChannels")))

    print("\n9. Stage: apply phase")
    snap = SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="Z", raw=ZONE), zone_ap_models=MODELS,
                               aps=[{"model": "H550"}])

    class R:
        reports = {}

        async def get(self, k):
            return snap.model_dump_json()

        async def hset(self, k, f, v):
            R.reports[f] = v

        async def expire(self, *a):
            pass

    async def fake_redis():
        return R()
    venue_config_mod.get_redis_client = fake_redis

    def run(cls, options, r1):
        phase = cls(PhaseContext(job_id="j", r1_client=r1, venue_id="v1", tenant_id="ec-1", options=options))
        async def emit(*a, **k):
            pass
        phase.emit = emit
        return asyncio.run(phase.execute(phase.Inputs(sz_snapshot_job_id="s")))

    full = build_settings_plan(ZONE, MODELS)
    r1 = FakeR1()
    out = run(phase_mod.ApplyVenueSettingsPhase, {}, r1)
    written = {c[1].rsplit("/", 1)[1] for c in r1.calls if c[0] == "put"}
    failures += check("default: every section applied",
                      out.applied == len(full["sections"]) and {"apRadioSettings", "apMeshSettings"} <= written,
                      str(written))
    failures += check("zone LAN ports all on R1's defaults: apModelLanPortSettings not written at all",
                      "apModelLanPortSettings" not in written, str(written))
    led_put = next((c[4] for c in r1.calls if c[0] == "put" and c[1].endswith("apModelLedSettings")), None)
    failures += check("LED PUT carries only the zone model that differs (R510 off)",
                      led_put == [{"model": "R510", "ledEnabled": False}], str(led_put))
    failures += check("apply results kept in the stage report", "apply" in R.reports)
    r1 = FakeR1()
    out = run(phase_mod.ApplyVenueSettingsPhase, {"venue_settings": []}, r1)
    failures += check("none chosen: skipped, nothing written", out.skipped and not r1.calls)
    r1 = FakeR1(fail_paths=("apLoadBalancingSettings",))
    try:
        run(phase_mod.ApplyVenueSettingsPhase, {"venue_settings": ["radio", "load_balancing"]}, r1)
        failures += check("R1 refusal fails the phase", False)
    except RuntimeError as e:
        failures += check("R1 refusal fails the phase with R1's message",
                          "1 of 2" in str(e) and "Invalid channel" in str(e), str(e))

    print("\n10. Per-AP-model settings")
    lan = section(full, "model_lan_ports")
    h550 = next(e for e in lan.body if e["model"] == "H550")
    r510 = next(e for e in lan.body if e["model"] == "R510")
    failures += check("H550: PoE Auto, PoE out off", h550.get("poeMode") == "Auto" and h550.get("poeOut") is False, str(h550))
    failures += check("H550: LAN1-4 access VLAN 1, LAN5 trunk 1-4094",
                      [(p["portId"], p["type"], p["untagId"], p["vlanMembers"]) for p in h550["lanPorts"]]
                      == [("1", "ACCESS", 1, "1"), ("2", "ACCESS", 1, "1"), ("3", "ACCESS", 1, "1"),
                          ("4", "ACCESS", 1, "1"), ("5", "TRUNK", 1, "1-4094")], str(h550["lanPorts"]))
    failures += check("R510: no PoE fields (SZ sets none), trunk ports",
                      "poeMode" not in r510 and all(p["type"] == "TRUNK" for p in r510["lanPorts"]))
    failures += check("LEDs per model", sorted(section(full, "model_leds").body, key=lambda e: e["model"])
                      == [{"model": "H550", "ledEnabled": True}, {"model": "R510", "ledEnabled": False}])
    failures += check("802.1X on a port profile noted, Disable not",
                      any("R510" in n and "802.1X" in n for n in lan.notes)
                      and not any("H550" in n and "802.1X" in n for n in lan.notes), str(lan.notes))
    nc = " | ".join(full["not_carried"])
    failures += check("LLDP and USB listed as not carried", "LLDP" in nc and "USB" in nc, nc)
    failures += check("no antenna/band sections when SZ sets none",
                      section(full, "model_antennas") is None and section(full, "model_band_mode") is None)
    failures += check("public plan has no bodies", all("body" not in x for x in public_plan(full)["sections"]))

    print("\n11. What is pushed: only zone models R1 offers, only where they differ from R1's defaults")
    lan_all_default = section(full, "model_lan_ports")
    push, notes, defaults = customise("model_lan_ports", lan_all_default.body, CAPS_BY_MODEL)
    failures += check("real zone (H550, R510) = R1 defaults: nothing pushed", push == [] and defaults == ["H550", "R510"],
                      str(push))
    custom = build_settings_plan(ZONE, MODELS_CUSTOM)
    push, notes, defaults = customise("model_lan_ports", section(custom, "model_lan_ports").body, CAPS_BY_MODEL)
    h550 = push[0] if push else {}
    failures += check("H550 pushed alone, as a complete entry (all 5 ports), LAN3 on VLAN 30",
                      [e["model"] for e in push] == ["H550"] and len(h550.get("lanPorts", [])) == 5
                      and h550["lanPorts"][2] == {"portId": "3", "enabled": True, "type": "ACCESS",
                                                  "untagId": 30, "vlanMembers": "30"}, str(push))
    failures += check("PoE Auto (R1's default) not sent", "poeMode" not in h550, str(h550))
    failures += check("H375 isn't offered by R1 at the venue: noted, never pushed",
                      any("H375" in n and "doesn't offer" in n for n in notes) and "H375" not in defaults, str(notes))
    led_push, _, _ = customise("model_leds", section(custom, "model_leds").body, CAPS_BY_MODEL)
    failures += check("LEDs: only R510 (off) pushed; H375 never", led_push == [{"model": "R510", "ledEnabled": False}],
                      str(led_push))
    odd = [{"model": "R510", "poeMode": "802.3bt-Class_5", "poeOut": True,
            "lanPorts": [{"portId": "2", "enabled": False}]}]
    push, notes, _ = customise("model_lan_ports", odd, CAPS_BY_MODEL)
    failures += check("unsupported PoE mode and PoE out noted; a port that can't be disabled stays enabled",
                      push == [] and any("isn't offered" in n for n in notes)
                      and any("PoE out" in n for n in notes) and any("can't be disabled" in n for n in notes),
                      f"{push} {notes}")
    no_trunk = [{"model": "R510", "lanPorts": [{"portId": "1", "enabled": False},
                                               {"portId": "2", "type": "ACCESS", "untagId": 5, "vlanMembers": "5"}]}]
    push, notes, _ = customise("model_lan_ports", no_trunk, CAPS_BY_MODEL)
    failures += check("no enabled trunk port left: R1 would refuse, so not pushed",
                      push == [] and any("one enabled trunk" in n for n in notes), f"{push} {notes}")
    diffs = diff_section(section(custom, "model_lan_ports"), [{"model": "H550", "lanPorts": h550["lanPorts"]}],
                         CAPS_BY_MODEL)
    failures += check("verify compares only what is pushed (R510 on defaults, H375 absent: no difference)",
                      diffs == [], str(diffs))

    print("\n12. Read-back")
    snap = SZMigrationSnapshot(zone=SZZoneSnapshot(id="z", name="Z", raw=ZONE), zone_ap_models=MODELS_CUSTOM,
                               aps=[{"model": "H550"}])
    full = build_settings_plan(ZONE, MODELS_CUSTOM)
    r1 = FakeR1()
    run(phase_mod.ApplyVenueSettingsPhase, {}, r1)
    out = run(venue_config_mod.VerifyVenueSettingsPhase, {}, r1)
    failures += check("verified when R1 holds what was written", out.verified == len(full["sections"]), str(out))
    r1 = FakeR1(ignore_paths=("apModelLanPortSettings",))
    run(phase_mod.ApplyVenueSettingsPhase, {}, r1)
    try:
        run(venue_config_mod.VerifyVenueSettingsPhase, {}, r1)
        failures += check("R1 keeping its own value fails verification", False)
    except RuntimeError as e:
        failures += check("R1 keeping its own value fails verification, naming the field",
                          "AP model LAN ports" in str(e) and "H550" in str(e), str(e)[:200])
    failures += check("verify results kept in the stage report even when failing", "verify" in R.reports)
    r1 = FakeR1()
    run(phase_mod.ApplyVenueSettingsPhase, {}, r1)
    lan_put = next(c[4] for c in r1.calls if c[0] == "put" and c[1].endswith("apModelLanPortSettings"))
    failures += check("the LAN-port PUT carries only H550 -- no other model, no H375",
                      [e["model"] for e in lan_put] == ["H550"], str([e["model"] for e in lan_put]))
    r1 = FakeR1()
    run(phase_mod.ApplyVenueSettingsPhase, {}, r1)
    plan_out = run(venue_config_mod.VenueConfigPlanPhase, {"venue_settings": ["radio", "model_leds"]}, r1)
    acts = {a.name: a.action for a in plan_out.validation_result.actions}
    failures += check("plan: sections R1 already matches are reuse, unticked are skip",
                      acts["Radio & channel plan"] == "reuse" and acts["AP model LEDs"] == "reuse"
                      and acts["Mesh"] == "skip", str(acts))
    plan_out = run(venue_config_mod.VenueConfigPlanPhase, {}, FakeR1())
    failures += check("plan: differences on a fresh venue are updates",
                      all(a.action == "update" for a in plan_out.validation_result.actions))

    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
