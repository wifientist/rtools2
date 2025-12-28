import logging
import requests
import time
import asyncio
from r1api.token_cache import get_cached_token, store_token
from r1api.services.msp import MspService

logger = logging.getLogger(__name__)
from r1api.services.venues import VenueService
from r1api.services.networks import NetworksService
from r1api.services.tenant import TenantService
from r1api.services.aps import ApService
from r1api.services.clients import ClientsService
from r1api.services.entitlements import EntitlementsService
from r1api.services.dpsk import DpskService
from r1api.services.identity import IdentityService
from r1api.services.policy_sets import PolicySetService

class R1Client:
    def __init__(self, tenant_id, client_id, shared_secret, ec_type=None, region=None):
        logger.debug(f"Initializing R1Client for tenant_id={tenant_id}, ec_type={ec_type}, region={region}")
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
        self.dpsk = DpskService(self)
        self.identity = IdentityService(self)
        self.policy_sets = PolicySetService(self)

        logger.info(f"R1Client initialized: tenant_id={tenant_id}, ec_type={self.ec_type}, host={self.host}")

    def __repr__(self):
        return f"<R1Client tenant_id={self.tenant_id}, ec_type={self.ec_type}, host={self.host}>"

    def _authenticate(self):
        """Authenticate with R1 API using client_id and shared_secret."""
        logger.debug(f"Authenticating R1Client for tenant_id={self.tenant_id} ec_type={self.ec_type}")
        url = f"https://{self.host}/oauth2/token/{self.tenant_id}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.shared_secret
        }
        response = self.session.post(url, headers=headers, data=data, verify=True)
        #print(f"Response status code: {response.status_code}")
        #print(f"Response headers: {response.headers}")
        #print(f"Response content: {response.content[:300]}")  # Print first 300 chars for brevity

        if not response.ok:
            logger.error(f"Auth failed: {response.status_code} - {response.text[:300]}")
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
            logger.error("Failed to decode JSON during authentication")
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

        logger.debug(f"Authentication successful, token expires in {expires_in}s")

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

        logger.debug(f"R1Client Request: client_id={self.client_id}, tenant_id={self.tenant_id}, host={self.host}{path}")
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
            verify=True
        )

        logger.debug(f"{method.upper()} {url} --> {response.status_code}")
        if not response.ok:
            logger.warning(f"Request error: {response.status_code} - {response.text[:500]}")

        return response

    # Basic HTTP verbs
    def get(self, path, params=None, override_tenant_id=None):
        return self._request("get", path, params=params, override_tenant_id=override_tenant_id)

    def post(self, path, payload=None, override_tenant_id=None):
        return self._request("post", path, payload=payload, override_tenant_id=override_tenant_id)

    def put(self, path, payload=None, override_tenant_id=None):
        return self._request("put", path, payload=payload, override_tenant_id=override_tenant_id)

    def delete(self, path, payload=None, override_tenant_id=None):
        return self._request("delete", path, payload=payload, override_tenant_id=override_tenant_id)

    def _extract_error_message(self, data: dict) -> str:
        """
        Extract detailed error message from RuckusONE activity response

        RuckusONE error structure can be complex and nested:
        {
            'error': '{"requestId":"...","errors":["{\\"message\\":\\"Validation Error\\",\\"subErrors\\":[...]}"]}'
        }

        This method attempts to parse nested JSON and extract meaningful validation errors.

        Args:
            data: Activity response data from /activities/{requestId}

        Returns:
            str: Detailed error message
        """
        import json

        # Try to get error from 'error' field (most common)
        if 'error' in data:
            error_value = data['error']

            # If it's a string, try to parse it as JSON
            if isinstance(error_value, str):
                try:
                    error_obj = json.loads(error_value)

                    # Check for 'errors' array
                    if isinstance(error_obj, dict) and 'errors' in error_obj:
                        errors_array = error_obj['errors']

                        # Parse each error in the array (they might be JSON strings too)
                        error_messages = []
                        for err in errors_array:
                            if isinstance(err, str):
                                try:
                                    err_obj = json.loads(err)

                                    # Check for subErrors (validation errors)
                                    if isinstance(err_obj, dict) and 'subErrors' in err_obj:
                                        for sub_err in err_obj['subErrors']:
                                            if isinstance(sub_err, dict):
                                                # Format: "DPSK service.name: Name must be unique"
                                                obj = sub_err.get('object', 'Unknown')
                                                field = sub_err.get('field', '')
                                                msg = sub_err.get('message', 'Unknown error')

                                                if field:
                                                    error_messages.append(f"{obj}.{field}: {msg}")
                                                else:
                                                    error_messages.append(f"{obj}: {msg}")

                                    # If no subErrors, use the main message
                                    elif isinstance(err_obj, dict) and 'message' in err_obj:
                                        error_messages.append(err_obj['message'])
                                    else:
                                        error_messages.append(str(err))

                                except json.JSONDecodeError:
                                    # Not JSON, use as-is
                                    error_messages.append(str(err))
                            else:
                                error_messages.append(str(err))

                        if error_messages:
                            return '; '.join(error_messages)

                    # If error_obj has a message field directly
                    elif isinstance(error_obj, dict) and 'message' in error_obj:
                        return error_obj['message']

                    # Otherwise return the stringified object
                    return str(error_obj)

                except json.JSONDecodeError:
                    # Not JSON, return as-is
                    return error_value

            # If error_value is already a dict
            elif isinstance(error_value, dict):
                if 'message' in error_value:
                    return error_value['message']
                return str(error_value)

        # Try 'message' field (fallback)
        if 'message' in data:
            return data['message']

        # Try 'errorMessage' field (another common pattern)
        if 'errorMessage' in data:
            return data['errorMessage']

        # Last resort: return full data as string
        return 'Unknown error'

    async def await_task_completion(
        self,
        request_id: str,
        override_tenant_id: str = None,
        max_attempts: int = 20,
        sleep_seconds: int = 3
    ):
        """
        Poll /activities/{requestId} until async task completes

        RuckusONE API returns 202 Accepted for many operations with a requestId.
        This method polls the /activities endpoint to check task status until
        completion (SUCCESS or FAIL).

        Args:
            request_id: The requestId returned from a 202 response
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls
            max_attempts: Maximum number of polling attempts (default: 20)
            sleep_seconds: Seconds to wait between polls (default: 3)

        Returns:
            dict: Final task status response from /activities/{requestId}

        Raises:
            TimeoutError: If task doesn't complete within max_attempts
            Exception: If task status is FAIL
        """
        logger.info(f"Waiting for task {request_id} to complete...")

        for attempt in range(1, max_attempts + 1):
            response = self.get(f"/activities/{request_id}", override_tenant_id=override_tenant_id)

            if not response.ok:
                # Activity might not exist yet - this is normal for the first few attempts
                if attempt == 1:
                    logger.debug(f"Waiting for activity {request_id} to be created...")
                elif attempt % 5 == 0:
                    logger.debug(f"Still waiting for {request_id}... (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_seconds)
                continue

            data = response.json()
            status = data.get('status')

            # Log status updates periodically
            if attempt == 1 or attempt % 5 == 0:
                logger.debug(f"Poll {attempt}/{max_attempts}: status={status}")

            # Debug: Log full data on first successful poll
            if attempt == 1:
                logger.debug(f"Activity data for {request_id}: {data}")

            if status == 'SUCCESS':
                logger.info(f"Task {request_id} completed successfully")
                logger.debug(f"Success response for {request_id}: {data}")
                return data

            elif status == 'FAIL':
                # Extract detailed error message from RuckusONE response
                error_msg = self._extract_error_message(data)
                logger.error(f"Task {request_id} failed: {error_msg}")
                logger.debug(f"Full failure response for {request_id}: {data}")
                raise Exception(f"Task {request_id} failed: {error_msg}")

            # Status is still PENDING or IN_PROGRESS
            if attempt < max_attempts:
                await asyncio.sleep(sleep_seconds)

        # Max attempts reached
        raise TimeoutError(
            f"Task {request_id} did not complete after {max_attempts} attempts "
            f"({max_attempts * sleep_seconds} seconds)"
        )

    async def await_task_completion_bulk(
        self,
        request_ids: list[str],
        override_tenant_id: str = None,
        max_attempts: int = 60,
        sleep_seconds: int = 3,
        max_concurrent: int = 100,
        progress_callback=None,
        global_timeout_seconds: int = None
    ):
        """
        Poll multiple async tasks in parallel with throttling

        Args:
            request_ids: List of requestId strings to poll
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls
            max_attempts: Maximum polling attempts per task (default: 60)
            sleep_seconds: Seconds between polls (default: 3)
            max_concurrent: Max concurrent polls (default: 100)
            progress_callback: Optional callback(completed_count, total_count, result)
            global_timeout_seconds: Optional global timeout for all tasks

        Returns:
            dict: {request_id: result_data} for all tasks

        Note:
            - Failed tasks are included in results with their exception
            - Use return_exceptions=True to handle mixed success/failure
            - Progress callback receives updates as tasks complete
        """
        if not request_ids:
            return {}

        logger.info(f"Bulk polling {len(request_ids)} async tasks (max_concurrent={max_concurrent})")

        results = {}
        semaphore = asyncio.Semaphore(max_concurrent)
        completed_count = 0
        total_count = len(request_ids)

        async def poll_single_task(request_id: str):
            """Poll a single task with semaphore throttling"""
            nonlocal completed_count

            async with semaphore:
                try:
                    result = await self.await_task_completion(
                        request_id=request_id,
                        override_tenant_id=override_tenant_id,
                        max_attempts=max_attempts,
                        sleep_seconds=sleep_seconds
                    )

                    # Store successful result
                    results[request_id] = {
                        "success": True,
                        "data": result,
                        "request_id": request_id
                    }

                except Exception as e:
                    # Store failed result
                    results[request_id] = {
                        "success": False,
                        "error": str(e),
                        "request_id": request_id
                    }
                    logger.warning(f"Task {request_id} failed: {str(e)}")

                # Update progress
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, total_count, results[request_id])

                # Log progress periodically
                if completed_count % 10 == 0 or completed_count == total_count:
                    success_count = sum(1 for r in results.values() if r.get("success"))
                    fail_count = completed_count - success_count
                    logger.info(f"Bulk progress: {completed_count}/{total_count} "
                                f"(success={success_count}, failed={fail_count})")

                return results[request_id]

        # Create tasks for all request_ids
        tasks = [poll_single_task(request_id) for request_id in request_ids]

        # Execute with optional global timeout
        try:
            if global_timeout_seconds:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=global_timeout_seconds
                )
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.TimeoutError:
            logger.warning(f"Global timeout of {global_timeout_seconds}s exceeded")
            # Mark incomplete tasks as timed out
            for request_id in request_ids:
                if request_id not in results:
                    results[request_id] = {
                        "success": False,
                        "error": f"Global timeout of {global_timeout_seconds}s exceeded",
                        "request_id": request_id
                    }

        # Final summary
        success_count = sum(1 for r in results.values() if r.get("success"))
        fail_count = len(results) - success_count
        logger.info(f"Bulk polling complete: {success_count} succeeded, {fail_count} failed")

        return results
