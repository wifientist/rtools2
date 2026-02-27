"""
Security Type Mapper — M2

Maps SZ auth types to R1 network types for migration planning.

| SZ Auth Type              | R1 Network Type | Notes                                  |
|--------------------------|----------------|----------------------------------------|
| WPA2-PSK, WPA3-SAE, WPA-PSK | psk        | Direct mapping                          |
| Open, Open + Portal       | open           | Portal config needs manual post-migration |
| WPA2-Enterprise, WPA3-Enterprise | aaa    | Requires RADIUS profile in R1           |
| WPA-Enterprise            | aaa            | Legacy, same R1 handling                |
| DPSK (internal)           | dpsk           | Passphrase migration needed             |
| DPSK (external/Cloudpath) | dpsk or aaa    | User decides per WLAN                   |
| WEP                       | unsupported    | R1 does not support WEP                 |
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from schemas.sz_migration import SZWLANFull

logger = logging.getLogger(__name__)


@dataclass
class R1NetworkTypeMapping:
    """Result of mapping an SZ WLAN to an R1 network type."""
    sz_auth_type: str
    r1_network_type: str  # psk | open | aaa | dpsk | unsupported
    notes: str = ""
    needs_user_decision: bool = False
    dpsk_type: Optional[str] = None  # "internal" | "external" | None


# Static mapping table: SZ auth type → R1 network type
SZ_TO_R1_TYPE_MAP: Dict[str, str] = {
    "WPA2-PSK": "psk",
    "WPA3-SAE": "psk",
    "WPA-PSK": "psk",
    "WPA3": "psk",  # WPA3 without further classification = SAE personal
    "Open": "open",
    "Open + Portal": "open",
    "WPA2-Enterprise": "aaa",
    "WPA3-Enterprise": "aaa",
    "WPA-Enterprise": "aaa",
    "DPSK": "dpsk",  # Refined below based on external_dpsk
    "WEP": "unsupported",
}


def map_wlan_to_r1_type(wlan: SZWLANFull) -> R1NetworkTypeMapping:
    """
    Map a single SZ WLAN to its R1 network type.

    Args:
        wlan: Fully extracted SZ WLAN

    Returns:
        R1NetworkTypeMapping with type, notes, and decision flags
    """
    base_type = SZ_TO_R1_TYPE_MAP.get(wlan.auth_type)

    if base_type is None:
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="unsupported",
            notes=f"Unknown SZ auth type '{wlan.auth_type}' — manual configuration required",
        )

    # ── DPSK refinement ───────────────────────────────────────────────
    if wlan.auth_type == "DPSK":
        is_external = _is_external_dpsk(wlan)
        if is_external:
            return R1NetworkTypeMapping(
                sz_auth_type=wlan.auth_type,
                r1_network_type="dpsk",  # Default suggestion
                notes="External DPSK (Cloudpath) — may need 'aaa' type if using external RADIUS",
                needs_user_decision=True,
                dpsk_type="external",
            )
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="dpsk",
            notes="Internal DPSK — passphrase pool migration needed",
            dpsk_type="internal",
        )

    # ── Portal note ───────────────────────────────────────────────────
    if wlan.auth_type == "Open + Portal":
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="open",
            notes="Captive portal config requires manual setup in R1 after migration",
        )

    # ── Enterprise note ───────────────────────────────────────────────
    if base_type == "aaa":
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="aaa",
            notes="Requires RADIUS authentication profile in R1",
        )

    # ── WEP ───────────────────────────────────────────────────────────
    if base_type == "unsupported":
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="unsupported",
            notes="WEP is not supported in R1 — must upgrade to WPA2/WPA3",
        )

    # ── PSK / Open (straightforward) ─────────────────────────────────
    return R1NetworkTypeMapping(
        sz_auth_type=wlan.auth_type,
        r1_network_type=base_type,
    )


def map_all_wlans(wlans: List[SZWLANFull]) -> Dict[str, R1NetworkTypeMapping]:
    """
    Map all WLANs in a snapshot to R1 network types.

    Args:
        wlans: List of SZ WLANs from snapshot

    Returns:
        Dict keyed by WLAN ID → R1NetworkTypeMapping
    """
    mappings = {}
    for wlan in wlans:
        mappings[wlan.id] = map_wlan_to_r1_type(wlan)
    return mappings


def _is_external_dpsk(wlan: SZWLANFull) -> bool:
    """
    Detect if a DPSK WLAN uses external DPSK (Cloudpath).

    External DPSK indicators:
    - externalDpsk config block present and enabled
    - Auth service reference present (DPSK backed by external RADIUS)
    """
    if wlan.external_dpsk:
        if isinstance(wlan.external_dpsk, dict):
            if wlan.external_dpsk.get("enabled"):
                return True
    # DPSK with an auth service reference = external
    if wlan.auth_service_id:
        return True
    return False
