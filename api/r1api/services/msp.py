
class MspService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    async def get_msp_ecs(self):
        print(f"client:", self.client)
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}        
        body = {
            'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {'tenantType': ['MSP_EC']}
        }
        return self.client.post("/mspecs/query", payload=body).json()

    async def get_msp_tech_partners(self):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        body = {
            'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {'tenantType': ['MSP_INSTALLER', 'MSP_INTEGRATOR']}
        }
        return self.client.post("/techpartners/mspecs/query", payload=body).json()

    async def get_msp_labels(self): #, r1_client: R1Client = None):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        #r1_client = r1_client or get_r1_client()
        print("Fetching MSP labels")
        #return self.client.get("/mspLabels").json()
        response = self.client.get("/mspLabels")
        print(f"Response content: {response.content}")

        if response.ok:
            try:
                return response.json()
            except ValueError:
                print("Failed to decode JSON")
                return None
        else:
            print(f"Failed to fetch labels: {response.status_code}")
            return None

    async def get_entitlements(self): #, r1_client: R1Client = None):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        return self.client.get("/entitlements").json()

    async def get_msp_entitlements(self): #, r1_client: R1Client = None):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        return self.client.get("/mspEntitlements").json()

    async def get_msp_admins(self): #, r1_client: R1Client = None):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        return self.client.get("/admins").json()

    async def get_msp_customer_admins(self, tenant_id: str): #, r1_client: R1Client = None):
        if self.client.ec_type != "MSP":
            return {"success": False, "error": "Unavailable for non-MSP clients."}
        return self.client.get(f"/mspCustomers/{tenant_id}/admins", override_tenant_id=tenant_id).json()
