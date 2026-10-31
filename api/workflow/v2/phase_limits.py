"""
When a running phase should be given up on.

A fixed limit cannot tell a hung phase from a slow one: 2,296 policies at 6s
each is a healthy 3.8 hours, and it died at 60 minutes with the work going
fine. So a phase that reports progress is judged on whether the counts are
still moving. One that has never reported keeps its fixed limit, because
silence is all there is to go on.
"""

import asyncio
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Dict, Optional

# No count has moved for this long: the phase is treated as hung.
PHASE_STALL_SECONDS = 600
# Backstop, so counts that creep forever cannot hold a phase slot forever.
PHASE_HARD_LIMIT_SECONDS = 4 * 3600
WATCHDOG_INTERVAL_SECONDS = 15


class PhaseTimedOut(Exception):
    """The phase was cancelled by a limit. The message says which one."""


class PhaseActivity:
    """What the running phase has reported. Written by emit_progress."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.last_progress: Optional[float] = None
        self.last_summary = ""
        self._counts: Dict[str, int] = {}

    def report(self, item_name: str, current: int, total: int) -> None:
        now = time.monotonic()
        # The first report is what switches the phase to stall judging, so it
        # counts even at 0. After that only a count that moved is activity.
        if self.last_progress is None or self._counts.get(item_name) != current:
            self.last_progress = now
            self.last_summary = f"{current}/{total} {item_name}"
        self._counts[item_name] = current

    def expired(self, fixed_limit: float, now: Optional[float] = None) -> Optional[str]:
        now = time.monotonic() if now is None else now
        elapsed = now - self.started
        if self.last_progress is None:
            if elapsed >= fixed_limit:
                return f"Phase execution timed out after {fixed_limit:.0f}s"
            return None
        if now - self.last_progress >= PHASE_STALL_SECONDS:
            return (
                f"Phase stopped making progress: no change for "
                f"{PHASE_STALL_SECONDS // 60} minutes, last at {self.last_summary}"
            )
        if elapsed >= PHASE_HARD_LIMIT_SECONDS:
            return (
                f"Phase hit the {PHASE_HARD_LIMIT_SECONDS // 3600} hour limit "
                f"at {self.last_summary}"
            )
        return None


current_phase_activity: ContextVar[Optional[PhaseActivity]] = ContextVar(
    "current_phase_activity", default=None
)


async def run_with_phase_limits(work: Awaitable[Any], fixed_limit: float) -> Any:
    """
    Await a phase, cancelling it once PhaseActivity.expired says to.

    Raises PhaseTimedOut in place of the TimeoutError asyncio.wait_for raised.
    """
    activity = PhaseActivity()
    # The task copies the context here, so everything the phase spawns
    # (parallel_map's workers included) reports to this same object.
    token = current_phase_activity.set(activity)
    try:
        task = asyncio.ensure_future(work)
    finally:
        current_phase_activity.reset(token)

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=WATCHDOG_INTERVAL_SECONDS)
            if done:
                return task.result()
            reason = activity.expired(fixed_limit)
            if reason:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise PhaseTimedOut(reason)
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
