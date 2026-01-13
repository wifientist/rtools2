"""
Phase 5: Configure LAN Ports

Configures LAN port VLANs on APs with configurable LAN ports to match unit VLANs.

Uses the same approach as the R1 GUI:
1. Find the existing "Default ACCESS Port" profile (built-in to every venue)
2. Disable venue settings inheritance for each AP's LAN ports
3. Set VLAN overrides on each port via lanPorts/{port}/settings
4. Activate the default ACCESS profile on the port

For each unit:
1. Get APs in the unit's AP Group
2. Filter for APs with configurable LAN ports (H-series, R-series, T-series)
3. Configure each LAN port with the unit's VLAN and default ACCESS profile
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)

# Model to port count mapping
# This defines how many configurable LAN ports each model has
# (excludes the uplink port which is handled separately)
MODEL_PORT_COUNTS = {
    # 1-port models with LAN1 as uplink (LAN2 is configurable)
    "R500": 1,
    "R510": 1,
    "R600": 1,
    "R610": 1,
    "R710": 1,
    "T610": 1,
    "T610S": 1,
    "T710": 1,
    "T710S": 1,
    # 1-port models with LAN2 as uplink (LAN1 is configurable)
    "R550": 1,
    "R560": 1,
    "R575": 1,
    "R650": 1,
    "R670": 1,
    "R720": 1,
    "R730": 1,
    "R750": 1,
    "R760": 1,
    "R770": 1,
    "R850": 1,
    "T350": 1,
    "T670": 1,
    "T670SN": 1,
    # 2-port models (LAN1, LAN2 configurable; LAN3 is uplink)
    "H320": 2,
    "H350": 2,
    "T750": 2,
    "T750SE": 2,
    # 4-port models (LAN1-4 configurable; LAN5 is uplink)
    "H510": 4,
    "H550": 4,
    "H670": 4,
}

# Uplink/WAN port for each model - these ports should be protected from disable/VLAN changes
# Based on Ruckus hardware documentation - the uplink is the PoE-in port
MODEL_UPLINK_PORTS = {
    # 1-port models: LAN1 is the POE/uplink, leaving LAN2 as access
    "R500": "LAN1",
    "R510": "LAN1",
    "R600": "LAN1",
    "R610": "LAN1",
    "R710": "LAN1",
    "T610": "LAN1",
    "T610S": "LAN1",
    "T710": "LAN1",
    "T710S": "LAN1",
    # 1-port models: LAN2 is the POE/uplink port, leaving LAN1 as access
    "R550": "LAN2",
    "R560": "LAN2",
    "R650": "LAN2",
    "R670": "LAN2",
    "R720": "LAN2",
    "R730": "LAN2",
    "R750": "LAN2",
    "R760": "LAN2",
    "R770": "LAN2",
    "R850": "LAN2",
    "T670": "LAN2",
    "T670SN": "LAN2",
    # 2-port models: LAN3 is the uplink (PoE-in), LAN1-2 are the access ports
    'H320': 'LAN3',
    'H350': 'LAN3',
    "T750": "LAN3",
    "T750SE": "LAN3",
    # 4-port models: LAN5 is the uplink (PoE-in), LAN1-4 are access ports
    'H510': 'LAN5',
    'H550': 'LAN5',
    'H670': 'LAN5',
}


def has_configurable_ports(model: str) -> bool:
    """Check if AP model has configurable LAN ports"""
    if not model:
        return False
    # Check if model is in our port count mapping
    model_upper = model.upper()
    for model_prefix in MODEL_PORT_COUNTS.keys():
        if model_upper.startswith(model_prefix.upper()):
            return True
    return False


def get_port_count(model: str) -> int:
    """Get the number of LAN ports for a given model"""
    if not model:
        return 0
    model_upper = model.upper()
    for model_prefix, count in MODEL_PORT_COUNTS.items():
        if model_upper.startswith(model_prefix.upper()):
            return count
    return 0


def get_uplink_port(model: str) -> str | None:
    """
    Get the uplink/WAN port for a given model.

    Returns the port ID (e.g., 'LAN1') that is the uplink for the model,
    or None if no uplink is defined for the model.
    """
    if not model:
        return None
    model_upper = model.upper()
    for model_prefix, uplink_port in MODEL_UPLINK_PORTS.items():
        if model_upper.startswith(model_prefix.upper()):
            return uplink_port
    return None


def is_uplink_port(model: str, port_id: str) -> bool:
    """Check if the given port is the uplink port for this model."""
    uplink = get_uplink_port(model)
    if not uplink:
        return False
    return port_id.upper() == uplink.upper()


def get_port_configs_for_model(model_port_configs: Dict[str, Any], model: str) -> List[Dict[str, Any]]:
    """
    Get the appropriate port configs based on the AP model.

    Args:
        model_port_configs: Dict with keys for different model types:
            - 'one_port_lan1_uplink': 1-port models where LAN1 is uplink
            - 'one_port_lan2_uplink': 1-port models where LAN2 is uplink
            - 'two_port': 2-port wall-plate models
            - 'four_port': 4-port wall-plate models
        model: AP model to determine which config to use

    Returns:
        List of port config dicts for this model
    """
    port_count = get_port_count(model)
    uplink_port = get_uplink_port(model)

    if port_count == 1:
        # Select config based on which port is the uplink
        if uplink_port == 'LAN1':
            return model_port_configs.get('one_port_lan1_uplink', [])
        elif uplink_port == 'LAN2':
            return model_port_configs.get('one_port_lan2_uplink', [])
        else:
            # Fallback - shouldn't happen if MODEL_UPLINK_PORTS is complete
            return model_port_configs.get('one_port_lan1_uplink', [])
    elif port_count == 2:
        return model_port_configs.get('two_port', [])
    elif port_count == 4:
        return model_port_configs.get('four_port', [])
    else:
        return []


def get_ports_to_configure(
    port_configs: List[Dict[str, Any]],
    default_vlan: int,
    model: str = None
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Process port_configs list and return list of ports to configure.

    Protects uplink ports from being disabled or having VLAN changed.

    Args:
        port_configs: List of port configuration dicts for this model type
        default_vlan: Unit's default VLAN for 'match' mode
        model: AP model to check for uplink port protection

    Returns:
        Tuple of (ports_to_configure, protected_ports):
        - ports_to_configure: List of dicts with port_id, vlan, and action for each port
        - protected_ports: List of ports that were protected from changes (skipped)
    """
    ports_to_configure = []
    protected_ports = []
    uplink_port = get_uplink_port(model) if model else None

    for idx, config in enumerate(port_configs):
        port_num = idx + 1  # Port numbers are 1-indexed
        port_id = f"LAN{port_num}"
        mode = config.get('mode', 'match')

        # Check if this is the uplink port (either by mode or by MODEL_UPLINK_PORTS)
        is_uplink = uplink_port and port_id.upper() == uplink_port.upper()

        if mode == 'uplink':
            # Explicitly marked as uplink - skip (don't configure)
            protected_ports.append({
                'port_id': port_id,
                'requested_action': 'skip',
                'reason': 'uplink_port',
                'is_uplink': True
            })
            logger.debug(f"    SKIP: {port_id} is marked as uplink - not configurable")
        elif mode == 'ignore':
            # Ignore - don't make any changes to this port
            logger.debug(f"    IGNORE: {port_id} - no changes requested")
            # Don't add to either list - just skip silently
        elif mode == 'match':
            # Use unit's default VLAN
            ports_to_configure.append({
                'port_id': port_id,
                'vlan': default_vlan,
                'action': 'configure',
                'is_uplink': is_uplink
            })
        elif mode == 'specific':
            # Use the specified VLAN
            specific_vlan = config.get('vlan', 1)
            ports_to_configure.append({
                'port_id': port_id,
                'vlan': specific_vlan,
                'action': 'configure',
                'is_uplink': is_uplink
            })
        elif mode == 'disable':
            # Protect uplink from being disabled
            if is_uplink:
                protected_ports.append({
                    'port_id': port_id,
                    'requested_action': 'disable',
                    'reason': 'uplink_protected',
                    'is_uplink': True
                })
                logger.warning(f"    PROTECTED: {port_id} is the uplink port for {model} - cannot disable")
            else:
                # Mark for disabling (non-uplink ports only)
                ports_to_configure.append({
                    'port_id': port_id,
                    'vlan': None,
                    'action': 'disable',
                    'is_uplink': False
                })

    return ports_to_configure, protected_ports


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Configure LAN ports on APs to match unit VLANs.

    Args:
        context: Execution context with unit data from previous phases

    Returns:
        List of tasks with LAN port configuration results
    """
    logger.info("Phase 5: Configure LAN Ports")

    # Get data from previous phase (process_units)
    phase4_results = context.get('previous_phase_results', {}).get('process_units', {})
    aggregated = phase4_results.get('aggregated', {})

    # Get unit results from phase 4 - contains unit_number, ap_group_id, matched APs
    unit_results_list = aggregated.get('unit_results', [[]])
    unit_results = unit_results_list[0] if unit_results_list else []

    # Get original units data for VLAN info from input_data (has the original CSV data)
    input_data = context.get('input_data', {})
    units = input_data.get('units', [])

    # Build unit lookup by unit_number
    unit_lookup = {u.get('unit_number'): u for u in units}

    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')
    event_publisher = context.get('event_publisher')
    job_id = context.get('job_id')

    # Get options from context (workflow engine passes these)
    options = context.get('options', {})
    input_data = context.get('input_data', {})

    # Get model port configurations - default to 'ignore' (no changes), uplinks marked as 'uplink'
    default_model_port_configs = {
        # 1-port models with LAN1 as uplink: [LAN1=uplink, LAN2=ignore]
        'one_port_lan1_uplink': [{'mode': 'uplink'}, {'mode': 'ignore'}],
        # 1-port models with LAN2 as uplink: [LAN1=ignore, LAN2=uplink]
        'one_port_lan2_uplink': [{'mode': 'ignore'}, {'mode': 'uplink'}],
        # 2-port wall-plates: [LAN1=ignore, LAN2=ignore, LAN3=uplink]
        'two_port': [{'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'uplink'}],
        # 4-port wall-plates: [LAN1-4=ignore, LAN5=uplink]
        'four_port': [{'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'uplink'}]
    }
    model_port_configs = options.get('model_port_configs') or input_data.get('model_port_configs', default_model_port_configs)

    # Helper to emit status messages
    async def emit_message(message: str, level: str = "info", details: dict = None):
        if event_publisher and job_id:
            await event_publisher.message(job_id, message, level, details)

    if not r1_client:
        raise Exception("R1 client not available in context")

    if not unit_results:
        logger.info("  No unit results from Phase 4 - skipping LAN port configuration")
        return [Task(
            id="configure_lan_ports",
            name="No units to configure",
            status=TaskStatus.COMPLETED,
            output_data={'message': 'No units processed in Phase 4', 'configured_aps': 0}
        )]

    await emit_message("Configuring LAN ports on APs using Ethernet Port Profiles...", "info")

    # Fetch all APs in venue to get model information
    all_aps = []
    try:
        logger.info(f"    Fetching APs in venue {venue_id}...")
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
        logger.info(f"    Found {len(all_aps)} total APs in venue")
    except Exception as e:
        logger.error(f"    Failed to fetch APs: {str(e)}")
        await emit_message(f"Failed to fetch APs: {str(e)}", "error")
        return [Task(
            id="configure_lan_ports",
            name="Failed to fetch APs",
            status=TaskStatus.FAILED,
            output_data={'error': str(e)}
        )]

    # Build AP lookup by serial
    ap_lookup = {ap.get('serialNumber'): ap for ap in all_aps}

    # Count APs with configurable ports
    configurable_ap_count = sum(1 for ap in all_aps if has_configurable_ports(ap.get('model', '')))
    logger.info(f"    Found {configurable_ap_count} APs with configurable LAN ports in venue")
    await emit_message(f"Found {configurable_ap_count} APs with configurable LAN ports", "info")

    if configurable_ap_count == 0:
        logger.info("  No APs with configurable ports found - skipping LAN port configuration")
        return [Task(
            id="configure_lan_ports",
            name="No APs with configurable ports",
            status=TaskStatus.COMPLETED,
            output_data={'message': 'No APs with configurable LAN ports found', 'configured_aps': 0}
        )]

    # Step 1: Find the existing "Default ACCESS Port" profile (built-in to every venue)
    default_profile = await r1_client.ethernet_port_profiles.find_default_access_profile(tenant_id=tenant_id)

    if not default_profile:
        logger.error("    Could not find 'Default ACCESS Port' profile - cannot configure LAN ports")
        await emit_message("Could not find Default ACCESS Port profile", "error")
        return [Task(
            id="configure_lan_ports",
            name="Default ACCESS profile not found",
            status=TaskStatus.FAILED,
            output_data={'error': 'Could not find Default ACCESS Port profile', 'configured_aps': 0}
        )]

    default_profile_id = default_profile.get('id')
    logger.info(f"    Found default ACCESS profile: {default_profile.get('name')} (ID: {default_profile_id})")
    await emit_message(f"Using default ACCESS profile: {default_profile.get('name')}", "info")

    configured_aps = 0
    failed_aps = 0
    skipped_aps = 0
    protected_count = 0  # Count of ports protected from disable/changes
    results = []

    # Step 2: Process each unit that was successfully configured in Phase 4
    for unit_result in unit_results:
        unit_number = unit_result.get('unit_number')
        status = unit_result.get('status')

        if status != 'success':
            logger.info(f"    Skipping unit {unit_number} - Phase 4 status: {status}")
            continue

        # Get the VLAN for this unit
        unit_data = unit_lookup.get(unit_number, {})
        default_vlan = unit_data.get('default_vlan', '1')

        try:
            vlan_id = int(default_vlan)
        except ValueError:
            logger.warning(f"    Invalid VLAN '{default_vlan}' for unit {unit_number}, skipping")
            continue

        # Get the AP names/serials from phase 4 result
        ap_names = unit_result.get('details', {}).get('ap_names', [])

        if not ap_names:
            logger.info(f"    Unit {unit_number}: No APs assigned, skipping")
            continue

        logger.info(f"    [{unit_number}] Configuring LAN ports for {len(ap_names)} APs (VLAN {vlan_id})")
        await emit_message(f"[{unit_number}] Configuring LAN ports (VLAN {vlan_id})", "info")

        for ap_name in ap_names:
            # Find AP by name to get serial and model
            ap = None
            for candidate in all_aps:
                if candidate.get('name') == ap_name or candidate.get('serialNumber') == ap_name:
                    ap = candidate
                    break

            if not ap:
                logger.warning(f"      AP '{ap_name}' not found in venue")
                skipped_aps += 1
                continue

            serial = ap.get('serialNumber')
            model = ap.get('model', '')

            # Check if this AP has configurable LAN ports
            if not has_configurable_ports(model):
                logger.info(f"      Skipping {ap_name} ({model}) - no configurable LAN ports")
                skipped_aps += 1
                continue

            # Get the right port configs for this model type
            port_configs_for_model = get_port_configs_for_model(model_port_configs, model)

            if not port_configs_for_model:
                logger.warning(f"      No port configs for {ap_name} ({model})")
                skipped_aps += 1
                continue

            # Get ports to configure with their actions (now returns tuple with protected ports)
            ports_to_configure, protected_ports = get_ports_to_configure(port_configs_for_model, vlan_id, model)

            # Track protected uplink ports
            if protected_ports:
                protected_count += len(protected_ports)
                for pp in protected_ports:
                    await emit_message(
                        f"[{unit_number}] {ap_name}: {pp['port_id']} protected (uplink port cannot be disabled)",
                        "warning"
                    )

            if not ports_to_configure:
                logger.warning(f"      No ports to configure for {ap_name} ({model})")
                skipped_aps += 1
                continue

            def format_port(p):
                if p['action'] == 'disable':
                    return f"{p['port_id']}=disabled"
                uplink_marker = " (uplink)" if p.get('is_uplink') else ""
                return f"{p['port_id']}=VLAN{p['vlan']}{uplink_marker}"

            port_summary = ", ".join([format_port(p) for p in ports_to_configure])
            logger.info(f"      Configuring {ap_name} ({model}): {port_summary}")

            try:
                # Step 2a: Disable venue settings inheritance for this AP's LAN ports
                await r1_client.venues.set_ap_lan_port_specific_settings(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    use_venue_settings=False,
                    wait_for_completion=True
                )

                # Step 2b: Configure each port - set VLAN override then activate default profile
                configured_ports = []
                for port_config in ports_to_configure:
                    if port_config['action'] == 'configure':
                        port_vlan = port_config['vlan']

                        # First, set the VLAN override on the port
                        await r1_client.venues.set_ap_lan_port_settings(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            serial_number=serial,
                            port_id=port_config['port_id'],
                            untagged_vlan=port_vlan,
                            wait_for_completion=True
                        )

                        # Then activate the default ACCESS profile on the port
                        await r1_client.ethernet_port_profiles.activate_profile_on_ap_lan_port(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            serial_number=serial,
                            port_id=port_config['port_id'],
                            profile_id=default_profile_id,
                            wait_for_completion=True
                        )

                    elif port_config['action'] == 'disable':
                        # For disabling, we still use the direct API
                        await r1_client.venues.set_ap_lan_port_enabled(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            serial_number=serial,
                            port_id=port_config['port_id'],
                            enabled=False,
                            wait_for_completion=True
                        )
                    configured_ports.append(port_config)

                configured_aps += 1
                results.append({
                    'unit_number': unit_number,
                    'ap_name': ap_name,
                    'serial': serial,
                    'model': model,
                    'uplink_port': get_uplink_port(model),
                    'ports': configured_ports,
                    'protected_ports': protected_ports,
                    'profile_id': default_profile_id,
                    'status': 'success'
                })

                protected_info = f", {len(protected_ports)} protected" if protected_ports else ""
                logger.info(f"        Configured {ap_name}: {len(configured_ports)} ports{protected_info}")

            except Exception as e:
                logger.error(f"        Failed to configure {ap_name}: {str(e)}")
                failed_aps += 1
                results.append({
                    'unit_number': unit_number,
                    'ap_name': ap_name,
                    'serial': serial,
                    'model': model,
                    'error': str(e),
                    'status': 'failed'
                })

    # Summary
    protected_msg = f", {protected_count} uplink ports protected" if protected_count > 0 else ""
    logger.info(f"  Phase 5 complete: {configured_aps} configured, {failed_aps} failed, {skipped_aps} skipped{protected_msg}")

    if failed_aps == 0 and configured_aps > 0:
        success_msg = f"Configured LAN ports on {configured_aps} APs"
        if protected_count > 0:
            success_msg += f" ({protected_count} uplink ports protected)"
        await emit_message(success_msg, "success")
        task_status = TaskStatus.COMPLETED
    elif configured_aps > 0:
        await emit_message(f"LAN ports: {configured_aps} configured, {failed_aps} failed", "warning")
        task_status = TaskStatus.COMPLETED
    elif failed_aps > 0:
        await emit_message(f"LAN port configuration failed for all {failed_aps} APs", "error")
        task_status = TaskStatus.FAILED
    else:
        await emit_message("No APs required LAN port configuration", "info")
        task_status = TaskStatus.COMPLETED

    return [Task(
        id="configure_lan_ports",
        name=f"LAN Ports: {configured_aps} configured, {failed_aps} failed, {skipped_aps} skipped",
        status=task_status,
        output_data={
            'configured_aps': configured_aps,
            'failed_aps': failed_aps,
            'skipped_aps': skipped_aps,
            'protected_uplink_ports': protected_count,
            'default_profile_id': default_profile_id,
            'default_profile_name': default_profile.get('name'),
            'results': results
        }
    )]
