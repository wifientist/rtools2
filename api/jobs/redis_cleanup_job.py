"""
Scheduled job: Redis index cleanup.

Prunes stale entries from workflow index sets (jobs:index, jobs:active,
jobs:by_venue, and legacy v1 index) where the underlying job data has
already expired via TTL.

Runs on the 1st of each month at 04:00 UTC.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

JOB_ID = "redis_index_cleanup"


async def run_cleanup() -> Dict[str, Any]:
    """
    Prune stale workflow index entries from Redis.

    Called monthly by the scheduler service.
    """
    from redis_client import get_redis_client
    from workflow.v2.state_manager import RedisStateManagerV2

    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        stats = await state_manager.cleanup_expired_jobs()

        total = sum(stats.values())
        logger.info(f"Redis cleanup removed {total} stale entries: {stats}")

        return {
            "status": "success",
            "total_pruned": total,
            **stats,
        }
    except Exception as e:
        logger.error(f"Redis cleanup failed: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


async def ensure_registered(scheduler) -> None:
    """Register the monthly cleanup job if it doesn't already exist."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        logger.info(f"Redis cleanup job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Redis Index Cleanup",
        callable_path="jobs.redis_cleanup_job:run_cleanup",
        trigger_type="cron",
        trigger_config={"day": 1, "hour": 4, "minute": 0},
        owner_type="system",
        description="Monthly cleanup of stale workflow index entries in Redis (1st of month, 04:00 UTC)",
    )
    logger.info(f"Registered Redis cleanup job '{JOB_ID}' (monthly on 1st at 04:00 UTC)")
