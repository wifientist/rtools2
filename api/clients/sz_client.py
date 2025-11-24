"""
SmartZone API Client

Provides a client for interacting with Ruckus SmartZone controllers.
Supports AP inventory queries needed for migration to RuckusONE.
"""

import httpx
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SmartZoneClient:
    """
    Client for Ruckus SmartZone API

    Handles authentication, session management, and AP inventory queries
    for SmartZone controllers.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8443,
        use_https: bool = True,
        verify_ssl: bool = False,
        api_version: str = "v12_0"
    ):
        """
        Initialize SmartZone API client

        Args:
            host: SmartZone hostname or IP
            username: Admin username
            password: Admin password
            port: API port (default 8443)
            use_https: Use HTTPS protocol (default True)
            verify_ssl: Verify SSL certificates (default False for self-signed certs)
            api_version: SmartZone API version (e.g., v11_1, v12_0, v13_0). Default v12_0
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_https = use_https
        self.verify_ssl = verify_ssl
        self.api_version = api_version

        protocol = "https" if use_https else "http"
        self.base_url = f"{protocol}://{host}:{port}/wsg/api/public"

        self.session_id: Optional[str] = None
        self.session_expiry: Optional[datetime] = None

        # HTTP client with timeout and SSL verification settings
        self.client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=30.0,
            headers={
                "Content-Type": "application/json"
            }
        )

    async def __aenter__(self):
        """Async context manager entry"""
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.logout()
        await self.client.aclose()

    async def login(self) -> str:
        """
        Authenticate to SmartZone and obtain session cookie

        Returns:
            Session ID string

        Raises:
            ValueError: If authentication fails with detailed error message
            Exception: For other connection issues
        """
        url = f"{self.base_url}/{self.api_version}/serviceTicket"

        payload = {
            "username": self.username,
            "password": self.password
        }

        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            self.session_id = data.get("serviceTicket")

            # Session typically expires in 30 minutes
            self.session_expiry = datetime.now() + timedelta(minutes=25)

            logger.info(f"Successfully authenticated to SmartZone at {self.host}")
            return self.session_id

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 401:
                logger.error(f"SmartZone authentication failed - Invalid credentials for {self.username}@{self.host}")
                raise ValueError(f"Invalid SmartZone credentials for {self.username}@{self.host}")
            elif status_code == 404:
                logger.error(f"SmartZone API endpoint not found at {self.host}:{self.port} - Check host/port/version")
                raise ValueError(f"SmartZone API not found at {self.host}:{self.port}. Verify host, port, and API version.")
            else:
                logger.error(f"SmartZone authentication failed with status {status_code}: {e}")
                raise ValueError(f"SmartZone authentication failed: HTTP {status_code}")
        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to SmartZone at {self.host}:{self.port} - {e}")
            raise ValueError(f"Cannot connect to SmartZone at {self.host}:{self.port}. Check network connectivity and hostname.")
        except httpx.TimeoutException as e:
            logger.error(f"SmartZone connection timeout at {self.host}:{self.port}")
            raise ValueError(f"Connection timeout to SmartZone at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Unexpected error during SmartZone authentication: {e}")
            raise

    async def logout(self):
        """Log out and invalidate the session"""
        if not self.session_id:
            return

        url = f"{self.base_url}/{self.api_version}/serviceTicket"

        try:
            await self.client.delete(
                url,
                params={"serviceTicket": self.session_id}
            )
            logger.info(f"Logged out from SmartZone at {self.host}")
        except Exception as e:
            logger.warning(f"Error during SmartZone logout: {e}")
        finally:
            self.session_id = None
            self.session_expiry = None

    async def ensure_authenticated(self):
        """Ensure we have a valid session, re-authenticate if needed"""
        if not self.session_id or (
            self.session_expiry and datetime.now() >= self.session_expiry
        ):
            await self.login()

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments for httpx request

        Returns:
            JSON response data
        """
        await self.ensure_authenticated()

        url = f"{self.base_url}{endpoint}"

        # Add session ticket to query params
        params = kwargs.get("params", {})
        params["serviceTicket"] = self.session_id
        kwargs["params"] = params

        response = await self.client.request(method, url, **kwargs)
        response.raise_for_status()

        return response.json()

    async def get_zones(self) -> List[Dict[str, Any]]:
        """
        Get all zones (domains) in the SmartZone

        Returns:
            List of zone objects with id, name, description
        """
        data = await self._request("GET", f"/{self.api_version}/rkszones")
        return data.get("list", [])

    async def get_aps_by_zone(
        self,
        zone_id: str,
        page: int = 0,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """
        Get all APs in a specific zone

        Args:
            zone_id: Zone/domain UUID
            page: Page number (default 0)
            limit: Results per page (default 1000, max 1000)

        Returns:
            Dict with 'list' of AP objects and pagination info
        """
        endpoint = f"/{self.api_version}/rkszones/{zone_id}/aps"
        params = {
            "index": page,
            "listSize": min(limit, 1000)
        }

        return await self._request("GET", endpoint, params=params)

    async def get_all_aps(self) -> List[Dict[str, Any]]:
        """
        Get all APs across all zones in the SmartZone

        Returns:
            List of all AP objects
        """
        all_aps = []

        # Get all zones
        zones = await self.get_zones()

        # Get APs from each zone
        for zone in zones:
            zone_id = zone.get("id")
            zone_name = zone.get("name", "Unknown")

            logger.info(f"Fetching APs from zone: {zone_name} ({zone_id})")

            page = 0
            while True:
                result = await self.get_aps_by_zone(zone_id, page=page)

                aps = result.get("list", [])
                if not aps:
                    break

                # Add zone info to each AP
                for ap in aps:
                    ap["zoneName"] = zone_name
                    ap["zoneId"] = zone_id

                all_aps.extend(aps)

                # Check if there are more pages
                total = result.get("totalCount", 0)
                current_count = len(all_aps)

                if current_count >= total:
                    break

                page += 1

        logger.info(f"Retrieved {len(all_aps)} total APs from SmartZone")
        return all_aps

    async def get_ap_details(self, ap_mac: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific AP

        Args:
            ap_mac: AP MAC address

        Returns:
            AP detail object
        """
        endpoint = f"/{self.api_version}/aps/{ap_mac}"
        return await self._request("GET", endpoint)

    def format_ap_for_r1_import(self, ap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format SmartZone AP data for RuckusONE import

        Args:
            ap: SmartZone AP object

        Returns:
            Formatted AP object ready for R1 import
        """
        return {
            "name": ap.get("name", ""),
            "description": ap.get("description", ""),
            "serial": ap.get("serial", ""),
            "model": ap.get("model", ""),
            "mac": ap.get("mac", ""),
            "zoneName": ap.get("zoneName", ""),
            "location": ap.get("location", ""),
            "latitude": ap.get("latitude"),
            "longitude": ap.get("longitude"),
            "gpsSource": ap.get("gpsSource"),
            # Add additional fields as needed
        }
