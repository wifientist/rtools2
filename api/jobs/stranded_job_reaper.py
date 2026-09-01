"""
Periodically mark workflow jobs that are RUNNING with nobody running them.

The Brain writes a short-TTL heartbeat while a job executes. Nothing survives
a process restart, so a RUNNING job whose heartbeat has expired is provably
orphaned -- its background task is gone and no other process will finish it.
Left alone it reports RUNNING forever and the UI spins on it.

This exists because the startup-only sweep was not enough. A restart is
usually FASTER than the heartbeat TTL, so at boot the dead process's
heartbeat still looks alive, the job is skipped as healthy, and it is then
never re-examined. Running on an interval closes that window: whenever the
heartbeat finally lapses, the next pass reaps it.
"""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

JOB_ID = "system_stranded_workflow_reaper"


async def run_reap() -> Dict[str, Any]:
    """Mark every heartbeat-less RUNNING job as FAILED."""
    from redis_client import get_redis_client
    from workflow.v2.state_manager import RedisStateManagerV2

    state = RedisStateManagerV2(await get_redis_client())
    reaped = await state.reap_stranded_jobs()
    if reaped:
        logger.warning(
            f"Reaped {len(reaped)} stranded workflow job(s): "
            f"{[j[:8] for j in reaped]}"
        )
    return {"reaped": len(reaped), "job_ids": reaped}


async def ensure_registered(scheduler) -> None:
    """Register the reaper on a short interval if not already present."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        logger.info(f"Stranded-job reaper '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Stranded Workflow Job Reaper",
        callable_path="jobs.stranded_job_reaper:run_reap",
        trigger_type="interval",
        # Comfortably longer than the 120s heartbeat TTL, so a live-but-slow
        # job is never mistaken for a dead one, and short enough that a
        # stranded job clears on its own rather than needing a human.
        trigger_config={"minutes": 5},
        owner_type="system",
        description=(
            "Marks workflow jobs stuck in RUNNING with no live heartbeat as "
            "failed (every 5 minutes)"
        ),
    )
    logger.info(f"Registered stranded-job reaper '{JOB_ID}' (every 5 minutes)")
