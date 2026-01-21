"""
SmartZone WLANs Service

Handles WLAN and WLAN Group operations for SmartZone.
"""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class WlanService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_wlans_by_zone(
        self,
        zone_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all WLANs in a specific zone

        Args:
            zone_id: Zone UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of WLAN objects
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/wlans"

        params = {
            "index": (page - 1) * limit,
            "listSize": min(limit, 1000)
        }

        result = await self.client._request("GET", endpoint, params=params)
        wlans = result.get("list", [])

        logger.debug(f"Retrieved {len(wlans)} WLANs from zone {zone_id}")
        return wlans

    async def get_wlan_details(
        self,
        zone_id: str,
        wlan_id: str
    ) -> Dict[str, Any]:
        """
        Get details for a specific WLAN

        Args:
            zone_id: Zone UUID
            wlan_id: WLAN UUID

        Returns:
            WLAN detail object
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/wlans/{wlan_id}"
        return await self.client._request("GET", endpoint)

    async def get_wlan_groups_by_zone(
        self,
        zone_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all WLAN Groups in a specific zone

        Args:
            zone_id: Zone UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of WLAN Group objects
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/wlangroups"

        params = {
            "index": (page - 1) * limit,
            "listSize": min(limit, 1000)
        }

        result = await self.client._request("GET", endpoint, params=params)
        wlan_groups = result.get("list", [])

        logger.debug(f"Retrieved {len(wlan_groups)} WLAN Groups from zone {zone_id}")
        return wlan_groups

    async def get_all_wlans_paginated(
        self,
        zone_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all WLANs in a zone across all pages

        Args:
            zone_id: Zone UUID

        Returns:
            List of all WLAN objects in the zone
        """
        all_wlans = []
        page = 1

        while True:
            wlans = await self.get_wlans_by_zone(zone_id, page=page)

            if not wlans:
                break

            all_wlans.extend(wlans)
            page += 1

            # Safety limit to prevent infinite loops
            if page > 100:
                logger.warning(f"Hit page limit (100) when fetching WLANs for zone {zone_id}")
                break

        return all_wlans

    @staticmethod
    def extract_auth_type(wlan: Dict[str, Any]) -> str:
        """
        Extract a human-readable authentication type from WLAN data

        SmartZone WLAN types:
        - Open: No encryption
        - Open + Captive Portal: Open with guest portal
        - WPA2-PSK: Pre-shared key
        - WPA2-Enterprise: 802.1X with RADIUS
        - WPA3-SAE: WPA3 personal (SAE)
        - WPA3-Enterprise: WPA3 with 802.1X
        - DPSK: Dynamic Pre-Shared Key (unique PSK per user/device)

        Args:
            wlan: WLAN object from SmartZone API

        Returns:
            Human-readable auth type string
        """
        # Handle None values - .get() returns None if key exists but value is None
        encryption = wlan.get("encryption") or {}
        method = encryption.get("method") or ""
        dpsk_config = wlan.get("dpsk") or {}
        auth_service = wlan.get("authServiceOrProfile") or {}

        # Check for DPSK first - this takes priority as it's a special WPA2 variant
        # DPSK can be detected by:
        # 1. dpsk.enabled = True
        # 2. encryption.method contains "DPSK" or "dpsk"
        # 3. dpsk.dpskEnabled = True (alternate field)
        if dpsk_config:
            if dpsk_config.get("enabled") or dpsk_config.get("dpskEnabled"):
                return "DPSK"

        # Some SmartZone versions use dpskEnabled at top level
        if wlan.get("dpskEnabled"):
            return "DPSK"

        # Check encryption method for DPSK indicator
        if method and "dpsk" in method.lower():
            return "DPSK"

        # Open network (no encryption)
        if method in ("None", "OPEN", "") or not method:
            # Check for captive portal / guest access
            portal = wlan.get("portalServiceProfile")
            if portal and (portal.get("id") or portal.get("name")):
                return "Open + Portal"
            return "Open"

        # WPA3 variants (check before WPA2 since WPA3 may include WPA2 in name)
        if "WPA3" in method.upper():
            # SAE = Simultaneous Authentication of Equals (WPA3 Personal)
            sae = encryption.get("sae") or {}
            if sae.get("enabled"):
                return "WPA3-SAE"
            # Check for enterprise (802.1X)
            if auth_service.get("id") or auth_service.get("name"):
                return "WPA3-Enterprise"
            # WPA3 with saePassphrase is WPA3-SAE (Personal)
            if encryption.get("saePassphrase"):
                return "WPA3-SAE"
            return "WPA3"

        # WPA2 variants
        if "WPA2" in method.upper() or method.upper() == "WPA_MIXED":
            # Check for enterprise (802.1X with RADIUS)
            if auth_service.get("id") or auth_service.get("name"):
                # Could also check throughController field
                return "WPA2-Enterprise"
            # PSK (passphrase-based)
            if encryption.get("passphrase"):
                return "WPA2-PSK"
            # Default WPA2 (likely PSK without passphrase visible)
            return "WPA2-PSK"

        # WPA (legacy)
        if "WPA" in method.upper():
            if auth_service.get("id"):
                return "WPA-Enterprise"
            return "WPA-PSK"

        # WEP (very legacy)
        if "WEP" in method.upper():
            return "WEP"

        # Fallback - return the method as-is or Unknown
        return method if method else "Unknown"

    @staticmethod
    def extract_encryption(wlan: Dict[str, Any]) -> str:
        """
        Extract encryption algorithm from WLAN data

        Args:
            wlan: WLAN object from SmartZone API

        Returns:
            Encryption algorithm string (AES, TKIP, etc.)
        """
        # Handle None values
        encryption = wlan.get("encryption") or {}
        algorithm = encryption.get("algorithm") or ""

        if not algorithm:
            method = encryption.get("method") or ""
            if method == "None" or not method:
                return "None"

        return algorithm if algorithm else "Unknown"

    @staticmethod
    def extract_vlan(wlan: Dict[str, Any]) -> int | None:
        """
        Extract VLAN ID from WLAN data

        Args:
            wlan: WLAN object from SmartZone API

        Returns:
            VLAN ID or None if not configured
        """
        # Handle None values
        vlan_config = wlan.get("vlan") or {}
        access_vlan = vlan_config.get("accessVlan")

        if access_vlan is not None:
            return int(access_vlan)

        return None
