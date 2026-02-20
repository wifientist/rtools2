"""
Workflow Brain

Central orchestrator for V2 workflow execution.

The Brain:
- Builds and validates the dependency graph
- Runs Phase 0 validation (dry-run) and pauses for confirmation
- Executes phases with per-unit parallelism
- Wires phase outputs to inputs via UnitMapping
- Coordinates with ActivityTracker for bulk R1 polling
- Tracks all progress in Redis for multi-worker support
- Publishes events for frontend real-time updates

Key design: per-unit parallel pipelines.
Unit 1's Phase 3 can start as soon as Unit 1's Phase 2 completes,
even if Unit 50's Phase 2 hasn't started yet.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, Any, Optional, List, Set, Tuple, TYPE_CHECKING
from datetime import datetime

from workflow.v2.models import (
    WorkflowJobV2,
    UnitMapping,
    UnitStatus,
    JobStatus,
    PhaseStatus,
    PhaseResult,
    PhaseDefinitionV2,
)
from workflow.v2.graph import DependencyGraph
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.activity_tracker import ActivityTracker

if TYPE_CHECKING:
    from workflow.phases.phase_executor import PhaseContext, PhaseExecutor
    from workflow.workflows.definition import Workflow

logger = logging.getLogger(__name__)

# How long to wait between checking for ready work
SCHEDULE_INTERVAL = 0.25  # seconds

# Concurrency control: limit parallel phase executions to prevent Redis connection exhaustion
# With 50 units, unbounded parallelism can spawn 50+ concurrent tasks, each doing 3-5 Redis ops
# This semaphore ensures we don't exceed Redis connection pool capacity
MAX_CONCURRENT_PHASE_TASKS = 20

# R1's SSID-per-AP-Group limit and safety buffer
# When an SSID is activated with isAllApGroups=True, it consumes a slot in EVERY AP Group.
# We track the actual count of venue-wide SSIDs and block new activations when near the limit.
# The live R1 reconciliation (every ~15s) self-corrects any counter drift, so a
# minimal safety buffer of 1 is sufficient while maximizing throughput.
SSID_LIMIT_PER_AP_GROUP = 15
SSID_SAFETY_BUFFER = 1
DEFAULT_VENUE_WIDE_LIMIT = SSID_LIMIT_PER_AP_GROUP - SSID_SAFETY_BUFFER  # 14

# Phase execution timeout (seconds) - prevents indefinite hangs on stuck API calls
# The sequential 3-step SSID config can take 3+ minutes (3 steps × ~60s polling each)
# plus AP assignment time. Set high enough to allow these legitimate slow operations.
PHASE_EXECUTION_TIMEOUT = 600  # 10 minutes (per-unit phases)

# Global phase timeout (seconds) - longer because global phases process many
# resources sequentially (e.g., deleting 274 WiFi networks at 5 concurrent).
# Per-resource: ~30-60s for deactivation + deletion + retry delays.
# 274 networks / 5 concurrent × 60s ≈ 55 min. Set to 1 hour.
GLOBAL_PHASE_EXECUTION_TIMEOUT = 3600  # 60 minutes

# Max time a unit waits with NO slot releases before failing.
# This is a STALE timeout, not an absolute deadline: each time a slot is
# released (notify_all), all waiters reset their timer. The timeout only
# fires when no progress is made for this duration, indicating R1 is stuck.
# Must exceed PHASE_EXECUTION_TIMEOUT since each slot is held for a full
# phase execution (up to 10 min for 3-step SSID config).
ACTIVATION_SLOT_STALE_TIMEOUT = PHASE_EXECUTION_TIMEOUT + 60  # 11 minutes

# Max times a failed unit can be requeued for retry. When a unit fails and its
# SSID is orphaned on venue-wide, we deactivate the SSID to free the R1 slot
# and re-add the unit to the back of the queue. Only allow 1 requeue to avoid
# infinite retry loops for genuinely broken units.
MAX_REQUEUE_ATTEMPTS = 1


class WorkflowBrain:
    """
    Central orchestrator for V2 workflow execution.

    Manages the full lifecycle: validate → confirm → execute → complete.
    """

    # Debounce interval for progress updates (seconds)
    PROGRESS_DEBOUNCE_INTERVAL = 1.0

    def __init__(
        self,
        state_manager: RedisStateManagerV2,
        activity_tracker: ActivityTracker,
        event_publisher: Any = None,
        r1_client: Any = None,
    ):
        self.state = state_manager
        self.tracker = activity_tracker
        self.events = event_publisher
        self.r1_client = r1_client
        self._last_progress_time: float = 0
        self._phase_semaphore: Optional[asyncio.Semaphore] = None

        # Venue-wide SSID activation gating for R1's 15-SSID-per-AP-Group limit.
        # Counter ONLY tracks our NEW activations (Scenario C units).
        # Existing venue-wide SSIDs are accounted for in the limit, not the count.
        # This prevents deadlocks when existing SSIDs can't be drained.
        self._venue_wide_count: int = 0  # New activations currently in-flight
        self._venue_wide_limit: int = DEFAULT_VENUE_WIDE_LIMIT  # Overridden at execution time
        self._venue_wide_condition: Optional[asyncio.Condition] = None
        self._activation_slots: Set[str] = set()  # unit_ids that did a NEW activation
        # Last known R1 venue-wide count (updated every reconcile cycle)
        self._last_r1_venue_wide: Optional[int] = None
        # Count of units currently waiting for an activation slot
        self._units_waiting_count: int = 0
        # Track how many times each unit has been requeued after failure
        self._requeue_counts: Dict[str, int] = {}

    # =========================================================================
    # Job Creation
    # =========================================================================

    async def create_job(
        self,
        workflow: Workflow,
        venue_id: str,
        tenant_id: str,
        controller_id: int,
        user_id: int,
        options: Dict[str, Any] = None,
        input_data: Dict[str, Any] = None,
    ) -> WorkflowJobV2:
        """
        Create a new workflow job in PENDING state.

        Args:
            workflow: Workflow definition
            venue_id: Target venue
            tenant_id: Tenant/EC ID
            controller_id: Controller ID
            user_id: User who initiated
            options: Workflow-specific options
            input_data: Original request payload

        Returns:
            Created WorkflowJobV2 (saved to Redis)
        """
        # Validate the workflow definition
        errors = workflow.validate_definition()
        if errors:
            raise ValueError(f"Invalid workflow definition: {errors}")

        # Merge options: workflow defaults < user options
        merged_options = {**(workflow.default_options or {}), **(options or {})}

        job = WorkflowJobV2(
            id=str(uuid.uuid4()),
            workflow_name=workflow.name,
            status=JobStatus.PENDING,
            venue_id=venue_id,
            tenant_id=tenant_id,
            controller_id=controller_id,
            user_id=user_id,
            options=merged_options,
            input_data=input_data or {},
            phase_definitions=workflow.get_phase_definitions(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        # Pre-mark skipped phases based on options
        self._mark_skipped_phases(job)

        await self.state.save_job(job)
        logger.info(f"Created job {job.id} for workflow '{workflow.name}'")

        return job

    # =========================================================================
    # Validation (Phase 0 / Dry-Run)
    # =========================================================================

    async def run_validation(self, job: WorkflowJobV2) -> WorkflowJobV2:
        """
        Run Phase 0 validation (dry-run).

        Finds the root phase (first phase with no dependencies) and
        executes it. This is the validate/inventory/plan phase that:
        1. Pre-computes all unit mappings
        2. Checks existing resources
        3. Builds the execution plan
        4. Returns validation results

        After validation, job moves to AWAITING_CONFIRMATION.

        Args:
            job: Job to validate

        Returns:
            Updated job with validation_result populated
        """
        job.status = JobStatus.VALIDATING
        await self.state.save_job(job)

        # Find the root phase (Phase 0) — first phase with no dependencies
        validate_phase = None
        for phase_def in job.phase_definitions:
            if not phase_def.depends_on:
                validate_phase = phase_def
                break

        if not validate_phase:
            # No root phase - auto-confirm
            logger.info(f"Job {job.id}: No root phase, skipping validation")
            job.status = JobStatus.AWAITING_CONFIRMATION
            await self.state.save_job(job)
            return job

        phase_id = validate_phase.id
        logger.info(f"Job {job.id}: Running Phase 0 '{phase_id}'")

        try:
            # Execute validation phase
            phase_class = self._resolve_phase_class(phase_id, validate_phase)
            context = self._build_context(job)
            executor = phase_class(context)

            # Build validation inputs from job input_data + options
            input_kwargs = {**job.input_data}
            # Merge options so phases like inventory can access nuclear_mode etc.
            if job.options:
                for k, v in job.options.items():
                    if k not in input_kwargs:
                        input_kwargs[k] = v
            inputs = executor.Inputs(**input_kwargs)
            result = await executor.execute(inputs)

            # Store validation result and unit mappings
            if hasattr(result, 'unit_mappings') and result.unit_mappings:
                job.units = result.unit_mappings
                # Also persist units individually
                await self.state.save_all_units(job.id, job.units)

            if hasattr(result, 'validation_result'):
                job.validation_result = result.validation_result
            elif hasattr(result, 'valid'):
                job.validation_result = result

            # Store remaining outputs in global_phase_results for downstream access
            # (e.g., all_venue_aps for assign_aps, inventory for delete phases)
            output_dict = result.model_dump() if hasattr(result, 'model_dump') else {}
            # Remove specially-handled fields to avoid duplication
            output_dict.pop('unit_mappings', None)
            output_dict.pop('validation_result', None)
            job.global_phase_results[phase_id] = output_dict

            logger.debug(
                f"Job {job.id}: Storing phase '{phase_id}' outputs, "
                f"keys: {list(output_dict.keys())}"
            )

            # Update global phase status
            job.global_phase_status[phase_id] = PhaseStatus.COMPLETED

            # Move to awaiting confirmation
            job.status = JobStatus.AWAITING_CONFIRMATION

            logger.debug(
                f"Job {job.id}: Before save - global_phase_results keys: "
                f"{list(job.global_phase_results.keys())}"
            )
            await self.state.save_job(job)

            # Verify save worked
            verify_job = await self.state.get_job(job.id)
            if verify_job:
                logger.debug(
                    f"Job {job.id}: After save - global_phase_results keys: "
                    f"{list(verify_job.global_phase_results.keys())}"
                )

            await self._publish_event(job.id, "validation_complete", {
                "valid": job.validation_result.valid if job.validation_result else True,
                "unit_count": len(job.units),
            })

            logger.info(
                f"Job {job.id}: Phase 0 '{phase_id}' complete "
                f"({len(job.units)} units, "
                f"valid={job.validation_result.valid if job.validation_result else True})"
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Job {job.id}: Phase 0 '{phase_id}' failed: {e}")
            job.status = JobStatus.FAILED
            job.errors.append(f"Validation failed: {error_msg}")
            await self.state.save_job(job)

            # Publish event so frontend knows validation failed
            await self._publish_event(job.id, "validation_failed", {
                "phase_id": phase_id,
                "error": error_msg,
            })

        return job

    # =========================================================================
    # Main Execution Loop
    # =========================================================================

    async def execute_workflow(self, job: WorkflowJobV2) -> WorkflowJobV2:
        """
        Execute a workflow with per-unit parallelism.

        This is the main execution entry point called after confirmation.
        It continuously finds and executes ready work until all phases
        are complete or a critical failure occurs.

        Args:
            job: Confirmed job with units populated

        Returns:
            Completed job
        """
        logger.info(
            f"Starting workflow '{job.workflow_name}' "
            f"(job={job.id}, units={len(job.units)})"
        )

        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        await self.state.save_job(job)
        await self._publish_event(job.id, "workflow_started", {})
        last_heartbeat = time.time()

        # Build dependency graph from all phases
        # (Phase 0 is already in global_phase_status, so it won't re-run)
        graph = DependencyGraph(job.phase_definitions)

        # Initialize concurrency control semaphore
        # This prevents Redis connection pool exhaustion with large unit counts
        self._phase_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PHASE_TASKS)

        # Initialize venue-wide SSID activation gating for R1's 15-SSID-per-AP-Group limit.
        #
        # Simple formula: limit = counter + available_new
        #   available_new = max(0, 15 - actual_venue_wide - buffer)
        #
        # This directly uses R1's actual state. No baseline estimation needed.
        # Counter tracks our NEW activations. Available is how many more R1 can take.
        # As recovery units (already-venue-wide) complete 3-step config and free
        # R1 capacity, available_new increases and the limit goes up.
        validate_results = (
            job.global_phase_results.get('validate_and_plan', {})
            or job.global_phase_results.get('validate', {})
        )
        existing_venue_wide = validate_results.get('venue_wide_ssid_count', 0)
        self._venue_wide_count = 0  # Only tracks OUR new activations
        available_new = max(0, SSID_LIMIT_PER_AP_GROUP - existing_venue_wide - SSID_SAFETY_BUFFER)
        self._venue_wide_limit = max(1, min(available_new, DEFAULT_VENUE_WIDE_LIMIT))
        self._venue_wide_condition = asyncio.Condition()
        self._activation_slots = set()

        logger.info(
            f"Job {job.id}: [SSID-GATE] Initialized: "
            f"venue_wide={existing_venue_wide}/{SSID_LIMIT_PER_AP_GROUP}, "
            f"available_new={available_new}, limit={self._venue_wide_limit} "
            f"(buffer={SSID_SAFETY_BUFFER})"
        )

        # Pre-flight: verify against ACTUAL R1 state.
        # Validation data may be stale if user waited before confirming.
        await self._reconcile_venue_wide_limit(job)

        # Pre-complete phases whose outcomes are already determined by
        # validation data (pre-resolved IDs, no APs, already activated).
        # This immediately reflects in progress stats and avoids scheduling
        # overhead for no-op phase executions.
        await self._pre_complete_resolved_phases(job)

        # Track in-flight work
        in_flight: Dict[str, asyncio.Task] = {}  # "unit:phase" → task
        last_reconcile = time.time()

        try:
            while not await self._is_workflow_complete(job, graph):
                # Check for cancellation
                if await self.state.is_cancelled(job.id):
                    logger.info(f"Job {job.id}: Cancelled by user")
                    job.status = JobStatus.CANCELLED
                    job.errors.append("Cancelled by user")
                    break

                # Find all ready work
                ready_work = self._find_ready_work(job, graph)

                # Launch ready work that isn't already in flight
                # Use semaphore to limit concurrent phase executions
                for unit_id, phase_id in ready_work:
                    key = f"{unit_id}:{phase_id}"
                    if key not in in_flight:
                        task = asyncio.create_task(
                            self._execute_phase_with_limit(job, unit_id, phase_id)
                        )
                        in_flight[key] = task

                # Also check for ready global phases
                ready_global = self._find_ready_global_phases(job, graph)
                for phase_id in ready_global:
                    key = f"global:{phase_id}"
                    if key not in in_flight:
                        task = asyncio.create_task(
                            self._execute_global_phase_with_limit(job, phase_id)
                        )
                        in_flight[key] = task

                if not in_flight:
                    # No work in flight and nothing ready - shouldn't happen
                    # unless all work is done or blocked
                    await asyncio.sleep(SCHEDULE_INTERVAL)
                    continue

                # Wait for at least one task to complete
                done, _ = await asyncio.wait(
                    in_flight.values(),
                    timeout=SCHEDULE_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Process completed tasks
                completed_keys = []
                for key, task in in_flight.items():
                    if task.done():
                        completed_keys.append(key)
                        try:
                            result = await task
                            await self._handle_phase_result(job, result, graph)
                        except Exception as e:
                            logger.error(f"Job {job.id}: Phase task error: {e}")
                            # Task raised an exception instead of returning a
                            # PhaseResult.  This can happen when:
                            #   - Activation slot wait times out
                            #   - Phase execution times out
                            # Mark the unit/phase as failed so it doesn't stay RUNNING.
                            parts = key.split(':')

                            # Global phase failure — mark as FAILED so
                            # downstream phases don't wait forever.
                            if len(parts) == 2 and parts[0] == "global":
                                global_phase_id = parts[1]
                                error_msg = str(e)
                                await self.state.update_global_phase_status(
                                    job.id, global_phase_id, PhaseStatus.FAILED
                                )
                                job.global_phase_status[global_phase_id] = PhaseStatus.FAILED
                                job.errors.append(
                                    f"Phase '{global_phase_id}' failed: {error_msg}"
                                )
                                await self._publish_event(job.id, "phase_failed", {
                                    "phase_id": global_phase_id,
                                    "error": error_msg,
                                })
                                logger.error(
                                    f"Job {job.id}: Global phase '{global_phase_id}' "
                                    f"failed: {error_msg}"
                                )

                            # Per-unit phase failure
                            elif len(parts) == 2 and parts[0] != "global":
                                unit_id_err, phase_id_err = parts
                                unit_err = job.units.get(unit_id_err)
                                if unit_err and unit_err.status != UnitStatus.FAILED:
                                    # Release leaked activation slot if the exception
                                    # bypassed normal slot release (e.g. phase timeout)
                                    if unit_id_err in self._activation_slots:
                                        self._activation_slots.discard(unit_id_err)
                                        async with self._venue_wide_condition:
                                            self._venue_wide_count -= 1
                                            self._venue_wide_condition.notify_all()

                                    # Try deactivate-and-requeue before marking failed.
                                    # Works for both new activations (slot just released
                                    # above) and recovery units (no slot held).
                                    requeued = await self._try_deactivate_and_requeue(
                                        job, unit_id_err, phase_id_err, None
                                    )
                                    if requeued:
                                        continue  # Unit reset; skip failure marking

                                    error_msg = str(e)
                                    await self.state.update_unit_phase_status(
                                        job.id, unit_id_err, phase_id_err,
                                        failed=True, error=error_msg
                                    )
                                    unit_err.status = UnitStatus.FAILED
                                    await self.state.save_unit(job.id, unit_err)
                                    await self._publish_event(job.id, "unit_completed", {
                                        "unit_id": unit_id_err,
                                        "unit_number": unit_err.unit_number,
                                        "phases_completed": len(unit_err.completed_phases),
                                        "phases_failed": len(unit_err.failed_phases) + 1,
                                        "success": False,
                                    })

                for key in completed_keys:
                    del in_flight[key]

                # Refresh global job metadata from Redis (other workers may
                # have updated global_phase_status, created_resources, etc.)
                # Uses get_job_metadata() to avoid reloading all 261+ units —
                # in-memory units are already kept in sync by Fix 1 (each
                # update_unit_phase_status call applies the result to job.units).
                refreshed = await self.state.get_job_metadata(job.id)
                if refreshed:
                    job.global_phase_status = refreshed.global_phase_status
                    job.global_phase_results = refreshed.global_phase_results
                    job.created_resources = refreshed.created_resources
                    job.errors = refreshed.errors

                # Periodic heartbeat (every 15 seconds) - ensures frontend
                # sees progress even during long-running phases like 3-step config
                now_ts = time.time()
                if now_ts - last_heartbeat >= 15 and in_flight:
                    last_heartbeat = now_ts
                    await self._log_diagnostic_summary(job, in_flight)

                # SSID gate reconciliation (every 30s when activate_network
                # phases are in-flight — either new activations or recovery
                # 3-step configs). Each call fetches all WiFi networks from
                # R1 to count venue-wide SSIDs, so keep it infrequent.
                has_activation_work = any(
                    ':activate_network' in key for key in in_flight
                )
                if has_activation_work and now_ts - last_reconcile >= 30:
                    last_reconcile = now_ts
                    await self._reconcile_venue_wide_limit(job)

        except Exception as e:
            logger.error(f"Job {job.id}: Workflow execution error: {e}")
            job.status = JobStatus.FAILED
            job.errors.append(f"Execution error: {str(e)}")

        finally:
            # Cancel any remaining in-flight tasks
            for key, task in in_flight.items():
                if not task.done():
                    task.cancel()

        # Determine final status
        job = self._determine_final_status(job)
        job.completed_at = datetime.utcnow()
        await self.state.save_job(job)

        # Emit unit_completed for successful units
        # (Failed units already had unit_completed emitted when they failed)
        for unit in job.units.values():
            if unit.status == UnitStatus.COMPLETED:
                await self._publish_event(job.id, "unit_completed", {
                    "unit_id": unit.unit_id,
                    "unit_number": unit.unit_number,
                    "phases_completed": len(unit.completed_phases),
                    "phases_failed": 0,
                    "success": True,
                })

        await self._publish_event(job.id, "workflow_completed", {
            "status": job.status.value,
            "progress": job.get_progress(),
        })

        logger.info(
            f"Workflow '{job.workflow_name}' completed: {job.status.value} "
            f"(job={job.id})"
        )

        # Log failure summary for debugging
        if job.status in (JobStatus.PARTIAL, JobStatus.FAILED):
            await self._log_failure_summary(job)

        return job

    async def _log_failure_summary(self, job: WorkflowJobV2) -> None:
        """Log a summary of failures to help debug issues."""
        total_units = len(job.units)
        failed_units = [u for u in job.units.values() if u.status == UnitStatus.FAILED]

        logger.warning(
            f"Job {job.id} FAILURE SUMMARY: {len(failed_units)}/{total_units} units failed"
        )

        # Group failures by phase
        failures_by_phase: Dict[str, List[str]] = {}
        for unit in failed_units:
            for phase_id in unit.failed_phases:
                if phase_id not in failures_by_phase:
                    failures_by_phase[phase_id] = []
                failures_by_phase[phase_id].append(unit.unit_id)

        for phase_id, unit_ids in sorted(failures_by_phase.items()):
            logger.warning(
                f"Job {job.id} FAILURE SUMMARY: Phase '{phase_id}' failed for "
                f"{len(unit_ids)} units: {unit_ids[:5]}{'...' if len(unit_ids) > 5 else ''}"
            )

        # Log first few error messages
        error_samples: Dict[str, str] = {}
        for unit in failed_units[:5]:
            if hasattr(unit, 'phase_results'):
                for phase_id, result in unit.phase_results.items():
                    if hasattr(result, 'error') and result.error:
                        if phase_id not in error_samples:
                            error_samples[phase_id] = result.error
                            break

        for phase_id, error in error_samples.items():
            logger.warning(
                f"Job {job.id} FAILURE SAMPLE: Phase '{phase_id}' error: {error[:200]}"
            )

    # =========================================================================
    # Pre-completion of Resolved Phases
    # =========================================================================

    async def _pre_complete_resolved_phases(self, job: WorkflowJobV2) -> None:
        """
        Pre-complete phases whose outcomes are already determined by validation.

        After validation, many units already have resolved IDs (AP group exists,
        network exists, already activated, no APs to assign). Instead of
        scheduling these as real phase executions (261 units × 4 phases =
        1000+ async tasks through the semaphore), mark them complete immediately.

        This gives instant progress stats and frees the scheduling loop to
        focus on phases that actually need R1 API calls.
        """
        pre_completed: Dict[str, int] = {}  # phase_id → count

        for unit_id, unit in job.units.items():
            phases_added = []

            # create_ap_group: validation already found the AP group
            if (
                unit.resolved.ap_group_id
                and "create_ap_group" not in unit.completed_phases
            ):
                unit.completed_phases.append("create_ap_group")
                phases_added.append("create_ap_group")

            # create_psk_network: validation already found the network
            if (
                unit.resolved.network_id
                and "create_psk_network" not in unit.completed_phases
            ):
                unit.completed_phases.append("create_psk_network")
                phases_added.append("create_psk_network")

            # assign_aps: no APs to assign for this unit
            if (
                not unit.plan.ap_serial_numbers
                and "assign_aps" not in unit.completed_phases
            ):
                unit.completed_phases.append("assign_aps")
                phases_added.append("assign_aps")

            # activate_network: already activated on a specific AP group
            # (Fast Path A in activate_network.py — nothing to do at all)
            # Note: is_venue_wide=True still needs the 3-step config, so skip those.
            if (
                unit.input_config.get("already_activated")
                and not unit.input_config.get("is_venue_wide")
                and "activate_network" not in unit.completed_phases
            ):
                unit.completed_phases.append("activate_network")
                # Apply the outputs that activate_network would have produced
                unit.resolved.activated = True
                unit.resolved.already_active = True
                phases_added.append("activate_network")

            # configure_lan_ports: skipped when LAN port config is disabled
            # (matches skip_if="not options.get('configure_lan_ports', False)")
            if (
                not job.options.get("configure_lan_ports", False)
                and "configure_lan_ports" not in unit.completed_phases
            ):
                unit.completed_phases.append("configure_lan_ports")
                phases_added.append("configure_lan_ports")

            if phases_added:
                await self.state.save_unit(job.id, unit)
                for pid in phases_added:
                    pre_completed[pid] = pre_completed.get(pid, 0) + 1

        if pre_completed:
            total = sum(pre_completed.values())
            details = ", ".join(
                f"{pid}={count}" for pid, count in sorted(pre_completed.items())
            )
            logger.info(
                f"Job {job.id}: Pre-completed {total} phase executions "
                f"from validation data ({details})"
            )
            # Single progress event so frontend updates immediately
            await self._publish_event(job.id, "progress", {
                "progress": job.get_progress(),
            })

    # =========================================================================
    # Work Scheduling
    # =========================================================================

    def _find_ready_work(
        self,
        job: WorkflowJobV2,
        graph: DependencyGraph
    ) -> List[Tuple[str, str]]:
        """
        Find all (unit_id, phase_id) pairs ready to execute.

        A per-unit phase is ready when:
        1. All dependencies are satisfied FOR THAT UNIT
        2. The unit isn't already running a phase
        3. The phase hasn't already completed/failed for that unit

        Returns:
            List of (unit_id, phase_id) tuples
        """
        ready = []
        global_completed = {
            pid for pid, status in job.global_phase_status.items()
            if status == PhaseStatus.COMPLETED
        }

        for unit_id, unit in job.units.items():
            if unit.status == UnitStatus.FAILED:
                continue  # Skip failed units
            if unit.current_phase is not None:
                continue  # Unit is busy

            completed_set = set(unit.completed_phases)
            failed_set = set(unit.failed_phases)

            ready_phases = graph.get_ready_work_for_unit(
                unit_completed=completed_set,
                unit_current=unit.current_phase,
                global_completed=global_completed
            )

            # Filter out already completed/failed phases
            for phase_id in ready_phases:
                if phase_id not in completed_set and phase_id not in failed_set:
                    ready.append((unit_id, phase_id))

        return ready

    def _find_ready_global_phases(
        self,
        job: WorkflowJobV2,
        graph: DependencyGraph
    ) -> List[str]:
        """
        Find global phases (per_unit=False) that are ready to execute.

        Global phases can depend on:
        - Other global phases (must be in global_completed)
        - Per-unit phases (must be completed for ALL units)
        """
        ready = []
        global_completed = {
            pid for pid, status in job.global_phase_status.items()
            if status == PhaseStatus.COMPLETED
        }

        # Build set of per-unit phases that are complete for ALL units
        per_unit_completed_all = set()
        per_unit_phase_ids = {p.id for p in job.phase_definitions if p.per_unit}

        for phase_id in per_unit_phase_ids:
            # Check if all units have completed this phase
            all_complete = all(
                phase_id in unit.completed_phases
                for unit in job.units.values()
            )
            if all_complete:
                per_unit_completed_all.add(phase_id)

        for phase_def in job.phase_definitions:
            if phase_def.per_unit:
                continue  # Skip per-unit phases
            if phase_def.id in job.global_phase_status:
                continue  # Already started/completed/failed (incl. Phase 0)

            # Check dependencies - can be global OR per-unit (all units done)
            deps_met = all(
                dep in global_completed or dep in per_unit_completed_all
                for dep in phase_def.depends_on
            )
            if deps_met:
                ready.append(phase_def.id)

        return ready

    # =========================================================================
    # Phase Execution
    # =========================================================================

    async def _execute_phase_with_limit(
        self,
        job: WorkflowJobV2,
        unit_id: str,
        phase_id: str
    ) -> PhaseResult:
        """
        Execute a phase for a unit with concurrency limiting.

        Wraps _execute_phase_for_unit with semaphore to prevent
        Redis connection pool exhaustion.

        Also manages venue-wide SSID activation gating for R1's 15-SSID limit.
        The counter ONLY tracks our NEW activations (Scenario C).
        Existing SSIDs (Scenarios A & B) run freely without counter interaction.

        - Scenario A (already on specific group): no counter interaction
        - Scenario B (existing on All AP Groups): no counter interaction
        - Scenario C (new SSID): acquire increments, release decrements
        """
        phase_def = job.get_phase_definition(phase_id)
        activation_slot = getattr(phase_def, 'activation_slot', None) if phase_def else None

        # Three scenarios for activation slot handling:
        # A) Already on specific AP group → skip entirely (pre-completed)
        # B) Already on venue-wide (All AP Groups) → recovery path (3-step config
        #    with concurrency control to prevent R1 overload)
        # C) New SSID → acquire slot, wait for capacity, full activation
        needs_slot = False
        needs_recovery = False
        if activation_slot in ("acquire", "acquire_release"):
            unit = job.units.get(unit_id)
            if unit and unit.input_config.get('already_activated', False):
                if unit.input_config.get('is_venue_wide', False):
                    # Scenario B: stuck on venue-wide, needs 3-step config
                    needs_recovery = True
                    logger.info(
                        f"Job {job.id}: [SSID-GATE] {unit_id} RECOVERY "
                        f"(venue-wide → specific AP group, {self._ssid_gate_status()})"
                    )
                else:
                    # Scenario A: already on specific group, skip
                    logger.debug(
                        f"Job {job.id}: [SSID-GATE] {unit_id} SKIP "
                        f"(already on specific AP group)"
                    )
            else:
                needs_slot = True

        # Scenario C: wait for capacity before activating a NEW SSID.
        #
        # Uses a STALE timeout: each wait_for() call has a fresh timeout.
        # When a slot is released (notify_all), all waiters wake up — even if
        # they don't get the slot, the system is making progress, so they
        # loop back with a fresh timeout. The timeout only fires when NO
        # slot is released for ACTIVATION_SLOT_STALE_TIMEOUT seconds,
        # indicating R1 is genuinely stuck.
        #
        # This prevents the old cascade where all ~160 waiting units shared
        # an absolute deadline and all timed out simultaneously.
        if needs_slot and unit_id not in self._activation_slots:
            wait_start = asyncio.get_event_loop().time()
            self._units_waiting_count += 1
            # Only log at INFO when slots are available (imminent acquire).
            # When at capacity, use DEBUG to avoid flooding with 150+ identical lines.
            at_capacity = self._venue_wide_count >= self._venue_wide_limit
            log_fn = logger.debug if at_capacity else logger.info
            log_fn(
                f"Job {job.id}: [SSID-GATE] {unit_id} WAITING for slot "
                f"({self._ssid_gate_status()}, queued={self._units_waiting_count})"
            )
            try:
                async with self._venue_wide_condition:
                    while self._venue_wide_count >= self._venue_wide_limit:
                        try:
                            await asyncio.wait_for(
                                self._venue_wide_condition.wait(),
                                timeout=ACTIVATION_SLOT_STALE_TIMEOUT,
                            )
                            # Notification received — a slot was released somewhere.
                            # Loop back to re-check the condition with a fresh timeout.
                        except asyncio.TimeoutError:
                            elapsed = asyncio.get_event_loop().time() - wait_start
                            raise RuntimeError(
                                f"No activation slots released for "
                                f"{ACTIVATION_SLOT_STALE_TIMEOUT}s "
                                f"(waited {elapsed:.0f}s total, "
                                f"{self._ssid_gate_status()}). "
                                f"R1 activities may be stuck. "
                                f"This unit will be retried on the next run."
                            )
                    self._venue_wide_count += 1
                    self._activation_slots.add(unit_id)
            except:
                self._units_waiting_count -= 1
                raise
            self._units_waiting_count -= 1
            elapsed = asyncio.get_event_loop().time() - wait_start
            logger.info(
                f"Job {job.id}: [SSID-GATE] {unit_id} ACQUIRED slot "
                f"after {elapsed:.0f}s wait "
                f"({self._ssid_gate_status()}, queued={self._units_waiting_count})"
            )

        # =====================================================================
        # Recovery path: already-venue-wide SSIDs doing 3-step config.
        # No extra concurrency limit — these REMOVE SSIDs from venue-wide,
        # freeing R1 capacity. The only constraint is _phase_semaphore (20).
        # When recovery completes, immediately recalculate the limit so
        # new activations can start without waiting for the next reconcile.
        # =====================================================================
        if needs_recovery:
            async with self._phase_semaphore:
                try:
                    result = await asyncio.wait_for(
                        self._execute_phase_for_unit(job, unit_id, phase_id),
                        timeout=PHASE_EXECUTION_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"Job {job.id}: Phase {phase_id} timed out for {unit_id} "
                        f"(recovery) after {PHASE_EXECUTION_TIMEOUT}s"
                    )
                    raise RuntimeError(
                        f"Recovery 3-step config timed out after "
                        f"{PHASE_EXECUTION_TIMEOUT}s"
                    )

            if result.success:
                # 3-step config completed — SSID moved off venue-wide.
                # Update limit immediately so new activations can start
                # without waiting for the next 30s reconcile cycle.
                if self._last_r1_venue_wide is not None and self._last_r1_venue_wide > 0:
                    self._last_r1_venue_wide -= 1
                available = max(
                    0,
                    SSID_LIMIT_PER_AP_GROUP
                    - (self._last_r1_venue_wide or 0)
                    - SSID_SAFETY_BUFFER,
                )
                async with self._venue_wide_condition:
                    old_limit = self._venue_wide_limit
                    self._venue_wide_limit = max(
                        1,
                        min(
                            self._venue_wide_count + available,
                            DEFAULT_VENUE_WIDE_LIMIT,
                        ),
                    )
                    self._venue_wide_condition.notify_all()
                logger.info(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RECOVERY COMPLETE "
                    f"(limit {old_limit}→{self._venue_wide_limit}, "
                    f"{self._ssid_gate_status()})"
                )
            else:
                # Recovery 3-step config failed — SSID may still be on
                # venue-wide, consuming an R1 slot for nothing.
                # Try deactivate-and-requeue to free the slot and retry.
                requeued = await self._try_deactivate_and_requeue(
                    job, unit_id, phase_id, result
                )
                if requeued:
                    return PhaseResult(
                        success=True, phase_id=phase_id, unit_id=unit_id,
                        outputs={"requeued": True},
                    )
                logger.warning(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RECOVERY FAILED "
                    f"(SSID still on venue-wide, {self._ssid_gate_status()})"
                )

            return result

        # =====================================================================
        # Normal path: new SSID activations (Scenario C)
        # =====================================================================
        try:
            async with self._phase_semaphore:
                # Wrap execution in timeout to prevent indefinite hangs
                try:
                    result = await asyncio.wait_for(
                        self._execute_phase_for_unit(job, unit_id, phase_id),
                        timeout=PHASE_EXECUTION_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"Job {job.id}: Phase {phase_id} timed out for unit {unit_id} "
                        f"after {PHASE_EXECUTION_TIMEOUT}s"
                    )
                    raise RuntimeError(
                        f"Phase execution timed out after {PHASE_EXECUTION_TIMEOUT}s"
                    )

            # Phase failed: release the slot immediately to prevent leak.
            #
            # CRITICAL: _execute_phase_for_unit catches all exceptions internally
            # and returns PhaseResult(success=False). This means the `except`
            # handler below is NOT triggered — only raised exceptions reach it.
            # Without this check, failed phases permanently leak their slot,
            # eventually starving all remaining units of activation capacity.
            if not result.success and unit_id in self._activation_slots:
                self._activation_slots.discard(unit_id)
                async with self._venue_wide_condition:
                    self._venue_wide_count -= 1
                    self._venue_wide_condition.notify_all()

                # Deactivate-and-requeue: if this unit's SSID is orphaned on
                # venue-wide (consuming one of the 15 R1 slots for nothing),
                # deactivate it to free capacity, then requeue the unit for
                # one more attempt.
                requeued = await self._try_deactivate_and_requeue(
                    job, unit_id, phase_id, result
                )
                if requeued:
                    # Return a "requeued" result so _handle_phase_result doesn't
                    # re-mark the unit as FAILED (it checks success=False + critical).
                    return PhaseResult(
                        success=True, phase_id=phase_id, unit_id=unit_id,
                        outputs={"requeued": True},
                    )

                logger.info(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RELEASED (phase failed) "
                    f"({self._ssid_gate_status()})"
                )

            # Early release: if activate_network discovered the SSID was already
            # active (race between validate and execute), it didn't actually add
            # a new venue-wide SSID, so undo the increment.
            elif (
                activation_slot in ("acquire", "acquire_release")
                and unit_id in self._activation_slots
                and result.outputs.get('already_active', False)
            ):
                self._activation_slots.discard(unit_id)
                async with self._venue_wide_condition:
                    self._venue_wide_count -= 1
                    self._venue_wide_condition.notify_all()
                logger.info(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RELEASED (already_active) "
                    f"({self._ssid_gate_status()})"
                )

            # Self-releasing slot (acquire_release): phase does both venue-wide
            # activation AND 3-step config, so release when the phase completes.
            elif (
                activation_slot == "acquire_release"
                and unit_id in self._activation_slots
                and result.success
            ):
                self._activation_slots.discard(unit_id)
                async with self._venue_wide_condition:
                    self._venue_wide_count -= 1
                    self._venue_wide_condition.notify_all()
                logger.info(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RELEASED (phase complete) "
                    f"({self._ssid_gate_status()})"
                )

            # Release phase (assign_aps): 3-step config moved SSID to specific AP Group.
            # Used by Cloudpath where activate and assign are separate phases.
            if activation_slot == "release":
                if unit_id in self._activation_slots:
                    # Scenario C: new SSID completed 3-step, release the slot
                    self._activation_slots.discard(unit_id)
                    async with self._venue_wide_condition:
                        self._venue_wide_count -= 1
                        self._venue_wide_condition.notify_all()
                    logger.info(
                        f"Job {job.id}: [SSID-GATE] {unit_id} RELEASED (3-step done) "
                        f"({self._ssid_gate_status()})"
                    )

            return result

        except Exception as e:
            # On failure, release the slot to prevent deadlock
            if unit_id in self._activation_slots:
                self._activation_slots.discard(unit_id)
                async with self._venue_wide_condition:
                    self._venue_wide_count -= 1
                    self._venue_wide_condition.notify_all()
                logger.info(
                    f"Job {job.id}: [SSID-GATE] {unit_id} RELEASED (error) "
                    f"({self._ssid_gate_status()})"
                )
            raise

    async def _execute_global_phase_with_limit(
        self,
        job: WorkflowJobV2,
        phase_id: str
    ) -> PhaseResult:
        """
        Execute a global phase with concurrency limiting.

        Wraps _execute_global_phase with semaphore to prevent
        Redis connection pool exhaustion.

        Uses GLOBAL_PHASE_EXECUTION_TIMEOUT (60 min) instead of
        PHASE_EXECUTION_TIMEOUT (10 min) because global phases can
        process hundreds of resources (e.g., deleting 274 WiFi networks).
        """
        async with self._phase_semaphore:
            try:
                return await asyncio.wait_for(
                    self._execute_global_phase(job, phase_id),
                    timeout=GLOBAL_PHASE_EXECUTION_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Job {job.id}: Global phase {phase_id} timed out "
                    f"after {GLOBAL_PHASE_EXECUTION_TIMEOUT}s"
                )
                raise RuntimeError(
                    f"Phase execution timed out after "
                    f"{GLOBAL_PHASE_EXECUTION_TIMEOUT}s"
                )

    async def _execute_phase_for_unit(
        self,
        job: WorkflowJobV2,
        unit_id: str,
        phase_id: str
    ) -> PhaseResult:
        """
        Execute a single phase for a single unit.

        This is the atomic unit of work in the Brain.
        """
        unit = job.units[unit_id]
        phase_def = job.get_phase_definition(phase_id)

        if not phase_def:
            return PhaseResult(
                success=False,
                phase_id=phase_id,
                unit_id=unit_id,
                error=f"Phase '{phase_id}' not found in workflow"
            )

        # Check skip condition
        if phase_def.skip_if:
            try:
                should_skip = eval(phase_def.skip_if, {}, {"options": job.options})
                if should_skip:
                    logger.debug(f"Skipping phase {phase_id} for {unit_id}")
                    # Mark as complete so we don't re-schedule
                    updated = await self.state.update_unit_phase_status(
                        job.id, unit_id, phase_id, completed=True
                    )
                    if updated:
                        job.units[unit_id] = updated
                    return PhaseResult(
                        success=True,
                        phase_id=phase_id,
                        unit_id=unit_id,
                        outputs={}
                    )
            except Exception as e:
                logger.warning(f"Skip condition error for {phase_id}: {e}")

        # Emit unit_started if this is the first phase for this unit
        if not unit.completed_phases and not unit.failed_phases:
            per_unit_phases = [p for p in job.phase_definitions if p.per_unit]
            await self._publish_event(job.id, "unit_started", {
                "unit_id": unit_id,
                "unit_number": unit.unit_number,
                "total_phases": len(per_unit_phases),
            })

        # Update unit status (starting phase)
        updated = await self.state.update_unit_phase_status(job.id, unit_id, phase_id)
        if updated:
            job.units[unit_id] = updated

        start_time = datetime.utcnow()

        try:
            # Get phase executor class (uses executor path from definition,
            # falls back to registry by phase_id)
            phase_class = self._resolve_phase_class(phase_id, phase_def)

            # Build context
            context = self._build_context(job, unit_id=unit_id)
            executor = phase_class(context)

            # Build typed inputs from unit mapping
            inputs = self._build_inputs(executor, unit, job)

            await self._publish_event(job.id, "phase_started", {
                "unit_id": unit_id,
                "phase_id": phase_id,
                "phase_name": phase_def.name,
            })

            # Execute
            outputs = await executor.execute(inputs)

            # Apply outputs to unit mapping
            output_dict = outputs.model_dump() if hasattr(outputs, 'model_dump') else outputs
            await self._apply_outputs(job.id, unit_id, output_dict)

            # Mark phase complete for this unit and sync in-memory
            # so job.get_progress() returns accurate counts immediately
            updated = await self.state.update_unit_phase_status(
                job.id, unit_id, phase_id, completed=True
            )
            if updated:
                job.units[unit_id] = updated

            duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            await self._publish_event(job.id, "phase_completed", {
                "unit_id": unit_id,
                "phase_id": phase_id,
                "phase_name": phase_def.name,
                "duration_ms": duration,
            })

            return PhaseResult(
                success=True,
                phase_id=phase_id,
                unit_id=unit_id,
                outputs=output_dict,
                duration_ms=duration,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Job {job.id}: Phase {phase_id} failed for {unit_id}: {error_msg}"
            )

            updated = await self.state.update_unit_phase_status(
                job.id, unit_id, phase_id, failed=True, error=error_msg
            )
            if updated:
                job.units[unit_id] = updated

            duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            await self._publish_event(job.id, "phase_failed", {
                "unit_id": unit_id,
                "phase_id": phase_id,
                "error": error_msg,
            })

            return PhaseResult(
                success=False,
                phase_id=phase_id,
                unit_id=unit_id,
                error=error_msg,
                duration_ms=duration,
            )

    async def _execute_global_phase(
        self,
        job: WorkflowJobV2,
        phase_id: str
    ) -> PhaseResult:
        """Execute a global (non-per-unit) phase."""
        phase_def = job.get_phase_definition(phase_id)

        # Mark as running
        await self.state.update_global_phase_status(
            job.id, phase_id, PhaseStatus.RUNNING
        )

        start_time = datetime.utcnow()

        try:
            phase_class = self._resolve_phase_class(phase_id, phase_def)
            context = self._build_context(job)
            executor = phase_class(context)

            # Build inputs: start with input_data + options, then layer
            # global phase results from upstream phases (e.g., inventory)
            input_kwargs = {**job.input_data, **job.options}

            # Refresh job state from Redis
            refreshed = await self.state.get_job(job.id)
            if refreshed:
                job = refreshed

            # Layer global phase results
            for upstream_id, results in job.global_phase_results.items():
                if isinstance(results, dict):
                    for k, v in results.items():
                        if k not in input_kwargs:
                            input_kwargs[k] = v

            # Aggregate per-unit outputs if this phase depends on per-unit phases
            if phase_def and phase_def.depends_on:
                per_unit_deps = [
                    dep for dep in phase_def.depends_on
                    if any(p.id == dep and p.per_unit for p in job.phase_definitions)
                ]
                if per_unit_deps and job.units:
                    aggregated = self._aggregate_unit_outputs(job)
                    for k, v in aggregated.items():
                        if k not in input_kwargs:
                            input_kwargs[k] = v
                    logger.debug(
                        f"Aggregated per-unit outputs for global phase {phase_id}: "
                        f"{list(aggregated.keys())}"
                    )

            inputs = executor.Inputs(**input_kwargs)
            outputs = await executor.execute(inputs)

            output_dict = outputs.model_dump() if hasattr(outputs, 'model_dump') else outputs

            await self.state.update_global_phase_status(
                job.id, phase_id, PhaseStatus.COMPLETED, result=output_dict
            )

            duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            return PhaseResult(
                success=True,
                phase_id=phase_id,
                outputs=output_dict,
                duration_ms=duration,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Job {job.id}: Global phase {phase_id} failed: {error_msg}")

            await self.state.update_global_phase_status(
                job.id, phase_id, PhaseStatus.FAILED
            )

            return PhaseResult(
                success=False,
                phase_id=phase_id,
                error=error_msg,
            )

    # =========================================================================
    # Input/Output Wiring
    # =========================================================================

    def _build_inputs(
        self,
        executor: PhaseExecutor,
        unit: UnitMapping,
        job: WorkflowJobV2,
    ) -> Any:
        """
        Build typed inputs for a phase from the unit mapping.

        Wires resolved IDs and plan data to the executor's Inputs model.
        """
        input_class = executor.Inputs
        input_data = {}

        for field_name, field_info in input_class.model_fields.items():
            value = None

            # 1. Check unit resolved (IDs from completed phases)
            if hasattr(unit.resolved, field_name):
                value = getattr(unit.resolved, field_name)

            # 2. Check unit plan (names from validation)
            if value is None and hasattr(unit.plan, field_name):
                value = getattr(unit.plan, field_name)

            # 3. Check unit input_config (original per-unit config)
            if value is None and field_name in unit.input_config:
                value = unit.input_config[field_name]

            # 4. Check unit-level fields
            if value is None and field_name == "unit_id":
                value = unit.unit_id
            elif value is None and field_name == "unit_number":
                value = unit.unit_number

            # 5. Check global phase results
            if value is None:
                for phase_id, results in job.global_phase_results.items():
                    if field_name in results:
                        value = results[field_name]
                        break

            # 6. Check job options
            if value is None and field_name in job.options:
                value = job.options[field_name]

            # 7. Check resolved.extra
            if value is None and field_name in unit.resolved.extra:
                value = unit.resolved.extra[field_name]

            # 8. Check plan.extra
            if value is None and field_name in unit.plan.extra:
                value = unit.plan.extra[field_name]

            if value is not None:
                input_data[field_name] = value

        return input_class(**input_data)

    async def _apply_outputs(
        self,
        job_id: str,
        unit_id: str,
        outputs: Dict[str, Any]
    ) -> None:
        """
        Apply phase outputs to the unit mapping.
        Enriches the unit's resolved IDs for downstream phases.
        """
        for field_name, value in outputs.items():
            if value is None:
                continue
            if field_name in ("unit_id", "unit_number", "reused"):
                continue  # Skip meta fields

            await self.state.update_unit_resolved(
                job_id, unit_id, field_name, value
            )

    # =========================================================================
    # Completion Detection
    # =========================================================================

    async def _is_workflow_complete(
        self,
        job: WorkflowJobV2,
        graph: DependencyGraph
    ) -> bool:
        """Check if the workflow is complete (all units done, all phases done)."""
        # Check all per-unit phases
        per_unit_phase_ids = {
            p.id for p in job.phase_definitions
            if p.per_unit
        }

        # Global phases that are done (needed for dependency checking)
        global_completed = {
            pid for pid, status in job.global_phase_status.items()
            if status == PhaseStatus.COMPLETED
        }

        for unit in job.units.values():
            if unit.status == UnitStatus.FAILED:
                # Check if the failed phase was critical
                for failed_phase_id in unit.failed_phases:
                    phase_def = job.get_phase_definition(failed_phase_id)
                    if phase_def and phase_def.critical:
                        continue  # Critical failure - unit is done
                continue

            completed_and_failed = set(unit.completed_phases) | set(unit.failed_phases)

            # Check if we should skip any phases
            for phase_def in job.phase_definitions:
                if phase_def.skip_if and phase_def.per_unit:
                    try:
                        should_skip = eval(phase_def.skip_if, {}, {"options": job.options})
                        if should_skip:
                            completed_and_failed.add(phase_def.id)
                    except Exception:
                        pass

            remaining = per_unit_phase_ids - completed_and_failed
            if remaining:
                # Check if remaining phases are blocked by failed dependencies
                # Include global_completed since per-unit phases can depend on global phases
                all_satisfied = completed_and_failed | global_completed
                all_blocked = True
                for phase_id in remaining:
                    deps = graph.get_dependencies(phase_id)
                    if deps.issubset(all_satisfied):
                        all_blocked = False
                        break
                if not all_blocked:
                    return False

        # Check global phases (Phase 0 is already in global_done from validation)
        global_phase_ids = {
            p.id for p in job.phase_definitions
            if not p.per_unit
        }
        global_done = {
            pid for pid, status in job.global_phase_status.items()
            if status in (PhaseStatus.COMPLETED, PhaseStatus.FAILED, PhaseStatus.SKIPPED)
        }
        if global_phase_ids - global_done:
            # Check if remaining global phases are blocked
            for phase_id in global_phase_ids - global_done:
                phase_def = job.get_phase_definition(phase_id)
                if phase_def and phase_def.skip_if:
                    try:
                        should_skip = eval(phase_def.skip_if, {}, {"options": job.options})
                        if should_skip:
                            continue
                    except Exception:
                        pass
                return False

        return True

    async def _handle_phase_result(
        self,
        job: WorkflowJobV2,
        result: PhaseResult,
        graph: DependencyGraph
    ) -> None:
        """Handle a completed phase result."""
        if not result.success:
            phase_def = job.get_phase_definition(result.phase_id)
            if phase_def and phase_def.critical and result.unit_id:
                # Mark unit as failed for critical phase failures
                unit = job.units.get(result.unit_id)
                if unit:
                    unit.status = UnitStatus.FAILED
                    await self.state.save_unit(job.id, unit)
                    # Emit unit_completed (failed)
                    await self._publish_event(job.id, "unit_completed", {
                        "unit_id": unit.unit_id,
                        "unit_number": unit.unit_number,
                        "phases_completed": len(unit.completed_phases),
                        "phases_failed": len(unit.failed_phases),
                        "success": False,
                    })

        # Debounce progress updates to avoid flooding
        now = time.time()
        if now - self._last_progress_time >= self.PROGRESS_DEBOUNCE_INTERVAL:
            self._last_progress_time = now
            await self._publish_event(job.id, "progress_update", {
                "progress": job.get_progress(),
            })

    def _mark_skipped_phases(self, job: WorkflowJobV2) -> None:
        """
        Pre-mark phases as SKIPPED based on skip_if conditions.

        Called during job creation so the UI doesn't show skipped phases
        as pending/waiting.
        """
        for phase_def in job.phase_definitions:
            if phase_def.skip_if:
                try:
                    should_skip = eval(
                        phase_def.skip_if, {}, {"options": job.options}
                    )
                    if should_skip:
                        job.global_phase_status[phase_def.id] = PhaseStatus.SKIPPED
                        logger.debug(
                            f"Pre-marked phase {phase_def.id} as SKIPPED "
                            f"(skip_if={phase_def.skip_if})"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to evaluate skip_if for {phase_def.id}: {e}"
                    )

    def _determine_final_status(self, job: WorkflowJobV2) -> WorkflowJobV2:
        """Determine final job status based on unit outcomes."""
        if job.status == JobStatus.CANCELLED:
            return job

        total_units = len(job.units)
        if total_units == 0:
            job.status = JobStatus.COMPLETED
            return job

        # Build set of required per-unit phases (excluding skipped)
        required_phases: Set[str] = set()
        for phase_def in job.phase_definitions:
            if not phase_def.per_unit:
                continue
            if phase_def.skip_if:
                try:
                    if eval(phase_def.skip_if, {}, {"options": job.options}):
                        continue
                except Exception:
                    pass
            required_phases.add(phase_def.id)

        # Finalize unit statuses.
        # Units that completed all required phases are COMPLETED, even if
        # their status is still RUNNING (state manager doesn't auto-transition).
        # Units that are still RUNNING/PENDING with incomplete phases are FAILED
        # (tasks raised exceptions or got cancelled).
        for unit in job.units.values():
            if unit.status in (UnitStatus.RUNNING, UnitStatus.PENDING):
                completed = set(unit.completed_phases)
                if required_phases.issubset(completed):
                    unit.status = UnitStatus.COMPLETED
                else:
                    remaining = required_phases - completed
                    logger.warning(
                        f"Job {job.id}: Unit {unit.unit_id} still "
                        f"{unit.status.value} with incomplete phases: "
                        f"{remaining} — marking as FAILED"
                    )
                    unit.status = UnitStatus.FAILED

        failed_units = sum(
            1 for u in job.units.values()
            if u.status == UnitStatus.FAILED
        )

        if failed_units == 0:
            job.status = JobStatus.COMPLETED
        elif failed_units == total_units:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.PARTIAL

        return job

    async def _log_diagnostic_summary(
        self,
        job: WorkflowJobV2,
        in_flight: Dict[str, asyncio.Task]
    ) -> None:
        """
        Log a diagnostic summary of workflow progress.

        Helps identify where units are stuck or failing.
        """
        total_units = len(job.units)

        # Count unit statuses
        status_counts = {}
        for unit in job.units.values():
            status = unit.status.value if hasattr(unit.status, 'value') else str(unit.status)
            status_counts[status] = status_counts.get(status, 0) + 1

        # Count phase completions across units
        phase_counts = {}
        for phase_def in job.phase_definitions:
            if not phase_def.per_unit:
                continue
            completed = sum(
                1 for u in job.units.values()
                if phase_def.id in u.completed_phases
            )
            failed = sum(
                1 for u in job.units.values()
                if phase_def.id in u.failed_phases
            )
            if completed > 0 or failed > 0:
                phase_counts[phase_def.id] = {'completed': completed, 'failed': failed}

        # Build concise unit summary
        unit_parts = []
        for s in ('COMPLETED', 'RUNNING', 'PENDING', 'FAILED'):
            if s in status_counts:
                unit_parts.append(f"{status_counts[s]} {s.lower()}")
        unit_summary = ", ".join(unit_parts) if unit_parts else str(status_counts)

        # Activity status from tracker
        activity_status = ""
        if self.tracker and hasattr(self.tracker, '_last_poll_status'):
            ps = self.tracker._last_poll_status
            if ps:
                activity_parts = [f"{c} {s.lower()}" for s, c in sorted(ps.items())]
                activity_status = (
                    f" | Activities: {', '.join(activity_parts)} "
                    f"({self.tracker.pending_count} tracked)"
                )

        logger.info(
            f"Job {job.id} DIAGNOSTIC: "
            f"Units: {unit_summary} | "
            f"SSID-GATE: {self._ssid_gate_status()}, "
            f"queued={self._units_waiting_count}"
            f"{activity_status}"
        )

        # Log phase breakdown for phases with failures
        for phase_id, counts in phase_counts.items():
            if counts['failed'] > 0:
                logger.warning(
                    f"Job {job.id} DIAGNOSTIC: Phase '{phase_id}': "
                    f"{counts['completed']}/{total_units} completed, "
                    f"{counts['failed']} failed"
                )

        # Log what's currently in-flight (at DEBUG to reduce noise)
        if in_flight:
            in_flight_summary = {}
            for key in in_flight:
                parts = key.split(':')
                if len(parts) == 2:
                    phase = parts[1]
                    in_flight_summary[phase] = in_flight_summary.get(phase, 0) + 1
            logger.debug(
                f"Job {job.id} DIAGNOSTIC: In-flight by phase: {in_flight_summary}"
            )

        # Force a progress update (heartbeat) even if phases haven't completed
        # This ensures clients see progress during long-running operations
        await self._publish_event(job.id, "progress_update", {
            "progress": job.get_progress(),
        })

    async def _reconcile_venue_wide_limit(self, job: WorkflowJobV2) -> None:
        """
        Query R1 for the ACTUAL venue-wide SSID count and adjust the limit.

        Simple formula: limit = counter + available_new
          available_new = max(0, 15 - actual_venue_wide - buffer)

        This directly uses R1's actual state with no baseline estimation.
        Our counter already tracks new activations that are included in
        R1's actual_venue_wide. The available slots are how many more
        R1 can accommodate before hitting the 15-SSID limit.

        Also cross-references venue-wide SSIDs with job's managed network_ids
        for definitive managed vs external classification (logging only).

        Called periodically from the main scheduling loop (~every 30s).
        """
        if not self.r1_client or not self._venue_wide_condition:
            return

        try:
            networks_response = await self.r1_client.networks.get_wifi_networks(
                job.tenant_id
            )
            all_networks = (
                networks_response.get('data', [])
                if isinstance(networks_response, dict)
                else networks_response
            )

            # Count SSIDs currently on "All AP Groups" for this venue
            actual_venue_wide = 0
            venue_wide_ssid_names: list = []
            venue_wide_network_ids: set = set()
            for network in all_networks:
                for vag in network.get('venueApGroups', []):
                    if vag.get('venueId') == job.venue_id and vag.get('isAllApGroups', False):
                        actual_venue_wide += 1
                        venue_wide_ssid_names.append(
                            network.get('ssid', network.get('name', '?'))
                        )
                        venue_wide_network_ids.add(network.get('id'))
                        break

            # Store last known R1 count for status display
            self._last_r1_venue_wide = actual_venue_wide

            # Definitive managed/external split by cross-referencing
            # with our job's network IDs
            managed_network_ids = {
                unit.resolved.network_id
                for unit in job.units.values()
                if unit.resolved.network_id
            }
            managed_vw = len(venue_wide_network_ids & managed_network_ids)
            external_vw = actual_venue_wide - managed_vw

            # Identify failed units with SSIDs orphaned on venue-wide
            failed_orphans = 0
            for unit in job.units.values():
                if (
                    unit.status == UnitStatus.FAILED
                    and unit.resolved.network_id
                    and unit.resolved.network_id in venue_wide_network_ids
                ):
                    failed_orphans += 1

            # Simple, correct limit formula:
            # available_new = how many more venue-wide SSIDs R1 can take
            # limit = counter (already in R1) + available_new
            available_new = max(
                0,
                SSID_LIMIT_PER_AP_GROUP - actual_venue_wide - SSID_SAFETY_BUFFER,
            )
            new_limit = max(
                1,
                min(
                    self._venue_wide_count + available_new,
                    DEFAULT_VENUE_WIDE_LIMIT,
                ),
            )

            old_limit = self._venue_wide_limit
            async with self._venue_wide_condition:
                self._venue_wide_limit = new_limit
                if new_limit > old_limit:
                    self._venue_wide_condition.notify_all()

            orphan_note = f", orphaned={failed_orphans}" if failed_orphans else ""
            limit_change = (
                f"limit {old_limit}→{new_limit}"
                if new_limit != old_limit
                else f"limit={new_limit}"
            )
            logger.info(
                f"Job {job.id}: [SSID-GATE] RECONCILE: "
                f"R1 venue-wide={actual_venue_wide}/{SSID_LIMIT_PER_AP_GROUP} "
                f"(managed={managed_vw}, external={external_vw}{orphan_note}), "
                f"{limit_change}, "
                f"in-flight={self._venue_wide_count}, "
                f"available_new={available_new} | "
                f"SSIDs on All AP Groups: {venue_wide_ssid_names}"
            )

            if failed_orphans > 0:
                # Count how many orphans have already exhausted their requeue attempts
                permanently_failed = sum(
                    1 for unit in job.units.values()
                    if (
                        unit.status == UnitStatus.FAILED
                        and unit.resolved.network_id
                        and unit.resolved.network_id in venue_wide_network_ids
                        and self._requeue_counts.get(unit.unit_id, 0) >= MAX_REQUEUE_ATTEMPTS
                    )
                )
                pending_requeue = failed_orphans - permanently_failed
                if pending_requeue > 0:
                    logger.warning(
                        f"Job {job.id}: [SSID-GATE] {failed_orphans} failed units "
                        f"have SSIDs stuck on venue-wide (consuming R1 capacity). "
                        f"{pending_requeue} will be deactivated and requeued on failure."
                    )
                elif permanently_failed > 0:
                    logger.warning(
                        f"Job {job.id}: [SSID-GATE] {failed_orphans} failed units "
                        f"have SSIDs stuck on venue-wide (consuming R1 capacity). "
                        f"All have exhausted requeue attempts."
                    )

        except Exception as e:
            # Non-fatal: just log and continue with current limit
            logger.warning(f"Job {job.id}: [SSID-GATE] RECONCILE failed: {e}")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _ssid_gate_status(self) -> str:
        """Format SSID gate status for log messages.

        Shows our slot count, the limit, the last known actual R1
        venue-wide count, and available new slots.
        """
        r1 = self._last_r1_venue_wide if self._last_r1_venue_wide is not None else "?"
        available = max(
            0,
            SSID_LIMIT_PER_AP_GROUP
            - (self._last_r1_venue_wide or 0)
            - SSID_SAFETY_BUFFER,
        ) if self._last_r1_venue_wide is not None else "?"
        return (
            f"slots={self._venue_wide_count}/{self._venue_wide_limit}, "
            f"R1_vw={r1}/{SSID_LIMIT_PER_AP_GROUP}, "
            f"avail={available}"
        )

    async def _try_deactivate_and_requeue(
        self,
        job: WorkflowJobV2,
        unit_id: str,
        phase_id: str,
        result,
    ) -> bool:
        """
        After a phase failure, check if the unit's SSID is orphaned on venue-wide.
        If so, deactivate it from R1 to free the slot, then reset the unit to
        PENDING so the main loop re-schedules it.

        Returns True if the unit was requeued, False otherwise.
        """
        # Guard: only requeue once
        if self._requeue_counts.get(unit_id, 0) >= MAX_REQUEUE_ATTEMPTS:
            return False

        # Guard: need R1 client and venue/tenant info
        if not self.r1_client:
            return False

        unit = job.units.get(unit_id)
        if not unit or not unit.resolved.network_id:
            return False

        network_id = unit.resolved.network_id

        # Attempt deactivation directly without verifying venue-wide status.
        # The individual GET /wifiNetworks/{id} can return stale data that
        # disagrees with the bulk query (which correctly shows the SSID on
        # All AP Groups). The DELETE is idempotent — if the SSID isn't on
        # the venue, it returns 404 which deactivate_ssid_from_venue handles.
        try:
            ssid_name = unit.resolved.ssid_name or unit_id
            logger.info(
                f"Job {job.id}: [SSID-GATE] {unit_id} DEACTIVATING orphaned SSID "
                f"'{ssid_name}' from venue to free R1 slot "
                f"(attempt {self._requeue_counts.get(unit_id, 0) + 1}/{MAX_REQUEUE_ATTEMPTS})"
            )

            await asyncio.wait_for(
                self.r1_client.venues.deactivate_ssid_from_venue(
                    tenant_id=job.tenant_id,
                    venue_id=job.venue_id,
                    wifi_network_id=network_id,
                    wait_for_completion=True,
                ),
                timeout=120,  # 2 min max for deactivation
            )

            logger.info(
                f"Job {job.id}: [SSID-GATE] {unit_id} DEACTIVATED — "
                f"resetting unit for retry ({self._ssid_gate_status()})"
            )

            # Reset the unit to PENDING so _find_ready_work picks it up again.
            # Clear the stale validation flags — the SSID is no longer on the
            # venue, so it needs a fresh Scenario C activation (not recovery).
            unit.status = UnitStatus.PENDING
            unit.current_phase = None
            if phase_id in unit.failed_phases:
                unit.failed_phases.remove(phase_id)
            if phase_id in unit.phase_errors:
                del unit.phase_errors[phase_id]
            unit.input_config.pop('already_activated', None)
            unit.input_config.pop('is_venue_wide', None)
            await self.state.save_unit(job.id, unit)

            # Track the requeue
            self._requeue_counts[unit_id] = self._requeue_counts.get(unit_id, 0) + 1

            # Update R1 venue-wide estimate since we just removed one
            if self._last_r1_venue_wide is not None and self._last_r1_venue_wide > 0:
                self._last_r1_venue_wide -= 1

            # Recalculate limit to reflect freed capacity
            available = max(
                0,
                SSID_LIMIT_PER_AP_GROUP
                - (self._last_r1_venue_wide or 0)
                - SSID_SAFETY_BUFFER,
            )
            async with self._venue_wide_condition:
                old_limit = self._venue_wide_limit
                self._venue_wide_limit = max(
                    1,
                    min(
                        self._venue_wide_count + available,
                        DEFAULT_VENUE_WIDE_LIMIT,
                    ),
                )
                self._venue_wide_condition.notify_all()

            logger.info(
                f"Job {job.id}: [SSID-GATE] {unit_id} REQUEUED for retry "
                f"(limit {old_limit}→{self._venue_wide_limit}, "
                f"{self._ssid_gate_status()})"
            )
            return True

        except Exception as e:
            # Deactivation failed — fall through to normal failure handling.
            # The orphan stays, but we're no worse off than before.
            logger.warning(
                f"Job {job.id}: [SSID-GATE] {unit_id} deactivate-and-requeue "
                f"failed: {e} — falling back to normal failure"
            )
            return False

    def _aggregate_unit_outputs(self, job: WorkflowJobV2) -> Dict[str, Any]:
        """
        Aggregate outputs from all units for global phase inputs.

        Aggregation rules:
        - int/float: sum across all units
        - dict: merge (later units override earlier)
        - list: concatenate
        - other: take last non-None value
        """
        aggregated: Dict[str, Any] = {}

        for unit in job.units.values():
            resolved = unit.resolved

            # Aggregate each field from UnitResolved
            for field_name in resolved.model_fields.keys():
                value = getattr(resolved, field_name, None)
                if value is None:
                    continue
                # Skip empty containers
                if isinstance(value, (list, dict)) and not value:
                    continue

                if field_name not in aggregated:
                    # First value
                    if isinstance(value, (int, float)):
                        aggregated[field_name] = value
                    elif isinstance(value, dict):
                        aggregated[field_name] = dict(value)
                    elif isinstance(value, list):
                        aggregated[field_name] = list(value)
                    else:
                        aggregated[field_name] = value
                else:
                    # Aggregate with existing
                    existing = aggregated[field_name]
                    if isinstance(value, (int, float)) and isinstance(existing, (int, float)):
                        aggregated[field_name] = existing + value
                    elif isinstance(value, dict) and isinstance(existing, dict):
                        existing.update(value)
                    elif isinstance(value, list) and isinstance(existing, list):
                        existing.extend(value)
                    else:
                        # Take latest non-None value
                        aggregated[field_name] = value

        return aggregated

    def _build_context(
        self,
        job: WorkflowJobV2,
        unit_id: str = None,
    ) -> PhaseContext:
        """Build execution context for a phase."""
        from workflow.phases.phase_executor import PhaseContext
        return PhaseContext(
            job_id=job.id,
            r1_client=self.r1_client,
            venue_id=job.venue_id,
            tenant_id=job.tenant_id,
            activity_tracker=self.tracker,
            state_manager=self.state,
            event_publisher=self.events,
            options=job.options,
            unit_id=unit_id,
        )

    def _resolve_phase_class(self, phase_id: str, phase_def=None):
        """
        Resolve phase executor class from definition or registry.

        Uses the executor path from PhaseDefinitionV2 (e.g.,
        "workflow.phases.activate_network_direct.ActivateNetworkDirectPhase")
        as the authoritative source. Falls back to registry lookup by phase ID
        only if the executor path is missing or fails to import.

        This ensures the workflow definition controls which class runs,
        not the import-order-dependent registry.
        """
        import importlib

        # Try executor path from phase definition (authoritative)
        if phase_def and phase_def.executor:
            try:
                module_path, class_name = phase_def.executor.rsplit('.', 1)
                module = importlib.import_module(module_path)
                return getattr(module, class_name)
            except (ImportError, AttributeError, ValueError) as e:
                logger.warning(
                    f"Failed to import executor '{phase_def.executor}': {e}, "
                    f"falling back to registry for '{phase_id}'"
                )

        # Fallback to registry
        from workflow.phases.registry import get_phase_class
        return get_phase_class(phase_id)

    async def _publish_event(
        self,
        job_id: str,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """Publish an event via Redis and event publisher."""
        try:
            await self.state.publish_event(job_id, event_type, data)
        except Exception as e:
            logger.debug(f"Event publish error: {e}")

        if self.events:
            try:
                method = getattr(self.events, event_type, None)
                if method:
                    await method(job_id, **data)
            except Exception as e:
                logger.debug(f"Event publisher error: {e}")
