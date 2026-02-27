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

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    section: str                         # Accordion section name
    label: str                           # Human-readable field name
    sz_path: str                         # Dot-path into SZ wlan.raw
    r1_path: Optional[str] = None        # Dot-path into R1 network object (None = SZ-only)
    transform: Optional[Dict] = None     # SZ value → R1 value normalization map
    sz_only: bool = False                # True if no R1 equivalent exists


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
                 transform={"disabled": "Disabled", "capable": "Optional", "required": "Required"}),
    FieldMapping("Security & Encryption", "802.11r (Fast Roaming)", "encryption.support80211rEnabled",
                 "wlan.advancedCustomization.enableFastRoaming"),
    FieldMapping("Security & Encryption", "Mobility Domain ID", "encryption.mobilityDomainId",
                 "wlan.advancedCustomization.mobilityDomainId"),
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
    FieldMapping("Authentication & RADIUS", "NAS ID Type",
                 "radiusOptions.nasIdType",
                 "wlan.advancedCustomization.radiusOptions.nasIdType"),
    FieldMapping("Authentication & RADIUS", "Called-Station-ID Type",
                 "radiusOptions.calledStaIdType",
                 "wlan.advancedCustomization.radiusOptions.calledStationIdType",
                 transform={"WLAN_BSSID": "BSSID"}),
    FieldMapping("Authentication & RADIUS", "NAS Request Timeout",
                 "radiusOptions.nasRequestTimeoutSec",
                 "wlan.advancedCustomization.radiusOptions.nasRequestTimeoutSec"),
    FieldMapping("Authentication & RADIUS", "NAS Max Retry",
                 "radiusOptions.nasMaxRetry",
                 "wlan.advancedCustomization.radiusOptions.nasMaxRetry"),
    FieldMapping("Authentication & RADIUS", "Single Session ID Accounting",
                 "radiusOptions.singleSessionIdAcctEnabled",
                 "wlan.advancedCustomization.radiusOptions.singleSessionIdAccounting"),

    # ── 5. Client Management ──────────────────────────────────────────────
    FieldMapping("Client Management", "Max Clients per Radio",
                 "advancedOptions.maxClientsPerRadio",
                 "wlan.advancedCustomization.maxClientsOnWlanPerRadio"),
    FieldMapping("Client Management", "Client Inactivity Timeout",
                 "advancedOptions.clientIdleTimeoutSec",
                 "wlan.advancedCustomization.clientInactivityTimeout"),
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
                 transform={True: "OFDM", False: "NONE"}),
    FieldMapping("Radio & Spectrum", "Band Balancing",
                 "advancedOptions.bandBalancing",
                 "wlan.advancedCustomization.enableBandBalancing",
                 transform={"UseZoneSetting": True, "Disabled": False}),
    FieldMapping("Radio & Spectrum", "BSS Min Rate",
                 "advancedOptions.bssMinRateMbps",
                 "wlan.advancedCustomization.radioCustomization.bssMinimumPhyRate"),
    FieldMapping("Radio & Spectrum", "MLO (Wi-Fi 7)",
                 "advancedOptions.multiLinkOperationEnabled",
                 "wlan.advancedCustomization.multiLinkOperationEnabled"),

    # ── 7. Rate Limiting ──────────────────────────────────────────────────
    FieldMapping("Rate Limiting", "User Uplink Limit",
                 "advancedOptions.uplinkRate",
                 "wlan.advancedCustomization.userUplinkRateLimiting"),
    FieldMapping("Rate Limiting", "User Downlink Limit",
                 "advancedOptions.downlinkRate",
                 "wlan.advancedCustomization.userDownlinkRateLimiting"),
    FieldMapping("Rate Limiting", "Total Uplink Limit",
                 "firewallUplinkRateLimitingMbps",
                 "wlan.advancedCustomization.totalUplinkRateLimiting"),
    FieldMapping("Rate Limiting", "Total Downlink Limit",
                 "firewallDownlinkRateLimitingMbps",
                 "wlan.advancedCustomization.totalDownlinkRateLimiting"),
    FieldMapping("Rate Limiting", "Multicast DL Rate Limit",
                 "advancedOptions.multicastDownlinkRateLimitEnabled",
                 "wlan.advancedCustomization.enableMulticastDownlinkRateLimiting"),

    # ── 8. DHCP & IP ──────────────────────────────────────────────────────
    FieldMapping("DHCP & IP", "DHCP Option 82",
                 "advancedOptions.dhcpOption82Enabled",
                 "wlan.advancedCustomization.dhcpOption82Enabled"),
    FieldMapping("DHCP & IP", "Option 82 MAC Format",
                 "advancedOptions.dhcp82MacFormat",
                 "wlan.advancedCustomization.dhcpOption82MacFormat"),
    FieldMapping("DHCP & IP", "Force Client DHCP",
                 "advancedOptions.forceClientDHCPTimeoutSec",
                 "wlan.advancedCustomization.forceMobileDeviceDhcp"),
    FieldMapping("DHCP & IP", "ARP Rate Limit",
                 "advancedOptions.arpRequestRateLimit",
                 "wlan.advancedCustomization.arpRequestRateLimit"),
    FieldMapping("DHCP & IP", "DHCP Rate Limit",
                 "advancedOptions.dhcpRequestRateLimit",
                 "wlan.advancedCustomization.dhcpRequestRateLimit"),

    # ── 9. Roaming & RSSI ─────────────────────────────────────────────────
    FieldMapping("Roaming & RSSI", "RSSI Threshold",
                 "advancedOptions.probeRssiThr",
                 "wlan.advancedCustomization.joinRSSIThreshold"),
    FieldMapping("Roaming & RSSI", "Join Wait Time",
                 "advancedOptions.joinIgnoreTimeout",
                 "wlan.advancedCustomization.joinWaitTime"),
    FieldMapping("Roaming & RSSI", "Join Expire Time",
                 "advancedOptions.joinAcceptTimeout",
                 "wlan.advancedCustomization.joinExpireTime"),
    FieldMapping("Roaming & RSSI", "Join Wait Threshold",
                 "advancedOptions.joinIgnoreThr",
                 "wlan.advancedCustomization.joinWaitThreshold"),

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
                 transform={"High": "HIGH", "Low": "LOW"}),
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


