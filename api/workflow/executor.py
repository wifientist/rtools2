"""
Task Executor

Handles execution of individual tasks with:
- Retry logic with exponential backoff
- Async task polling
- Error handling
- Status tracking
"""

import asyncio
from typing import Callable, Any, Dict
from datetime import datetime
from workflow.models import Task, TaskStatus
import logging

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Executes individual workflow tasks with retry and error handling"""

    def __init__(
        self,
        max_retries: int = 3,
        retry_backoff_base: int = 2,
        r1_client=None,
        event_publisher=None,
        state_manager=None,
        activation_semaphore=None
    ):
        """
        Initialize task executor

        Args:
            max_retries: Maximum retry attempts per task
            retry_backoff_base: Base for exponential backoff (seconds)
            r1_client: R1Client instance for async task polling
            event_publisher: WorkflowEventPublisher for real-time updates
            state_manager: RedisStateManager for cancellation checking
            activation_semaphore: Optional semaphore for throttling SSID activation
        """
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.r1_client = r1_client
        self.event_publisher = event_publisher
        self.state_manager = state_manager
        self.activation_semaphore = activation_semaphore
        self._current_job_id = None
        self._current_phase_id = None

    async def is_cancelled(self) -> bool:
        """Check if the current job has been cancelled"""
        if self.state_manager and self._current_job_id:
            return await self.state_manager.is_cancelled(self._current_job_id)
        return False

    async def execute_task(
        self,
        task: Task,
        executor_func: Callable,
        context: Dict[str, Any]
    ) -> Task:
        """
        Execute a single task with retry logic

        Args:
            task: Task instance to execute
            executor_func: Async function to execute the task
            context: Context data (job config, previous phase results, etc.)

        Returns:
            Updated Task instance
        """
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.utcnow()

        # Emit task_started event
        if self.event_publisher and self._current_job_id and self._current_phase_id:
            await self.event_publisher.task_started(
                self._current_job_id,
                self._current_phase_id,
                task
            )

        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                logger.info(f"Executing task {task.id} ({task.name})")

                # Execute the task function
                result = await executor_func(task, context, self.r1_client)

                # Check if result is a 202 async response with request_id
                if isinstance(result, dict) and result.get('request_id'):
                    # Handle async R1 task
                    task.request_id = result['request_id']
                    task.status = TaskStatus.POLLING
                    logger.info(f"Task {task.id} returned async request_id: {task.request_id}")

                    # Poll for completion
                    final_result = await self._poll_async_task(task, context)
                    task.output_data = final_result
                else:
                    # Synchronous result
                    task.output_data = result

                # Mark as completed
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.utcnow()
                logger.info(f"Task {task.id} completed successfully")

                # Emit task_completed event
                if self.event_publisher and self._current_job_id and self._current_phase_id:
                    await self.event_publisher.task_completed(
                        self._current_job_id,
                        self._current_phase_id,
                        task
                    )

                return task

            except Exception as e:
                retry_count += 1
                task.retry_count = retry_count
                error_msg = str(e)
                logger.error(f"Task {task.id} failed (attempt {retry_count}/{self.max_retries + 1}): {error_msg}")

                if retry_count > self.max_retries:
                    # Max retries exceeded
                    task.status = TaskStatus.FAILED
                    task.error_message = f"Failed after {self.max_retries} retries: {error_msg}"
                    task.completed_at = datetime.utcnow()
                    logger.error(f"Task {task.id} failed permanently: {task.error_message}")

                    # Emit task_completed event (with failed status)
                    if self.event_publisher and self._current_job_id and self._current_phase_id:
                        await self.event_publisher.task_completed(
                            self._current_job_id,
                            self._current_phase_id,
                            task
                        )

                    return task

                # Exponential backoff
                backoff_seconds = self.retry_backoff_base ** retry_count
                logger.info(f"Retrying task {task.id} in {backoff_seconds} seconds...")
                await asyncio.sleep(backoff_seconds)

        return task

    async def _poll_async_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Poll R1 async task until completion

        Args:
            task: Task instance with request_id
            context: Context data with tenant_id, etc.

        Returns:
            Final task result data

        Raises:
            Exception: If polling fails or times out
        """
        if not self.r1_client:
            raise Exception("R1Client not available for async task polling")

        if not task.request_id:
            raise Exception("Task has no request_id for polling")

        tenant_id = context.get('tenant_id')
        max_polls = task.max_polls or 60
        sleep_seconds = 3

        logger.info(f"Polling async task {task.request_id} (max {max_polls} attempts)")

        try:
            result = await self.r1_client.await_task_completion(
                request_id=task.request_id,
                override_tenant_id=tenant_id,
                max_attempts=max_polls,
                sleep_seconds=sleep_seconds
            )
            task.poll_count += max_polls  # Update poll count
            return result

        except Exception as e:
            logger.error(f"Async polling failed for task {task.request_id}: {str(e)}")
            raise

    async def execute_tasks_parallel(
        self,
        tasks: list[Task],
        executor_func: Callable,
        context: Dict[str, Any],
        max_concurrent: int = 50,
        job_id: str = None,
        phase_id: str = None
    ) -> list[Task]:
        """
        Execute multiple tasks in parallel with throttling

        Args:
            tasks: List of Task instances
            executor_func: Async function to execute each task
            context: Context data
            max_concurrent: Maximum concurrent executions
            job_id: Job ID for event publishing
            phase_id: Phase ID for event publishing

        Returns:
            List of updated Task instances
        """
        # Set context for event publishing
        self._current_job_id = job_id or context.get('job_id')
        self._current_phase_id = phase_id

        semaphore = asyncio.Semaphore(max_concurrent)

        async def execute_with_semaphore(task: Task) -> Task:
            async with semaphore:
                # Check for cancellation before starting task
                if await self.is_cancelled():
                    logger.info(f"🛑 Job cancelled - skipping task {task.id}")
                    task.status = TaskStatus.FAILED
                    task.error_message = "Job cancelled by user"
                    return task
                return await self.execute_task(task, executor_func, context)

        logger.info(f"Executing {len(tasks)} tasks in parallel (max_concurrent={max_concurrent})")

        # Execute all tasks in parallel
        results = await asyncio.gather(
            *[execute_with_semaphore(task) for task in tasks],
            return_exceptions=False  # Let exceptions propagate
        )

        return results

    async def execute_tasks_sequential(
        self,
        tasks: list[Task],
        executor_func: Callable,
        context: Dict[str, Any],
        job_id: str = None,
        phase_id: str = None
    ) -> list[Task]:
        """
        Execute tasks sequentially (one after another)

        Args:
            tasks: List of Task instances
            executor_func: Async function to execute each task
            context: Context data
            job_id: Job ID for event publishing
            phase_id: Phase ID for event publishing

        Returns:
            List of updated Task instances
        """
        # Set context for event publishing
        self._current_job_id = job_id or context.get('job_id')
        self._current_phase_id = phase_id

        logger.info(f"Executing {len(tasks)} tasks sequentially")

        results = []
        for task in tasks:
            # Check for cancellation before each task
            if await self.is_cancelled():
                logger.info(f"🛑 Job cancelled - skipping remaining {len(tasks) - len(results)} tasks")
                # Mark remaining tasks as failed due to cancellation
                task.status = TaskStatus.FAILED
                task.error_message = "Job cancelled by user"
                results.append(task)
                # Skip remaining tasks
                for remaining_task in tasks[len(results):]:
                    remaining_task.status = TaskStatus.FAILED
                    remaining_task.error_message = "Job cancelled by user"
                    results.append(remaining_task)
                break

            result = await self.execute_task(task, executor_func, context)
            results.append(result)

            # Stop if task failed and is critical
            if result.status == TaskStatus.FAILED:
                logger.warning(f"Task {task.id} failed in sequential execution")
                # Continue anyway - let the workflow engine decide if it's critical

        return results
