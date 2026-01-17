import asyncio
import logging

logger = logging.getLogger(__name__)


class VenueService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_venues(self, tenant_id: str):
        """
        Get all venues for a tenant
        """
        if self.client.ec_type == "MSP":
            resp = self.client.get("/venues", override_tenant_id=tenant_id).json()
            return resp
        else:
            resp = self.client.get("/venues").json()
            return resp

    async def get_venue(self, tenant_id: str, venue_id: str):
        """
        Get a specific venue by ID

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID

        Returns:
            Venue details object
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}").json()

    async def get_aps_by_tenant_venue(self, tenant_id: str, venue_id: str):
        """
        Get all APs for a venue, handling pagination automatically

        NOTE: The /venues/aps/query endpoint has broken pagination - it ignores the 'page'
        parameter and returns duplicates. We work around this by:
        1. Using a large pageSize (500) to get all APs in one request
        2. Deduplicating results by serial number as a safety measure
        """
        body = {
            'fields': ["name","status","model","networkStatus","macAddress","venueName","switchName","meshRole","clientCount","apGroupId","apGroupName","lanPortStatuses","tags","serialNumber","radioStatuses","venueId","poePort","firmwareVersion","uptime","afcStatus","powerSavingStatus"],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {
                'venueId': [venue_id]
            },
            'page': 0,
            'pageSize': 500  # Large enough to get all APs in one request (broken pagination workaround)
        }

        # Fetch first page
        if self.client.ec_type == "MSP":
            first_response = self.client.post(f"/venues/aps/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            first_response = self.client.post(f"/venues/aps/query", payload=body).json()

        all_aps = first_response.get('data', [])
        total_count = first_response.get('totalCount', len(all_aps))

        logger.debug(f"APs query: fetched {len(all_aps)}, total reported: {total_count}")

        # Check if we got everything in one request
        if total_count > len(all_aps):
            logger.warning(f"API returned {len(all_aps)} APs but reports {total_count} total - pagination is broken on this endpoint")

        # Deduplicate APs by serial number (in case API returns duplicates)
        seen_serials = set()
        deduplicated_aps = []
        duplicate_count = 0
        for ap in all_aps:
            serial = ap.get('serialNumber')
            if serial and serial not in seen_serials:
                seen_serials.add(serial)
                deduplicated_aps.append(ap)
            elif serial:
                duplicate_count += 1

        if duplicate_count > 0:
            logger.warning(f"Removed {duplicate_count} duplicate APs, final count: {len(deduplicated_aps)}")

        return {'data': deduplicated_aps, 'totalCount': len(deduplicated_aps)}

    async def get_ap_by_tenant_venue_serial(self, tenant_id: str, venue_id: str, serial_number: str):
        """
        Get a specific AP in a venue by serial number
        """
        # body = {
        #     'fields': ["name","status","model","networkStatus","macAddress","venueName","switchName","meshRole","clientCount","apGroupId","apGroupName","lanPortStatuses","tags","serialNumber","radioStatuses","venueId","poePort","firmwareVersion","uptime","afcStatus","powerSavingStatus"],
        #     'sortField': 'name',
        #     'sortOrder': 'ASC',
        #     'filters': {
        #         'venueId': [venue_id],
        #         'serialNumber': [serial_number]
        #     }
        # }
        if self.client.ec_type == "MSP":
            return self.client.post(f"/venues/{venue_id}/aps/{serial_number}", override_tenant_id=tenant_id).json()
        else:
            return self.client.post(f"/venues/{venue_id}/aps/{serial_number}").json()

    async def get_ap_groups(self, tenant_id: str = None):
        """
        Get all AP groups for a tenant (simple GET without filtering)

        Note: This returns a simple list. For more advanced querying with filters,
        use query_ap_groups() instead.
        """
        logger.debug(f"get_ap_groups called - tenant_id: {tenant_id}, ec_type: {self.client.ec_type}")

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.get("/venues/apGroups", override_tenant_id=tenant_id).json()
        else:
            response = self.client.get("/venues/apGroups").json()

        logger.debug(f"AP Groups Response Type: {type(response)}")

        if isinstance(response, dict):
            logger.debug(f"AP Groups Response Keys: {response.keys()}")
            if 'data' in response:
                logger.debug(f"AP Groups Count: {len(response.get('data', []))}")
        elif isinstance(response, list):
            logger.debug(f"AP Groups Count (list): {len(response)}")

        return response

    async def get_ap_group(self, tenant_id: str, venue_id: str, ap_group_id: str):
        """
        Get a specific AP Group's details by ID

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            ap_group_id: AP Group ID

        Returns:
            AP Group details object
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apGroups/{ap_group_id}", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apGroups/{ap_group_id}").json()

    async def query_ap_groups(self, tenant_id: str, venue_id: str = None, fields: list = None, filters: dict = None, page: int = None, limit: int = None):
        """
        Query AP Groups with advanced filtering and field selection

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Optional venue ID to filter by
            fields: List of fields to return (e.g., ['id', 'name', 'venueId', 'description', 'aps'])
            filters: Dictionary of filters (e.g., {'name': ['GroupName'], 'venueId': ['venue-id']})
            page: Optional page number for pagination (0-based)
            limit: Optional page size limit

        Returns:
            Query response with data array and totalCount

        Example:
            # Get all AP groups with their AP count
            response = await query_ap_groups(
                tenant_id='abc123',
                fields=['id', 'name', 'venueId', 'aps'],
                filters={'venueId': ['my-venue-id']}
            )
        """
        # Default fields if not specified
        if fields is None:
            fields = ['id', 'name', 'venueId', 'venueName', 'description', 'aps', 'wifiNetworks']

        body = {
            'fields': fields,
            'sortField': 'name',
            'sortOrder': 'ASC',
        }

        # Add pagination if specified
        if page is not None:
            body['page'] = page
        if limit is not None:
            body['limit'] = limit

        # Add filters if provided
        if filters:
            body['filters'] = filters
        elif venue_id:
            # Convenience: filter by venue_id if provided
            body['filters'] = {'venueId': [venue_id]}

        if self.client.ec_type == "MSP":
            return self.client.post("/venues/apGroups/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            return self.client.post("/venues/apGroups/query", payload=body).json()

    # ========== Venue WiFi Settings Methods ==========

    async def get_ap_load_balancing_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP load balancing settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apLoadBalancingSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apLoadBalancingSettings").json()

    async def get_ap_radio_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP radio settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apRadioSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apRadioSettings").json()

    async def get_wifi_available_channels(self, tenant_id: str, venue_id: str):
        """
        Get WiFi available channels for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/wifiAvailableChannels", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/wifiAvailableChannels").json()

    async def get_ap_model_band_mode_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP model band mode settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apModelBandModeSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apModelBandModeSettings").json()

    async def get_ap_model_external_antenna_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP model external antenna settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apModelExternalAntennaSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apModelExternalAntennaSettings").json()

    async def get_ap_client_admission_control_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP client admission control settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apClientAdmissionControlSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apClientAdmissionControlSettings").json()

    async def get_ap_model_capabilities(self, tenant_id: str, venue_id: str):
        """
        Get AP model capabilities for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apModelCapabilities", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apModelCapabilities").json()

    async def get_ap_model_antenna_type_settings(self, tenant_id: str, venue_id: str):
        """
        Get AP model antenna type settings for a venue
        """
        if self.client.ec_type == "MSP":
            return self.client.get(f"/venues/{venue_id}/apModelAntennaTypeSettings", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/venues/{venue_id}/apModelAntennaTypeSettings").json()

    async def add_ap_to_venue(
        self,
        venue_id: str,
        name: str,
        serial_number: str,
        tenant_id: str = None,
        description: str = None,
        model: str = None,
        tags: list = None,
        latitude: str = None,
        longitude: str = None
    ):
        """
        Add an AP to a venue

        Args:
            venue_id: The venue ID to add the AP to
            name: AP name
            serial_number: AP serial number
            tenant_id: Optional tenant ID (required for MSP)
            description: Optional AP description
            model: Optional AP model
            tags: Optional list of tags
            latitude: Optional GPS latitude
            longitude: Optional GPS longitude

        Returns:
            Response from the R1 API
        """
        # Build request payload
        payload = {
            "name": name,
            "serialNumber": serial_number
        }

        # Add optional fields if provided
        if description:
            payload["description"] = description
        if model:
            payload["model"] = model
        if tags:
            payload["tags"] = tags
        if latitude and longitude:
            payload["deviceGps"] = {
                "latitude": str(latitude),
                "longitude": str(longitude)
            }

        logger.info(f"Adding AP {serial_number} to venue {venue_id}")
        logger.info(f"Payload: {payload}")

        # Make API call
        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.post(
                f"/venues/{venue_id}/aps",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                f"/venues/{venue_id}/aps",
                payload=payload
            )

        return response.json()

    # ========== AP Group Methods ==========

    async def find_ap_group_by_name(self, tenant_id: str, venue_id: str, group_name: str):
        """
        Search for an AP Group by name (IDEMPOTENT check)

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID to search within
            group_name: Name of the AP Group to find

        Returns:
            AP Group object if found, None otherwise
        """
        body = {
            'fields': ['id', 'name', 'venueId', 'description'],
            'filters': {
                'name': [group_name],
                'venueId': [venue_id]
            },
            'sortField': 'name',
            'sortOrder': 'ASC',
        }

        if self.client.ec_type == "MSP":
            response = self.client.post("/venues/apGroups/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            response = self.client.post("/venues/apGroups/query", payload=body).json()

        # Response format: {"data": [...], "totalCount": N}
        groups = response.get('data', [])

        if groups and len(groups) > 0:
            return groups[0]

        return None

    async def create_ap_group(
        self,
        tenant_id: str,
        venue_id: str,
        name: str,
        description: str = None,
        wait_for_completion: bool = True
    ):
        """
        Create a new AP Group in a venue

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID where AP Group will be created
            name: AP Group name
            description: Optional description
            wait_for_completion: If True, wait for async task to complete (default: True)

        Returns:
            Created AP Group response from API
        """
        payload = {
            "name": name
        }

        if description:
            payload["description"] = description

        # Make API call
        if self.client.ec_type == "MSP":
            response = self.client.post(
                f"/venues/{venue_id}/apGroups",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                f"/venues/{venue_id}/apGroups",
                payload=payload
            )

        # API returns 202 Accepted for async operations
        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

                    # After async task completes, fetch the created resource to get its ID
                    created_group = await self.find_ap_group_by_name(tenant_id, venue_id, name)
                    if created_group:
                        return created_group
                    else:
                        logger.warning(f"Task completed but could not find created AP Group '{name}'")
                        return result

            return result
        else:
            logger.error(f"Failed to create AP Group: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def assign_ap_to_group(
        self,
        tenant_id: str,
        venue_id: str,
        ap_group_id: str,
        ap_serial_number: str,
        wait_for_completion: bool = True
    ):
        """
        Assign an AP to an AP Group

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID where AP is located
            ap_group_id: AP Group ID to assign AP to
            ap_serial_number: Serial number of the AP
            wait_for_completion: If True, wait for async task to complete (default: True)

        Returns:
            Response from API
        """

        # Make API call - PUT with no body
        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/apGroups/{ap_group_id}/aps/{ap_serial_number}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/apGroups/{ap_group_id}/aps/{ap_serial_number}"
            )

        # API returns 202 Accepted for async operations
        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            logger.error(f"Failed to assign AP: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def activate_ssid_on_venue(
        self,
        tenant_id: str,
        venue_id: str,
        wifi_network_id: str,
        wait_for_completion: bool = True
    ):
        """
        Activate an SSID (WiFi Network) on a Venue

        This MUST be done before activating the SSID on AP Groups.
        This makes the SSID available to the venue.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            wifi_network_id: WiFi Network (SSID) ID to activate
            wait_for_completion: If True, wait for async task to complete (default: True)

        Returns:
            Response from API
        """
        logger.info(f"[activate_ssid_on_venue] PUT /venues/{venue_id}/wifiNetworks/{wifi_network_id}")

        # Make API call - PUT with empty body
        payload = {}

        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}",
                payload=payload
            )

        logger.info(f"[activate_ssid_on_venue] Response: {response.status_code}")

        # API returns 202 Accepted for async operations
        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}
            request_id = result.get('requestId') if response.status_code == 202 else None
            logger.info(f"[activate_ssid_on_venue] Success (requestId: {request_id})")

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == 202 and wait_for_completion:
                if request_id:
                    logger.info(f"[activate_ssid_on_venue] Waiting for task {request_id}...")
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)
                    logger.info(f"[activate_ssid_on_venue] Task complete")

            return result
        else:
            logger.error(f"[activate_ssid_on_venue] FAILED: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def activate_ssid_on_ap_group(
        self,
        tenant_id: str,
        venue_id: str,
        wifi_network_id: str,
        ap_group_id: str,
        radio_types: list = None,
        vlan_id: int = None,
        wait_for_completion: bool = True
    ):
        """
        Activate an SSID (WiFi Network) on an AP Group

        This is the final step that makes the SSID broadcast on the APs in the group.
        NOTE: The SSID must first be activated on the venue using activate_ssid_on_venue()

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            wifi_network_id: WiFi Network (SSID) ID to activate
            ap_group_id: AP Group ID to activate on
            radio_types: List of radio types (e.g., ["2.4-GHz", "5-GHz", "6-GHz"])
            vlan_id: Optional VLAN ID override
            wait_for_completion: If True, wait for async task to complete (default: True)

        Returns:
            Response from API
        """

        # Default to all radio types if not specified
        if radio_types is None:
            radio_types = ["2.4-GHz", "5-GHz", "6-GHz"]

        # Build the payload with activation settings
        payload = {
            "radioTypes": radio_types
        }

        # Add VLAN if specified
        if vlan_id is not None:
            payload["vlanId"] = int(vlan_id) if isinstance(vlan_id, str) else vlan_id

        # Make API call - PUT with activation payload
        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}",
                payload=payload
            )

        # API returns 202 Accepted for async operations
        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            logger.error(f"Failed to activate SSID on AP Group: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def configure_ssid_for_specific_ap_group(
        self,
        tenant_id: str,
        venue_id: str,
        wifi_network_id: str,
        ap_group_id: str,
        radio_types: list = None,
        vlan_id: int = None,
        wait_for_completion: bool = True,
        debug_delay: float = 0
    ):
        """
        Configure SSID to use a specific AP Group (not All AP Groups).

        This performs the 3-step process the R1 frontend uses:
        1. PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings - set isAllApGroups=false
        2. PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId} - activate AP Group
        3. PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings - configure settings

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            wifi_network_id: WiFi Network (SSID) ID
            ap_group_id: AP Group ID to activate
            radio_types: List of radio types (e.g., ["2.4-GHz", "5-GHz", "6-GHz"])
            vlan_id: Optional VLAN ID override
            wait_for_completion: If True, wait for async task to complete (default: True)
            debug_delay: Seconds to wait between steps (for debugging)

        Returns:
            Response from API
        """
        import asyncio

        # Default to all radio types if not specified
        if radio_types is None:
            radio_types = ["2.4-GHz", "5-GHz", "6-GHz"]

        logger.info(f"[configure_ssid_for_ap_group] Starting 3-step process:")
        logger.info(f"  venue_id={venue_id}")
        logger.info(f"  wifi_network_id={wifi_network_id}")
        logger.info(f"  ap_group_id={ap_group_id}")
        logger.info(f"  radio_types={radio_types}")
        logger.info(f"  vlan_id={vlan_id}")

        # Step 1: Update venue SSID settings to set isAllApGroups=false
        logger.info(f"[Step 1/3] PUT /venues/{venue_id}/wifiNetworks/{wifi_network_id}/settings")
        settings_payload = {
            "dual5gEnabled": False,
            "tripleBandEnabled": False,
            "allApGroupsRadio": "Both",
            "isAllApGroups": False,
            "allApGroupsRadioTypes": radio_types,
            "scheduler": None,
            "allApGroupsVlanId": None,
            "oweTransWlanId": None,
            "isEnforced": False,
            "networkId": wifi_network_id,
            "apGroups": [{
                "apGroupId": ap_group_id,
                "radioTypes": radio_types,
                "radio": "Both"
            }],
            "venueId": venue_id
        }

        # Add VLAN if specified
        if vlan_id is not None:
            settings_payload["apGroups"][0]["vlanId"] = int(vlan_id) if isinstance(vlan_id, str) else vlan_id

        logger.info(f"[Step 1/3] Payload: isAllApGroups={settings_payload['isAllApGroups']}, apGroups={len(settings_payload['apGroups'])}")

        # Step 1: PUT settings
        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/settings",
                payload=settings_payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/settings",
                payload=settings_payload
            )

        logger.info(f"[Step 1/3] Response: {response.status_code}")
        if response.status_code not in [200, 201, 202]:
            logger.error(f"[Step 1/3] FAILED: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

        result_1 = response.json() if response.content else {"status": "accepted"}
        request_id_1 = result_1.get('requestId') if response.status_code == 202 else None
        logger.info(f"[Step 1/3] Success (requestId: {request_id_1})")

        if debug_delay > 0:
            logger.info(f"[DEBUG] Waiting {debug_delay}s before Step 2...")
            await asyncio.sleep(debug_delay)

        # Step 2: Activate AP Group on the SSID (fire immediately, don't wait)
        logger.info(f"[Step 2/3] PUT /venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}")
        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}",
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}"
            )

        logger.info(f"[Step 2/3] Response: {response.status_code}")
        if response.status_code not in [200, 201, 202]:
            logger.error(f"[Step 2/3] FAILED: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

        result_2 = response.json() if response.content else {"status": "accepted"}
        request_id_2 = result_2.get('requestId') if response.status_code == 202 else None
        logger.info(f"[Step 2/3] Success (requestId: {request_id_2})")

        if debug_delay > 0:
            logger.info(f"[DEBUG] Waiting {debug_delay}s before Step 3...")
            await asyncio.sleep(debug_delay)

        # Step 3: Configure AP Group settings on the SSID (fire immediately, don't wait)
        logger.info(f"[Step 3/3] PUT /venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}/settings")
        ap_group_settings_payload = {
            "apGroupId": ap_group_id,
            "radioTypes": radio_types,
            "radio": "Both"
        }
        if vlan_id is not None:
            ap_group_settings_payload["vlanId"] = int(vlan_id) if isinstance(vlan_id, str) else vlan_id

        logger.info(f"[Step 3/3] Payload: {ap_group_settings_payload}")

        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}/settings",
                payload=ap_group_settings_payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/wifiNetworks/{wifi_network_id}/apGroups/{ap_group_id}/settings",
                payload=ap_group_settings_payload
            )

        logger.info(f"[Step 3/3] Response: {response.status_code}")
        if response.status_code not in [200, 201, 202]:
            logger.error(f"[Step 3/3] FAILED: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

        result_3 = response.json() if response.content else {"status": "accepted"}
        request_id_3 = result_3.get('requestId') if response.status_code == 202 else None
        logger.info(f"[Step 3/3] Success (requestId: {request_id_3})")

        # Now wait for all 3 to complete (in order)
        if wait_for_completion:
            if request_id_1:
                logger.info(f"[Waiting] Step 1 task {request_id_1}...")
                await self.client.await_task_completion(request_id_1, override_tenant_id=tenant_id)
                logger.info(f"[Waiting] Step 1 complete")
            if request_id_2:
                logger.info(f"[Waiting] Step 2 task {request_id_2}...")
                await self.client.await_task_completion(request_id_2, override_tenant_id=tenant_id)
                logger.info(f"[Waiting] Step 2 complete")
            if request_id_3:
                logger.info(f"[Waiting] Step 3 task {request_id_3}...")
                await self.client.await_task_completion(request_id_3, override_tenant_id=tenant_id)
                logger.info(f"[Waiting] Step 3 complete")

        logger.info(f"[configure_ssid_for_ap_group] All 3 steps complete for SSID {wifi_network_id} -> AP Group {ap_group_id}")
        return result_3

    # ========== Comprehensive View Methods ==========

    async def get_ap_group_with_members_and_ssids(self, tenant_id: str, venue_id: str, ap_group_id: str):
        """
        Get comprehensive AP Group view with:
        - AP Group details
        - All APs in the group
        - All SSIDs activated on the group

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            ap_group_id: AP Group ID

        Returns:
            Dictionary with:
            {
                'ap_group': {...},          # AP Group details
                'aps': [...],               # List of APs in this group
                'ssids': [...],             # List of SSIDs activated on this group
                'total_aps': int,
                'total_ssids': int
            }
        """
        result = {
            'ap_group': None,
            'aps': [],
            'ssids': [],
            'total_aps': 0,
            'total_ssids': 0
        }

        try:
            # 1. Get AP Group details
            logger.info(f"Fetching AP Group {ap_group_id} details...")
            result['ap_group'] = await self.get_ap_group(tenant_id, venue_id, ap_group_id)

            # 2. Get all APs in venue and filter by this AP Group
            logger.info(f"Fetching APs in venue {venue_id}...")
            aps_response = await self.get_aps_by_tenant_venue(tenant_id, venue_id)
            all_aps = aps_response.get('data', [])

            # Filter APs that belong to this group
            group_aps = [ap for ap in all_aps if ap.get('apGroupId') == ap_group_id]
            result['aps'] = group_aps
            result['total_aps'] = len(group_aps)

            logger.info(f"Found {result['total_aps']} APs in group {ap_group_id}")

            # 3. Get WiFi Networks and check which are activated on this AP Group
            logger.info(f"Fetching WiFi Networks for tenant {tenant_id}...")
            networks_response = await self.client.networks.get_wifi_networks(tenant_id)
            all_networks = networks_response.get('data', [])

            # Filter networks that are activated on this AP Group
            group_ssids = []
            for network in all_networks:
                venue_ap_groups = network.get('venueApGroups', [])
                # Check if this venue + AP group combo is in the network's activations
                for vag in venue_ap_groups:
                    if vag.get('venueId') == venue_id and vag.get('apGroupId') == ap_group_id:
                        group_ssids.append({
                            'id': network.get('id'),
                            'name': network.get('name'),
                            'ssid': network.get('ssid'),
                            'vlan': network.get('vlan'),
                            'nwSubType': network.get('nwSubType'),
                            'activation_details': vag  # Includes radio types, VLAN overrides, etc.
                        })
                        break

            result['ssids'] = group_ssids
            result['total_ssids'] = len(group_ssids)

            logger.info(f"Found {result['total_ssids']} SSIDs activated on group {ap_group_id}")

            return result

        except Exception as e:
            logger.error(f"Error fetching comprehensive AP Group view: {str(e)}")
            raise

    async def get_all_ap_groups_with_details(self, tenant_id: str, venue_id: str = None, include_lan_port_settings: bool = False):
        """
        Get all AP Groups with their members and SSIDs

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Optional - filter by specific venue
            include_lan_port_settings: If True, fetch full LAN port settings (VLANs, enabled status)
                                       for each AP. This requires additional API calls per AP.

        Returns:
            List of AP Group summaries:
            [
                {
                    'ap_group_id': str,
                    'ap_group_name': str,
                    'venue_id': str,
                    'venue_name': str,
                    'description': str,
                    'total_aps': int,
                    'ap_names': [str],  # List of AP names in this group
                    'total_ssids': int,
                    'ssid_names': [str]  # List of SSID names on this group
                },
                ...
            ]
        """
        try:
            # 1. Get all AP Groups using the simple GET endpoint (query endpoint is broken)
            # The query endpoint ignores pagination and only returns 25 items
            logger.info(f"Fetching AP Groups for tenant {tenant_id}...")

            # Use the simple GET endpoint which returns all groups
            all_ap_groups_response = await self.get_ap_groups(tenant_id=tenant_id)

            # The response might be a list or a dict with 'data' key
            if isinstance(all_ap_groups_response, list):
                all_ap_groups = all_ap_groups_response
            elif isinstance(all_ap_groups_response, dict):
                all_ap_groups = all_ap_groups_response.get('data', all_ap_groups_response.get('list', []))
            else:
                all_ap_groups = []

            # Filter by venue_id if specified
            if venue_id:
                all_ap_groups = [g for g in all_ap_groups if g.get('venueId') == venue_id]

            logger.debug(f"AP Groups: {len(all_ap_groups)} total, venue filter: {venue_id is not None}")

            # Skip the broken query endpoint pagination logic
            first_response = {'data': all_ap_groups, 'totalCount': len(all_ap_groups)}

            logger.info(f"Fetched {len(all_ap_groups)} AP Groups")

            # 2. Get all APs in venue to map serial numbers to names
            logger.info(f"Fetching APs for venue {venue_id}...")
            if venue_id:
                aps_response = await self.get_aps_by_tenant_venue(tenant_id, venue_id)
                all_venue_aps = aps_response.get('data', [])
                # Create lookup: serial -> AP object
                ap_lookup = {ap.get('serialNumber'): ap for ap in all_venue_aps}
            else:
                ap_lookup = {}

            # 3. Get all WiFi networks to find which are activated on each AP Group
            logger.debug(f"Fetching WiFi Networks for tenant {tenant_id}")
            networks_response = await self.client.networks.get_wifi_networks(tenant_id)
            all_networks = networks_response.get('data', [])

            logger.debug(f"WiFi Networks: {len(all_networks)} total")

            # Build reverse lookup: AP Group ID -> List of SSIDs activated on it
            # The relationship is stored in each network's venueApGroups field
            apgroup_to_ssids = {}
            for network in all_networks:
                venue_ap_groups = network.get('venueApGroups', [])
                for vag in venue_ap_groups:
                    # Check if this is for our venue
                    if vag.get('venueId') == venue_id:
                        ap_group_ids = vag.get('apGroupIds', [])
                        is_all_groups = vag.get('isAllApGroups', False)

                        # Build enriched SSID object with VLAN info
                        base_vlan = network.get('vlan')
                        vlan_override = vag.get('vlanId')  # Per-activation VLAN override
                        effective_vlan = vlan_override if vlan_override is not None else base_vlan

                        ssid_info = {
                            'id': network.get('id'),
                            'name': network.get('name'),
                            'ssid': network.get('ssid'),
                            'base_vlan': base_vlan,
                            'vlan_override': vlan_override,
                            'effective_vlan': effective_vlan,
                            'is_all_ap_groups': is_all_groups,
                            'radio_types': vag.get('radioTypes', []),
                        }

                        if is_all_groups:
                            # SSID is activated on all AP Groups in this venue
                            for group in all_ap_groups:
                                group_id = group.get('id')
                                if group_id not in apgroup_to_ssids:
                                    apgroup_to_ssids[group_id] = []
                                apgroup_to_ssids[group_id].append(ssid_info)
                        else:
                            # SSID is activated on specific AP Groups
                            for ap_group_id in ap_group_ids:
                                if ap_group_id not in apgroup_to_ssids:
                                    apgroup_to_ssids[ap_group_id] = []
                                apgroup_to_ssids[ap_group_id].append(ssid_info)

            logger.debug(f"SSID-to-AP-Group mapping: {len(apgroup_to_ssids)} groups with SSIDs")

            # 4. Build summary for each group
            summaries = []
            for group in all_ap_groups:
                group_id = group.get('id')

                # Handle both possible field names for APs
                # Some responses have 'apSerialNumbers', others have 'aps' with objects
                ap_serials = group.get('apSerialNumbers', [])
                if not ap_serials and 'aps' in group:
                    # Extract serial numbers from aps objects
                    aps_objects = group.get('aps', [])
                    ap_serials = [ap.get('serialNumber') for ap in aps_objects if ap.get('serialNumber')]

                # Get AP details from lookup (includes LAN port statuses)
                ap_names = []
                ap_serials_list = []
                ap_details = []  # Full AP details for each AP in this group
                missing_serials = []
                for serial in ap_serials:
                    ap = ap_lookup.get(serial, {})
                    if not ap:
                        missing_serials.append(serial)
                        ap_names.append(serial)
                        ap_serials_list.append(serial)
                        ap_details.append({
                            'serial': serial,
                            'name': serial,
                            'model': None,
                            'lan_port_statuses': [],
                            'lan_port_settings': None
                        })
                    else:
                        ap_name = ap.get('name') or serial
                        ap_model = ap.get('model')
                        ap_names.append(ap_name)
                        ap_serials_list.append(serial)

                        # Get live LAN port statuses from AP data (shows physical link status)
                        lan_port_statuses = ap.get('lanPortStatuses', [])

                        ap_details.append({
                            'serial': serial,
                            'name': ap_name,
                            'model': ap_model,
                            'lan_port_statuses': lan_port_statuses,  # Live physical link status
                            'lan_port_settings': None   # Will be populated below if requested
                        })

                if missing_serials:
                    logger.warning(f"AP Group '{group.get('name')}': {len(missing_serials)} APs not found in venue")

                # Get SSIDs activated on this AP Group from our reverse lookup
                ssids = apgroup_to_ssids.get(group_id, [])
                ssid_names = [net.get('name') or net.get('ssid', f'Unknown-{net.get("id")}') for net in ssids]

                # Skip groups without names (usually default groups)
                group_name = group.get('name')
                if not group_name:
                    logger.debug(f"Skipping AP Group without name: {group.get('id')}")
                    continue

                summary = {
                    'ap_group_id': group_id,
                    'ap_group_name': group_name,
                    'venue_id': group.get('venueId'),
                    'venue_name': group.get('venueName', 'Unknown'),
                    'description': group.get('description', ''),
                    'total_aps': len(ap_serials),
                    'ap_names': ap_names,
                    'ap_serials': ap_serials_list,
                    'aps': ap_details,  # Full AP details with LAN port statuses
                    'total_ssids': len(ssids),
                    'ssid_names': ssid_names,
                    'ssids': ssids  # Full SSID objects with details
                }

                summaries.append(summary)

            logger.info(f"AP Groups summary: {len(summaries)} groups ({len(all_ap_groups) - len(summaries)} filtered out)")

            # Fetch LAN port settings in parallel if requested
            if include_lan_port_settings and venue_id:
                # Collect all APs that need LAN port settings
                aps_to_fetch = []
                for summary in summaries:
                    for ap in summary.get('aps', []):
                        if ap.get('serial'):
                            aps_to_fetch.append({
                                'serial': ap['serial'],
                                'model': ap.get('model'),
                                'ap_ref': ap  # Reference to update in place
                            })

                if aps_to_fetch:
                    logger.info(f"Fetching LAN port settings for {len(aps_to_fetch)} APs in parallel...")

                    # Create async tasks with semaphore to limit concurrency
                    semaphore = asyncio.Semaphore(10)  # Max 10 concurrent requests

                    async def fetch_lan_port_settings(ap_info):
                        async with semaphore:
                            serial = ap_info['serial']
                            model = ap_info['model']
                            try:
                                settings = await self.get_ap_all_lan_port_settings(
                                    tenant_id, venue_id, serial, model=model
                                )
                                ap_info['ap_ref']['lan_port_settings'] = settings
                                return True
                            except Exception as e:
                                logger.debug(f"Failed to get LAN port settings for AP {serial}: {str(e)}")
                                return False

                    results = await asyncio.gather(
                        *[fetch_lan_port_settings(ap) for ap in aps_to_fetch],
                        return_exceptions=True
                    )
                    successful = sum(1 for r in results if r is True)
                    logger.info(f"Fetched LAN port settings: {successful}/{len(aps_to_fetch)} successful")

            return summaries

        except Exception as e:
            logger.error(f"Error fetching all AP Groups with details: {str(e)}")
            raise

    async def get_venue_network_summary(self, tenant_id: str, venue_id: str, include_lan_port_settings: bool = True):
        """
        Get a complete network summary for a venue:
        - Venue details
        - All AP Groups with their members
        - All SSIDs and their activations
        - LAN port settings (VLANs, enabled status) for each AP

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            include_lan_port_settings: If True, fetch full LAN port settings for each AP

        Returns:
            Dictionary with:
            {
                'venue': {...},
                'ap_groups': [...],         # List of AP Groups with members and SSIDs
                'total_ap_groups': int,
                'total_aps': int,
                'total_ssids': int
            }
        """
        try:
            logger.info(f"Building network summary for venue {venue_id} (include_lan_port_settings={include_lan_port_settings})")

            # 1. Get venue details
            venue = await self.get_venue(tenant_id, venue_id)

            # 2. Get venue-level LAN port settings (defaults for all AP models)
            venue_lan_port_settings = []
            if include_lan_port_settings:
                venue_lan_port_settings = await self.get_venue_lan_port_settings(tenant_id, venue_id)
                logger.info(f"Fetched venue LAN port settings for {len(venue_lan_port_settings)} AP models")

            # 3. Get all AP Groups with details
            ap_groups = await self.get_all_ap_groups_with_details(tenant_id, venue_id, include_lan_port_settings=include_lan_port_settings)

            # 4. Calculate totals
            total_aps = sum(g['total_aps'] for g in ap_groups)
            unique_ssids = set()
            for g in ap_groups:
                unique_ssids.update(g['ssid_names'])

            summary = {
                'venue': venue,
                'venue_lan_port_settings': venue_lan_port_settings,  # Default LAN port settings per model
                'ap_groups': ap_groups,
                'total_ap_groups': len(ap_groups),
                'total_aps': total_aps,
                'total_ssids': len(unique_ssids)
            }

            logger.info(f"Venue Summary: {len(ap_groups)} AP Groups, {total_aps} APs, {len(unique_ssids)} unique SSIDs")

            return summary

        except Exception as e:
            logger.error(f"Error building venue network summary: {str(e)}")
            raise

    # ========== LAN Port Configuration Methods ==========

    async def get_venue_lan_port_settings(self, tenant_id: str, venue_id: str):
        """
        Get venue-level LAN port settings for all AP models.

        These are the default/parent settings that APs inherit before any per-AP overrides.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID

        Returns:
            List of model settings:
            [
                {
                    'model': 'H510',
                    'poeMode': 'Auto',
                    'poeOut': False,
                    'lanPorts': [
                        {'portId': '1', 'enabled': True, 'untagId': 1, 'type': 'ACCESS', 'vlanMembers': '1'},
                        ...
                    ]
                },
                ...
            ]
        """
        try:
            logger.info(f"Fetching venue LAN port settings for venue {venue_id}")
            if self.client.ec_type == "MSP":
                response = self.client.get(
                    f"/templates/venues/{venue_id}/apModelLanPortSettings",
                    override_tenant_id=tenant_id
                )
            else:
                response = self.client.get(
                    f"/templates/venues/{venue_id}/apModelLanPortSettings"
                )

            logger.debug(f"Venue LAN port settings response: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Venue LAN port settings data type: {type(data)}, length: {len(data) if isinstance(data, list) else 'N/A'}")
                # Response is an array of model settings
                if isinstance(data, list):
                    logger.info(f"Got venue LAN port settings for {len(data)} models")
                    return data
                elif isinstance(data, dict) and 'data' in data:
                    logger.info(f"Got venue LAN port settings for {len(data['data'])} models")
                    return data['data']
                else:
                    logger.warning(f"Unexpected venue LAN port settings format: {type(data)}")
                    return []
            else:
                logger.warning(f"Failed to get venue LAN port settings: {response.status_code} - {response.text[:200]}")
                return []
        except Exception as e:
            logger.warning(f"Error getting venue LAN port settings: {str(e)}")
            return []

    async def get_ap_lan_port_specific_settings(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str
    ):
        """
        Get AP-level LAN port specific settings (poeMode, poeOut, useVenueSettings).

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number

        Returns:
            Dict with poeMode, poeOut, useVenueSettings
        """
        try:
            if self.client.ec_type == "MSP":
                response = self.client.get(
                    f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings",
                    override_tenant_id=tenant_id
                )
            else:
                response = self.client.get(
                    f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings"
                )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Failed to get AP LAN port specific settings for {serial_number}: {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"Error getting AP LAN port specific settings for {serial_number}: {str(e)}")
            return None

    async def get_ap_lan_port_settings(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str,
        port_id: str
    ):
        """
        Get settings for a specific LAN port on an AP.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number
            port_id: Port ID (e.g., "LAN1", "LAN2", "1", "2")

        Returns:
            Dict with enabled, overwriteUntagId, overwriteVlanMembers, etc.
        """
        try:
            # Normalize port_id - API uses just the number (1, 2, 3, 4)
            if port_id.upper().startswith('LAN'):
                port_number = port_id.upper().replace('LAN', '')
            else:
                port_number = port_id

            if self.client.ec_type == "MSP":
                response = self.client.get(
                    f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                    override_tenant_id=tenant_id
                )
            else:
                response = self.client.get(
                    f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings"
                )

            if response.status_code == 200:
                return response.json()
            else:
                logger.debug(f"Failed to get LAN port {port_id} settings for {serial_number}: {response.status_code}")
                return None
        except Exception as e:
            logger.debug(f"Error getting LAN port {port_id} settings for {serial_number}: {str(e)}")
            return None

    async def get_ap_all_lan_port_settings(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str,
        model: str = None
    ):
        """
        Get all LAN port settings for an AP (specific settings + per-port settings).

        If model is provided, only queries ports that exist on that model.
        Otherwise falls back to querying LAN1-LAN4.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number
            model: Optional AP model (e.g., "H510", "R750") to optimize port queries

        Returns:
            Dict with:
            {
                'poeMode': str,
                'poeOut': bool,
                'useVenueSettings': bool,
                'ports': [
                    {'portId': 'LAN1', 'enabled': bool, 'untagId': int, 'vlanMembers': str},
                    ...
                ]
            }
        """
        from r1api.models import get_all_ports, has_configurable_lan_ports

        result = {
            'poeMode': None,
            'poeOut': False,
            'useVenueSettings': True,
            'ports': []
        }

        # Skip LAN port queries entirely for models without LAN ports
        if model and not has_configurable_lan_ports(model):
            logger.debug(f"AP {serial_number} model {model} has no configurable LAN ports, skipping port queries")
            return result

        # Get AP-level specific settings
        specific_settings = await self.get_ap_lan_port_specific_settings(
            tenant_id, venue_id, serial_number
        )
        if specific_settings:
            result['poeMode'] = specific_settings.get('poeMode')
            result['poeOut'] = specific_settings.get('poeOut', False)
            result['useVenueSettings'] = specific_settings.get('useVenueSettings', True)

        # Determine which ports to query based on model
        if model:
            ports_to_query = get_all_ports(model)
            if not ports_to_query:
                # Model not recognized, fall back to standard ports
                ports_to_query = ['LAN1', 'LAN2']
            logger.debug(f"AP {serial_number} model {model}: querying ports {ports_to_query}")
        else:
            # No model provided, fall back to LAN1-LAN4
            ports_to_query = ['LAN1', 'LAN2', 'LAN3', 'LAN4']
            logger.debug(f"AP {serial_number}: no model provided, querying all ports {ports_to_query}")

        # Query each port
        for port_id in ports_to_query:
            port_settings = await self.get_ap_lan_port_settings(
                tenant_id, venue_id, serial_number, port_id
            )
            logger.debug(f"AP {serial_number} {port_id}: {port_settings}")
            if port_settings:
                result['ports'].append({
                    'portId': port_id,
                    'enabled': port_settings.get('enabled', True),
                    'untagId': port_settings.get('overwriteUntagId'),
                    'vlanMembers': port_settings.get('overwriteVlanMembers', '')
                })

        logger.debug(f"AP {serial_number} final LAN port settings: {result}")
        return result

    async def set_ap_lan_port_specific_settings(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str,
        use_venue_settings: bool = False,
        wait_for_completion: bool = True
    ):
        """
        Set AP-level LAN port specific settings (enable/disable venue settings inheritance).

        This must be called with use_venue_settings=False before setting per-port VLANs.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number
            use_venue_settings: If True, inherit from venue; if False, use AP-level overrides
            wait_for_completion: If True, wait for async task to complete

        Returns:
            Response from API
        """
        payload = {
            "useVenueSettings": use_venue_settings
        }

        logger.debug(f"Setting AP {serial_number} LAN port specific settings: useVenueSettings={use_venue_settings}")

        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPortSpecificSettings",
                payload=payload
            )

        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            logger.error(f"Failed to set AP LAN port specific settings: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def set_ap_lan_port_settings(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str,
        port_id: str,
        untagged_vlan: int,
        vlan_members: str = None,
        wait_for_completion: bool = True
    ):
        """
        Set VLAN configuration for a specific LAN port on an AP.

        This sets the overwrite VLAN for the port, overriding venue-level settings.
        Note: set_ap_lan_port_specific_settings must be called first with use_venue_settings=False.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number
            port_id: Port ID (e.g., "LAN1", "LAN2", "1", "2")
            untagged_vlan: Untagged VLAN ID (1-4094)
            vlan_members: Optional VLAN members string (e.g., "100,200-300") for trunk ports
            wait_for_completion: If True, wait for async task to complete

        Returns:
            Response from API
        """
        # Normalize port_id - accept both "LAN1" and "1" formats
        if not port_id.upper().startswith('LAN'):
            port_id = f"LAN{port_id}"

        # For the API, we need the port number (1, 2, 3, 4)
        port_number = port_id.upper().replace('LAN', '')

        payload = {
            "overwriteUntagId": untagged_vlan,
            "overwriteType": "ACCESS"  # Set port as ACCESS (not TRUNK)
        }

        # Add vlan_members if specified (for trunk ports)
        if vlan_members:
            payload["overwriteVlanMembers"] = vlan_members
            payload["overwriteType"] = "TRUNK"  # If vlan_members specified, use TRUNK
        else:
            # Set vlan_members to just the untagged VLAN for access port behavior
            payload["overwriteVlanMembers"] = str(untagged_vlan)

        logger.debug(f"Setting AP {serial_number} port {port_id} VLAN: {untagged_vlan}")

        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                payload=payload
            )

        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            logger.error(f"Failed to set AP LAN port settings: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def set_ap_lan_port_enabled(
        self,
        tenant_id: str,
        venue_id: str,
        serial_number: str,
        port_id: str,
        enabled: bool,
        wait_for_completion: bool = True
    ):
        """
        Enable or disable a specific LAN port on an AP.

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID
            serial_number: AP serial number
            port_id: Port ID (e.g., "LAN1", "LAN2", "1", "2")
            enabled: True to enable, False to disable
            wait_for_completion: If True, wait for async task to complete

        Returns:
            Response from API
        """
        # Normalize port_id - accept both "LAN1" and "1" formats
        if not port_id.upper().startswith('LAN'):
            port_id = f"LAN{port_id}"

        # For the API, we need the port number (1, 2, 3, 4)
        port_number = port_id.upper().replace('LAN', '')

        payload = {
            "enabled": enabled
        }

        logger.debug(f"Setting AP {serial_number} port {port_id} enabled: {enabled}")

        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/venues/{venue_id}/aps/{serial_number}/lanPorts/{port_number}/settings",
                payload=payload
            )

        if response.status_code in [200, 201, 202]:
            result = response.json() if response.content else {"status": "accepted"}

            if response.status_code == 202 and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            logger.error(f"Failed to set AP LAN port enabled: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

