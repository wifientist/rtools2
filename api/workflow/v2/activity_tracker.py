"""
Centralized Activity Tracker

Manages bulk polling of R1 async activities across all phases and units.

Features:
- Single background poller for ALL pending activities across all jobs
- 2s polling interval, drops to 1s when < 10 activities remain
- Redis-backed for multi-worker visibility
- asyncio.Event-based notifications for phase executors waiting on results
- Circuit breaker: stops polling after 10 consecutive failures

R1 API Limitation:
    The R1 API does NOT support bulk activity queries. The POST /activities/query
    endpoint returns 500 "bad SQL grammar" when using the 'id.in' filter.

    WORKAROUND: Instead of bulk POST, we use concurrent individual GET calls
    via asyncio.gather(). Each activity is fetched individually with
    GET /activities/{id}. This is still efficient due to parallelism.

    Future: If R1 adds bulk query support, set BULK_QUERY_ENABLED = True
    and implement _poll_activities_bulk().

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
import uuid
from typing import Dict, Optional
from datetime import datetime

from workflow.v2.models import ActivityRef, ActivityResult
from workflow.v2.state_manager import RedisStateManagerV2

logger = logging.getLogger(__name__)


def _ts() -> str:
    """Return current timestamp for logging."""
    return datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]

# Polling intervals
POLL_INTERVAL_DEFAULT = 2.0    # Seconds between polls
POLL_INTERVAL_FAST = 1.0       # When < 10 activities remaining
FAST_THRESHOLD = 10            # Switch to fast interval below this count

# Max time to track a single activity before timeout
ACTIVITY_TIMEOUT_SECONDS = 300  # 5 minutes

# Error handling
MAX_CONSECUTIVE_ERRORS = 10    # Stop polling after this many consecutive failures
BULK_QUERY_ENABLED = False     # POST /activities/query not supported by R1 (returns 500)


class ActivityTracker:
    """
    Centralized tracker for R1 async activities.

    Collects activities from all phases across all jobs, polls them
    in bulk, and notifies waiting coroutines on completion.
    """

    def __init__(
        self,
        r1_client,
        state_manager: RedisStateManagerV2,
        tenant_id: str = None
    ):
        """
        Args:
            r1_client: RuckusONE API client (with await_task_completion methods)
            state_manager: Redis state manager for multi-worker persistence
            tenant_id: Default tenant ID for R1 API calls
        """
        self.r1_client = r1_client
        self.state = state_manager
        self.tenant_id = tenant_id

        # In-memory tracking (per-worker)
        self._pending: Dict[str, ActivityRef] = {}        # activity_id → ref
        self._events: Dict[str, asyncio.Event] = {}       # activity_id → completion event
        self._results: Dict[str, ActivityResult] = {}      # activity_id → result

        # Background poller control
        self._polling = False
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._poll_cycle = 0

        # Stats
        self._total_registered = 0
        self._total_completed = 0
        self._consecutive_errors = 0

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

        Args:
            activity_id: The activity to wait for
            timeout: Max seconds to wait (default 5 minutes)

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
            # Create a timeout result
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
        """
        Wait for multiple activities to complete.

        Returns:
            Dict of activity_id → ActivityResult
        """
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
                # Find which activity completed
                for aid, task in pending_events.items():
                    if task in done:
                        return self._results.get(aid)
        except Exception as e:
            logger.warning(f"Error in wait_for_any: {e}")
        finally:
            # Cancel remaining futures
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
        }

    # =========================================================================
    # Background Polling
    # =========================================================================

    def _ensure_polling(self) -> None:
        """Start the background polling loop if not already running."""
        if not self._polling:
            self._polling = True  # Set BEFORE creating task to prevent race condition
            self._stop_event.clear()
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """
        Background loop that polls R1 for all pending activities.

        Uses bulk polling via the R1 client to check all activities at once.
        Polls at 2s intervals, dropping to 1s when < 10 activities remain.
        """
        self._polling = True
        self._poll_cycle = 0
        logger.info(f"[{_ts()}] ActivityTracker poll loop started")

        try:
            while self._pending and not self._stop_event.is_set():
                self._poll_cycle += 1
                cycle_id = self._poll_cycle

                # Determine poll interval
                pending_count = len(self._pending)
                interval = (
                    POLL_INTERVAL_FAST
                    if pending_count < FAST_THRESHOLD
                    else POLL_INTERVAL_DEFAULT
                )

                activity_ids = list(self._pending.keys())
                logger.debug(
                    f"[{_ts()}] POLL CYCLE #{cycle_id}: {pending_count} activities "
                    f"(interval={interval}s) ids={[a[:8] for a in activity_ids]}"
                )

                try:
                    await self._poll_activities(activity_ids, cycle_id)
                except Exception as e:
                    logger.error(f"[{_ts()}] Poll cycle #{cycle_id} error: {e}")

                # Wait for interval or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=interval
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
        Poll all pending activities using concurrent individual GET requests.

        Note: POST /activities/query bulk endpoint is not supported by R1 API
        (returns 500 "bad SQL grammar" for id.in filter). Instead, we use
        concurrent individual GET /activities/{id} calls via thread pool.

        This is still efficient due to concurrency - all requests go out in parallel.
        """
        if not activity_ids:
            return

        logger.debug(
            f"[{_ts()}] Cycle #{cycle_id}: Polling {len(activity_ids)} activities "
            f"(concurrent GET calls)"
        )

        # Use concurrent individual GET calls (bulk POST not supported by R1)
        loop = asyncio.get_event_loop()
        results = {}
        errors = 0

        # Fetch all activities concurrently using thread pool
        async def fetch_one(activity_id: str):
            try:
                return activity_id, await loop.run_in_executor(
                    None,
                    self._fetch_activity_sync,
                    activity_id
                )
            except Exception as e:
                logger.debug(f"[{_ts()}] Failed to fetch {activity_id[:8]}: {e}")
                return activity_id, None

        # Run all fetches concurrently
        tasks = [fetch_one(aid) for aid in activity_ids]
        fetch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for item in fetch_results:
            if isinstance(item, Exception):
                errors += 1
                continue
            activity_id, data = item
            if data:
                results[activity_id] = data
            else:
                errors += 1

        # Track consecutive errors for circuit breaker
        if errors == len(activity_ids) and len(activity_ids) > 0:
            self._consecutive_errors += 1
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    f"[{_ts()}] Cycle #{cycle_id}: {MAX_CONSECUTIVE_ERRORS} consecutive "
                    f"poll failures - activities may have timed out"
                )
                # Mark all as failed to prevent infinite loop
                for activity_id in activity_ids:
                    await self._handle_completion(
                        activity_id,
                        success=False,
                        error=f"Activity polling failed after {MAX_CONSECUTIVE_ERRORS} attempts"
                    )
                return
        else:
            self._consecutive_errors = 0

        logger.debug(
            f"[{_ts()}] Cycle #{cycle_id}: Got {len(results)}/{len(activity_ids)} activities"
        )

        # Process all results
        for activity_id in activity_ids:
            data = results.get(activity_id)
            if data:
                await self._process_activity_result(activity_id, data)
            # If not in results, activity doesn't exist yet - will check next cycle

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
                # Activity not yet created - this is normal, will appear soon
                return None
            else:
                logger.debug(
                    f"Activity {activity_id[:8]} fetch failed: {response.status_code}"
                )
                return None
        except Exception as e:
            logger.debug(f"Activity {activity_id[:8]} fetch error: {e}")
            return None

    async def _process_activity_result(
        self,
        activity_id: str,
        data: dict
    ) -> None:
        """Process a single activity result from GET /activities/{id}."""
        if not data:
            return  # Empty/missing, will check next cycle

        # Check if activity is complete
        status = data.get("status", "").upper()

        # Debug log the actual status for diagnosis
        # R1 statuses: PENDING, INPROGRESS, SUCCESS, FAIL
        if status not in ("INPROGRESS", "IN_PROGRESS", "PENDING", "RUNNING"):
            logger.debug(f"[{_ts()}] Activity {activity_id[:8]}... status={status}")

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
        # else: still pending (IN_PROGRESS, PENDING, etc.) - check next cycle

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
        # Keep the event and result around for waiters
        # They'll be garbage collected when no one holds a reference

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the tracker (restores pending activities from Redis)."""
        # Reload pending activities from Redis (for worker restart)
        pending = await self.state.get_pending_activities()
        for activity_id, ref in pending.items():
            self._pending[activity_id] = ref
            self._events[activity_id] = asyncio.Event()

        if self._pending:
            logger.info(
                f"ActivityTracker restored {len(self._pending)} "
                f"pending activities from Redis"
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
