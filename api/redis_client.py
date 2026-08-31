"""
Redis client configuration for workflow state management.

TWO POOLS, deliberately.

The workflow engine and the request path used to share one pool. The engine
is the heavy, long-running, leak-prone side; the request path is what serves
auth and session checks. When a large import drained the shared pool, the
rate-limit middleware -- which runs on EVERY request -- blocked for the full
pool timeout and the whole app appeared dead, not just the imports.

Splitting them means a drained workflow pool degrades imports and leaves
people able to log in. The request pool is also deliberately small and
fast-failing: the middleware already fails open on error, so a short timeout
turns "every page hangs for 20s" into "rate limiting is skipped for a moment".
"""
import logging
import os
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool, BlockingConnectionPool
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# name -> (env var for size, default size, env var for timeout, default timeout)
POOL_PROFILES = {
    # Brain, state manager, activity tracker, SSE streams. Blocking with a
    # generous timeout: a background job would rather wait than fail.
    "workflow": ("REDIS_MAX_CONNECTIONS", 200, "REDIS_POOL_TIMEOUT", 20.0),
    # Per-request work (rate limiting, and anything else on the hot path).
    # Small and impatient on purpose -- see module docstring.
    "request": ("REDIS_REQUEST_MAX_CONNECTIONS", 25, "REDIS_REQUEST_POOL_TIMEOUT", 2.0),
}


class RedisClient:
    """Named Redis clients, one connection pool each."""

    _instances: Dict[str, redis.Redis] = {}
    _pools: Dict[str, ConnectionPool] = {}

    @classmethod
    async def get_client(cls, pool: str = "workflow") -> redis.Redis:
        """Get or create the client for a named pool."""
        if pool not in POOL_PROFILES:
            raise ValueError(
                f"Unknown Redis pool '{pool}'. Known: {list(POOL_PROFILES)}"
            )
        if pool in cls._instances:
            return cls._instances[pool]

        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv("REDIS_DB", "1"))
        password = os.getenv("REDIS_PASSWORD", None)

        size_var, size_default, timeout_var, timeout_default = POOL_PROFILES[pool]
        max_connections = int(os.getenv(size_var, str(size_default)))
        pool_timeout = float(os.getenv(timeout_var, str(timeout_default)))

        connection_kwargs = {
            "host": host,
            "port": port,
            "db": db,
            "decode_responses": True,
            "socket_connect_timeout": 10,
            "socket_timeout": 10,
            # Drop half-dead sockets proactively so they don't linger as
            # checked-out-but-unusable connections during traffic spikes.
            # Note this does NOT recover a leaked connection: a connection that
            # is checked out and never returned is never health-checked.
            "socket_keepalive": True,
            "health_check_interval": 30,
            # Retry transient timeouts / dropped connections at the command
            # level so a brief Redis hiccup does not fail a long-running job.
            "retry": Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=3),
            "retry_on_error": [redis.TimeoutError, redis.ConnectionError],
        }
        if password:
            connection_kwargs["password"] = password

        # Blocking: callers wait for a free connection instead of erroring the
        # instant the pool is momentarily full. The timeout is what bounds it.
        cls._pools[pool] = BlockingConnectionPool(
            max_connections=max_connections,
            timeout=pool_timeout,
            **connection_kwargs,
        )
        client = redis.Redis(connection_pool=cls._pools[pool])

        try:
            await client.ping()
            logger.info(
                f"Redis connected [{pool}]: {host}:{port} (DB {db}, "
                f"max_connections={max_connections}, "
                f"pool_timeout={pool_timeout}s, blocking)"
            )
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed [{pool}]: {e}")
            cls._pools.pop(pool, None)
            raise

        cls._instances[pool] = client
        return client

    @classmethod
    def pool_stats(cls) -> Dict[str, Dict[str, int]]:
        """
        Live utilisation per pool, for diagnostics and health checks.

        in_use climbing and never falling while nothing is running is the
        signature of leaked connections, which is otherwise invisible until
        the pool is empty and the process has to be restarted.
        """
        stats: Dict[str, Dict[str, int]] = {}
        for name, p in cls._pools.items():
            in_use = len(getattr(p, "_in_use_connections", ()) or ())
            available = len(getattr(p, "_available_connections", ()) or ())
            maximum = getattr(p, "max_connections", 0) or 0
            stats[name] = {
                "in_use": in_use,
                "available": available,
                "max": maximum,
                "percent": round(in_use / maximum * 100, 1) if maximum else 0.0,
            }
        return stats

    @classmethod
    async def close(cls):
        """Close every client and pool."""
        for name, client in list(cls._instances.items()):
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"Error closing Redis client [{name}]: {e}")
        cls._instances.clear()
        for name, p in list(cls._pools.items()):
            try:
                await p.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting Redis pool [{name}]: {e}")
        cls._pools.clear()
        logger.info("Redis connections closed")


# Convenience functions for FastAPI dependency injection
async def get_redis() -> redis.Redis:
    """FastAPI dependency for async Redis client (workflow pool)."""
    return await RedisClient.get_client()


async def get_redis_client() -> redis.Redis:
    """Async Redis client for the workflow engine."""
    return await RedisClient.get_client("workflow")


async def get_request_redis() -> redis.Redis:
    """
    Small, fast-failing client for per-request work.

    Use this anywhere that runs on every request. It must not be able to
    hang behind a workflow that has drained the main pool.
    """
    return await RedisClient.get_client("request")


def redis_pool_stats() -> Dict[str, Dict[str, int]]:
    """Live per-pool utilisation. See RedisClient.pool_stats."""
    return RedisClient.pool_stats()
