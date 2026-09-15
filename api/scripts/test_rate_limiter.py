#!/usr/bin/env python3
"""
Rate limiter regression test.

Two bugs, found 2026-09-15 while chasing a "Plan creation failed" report.

1. A tripped limit reached the user as a 500 "Internal server error".
   The limiter is middleware, and it RAISED its 429. Middleware runs outside
   the app's HTTPException handler, so the exception fell through to the
   catch-all Exception handler: no mention of a limit, no Retry-After, and a
   traceback logged as an unexpected error.

2. Every user shared one bucket per endpoint.
   It keyed on request.client.host, which behind nginx is nginx -- for
   everyone. The 5-per-minute login OTP limit was 5 per minute for the whole
   user base: six OTP requests anywhere locked everybody out of login.

WHAT THIS GUARDS

  1. Over the limit -> 429 with an {"error"} body and a Retry-After header.
  2. Two users behind nginx get separate buckets (nginx's X-Real-IP).
  3. A client connecting to the backend directly cannot spoof X-Real-IP to
     escape its bucket -- port 4174 is published, so this is reachable.
  4. Junk in X-Real-IP from nginx falls back to the peer, not a new bucket.
  5. A key left without a TTL is re-armed instead of locking out forever.

Runs a tiny app with the real middleware against the real request-pool
Redis, on probe paths that are cleaned up afterwards. Uses the auth OTP path
prefix only for its 5/minute limit -- nothing is routed, no email is sent.

Usage:
    docker compose exec backend python scripts/test_rate_limiter.py
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# The trusted proxy for this test: an address no real peer will have.
NGINX = "10.99.99.1"
os.environ["TRUSTED_PROXIES"] = NGINX

import httpx
from fastapi import FastAPI

from middleware.rate_limiter import RateLimitMiddleware
from redis_client import get_request_redis

app = FastAPI()
app.add_middleware(RateLimitMiddleware)


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def anything(path: str):
    return {"ok": True}


def client_from(peer: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 50000)),
        base_url="http://test",
    )


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    run = uuid.uuid4().hex[:8]
    # Auth prefix => the 5/minute bucket. Unique suffix => our own keys.
    path = f"/api/auth/request-otp/_probe_{run}"
    r = await get_request_redis()
    print("Rate limiter\n")

    try:
        # 1. over the limit -> a real 429
        async with client_from(NGINX) as c:
            codes = [(await c.post(path, headers={"X-Real-IP": "203.0.113.10"})).status_code
                     for _ in range(5)]
            over = await c.post(path, headers={"X-Real-IP": "203.0.113.10"})
        body = over.json() if over.headers.get("content-type", "").startswith("application/json") else {}
        failures += check(
            "6th request in a minute -> 429 with an error body and Retry-After",
            codes == [200] * 5 and over.status_code == 429
            and "Rate limit exceeded" in str(body.get("error", ""))
            and over.headers.get("retry-after", "").isdigit(),
            f"codes={codes}, over={over.status_code}, body={body}, "
            f"retry-after={over.headers.get('retry-after')}",
        )

        # 2. a second user behind the same nginx is unaffected
        async with client_from(NGINX) as c:
            other = await c.post(path, headers={"X-Real-IP": "203.0.113.20"})
        failures += check(
            "a different user behind nginx has their own bucket",
            other.status_code == 200, f"got {other.status_code}",
        )

        # 3. a direct (untrusted) client cannot pick its bucket
        direct_path = f"/api/auth/request-otp/_direct_{run}"
        async with client_from("198.51.100.7") as c:
            codes = [(await c.post(direct_path,
                                   headers={"X-Real-IP": f"192.0.2.{i}"})).status_code
                     for i in range(6)]
        failures += check(
            "a direct client rotating X-Real-IP is still limited as itself",
            codes[:5] == [200] * 5 and codes[5] == 429, f"codes={codes}",
        )

        # 4. junk X-Real-IP from nginx -> the peer's bucket, not a new one
        junk_path = f"/api/_junk_{run}"
        async with client_from(NGINX) as c:
            await c.get(junk_path, headers={"X-Real-IP": "not-an-ip"})
        junk_keys = [k async for k in r.scan_iter(match=f"ratelimit:*:{junk_path}", count=500)]
        failures += check(
            "junk X-Real-IP falls back to the peer address",
            junk_keys == [f"ratelimit:{NGINX}:{junk_path}"], f"keys={junk_keys}",
        )

        # 5. a key with no TTL is re-armed, not a permanent lockout
        stuck_path = f"/api/auth/request-otp/_stuck_{run}"
        stuck_key = f"ratelimit:203.0.113.30:{stuck_path}"
        await r.set(stuck_key, 99)  # over the limit, and no expiry
        async with client_from(NGINX) as c:
            resp = await c.post(stuck_path, headers={"X-Real-IP": "203.0.113.30"})
        ttl = await r.ttl(stuck_key)
        failures += check(
            "a key stranded without a TTL gets one back",
            resp.status_code == 429 and 0 < ttl <= 60, f"status={resp.status_code}, ttl={ttl}",
        )
    finally:
        keys = [k async for k in r.scan_iter(match=f"ratelimit:*_{run}", count=500)]
        if keys:
            await r.delete(*keys)
        # Close the pool inside the loop, or its connections are torn down
        # after it and print a harmless but alarming traceback in deploy.sh.
        from redis_client import RedisClient
        await RedisClient.close()

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
