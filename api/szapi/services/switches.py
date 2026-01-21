"""
SmartZone Switches Service

Handles all switch-related operations for SmartZone.
ICX Switch Management uses a separate API prefix: /switchm/api

Uses endpoints:
- GET /switchm/api/v11_1/group/ids/byDomain/{domainId} - Get switch group IDs in domain
- GET /switchm/api/v11_1/group/{groupId} - Get switch group details
- POST /switchm/api/v11_1/switch - List switches with filters
"""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)

# ICX Switch Management API uses a different base path
SWITCHM_API_PREFIX = "/switchm/api"


class SwitchService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    def _switchm_endpoint(self, path: str) -> str:
        """Build endpoint with switchm API prefix"""
        return f"{SWITCHM_API_PREFIX}/{self.client.api_version}{path}"

    async def get_switch_group_ids_by_domain(
        self,
        domain_id: str
    ) -> List[str]:
        """
        Get all switch group IDs in a specific domain

        Endpoint: GET /switchm/api/v11_1/group/ids/byDomain/{domainId}

        Args:
            domain_id: Domain UUID

        Returns:
            List of switch group ID strings
        """
        endpoint = self._switchm_endpoint(f"/group/ids/byDomain/{domain_id}")
        try:
            result = await self.client._request("GET", endpoint, use_root_url=True)
            # Response is typically {"list": ["id1", "id2", ...]} or just a list
            if isinstance(result, list):
                return result
            return result.get("list", [])
        except Exception as e:
            # 404 is expected if switch module not enabled - use debug level
            error_str = str(e).lower()
            if "404" in error_str or "not found" in error_str:
                logger.debug(f"Switch groups endpoint not available for domain {domain_id} (switch module may not be enabled)")
            else:
                logger.warning(f"Failed to get switch group IDs for domain {domain_id}: {e}")
            return []

    async def get_switch_groups_by_domain(
        self,
        domain_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all switch groups in a specific domain with details

        Args:
            domain_id: Domain UUID

        Returns:
            List of switch group objects with id, name, and details
        """
        # First get the list of switch group IDs
        group_ids = await self.get_switch_group_ids_by_domain(domain_id)

        if not group_ids:
            logger.debug(f"No switch groups found in domain {domain_id}")
            return []

        # Fetch details for each switch group
        switch_groups = []
        for group_id in group_ids:
            try:
                details = await self.get_switchgroup_details(group_id)
                switch_groups.append({
                    "id": group_id,
                    "name": details.get("name", group_id),
                    "label": details.get("name", group_id),
                    **details
                })
            except Exception as e:
                logger.warning(f"Failed to get details for switch group {group_id}: {e}")
                # Still include the group with just the ID
                switch_groups.append({
                    "id": group_id,
                    "name": group_id,
                    "label": group_id
                })

        logger.debug(f"Found {len(switch_groups)} switch groups in domain {domain_id}")
        return switch_groups

    async def get_switchgroup_details(
        self,
        switchgroup_id: str
    ) -> Dict[str, Any]:
        """
        Get details for a specific switch group

        Endpoint: GET /switchm/api/v11_1/group/{groupId}

        Args:
            switchgroup_id: Switch Group UUID

        Returns:
            Switch group detail object
        """
        endpoint = self._switchm_endpoint(f"/group/{switchgroup_id}")
        return await self.client._request("GET", endpoint, use_root_url=True)

    async def get_switches(
        self,
        domain_id: str = None,
        page: int = 1,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get switches using POST with filters

        Endpoint: POST /switchm/api/v11_1/switch

        Args:
            domain_id: Optional domain UUID to filter by
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            Dict with 'list' of switch objects and pagination info
        """
        endpoint = self._switchm_endpoint("/switch")

        # Build minimal request body - SmartZone switch API is sensitive to extra fields
        body = {
            "page": page,
            "limit": min(limit, 1000)
        }

        # Add domain filter if specified
        if domain_id:
            body["filters"] = [
                {
                    "type": "DOMAIN",
                    "value": domain_id
                }
            ]

        try:
            result = await self.client._request("POST", endpoint, use_root_url=True, json=body)
            logger.debug(f"Successfully fetched switches from {endpoint}")
            return result
        except Exception as e:
            # 404 is expected if switch module not enabled - use debug level
            error_str = str(e).lower()
            if "404" in error_str or "not found" in error_str:
                logger.debug(f"Switch endpoint not available (switch module may not be enabled)")
            else:
                logger.warning(f"Failed to fetch switches: {e}")
            return {"list": [], "totalCount": 0}

    async def get_all_switches(
        self,
        page: int = 1,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get all switches managed by SmartZone (no domain filter)

        Args:
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            Dict with 'list' of switch objects and pagination info
        """
        return await self.get_switches(domain_id=None, page=page, limit=limit)

    async def get_switches_by_domain(
        self,
        domain_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all switches in a specific domain using API filter

        Args:
            domain_id: Domain UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of switch objects in the domain
        """
        result = await self.get_switches(domain_id=domain_id, page=page, limit=limit)
        switches = result.get("list", [])
        logger.debug(f"Retrieved {len(switches)} switches from domain {domain_id}")
        return switches

    async def get_switches_by_switchgroup(
        self,
        switchgroup_id: str,
        page: int = 1,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all switches in a specific switch group

        Note: SmartZone API doesn't have a direct switch group filter,
        so we fetch all switches and filter by switchGroupId in Python

        Args:
            switchgroup_id: Switch Group UUID
            page: Page number (default 1)
            limit: Results per page (default 1000)

        Returns:
            List of switch objects in the switch group
        """
        # Get all switches (no domain filter - fetch all)
        result = await self.get_all_switches(page=page, limit=limit)
        all_switches = result.get("list", [])

        # Filter by switch group ID in Python
        switchgroup_switches = [
            s for s in all_switches
            if s.get("switchGroupId") == switchgroup_id or s.get("groupId") == switchgroup_id
        ]

        logger.debug(f"Retrieved {len(switchgroup_switches)} switches from switch group {switchgroup_id}")
        return switchgroup_switches

    async def get_all_switches_paginated(
        self,
        domain_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get all switches across all pages, optionally filtered by domain

        Args:
            domain_id: Optional domain UUID to filter by

        Returns:
            List of all switch objects
        """
        all_switches = []
        page = 1  # SmartZone uses 1-based pagination

        while True:
            result = await self.get_switches(domain_id=domain_id, page=page)
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
