"""
Workflow V2 State Manager

Redis-backed state management for V2 workflow engine.
Supports per-unit atomic updates for multi-worker parallel execution.

Redis Key Schema:
    workflow:v2:jobs:{job_id}                    → WorkflowJobV2 (full state)
    workflow:v2:jobs:{job_id}:units:{unit_id}    → UnitMapping (per-unit state)
    workflow:v2:jobs:{job_id}:activities          → Set of pending activity IDs
    workflow:v2:activities:pending                → Hash: activity_id → ActivityRef JSON
    workflow:v2:events:{job_id}                   → Pub/Sub channel for job events
    workflow:v2:events:global                     → Global event channel
    workflow:v2:jobs:index                        → Sorted Set: job_id → timestamp
    workflow:v2:jobs:by_venue:{venue_id}          → Set of job IDs
    workflow:v2:jobs:active                       → Set of running job IDs
    workflow:v2:jobs:{job_id}:lock                → Distributed lock
    workflow:v2:units:{job_id}:{unit_id}:lock     → Per-unit lock
"""

import json
import logging
import asyncio
import redis.asyncio as redis
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from workflow.v2.models import (
    WorkflowJobV2,
    UnitMapping,
    JobStatus,
    PhaseStatus,
    UnitStatus,
    ActivityRef,
)

logger = logging.getLogger(__name__)

# TTL Settings
JOB_TTL_SECONDS = 604800      # 7 days
LOCK_TTL_SECONDS = 300         # 5 minutes
UNIT_LOCK_TTL_SECONDS = 60     # 1 minute (shorter for fine-grained ops)

# Key prefixes
PREFIX = "workflow:v2"


