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

from workflow.models import WorkflowJob, JobStatus, TaskStatus
from workflow.state_manager import RedisStateManager

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
    Returns paginated list of job summaries.

    Query Parameters:
    - workflow_name: Filter by workflow type (e.g., 'cloudpath_dpsk_migration')
    - status: Filter by job status (PENDING, RUNNING, COMPLETED, FAILED, PARTIAL)
    - limit: Maximum number of jobs to return (default: 50)
    - offset: Offset for pagination (default: 0)
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)

    # Get all jobs (this will get more than we need, but we filter after)
    all_jobs = await state_manager.list_jobs(limit=1000, offset=0)

    # Filter by user_id (RBAC)
    # Note: Legacy jobs (user_id=0) are shown to all users since they pre-date user association
    user_jobs = [job for job in all_jobs if job.user_id == current_user.id or job.user_id == 0]

    # Filter out child jobs - only show parent/standalone jobs in the list
    # Child jobs are tracked under their parent and can be viewed in the parent's detail view
    user_jobs = [job for job in user_jobs if job.parent_job_id is None]

    # Apply optional filters
    if workflow_name:
        user_jobs = [job for job in user_jobs if job.workflow_name == workflow_name]

    if status:
        user_jobs = [job for job in user_jobs if job.status == status]

    # Sort by created_at descending (newest first)
    user_jobs.sort(key=lambda j: j.created_at, reverse=True)

    # Apply pagination
    total = len(user_jobs)
    paginated_jobs = user_jobs[offset:offset + limit]

    # Build response
    job_items = []
    for i, job in enumerate(paginated_jobs):
        progress = job.get_progress_stats()
        job_items.append(JobListItem(
            job_id=job.id,
            workflow_name=job.workflow_name,
            status=job.status,
            created_at=job.created_at.isoformat() + 'Z',  # Add Z suffix to indicate UTC
            completed_at=job.completed_at.isoformat() + 'Z' if job.completed_at else None,
            venue_id=job.venue_id,
            controller_id=job.controller_id,
            progress_percent=progress['percent']
        ))
        # Yield every 20 jobs to prevent blocking
        if i > 0 and i % 20 == 0:
            await asyncio.sleep(0)

    return JobListResponse(
        jobs=job_items,
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
    state_manager = RedisStateManager(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    # Build progress stats
    progress = job.get_progress_stats()

    # Get current phase info (only for running jobs)
    current_phase = None
    if job.status == JobStatus.RUNNING and job.current_phase_id:
        phase = job.get_phase_by_id(job.current_phase_id)
        if phase:
            current_phase = {
                'id': phase.id,
                'name': phase.name,
                'status': phase.status,
                'tasks_completed': len([t for t in phase.tasks if t.status == TaskStatus.COMPLETED]),
                'tasks_total': len(phase.tasks)
            }

    # Build phase summary with tasks
    phases = []
    for p in job.phases:
        phase_data = {
            'id': p.id,
            'name': p.name,
            'status': p.status,
            'started_at': p.started_at.isoformat() if p.started_at else None,
            'completed_at': p.completed_at.isoformat() if p.completed_at else None,
            'duration_seconds': (
                (p.completed_at - p.started_at).total_seconds()
                if p.completed_at and p.started_at else None
            ),
            'tasks': [
                {
                    'id': task.id,
                    'name': task.name,
                    'status': task.status,
                    'started_at': task.started_at.isoformat() if task.started_at else None,
                    'completed_at': task.completed_at.isoformat() if task.completed_at else None,
                    'error_message': task.error_message
                }
                for task in p.tasks
            ]
        }

        # Add phase result summary if available
        if p.result:
            phase_data['result'] = p.result

        phases.append(phase_data)

    # Check if this is a parallel parent job
    is_parallel = job.is_parent_job()
    parallel_progress = None
    child_jobs = None

    if is_parallel:
        # Fetch child job statuses using MGET for efficiency
        child_jobs = []
        completed_children = 0
        failed_children = 0
        running_children = 0

        if job.child_job_ids:
            # Use MGET to fetch all child jobs in one Redis call
            keys = [f"workflow:jobs:{child_id}" for child_id in job.child_job_ids]
            child_data_list = await redis_client.mget(keys)

            for i, child_data in enumerate(child_data_list):
                if child_data:
                    try:
                        child_dict = json.loads(child_data)
                        # Handle legacy jobs without user_id
                        if 'user_id' not in child_dict:
                            child_dict['user_id'] = 0
                        child = WorkflowJob(**child_dict)

                        child_progress = child.get_progress_stats() if child.phases else {}
                        child_jobs.append({
                            'job_id': child.id,
                            'item_id': child.get_item_identifier(),
                            'status': child.status,
                            'current_phase': child.current_phase_id,
                            'progress': child_progress,
                            'errors': child.errors[:3] if child.errors else []  # Limit errors
                        })

                        if child.status == JobStatus.COMPLETED:
                            completed_children += 1
                        elif child.status == JobStatus.FAILED:
                            failed_children += 1
                        elif child.status == JobStatus.RUNNING:
                            running_children += 1
                    except Exception as e:
                        logger.warning(f"Failed to deserialize child job: {e}")

                # Yield every 20 children to prevent blocking
                if i > 0 and i % 20 == 0:
                    await asyncio.sleep(0)

        total_children = len(job.child_job_ids)
        parallel_progress = {
            'total_items': total_children,
            'completed': completed_children,
            'failed': failed_children,
            'running': running_children,
            'pending': total_children - completed_children - failed_children - running_children,
            'percent': round((completed_children + failed_children) / total_children * 100, 2) if total_children > 0 else 0
        }

        # Override progress with parallel progress for parent jobs
        progress = parallel_progress

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=progress,
        current_phase=current_phase,
        phases=phases,
        created_resources=job.created_resources,
        errors=job.errors,
        summary=job.summary,
        is_parallel=is_parallel,
        parallel_progress=parallel_progress,
        child_jobs=child_jobs
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
    state_manager = RedisStateManager(redis_client)

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
    state_manager = RedisStateManager(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Verify user owns this job (legacy jobs with user_id=0 are accessible to all)
    if job.user_id != current_user.id and job.user_id != 0:
        raise HTTPException(status_code=403, detail=f"Access denied to job {job_id}")

    # Check if job is in a cancellable state
    if job.status not in [JobStatus.PENDING, JobStatus.RUNNING]:
        return CancelJobResponse(
            job_id=job_id,
            status=job.status,
            message=f"Job cannot be cancelled - already in terminal state: {job.status}"
        )

    # Set the cancellation flag on this job
    await state_manager.set_cancelled(job_id)

    # If this is a parallel parent job, also cancel all child jobs
    if job.is_parent_job():
        for i, child_id in enumerate(job.child_job_ids):
            await state_manager.set_cancelled(child_id)
            # Yield every 20 to prevent blocking
            if i > 0 and i % 20 == 0:
                await asyncio.sleep(0)
        logger.info(f"Set cancellation flag on {len(job.child_job_ids)} child jobs")

    # Update job status to CANCELLED
    job.status = JobStatus.CANCELLED
    job.errors.append("Job cancelled by user")
    await state_manager.save_job(job)

    logger.info(f"Job {job_id} cancelled by user {current_user.email}")

    # Publish cancellation event so frontend gets notified
    from workflow.events import WorkflowEventPublisher
    event_publisher = WorkflowEventPublisher(redis_client)
    await event_publisher.publish_event(job_id, "job_cancelled", {
        "status": JobStatus.CANCELLED,
        "message": "Job cancelled by user"
    })

    return CancelJobResponse(
        job_id=job_id,
        status=JobStatus.CANCELLED,
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
    state_manager = RedisStateManager(redis_client)

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
                progress = current_job.get_progress_stats()
                yield f"event: status\ndata: {json.dumps({'status': current_job.status, 'progress': progress})}\n\n"

                # If job already in terminal state, send final event and close
                if current_job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL, JobStatus.CANCELLED]:
                    final_event_type = 'job_completed' if current_job.status == JobStatus.COMPLETED else ('job_cancelled' if current_job.status == JobStatus.CANCELLED else 'job_failed')
                    yield f"event: {final_event_type}\ndata: {json.dumps({'status': current_job.status, 'progress': progress})}\n\n"
                    logger.info(f"Job {job_id} already in terminal state {current_job.status}, closing SSE stream")
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
                        current_job = await state_manager.get_job(job_id)
                        if current_job and current_job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL, JobStatus.CANCELLED]:
                            progress = current_job.get_progress_stats()
                            final_event_type = 'job_completed' if current_job.status == JobStatus.COMPLETED else ('job_cancelled' if current_job.status == JobStatus.CANCELLED else 'job_failed')
                            yield f"event: {final_event_type}\ndata: {json.dumps({'status': current_job.status, 'progress': progress})}\n\n"
                            logger.info(f"Job {job_id} reached terminal state {current_job.status}, closing SSE stream")
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
                            progress = current_job.get_progress_stats()
                            final_event_type = 'job_completed' if current_job.status == JobStatus.COMPLETED else ('job_cancelled' if current_job.status == JobStatus.CANCELLED else 'job_failed')
                            yield f"event: {final_event_type}\ndata: {json.dumps({'status': current_job.status, 'progress': progress})}\n\n"
                            logger.info(f"Job {job_id} reached terminal state {current_job.status}, closing SSE stream")
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
    state_manager = RedisStateManager(redis_client)

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
