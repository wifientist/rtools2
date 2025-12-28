import logging

logger = logging.getLogger(__name__)


class TenantService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_tenant_self(self): #, tenant_id: str):
        """
        Get tenant self information
        """
        return self.client.get(f"/tenants/self").json()

    async def get_tenant_user_profiles(self): #, tenant_id: str):
        """
        Get all user profiles for a tenant
        """
        return self.client.get("/tenants/userProfiles").json()

    async def get_tenant_venues(self, tenant_id: str):
        """
        Get all venues for a tenant
        """
        if self.client.ec_type == "MSP":
            return self.client.get("/venues", override_tenant_id=tenant_id).json()
        else:
            # For non-MSP, we assume the tenant_id is not needed
            return self.client.get("/venues").json()

    async def get_tenant_aps(self, tenant_id: str):
        """
        Get all APs for a venue
        """
        body = {
            'fields': ["name","status","model","networkStatus","macAddress","venueName","switchName","meshRole","clientCount","apGroupId","apGroupName","lanPortStatuses","tags","serialNumber","radioStatuses","venueId","poePort","firmwareVersion","uptime","afcStatus","powerSavingStatus"],
            'sortField': 'name',
            'sortOrder': 'ASC',
        }
        if self.client.ec_type == "MSP":
            return self.client.post(f"/venues/aps/query", payload=body, override_tenant_id=tenant_id).json()
        else:
            return self.client.post(f"/venues/aps/query", payload=body).json()

