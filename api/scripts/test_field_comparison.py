#!/usr/bin/env python3
"""
SZ vs R1 per-WLAN field comparison: same value, different words.

Built from a real audit row (209@The_riviera) where most "mismatches" were
the controllers phrasing the same thing differently -- Yes vs true, High vs
HIGH, WPA2 vs WPA2Personal, 0 vs No -- or one side simply not reporting a
field the other showed at its default.

WHAT THIS GUARDS

  1. Booleans compare as on/off whatever the words (Yes/true/Enabled/1/0).
  2. Security mode compares the protocol family (WPA2 == WPA2Personal).
  3. A field R1 omits is at R1's default; SZ is compared against that.
  4. A field SZ omits is "default" when R1 is at its default, "review" when
     not -- never a mismatch.
  5. SZ "0 = off" fields show Off / On (n) and compare the on/off state.
  6. Real differences stay differences (BSS min rate, mgmt rate, AAA
     VLAN override, R1 ARP limiting on while SZ had it off).
  7. Display values share one vocabulary; raw values are kept.

Usage:
    docker compose exec backend python scripts/test_field_comparison.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sz_migration.field_mappings import compare_fields


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


SZ = {
    "name": "209@The_riviera", "ssid": "209@The_riviera",
    "encryption": {"method": "WPA2", "algorithm": "AES", "mfp": "disabled", "support80211rEnabled": False},
    "vlan": {"accessVlan": 2015, "aaaVlanOverride": False},
    "advancedOptions": {
        "antiSpoofingEnabled": False, "hideSsidEnabled": False, "wifi6Enabled": True,
        "ofdmOnlyEnabled": True, "bandBalancing": "UseZoneSetting", "bssMinRateMbps": "24 mbps",
        "mgmtTxRateMbps": "24 mbps", "dhcp82MacFormat": "COLON", "forceClientDHCPTimeoutSec": 0,
        "arpRequestRateLimit": 0, "dhcpRequestRateLimit": 0, "probeRssiThr": 0, "priority": "High",
        "proxyARPEnabled": False,
    },
}
R1 = {
    "name": "209@The_riviera", "description": "Per-unit DPSK SSID for unit 209",
    "wlan": {
        "ssid": "209@The_riviera", "wlanSecurity": "WPA2Personal", "vlanId": 2015,
        "advancedCustomization": {
            "enableGtkRekey": True, "enableAntiSpoofing": False, "hideSsid": False,
            "enableAaaVlanOverride": True, "wifi6Enabled": True, "enableBandBalancing": True,
            "radioCustomization": {"phyTypeConstraint": "OFDM", "bssMinimumPhyRate": "default",
                                   "managementFrameMinimumPhyRate": "6"},
            "multiLinkOperationEnabled": False, "dhcpOption82Enabled": False, "dhcpOption82MacFormat": "COLON",
            "forceMobileDeviceDhcp": False, "arpRequestRateLimit": 15, "dhcpRequestRateLimit": 15,
            "joinRSSIThreshold": -85, "joinWaitTime": 30, "joinExpireTime": 300, "joinWaitThreshold": 10,
            "bssPriority": "HIGH", "proxyARP": False,
        },
    },
}


def main():
    failures = 0
    print("Field comparison vocabulary")
    rows = {c.label: c for c in compare_fields(SZ, R1)}

    def st(label):
        return rows[label].status

    same = ["Network Name", "SSID", "Security Mode", "Mgmt Frame Protection", "802.11r (Fast Roaming)",
            "Anti-Spoofing", "Hide SSID", "VLAN ID", "Wi-Fi 6 (802.11ax)", "OFDM Only", "Band Balancing",
            "Option 82 MAC Format", "Force Client DHCP", "RSSI Threshold", "BSS Priority", "Proxy ARP"]
    for label in same:
        failures += check(f"{label} aligns", st(label) == "match", f"{rows[label]}")
    for label in ("GTK Rekey", "MLO (Wi-Fi 7)", "DHCP Option 82", "Join Wait Time", "Join Expire Time",
                  "Join Wait Threshold"):
        failures += check(f"{label}: one side unset, other at default -> default", st(label) == "default", f"{rows[label]}")
    failures += check("Description: SZ none, R1 set -> review, not a mismatch", st("Description") == "review")
    for label in ("BSS Min Rate", "Mgmt Tx Rate", "AAA VLAN Override", "ARP Rate Limit", "DHCP Rate Limit"):
        failures += check(f"{label} is a real difference", st(label) == "differs", f"{rows[label]}")

    # display vocabulary
    failures += check("booleans displayed On/Off on both sides",
                      (rows["802.11r (Fast Roaming)"].sz_display, rows["802.11r (Fast Roaming)"].r1_display) == ("Off", "Off"))
    failures += check("security displayed as the protocol on both sides",
                      (rows["Security Mode"].sz_display, rows["Security Mode"].r1_display) == ("WPA2", "WPA2"))
    failures += check("0-means-off shown as Off vs On (15)",
                      (rows["ARP Rate Limit"].sz_display, rows["ARP Rate Limit"].r1_display) == ("Off", "On (15)"))
    failures += check("R1 omitted field shows its default with a note",
                      rows["Mgmt Frame Protection"].r1_display == "Disabled" and rows["Mgmt Frame Protection"].note == "R1 default")
    failures += check("raw values kept", rows["BSS Priority"].sz_value == "High" and rows["BSS Priority"].r1_value == "HIGH")
    diffs = [c.label for c in rows.values() if c.status == "differs"]
    failures += check("only the real differences remain", len(diffs) == 5, str(diffs))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
