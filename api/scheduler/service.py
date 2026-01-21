"""
Generic Scheduler Service.

This service provides a reusable scheduler that any feature can use to register
and execute scheduled jobs. It wraps APScheduler with our own persistence layer.

Features:
- Register jobs with interval, cron, or one-time triggers
- Jobs persist across restarts (stored in database)
- Execution history tracking
- Pause/resume/trigger-now capabilities
- Owner-based filtering for multi-feature support
"""
import asyncio
import importlib
import logging
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from models.scheduler import ScheduledJob, ScheduledJobRun

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Generic scheduler service for running periodic tasks.

    Features register jobs by calling register_job(); the scheduler
    executes them according to their configured triggers.
    """

    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            job_defaults={
                'coalesce': True,           # Combine missed runs into one
                'max_instances': 1,          # Don't overlap same job
                'misfire_grace_time': 300    # 5 min grace for missed jobs
            }
        )
        self._db_factory = None
        self._started = False

    async def start(self, db_session_factory: Callable[[], Session]):
        """
        Start the scheduler and load persisted jobs from database.

        Args:
            db_session_factory: Callable that returns a new database session
        """
        if self._started:
            logger.warning("Scheduler already started")
            return

        self._db_factory = db_session_factory
        self.scheduler.start()
        await self._load_persisted_jobs()
        self._started = True
        logger.info("Scheduler service started")

    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if not self._started:
            return

        self.scheduler.shutdown(wait=True)
        self._started = False
        logger.info("Scheduler service stopped")

    async def _load_persisted_jobs(self):
        """Load enabled jobs from database on startup."""
        db = self._db_factory()
        try:
            jobs = db.query(ScheduledJob).filter_by(enabled=True, paused=False).all()
            for job in jobs:
                try:
                    self._add_job_to_scheduler(job)
                except Exception as e:
                    logger.error(f"Failed to load job {job.id}: {e}")
            logger.info(f"Loaded {len(jobs)} scheduled jobs from database")
        finally:
            db.close()

    def _add_job_to_scheduler(self, job: ScheduledJob):
        """Add a job to APScheduler from our database model."""
        trigger = self._create_trigger(job.trigger_type, job.trigger_config)

        self.scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            id=job.id,
            replace_existing=True,
            kwargs={
                'job_id': job.id,
                'callable_path': job.callable_path,
                'callable_kwargs': job.callable_kwargs or {}
            }
        )
        logger.debug(f"Added job {job.id} to scheduler")

    async def _execute_job(self, job_id: str, callable_path: str, callable_kwargs: dict):
        """
        Execute a job and record the result.

        This is called by APScheduler when a job is triggered.
        """
        db = self._db_factory()
        run = ScheduledJobRun(job_id=job_id, started_at=datetime.utcnow(), status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        try:
            # Import and get the callable
            callable_fn = self._import_callable(callable_path)

            # Execute the callable
            if asyncio.iscoroutinefunction(callable_fn):
                result = await callable_fn(**callable_kwargs)
            else:
                result = callable_fn(**callable_kwargs)

            # Record success
            run.status = "success"
            if result is not None:
                if isinstance(result, dict):
                    run.result = result
                else:
                    run.result = {"result": str(result)}
            run.completed_at = datetime.utcnow()
            run.duration_seconds = (run.completed_at - run.started_at).total_seconds()

            # Update job's last_run_at
            job = db.query(ScheduledJob).get(job_id)
            if job:
                job.last_run_at = run.completed_at

            db.commit()
            logger.info(f"Job {job_id} completed successfully in {run.duration_seconds:.2f}s")

        except Exception as e:
            run.status = "failed"
            run.error = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            run.completed_at = datetime.utcnow()
            run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
            db.commit()
            logger.error(f"Job {job_id} failed: {e}")

        finally:
            db.close()

    def _create_trigger(self, trigger_type: str, config: dict):
        """Create an APScheduler trigger from our config format."""
        if trigger_type == "interval":
            return IntervalTrigger(**config)  # e.g., {"minutes": 30}
        elif trigger_type == "cron":
            return CronTrigger(**config)      # e.g., {"hour": 0, "minute": 0}
        elif trigger_type == "date":
            return DateTrigger(**config)      # e.g., {"run_date": "2024-01-01 00:00:00"}
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

    def _import_callable(self, path: str) -> Callable:
        """
        Import a callable from a dotted path.

        Args:
            path: Module path with function name, e.g., "api.module.submodule:function_name"

        Returns:
            The imported callable
        """
        module_path, fn_name = path.rsplit(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)

    # ========== Public API for consumers ==========

    async def register_job(
        self,
        job_id: str,
        name: str,
        callable_path: str,
        trigger_type: str,
        trigger_config: dict,
        callable_kwargs: Optional[dict] = None,
        owner_type: Optional[str] = None,
        owner_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> ScheduledJob:
        """
        Register a new scheduled job.

        Args:
            job_id: Unique identifier (e.g., "orchestrator_123_sync")
            name: Human-readable name for display
            callable_path: Import path to function (e.g., "api.routers.orchestrator.sync_engine:run_sync")
            trigger_type: "interval", "cron", or "date"
            trigger_config: Trigger-specific config (e.g., {"minutes": 30})
            callable_kwargs: Arguments to pass to the callable
            owner_type: Type of owning feature (for filtering)
            owner_id: ID of owning entity (for filtering)
            description: Optional description

        Returns:
            The created ScheduledJob

        Raises:
            ValueError: If a job with the same ID already exists
        """
        db = self._db_factory()
        try:
            # Check for existing job
            existing = db.query(ScheduledJob).get(job_id)
            if existing:
                raise ValueError(f"Job with ID '{job_id}' already exists")

            # Create job record
            job = ScheduledJob(
                id=job_id,
                name=name,
                description=description,
                callable_path=callable_path,
                callable_kwargs=callable_kwargs or {},
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                owner_type=owner_type,
                owner_id=owner_id,
                enabled=True,
                paused=False
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            # Add to scheduler
            self._add_job_to_scheduler(job)
            logger.info(f"Registered job: {job_id}")

            return job
        finally:
            db.close()

    async def unregister_job(self, job_id: str) -> bool:
        """
        Remove a scheduled job.

        Args:
            job_id: The job ID to remove

        Returns:
            True if the job was removed, False if it didn't exist
        """
        db = self._db_factory()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return False

            # Remove from APScheduler
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass  # Job might not be in scheduler

            # Remove from database (cascade deletes runs)
            db.delete(job)
            db.commit()
            logger.info(f"Unregistered job: {job_id}")
            return True
        finally:
            db.close()

    async def update_job(
        self,
        job_id: str,
        trigger_config: Optional[dict] = None,
        enabled: Optional[bool] = None,
        callable_kwargs: Optional[dict] = None
    ) -> Optional[ScheduledJob]:
        """
        Update a job's configuration.

        Args:
            job_id: The job ID to update
            trigger_config: New trigger configuration (optional)
            enabled: New enabled state (optional)
            callable_kwargs: New callable arguments (optional)

        Returns:
            The updated job, or None if not found
        """
        db = self._db_factory()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return None

            if trigger_config is not None:
                job.trigger_config = trigger_config
            if enabled is not None:
                job.enabled = enabled
            if callable_kwargs is not None:
                job.callable_kwargs = callable_kwargs

            db.commit()
            db.refresh(job)

            # Reschedule if needed
            if job.enabled and not job.paused:
                self._add_job_to_scheduler(job)
            else:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    pass

            return job
        finally:
            db.close()

    async def pause_job(self, job_id: str) -> bool:
        """Pause a job without removing it."""
        db = self._db_factory()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return False

            job.paused = True
            db.commit()

            try:
                self.scheduler.pause_job(job_id)
            except Exception:
                pass

            logger.info(f"Paused job: {job_id}")
            return True
        finally:
            db.close()

    async def resume_job(self, job_id: str) -> bool:
        """Resume a paused job."""
        db = self._db_factory()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return False

            job.paused = False
            db.commit()

            if job.enabled:
                self._add_job_to_scheduler(job)

            logger.info(f"Resumed job: {job_id}")
            return True
        finally:
            db.close()

    async def trigger_job_now(self, job_id: str) -> bool:
        """
        Manually trigger a job to run immediately.

        Returns:
            True if the job was triggered, False if not found
        """
        apscheduler_job = self.scheduler.get_job(job_id)
        if apscheduler_job:
            apscheduler_job.modify(next_run_time=datetime.utcnow())
            logger.info(f"Triggered job {job_id} to run now")
            return True
        return False

    async def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        """Get a job by ID."""
        db = self._db_factory()
        try:
            return db.query(ScheduledJob).get(job_id)
        finally:
            db.close()

    async def get_job_status(self, job_id: str) -> Optional[dict]:
        """
        Get the current status of a job.

        Returns:
            Dict with job status info, or None if not found
        """
        db = self._db_factory()
        try:
            job = db.query(ScheduledJob).get(job_id)
            if not job:
                return None

            apscheduler_job = self.scheduler.get_job(job_id)

            return {
                "id": job.id,
                "name": job.name,
                "description": job.description,
                "enabled": job.enabled,
                "paused": job.paused,
                "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
                "next_run_at": apscheduler_job.next_run_time.isoformat() if apscheduler_job and apscheduler_job.next_run_time else None,
                "trigger_type": job.trigger_type,
                "trigger_config": job.trigger_config,
                "owner_type": job.owner_type,
                "owner_id": job.owner_id
            }
        finally:
            db.close()

    async def list_jobs(
        self,
        owner_type: Optional[str] = None,
        owner_id: Optional[str] = None,
        enabled_only: bool = False
    ) -> List[ScheduledJob]:
        """
        List jobs, optionally filtered by owner.

        Args:
            owner_type: Filter by owner type
            owner_id: Filter by owner ID
            enabled_only: Only return enabled jobs

        Returns:
            List of matching jobs
        """
        db = self._db_factory()
        try:
            query = db.query(ScheduledJob)
            if owner_type:
                query = query.filter_by(owner_type=owner_type)
            if owner_id:
                query = query.filter_by(owner_id=owner_id)
            if enabled_only:
                query = query.filter_by(enabled=True)
            return query.order_by(ScheduledJob.created_at.desc()).all()
        finally:
            db.close()

    async def get_job_history(
        self,
        job_id: str,
        limit: int = 50,
        status: Optional[str] = None
    ) -> List[ScheduledJobRun]:
        """
        Get execution history for a job.

        Args:
            job_id: The job ID
            limit: Maximum number of runs to return
            status: Filter by status (optional)

        Returns:
            List of job runs, most recent first
        """
        db = self._db_factory()
        try:
            query = db.query(ScheduledJobRun).filter_by(job_id=job_id)
            if status:
                query = query.filter_by(status=status)
            return query.order_by(ScheduledJobRun.started_at.desc()).limit(limit).all()
        finally:
            db.close()


# ========== Global instance management ==========

_scheduler_service: Optional[SchedulerService] = None


def get_scheduler() -> SchedulerService:
    """
    Get the global scheduler service instance.

    Raises:
        RuntimeError: If the scheduler hasn't been initialized
    """
    global _scheduler_service
    if _scheduler_service is None:
        raise RuntimeError("Scheduler not initialized. Call init_scheduler() first.")
    return _scheduler_service


def init_scheduler() -> SchedulerService:
    """
    Initialize the global scheduler service.

    Returns:
        The scheduler service instance
    """
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
