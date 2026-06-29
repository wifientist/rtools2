"""
Redis client configuration for workflow state management
"""
import logging
import os
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool, BlockingConnectionPool
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from typing import Optional

logger = logging.getLogger(__name__)

class RedisClient:
    """Singleton Redis client for workflow state storage"""

    _instance: Optional[redis.Redis] = None
    _pool: Optional[ConnectionPool] = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        """Get or create Redis client instance"""
        if cls._instance is None:
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "1"))
            password = os.getenv("REDIS_PASSWORD", None)
            max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "200"))
            # How long a caller will WAIT for a free connection before failing,
            # instead of erroring instantly when the pool is momentarily full.
            pool_timeout = float(os.getenv("REDIS_POOL_TIMEOUT", "20"))

            # Connection settings for parallel workloads.
            # With concurrency limits in Brain (20) and ActivityTracker (25),
            # 200 connections provides headroom for:
            # - 20 concurrent phase tasks × ~5 Redis ops each
            # - 25 concurrent activity polls
            # - SSE streams + progress tracking + pub/sub
            connection_kwargs = {
                "host": host,
                "port": port,
                "db": db,
                "decode_responses": True,
                "socket_connect_timeout": 10,
                "socket_timeout": 10,
                # Drop half-dead sockets proactively so they don't linger as
                # checked-out-but-unusable connections during traffic spikes.
                "socket_keepalive": True,
                "health_check_interval": 30,
                # Retry transient timeouts / dropped connections at the command
                # level (3 attempts, exponential backoff) so a brief Redis
                # hiccup does not bubble up and fail an entire long-running job.
                "retry": Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=3),
                "retry_on_error": [redis.TimeoutError, redis.ConnectionError],
            }

            if password:
                connection_kwargs["password"] = password

            # Use a BLOCKING pool: when all connections are checked out (e.g. a
            # large import where Brain + ActivityTracker + SSE streams + the
            # plan poll all contend at once), callers wait up to pool_timeout
            # for one to free up instead of immediately raising
            # "Too many connections" and 500ing. This is the key resilience
            # change — a plain ConnectionPool turns a momentary spike over the
            # cap into hard failures for whichever requests lose the race.
            cls._pool = BlockingConnectionPool(
                max_connections=max_connections,
                timeout=pool_timeout,
                **connection_kwargs,
            )
            cls._instance = redis.Redis(connection_pool=cls._pool)

            # Test connection
            try:
                await cls._instance.ping()
                logger.info(
                    f"Redis connected: {host}:{port} (DB {db}, "
                    f"max_connections={max_connections}, pool_timeout={pool_timeout}s, blocking)"
                )
            except redis.ConnectionError as e:
                logger.error(f"Redis connection failed: {e}")
                cls._instance = None
                cls._pool = None
                raise

        return cls._instance

    @classmethod
    async def close(cls):
        """Close Redis connection and pool"""
        if cls._instance:
            await cls._instance.close()
            cls._instance = None
        if cls._pool:
            await cls._pool.disconnect()
            cls._pool = None
        logger.info("Redis connection closed")

# Convenience functions for FastAPI dependency injection
async def get_redis() -> redis.Redis:
    """FastAPI dependency for async Redis client"""
    return await RedisClient.get_client()

async def get_redis_client() -> redis.Redis:
    """Async Redis client for workflow engine (alias for get_redis)"""
    return await RedisClient.get_client()
