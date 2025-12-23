"""
Cloudpath DPSK Cleanup Workflow Definition

Defines the 6-phase workflow for cleaning up failed/test DPSK migrations:
1. Inventory resources - Scan what needs to be deleted
2. Delete identities - Remove identities (cascades to delete passphrases)
3. Delete remaining passphrases - Safety net for any passphrases not cascade-deleted
4. Delete DPSK pools - Remove DPSK service configurations
5. Delete identity groups - Remove the groups (only works if DPSK pool is already deleted)
6. Verify cleanup - Confirm all resources are gone

IMPORTANT: Deletion order matters due to dependencies!
- Deleting Identities → Cascades to delete Passphrases ✅
- Deleting Passphrases → Leaves orphaned Identities ❌
- Identity Groups → Cannot delete if still associated with DPSK pool ❌
- DPSK Pools → Must delete before deleting Identity Groups ✅

Therefore: Identities → Passphrases (safety net) → DPSK Pools → Identity Groups
"""

from workflow.models import WorkflowDefinition, PhaseDefinition


CLOUDPATH_CLEANUP_WORKFLOW = WorkflowDefinition(
    name="cloudpath_dpsk_cleanup",
    description="Clean up Cloudpath DPSK migration resources from RuckusONE",
    phases=[
        # Phase 1: Inventory Resources
        PhaseDefinition(
            id="inventory_resources",
            name="Inventory Resources to Delete",
            dependencies=[],
            parallelizable=False,
            critical=True,
            executor="routers.cloudpath.cleanup_phases.inventory.execute"
        ),

        # Phase 2: Delete Identities (cascades to delete passphrases)
        PhaseDefinition(
            id="delete_identities",
            name="Delete Identities (cascades to passphrases)",
            dependencies=["inventory_resources"],
            parallelizable=True,
            critical=False,
            executor="routers.cloudpath.cleanup_phases.delete_identities.execute"
        ),

        # Phase 3: Delete Remaining Passphrases (safety net)
        PhaseDefinition(
            id="delete_passphrases",
            name="Delete Remaining Passphrases (safety net)",
            dependencies=["inventory_resources", "delete_identities"],
            parallelizable=True,
            critical=False,
            executor="routers.cloudpath.cleanup_phases.delete_passphrases.execute"
        ),

        # Phase 4: Delete DPSK Pools
        PhaseDefinition(
            id="delete_dpsk_pools",
            name="Delete DPSK Pools",
            dependencies=["inventory_resources", "delete_passphrases"],
            parallelizable=True,
            critical=False,
            executor="routers.cloudpath.cleanup_phases.delete_dpsk_pools.execute"
        ),

        # Phase 5: Delete Identity Groups
        PhaseDefinition(
            id="delete_identity_groups",
            name="Delete Identity Groups",
            dependencies=["inventory_resources", "delete_dpsk_pools"],
            parallelizable=True,
            critical=False,
            executor="routers.cloudpath.cleanup_phases.delete_identity_groups.execute"
        ),

        # Phase 6: Verify Cleanup
        PhaseDefinition(
            id="verify_cleanup",
            name="Verify Cleanup Complete",
            dependencies=["delete_identity_groups"],
            parallelizable=False,
            critical=False,
            executor="routers.cloudpath.cleanup_phases.verify.execute"
        ),
    ]
)


def get_cleanup_workflow_definition() -> WorkflowDefinition:
    """Get the Cloudpath DPSK cleanup workflow definition"""
    return CLOUDPATH_CLEANUP_WORKFLOW
