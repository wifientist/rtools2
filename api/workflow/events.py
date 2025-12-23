"""
Workflow Event Publishing

Publishes workflow events to Redis pub/sub for real-time monitoring
"""

import json
import logging
from typing import Dict, Any
from datetime import datetime
from workflow.models import WorkflowJob, Phase, Task

logger = logging.getLogger(__name__)


class WorkflowEventPublisher:
    """Publishes workflow events to Redis pub/sub (async)"""

    def __init__(self, redis_client):
        """
        Initialize event publisher

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client

    async def _publish_event(self, job_id: str, event_type: str, data: Dict[str, Any]):
        """
        Publish event to Redis pub/sub

        Args:
            job_id: Job ID
            event_type: Event type (e.g., 'job_started', 'task_completed')
            data: Event data
        """
        channel = f"workflow:events:{job_id}"

        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        }

        try:
            await self.redis.publish(channel, json.dumps(event))
            logger.debug(f"Published event {event_type} to channel {channel}")
        except Exception as e:
            logger.error(f"Failed to publish event {event_type}: {str(e)}")

    async def job_started(self, job: WorkflowJob):
        """Publish job started event"""
        await self._publish_event(job.id, "job_started", {
            "job_id": job.id,
            "workflow_name": job.workflow_name,
            "total_phases": len(job.phases),
            "venue_id": job.venue_id
        })

    async def job_completed(self, job: WorkflowJob):
        """Publish job completed event"""
        await self._publish_event(job.id, "job_completed", {
            "job_id": job.id,
            "status": job.status,
            "summary": job.summary,
            "duration_seconds": (
                (job.completed_at - job.created_at).total_seconds()
                if job.completed_at and job.created_at else None
            )
        })

    async def job_failed(self, job: WorkflowJob):
        """Publish job failed event"""
        await self._publish_event(job.id, "job_failed", {
            "job_id": job.id,
            "errors": job.errors,
            "summary": job.summary
        })

    async def phase_started(self, job_id: str, phase: Phase):
        """Publish phase started event"""
        await self._publish_event(job_id, "phase_started", {
            "phase_id": phase.id,
            "phase_name": phase.name,
            "total_tasks": len(phase.tasks)
        })

    async def phase_completed(self, job_id: str, phase: Phase):
        """Publish phase completed event"""
        await self._publish_event(job_id, "phase_completed", {
            "phase_id": phase.id,
            "phase_name": phase.name,
            "status": phase.status,
            "completed_tasks": len([t for t in phase.tasks if t.status == "COMPLETED"]),
            "failed_tasks": len([t for t in phase.tasks if t.status == "FAILED"]),
            "duration_seconds": (
                (phase.completed_at - phase.started_at).total_seconds()
                if phase.completed_at and phase.started_at else None
            )
        })

    async def task_started(self, job_id: str, phase_id: str, task: Task):
        """Publish task started event"""
        await self._publish_event(job_id, "task_started", {
            "phase_id": phase_id,
            "task_id": task.id,
            "task_name": task.name
        })

    async def task_completed(self, job_id: str, phase_id: str, task: Task):
        """Publish task completed event"""
        await self._publish_event(job_id, "task_completed", {
            "phase_id": phase_id,
            "task_id": task.id,
            "task_name": task.name,
            "status": task.status,
            "duration_seconds": (
                (task.completed_at - task.started_at).total_seconds()
                if task.completed_at and task.started_at else None
            )
        })

    async def progress_update(self, job_id: str, progress: Dict[str, Any]):
        """Publish progress update"""
        await self._publish_event(job_id, "progress", progress)

    async def message(self, job_id: str, message: str, level: str = "info", details: Dict[str, Any] = None):
        """
        Publish a status message for display in the UI

        Args:
            job_id: Job ID
            message: Human-readable status message
            level: Message level (info, warning, error, success)
            details: Optional additional details
        """
        await self._publish_event(job_id, "message", {
            "message": message,
            "level": level,
            "details": details or {}
        })
