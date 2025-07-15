
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

    

