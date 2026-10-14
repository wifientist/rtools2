#!/usr/bin/env python3
"""
SZ -> R1 network settings: every enum value sent must be one R1 accepts.

R1 rejects a whole network create on a single bad enum value (WIFI-10001,
"Invalid NasIdTypeEnum value: WLAN_BSSID"), and the same SZ default sits on
every WLAN in a zone -- so one unmapped value failed all seven networks of a
real migration before anything was created.

WHAT THIS GUARDS

  1. The values a real vSZ 7.1 zone produced (WLAN_BSSID, "Disable",
     "12 mbps", ...) all map to R1 values.
  2. NAS ID "Customized" becomes USER and carries the custom NAS ID; any other
     type does not send userDefinedNasId (SZ fills it on every WLAN).
  3. An SZ value with no R1 equivalent (Called-Station-ID AP_NAME) is left out
     and reported, not sent.
  4. Every mapping that declares r1_enum is honoured, for every value either
     SZ spec allows.

Usage:
    docker compose exec backend python scripts/test_migration_field_enums.py

Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sz_migration.field_mappings import (
    WLAN_FIELD_MAPPINGS, build_r1_advanced_settings, get_nested,
)


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def build(raw):
    dropped = []
    out = build_r1_advanced_settings(raw, dropped)
    return {"wlan": out}, dropped


def wlan(radius=None, advanced=None, mfp="disabled"):
    return {
        "radiusOptions": {"nasIdType": "WLAN_BSSID", "calledStaIdType": "WLAN_BSSID",
                          "customizedNasId": "zone-default", **(radius or {})},
        "advancedOptions": {"bssMinRateMbps": "Disable", "priority": "High",
                            "ofdmOnlyEnabled": False, **(advanced or {})},
        "encryption": {"mfp": mfp},
    }


RO = "wlan.advancedCustomization.radiusOptions"
RC = "wlan.advancedCustomization.radioCustomization"

# Every value the SZ v11.1 / v13.1 specs allow for the enum-mapped fields.
SZ_VALUES = {
    "radiusOptions.nasIdType": ["WLAN_BSSID", "AP_MAC", "Customized"],
    "radiusOptions.calledStaIdType": ["WLAN_BSSID", "AP_MAC", "NONE", "AP_GROUP", "AP_NAME"],
    "encryption.mfp": ["disabled", "capable", "required"],
    "advancedOptions.ofdmOnlyEnabled": [True, False],
    "advancedOptions.bssMinRateMbps": ["Disable", "1 mbps", "2 mbps", "5.5 mbps", "12 mbps", "24 mbps"],
    "advancedOptions.dhcp82MacFormat": ["COLON", "HYPHEN", "NODELIMITER"],
    "advancedOptions.priority": ["High", "Low"],
}


def main():
    failures = 0
    print("SZ -> R1 field enums")

    # 1. the real zone's defaults
    out, dropped = build(wlan())
    failures += check("WLAN_BSSID NAS ID -> BSSID", get_nested(out, f"{RO}.nasIdType") == "BSSID")
    failures += check("WLAN_BSSID Called-Station-ID -> BSSID",
                      get_nested(out, f"{RO}.calledStationIdType") == "BSSID")
    failures += check('BSS rate "Disable" -> default', get_nested(out, f"{RC}.bssMinimumPhyRate") == "default")
    out12, _ = build(wlan(advanced={"bssMinRateMbps": "12 mbps", "ofdmOnlyEnabled": True}))
    failures += check('BSS rate "12 mbps" -> 12', get_nested(out12, f"{RC}.bssMinimumPhyRate") == "12")
    failures += check("nothing dropped for a default zone", not dropped, str(dropped))

    # 2. custom NAS ID only travels with Customized
    failures += check("userDefinedNasId not sent for BSSID",
                      get_nested(out, f"{RO}.userDefinedNasId") is None)
    outc, _ = build(wlan(radius={"nasIdType": "Customized", "customizedNasId": "bldg-7"}))
    failures += check("Customized -> USER + userDefinedNasId",
                      get_nested(outc, f"{RO}.nasIdType") == "USER"
                      and get_nested(outc, f"{RO}.userDefinedNasId") == "bldg-7")

    # 3. no R1 equivalent: left out and reported
    outn, dropped = build(wlan(radius={"calledStaIdType": "AP_NAME"}))
    failures += check("AP_NAME Called-Station-ID is not sent",
                      get_nested(outn, f"{RO}.calledStationIdType") is None)
    failures += check("AP_NAME is reported as dropped",
                      any(d["label"] == "Called-Station-ID Type" for d in dropped), str(dropped))
    failures += check("other fields still migrate alongside a dropped one",
                      get_nested(outn, f"{RO}.nasIdType") == "BSSID")

    # 5. SZ "0 = off" -> R1 enable flag False, value not sent (R1 defaults ARP/DHCP ON)
    AC = "wlan.advancedCustomization"
    off, _ = build(wlan(advanced={"arpRequestRateLimit": 0, "dhcpRequestRateLimit": 0, "probeRssiThr": 0}))
    failures += check("SZ 0 rate limits/RSSI -> R1 flags off, no out-of-range value sent",
                      get_nested(off, f"{AC}.enableArpRequestRateLimit") is False
                      and get_nested(off, f"{AC}.enableDhcpRequestRateLimit") is False
                      and get_nested(off, f"{AC}.enableJoinRSSIThreshold") is False
                      and get_nested(off, f"{AC}.arpRequestRateLimit") is None
                      and get_nested(off, f"{AC}.joinRSSIThreshold") is None)
    on, _ = build(wlan(advanced={"arpRequestRateLimit": 30, "probeRssiThr": -85}))
    failures += check("set values -> R1 flag on plus value",
                      get_nested(on, f"{AC}.enableArpRequestRateLimit") is True
                      and get_nested(on, f"{AC}.arpRequestRateLimit") == 30
                      and get_nested(on, f"{AC}.enableJoinRSSIThreshold") is True
                      and get_nested(on, f"{AC}.joinRSSIThreshold") == -85)

    # 6. out of R1's range -> dropped and reported
    oor, dropped = build(wlan(advanced={"probeRssiThr": -40, "clientIdleTimeoutSec": 30}))
    failures += check("out-of-range values are not sent",
                      get_nested(oor, f"{AC}.joinRSSIThreshold") is None
                      and get_nested(oor, f"{AC}.clientInactivityTimeout") is None)
    failures += check("out-of-range values are reported",
                      {d["label"] for d in dropped} >= {"RSSI Threshold", "Client Inactivity Timeout"},
                      str(dropped))

    # 7. WIFI-10107: management rate must equal a set BSS minimum rate
    r12, _ = build(wlan(advanced={"bssMinRateMbps": "12 mbps", "ofdmOnlyEnabled": True, "mgmtTxRateMbps": "12 mbps"}))
    failures += check("BSS min 12 -> management 12",
                      get_nested(r12, f"{RC}.managementFrameMinimumPhyRate") == "12")
    rmis, dropped = build(wlan(advanced={"bssMinRateMbps": "24 mbps", "ofdmOnlyEnabled": True, "mgmtTxRateMbps": "12 mbps"}))
    failures += check("differing SZ management rate is forced to the BSS minimum and reported",
                      get_nested(rmis, f"{RC}.managementFrameMinimumPhyRate") == "24"
                      and any(d["label"] == "Mgmt Tx Rate" for d in dropped))
    rdef, _ = build(wlan(advanced={"mgmtTxRateMbps": "2 mbps"}))
    failures += check("no BSS minimum, OFDM off -> SZ management rate kept",
                      get_nested(rdef, f"{RC}.managementFrameMinimumPhyRate") == "2")

    # 8. every ranged mapping's value lands in range for a real-zone default WLAN
    default_out, _ = build(wlan())
    stray = [m.label for m in WLAN_FIELD_MAPPINGS if m.r1_range and m.r1_path
             and (v := get_nested(default_out, m.r1_path)) is not None
             and not m.r1_range[0] <= v <= m.r1_range[1]]
    failures += check("no value outside R1's range is ever sent", not stray, str(stray))

    # 4. exhaustive: every SZ value, every enum mapping
    by_path = {m.sz_path: m for m in WLAN_FIELD_MAPPINGS}
    violations = []
    for sz_path, values in SZ_VALUES.items():
        m = by_path[sz_path]
        for v in values:
            raw = wlan()
            section, key = sz_path.split(".", 1)
            raw[section][key] = v
            got, _ = build(raw)
            r1v = get_nested(got, m.r1_path)
            if r1v is not None and r1v not in m.r1_enum:
                violations.append(f"{m.label}: SZ {v!r} -> R1 {r1v!r}")
    failures += check("no SZ value produces an R1 value outside the enum",
                      not violations, "; ".join(violations))
    unguarded = [p for p in SZ_VALUES if by_path[p].r1_enum is None]
    failures += check("every enum-typed mapping declares r1_enum", not unguarded, str(unguarded))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
