"""
Cloudpath DPSK Migration Workflow Definition

Defines the 8-phase workflow for migrating DPSK data from Cloudpath to RuckusONE:
1. Parse and validate Cloudpath JSON export
2. Create Identity Groups
3. Create DPSK Pools
4. Create Adaptive Policy Sets (optional)
5. Attach Policy Sets to Pools (optional)
6. Create DPSK Passphrases
7. Activate on WiFi Networks (optional)
8. Audit results
"""

from workflow.models import WorkflowDefinition, PhaseDefinition


CLOUDPATH_DPSK_WORKFLOW = WorkflowDefinition(
    name="cloudpath_dpsk_migration",
    description="Migrate DPSK configuration from Cloudpath to RuckusONE",
    phases=[
        # Phase 1: Parse and Validate
        PhaseDefinition(
            id="parse_validate",
            name="Parse and Validate Cloudpath Data",
            dependencies=[],
            parallelizable=False,
            critical=True,
            executor="routers.cloudpath.phases.parse.execute"
        ),

        # Phase 2: Create Identity Groups
        PhaseDefinition(
            id="create_identity_groups",
            name="Create Identity Groups",
            dependencies=["parse_validate"],
            parallelizable=True,
            critical=True,
            executor="routers.cloudpath.phases.identity_groups.execute"
        ),

        # Phase 3: Create DPSK Pools
        PhaseDefinition(
            id="create_dpsk_pools",
            name="Create DPSK Pools",
            dependencies=["create_identity_groups"],
            parallelizable=True,
            critical=True,
            executor="routers.cloudpath.phases.dpsk_pools.execute"
        ),

        # Phase 4: Create Policy Sets (Optional)
        PhaseDefinition(
            id="create_policy_sets",
            name="Create Adaptive Policy Sets",
            dependencies=["parse_validate"],
            parallelizable=True,
            critical=False,
            skip_condition="not options.get('include_adaptive_policy_sets', False)",
            executor="routers.cloudpath.phases.policy_sets.execute"
        ),

        # Phase 5: Attach Policy Sets (Optional)
        PhaseDefinition(
            id="attach_policies",
            name="Attach Policy Sets to DPSK Pools",
            dependencies=["create_dpsk_pools", "create_policy_sets"],
            parallelizable=True,
            critical=False,
            skip_condition="not options.get('include_adaptive_policy_sets', False)",
            executor="routers.cloudpath.phases.attach_policies.execute"
        ),

        # Phase 6: Create Passphrases
        PhaseDefinition(
            id="create_passphrases",
            name="Create DPSK Passphrases",
            dependencies=["create_dpsk_pools"],
            parallelizable=True,
            critical=True,
            executor="routers.cloudpath.phases.passphrases.execute"
        ),

        # Phase 7: Activate on Networks (Optional)
        PhaseDefinition(
            id="activate_networks",
            name="Activate DPSK on WiFi Networks",
            dependencies=["create_passphrases"],
            parallelizable=True,
            critical=False,
            skip_condition="options.get('just_copy_dpsks', True)",
            executor="routers.cloudpath.phases.activate.execute"
        ),

        # Phase 8: Audit Results
        PhaseDefinition(
            id="audit_results",
            name="Audit Created Resources",
            dependencies=["create_passphrases"],
            parallelizable=False,
            critical=False,
            executor="routers.cloudpath.phases.audit.execute"
        ),
    ]
)


def get_workflow_definition() -> WorkflowDefinition:
    """Get the Cloudpath DPSK workflow definition"""
    return CLOUDPATH_DPSK_WORKFLOW
