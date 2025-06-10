
class TenantService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_tenant_venues(self, tenant_id: str):
        """
        Get all venues for a tenant
        """
        return self.client.get("/venues", override_tenant_id=tenant_id).json()

    async def get_tenant_aps(self, tenant_id: str):
        """
        Get all APs for a venue
        """
        body = {
            'fields': ["name","status","model","networkStatus","macAddress","venueName","switchName","meshRole","clientCount","apGroupId","apGroupName","lanPortStatuses","tags","serialNumber","radioStatuses","venueId","poePort","firmwareVersion","uptime","afcStatus","powerSavingStatus"],
            'sortField': 'name',
            'sortOrder': 'ASC',
        }
        return self.client.post(f"/venues/aps/query", payload=body, override_tenant_id=tenant_id).json()

