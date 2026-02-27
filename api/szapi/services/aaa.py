"""
SmartZone AAA Services

Fetches authentication and accounting service details by ID.
Used by the migration extractor to chase WLAN foreign key references.

SZ API paths (from sz_openapi_v13_1.json):
  - GET /services/auth/radius/{id}  — RADIUS auth service
  - GET /services/auth/ad/{id}      — Active Directory auth service
  - GET /services/auth/ldap/{id}    — LDAP auth service
  - GET /services/auth/guest/{id}   — Guest auth service
  - GET /services/auth/local_db/{id} — Local DB auth service
  - GET /services/acct/radius/{id}  — RADIUS accounting service
  - GET /profiles/auth/{id}         — Auth profile by ID (zone-scoped)
  - GET /profiles/acct/{id}         — Acct profile by ID (zone-scoped)
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class AAAService:
    def __init__(self, client):
        self.client = client

    async def get_auth_service(self, service_id: str) -> Dict[str, Any]:
        """
        Fetch an authentication service/profile by ID.

        Tries the profile endpoint first (zone-scoped), then falls back
        to the RADIUS service endpoint (system-scoped).

        Args:
            service_id: Auth service/profile UUID

        Returns:
            Auth service detail object
        """
        # Try profile endpoint first (covers zone-scoped auth profiles)
        try:
            endpoint = f"/{self.client.api_version}/profiles/auth/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.debug(f"Auth profile lookup failed for {service_id}, trying RADIUS service: {e}")

        # Fall back to RADIUS service endpoint
        try:
            endpoint = f"/{self.client.api_version}/services/auth/radius/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.debug(f"RADIUS auth service lookup failed for {service_id}, trying AD: {e}")

        # Try AD service
        try:
            endpoint = f"/{self.client.api_version}/services/auth/ad/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.debug(f"AD auth service lookup failed for {service_id}, trying LDAP: {e}")

        # Try LDAP service
        try:
            endpoint = f"/{self.client.api_version}/services/auth/ldap/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.warning(f"All auth service lookups failed for {service_id}: {e}")
            return {"id": service_id, "_error": f"Could not resolve auth service: {e}"}

    async def get_accounting_service(self, service_id: str) -> Dict[str, Any]:
        """
        Fetch an accounting service/profile by ID.

        Args:
            service_id: Accounting service/profile UUID

        Returns:
            Accounting service detail object
        """
        # Try profile endpoint first
        try:
            endpoint = f"/{self.client.api_version}/profiles/acct/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.debug(f"Acct profile lookup failed for {service_id}, trying RADIUS service: {e}")

        # Fall back to RADIUS accounting service
        try:
            endpoint = f"/{self.client.api_version}/services/acct/radius/{service_id}"
            return await self.client._request("GET", endpoint)
        except Exception as e:
            logger.warning(f"All accounting service lookups failed for {service_id}: {e}")
            return {"id": service_id, "_error": f"Could not resolve accounting service: {e}"}
