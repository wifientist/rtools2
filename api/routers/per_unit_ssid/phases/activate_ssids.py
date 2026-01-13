"""
Phase 3: Activate SSIDs on Venue

Activates the created SSIDs on the venue (required before AP Group activation)
"""

import asyncio
import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Activate SSIDs on the venue

    Args:
        context: Execution context with ssid_map and ap_group_map from Phase 2

    Returns:
        Single completed task with activation results and ap_group_map for Phase 4
    """
    logger.info("Phase 3: Activate SSIDs on Venue")

    # Get data from previous phase (create_ssids)
    phase2_results = context.get('previous_phase_results', {}).get('create_ssids', {})
    aggregated = phase2_results.get('aggregated', {})

    ssid_map_list = aggregated.get('ssid_map', [{}])
    ssid_map = ssid_map_list[0] if ssid_map_list else {}

    ap_group_map_list = aggregated.get('ap_group_map', [{}])
    ap_group_map = ap_group_map_list[0] if ap_group_map_list else {}

    units_list = aggregated.get('units', [[]])
    units = units_list[0] if units_list else []

    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')
    event_publisher = context.get('event_publisher')
    job_id = context.get('job_id')
    activation_semaphore = context.get('activation_semaphore')  # For throttling in parallel mode

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
            output_data={'ssid_map': ssid_map, 'ap_group_map': ap_group_map, 'units': units, 'activation_results': []}
        )]

    logger.info(f"  Activating {len(ssid_map)} SSIDs on venue {venue_id}...")
    logger.info(f"  ssid_map: {ssid_map}")
    logger.info(f"  activation_semaphore available: {activation_semaphore is not None}")
    await emit_message(f"Activating {len(ssid_map)} SSIDs on venue...", "info")

    # NOTE: We no longer rely on venueApGroups to check activation status because
    # having a venueApGroups entry doesn't always mean R1 considers the SSID "activated".
    # Instead, we always try to activate and handle any errors gracefully.

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
            # Use semaphore if available (parallel mode) to throttle activations
            # This prevents hitting the 15 SSID limit during the "in-flight" window
            if activation_semaphore:
                logger.info(f"    [{unit_number}] Waiting for activation slot...")
                await emit_message(f"[{unit_number}] Waiting for activation slot...", "info")

            async def do_activation():
                logger.info(f"    [{unit_number}] Activating SSID '{ssid_name}' (id={ssid_id}) on venue {venue_id}...")
                await emit_message(f"[{unit_number}] Activating '{ssid_name}'...", "info")
                await r1_client.venues.activate_ssid_on_venue(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    wifi_network_id=ssid_id,
                    wait_for_completion=True
                )
                logger.info(f"    [{unit_number}] SSID '{ssid_name}' (id={ssid_id}) activated on venue")
                await emit_message(f"[{unit_number}] '{ssid_name}' activated", "success")

            if activation_semaphore:
                logger.info(f"    [{unit_number}] Acquiring semaphore...")
                async with activation_semaphore:
                    logger.info(f"    [{unit_number}] Semaphore acquired, starting activation")
                    await do_activation()
                    logger.info(f"    [{unit_number}] Releasing semaphore")
            else:
                await do_activation()

            activation_results.append({
                'unit_number': unit_number,
                'ssid_id': ssid_id,
                'status': 'activated'
            })

        except Exception as e:
            error_str = str(e).lower()
            # Check for "already activated" type errors - treat as success
            if 'already activated' in error_str or 'already exists' in error_str:
                logger.info(f"    [{unit_number}] SSID '{ssid_name}' already activated on venue")
                await emit_message(f"[{unit_number}] '{ssid_name}' already activated", "info")
                activation_results.append({
                    'unit_number': unit_number,
                    'ssid_id': ssid_id,
                    'status': 'already_activated'
                })
            else:
                logger.error(f"    [{unit_number}] Failed to activate SSID on venue: {str(e)}")
                await emit_message(f"[{unit_number}] Failed to activate: {str(e)}", "error")
                activation_results.append({
                    'unit_number': unit_number,
                    'ssid_id': ssid_id,
                    'status': 'failed',
                    'error': str(e)
                })

    activated_count = len([r for r in activation_results if r['status'] == 'activated'])
    already_activated_count = len([r for r in activation_results if r['status'] == 'already_activated'])
    failed_count = len([r for r in activation_results if r['status'] == 'failed'])

    # Build summary
    summary_parts = []
    if activated_count > 0:
        summary_parts.append(f"{activated_count} activated")
    if already_activated_count > 0:
        summary_parts.append(f"{already_activated_count} already active")
    if failed_count > 0:
        summary_parts.append(f"{failed_count} failed")
    summary_str = ", ".join(summary_parts) if summary_parts else "no changes"

    logger.info(f"  Phase 3 complete: {summary_str}")

    # Summary message
    if failed_count == 0:
        await emit_message(f"SSIDs on venue: {summary_str}", "success")
    else:
        await emit_message(f"SSIDs on venue: {summary_str}", "warning")

    return [Task(
        id="activate_ssids",
        name=f"SSIDs on venue: {summary_str}",
        status=TaskStatus.COMPLETED,
        output_data={
            'ssid_map': ssid_map,
            'ap_group_map': ap_group_map,  # Pass through for Phase 4
            'units': units,
            'activation_results': activation_results
        }
    )]
