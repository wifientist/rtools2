#!/usr/bin/env python3
"""
Phase limits and progress reporting.

A phase that reports progress runs as long as its counts move and is cancelled
once they stop. One that never reports keeps its fixed limit. The monitor gets
counts at most every 30s, and they outlive a cancelled phase.

Usage:
    docker compose exec backend python scripts/test_phase_limits.py
"""

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.phase_executor import PhaseContext, PhaseExecutor
from workflow.v2 import phase_limits
from workflow.v2.phase_limits import (
    PHASE_HARD_LIMIT_SECONDS,
    PHASE_STALL_SECONDS,
    PhaseActivity,
    PhaseTimedOut,
    current_phase_activity,
    run_with_phase_limits,
)
from workflow.v2.state_manager import RedisStateManagerV2


def check(label, ok):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        sys.exit(1)


class Publisher:
    def __init__(self):
        self.events = []

    async def phase_progress(self, **kw):
        self.events.append(kw)


class Redis:
    """Just the hash calls progress storage makes."""

    def __init__(self):
        self.hashes = {}

    def pipeline(self):
        outer = self

        class Pipe:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def hset(self, key, field, value):
                outer.hashes.setdefault(key, {})[field] = value

            def expire(self, key, ttl):
                pass

            async def execute(self):
                pass

        return Pipe()

    async def hgetall(self, key):
        return self.hashes.get(key, {})


class Phase(PhaseExecutor):
    phase_id = "p"

    async def execute(self, inputs):
        pass


def executor(publisher=None, state=None, unit_id=None):
    return Phase(PhaseContext(
        job_id="j", r1_client=None, venue_id="v", tenant_id="t",
        event_publisher=publisher, state_manager=state, unit_id=unit_id,
    ))


def test_expiry_rules():
    print("Expiry rules")
    a = PhaseActivity()
    t0 = a.started
    check("silent phase runs to its fixed limit", a.expired(3600, t0 + 3599) is None)
    check("silent phase stops at its fixed limit", "3600s" in (a.expired(3600, t0 + 3600) or ""))

    a = PhaseActivity()
    t0 = a.started
    with mock.patch.object(phase_limits.time, "monotonic", return_value=t0 + 3000):
        a.report("resident", 5, 10)
    check("reporting phase passes the fixed limit",
          a.expired(3600, t0 + 3000 + PHASE_STALL_SECONDS - 1) is None)
    reason = a.expired(3600, t0 + 3000 + PHASE_STALL_SECONDS)
    check("stops once counts stall, naming where", "5/10 resident" in (reason or ""))

    with mock.patch.object(phase_limits.time, "monotonic", return_value=t0 + 3100):
        a.report("resident", 5, 10)
    check("a repeated count is not activity",
          a.expired(3600, t0 + 3000 + PHASE_STALL_SECONDS) is not None)

    a = PhaseActivity()
    t0 = a.started
    with mock.patch.object(phase_limits.time, "monotonic",
                           return_value=t0 + PHASE_HARD_LIMIT_SECONDS - 1):
        a.report("resident", 1, 10)
    check("moving counts still hit the hard limit",
          "hour limit" in (a.expired(3600, t0 + PHASE_HARD_LIMIT_SECONDS) or ""))


async def test_cancellation():
    print("Cancellation")
    cancelled = []

    async def hung():
        try:
            current_phase_activity.get().report("resident", 1, 2)
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with mock.patch.object(phase_limits, "WATCHDOG_INTERVAL_SECONDS", 0.01), \
            mock.patch.object(phase_limits, "PHASE_STALL_SECONDS", 0.03):
        try:
            await run_with_phase_limits(hung(), 3600)
            check("hung phase raises", False)
        except PhaseTimedOut as e:
            check("hung phase is cancelled and named", cancelled and "1/2 resident" in str(e))

    async def fine():
        return "done"

    check("a finished phase returns its result",
          await run_with_phase_limits(fine(), 3600) == "done")

    # parallel_map's workers are separate tasks and must report to the same object.
    seen = []

    async def spawns():
        ex = executor()

        async def item(i):
            seen.append(current_phase_activity.get())
            return i

        await ex.parallel_map([1, 2], item, item_name="thing")
        return current_phase_activity.get()

    activity = await run_with_phase_limits(spawns(), 3600)
    check("parallel_map workers share the phase's tracker",
          all(s is activity for s in seen) and activity.last_progress is not None)


async def test_publish_throttle():
    print("Publishing")
    pub = Publisher()
    ex = executor(pub)
    for i in range(101):
        await ex.emit_progress(i, 100, item_name="resident")
    check("first and last only, within 30s",
          [e["current"] for e in pub.events] == [0, 100])

    pub = Publisher()
    ex = executor(pub)
    await ex.emit_progress(0, 10, item_name="resident")
    ex._progress_published["resident"] -= 31
    await ex.emit_progress(1, 10, item_name="resident", detail="1 created")
    check("again after 30s, with the breakdown",
          len(pub.events) == 2 and pub.events[1]["detail"] == "1 created")

    class Broken:
        async def phase_progress(self, **kw):
            raise RuntimeError("redis down")

    await executor(Broken()).emit_progress(1, 2)
    check("a publishing failure does not fail the phase", True)


async def test_persisted():
    print("Saved counts")
    state = RedisStateManagerV2(Redis())
    await executor(state=state).emit_progress(3, 10, failed=1, item_name="resident", detail="x")
    saved = await state.get_phase_progress("j")
    check("global phase keeps its counts and breakdown", saved["p"] == [
        {"item_name": "resident", "current": 3, "total": 10, "failed": 1, "detail": "x"}
    ])

    state = RedisStateManagerV2(Redis())
    await executor(state=state, unit_id="u1").emit_progress(2, 5, item_name="passphrase", detail="a")
    await executor(state=state, unit_id="u2").emit_progress(1, 5, item_name="passphrase", detail="b")
    saved = await state.get_phase_progress("j")
    check("per-unit phases sum across units", saved["p"] == [
        {"item_name": "passphrase", "current": 3, "total": 10, "failed": 0, "detail": ""}
    ])


async def main():
    test_expiry_rules()
    await test_cancellation()
    await test_publish_throttle()
    await test_persisted()
    print("All passed")


if __name__ == "__main__":
    asyncio.run(main())
