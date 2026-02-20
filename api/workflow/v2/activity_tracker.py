"""
Centralized Activity Tracker

Manages bulk polling of R1 async activities across all phases and units.

Features:
- Single background poller for ALL pending activities across all jobs
- 3s polling interval with bulk time-based query (POST /activities/query)
- Uses fromTime/toTime filters to fetch all activities since workflow start
- Matches returned activities by requestId against pending set
- Falls back to individual GET /activities/{id} if bulk query fails
- Redis-backed for multi-worker visibility
- asyncio.Event-based notifications for phase executors waiting on results
- Circuit breaker: stops polling after 10 consecutive failures

Usage:
    tracker = ActivityTracker(r1_client, state_manager)

    # Phase registers an activity
    await tracker.register(activity_id, job_id, unit_id, phase_id)

    # Phase waits for its activity
    result = await tracker.wait(activity_id)

    # Brain waits for any activity to complete
    completed = await tracker.wait_for_any()
"""

import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta

from workflow.v2.models import ActivityRef, ActivityResult
from workflow.v2.state_manager import RedisStateManagerV2

logger = logging.getLogger(__name__)


def _ts() -> str:
    """Return current timestamp for logging."""
    return datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]


# Polling interval: single bulk query every 10 seconds.
# R1 activities are slow (typically 30-90s), so polling faster just wastes API calls.
POLL_INTERVAL = 10.0

# Max time to track a single activity before timeout
# Must be less than PHASE_EXECUTION_TIMEOUT (600s) to allow the phase
# to handle the timeout result before the phase itself times out.
ACTIVITY_TIMEOUT_SECONDS = 540  # 9 minutes

# Error handling
MAX_CONSECUTIVE_ERRORS = 10    # Stop polling after this many consecutive failures

# Concurrency control for fallback individual GETs
MAX_CONCURRENT_ACTIVITY_POLLS = 25

# Fields to request from the activities query
ACTIVITY_QUERY_FIELDS = [
    "startDatetime",
    "endDatetime",
    "status",
    "product",
    "admin",
    "descriptionTemplate",
    "descriptionData",
    "severity",
]


