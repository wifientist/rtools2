import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware to prevent brute force attacks on auth endpoints.

    Default limits:
    - 5 requests per minute for auth endpoints
    - 100 requests per minute for other endpoints
    """

    def __init__(self, app):
        super().__init__(app)
        # Store request counts: {ip: {endpoint: [(timestamp, count)]}}
        self.request_counts = defaultdict(lambda: defaultdict(list))
        self.cleanup_task = None

    async def dispatch(self, request: Request, call_next):
        # Get client IP
        client_ip = request.client.host

        # Define rate limits by endpoint pattern
        endpoint = request.url.path
        is_auth_endpoint = any(
            endpoint.startswith(path) for path in [
                "/api/auth/request-otp",
                "/api/auth/signup-request-otp",
                "/api/auth/login-otp",
                "/api/auth/signup-verify-otp",
            ]
        )

        # Skip rate limiting for SSE streams (long-lived connections)
        is_sse_stream = endpoint.endswith("/stream")
        if is_sse_stream:
            return await call_next(request)

        # Skip rate limiting for job status polling (high-frequency monitoring)
        is_job_status = "/jobs/" in endpoint and endpoint.endswith("/status")
        if is_job_status:
            return await call_next(request)

        # Set limits based on endpoint type
        if is_auth_endpoint:
            max_requests = 5  # 5 requests per minute for auth
            window_seconds = 60
        else:
            max_requests = 300  # 300 requests per minute for everything else
            window_seconds = 60

        # Check rate limit
        now = datetime.utcnow()
        cutoff_time = now - timedelta(seconds=window_seconds)

        # Clean old entries for this IP and endpoint
        self.request_counts[client_ip][endpoint] = [
            ts for ts in self.request_counts[client_ip][endpoint]
            if ts > cutoff_time
        ]

        # Count requests in current window
        current_count = len(self.request_counts[client_ip][endpoint])

        if current_count >= max_requests:
            logger.warning(f"Rate limit exceeded for {client_ip} on {endpoint} ({current_count}/{max_requests})")
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds} seconds."
            )

        # Record this request
        self.request_counts[client_ip][endpoint].append(now)

        # Process request
        response = await call_next(request)
        return response

    async def cleanup_old_entries(self):
        """
        Periodic cleanup task to remove old rate limit entries.
        Runs every 5 minutes.
        """
        while True:
            await asyncio.sleep(300)  # 5 minutes
            now = datetime.utcnow()
            cutoff_time = now - timedelta(minutes=10)

            # Clean up entries older than 10 minutes
            for ip in list(self.request_counts.keys()):
                for endpoint in list(self.request_counts[ip].keys()):
                    self.request_counts[ip][endpoint] = [
                        ts for ts in self.request_counts[ip][endpoint]
                        if ts > cutoff_time
                    ]
                    # Remove empty endpoint entries
                    if not self.request_counts[ip][endpoint]:
                        del self.request_counts[ip][endpoint]

                # Remove empty IP entries
                if not self.request_counts[ip]:
                    del self.request_counts[ip]
