"""
SmartZone Zones Service

Handles all zone (domain) related operations for SmartZone.
"""

from typing import Dict, List, Any


class ZoneService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_domains(
        self,
        index: int = 0,
        list_size: int = 1000,
        recursively: bool = False,
        include_self: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get domains from the SmartZone controller

        Args:
            index: The index of the first entry to be retrieved (default: 0)
            list_size: The maximum number of entries to be retrieved (default: 1000, max: 1000)
            recursively: Get domain list recursively (default: False)
            include_self: Get domain list include self (default: False)

        Returns:
            List of domain objects with id, name, description
        """
        params = {
            "index": str(index),
            "listSize": str(list_size)
        }

        if recursively:
            params["recursively"] = "true"
        if include_self:
            params["includeSelf"] = "true"

        data = await self.client._request("GET", f"/{self.client.api_version}/domains", params=params)
        return data.get("list", [])

    async def get_zones(self, domain_id: str = None) -> List[Dict[str, Any]]:
        """
        Get zones in the SmartZone

        Args:
            domain_id: Optional domain ID. If provided, returns zones within that domain.
                      If not provided, returns zones from current logon domain.

        Returns:
            List of zone/domain objects with id, name, description
        """
        params = {}
        if domain_id:
            params["domainId"] = domain_id

        data = await self.client._request("GET", f"/{self.client.api_version}/rkszones", params=params)
        return data.get("list", [])
