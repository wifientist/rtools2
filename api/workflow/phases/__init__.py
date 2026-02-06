"""
Workflow Phases Package - V2

V2 Phase System:
- New phases use @register_phase decorator from workflow.phases.registry
- Phases extend PhaseExecutor base class from workflow.phases.phase_executor
- Phases have typed Inputs/Outputs contracts

For new phases, see:
- workflow.phases.phase_executor.PhaseExecutor (base class)
- workflow.phases.registry (decorator and lookup)
"""

# V2 Phase system exports
from .phase_executor import PhaseExecutor, PhaseContext, PhaseValidation
from .registry import register_phase, get_phase_class, list_registered_phases

__all__ = [
    "PhaseExecutor",
    "PhaseContext",
    "PhaseValidation",
    "register_phase",
    "get_phase_class",
    "list_registered_phases",
]
