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
   - 1 shared DPSK pool → N SSIDs (one per unit)
   - Each unit gets its own SSID (e.g., "108@Property")
   - AP Groups created per-unit for targeted broadcast
   - APs assigned to groups, SSIDs configured per-group
   - Passphrases work on any unit's SSID (roaming enabled)
   - Use when: Per-unit SSID visibility needed

Flow (ssid_mode="per_unit"):
    validate_and_plan (global)
        │
        ├── create_identity_groups (shared)
        │       └── create_dpsk_pools (shared)
        │               │
        │               ├── create_passphrases ──> update_identity_descriptions
        │               │
        │               └── create_ap_group (per-unit) ─────────────────┐
        │                       │                                       │
        │                       └── create_dpsk_network (per-unit)      │
        │                               │                               │
        │                               └── activate_network            │
        │                                       │                       │
        │                                       └── assign_aps ◄────────┘
        │                                               │
        │                                       configure_lan_ports (optional)
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
    # Limit concurrent SSID activations to avoid R1's 15-SSID-per-AP-Group limit
    # When activating SSIDs, they temporarily broadcast to ALL AP Groups until
    # assigned to specific groups. This limits how many can be "in-flight".
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

        # AP Group settings (only used when ssid_mode="per_unit")
        "ap_group_prefix": "",       # e.g., "Unit-"
        "ap_group_postfix": "",      # e.g., "-APs"
        "ap_assignment_mode": "skip",  # "skip" | "csv" (from ap_assignments list)
        # ap_assignments: [{unit_number: "108", ap_identifier: "ABC123"}, ...]
        # ap_identifier can be AP serial number or AP name
        "ap_assignments": [],

        # Network settings
        "default_vlan": 1,
        "name_conflict_resolution": "keep",  # "keep" | "replace" | "rename"

        # LAN port configuration (optional)
        "configure_lan_ports": False,

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
        # Phase 1: Create Identity Group(s) - Shared across all units
        # =====================================================================
        Phase(
            id="create_identity_groups",
            name="Create Identity Groups",
            description="Create identity group(s) for DPSK passphrases.",
            executor="create_identity_groups",
            depends_on=["validate_and_plan"],
            per_unit=True,
            critical=True,
            inputs=[
                "import_mode", "identity_group_name", "identity_groups",
                "will_create_identity_group"  # From unit.plan - controls shared group creation
            ],
            outputs=["identity_group_id", "identity_group_ids"],
            api_calls_per_unit=2,
        ),

        # =====================================================================
        # Phase 2: Create DPSK Pool(s) - Shared across all units
        # =====================================================================
        Phase(
            id="create_dpsk_pools",
            name="Create DPSK Pools",
            description="Create DPSK service pool(s) linked to identity groups.",
            executor="create_dpsk_pools",
            depends_on=["create_identity_groups"],
            per_unit=True,
            critical=True,
            inputs=[
                "import_mode", "dpsk_pool_name", "identity_group_id",
                "pool_config", "dpsk_pools", "identity_group_ids",
                "will_create_dpsk_pool"  # From unit.plan - controls shared pool creation
            ],
            outputs=["dpsk_pool_id", "dpsk_pool_ids"],
            api_calls_per_unit=2,
        ),

        # =====================================================================
        # Phase 3: Create Passphrases - All go into the shared pool
        # =====================================================================
        Phase(
            id="create_passphrases",
            name="Create DPSK Passphrases",
            description=(
                "Create passphrases in DPSK pools. "
                "Uses parallel execution for bulk imports."
            ),
            executor="create_passphrases",
            depends_on=["create_dpsk_pools"],
            per_unit=True,
            critical=True,
            inputs=[
                "import_mode", "dpsk_pool_id", "passphrases",
                "options", "dpsk_pool_ids"
            ],
            outputs=[
                "created_count", "failed_count", "skipped_count",
                "created_passphrases", "failed_passphrases"
            ],
            api_calls_per_unit="dynamic",
        ),

        # =====================================================================
        # Phase 4: Update Identity Descriptions
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
            inputs=[
                "import_mode", "identity_group_id", "identity_group_ids",
                "created_passphrases", "options"
            ],
            outputs=["updated_count", "update_results"],
            api_calls_per_unit="dynamic",
        ),

        # =====================================================================
        # Phase 5: Create Access Policies (Optional)
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
        # Phase 6: Create AP Groups (per-unit, only when ssid_mode="per_unit")
        # Must happen BEFORE SSIDs to avoid 15-SSID limit per AP Group
        # =====================================================================
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description=(
                "Create AP Group for each unit. Required for per-unit SSID "
                "broadcast. Must happen BEFORE SSIDs to avoid 15-SSID limit."
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
        ),

        # =====================================================================
        # Phase 7: Create DPSK Networks (Optional - when ssid_mode != "none")
        # =====================================================================
        Phase(
            id="create_dpsk_network",
            name="Create DPSK Networks",
            description=(
                "Create DPSK WiFi networks linked to the DPSK pools. "
                "Runs when ssid_mode is 'single' or 'per_unit'."
            ),
            executor="create_dpsk_network",
            depends_on=["create_dpsk_pools"],
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
        ),

        # =====================================================================
        # Phase 8: Activate Networks on Venue (Optional)
        # NOTE: Uses activation_slot="acquire" to limit concurrent activations
        # due to R1's 15-SSID-per-AP-Group limit. The slot is held until
        # assign_aps completes to prevent too many SSIDs being "in-flight".
        # =====================================================================
        Phase(
            id="activate_network",
            name="Activate Networks",
            description=(
                "Activate SSIDs at venue level. "
                "Required before the SSID will be usable."
            ),
            executor="activate_network",
            depends_on=["create_dpsk_network"],
            per_unit=True,
            critical=False,
            skip_if="options.get('ssid_mode', 'none') == 'none'",
            inputs=[
                "unit_id", "unit_number", "network_id", "ssid_name",
            ],
            outputs=["activated", "already_active"],
            api_calls_per_unit=1,
            activation_slot="acquire",  # Acquire slot - released by assign_aps
        ),

        # =====================================================================
        # Phase 9: Assign APs & Configure SSID (per-unit, only when per_unit mode)
        # This is what makes per-unit SSID actually work - configures SSID
        # to broadcast ONLY on the unit's AP Group, not venue-wide.
        # NOTE: Uses activation_slot="release" to complete the activation cycle
        # and free up slot for the next unit's SSID activation.
        # =====================================================================
        Phase(
            id="assign_aps",
            name="Assign APs & Configure SSID",
            description=(
                "Find APs by serial number, assign to AP Groups, and "
                "configure SSID to broadcast only on that AP Group. "
                "This is the key step for per-unit SSID isolation."
            ),
            executor="assign_aps",
            depends_on=["create_ap_group", "activate_network"],
            per_unit=True,
            critical=True,
            # Only skip if not in per_unit mode - phase handles no-APs gracefully
            skip_if="options.get('ssid_mode') != 'per_unit'",
            inputs=[
                "unit_id", "unit_number", "network_id", "ap_group_id",
                "ap_group_name", "ssid_name", "default_vlan",
                "ap_serial_numbers", "all_venue_aps",
            ],
            outputs=["aps_matched", "aps_assigned", "ssid_configured"],
            api_calls_per_unit="dynamic",
            activation_slot="release",  # Release slot acquired by activate_network
        ),

        # =====================================================================
        # Phase 10: Configure LAN Ports (Optional, non-critical)
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
        ),

        # =====================================================================
        # Phase 11: Audit Results (GLOBAL)
        # =====================================================================
        Phase(
            id="cloudpath_audit",
            name="Audit Import Results",
            description="Summarize import results and verify resources.",
            executor="cloudpath_audit",
            depends_on=[
                "update_identity_descriptions",
                "create_access_policies",
                "activate_network",
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
