"""
Slack notifications for a job's owner.

Hooked into save_job because every path that ends a job goes through it: the
Brain, the crash failsafe, the startup reaper and the older routers. The
job_completed event would miss Brain jobs, which never publish it.

Sending runs in its own task, so a slow Slack can't hold up a job, and nothing
here may raise into save_job.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

from utils.slack import send_slack_message
from workflow.v2.models import JobStatus, WorkflowJobV2

logger = logging.getLogger(__name__)

APP_URL = "https://ruckus.tools"
NOTIFIED_TTL_SECONDS = 604800  # Matches the job's own TTL
SUBJECT_TIMEOUT_SECONDS = 10

# status -> (User preference column, emoji, wording)
NOTIFY_STATUSES = {
    JobStatus.COMPLETED: ("slack_notify_completed", ":white_check_mark:", "completed"),
    JobStatus.PARTIAL: ("slack_notify_failed", ":warning:", "partially completed"),
    JobStatus.FAILED: ("slack_notify_failed", ":x:", "failed"),
    JobStatus.CANCELLED: ("slack_notify_failed", ":no_entry_sign:", "was cancelled"),
    JobStatus.AWAITING_CONFIRMATION: ("slack_notify_confirmation", ":hourglass:", "is waiting for your confirmation"),
}

# Tasks are only weakly held by the loop; keep them until they finish.
_tasks: Set[asyncio.Task] = set()


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def build_message(job: WorkflowJobV2) -> str:
    """
    Status, counts, duration and a link. job.errors stays out: it is free text
    of unknown length and content, and the job page already shows it.
    """
    _, emoji, wording = NOTIFY_STATUSES[job.status]
    name = job.workflow_name.replace("_", " ").capitalize()
    lines = [f"{emoji} *{name}* {wording}"]

    details = []
    if job.status != JobStatus.AWAITING_CONFIRMATION:
        progress = job.get_progress()
        if progress.get("total_work"):
            details.append(f"{progress.get('completed_work', 0)}/{progress['total_work']} done")
        if progress.get("units_failed"):
            details.append(f"{progress['units_failed']} units failed")
        if job.completed_at and job.created_at:
            details.append(_format_duration((job.completed_at - job.created_at).total_seconds()))
    if details:
        lines.append(" · ".join(details))

    lines.append(f"<{APP_URL}/jobs/{job.id}|View job>")
    return "\n".join(lines)


def _load_webhook(user_id: int, preference: str) -> Optional[str]:
    """The user's webhook, if they want this event. Blocking; run in a thread."""
    from database import SessionLocal
    from models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not getattr(user, preference):
            return None
        return user.get_slack_webhook() or None
    finally:
        db.close()


def _load_controller_name(controller_id: int) -> Optional[str]:
    """Blocking; run in a thread."""
    from database import SessionLocal
    from models.controller import Controller

    db = SessionLocal()
    try:
        controller = db.query(Controller).filter(Controller.id == controller_id).first()
        return controller.name if controller else None
    finally:
        db.close()


def _load_r1_client(controller_id: int):
    """Blocking; run in a thread."""
    from clients.r1_client import create_r1_client_from_controller
    from database import SessionLocal

    db = SessionLocal()
    try:
        return create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()


async def _venue_name(context: Dict[str, Any]) -> Optional[str]:
    if not context["venue_id"] or not context["controller_id"]:
        return None
    client = await asyncio.to_thread(_load_r1_client, context["controller_id"])
    venue = await client.venues.get_venue(context["tenant_id"], context["venue_id"])
    return venue.get("name")


async def _zone_name(redis_client, context: Dict[str, Any]) -> Optional[str]:
    """SZ migration jobs only. The snapshot they point at expires after 24h."""
    if not context["snapshot_id"]:
        return None
    raw = await redis_client.get(f"sz_migration:extraction:{context['snapshot_id']}:snapshot")
    if not raw:
        return None
    # A snapshot can be large; don't parse it on the event loop.
    snapshot = await asyncio.to_thread(json.loads, raw)
    return snapshot["zone"]["name"]


async def _controller_name(context: Dict[str, Any]) -> Optional[str]:
    if not context["controller_id"]:
        return None
    return await asyncio.to_thread(_load_controller_name, context["controller_id"])


def _escape(text: str) -> str:
    """Slack control characters, so a name can't become a link or an @channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _job_subject(redis_client, context: Dict[str, Any]) -> str:
    """
    "Venue: X · Zone: Y · Controller: Z", with whichever could be found. Each
    is best effort: a lookup that fails or hangs only loses its own name, and
    the message still goes out.
    """
    labelled = (
        ("Venue", _venue_name(context)),
        ("Zone", _zone_name(redis_client, context)),
        ("Controller", _controller_name(context)),
    )
    names = await asyncio.gather(
        *(asyncio.wait_for(lookup, SUBJECT_TIMEOUT_SECONDS) for _, lookup in labelled),
        return_exceptions=True,
    )
    parts = []
    for (label, _), name in zip(labelled, names):
        if isinstance(name, Exception):
            logger.info(f"Job {context['job_id']}: no {label} name for Slack: {type(name).__name__}")
        elif name:
            parts.append(f"{label}: {_escape(str(name))}")
    return " · ".join(parts)


async def _deliver(redis_client, context: Dict[str, Any], status: JobStatus, preference: str, message: str) -> None:
    job_id = context["job_id"]
    try:
        webhook = await asyncio.to_thread(_load_webhook, context["user_id"], preference)
        if not webhook:
            return
        # A job can be saved several times with the same status. One message.
        first = await redis_client.set(
            f"workflow:v2:jobs:{job_id}:slack_notified:{status.value}",
            "1", nx=True, ex=NOTIFIED_TTL_SECONDS,
        )
        if first:
            subject = await _job_subject(redis_client, context)
            if subject:
                headline, _, rest = message.partition("\n")
                message = f"{headline}\n{subject}\n{rest}"
            await send_slack_message(webhook, message)
    except Exception as e:
        logger.warning(f"Job {job_id}: Slack notification failed: {type(e).__name__}")


def notify_job_status(redis_client, job: WorkflowJobV2) -> None:
    """Queue a message to the job's owner if this status warrants one."""
    event = NOTIFY_STATUSES.get(job.status)
    if not event or not job.user_id:
        return
    try:
        # Built now: the caller keeps mutating this job after save_job returns.
        message = build_message(job)
        context = {
            "job_id": job.id,
            "user_id": job.user_id,
            "venue_id": job.venue_id,
            "tenant_id": job.tenant_id,
            "controller_id": job.controller_id,
            "snapshot_id": job.input_data.get("sz_snapshot_job_id"),
        }
        task = asyncio.create_task(_deliver(redis_client, context, job.status, event[0], message))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as e:
        logger.warning(f"Job {job.id}: could not queue Slack notification: {type(e).__name__}")
