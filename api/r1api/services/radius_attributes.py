"""
RADIUS Attribute Group Management Service

Manages RADIUS attributes and attribute groups for use with
adaptive policies in DPSK and per-unit SSID configurations.
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class RadiusAttributeService:
    """
    Service for managing RADIUS Attributes and Attribute Groups in RuckusONE.

    RADIUS Attribute Groups can be attached to:
    - Adaptive Policies (to return specific RADIUS attributes)
    - DPSK pools
    - Identity configurations

    Typical use cases:
    - Assigning VLANs via RADIUS attributes
    - Setting bandwidth limits
    - Applying QoS policies
    """

    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    # ========== RADIUS Attributes (Read-only lookups) ==========

    async def get_radius_attributes(
        self,
        tenant_id: str = None
    ):
        """
        Get all available RADIUS attributes

        Args:
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            List of available RADIUS attributes
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                "/radiusAttributes",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get("/radiusAttributes").json()

    async def query_radius_attributes(
        self,
        tenant_id: str = None,
        filters: dict = None,
        search_string: str = None,
        page: int = 0,
        limit: int = 100
    ):
        """
        Query RADIUS attributes with filtering

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Optional filters
            search_string: Optional search string
            page: Page number (0-based)
            limit: Number of results per page

        Returns:
            Query response with RADIUS attributes
        """
        body = {
            "page": page,
            "limit": limit
        }

        if filters:
            body["filters"] = filters
        if search_string:
            body["searchString"] = search_string

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                "/radiusAttributes/query",
                payload=body,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                "/radiusAttributes/query",
                payload=body
            ).json()

    async def get_radius_attribute(
        self,
        attribute_id: str,
        tenant_id: str = None
    ):
        """
        Get a specific RADIUS attribute by ID

        Args:
            attribute_id: RADIUS attribute ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            RADIUS attribute details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/radiusAttributes/{attribute_id}",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/radiusAttributes/{attribute_id}"
            ).json()

    async def get_radius_attribute_vendors(
        self,
        tenant_id: str = None
    ):
        """
        Get list of RADIUS attribute vendors (Ruckus, Cisco, etc.)

        Args:
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            List of vendor names/IDs
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                "/radiusAttributes/vendors",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get("/radiusAttributes/vendors").json()

    # ========== RADIUS Attribute Groups ==========

    async def get_radius_attribute_groups(
        self,
        tenant_id: str = None
    ):
        """
        Get all RADIUS attribute groups

        Args:
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            List of RADIUS attribute groups
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                "/radiusAttributeGroups",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get("/radiusAttributeGroups").json()

    async def query_radius_attribute_groups(
        self,
        tenant_id: str = None,
        filters: dict = None,
        search_string: str = None,
        page: int = 0,
        limit: int = 100
    ):
        """
        Query RADIUS attribute groups with filtering

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Optional filters
            search_string: Optional search string
            page: Page number (0-based)
            limit: Number of results per page

        Returns:
            Query response with RADIUS attribute groups
        """
        body = {
            "page": page,
            "limit": limit
        }

        if filters:
            body["filters"] = filters
        if search_string:
            body["searchString"] = search_string

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                "/radiusAttributeGroups/query",
                payload=body,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                "/radiusAttributeGroups/query",
                payload=body
            ).json()

    async def get_radius_attribute_group(
        self,
        group_id: str,
        tenant_id: str = None
    ):
        """
        Get a specific RADIUS attribute group

        Args:
            group_id: RADIUS attribute group ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            RADIUS attribute group details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}"
            ).json()

    async def create_radius_attribute_group(
        self,
        name: str,
        attributes: List[Dict[str, Any]],
        tenant_id: str = None,
        description: str = None
    ):
        """
        Create a new RADIUS attribute group

        Args:
            name: Group name
            attributes: List of attribute configurations, e.g.:
                [
                    {"attributeId": "...", "value": "100"},  # VLAN
                    {"attributeId": "...", "value": "10"}    # Bandwidth
                ]
            tenant_id: Tenant/EC ID (required for MSP)
            description: Optional description

        Returns:
            Created RADIUS attribute group
        """
        payload = {
            "name": name,
            "attributes": attributes
        }

        if description:
            payload["description"] = description

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                "/radiusAttributeGroups",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                "/radiusAttributeGroups",
                payload=payload
            )

        return response.json()

    async def update_radius_attribute_group(
        self,
        group_id: str,
        tenant_id: str = None,
        name: str = None,
        attributes: List[Dict[str, Any]] = None,
        description: str = None
    ):
        """
        Update a RADIUS attribute group

        Args:
            group_id: RADIUS attribute group ID
            tenant_id: Tenant/EC ID (required for MSP)
            name: Optional new name
            attributes: Optional new attribute list
            description: Optional new description

        Returns:
            Updated RADIUS attribute group
        """
        payload = {}

        if name:
            payload["name"] = name
        if attributes is not None:
            payload["attributes"] = attributes
        if description:
            payload["description"] = description

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.patch(
                f"/radiusAttributeGroups/{group_id}",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.patch(
                f"/radiusAttributeGroups/{group_id}",
                payload=payload
            )

        return response.json()

    async def delete_radius_attribute_group(
        self,
        group_id: str,
        tenant_id: str = None
    ):
        """
        Delete a RADIUS attribute group

        Args:
            group_id: RADIUS attribute group ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Deletion response
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/radiusAttributeGroups/{group_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/radiusAttributeGroups/{group_id}"
            )

        return response.json() if response.content else {"status": "deleted"}

    # ========== RADIUS Attribute Group Assignments ==========

    async def get_group_assignments(
        self,
        group_id: str,
        tenant_id: str = None
    ):
        """
        Get external assignments for a RADIUS attribute group
        (shows where this group is being used)

        Args:
            group_id: RADIUS attribute group ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            List of assignments (policies, DPSK pools, etc.)
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}/assignments",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}/assignments"
            ).json()

    async def get_group_assignment(
        self,
        group_id: str,
        assignment_id: str,
        tenant_id: str = None
    ):
        """
        Get a specific assignment

        Args:
            group_id: RADIUS attribute group ID
            assignment_id: Assignment ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Assignment details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}/assignments/{assignment_id}",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/radiusAttributeGroups/{group_id}/assignments/{assignment_id}"
            ).json()

    async def create_group_assignment(
        self,
        group_id: str,
        assignment_data: dict,
        tenant_id: str = None
    ):
        """
        Create an external assignment for a RADIUS attribute group

        Args:
            group_id: RADIUS attribute group ID
            assignment_data: Assignment configuration
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Created assignment
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                f"/radiusAttributeGroups/{group_id}/assignments",
                payload=assignment_data,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                f"/radiusAttributeGroups/{group_id}/assignments",
                payload=assignment_data
            )

        return response.json()

    async def delete_group_assignment(
        self,
        group_id: str,
        assignment_id: str,
        tenant_id: str = None
    ):
        """
        Delete an external assignment

        Args:
            group_id: RADIUS attribute group ID
            assignment_id: Assignment ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Deletion response
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/radiusAttributeGroups/{group_id}/assignments/{assignment_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/radiusAttributeGroups/{group_id}/assignments/{assignment_id}"
            )

        return response.json() if response.content else {"status": "deleted"}
