"""
Phase 4: Process Units

Final phase that:
1. Finds APs by identifiers (serial or name)
2. Assigns APs to their unit's AP Group
3. Activates SSID on AP Group with radio types and VLAN settings
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Process units: find APs, assign to groups, activate SSIDs

    Args:
        context: Execution context with ssid_map, ap_group_map, and units from previous phases

    Returns:
        Single completed task with unit processing results
    """
    logger.info("Phase 4: Process Units")

    # Get data from previous phase
    phase3_results = context.get('previous_phase_results', {}).get('create_ap_groups', {})
    aggregated = phase3_results.get('aggregated', {})

    ssid_map_list = aggregated.get('ssid_map', [{}])
    ssid_map = ssid_map_list[0] if ssid_map_list else {}

    ap_group_map_list = aggregated.get('ap_group_map', [{}])
    ap_group_map = ap_group_map_list[0] if ap_group_map_list else {}

    units_list = aggregated.get('units', [[]])
    units = units_list[0] if units_list else []

    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    ap_group_prefix = context.get('ap_group_prefix', 'APGroup-')
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
            id="process_units",
            name="No units to process",
            status=TaskStatus.COMPLETED,
            output_data={'unit_results': [], 'summary': {'successful': 0, 'failed': 0}}
        )]

    logger.info(f"  Processing {len(units)} units...")
    await emit_message(f"Processing {len(units)} units", "info")

    # Fetch all APs in venue once (optimization)
    all_aps = []
    try:
        logger.info(f"    Fetching APs in venue {venue_id}...")
        await emit_message("Fetching APs in venue...", "info")
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
        logger.info(f"    Found {len(all_aps)} total APs in venue")
        await emit_message(f"Found {len(all_aps)} APs in venue", "info")
    except Exception as e:
        logger.error(f"    Failed to fetch APs: {str(e)}")
        await emit_message(f"Failed to fetch APs: {str(e)}", "warning")

    unit_results = []
    successful_units = 0
    failed_units = 0

    for unit in units:
        unit_number = unit.get('unit_number')
        ssid_name = unit.get('ssid_name')
        ssid_id = ssid_map.get(unit_number)
        ap_group_id = ap_group_map.get(unit_number)
        ap_group_name = f"{ap_group_prefix}{unit_number}"
        ap_identifiers = unit.get('ap_identifiers', [])
        default_vlan = unit.get('default_vlan', '1')

        logger.info(f"    [{unit_number}] Processing unit...")
        await emit_message(f"[{unit_number}] Processing unit '{ssid_name}'", "info")

        result = {
            'unit_number': unit_number,
            'ssid_id': ssid_id,
            'ap_group_id': ap_group_id,
            'status': 'pending',
            'details': {}
        }

        try:
            # Validate we have the required IDs
            if not ssid_id:
                raise Exception(f"SSID ID is missing for unit {unit_number}")
            if not ap_group_id:
                raise Exception(f"AP Group ID is missing for unit {unit_number}")

            # Step 1: Find APs (if any were specified)
            matched_aps = []
            if ap_identifiers and len(ap_identifiers) > 0:
                for ap_identifier in ap_identifiers:
                    for ap in all_aps:
                        if (ap.get('serialNumber') == ap_identifier or
                            ap.get('name') == ap_identifier or
                            ap_identifier in ap.get('name', '')):  # Partial name match
                            matched_aps.append(ap)
                            logger.info(f"      Matched AP: {ap.get('name')} ({ap.get('serialNumber')})")
                            break

                if len(matched_aps) != len(ap_identifiers):
                    logger.warning(f"      Only matched {len(matched_aps)}/{len(ap_identifiers)} APs")
            else:
                logger.info(f"      No APs specified - skipping AP assignment")

            result['details']['matched_aps'] = len(matched_aps)
            result['details']['ap_names'] = [ap.get('name') for ap in matched_aps]

            # Step 2: Assign APs to AP Group (if any APs were found)
            if len(matched_aps) > 0:
                logger.info(f"      Assigning {len(matched_aps)} APs to group {ap_group_name}")
                assigned_count = 0
                for ap in matched_aps:
                    try:
                        await r1_client.venues.assign_ap_to_group(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            ap_group_id=ap_group_id,
                            ap_serial_number=ap.get('serialNumber'),
                            wait_for_completion=True
                        )
                        assigned_count += 1
                        logger.info(f"        Assigned {ap.get('serialNumber')} ({assigned_count}/{len(matched_aps)})")
                    except Exception as e:
                        logger.warning(f"        Failed to assign AP {ap.get('serialNumber')}: {str(e)}")

                result['details']['aps_assigned'] = assigned_count
            else:
                result['details']['aps_assigned'] = 0

            # Step 3: Configure SSID for specific AP Group (3-step process matching R1 frontend)
            # 3a: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings - set isAllApGroups=false
            # 3b: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId} - activate AP Group
            # 3c: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings - configure settings
            logger.info(f"      Step 3: Configuring SSID '{ssid_name}' for AP Group '{ap_group_name}' (3-step process)...")
            try:
                await r1_client.venues.configure_ssid_for_specific_ap_group(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    wifi_network_id=ssid_id,
                    ap_group_id=ap_group_id,
                    radio_types=["2.4-GHz", "5-GHz", "6-GHz"],
                    vlan_id=int(default_vlan) if default_vlan else None,
                    wait_for_completion=True
                )
                result['details']['ssid_activated_on_group'] = True
                logger.info(f"      Step 3 complete: SSID configured for specific AP Group")
            except Exception as e:
                logger.error(f"      Failed to configure SSID for AP Group: {str(e)}")
                raise Exception(f"Configuring SSID for AP Group failed: {str(e)}")

            # Success
            if len(matched_aps) > 0:
                result['message'] = f"Configured {len(matched_aps)} APs with SSID '{ssid_name}' (VLAN {default_vlan})"
            else:
                result['message'] = f"Activated SSID '{ssid_name}' (VLAN {default_vlan}) on AP Group '{ap_group_name}'"
            result['status'] = 'success'
            successful_units += 1
            await emit_message(f"[{unit_number}] {result['message']}", "success")

        except Exception as e:
            logger.error(f"      Error: {str(e)}")
            result['status'] = 'error'
            result['message'] = str(e)
            failed_units += 1
            await emit_message(f"[{unit_number}] Failed: {str(e)}", "error")

        unit_results.append(result)

    logger.info(f"  Phase 4 complete: {successful_units} successful, {failed_units} failed")

    # Emit summary message
    if failed_units == 0:
        await emit_message(f"All {successful_units} units configured successfully", "success")
    elif successful_units > 0:
        await emit_message(f"Completed with {successful_units} successful, {failed_units} failed", "warning")
    else:
        await emit_message(f"All {failed_units} units failed", "error")

    # Determine overall status
    if failed_units == 0:
        task_status = TaskStatus.COMPLETED
    elif successful_units > 0:
        task_status = TaskStatus.COMPLETED  # Partial success still marked as completed
    else:
        task_status = TaskStatus.FAILED

    return [Task(
        id="process_units",
        name=f"Processed {len(units)} units: {successful_units} successful, {failed_units} failed",
        status=task_status,
        output_data={
            'unit_results': unit_results,
            'summary': {
                'total_units': len(units),
                'successful': successful_units,
                'failed': failed_units
            }
        }
    )]
