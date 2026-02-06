"""
Initialize Phase

Gets system info and domains list from SmartZone.
Stores results in task output_data for use by subsequent phases.

In cached_only mode, skips API calls and uses cached metadata.
"""

import logging
from typing import Dict, Any, List
from workflow.v2.models import Task, TaskStatus
from routers.sz.zone_cache import RefreshMode

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Initialize audit - get system info and domains

    Args:
        context: Workflow context containing sz_client

    Returns:
        List with single task containing system info and domains
    """
    logger.info("Phase 1: Initialize Audit - Getting system info and domains")

    sz_client = context.get('sz_client')
    if not sz_client:
        raise ValueError("sz_client not found in context")

    update_activity = context.get('update_activity')
    refresh_mode = context.get('refresh_mode', RefreshMode.FULL)
    zone_cache = context.get('zone_cache')

    partial_errors = []
    cluster_ip = None
    controller_firmware = None
    domains_raw = []
    prefetched_zones = None  # Stores zones when fallback is used (to avoid re-fetching)

    # In cached_only or switches_only mode, use cached zone data
    # switches_only will still fetch switches, but zones come from cache
    if refresh_mode in (RefreshMode.CACHED_ONLY, RefreshMode.SWITCHES_ONLY) and zone_cache:
        mode_name = "switches_only" if refresh_mode == RefreshMode.SWITCHES_ONLY else "cached_only"
        logger.info(f"Initialize: {mode_name} mode - using cached zone data")
        if update_activity:
            msg = "Refreshing switches only..." if refresh_mode == RefreshMode.SWITCHES_ONLY else "Using cached data (no API calls)"
            await update_activity(msg)

        # Get cached metadata and derive real domain IDs from cached zones
        cache_meta = await zone_cache.get_cache_meta()
        if cache_meta and cache_meta.get('zone_ids'):
            zone_ids = cache_meta['zone_ids']
            cached_zones = await zone_cache.get_cached_zones(zone_ids)

            # Derive real domain IDs from cached zones
            domain_map = {}
            for zone_data in cached_zones.values():
                domain_id = zone_data.get('domain_id')
                if domain_id and domain_id not in domain_map:
                    domain_map[domain_id] = {
                        "id": domain_id,
                        "name": zone_data.get('domain_name') or 'Unknown Domain'
                    }

            # Check if we have real domain IDs or just "_prefetched_"
            has_real_domains = domain_map and not all(
                d_id in ("_prefetched_", "_cached_") for d_id in domain_map.keys()
            )

            if has_real_domains:
                domains_raw = list(domain_map.values())
                logger.info(f"Initialize: {mode_name} mode - derived {len(domains_raw)} domains from {len(cached_zones)} cached zones")
            elif refresh_mode == RefreshMode.SWITCHES_ONLY:
                # For switches_only mode with _prefetched_ zones, we MUST fetch real domains
                # otherwise switch fetching will fail
                logger.info(f"Initialize: {mode_name} mode - cached zones have synthetic domain IDs, fetching real domains for switch data")
                if update_activity:
                    await update_activity("Fetching domains for switch data...")
                try:
                    domains_raw = await sz_client.zones.get_domains(recursively=True, include_self=True)
                    logger.info(f"Initialize: {mode_name} mode - fetched {len(domains_raw)} real domains for switch data")
                except Exception as e:
                    logger.warning(f"Initialize: {mode_name} mode - failed to fetch domains: {e}")
                    # Fall back to synthetic - switches won't work but zones will
                    domains_raw = [{"id": "_cached_", "name": "Cached Data"}]
                    partial_errors.append(f"Could not fetch domains for switch data: {str(e)}")
            else:
                # cached_only mode - use synthetic entry
                domains_raw = [{"id": "_cached_", "name": "Cached Data"}]
                logger.warning(f"Initialize: {mode_name} mode - no real domain info in cached zones")
        else:
            partial_errors.append("No cached data available")
            logger.warning(f"Initialize: {mode_name} mode but no cache metadata found")

        task = Task(
            id="initialize",
            name="Initialize Audit",
            task_type="initialize",
            status=TaskStatus.COMPLETED,
            input_data={},
            output_data={
                'cluster_ip': None,
                'controller_firmware': None,
                'domains_raw': domains_raw,
                'partial_errors': partial_errors,
                'prefetched_zones': None
            }
        )
        return [task]

    # Get system info
    if update_activity:
        await update_activity("Fetching system info...")
    try:
        audit_info = await sz_client.system.get_audit_info()
        cluster_ip = audit_info.get("cluster_ip")
        controller_firmware = audit_info.get("firmware_version")
        logger.info(f"System info: cluster_ip={cluster_ip}, firmware={controller_firmware}")
    except Exception as e:
        partial_errors.append(f"Failed to get system info: {str(e)}")
        logger.warning(f"Failed to get system info: {e}")

    # Get all domains recursively
    if update_activity:
        await update_activity("Discovering domains...")
    try:
        domains_raw = await sz_client.zones.get_domains(recursively=True, include_self=True)
        logger.info(f"Audit: Found {len(domains_raw)} domains")
        if update_activity:
            await update_activity(f"Found {len(domains_raw)} domains")
    except Exception as e:
        # Fallback: if /domains fails (likely 403 permission), try getting zones directly
        # The /rkszones endpoint without domain filter returns zones for the user's logon domain
        logger.warning(f"Failed to get domains (trying zone fallback): {e}")

        if update_activity:
            await update_activity("Domains access denied, trying zone discovery...")

        try:
            # Use pagination to get ALL zones (default page size is 100)
            zones_direct = await sz_client.zones.get_zones(paginate=True)
            if zones_direct:
                logger.info(f"Fallback: Got {len(zones_direct)} zones directly from /rkszones (with pagination)")

                # Store zones so audit_zones can use them directly without re-fetching
                prefetched_zones = zones_direct

                # Build synthetic domain entries from zones' domain info
                # Group zones by their domainId to create domain entries
                domain_map = {}
                for zone in zones_direct:
                    domain_id = zone.get("domainId")
                    if domain_id and domain_id not in domain_map:
                        domain_map[domain_id] = {
                            "id": domain_id,
                            "name": zone.get("domainName", "Current Domain"),
                        }

                if domain_map:
                    domains_raw = list(domain_map.values())
                    logger.info(f"Fallback: Created {len(domains_raw)} synthetic domain entries from zone domainIds")
                    if update_activity:
                        await update_activity(f"Found {len(zones_direct)} zones in {len(domains_raw)} domain(s)")
                else:
                    # No domain info in zones - use a special marker to tell audit_zones to use prefetched zones
                    domains_raw = [{"id": "_prefetched_", "name": "Accessible Zones"}]
                    logger.info("Fallback: Zones don't have domainId - will use prefetched zones directly")
                    if update_activity:
                        await update_activity(f"Found {len(zones_direct)} zones (using direct access)")

                partial_errors.append(f"Limited domain access - auditing {len(zones_direct)} accessible zones")
            else:
                partial_errors.append(f"Failed to get domains: {str(e)}")
                logger.error(f"Fallback also failed - no zones accessible")
        except Exception as fallback_e:
            partial_errors.append(f"Failed to get domains: {str(e)}")
            partial_errors.append(f"Zone fallback also failed: {str(fallback_e)}")
            logger.error(f"Zone fallback failed: {fallback_e}")

    # Return task with results
    task = Task(
        id="initialize",
        name="Initialize Audit",
        task_type="initialize",
        status=TaskStatus.COMPLETED,
        input_data={},
        output_data={
            'cluster_ip': cluster_ip,
            'controller_firmware': controller_firmware,
            'domains_raw': domains_raw,
            'partial_errors': partial_errors,
            'prefetched_zones': prefetched_zones  # None unless fallback was used
        }
    )

    logger.info(f"Initialize complete: {len(domains_raw)} domains found")
    return [task]
