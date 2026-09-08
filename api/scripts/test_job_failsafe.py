#!/usr/bin/env python3
"""
Job failsafe regression test.

A workflow job must never go quiet. Before this, one could: if the engine
crashed in a way that stopped it recording its own terminal status, every
launcher's handler was

    except Exception as e:
        logger.exception(f"[...] Execution failed for job {job_id}: {e}")

which logs and returns. The job stayed RUNNING with no heartbeat, the UI
spun, and the stranded-job reaper failed it up to five minutes later
(120s heartbeat TTL, 300s sweep) with a generic message, while the real
exception sat in the logs. Reported from production 2026-09-08 as "it just
stopped after one or two phases, then slept for 300 seconds and failed".

fail_job() writes the terminal status immediately, carrying the real error.

WHAT THIS GUARDS

  1. A RUNNING job is marked FAILED, with the exception text preserved.
  2. A job that already finished is LEFT ALONE -- a crash while shutting
     down must not rewrite COMPLETED as FAILED.
  3. The cancellation path writes CANCELLED, not FAILED.
  4. A broken Redis does not raise out of fail_job. It is called from an
     except block on the path where Redis is the likely culprit, so raising
     there would mask the original error.
  5. Every launcher that runs a workflow also calls fail_job. All eight were
     identical because each new tool is copied from the last one, which is
     exactly how this spread -- a behavioural test cannot see that.

Usage:
    docker compose exec backend python scripts/test_job_failsafe.py

Exits non-zero on failure.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.v2.failsafe import fail_job
from workflow.v2.models import JobStatus


class FakeJob:
    def __init__(self, status):
        self.id = "job-1"
        self.status = status
        self.errors = []
        self.completed_at = None
        self.phase_definitions = []
        self.global_phase_status = {}

    def get_progress(self):
        return {}


class FakeStateManager:
    """fail_on: None, "get" or "save" -- simulates a drained Redis pool."""

    def __init__(self, job, fail_on=None):
        self.job = job
        self.fail_on = fail_on
        self.saved = None

    async def get_job(self, job_id):
        if self.fail_on == "get":
            raise ConnectionError("No connection available.")
        return self.job

    async def save_job(self, job):
        if self.fail_on == "save":
            raise ConnectionError("No connection available.")
        self.saved = job
        return True


class FakePublisher:
    def __init__(self):
        self.events = []

    async def job_failed(self, job):
        self.events.append("job_failed")

    async def job_cancelled(self, job):
        self.events.append("job_cancelled")


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def behaviour_checks() -> int:
    failures = 0
    err = RuntimeError("brain died mid-run")

    # 1. RUNNING -> FAILED, error preserved, UI notified
    sm = FakeStateManager(FakeJob(JobStatus.RUNNING))
    pub = FakePublisher()
    wrote = await fail_job(sm, "job-1", err, source="Test", event_publisher=pub)
    failures += check(
        "RUNNING job is marked FAILED with the real error",
        wrote
        and sm.saved.status == JobStatus.FAILED
        and "brain died mid-run" in sm.saved.errors[0]
        and sm.saved.completed_at is not None
        and pub.events == ["job_failed"],
        f"saved={sm.saved and sm.saved.status}, errors={sm.saved and sm.saved.errors}",
    )

    # 2. a finished job is not rewritten
    for status in (JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.CANCELLED):
        sm = FakeStateManager(FakeJob(status))
        wrote = await fail_job(sm, "job-1", err, source="Test")
        failures += check(
            f"{status.value} job is left alone", not wrote and sm.saved is None
        )

    # 3. cancellation writes CANCELLED
    sm = FakeStateManager(FakeJob(JobStatus.RUNNING))
    pub = FakePublisher()
    await fail_job(
        sm, "job-1", asyncio.CancelledError(), source="Test",
        status=JobStatus.CANCELLED, event_publisher=pub,
    )
    failures += check(
        "cancellation writes CANCELLED, not FAILED",
        sm.saved.status == JobStatus.CANCELLED and pub.events == ["job_cancelled"],
    )

    # 4. a broken Redis must not raise
    for mode in ("get", "save"):
        sm = FakeStateManager(FakeJob(JobStatus.RUNNING), fail_on=mode)
        try:
            wrote = await fail_job(sm, "job-1", err, source="Test")
            failures += check(f"Redis broken on {mode}: returns False, no raise", not wrote)
        except Exception as e:
            failures += check(f"Redis broken on {mode}: returns False, no raise", False, repr(e))

    # 5. a job that is gone
    class Gone(FakeStateManager):
        async def get_job(self, job_id):
            return None

    failures += check(
        "missing job reports and returns False",
        not await fail_job(Gone(None), "job-1", err, source="Test"),
    )
    return failures


def launcher_checks() -> int:
    """Every background launcher that runs a workflow must call fail_job."""
    routers = Path(__file__).parent.parent / "routers"
    offenders = []
    for path in sorted(routers.rglob("*.py")):
        src = path.read_text()
        if "brain.execute_workflow" not in src:
            continue
        # Per enclosing async def, not per file: one function may handle it
        # while the next one added by copy-paste does not.
        for block in re.split(r"\nasync def ", src):
            if "brain.execute_workflow" in block and "fail_job" not in block:
                name = block.split("(")[0].strip().splitlines()[0]
                offenders.append(f"{path.relative_to(routers.parent)}:{name}")

    return check(
        "every launcher marks its job terminal on a crash",
        not offenders,
        ", ".join(offenders),
    )


async def main() -> int:
    print("Job failsafe\n")
    failures = await behaviour_checks()
    failures += launcher_checks()
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
