"""
Workflow Engine V2

Parallel, per-unit workflow execution with typed phase contracts.

Key components:
- WorkflowBrain: Central orchestrator (workflow.v2.brain)
- PhaseExecutor: Base class for typed phase executors (workflow.phases.phase_executor)
- Phase Registry: @register_phase decorator (workflow.phases.registry)
- ActivityTracker: Centralized R1 activity polling (workflow.v2.activity_tracker)
- DependencyGraph: DAG-based dependency resolution (workflow.v2.graph)
- RedisStateManagerV2: Multi-worker state persistence (workflow.v2.state_manager)

Usage:
    from workflow.v2 import WorkflowBrain, ActivityTracker
    from workflow.phases.phase_executor import PhaseExecutor
    from workflow.phases.registry import register_phase
    from workflow.workflows import get_workflow
"""

from workflow.v2.brain import WorkflowBrain
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.graph import DependencyGraph
from workflow.v2.models import (
    WorkflowJobV2,
    UnitMapping,
    UnitPlan,
    UnitResolved,
    JobStatus,
    PhaseStatus,
    UnitStatus,
    TaskStatus,
    PhaseContract,
    PhaseInput,
    PhaseOutput,
    ValidationResult,
    ValidationSummary,
    PhaseResult,
    ActivityRef,
    ActivityResult,
)

__all__ = [
    # Core
    "WorkflowBrain",
    "ActivityTracker",
    "RedisStateManagerV2",
    "DependencyGraph",

    # Models
    "WorkflowJobV2",
    "UnitMapping",
    "UnitPlan",
    "UnitResolved",
    "JobStatus",
    "PhaseStatus",
    "UnitStatus",
    "TaskStatus",
    "PhaseContract",
    "PhaseInput",
    "PhaseOutput",
    "ValidationResult",
    "ValidationSummary",
    "PhaseResult",
    "ActivityRef",
    "ActivityResult",
]
