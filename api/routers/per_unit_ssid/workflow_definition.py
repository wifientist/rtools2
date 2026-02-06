"""
Per-Unit SSID Configuration Workflow Definition

Defines the workflow for configuring per-unit SSIDs in RuckusONE.

PSK Mode (default):
1. Create AP Groups FIRST (before SSIDs exist - avoids 15 SSID limit issue)
2. Create SSIDs for each unit (WPA2/WPA3)
3. Activate SSIDs on venue
4. Process units (find APs, assign to groups, activate SSIDs on groups)
5. (Optional) Configure LAN ports on APs with configurable ports

DPSK Mode (when dpsk_mode=True):
1. Create AP Groups
2. Create Identity Groups (one per unit)
3. Create DPSK Pools (linked to identity groups)
4. Create DPSK WiFi Networks (linked to DPSK pools)
5. Create Passphrases (in DPSK pools)
6. Activate SSIDs on venue
7. Process units (AP assignment + SSID activation on groups)
8. (Optional) Configure LAN ports

NOTE: AP Groups are created first because R1 auto-activates all existing venue
SSIDs on new AP Groups. By creating AP Groups before the per-unit SSIDs exist,
we avoid hitting the default 15 SSID per AP Group limit.
"""

from typing import List
from workflow.v2.models import WorkflowDefinition, PhaseDefinition


# ============================================================================
# PSK Mode Phases (Standard WPA2/WPA3)
# ============================================================================

PSK_PHASES: List[PhaseDefinition] = [
    # Phase 1: Create AP Groups FIRST (before SSIDs exist to avoid 15 SSID limit)
    PhaseDefinition(
        id="create_ap_groups",
        name="Create AP Groups",
        dependencies=[],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ap_groups.execute"
    ),

    # Phase 2: Create SSIDs (PSK mode)
    PhaseDefinition(
        id="create_ssids",
        name="Create SSIDs",
        dependencies=["create_ap_groups"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.wifi_networks.execute"
    ),

    # Phase 3: Activate SSIDs on Venue
    PhaseDefinition(
        id="activate_ssids",
        name="Activate SSIDs on Venue",
        dependencies=["create_ssids"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ssid_activation.execute"
    ),

    # Phase 4: Process Units (AP assignment + SSID activation on groups)
    PhaseDefinition(
        id="process_units",
        name="Process Units",
        dependencies=["activate_ssids"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ap_assignment.execute"
    ),
]


# ============================================================================
# DPSK Mode Phases (Dynamic Pre-Shared Key)
# ============================================================================

DPSK_PHASES: List[PhaseDefinition] = [
    # Phase 1: Create AP Groups
    PhaseDefinition(
        id="create_ap_groups",
        name="Create AP Groups",
        dependencies=[],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ap_groups.execute"
    ),

    # Phase 2: Create Identity Groups
    PhaseDefinition(
        id="create_identity_groups",
        name="Create Identity Groups",
        dependencies=["create_ap_groups"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.identity_groups.execute"
    ),

    # Phase 3: Create DPSK Pools
    PhaseDefinition(
        id="create_dpsk_pools",
        name="Create DPSK Pools",
        dependencies=["create_identity_groups"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.dpsk_pools.execute"
    ),

    # Phase 4: Create DPSK WiFi Networks
    PhaseDefinition(
        id="create_wifi_networks",
        name="Create DPSK WiFi Networks",
        dependencies=["create_dpsk_pools"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.wifi_networks.execute"
    ),

    # Phase 5: Create Passphrases
    PhaseDefinition(
        id="create_passphrases",
        name="Create Passphrases",
        dependencies=["create_wifi_networks"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.passphrases.execute"
    ),

    # Phase 6: Activate SSIDs on Venue
    PhaseDefinition(
        id="activate_ssids",
        name="Activate SSIDs on Venue",
        dependencies=["create_passphrases"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ssid_activation.execute"
    ),

    # Phase 7: Process Units (AP assignment + SSID activation on groups)
    PhaseDefinition(
        id="process_units",
        name="Process Units",
        dependencies=["activate_ssids"],
        parallelizable=False,
        critical=True,
        executor="workflow.phases.ap_assignment.execute"
    ),
]


# ============================================================================
# Optional LAN Port Configuration Phase
# ============================================================================

LAN_PORT_PHASE = PhaseDefinition(
    id="configure_lan_ports",
    name="Configure LAN Ports",
    dependencies=["process_units"],
    parallelizable=False,
    critical=False,  # Non-critical - workflow succeeds even if this fails
    executor="workflow.phases.lan_ports.execute"
)


# ============================================================================
# Legacy Phase Definitions (for backward compatibility)
# These use the old router-specific paths but can be updated to shared phases
# ============================================================================

LEGACY_PSK_PHASES: List[PhaseDefinition] = [
    PhaseDefinition(
        id="create_ap_groups",
        name="Create AP Groups",
        dependencies=[],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_ap_groups.execute"
    ),
    PhaseDefinition(
        id="create_ssids",
        name="Create SSIDs",
        dependencies=["create_ap_groups"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.create_ssids.execute"
    ),
    PhaseDefinition(
        id="activate_ssids",
        name="Activate SSIDs on Venue",
        dependencies=["create_ssids"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.activate_ssids.execute"
    ),
    PhaseDefinition(
        id="process_units",
        name="Process Units",
        dependencies=["activate_ssids"],
        parallelizable=False,
        critical=True,
        executor="routers.per_unit_ssid.phases.process_units.execute"
    ),
]

LEGACY_LAN_PORT_PHASE = PhaseDefinition(
    id="configure_lan_ports",
    name="Configure LAN Ports",
    dependencies=["process_units"],
    parallelizable=False,
    critical=False,
    executor="routers.per_unit_ssid.phases.configure_lan_ports.execute"
)


def get_workflow_definition(
    configure_lan_ports: bool = False,
    dpsk_mode: bool = False,
    use_legacy_phases: bool = False
) -> WorkflowDefinition:
    """
    Get the Per-Unit SSID workflow definition.

    Args:
        configure_lan_ports: If True, includes LAN port configuration phase
        dpsk_mode: If True, uses DPSK workflow phases instead of PSK
        use_legacy_phases: If True, uses old router-specific phase paths
                          (for backward compatibility during migration)

    Returns:
        WorkflowDefinition with appropriate phases
    """
    if use_legacy_phases:
        # Use old router-specific paths
        phases = LEGACY_PSK_PHASES.copy()
        lan_phase = LEGACY_LAN_PORT_PHASE
    elif dpsk_mode:
        # Use DPSK workflow with shared phases
        phases = DPSK_PHASES.copy()
        lan_phase = LAN_PORT_PHASE
    else:
        # Use PSK workflow with shared phases
        phases = PSK_PHASES.copy()
        lan_phase = LAN_PORT_PHASE

    if configure_lan_ports:
        phases.append(lan_phase)

    description = (
        "Configure per-unit DPSK SSIDs in RuckusONE with AP Groups and Identity Groups"
        if dpsk_mode
        else "Configure per-unit SSIDs in RuckusONE with AP Groups"
    )

    return WorkflowDefinition(
        name="per_unit_ssid_configuration",
        description=description,
        phases=phases
    )
