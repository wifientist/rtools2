
class NetworksService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_wifi_networks(self, tenant_id): #, r1_client: R1Client = None):
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
        body = {
            'fields': fields,
            'sortField': 'name',
            'sortOrder': 'ASC',
            }
        return self.client.post("/wifiNetworks/query", payload=body, override_tenant_id=tenant_id).json()