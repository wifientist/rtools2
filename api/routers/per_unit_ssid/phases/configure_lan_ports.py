"""
Phase 5: Configure LAN Ports

Configures LAN port VLANs on wall-plate APs (H-series) to match unit VLANs.

For each unit:
1. Get APs in the unit's AP Group
2. Filter for wall-plate APs (H320, H510, H550, etc.)
3. Disable venue settings inheritance for LAN ports
4. Set LAN port untagged VLAN to match unit's default_vlan
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)

# Wall-plate AP models that have configurable LAN ports
WALL_PLATE_MODELS = [
    'H320',
    'H350',
    'H510',
    'H550',
    'H670',
]

# Model to port count mapping
MODEL_PORT_COUNTS = {
    # 1-port models (future/custom)
    "R550": 1,
    "R650": 1,
    "R750": 1,
    "R850": 1,
    "T350": 1,
    "T750": 1,
    "R575": 1,
    "R670": 1,
    "R770": 1,
    "T670": 1,
    # 2-port models
    'H320': 2,
    'H350': 2,
    # 4-port models
    'H510': 4,
    'H550': 4,
    'H670': 4,
}


def is_wall_plate_ap(model: str) -> bool:
    """Check if AP model is a wall-plate with configurable LAN ports"""
    if not model:
        return False
    # Check if model starts with any wall-plate prefix
    model_upper = model.upper()
    for wall_plate in WALL_PLATE_MODELS:
        if model_upper.startswith(wall_plate.upper()):
            return True
    return False


def get_port_count(model: str) -> int:
    """Get the number of LAN ports for a given model"""
    if not model:
        return 0
    model_upper = model.upper()
    for wall_plate, count in MODEL_PORT_COUNTS.items():
        if model_upper.startswith(wall_plate.upper()):
            return count
    return 0


def get_port_configs_for_model(model_port_configs: Dict[str, Any], model: str) -> List[Dict[str, Any]]:
    """
    Get the appropriate port configs based on the AP model.

    Args:
        model_port_configs: Dict with 'one_port', 'two_port' and 'four_port' keys
        model: AP model to determine which config to use

    Returns:
        List of port config dicts for this model
    """
    port_count = get_port_count(model)

    if port_count == 1:
        return model_port_configs.get('one_port', [])
    elif port_count == 2:
        return model_port_configs.get('two_port', [])
    elif port_count == 4:
        return model_port_configs.get('four_port', [])
    else:
        return []


def get_ports_to_configure(port_configs: List[Dict[str, Any]], default_vlan: int) -> List[Dict[str, Any]]:
    """
    Process port_configs list and return list of ports to configure.

    Args:
        port_configs: List of port configuration dicts for this model type
        default_vlan: Unit's default VLAN for 'match' mode

    Returns:
        List of dicts with port_id, vlan, and action for each port
    """
    ports_to_configure = []

    for idx, config in enumerate(port_configs):
        port_num = idx + 1  # Port numbers are 1-indexed
        port_id = f"LAN{port_num}"
        mode = config.get('mode', 'match')

        if mode == 'match':
            # Use unit's default VLAN
            ports_to_configure.append({
                'port_id': port_id,
                'vlan': default_vlan,
                'action': 'configure'
            })
        elif mode == 'specific':
            # Use the specified VLAN
            specific_vlan = config.get('vlan', 1)
            ports_to_configure.append({
                'port_id': port_id,
                'vlan': specific_vlan,
                'action': 'configure'
            })
        elif mode == 'disable':
            # Mark for disabling
            ports_to_configure.append({
                'port_id': port_id,
                'vlan': None,
                'action': 'disable'
            })

    return ports_to_configure


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Configure LAN ports on wall-plate APs to match unit VLANs.

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

    # Get original units data for VLAN info
    phase3_results = context.get('previous_phase_results', {}).get('create_ap_groups', {})
    phase3_aggregated = phase3_results.get('aggregated', {})
    units_list = phase3_aggregated.get('units', [[]])
    units = units_list[0] if units_list else []

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

    # Get model port configurations - default to all ports with 'match' mode
    default_model_port_configs = {
        'one_port': [{'mode': 'match'}],
        'two_port': [{'mode': 'match'}, {'mode': 'match'}],
        'four_port': [{'mode': 'match'}, {'mode': 'match'}, {'mode': 'match'}, {'mode': 'match'}]
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

    await emit_message("Configuring LAN ports on wall-plate APs...", "info")

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

    # Count wall-plate APs
    wall_plate_count = sum(1 for ap in all_aps if is_wall_plate_ap(ap.get('model', '')))
    logger.info(f"    Found {wall_plate_count} wall-plate APs in venue")
    await emit_message(f"Found {wall_plate_count} wall-plate APs in venue", "info")

    if wall_plate_count == 0:
        logger.info("  No wall-plate APs found - skipping LAN port configuration")
        return [Task(
            id="configure_lan_ports",
            name="No wall-plate APs in venue",
            status=TaskStatus.COMPLETED,
            output_data={'message': 'No wall-plate APs found', 'configured_aps': 0}
        )]

    configured_aps = 0
    failed_aps = 0
    skipped_aps = 0
    results = []

    # Process each unit that was successfully configured in Phase 4
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

            # Check if this is a wall-plate AP
            if not is_wall_plate_ap(model):
                logger.info(f"      Skipping {ap_name} ({model}) - not a wall-plate AP")
                skipped_aps += 1
                continue

            # Get the right port configs for this model type
            port_configs_for_model = get_port_configs_for_model(model_port_configs, model)

            if not port_configs_for_model:
                logger.warning(f"      No port configs for {ap_name} ({model})")
                skipped_aps += 1
                continue

            # Get ports to configure with their actions
            ports_to_configure = get_ports_to_configure(port_configs_for_model, vlan_id)

            if not ports_to_configure:
                logger.warning(f"      No ports to configure for {ap_name} ({model})")
                skipped_aps += 1
                continue

            def format_port(p):
                if p['action'] == 'disable':
                    return f"{p['port_id']}=disabled"
                return f"{p['port_id']}=VLAN{p['vlan']}"

            port_summary = ", ".join([format_port(p) for p in ports_to_configure])
            logger.info(f"      Configuring {ap_name} ({model}): {port_summary}")

            try:
                # Step 1: Disable venue settings inheritance for this AP's LAN ports
                await r1_client.venues.set_ap_lan_port_specific_settings(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    use_venue_settings=False,
                    wait_for_completion=True
                )

                # Step 2: Configure each port based on its action
                configured_ports = []
                for port_config in ports_to_configure:
                    if port_config['action'] == 'configure':
                        await r1_client.venues.set_ap_lan_port_settings(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            serial_number=serial,
                            port_id=port_config['port_id'],
                            untagged_vlan=port_config['vlan'],
                            wait_for_completion=True
                        )
                    elif port_config['action'] == 'disable':
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
                    'ports': configured_ports,
                    'status': 'success'
                })

                logger.info(f"        Configured {ap_name}: {len(configured_ports)} ports")

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
    logger.info(f"  Phase 5 complete: {configured_aps} configured, {failed_aps} failed, {skipped_aps} skipped")

    if failed_aps == 0 and configured_aps > 0:
        await emit_message(f"Configured LAN ports on {configured_aps} wall-plate APs", "success")
        task_status = TaskStatus.COMPLETED
    elif configured_aps > 0:
        await emit_message(f"LAN ports: {configured_aps} configured, {failed_aps} failed", "warning")
        task_status = TaskStatus.COMPLETED
    elif failed_aps > 0:
        await emit_message(f"LAN port configuration failed for all {failed_aps} APs", "error")
        task_status = TaskStatus.FAILED
    else:
        await emit_message("No wall-plate APs required configuration", "info")
        task_status = TaskStatus.COMPLETED

    return [Task(
        id="configure_lan_ports",
        name=f"LAN Ports: {configured_aps} configured, {failed_aps} failed, {skipped_aps} skipped",
        status=task_status,
        output_data={
            'configured_aps': configured_aps,
            'failed_aps': failed_aps,
            'skipped_aps': skipped_aps,
            'results': results
        }
    )]
