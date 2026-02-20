"""
Per-Unit PSK Workflow Definition

Configures per-unit PSK (WPA2/WPA3) SSIDs for a venue.

Flow:
1. validate (global) → Pre-check resources, build unit mappings, pause for confirmation
2. create_ap_group (per-unit) → Create/reuse AP Group per unit
3. create_psk_network (per-unit) → Create/reuse WiFi network per unit
4. activate_network (per-unit) → Activate SSID on venue, then move to specific AP Group
5. assign_aps (per-unit) → Assign APs to the AP Group
6. configure_lan_ports (per-unit, optional) → Configure LAN port VLANs on APs

Dependency graph (parallel execution):
    validate ─┬─> create_ap_group ──┬─> activate_network
              │                      └─> assign_aps ──> configure_lan_ports
              └─> create_psk_network ─┘

activate_network and assign_aps run in PARALLEL after both
create_ap_group and create_psk_network complete.

activate_network uses POST /networkActivations for single-step direct
activation to specific AP groups (no venue-wide intermediate state).
"""

from workflow.workflows.definition import Workflow, Phase


PerUnitPSKWorkflow = Workflow(
    name="per_unit_psk",
    description="Configure per-unit PSK (WPA2/WPA3) SSIDs",
    requires_confirmation=True,
    default_options={
        "name_conflict_resolution": "keep",
        "configure_lan_ports": False,
    },
    phases=[
        # Phase 0: Validate & Plan (global)
        Phase(
            id="validate",
            name="Validate & Plan",
            description=(
                "Pre-check existing resources, build unit mappings, "
                "fetch venue APs, and pause for user confirmation."
            ),
            executor="workflow.phases.validate_psk.ValidatePSKPhase",
            per_unit=False,
            critical=True,
            inputs=["units", "ap_group_prefix", "ap_group_postfix",
                    "name_conflict_resolution", "configure_lan_ports"],
            outputs=["unit_mappings", "validation_result", "all_venue_aps"],
            api_calls_per_unit=0,
        ),

        # Phase 1: Create AP Groups (per-unit, runs in parallel)
        Phase(
            id="create_ap_group",
            name="Create AP Groups",
            description=(
                "Create or reuse AP Group for each unit."
            ),
            executor="workflow.phases.create_ap_group.CreateAPGroupPhase",
            depends_on=["validate"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_name"],
            outputs=["ap_group_id"],
            api_calls_per_unit=1,
        ),

        # Phase 2: Create PSK Networks (per-unit, runs in parallel with Phase 1)
        Phase(
            id="create_psk_network",
            name="Create PSK Networks",
            description=(
                "Create or reuse WPA2/WPA3 WiFi networks. "
                "Handles name conflicts per resolution mode."
            ),
            executor="workflow.phases.create_psk_network.CreatePSKNetworkPhase",
            depends_on=["validate"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "network_name", "ssid_name",
                    "ssid_password", "security_type", "default_vlan",
                    "name_conflict_resolution"],
            outputs=["network_id"],
            api_calls_per_unit=1,
        ),

        # Phase 3: Activate Networks directly on specific AP Group
        # Depends on BOTH create_ap_group (need ap_group_id) and create_psk_network
        # (need network_id). Runs in PARALLEL with assign_aps.
        #
        # Uses POST /networkActivations for single-step direct activation
        # to a specific AP group (no venue-wide intermediate state).
        # No activation_slot needed — never goes venue-wide.
        #
        # Old 3-step phase (activate_network.py) kept as fallback.
        Phase(
            id="activate_network",
            name="Activate Networks",
            description=(
                "Activate SSIDs directly on specific AP Groups via "
                "POST /networkActivations (single-step)."
            ),
            executor="workflow.phases.activate_network_direct.ActivateNetworkDirectPhase",
            depends_on=["create_ap_group", "create_psk_network"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "network_id", "ap_group_id",
                    "ap_group_name", "ssid_name", "default_vlan",
                    "already_activated", "is_venue_wide"],
            outputs=["activated", "already_active"],
            api_calls_per_unit=1,
        ),

        # Phase 4: Assign APs to AP Group (per-unit, runs in PARALLEL with activate_network)
        # Only depends on create_ap_group (need ap_group_id).
        # AP assignment is independent of SSID activation.
        Phase(
            id="assign_aps",
            name="Assign APs to AP Groups",
            description=(
                "Find APs and assign them to the unit's AP Group."
            ),
            executor="workflow.phases.assign_aps.AssignAPsPhase",
            depends_on=["create_ap_group"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap_group_id",
                    "ap_group_name", "ssid_name",
                    "ap_serial_numbers", "all_venue_aps"],
            outputs=["aps_matched", "aps_assigned"],
            api_calls_per_unit="dynamic",
        ),

        # Phase 5: Configure LAN Ports (per-unit, optional, non-critical)
        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            description=(
                "Configure LAN port VLANs on APs with configurable ports. "
                "Optional and non-critical."
            ),
            executor="workflow.phases.configure_lan_ports.ConfigureLANPortsPhase",
            depends_on=["assign_aps"],
            per_unit=True,
            critical=False,
            skip_if="not options.get('configure_lan_ports', False)",
            inputs=["unit_id", "unit_number", "default_vlan",
                    "ap_serial_numbers", "all_venue_aps"],
            outputs=["configured_aps", "failed_aps"],
            api_calls_per_unit="dynamic",
        ),
    ],
)
