import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from utils.client_ip import client_ip

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-backed rate limiting middleware.

    Uses Redis INCR + EXPIRE for atomic, distributed counters that work
    correctly across multiple uvicorn workers.

    Limits:
    - 5 requests per minute for auth endpoints
    - 300 requests per minute for other endpoints
    """

    def __init__(self, app):
        super().__init__(app)
        self._redis = None

    async def _get_redis(self):
        """
        Lazy Redis init — avoids import/connection at middleware construction.

        Uses the dedicated REQUEST pool, not the workflow one. This middleware
        runs on every request, so sharing a pool with the workflow engine meant
        a large import could drain it and leave every page — including the
        session check — blocking for the full pool timeout. The request pool is
        small and impatient, and the except-block below already fails open, so
        the worst case is that rate limiting is briefly skipped.
        """
        if self._redis is None:
            from redis_client import get_request_redis
            self._redis = await get_request_redis()
        return self._redis

    async def dispatch(self, request: Request, call_next):
        # Not request.client.host: behind nginx that is nginx, for everyone,
        # which made every limit here global. See utils/client_ip.py.
        ip = client_ip(request)
        endpoint = request.url.path

        # Skip rate limiting for long-lived / high-frequency endpoints
        if endpoint.endswith("/stream"):
            return await call_next(request)
        if "/jobs/" in endpoint and endpoint.endswith("/status"):
            return await call_next(request)

        is_auth_endpoint = any(
            endpoint.startswith(path) for path in [
                "/api/auth/request-otp",
                "/api/auth/signup-request-otp",
                "/api/auth/login-otp",
                "/api/auth/signup-verify-otp",
            ]
        )

        if is_auth_endpoint:
            max_requests = 5
            window_seconds = 60
        else:
            max_requests = 300
            window_seconds = 60

        # Build a Redis key: ratelimit:{ip}:{endpoint}
        key = f"ratelimit:{ip}:{endpoint}"

        try:
            r = await self._get_redis()
            # INCR is atomic — safe across workers
            current_count = await r.incr(key)

            if current_count == 1:
                # First request in window — set TTL so key auto-expires
                await r.expire(key, window_seconds)

            if current_count > max_requests:
                # Read remaining TTL for the Retry-After header
                ttl = await r.ttl(key)
                if ttl is None or ttl < 0:
                    # INCR landed but the EXPIRE after it did not, so this
                    # key would never expire and the client would stay
                    # locked out for good. Re-arm it.
                    await r.expire(key, window_seconds)
                    ttl = window_seconds
                logger.warning(f"Rate limit exceeded for {ip} on {endpoint} ({current_count}/{max_requests})")
                # RETURNED, not raised. This is middleware, which runs
                # outside the app's HTTPException handler, so a raised 429
                # fell through to the catch-all Exception handler and reached
                # the user as a 500 "Internal server error" -- no mention of
                # a limit, and no Retry-After.
                return JSONResponse(
                    status_code=429,
                    content={"error": f"Rate limit exceeded. Try again in {ttl} seconds."},
                    headers={"Retry-After": str(ttl)},
                )
        except Exception as e:
            # If Redis is down, fail open — don't block requests
            logger.error(f"Rate limiter Redis error (failing open): {e}")

        response = await call_next(request)
        return response
