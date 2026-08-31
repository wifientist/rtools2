"""
Cloudpath DPSK Import Workflow Definition

Unified workflow for importing DPSK passphrases from Cloudpath exports.
Always uses a SINGLE SHARED DPSK pool. SSID configuration is controlled
by the ssid_mode option.

SSID MODES:

1. ssid_mode="none" (default)
   - Import passphrases only, no SSID creation
   - User configures SSIDs manually or uses existing ones
   - Use when: Just migrating passphrases to existing infrastructure

2. ssid_mode="single"
   - 1 shared DPSK pool → 1 property-wide SSID
   - All passphrases work on the single SSID
   - Use when: Single site-wide SSID for all residents

3. ssid_mode="per_unit"
   - 1 shared DPSK pool → N SSIDs (one per unit), up to 1024
   - Each unit gets its own SSID (e.g., "108@Property")
   - AP Groups created per-unit for targeted broadcast
   - APs assigned to groups, SSIDs configured per-group
   - Passphrases work on any unit's SSID (roaming enabled)
   - Plus the property-wide SSID from the export, on that same shared
     pool, activated venue-wide (create_property_ssid)
   - Use when: Per-unit SSID visibility with property-wide roaming

Every mode uses exactly ONE identity group and ONE DPSK pool, created up
front by the global create_shared_resources phase. The 1:1
"per_unit_isolated" mode (one pool per unit, no roaming) has been removed.

ACTIVATION
    Per-unit SSIDs bind straight to their AP Group via activate_ap_group,
    which never puts the SSID into an "All AP Groups" state. That removes
    the in-flight activation cap the old venue-wide-then-move path needed
    to respect R1's 15-SSID-per-AP-Group limit, and it avoids the
    deprecated POST /networkActivations endpoint entirely.

Flow (ssid_mode="per_unit"):
    validate_and_plan (global)
        │
        ├── create_shared_resources (global: the 1 identity group + 1 pool)
        │       │
        │       ├── create_passphrases ──> update_identity_descriptions
        │       │
        │       └── create_dpsk_network (per-unit)
        │                       │
        ├── create_ap_group ────┴──> activate_ap_group
        │           │                        │
        │           └───────────────> assign_aps
        │                                    │
        │                            configure_lan_ports (optional)
        │
        ├── create_access_policies (optional, global)
        │
        └── cloudpath_audit (global)
"""

from workflow.workflows.definition import Workflow, Phase


