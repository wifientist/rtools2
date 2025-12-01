
class VenueService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_venues(self, tenant_id: str):
        """
        Get all venues
        """
        # print(f" ================== getting venues for tenant_id: {tenant_id} from venue service")
        # print(f"client.ec_type: {self.client.ec_type}")
        # print(f"tenant_id: {tenant_id}")
        # print(f"client tenant_id: {self.client.tenant_id}")
        # print("venues without override:")
        # print(self.client.get("/venues").json())
        # print("venues with override:")
        # print(self.client.get("/venues", override_tenant_id=tenant_id).json())
        if self.client.ec_type == "MSP":
            resp = self.client.get("/venues", override_tenant_id=tenant_id).json()
            print(resp)
            return resp
        else:
            resp = self.client.get("/venues").json()
            print(resp)
            return resp

    async def get_aps_by_tenant_venue(self, tenant_id: str, venue_id: str):
        """
        Get all APs for a venue
        """
        body = {
            'fields': ["name","status","model","networkStatus","macAddress","venueName","switchName","meshRole","clientCount","apGroupId","apGroupName","lanPortStatuses","tags","serialNumber","radioStatuses","venueId","poePort","firmwareVersion","uptime","afcStatus","powerSavingStatus"],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {
                'venueId': [venue_id]
            }
        }
        if self.client.ec_type == "MSP":
            return self.client.post(f"/venues/aps/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            return self.client.post(f"/venues/aps/query", payload=body).json()

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
        Get all AP groups for a tenant
        """
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"🔍 get_ap_groups called - tenant_id: {tenant_id}, ec_type: {self.client.ec_type}")

        if self.client.ec_type == "MSP" and tenant_id:
            response = self.client.get("/venues/apGroups", override_tenant_id=tenant_id).json()
        else:
            response = self.client.get("/venues/apGroups").json()

        logger.info(f"📊 AP Groups Response Type: {type(response)}")

        if isinstance(response, dict):
            logger.info(f"📊 AP Groups Response Keys: {response.keys()}")
            if 'data' in response:
                logger.info(f"📊 AP Groups Count: {len(response.get('data', []))}")
                for idx, group in enumerate(response.get('data', [])[:5]):  # Log first 5 groups
                    logger.info(f"  Group {idx}: {group}")
        elif isinstance(response, list):
            logger.info(f"📊 AP Groups Count (list): {len(response)}")
            for idx, group in enumerate(response[:5]):  # Log first 5 groups
                logger.info(f"  Group {idx}: {group}")

        return response

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
        import logging
        logger = logging.getLogger(__name__)

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


