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

    # Get options
    options = context.get('options', {})
    input_data = context.get('input_data', {})
    name_conflict_resolution = options.get('name_conflict_resolution') or input_data.get('name_conflict_resolution', 'keep')

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
        ssid_name = unit.get('ssid_name')  # Broadcast name (what clients see)
        network_name = unit.get('network_name') or ssid_name  # Internal R1 name (defaults to ssid_name)
        ssid_password = unit.get('ssid_password')
        security_type = unit.get('security_type', 'WPA3')
        default_vlan = unit.get('default_vlan', '1')

        logger.info(f"    [{unit_number}] Checking/creating SSID: {ssid_name} (network: {network_name})")
        await emit_message(f"[{unit_number}] Checking SSID '{ssid_name}'...", "info")

        try:
            # SSID (broadcast name) is the source of truth - check it first
            existing_by_ssid = await r1_client.networks.find_wifi_network_by_ssid(
                tenant_id, venue_id, ssid_name
            )

            if existing_by_ssid:
                # SSID exists - check if network name matches
                existing_network_id = existing_by_ssid.get('id')
                existing_network_name = existing_by_ssid.get('name', 'unknown')

                if existing_network_name == network_name:
                    # Perfect match - reuse as-is
                    logger.info(f"    [{unit_number}] SSID '{ssid_name}' exists with matching name '{network_name}' (ID: {existing_network_id})")
                    await emit_message(f"[{unit_number}] '{ssid_name}' already exists", "info")
                    ssid_map[unit_number] = existing_network_id
                    ssid_results.append({
                        'unit_number': unit_number,
                        'ssid_name': ssid_name,
                        'network_name': network_name,
                        'ssid_id': existing_network_id,
                        'status': 'existed'
                    })
                else:
                    # SSID exists but network name differs
                    if name_conflict_resolution == 'keep':
                        # Keep existing R1 name, just reuse the network
                        logger.info(f"    [{unit_number}] SSID '{ssid_name}' exists as '{existing_network_name}' (keeping R1 name)")
                        await emit_message(f"[{unit_number}] '{ssid_name}' exists (R1 name: '{existing_network_name}')", "info")
                        ssid_map[unit_number] = existing_network_id
                        ssid_results.append({
                            'unit_number': unit_number,
                            'ssid_name': ssid_name,
                            'network_name': existing_network_name,  # Use R1's name
                            'requested_network_name': network_name,  # What ruckus.tools wanted
                            'ssid_id': existing_network_id,
                            'status': 'existed',
                            'name_kept': True
                        })
                    else:
                        # Overwrite - update the network name to match ruckus.tools
                        logger.info(f"    [{unit_number}] SSID '{ssid_name}' exists as '{existing_network_name}' - updating to '{network_name}'")
                        await emit_message(f"[{unit_number}] Renaming '{existing_network_name}' -> '{network_name}'", "info")

                        await r1_client.networks.update_wifi_network_name(
                            tenant_id=tenant_id,
                            network_id=existing_network_id,
                            new_name=network_name,
                            wait_for_completion=True
                        )

                        logger.info(f"    [{unit_number}] Renamed network to '{network_name}'")
                        await emit_message(f"[{unit_number}] Renamed to '{network_name}'", "success")
                        ssid_map[unit_number] = existing_network_id
                        ssid_results.append({
                            'unit_number': unit_number,
                            'ssid_name': ssid_name,
                            'network_name': network_name,
                            'previous_network_name': existing_network_name,
                            'ssid_id': existing_network_id,
                            'status': 'renamed'
                        })
                continue

            # SSID doesn't exist - check if the network name is already taken
            existing_by_name = await r1_client.networks.find_wifi_network_by_name(
                tenant_id, venue_id, network_name
            )

            if existing_by_name:
                # Name is taken by a different SSID - error
                existing_ssid = existing_by_name.get('ssid', 'unknown')
                error_msg = f"Network name '{network_name}' already in use by SSID '{existing_ssid}'"
                logger.error(f"    [{unit_number}] {error_msg}")
                await emit_message(f"[{unit_number}] {error_msg}", "error")
                ssid_results.append({
                    'unit_number': unit_number,
                    'ssid_name': ssid_name,
                    'network_name': network_name,
                    'ssid_id': None,
                    'status': 'failed',
                    'error': error_msg
                })
                continue

            # Both name and SSID are available - create the network
            logger.info(f"    [{unit_number}] Creating network '{network_name}' with SSID '{ssid_name}'...")
            await emit_message(f"[{unit_number}] Creating '{network_name}'...", "info")
            ssid_result = await r1_client.networks.create_wifi_network(
                tenant_id=tenant_id,
                venue_id=venue_id,
                name=network_name,  # Internal R1 name
                ssid=ssid_name,     # Broadcast SSID (what clients see)
                passphrase=ssid_password,
                security_type=security_type,
                vlan_id=int(default_vlan),
                description=f"Per-unit SSID for unit {unit_number}",
                wait_for_completion=True
            )

            ssid_id = ssid_result.get('id') if ssid_result else None
            if ssid_id:
                logger.info(f"    [{unit_number}] Created network '{network_name}' (ID: {ssid_id})")
                await emit_message(f"[{unit_number}] Created '{network_name}'", "success")
                ssid_map[unit_number] = ssid_id
                ssid_results.append({
                    'unit_number': unit_number,
                    'ssid_name': ssid_name,
                    'network_name': network_name,
                    'ssid_id': ssid_id,
                    'status': 'created'
                })
            else:
                logger.error(f"    [{unit_number}] Failed to create network '{network_name}' - no ID returned")
                await emit_message(f"[{unit_number}] Failed - no ID returned", "error")
                ssid_results.append({
                    'unit_number': unit_number,
                    'ssid_name': ssid_name,
                    'network_name': network_name,
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
                'network_name': network_name,
                'ssid_id': None,
                'status': 'failed',
                'error': str(e)
            })

    created_count = len([r for r in ssid_results if r['status'] == 'created'])
    existed_count = len([r for r in ssid_results if r['status'] == 'existed'])
    renamed_count = len([r for r in ssid_results if r['status'] == 'renamed'])
    failed_count = len([r for r in ssid_results if r['status'] == 'failed'])

    # Build summary parts
    summary_parts = []
    if created_count > 0:
        summary_parts.append(f"{created_count} created")
    if existed_count > 0:
        summary_parts.append(f"{existed_count} existed")
    if renamed_count > 0:
        summary_parts.append(f"{renamed_count} renamed")
    if failed_count > 0:
        summary_parts.append(f"{failed_count} failed")

    summary_str = ", ".join(summary_parts) if summary_parts else "no changes"
    logger.info(f"  Phase 1 complete: {summary_str}")

    # Summary message
    if failed_count == 0:
        await emit_message(f"SSIDs ready: {summary_str}", "success")
    else:
        await emit_message(f"SSIDs: {summary_str}", "warning")

    return [Task(
        id="create_ssids",
        name=f"SSIDs: {summary_str}",
        status=TaskStatus.COMPLETED,
        output_data={
            'ssid_map': ssid_map,
            'ssid_results': ssid_results,
            'units': units  # Forward units to next phase
        }
    )]
