
class MspService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client

    def get_msp_ecs(self):
        body = {
            'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {'tenantType': ['MSP_EC']}
        }
        return self.client.post("/mspecs/query", payload=body).json()

    def get_msp_tech_partners(self):
        body = {
            'fields': ['check-all', 'id', 'name', 'tenantType', 'mspAdminCount', 'mspEcAdminCount'],
            'sortField': 'name',
            'sortOrder': 'ASC',
            'filters': {'tenantType': ['MSP_INSTALLER', 'MSP_INTEGRATOR']}
        }
        return self.client.post("/mspecs/query", payload=body).json()
