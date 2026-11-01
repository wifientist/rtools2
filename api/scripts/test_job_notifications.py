#!/usr/bin/env python3
"""
Slack job notification regression test.

Each user can save their own Slack webhook and be told when their jobs change
state. The hook sits in save_job so that every path ending a job is covered.

WHAT THIS GUARDS

  1. A real save_job on a finished job sends its owner one message, even when
     the job is saved again with the same status.
  2. Running jobs, ownerless jobs and events the user turned off send nothing.
  3. A Slack failure never reaches save_job.
  4. Messages carry no job.errors, which can name customers.
  5. Only Slack webhook URLs are accepted, since the server POSTs to them.
  6. The settings API never returns the URL, and stores it encrypted.
  7. Messages name the venue, zone and controller. A lookup that fails only
     loses its own name, and names can't inject Slack links or @channel.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dependencies import get_current_user, get_db
from models.user import User
from routers import protected
from utils import slack as slack_utils
from utils.slack import is_slack_webhook_url, send_slack_message
from workflow.v2 import notifications
from workflow.v2.models import JobStatus, WorkflowJobV2
from workflow.v2.state_manager import RedisStateManagerV2

WEBHOOK = "https://hooks.slack.com/services/T000/B000/abc123"
failures = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        failures.append(name)


class FakePipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    async def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.keys = set()

    def pipeline(self, *args, **kwargs):
        return FakePipeline()

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def make_job(status=JobStatus.COMPLETED, user_id=7, **kwargs):
    return WorkflowJobV2(id="job-1", workflow_name="cloudpath_import", status=status, user_id=user_id, **kwargs)


async def run_saves(*jobs, webhook=WEBHOOK, send=None):
    """save_job each job against fakes; returns the messages sent."""
    sent = []

    async def fake_send(url, text):
        sent.append(text)
        return True

    notifications._load_webhook = lambda user_id, preference: webhook
    notifications.send_slack_message = send or fake_send
    state = RedisStateManagerV2(FakeRedis())
    for job in jobs:
        await state.save_job(job)
    await asyncio.sleep(0.05)
    return sent


async def test_delivery():
    print("Delivery")
    sent = await run_saves(make_job(), make_job())
    check("a finished job sends one message across repeat saves", len(sent) == 1)

    sent = await run_saves(make_job(JobStatus.AWAITING_CONFIRMATION), make_job(JobStatus.COMPLETED))
    check("each status gets its own message", len(sent) == 2)

    sent = await run_saves(make_job(JobStatus.RUNNING))
    check("a running job sends nothing", sent == [])

    sent = await run_saves(make_job(user_id=0))
    check("a job with no owner sends nothing", sent == [])

    sent = await run_saves(make_job(), webhook=None)
    check("no webhook or event turned off sends nothing", sent == [])

    async def boom(url, text):
        raise RuntimeError("slack is down")

    try:
        await run_saves(make_job(), send=boom)
        check("a Slack failure does not reach save_job", True)
    except Exception:
        check("a Slack failure does not reach save_job", False)


def test_message():
    print("Message")
    now = datetime.utcnow()
    job = make_job(
        JobStatus.FAILED,
        errors=["Acme Towers unit 12 rejected"],
        created_at=now - timedelta(minutes=2, seconds=5),
        completed_at=now,
    )
    message = notifications.build_message(job)
    check("names the job and its outcome", "Cloudpath import" in message and "failed" in message)
    check("includes the duration", "2m 5s" in message)
    check("links to the job", "https://ruckus.tools/jobs/job-1" in message)
    check("leaves out job.errors", "Acme" not in message)


async def test_subject():
    print("Subject")
    originals = notifications._venue_name, notifications._zone_name, notifications._controller_name

    async def venue(context):
        return "Oak <!channel> & Pine"

    async def zone(redis_client, context):
        return "Oak-Zone"

    async def controller(context):
        return "Lab EC"

    async def broken(context):
        raise RuntimeError("R1 is down")

    async def hangs(context):
        await asyncio.sleep(60)

    notifications._venue_name, notifications._zone_name, notifications._controller_name = venue, zone, controller
    sent = await run_saves(make_job())
    check(
        "names the venue, zone and controller on their own line",
        len(sent) == 1 and sent[0].split("\n")[1] == "Venue: Oak &lt;!channel&gt; &amp; Pine · Zone: Oak-Zone · Controller: Lab EC",
    )
    check("the headline stays first", ":white_check_mark:" in sent[0].split("\n")[0])

    notifications._venue_name = broken
    sent = await run_saves(make_job())
    check("a failed lookup only loses its own name", len(sent) == 1 and "Venue" not in sent[0] and "Controller: Lab EC" in sent[0])

    notifications._venue_name, notifications._zone_name, notifications._controller_name = originals
    notifications.SUBJECT_TIMEOUT_SECONDS, timeout = 0.01, notifications.SUBJECT_TIMEOUT_SECONDS
    notifications._controller_name = hangs
    sent = await run_saves(make_job(controller_id=1))
    notifications.SUBJECT_TIMEOUT_SECONDS = timeout
    notifications._controller_name = originals[2]
    check("a hung lookup does not hold up the message", len(sent) == 1)

    sent = await run_saves(make_job())
    check("a job with no subject skips the line", len(sent) == 1 and sent[0].split("\n")[1].startswith("<https://"))


def test_url_validation():
    print("URL validation")
    check("accepts a Slack webhook", is_slack_webhook_url(WEBHOOK))
    for bad in (
        "http://hooks.slack.com/services/T/B/x",
        "https://hooks.slack.com.evil.test/services/T/B/x",
        "https://hooks.slack.com/services/T/B/x/../../internal",
        "https://hooks.slack.com/services/T/B/x\n",
        "http://redis:6379/",
    ):
        check(f"rejects {bad!r}", not is_slack_webhook_url(bad))


async def test_no_url_in_logs():
    print("Logging")
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    slack_utils.logger.addHandler(handler)
    # Reserved TLD: fails fast, down the same error path as a Slack outage.
    await send_slack_message("https://secret-token.invalid/services/T/B/x", "hi")
    slack_utils.logger.removeHandler(handler)
    check("a failed send logs something", bool(records))
    check("a failed send does not log the URL", not any("secret-token" in r for r in records))


def test_settings_api():
    print("Settings API")
    user = User(
        id=7, slack_notify_completed=True, slack_notify_failed=True, slack_notify_confirmation=True,
    )
    db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None)
    app = FastAPI()
    app.include_router(protected.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    check("starts unconfigured", client.get("/slack_settings").json()["configured"] is False)
    check("rejects a non-Slack URL", client.put("/slack_settings", json={"webhook_url": "http://redis:6379/"}).status_code == 400)
    check("test without a webhook is refused", client.post("/slack_settings/test").status_code == 400)

    resp = client.put("/slack_settings", json={"webhook_url": WEBHOOK, "notify_completed": False})
    check("saves a webhook", resp.status_code == 200 and resp.json()["configured"] is True)
    check("saves the event choices", user.slack_notify_completed is False)
    check("never returns the URL", WEBHOOK not in resp.text and WEBHOOK not in client.get("/slack_settings").text)
    check("stores it encrypted", user.encrypted_slack_webhook != WEBHOOK and user.get_slack_webhook() == WEBHOOK)

    client.put("/slack_settings", json={"notify_completed": True})
    check("saving without a URL keeps the webhook", user.get_slack_webhook() == WEBHOOK)

    check("remove clears it", client.delete("/slack_settings").json()["configured"] is False and user.encrypted_slack_webhook is None)


async def main():
    await test_delivery()
    test_message()
    await test_subject()
    test_url_validation()
    await test_no_url_in_logs()
    test_settings_api()
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("\nAll checks passed")


if __name__ == "__main__":
    asyncio.run(main())
