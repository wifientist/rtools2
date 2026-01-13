"""
Phase 1: Create AP Groups (FIRST - before SSIDs exist)

Creates AP Groups for each unit in RuckusONE BEFORE SSIDs are created.
This avoids hitting the 15 SSID per AP Group limit, since R1 auto-activates
all existing venue SSIDs on new AP Groups.
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create AP Groups for each unit in RuckusONE

    This runs FIRST (before SSIDs) to avoid the 15 SSID limit issue.

    Args:
        context: Execution context with units from input_data

    Returns:
        Single completed task with ap_group_map for downstream phases
    """
    logger.info("Phase 1: Create AP Groups (before SSIDs)")

    # Get units directly from context (this is now Phase 1, no previous phase)
    units = context.get('units', [])

    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    ap_group_prefix = context.get('ap_group_prefix', '')
    ap_group_postfix = context.get('ap_group_postfix', '')
    r1_client = context.get('r1_client')
    event_publisher = context.get('event_publisher')
    job_id = context.get('job_id')

    # Helper to emit status messages
    async def emit_message(message: str, level: str = "info", details: dict = None):
        if event_publisher and job_id:
            await event_publisher.message(job_id, message, level, details)

    if not r1_client:
        raise Exception("R1 client not available in context")

    if not units:
        logger.warning("  No units to process")
        return [Task(
            id="create_ap_groups",
            name="No units to process",
            status=TaskStatus.COMPLETED,
            output_data={
                'ap_group_map': {},
                'units': units,
                'ap_group_results': []
            }
        )]

    logger.info(f"  Creating AP Groups for {len(units)} units...")
    await emit_message(f"Creating AP Groups for {len(units)} units...", "info")

    ap_group_map = {}  # unit_number -> ap_group_id
    ap_group_results = []

    for unit in units:
        unit_number = unit.get('unit_number')
        ap_group_name = f"{ap_group_prefix}{unit_number}{ap_group_postfix}"

        logger.info(f"    [{unit_number}] Checking/creating AP Group: {ap_group_name}")
        await emit_message(f"[{unit_number}] Checking AP Group '{ap_group_name}'...", "info")

        try:
            # Check if AP Group with EXACT name exists
            existing_group = await r1_client.venues.find_ap_group_by_name(
                tenant_id, venue_id, ap_group_name
            )

            # Verify EXACT name match (API might return partial matches)
            if existing_group and existing_group.get('name') == ap_group_name:
                logger.info(f"    [{unit_number}] AP Group '{ap_group_name}' already exists (ID: {existing_group.get('id')})")
                await emit_message(f"[{unit_number}] '{ap_group_name}' already exists", "info")
                ap_group_map[unit_number] = existing_group.get('id')
                ap_group_results.append({
                    'unit_number': unit_number,
                    'ap_group_name': ap_group_name,
                    'ap_group_id': existing_group.get('id'),
                    'status': 'existed'
                })
            else:
                # Log if we got a partial match that we're ignoring
                if existing_group:
                    logger.info(f"    [{unit_number}] Found group '{existing_group.get('name')}' but need exact '{ap_group_name}' - creating new")
                # Create AP Group
                logger.info(f"    [{unit_number}] Creating AP Group '{ap_group_name}'...")
                await emit_message(f"[{unit_number}] Creating '{ap_group_name}'...", "info")
                group_result = await r1_client.venues.create_ap_group(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    name=ap_group_name,
                    description=f"AP Group for unit {unit_number}",
                    wait_for_completion=True
                )

                ap_group_id = group_result.get('id') if group_result else None
                if ap_group_id:
                    logger.info(f"    [{unit_number}] Created AP Group '{ap_group_name}' (ID: {ap_group_id})")
                    await emit_message(f"[{unit_number}] Created '{ap_group_name}'", "success")
                    ap_group_map[unit_number] = ap_group_id
                    ap_group_results.append({
                        'unit_number': unit_number,
                        'ap_group_name': ap_group_name,
                        'ap_group_id': ap_group_id,
                        'status': 'created'
                    })
                else:
                    logger.error(f"    [{unit_number}] Failed to create AP Group '{ap_group_name}' - no ID returned")
                    await emit_message(f"[{unit_number}] Failed - no ID returned", "error")
                    ap_group_results.append({
                        'unit_number': unit_number,
                        'ap_group_name': ap_group_name,
                        'ap_group_id': None,
                        'status': 'failed',
                        'error': 'No ID returned from API'
                    })

        except Exception as e:
            logger.error(f"    [{unit_number}] AP Group creation error: {str(e)}")
            await emit_message(f"[{unit_number}] Error: {str(e)}", "error")
            ap_group_results.append({
                'unit_number': unit_number,
                'ap_group_name': ap_group_name,
                'ap_group_id': None,
                'status': 'failed',
                'error': str(e)
            })

    created_count = len([r for r in ap_group_results if r['status'] == 'created'])
    existed_count = len([r for r in ap_group_results if r['status'] == 'existed'])
    failed_count = len([r for r in ap_group_results if r['status'] == 'failed'])

    logger.info(f"  Phase 3 complete: {created_count} created, {existed_count} existed, {failed_count} failed")

    # Summary message
    if failed_count == 0:
        await emit_message(f"AP Groups ready: {created_count} created, {existed_count} existed", "success")
    else:
        await emit_message(f"AP Groups: {created_count} created, {existed_count} existed, {failed_count} failed", "warning")

    return [Task(
        id="create_ap_groups",
        name=f"Created {created_count} AP Groups ({existed_count} existed, {failed_count} failed)",
        status=TaskStatus.COMPLETED,
        output_data={
            'ap_group_map': ap_group_map,
            'units': units,
            'ap_group_results': ap_group_results
        }
    )]
