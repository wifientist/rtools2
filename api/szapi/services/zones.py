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

    async def get_zones(
        self,
        domain_id: str = None,
        paginate: bool = False,
        list_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get zones in the SmartZone

        Args:
            domain_id: Optional domain ID. If provided, returns zones within that domain.
                      If not provided, returns zones from current logon domain.
            paginate: If True, fetch all zones across multiple pages. Default False for backwards compat.
            list_size: Number of zones per page (default 100, max 1000)

        Returns:
            List of zone/domain objects with id, name, description
        """
        params = {"listSize": str(list_size)}
        if domain_id:
            params["domainId"] = domain_id

        if not paginate:
            data = await self.client._request("GET", f"/{self.client.api_version}/rkszones", params=params)
            return data.get("list", [])

        # Paginate through all zones
        all_zones = []
        index = 0

        while True:
            params["index"] = str(index)
            data = await self.client._request("GET", f"/{self.client.api_version}/rkszones", params=params)
            zones = data.get("list", [])
            total_count = data.get("totalCount", 0)

            all_zones.extend(zones)

            if len(all_zones) >= total_count or not zones:
                break

            index += len(zones)

        return all_zones

    async def get_zone_details(self, zone_id: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific zone

        Args:
            zone_id: Zone UUID

        Returns:
            Zone detail object including:
            - id, name, description
            - login, timezone, countryCode
            - latitude, longitude (location)
            - ipMode, ipv6IpMode
            - And other zone configuration details
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}"
        return await self.client._request("GET", endpoint)

    @staticmethod
    def extract_external_ip(zone_details: Dict[str, Any]) -> str | None:
        """
        Extract external/public IP from zone details

        The external IP may be in several places depending on zone configuration:
        - tunnel profile settings
        - AP registration settings

        Args:
            zone_details: Zone detail object from get_zone_details()

        Returns:
            External IP string or None if not configured
        """
        # Check for tunnel profile external IP
        tunnel_profile = zone_details.get("tunnelProfile", {})
        if tunnel_profile:
            tunnel_ip = tunnel_profile.get("tunnelMtuAutoEnabled")
            # Note: The actual external IP field may vary by SZ version

        # Check AP registration settings
        ap_registration = zone_details.get("apRegistrationRules", {})

        # Check for syslog/external reporting IP
        syslog = zone_details.get("syslog", {})
        if syslog:
            syslog_ip = syslog.get("primaryServer", {}).get("host")

        # SmartZone zone doesn't directly expose "external IP" but we can
        # check the AP control plane settings or simply return None
        # The cluster management IP is more relevant for external access

        return None  # Zone-level external IP typically not directly exposed

    async def get_all_zones_with_domains(self) -> List[Dict[str, Any]]:
        """
        Get all zones across all domains with domain information included

        Returns:
            List of zone objects with domain info attached
        """
        all_zones = []

        # First get all domains recursively
        domains = await self.get_domains(recursively=True, include_self=True)

        # For each domain, get zones
        for domain in domains:
            domain_id = domain.get("id")
            domain_name = domain.get("name")

            zones = await self.get_zones(domain_id=domain_id)

            for zone in zones:
                zone["_domain_id"] = domain_id
                zone["_domain_name"] = domain_name
                all_zones.append(zone)

        return all_zones
