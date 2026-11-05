"""
Scheduled reports: one scheduler job per enabled schedule.

Each ScheduledReport gets its own cron job ("report_<id>") that fires only when
that schedule says so, at its local send time in its own IANA time zone, so
the send stays put across daylight-saving changes. Nothing runs in between.

The job is created, moved or removed when the schedule is saved
(sync_report_job), and reconciled once at startup (ensure_registered).
"""
import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database import SessionLocal
from models.scheduled_report import ScheduledReport

logger = logging.getLogger(__name__)

# The old single dispatcher that checked every schedule once a weekday.
LEGACY_JOB_ID = "report_dispatcher"
JOB_PREFIX = "report_"
CALLABLE_PATH = "jobs.report_dispatcher_job:run_scheduled_report"
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def job_id_for(report_id: int) -> str:
    return f"{JOB_PREFIX}{report_id}"


def report_zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(f"Unknown report time zone {name!r}, using UTC")
        return ZoneInfo("UTC")


def parse_send_time(value: Optional[str]) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in (value or "").split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return 12, 30


def trigger_for(report: ScheduledReport) -> dict:
    """
    Cron trigger for a schedule, on its local clock:
    - daily: weekdays (Mon-Fri)
    - weekly: the configured day_of_week (0=Mon, 6=Sun)
    - monthly: the 1st
    """
    h, m = parse_send_time(report.send_time)
    config: dict = {"hour": h, "minute": m, "timezone": report_zone(report.timezone).key}
    if report.frequency == "monthly":
        config["day"] = 1
    elif report.frequency == "weekly":
        config["day_of_week"] = DAY_NAMES[report.day_of_week % 7]
    else:
        config["day_of_week"] = "mon-fri"
    return config


async def sync_report_job(scheduler, report: ScheduledReport) -> None:
    """Create, move or remove the scheduler job for one schedule."""
    job_id = job_id_for(report.id)
    existing = await scheduler.get_job(job_id)
    if not report.enabled:
        if existing:
            await scheduler.unregister_job(job_id)
            logger.info(f"Removed report job {job_id} (schedule disabled)")
        return

    trigger = trigger_for(report)
    if existing:
        if existing.trigger_config != trigger:
            await scheduler.update_job(job_id, trigger_config=trigger)
            logger.info(f"Moved report job {job_id} to {trigger}")
        return

    await scheduler.register_job(
        job_id=job_id,
        name=f"Report: {report.report_type} {report.context_id}",
        callable_path=CALLABLE_PATH,
        callable_kwargs={"report_id": report.id},
        trigger_type="cron",
        trigger_config=trigger,
        owner_type="reports",
        owner_id=str(report.id),
        description=f"Emails the {report.report_type} report on its schedule",
    )
    logger.info(f"Registered report job {job_id} at {trigger}")


async def run_scheduled_report(report_id: int) -> Dict[str, Any]:
    """Generate and email one scheduled report (called by its own cron job)."""
    from services.report_engine import generate_and_send_report

    db = SessionLocal()
    try:
        report = db.get(ScheduledReport, report_id)
        if not report or not report.enabled:
            return {"status": "skipped", "reason": "disabled_or_missing"}
        if not report.recipients:
            logger.warning(f"Report {report.id} ({report.report_type}): no recipients, skipping")
            return {"status": "skipped", "reason": "no_recipients"}

        # One send per local day, even if the job fires twice (e.g. a restart
        # landing inside the misfire grace window).
        tz = report_zone(report.timezone)
        now = datetime.utcnow()
        if report.last_sent_at:
            last_local = report.last_sent_at.replace(tzinfo=dt_timezone.utc).astimezone(tz)
            now_local = now.replace(tzinfo=dt_timezone.utc).astimezone(tz)
            if last_local.date() == now_local.date():
                return {"status": "skipped", "reason": "already_sent_today"}

        logger.info(f"Generating report {report.id}: type={report.report_type} context={report.context_id}")
        result = await generate_and_send_report(report, db)
        report.last_sent_at = now
        db.commit()
        return {"status": "success", "report_id": report.id, "emails_sent": result["emails_sent"]}
    finally:
        db.close()


async def ensure_registered(scheduler) -> None:
    """
    Startup reconcile: drop the legacy dispatcher, give every enabled schedule
    its job with the current trigger, and remove jobs whose schedule is gone.
    """
    if await scheduler.get_job(LEGACY_JOB_ID):
        await scheduler.unregister_job(LEGACY_JOB_ID)
        logger.info(f"Removed legacy job '{LEGACY_JOB_ID}'")

    db = SessionLocal()
    try:
        reports = db.query(ScheduledReport).all()
        for report in reports:
            try:
                await sync_report_job(scheduler, report)
            except Exception as e:
                logger.error(f"Could not sync report job for schedule {report.id}: {e}")
        keep = {job_id_for(r.id) for r in reports if r.enabled}
    finally:
        db.close()

    for job in await scheduler.list_jobs(owner_type="reports"):
        if job.id.startswith(JOB_PREFIX) and job.id not in keep:
            await scheduler.unregister_job(job.id)
            logger.info(f"Removed orphan report job {job.id}")
