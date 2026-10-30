import logging
import os
import json
import re
import requests
import threading
import time
import asyncio
from r1api.token_cache import get_cached_token, store_token, invalidate_token
from r1api.services.msp import MspService

logger = logging.getLogger(__name__)

# R1_VERBOSE logs full payloads and responses. Those carry DPSK passphrases
# and RADIUS shared secrets, which must never reach a log; mask them first.
_SECRET_LOG_KEY = re.compile(r"(passphrase|sharedsecret|secret|password)$", re.IGNORECASE)
_SECRET_LOG_TEXT = re.compile(
    r'("(?:[A-Za-z_]*(?:[Pp]assphrase|[Ss]ecret|[Pp]assword))"\s*:\s*)"(?:[^"\\]|\\.)*"'
)


def _mask_for_log(value):
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and _SECRET_LOG_KEY.search(k) and isinstance(v, str)
                else _mask_for_log(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mask_for_log(v) for v in value]
    return value


def _mask_text_for_log(text: str) -> str:
    return _SECRET_LOG_TEXT.sub(r'\1"***"', text)
from r1api.services.venues import VenueService
from r1api.services.networks import NetworksService
from r1api.services.tenant import TenantService
from r1api.services.aps import ApService
from r1api.services.clients import ClientsService
from r1api.services.floorplans import FloorplanService
from r1api.services.entitlements import EntitlementsService
from r1api.services.dpsk import DpskService
from r1api.services.identity import IdentityService
from r1api.services.policy_sets import PolicySetService
from r1api.services.radius_attributes import RadiusAttributeService
from r1api.services.ethernet_port_profiles import EthernetPortProfileService
from r1api.services.radius_profiles import RadiusProfileService
from r1api.services.switches import SwitchService
from contextvars import ContextVar

# An async task R1 still reports as pending is waited on this long. On big
# runs creates/activations/AP adds take minutes and then succeed; the old
# ~97s cap called them failed and callers re-sent work that was landing.
ACTIVITY_TIMEOUT_SECONDS = 300
# GET /activities/{id} answers 404 / "invalid id" for a few seconds after
# the request is accepted (not registered yet). An activity never seen this
# long stops the wait.
ACTIVITY_UNSEEN_SECONDS = 120
# How often a long wait tells activity_wait_listener it's still going.
ACTIVITY_NOTICE_SECONDS = 60

# Set by a caller (a workflow phase) to hear about long waits:
# async listener(request_id, status, elapsed_seconds). Context-scoped, so it
# reaches waits deep inside service calls without threading it through them.
activity_wait_listener: ContextVar = ContextVar("activity_wait_listener", default=None)


class ActivityPending(TimeoutError):
    """R1 accepted the request; its task hadn't finished when the wait ended.
    Not a refusal -- re-sending the request would duplicate work R1 is doing."""

    def __init__(self, request_id: str, status, elapsed: float):
        self.request_id, self.status, self.elapsed = request_id, status, elapsed
        seen = f"R1 still reports it {status}" if status else "R1 never showed the activity"
        super().__init__(f"Task {request_id} did not complete after {elapsed:.0f}s ({seen})")


# Every await_task_completion on a client shares one poller per tenant: one
# POST /activities/query per cycle (the ActivityTracker's time-window match on
# requestId), run in a worker thread. Each wait used to GET /activities/{id}
# on its own, synchronously on the event loop -- a few hundred waits at once
# kept the loop busy enough to starve SSE and every other job in the process.
ACTIVITY_POLL_SECONDS = 3.0
ACTIVITY_QUERY_PAGE_SIZE = 500
ACTIVITY_QUERY_MAX_PAGES = 20
# A task the bulk query hasn't shown for this long is also read on its own,
# in case the query misses it (e.g. a clock skew outside the time window).
ACTIVITY_BULK_MISS_SECONDS = 20
ACTIVITY_SINGLE_READS_PER_CYCLE = 20
_ACTIVITY_DONE = ("SUCCESS", "FAIL")


