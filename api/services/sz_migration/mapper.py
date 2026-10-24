"""
Security Type Mapper — M2

Maps SZ auth types to R1 network types for migration planning.

| SZ Auth Type              | R1 Network Type | Notes                                  |
|--------------------------|----------------|----------------------------------------|
| WPA2-PSK, WPA3-SAE, WPA-PSK | psk        | Direct mapping                          |
| Open                      | open           | Direct mapping                          |
| Open + Portal: Hotspot    | wispr          | R1 WISPr guest network built from the   |
|                           |                | SZ hotspot portal (+ its RADIUS auth)   |
| Open + Portal: other      | open           | Guest / web-auth portal not copied      |
| WPA2-Enterprise, WPA3-Enterprise | aaa    | Requires RADIUS profile in R1           |
| WPA-Enterprise            | aaa            | Legacy, same R1 handling                |
| DPSK (internal)           | dpsk           | Passphrase migration needed             |
| External DPSK (Cloudpath) | dpsk_external  | R1 DPSK network authenticated by the    |
|                           |                | RADIUS server; or converted to dpsk     |
| WPA2-PSK + Portal         | psk            | Portal not copied                       |
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
    r1_network_type: str  # psk | open | aaa | dpsk | dpsk_external | unsupported
    notes: str = ""
    needs_user_decision: bool = False
    dpsk_type: Optional[str] = None  # "internal" | "external" | None


# SZ WLAN types served by a hotspot (WISPr) portal.
HOTSPOT_WLAN_TYPES = ("Hotspot", "Hotspot_MacByPass")

# Static mapping table: SZ auth type → R1 network type
SZ_TO_R1_TYPE_MAP: Dict[str, str] = {
    "WPA2-PSK": "psk",
    "WPA3-SAE": "psk",
    "WPA-PSK": "psk",
    "WPA2-PSK + Portal": "psk",
    "WPA3": "psk",  # WPA3 without further classification = SAE personal
    "Open": "open",
    "Open + Portal": "open",
    "WPA2-Enterprise": "aaa",
    "WPA3-Enterprise": "aaa",
    "WPA-Enterprise": "aaa",
    "DPSK": "dpsk",  # Refined below based on external_dpsk
    "External DPSK": "dpsk_external",
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
    # External DPSK stays external by default: an R1 DPSK network that asks
    # the same RADIUS server (Cloudpath) for keys. Review can convert it to
    # R1's own DPSK service instead.
    if wlan.auth_type in ("DPSK", "External DPSK"):
        is_external = wlan.auth_type == "External DPSK" or _is_external_dpsk(wlan)
        if is_external:
            return R1NetworkTypeMapping(
                sz_auth_type=wlan.auth_type,
                r1_network_type="dpsk_external",
                notes="External DPSK (Cloudpath) — R1 DPSK network using the same RADIUS server; "
                      "can be converted to R1 DPSK on Review",
                dpsk_type="external",
            )
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="dpsk",
            notes="Internal DPSK — passphrase pool migration needed",
            dpsk_type="internal",
        )

    # ── Portal note ───────────────────────────────────────────────────
    if wlan.auth_type == "WPA2-PSK + Portal":
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="psk",
            notes="SZ portal (Hotspot/Guest) is not copied — created as a plain PSK network",
        )

    if wlan.auth_type == "Open + Portal":
        raw = wlan.raw or {}
        if raw.get("type") in HOTSPOT_WLAN_TYPES and (raw.get("portalServiceProfile") or {}).get("id"):
            # An SZ hotspot (WISPr) portal has an R1 equivalent: a WISPr guest
            # network with the same portal, walled garden, session times, key
            # and RADIUS auth (built as WLAN Grabber builds it).
            return R1NetworkTypeMapping(
                sz_auth_type=wlan.auth_type,
                r1_network_type="wispr",
                notes=f"SZ hotspot portal '{(raw.get('portalServiceProfile') or {}).get('name')}' -> "
                      "R1 WISPr guest network (portal, walled garden, session, RADIUS auth copied)",
            )
        return R1NetworkTypeMapping(
            sz_auth_type=wlan.auth_type,
            r1_network_type="open",
            notes="SZ guest / web-auth portal is not copied — created as a plain Open network",
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


def wlan_vlans(wlan: SZWLANFull, existing_network: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    The default (access) VLAN on each side, for Review.

    R1 gets the SZ access VLAN (1 when SZ has none) -- unless an R1 network
    of the same name already exists: the migration reuses it as it is, so
    its own VLAN is what R1 will have.
    """
    v = (wlan.raw or {}).get("vlan") or {}
    pool = v.get("vlanPooling") or {}
    notes: List[str] = []
    if pool.get("id") or pool.get("name"):
        notes.append(f"SZ uses VLAN pool '{pool.get('name') or pool.get('id')}' — R1 gets only the access VLAN")
    if v.get("aaaVlanOverride"):
        notes.append("SZ lets RADIUS assign the VLAN (AAA override)")
    sz_vlan = wlan.vlan_id
    if existing_network is not None:
        r1_vlan, source = existing_network.get("vlan"), "existing"
    else:
        r1_vlan, source = sz_vlan or 1, "create"
    return {"sz_vlan": sz_vlan, "r1_vlan": r1_vlan, "r1_vlan_source": source, "vlan_notes": notes}
