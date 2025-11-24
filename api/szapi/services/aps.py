"""
SmartZone APs Service

Handles all AP (Access Point) related operations for SmartZone.
"""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class ApService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_aps_by_zone(
        self,
        zone_id: str,
        page: int = 0,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get all APs in a specific zone

        Args:
            zone_id: Zone/domain UUID
            page: Page number (default 0)
            limit: Results per page (default 1000, max 1000)

        Returns:
            Dict with 'list' of AP objects and pagination info
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/aps"
        params = {
            "index": page,
            "listSize": min(limit, 1000)
        }

        return await self.client._request("GET", endpoint, params=params)

    async def get_all_aps(self) -> List[Dict[str, Any]]:
        """
        Get all APs across all zones in the SmartZone

        Returns:
            List of all AP objects
        """
        all_aps = []

        # Get all zones
        zones = await self.client.zones.get_zones()

        # Get APs from each zone
        for zone in zones:
            zone_id = zone.get("id")
            zone_name = zone.get("name", "Unknown")

            logger.info(f"Fetching APs from zone: {zone_name} ({zone_id})")

            page = 0
            while True:
                result = await self.get_aps_by_zone(zone_id, page=page)

                aps = result.get("list", [])
                if not aps:
                    break

                # Add zone info to each AP
                for ap in aps:
                    ap["zoneName"] = zone_name
                    ap["zoneId"] = zone_id

                all_aps.extend(aps)

                # Check if there are more pages
                total = result.get("totalCount", 0)
                current_count = len(all_aps)

                if current_count >= total:
                    break

                page += 1

        logger.info(f"Retrieved {len(all_aps)} total APs from SmartZone")
        return all_aps

    async def get_ap_details(self, ap_mac: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific AP

        Args:
            ap_mac: AP MAC address

        Returns:
            AP detail object
        """
        endpoint = f"/{self.client.api_version}/aps/{ap_mac}"
        return await self.client._request("GET", endpoint)

    def format_ap_for_r1_import(self, ap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format SmartZone AP data for RuckusONE import

        Args:
            ap: SmartZone AP object

        Returns:
            Formatted AP object ready for R1 import
        """
        return {
            "name": ap.get("name", ""),
            "description": ap.get("description", ""),
            "serial": ap.get("serial", ""),
            "model": ap.get("model", ""),
            "mac": ap.get("mac", ""),
            "zoneName": ap.get("zoneName", ""),
            "location": ap.get("location", ""),
            "latitude": ap.get("latitude"),
            "longitude": ap.get("longitude"),
            "gpsSource": ap.get("gpsSource"),
            # Add additional fields as needed
        }
