"""
Parallel Job Orchestrator

Manages parallel execution of child jobs under a parent job:
- Spawns child jobs for each item
- Controls concurrency via semaphore
- Aggregates progress and results
- Handles partial failures gracefully
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime
from workflow.models import (
    WorkflowJob,
    Phase,
    JobStatus,
    PhaseStatus,
)
from workflow.state_manager import RedisStateManager
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)


class ParallelJobOrchestrator:
    """Orchestrates parallel execution of child jobs under a parent"""

    def __init__(
        self,
        state_manager: RedisStateManager,
        event_publisher: WorkflowEventPublisher = None
    ):
        self.state_manager = state_manager
        self.event_publisher = event_publisher

    async def execute_parallel_workflow(
        self,
        parent_job: WorkflowJob,
        items: List[Dict[str, Any]],
        item_key: str,
        child_workflow_executor: Callable,
        max_concurrent: int = 10
    ) -> WorkflowJob:
        """
        Execute a workflow in parallel across multiple items.

        Each item becomes its own child job that runs through all phases
        independently. The parent job aggregates results.

        Args:
            parent_job: The parent WorkflowJob instance
            items: List of items to process (e.g., list of units)
            item_key: Key to identify each item (e.g., 'unit_number')
            child_workflow_executor: Async function to execute each child job
            max_concurrent: Max number of concurrent child jobs

        Returns:
            Updated parent WorkflowJob with aggregated results
        """
        logger.info(f"Starting parallel workflow {parent_job.workflow_name} with {len(items)} items (max_concurrent={max_concurrent})")

        # Store parallel config on parent
        parent_job.parallel_config = {
            'max_concurrent': max_concurrent,
            'item_key': item_key,
            'total_items': len(items)
        }

        # Create child jobs
        child_jobs = []
        for i, item in enumerate(items):
            child_job = self._create_child_job(parent_job, item, item_key)
            child_jobs.append(child_job)
            parent_job.child_job_ids.append(child_job.id)
            # Yield every 20 items to let other requests through
            if i > 0 and i % 20 == 0:
                await asyncio.sleep(0)

        # Save parent with child references
        parent_job.status = JobStatus.RUNNING
        parent_job.updated_at = datetime.utcnow()
        await self.state_manager.save_job(parent_job)

        # Save all child jobs
        for i, child_job in enumerate(child_jobs):
            await self.state_manager.save_job(child_job)
            # Yield every 10 saves to let other requests through
            if i > 0 and i % 10 == 0:
                await asyncio.sleep(0)

        # Publish parent started event
        if self.event_publisher:
            await self.event_publisher.job_started(parent_job)
            # Publish initial progress so frontend knows total items right away
            initial_progress = {
                'total_items': len(items),
                'completed': 0,
                'failed': 0,
                'running': 0,
                'pending': len(items),
                'percent': 0,
                # Compatibility fields for frontend
                'total': len(items),
                'total_tasks': len(items),
            }
            await self.event_publisher.progress_update(parent_job.id, initial_progress)
            await self.event_publisher.message(
                parent_job.id,
                f"Starting parallel execution of {len(items)} items (max {max_concurrent} concurrent)",
                "info"
            )

        # Execute child jobs with concurrency control
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_child_with_semaphore(child_job: WorkflowJob):
            async with semaphore:
                try:
                    return await child_workflow_executor(child_job)
                except Exception as e:
                    logger.error(f"Child job {child_job.id} failed: {str(e)}")
                    child_job.status = JobStatus.FAILED
                    child_job.errors.append(str(e))
                    child_job.completed_at = datetime.utcnow()
                    await self.state_manager.save_job(child_job)
                    return child_job

        # Start progress tracking task
        progress_task = asyncio.create_task(
            self._track_progress_loop(parent_job.id, len(child_jobs))
        )

        # Execute all children in parallel (with semaphore limiting concurrency)
        try:
            completed_children = await asyncio.gather(
                *[run_child_with_semaphore(child) for child in child_jobs],
                return_exceptions=True
            )
        finally:
            # Stop progress tracking
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        # Aggregate results
        parent_job = await self._aggregate_child_results(parent_job, completed_children, item_key)

        # Determine final parent status
        parent_job = self._determine_parent_status(parent_job, completed_children)
        parent_job.completed_at = datetime.utcnow()
        await self.state_manager.save_job(parent_job)

        # Publish completion event
        if self.event_publisher:
            if parent_job.status == JobStatus.FAILED:
                await self.event_publisher.job_failed(parent_job)
            else:
                await self.event_publisher.job_completed(parent_job)

        logger.info(f"Parallel workflow {parent_job.workflow_name} completed with status {parent_job.status}")
        return parent_job

    def _create_child_job(
        self,
        parent_job: WorkflowJob,
        item: Dict[str, Any],
        item_key: str
    ) -> WorkflowJob:
        """
        Create a child job for a single item.

        Args:
            parent_job: The parent job
            item: The item data for this child
            item_key: Key to identify the item

        Returns:
            New WorkflowJob for this item
        """
        item_id = item.get(item_key) or item.get('id') or str(uuid.uuid4())[:8]

        # Build input_data for child:
        # - Keep all parent input_data except 'units'
        # - Set 'units' to a list containing just this single item
        # This way phase executors work unchanged (they iterate over 'units')
        child_input_data = {
            k: v for k, v in parent_job.input_data.items()
            if k != 'units'
        }
        child_input_data['units'] = [item]  # Single-item list
        child_input_data['item'] = item      # Also store directly for convenience

        child_job = WorkflowJob(
            id=f"{parent_job.id}-{item_id}",
            workflow_name=f"{parent_job.workflow_name}_item",
            status=JobStatus.PENDING,
            user_id=parent_job.user_id,
            controller_id=parent_job.controller_id,
            venue_id=parent_job.venue_id,
            tenant_id=parent_job.tenant_id,
            options=parent_job.options.copy(),
            parent_job_id=parent_job.id,
            parallel_config={'item_key': item_key},
            input_data=child_input_data,
            phases=[]  # Phases will be created by the child executor
        )

        return child_job

    async def _track_progress_loop(self, parent_job_id: str, total_children: int):
        """
        Periodically update parent progress based on child status.

        Args:
            parent_job_id: Parent job ID
            total_children: Total number of child jobs
        """
        while True:
            try:
                await asyncio.sleep(2)  # Update every 2 seconds

                parent_job = await self.state_manager.get_job(parent_job_id)
                if not parent_job:
                    break

                # Check for cancellation
                if await self.state_manager.is_cancelled(parent_job_id):
                    logger.info(f"Parent job {parent_job_id} cancelled - stopping progress tracking")
                    break

                # Check if job has reached terminal state
                if parent_job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL, JobStatus.CANCELLED):
                    logger.info(f"Parent job {parent_job_id} is {parent_job.status} - stopping progress tracking")
                    break

                # Count completed children using batch fetch for efficiency
                completed = 0
                failed = 0
                running = 0

                # Use MGET to fetch all child jobs in one Redis call (much faster than N individual calls)
                if parent_job.child_job_ids:
                    keys = [f"workflow:jobs:{child_id}" for child_id in parent_job.child_job_ids]
                    child_data_list = await self.state_manager.redis.mget(keys)

                    for child_data in child_data_list:
                        if child_data:
                            try:
                                child_dict = json.loads(child_data)
                                status = child_dict.get('status', '')
                                if status == 'COMPLETED':
                                    completed += 1
                                elif status == 'FAILED':
                                    failed += 1
                                elif status == 'RUNNING':
                                    running += 1
                            except Exception:
                                pass  # Skip malformed entries

                # Publish progress
                if self.event_publisher:
                    progress = {
                        # Parallel-specific fields
                        'total_items': total_children,
                        'completed': completed,
                        'failed': failed,
                        'running': running,
                        'pending': total_children - completed - failed - running,
                        'percent': round((completed + failed) / total_children * 100, 2) if total_children > 0 else 0,
                        # Compatibility fields for frontend (expects total_tasks or total)
                        'total': total_children,
                        'total_tasks': total_children,
                    }
                    await self.event_publisher.progress_update(parent_job_id, progress)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Progress tracking error: {str(e)}")
                await asyncio.sleep(5)  # Back off on errors

    async def _aggregate_child_results(
        self,
        parent_job: WorkflowJob,
        completed_children: List,
        item_key: str
    ) -> WorkflowJob:
        """
        Aggregate results from all child jobs into parent.

        Args:
            parent_job: The parent job
            completed_children: List of completed child jobs (or exceptions)
            item_key: Key identifying items

        Returns:
            Updated parent job with aggregated results
        """
        # Reload parent from Redis to get latest state
        parent_job = await self.state_manager.get_job(parent_job.id) or parent_job

        aggregated_resources = {}
        all_errors = []
        child_summaries = []

        for child in completed_children:
            if isinstance(child, Exception):
                all_errors.append(str(child))
                continue

            if isinstance(child, WorkflowJob):
                # Aggregate created resources
                for resource_type, resources in child.created_resources.items():
                    if resource_type not in aggregated_resources:
                        aggregated_resources[resource_type] = []
                    aggregated_resources[resource_type].extend(resources)

                # Collect errors
                all_errors.extend(child.errors)

                # Child summary
                item_id = child.get_item_identifier() or child.id
                child_summaries.append({
                    'item_id': item_id,
                    'status': child.status.value,
                    'phases_completed': len([p for p in child.phases if p.status == PhaseStatus.COMPLETED]),
                    'phases_failed': len([p for p in child.phases if p.status == PhaseStatus.FAILED]),
                    'errors': child.errors
                })

        parent_job.created_resources = aggregated_resources
        parent_job.errors = all_errors
        parent_job.summary = {
            'total_items': len(completed_children),
            'completed': len([c for c in completed_children if isinstance(c, WorkflowJob) and c.status == JobStatus.COMPLETED]),
            'failed': len([c for c in completed_children if isinstance(c, WorkflowJob) and c.status == JobStatus.FAILED]),
            'partial': len([c for c in completed_children if isinstance(c, WorkflowJob) and c.status == JobStatus.PARTIAL]),
            'child_summaries': child_summaries,
            'resources': {k: len(v) for k, v in aggregated_resources.items()}
        }

        return parent_job

    def _determine_parent_status(
        self,
        parent_job: WorkflowJob,
        completed_children: List
    ) -> WorkflowJob:
        """
        Determine parent job status based on child results.

        Args:
            parent_job: The parent job
            completed_children: List of completed child jobs

        Returns:
            Parent job with updated status
        """
        successful = 0
        failed = 0

        for child in completed_children:
            if isinstance(child, Exception):
                failed += 1
            elif isinstance(child, WorkflowJob):
                if child.status == JobStatus.COMPLETED:
                    successful += 1
                elif child.status in (JobStatus.FAILED, JobStatus.PARTIAL):
                    failed += 1

        if failed == 0:
            parent_job.status = JobStatus.COMPLETED
        elif successful == 0:
            parent_job.status = JobStatus.FAILED
        else:
            parent_job.status = JobStatus.PARTIAL

        return parent_job

    async def cancel_parallel_workflow(self, parent_job_id: str) -> bool:
        """
        Cancel a parallel workflow and all its child jobs.

        Args:
            parent_job_id: Parent job ID

        Returns:
            True if cancellation was initiated
        """
        parent_job = await self.state_manager.get_job(parent_job_id)
        if not parent_job:
            return False

        # Set cancellation flag on parent
        await self.state_manager.set_cancelled(parent_job_id)

        # Set cancellation flag on all children
        for i, child_id in enumerate(parent_job.child_job_ids):
            await self.state_manager.set_cancelled(child_id)
            # Yield every 20 to prevent blocking
            if i > 0 and i % 20 == 0:
                await asyncio.sleep(0)

        logger.info(f"Cancelled parallel workflow {parent_job_id} and {len(parent_job.child_job_ids)} child jobs")
        return True

    async def get_parallel_progress(self, parent_job_id: str) -> Dict[str, Any]:
        """
        Get detailed progress for a parallel workflow.

        Args:
            parent_job_id: Parent job ID

        Returns:
            Progress details including per-item status
        """
        parent_job = await self.state_manager.get_job(parent_job_id)
        if not parent_job:
            return {'error': 'Job not found'}

        item_statuses = []

        # Use MGET to fetch all child jobs in one Redis call
        if parent_job.child_job_ids:
            keys = [f"workflow:jobs:{child_id}" for child_id in parent_job.child_job_ids]
            child_data_list = await self.state_manager.redis.mget(keys)

            for i, child_data in enumerate(child_data_list):
                if child_data:
                    try:
                        child_dict = json.loads(child_data)
                        if 'user_id' not in child_dict:
                            child_dict['user_id'] = 0
                        child = WorkflowJob(**child_dict)

                        item_statuses.append({
                            'id': child.id,
                            'item_id': child.get_item_identifier(),
                            'status': child.status.value if hasattr(child.status, 'value') else child.status,
                            'current_phase': child.current_phase_id,
                            'progress': child.get_progress_stats() if child.phases else {}
                        })
                    except Exception as e:
                        logger.warning(f"Failed to deserialize child job: {e}")

                # Yield every 20 children to prevent blocking
                if i > 0 and i % 20 == 0:
                    await asyncio.sleep(0)

        return {
            'parent_id': parent_job_id,
            'parent_status': parent_job.status.value if hasattr(parent_job.status, 'value') else parent_job.status,
            'total_items': len(parent_job.child_job_ids),
            'items': item_statuses,
            'summary': {
                'completed': len([i for i in item_statuses if i['status'] == 'COMPLETED']),
                'failed': len([i for i in item_statuses if i['status'] == 'FAILED']),
                'running': len([i for i in item_statuses if i['status'] == 'RUNNING']),
                'pending': len([i for i in item_statuses if i['status'] == 'PENDING']),
            }
        }
