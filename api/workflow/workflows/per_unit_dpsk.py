"""
Per-Unit DPSK Workflow Definition

Configures per-unit DPSK (Dynamic Pre-Shared Key) SSIDs for a venue with
ONE SHARED Identity Group and DPSK Pool for all units.

Flow:
1. validate_dpsk (global) → Pre-check resources, build unit mappings,
   group passphrases, pause for confirmation
2. create_ap_group (per-unit) → Create/reuse AP Group per unit
3. create_identity_group (per-unit) → First unit creates shared IDG, others reuse
4. create_dpsk_pool (per-unit) → First unit creates shared pool, others reuse
5. create_dpsk_network (per-unit) → Create DPSK WiFi network linked to shared pool
6. create_passphrases (per-unit) → Create passphrases in the shared pool
7. activate_network (per-unit) → Activate SSID on venue
8. assign_aps (per-unit) → Assign APs to group + 3-step SSID→AP Group config
9. configure_lan_ports (per-unit, optional) → Configure LAN port VLANs on APs

Dependency graph (parallel execution):
    validate ─┬─> create_ap_group ──────────────────────────────────────────────┐
              │                                                                 │
              └─> create_identity_group ──> create_dpsk_pool ─┬─> create_dpsk_network ──> activate_network ─┤
                                                              │                                             │
                                                              └─> create_passphrases                        │
                                                                                                            │
                                                                          assign_aps ◄──────────────────────┘
                                                                              │
                                                                     configure_lan_ports

Key points:
- ONE Identity Group for all units (first unit creates, others reuse)
- ONE DPSK Pool for all units (first unit creates, others reuse)
- Each unit gets its own SSID (e.g., "108@PropertyName")
- All SSIDs link to the same shared DPSK pool
- Each unit's passphrases go into the shared pool
"""

from workflow.workflows.definition import Workflow, Phase