CloudpathImportWorkflow = Workflow(
    name="cloudpath_import",
    description=(
        "Import DPSK passphrases from Cloudpath export. "
        "Supports property-wide or per-unit SSID modes."
    ),
    requires_confirmation=True,
    # Retained only for the legacy activate_network path. Per-unit SSIDs now
    # activate straight onto their AP Group (activate_ap_group) and never
    # broadcast venue-wide, so nothing in this workflow acquires a slot.
    max_activation_slots=12,
    default_options={
        # Passphrase import options
        "max_concurrent_passphrases": 10,
        "skip_expired_dpsks": False,
        "renew_expired_dpsks": False,
        "renewal_days": 365,
        "just_copy_dpsks": False,  # Skip identity creation, just passphrases

        # SSID mode: "none" | "single" | "per_unit"
        "ssid_mode": "none",
        "activate_networks": False,  # Auto-activate created SSIDs

        # Hybrid: alongside the per-unit SSIDs, also create the property-wide
        # SSID from the export, on the same shared pool and activated across
        # the venue. Most Cloudpath exports carry one, and residents who are
        # only on it would otherwise be dropped. No-op when none is detected.
        "create_property_ssid": True,

        # AP Group settings (used by ssid_mode='per_unit')
        "ap_group_prefix": "",       # e.g., "Unit-"
        "ap_group_postfix": "",      # e.g., "-APs"
        "ap_assignment_mode": "skip",  # "skip" | "csv" (from ap_assignments list)
        # ap_assignments: [{unit_number: "108", ap_identifier: "ABC123"}, ...]
        # ap_identifier can be AP serial number or AP name
        "ap_assignments": [],

        # DPSK passphrase generation. Empty format = translate whatever
        # Cloudpath exported. DICTIONARY_WORDS produces word-based passphrases
        # and is the only format that honours the two settings below.
        "passphrase_format": "",       # "" | NUMBERS_ONLY | KEYBOARD_FRIENDLY
                                       #    | MOST_SECURED | DICTIONARY_WORDS
        "word_count": 4,               # 3-6, DICTIONARY_WORDS only
        "numeric_suffix_enabled": False,  # DICTIONARY_WORDS only

        # Network settings
        "default_vlan": "1",
        "name_conflict_resolution": "keep",  # "keep" | "replace" | "rename"

        # LAN port configuration (optional)
        "configure_lan_ports": False,

        # Identity description updates (Phase 3)
        "skip_identity_descriptions": False,  # Skip writing Cloudpath GUIDs to R1 identity descriptions

        # Access policy options
        "enable_access_policies": False,  # Create adaptive policies for rate limiting
        "policy_set_name": "",  # Name for the policy set (defaults to property name)
    },
    phases=[
        # =====================================================================
        # Phase 0: Validate and Plan (GLOBAL)
        # Parses input, detects mode, creates unit_mappings if per-unit
        # =====================================================================
        Phase(
            id="validate_and_plan",
            name="Validate & Plan Import",
            description=(
                "Parse Cloudpath JSON export, detect import mode, "
                "fetch venue APs if needed, and build execution plan."
            ),
            executor="validate_and_plan",  # Matches @register_phase ID
            per_unit=False,
            critical=True,
            inputs=["cloudpath_data", "options"],
            outputs=[
                "import_mode",
                "pool_config",
                "identity_groups",
                "dpsk_pools",
                "passphrases",
                "unit_mappings",
                "validation_result",
                "all_venue_aps",  # Pre-fetched for assign_aps phase
            ],
            api_calls_per_unit=0,
        ),

        # =====================================================================
        # Phase 1: Create the Identity Group + DPSK Pool (GLOBAL)
        # Runs once, before any per-unit work. Every mode shares one identity
        # group and one pool, so this always runs.
        # Without it the first unit created them and the other N-1 polled by
        # name — which deadlocked, because the pollers held every concurrency
        # slot while sleeping and the creator could never be scheduled.
        # =====================================================================
        Phase(
            id="create_shared_resources",
            name="Create Shared Identity Group & DPSK Pool",
            description=(
                "Create the one identity group and DPSK pool shared by all "
                "units, before any per-unit phase runs."
            ),
            executor="create_shared_resources",
            depends_on=["validate_and_plan"],
            per_unit=False,
            critical=True,
            inputs=["identity_groups", "dpsk_pools", "pool_config"],
            outputs=[
                "identity_group_id", "dpsk_pool_id",
                "identity_group_ids", "dpsk_pool_ids",
            ],
            api_calls_per_unit=4,
        ),

        # =====================================================================
        # Phase 2: Create Passphrases - All go into the shared pool
        # =====================================================================
        Phase(
            id="create_passphrases",
            name="Create DPSK Passphrases",
            description=(
                "Create passphrases in DPSK pools. "
                "Uses parallel execution for bulk imports."
            ),
            executor="create_passphrases",
            depends_on=["create_shared_resources"],
            per_unit=True,
            critical=True,
            inputs=[
                "import_mode", "dpsk_pool_id", "passphrases",
                "options", "dpsk_pool_ids",
                "identity_group_id",  # to backfill ids R1 reports late
            ],
            outputs=[
                "created_count", "failed_count", "skipped_count",
                "created_passphrases", "failed_passphrases"
            ],
            api_calls_per_unit="dynamic",
            # Shared-pool modes import every passphrase under the first unit,
            # so the unit count says nothing about the import size.
            effect_field="created_count",
            effect_label="passphrases",
            effect_agg="sum",
        ),

        # =====================================================================
        # Phase 3: Update Identity Descriptions
        # =====================================================================
        Phase(
            id="update_identity_descriptions",
            name="Update Identity Descriptions",
            description=(
                "Update identity descriptions with Cloudpath GUIDs "
                "for traceability and reconciliation."
            ),
            executor="update_identity_descriptions",
            depends_on=["create_passphrases"],
            per_unit=True,
            critical=False,  # Non-critical - import still succeeds without this
            skip_if="options.get('skip_identity_descriptions', False)",
            inputs=[
                "import_mode", "identity_group_id", "identity_group_ids",
                "created_passphrases", "options"
            ],
            outputs=["updated_count", "update_results"],
            api_calls_per_unit="dynamic",
            effect_field="updated_count",
            effect_label="identities updated",
            effect_agg="sum",
        ),

        # =====================================================================
        # Phase 4: Create Access Policies (Optional)
        # =====================================================================
        Phase(
            id="create_access_policies",
            name="Create Access Policies",
            description=(
                "Create adaptive policies for rate limiting based on "
                "username suffix patterns (e.g., _fast, _gigabit). "
                "Strips suffix from identity names after creating policies."
            ),
            executor="create_access_policies",
            depends_on=["create_passphrases"],
            per_unit=False,  # Global phase - dedupe RADIUS groups
            critical=False,  # Non-critical - import succeeds without policies
            skip_if="not options.get('enable_access_policies', False)",
            inputs=[
                "created_passphrases", "passphrases", "options",
                "identity_group_id", "identity_group_ids"
            ],
            outputs=[
                "radius_groups_created", "policies_created", "policy_set_id",
                "identities_renamed"
            ],
            api_calls_per_unit="dynamic",
        ),

        # =====================================================================
        # Phase 5: Create AP Groups (ssid_mode='per_unit' only)
        # Must happen BEFORE activation so the SSID has a group to bind to
        # =====================================================================
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description=(
                "Create AP Group for each unit. Required for per-unit SSID "
                "broadcast, and must exist before the SSID is activated so "
                "activation can bind directly to it."
            ),
            executor="create_ap_group",
            depends_on=["validate_and_plan"],
            per_unit=True,
            critical=True,
            skip_if="options.get('ssid_mode') != 'per_unit'",
            inputs=[
                "unit_id", "unit_number", "ap_group_name",
                "ap_group_prefix", "ap_group_postfix",
            ],
            outputs=["ap_group_id"],
            api_calls_per_unit=1,
            # The property-wide unit broadcasts across the venue and gets no
            # AP Group, so this trails the unit count by one when it runs.
            effect_field="ap_group_id",
            effect_label="AP Groups",
            effect_agg="distinct",
        ),

        # =====================================================================
        # Phase 6: Create DPSK Networks (Optional - when ssid_mode != "none")
        # =====================================================================
        Phase(
            id="create_dpsk_network",
            name="Create DPSK Networks",
            description=(
                "Create DPSK WiFi networks linked to the DPSK pools. "
                "Runs when ssid_mode is 'single' or 'per_unit'."
            ),
            executor="create_dpsk_network",
            depends_on=["create_shared_resources"],
            per_unit=True,
            critical=False,  # Non-critical - can configure SSIDs manually
            skip_if="options.get('ssid_mode', 'none') == 'none'",
            inputs=[
                "unit_id", "unit_number", "network_name", "ssid_name",
                "dpsk_pool_id", "default_vlan", "name_conflict_resolution",
                "options"
            ],
            outputs=["network_id", "network_ids"],
            api_calls_per_unit=2,  # create + link DPSK service
            effect_field="network_id",
            effect_label="SSIDs",
            effect_agg="distinct",
        ),

        # =====================================================================
        # Phase 7a: Activate the property SSID (ssid_mode="single" ONLY)
        # Named for the mode, not the action: in per_unit mode this phase is
        # correctly skipped while activate_ap_group does the venue-wide
        # activation for the property SSID. The old name ("Activate Network
        # Venue-Wide") made a correct skip look like missing work.
        # Property-wide SSID - just activate on all AP groups, no targeting.
        # Uses cloudpath-specific phase to avoid touching the shared
        # activate_network phase used by the Per-Unit SSID tool.
        # =====================================================================
        Phase(
            id="activate_venue_wide",
            name="Activate Property SSID (single-SSID mode)",
            description=(
                "Activate property-wide SSID on all AP groups. "
                "Only runs for ssid_mode='single'."
            ),
            executor="activate_venue_wide",
            depends_on=["create_dpsk_network"],
            per_unit=True,
            critical=False,
            skip_if="options.get('ssid_mode') != 'single'",
            inputs=[
                "unit_id", "unit_number", "network_id", "ssid_name",
            ],
            outputs=["activated", "already_active"],
            api_calls_per_unit=1,
            effect_field="activated",
            effect_label="SSIDs activated",
            effect_agg="truthy",
        ),

        # =====================================================================
        # Phase 7b: Activate Networks on their AP Group (per-unit mode)
        # Binds the SSID straight to the unit's AP Group. The SSID is never
        # in an "All AP Groups" state, so there is no venue-wide slot
        # pressure and no activation_slot is needed — this is what lets the
        # per-unit mode scales past a handful of concurrent units.
        # Depends on create_ap_group because the group must exist to bind to.
        # =====================================================================
        Phase(
            id="activate_ap_group",
            name="Activate SSIDs (AP Groups + property-wide)",
            description=(
                "Bind each unit's SSID directly to its AP Group without a "
                "venue-wide broadcast step."
            ),
            executor="activate_ap_group",
            depends_on=["create_dpsk_network", "create_ap_group"],
            per_unit=True,
            critical=False,
            skip_if="options.get('ssid_mode') != 'per_unit'",
            inputs=[
                "unit_id", "unit_number", "network_id", "ssid_name",
                "ap_group_id", "ap_group_name", "default_vlan",
                "dpsk_pool_id", "already_activated", "is_venue_wide",
            ],
            outputs=["activated", "already_active"],
            api_calls_per_unit=3,
            effect_field="activated",
            effect_label="SSIDs activated",
            effect_agg="truthy",
        ),

        # =====================================================================
        # Phase 8: Assign APs to the unit's AP Group (per-unit mode)
        # The SSID→AP Group binding is done by activate_ap_group; this phase
        # puts the unit's physical APs into that group.
        # =====================================================================
        Phase(
            id="assign_aps",
            name="Assign APs to AP Groups",
            description=(
                "Find APs by serial number or name and assign them to the "
                "unit's AP Group, so the unit's SSID reaches its own APs."
            ),
            executor="assign_aps",
            depends_on=["create_ap_group", "activate_ap_group"],
            per_unit=True,
            critical=True,
            # Phase handles the no-APs case gracefully
            skip_if="options.get('ssid_mode') != 'per_unit'",
            inputs=[
                "unit_id", "unit_number", "network_id", "ap_group_id",
                "ap_group_name", "ssid_name", "default_vlan",
                "ap_serial_numbers", "all_venue_aps", "is_venue_wide",
            ],
            outputs=["aps_matched", "aps_assigned", "ssid_configured"],
            api_calls_per_unit="dynamic",
            # Units with no AP assignment complete here without moving an AP,
            # so the unit count on its own reads as more work than happened.
            effect_field="aps_assigned",
            effect_label="APs assigned",
            effect_agg="sum",
        ),

        # =====================================================================
        # Phase 9: Configure LAN Ports (Optional, non-critical)
        # =====================================================================
        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            description=(
                "Configure LAN port VLANs on APs. "
                "Optional and non-critical."
            ),
            executor="configure_lan_ports",
            depends_on=["assign_aps"],
            per_unit=True,
            critical=False,
            skip_if="not options.get('configure_lan_ports', False)",
            inputs=[
                "unit_id", "unit_number", "default_vlan",
                "ap_serial_numbers", "all_venue_aps",
            ],
            outputs=["configured_aps", "failed_aps"],
            api_calls_per_unit="dynamic",
            effect_field="configured_aps",
            effect_label="APs configured",
            effect_agg="sum",
        ),

        # =====================================================================
        # Phase 10: Audit Results (GLOBAL)
        # =====================================================================
        Phase(
            id="cloudpath_audit",
            name="Audit Import Results",
            description="Summarize import results and verify resources.",
            executor="cloudpath_audit",
            depends_on=[
                "update_identity_descriptions",
                "create_access_policies",
                "activate_venue_wide",
                "activate_ap_group",
                "assign_aps",
                "configure_lan_ports",
            ],
            per_unit=False,
            critical=False,
            inputs=[
                "import_mode", "identity_group_ids", "dpsk_pool_ids",
                "created_count", "failed_count", "skipped_count",
                "policies_created", "identities_renamed",
                "aps_assigned", "ssid_configured",
            ],
            outputs=["summary", "success", "message"],
            api_calls_per_unit=0,
        ),
    ],
)
