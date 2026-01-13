"""
Per-Unit SSID Configuration Workflow Definition

Defines the workflow for configuring per-unit SSIDs in RuckusONE:
1. Create SSIDs for each unit
2. Activate SSIDs on venue
3. Create AP Groups for each unit
4. Process units (find APs, assign to groups, activate SSIDs on groups)
5. (Optional) Configure LAN ports on APs with configurable ports
"""

from typing import List
from workflow.models import WorkflowDefinition, PhaseDefinition


# Base phases (always included)
BASE_PHASES: List[PhaseDefinition] = [
    # Phase 1: Create SSIDs
    PhaseDefinition(
        id="create_ssids",
        name="Create SSIDs",
        dependencies=[],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_ssids.execute"
    ),

    # Phase 2: Activate SSIDs on Venue
    PhaseDefinition(
        id="activate_ssids",
        name="Activate SSIDs on Venue",
        dependencies=["create_ssids"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.activate_ssids.execute"
    ),

    # Phase 3: Create AP Groups
    PhaseDefinition(
        id="create_ap_groups",
        name="Create AP Groups",
        dependencies=["activate_ssids"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_ap_groups.execute"
    ),

    # Phase 4: Process Units (AP assignment + SSID activation on groups)
    PhaseDefinition(
        id="process_units",
        name="Process Units",
        dependencies=["create_ap_groups"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.process_units.execute"
    ),
]

# Phase 5: Configure LAN Ports (optional)
LAN_PORT_PHASE = PhaseDefinition(
    id="configure_lan_ports",
    name="Configure LAN Ports",
    dependencies=["process_units"],
    parallelizable=False,
    critical=False,  # Non-critical - workflow succeeds even if this fails
    executor="routers.per_unit_ssid.phases.configure_lan_ports.execute"
)


def get_workflow_definition(configure_lan_ports: bool = False) -> WorkflowDefinition:
    """
    Get the Per-Unit SSID workflow definition.

    Args:
        configure_lan_ports: If True, includes Phase 5 for LAN port configuration

    Returns:
        WorkflowDefinition with appropriate phases
    """
    phases = BASE_PHASES.copy()

    if configure_lan_ports:
        phases.append(LAN_PORT_PHASE)

    return WorkflowDefinition(
        name="per_unit_ssid_configuration",
        description="Configure per-unit SSIDs in RuckusONE with AP Groups",
        phases=phases
    )
