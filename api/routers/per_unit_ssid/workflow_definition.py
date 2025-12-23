"""
Per-Unit SSID Configuration Workflow Definition

Defines the 4-phase workflow for configuring per-unit SSIDs in RuckusONE:
1. Create SSIDs for each unit
2. Activate SSIDs on venue
3. Create AP Groups for each unit
4. Process units (find APs, assign to groups, activate SSIDs on groups)
"""

from workflow.models import WorkflowDefinition, PhaseDefinition


PER_UNIT_SSID_WORKFLOW = WorkflowDefinition(
    name="per_unit_ssid_configuration",
    description="Configure per-unit SSIDs in RuckusONE with AP Groups",
    phases=[
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
)


def get_workflow_definition() -> WorkflowDefinition:
    """Get the Per-Unit SSID workflow definition"""
    return PER_UNIT_SSID_WORKFLOW
