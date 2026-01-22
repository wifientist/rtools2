"""
SmartZone Audit Workflow Definition

Defines the phased workflow for auditing SmartZone controllers:
1. Initialize - Get system info and domains
2. Fetch Switches - Get all switches and switch groups
3. Audit Zones - Audit each zone (APs, WLANs, groups)
4. Finalize - Aggregate stats, match switch groups, store results
"""

from workflow.models import WorkflowDefinition, PhaseDefinition


SZ_AUDIT_WORKFLOW = WorkflowDefinition(
    name="sz_audit",
    description="Comprehensive audit of SmartZone controller",
    phases=[
        # Phase 1: Initialize - Get system info and domains
        PhaseDefinition(
            id="initialize",
            name="Initialize Audit",
            dependencies=[],
            parallelizable=False,
            critical=True,
            executor="routers.sz.phases.initialize.execute"
        ),

        # Phase 2: Fetch Switches - Get all switches and switch groups
        PhaseDefinition(
            id="fetch_switches",
            name="Fetch Switches",
            dependencies=["initialize"],
            parallelizable=False,
            critical=False,  # Audit can continue without switches
            executor="routers.sz.phases.fetch_switches.execute"
        ),

        # Phase 3: Audit Zones - Audit each zone (APs, WLANs, etc.)
        PhaseDefinition(
            id="audit_zones",
            name="Audit Zones",
            dependencies=["initialize"],
            parallelizable=False,
            critical=True,
            executor="routers.sz.phases.audit_zones.execute"
        ),

        # Phase 4: Finalize - Aggregate and store results
        PhaseDefinition(
            id="finalize",
            name="Finalize Results",
            dependencies=["fetch_switches", "audit_zones"],
            parallelizable=False,
            critical=True,
            executor="routers.sz.phases.finalize.execute"
        ),
    ]
)


def get_workflow_definition() -> WorkflowDefinition:
    """Get the SmartZone Audit workflow definition"""
    return SZ_AUDIT_WORKFLOW
