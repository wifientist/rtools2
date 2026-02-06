"""
Phase 5: Configure LAN Ports

Configures LAN port VLANs on APs with configurable LAN ports to match unit VLANs.

This phase uses the shared ap_port_config service which handles:
1. Finding the existing "Default ACCESS Port" profile (built-in to every venue)
2. Disabling venue settings inheritance for each AP's LAN ports
3. Setting VLAN overrides on each port via lanPorts/{port}/settings
4. Activating the default ACCESS profile on the port

For each unit:
1. Get APs in the unit's AP Group
2. Filter for APs with configurable LAN ports (H-series, R-series, T-series)
3. Configure each LAN port with the unit's VLAN and default ACCESS profile
"""

import logging
from typing import Dict, Any, List
from workflow.v2.models import Task, TaskStatus

# Import shared service
from services.ap_port_config import (
    configure_ap_ports,
    APPortRequest,
    PortConfig,
    PortMode,
)

# Import centralized AP model metadata
from r1api.models import (
    has_configurable_lan_ports as has_configurable_ports,
    get_port_count,
    get_uplink_port,
)

logger = logging.getLogger(__name__)


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


def build_port_configs_from_model_configs(
    model_port_configs: List[Dict[str, Any]],
    default_vlan: int,
    model: str
) -> Dict[str, PortConfig]:
    """
    Convert the per-unit SSID model_port_configs format to the shared service format.

    Args:
        model_port_configs: List of port config dicts from per-unit SSID input
        default_vlan: Unit's default VLAN for 'match' mode
        model: AP model (for uplink detection)

    Returns:
        Dict mapping port_id to PortConfig
    """
    port_configs = {}
    uplink_port = get_uplink_port(model)

    for idx, config in enumerate(model_port_configs):
        port_num = idx + 1
        port_id = f"LAN{port_num}"
        mode_str = config.get('mode', 'ignore')

        if mode_str == 'uplink':
            port_configs[port_id] = PortConfig(mode=PortMode.UPLINK)
        elif mode_str == 'ignore':
            port_configs[port_id] = PortConfig(mode=PortMode.IGNORE)
        elif mode_str == 'match':
            port_configs[port_id] = PortConfig(mode=PortMode.SPECIFIC, vlan=default_vlan)
        elif mode_str == 'specific':
            specific_vlan = config.get('vlan', 1)
            port_configs[port_id] = PortConfig(mode=PortMode.SPECIFIC, vlan=specific_vlan)
        elif mode_str == 'disable':
            port_configs[port_id] = PortConfig(mode=PortMode.DISABLE)

    return port_configs


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

    # Build AP lookup by name and serial
    ap_lookup_by_name = {ap.get('name', '').lower(): ap for ap in all_aps}
    ap_lookup_by_serial = {ap.get('serialNumber', '').upper(): ap for ap in all_aps}

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

    # Build APPortRequest list from unit results
    ap_requests: List[APPortRequest] = []
    unit_ap_mapping = {}  # Track which unit each AP belongs to

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

        logger.info(f"    [{unit_number}] Preparing LAN port config for {len(ap_names)} APs (VLAN {vlan_id})")
        await emit_message(f"[{unit_number}] Configuring LAN ports (VLAN {vlan_id})", "info")

        for ap_name in ap_names:
            # Find AP by name to get model
            ap = ap_lookup_by_name.get(ap_name.lower()) or ap_lookup_by_serial.get(ap_name.upper())

            if not ap:
                logger.warning(f"      AP '{ap_name}' not found in venue")
                continue

            model = ap.get('model', '')

            # Check if this AP has configurable LAN ports
            if not has_configurable_ports(model):
                logger.debug(f"      Skipping {ap_name} ({model}) - no configurable LAN ports")
                continue

            # Get the right port configs for this model type
            port_configs_for_model = get_port_configs_for_model(model_port_configs, model)

            if not port_configs_for_model:
                logger.warning(f"      No port configs for {ap_name} ({model})")
                continue

            # Convert to shared service format
            port_configs = build_port_configs_from_model_configs(
                port_configs_for_model, vlan_id, model
            )

            ap_requests.append(APPortRequest(
                ap_identifier=ap_name,
                ports=port_configs
            ))

            unit_ap_mapping[ap_name] = unit_number

    if not ap_requests:
        logger.info("  No APs to configure after filtering")
        return [Task(
            id="configure_lan_ports",
            name="No APs to configure",
            status=TaskStatus.COMPLETED,
            output_data={'message': 'No APs required configuration', 'configured_aps': 0}
        )]

    # Call the shared service
    result = await configure_ap_ports(
        r1_client=r1_client,
        venue_id=venue_id,
        tenant_id=tenant_id,
        ap_configs=ap_requests,
        dry_run=False,
        emit_message=emit_message
    )

    # Enhance results with unit information
    for ap_result in result.get('configured', []):
        ap_name = ap_result.get('ap_identifier')
        ap_result['unit_number'] = unit_ap_mapping.get(ap_name)

    for ap_result in result.get('already_configured', []):
        ap_name = ap_result.get('ap_identifier')
        ap_result['unit_number'] = unit_ap_mapping.get(ap_name)

    for ap_result in result.get('failed', []):
        ap_name = ap_result.get('ap_identifier')
        ap_result['unit_number'] = unit_ap_mapping.get(ap_name)

    for ap_result in result.get('skipped', []):
        ap_name = ap_result.get('ap_identifier')
        ap_result['unit_number'] = unit_ap_mapping.get(ap_name)

    # Build summary
    summary = result.get('summary', {})
    configured_aps = summary.get('configured', 0)
    already_configured_aps = summary.get('already_configured', 0)
    failed_aps = summary.get('failed', 0)
    skipped_aps = summary.get('skipped', 0)

    # Build summary string
    summary_parts = []
    if configured_aps > 0:
        summary_parts.append(f"{configured_aps} configured")
    if already_configured_aps > 0:
        summary_parts.append(f"{already_configured_aps} already correct")
    if failed_aps > 0:
        summary_parts.append(f"{failed_aps} failed")
    if skipped_aps > 0:
        summary_parts.append(f"{skipped_aps} skipped")
    summary_str = ", ".join(summary_parts) if summary_parts else "no changes"

    logger.info(f"  Phase 5 complete: {summary_str}")

    if failed_aps == 0 and (configured_aps > 0 or already_configured_aps > 0):
        await emit_message(f"LAN ports: {summary_str}", "success")
        task_status = TaskStatus.COMPLETED
    elif configured_aps > 0 or already_configured_aps > 0:
        await emit_message(f"LAN ports: {summary_str}", "warning")
        task_status = TaskStatus.COMPLETED
    elif failed_aps > 0:
        await emit_message(f"LAN port configuration failed for all {failed_aps} APs", "error")
        task_status = TaskStatus.FAILED
    else:
        await emit_message("No APs required LAN port configuration", "info")
        task_status = TaskStatus.COMPLETED

    return [Task(
        id="configure_lan_ports",
        name=f"LAN Ports: {summary_str}",
        status=task_status,
        output_data={
            'configured_aps': configured_aps,
            'already_configured_aps': already_configured_aps,
            'failed_aps': failed_aps,
            'skipped_aps': skipped_aps,
            'default_profile_id': result.get('default_profile_id'),
            'default_profile_name': result.get('default_profile_name'),
            'results': {
                'configured': result.get('configured', []),
                'already_configured': result.get('already_configured', []),
                'failed': result.get('failed', []),
                'skipped': result.get('skipped', [])
            }
        }
    )]
