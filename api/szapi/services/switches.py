"""
SmartZone Switches Service

Handles all switch-related operations for SmartZone.
"""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class SwitchService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def get_switch_groups_by_domain(
        self,
        domain_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all switch groups in a specific domain by querying switches
        and extracting unique switch group IDs

        Args:
            domain_id: Domain UUID

        Returns:
            List of switch group objects with id and label
        """
        # Get all switches in the domain
        switches = await self.get_switches_by_domain(domain_id)

        # Extract unique switch groups
        switch_groups_map = {}
        for switch in switches:
            sg_id = switch.get("switchGroupId")
            sg_name = switch.get("switchGroupName")

            if sg_id and sg_id not in switch_groups_map:
                switch_groups_map[sg_id] = {
                    "id": sg_id,
                    "label": sg_name or sg_id,
                    "name": sg_name or sg_id
                }

        switch_groups = list(switch_groups_map.values())
        logger.info(f"Found {len(switch_groups)} switch groups in domain {domain_id}")
        return switch_groups

    async def get_switchgroup_details(
        self,
        switchgroup_id: str
    ) -> Dict[str, Any]:
        """
        Get details for a specific switch group

        Args:
            switchgroup_id: Switch Group UUID

        Returns:
            Switch group detail object
        """
        endpoint = f"/{self.client.api_version}/group/{switchgroup_id}"
        return await self.client._request("GET", endpoint)

    async def get_all_switches(
        self,
        page: int = 1,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get all switches managed by SmartZone using POST with filters

        Args:
            page: Page number (default 1, SmartZone uses 1-based indexing)
            limit: Results per page (default 1000, max 1000)

        Returns:
            Dict with 'list' of switch objects and pagination info
        """
        endpoint = f"/{self.client.api_version}/switch"

        # SmartZone switch endpoint requires POST with request body
        body = {
            "page": page,
            "limit": min(limit, 1000)
        }

        return await self.client._request("POST", endpoint, json=body)

    async def get_switches_by_domain(
        self,
        domain_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all switches in a specific domain by fetching all switches
        and filtering by domain ID

        Args:
            domain_id: Domain UUID
            page: Page number (default 1)
            limit: Results per page (default 1000, max 1000)

        Returns:
            List of switch objects filtered by domain
        """
        # Get all switches (no filters)
        result = await self.get_all_switches(page=page, limit=limit)
        all_switches = result.get("list", [])

        # Filter by domain ID in Python
        domain_switches = [s for s in all_switches if s.get("domainId") == domain_id]

        logger.info(f"Retrieved {len(domain_switches)} switches from domain {domain_id}")
        return domain_switches

    async def get_switches_by_switchgroup(
        self,
        switchgroup_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all switches in a specific switch group by fetching all switches
        and filtering by switch group ID

        Args:
            switchgroup_id: Switch Group UUID
            page: Page number (default 1)
            limit: Results per page (default 1000, max 1000)

        Returns:
            List of switch objects filtered by switch group
        """
        # Get all switches (no filters)
        result = await self.get_all_switches(page=page, limit=limit)
        all_switches = result.get("list", [])

        # Filter by switch group ID in Python
        switchgroup_switches = [s for s in all_switches if s.get("switchGroupId") == switchgroup_id]

        logger.info(f"Retrieved {len(switchgroup_switches)} switches from switch group {switchgroup_id}")
        return switchgroup_switches

    async def get_all_switches_paginated(self) -> List[Dict[str, Any]]:
        """
        Get all switches across all pages

        Returns:
            List of all switch objects
        """
        all_switches = []
        page = 0

        while True:
            result = await self.get_all_switches(page=page)
            switches = result.get("list", [])

            if not switches:
                break

            all_switches.extend(switches)

            # Check if there are more pages
            total = result.get("totalCount", 0)
            has_more = result.get("hasMore", False)

            if not has_more or len(all_switches) >= total:
                break

            page += 1

        logger.info(f"Retrieved {len(all_switches)} total switches from SmartZone")
        return all_switches

    def format_switch_for_r1_import(self, switch: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format SmartZone switch data for RuckusONE import

        Args:
            switch: SmartZone switch object

        Returns:
            Formatted switch object ready for R1 import
        """
        return {
            "name": switch.get("name", ""),
            "description": switch.get("description", ""),
            "serial": switch.get("serialNumber", ""),
            "model": switch.get("model", ""),
            "mac": switch.get("mac", ""),
            "switchGroupId": switch.get("switchGroupId", ""),
            "switchGroupName": switch.get("switchGroupName", ""),
            "domainId": switch.get("domainId", ""),
            "ipAddress": switch.get("ipAddress", ""),
            "firmwareVersion": switch.get("firmwareVersion", ""),
            "status": switch.get("status", ""),
            # Add additional fields as needed
        }
