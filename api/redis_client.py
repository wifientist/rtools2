"""
Redis client configuration for workflow state management
"""
import logging
import os
import redis.asyncio as redis
from typing import Optional

logger = logging.getLogger(__name__)

class RedisClient:
    """Singleton Redis client for workflow state storage"""

    _instance: Optional[redis.Redis] = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        """Get or create Redis client instance"""
        if cls._instance is None:
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "1"))
            password = os.getenv("REDIS_PASSWORD", None)

            # Only pass password if it's set (not empty string)
            redis_kwargs = {
                "host": host,
                "port": port,
                "db": db,
                "decode_responses": True,  # Automatically decode bytes to strings
                "socket_connect_timeout": 5,
                "socket_timeout": 5,
            }

            if password:
                redis_kwargs["password"] = password

            cls._instance = redis.Redis(**redis_kwargs)

            # Test connection
            try:
                await cls._instance.ping()
                logger.info(f"Redis connected: {host}:{port} (DB {db})")
            except redis.ConnectionError as e:
                logger.error(f"Redis connection failed: {e}")
                cls._instance = None
                raise

        return cls._instance

    @classmethod
    async def close(cls):
        """Close Redis connection"""
        if cls._instance:
            await cls._instance.close()
            cls._instance = None
            logger.info("Redis connection closed")

# Convenience functions for FastAPI dependency injection
async def get_redis() -> redis.Redis:
    """FastAPI dependency for async Redis client"""
    return await RedisClient.get_client()

async def get_redis_client() -> redis.Redis:
    """Async Redis client for workflow engine (alias for get_redis)"""
    return await RedisClient.get_client()
