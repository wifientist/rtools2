"""
Fetch Switches Phase

Fetches all switches and switch groups across all domains.
In cached_only mode, skips API calls entirely.
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from routers.sz.zone_cache import RefreshMode

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Fetch all switches and switch groups

    Args:
        context: Workflow context containing sz_client and domains from initialize phase

    Returns:
        List with single task containing switches and switch groups
    """
    logger.info("Phase 2: Fetch Switches - Getting switches and switch groups")

    sz_client = context.get('sz_client')
    if not sz_client:
        raise ValueError("sz_client not found in context")

    update_activity = context.get('update_activity')
    refresh_mode = context.get('refresh_mode', RefreshMode.FULL)
    state_manager = context.get('state_manager')
    job_id = context.get('job_id')

    # Helper to check if job has been cancelled
    async def is_cancelled() -> bool:
        if state_manager and job_id:
            return await state_manager.is_cancelled(job_id)
        return False

    # Note: We always fetch switches even in cached_only mode since:
    # 1. Switch fetching is fast (just a few API calls)
    # 2. Zone caching doesn't include switch data
    # 3. Users expect switch counts to be accurate

    # Get domains from previous phase
    phase_results = context.get('phase_results', {})
    init_data = phase_results.get('initialize', {})
    domains_raw = init_data.get('domains_raw', [])

    if not domains_raw:
        logger.warning("No domains found, skipping switch fetch")
        task = Task(
            id="fetch_switches",
            name="Fetch Switches",
            task_type="fetch_switches",
            status=TaskStatus.COMPLETED,
            input_data={},
            output_data={
                'all_switches': [],
                'all_switch_groups': {},
                'partial_errors': ["No domains found"]
            }
        )
        return [task]

    partial_errors = []
    all_switches = []
    seen_switch_ids = set()
    all_switch_groups = {}  # domain_id -> [switch_group_details]
    seen_switch_group_ids = set()

    total_domains = len(domains_raw)
    for idx, domain in enumerate(domains_raw, 1):
        # Check for cancellation
        if await is_cancelled():
            logger.info("Fetch Switches: Cancellation detected, stopping")
            partial_errors.append("Switch fetch cancelled by user")
            break

        domain_id = domain.get("id")
        domain_name = domain.get("name", "Unknown")

        if update_activity:
            await update_activity(f"Fetching switches from domain {idx}/{total_domains}: {domain_name}")

        # Fetch switch groups
        try:
            switch_groups = await sz_client.switches.get_switch_groups_by_domain(domain_id)
            if switch_groups:
                unique_groups = []
                for sg in switch_groups:
                    sg_id = sg.get("id")
                    if sg_id and sg_id not in seen_switch_group_ids:
                        seen_switch_group_ids.add(sg_id)
                        unique_groups.append(sg)

                if unique_groups:
                    logger.info(f"Domain {domain_name}: Found {len(unique_groups)} switch groups")
                    all_switch_groups[domain_id] = unique_groups
        except Exception as e:
            if "404" not in str(e).lower():
                logger.debug(f"Domain {domain_name}: Switch groups not available: {e}")

        # Fetch switches
        try:
            switches = await sz_client.switches.get_switches_by_domain(domain_id)
            if switches:
                new_switches = []
                for s in switches:
                    switch_id = s.get("id") or s.get("switchId") or s.get("serialNumber")
                    if switch_id and switch_id not in seen_switch_ids:
                        seen_switch_ids.add(switch_id)
                        new_switches.append(s)

                if new_switches:
                    logger.info(f"Domain {domain_name}: Fetched {len(new_switches)} switches")
                    all_switches.extend(new_switches)
        except Exception as e:
            if "404" not in str(e).lower():
                partial_errors.append(f"Domain {domain_name}: Failed to fetch switches: {str(e)}")

    if all_switches:
        logger.info(f"Total switches fetched: {len(all_switches)}")
    if all_switch_groups:
        total_groups = sum(len(sgs) for sgs in all_switch_groups.values())
        logger.info(f"Total switch groups found: {total_groups}")

    if update_activity:
        summary = f"Found {len(all_switches)} switches"
        if all_switch_groups:
            total_groups = sum(len(sgs) for sgs in all_switch_groups.values())
            summary += f", {total_groups} switch groups"
        await update_activity(summary)

    task = Task(
        id="fetch_switches",
        name="Fetch Switches",
        task_type="fetch_switches",
        status=TaskStatus.COMPLETED,
        input_data={},
        output_data={
            'all_switches': all_switches,
            'all_switch_groups': all_switch_groups,
            'partial_errors': partial_errors
        }
    )

    return [task]
