"""
Workflow Event Publishing

Publishes workflow events to Redis pub/sub for real-time monitoring.
V2 WorkflowJobV2 only.
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from workflow.v2.models import WorkflowJobV2, PhaseStatus

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

    async def job_started(self, job: WorkflowJobV2):
        """Publish job started event"""
        await self._publish_event(job.id, "job_started", {
            "job_id": job.id,
            "workflow_name": job.workflow_name,
            "total_phases": len(job.phase_definitions),
            "venue_id": job.venue_id
        })

    async def job_completed(self, job: WorkflowJobV2):
        """Publish job completed event"""
        total_phases = len(job.phase_definitions)
        completed_phases = sum(1 for p in job.phase_definitions if job.global_phase_status.get(p.id) == PhaseStatus.COMPLETED)
        failed_phases = sum(1 for p in job.phase_definitions if job.global_phase_status.get(p.id) == PhaseStatus.FAILED)
        progress = job.get_progress()

        await self._publish_event(job.id, "job_completed", {
            "job_id": job.id,
            "status": job.status.value if hasattr(job.status, 'value') else job.status,
            "created_resources": job.created_resources,
            "total_phases": total_phases,
            "completed_phases": completed_phases,
            "failed_phases": failed_phases,
            "total_tasks": progress.get('total_work', 0),
            "completed": progress.get('completed_work', 0),
            "failed": progress.get('units_failed', 0),
            "progress": progress,
            "duration_seconds": (
                (job.completed_at - job.created_at).total_seconds()
                if job.completed_at and job.created_at else None
            )
        })

    async def job_failed(self, job: WorkflowJobV2):
        """Publish job failed event"""
        total_phases = len(job.phase_definitions)
        completed_phases = sum(1 for p in job.phase_definitions if job.global_phase_status.get(p.id) == PhaseStatus.COMPLETED)
        failed_phases = sum(1 for p in job.phase_definitions if job.global_phase_status.get(p.id) == PhaseStatus.FAILED)
        progress = job.get_progress()

        await self._publish_event(job.id, "job_failed", {
            "job_id": job.id,
            "status": job.status.value if hasattr(job.status, 'value') else job.status,
            "errors": job.errors,
            "total_phases": total_phases,
            "completed_phases": completed_phases,
            "failed_phases": failed_phases,
            "total_tasks": progress.get('total_work', 0),
            "completed": progress.get('completed_work', 0),
            "failed": progress.get('units_failed', 0),
            "progress": progress
        })

    async def job_cancelled(self, job: WorkflowJobV2):
        """Publish job cancelled event"""
        await self._publish_event(job.id, "job_cancelled", {
            "job_id": job.id,
            "status": job.status.value if hasattr(job.status, 'value') else job.status,
            "message": "Job cancelled by user"
        })

    async def phase_started(
        self,
        job_id: str,
        phase_id: str,
        phase_name: str,
        unit_id: str = None,
        **kwargs
    ):
        """Publish phase started event"""
        await self._publish_event(job_id, "phase_started", {
            "phase_id": phase_id,
            "phase_name": phase_name,
            "unit_id": unit_id,
        })

    async def phase_completed(
        self,
        job_id: str,
        phase_id: str,
        phase_name: str,
        unit_id: str = None,
        duration_ms: int = None,
        **kwargs
    ):
        """Publish phase completed event"""
        await self._publish_event(job_id, "phase_completed", {
            "phase_id": phase_id,
            "phase_name": phase_name,
            "unit_id": unit_id,
            "duration_ms": duration_ms,
        })

    async def task_started(
        self,
        job_id: str,
        phase_id: str,
        task_id: str,
        task_name: str,
        **kwargs
    ):
        """Publish task started event"""
        await self._publish_event(job_id, "task_started", {
            "phase_id": phase_id,
            "task_id": task_id,
            "task_name": task_name
        })

    async def task_completed(
        self,
        job_id: str,
        phase_id: str,
        task_id: str,
        task_name: str,
        status: str = None,
        **kwargs
    ):
        """Publish task completed event"""
        await self._publish_event(job_id, "task_completed", {
            "phase_id": phase_id,
            "task_id": task_id,
            "task_name": task_name,
            "status": status,
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

    async def publish_event(self, job_id: str, event_type: str, data: Dict[str, Any]):
        """
        Public method to publish arbitrary events

        Args:
            job_id: Job ID
            event_type: Event type string
            data: Event data dict
        """
        await self._publish_event(job_id, event_type, data)
