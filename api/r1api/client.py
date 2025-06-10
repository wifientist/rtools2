import requests
import time
from r1api.token_cache import get_cached_token, store_token
from r1api.services.msp import MspService
from r1api.services.venues import VenueService
from r1api.services.networks import NetworksService
from r1api.services.tenant import TenantService

class R1Client:
    def __init__(self, tenant_id, client_id, shared_secret, region=None):
        print("Initializing R1Client...")
        # self.token = None
        # self.token_expiry = None  # optional if you want expiry management
        self.session = requests.Session()

        if region == 'EU':
            self.host = 'api.eu.ruckus.cloud'
        elif region == 'ASIA':
            self.host = 'api.asia.ruckus.cloud'
        else:
            self.host = 'api.ruckus.cloud'

        self.tenant_id = tenant_id
        self.client_id = client_id
        self.shared_secret = shared_secret

        token = get_cached_token(tenant_id)
        if token:
            self.token = token
        else:
            self._authenticate()
        
        # Attach modular services
        self.msp = MspService(self)
        self.networks = NetworksService(self)
        self.venues = VenueService(self)
        self.tenant = TenantService(self)

    def _authenticate(self):
        """Authenticate with R1 API using client_id and shared_secret."""
        url = f"https://{self.host}/oauth2/token/{self.tenant_id}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.shared_secret
        }
        response = self.session.post(url, headers=headers, data=data, verify=False)

        if not response.ok:
            raise Exception(f"Failed to authenticate: {response.text}")

        data = response.json()
        self.token = data.get('access_token') or data.get('token')
        expires_in = data.get('expires_in', 3600)  # default to 1hr if not specified
        store_token(self.tenant_id, self.token, expires_in)

        print(f"Authentication successful: {self.token[:8]}...")
        print(f'Token expiry: {data.get("expires_in", "N/A")} seconds')

    def _request(self, method, path, payload=None, params=None, override_tenant_id=None):
        """General request wrapper."""
        #self._ensure_token()
        url = f"https://{self.host}{path}"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if override_tenant_id:
            headers["x-rks-tenantid"] = override_tenant_id

        print("--- R1Client Request ---")
        print(f'client_id: {self.client_id}, tenant_id: {self.tenant_id}, host (region): {self.host}')
        print("Preparing _request:")
        print(f"Method: {method.upper()}")
        print(f"URL: {url}")
        # if payload is None:
        #     payload = {}
        # if params is None:
        #     params = {}
        print("Payload, Params:")
        # print(headers)
        print(payload)
        print(params)

        response = self.session.request(
            method,
            url,
            headers=headers,
            json=payload,
            params=params,
            verify=False
        )

        print(f"{method.upper()} {url} --> {response.status_code}")
        if not response.ok:
            print(f"Error body: {response.text}")

        return response

    # Basic HTTP verbs
    def get(self, path, params=None, override_tenant_id=None):
        return self._request("get", path, params=params, override_tenant_id=override_tenant_id)

    def post(self, path, payload=None, override_tenant_id=None):
        return self._request("post", path, payload=payload, override_tenant_id=override_tenant_id)

    def put(self, path, payload=None, override_tenant_id=None):
        return self._request("put", path, payload=payload, override_tenant_id=override_tenant_id)

    def delete(self, path, override_tenant_id=None):
        return self._request("delete", path, override_tenant_id=override_tenant_id)
