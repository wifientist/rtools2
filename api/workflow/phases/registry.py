"""
Phase Registry

Central registry for V2 phase executors.
Phases register themselves using the @register_phase decorator.

Usage:
    from workflow.phases.registry import register_phase, get_phase_class

    @register_phase("create_ap_group")
    class CreateAPGroupPhase(PhaseExecutor):
        ...

    # Later, retrieve by ID
    phase_class = get_phase_class("create_ap_group")
    executor = phase_class(context)
    result = await executor.execute(inputs)
"""

import logging
from typing import Dict, Type, List, Optional

from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)

# Global registry: phase_id → PhaseExecutor subclass
_PHASE_REGISTRY: Dict[str, Type[PhaseExecutor]] = {}


def register_phase(phase_id: str, phase_name: str = ""):
    """
    Decorator to register a phase executor class.

    Args:
        phase_id: Unique identifier for the phase
        phase_name: Human-readable name (defaults to class name)

    Usage:
        @register_phase("create_ap_group", "Create AP Group")
        class CreateAPGroupPhase(PhaseExecutor):
            ...
    """
    def decorator(cls: Type[PhaseExecutor]) -> Type[PhaseExecutor]:
        if phase_id in _PHASE_REGISTRY:
            existing = _PHASE_REGISTRY[phase_id]
            logger.warning(
                f"Overwriting phase '{phase_id}': "
                f"{existing.__name__} → {cls.__name__}"
            )

        cls.phase_id = phase_id
        cls.phase_name = phase_name or cls.__name__
        _PHASE_REGISTRY[phase_id] = cls

        logger.debug(f"Registered V2 phase: {phase_id} → {cls.__name__}")
        return cls

    return decorator


def get_phase_class(phase_id: str) -> Type[PhaseExecutor]:
    """
    Get the executor class for a phase ID.

    Args:
        phase_id: The phase identifier

    Returns:
        PhaseExecutor subclass

    Raises:
        ValueError: If phase_id is not registered
    """
    if phase_id not in _PHASE_REGISTRY:
        raise ValueError(
            f"Unknown phase '{phase_id}'. "
            f"Registered phases: {list(_PHASE_REGISTRY.keys())}"
        )
    return _PHASE_REGISTRY[phase_id]


def get_phase_class_optional(phase_id: str) -> Optional[Type[PhaseExecutor]]:
    """Get executor class or None if not found."""
    return _PHASE_REGISTRY.get(phase_id)


def list_registered_phases() -> List[str]:
    """List all registered phase IDs."""
    return sorted(_PHASE_REGISTRY.keys())


def get_all_phases() -> Dict[str, Type[PhaseExecutor]]:
    """Get the full registry."""
    return dict(_PHASE_REGISTRY)


def validate_workflow_phases(phase_ids: List[str]) -> List[str]:
    """
    Validate that all phase IDs in a workflow are registered.

    Args:
        phase_ids: List of phase IDs from a workflow definition

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []
    for phase_id in phase_ids:
        if phase_id not in _PHASE_REGISTRY:
            errors.append(f"Phase '{phase_id}' is not registered")
    return errors
