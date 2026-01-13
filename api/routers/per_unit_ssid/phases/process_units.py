"""
Phase 4: Process Units

Final phase that:
1. Finds APs by identifiers (serial or name)
2. Assigns APs to their unit's AP Group
3. Activates SSID on AP Group with radio types and VLAN settings
"""

import asyncio
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

    # Get data from previous phase (activate_ssids - Phase 3)
    phase3_results = context.get('previous_phase_results', {}).get('activate_ssids', {})
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

    # Fetch all WiFi networks to check SSID activation status on AP groups
    all_networks = []
    try:
        networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = networks_response if isinstance(networks_response, list) else networks_response.get('data', [])
    except Exception as e:
        logger.warning(f"    Could not fetch networks for idempotency check: {str(e)}")

    # Build lookup of SSID ID -> venueApGroups for activation status
    network_activation_lookup = {n.get('id'): n.get('venueApGroups', []) for n in all_networks}

    def is_ssid_activated_on_specific_ap_group(ssid_id: str, target_venue_id: str, target_ap_group_id: str) -> tuple:
        """
        Check if SSID is already activated on the specific AP group (not "All AP Groups").

        Returns:
            (is_specific, is_all_ap_groups) tuple:
            - (True, False) = SSID is specifically assigned to this AP Group - skip
            - (False, True) = SSID is set to "All AP Groups" - need to narrow it
            - (False, False) = SSID is not activated on this AP Group at all - need to add it
        """
        venue_ap_groups = network_activation_lookup.get(ssid_id, [])
        for vag in venue_ap_groups:
            if vag.get('venueId') != target_venue_id:
                continue

            # Check if it's set to "All AP Groups"
            if vag.get('isAllApGroups', False):
                return (False, True)  # Need to narrow from "All" to specific

            # Check if AP group is specifically in the activation list
            ap_group_ids = vag.get('apGroupIds', [])
            if target_ap_group_id in ap_group_ids:
                return (True, False)  # Already specifically assigned

        return (False, False)  # Not activated at all

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
                skipped_count = 0
                for ap in matched_aps:
                    current_group_id = ap.get('apGroupId')
                    ap_serial = ap.get('serialNumber')
                    ap_name = ap.get('name', ap_serial)

                    # Idempotency check: skip if already in correct group
                    if current_group_id == ap_group_id:
                        skipped_count += 1
                        logger.info(f"        ✓ {ap_name} already in group {ap_group_name} - skipping")
                        continue

                    try:
                        await r1_client.venues.assign_ap_to_group(
                            tenant_id=tenant_id,
                            venue_id=venue_id,
                            ap_group_id=ap_group_id,
                            ap_serial_number=ap_serial,
                            wait_for_completion=True
                        )
                        assigned_count += 1
                        logger.info(f"        Assigned {ap_name} ({assigned_count}/{len(matched_aps) - skipped_count})")
                    except Exception as e:
                        logger.warning(f"        Failed to assign AP {ap_serial}: {str(e)}")

                result['details']['aps_assigned'] = assigned_count
                result['details']['aps_already_in_group'] = skipped_count
            else:
                result['details']['aps_assigned'] = 0
                result['details']['aps_already_in_group'] = 0

            # Step 3: Configure SSID for specific AP Group (3-step process matching R1 frontend)
            # 3a: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/settings - set isAllApGroups=false
            # 3b: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId} - activate AP Group
            # 3c: PUT /venues/{venueId}/wifiNetworks/{wifiNetworkId}/apGroups/{apGroupId}/settings - configure settings

            # Idempotency check: is SSID already specifically assigned to this AP group?
            is_specific, is_all_ap_groups = is_ssid_activated_on_specific_ap_group(ssid_id, venue_id, ap_group_id)

            if is_specific:
                # Already specifically assigned to this AP Group - nothing to do
                logger.info(f"      ✓ SSID '{ssid_name}' already specifically assigned to AP Group '{ap_group_name}' - skipping")
                result['details']['ssid_activated_on_group'] = True
                result['details']['ssid_already_activated'] = True
            else:
                # Either "All AP Groups" or not activated - need to configure
                if is_all_ap_groups:
                    logger.info(f"      [{unit_number}] SSID '{ssid_name}' is set to 'All AP Groups' - narrowing to specific AP Group")
                    await emit_message(f"[{unit_number}] Narrowing SSID from 'All AP Groups' to specific...", "info")
                else:
                    logger.info(f"      [{unit_number}] SSID '{ssid_name}' not yet activated on AP Group - adding")
                    await emit_message(f"[{unit_number}] Configuring SSID for AP Group...", "info")

                logger.info(f"      [{unit_number}] Starting 3-step SSID->AP Group config:")
                logger.info(f"        ssid_id={ssid_id}")
                logger.info(f"        ap_group_id={ap_group_id}")
                logger.info(f"        vlan={default_vlan}")
                try:
                    # Get debug_delay from options if set (for debugging slow parallel issues)
                    debug_delay = context.get('options', {}).get('debug_delay', 0)

                    await r1_client.venues.configure_ssid_for_specific_ap_group(
                        tenant_id=tenant_id,
                        venue_id=venue_id,
                        wifi_network_id=ssid_id,
                        ap_group_id=ap_group_id,
                        radio_types=["2.4-GHz", "5-GHz", "6-GHz"],
                        vlan_id=int(default_vlan) if default_vlan else None,
                        wait_for_completion=True,
                        debug_delay=debug_delay
                    )
                    result['details']['ssid_activated_on_group'] = True
                    result['details']['ssid_already_activated'] = False
                    result['details']['was_all_ap_groups'] = is_all_ap_groups
                    logger.info(f"      [{unit_number}] 3-step process complete!")
                    await emit_message(f"[{unit_number}] SSID configured for AP Group", "success")
                except Exception as e:
                    logger.error(f"      [{unit_number}] 3-step process FAILED: {str(e)}")
                    raise Exception(f"Configuring SSID for AP Group failed: {str(e)}")

            # Success
            if len(matched_aps) > 0:
                skipped_info = f", {result['details'].get('aps_already_in_group', 0)} already in group" if result['details'].get('aps_already_in_group', 0) > 0 else ""
                result['message'] = f"Configured {len(matched_aps)} APs with SSID '{ssid_name}' (VLAN {default_vlan}){skipped_info}"
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
