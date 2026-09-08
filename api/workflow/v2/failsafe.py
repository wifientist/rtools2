"""
Recording a crash on a job that can no longer report on itself.

WHY THIS EXISTS

WorkflowBrain.execute_workflow() writes its own terminal status: a normal
exception inside the scheduling loop becomes FAILED, a cancel becomes
CANCELLED. But that write is itself Redis work, and so is the setup that runs
before the loop. When Redis is the thing that broke, the engine cannot record
its own death -- the FAILED write fails too, the exception escapes to the
background task that launched it, and every launcher's handler was:

    except Exception as e:
        logger.exception(f"[...] Execution failed for job {job_id}: {e}")

which logs and returns. Nothing ever wrote a terminal status.

The job then sat in RUNNING with no heartbeat until the stranded-job reaper
noticed. The heartbeat TTL is 120s and the reaper sweeps every 300s, so the
observable behaviour was: work stops after a phase or two, the UI keeps
spinning for up to five minutes, and the job finally fails with a generic
"stranded" message while the real exception sits in the logs where nobody is
looking. That is exactly the shape of the 2026-09-08 production report.

fail_job() closes that window: the job goes terminal immediately, carrying
the actual error, and the UI is told. The reaper stays as the backstop for
the case this cannot cover -- a process that dies without unwinding at all.

It is best-effort by construction. It is called from an except block, on the
path where Redis may already be unusable, so it must never raise and never
mask the original exception.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from workflow.v2.models import JobStatus

logger = logging.getLogger(__name__)

# A job that already reached one of these has been reported on. Anything
# raised afterwards -- an activity tracker failing to stop, a DB session
# blowing up on close -- must not rewrite a COMPLETED job as FAILED.
TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.PARTIAL,
    JobStatus.CANCELLED,
}


async def fail_job(
    state_manager: Any,
    job_id: str,
    error: BaseException,
    *,
    source: str,
    status: JobStatus = JobStatus.FAILED,
    event_publisher: Optional[Any] = None,
) -> bool:
    """
    Mark a job terminal after its engine crashed without doing so.

    Re-reads the job rather than trusting an in-memory copy, which may be
    stale or may be the very object whose save failed.

    Args:
        state_manager: RedisStateManagerV2, or None to build one
        job_id: job to mark
        error: the exception that ended the run; its text reaches the UI
        source: short tag for logs, e.g. "Cloudpath V2"
        status: FAILED, or CANCELLED for a cancellation path
        event_publisher: optional, to notify listening clients immediately

    Returns:
        True if a terminal status was written.
    """
    try:
        if state_manager is None:
            # The caller may have crashed before it built one -- or while
            # building it. Imported here to keep this module importable from
            # anywhere without dragging in the Redis client at module load.
            from redis_client import get_redis_client
            from workflow.v2.state_manager import RedisStateManagerV2
            state_manager = RedisStateManagerV2(await get_redis_client())

        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(
                f"[{source}] Job {job_id} crashed and could not be marked "
                f"{status.value}: not found in Redis"
            )
            return False

        if job.status in TERMINAL_STATUSES:
            logger.info(
                f"[{source}] Job {job_id} already {job.status.value}; "
                f"leaving it alone (crash after completion: {error})"
            )
            return False

        job.status = status
        job.errors.append(f"{type(error).__name__}: {error}")
        job.completed_at = datetime.utcnow()
        await state_manager.save_job(job)

        logger.warning(
            f"[{source}] Job {job_id} marked {status.value} by the launcher "
            f"after the engine crashed: {error}"
        )

        if event_publisher is not None:
            try:
                if status == JobStatus.CANCELLED:
                    await event_publisher.job_cancelled(job)
                else:
                    await event_publisher.job_failed(job)
            except Exception as pub_err:
                logger.debug(
                    f"[{source}] Could not publish terminal event for "
                    f"{job_id}: {pub_err}"
                )
        return True

    except Exception as write_err:
        # Redis is very likely the reason we are here at all. The reaper is
        # the backstop; say so plainly so the delay is not a mystery.
        logger.error(
            f"[{source}] Job {job_id} crashed ({error}) AND could not be "
            f"marked {status.value} ({write_err}). It will stay RUNNING until "
            f"the stranded-job reaper clears it."
        )
        return False
