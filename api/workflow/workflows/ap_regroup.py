"""
AP Regroup Workflow (standalone)

Move APs into AP Groups from a flat CSV, with no SSID, DPSK or property
involvement.

    ap_identifier,ap_group_name
    R350-ABC123,Building-1
    1-101@Fieldhouse,1-101-APs

ap_identifier matches an AP by serial number OR name; ap_group_name is
created if it does not already exist.

The engine's per-unit parallelism is reused with ONE UNIT PER AP GROUP, so
create_ap_group and assign_aps are the same executors the per-unit SSID
tools use — this workflow adds only its own validation phase.

Flow:
    validate_ap_regroup (global)
        └── create_ap_group (per AP Group)
                └── assign_aps (per AP Group)
"""

from workflow.workflows.definition import Workflow, Phase


APRegroupWorkflow = Workflow(
    name="ap_regroup",
    description=(
        "Move APs into AP Groups from a CSV. Creates any AP Group that does "
        "not exist yet."
    ),
    requires_confirmation=True,
    default_options={},
    phases=[
        # =====================================================================
        # Phase 0: Resolve the CSV against the venue (GLOBAL)
        # =====================================================================
        Phase(
            id="validate_ap_regroup",
            name="Validate & Plan AP Regroup",
            description=(
                "Match each CSV row to an AP in the venue, work out which AP "
                "Groups exist, and build one unit per target AP Group."
            ),
            executor="validate_ap_regroup",
            per_unit=False,
            critical=True,
            inputs=["ap_assignments", "options"],
            outputs=[
                "unit_mappings",
                "validation_result",
                "all_venue_aps",
                "unmatched_identifiers",
                "ap_groups_to_create",
                "ap_groups_to_reuse",
            ],
            api_calls_per_unit=0,
        ),

        # =====================================================================
        # Phase 1: Create the AP Group (per group)
        # Reused unchanged. Groups that already exist are pre-resolved by
        # validation, so the Brain pre-completes them without an API call.
        # =====================================================================
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description="Create each AP Group that does not already exist.",
            executor="create_ap_group",
            depends_on=["validate_ap_regroup"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_name", "ap_group_id"],
            outputs=["ap_group_id"],
            api_calls_per_unit=1,
            effect_field="ap_group_id",
            effect_label="AP Groups",
            effect_agg="distinct",
        ),

        # =====================================================================
        # Phase 2: Move the APs in (per group)
        # =====================================================================
        Phase(
            id="assign_aps",
            name="Assign APs to AP Groups",
            description="Move each AP into its target AP Group.",
            executor="assign_aps",
            depends_on=["create_ap_group"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "ap_group_id", "ap_group_name",
                "ap_serial_numbers", "all_venue_aps",
            ],
            outputs=["aps_matched", "aps_assigned", "aps_already_in_group"],
            api_calls_per_unit="dynamic",
            effect_field="aps_assigned",
            effect_label="APs moved",
            effect_agg="sum",
        ),
    ],
)
