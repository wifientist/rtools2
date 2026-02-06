"""
Generic Workflow Job Management Router

Provides workflow-agnostic endpoints for managing jobs across all workflow types.
Filters jobs by workflow_name to support multiple workflow implementations.

Endpoints:
- GET /jobs - List all jobs (filterable by workflow_name, status)
- GET /jobs/{job_id}/status - Get job status
- GET /jobs/{job_id}/stream - Stream job events (SSE)
- POST /jobs/{job_id}/cleanup - Cleanup failed job resources
- DELETE /jobs - Delete jobs (Admin only)
"""

import logging
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User, RoleEnum
from redis_client import get_redis_client

from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.models import (
    WorkflowJobV2,
    JobStatus,
    PhaseStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["Workflow Jobs"]
)


# ==================== Request/Response Models ====================

class JobStatusResponse(BaseModel):
    """Job status response"""
    job_id: str
    status: str
    progress: Dict[str, Any]
    current_phase: Optional[Dict[str, Any]]
    phases: list
    created_resources: Dict[str, list]
    errors: list
    summary: Dict[str, Any]
    # Parallel execution fields (only present for parent jobs)
    is_parallel: bool = False
    parallel_progress: Optional[Dict[str, Any]] = None
    child_jobs: Optional[List[Dict[str, Any]]] = None


class JobListItem(BaseModel):
    """Job summary for listing"""
    job_id: str
    workflow_name: str
    status: str
    created_at: str
    completed_at: Optional[str]
    venue_id: str
    controller_id: int
    progress_percent: float


class JobListResponse(BaseModel):
    """List of jobs"""
    jobs: List[JobListItem]
    total: int


class CleanupRequest(BaseModel):
    """Cleanup request"""
    delete_partial_resources: bool = Field(
        default=True,
        description="Delete partially created resources"
    )


class CleanupResponse(BaseModel):
    """Cleanup response"""
    status: str
    deleted: Dict[str, int]


class DeleteJobRequest(BaseModel):
    """Delete job request"""
    job_ids: List[str] = Field(..., description="List of job IDs to delete")


class DeleteJobResponse(BaseModel):
    """Delete job response"""
    deleted: List[str] = Field(..., description="Successfully deleted job IDs")
    failed: List[Dict[str, str]] = Field(default_factory=list, description="Failed deletions with reasons")


class CancelJobResponse(BaseModel):
    """Cancel job response"""
    job_id: str
    status: str
    message: str




# ==================== Helper Functions ====================

def _job_to_status_response(job: WorkflowJobV2) -> JobStatusResponse:
    """Transform a V2 workflow job into the JobStatusResponse format."""
    v2_progress = job.get_progress()

    # Compute aggregate phase statuses (works for both global and per-unit phases)
    phase_statuses = {
        defn.id: job.get_phase_aggregate_status(defn.id)
        for defn in job.phase_definitions
    }

    progress = {
        "total_tasks": v2_progress.get("total_work", 0),
        "completed": v2_progress.get("completed_work", 0),
        "failed": v2_progress.get("units_failed", 0),
        "pending": v2_progress.get("units_pending", 0),
        "percent": v2_progress.get("percent", 0),
        "total_phases": v2_progress.get("total_phases", 0),
        "completed_phases": sum(
            1 for s in phase_statuses.values()
            if s == PhaseStatus.COMPLETED
        ),
        "failed_phases": sum(
            1 for s in phase_statuses.values()
            if s == PhaseStatus.FAILED
        ),
        "running_phases": sum(
            1 for s in phase_statuses.values()
            if s == PhaseStatus.RUNNING
        ),
        # Include per-phase stats for frontend display
        "phase_stats": v2_progress.get("phase_stats", {}),
    }

    # Build phases array from phase definitions + aggregate status
    current_phase = None
    phases = []
    for defn in job.phase_definitions:
        status = phase_statuses.get(defn.id, PhaseStatus.PENDING)
        result = job.global_phase_results.get(defn.id)

        phase_data = {
            "id": defn.id,
            "name": defn.name,
            "status": status.value if isinstance(status, PhaseStatus) else status,
            "started_at": None,
            "completed_at": None,
            "tasks": [],
        }
        if result:
            phase_data["result"] = result
        phases.append(phase_data)

        if status == PhaseStatus.RUNNING and current_phase is None:
            current_phase = {
                "id": defn.id,
                "name": defn.name,
                "status": "RUNNING",
                "tasks_completed": 0,
                "tasks_total": 1,
            }

    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        progress=progress,
        current_phase=current_phase,
        phases=phases,
        created_resources=job.created_resources,
        errors=job.errors,
        summary={},
        is_parallel=False,
    )


# ==================== API Endpoints ====================

