"""
AP LAN Port Configuration Workflow (Standalone)

Configures LAN port VLANs on APs as a standalone workflow.
Reuses the same ConfigureLANPortsPhase from per-unit PSK/DPSK workflows.

This demonstrates the Phase-as-Workflow pattern:
- Same phase code runs standalone or as step N in a larger workflow.
- A standalone workflow is just a thin wrapper around the same phase(s).

Flow:
1. validate_lan_ports (global) → Build unit mappings, fetch venue APs
2. configure_lan_ports (per-unit) → Configure LAN port VLANs on APs

This is a simple 2-phase workflow. Units are processed in parallel.
"""

from workflow.workflows.definition import Workflow, Phase


APLanPortConfigWorkflow = Workflow(
    name="ap_lan_port_config",
    description="Configure LAN port VLANs on APs (standalone)",
    requires_confirmation=True,
    default_options={
        "configure_lan_ports": True,  # Always true for standalone
    },
    phases=[
        # Phase 0: Validate (global) - builds unit mappings and fetches APs
        Phase(
            id="validate_lan_ports",
            name="Validate AP Port Config",
            description=(
                "Build unit mappings and fetch venue APs for "
                "LAN port configuration."
            ),
            executor=(
                "workflow.phases.validate_lan_ports"
                ".ValidateLANPortsPhase"
            ),
            per_unit=False,
            critical=True,
            inputs=["units"],
            outputs=[
                "unit_mappings", "validation_result",
                "all_venue_aps",
            ],
            api_calls_per_unit=0,
        ),

        # Phase 1: Configure LAN Ports (per-unit)
        # Reuses the SAME phase from PSK/DPSK workflows
        Phase(
            id="configure_lan_ports",
            name="Configure LAN Ports",
            description=(
                "Configure LAN port VLANs on APs with "
                "configurable ports."
            ),
            executor=(
                "workflow.phases.configure_lan_ports"
                ".ConfigureLANPortsPhase"
            ),
            depends_on=["validate_lan_ports"],
            per_unit=True,
            critical=True,  # Critical for standalone workflow
            inputs=[
                "unit_id", "unit_number", "default_vlan",
                "ap_serial_numbers", "all_venue_aps",
            ],
            outputs=["configured_aps", "failed_aps"],
            api_calls_per_unit="dynamic",
        ),
    ],
)
