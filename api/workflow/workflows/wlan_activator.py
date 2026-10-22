"""
WLAN Activator Workflow (standalone)

Put APs into AP Groups and activate each group's WLAN directly on it --
the AP Group half of the Cloudpath import and per-unit SSID tools, for
networks that already exist (typically created by a migration and never
activated).

    ap_identifier,ap_group_name,wlan
    101-R550,101,101@Fieldhouse
    ,102,102@Fieldhouse          <- no AP: create the group, activate only

ONE UNIT PER AP GROUP, like AP Regroup; create_ap_group and assign_aps are
reused unchanged.

Flow:
    validate_wlan_activator (global)
        └── create_ap_group (per AP Group)
                └── activate_wlans (per AP Group)
                        └── assign_aps (per AP Group)

assign_aps runs after activation (as in the Cloudpath import): APs join a
group that already carries its WLAN, and a failed activation leaves them
where they were.
"""

from workflow.workflows.definition import Workflow, Phase


WlanActivatorWorkflow = Workflow(
    name="wlan_activator",
    description=(
        "Move APs into AP Groups and activate each group's WLAN directly on "
        "it. Creates any AP Group that does not exist yet."
    ),
    requires_confirmation=True,
    default_options={},
    phases=[
        Phase(
            id="validate_wlan_activator",
            name="Validate & Plan WLAN Activation",
            description=(
                "Resolve APs, AP Groups and WLANs against the venue and build "
                "one unit per AP Group."
            ),
            executor="validate_wlan_activator",
            per_unit=False,
            critical=True,
            inputs=["activations", "options"],
            outputs=[
                "unit_mappings",
                "validation_result",
                "all_venue_aps",
                "unmatched_identifiers",
                "unresolved_wlans",
                "ap_groups_to_create",
                "ap_groups_to_reuse",
            ],
            api_calls_per_unit=0,
        ),
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description="Create each AP Group that does not already exist.",
            executor="create_ap_group",
            depends_on=["validate_wlan_activator"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_name", "ap_group_id"],
            outputs=["ap_group_id"],
            api_calls_per_unit=1,
            effect_field="ap_group_id",
            effect_label="AP Groups",
            effect_agg="distinct",
        ),
        Phase(
            id="activate_wlans",
            name="Activate WLANs on AP Groups",
            description=(
                "Activate each group's WLAN directly on it (3-step direct "
                "activation, never All AP Groups), verified by read-back."
            ),
            executor="activate_wlans",
            depends_on=["create_ap_group"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_id", "ap_group_name", "wlans"],
            outputs=["activated", "wlans_activated", "wlans_already_active"],
            api_calls_per_unit="dynamic",
            effect_field="wlans_activated",
            effect_label="WLANs activated",
            effect_agg="sum",
        ),
        Phase(
            id="assign_aps",
            name="Assign APs to AP Groups",
            description="Move each AP into its target AP Group.",
            executor="assign_aps",
            depends_on=["activate_wlans"],
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
