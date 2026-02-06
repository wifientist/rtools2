"""
Workflow Engine Package - V2

Primary workflow orchestration using the V2 Brain (workflow.v2.brain).
All new workflows should use V2 models and state management.

Legacy V1 models (WorkflowJob, Phase) are kept in workflow/models.py
for backward compatibility with cloudpath_router.py only.
TODO: Migrate cloudpath_router.py to V2 and remove V1 models.
"""

# V2 is the primary API
from workflow.v2.models import (
    WorkflowJobV2,
    JobStatus,
    PhaseStatus,
    TaskStatus,
    Task,
    PhaseDefinitionV2,
    WorkflowDefinition,
    PhaseDefinition,
    UnitMapping,
    UnitPlan,
    UnitResolved,
    UnitStatus,
    ValidationResult,
    ActivityRef,
)

from workflow.v2.state_manager import RedisStateManagerV2

# Legacy V1 models (only for cloudpath backward compat)
from workflow.models import (
    WorkflowJob,
    Phase,
    FlowStatus,
)

__all__ = [
    # V2 models (primary API)
    "WorkflowJobV2",
    "JobStatus",
    "PhaseStatus",
    "TaskStatus",
    "Task",
    "PhaseDefinitionV2",
    "WorkflowDefinition",
    "PhaseDefinition",
    "UnitMapping",
    "UnitPlan",
    "UnitResolved",
    "UnitStatus",
    "ValidationResult",
    "ActivityRef",
    # V2 state manager
    "RedisStateManagerV2",
    # Legacy V1 (deprecated - cloudpath compat only)
    "WorkflowJob",
    "Phase",
    "FlowStatus",
]
