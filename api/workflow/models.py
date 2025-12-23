"""
Workflow Engine Data Models

Pydantic models for workflow orchestration:
- WorkflowJob: Main job container
- Phase: Execution phase with dependencies
- Task: Individual unit of work
- Status enums for tracking state
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    """Status of a workflow job"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"  # Some tasks succeeded, some failed


class PhaseStatus(str, Enum):
    """Status of a workflow phase"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskStatus(str, Enum):
    """Status of an individual task"""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    POLLING = "POLLING"  # Waiting for async task
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Task(BaseModel):
    """Individual unit of work within a phase"""
    id: str = Field(..., description="Unique task ID (UUID)")
    name: str = Field(..., description="Human-readable task name")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Current task status")

    # R1 async tracking
    request_id: Optional[str] = Field(None, description="R1 async task ID (for 202 responses)")
    poll_count: int = Field(default=0, description="Number of times polled")
    max_polls: int = Field(default=60, description="Maximum poll attempts")

    # Data
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Input data for task")
    output_data: Dict[str, Any] = Field(default_factory=dict, description="Result data from task")

    # Error handling
    error_message: Optional[str] = Field(None, description="Error message if failed")
    retry_count: int = Field(default=0, description="Number of retries attempted")
    max_retries: int = Field(default=3, description="Maximum retry attempts")

    # Timing
    started_at: Optional[datetime] = Field(None, description="When task started")
    completed_at: Optional[datetime] = Field(None, description="When task completed")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class Phase(BaseModel):
    """Execution phase containing multiple tasks"""
    id: str = Field(..., description="Unique phase ID")
    name: str = Field(..., description="Human-readable phase name")
    status: PhaseStatus = Field(default=PhaseStatus.PENDING, description="Current phase status")
    started_at: Optional[datetime] = Field(None, description="When phase started")
    completed_at: Optional[datetime] = Field(None, description="When phase completed")

    # Dependencies & execution
    dependencies: List[str] = Field(default_factory=list, description="Phase IDs that must complete first")
    parallelizable: bool = Field(default=True, description="Can tasks run in parallel?")
    critical: bool = Field(default=False, description="Stop entire job if this fails?")
    skip_condition: Optional[str] = Field(None, description="Python expression to evaluate for skipping")

    # Tasks and results
    tasks: List[Task] = Field(default_factory=list, description="Tasks in this phase")
    result: Dict[str, Any] = Field(default_factory=dict, description="Output data for dependent phases")
    errors: List[str] = Field(default_factory=list, description="Error messages from failed tasks")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class WorkflowJob(BaseModel):
    """Main workflow job container"""
    id: str = Field(..., description="Unique job ID (UUID)")
    workflow_name: str = Field(..., description="Name of workflow (e.g., 'cloudpath_dpsk_migration')")
    status: JobStatus = Field(default=JobStatus.PENDING, description="Current job status")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When job was created")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update time")
    completed_at: Optional[datetime] = Field(None, description="When job completed")

    # Configuration
    user_id: int = Field(..., description="User ID who created this job")
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: str = Field(..., description="Tenant/EC ID")
    options: Dict[str, Any] = Field(default_factory=dict, description="Workflow-specific options")

    # Input data
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Original request payload")

    # Execution tracking
    current_phase_id: Optional[str] = Field(None, description="Currently executing phase ID")
    phases: List[Phase] = Field(default_factory=list, description="All phases in workflow")

    # Results
    created_resources: Dict[str, List[Dict]] = Field(
        default_factory=dict,
        description="Created resources by type (e.g., {'identity_groups': [...]})"
    )
    summary: Dict[str, Any] = Field(default_factory=dict, description="Final summary statistics")
    errors: List[str] = Field(default_factory=list, description="Job-level error messages")

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

    def get_progress_stats(self) -> Dict[str, Any]:
        """Calculate progress statistics including phase-level progress"""
        total_tasks = sum(len(phase.tasks) for phase in self.phases)
        completed_tasks = sum(
            1 for phase in self.phases
            for task in phase.tasks
            if task.status == TaskStatus.COMPLETED
        )
        failed_tasks = sum(
            1 for phase in self.phases
            for task in phase.tasks
            if task.status == TaskStatus.FAILED
        )

        # Phase-level progress (more useful for multi-phase workflows)
        total_phases = len(self.phases)
        completed_phases = len([
            p for p in self.phases
            if p.status in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED)
        ])
        failed_phases = len([p for p in self.phases if p.status == PhaseStatus.FAILED])
        running_phases = len([p for p in self.phases if p.status == PhaseStatus.RUNNING])

        # Progress = (completed + failed) / total
        # This represents how much of the work has been attempted (successfully or not)
        finished_tasks = completed_tasks + failed_tasks
        task_percent = (finished_tasks / total_tasks * 100) if total_tasks > 0 else 0

        # Phase-based percent (more stable for UI display)
        phase_percent = (completed_phases / total_phases * 100) if total_phases > 0 else 0

        return {
            "total_tasks": total_tasks,
            "completed": completed_tasks,
            "failed": failed_tasks,
            "pending": total_tasks - completed_tasks - failed_tasks,
            "percent": round(task_percent, 2),
            # Phase-level progress
            "total_phases": total_phases,
            "completed_phases": completed_phases,
            "failed_phases": failed_phases,
            "running_phases": running_phases,
            "phase_percent": round(phase_percent, 2)
        }

    def get_current_phase(self) -> Optional[Phase]:
        """Get the currently executing phase"""
        if self.current_phase_id:
            return self.get_phase_by_id(self.current_phase_id)
        return None


class PhaseDefinition(BaseModel):
    """Definition for a workflow phase (used when creating workflows)"""
    id: str = Field(..., description="Unique phase ID")
    name: str = Field(..., description="Human-readable phase name")
    dependencies: List[str] = Field(default_factory=list, description="Phase IDs that must complete first")
    parallelizable: bool = Field(default=True, description="Can tasks run in parallel?")
    critical: bool = Field(default=False, description="Stop entire job if this fails?")
    skip_condition: Optional[str] = Field(None, description="Python expression to evaluate for skipping")
    executor: str = Field(..., description="Fully qualified path to executor function")


class WorkflowDefinition(BaseModel):
    """Complete workflow definition"""
    name: str = Field(..., description="Workflow name")
    description: str = Field(..., description="Workflow description")
    phases: List[PhaseDefinition] = Field(..., description="Phase definitions in execution order")