class _ActivityWatch:
    """Polls every activity one client is waiting on in one tenant, in bulk."""

    def __init__(self, client, tenant_id, loop):
        self.client, self.tenant_id, self.loop = client, tenant_id, loop
        self.waiting: dict = {}      # request_id -> loop time it was first asked about
        self.latest: dict = {}       # request_id -> last activity record read
        self.cycle = asyncio.Event()
        self.task = None
        self.from_time = None        # ISO, the query's fromTime

    async def next_read(self, request_id: str):
        """The activity's record after the next poll cycle (None = not seen yet)."""
        if request_id not in self.waiting:
            self.waiting[request_id] = self.loop.time()
            start = time.gmtime(time.time() - 60)    # buffer for clock skew
            from_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", start)
            self.from_time = min(self.from_time or from_time, from_time)
        if not self.task or self.task.done():
            self.task = self.loop.create_task(self._run())
        cycle = self.cycle
        await cycle.wait()
        return self.latest.get(request_id)

    def release(self, request_id: str):
        self.waiting.pop(request_id, None)
        self.latest.pop(request_id, None)
        if not self.waiting:
            self.from_time = None

    async def _run(self):
        await asyncio.sleep(1.0)     # let a burst of new waits join the first query
        while self.waiting:
            try:
                await self._poll()
            except Exception as e:
                logger.warning(f"Activity poll failed: {e}")
            cycle, self.cycle = self.cycle, asyncio.Event()
            cycle.set()
            await asyncio.sleep(ACTIVITY_POLL_SECONDS)

    async def _poll(self):
        ids = list(self.waiting)
        bulk_ok = True
        try:
            found = await self.loop.run_in_executor(
                None, self.client._query_activities_since, set(ids), self.from_time, self.tenant_id)
        except Exception as e:
            logger.debug(f"POST /activities/query failed ({e}); reading activities one by one")
            found, bulk_ok = {}, False

        # One by one: everything if the query failed, else what it has missed for a while.
        now = self.loop.time()
        missed = [rid for rid in ids if rid not in found and (
            not bulk_ok or now - self.waiting[rid] >= ACTIVITY_BULK_MISS_SECONDS)]
        # Finished ones too, once: the full record carries R1's error text.
        done = [rid for rid in ids if str((found.get(rid) or {}).get("status")).upper() in _ACTIVITY_DONE]
        single = done + missed[:ACTIVITY_SINGLE_READS_PER_CYCLE]
        if single:
            reads = await asyncio.gather(*(
                self.loop.run_in_executor(None, self.client._read_activity, rid, self.tenant_id)
                for rid in single), return_exceptions=True)
            for rid, data in zip(single, reads):
                if isinstance(data, dict):
                    found[rid] = data
        for rid in ids:
            if rid in found and rid in self.waiting:
                self.latest[rid] = found[rid]


