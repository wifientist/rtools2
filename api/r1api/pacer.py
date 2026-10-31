"""
Keeps each tenant's R1 calls under its rate limit.

R1 gives a tenant a token bucket of 2,000 calls a minute, shared with everything
else on that tenant, the R1 UI included. So this aims lower, and treats a 429 as
the real signal: everyone on the tenant pauses, continues at half the rate, and
climbs back slowly, because R1 restores access gradually rather than at once.

One process only. Another backend or a browser on the same tenant is invisible
here, which is what the margin and the 429 handling are for.
"""

import logging
import os
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 2,000 a minute less room for the UI, which refreshes constantly. 1,500 held for
# several minutes drew 429s on a 2,000-resident import.
DEFAULT_CALLS_PER_MINUTE = 1200
# R1 does not publish its bucket size, so bursts stay small.
BURST_CALLS = 50
MIN_CALLS_PER_SECOND = 2.0
# One burst of 429s is one event, however many workers see it.
CUT_COOLDOWN_SECONDS = 5.0
DEFAULT_PAUSE_SECONDS = 2.0
RECOVER_AFTER_SECONDS = 30.0
RECOVER_CALLS_PER_SECOND_PER_SECOND = 0.05


def _ceiling_per_second() -> float:
    try:
        per_minute = float(os.environ.get("R1_CALLS_PER_MINUTE", DEFAULT_CALLS_PER_MINUTE))
    except ValueError:
        per_minute = DEFAULT_CALLS_PER_MINUTE
    return max(MIN_CALLS_PER_SECOND, per_minute / 60)


class TenantPacer:
    def __init__(self, ceiling: float, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        self.ceiling = ceiling
        self.rate = ceiling
        self.tokens = float(BURST_CALLS)
        self.updated = now
        self.paused_until = 0.0
        self.last_cut = None
        self.lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        if (
            self.rate < self.ceiling
            and self.last_cut is not None
            and now - self.last_cut >= RECOVER_AFTER_SECONDS
        ):
            self.rate = min(
                self.ceiling, self.rate + elapsed * RECOVER_CALLS_PER_SECOND_PER_SECOND
            )
        self.tokens = min(float(BURST_CALLS), self.tokens + elapsed * self.rate)

    def reserve(self, now: Optional[float] = None) -> float:
        """Take the next call's place. Returns how long to wait before sending it."""
        now = time.monotonic() if now is None else now
        with self.lock:
            self._refill(now)
            self.tokens -= 1
            wait = -self.tokens / self.rate if self.tokens < 0 else 0.0
            return max(wait, self.paused_until - now)

    def rate_limited(self, retry_after: Optional[float], now: Optional[float] = None) -> bool:
        """R1 said 429. Returns True when this one lowered the rate."""
        now = time.monotonic() if now is None else now
        with self.lock:
            self._refill(now)
            pause = retry_after if retry_after and retry_after > 0 else DEFAULT_PAUSE_SECONDS
            self.paused_until = max(self.paused_until, now + pause)
            self.tokens = min(self.tokens, 0.0)
            if self.last_cut is not None and now - self.last_cut < CUT_COOLDOWN_SECONDS:
                return False
            self.last_cut = now
            self.rate = max(MIN_CALLS_PER_SECOND, self.rate / 2)
            return True


_pacers: Dict[str, TenantPacer] = {}
_pacers_lock = threading.Lock()


def pacer_for(tenant: str) -> TenantPacer:
    with _pacers_lock:
        pacer = _pacers.get(tenant)
        if pacer is None:
            pacer = _pacers[tenant] = TenantPacer(_ceiling_per_second())
        return pacer


def wait_for_turn(tenant: str, may_sleep: bool) -> float:
    """
    Blocks until this tenant may send another call, and returns the seconds
    waited. Never blocks an event loop: such a call is counted and sent at once.
    """
    wait = pacer_for(tenant).reserve()
    if wait <= 0 or not may_sleep:
        return 0.0
    time.sleep(wait)
    return wait


def note_rate_limited(tenant: str, retry_after: Optional[float]) -> None:
    pacer = pacer_for(tenant)
    if pacer.rate_limited(retry_after):
        logger.warning(
            f"R1 rate limit reached: slowing this tenant to "
            f"{pacer.rate * 60:.0f} calls/min"
        )