class RedisStateManagerV2:
    """
    Manages V2 workflow state in Redis.

    Supports multi-worker execution with:
    - Per-unit atomic updates (workers can update different units concurrently)
    - Distributed locks for concurrent access
    - Pub/Sub for event notifications
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    # =========================================================================
    # Job Operations
    # =========================================================================

    async def save_job(self, job: WorkflowJobV2) -> bool:
        """Save full job state to Redis."""
        key = f"{PREFIX}:jobs:{job.id}"
        job.updated_at = datetime.utcnow()
        job_data = job.model_dump_json()

        pipe = self.redis.pipeline()
        pipe.setex(key, JOB_TTL_SECONDS, job_data)
        pipe.zadd(f"{PREFIX}:jobs:index", {job.id: job.created_at.timestamp()})

        if job.venue_id:
            pipe.sadd(f"{PREFIX}:jobs:by_venue:{job.venue_id}", job.id)

        if job.status == JobStatus.RUNNING:
            pipe.sadd(f"{PREFIX}:jobs:active", job.id)
        else:
            pipe.srem(f"{PREFIX}:jobs:active", job.id)

        await pipe.execute()
        return True

    async def get_job(self, job_id: str) -> Optional[WorkflowJobV2]:
        """Retrieve full job state from Redis, including fresh unit data."""
        key = f"{PREFIX}:jobs:{job_id}"
        data = await self.redis.get(key)
        if not data:
            return None

        job_dict = json.loads(data)
        job = WorkflowJobV2(**job_dict)

        # Reload units from their separate Redis keys (they may have been updated
        # independently via save_unit() by parallel workers)
        fresh_units = await self.get_all_units(job_id)
        if fresh_units:
            job.units = fresh_units

        return job

    async def delete_job(self, job_id: str) -> bool:
        """Delete job and all related data."""
        # Find all related keys
        keys = []
        async for key in self.redis.scan_iter(match=f"{PREFIX}:jobs:{job_id}*"):
            keys.append(key)
        async for key in self.redis.scan_iter(match=f"{PREFIX}:units:{job_id}*"):
            keys.append(key)

        if keys:
            await self.redis.delete(*keys)

        # Clean up indexes
        await self.redis.zrem(f"{PREFIX}:jobs:index", job_id)
        await self.redis.srem(f"{PREFIX}:jobs:active", job_id)

        return True

    async def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        venue_id: str = None,
        status: JobStatus = None
    ) -> List[WorkflowJobV2]:
        """List jobs with optional filtering."""
        if venue_id:
            job_ids = list(await self.redis.smembers(
                f"{PREFIX}:jobs:by_venue:{venue_id}"
            ))
        else:
            job_ids = await self.redis.zrevrange(
                f"{PREFIX}:jobs:index", offset, offset + limit - 1
            )

        if not job_ids:
            return []

        keys = [f"{PREFIX}:jobs:{jid}" for jid in job_ids]
        job_data_list = await self.redis.mget(keys)

        jobs = []
        for job_data in job_data_list:
            if job_data:
                try:
                    job = WorkflowJobV2(**json.loads(job_data))
                    if status and job.status != status:
                        continue
                    jobs.append(job)
                except Exception as e:
                    logger.warning(f"Failed to deserialize job: {e}")

        # Sort by created_at descending
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error: str = None
    ) -> bool:
        """Update job status atomically."""
        job = await self.get_job(job_id)
        if not job:
            return False

        job.status = status
        if error:
            job.errors.append(error)
        if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL):
            job.completed_at = datetime.utcnow()

        return await self.save_job(job)

    # =========================================================================
    # Unit Operations (atomic per-unit updates)
    # =========================================================================

    async def save_unit(self, job_id: str, unit: UnitMapping) -> bool:
        """
        Save a single unit's state atomically.
        This is the primary update method for multi-worker execution.
        """
        key = f"{PREFIX}:jobs:{job_id}:units:{unit.unit_id}"
        unit_data = unit.model_dump_json()
        await self.redis.setex(key, JOB_TTL_SECONDS, unit_data)
        return True

    async def get_unit(self, job_id: str, unit_id: str) -> Optional[UnitMapping]:
        """Get a single unit's state."""
        key = f"{PREFIX}:jobs:{job_id}:units:{unit_id}"
        data = await self.redis.get(key)
        if not data:
            return None
        return UnitMapping(**json.loads(data))

    async def save_all_units(self, job_id: str, units: Dict[str, UnitMapping]) -> bool:
        """Save all units in a pipeline (used during initial setup)."""
        pipe = self.redis.pipeline()
        for unit_id, unit in units.items():
            key = f"{PREFIX}:jobs:{job_id}:units:{unit_id}"
            pipe.setex(key, JOB_TTL_SECONDS, unit.model_dump_json())
        await pipe.execute()
        return True

    async def get_all_units(self, job_id: str) -> Dict[str, UnitMapping]:
        """Get all units for a job."""
        units = {}
        async for key in self.redis.scan_iter(
            match=f"{PREFIX}:jobs:{job_id}:units:*"
        ):
            data = await self.redis.get(key)
            if data:
                unit = UnitMapping(**json.loads(data))
                units[unit.unit_id] = unit
        return units

    async def update_unit_phase_status(
        self,
        job_id: str,
        unit_id: str,
        phase_id: str,
        completed: bool = False,
        failed: bool = False,
        error: str = None
    ) -> Optional[UnitMapping]:
        """
        Update a unit's phase status atomically.
        Returns the updated unit mapping.
        """
        async with self._unit_lock(job_id, unit_id):
            unit = await self.get_unit(job_id, unit_id)
            if not unit:
                return None

            if completed:
                unit.current_phase = None
                if phase_id not in unit.completed_phases:
                    unit.completed_phases.append(phase_id)
            elif failed:
                unit.current_phase = None
                if phase_id not in unit.failed_phases:
                    unit.failed_phases.append(phase_id)
                if error:
                    unit.phase_errors[phase_id] = error
            else:
                # Starting phase
                unit.current_phase = phase_id
                unit.status = UnitStatus.RUNNING

            await self.save_unit(job_id, unit)
            return unit

    async def update_unit_resolved(
        self,
        job_id: str,
        unit_id: str,
        field_name: str,
        value: Any
    ) -> bool:
        """
        Update a single resolved field on a unit.
        Used to enrich unit mapping as phases complete.
        """
        async with self._unit_lock(job_id, unit_id):
            unit = await self.get_unit(job_id, unit_id)
            if not unit:
                return False

            if hasattr(unit.resolved, field_name):
                setattr(unit.resolved, field_name, value)
                await self.save_unit(job_id, unit)
                return True
            elif field_name in ('extra',):
                # Merge into extra dict
                unit.resolved.extra.update(value if isinstance(value, dict) else {field_name: value})
                await self.save_unit(job_id, unit)
                return True

            logger.warning(f"Unknown resolved field: {field_name}")
            return False

    # =========================================================================
    # Global Phase Status (for per_unit=False phases)
    # =========================================================================

    async def update_global_phase_status(
        self,
        job_id: str,
        phase_id: str,
        status: PhaseStatus,
        result: Dict[str, Any] = None
    ) -> bool:
        """Update status of a global (non-per-unit) phase."""
        job = await self.get_job(job_id)
        if not job:
            return False

        job.global_phase_status[phase_id] = status
        if result:
            job.global_phase_results[phase_id] = result

        return await self.save_job(job)

    # =========================================================================
    # Activity Tracking
    # =========================================================================

    async def register_activity(self, activity: ActivityRef) -> bool:
        """Register a pending R1 activity."""
        await self.redis.hset(
            f"{PREFIX}:activities:pending",
            activity.activity_id,
            activity.model_dump_json()
        )
        await self.redis.sadd(
            f"{PREFIX}:jobs:{activity.job_id}:activities",
            activity.activity_id
        )
        return True

    async def get_pending_activities(self) -> Dict[str, ActivityRef]:
        """Get all pending activities across all jobs."""
        data = await self.redis.hgetall(f"{PREFIX}:activities:pending")
        activities = {}
        for act_id, act_json in data.items():
            try:
                act_id_str = act_id if isinstance(act_id, str) else act_id.decode()
                act_json_str = act_json if isinstance(act_json, str) else act_json.decode()
                activities[act_id_str] = ActivityRef(**json.loads(act_json_str))
            except Exception as e:
                logger.warning(f"Failed to deserialize activity {act_id}: {e}")
        return activities

    async def get_job_activities(self, job_id: str) -> List[str]:
        """Get pending activity IDs for a specific job."""
        return list(await self.redis.smembers(
            f"{PREFIX}:jobs:{job_id}:activities"
        ))

    async def complete_activity(self, activity_id: str, job_id: str) -> bool:
        """Remove a completed activity from tracking."""
        pipe = self.redis.pipeline()
        pipe.hdel(f"{PREFIX}:activities:pending", activity_id)
        pipe.srem(f"{PREFIX}:jobs:{job_id}:activities", activity_id)
        await pipe.execute()
        return True

    # =========================================================================
    # Resource Tracking
    # =========================================================================

    async def track_created_resource(
        self,
        job_id: str,
        resource_type: str,
        resource_data: Dict[str, Any]
    ) -> bool:
        """Track a resource created during the workflow."""
        job = await self.get_job(job_id)
        if not job:
            return False

        if resource_type not in job.created_resources:
            job.created_resources[resource_type] = []
        job.created_resources[resource_type].append(resource_data)

        return await self.save_job(job)

    # =========================================================================
    # Events (Pub/Sub)
    # =========================================================================

    async def publish_event(
        self,
        job_id: str,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """Publish an event for a job."""
        event = {
            "type": event_type,
            "job_id": job_id,
            "timestamp": datetime.utcnow().isoformat(),
            **data
        }
        await self.redis.publish(
            f"{PREFIX}:events:{job_id}",
            json.dumps(event)
        )
        # Also publish to global channel
        await self.redis.publish(
            f"{PREFIX}:events:global",
            json.dumps(event)
        )

    async def subscribe_job_events(self, job_id: str):
        """Get a pub/sub subscription for job events."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"{PREFIX}:events:{job_id}")
        return pubsub

    # =========================================================================
    # Cancellation
    # =========================================================================

    async def set_cancelled(self, job_id: str) -> bool:
        """Mark a job as cancelled."""
        key = f"{PREFIX}:jobs:{job_id}:cancelled"
        await self.redis.setex(key, JOB_TTL_SECONDS, "1")
        return True

    async def is_cancelled(self, job_id: str) -> bool:
        """Check if a job has been cancelled."""
        key = f"{PREFIX}:jobs:{job_id}:cancelled"
        result = await self.redis.get(key)
        return result is not None and (result == "1" or result == b"1")

    # =========================================================================
    # Locking
    # =========================================================================

    async def acquire_job_lock(self, job_id: str, timeout: int = LOCK_TTL_SECONDS) -> bool:
        """Acquire distributed lock for a job."""
        key = f"{PREFIX}:jobs:{job_id}:lock"
        return await self.redis.set(key, "1", nx=True, ex=timeout)

    async def release_job_lock(self, job_id: str) -> bool:
        """Release distributed lock for a job."""
        key = f"{PREFIX}:jobs:{job_id}:lock"
        return (await self.redis.delete(key)) > 0

    def _unit_lock(self, job_id: str, unit_id: str):
        """Context manager for per-unit locking."""
        return _RedisLock(
            self.redis,
            f"{PREFIX}:units:{job_id}:{unit_id}:lock",
            UNIT_LOCK_TTL_SECONDS
        )

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def cleanup_expired_jobs(self) -> int:
        """Clean up expired jobs."""
        cutoff = datetime.utcnow() - timedelta(seconds=JOB_TTL_SECONDS)
        expired_ids = await self.redis.zrangebyscore(
            f"{PREFIX}:jobs:index", 0, cutoff.timestamp()
        )

        count = 0
        for job_id in expired_ids:
            if await self.delete_job(job_id):
                count += 1
        return count


class _RedisLock:
    """Async context manager for distributed Redis locks."""

    def __init__(self, redis_client: redis.Redis, key: str, ttl: int):
        self.redis = redis_client
        self.key = key
        self.ttl = ttl

    async def __aenter__(self):
        # Spin-wait with backoff
        for attempt in range(50):  # Max ~5 seconds
            acquired = await self.redis.set(self.key, "1", nx=True, ex=self.ttl)
            if acquired:
                return self
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Could not acquire lock: {self.key}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.delete(self.key)
        return False
