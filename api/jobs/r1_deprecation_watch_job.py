"""
Scheduled job: daily R1 deprecation audit.

Asked for explicitly (2026-11-11) so R1 deprecations surface before a tool
breaks. It sends no requests to R1: it reads the code, the newest spec in
api/specs and the deprecation signals R1 already put on live responses
(r1api.deprecation_watch), then stores the report for the banner.

Slack: super users with a webhook hear about each critical/high call once
per severity (tracked in r1:deprecations:notified), never the same list twice.
"""

import asyncio
import logging
from typing import Any, Dict, List

from database import SessionLocal
from models.user import RoleEnum, User
from services import r1_deprecation_audit as audit
from utils.slack import send_slack_message

logger = logging.getLogger(__name__)

JOB_ID = "r1_deprecation_watch_daily"
TRIGGER_CONFIG = {"hour": 13, "minute": 0}
NOTIFY_SEVERITIES = ("critical", "high")
SLACK_LINES = 15


def _slack_text(changed: List[Dict[str, Any]], report: Dict[str, Any]) -> str:
    lines = [
        f"*R1 deprecation watch*: {len(changed)} call(s) new or worse "
        f"(spec {report.get('spec_file')}, {report.get('spec_age_days')} days old)"
    ]
    for f in changed[:SLACK_LINES]:
        repl = f" → use `{f['replacement']}`" if f.get("replacement") else ""
        lines.append(f"• *{f['severity']}* `{f['method']} {f['path']}`: {f['reasons'][0]}{repl}")
    if len(changed) > SLACK_LINES:
        lines.append(f"…and {len(changed) - SLACK_LINES} more on the R1 Call Timing page.")
    if report.get("spec_stale"):
        lines.append("_The newest R1 spec in api/specs is getting old; upload a fresh one._")
    return "\n".join(lines)


async def run_deprecation_watch() -> Dict[str, Any]:
    try:
        report = await asyncio.to_thread(audit.run_audit)
        await asyncio.to_thread(audit.save_report, report)
        changed = await asyncio.to_thread(audit.unnotified, report, NOTIFY_SEVERITIES)
    except Exception as e:
        logger.error(f"R1 deprecation audit failed: {e}")
        return {"status": "error", "error": str(e)}

    sent = 0
    if changed:
        db = SessionLocal()
        try:
            supers = db.query(User).filter(User.role == RoleEnum.super).all()
            hooks = [u.get_slack_webhook() for u in supers if u.encrypted_slack_webhook]
        finally:
            db.close()
        text = _slack_text(changed, report)
        for hook in hooks:
            if hook and await send_slack_message(hook, text):
                sent += 1
        if sent:
            # Only once someone heard it; with no webhook set yet, the
            # backlog arrives the day one is.
            await asyncio.to_thread(audit.mark_notified, changed)

    logger.info(
        f"R1 deprecation audit: {report['counts']} across {report['calls_checked']} calls; "
        f"{len(changed)} new or worse; Slack sent to {sent}"
    )
    return {
        "status": "success",
        "counts": report["counts"],
        "new_or_worse": len(changed),
        "slack_sent": sent,
    }


async def ensure_registered(scheduler) -> None:
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        if existing.trigger_config != TRIGGER_CONFIG:
            await scheduler.update_job(JOB_ID, trigger_config=TRIGGER_CONFIG)
        return
    await scheduler.register_job(
        job_id=JOB_ID,
        name="R1 Deprecation Watch",
        callable_path="jobs.r1_deprecation_watch_job:run_deprecation_watch",
        trigger_type="cron",
        trigger_config=TRIGGER_CONFIG,
        owner_type="system",
        description="Daily 13:00 UTC: checks the R1 calls our tools make against the newest spec and R1's live deprecation headers. No R1 calls.",
    )
    logger.info(f"Registered '{JOB_ID}' (daily at 13:00 UTC)")
