import logging

logger = logging.getLogger(__name__)


class IdentityService:
    """
    Service for managing Identity Groups and Identities in RuckusONE.

    Identity Groups are containers for individual identities (users/devices).
    They can have policy sets, DPSK pools, and MAC registration pools attached.
    Individual identities can be associated with units in multi-dwelling units (MDUs).
    """

    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    # ========== Identity Group Management ==========

    async def get_identity_groups(
        self,
        tenant_id: str = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Get all identity groups (paginated)

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            page: Page number (0-based)
            size: Page size

        Returns:
            Identity groups list with pagination
        """
        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                "/identityGroups",
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                "/identityGroups",
                params=params
            ).json()

    async def query_identity_groups(
        self,
        tenant_id: str = None,
        filters: dict = None,
        search_string: str = None,
        page: int = 1,
        size: int = 100
    ):
        """
        Query identity groups with advanced filtering

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Dictionary of filters
            search_string: Optional search string
            page: Page number (1-based, default: 1)
            size: Page size

        Returns:
            Query response with identity groups
        """
        body = {
            "page": page,
            "pageSize": size
        }

        if filters:
            body["filters"] = filters
        if search_string is not None:
            body["searchString"] = search_string

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                "/identityGroups/query",
                payload=body,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                "/identityGroups/query",
                payload=body
            ).json()

    async def get_identity_group(
        self,
        group_id: str,
        tenant_id: str = None
    ):
        """
        Get a specific identity group by ID

        Args:
            group_id: Identity group ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Identity group details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/identityGroups/{group_id}",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/identityGroups/{group_id}"
            ).json()

    async def create_identity_group(
        self,
        tenant_id: str,
        name: str,
        description: str = None,
        wait_for_completion: bool = True,
        **kwargs
    ):
        """
        Create a new identity group

        Args:
            tenant_id: Tenant/EC ID
            name: Identity group name
            description: Optional description
            wait_for_completion: Wait for async task to complete (default: True)
            **kwargs: Additional identity group fields

        Returns:
            Created identity group details
        """
        body = {
            "name": name,
            "description": description or f"Identity group {name}",
            **kwargs
        }

        if self.client.ec_type == "MSP":
            response = self.client.post(
                "/identityGroups",
                override_tenant_id=tenant_id,
                payload=body
            )
        else:
            response = self.client.post(
                "/identityGroups",
                payload=body
            )

        result = response.json()

        # Handle async pattern (202 Accepted with requestId)
        if response.status_code == 202 and wait_for_completion:
            request_id = result.get('requestId')
            if request_id:
                logger.info(f"Identity group creation is async (requestId: {request_id}), waiting...")
                await self.client.await_task_completion(
                    request_id=request_id,
                    override_tenant_id=tenant_id
                )
                logger.info(f"Identity group '{name}' creation completed")

                # Fetch the created identity group to get full details
                group_id = result.get('id')
                if group_id:
                    return await self.get_identity_group(group_id, tenant_id)

        return result

    async def delete_identity_group(
        self,
        group_id: str,
        tenant_id: str = None
    ):
        """
        Delete an identity group

        Args:
            group_id: Identity group ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Deletion response
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/identityGroups/{group_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/identityGroups/{group_id}"
            )

        return response.json() if response.content else {"status": "deleted"}

    async def export_identity_groups_to_csv(
        self,
        tenant_id: str = None,
        filters: dict = None
    ):
        """
        Export identity groups to CSV format

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Optional filters to apply

        Returns:
            CSV content
        """
        body = {}

        if filters:
            body["filters"] = filters

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                "/identityGroups/csvFile",
                payload=body,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                "/identityGroups/csvFile",
                payload=body
            )

        # Response is CSV content, not JSON
        return response.text

    # ========== Identity Management ==========

    async def get_all_identities(
        self,
        tenant_id: str = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Get all identities across all groups (paginated)

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            page: Page number (0-based)
            size: Page size

        Returns:
            Identities list with pagination
        """
        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                "/identities",
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                "/identities",
                params=params
            ).json()

    async def query_identities(
        self,
        tenant_id: str = None,
        filters: dict = None,
        search_string: str = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Query identities with advanced filtering

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Dictionary of filters
            search_string: Optional search string
            page: Page number (0-based)
            size: Page size

        Returns:
            Query response with identities
        """
        body = {}

        if filters:
            body["filters"] = filters
        if search_string:
            body["searchString"] = search_string

        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                "/identities/query",
                payload=body,
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                "/identities/query",
                payload=body,
                params=params
            ).json()

    async def get_identities_in_group(
        self,
        group_id: str,
        tenant_id: str = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Get all identities in a specific group

        Args:
            group_id: Identity group ID
            tenant_id: Tenant/EC ID (required for MSP)
            page: Page number (0-based)
            size: Page size

        Returns:
            Identities list with pagination
        """
        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/identityGroups/{group_id}/identities",
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/identityGroups/{group_id}/identities",
                params=params
            ).json()

    async def get_identity(
        self,
        group_id: str,
        identity_id: str,
        tenant_id: str = None
    ):
        """
        Get a specific identity by ID

        Args:
            group_id: Identity group ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Identity details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(
                f"/identityGroups/{group_id}/identities/{identity_id}",
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.get(
                f"/identityGroups/{group_id}/identities/{identity_id}"
            ).json()

    async def create_identity(
        self,
        group_id: str,
        name: str,
        tenant_id: str = None,
        display_name: str = None,
        description: str = None,
        vlan: int = None,
        phone_number: str = None
    ):
        """
        Create a new identity in an identity group

        Args:
            group_id: Identity group ID
            name: Identity name (required)
            tenant_id: Tenant/EC ID (required for MSP)
            display_name: Display name (optional)
            description: Description (optional) - use for Cloudpath GUID
            vlan: VLAN ID (optional)
            phone_number: Phone number (optional)

        Returns:
            Created identity details (async - returns requestId)
        """
        payload = {
            "name": name,
            "groupId": group_id
        }

        if display_name:
            payload["displayName"] = display_name
        if description:
            payload["description"] = description
        if vlan is not None:
            payload["vlan"] = vlan
        if phone_number:
            payload["phoneNumber"] = phone_number

        logger.warning(f"🔍 DEBUG IDENTITY API - create_identity payload: {payload}")

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                f"/identityGroups/{group_id}/identities",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                f"/identityGroups/{group_id}/identities",
                payload=payload
            )

        return response.json()

    async def update_identity(
        self,
        group_id: str,
        identity_id: str,
        tenant_id: str = None,
        name: str = None,
        display_name: str = None,
        description: str = None,
        vlan: int = None,
        phone_number: str = None
    ):
        """
        Update an existing identity

        Args:
            group_id: Identity group ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)
            name: New name (optional)
            display_name: New display name (optional)
            description: New description (optional)
            vlan: New VLAN ID (optional)
            phone_number: New phone number (optional)

        Returns:
            Updated identity details
        """
        payload = {}

        if name:
            payload["name"] = name
        if display_name:
            payload["displayName"] = display_name
        if description:
            payload["description"] = description
        if vlan is not None:
            payload["vlan"] = vlan
        if phone_number:
            payload["phoneNumber"] = phone_number

        logger.warning(f"🔍 DEBUG IDENTITY API - update_identity payload: {payload}")

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.patch(
                f"/identityGroups/{group_id}/identities/{identity_id}",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.patch(
                f"/identityGroups/{group_id}/identities/{identity_id}",
                payload=payload
            )

        return response.json()

    async def delete_identity(
        self,
        group_id: str,
        identity_id: str,
        tenant_id: str = None
    ):
        """
        Delete a specific identity from an identity group

        Args:
            group_id: Identity group ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Deletion response

        Note:
            This operation returns 202 Accepted and must be polled for completion
        """
        # API expects an array of identity IDs in the payload
        payload = [identity_id]

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities",
                payload=payload
            )

        # Handle 202 Accepted (async operation)
        if response.status_code == 202:
            response_data = response.json()
            request_id = response_data.get('requestId')

            if request_id:
                # Wait for async operation to complete
                result = await self.client.await_task_completion(
                    request_id=request_id,
                    override_tenant_id=tenant_id
                )
                return result
            else:
                # No requestId, just return the 202 response
                return response_data

        # Handle other responses (200, 204, etc.)
        return response.json() if response.content else {"status": "deleted"}

    async def delete_identities_bulk(
        self,
        group_id: str,
        identity_ids: list,
        tenant_id: str = None,
        wait_for_completion: bool = True
    ):
        """
        Delete multiple identities from an identity group in a single API call

        Args:
            group_id: Identity group ID
            identity_ids: List of identity IDs to delete
            tenant_id: Tenant/EC ID (required for MSP)
            wait_for_completion: If True, wait for async task (default True).
                                 If False, returns requestId for bulk tracking.

        Returns:
            Deletion response (includes requestId if wait_for_completion=False)

        Note:
            This operation returns 202 Accepted and must be polled for completion
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities",
                payload=identity_ids,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities",
                payload=identity_ids
            )

        # Handle 202 Accepted (async operation)
        if response.status_code == 202:
            response_data = response.json()
            request_id = response_data.get('requestId')

            if request_id and wait_for_completion:
                # Wait for async operation to complete
                result = await self.client.await_task_completion(
                    request_id=request_id,
                    override_tenant_id=tenant_id
                )
                return result
            else:
                # Return requestId for bulk tracking or no requestId
                return response_data

        # Handle other responses (200, 204, etc.)
        return response.json() if response.content else {"status": "deleted"}

    # ========== Identity Group Associations ==========

    async def attach_policy_set_to_identity_group(
        self,
        group_id: str,
        policy_set_id: str,
        tenant_id: str = None
    ):
        """
        Attach a policy set to an identity group

        Args:
            group_id: Identity group ID
            policy_set_id: Policy set ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(
                f"/identityGroups/{group_id}/policySets/{policy_set_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/identityGroups/{group_id}/policySets/{policy_set_id}"
            )

        return response.json() if response.content else {"status": "attached"}

    async def remove_policy_set_from_identity_group(
        self,
        group_id: str,
        policy_set_id: str,
        tenant_id: str = None
    ):
        """
        Remove a policy set from an identity group

        Args:
            group_id: Identity group ID
            policy_set_id: Policy set ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/identityGroups/{group_id}/policySets/{policy_set_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/identityGroups/{group_id}/policySets/{policy_set_id}"
            )

        return response.json() if response.content else {"status": "removed"}

    async def attach_dpsk_pool_to_identity_group(
        self,
        group_id: str,
        dpsk_pool_id: str,
        tenant_id: str = None
    ):
        """
        Attach a DPSK pool to an identity group

        Args:
            group_id: Identity group ID
            dpsk_pool_id: DPSK pool ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(
                f"/identityGroups/{group_id}/dpskPools/{dpsk_pool_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/identityGroups/{group_id}/dpskPools/{dpsk_pool_id}"
            )

        return response.json() if response.content else {"status": "attached"}

    async def attach_mac_registration_pool_to_identity_group(
        self,
        group_id: str,
        pool_id: str,
        tenant_id: str = None
    ):
        """
        Attach a MAC registration pool to an identity group

        Args:
            group_id: Identity group ID
            pool_id: MAC registration pool ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(
                f"/identityGroups/{group_id}/macRegistrationPools/{pool_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/identityGroups/{group_id}/macRegistrationPools/{pool_id}"
            )

        return response.json() if response.content else {"status": "attached"}

    # ========== Unit Identity Associations (MDU) ==========

    async def query_unit_identities(
        self,
        venue_id: str,
        tenant_id: str = None,
        filters: dict = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Query identities associated with units in a venue

        Args:
            venue_id: Venue ID
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Optional filters
            page: Page number
            size: Page size

        Returns:
            Query response with unit identities
        """
        body = {}

        if filters:
            body["filters"] = filters

        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                f"/venues/{venue_id}/units/identities/query",
                payload=body,
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                f"/venues/{venue_id}/units/identities/query",
                payload=body,
                params=params
            ).json()

    async def associate_identity_to_unit(
        self,
        venue_id: str,
        unit_id: str,
        identity_id: str,
        tenant_id: str = None
    ):
        """
        Associate an identity to a unit (for MDU scenarios)

        Args:
            venue_id: Venue ID
            unit_id: Unit ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(
                f"/venues/{venue_id}/units/{unit_id}/identities/{identity_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/units/{unit_id}/identities/{identity_id}"
            )

        return response.json() if response.content else {"status": "associated"}

    async def remove_identity_from_unit(
        self,
        venue_id: str,
        unit_id: str,
        identity_id: str,
        tenant_id: str = None
    ):
        """
        Remove an identity association from a unit

        Args:
            venue_id: Venue ID
            unit_id: Unit ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/venues/{venue_id}/units/{unit_id}/identities/{identity_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/venues/{venue_id}/units/{unit_id}/identities/{identity_id}"
            )

        return response.json() if response.content else {"status": "removed"}

    async def update_identity_ethernet_ports(
        self,
        group_id: str,
        identity_id: str,
        venue_id: str,
        ethernet_ports: list,
        tenant_id: str = None
    ):
        """
        Update ethernet port assignments for an identity

        Args:
            group_id: Identity group ID
            identity_id: Identity ID
            venue_id: Venue ID
            ethernet_ports: List of ethernet port configurations
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        payload = {
            "ethernetPorts": ethernet_ports
        }

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.put(
                f"/identityGroups/{group_id}/identities/{identity_id}/venues/{venue_id}/ethernetPorts",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/identityGroups/{group_id}/identities/{identity_id}/venues/{venue_id}/ethernetPorts",
                payload=payload
            )

        return response.json()

    async def retry_vni_allocation_for_identity(
        self,
        group_id: str,
        identity_id: str,
        tenant_id: str = None
    ):
        """
        Retry VNI (Virtual Network Identifier) allocation for an identity

        Args:
            group_id: Identity group ID
            identity_id: Identity ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            Response from API
        """
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities/{identity_id}/vnis",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.delete(
                f"/identityGroups/{group_id}/identities/{identity_id}/vnis"
            )

        return response.json() if response.content else {"status": "retried"}

    # ========== External Identities ==========

    async def query_external_identities(
        self,
        tenant_id: str = None,
        filters: dict = None,
        page: int = 0,
        size: int = 100
    ):
        """
        Query external identities (from external auth sources)

        Args:
            tenant_id: Tenant/EC ID (required for MSP)
            filters: Optional filters
            page: Page number
            size: Page size

        Returns:
            Query response with external identities
        """
        body = {}

        if filters:
            body["filters"] = filters

        params = {
            "page": page,
            "size": size
        }

        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.post(
                "/externalIdentities/query",
                payload=body,
                params=params,
                override_tenant_id=tenant_id
            ).json()
        else:
            return self.client.post(
                "/externalIdentities/query",
                payload=body,
                params=params
            ).json()
