"""
SmartZone API Client

Provides a client for interacting with Ruckus SmartZone controllers.
Supports AP inventory queries needed for migration to RuckusONE.
"""

import asyncio
import httpx
import logging
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from szapi.services.zones import ZoneService
from szapi.services.aps import ApService
from szapi.services.switches import SwitchService
from szapi.services.wlans import WlanService
from szapi.services.apgroups import ApGroupService
from szapi.services.system import SystemService

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """
    Token bucket rate limiter for async operations.

    Allows bursts up to `rate` requests, then throttles to
    maintain average of `rate` requests per `per` seconds.
    """

    def __init__(self, rate: float = 120.0, per: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            rate: Maximum requests allowed per time period (default 120)
            per: Time period in seconds (default 1.0)
        """
        self.rate = rate
        self.per = per
        self.allowance = rate  # Start with full bucket
        self.last_check = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """
        Acquire permission to make a request.
        Blocks if rate limit would be exceeded.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_check
            self.last_check = now

            # Replenish tokens based on elapsed time
            self.allowance += elapsed * (self.rate / self.per)
            if self.allowance > self.rate:
                self.allowance = self.rate

            if self.allowance < 1.0:
                # Need to wait for tokens to replenish
                wait_time = (1.0 - self.allowance) * (self.per / self.rate)
                logger.debug(f"Rate limit: waiting {wait_time:.3f}s")
                await asyncio.sleep(wait_time)
                self.allowance = 0
            else:
                self.allowance -= 1.0


class SZClient:
    """
    Client for Ruckus SmartZone API

    Handles authentication, session management, and delegates
    specific operations to service classes.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8443,
        use_https: bool = True,
        verify_ssl: bool = False,
        api_version: str = "v12_0",
        rate_limit: float = 100.0
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
            rate_limit: Max API requests per second (default 100, SmartZone limit is ~120)
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
        # Root URL without API prefix (for switchm and other alternate APIs)
        self.root_url = f"{protocol}://{host}:{port}"

        self.session_id: Optional[str] = None
        self.session_expiry: Optional[datetime] = None

        # Rate limiter to avoid overwhelming the SmartZone API
        # Default 100 req/s gives headroom under SmartZone's ~120 req/s limit
        self._rate_limiter = AsyncRateLimiter(rate=rate_limit, per=1.0)

        # API stats tracking
        self._stats = {
            'total_calls': 0,
            'start_time': None,
            'last_call_time': None,
            'calls_per_minute': [],  # Rolling window of timestamps
            'errors': 0,
            'endpoints': {}  # Per-endpoint call counts
        }
        self._stats_lock = asyncio.Lock()

        # HTTP client with timeout and SSL verification settings
        self.client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=30.0,
            headers={
                "Content-Type": "application/json"
            }
        )

        # Attach modular services
        self.zones = ZoneService(self)
        self.aps = ApService(self)
        self.switches = SwitchService(self)
        self.wlans = WlanService(self)
        self.apgroups = ApGroupService(self)
        self.system = SystemService(self)

        logger.info(f"SZClient initialized for {host}:{port} with API version {api_version}, rate limit {rate_limit}/s")

    def __repr__(self):
        return f"<SZClient host={self.host}:{self.port}, api_version={self.api_version}>"

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
            logger.info(f"Attempting SmartZone login to {url}")
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
            # Try to get error details from response body
            try:
                error_body = e.response.json()
                error_msg = error_body.get("message", str(e))
            except:
                error_msg = str(e)

            if status_code == 401:
                logger.error(f"SmartZone authentication failed - Invalid credentials for {self.username}@{self.host}. Response: {error_msg}")
                raise ValueError(f"Invalid SmartZone credentials for {self.username}@{self.host}: {error_msg}")
            elif status_code == 404:
                logger.error(f"SmartZone API endpoint not found at {url} - Check host/port/version. Response: {error_msg}")
                raise ValueError(f"SmartZone API not found at {self.host}:{self.port}. Verify host, port, and API version ({self.api_version}). Error: {error_msg}")
            else:
                logger.error(f"SmartZone authentication failed with status {status_code}: {error_msg}")
                raise ValueError(f"SmartZone authentication failed: HTTP {status_code} - {error_msg}")
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

    async def _track_api_call(self, endpoint: str):
        """Track an API call for statistics"""
        async with self._stats_lock:
            now = time.time()

            # Initialize start time on first call
            if self._stats['start_time'] is None:
                self._stats['start_time'] = now

            self._stats['total_calls'] += 1
            self._stats['last_call_time'] = now

            # Track calls per minute (rolling 60-second window)
            self._stats['calls_per_minute'].append(now)
            # Remove calls older than 60 seconds
            cutoff = now - 60
            self._stats['calls_per_minute'] = [
                t for t in self._stats['calls_per_minute'] if t > cutoff
            ]

            # Track per-endpoint counts (simplified endpoint)
            # Extract base endpoint (e.g., "/v12_0/rkszones" from "/v12_0/rkszones/abc/wlans")
            endpoint_parts = endpoint.split('/')
            if len(endpoint_parts) >= 3:
                base_endpoint = '/'.join(endpoint_parts[:3])
            else:
                base_endpoint = endpoint

            self._stats['endpoints'][base_endpoint] = self._stats['endpoints'].get(base_endpoint, 0) + 1

    async def _track_api_error(self):
        """Track an API error"""
        async with self._stats_lock:
            self._stats['errors'] += 1

    def get_api_stats(self) -> Dict[str, Any]:
        """
        Get current API statistics

        Returns:
            Dict with API call statistics
        """
        now = time.time()
        elapsed = now - self._stats['start_time'] if self._stats['start_time'] else 0
        calls_in_last_minute = len(self._stats['calls_per_minute'])

        return {
            'total_calls': self._stats['total_calls'],
            'errors': self._stats['errors'],
            'elapsed_seconds': round(elapsed, 1),
            'avg_calls_per_second': round(self._stats['total_calls'] / elapsed, 2) if elapsed > 0 else 0,
            'calls_last_minute': calls_in_last_minute,
            'current_rate_per_second': round(calls_in_last_minute / 60, 2),
            'top_endpoints': dict(
                sorted(self._stats['endpoints'].items(), key=lambda x: x[1], reverse=True)[:5]
            )
        }

    def reset_api_stats(self):
        """Reset API statistics"""
        self._stats = {
            'total_calls': 0,
            'start_time': None,
            'last_call_time': None,
            'calls_per_minute': [],
            'errors': 0,
            'endpoints': {}
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        use_root_url: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            use_root_url: If True, use root_url instead of base_url (for switchm, etc.)
            **kwargs: Additional arguments for httpx request

        Returns:
            JSON response data
        """
        # Rate limit to avoid overwhelming the SmartZone API
        await self._rate_limiter.acquire()

        await self.ensure_authenticated()

        # Update API stats
        await self._track_api_call(endpoint)

        # Use root URL for alternate APIs like switchm
        base = self.root_url if use_root_url else self.base_url
        url = f"{base}{endpoint}"

        # Add session ticket to query params
        params = kwargs.get("params", {})
        params["serviceTicket"] = self.session_id
        kwargs["params"] = params

        # Log the request (debug level to reduce noise)
        logger.debug(f"SmartZone API Request: {method} {url}")
        logger.debug(f"  Params: {params}")
        if kwargs.get("json"):
            logger.debug(f"  Body: {kwargs.get('json')}")

        try:
            response = await self.client.request(method, url, **kwargs)
            logger.debug(f"SmartZone API Response: {response.status_code} from {method} {endpoint}")

            response.raise_for_status()
            response_data = response.json()

            # Log response summary (debug level)
            if isinstance(response_data, dict):
                if "list" in response_data:
                    logger.debug(f"  Returned {len(response_data['list'])} items (totalCount: {response_data.get('totalCount', 'N/A')})")
                elif "totalCount" in response_data:
                    logger.debug(f"  Total count: {response_data['totalCount']}")

            return response_data

        except httpx.HTTPStatusError as e:
            await self._track_api_error()
            status_code = e.response.status_code
            logger.error(f"SmartZone API Error: {status_code} from {method} {endpoint}")

            # Try to get error details from response
            error_message = None
            error_type = None
            try:
                error_body = e.response.json()
                logger.error(f"  Error details: {error_body}")
                error_message = error_body.get("message")
                error_type = error_body.get("errorType")
            except Exception:
                logger.error(f"  Error body: {e.response.text[:500]}")

            # Build a user-friendly error message
            if error_message:
                friendly_msg = f"SmartZone API error ({status_code}): {error_message}"
                if error_type:
                    friendly_msg += f" [{error_type}]"
            else:
                friendly_msg = f"SmartZone API error ({status_code}) on {method} {endpoint}"

            raise ValueError(friendly_msg) from e
        except Exception as e:
            logger.error(f"SmartZone API Exception: {type(e).__name__} from {method} {endpoint}: {str(e)}")
            raise
