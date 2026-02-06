"""
Workflow Engine Data Models (Legacy V1)

NOTE: This module is being deprecated in favor of workflow.v2.models.
New code should import from workflow.v2.models directly.

This file is kept for backward compatibility with cloudpath_router.py
which has not yet been fully migrated to V2.

TODO: Migrate cloudpath_router.py to use WorkflowJobV2 and remove this file.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Re-export shared types from V2
from workflow.v2.models import (
    JobStatus,
    PhaseStatus,
    TaskStatus,
    Task,
    WorkflowDefinition,
    PhaseDefinition,
)


class FlowStatus(str, Enum):
    """Status of an individual flow (per-item workflow) - V1 only"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Phase(BaseModel):
    """
    Workflow phase - V1 model
    NOTE: V2 uses global_phase_status dict instead of Phase objects
    """
    id: str = Field(..., description="Unique phase ID")
    name: str = Field(..., description="Human-readable phase name")
    status: PhaseStatus = Field(default=PhaseStatus.PENDING, description="Current phase status")

    # Dependencies
    dependencies: List[str] = Field(default_factory=list, description="Phase IDs that must complete first")

    # Execution settings
    parallelizable: bool = Field(default=True, description="Can tasks run in parallel?")
    critical: bool = Field(default=False, description="Stop entire job if this fails?")
    skip_condition: Optional[str] = Field(None, description="Python expression to evaluate for skipping")

    # Tasks
    tasks: List[Task] = Field(default_factory=list, description="Tasks in this phase")

    # Timing
    started_at: Optional[datetime] = Field(None, description="When phase started")
    completed_at: Optional[datetime] = Field(None, description="When phase completed")

    # Result data
    result: Optional[Dict[str, Any]] = Field(None, description="Phase result data")
    errors: List[str] = Field(default_factory=list, description="Errors during execution")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class WorkflowJob(BaseModel):
    """
    Main workflow job container - V1 model

    NOTE: V2 uses WorkflowJobV2 which has per-unit tracking instead of phases.
    This model is kept for cloudpath_router.py backward compatibility.

    TODO: Migrate cloudpath_router.py to WorkflowJobV2 and remove this class.
    """
    id: str = Field(..., description="Unique job ID (UUID)")
    workflow_name: str = Field(..., description="Name of the workflow type")
    user_id: int = Field(default=0, description="User who created this job")
    status: JobStatus = Field(default=JobStatus.PENDING, description="Current job status")

    # Controller/Venue context
    controller_id: int = Field(default=0, description="Controller ID")
    venue_id: str = Field(default="", description="Venue ID")
    tenant_id: str = Field(default="", description="Tenant ID")

    # Phases
    phases: List[Phase] = Field(default_factory=list, description="Phases in execution order")
    current_phase_id: Optional[str] = Field(None, description="Currently executing phase ID")

    # Parallel execution (parent-child hierarchy) - V1 only
    parent_job_id: Optional[str] = Field(None, description="Parent job ID (if this is a child job)")
    child_job_ids: List[str] = Field(default_factory=list, description="Child job IDs (if this is a parent job)")

    # Input/Output
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Job input configuration")
    options: Dict[str, Any] = Field(default_factory=dict, description="Execution options")
    summary: Dict[str, Any] = Field(default_factory=dict, description="Result summary")

    # Resource tracking
    created_resources: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="Resources created during workflow (for cleanup)"
    )

    # Error handling
    errors: List[str] = Field(default_factory=list, description="Errors during execution")

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Job creation timestamp")
    started_at: Optional[datetime] = Field(None, description="When job started")
    completed_at: Optional[datetime] = Field(None, description="When job completed")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

    def get_phase_by_id(self, phase_id: str) -> Optional[Phase]:
        """Get a phase by its ID"""
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        return None

    def get_current_phase(self) -> Optional[Phase]:
        """Get the currently executing phase"""
        if self.current_phase_id:
            return self.get_phase_by_id(self.current_phase_id)
        return None

    def is_parent_job(self) -> bool:
        """Check if this is a parallel parent job"""
        return len(self.child_job_ids) > 0

    def is_child_job(self) -> bool:
        """Check if this is a parallel child job"""
        return self.parent_job_id is not None

    def get_progress_stats(self) -> Dict[str, Any]:
        """Calculate job progress statistics"""
        total_tasks = 0
        completed = 0
        failed = 0
        pending = 0

        for phase in self.phases:
            total_tasks += len(phase.tasks)
            for task in phase.tasks:
                if task.status == TaskStatus.COMPLETED:
                    completed += 1
                elif task.status == TaskStatus.FAILED:
                    failed += 1
                else:
                    pending += 1

        percent = round((completed / total_tasks) * 100, 2) if total_tasks > 0 else 0

        return {
            'total_tasks': total_tasks,
            'completed': completed,
            'failed': failed,
            'pending': pending,
            'percent': percent
        }

    def get_item_identifier(self) -> str:
        """Get the identifier for this job's item (for parallel child jobs)"""
        return self.input_data.get('item', {}).get('id', self.id)


# Export all for backward compatibility
__all__ = [
    'JobStatus',
    'FlowStatus',
    'PhaseStatus',
    'TaskStatus',
    'Task',
    'Phase',
    'WorkflowJob',
    'WorkflowDefinition',
    'PhaseDefinition',
]
