"""
Scheduled job: Daily migration dashboard snapshot.

Polls all MSP controllers that have dashboard settings configured
and captures a snapshot of migration progress.

Runs daily at 12:00 UTC (~7 AM EST / ~6 AM CST).
"""

import logging
from typing import Dict, Any

from database import SessionLocal
from models.controller import Controller
from models.migration_dashboard_settings import MigrationDashboardSettings

logger = logging.getLogger(__name__)

JOB_ID = "migration_dashboard_daily_snapshot"


async def run_daily_snapshot() -> Dict[str, Any]:
    """
    Poll all MSP controllers with dashboard settings and capture snapshots.

    Called daily by the scheduler service.
    """
    from routers.migration_dashboard import fetch_controller_progress

    db = SessionLocal()
    results = []

    try:
        # Find MSP controllers that have dashboard settings configured
        controllers = (
            db.query(Controller)
            .join(
                MigrationDashboardSettings,
                MigrationDashboardSettings.controller_id == Controller.id,
            )
            .filter(
                Controller.controller_type == "RuckusONE",
                Controller.controller_subtype == "MSP",
            )
            .all()
        )

        if not controllers:
            logger.info("No MSP controllers with dashboard settings found, skipping snapshot")
            return {"status": "skipped", "reason": "no_controllers", "snapshots": 0}

        for controller in controllers:
            try:
                logger.info(f"Capturing snapshot for controller {controller.id} ({controller.name})")
                progress_data, tenants, _ = await fetch_controller_progress(
                    controller.id, db
                )
                results.append({
                    "controller_id": controller.id,
                    "controller_name": controller.name,
                    "status": "success",
                    "total_aps": progress_data["total_aps"],
                    "total_ecs": progress_data["total_ecs"],
                })
            except Exception as e:
                logger.warning(
                    f"Snapshot failed for controller {controller.id} ({controller.name}): {e}"
                )
                results.append({
                    "controller_id": controller.id,
                    "controller_name": controller.name,
                    "status": "error",
                    "error": str(e),
                })

        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "error")
        logger.info(f"Daily snapshot complete: {succeeded} succeeded, {failed} failed")

        return {
            "status": "success",
            "controllers_polled": len(controllers),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
    finally:
        db.close()


TRIGGER_CONFIG = {"hour": 12, "minute": 0}


async def ensure_registered(scheduler) -> None:
    """Register or update the daily snapshot job."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        if existing.trigger_config != TRIGGER_CONFIG:
            await scheduler.update_job(JOB_ID, trigger_config=TRIGGER_CONFIG)
            logger.info(f"Updated snapshot job trigger to {TRIGGER_CONFIG}")
        else:
            logger.info(f"Snapshot job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Migration Dashboard Daily Snapshot",
        callable_path="jobs.migration_snapshot_job:run_daily_snapshot",
        trigger_type="cron",
        trigger_config=TRIGGER_CONFIG,
        owner_type="migration_dashboard",
        description="Polls MSP controllers daily at 12:00 UTC and captures migration progress snapshots",
    )
    logger.info(f"Registered snapshot job '{JOB_ID}' (daily at 12:00 UTC)")
