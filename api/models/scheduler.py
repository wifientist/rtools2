"""
Generic Scheduler Service database models.

These models support a reusable scheduler service that can be used by any feature
to register and track scheduled jobs.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Float, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class ScheduledJob(Base):
    """
    Persistent scheduled job configuration.

    Jobs are registered by features (like DPSK Orchestrator) and executed
    by the scheduler service at configured intervals.
    """
    __tablename__ = "scheduled_jobs"

    id = Column(String, primary_key=True)              # Unique job ID (e.g., "orchestrator_123_sync")
    name = Column(String, nullable=False)              # Human-readable name
    description = Column(String, nullable=True)

    # Job target (what to call)
    callable_path = Column(String, nullable=False)     # e.g., "api.routers.orchestrator.sync_engine:run_sync"
    callable_kwargs = Column(JSON, default=dict)       # Arguments to pass to the callable

    # Trigger configuration
    trigger_type = Column(String, nullable=False)      # "interval", "cron", "date"
    trigger_config = Column(JSON, nullable=False)      # e.g., {"minutes": 30} or {"hour": 0, "minute": 0}

    # State
    enabled = Column(Boolean, default=True)
    paused = Column(Boolean, default=False)

    # Ownership (for filtering by feature)
    owner_type = Column(String, nullable=True)         # e.g., "orchestrator", "cleanup", etc.
    owner_id = Column(String, nullable=True)           # ID of the owning entity

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)

    # Relationships
    runs = relationship("ScheduledJobRun", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ScheduledJob id={self.id} name='{self.name}' trigger={self.trigger_type}>"


class ScheduledJobRun(Base):
    """
    Execution history for scheduled jobs.

    Each time a job runs, a record is created to track the execution
    status, duration, result, and any errors.
    """
    __tablename__ = "scheduled_job_runs"

    id = Column(Integer, primary_key=True)
    job_id = Column(String, ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False)

    # Execution details
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Result
    status = Column(String, nullable=False, default="running")  # "running", "success", "failed", "timeout"
    result = Column(JSON, nullable=True)                        # Return value (if any)
    error = Column(Text, nullable=True)                         # Error message/traceback

    # Relationships
    job = relationship("ScheduledJob", back_populates="runs")

    def __repr__(self):
        return f"<ScheduledJobRun id={self.id} job_id={self.job_id} status={self.status}>"
