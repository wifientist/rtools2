"""
Venue Cleanup Workflow Definition

Deletes all workflow-created resources from a venue.
Operates in reverse dependency order to avoid constraint violations.

Flow:
1. inventory (global) → Scan venue for resources to delete
2. delete_passphrases (global) → Remove DPSK passphrases
3. delete_dpsk_pools (global) → Remove DPSK pools/services
4. delete_identities (global) → Remove identities from identity groups
5. delete_identity_groups (global) → Remove identity groups (must be empty)
6. delete_networks (global) → Remove WiFi networks (deactivated first)
7. delete_ap_groups (global) → Remove AP groups
8. verify_cleanup (global) → Summarize results

Dependency chain (strict sequential - reverse creation order):
    inventory → delete_passphrases → delete_dpsk_pools
              → delete_identities → delete_identity_groups
              → delete_networks → delete_ap_groups
              → verify_cleanup

All delete phases are non-critical: the workflow reports partial
success rather than failing entirely if some deletions fail.
"""

from workflow.workflows.definition import Workflow, Phase


VenueCleanupWorkflow = Workflow(
    name="venue_cleanup",
    description=(
        "Delete all workflow-created resources from a venue"
    ),
    requires_confirmation=True,
    default_options={
        "nuclear_mode": False,
        "name_pattern": None,
        "all_networks": False,
    },
    phases=[
        # Phase 0: Inventory Resources (global, critical)
        Phase(
            id="inventory",
            name="Inventory Resources",
            description=(
                "Scan venue for resources to delete. "
                "Supports job-specific or nuclear (venue-wide) mode."
            ),
            executor=(
                "workflow.phases.cleanup.inventory.InventoryPhase"
            ),
            per_unit=False,
            critical=True,
            inputs=[
                "name_pattern", "nuclear_mode",
                "all_networks", "created_resources",
            ],
            outputs=["inventory", "total_resources"],
            api_calls_per_unit=0,
        ),

        # Phase 1: Delete Passphrases
        Phase(
            id="delete_passphrases",
            name="Delete DPSK Passphrases",
            description=(
                "Delete all passphrases from DPSK pools. "
                "Safety net for cascade-deleted passphrases."
            ),
            executor=(
                "workflow.phases.cleanup.delete_passphrases"
                ".DeletePassphrasesPhase"
            ),
            depends_on=["inventory"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 2: Delete DPSK Pools
        Phase(
            id="delete_dpsk_pools",
            name="Delete DPSK Pools",
            description=(
                "Delete DPSK pools/services. "
                "Must happen after passphrases are deleted."
            ),
            executor=(
                "workflow.phases.cleanup.delete_dpsk_pools"
                ".DeleteDPSKPoolsPhase"
            ),
            depends_on=["delete_passphrases"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 3: Delete Identities
        Phase(
            id="delete_identities",
            name="Delete Identities",
            description=(
                "Delete identities from identity groups. "
                "Groups must be empty before they can be deleted."
            ),
            executor=(
                "workflow.phases.cleanup.delete_identities"
                ".DeleteIdentitiesPhase"
            ),
            depends_on=["delete_dpsk_pools"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 4: Delete Identity Groups
        Phase(
            id="delete_identity_groups",
            name="Delete Identity Groups",
            description=(
                "Delete identity groups. "
                "Must happen after identities are removed."
            ),
            executor=(
                "workflow.phases.cleanup.delete_identity_groups"
                ".DeleteIdentityGroupsPhase"
            ),
            depends_on=["delete_identities"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 5: Delete WiFi Networks
        Phase(
            id="delete_networks",
            name="Delete WiFi Networks",
            description=(
                "Delete WiFi networks activated on this venue. "
                "Must happen after identity groups are deleted."
            ),
            executor=(
                "workflow.phases.cleanup.delete_networks"
                ".DeleteNetworksPhase"
            ),
            depends_on=["delete_identity_groups"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 6: Delete AP Groups
        Phase(
            id="delete_ap_groups",
            name="Delete AP Groups",
            description=(
                "Delete non-default AP groups in this venue. "
                "Must happen after networks are deleted."
            ),
            executor=(
                "workflow.phases.cleanup.delete_ap_groups"
                ".DeleteAPGroupsPhase"
            ),
            depends_on=["delete_networks"],
            per_unit=False,
            critical=False,
            inputs=["inventory"],
            outputs=[
                "deleted_count", "failed_count", "errors",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 7: Verify Cleanup
        Phase(
            id="verify_cleanup",
            name="Verify Cleanup",
            description="Summarize cleanup results.",
            executor=(
                "workflow.phases.cleanup.verify"
                ".VerifyCleanupPhase"
            ),
            depends_on=["delete_ap_groups"],
            per_unit=False,
            critical=False,
            inputs=[
                "delete_passphrases_result",
                "delete_dpsk_pools_result",
                "delete_identities_result",
                "delete_identity_groups_result",
                "delete_networks_result",
                "delete_ap_groups_result",
            ],
            outputs=[
                "total_deleted", "total_failed",
                "total_skipped", "summary",
            ],
            api_calls_per_unit=0,
        ),
    ],
)
