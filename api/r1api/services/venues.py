
class VenueService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    # async def get_msp_ecs(self):
    #     body = {
    #         'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
    #         'sortField': 'name',
    #         'sortOrder': 'ASC',
    #         'filters': {'tenantType': ['MSP_EC']}
    #     }
    #     return self.client.post("/mspecs/query", payload=body).json()


    # async def get_msp_customer_admins(self, tenant_id: str): #, r1_client: R1Client = None):
    #     #r1_client = r1_client or get_r1_client()
    #     return self.client.get(f"/mspCustomers/{tenant_id}/admins", override_tenant_id=tenant_id).json()

    async def get_venues(self):
        """
        Get all venues
        """
        return self.client.get("/venues").json()

    async def get_venue_aps(self, venue_id: str):
        """
        Get all APs for a venue
        """
        body = {
            'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {'tenantType': ['MSP_EC']}
        }

        return self.client.post(f"/venues/{venue_id}/aps/query").json()
