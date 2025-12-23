from r1api.constants import (
    WifiNetworkType,
    WlanSecurity,
    SECURITY_TYPE_MAP,
    R1StatusCode
)


class NetworksService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_wifi_networks(self, tenant_id): #, r1_client: R1Client = None):
        """
        Get all WiFi networks for a tenant, handling pagination automatically.

        The query endpoint has pagination that may limit results. This method
        fetches all pages and returns the complete list.
        """
        fields = [
            "check-all",
            "name",
            "description",
            "nwSubType",
            "venues",
            "aps",
            "clients",
            "vlan",
            "cog",
            "ssid",
            "vlanPool",
            "captiveType",
            "id",
            "isOweMaster",
            "owePairNetworkId",
            "dsaeOnboardNetwork",
            "venueApGroups"
            ]

        # Fetch first page to get totalCount
        body = {
            'fields': fields,
            'sortField': 'name',
            'sortOrder': 'ASC',
            'page': 0,
            'pageSize': 100
        }

        first_response = self.client.post("/wifiNetworks/query", payload=body, override_tenant_id=tenant_id).json()

        all_networks = first_response.get('data', [])
        total_count = first_response.get('totalCount', len(all_networks))

        print(f"📡 WIFI NETWORKS PAGINATION:")
        print(f"  - First page returned: {len(all_networks)} networks")
        print(f"  - Total count: {total_count}")

        # If there are more pages, fetch them
        if total_count > len(all_networks):
            page_size = len(all_networks) or 100  # Actual page size returned
            pages_needed = (total_count + page_size - 1) // page_size

            print(f"  - Page size: {page_size}")
            print(f"  - Total pages needed: {pages_needed}")

            for page_num in range(1, pages_needed):
                body['page'] = page_num
                page_response = self.client.post("/wifiNetworks/query", payload=body, override_tenant_id=tenant_id).json()
                page_data = page_response.get('data', [])

                print(f"  - Page {page_num + 1} returned: {len(page_data)} networks")

                all_networks.extend(page_data)

        print(f"✅ Total WiFi Networks fetched: {len(all_networks)}")

        return {'data': all_networks, 'totalCount': total_count}

    async def get_wifi_network_by_id(self, network_id: str, tenant_id: str = None):
        """
        Get a specific WiFi network by ID

        Args:
            network_id: WiFi network ID
            tenant_id: Tenant/EC ID (required for MSP)

        Returns:
            WiFi network details
        """
        if self.client.ec_type == "MSP" and tenant_id:
            return self.client.get(f"/wifiNetworks/{network_id}", override_tenant_id=tenant_id).json()
        else:
            return self.client.get(f"/wifiNetworks/{network_id}").json()

    async def find_wifi_network_by_name(self, tenant_id: str, venue_id: str, network_name: str):
        """
        Search for a WiFi network by name (IDEMPOTENT check)

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID to search within
            network_name: Name of the network to find

        Returns:
            Network object if found, None otherwise
        """
        body = {
            'fields': ['id', 'name', 'ssid', 'vlan', 'nwSubType', 'venueApGroups'],
            'filters': {
                'name': [network_name]
            },
            'sortField': 'name',
            'sortOrder': 'ASC',
        }

        if self.client.ec_type == "MSP":
            response = self.client.post("/wifiNetworks/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            response = self.client.post("/wifiNetworks/query", payload=body).json()

        # Response format: {"data": [...], "totalCount": N}
        networks = response.get('data', [])

        if networks and len(networks) > 0:
            # Network found (silent - will be logged by caller)
            return networks[0]

        # Network not found (silent - will be logged by caller)
        return None

    async def create_wifi_network(
        self,
        tenant_id: str,
        venue_id: str,
        name: str,
        ssid: str,
        passphrase: str,
        security_type: str = "WPA3",
        vlan_id: int = 1,
        description: str = None,
        wait_for_completion: bool = True
    ):
        """
        Create a new WiFi network (SSID) in RuckusONE

        Args:
            tenant_id: Tenant/EC ID
            venue_id: Venue ID where network will be created
            name: Network name (internal identifier)
            ssid: SSID broadcast name
            passphrase: WiFi password (8-64 characters)
            security_type: One of: WPA3, WPA2, WPA2/WPA3 (default: WPA3)
            vlan_id: VLAN ID (1-4094, default: 1)
            description: Optional description
            wait_for_completion: If True, wait for async task to complete (default: True)

        Returns:
            Created network response from API
        """
        # Map security types to API values using constants
        wlan_security = SECURITY_TYPE_MAP.get(security_type, WlanSecurity.WPA3)

        # Build WLAN settings based on security type
        wlan_settings = {
            "ssid": ssid,
            "wlanSecurity": wlan_security,
            "vlanId": int(vlan_id),
            "enabled": True
        }

        # Add passphrase field(s) based on security type
        # WPA3 uses saePassphrase, WPA2 uses passphrase, Mixed uses both
        if wlan_security == WlanSecurity.WPA3:
            wlan_settings["saePassphrase"] = passphrase
        elif wlan_security == WlanSecurity.WPA2_PERSONAL:
            wlan_settings["passphrase"] = passphrase
        elif wlan_security == WlanSecurity.WPA23_MIXED:
            # Mixed mode needs both
            wlan_settings["passphrase"] = passphrase
            wlan_settings["saePassphrase"] = passphrase
        else:
            # Fallback for other types (WPA, etc.)
            wlan_settings["passphrase"] = passphrase

        # Build payload for PSK network
        payload = {
            "type": WifiNetworkType.PSK,  # Required discriminator field for polymorphic WiFi networks
            "name": name,
            "wlan": wlan_settings
        }

        if description:
            payload["description"] = description

        # Make API call
        if self.client.ec_type == "MSP":
            response = self.client.post(
                "/wifiNetworks",
                payload=payload,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.post(
                "/wifiNetworks",
                payload=payload
            )

        # API returns 202 Accepted for async operations
        if response.status_code in [R1StatusCode.OK, R1StatusCode.CREATED, R1StatusCode.ACCEPTED]:
            result = response.json() if response.content else {"status": "accepted"}

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == R1StatusCode.ACCEPTED and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

                    # After async task completes, fetch the created resource to get its ID
                    created_network = await self.find_wifi_network_by_name(tenant_id, venue_id, name)
                    if created_network:
                        return created_network
                    else:
                        print(f"    ⚠️  Task completed but could not find created network '{name}'")
                        return result

            return result
        else:
            print(f"  ❌ Failed to create network: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None

    async def update_wifi_network_venue_config(
        self,
        tenant_id: str,
        network_id: str,
        venue_id: str,
        ap_group_id: str,
        ap_group_name: str = None,
        radio_types: list = None,
        vlan_id: int = None,
        wait_for_completion: bool = True
    ):
        """
        Update a WiFi network's venue configuration to use specific AP Groups.

        This is the approach the R1 frontend uses - it updates the network object
        directly with the venue configuration including isAllApGroups: false.

        Args:
            tenant_id: Tenant/EC ID
            network_id: WiFi Network ID to update
            venue_id: Venue ID
            ap_group_id: AP Group ID to activate on
            ap_group_name: AP Group name (optional)
            radio_types: List of radio types (default: all radios)
            vlan_id: Optional VLAN ID override
            wait_for_completion: If True, wait for async task to complete

        Returns:
            Updated network response from API
        """
        import logging
        logger = logging.getLogger(__name__)

        # Default radio types
        if radio_types is None:
            radio_types = ["2.4-GHz", "5-GHz", "6-GHz"]

        # First, get the current network object
        logger.info(f"    Fetching current network {network_id}...")
        current_network = await self.get_wifi_network_by_id(network_id, tenant_id)

        if not current_network:
            raise Exception(f"Network {network_id} not found")

        # Log current venue config for debugging
        current_venues = current_network.get("venues", [])
        logger.info(f"    Current venues count: {len(current_venues)}")
        for cv in current_venues:
            logger.info(f"      Venue {cv.get('venueId')}: isAllApGroups={cv.get('isAllApGroups')}, apGroups={len(cv.get('apGroups', []))}")

        # Build the AP Group entry
        ap_group_entry = {
            "apGroupId": ap_group_id,
            "radioTypes": radio_types,
            "radio": "Both",
            "isDefault": False
        }
        if ap_group_name:
            ap_group_entry["apGroupName"] = ap_group_name
        if vlan_id is not None:
            ap_group_entry["vlanId"] = int(vlan_id) if isinstance(vlan_id, str) else vlan_id

        # Update or add the venue in the network's venues array
        # MERGE with existing venue config instead of replacing entirely
        venues = current_network.get("venues", [])
        venue_found = False
        for i, v in enumerate(venues):
            if v.get("venueId") == venue_id:
                # Merge: keep existing fields, update AP Group settings
                v["isAllApGroups"] = False
                v["apGroups"] = [ap_group_entry]
                v["scheduler"] = v.get("scheduler", {"type": "ALWAYS_ON"})
                v["allApGroupsRadio"] = "Both"
                v["allApGroupsRadioTypes"] = radio_types
                venues[i] = v
                venue_found = True
                logger.info(f"    Merged venue config: {v}")
                break

        if not venue_found:
            # Create new venue config
            venue_config = {
                "venueId": venue_id,
                "isAllApGroups": False,
                "apGroups": [ap_group_entry],
                "scheduler": {"type": "ALWAYS_ON"},
                "allApGroupsRadio": "Both",
                "allApGroupsRadioTypes": radio_types
            }
            venues.append(venue_config)
            logger.info(f"    Added new venue config: {venue_config}")

        current_network["venues"] = venues

        logger.info(f"    Updating network with venue config: isAllApGroups=False, apGroup={ap_group_name or ap_group_id}")

        # PUT the updated network object
        if self.client.ec_type == "MSP":
            response = self.client.put(
                f"/wifiNetworks/{network_id}",
                payload=current_network,
                override_tenant_id=tenant_id
            )
        else:
            response = self.client.put(
                f"/wifiNetworks/{network_id}",
                payload=current_network
            )

        # Handle response
        if response.status_code in [R1StatusCode.OK, R1StatusCode.CREATED, R1StatusCode.ACCEPTED]:
            result = response.json() if response.content else {"status": "accepted"}

            # If 202 Accepted and wait_for_completion=True, poll for task completion
            if response.status_code == R1StatusCode.ACCEPTED and wait_for_completion:
                request_id = result.get('requestId')
                if request_id:
                    await self.client.await_task_completion(request_id, override_tenant_id=tenant_id)

            return result
        else:
            print(f"  ❌ Failed to update network venue config: {response.status_code} - {response.text}")
            response.raise_for_status()
            return None