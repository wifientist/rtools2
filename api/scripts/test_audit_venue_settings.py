#!/usr/bin/env python3
"""
Migration audit, checkpoint 1b: SZ zone settings vs R1 venue settings.

The two controllers encode the same setting differently, so a naive
comparison reports differences that aren't there (or hides ones that are):

  * SZ "off" is an absent object; R1 has an enabled flag.
  * txPower "Full"/"-3dB(1/2)"/"Min" vs "MAX"/"-3"/"MIN"; -11..-23 dB has no R1 value.
  * channelWidth 0 (auto) / 40 / 8080 vs "AUTO"/"40MHz"/(nothing).
  * channel lists are ints on SZ, strings on R1.
  * 6 GHz BSS min rate "HE-MCS0" vs "HE_MCS_0"; mgmt rate "6 mbps" vs "6".

WHAT THIS GUARDS

  1. Equivalent settings in different encodings compare as matches.
  2. Real differences are reported; unrepresentable SZ values are "unmappable".
  3. Absent SZ objects compare as "off".
  4. An R1 section that failed to read is "unavailable", not "differs".
  5. Mesh passphrase: compared set/not-set only; never held after the fetch.
  6. MSP: every venue-settings read goes out as the EC tenant; v1.1 is
     requested for radio and mesh.
  7. Scorecard row counts only comparable rows.

Usage:
    docker compose exec backend python scripts/test_audit_venue_settings.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sz_migration.venue_settings import (
    compare_venue_settings, fetch_r1_venue_settings, tx_power, channel_width,
)
from services.sz_migration.audit_scorecard import _venue_config

MESH_PASS = "mesh-secret-passphrase"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


ZONE = {
    "countryCode": "US",
    "timezone": {"systemTimezone": "America/Chicago"},
    "radioConfig": {
        "dual5gEnabled": False,
        "radio24g": {"txPower": "Full", "channelWidth": 20, "channelRange": [1, 6, 11],
                     "autoChannelSelection": {"channelSelectMode": "BackgroundScanning"}},
        "radio5g": {"txPower": "-3dB(1/2)", "channelWidth": 0, "channelRange": [36, 40, 44, 48],
                    "channelRangeIndoor": [36, 40, 44, 48, 149],
                    "autoChannelSelection": {"channelSelectMode": "ChannelFly"}},
        "radio6g": {"txPower": "-14dB", "channelWidth": 8080, "bssMinRate": "HE-MCS0",
                    "mgmtTxRate": "6 mbps", "afcEnabled": True,
                    "autoChannelSelection": {"channelSelectMode": "BackgroundScanning"}},
    },
    "apMgmtVlan": {"mode": "USER_DEFINED", "id": 10},
    "mesh": {"ssid": "mesh-1", "passphrase": MESH_PASS, "meshRadioOption": "Radio56G"},
    "loadBalancing": {"loadBalancingMethod": "BASED_ON_CLIENT_COUNT",
                      "bandBalancing": {"wifi24Percentage": 25}, "steeringMode": "BASIC"},
    # clientAdmissionControl24/50 absent -> off
    "smartMonitor": {"intervalInSec": 10, "retryThreshold": 3},
    "bssColoringEnable": True,
}

R1 = {
    "_errors": [],
    "radio": {
        "radioParams24G": {"txPower": "MAX", "channelBandwidth": "20MHz", "allowedChannels": ["1", "6", "11"],
                           "method": "BACKGROUND_SCANNING"},
        "radioParams50G": {"txPower": "-3", "channelBandwidth": "AUTO", "allowedOutdoorChannels": ["36", "40", "44", "48"],
                           "allowedIndoorChannels": ["36", "40", "44", "48"], "method": "CHANNELFLY"},
        "radioParams6G": {"txPower": "MIN", "channelBandwidth": "160MHz", "bssMinRate6G": "HE_MCS_0",
                          "mgmtTxRate6G": "6", "enableAfc": True, "method": "BACKGROUND_SCANNING"},
        "radioParamsDual5G": {"enabled": False},
    },
    "mgmt_vlan": {"vlanId": 10},
    "mesh": {"enabled": True, "ssid": "mesh-1", "radioType": "5-6-GHz", "passphrase_set": True},
    "load_balancing": {"enabled": True, "loadBalancingMethod": "BASED_ON_CLIENT_COUNT", "bandBalancingEnabled": True,
                       "bandBalancingClientPercent24G": 25, "steeringMode": "BASIC"},
    "cac": {"enable24G": False, "enable50G": True},
    "rogue_policy": None,
    "smart_monitor": None,                       # failed to read
    "reboot_timeout": {},
    "bss_coloring": {"bssColoringEnabled": True},
    "directed_multicast": {},
    "venue_query": {"rogueDetection": {"enabled": False}},
}
VENUE = {"address": {"countryCode": "us", "timezone": "America/Chicago"}}


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


class FakeClient:
    ec_type = "MSP"

    def __init__(self):
        self.calls = []

    def get(self, path, params=None, override_tenant_id=None, accept=None):
        self.calls.append(("GET", path, override_tenant_id, accept))
        if path.endswith("/apMeshSettings"):
            return Resp({"enabled": True, "ssid": "m", "passphrase": MESH_PASS, "radioType": "5-GHz"})
        return Resp({})

    def post(self, path, payload=None, params=None, override_tenant_id=None):
        self.calls.append(("POST", path, override_tenant_id, None))
        return Resp({"data": [{"id": "v", "rogueDetection": {"enabled": True}}]})


async def main():
    failures = 0
    print("Audit venue settings (checkpoint 1b)")

    rows = {(r.section, r.label): r for r in compare_venue_settings(ZONE, VENUE, R1).rows}

    def st(sec, label):
        r = rows.get((sec, label))
        return r.status if r else None

    # 1
    for key in (("Venue", "Country code"), ("Radio 2.4 GHz", "Transmit power"), ("Radio 2.4 GHz", "Allowed channels"),
                ("Radio 5 GHz", "Transmit power"), ("Radio 5 GHz", "Channel width"), ("Radio 5 GHz", "Channel selection"),
                ("Radio 6 GHz", "BSS minimum rate"), ("Radio 6 GHz", "Management Tx rate"),
                ("AP management", "Management VLAN"), ("Mesh", "Mesh radio"), ("Load balancing", "Band balancing 2.4 GHz client %")):
        failures += check(f"{key[0]} / {key[1]} matches across encodings", st(*key) == "match", str(rows.get(key)))
    # 2
    failures += check("5 GHz indoor channels differ (149 missing in R1)",
                      st("Radio 5 GHz", "Allowed channels (indoor)") == "differs")
    failures += check("-14 dB is unmappable", st("Radio 6 GHz", "Transmit power") == "unmappable")
    failures += check("80+80 width is unmappable", st("Radio 6 GHz", "Channel width") == "unmappable")
    failures += check("rogue: SZ off, R1 off -> match", st("Rogue detection", "Rogue detection enabled") == "match")
    import copy
    z2 = copy.deepcopy(ZONE)
    z2["radioConfig"]["radio6g"].pop("afcEnabled")
    r1b = copy.deepcopy(R1)
    r1b["radio"]["radioParams6G"]["enableAfc"] = False
    afc = {(r.section, r.label): r for r in compare_venue_settings(z2, VENUE, r1b).rows}[("Radio 6 GHz", "AFC")]
    failures += check("AFC not set on SZ vs 'no' in R1 -> match (off both)",
                      afc.status == "match" and afc.sz_value is False and "Not set on SZ" in afc.note, str(afc))
    # 3
    failures += check("absent SZ CAC = off; R1 5 GHz on -> differs, 2.4 off -> match",
                      st("Client admission control", "5 GHz enabled") == "differs"
                      and st("Client admission control", "2.4 GHz enabled") == "match")
    # 4
    failures += check("unreadable R1 section -> unavailable", st("AP behaviour", "Smart Monitor") == "unavailable")
    # 5
    failures += check("mesh passphrase compared as set/not set", st("Mesh", "Mesh passphrase set") == "match")
    client = FakeClient()
    fetched = await fetch_r1_venue_settings(client, "ec-tenant-1", "v")
    failures += check("mesh passphrase never held after fetch",
                      MESH_PASS not in json.dumps(fetched) and fetched["mesh"]["passphrase_set"] is True)
    # 6
    failures += check("MSP: every read is made as the EC tenant",
                      client.calls and all(c[2] == "ec-tenant-1" for c in client.calls), str(client.calls[:3]))
    accepts = {c[1].rsplit("/", 1)[1]: c[3] for c in client.calls if c[0] == "GET"}
    failures += check("v1.1 for radio and mesh",
                      accepts.get("apRadioSettings") == "application/vnd.ruckus.v1.1+json"
                      and accepts.get("apMeshSettings") == "application/vnd.ruckus.v1.1+json")
    failures += check("rogue enabled read from the venue row", fetched["venue_query"]["rogueDetection"]["enabled"])
    # 7
    cat = _venue_config(compare_venue_settings(ZONE, VENUE, R1))
    comparable = [r for r in compare_venue_settings(ZONE, VENUE, R1).rows if r.status in ("match", "differs", "unmappable")]
    failures += check("scorecard counts comparable rows only",
                      cat.sz_count == len(comparable) and cat.status == "partial"
                      and any("could not be read" in d for d in cat.details), str(cat))

    # normalisers
    failures += check("txPower map", tx_power("Full") == "MAX" and tx_power("-9dB(1/8)") == "-9"
                      and tx_power("Min") == "MIN" and tx_power("-11dB").startswith("UNMAPPABLE"))
    failures += check("width map", channel_width(0) == "AUTO" and channel_width(80) == "80MHz")

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
