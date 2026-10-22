"""
SZ → R1 WLAN Field Mapping Registry

Shared foundation for both migration (building R1 payloads from SZ data)
and audit (comparing SZ values vs R1 actual values).

Each FieldMapping defines:
- Where to read the SZ value (dot-path into wlan.raw)
- Where the R1 equivalent lives (dot-path into R1 network object)
- How to normalize values between the two platforms
- UI grouping for the audit accordion

The r1_path encodes the R1 nesting level, following bulk_wlan_router.py's
field routing:
  wlan.{field}                                          → WLAN level
  wlan.advancedCustomization.radioCustomization.{field}  → radio sub-object
  wlan.advancedCustomization.clientIsolationOptions.{field} → isolation sub-object
  wlan.advancedCustomization.radiusOptions.{field}       → RADIUS options sub-object
  wlan.advancedCustomization.{field}                     → default advancedCustomization
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    section: str                         # Accordion section name
    label: str                           # Human-readable field name
    sz_path: str                         # Dot-path into SZ wlan.raw
    r1_path: Optional[str] = None        # Dot-path into R1 network object (None = SZ-only)
    # SZ value → R1 value: a lookup map (unmapped values pass through) or a function
    transform: Optional[Union[Dict, Callable[[Any], Any]]] = None
    sz_only: bool = False                # True if no R1 equivalent exists
    # Values R1 accepts, for enum fields. A value outside this set is left out
    # of the create payload and reported, rather than sent: R1 rejects the
    # whole network on one bad enum (WIFI-10001), so one unmapped SZ value
    # would otherwise fail every WLAN that carries it.
    r1_enum: Optional[FrozenSet[Any]] = None
    # Only migrate this field when the predicate holds for the SZ WLAN raw.
    only_if: Optional[Callable[[dict], bool]] = None
    # Inclusive numeric bounds R1 accepts (from the R1 spec). Out-of-range
    # values are left out and reported, same as a bad enum: R1 rejects the
    # whole network on one (WIFI-10200 "must be greater than or equal to").
    r1_range: Optional[Tuple[float, float]] = None
    # SZ often encodes "feature off" as a value (0) where R1 has a separate
    # enable flag. When the SZ value is in off_values, the R1 flag at
    # r1_toggle is set False and the value is not sent; otherwise the flag is
    # set True alongside it. Needed both ways: R1 defaults some of these ON.
    r1_toggle: Optional[str] = None
    off_values: Tuple[Any, ...] = ()


def _sz_rate_to_r1(value: Any) -> Any:
    """SZ bssMinRateMbps ("Disable", "12 mbps", "5.5 mbps") → R1 bssMinimumPhyRate ("default", "12", "5.5")."""
    if not isinstance(value, str):
        return value
    v = value.strip().lower()
    if v in ("disable", "disabled", "default", ""):
        return "default"
    return v.replace("mbps", "").strip()


# ═══════════════════════════════════════════════════════════════════════════
# WLAN Field Mappings (~60 fields across 11 sections)
# ═══════════════════════════════════════════════════════════════════════════

WLAN_FIELD_MAPPINGS: List[FieldMapping] = [

    # ── 1. Basic Settings ─────────────────────────────────────────────────
    FieldMapping("Basic Settings", "Network Name", "name", "name"),
    FieldMapping("Basic Settings", "SSID", "ssid", "wlan.ssid"),
    FieldMapping("Basic Settings", "Description", "description", "description"),

    # ── 2. Security & Encryption ──────────────────────────────────────────
    FieldMapping("Security & Encryption", "Security Mode", "encryption.method", "wlan.wlanSecurity",
                 transform={"WPA2": "WPA2-Personal", "WPA3": "WPA3", "None": "Open", "OPEN": "Open"}),
    FieldMapping("Security & Encryption", "Encryption Algorithm", "encryption.algorithm",
                 sz_only=True),
    FieldMapping("Security & Encryption", "Mgmt Frame Protection", "encryption.mfp",
                 "wlan.managementFrameProtection",
                 transform={"disabled": "Disabled", "capable": "Optional", "required": "Required"},
                 r1_enum=frozenset({"Disabled", "Optional", "Required"})),
    FieldMapping("Security & Encryption", "802.11r (Fast Roaming)", "encryption.support80211rEnabled",
                 "wlan.advancedCustomization.enableFastRoaming"),
    FieldMapping("Security & Encryption", "Mobility Domain ID", "encryption.mobilityDomainId",
                 "wlan.advancedCustomization.mobilityDomainId",
                 r1_range=(1, 65535)),
    FieldMapping("Security & Encryption", "GTK Rekey", "advancedOptions.gtkRekeyEnabled",
                 "wlan.advancedCustomization.enableGtkRekey"),
    FieldMapping("Security & Encryption", "Anti-Spoofing", "advancedOptions.antiSpoofingEnabled",
                 "wlan.advancedCustomization.enableAntiSpoofing"),
    FieldMapping("Security & Encryption", "Hide SSID", "advancedOptions.hideSsidEnabled",
                 "wlan.advancedCustomization.hideSsid"),

    # ── 3. VLAN & Network ─────────────────────────────────────────────────
    FieldMapping("VLAN & Network", "VLAN ID", "vlan.accessVlan", "wlan.vlanId"),
    FieldMapping("VLAN & Network", "AAA VLAN Override", "vlan.aaaVlanOverride",
                 "wlan.advancedCustomization.enableAaaVlanOverride"),
    FieldMapping("VLAN & Network", "VLAN Pooling", "vlan.vlanPooling",
                 sz_only=True),

    # ── 4. Authentication & RADIUS ────────────────────────────────────────
    FieldMapping("Authentication & RADIUS", "Auth Service", "authServiceOrProfile.name",
                 sz_only=True),
    FieldMapping("Authentication & RADIUS", "Accounting Service", "accountingServiceOrProfile.name",
                 sz_only=True),
    FieldMapping("Authentication & RADIUS", "MAC Auth", "macAuth.macAuthMacFormat",
                 "wlan.macAddressAuthentication"),
    # SZ: WLAN_BSSID | AP_MAC | Customized.  R1: BSSID | AP_MAC | USER | VENUE_NAME | AP_GROUP_NAME.
    FieldMapping("Authentication & RADIUS", "NAS ID Type",
                 "radiusOptions.nasIdType",
                 "wlan.advancedCustomization.radiusOptions.nasIdType",
                 transform={"WLAN_BSSID": "BSSID", "Customized": "USER"},
                 r1_enum=frozenset({"BSSID", "AP_MAC", "USER", "VENUE_NAME", "AP_GROUP_NAME"})),
    # SZ fills customizedNasId on every WLAN; it only means anything when the type is Customized.
    FieldMapping("Authentication & RADIUS", "Custom NAS ID",
                 "radiusOptions.customizedNasId",
                 "wlan.advancedCustomization.radiusOptions.userDefinedNasId",
                 only_if=lambda raw: get_nested(raw, "radiusOptions.nasIdType") == "Customized"),
    # SZ: WLAN_BSSID | AP_MAC | NONE | AP_GROUP | AP_NAME.  R1 has no AP_NAME.
    FieldMapping("Authentication & RADIUS", "Called-Station-ID Type",
                 "radiusOptions.calledStaIdType",
                 "wlan.advancedCustomization.radiusOptions.calledStationIdType",
                 transform={"WLAN_BSSID": "BSSID"},
                 r1_enum=frozenset({"BSSID", "AP_MAC", "NONE", "AP_GROUP"})),
    FieldMapping("Authentication & RADIUS", "NAS Request Timeout",
                 "radiusOptions.nasRequestTimeoutSec",
                 "wlan.advancedCustomization.radiusOptions.nasRequestTimeoutSec",
                 r1_range=(2, 20)),
    FieldMapping("Authentication & RADIUS", "NAS Max Retry",
                 "radiusOptions.nasMaxRetry",
                 "wlan.advancedCustomization.radiusOptions.nasMaxRetry",
                 r1_range=(2, 10)),
    FieldMapping("Authentication & RADIUS", "Single Session ID Accounting",
                 "radiusOptions.singleSessionIdAcctEnabled",
                 "wlan.advancedCustomization.radiusOptions.singleSessionIdAccounting"),

    # ── 5. Client Management ──────────────────────────────────────────────
    FieldMapping("Client Management", "Max Clients per Radio",
                 "advancedOptions.maxClientsPerRadio",
                 "wlan.advancedCustomization.maxClientsOnWlanPerRadio",
                 r1_range=(1, 512)),
    FieldMapping("Client Management", "Client Inactivity Timeout",
                 "advancedOptions.clientIdleTimeoutSec",
                 "wlan.advancedCustomization.clientInactivityTimeout",
                 r1_range=(60, 86400)),
    FieldMapping("Client Management", "Client Isolation",
                 "advancedOptions.clientIsolationEnabled",
                 "wlan.advancedCustomization.clientIsolation"),
    FieldMapping("Client Management", "Client Load Balancing",
                 "advancedOptions.clientLoadBalancingEnabled",
                 "wlan.advancedCustomization.clientLoadBalancingEnable"),
    FieldMapping("Client Management", "Transient Client Mgmt",
                 "advancedOptions.transientClientMgmtEnable",
                 "wlan.advancedCustomization.enableTransientClientManagement"),

    # ── 6. Radio & Spectrum ───────────────────────────────────────────────
    FieldMapping("Radio & Spectrum", "Wi-Fi 6 (802.11ax)",
                 "advancedOptions.wifi6Enabled",
                 "wlan.advancedCustomization.wifi6Enabled"),
    FieldMapping("Radio & Spectrum", "OFDM Only",
                 "advancedOptions.ofdmOnlyEnabled",
                 "wlan.advancedCustomization.radioCustomization.phyTypeConstraint",
                 transform={True: "OFDM", False: "NONE"},
                 r1_enum=frozenset({"OFDM", "NONE"})),
    FieldMapping("Radio & Spectrum", "Band Balancing",
                 "advancedOptions.bandBalancing",
                 "wlan.advancedCustomization.enableBandBalancing",
                 transform={"UseZoneSetting": True, "Disabled": False}),
    # SZ: "Disable" | "12 mbps" | ...  R1: default | 1 | 2 | 5.5 | 12 | 24.
    FieldMapping("Radio & Spectrum", "BSS Min Rate",
                 "advancedOptions.bssMinRateMbps",
                 "wlan.advancedCustomization.radioCustomization.bssMinimumPhyRate",
                 transform=_sz_rate_to_r1,
                 r1_enum=frozenset({"default", "1", "2", "5.5", "12", "24"})),
    # SZ: "2 mbps" ... R1: 1 | 2 | 5.5 | 6 | 9 | 11 | 12 | 18 | 24 | 36 | 48 | 54.
    # Must equal the BSS minimum rate when one is set -- see _reconcile_rates.
    FieldMapping("Radio & Spectrum", "Mgmt Tx Rate",
                 "advancedOptions.mgmtTxRateMbps",
                 "wlan.advancedCustomization.radioCustomization.managementFrameMinimumPhyRate",
                 transform=_sz_rate_to_r1,
                 r1_enum=frozenset({"1", "2", "5.5", "6", "9", "11", "12", "18", "24", "36", "48", "54"})),
    FieldMapping("Radio & Spectrum", "MLO (Wi-Fi 7)",
                 "advancedOptions.multiLinkOperationEnabled",
                 "wlan.advancedCustomization.multiLinkOperationEnabled"),

    # ── 7. Rate Limiting ──────────────────────────────────────────────────
    FieldMapping("Rate Limiting", "User Uplink Limit",
                 "advancedOptions.uplinkRate",
                 "wlan.advancedCustomization.userUplinkRateLimiting",
                 r1_range=(0, 200)),
    FieldMapping("Rate Limiting", "User Downlink Limit",
                 "advancedOptions.downlinkRate",
                 "wlan.advancedCustomization.userDownlinkRateLimiting",
                 r1_range=(0, 200)),
    FieldMapping("Rate Limiting", "Total Uplink Limit",
                 "firewallUplinkRateLimitingMbps",
                 "wlan.advancedCustomization.totalUplinkRateLimiting",
                 r1_range=(0, 500)),
    FieldMapping("Rate Limiting", "Total Downlink Limit",
                 "firewallDownlinkRateLimitingMbps",
                 "wlan.advancedCustomization.totalDownlinkRateLimiting",
                 r1_range=(0, 500)),
    FieldMapping("Rate Limiting", "Multicast DL Rate Limit",
                 "advancedOptions.multicastDownlinkRateLimitEnabled",
                 "wlan.advancedCustomization.enableMulticastDownlinkRateLimiting"),

    # ── 8. DHCP & IP ──────────────────────────────────────────────────────
    FieldMapping("DHCP & IP", "DHCP Option 82",
                 "advancedOptions.dhcpOption82Enabled",
                 "wlan.advancedCustomization.dhcpOption82Enabled"),
    FieldMapping("DHCP & IP", "Option 82 MAC Format",
                 "advancedOptions.dhcp82MacFormat",
                 "wlan.advancedCustomization.dhcpOption82MacFormat",
                 r1_enum=frozenset({"COLON", "HYPHEN", "NODELIMITER"})),
    FieldMapping("DHCP & IP", "Force Client DHCP",
                 "advancedOptions.forceClientDHCPTimeoutSec",
                 "wlan.advancedCustomization.forceMobileDeviceDhcp"),
    FieldMapping("DHCP & IP", "ARP Rate Limit",
                 "advancedOptions.arpRequestRateLimit",
                 "wlan.advancedCustomization.arpRequestRateLimit",
                 r1_range=(15, 100), off_values=(0,),
                 r1_toggle="wlan.advancedCustomization.enableArpRequestRateLimit"),
    FieldMapping("DHCP & IP", "DHCP Rate Limit",
                 "advancedOptions.dhcpRequestRateLimit",
                 "wlan.advancedCustomization.dhcpRequestRateLimit",
                 r1_range=(15, 100), off_values=(0,),
                 r1_toggle="wlan.advancedCustomization.enableDhcpRequestRateLimit"),

    # ── 9. Roaming & RSSI ─────────────────────────────────────────────────
    FieldMapping("Roaming & RSSI", "RSSI Threshold",
                 "advancedOptions.probeRssiThr",
                 "wlan.advancedCustomization.joinRSSIThreshold",
                 r1_range=(-90, -60), off_values=(0,),
                 r1_toggle="wlan.advancedCustomization.enableJoinRSSIThreshold"),
    FieldMapping("Roaming & RSSI", "Join Wait Time",
                 "advancedOptions.joinIgnoreTimeout",
                 "wlan.advancedCustomization.joinWaitTime",
                 r1_range=(1, 60)),
    FieldMapping("Roaming & RSSI", "Join Expire Time",
                 "advancedOptions.joinAcceptTimeout",
                 "wlan.advancedCustomization.joinExpireTime",
                 r1_range=(1, 300)),
    FieldMapping("Roaming & RSSI", "Join Wait Threshold",
                 "advancedOptions.joinIgnoreThr",
                 "wlan.advancedCustomization.joinWaitThreshold",
                 r1_range=(1, 50)),

    # ── 10. Advanced Features ─────────────────────────────────────────────
    FieldMapping("Advanced Features", "Proxy ARP",
                 "advancedOptions.proxyARPEnabled",
                 "wlan.advancedCustomization.proxyARP"),
    FieldMapping("Advanced Features", "802.11d",
                 "advancedOptions.support80211dEnabled",
                 "wlan.advancedCustomization.enableAdditionalRegulatoryDomains"),
    FieldMapping("Advanced Features", "802.11k Neighbor Report",
                 "advancedOptions.support80211kEnabled",
                 "wlan.advancedCustomization.enableNeighborReport"),
    FieldMapping("Advanced Features", "OCE",
                 "advancedOptions.oceEnabled",
                 "wlan.advancedCustomization.enableOptimizedConnectivityExperience"),
    FieldMapping("Advanced Features", "Application Visibility",
                 "advancedOptions.avcEnabled",
                 "wlan.advancedCustomization.applicationVisibilityEnabled"),
    FieldMapping("Advanced Features", "Airtime Decongestion",
                 "advancedOptions.hdOverheadOptimizeEnable",
                 "wlan.advancedCustomization.enableAirtimeDecongestion"),
    FieldMapping("Advanced Features", "BSS Priority",
                 "advancedOptions.priority",
                 "wlan.advancedCustomization.bssPriority",
                 transform={"High": "HIGH", "Low": "LOW"},
                 r1_enum=frozenset({"HIGH", "LOW"})),
    FieldMapping("Advanced Features", "Multicast Filter",
                 "advancedOptions.multicastFilterDrop",
                 "wlan.advancedCustomization.multicastFilterEnabled"),
    FieldMapping("Advanced Features", "Wi-Fi Calling",
                 "advancedOptions.wifiCallingEnabled",
                 "wlan.advancedCustomization.wifiCallingEnabled"),

    # ── 11. SZ-Only (No R1 Equivalent) ────────────────────────────────────
    FieldMapping("SZ-Only Settings", "DTIM Interval",
                 "advancedOptions.dtimInterval", sz_only=True),
    FieldMapping("SZ-Only Settings", "WLAN Schedule",
                 "schedule.type", sz_only=True),
    FieldMapping("SZ-Only Settings", "Session Timeout",
                 "advancedOptions.userSessionTimeout", sz_only=True),
    FieldMapping("SZ-Only Settings", "Directed Multicast",
                 "advancedOptions.directedMulticastEnabled", sz_only=True),
    FieldMapping("SZ-Only Settings", "Proxy ARP Force DHCP",
                 "advancedOptions.proxyARPForceDHCPEnabled", sz_only=True),
]

# Section display order for the UI
SECTION_ORDER = [
    "Basic Settings",
    "Security & Encryption",
    "VLAN & Network",
    "Authentication & RADIUS",
    "Client Management",
    "Radio & Spectrum",
    "Rate Limiting",
    "DHCP & IP",
    "Roaming & RSSI",
    "Advanced Features",
    "SZ-Only Settings",
]


# ═══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════

def get_nested(obj: Any, dot_path: str) -> Any:
    """
    Safely extract a value from a nested dict using a dot-separated path.

    >>> get_nested({"a": {"b": {"c": 42}}}, "a.b.c")
    42
    >>> get_nested({"a": 1}, "a.b.c") is None
    True
    """
    if obj is None:
        return None
    parts = dot_path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def set_nested(obj: dict, dot_path: str, value: Any) -> None:
    """
    Set a value in a nested dict, creating intermediate dicts as needed.

    >>> d = {}
    >>> set_nested(d, "a.b.c", 42)
    >>> d
    {'a': {'b': {'c': 42}}}
    """
    parts = dot_path.split(".")
    current = obj
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _apply_transform(value: Any, transform: Optional[Union[Dict, Callable[[Any], Any]]]) -> Any:
    """Apply a value transform (map or function). A map returns the original value if unmapped."""
    if transform is None or value is None:
        return value
    if callable(transform):
        return transform(value)
    try:
        return transform.get(value, value)
    except TypeError:  # unhashable SZ value (dict/list) -- nothing to map
        return value


def _enum_accepts(allowed: FrozenSet[Any], value: Any) -> bool:
    try:
        return value in allowed
    except TypeError:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Migration: Build R1 payload from SZ raw
# ═══════════════════════════════════════════════════════════════════════════

def build_r1_advanced_settings(sz_raw: dict, dropped: Optional[List[Dict[str, Any]]] = None) -> dict:
    """
    Build an R1 network creation payload fragment from SZ WLAN raw data.

    Iterates through WLAN_FIELD_MAPPINGS, extracts SZ values, applies
    transforms, and places them at the correct R1 path. Returns a nested
    dict ready to deep-merge into the R1 creation payload.

    Only includes fields that:
    - Have an r1_path (not SZ-only)
    - Have a non-None value in the SZ raw
    - Are NOT basic fields handled by the creation method itself
      (name, ssid, vlan, security type, passphrase)

    A value R1 would reject (outside the mapping's r1_enum) is left out, so
    R1 applies its default, and appended to `dropped` as
    {label, sz_value, reason} for the plan to show before anything runs.

    Returns:
        Dict like {"advancedCustomization": {"enableFastRoaming": True, ...}}
        ready to merge into wlan_settings.
    """
    if not sz_raw:
        return {}

    # Fields already handled by the R1 create methods — skip these
    SKIP_R1_PATHS = {"name", "description", "wlan.ssid", "wlan.vlanId", "wlan.wlanSecurity"}

    result: dict = {}

    for mapping in WLAN_FIELD_MAPPINGS:
        if mapping.sz_only or not mapping.r1_path:
            continue
        if mapping.r1_path in SKIP_R1_PATHS:
            continue

        if mapping.only_if and not mapping.only_if(sz_raw):
            continue

        sz_value = get_nested(sz_raw, mapping.sz_path)
        if sz_value is None:
            continue

        def drop(reason: str) -> None:
            if dropped is not None:
                dropped.append({"label": mapping.label, "sz_value": sz_value, "reason": reason})

        # "Off" in SZ is a value; in R1 it is an enable flag.
        if mapping.r1_toggle and sz_value in mapping.off_values:
            set_nested(result, _strip_wlan(mapping.r1_toggle), False)
            continue

        r1_value = _apply_transform(sz_value, mapping.transform)

        if mapping.r1_enum is not None and not _enum_accepts(mapping.r1_enum, r1_value):
            drop(
                f"R1 has no equivalent for SZ value {sz_value!r}; "
                f"R1's default will apply (accepts: {', '.join(sorted(map(str, mapping.r1_enum)))})"
            )
            continue

        if mapping.r1_range is not None and not _in_range(mapping.r1_range, r1_value):
            lo, hi = mapping.r1_range
            drop(f"SZ value {sz_value!r} is outside R1's range {lo:g}–{hi:g}; R1's default will apply")
            continue

        if mapping.r1_toggle:
            set_nested(result, _strip_wlan(mapping.r1_toggle), True)
        set_nested(result, _strip_wlan(mapping.r1_path), r1_value)

    _reconcile_rates(result, sz_raw, dropped)
    return result


def _strip_wlan(r1_path: str) -> str:
    """The caller merges into wlan_settings, so drop the leading "wlan."."""
    return r1_path[5:] if r1_path.startswith("wlan.") else r1_path


def _in_range(bounds: Tuple[float, float], value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return bounds[0] <= value <= bounds[1]


def _reconcile_rates(result: dict, sz_raw: dict, dropped: Optional[List[Dict[str, Any]]]) -> None:
    """
    R1 rule (WIFI-10107): the management Tx rate must equal the BSS minimum
    rate, except when OFDM-only is off and the BSS minimum is "default".
    SZ allows them to differ, so when a BSS minimum is set, R1 gets it as
    the management rate too, and a differing SZ value is reported.
    """
    radio = (result.get("advancedCustomization") or {}).get("radioCustomization") or {}
    bss = radio.get("bssMinimumPhyRate")
    if bss is None or bss == "default":
        if radio.get("phyTypeConstraint") == "OFDM" and "managementFrameMinimumPhyRate" in radio:
            # OFDM-only with no BSS minimum: leave R1's own (OFDM-safe) default.
            mgmt = radio.pop("managementFrameMinimumPhyRate")
            if dropped is not None and mgmt != "6":
                dropped.append({
                    "label": "Mgmt Tx Rate",
                    "sz_value": get_nested(sz_raw, "advancedOptions.mgmtTxRateMbps"),
                    "reason": "OFDM-only with no BSS minimum rate: R1's default management rate (6) applies",
                })
        return
    mgmt = radio.get("managementFrameMinimumPhyRate")
    if mgmt != bss:
        radio["managementFrameMinimumPhyRate"] = bss
        if dropped is not None and mgmt is not None:
            dropped.append({
                "label": "Mgmt Tx Rate",
                "sz_value": get_nested(sz_raw, "advancedOptions.mgmtTxRateMbps"),
                "reason": f"R1 requires the management rate to equal the BSS minimum rate; set to {bss}",
            })


# ═══════════════════════════════════════════════════════════════════════════
# Audit: Compare SZ vs R1 field-by-field
# ═══════════════════════════════════════════════════════════════════════════

# R1 field type and default (from the 08122026 spec), for every mapped R1
# path where the type or default matters. When R1 omits a field it is at
# its default, so the SZ value can still be compared; booleans are compared
# as on/off whatever each controller calls them.
R1_FIELD_INFO: Dict[str, Tuple[str, Any]] = {
    "wlan.managementFrameProtection": ("string", "Disabled"),
    "wlan.advancedCustomization.enableFastRoaming": ("boolean", False),
    "wlan.advancedCustomization.mobilityDomainId": ("integer", 1),
    "wlan.advancedCustomization.enableGtkRekey": ("boolean", True),
    "wlan.advancedCustomization.enableAntiSpoofing": ("boolean", False),
    "wlan.advancedCustomization.hideSsid": ("boolean", False),
    "wlan.vlanId": ("integer", 1),
    "wlan.advancedCustomization.enableAaaVlanOverride": ("boolean", True),
    "wlan.macAddressAuthentication": ("boolean", False),
    "wlan.advancedCustomization.radiusOptions.nasIdType": ("string", "BSSID"),
    "wlan.advancedCustomization.radiusOptions.calledStationIdType": ("string", "BSSID"),
    "wlan.advancedCustomization.radiusOptions.nasRequestTimeoutSec": ("integer", 3),
    "wlan.advancedCustomization.radiusOptions.nasMaxRetry": ("integer", 2),
    "wlan.advancedCustomization.radiusOptions.singleSessionIdAccounting": ("boolean", False),
    "wlan.advancedCustomization.maxClientsOnWlanPerRadio": ("integer", 100),
    "wlan.advancedCustomization.clientInactivityTimeout": ("integer", 120),
    "wlan.advancedCustomization.clientIsolation": ("boolean", False),
    "wlan.advancedCustomization.clientLoadBalancingEnable": ("boolean", True),
    "wlan.advancedCustomization.enableTransientClientManagement": ("boolean", False),
    "wlan.advancedCustomization.wifi6Enabled": ("boolean", True),
    "wlan.advancedCustomization.radioCustomization.phyTypeConstraint": ("string", "OFDM"),
    "wlan.advancedCustomization.enableBandBalancing": ("boolean", False),
    "wlan.advancedCustomization.radioCustomization.bssMinimumPhyRate": ("string", "default"),
    "wlan.advancedCustomization.radioCustomization.managementFrameMinimumPhyRate": ("string", "6"),
    "wlan.advancedCustomization.multiLinkOperationEnabled": ("boolean", False),
    "wlan.advancedCustomization.userUplinkRateLimiting": ("integer", 0),
    "wlan.advancedCustomization.userDownlinkRateLimiting": ("integer", 0),
    "wlan.advancedCustomization.totalUplinkRateLimiting": ("integer", 0),
    "wlan.advancedCustomization.totalDownlinkRateLimiting": ("integer", 0),
    "wlan.advancedCustomization.enableMulticastDownlinkRateLimiting": ("boolean", False),
    "wlan.advancedCustomization.dhcpOption82Enabled": ("boolean", False),
    "wlan.advancedCustomization.dhcpOption82MacFormat": ("string", "COLON"),
    "wlan.advancedCustomization.forceMobileDeviceDhcp": ("boolean", False),
    "wlan.advancedCustomization.arpRequestRateLimit": ("integer", 15),
    "wlan.advancedCustomization.dhcpRequestRateLimit": ("integer", 15),
    "wlan.advancedCustomization.joinRSSIThreshold": ("integer", -85),
    "wlan.advancedCustomization.joinWaitTime": ("integer", 30),
    "wlan.advancedCustomization.joinExpireTime": ("integer", 300),
    "wlan.advancedCustomization.joinWaitThreshold": ("integer", 10),
    "wlan.advancedCustomization.proxyARP": ("boolean", False),
    "wlan.advancedCustomization.enableAdditionalRegulatoryDomains": ("boolean", True),
    "wlan.advancedCustomization.enableNeighborReport": ("boolean", True),
    "wlan.advancedCustomization.enableOptimizedConnectivityExperience": ("boolean", False),
    "wlan.advancedCustomization.applicationVisibilityEnabled": ("boolean", False),
    "wlan.advancedCustomization.enableAirtimeDecongestion": ("boolean", False),
    "wlan.advancedCustomization.bssPriority": ("string", "HIGH"),
    "wlan.advancedCustomization.multicastFilterEnabled": ("boolean", False),
    "wlan.advancedCustomization.wifiCallingEnabled": ("boolean", False),
}
# R1 flags whose default is ON: if R1 omits the flag, the feature is on.
_TOGGLE_DEFAULT_ON = {
    "wlan.advancedCustomization.enableArpRequestRateLimit": True,
    "wlan.advancedCustomization.enableDhcpRequestRateLimit": True,
    "wlan.advancedCustomization.enableJoinRSSIThreshold": False,
}

_TRUE = {"true", "yes", "on", "enabled", "enable", "1"}
_FALSE = {"false", "no", "off", "disabled", "disable", "0", "none"}


def as_bool(v: Any) -> Optional[bool]:
    """True/False for anything either controller uses for on/off; None if it isn't one."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


def security_family(v: Any) -> Optional[str]:
    """
    The encryption protocol alone. R1's wlanSecurity folds in the auth type
    (WPA2Personal / WPA2Enterprise); the network type is compared separately.
    """
    if v is None:
        return None
    s = str(v).replace("_", "").replace("-", "").lower()
    for suffix in ("personal", "enterprise"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return {"wpa2": "WPA2", "wpa3": "WPA3", "wpa23mixed": "WPA2/WPA3", "wpamixed": "WPA", "wpa": "WPA",
            "none": "Open", "open": "Open", "owe": "OWE", "owetransition": "OWE transition",
            "wep64": "WEP", "wep128": "WEP", "wep": "WEP"}.get(s, str(v))


def _norm_string(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    s = s[1:] if s.startswith("_") else s          # spec defaults like "_default", "_6"
    return s.replace(" mbps", "").replace("mbps", "").upper()


@dataclass
class FieldComparison:
    section: str
    label: str
    sz_value: Any = None          # raw, as SZ reports it
    r1_value: Any = None          # raw, as R1 reports it
    match: bool = True            # status in (match, default)
    sz_only: bool = False
    # match | differs | default (both unset/at default) | review (SZ unset, R1 not at default) | sz_only
    status: str = "match"
    sz_display: Optional[str] = None   # both sides in one vocabulary, for display
    r1_display: Optional[str] = None
    note: str = ""


def _display(v: Any, kind: str) -> Optional[str]:
    if v is None:
        return None
    if kind == "boolean":
        b = as_bool(v)
        return "On" if b else "Off" if b is False else str(v)
    if kind == "security":
        return security_family(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def compare_fields(sz_raw: dict, r1_network: dict) -> List[FieldComparison]:
    """
    Compare SZ WLAN raw data against an R1 network object, field by field.

    Both sides are put into one vocabulary first -- on/off for booleans
    (Yes/true/Enabled/1 alike), the protocol family for security mode,
    case and "mbps" for enums -- so only real differences count. A field R1
    omits is at R1's default; a field SZ omits is unknown, so it only
    counts as aligned when R1 is at its default ("default"), otherwise it's
    flagged for review rather than called a mismatch.
    """
    comparisons: List[FieldComparison] = []

    for mapping in WLAN_FIELD_MAPPINGS:
        if mapping.only_if and not mapping.only_if(sz_raw):
            continue

        sz_value = get_nested(sz_raw, mapping.sz_path)

        if mapping.sz_only or not mapping.r1_path:
            comparisons.append(FieldComparison(
                section=mapping.section, label=mapping.label,
                sz_value=sz_value, sz_display=_display(sz_value, "string"),
                match=True, sz_only=True, status="sz_only",
            ))
            continue

        r1_raw = get_nested(r1_network, mapping.r1_path)
        kind, r1_default = R1_FIELD_INFO.get(mapping.r1_path, ("string", None))
        if mapping.r1_path == "wlan.wlanSecurity":
            kind = "security"
        fc = FieldComparison(section=mapping.section, label=mapping.label, sz_value=sz_value, r1_value=r1_raw)

        # ── SZ "0 = off" fields with an R1 enable flag ─────────────────
        if mapping.r1_toggle:
            flag = get_nested(r1_network, mapping.r1_toggle)
            r1_on = _TOGGLE_DEFAULT_ON.get(mapping.r1_toggle, False) if flag is None else bool(flag)
            r1_val = r1_raw if r1_raw is not None else r1_default
            fc.r1_display = f"On ({r1_val})" if r1_on else "Off"
            if sz_value is None:
                fc.sz_display = None
                fc.status = "default" if not r1_on or r1_raw is None else "review"
            else:
                sz_off = sz_value in mapping.off_values
                fc.sz_display = "Off" if sz_off else f"On ({_apply_transform(sz_value, mapping.transform)})"
                if sz_off or not r1_on:
                    fc.status = "match" if (sz_off and not r1_on) else "differs"
                else:
                    fc.status = "match" if _same(_apply_transform(sz_value, mapping.transform), r1_val, "integer") else "differs"
            fc.match = fc.status in ("match", "default")
            comparisons.append(fc)
            continue

        # ── Everything else ───────────────────────────────────────────
        sz_norm = _apply_transform(sz_value, mapping.transform)
        r1_effective = r1_raw if r1_raw is not None else r1_default
        if kind == "boolean" and sz_norm is not None and as_bool(sz_norm) is None and as_bool(sz_value) is not None:
            sz_norm = sz_value
        fc.sz_display = _display(sz_norm, kind)
        fc.r1_display = _display(r1_effective, kind)
        if r1_raw is None and r1_default is not None:
            fc.note = "R1 default"

        if sz_value is None:
            if r1_effective is None:
                fc.status = "default"
            elif r1_default is not None and _same(r1_effective, r1_default, kind):
                fc.status = "default"
                fc.note = "SZ doesn't report it; R1 is at its default"
            else:
                fc.status = "review"
                fc.note = "SZ doesn't report it; R1 is set to a non-default value"
        elif r1_effective is None:
            fc.status = "review"
            fc.note = "R1 doesn't report it"
        else:
            fc.status = "match" if _same(sz_norm, r1_effective, kind) else "differs"
        fc.match = fc.status in ("match", "default")
        comparisons.append(fc)

    return comparisons


def _same(a: Any, b: Any, kind: str) -> bool:
    if kind == "boolean":
        ba, bb = as_bool(a), as_bool(b)
        if ba is not None and bb is not None:
            return ba == bb
    if kind == "security":
        return security_family(a) == security_family(b)
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        pass
    return _norm_string(a) == _norm_string(b)