class R1Client:
    def __init__(self, tenant_id, client_id, shared_secret, ec_type=None, region=None):
        logger.debug(f"Initializing R1Client for tenant_id={tenant_id}, ec_type={ec_type}, region={region}")
        # self.token = None
        # self.token_expiry = None  # optional if you want expiry management
        self.session = requests.Session()
        # Size the underlying urllib3 pool for the concurrency we actually
        # use. Now that the hot paths run via asyncio.to_thread, several
        # requests are genuinely in flight at once; urllib3's default of 10
        # would discard and reopen connections past that ("Connection pool is
        # full"). Headers are built per request in _request(), and the pool
        # itself is thread-safe, so sharing one Session across threads is fine.
        _adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=32
        )
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)

        # Serialises token refresh. The hot paths now run in threads, so a
        # burst of 401s would otherwise re-authenticate once per thread.
        self._auth_lock = threading.Lock()
        self.auth_failed = False
        self.auth_error = None

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
        self.floorplans = FloorplanService(self)
        self.entitlements = EntitlementsService(self)
        self.dpsk = DpskService(self)
        self.identity = IdentityService(self)
        self.policy_sets = PolicySetService(self)
        self.radius_attributes = RadiusAttributeService(self)
        self.ethernet_port_profiles = EthernetPortProfileService(self)
        self.radius_profiles = RadiusProfileService(self)
        self.switches = SwitchService(self)

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
        # Previously never cleared: a client that failed once stayed flagged
        # as failed even after a successful re-auth.
        self.auth_failed = False
        self.auth_error = None

        logger.debug(f"Authentication successful, token expires in {expires_in}s")

    def _refresh_token(self, stale_token) -> bool:
        """
        Get a working token after R1 rejected the one we hold.

        Returns True if a token different from the rejected one is now in
        place. Callers retry once on True.

        Three ways to land here at once -- many threads on one client, or
        several clients sharing the cached token -- so the lock plus the two
        early exits mean one re-authentication serves all of them.
        """
        with self._auth_lock:
            if self.token and self.token != stale_token:
                return True                      # another thread refreshed
            cached = get_cached_token(self.tenant_id)
            if cached and cached != stale_token:
                self.token = cached              # another client refreshed
                return True
            invalidate_token(self.tenant_id)     # stop handing out the dead one
            self._authenticate()
            return bool(self.token) and self.token != stale_token

    def _request(self, method, path, payload=None, params=None, override_tenant_id=None, accept=None,
                 content_type=None):
        """
        General request wrapper.

        accept: a versioned media type (e.g. "application/vnd.ruckus.v1.1+json").
        Several R1 endpoints only return newer fields under v1.1, and their
        plain application/json mapping was announced to move on 06/30/2026.

        content_type: the same for a request body -- R1 picks the schema a
        write is read with from it, so a v1.1 body needs a v1.1 Content-Type.
        """
        url = f"https://{self.host}{path}"
        verbose = os.environ.get('R1_VERBOSE', '').lower() in ('1', 'true', 'yes')

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": content_type or "application/json",
        }
        if accept:
            headers["Accept"] = accept
        if override_tenant_id:
            headers["x-rks-tenantid"] = override_tenant_id

        if verbose:
            logger.info(f">>> {method.upper()} {path}")
            if payload:
                logger.info(f">>> PAYLOAD: {json.dumps(_mask_for_log(payload), indent=2)[:2000]}")
            if params:
                logger.info(f">>> PARAMS: {params}")

        # R1 sessions expire mid-job (RCG-10003 "Please re-login"). Nothing
        # renewed them: the client took a token at construction and held it,
        # so an import outliving its token turned every call into a 401 --
        # which the activity tracker then read as "the work failed" and
        # failed 17 units for AP assignments R1 had already accepted.
        for attempt in (1, 2):
            token_used = self.token
            headers["Authorization"] = f"Bearer {token_used}"

            response = self.session.request(
                method,
                url,
                headers=headers,
                json=payload,
                params=params,
                verify=True
            )

            if verbose:
                body = _mask_text_for_log(response.text)[:3000] if response.text else '(empty)'
                logger.info(f"<<< {response.status_code} {path}\n{body}")
            else:
                logger.debug(f"{method.upper()} {url} --> {response.status_code}")

            if (
                response.status_code == 401
                and attempt == 1
                and self._refresh_token(token_used)
            ):
                logger.info(
                    f"R1 session expired on {method.upper()} {path}; "
                    f"re-authenticated, retrying once"
                )
                continue

            if not response.ok:
                logger.warning(f"Request error: {response.status_code} - {response.text[:500]}")

            return response

    # Basic HTTP verbs
    def get(self, path, params=None, override_tenant_id=None, accept=None):
        return self._request("get", path, params=params, override_tenant_id=override_tenant_id, accept=accept)

    def post(self, path, payload=None, params=None, override_tenant_id=None):
        return self._request("post", path, payload=payload, params=params, override_tenant_id=override_tenant_id)

    def put(self, path, payload=None, override_tenant_id=None, content_type=None):
        return self._request("put", path, payload=payload, override_tenant_id=override_tenant_id,
                             accept=content_type, content_type=content_type)

    def delete(self, path, payload=None, override_tenant_id=None):
        return self._request("delete", path, payload=payload, override_tenant_id=override_tenant_id)

    def patch(self, path, payload=None, override_tenant_id=None):
        return self._request("patch", path, payload=payload, override_tenant_id=override_tenant_id)

    def safe_json(self, response):
        """
        Safely extract JSON from a response, with proper error handling.

        Args:
            response: requests.Response object

        Returns:
            Parsed JSON data

        Raises:
            Exception with descriptive error message if response is not valid JSON
        """
        if not response.ok:
            try:
                error_data = response.json()
                error_msg = error_data.get('message', error_data.get('error', response.text[:200]))
            except ValueError:
                error_msg = response.text[:200] if response.text else f"HTTP {response.status_code}"
            raise Exception(f"R1 API error ({response.status_code}): {error_msg}")

        try:
            return response.json()
        except ValueError:
            logger.error(f"Failed to parse JSON response: {response.text[:200]}")
            raise Exception(f"Invalid JSON response from R1 API: {response.text[:100]}")

    def post_multipart(self, path, files, override_tenant_id=None):
        """
        POST request with multipart/form-data (for file uploads like CSV import).

        Args:
            path: API path
            files: Dict of files in format {'field_name': ('filename', content, 'content_type')}
            override_tenant_id: Optional tenant ID override for MSP

        Returns:
            Response object
        """
        url = f"https://{self.host}{path}"

        headers = {
            "Authorization": f"Bearer {self.token}",
            # Don't set Content-Type - requests will set it with boundary for multipart
        }
        if override_tenant_id:
            headers["x-rks-tenantid"] = override_tenant_id

        logger.debug(f"R1Client Multipart POST: {path}")

        response = self.session.post(
            url,
            headers=headers,
            files=files,
            verify=True
        )

        logger.debug(f"POST (multipart) {url} --> {response.status_code}")
        if not response.ok:
            logger.warning(f"Multipart request error: {response.status_code} - {response.text[:500]}")

        return response

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

    def _query_activities_since(self, request_ids: set, from_time: str, tenant_id=None) -> dict:
        """
        {requestId: activity} for the given ones, from POST /activities/query
        over [from_time, now + 1 min], newest first, paged until all are found.
        Blocking -- run it in a thread. Raises if R1 refuses the query.
        """
        to_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))
        found, seen = {}, 0
        for page in range(1, ACTIVITY_QUERY_MAX_PAGES + 1):
            response = self.post("/activities/query", payload={
                "page": page, "pageSize": ACTIVITY_QUERY_PAGE_SIZE,
                "sortField": "startDatetime", "sortOrder": "DESC",
                "filters": {"fromTime": from_time, "toTime": to_time},
            }, override_tenant_id=tenant_id)
            if not response.ok:
                raise RuntimeError(f"POST /activities/query: {response.status_code} {response.text[:200]}")
            data = response.json()
            rows = data.get("data") or []
            seen += len(rows)
            for row in rows:
                if row.get("requestId") in request_ids:
                    found[row["requestId"]] = row
            total = data.get("totalCount")
            if len(found) >= len(request_ids) or len(rows) < ACTIVITY_QUERY_PAGE_SIZE \
                    or (isinstance(total, int) and seen >= total):
                break
        return found

    def _read_activity(self, request_id: str, tenant_id=None):
        """GET /activities/{id} -> the record, or None if R1 doesn't show it (yet). Blocking."""
        response = self.get(f"/activities/{request_id}", override_tenant_id=tenant_id)
        return response.json() if response.ok else None

    async def _next_activity_read(self, request_id: str, tenant_id=None):
        """The activity's record after the shared poller's next cycle (None = not seen yet)."""
        loop = asyncio.get_running_loop()
        watches = self.__dict__.setdefault("_activity_watches", {})
        key = (tenant_id, id(loop))
        watch = watches.get(key)
        if watch is None or watch.loop is not loop:
            watch = watches[key] = _ActivityWatch(self, tenant_id, loop)
        return await watch.next_read(request_id)

    def _done_with_activity(self, request_id: str, tenant_id=None):
        watches = self.__dict__.get("_activity_watches") or {}
        for (tenant, _), watch in list(watches.items()):
            if tenant == tenant_id:
                watch.release(request_id)

    async def await_task_completion(
        self,
        request_id: str,
        override_tenant_id: str = None,
        max_attempts: int = None,
        assume_success_on_timeout: bool = False,
        timeout_seconds: float = ACTIVITY_TIMEOUT_SECONDS,
    ):
        """
        Poll /activities/{requestId} until async task completes

        RuckusONE API returns 202 Accepted for many operations with a requestId.
        This method polls the /activities endpoint until R1 says SUCCESS or
        FAIL. Only FAIL is a failure: a task still pending is waited on for
        up to timeout_seconds (5 min), since on big runs tasks take minutes
        and then succeed. An error from /activities (404, "invalid id") right
        after the request is the activity not being registered yet, and is
        waited out -- for at most ACTIVITY_UNSEEN_SECONDS if it never shows.

        Long waits are reported to activity_wait_listener, if one is set.

        Args:
            request_id: The requestId returned from a 202 response
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls
            max_attempts: Cap on polls instead of the time limit (older callers)
            assume_success_on_timeout: If True and activity never appeared (all 404s),
                return a synthetic success response instead of raising.
                Use this when the POST succeeded and you're confident the op went through.
            timeout_seconds: How long to wait on a task R1 reports as pending

        Returns:
            dict: Final task status response from /activities/{requestId}

        Raises:
            ActivityPending: (a TimeoutError) the wait ended before R1 finished
            Exception: If task status is FAIL
        """
        logger.info(f"Waiting for task {request_id} to complete...")

        loop = asyncio.get_running_loop()
        start = loop.time()
        activity_found = False  # Track if we ever got past 404
        status = None
        next_notice = ACTIVITY_NOTICE_SECONDS
        attempt = 0

        try:
            while True:
                attempt += 1
                # Read by the shared poller (one bulk query per cycle for every
                # wait on this client), never a request of our own on the loop.
                data = await self._next_activity_read(request_id, override_tenant_id)
                elapsed = loop.time() - start

                if data:
                    activity_found = True
                    status = data.get('status')
                    if attempt == 1 or attempt % 10 == 0:
                        logger.debug(f"Poll {attempt} ({elapsed:.0f}s): {request_id} status={status}")

                    if status == 'SUCCESS':
                        logger.info(f"Task {request_id} completed successfully ({elapsed:.0f}s)")
                        logger.debug(f"Success response for {request_id}: {data}")
                        return data

                    elif status == 'FAIL':
                        # Extract detailed error message from RuckusONE response
                        error_msg = self._extract_error_message(data)
                        logger.error(f"Task {request_id} failed: {error_msg}")
                        logger.debug(f"Full failure response for {request_id}: {data}")
                        raise Exception(f"Task {request_id} failed: {error_msg}")
                elif attempt % 10 == 0:
                    # Not registered yet (or a hiccup reading it) - keep waiting
                    logger.debug(f"Still waiting for {request_id} to appear ({elapsed:.0f}s)")

                if max_attempts is not None:
                    if attempt >= max_attempts:
                        break
                elif elapsed >= (timeout_seconds if activity_found else min(timeout_seconds, ACTIVITY_UNSEEN_SECONDS)):
                    break

                listener = activity_wait_listener.get()
                if listener and elapsed >= next_notice:
                    next_notice += ACTIVITY_NOTICE_SECONDS
                    try:
                        await listener(request_id, status if activity_found else None, elapsed)
                    except Exception as e:
                        logger.debug(f"activity_wait_listener failed: {e}")
        finally:
            self._done_with_activity(request_id, override_tenant_id)

        elapsed = loop.time() - start
        if not activity_found and assume_success_on_timeout:
            # Activity never appeared but POST succeeded - assume it went through
            # This handles R1's eventual consistency under heavy load
            logger.warning(
                f"Task {request_id} activity never appeared after {elapsed:.0f}s, "
                f"but POST succeeded - assuming success (R1 eventual consistency)"
            )
            return {
                'requestId': request_id,
                'status': 'ASSUMED_SUCCESS',
                'message': 'Activity polling timed out but creation request was accepted'
            }

        logger.warning(f"Stopped waiting on task {request_id} after {elapsed:.0f}s "
                       f"(status={status if activity_found else 'never seen'})")
        raise ActivityPending(request_id, status if activity_found else None, elapsed)

    def query_activities_bulk(
        self,
        activity_ids: list[str],
        override_tenant_id: str = None,
    ) -> dict[str, dict]:
        """
        Query multiple activities in a SINGLE API call using POST /activities/query.

        This is the efficient bulk endpoint - ONE request returns status for ALL activities.
        Use this instead of N individual GET /activities/{id} calls.

        Args:
            activity_ids: List of activity/request IDs to query
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls

        Returns:
            Dict mapping activity_id -> activity data (status, resourceId, etc.)
            Activities not found will not be in the result dict.
        """
        if not activity_ids:
            return {}

        # POST /activities/query with filter on activity IDs
        # NOTE: R1 uses direct array format for filters, not {"in": [...]} syntax
        # The {"in": [...]} format causes "bad SQL grammar" 500 errors
        payload = {
            "filters": {
                "id": activity_ids  # Direct array, not {"in": [...]}
            },
            "pageSize": min(len(activity_ids), 500),  # R1 max page size
            "page": 1,
            "sortField": "startDatetime",
            "sortOrder": "DESC",
        }

        response = self.post(
            "/activities/query",
            payload=payload,
            override_tenant_id=override_tenant_id
        )

        if not response.ok:
            logger.warning(f"POST /activities/query failed: {response.status_code}")
            return {}

        data = response.json()
        activities = data.get('data', [])

        # Build lookup dict by activity ID
        result = {}
        for activity in activities:
            aid = activity.get('id')
            if aid:
                result[aid] = activity

        logger.debug(
            f"Bulk query: requested {len(activity_ids)}, "
            f"returned {len(result)} activities"
        )

        return result

    async def await_task_completion_bulk(
        self,
        request_ids: list[str],
        override_tenant_id: str = None,
        max_attempts: int = None,
        max_concurrent: int = 100,
        progress_callback=None,
        global_timeout_seconds: int = None
    ):
        """
        Poll multiple async tasks in parallel with throttling

        Args:
            request_ids: List of requestId strings to poll
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls
            max_attempts: Maximum polling attempts per task (default: none -- each task gets await_task_completion's time limit)
            max_concurrent: Max concurrent polls (default: 100)
            progress_callback: Optional callback(completed_count, total_count, result)
            global_timeout_seconds: Optional global timeout for all tasks

        Returns:
            dict: {request_id: result_data} for all tasks

        Note:
            - Failed tasks are included in results with their exception
            - Use return_exceptions=True to handle mixed success/failure
            - Progress callback receives updates as tasks complete
            - Uses stepped backoff internally (1s, 2s, 3s)
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
                        max_attempts=max_attempts
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
                        # Still running in R1 when the wait ended: not refused
                        "pending": isinstance(e, TimeoutError),
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
                        "pending": True,
                        "request_id": request_id
                    }

        # Final summary
        success_count = sum(1 for r in results.values() if r.get("success"))
        fail_count = len(results) - success_count
        logger.info(f"Bulk polling complete: {success_count} succeeded, {fail_count} failed")

        return results

    async def await_tasks_bulk_query(
        self,
        request_ids: list[str],
        override_tenant_id: str = None,
        max_poll_seconds: int = 120,
        poll_interval: int = 3,
        assume_success_on_timeout: bool = False,
        progress_callback=None
    ) -> dict:
        """
        Poll multiple activities concurrently using parallel individual GET requests.

        Uses concurrent GET /activities/{id} requests via asyncio thread pool to
        efficiently poll multiple activities in parallel while respecting rate limits.

        Args:
            request_ids: List of activity requestIds to poll
            override_tenant_id: Optional tenant ID for MSP multi-tenant calls
            max_poll_seconds: Maximum total polling time (default: 120s)
            poll_interval: Seconds between polling rounds (default: 3s)
            assume_success_on_timeout: If True, treat activities that never appeared as success
            progress_callback: Optional callback(completed, total, latest_results)

        Returns:
            dict: {request_id: {"status": "SUCCESS"|"FAIL"|"TIMEOUT", "data": {...}}}
        """
        if not request_ids:
            return {}

        logger.info(f"Concurrent polling {len(request_ids)} activities (interval={poll_interval}s, max={max_poll_seconds}s)")

        pending_ids = set(request_ids)
        results = {}
        start_time = asyncio.get_event_loop().time()
        poll_count = 0

        # Helper to fetch a single activity (runs in thread pool since requests is sync)
        def fetch_activity(req_id: str):
            try:
                response = self.get(f"/activities/{req_id}", override_tenant_id=override_tenant_id)
                if response.ok:
                    return req_id, response.json()
                elif response.status_code == 404:
                    # Activity not created yet - normal for first few polls
                    return req_id, None
                else:
                    return req_id, {"error": f"HTTP {response.status_code}"}
            except Exception as e:
                return req_id, {"error": str(e)}

        while pending_ids and (asyncio.get_event_loop().time() - start_time) < max_poll_seconds:
            poll_count += 1

            # Fetch all pending activities concurrently using thread pool
            loop = asyncio.get_event_loop()
            fetch_tasks = [
                loop.run_in_executor(None, fetch_activity, req_id)
                for req_id in list(pending_ids)
            ]
            fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            # Process results
            newly_completed = []
            for result in fetch_results:
                if isinstance(result, Exception):
                    logger.warning(f"Activity fetch error: {result}")
                    continue

                req_id, activity = result
                if activity is None:
                    # Activity not found yet - keep polling
                    continue
                if "error" in activity and "status" not in activity:
                    # Fetch error - keep polling
                    logger.debug(f"Activity {req_id} fetch error: {activity.get('error')}")
                    continue

                status = activity.get("status")
                if status == "SUCCESS":
                    results[req_id] = {"status": "SUCCESS", "data": activity}
                    pending_ids.discard(req_id)
                    newly_completed.append(req_id)
                elif status == "FAIL":
                    error_msg = activity.get("error") or "Unknown error"
                    results[req_id] = {"status": "FAIL", "data": activity, "error": error_msg}
                    pending_ids.discard(req_id)
                    newly_completed.append(req_id)
                # PENDING/INPROGRESS - keep polling

            # Log progress
            completed = len(results)
            total = len(request_ids)
            if newly_completed or poll_count % 5 == 0:
                logger.debug(f"Concurrent poll #{poll_count}: {completed}/{total} complete, {len(pending_ids)} pending")

            # Progress callback (may be async)
            if progress_callback and newly_completed:
                result = progress_callback(completed, total, {r: results[r] for r in newly_completed})
                if asyncio.iscoroutine(result):
                    await result

            # Wait before next poll (unless we're done)
            if pending_ids:
                await asyncio.sleep(poll_interval)

        # Handle remaining pending IDs
        elapsed = asyncio.get_event_loop().time() - start_time
        if pending_ids:
            logger.warning(f"Bulk polling timed out after {elapsed:.0f}s with {len(pending_ids)} activities still pending")

            for req_id in pending_ids:
                if assume_success_on_timeout:
                    results[req_id] = {
                        "status": "ASSUMED_SUCCESS",
                        "data": {"requestId": req_id},
                        "message": "Activity polling timed out but creation was accepted"
                    }
                else:
                    results[req_id] = {
                        "status": "TIMEOUT",
                        "data": {"requestId": req_id},
                        "error": f"Polling timed out after {elapsed:.0f}s"
                    }

        success_count = sum(1 for r in results.values() if r["status"] in ("SUCCESS", "ASSUMED_SUCCESS"))
        fail_count = sum(1 for r in results.values() if r["status"] == "FAIL")
        timeout_count = sum(1 for r in results.values() if r["status"] == "TIMEOUT")

        logger.info(f"Concurrent polling complete in {elapsed:.0f}s ({poll_count} rounds): "
                   f"{success_count} success, {fail_count} failed, {timeout_count} timeout")

        return results
