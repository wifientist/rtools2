"""
Workflow State Manager

Handles Redis CRUD operations for workflow state:
- Save/retrieve job state
- Track phases and tasks
- Manage created resources
- Redis locks for concurrent access
"""

import asyncio
import json
import logging
import redis.asyncio as redis
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from workflow.models import WorkflowJob, Phase, Task, JobStatus

logger = logging.getLogger(__name__)

# TTL Settings
JOB_TTL_SECONDS = 604800  # 7 days
LOCK_TTL_SECONDS = 300     # 5 minutes


class RedisStateManager:
    """Manages workflow state in Redis (async)"""

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize state manager

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client

    # ==================== Job Operations ====================

    async def save_job(self, job: WorkflowJob) -> bool:
        """
        Save workflow job to Redis

        Args:
            job: WorkflowJob instance

        Returns:
            bool: True if successful
        """
        key = f"workflow:jobs:{job.id}"

        # Update timestamp
        job.updated_at = datetime.utcnow()

        # Serialize job to JSON
        job_data = job.model_dump_json()

        # Store in Redis with TTL
        await self.redis.setex(key, JOB_TTL_SECONDS, job_data)

        # Add to job index (sorted set by created_at)
        await self.redis.zadd(
            "workflow:jobs:index",
            {job.id: job.created_at.timestamp()}
        )

        return True

    async def get_job(self, job_id: str) -> Optional[WorkflowJob]:
        """
        Retrieve workflow job from Redis

        Args:
            job_id: Job ID

        Returns:
            WorkflowJob instance or None if not found
        """
        key = f"workflow:jobs:{job_id}"
        data = await self.redis.get(key)

        if not data:
            return None

        job_dict = json.loads(data)

        # Handle legacy jobs without user_id field (added in RBAC update)
        if 'user_id' not in job_dict:
            job_dict['user_id'] = 0  # Default for legacy jobs

        return WorkflowJob(**job_dict)

    async def update_job_status(self, job_id: str, status: JobStatus, error: str = None) -> bool:
        """
        Update job status

        Args:
            job_id: Job ID
            status: New job status
            error: Optional error message

        Returns:
            bool: True if successful
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        job.status = status
        job.updated_at = datetime.utcnow()

        if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL]:
            job.completed_at = datetime.utcnow()

        if error:
            job.errors.append(error)

        return await self.save_job(job)

    async def delete_job(self, job_id: str) -> bool:
        """
        Delete job and all related data

        Args:
            job_id: Job ID

        Returns:
            bool: True if successful
        """
        # Get all keys related to this job
        pattern = f"workflow:jobs:{job_id}*"
        keys = []
        async for key in self.redis.scan_iter(match=pattern):
            keys.append(key)

        if keys:
            await self.redis.delete(*keys)

        # Remove from index
        await self.redis.zrem("workflow:jobs:index", job_id)

        return True

    async def list_jobs(self, limit: int = 100, offset: int = 0) -> List[WorkflowJob]:
        """
        List recent jobs

        Args:
            limit: Maximum number of jobs to return
            offset: Offset for pagination

        Returns:
            List of WorkflowJob instances
        """
        # Get job IDs from sorted set (newest first)
        job_ids = await self.redis.zrevrange(
            "workflow:jobs:index",
            offset,
            offset + limit - 1
        )

        if not job_ids:
            return []

        # Use MGET to fetch all jobs in a single Redis call (much faster!)
        keys = [f"workflow:jobs:{job_id}" for job_id in job_ids]
        job_data_list = await self.redis.mget(keys)

        jobs = []
        for i, job_data in enumerate(job_data_list):
            if job_data:
                try:
                    job_dict = json.loads(job_data)
                    # Handle legacy jobs without user_id field
                    if 'user_id' not in job_dict:
                        job_dict['user_id'] = 0
                    jobs.append(WorkflowJob(**job_dict))
                except Exception as e:
                    logger.warning(f"Failed to deserialize job: {e}")

            # Yield to event loop every 20 jobs to prevent blocking other requests
            if i > 0 and i % 20 == 0:
                await asyncio.sleep(0)

        return jobs

    # ==================== Phase Operations ====================

    async def update_phase(self, job_id: str, phase: Phase) -> bool:
        """
        Update phase state within a job

        Args:
            job_id: Job ID
            phase: Phase instance

        Returns:
            bool: True if successful
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        # Find and update phase
        for i, p in enumerate(job.phases):
            if p.id == phase.id:
                job.phases[i] = phase
                break

        return await self.save_job(job)

    async def get_phase(self, job_id: str, phase_id: str) -> Optional[Phase]:
        """
        Get a specific phase from a job

        Args:
            job_id: Job ID
            phase_id: Phase ID

        Returns:
            Phase instance or None
        """
        job = await self.get_job(job_id)
        if not job:
            return None

        return job.get_phase_by_id(phase_id)

    # ==================== Task Operations ====================

    async def update_task(self, job_id: str, phase_id: str, task: Task) -> bool:
        """
        Update task state within a phase

        Args:
            job_id: Job ID
            phase_id: Phase ID
            task: Task instance

        Returns:
            bool: True if successful
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        # Find phase
        phase = job.get_phase_by_id(phase_id)
        if not phase:
            return False

        # Find and update task
        for i, t in enumerate(phase.tasks):
            if t.id == task.id:
                phase.tasks[i] = task
                break

        # Save updated phase
        return await self.update_phase(job_id, phase)

    # ==================== Resource Tracking ====================

    async def add_created_resource(
        self,
        job_id: str,
        resource_type: str,
        resource_data: Dict[str, Any]
    ) -> bool:
        """
        Track a created resource

        Args:
            job_id: Job ID
            resource_type: Type of resource (e.g., 'identity_groups', 'dpsk_pools')
            resource_data: Resource metadata

        Returns:
            bool: True if successful
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        if resource_type not in job.created_resources:
            job.created_resources[resource_type] = []

        job.created_resources[resource_type].append(resource_data)
        return await self.save_job(job)

    async def get_created_resources(
        self,
        job_id: str,
        resource_type: str = None
    ) -> Dict[str, List[Dict]]:
        """
        Get created resources

        Args:
            job_id: Job ID
            resource_type: Optional filter by resource type

        Returns:
            Dict of resources by type
        """
        job = await self.get_job(job_id)
        if not job:
            return {}

        if resource_type:
            return {resource_type: job.created_resources.get(resource_type, [])}

        return job.created_resources

    # ==================== Locking ====================

    async def acquire_lock(self, job_id: str, timeout: int = LOCK_TTL_SECONDS) -> bool:
        """
        Acquire distributed lock for job

        Args:
            job_id: Job ID
            timeout: Lock timeout in seconds

        Returns:
            bool: True if lock acquired
        """
        lock_key = f"workflow:jobs:{job_id}:lock"
        return await self.redis.set(lock_key, "1", nx=True, ex=timeout)

    async def release_lock(self, job_id: str) -> bool:
        """
        Release distributed lock for job

        Args:
            job_id: Job ID

        Returns:
            bool: True if lock released
        """
        lock_key = f"workflow:jobs:{job_id}:lock"
        result = await self.redis.delete(lock_key)
        return result > 0

    # ==================== Progress Tracking ====================

    async def update_progress(self, job_id: str) -> bool:
        """
        Update job progress statistics

        Args:
            job_id: Job ID

        Returns:
            bool: True if successful
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        # Calculate progress
        progress = job.get_progress_stats()
        job.summary['progress'] = progress

        return await self.save_job(job)

    # ==================== Cancellation ====================

    async def set_cancelled(self, job_id: str) -> bool:
        """
        Mark a job as cancelled

        Args:
            job_id: Job ID to cancel

        Returns:
            bool: True if cancellation flag was set
        """
        cancel_key = f"workflow:jobs:{job_id}:cancelled"
        await self.redis.setex(cancel_key, JOB_TTL_SECONDS, "1")
        return True

    async def is_cancelled(self, job_id: str) -> bool:
        """
        Check if a job has been cancelled

        Args:
            job_id: Job ID to check

        Returns:
            bool: True if job is cancelled
        """
        cancel_key = f"workflow:jobs:{job_id}:cancelled"
        result = await self.redis.get(cancel_key)
        return result == "1"

    async def clear_cancelled(self, job_id: str) -> bool:
        """
        Clear cancellation flag for a job

        Args:
            job_id: Job ID

        Returns:
            bool: True if cleared
        """
        cancel_key = f"workflow:jobs:{job_id}:cancelled"
        await self.redis.delete(cancel_key)
        return True

    # ==================== Cleanup ====================

    async def cleanup_expired_jobs(self) -> int:
        """
        Clean up expired jobs (older than TTL)

        Note: Redis TTL should handle this automatically,
        but this provides manual cleanup if needed.

        Returns:
            int: Number of jobs cleaned up
        """
        cutoff = datetime.utcnow() - timedelta(seconds=JOB_TTL_SECONDS)
        cutoff_timestamp = cutoff.timestamp()

        # Get expired job IDs
        expired_ids = await self.redis.zrangebyscore(
            "workflow:jobs:index",
            0,
            cutoff_timestamp
        )

        count = 0
        for job_id in expired_ids:
            if await self.delete_job(job_id):
                count += 1

        return count