@router.get("", response_model=JobListResponse)
async def list_jobs(
    workflow_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user)
):
    """
    List workflow jobs for the current user

    Filters jobs by user_id and optionally by workflow_name and status.

    Query Parameters:
    - workflow_name: Filter by workflow type (e.g., 'cloudpath_dpsk_migration')
    - status: Filter by job status (PENDING, RUNNING, COMPLETED, FAILED, PARTIAL)
    - limit: Maximum number of jobs to return (default: 50)
    - offset: Offset for pagination (default: 0)
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    # Get all jobs
    all_jobs = await state_manager.list_jobs(limit=1000, offset=0)

    # Filter jobs by user_id (RBAC)
    # Note: Legacy jobs (user_id=0) are shown to all users since they pre-date user association
    user_jobs = [job for job in all_jobs if job.user_id == current_user.id or job.user_id == 0]

    # Apply optional filters
    if workflow_name:
        user_jobs = [job for job in user_jobs if job.workflow_name == workflow_name]

    if status:
        user_jobs = [job for job in user_jobs if job.status.value == status]

    # Build response items
    job_items = []
    for job in user_jobs:
        progress = job.get_progress()
        job_items.append(JobListItem(
            job_id=job.id,
            workflow_name=job.workflow_name,
            status=job.status.value,
            created_at=job.created_at.isoformat() + 'Z',
            completed_at=job.completed_at.isoformat() + 'Z' if job.completed_at else None,
            venue_id=job.venue_id,
            controller_id=job.controller_id,
            progress_percent=progress.get('percent', 0) if isinstance(progress, dict) else 0
        ))

    # Sort all jobs by created_at descending (newest first)
    job_items.sort(key=lambda j: j.created_at, reverse=True)

    # Apply pagination
    total = len(job_items)
    paginated_jobs = job_items[offset:offset + limit]

    # Yield periodically to prevent blocking
    if len(paginated_jobs) > 20:
        await asyncio.sleep(0)

    return JobListResponse(
        jobs=paginated_jobs,
        total=total
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get workflow job status

    Returns detailed status including:
    - Overall job status and progress
    - Current executing phase (if running)
    - All phases with task details
    - Created resources
    - Errors
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    return _job_to_status_response(job
    )


@router.post("/{job_id}/cleanup", response_model=CleanupResponse)
async def cleanup_job(
    job_id: str,
    request: CleanupRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Cleanup failed job resources

    Optionally deletes partially created resources from a failed job.
    This is workflow-specific and delegates to the workflow's cleanup handler.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    if not request.delete_partial_resources:
        logger.info(f"Acknowledged job {job_id} without cleanup")
        return CleanupResponse(
            status="acknowledged",
            deleted={}
        )

    # Import cleanup function based on workflow type
    # This allows each workflow to implement its own cleanup logic
    try:
        if job.workflow_name == 'cloudpath_dpsk_migration':
            from routers.cloudpath.utils.cleanup import cleanup_job_resources
            deleted = await cleanup_job_resources(job, db)
            return CleanupResponse(
                status="cleaned",
                deleted=deleted
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Cleanup not implemented for workflow: {job.workflow_name}"
            )
    except ImportError as e:
        logger.error(f"Failed to import cleanup handler: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Cleanup handler not available for workflow: {job.workflow_name}"
        )


@router.post("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Cancel a running workflow job

    Sets a cancellation flag that the workflow engine checks between phases
    and the task executor checks between items. The job will stop at the
    next safe point (after completing any in-flight API calls).

    Returns:
    - job_id: The job ID
    - status: The updated job status
    - message: Description of what happened
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    # Check if job is in a cancellable state
    cancellable_statuses = [
        JobStatus.PENDING,
        JobStatus.RUNNING,
        JobStatus.VALIDATING,
        JobStatus.AWAITING_CONFIRMATION,
    ]

    if job.status not in cancellable_statuses:
        return CancelJobResponse(
            job_id=job_id,
            status=job.status.value,
            message=f"Job cannot be cancelled - already in terminal state: {job.status.value}"
        )

    # Set the cancellation flag and update job status
    await state_manager.set_cancelled(job_id)
    job.status = JobStatus.CANCELLED
    job.errors.append("Job cancelled by user")
    await state_manager.save_job(job)
    logger.info(f"Job {job_id} cancelled by user {current_user.email}")

    # Publish cancellation event so frontend gets notified
    from workflow.events import WorkflowEventPublisher
    event_publisher = WorkflowEventPublisher(redis_client)
    await event_publisher.publish_event(job_id, "job_cancelled", {
        "status": JobStatus.CANCELLED.value,
        "message": "Job cancelled by user"
    })

    return CancelJobResponse(
        job_id=job_id,
        status=JobStatus.CANCELLED.value,
        message="Job cancellation requested. The job will stop at the next safe point."
    )


@router.get("/{job_id}/stream")
async def stream_job_events(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Stream job events via Server-Sent Events (SSE)

    Provides real-time updates on job progress including:
    - Phase transitions
    - Task completions
    - Progress updates
    - Job completion/failure

    The stream automatically closes when the job reaches a terminal state.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    # Verify job exists
    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    async def event_stream():
        """Generate SSE stream from Redis pub/sub"""
        # Subscribe to job events channel
        pubsub = redis_client.pubsub()
        channel = f"workflow:events:{job_id}"
        await pubsub.subscribe(channel)

        try:
            # Send connection confirmation
            yield f"event: connected\ndata: {json.dumps({'job_id': job_id})}\n\n"

            # Send current job status immediately
            current_job = await state_manager.get_job(job_id)
            if current_job:
                progress = current_job.get_progress()
                status_val = current_job.status.value

                yield f"event: status\ndata: {json.dumps({'status': status_val, 'progress': progress})}\n\n"

                # If job already in terminal state, send final event and close
                terminal = ['COMPLETED', 'FAILED', 'PARTIAL', 'CANCELLED']
                if status_val in terminal:
                    final_event_type = 'job_completed' if status_val == 'COMPLETED' else ('job_cancelled' if status_val == 'CANCELLED' else 'job_failed')
                    yield f"event: {final_event_type}\ndata: {json.dumps({'status': status_val, 'progress': progress})}\n\n"
                    logger.info(f"Job {job_id} already in terminal state {status_val}, closing SSE stream")
                    return

            # Stream events from Redis pub/sub with timeout-based polling
            last_keepalive = asyncio.get_event_loop().time()
            keepalive_interval = 15  # Send keepalive every 15 seconds
            poll_timeout = 5  # Check for messages every 5 seconds

            while True:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_timeout),
                        timeout=poll_timeout + 1
                    )

                    # Send periodic keepalive comments to prevent connection timeout
                    now = asyncio.get_event_loop().time()
                    if now - last_keepalive > keepalive_interval:
                        yield f": keepalive\n\n"
                        last_keepalive = now

                        # Also check if job completed while we were waiting
                        ka_job = await state_manager.get_job(job_id)
                        if ka_job:
                            ka_status = ka_job.status.value
                            ka_progress = ka_job.get_progress()
                            if ka_status in ['COMPLETED', 'FAILED', 'PARTIAL', 'CANCELLED']:
                                final_event_type = 'job_completed' if ka_status == 'COMPLETED' else ('job_cancelled' if ka_status == 'CANCELLED' else 'job_failed')
                                yield f"event: {final_event_type}\ndata: {json.dumps({'status': ka_status, 'progress': ka_progress})}\n\n"
                                logger.info(f"Job {job_id} reached terminal state {ka_status}, closing SSE stream")
                                return

                    if message and message['type'] == 'message':
                        try:
                            event_data = json.loads(message['data'])
                            event_type = event_data.get('type', 'message')

                            # Format as SSE
                            sse_data = json.dumps(event_data['data'])
                            yield f"event: {event_type}\ndata: {sse_data}\n\n"

                            # Close stream on terminal events
                            if event_type in ['job_completed', 'job_failed', 'job_cancelled']:
                                logger.info(f"Job {job_id} reached terminal state, closing SSE stream")
                                return

                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse event data: {message['data']}")

                except asyncio.TimeoutError:
                    # Timeout is expected - send keepalive and check job status
                    now = asyncio.get_event_loop().time()
                    if now - last_keepalive > keepalive_interval:
                        yield f": keepalive\n\n"
                        last_keepalive = now

                        # Check if job completed
                        current_job = await state_manager.get_job(job_id)
                        if current_job and current_job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL, JobStatus.CANCELLED]:
                            progress = current_job.get_progress()
                            status_str = current_job.status.value
                            final_event_type = 'job_completed' if current_job.status == JobStatus.COMPLETED else ('job_cancelled' if current_job.status == JobStatus.CANCELLED else 'job_failed')
                            yield f"event: {final_event_type}\ndata: {json.dumps({'status': status_str, 'progress': progress})}\n\n"
                            logger.info(f"Job {job_id} reached terminal state {status_str}, closing SSE stream")
                            return

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.delete("", response_model=DeleteJobResponse)
async def delete_jobs(
    request: DeleteJobRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Delete workflow jobs (Admin only)

    Permanently deletes jobs and all associated data from Redis.
    Useful for cleaning up old test jobs during development.

    Requires admin or super role.
    """
    # Check if user is admin or super
    if current_user.role not in [RoleEnum.admin, RoleEnum.super]:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required to delete jobs"
        )

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    deleted = []
    failed = []

    for job_id in request.job_ids:
        try:
            # Verify job exists
            job = await state_manager.get_job(job_id)
            if not job:
                failed.append({
                    "job_id": job_id,
                    "reason": "Job not found"
                })
                continue

            # Delete the job
            success = await state_manager.delete_job(job_id)
            if success:
                deleted.append(job_id)
                logger.info(f"Admin {current_user.email} deleted job {job_id}")
            else:
                failed.append({
                    "job_id": job_id,
                    "reason": "Failed to delete"
                })

        except Exception as e:
            logger.error(f"Error deleting job {job_id}: {str(e)}")
            failed.append({
                "job_id": job_id,
                "reason": str(e)
            })

    return DeleteJobResponse(
        deleted=deleted,
        failed=failed
    )
