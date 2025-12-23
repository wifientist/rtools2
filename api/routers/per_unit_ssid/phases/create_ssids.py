"""
Phase 1: Create SSIDs

Creates SSIDs for each unit in RuckusONE
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create SSIDs for each unit in RuckusONE

    Args:
        context: Execution context with units data

    Returns:
        Single completed task with ssid_map for downstream phases
    """
    logger.info("Phase 1: Create SSIDs")

    # Get units from input_data
    units = context.get('units', [])
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
    if not tenant_id:
        raise Exception("tenant_id not available in context")
    if not venue_id:
        raise Exception("venue_id not available in context")

    if not units:
        logger.warning("  No units to process")
        return [Task(
            id="create_ssids",
            name="No units to process",
            status=TaskStatus.COMPLETED,
            output_data={'ssid_map': {}, 'units': []}
        )]

    logger.info(f"  Creating SSIDs for {len(units)} units...")
    await emit_message(f"Creating SSIDs for {len(units)} units...", "info")

    ssid_map = {}  # unit_number -> ssid_id
    ssid_results = []

    for unit in units:
        unit_number = unit.get('unit_number')
        ssid_name = unit.get('ssid_name')
        ssid_password = unit.get('ssid_password')
        security_type = unit.get('security_type', 'WPA3')
        default_vlan = unit.get('default_vlan', '1')

        logger.info(f"    [{unit_number}] Checking/creating SSID: {ssid_name}")
        await emit_message(f"[{unit_number}] Checking SSID '{ssid_name}'...", "info")

        try:
            # Check if SSID exists
            existing_ssid = await r1_client.networks.find_wifi_network_by_name(
                tenant_id, venue_id, ssid_name
            )

            if existing_ssid:
                logger.info(f"    [{unit_number}] SSID '{ssid_name}' already exists (ID: {existing_ssid.get('id')})")
                await emit_message(f"[{unit_number}] '{ssid_name}' already exists", "info")
                ssid_map[unit_number] = existing_ssid.get('id')
                ssid_results.append({
                    'unit_number': unit_number,
                    'ssid_name': ssid_name,
                    'ssid_id': existing_ssid.get('id'),
                    'status': 'existed'
                })
            else:
                # Create SSID
                logger.info(f"    [{unit_number}] Creating SSID '{ssid_name}'...")
                await emit_message(f"[{unit_number}] Creating '{ssid_name}'...", "info")
                ssid_result = await r1_client.networks.create_wifi_network(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    name=ssid_name,
                    ssid=ssid_name,
                    passphrase=ssid_password,
                    security_type=security_type,
                    vlan_id=int(default_vlan),
                    description=f"Per-unit SSID for unit {unit_number}",
                    wait_for_completion=True
                )

                ssid_id = ssid_result.get('id') if ssid_result else None
                if ssid_id:
                    logger.info(f"    [{unit_number}] Created SSID '{ssid_name}' (ID: {ssid_id})")
                    await emit_message(f"[{unit_number}] Created '{ssid_name}'", "success")
                    ssid_map[unit_number] = ssid_id
                    ssid_results.append({
                        'unit_number': unit_number,
                        'ssid_name': ssid_name,
                        'ssid_id': ssid_id,
                        'status': 'created'
                    })
                else:
                    logger.error(f"    [{unit_number}] Failed to create SSID '{ssid_name}' - no ID returned")
                    await emit_message(f"[{unit_number}] Failed - no ID returned", "error")
                    ssid_results.append({
                        'unit_number': unit_number,
                        'ssid_name': ssid_name,
                        'ssid_id': None,
                        'status': 'failed',
                        'error': 'No ID returned from API'
                    })

        except Exception as e:
            logger.error(f"    [{unit_number}] SSID creation error: {str(e)}")
            await emit_message(f"[{unit_number}] Error: {str(e)}", "error")
            ssid_results.append({
                'unit_number': unit_number,
                'ssid_name': ssid_name,
                'ssid_id': None,
                'status': 'failed',
                'error': str(e)
            })

    created_count = len([r for r in ssid_results if r['status'] == 'created'])
    existed_count = len([r for r in ssid_results if r['status'] == 'existed'])
    failed_count = len([r for r in ssid_results if r['status'] == 'failed'])

    logger.info(f"  Phase 1 complete: {created_count} created, {existed_count} existed, {failed_count} failed")

    # Summary message
    if failed_count == 0:
        await emit_message(f"SSIDs ready: {created_count} created, {existed_count} existed", "success")
    else:
        await emit_message(f"SSIDs: {created_count} created, {existed_count} existed, {failed_count} failed", "warning")

    return [Task(
        id="create_ssids",
        name=f"Created {created_count} SSIDs ({existed_count} existed, {failed_count} failed)",
        status=TaskStatus.COMPLETED,
        output_data={
            'ssid_map': ssid_map,
            'ssid_results': ssid_results,
            'units': units  # Forward units to next phase
        }
    )]
