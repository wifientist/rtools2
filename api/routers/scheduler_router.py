"""
Scheduler Admin Router.

Super admin endpoints for viewing and managing scheduled jobs.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel
from datetime import datetime

from models.user import User, RoleEnum
from dependencies import get_current_user
from decorators import require_role
from scheduler.service import get_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/scheduler",
    tags=["Scheduler Admin"],
)


class JobSummary(BaseModel):
    """Summary of a scheduled job for the dashboard."""
    id: str
    name: str
    description: Optional[str]
    owner_type: Optional[str]
    owner_id: Optional[str]
    trigger_type: str
    trigger_config: dict
    enabled: bool
    paused: bool
    created_at: Optional[datetime]
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]

    class Config:
        from_attributes = True


class SchedulerOverview(BaseModel):
    """Overview of all scheduler jobs for the dashboard."""
    total_jobs: int
    active_jobs: int
    paused_jobs: int
    disabled_jobs: int
    jobs: list[JobSummary]


@router.get("/overview", response_model=SchedulerOverview)
@require_role(RoleEnum.super)
async def get_scheduler_overview(
    current_user: User = Depends(get_current_user)
):
    """
    Get an overview of all scheduled jobs.

    Super admin only. Returns summary statistics and job details.
    """
    scheduler = get_scheduler()
    jobs = await scheduler.list_jobs()

    job_summaries = []
    active_count = 0
    paused_count = 0
    disabled_count = 0

    for job in jobs:
        # Get next run time from APScheduler
        apscheduler_job = scheduler.scheduler.get_job(job.id)
        next_run_at = None
        if apscheduler_job and apscheduler_job.next_run_time:
            next_run_at = apscheduler_job.next_run_time

        # Count by status
        if not job.enabled:
            disabled_count += 1
        elif job.paused:
            paused_count += 1
        else:
            active_count += 1

        job_summaries.append(JobSummary(
            id=job.id,
            name=job.name,
            description=job.description,
            owner_type=job.owner_type,
            owner_id=job.owner_id,
            trigger_type=job.trigger_type,
            trigger_config=job.trigger_config,
            enabled=job.enabled,
            paused=job.paused,
            created_at=job.created_at,
            last_run_at=job.last_run_at,
            next_run_at=next_run_at
        ))

    return SchedulerOverview(
        total_jobs=len(jobs),
        active_jobs=active_count,
        paused_jobs=paused_count,
        disabled_jobs=disabled_count,
        jobs=job_summaries
    )


@router.get("/jobs/{job_id}/history")
@require_role(RoleEnum.super)
async def get_job_history(
    job_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user)
):
    """
    Get execution history for a specific job.

    Super admin only.
    """
    scheduler = get_scheduler()
    runs = await scheduler.get_job_history(job_id, limit=limit)

    return {
        "job_id": job_id,
        "runs": [
            {
                "id": run.id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "duration_seconds": run.duration_seconds,
                "status": run.status,
                "result": run.result,
                "error": run.error[:500] if run.error else None  # Truncate long errors
            }
            for run in runs
        ]
    }


@router.post("/jobs/{job_id}/trigger")
@require_role(RoleEnum.super)
async def trigger_job(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Manually trigger a job to run immediately.

    Super admin only.
    """
    scheduler = get_scheduler()
    success = await scheduler.trigger_job_now(job_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    logger.info(f"Super admin {current_user.email} triggered job {job_id}")
    return {"message": f"Job '{job_id}' triggered to run now"}


@router.post("/jobs/{job_id}/pause")
@require_role(RoleEnum.super)
async def pause_job(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Pause a scheduled job.

    Super admin only.
    """
    scheduler = get_scheduler()
    success = await scheduler.pause_job(job_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    logger.info(f"Super admin {current_user.email} paused job {job_id}")
    return {"message": f"Job '{job_id}' paused"}


@router.post("/jobs/{job_id}/resume")
@require_role(RoleEnum.super)
async def resume_job(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Resume a paused job.

    Super admin only.
    """
    scheduler = get_scheduler()
    success = await scheduler.resume_job(job_id)

    if not success:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    logger.info(f"Super admin {current_user.email} resumed job {job_id}")
    return {"message": f"Job '{job_id}' resumed"}
