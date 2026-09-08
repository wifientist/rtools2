#!/usr/bin/env python3
"""
Redis connection-pool leak regression test.

Guards against the redis-py bug that exhausted the pool in production and
required a process restart to clear.

THE BUG (redis-py <= 5.0.7)

    BlockingConnectionPool.get_connection() held self._condition across the
    call that actually opens the socket:

        async with self._condition:
            await self._condition.wait_for(self.can_get_connection)
            return await super().get_connection(...)     # connects in here

    and the base class's failure path is:

        try:
            await self.ensure_connection(connection)
        except BaseException:
            await self.release(connection)               # "async with self._condition"
            raise

    asyncio.Lock is not reentrant, so ANY failure while connecting -- a
    cancelled task, a dropped socket, even a wrong password -- deadlocked
    against the lock the same task already held. The pool timeout then fired
    and raised the misleading ConnectionError("No connection available."),
    and the connection was never returned to the pool.

    Every occurrence permanently cost one connection, with no recovery short
    of a restart, and the real error was hidden behind an exhaustion message.

Cancellation is routine here, not exotic: SSE streams and status polls are
cancelled whenever a client navigates or reconnects, and every cancelled
request was rolling the dice on this window.

Fixed upstream in 5.0.8 by connecting outside the lock. api/redis_client.py
refuses to import below that version; this script proves the behaviour.

Scenarios cover the shapes the app actually produces: plain commands,
pipelines, scans (worst offender -- many commands means many chances to be
cancelled in the bad window), and more concurrent callers than the pool holds.

Usage:
    docker compose exec backend python scripts/test_redis_pool_leak.py

Exits non-zero if any scenario leaks, so it can gate a build.
"""

import asyncio
import os
import random
import sys

import redis
import redis.asyncio as aioredis
from redis.asyncio.connection import BlockingConnectionPool

POOL_SIZE = 25
CANARY_KEY = "pool_leak_test:canary"
SCAN_PREFIX = "pool_leak_test:k:"


def make_pool(max_connections: int = POOL_SIZE) -> BlockingConnectionPool:
    """A pool shaped like the app's, but small enough to exhaust quickly."""
    kwargs = dict(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "1")),
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
    )
    password = os.getenv("REDIS_PASSWORD")
    if password:
        kwargs["password"] = password
    return BlockingConnectionPool(max_connections=max_connections, timeout=2, **kwargs)


async def cancel_mid_flight(make_task, rounds: int, per_round: int):
    """Start a batch of operations, cancel them while they are still connecting."""
    for _ in range(rounds):
        tasks = [asyncio.create_task(make_task()) for _ in range(per_round)]
        # Somewhere between "not yet started" and "already sent" -- the whole
        # point is to land inside get_connection() sometimes.
        await asyncio.sleep(random.uniform(0, 0.003))
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except BaseException:
                pass


# --- scenarios ---------------------------------------------------------------

async def plain_commands(r, _pool):
    await cancel_mid_flight(lambda: r.get(CANARY_KEY), rounds=30, per_round=15)


async def pipelines(r, _pool):
    async def one():
        async with r.pipeline() as pipe:
            pipe.get(CANARY_KEY)
            pipe.set(CANARY_KEY, "canary")
            pipe.get(CANARY_KEY)
            return await pipe.execute()

    await cancel_mid_flight(one, rounds=30, per_round=15)


async def scans(r, _pool):
    for i in range(300):
        await r.set(f"{SCAN_PREFIX}{i}", i)

    async def one():
        return [k async for k in r.scan_iter(match=f"{SCAN_PREFIX}*", count=10)]

    try:
        await cancel_mid_flight(one, rounds=20, per_round=10)
    finally:
        try:
            keys = [k async for k in r.scan_iter(match=f"{SCAN_PREFIX}*", count=500)]
            if keys:
                await r.delete(*keys)
        except Exception:
            pass  # pool may be drained -- that is the result, not an error


async def oversubscribed(r, _pool):
    """More concurrent callers than connections, so some are queued when cancelled."""
    await cancel_mid_flight(lambda: r.get(CANARY_KEY), rounds=15, per_round=60)


SCENARIOS = [
    ("plain commands, 450 cancels", plain_commands),
    ("pipelines, 450 cancels", pipelines),
    ("scans, 200 cancels", scans),
    ("oversubscribed pool, 900 cancels", oversubscribed),
]


async def run_scenario(name, body) -> bool:
    """True if the scenario leaked."""
    pool = make_pool()
    r = aioredis.Redis(connection_pool=pool)
    try:
        await r.set(CANARY_KEY, "canary")
        await body(r, pool)
        await asyncio.sleep(0.1)  # let any in-flight releases land

        leaked = len(pool._in_use_connections)

        # A leak is only half the damage; the other half is whether the pool
        # can still serve, and whether a connection released mid-read hands
        # the NEXT caller somebody else's reply.
        served, wrong, failure = 0, 0, None
        for _ in range(POOL_SIZE + 5):
            try:
                if await r.get(CANARY_KEY) != "canary":
                    wrong += 1
                served += 1
            except Exception as e:
                failure = f"{type(e).__name__}: {e}"
                break

        bad = bool(leaked or wrong or failure)
        print(
            f"  {'FAIL' if bad else 'ok  '}  {name:34s} "
            f"leaked={leaked:3d}/{POOL_SIZE}  served={served:3d}  wrong={wrong}"
            + (f"  {failure}" if failure else "")
        )
        return bad
    finally:
        # An exhausted pool cannot even run the cleanup -- which is the failure
        # being tested for, so it must not replace the report with a traceback.
        try:
            await r.delete(CANARY_KEY)
        except Exception:
            pass
        try:
            await (r.aclose() if hasattr(r, "aclose") else r.close())
        except Exception:
            pass


async def main() -> int:
    print(f"redis-py {redis.__version__}, pool of {POOL_SIZE}\n")
    failures = 0
    for name, body in SCENARIOS:
        failures += await run_scenario(name, body)

    if failures:
        print(
            f"\n{failures} scenario(s) leaked. If redis-py is below 5.0.8 that "
            "is the known BlockingConnectionPool deadlock -- see the module "
            "docstring; otherwise it is new."
        )
    else:
        print("\nAll scenarios returned every connection.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
