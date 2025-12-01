import requests
import time
from r1api.token_cache import get_cached_token, store_token
from r1api.services.msp import MspService
from r1api.services.venues import VenueService
from r1api.services.networks import NetworksService
from r1api.services.tenant import TenantService
from r1api.services.aps import ApService
from r1api.services.clients import ClientsService
from r1api.services.entitlements import EntitlementsService

class R1Client:
    def __init__(self, tenant_id, client_id, shared_secret, ec_type=None, region=None):
        print("Initializing R1Client...")
        print(f"tenant_id: {tenant_id}, client_id: {client_id}, shared_secret: {shared_secret}, ec_type: {ec_type}, region: {region}")
        # self.token = None
        # self.token_expiry = None  # optional if you want expiry management
        self.session = requests.Session()

        if region == 'EU':
            self.host = 'api.eu.ruckus.cloud'
        elif region == 'ASIA':
            self.host = 'api.asia.ruckus.cloud'
        else:
            self.host = 'api.ruckus.cloud'

        self.ec_type = ec_type if ec_type else 'EC'

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
        self.aps = ApService(self)
        self.clients = ClientsService(self)
        self.entitlements = EntitlementsService(self)

        print(f"R1Client initialized for tenant_id={tenant_id}, ec_type={self.ec_type}, host={self.host}")

    def __repr__(self):
        return f"<R1Client tenant_id={self.tenant_id}, ec_type={self.ec_type}, host={self.host}>"

    def _authenticate(self):
        """Authenticate with R1 API using client_id and shared_secret."""
        print(f"Authenticating R1Client for tenant_id={self.tenant_id} {self.ec_type}...")
        url = f"https://{self.host}/oauth2/token/{self.tenant_id}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.shared_secret
        }
        response = self.session.post(url, headers=headers, data=data, verify=False)
        #print(f"Response status code: {response.status_code}")
        #print(f"Response headers: {response.headers}")
        #print(f"Response content: {response.content[:300]}")  # Print first 300 chars for brevity

        if not response.ok:
            print(f"❌ Auth failed: {response.status_code}")
            print(f"Response text: {response.text[:300]}")
            self.token = None
            self.auth_failed = True
            self.auth_error = {
                "success": False,
                "error": "Authentication failed",
                "status_code": response.status_code,
                "raw_response": response.text[:500]
            }
            return self.auth_error

        try:
            data = response.json()
        except ValueError:
            print("❌ Failed to decode JSON during authentication")
            self.token = None
            self.auth_failed = True
            self.auth_error = {
                "success": False,
                "error": "Invalid JSON response from token endpoint",
                "raw_response": response.text[:500]
            }
            return self.auth_error

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
        print(f'client_id: {self.client_id}, tenant_id: {self.tenant_id}, host (region): {self.host}{path}')
        #print("Preparing _request:")
        #print(f"Method: {method.upper()}")
        #print(f"URL: {url}")
        # if payload is None:
        #     payload = {}
        # if params is None:
        #     params = {}
        #print("Payload, Params:")
        # print(headers)
        #print(payload)
        #print(params)

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
