"""
Scheduled job: Signup attempt cleanup.

Deletes rejected signup attempt records older than 6 months.
Runs on the 1st of each month at 04:30 UTC.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any

logger = logging.getLogger(__name__)

JOB_ID = "signup_attempt_cleanup"


async def run_cleanup() -> Dict[str, Any]:
    """Delete signup attempts older than 6 months."""
    from database import SessionLocal
    from models.signup_attempt import SignupAttempt

    try:
        db = SessionLocal()
        cutoff = datetime.utcnow() - timedelta(days=180)
        deleted = db.query(SignupAttempt).filter(SignupAttempt.created_at < cutoff).delete()
        db.commit()
        db.close()

        logger.info(f"Signup attempt cleanup removed {deleted} old records")
        return {"status": "success", "deleted": deleted}
    except Exception as e:
        logger.error(f"Signup attempt cleanup failed: {e}")
        return {"status": "error", "error": str(e)}


async def ensure_registered(scheduler) -> None:
    """Register the monthly cleanup job if it doesn't already exist."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Signup Attempt Cleanup",
        callable_path="jobs.signup_attempt_cleanup_job:run_cleanup",
        trigger_type="cron",
        trigger_config={"day": 1, "hour": 4, "minute": 30},
        owner_type="system",
        description="Monthly cleanup of rejected signup attempts older than 6 months (1st of month, 04:30 UTC)",
    )
    logger.info(f"Registered signup attempt cleanup job '{JOB_ID}'")
