"""
Phase 2: Activate SSIDs on Venue

Activates the created SSIDs on the venue (required before AP Group activation)
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Activate SSIDs on the venue

    Args:
        context: Execution context with ssid_map from Phase 1

    Returns:
        Single completed task with activation results
    """
    logger.info("Phase 2: Activate SSIDs on Venue")

    # Get data from previous phase
    phase1_results = context.get('previous_phase_results', {}).get('create_ssids', {})
    aggregated = phase1_results.get('aggregated', {})

    ssid_map_list = aggregated.get('ssid_map', [{}])
    ssid_map = ssid_map_list[0] if ssid_map_list else {}

    units_list = aggregated.get('units', [[]])
    units = units_list[0] if units_list else []

    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')
    event_publisher = context.get('event_publisher')
    job_id = context.get('job_id')

    # Helper to emit status messages
    async def emit_message(message: str, level: str = "info", details: dict = None):
        if event_publisher and job_id:
            await event_publisher.message(job_id, message, level, details)

    if not r1_client:
        raise Exception("R1 client not available in context")

    if not ssid_map:
        logger.warning("  No SSIDs to activate")
        return [Task(
            id="activate_ssids",
            name="No SSIDs to activate",
            status=TaskStatus.COMPLETED,
            output_data={'ssid_map': ssid_map, 'units': units, 'activation_results': []}
        )]

    logger.info(f"  Activating {len(ssid_map)} SSIDs on venue...")
    await emit_message(f"Activating {len(ssid_map)} SSIDs on venue...", "info")

    activation_results = []

    for unit in units:
        unit_number = unit.get('unit_number')
        ssid_name = unit.get('ssid_name')
        ssid_id = ssid_map.get(unit_number)

        if not ssid_id:
            logger.warning(f"    [{unit_number}] Skipping - no SSID ID")
            activation_results.append({
                'unit_number': unit_number,
                'ssid_id': None,
                'status': 'skipped',
                'error': 'No SSID ID available'
            })
            continue

        try:
            logger.info(f"    [{unit_number}] Activating SSID '{ssid_name}' on venue...")
            await emit_message(f"[{unit_number}] Activating '{ssid_name}'...", "info")
            await r1_client.venues.activate_ssid_on_venue(
                tenant_id=tenant_id,
                venue_id=venue_id,
                wifi_network_id=ssid_id,
                wait_for_completion=True
            )
            logger.info(f"    [{unit_number}] SSID activated on venue")
            await emit_message(f"[{unit_number}] '{ssid_name}' activated", "success")
            activation_results.append({
                'unit_number': unit_number,
                'ssid_id': ssid_id,
                'status': 'activated'
            })

        except Exception as e:
            logger.error(f"    [{unit_number}] Failed to activate SSID on venue: {str(e)}")
            await emit_message(f"[{unit_number}] Failed to activate: {str(e)}", "error")
            activation_results.append({
                'unit_number': unit_number,
                'ssid_id': ssid_id,
                'status': 'failed',
                'error': str(e)
            })

    activated_count = len([r for r in activation_results if r['status'] == 'activated'])
    failed_count = len([r for r in activation_results if r['status'] == 'failed'])

    logger.info(f"  Phase 2 complete: {activated_count} activated, {failed_count} failed")

    # Summary message
    if failed_count == 0:
        await emit_message(f"All {activated_count} SSIDs activated successfully", "success")
    else:
        await emit_message(f"Activated {activated_count}, failed {failed_count}", "warning")

    return [Task(
        id="activate_ssids",
        name=f"Activated {activated_count} SSIDs on venue ({failed_count} failed)",
        status=TaskStatus.COMPLETED,
        output_data={
            'ssid_map': ssid_map,
            'units': units,
            'activation_results': activation_results
        }
    )]
