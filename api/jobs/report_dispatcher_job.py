"""
Scheduled job: Report dispatcher.

Runs daily at 12:30 UTC. Checks all enabled scheduled reports and
generates/emails those that are due based on their frequency settings.
"""
import logging
from datetime import datetime
from typing import Dict, Any

from database import SessionLocal
from models.scheduled_report import ScheduledReport

logger = logging.getLogger(__name__)

JOB_ID = "report_dispatcher"


def _is_report_due(report: ScheduledReport, now: datetime) -> bool:
    """
    Check whether a report should be sent today.

    Frequency rules:
    - daily: send every day
    - weekly: send on the configured day_of_week (0=Mon, 6=Sun)
    - monthly: send on the 1st of each month
    """
    freq = report.frequency

    if freq == "weekly":
        if now.weekday() != report.day_of_week:
            return False
    elif freq == "monthly":
        if now.day != 1:
            return False

    # Guard against double-sends (must be >23h since last send)
    if report.last_sent_at:
        hours_since = (now - report.last_sent_at).total_seconds() / 3600
        if hours_since < 23:
            return False

    return True


async def run_report_dispatcher() -> Dict[str, Any]:
    """
    Check all enabled reports and generate/send those that are due today.
    """
    from services.report_engine import generate_and_send_report

    db = SessionLocal()
    now = datetime.utcnow()
    results = []

    try:
        reports = (
            db.query(ScheduledReport)
            .filter(ScheduledReport.enabled == True)
            .all()
        )

        if not reports:
            logger.info("No enabled scheduled reports, skipping")
            return {"status": "skipped", "reason": "no_enabled_reports", "reports_sent": 0}

        for report in reports:
            if not _is_report_due(report, now):
                continue

            if not report.recipients:
                logger.warning(
                    f"Report {report.id} ({report.report_type}): enabled but no recipients, skipping"
                )
                continue

            try:
                logger.info(
                    f"Generating report {report.id}: type={report.report_type} context={report.context_id}"
                )
                result = await generate_and_send_report(report, db)

                # Update last_sent_at
                report.last_sent_at = now
                db.commit()

                results.append({
                    "report_id": report.id,
                    "report_type": report.report_type,
                    "context_id": report.context_id,
                    "status": "success",
                    "emails_sent": result["emails_sent"],
                })

            except Exception as e:
                logger.error(
                    f"Report {report.id} ({report.report_type}) failed: {e}",
                    exc_info=True,
                )
                db.rollback()
                results.append({
                    "report_id": report.id,
                    "report_type": report.report_type,
                    "context_id": report.context_id,
                    "status": "error",
                    "error": str(e),
                })

        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "error")
        logger.info(f"Report dispatcher complete: {succeeded} sent, {failed} failed")

        return {
            "status": "success",
            "reports_sent": succeeded,
            "reports_failed": failed,
            "results": results,
        }

    finally:
        db.close()


TRIGGER_CONFIG = {"hour": 12, "minute": 30}


async def ensure_registered(scheduler) -> None:
    """Register or update the daily report dispatcher job."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        # Update trigger config if it has drifted from the code definition
        if existing.trigger_config != TRIGGER_CONFIG:
            await scheduler.update_job(JOB_ID, trigger_config=TRIGGER_CONFIG)
            logger.info(
                f"Updated report dispatcher trigger to {TRIGGER_CONFIG}"
            )
        else:
            logger.info(f"Report dispatcher job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Report Dispatcher",
        callable_path="jobs.report_dispatcher_job:run_report_dispatcher",
        trigger_type="cron",
        trigger_config=TRIGGER_CONFIG,
        owner_type="reports",
        description="Daily at 12:30 UTC: generates and emails PDF reports for enabled schedules",
    )
    logger.info(f"Registered report dispatcher job '{JOB_ID}' (daily at 12:30 UTC)")
