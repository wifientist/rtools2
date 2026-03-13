import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

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
        """Lazy Redis init — avoids import/connection at middleware construction time."""
        if self._redis is None:
            from redis_client import get_redis_client
            self._redis = await get_redis_client()
        return self._redis

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
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
        key = f"ratelimit:{client_ip}:{endpoint}"

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
                logger.warning(f"Rate limit exceeded for {client_ip} on {endpoint} ({current_count}/{max_requests})")
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Try again in {ttl} seconds.",
                    headers={"Retry-After": str(ttl)},
                )
        except HTTPException:
            raise
        except Exception as e:
            # If Redis is down, fail open — don't block requests
            logger.error(f"Rate limiter Redis error (failing open): {e}")

        response = await call_next(request)
        return response
