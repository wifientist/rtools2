"""
SmartZone AP Groups Service

Handles AP Group operations for SmartZone.
"""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class ApGroupService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_ap_groups_by_zone(
        self,
        zone_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all AP Groups in a specific zone

        Args:
            zone_id: Zone UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of AP Group objects
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/apgroups"

        params = {
            "index": (page - 1) * limit,
            "listSize": min(limit, 1000)
        }

        result = await self.client._request("GET", endpoint, params=params)
        ap_groups = result.get("list", [])

        logger.debug(f"Retrieved {len(ap_groups)} AP Groups from zone {zone_id}")
        return ap_groups

    async def get_ap_group_details(
        self,
        zone_id: str,
        ap_group_id: str
    ) -> Dict[str, Any]:
        """
        Get details for a specific AP Group

        Args:
            zone_id: Zone UUID
            ap_group_id: AP Group UUID

        Returns:
            AP Group detail object
        """
        endpoint = f"/{self.client.api_version}/rkszones/{zone_id}/apgroups/{ap_group_id}"
        return await self.client._request("GET", endpoint)

    async def get_all_ap_groups_paginated(
        self,
        zone_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all AP Groups in a zone across all pages

        Args:
            zone_id: Zone UUID

        Returns:
            List of all AP Group objects in the zone
        """
        all_ap_groups = []
        page = 1

        while True:
            ap_groups = await self.get_ap_groups_by_zone(zone_id, page=page)

            if not ap_groups:
                break

            all_ap_groups.extend(ap_groups)
            page += 1

            # Safety limit to prevent infinite loops
            if page > 100:
                logger.warning(f"Hit page limit (100) when fetching AP Groups for zone {zone_id}")
                break

        return all_ap_groups

    async def get_aps_in_group(
        self,
        zone_id: str,
        ap_group_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get APs that belong to a specific AP Group

        Note: This may need to filter from the full AP list if SZ doesn't
        provide a direct endpoint for this.

        Args:
            zone_id: Zone UUID
            ap_group_id: AP Group UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of AP objects in the group
        """
        # SZ API doesn't have a direct endpoint for APs by group
        # We need to fetch all APs and filter by apGroupId
        all_aps = await self.client.aps.get_aps_by_zone(zone_id)

        # Filter APs by AP Group ID
        group_aps = [
            ap for ap in all_aps
            if ap.get("apGroupId") == ap_group_id
        ]

        logger.debug(f"Found {len(group_aps)} APs in AP Group {ap_group_id}")
        return group_aps

    async def count_aps_per_group(
        self,
        zone_id: str,
        ap_groups: List[Dict[str, Any]] | None = None,
        all_aps: List[Dict[str, Any]] | None = None
    ) -> Dict[str, int]:
        """
        Count the number of APs in each AP Group for a zone

        Args:
            zone_id: Zone UUID
            ap_groups: Optional pre-fetched AP groups list
            all_aps: Optional pre-fetched APs list

        Returns:
            Dict mapping AP Group ID to AP count
        """
        # Fetch AP groups if not provided
        if ap_groups is None:
            ap_groups = await self.get_ap_groups_by_zone(zone_id)

        # Fetch all APs if not provided
        if all_aps is None:
            all_aps = await self.client.aps.get_aps_by_zone(zone_id)

        # Count APs per group
        group_counts = {}
        for ap_group in ap_groups:
            group_id = ap_group.get("id")
            group_counts[group_id] = 0

        for ap in all_aps:
            group_id = ap.get("apGroupId")
            if group_id in group_counts:
                group_counts[group_id] += 1

        return group_counts