PerUnitDPSKWorkflow = Workflow(
    name="per_unit_dpsk",
    description="Configure per-unit DPSK SSIDs with one shared pool",
    requires_confirmation=True,
    default_options={
        "name_conflict_resolution": "keep",
        "configure_lan_ports": False,
        # Single shared resources for all units
        "identity_group_name": "",
        "dpsk_pool_name": "",
        "dpsk_pool_settings": {},
    },
    phases=[
        # Phase 0: Validate & Plan (global)
        Phase(
            id="validate_dpsk",
            name="Validate & Plan (DPSK)",
            description=(
                "Pre-check existing resources, build unit mappings, "
                "group passphrases by unit, fetch venue APs, "
                "and pause for user confirmation."
            ),
            executor="workflow.phases.validate_dpsk.ValidateDPSKPhase",
            per_unit=False,
            critical=True,
            inputs=[
                "units", "ap_group_prefix", "ap_group_postfix",
                # Single shared pool names (no more per-unit pools)
                "identity_group_name", "dpsk_pool_name",
                "dpsk_pool_settings", "name_conflict_resolution",
                "configure_lan_ports",
            ],
            outputs=[
                "unit_mappings", "validation_result", "all_venue_aps",
            ],
            api_calls_per_unit=0,
        ),

        # Phase 1: Create AP Groups (per-unit, parallel with Phase 2)
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description=(
                "Create or reuse AP Group for each unit. "
                "Must happen BEFORE SSIDs to avoid 15-SSID limit."
            ),
            executor="workflow.phases.create_ap_group.CreateAPGroupPhase",
            depends_on=["validate_dpsk"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_name"],
            outputs=["ap_group_id"],
            api_calls_per_unit=1,
        ),

        # Phase 2: Create Identity Group (first unit creates, others reuse)
        Phase(
            id="create_identity_group",
            name="Create Identity Group",
            description=(
                "Create shared Identity Group (first unit creates, others reuse). "
                "Required before DPSK pool can be created."
            ),
            executor=(
                "workflow.phases.create_identity_group"
                ".CreateIdentityGroupPhase"
            ),
            depends_on=["validate_dpsk"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "identity_group_name",
                "will_create_identity_group",  # Only first unit creates
            ],
            outputs=["identity_group_id"],
            api_calls_per_unit=1,
        ),

        # Phase 3: Create DPSK Pool (first unit creates, others reuse)
        Phase(
            id="create_dpsk_pool",
            name="Create DPSK Pool",
            description=(
                "Create shared DPSK Pool (first unit creates, others reuse). "
                "Contains passphrase settings for all units."
            ),
            executor=(
                "workflow.phases.create_dpsk_pool.CreateDPSKPoolPhase"
            ),
            depends_on=["create_identity_group"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "dpsk_pool_name",
                "identity_group_id", "passphrase_length",
                "passphrase_format", "max_devices_per_passphrase",
                "expiration_days",
                "will_create_dpsk_pool",  # Only first unit creates
            ],
            outputs=["dpsk_pool_id"],
            api_calls_per_unit=1,
        ),

        # Phase 4: Create DPSK Networks (per-unit, parallel with Phase 5)
        Phase(
            id="create_dpsk_network",
            name="Create DPSK Networks",
            description=(
                "Create DPSK WiFi network linked to DPSK pool. "
                "Two-step: create network + link DPSK service."
            ),
            executor=(
                "workflow.phases.create_dpsk_network"
                ".CreateDPSKNetworkPhase"
            ),
            depends_on=["create_dpsk_pool"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "network_name",
                "ssid_name", "dpsk_pool_id", "default_vlan",
                "name_conflict_resolution",
            ],
            outputs=["network_id"],
            api_calls_per_unit=2,  # create + link DPSK service
        ),

        # Phase 5: Create Passphrases (per-unit, parallel with Phase 4)
        Phase(
            id="create_passphrases",
            name="Create Passphrases",
            description=(
                "Create passphrases in the DPSK pool for each unit. "
                "Multiple passphrases per unit (one per CSV row)."
            ),
            executor=(
                "workflow.phases.create_passphrases"
                ".CreatePassphrasesPhase"
            ),
            depends_on=["create_dpsk_pool"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "dpsk_pool_id",
                "passphrases",
            ],
            outputs=[
                "created_count", "existed_count", "failed_count",
            ],
            api_calls_per_unit="dynamic",
        ),

        # Phase 6: Activate Networks on Venue (depends on network creation)
        Phase(
            id="activate_network",
            name="Activate Networks",
            description=(
                "Activate SSIDs at venue level "
                "(required before AP Group config)."
            ),
            executor=(
                "workflow.phases.activate_network.ActivateNetworkPhase"
            ),
            depends_on=["create_dpsk_network"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "network_id", "ssid_name",
            ],
            outputs=["activated", "already_active"],
            api_calls_per_unit=1,
        ),

        # Phase 7: Assign APs & Configure SSID
        # (depends on AP group + network activation)
        Phase(
            id="assign_aps",
            name="Assign APs & Configure SSID",
            description=(
                "Find APs, assign to AP Groups, and configure SSID "
                "for specific AP Groups (3-step R1 process)."
            ),
            executor="workflow.phases.assign_aps.AssignAPsPhase",
            depends_on=["create_ap_group", "activate_network"],
            per_unit=True,
            critical=True,
            inputs=[
                "unit_id", "unit_number", "network_id", "ap_group_id",
                "ap_group_name", "ssid_name", "default_vlan",
                "ap_serial_numbers", "all_venue_aps",
            ],
            outputs=["aps_matched", "aps_assigned", "ssid_configured"],
            api_calls_per_unit="dynamic",
        ),

        # Phase 8: Configure LAN Ports (optional, non-critical)
        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            description=(
                "Configure LAN port VLANs on APs with configurable "
                "ports. Optional and non-critical."
            ),
            executor=(
                "workflow.phases.configure_lan_ports"
                ".ConfigureLANPortsPhase"
            ),
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
    ],
)
