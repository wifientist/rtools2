"""
Workflow Definitions Registry

Central registry of all workflow definitions.
Each workflow composes independent phases into a complete flow.

Usage:
    from workflow.workflows import get_workflow, list_workflows

    workflow = get_workflow("per_unit_dpsk")
    phases = workflow.phases
"""

from typing import Dict, Optional, List
from workflow.workflows.definition import Workflow

# ============================================================================
# Workflow Registry
# ============================================================================

# Populated as workflow modules are imported
_WORKFLOWS: Dict[str, Workflow] = {}


def register_workflow(workflow: Workflow) -> None:
    """Register a workflow definition."""
    _WORKFLOWS[workflow.name] = workflow


def get_workflow(name: str) -> Workflow:
    """
    Get a workflow definition by name.

    Raises:
        ValueError: If workflow name is not registered
    """
    if name not in _WORKFLOWS:
        raise ValueError(
            f"Unknown workflow '{name}'. "
            f"Available: {list(_WORKFLOWS.keys())}"
        )
    return _WORKFLOWS[name]


def get_workflow_optional(name: str) -> Optional[Workflow]:
    """Get a workflow or None."""
    return _WORKFLOWS.get(name)


def list_workflows() -> List[str]:
    """List all registered workflow names."""
    return sorted(_WORKFLOWS.keys())


def get_all_workflows() -> Dict[str, Workflow]:
    """Get the full registry."""
    return dict(_WORKFLOWS)


# ============================================================================
# Import and register all workflow definitions
# Workflows are registered when their modules are imported.
# Add new workflows here as they are created.
# ============================================================================

# NOTE: Workflow definition files will be added as phases are migrated.
# Each file defines a Workflow() and calls register_workflow() at module level.
# Example:
#
#   # workflow/workflows/per_unit_psk.py
#   from workflow.workflows import register_workflow
#   from workflow.workflows.definition import Workflow, Phase
#
#   PerUnitPSKWorkflow = Workflow(
#       name="per_unit_psk",
#       phases=[...],
#   )
#   register_workflow(PerUnitPSKWorkflow)

# ============================================================================
# Register phase executors (triggers @register_phase decorators)
# ============================================================================
# PSK phases
import workflow.phases.create_ap_group  # noqa: F401
import workflow.phases.create_psk_network  # noqa: F401
import workflow.phases.activate_network  # noqa: F401
import workflow.phases.assign_aps  # noqa: F401
import workflow.phases.configure_lan_ports  # noqa: F401
import workflow.phases.validate_psk  # noqa: F401

# DPSK phases (reuses create_ap_group, activate_network, assign_aps, configure_lan_ports)
import workflow.phases.create_identity_group  # noqa: F401
import workflow.phases.create_dpsk_pool  # noqa: F401
import workflow.phases.create_dpsk_network  # noqa: F401
import workflow.phases.create_passphrases  # noqa: F401
import workflow.phases.validate_dpsk  # noqa: F401

# Cleanup phases
import workflow.phases.cleanup.inventory  # noqa: F401
import workflow.phases.cleanup.delete_passphrases  # noqa: F401
import workflow.phases.cleanup.delete_dpsk_pools  # noqa: F401
import workflow.phases.cleanup.delete_identities  # noqa: F401
import workflow.phases.cleanup.delete_identity_groups  # noqa: F401
import workflow.phases.cleanup.delete_networks  # noqa: F401
import workflow.phases.cleanup.delete_ap_groups  # noqa: F401
import workflow.phases.cleanup.verify  # noqa: F401

# Standalone phases
import workflow.phases.validate_lan_ports  # noqa: F401

# Cloudpath Import phases
import workflow.phases.cloudpath.validate  # noqa: F401
import workflow.phases.cloudpath.identity_groups  # noqa: F401
import workflow.phases.cloudpath.dpsk_pools  # noqa: F401
import workflow.phases.cloudpath.passphrases  # noqa: F401
import workflow.phases.cloudpath.update_identities  # noqa: F401
import workflow.phases.cloudpath.audit  # noqa: F401

# Access policies (reusable across workflows)
import workflow.phases.create_access_policies  # noqa: F401

# ============================================================================
# Register workflow definitions
# ============================================================================

# Per-Unit PSK Workflow
from workflow.workflows.per_unit_psk import PerUnitPSKWorkflow  # noqa: F401
register_workflow(PerUnitPSKWorkflow)

# Per-Unit DPSK Workflow
from workflow.workflows.per_unit_dpsk import PerUnitDPSKWorkflow  # noqa: F401
register_workflow(PerUnitDPSKWorkflow)

# Venue Cleanup Workflow
from workflow.workflows.cleanup import VenueCleanupWorkflow  # noqa: F401
register_workflow(VenueCleanupWorkflow)

# AP LAN Port Config Workflow (standalone)
from workflow.workflows.ap_lan_ports import APLanPortConfigWorkflow  # noqa: F401
register_workflow(APLanPortConfigWorkflow)

# Cloudpath Import Workflow
from workflow.workflows.cloudpath_import import CloudpathImportWorkflow  # noqa: F401
register_workflow(CloudpathImportWorkflow)
