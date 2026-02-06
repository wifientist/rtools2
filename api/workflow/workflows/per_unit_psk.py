"""
Per-Unit PSK Workflow Definition

Configures per-unit PSK (WPA2/WPA3) SSIDs for a venue.

Flow:
1. validate (global) → Pre-check resources, build unit mappings, pause for confirmation
2. create_ap_group (per-unit) → Create/reuse AP Group per unit
3. create_psk_network (per-unit) → Create/reuse WiFi network per unit
4. activate_network (per-unit) → Activate SSID on venue (must happen after network exists)
5. assign_aps (per-unit) → Assign APs to group + 3-step SSID→AP Group config
6. configure_lan_ports (per-unit, optional) → Configure LAN port VLANs on APs

Dependency graph (parallel execution):
    validate ─┬─> create_ap_group ──────────┬─> assign_aps ──> configure_lan_ports
              └─> create_psk_network ──> activate_network ─┘

Units are processed independently: Unit 1's assign_aps can start as soon as
Unit 1's create_ap_group and activate_network are done, even if Unit 50
is still on create_psk_network.
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
                "Create or reuse AP Group for each unit. "
                "Must happen BEFORE SSIDs to avoid 15-SSID limit."
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

        # Phase 3: Activate Networks on Venue (per-unit, depends on network creation)
        Phase(
            id="activate_network",
            name="Activate Networks",
            description="Activate SSIDs at venue level (required before AP Group config).",
            executor="workflow.phases.activate_network.ActivateNetworkPhase",
            depends_on=["create_psk_network"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "network_id", "ssid_name"],
            outputs=["activated", "already_active"],
            api_calls_per_unit=1,
        ),

        # Phase 4: Assign APs & Configure SSID (per-unit, depends on AP group + activation)
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
            inputs=["unit_id", "unit_number", "network_id", "ap_group_id",
                    "ap_group_name", "ssid_name", "default_vlan",
                    "ap_serial_numbers", "all_venue_aps"],
            outputs=["aps_matched", "aps_assigned", "ssid_configured"],
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
