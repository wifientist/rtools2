"""
SmartZone Zones Service

Handles all zone (domain) related operations for SmartZone.
"""

from typing import Dict, List, Any


class ZoneService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_zones(self) -> List[Dict[str, Any]]:
        """
        Get all zones (domains) in the SmartZone

        Returns:
            List of zone objects with id, name, description
        """
        data = await self.client._request("GET", f"/{self.client.api_version}/rkszones")
        return data.get("list", [])
