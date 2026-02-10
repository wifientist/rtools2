"""
Redis client configuration for workflow state management
"""
import logging
import os
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
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

            # Connection pool settings for parallel workloads
            # Default max_connections=10 is too small for parallel workflows
            # With concurrency limits in Brain (20) and ActivityTracker (25),
            # 200 connections provides sufficient headroom for:
            # - 20 concurrent phase tasks × ~5 Redis ops each
            # - 25 concurrent activity polls
            # - SSE streams + progress tracking + pub/sub
            pool_kwargs = {
                "host": host,
                "port": port,
                "db": db,
                "decode_responses": True,
                "socket_connect_timeout": 10,
                "socket_timeout": 10,
                "max_connections": 200,  # Sized for bounded concurrency + headroom
            }

            if password:
                pool_kwargs["password"] = password

            cls._pool = ConnectionPool(**pool_kwargs)
            cls._instance = redis.Redis(connection_pool=cls._pool)

            # Test connection
            try:
                await cls._instance.ping()
                logger.info(f"Redis connected: {host}:{port} (DB {db}, max_connections=200)")
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