def _apply_transform(value: Any, transform: Optional[Dict]) -> Any:
    """Apply a value transform map. Returns original value if no mapping found."""
    if transform is None or value is None:
        return value
    return transform.get(value, value)


# ═══════════════════════════════════════════════════════════════════════════
# Migration: Build R1 payload from SZ raw
# ═══════════════════════════════════════════════════════════════════════════

def build_r1_advanced_settings(sz_raw: dict) -> dict:
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

        sz_value = get_nested(sz_raw, mapping.sz_path)
        if sz_value is None:
            continue

        r1_value = _apply_transform(sz_value, mapping.transform)

        # Strip the "wlan." prefix — the caller merges this into wlan_settings
        r1_key = mapping.r1_path
        if r1_key.startswith("wlan."):
            r1_key = r1_key[5:]  # Remove "wlan."

        set_nested(result, r1_key, r1_value)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Audit: Compare SZ vs R1 field-by-field
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FieldComparison:
    section: str
    label: str
    sz_value: Any = None
    r1_value: Any = None
    match: bool = True
    sz_only: bool = False


def compare_fields(sz_raw: dict, r1_network: dict) -> List[FieldComparison]:
    """
    Compare SZ WLAN raw data against an R1 network object field-by-field.

    Returns a list of FieldComparison objects for every mapped field,
    indicating whether the values match (after transform normalization).
    """
    comparisons: List[FieldComparison] = []

    for mapping in WLAN_FIELD_MAPPINGS:
        sz_value = get_nested(sz_raw, mapping.sz_path)

        if mapping.sz_only or not mapping.r1_path:
            # SZ-only field — include for informational display
            comparisons.append(FieldComparison(
                section=mapping.section,
                label=mapping.label,
                sz_value=sz_value,
                r1_value=None,
                match=True,  # No mismatch possible — it's SZ-only
                sz_only=True,
            ))
            continue

        r1_value = get_nested(r1_network, mapping.r1_path)

        # Normalize the SZ value using the transform for comparison
        sz_normalized = _apply_transform(sz_value, mapping.transform)

        # Compare normalized values
        match = _values_match(sz_normalized, r1_value)

        comparisons.append(FieldComparison(
            section=mapping.section,
            label=mapping.label,
            sz_value=sz_value,
            r1_value=r1_value,
            match=match,
            sz_only=False,
        ))

    return comparisons


def _values_match(a: Any, b: Any) -> bool:
    """
    Compare two values with loose type coercion.

    Handles common mismatches like:
    - int vs str: 100 == "100"
    - bool vs str: True == "true"
    - None vs absent: both treated as None
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    # Direct equality
    if a == b:
        return True

    # String comparison (case-insensitive)
    try:
        if str(a).lower() == str(b).lower():
            return True
    except (ValueError, TypeError):
        pass

    # Numeric comparison
    try:
        if float(a) == float(b):
            return True
    except (ValueError, TypeError):
        pass

    return False
