"""
Workflow Engine

Main orchestrator for multi-phase workflows:
- Phase-by-phase execution
- Dependency resolution
- Parallel vs sequential task execution
- Error handling (critical vs non-critical phases)
- State management via Redis
"""

import asyncio
import logging
from typing import Dict, Any, Callable, List
from datetime import datetime
from workflow.models import (
    WorkflowJob,
    Phase,
    Task,
    JobStatus,
    PhaseStatus,
    TaskStatus,
    WorkflowDefinition
)
from workflow.state_manager import RedisStateManager
from workflow.executor import TaskExecutor
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Orchestrates multi-phase workflow execution"""

    def __init__(
        self,
        state_manager: RedisStateManager,
        task_executor: TaskExecutor,
        event_publisher: WorkflowEventPublisher = None
    ):
        """
        Initialize workflow engine

        Args:
            state_manager: Redis state manager
            task_executor: Task executor
            event_publisher: Optional event publisher for real-time updates
        """
        self.state_manager = state_manager
        self.task_executor = task_executor
        self.event_publisher = event_publisher

    async def execute_workflow(
        self,
        job: WorkflowJob,
        phase_executors: Dict[str, Callable]
    ) -> WorkflowJob:
        """
        Execute a complete workflow

        Args:
            job: WorkflowJob instance
            phase_executors: Dict mapping phase_id → executor function

        Returns:
            Updated WorkflowJob
        """
        logger.info(f"🚀 Starting workflow {job.workflow_name} (job_id={job.id})")

        try:
            # Update job status
            job.status = JobStatus.RUNNING
            job.updated_at = datetime.utcnow()
            await self.state_manager.save_job(job)

            # Publish job started event
            if self.event_publisher:
                await self.event_publisher.job_started(job)
                # Emit initial progress so frontend knows phase count right away
                progress = job.get_progress_stats()
                await self.event_publisher.progress_update(job.id, progress)

            # Execute phases in dependency order
            phases_to_execute = self._resolve_dependencies(job.phases)

            for phase in phases_to_execute:
                # Check for cancellation before starting each phase
                if await self.state_manager.is_cancelled(job.id):
                    logger.info(f"🛑 Job {job.id} cancelled - stopping before phase {phase.id}")
                    job.status = JobStatus.CANCELLED
                    job.errors.append("Job cancelled by user")
                    job.completed_at = datetime.utcnow()
                    await self.state_manager.save_job(job)

                    # Publish cancellation event
                    if self.event_publisher:
                        await self.event_publisher.job_cancelled(job)

                    return job
                # Check if phase should be skipped
                if await self._should_skip_phase(phase, job):
                    logger.info(f"⏭️  Skipping phase {phase.id} ({phase.name})")
                    phase.status = PhaseStatus.SKIPPED
                    await self.state_manager.update_phase(job.id, phase)
                    continue

                # Execute phase
                try:
                    job.current_phase_id = phase.id
                    await self.state_manager.save_job(job)

                    phase = await self._execute_phase(
                        job,
                        phase,
                        phase_executors.get(phase.id),
                        job.options
                    )

                    # Update phase in job
                    for i, p in enumerate(job.phases):
                        if p.id == phase.id:
                            job.phases[i] = phase
                            break

                    await self.state_manager.save_job(job)

                    # Check if critical phase failed
                    if phase.critical and phase.status == PhaseStatus.FAILED:
                        logger.error(f"❌ Critical phase {phase.id} failed - stopping workflow")
                        job.status = JobStatus.FAILED
                        job.errors.append(f"Critical phase '{phase.name}' failed")
                        job.completed_at = datetime.utcnow()
                        await self.state_manager.save_job(job)
                        return job

                except Exception as e:
                    error_msg = f"Phase {phase.id} execution error: {str(e)}"
                    logger.error(error_msg)
                    phase.status = PhaseStatus.FAILED
                    phase.errors.append(error_msg)
                    job.errors.append(error_msg)

                    await self.state_manager.update_phase(job.id, phase)

                    if phase.critical:
                        job.status = JobStatus.FAILED
                        job.completed_at = datetime.utcnow()
                        await self.state_manager.save_job(job)
                        return job

            # Workflow complete - determine final status
            job.current_phase_id = None
            job = self._determine_final_status(job)
            job.completed_at = datetime.utcnow()

            # Calculate summary
            job.summary = self._calculate_summary(job)

            await self.state_manager.save_job(job)
            logger.info(f"✅ Workflow {job.workflow_name} completed with status {job.status}")

            # Publish job completed/failed event
            if self.event_publisher:
                if job.status == JobStatus.FAILED:
                    await self.event_publisher.job_failed(job)
                else:
                    await self.event_publisher.job_completed(job)

            return job

        except Exception as e:
            logger.error(f"❌ Workflow execution failed: {str(e)}")
            job.status = JobStatus.FAILED
            job.errors.append(f"Workflow execution error: {str(e)}")
            job.completed_at = datetime.utcnow()
            await self.state_manager.save_job(job)

            # Publish job failed event
            if self.event_publisher:
                await self.event_publisher.job_failed(job)

            return job

    async def _execute_phase(
        self,
        job: WorkflowJob,
        phase: Phase,
        executor_func: Callable,
        context: Dict[str, Any]
    ) -> Phase:
        """
        Execute a single phase

        Args:
            job: WorkflowJob instance
            phase: Phase to execute
            executor_func: Phase executor function
            context: Execution context

        Returns:
            Updated Phase
        """
        logger.info(f"▶️  Executing phase {phase.id} ({phase.name})")

        phase.status = PhaseStatus.RUNNING
        phase.started_at = datetime.utcnow()
        await self.state_manager.update_phase(job.id, phase)

        # Publish phase started event
        if self.event_publisher:
            await self.event_publisher.phase_started(job.id, phase)

        try:
            # Build context for this phase
            phase_context = {
                'job_id': job.id,
                'workflow_name': job.workflow_name,
                'controller_id': job.controller_id,
                'venue_id': job.venue_id,
                'tenant_id': job.tenant_id,
                'options': job.options,
                'input_data': job.input_data,
                'previous_phase_results': self._get_previous_phase_results(job, phase),
                'r1_client': self.task_executor.r1_client if self.task_executor else None,
                'event_publisher': self.event_publisher,  # Allow phases to emit custom messages
                'activation_semaphore': self.task_executor.activation_semaphore if self.task_executor else None,
                **context,
                **(job.input_data if job.input_data else {})  # Unpack input_data for easy access
            }

            # Execute phase function to generate tasks
            if executor_func:
                phase.tasks = await executor_func(phase_context)
                logger.info(f"  Generated {len(phase.tasks)} tasks for phase {phase.id}")
            else:
                logger.warning(f"  No executor function for phase {phase.id}")
                phase.status = PhaseStatus.SKIPPED
                return phase

            # Execute tasks (parallel or sequential)
            if phase.parallelizable and len(phase.tasks) > 1:
                logger.info(f"  Executing {len(phase.tasks)} tasks in parallel")
                max_concurrent = context.get('max_parallel_tasks', 50)

                # Group tasks and execute in batches if needed
                completed_tasks = await self.task_executor.execute_tasks_parallel(
                    phase.tasks,
                    self._task_wrapper,
                    phase_context,
                    max_concurrent=max_concurrent,
                    job_id=job.id,
                    phase_id=phase.id
                )
            else:
                logger.info(f"  Executing {len(phase.tasks)} tasks sequentially")
                completed_tasks = await self.task_executor.execute_tasks_sequential(
                    phase.tasks,
                    self._task_wrapper,
                    phase_context,
                    job_id=job.id,
                    phase_id=phase.id
                )

            # Update phase tasks
            phase.tasks = completed_tasks

            # Check task results
            failed_tasks = [t for t in phase.tasks if t.status == TaskStatus.FAILED]
            completed_tasks = [t for t in phase.tasks if t.status == TaskStatus.COMPLETED]

            if failed_tasks:
                logger.warning(f"  Phase {phase.id}: {len(failed_tasks)} tasks failed")
                phase.errors.extend([
                    f"Task {t.name}: {t.error_message}"
                    for t in failed_tasks
                ])

                if len(completed_tasks) == 0:
                    # All tasks failed
                    phase.status = PhaseStatus.FAILED
                else:
                    # Partial success - mark as completed but with errors
                    phase.status = PhaseStatus.COMPLETED
            else:
                phase.status = PhaseStatus.COMPLETED

            # Store phase result data (output from tasks)
            phase.result = self._aggregate_task_outputs(phase.tasks)

            # Track created resources
            await self._track_created_resources(job, phase)

            phase.completed_at = datetime.utcnow()
            await self.state_manager.update_phase(job.id, phase)

            logger.info(f"✅ Phase {phase.id} completed: {phase.status}")

            # Publish phase completed event
            if self.event_publisher:
                await self.event_publisher.phase_completed(job.id, phase)

                # Publish progress update
                progress = job.get_progress_stats()
                await self.event_publisher.progress_update(job.id, progress)

            return phase

        except Exception as e:
            error_msg = f"Phase execution error: {str(e)}"
            logger.error(f"❌ {error_msg}")
            phase.status = PhaseStatus.FAILED
            phase.errors.append(error_msg)
            phase.completed_at = datetime.utcnow()
            await self.state_manager.update_phase(job.id, phase)
            raise

    async def _task_wrapper(
        self,
        task: Task,
        context: Dict[str, Any],
        r1_client
    ) -> Dict[str, Any]:
        """
        Wrapper for task execution - just returns task input
        (actual work happens in phase executors)

        This is a placeholder that gets overridden by specific phase executors
        """
        return task.output_data

    def _resolve_dependencies(self, phases: List[Phase]) -> List[Phase]:
        """
        Resolve phase dependencies and return execution order

        Args:
            phases: List of phases

        Returns:
            List of phases in dependency order
        """
        # Simple topological sort
        executed = set()
        ordered = []

        def can_execute(phase: Phase) -> bool:
            return all(dep in executed for dep in phase.dependencies)

        remaining = phases.copy()
        while remaining:
            # Find phases that can execute now
            ready = [p for p in remaining if can_execute(p)]

            if not ready:
                # Circular dependency or missing dependency
                logger.error("Cannot resolve phase dependencies")
                raise Exception("Circular or missing phase dependencies detected")

            # Add ready phases to ordered list
            for phase in ready:
                ordered.append(phase)
                executed.add(phase.id)
                remaining.remove(phase)

        return ordered

    async def _should_skip_phase(self, phase: Phase, job: WorkflowJob) -> bool:
        """
        Evaluate skip condition for a phase

        Args:
            phase: Phase to check
            job: WorkflowJob

        Returns:
            bool: True if phase should be skipped
        """
        if not phase.skip_condition:
            return False

        try:
            # Evaluate skip condition
            # Example: "options.include_policy_sets == False"
            context = {
                'options': job.options,
                'input_data': job.input_data
            }
            result = eval(phase.skip_condition, {}, context)
            return bool(result)
        except Exception as e:
            logger.warning(f"Error evaluating skip condition for {phase.id}: {str(e)}")
            return False

    def _get_previous_phase_results(self, job: WorkflowJob, current_phase: Phase) -> Dict[str, Any]:
        """
        Get results from all dependent phases

        Args:
            job: WorkflowJob
            current_phase: Current phase

        Returns:
            Dict of phase_id → phase result
        """
        results = {}
        for dep_id in current_phase.dependencies:
            dep_phase = job.get_phase_by_id(dep_id)
            if dep_phase:
                results[dep_id] = dep_phase.result
        return results

    def _aggregate_task_outputs(self, tasks: List[Task]) -> Dict[str, Any]:
        """
        Aggregate outputs from all tasks in a phase

        Args:
            tasks: List of completed tasks

        Returns:
            Aggregated result dict
        """
        import logging
        logger = logging.getLogger(__name__)

        results = {
            'task_outputs': [t.output_data for t in tasks],
            'completed_count': len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            'failed_count': len([t for t in tasks if t.status == TaskStatus.FAILED]),
        }

        # Collect all output data into lists by key
        all_outputs = {}
        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                logger.debug(f"Aggregating task {task.id}, output_data keys: {list(task.output_data.keys())}")
                for key, value in task.output_data.items():
                    if key not in all_outputs:
                        all_outputs[key] = []
                    all_outputs[key].append(value)
                    logger.debug(f"Added {key}: type={type(value)}, is_dict={isinstance(value, dict)}")

        results['aggregated'] = all_outputs
        logger.debug(f"Final aggregated keys: {list(all_outputs.keys())}")
        for key in all_outputs:
            logger.debug(f"aggregated['{key}'] is list of length {len(all_outputs[key])}")
        return results

    async def _track_created_resources(self, job: WorkflowJob, phase: Phase):
        """
        Track resources created in this phase

        Args:
            job: WorkflowJob
            phase: Phase
        """
        # Extract created resources from task outputs
        # This is phase-specific and should be customized per workflow
        pass

    def _determine_final_status(self, job: WorkflowJob) -> WorkflowJob:
        """
        Determine final job status based on phase results

        Args:
            job: WorkflowJob

        Returns:
            Updated WorkflowJob
        """
        failed_phases = [p for p in job.phases if p.status == PhaseStatus.FAILED]
        completed_phases = [p for p in job.phases if p.status == PhaseStatus.COMPLETED]

        if failed_phases:
            if completed_phases:
                job.status = JobStatus.PARTIAL
            else:
                job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.COMPLETED

        return job

    def _calculate_summary(self, job: WorkflowJob) -> Dict[str, Any]:
        """
        Calculate job summary statistics

        Args:
            job: WorkflowJob

        Returns:
            Summary dict
        """
        summary = job.get_progress_stats()

        # Add phase summary
        summary['phases'] = {
            'total': len(job.phases),
            'completed': len([p for p in job.phases if p.status == PhaseStatus.COMPLETED]),
            'failed': len([p for p in job.phases if p.status == PhaseStatus.FAILED]),
            'skipped': len([p for p in job.phases if p.status == PhaseStatus.SKIPPED]),
        }

        # Add resource counts
        summary['resources'] = {
            resource_type: len(resources)
            for resource_type, resources in job.created_resources.items()
        }

        return summary