class ActivityTracker:
    """
    Centralized tracker for R1 async activities.

    Collects activities from all phases across all jobs, polls them
    in bulk via POST /activities/query with time-based filtering,
    and notifies waiting coroutines on completion.
    """

    def __init__(
        self,
        r1_client,
        state_manager: RedisStateManagerV2,
        tenant_id: str = None
    ):
        self.r1_client = r1_client
        self.state = state_manager
        self.tenant_id = tenant_id

        # In-memory tracking (per-worker)
        self._pending: Dict[str, ActivityRef] = {}        # activity_id → ref
        self._events: Dict[str, asyncio.Event] = {}       # activity_id → completion event
        self._results: Dict[str, ActivityResult] = {}      # activity_id → result

        # Time-based query tracking
        self._from_time: Optional[str] = None  # ISO timestamp for fromTime filter

        # Background poller control
        self._polling = False
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._poll_cycle = 0

        # Stats
        self._total_registered = 0
        self._total_completed = 0
        self._consecutive_errors = 0
        self._bulk_query_failures = 0
        # Last poll cycle status breakdown (e.g. {"INPROGRESS": 12, "SUCCESS": 3})
        self._last_poll_status: Dict[str, int] = {}

    # =========================================================================
    # Public API
    # =========================================================================

    async def register(
        self,
        activity_id: str,
        job_id: str,
        unit_id: str = None,
        phase_id: str = "",
        task_id: str = None
    ) -> None:
        """
        Register an R1 activity for tracking.

        Call this after making an R1 API call that returns a requestId.
        The tracker will poll for completion and notify when done.
        """
        ref = ActivityRef(
            activity_id=activity_id,
            job_id=job_id,
            unit_id=unit_id,
            phase_id=phase_id,
            task_id=task_id,
            registered_at=datetime.utcnow()
        )

        self._pending[activity_id] = ref
        self._events[activity_id] = asyncio.Event()
        self._total_registered += 1

        # Set fromTime on first registration (with 30s buffer for clock skew)
        if self._from_time is None:
            buffer = datetime.now(timezone.utc) - timedelta(seconds=30)
            self._from_time = buffer.strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.info(f"[{_ts()}] ActivityTracker fromTime set to {self._from_time}")

        # Persist to Redis for multi-worker visibility
        await self.state.register_activity(ref)

        logger.debug(
            f"[{_ts()}] Registered activity {activity_id[:8]}... "
            f"(job={job_id[:8]}..., unit={unit_id}, phase={phase_id}) "
            f"[{len(self._pending)} total pending]"
        )

        # Ensure poller is running
        self._ensure_polling()

    async def register_batch(
        self,
        activities: list[tuple[str, str, str, str]],
    ) -> None:
        """
        Register multiple activities at once.

        Args:
            activities: List of (activity_id, job_id, unit_id, phase_id) tuples
        """
        for activity_id, job_id, unit_id, phase_id in activities:
            await self.register(activity_id, job_id, unit_id, phase_id)

    async def wait(self, activity_id: str, timeout: float = ACTIVITY_TIMEOUT_SECONDS) -> ActivityResult:
        """
        Wait for a specific activity to complete.

        Returns:
            ActivityResult with success/failure and resource ID

        Raises:
            TimeoutError: If activity doesn't complete within timeout
            ValueError: If activity was never registered
        """
        # Already completed?
        if activity_id in self._results:
            return self._results[activity_id]

        if activity_id not in self._events:
            raise ValueError(f"Activity {activity_id} was never registered with this tracker")

        # Wait for completion event
        try:
            await asyncio.wait_for(
                self._events[activity_id].wait(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Activity {activity_id} timed out after {timeout}s")
            result = ActivityResult(
                activity_id=activity_id,
                success=False,
                error=f"Activity timed out after {timeout} seconds"
            )
            self._results[activity_id] = result
            self._cleanup_activity(activity_id)
            return result

        return self._results[activity_id]

    async def wait_batch(
        self,
        activity_ids: list[str],
        timeout: float = ACTIVITY_TIMEOUT_SECONDS
    ) -> Dict[str, ActivityResult]:
        """Wait for multiple activities to complete."""
        results = {}
        wait_tasks = [
            self.wait(aid, timeout=timeout) for aid in activity_ids
        ]
        completed = await asyncio.gather(*wait_tasks, return_exceptions=True)

        for aid, result in zip(activity_ids, completed):
            if isinstance(result, Exception):
                results[aid] = ActivityResult(
                    activity_id=aid,
                    success=False,
                    error=str(result)
                )
            else:
                results[aid] = result

        return results

    async def wait_for_any(self, timeout: float = 30.0) -> Optional[ActivityResult]:
        """
        Wait for any activity to complete. Used by the Brain.

        Returns:
            The first completed ActivityResult, or None on timeout
        """
        if not self._events:
            await asyncio.sleep(0.1)
            return None

        # Create futures for all pending events
        pending_events = {
            aid: asyncio.create_task(event.wait())
            for aid, event in self._events.items()
            if not event.is_set()
        }

        if not pending_events:
            return None

        try:
            done, _ = await asyncio.wait(
                pending_events.values(),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED
            )

            if done:
                for aid, task in pending_events.items():
                    if task in done:
                        return self._results.get(aid)
        except Exception as e:
            logger.warning(f"Error in wait_for_any: {e}")
        finally:
            for task in pending_events.values():
                if not task.done():
                    task.cancel()

        return None

    @property
    def pending_count(self) -> int:
        """Number of currently pending activities."""
        return len(self._pending)

    @property
    def stats(self) -> Dict:
        """Tracker statistics."""
        return {
            "pending": len(self._pending),
            "total_registered": self._total_registered,
            "total_completed": self._total_completed,
            "polling": self._polling,
            "bulk_query_failures": self._bulk_query_failures,
        }

    # =========================================================================
    # Background Polling
    # =========================================================================

    def _ensure_polling(self) -> None:
        """Start the background polling loop if not already running."""
        if not self._polling:
            self._polling = True
            self._stop_event.clear()
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """
        Background loop that polls R1 for all pending activities.

        Uses a single POST /activities/query with fromTime/toTime filters
        to fetch all activities in one request, then matches against pending set.
        Polls every 10 seconds.
        """
        self._polling = True
        self._poll_cycle = 0
        logger.info(f"[{_ts()}] ActivityTracker poll loop started (interval={POLL_INTERVAL}s)")

        try:
            while self._pending and not self._stop_event.is_set():
                self._poll_cycle += 1
                cycle_id = self._poll_cycle

                pending_count = len(self._pending)
                activity_ids = list(self._pending.keys())

                logger.debug(
                    f"[{_ts()}] POLL CYCLE #{cycle_id}: {pending_count} pending activities"
                )

                try:
                    await self._poll_activities(activity_ids, cycle_id)
                except Exception as e:
                    logger.error(f"[{_ts()}] Poll cycle #{cycle_id} error: {e}")

                # Expire activities that have been pending too long
                now = datetime.utcnow()
                for aid in list(self._pending.keys()):
                    ref = self._pending[aid]
                    age = (now - ref.registered_at).total_seconds()
                    if age > ACTIVITY_TIMEOUT_SECONDS:
                        logger.warning(
                            f"[{_ts()}] Activity {aid[:8]}... expired after "
                            f"{int(age)}s (max {ACTIVITY_TIMEOUT_SECONDS}s) — "
                            f"forcing timeout (unit={ref.unit_id}, phase={ref.phase_id})"
                        )
                        await self._handle_completion(
                            aid,
                            success=False,
                            error=f"Activity expired after {int(age)}s "
                                  f"(R1 never returned a terminal status)"
                        )

                # Wait for interval or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=POLL_INTERVAL
                    )
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Normal - interval elapsed, continue polling

        finally:
            self._polling = False
            logger.info(
                f"[{_ts()}] ActivityTracker poll loop stopped "
                f"(completed={self._total_completed}, cycles={self._poll_cycle})"
            )

    async def _poll_activities(self, activity_ids: list[str], cycle_id: int = 0) -> None:
        """
        Poll all pending activities.

        Strategy:
        1. Try bulk time-based query via POST /activities/query (single request)
        2. Fall back to concurrent individual GETs if bulk fails
        """
        if not activity_ids:
            return

        results = {}
        used_bulk = False

        # Try bulk time-based query first
        try:
            results = await self._poll_activities_bulk_time(activity_ids, cycle_id)
            used_bulk = True
            self._consecutive_errors = 0
        except Exception as e:
            self._bulk_query_failures += 1
            logger.debug(
                f"[{_ts()}] Cycle #{cycle_id}: Bulk query failed ({e}), "
                f"falling back to individual GETs"
            )

        # Fall back to individual GETs
        if not used_bulk:
            results, errors = await self._poll_activities_individual(activity_ids, cycle_id)

            # Track consecutive errors for circuit breaker
            if errors == len(activity_ids) and len(activity_ids) > 0:
                self._consecutive_errors += 1
                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        f"[{_ts()}] Cycle #{cycle_id}: {MAX_CONSECUTIVE_ERRORS} consecutive "
                        f"poll failures — marking all as failed"
                    )
                    for activity_id in activity_ids:
                        await self._handle_completion(
                            activity_id,
                            success=False,
                            error=f"Activity polling failed after {MAX_CONSECUTIVE_ERRORS} attempts"
                        )
                    return
            else:
                self._consecutive_errors = 0

        # Build status breakdown from matched results
        status_counts: Dict[str, int] = {}
        for aid in activity_ids:
            data = results.get(aid)
            if data:
                s = data.get("status", "UNKNOWN").upper()
                status_counts[s] = status_counts.get(s, 0) + 1
        self._last_poll_status = status_counts

        status_summary = ", ".join(f"{s}={c}" for s, c in sorted(status_counts.items()))
        logger.debug(
            f"[{_ts()}] Cycle #{cycle_id}: Got {len(results)}/{len(activity_ids)} activities"
            f"{' (bulk)' if used_bulk else ' (individual)'}"
            f" | {status_summary}"
        )

        # Process all results
        for activity_id in activity_ids:
            data = results.get(activity_id)
            if data:
                await self._process_activity_result(activity_id, data)

    # =========================================================================
    # Bulk Time-Based Query (primary method)
    # =========================================================================

    async def _poll_activities_bulk_time(
        self,
        activity_ids: list[str],
        cycle_id: int = 0
    ) -> dict[str, dict]:
        """
        Poll activities using POST /activities/query with fromTime/toTime.

        Returns dict mapping activity_id -> activity data.
        Raises exception on failure (caller falls back to individual GETs).
        """
        # Build time window: fromTime = workflow start, toTime = now + 1min buffer
        now = datetime.now(timezone.utc)
        to_time = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        from_time = self._from_time or (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "fields": ACTIVITY_QUERY_FIELDS,
            "page": 1,
            "pageSize": 500,
            "sortField": "startDatetime",
            "sortOrder": "DESC",
            "filters": {
                "fromTime": from_time,
                "toTime": to_time,
            },
        }

        loop = asyncio.get_event_loop()

        def _do_query():
            if self.r1_client.ec_type == "MSP":
                return self.r1_client.post(
                    "/activities/query",
                    payload=payload,
                    override_tenant_id=self.tenant_id
                )
            else:
                return self.r1_client.post(
                    "/activities/query",
                    payload=payload
                )

        response = await loop.run_in_executor(None, _do_query)

        if not response.ok:
            raise RuntimeError(
                f"POST /activities/query failed: {response.status_code} - "
                f"{response.text[:200]}"
            )

        data = response.json()
        activities = data.get('data', [])
        total_count = data.get('totalCount', '?')

        logger.debug(
            f"[{_ts()}] Cycle #{cycle_id}: Bulk query returned "
            f"{len(activities)} activities (totalCount={total_count}, "
            f"fromTime={from_time})"
        )

        # Build lookup dict by requestId, only for activities we're tracking
        pending_set = set(activity_ids)
        result = {}
        for activity in activities:
            req_id = activity.get('requestId')
            if req_id and req_id in pending_set:
                result[req_id] = activity

        if result:
            logger.debug(
                f"[{_ts()}] Cycle #{cycle_id}: Matched {len(result)}/{len(activity_ids)} "
                f"pending activities from bulk query"
            )

        return result

    # =========================================================================
    # Individual GET Fallback
    # =========================================================================

    async def _poll_activities_individual(
        self,
        activity_ids: list[str],
        cycle_id: int = 0
    ) -> tuple[dict[str, dict], int]:
        """
        Fall back to concurrent individual GET /activities/{id} requests.

        Returns (results_dict, error_count).
        """
        logger.debug(
            f"[{_ts()}] Cycle #{cycle_id}: Polling {len(activity_ids)} activities "
            f"via individual GETs"
        )

        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACTIVITY_POLLS)

        async def fetch_one(activity_id: str):
            async with semaphore:
                try:
                    return activity_id, await loop.run_in_executor(
                        None,
                        self._fetch_activity_sync,
                        activity_id
                    )
                except Exception as e:
                    logger.debug(f"[{_ts()}] Failed to fetch {activity_id[:8]}: {e}")
                    return activity_id, None

        tasks = [fetch_one(aid) for aid in activity_ids]
        fetch_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        errors = 0
        for item in fetch_results:
            if isinstance(item, Exception):
                errors += 1
                continue
            activity_id, data = item
            if data:
                results[activity_id] = data
            else:
                errors += 1

        return results, errors

    def _fetch_activity_sync(self, activity_id: str) -> dict | None:
        """
        Synchronously fetch a single activity via GET /activities/{id}.
        Runs in thread pool executor.
        """
        try:
            response = self.r1_client.get(
                f"/activities/{activity_id}",
                override_tenant_id=self.tenant_id
            )
            if response.ok:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                logger.debug(
                    f"Activity {activity_id[:8]} fetch failed: {response.status_code}"
                )
                return None
        except Exception as e:
            logger.debug(f"Activity {activity_id[:8]} fetch error: {e}")
            return None

    # =========================================================================
    # Result Processing
    # =========================================================================

    async def _process_activity_result(
        self,
        activity_id: str,
        data: dict
    ) -> None:
        """Process a single activity result."""
        if not data:
            return

        status = data.get("status", "").upper()

        # Log status — always for terminal/unusual, periodically for in-progress
        ref = self._pending.get(activity_id)
        if status not in ("INPROGRESS", "IN_PROGRESS", "PENDING", "RUNNING"):
            logger.debug(f"[{_ts()}] Activity {activity_id[:8]}... status={status}")
        elif ref and self._poll_cycle % 30 == 0:
            age = int((datetime.utcnow() - ref.registered_at).total_seconds())
            logger.info(
                f"[{_ts()}] Activity {activity_id[:8]}... still {status} "
                f"after {age}s (unit={ref.unit_id}, phase={ref.phase_id})"
            )

        if status in ("SUCCESS", "COMPLETED", "COMPLETE", "DONE"):
            await self._handle_completion(
                activity_id,
                success=True,
                resource_id=data.get("resourceId"),
                raw_response=data
            )
        elif status in ("FAIL", "FAILED", "ERROR", "FAILURE"):
            error_msg = (
                data.get("errorMessage")
                or data.get("error")
                or data.get("message")
                or f"Activity failed with status: {status}"
            )
            await self._handle_completion(
                activity_id,
                success=False,
                error=error_msg,
                raw_response=data
            )

    async def _handle_completion(
        self,
        activity_id: str,
        success: bool,
        resource_id: str = None,
        error: str = None,
        raw_response: Dict = None
    ) -> None:
        """Handle an activity completing."""
        ref = self._pending.get(activity_id)
        if not ref:
            return

        result = ActivityResult(
            activity_id=activity_id,
            success=success,
            resource_id=resource_id,
            error=error,
            raw_response=raw_response or {},
            completed_at=datetime.utcnow()
        )

        self._results[activity_id] = result
        self._total_completed += 1

        # Signal waiting coroutine
        if activity_id in self._events:
            self._events[activity_id].set()

        # Clean up from pending
        self._cleanup_activity(activity_id)

        # Remove from Redis
        if ref.job_id:
            await self.state.complete_activity(activity_id, ref.job_id)

        # Publish event
        await self.state.publish_event(
            ref.job_id,
            "activity_completed",
            {
                "activity_id": activity_id,
                "unit_id": ref.unit_id,
                "phase_id": ref.phase_id,
                "success": success,
                "resource_id": resource_id,
                "error": error,
            }
        )

        level = "info" if success else "warning"
        getattr(logger, level)(
            f"[{_ts()}] Activity {activity_id[:8]}... {'completed' if success else 'failed'} "
            f"(job={ref.job_id[:8] if ref.job_id else 'N/A'}..., unit={ref.unit_id}, phase={ref.phase_id})"
        )

    def _cleanup_activity(self, activity_id: str) -> None:
        """Remove activity from pending tracking."""
        self._pending.pop(activity_id, None)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the tracker (restores pending activities from Redis)."""
        pending = await self.state.get_pending_activities()
        for activity_id, ref in pending.items():
            self._pending[activity_id] = ref
            self._events[activity_id] = asyncio.Event()

        if self._pending:
            # Set fromTime based on earliest restored activity
            earliest = min(ref.registered_at for ref in self._pending.values())
            buffer = earliest - timedelta(seconds=30)
            self._from_time = buffer.strftime("%Y-%m-%dT%H:%M:%SZ")

            logger.info(
                f"ActivityTracker restored {len(self._pending)} "
                f"pending activities from Redis (fromTime={self._from_time})"
            )
            self._ensure_polling()

    async def stop(self) -> None:
        """Stop the tracker gracefully."""
        self._stop_event.set()
        if self._poll_task and not self._poll_task.done():
            try:
                await asyncio.wait_for(self._poll_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._poll_task.cancel()
        logger.info("ActivityTracker stopped")
