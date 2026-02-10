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
    from workflow.phases.registry import get_phase_class
    from workflow.phases.phase_executor import PhaseContext, PhaseExecutor
    from workflow.workflows.definition import Workflow

logger = logging.getLogger(__name__)

# How long to wait between checking for ready work
SCHEDULE_INTERVAL = 0.25  # seconds

# Concurrency control: limit parallel phase executions to prevent Redis connection exhaustion
# With 50 units, unbounded parallelism can spawn 50+ concurrent tasks, each doing 3-5 Redis ops
# This semaphore ensures we don't exceed Redis connection pool capacity
MAX_CONCURRENT_PHASE_TASKS = 20

# Default activation slots for R1's 15-SSID-per-AP-Group limit
# Controls how many SSIDs can be "in-flight" (activated but not yet assigned to specific AP Group)
# Default 12 leaves buffer for existing venue-wide SSIDs
DEFAULT_MAX_ACTIVATION_SLOTS = 12

# Phase execution timeout (seconds) - prevents indefinite hangs on stuck API calls
# Most phases complete in <30s, but some (like passphrase creation) may take longer
# Set high enough to allow legitimate slow operations but catch real hangs
PHASE_EXECUTION_TIMEOUT = 300  # 5 minutes


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

        # Activation slot management for R1's 15-SSID-per-AP-Group limit
        # Limits how many SSIDs can be "in-flight" (activated but not assigned to AP Group)
        self._activation_semaphore: Optional[asyncio.Semaphore] = None
        self._activation_slots: Set[str] = set()  # unit_ids holding activation slots

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

        # Ensure max_activation_slots is set from workflow if not overridden
        if 'max_activation_slots' not in merged_options:
            merged_options['max_activation_slots'] = getattr(
                workflow, 'max_activation_slots', DEFAULT_MAX_ACTIVATION_SLOTS
            )

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
            from workflow.phases.registry import get_phase_class
            phase_class = get_phase_class(phase_id)
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

        # Build dependency graph from all phases
        # (Phase 0 is already in global_phase_status, so it won't re-run)
        graph = DependencyGraph(job.phase_definitions)

        # Initialize concurrency control semaphore
        # This prevents Redis connection pool exhaustion with large unit counts
        self._phase_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PHASE_TASKS)

        # Initialize activation slot semaphore for R1's 15-SSID limit
        # Check if validation phase calculated a dynamic limit based on existing venue SSIDs
        max_slots = job.options.get('max_activation_slots', DEFAULT_MAX_ACTIVATION_SLOTS)

        # Override with validation result if available (more accurate, based on venue state)
        validate_results = job.global_phase_results.get('validate_and_plan', {})
        if 'max_activation_slots' in validate_results:
            max_slots = validate_results['max_activation_slots']
            venue_ssids = validate_results.get('venue_wide_ssid_count', 0)
            logger.info(
                f"Job {job.id}: Using calculated activation slot limit = {max_slots} "
                f"(venue has {venue_ssids} existing venue-wide SSIDs)"
            )
        else:
            logger.info(f"Job {job.id}: Using default activation slot limit = {max_slots}")

        self._activation_semaphore = asyncio.Semaphore(max_slots)
        self._activation_slots = set()

        # Track in-flight work
        in_flight: Dict[str, asyncio.Task] = {}  # "unit:phase" → task

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

                for key in completed_keys:
                    del in_flight[key]

                # Refresh job state from Redis (other workers may have updated)
                refreshed = await self.state.get_job(job.id)
                if refreshed:
                    job.global_phase_status = refreshed.global_phase_status
                    job.global_phase_results = refreshed.global_phase_results
                    job.created_resources = refreshed.created_resources
                    job.errors = refreshed.errors

                # Periodic diagnostic summary (every 30 seconds)
                if int(time.time()) % 30 == 0 and in_flight:
                    await self._log_diagnostic_summary(job, in_flight)

                # Refresh unit states
                for unit_id in job.units:
                    refreshed_unit = await self.state.get_unit(job.id, unit_id)
                    if refreshed_unit:
                        job.units[unit_id] = refreshed_unit

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

        Also handles activation slot management for R1's 15-SSID limit:
        - Phases with activation_slot="acquire" get a slot before starting
        - Phases with activation_slot="release" release the slot after completing
        """
        phase_def = job.get_phase_definition(phase_id)
        activation_slot = getattr(phase_def, 'activation_slot', None) if phase_def else None

        # Acquire activation slot if this phase requires it
        if activation_slot == "acquire" and unit_id not in self._activation_slots:
            logger.debug(f"Job {job.id}: Unit {unit_id} acquiring activation slot for {phase_id}")
            await self._activation_semaphore.acquire()
            self._activation_slots.add(unit_id)
            logger.debug(f"Job {job.id}: Unit {unit_id} acquired activation slot ({len(self._activation_slots)} held)")

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

            # Release activation slot if this phase completes the cycle
            if activation_slot == "release" and unit_id in self._activation_slots:
                self._activation_slots.discard(unit_id)
                self._activation_semaphore.release()
                logger.debug(f"Job {job.id}: Unit {unit_id} released activation slot ({len(self._activation_slots)} held)")

            return result

        except Exception as e:
            # On failure, release the slot to prevent deadlock
            if unit_id in self._activation_slots:
                self._activation_slots.discard(unit_id)
                self._activation_semaphore.release()
                logger.debug(f"Job {job.id}: Unit {unit_id} released activation slot on error")
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
        """
        async with self._phase_semaphore:
            try:
                return await asyncio.wait_for(
                    self._execute_global_phase(job, phase_id),
                    timeout=PHASE_EXECUTION_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Job {job.id}: Global phase {phase_id} timed out "
                    f"after {PHASE_EXECUTION_TIMEOUT}s"
                )
                raise RuntimeError(
                    f"Phase execution timed out after {PHASE_EXECUTION_TIMEOUT}s"
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
                    await self.state.update_unit_phase_status(
                        job.id, unit_id, phase_id, completed=True
                    )
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

        # Update unit status
        await self.state.update_unit_phase_status(job.id, unit_id, phase_id)

        start_time = datetime.utcnow()

        try:
            # Get phase executor class
            from workflow.phases.registry import get_phase_class
            phase_class = get_phase_class(phase_id)

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

            # Mark phase complete for this unit
            await self.state.update_unit_phase_status(
                job.id, unit_id, phase_id, completed=True
            )

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

            await self.state.update_unit_phase_status(
                job.id, unit_id, phase_id, failed=True, error=error_msg
            )

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
            from workflow.phases.registry import get_phase_class
            phase_class = get_phase_class(phase_id)
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

        failed_units = sum(
            1 for u in job.units.values()
            if u.status == UnitStatus.FAILED
        )
        completed_units = sum(
            1 for u in job.units.values()
            if u.status == UnitStatus.COMPLETED
        )

        # Update unit statuses and emit unit_completed for remaining units
        for unit in job.units.values():
            if unit.status == UnitStatus.RUNNING:
                # Shouldn't happen but handle gracefully
                unit.status = UnitStatus.COMPLETED
            # Note: unit_completed for FAILED units is already emitted in _handle_phase_result
            # Here we only need to mark successful units that completed all phases
            # (event emission happens async so we don't await here in sync method)

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

        # Log summary
        logger.info(
            f"Job {job.id} DIAGNOSTIC: "
            f"Units: {status_counts} | "
            f"In-flight: {len(in_flight)} | "
            f"Activation slots: {len(self._activation_slots)}/{self._activation_semaphore._value if self._activation_semaphore else 'N/A'}"
        )

        # Log phase breakdown for phases with failures
        for phase_id, counts in phase_counts.items():
            if counts['failed'] > 0:
                logger.warning(
                    f"Job {job.id} DIAGNOSTIC: Phase '{phase_id}': "
                    f"{counts['completed']}/{total_units} completed, "
                    f"{counts['failed']} failed"
                )

        # Log what's currently in-flight
        if in_flight:
            in_flight_summary = {}
            for key in in_flight:
                parts = key.split(':')
                if len(parts) == 2:
                    phase = parts[1]
                    in_flight_summary[phase] = in_flight_summary.get(phase, 0) + 1
            logger.info(
                f"Job {job.id} DIAGNOSTIC: In-flight by phase: {in_flight_summary}"
            )

        # Force a progress update (heartbeat) even if phases haven't completed
        # This ensures clients see progress during long-running operations
        await self._publish_event(job.id, "progress_update", {
            "progress": job.get_progress(),
            "heartbeat": True,  # Flag to indicate this is a periodic heartbeat
        })

    # =========================================================================
    # Helpers
    # =========================================================================

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
