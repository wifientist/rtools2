"""
Audit Zones Phase

Audits each zone to collect AP, WLAN, and group information.
Supports zone-level caching for incremental audits.
"""

import asyncio
import logging
from collections import Counter
from typing import Dict, Any, List, Optional

from workflow.models import Task, TaskStatus
from szapi.services.wlans import WlanService
from schemas.sz_audit import (
    ZoneAudit,
    ApStatusBreakdown,
    ApGroupSummary,
    WlanGroupSummary,
    WlanSummary,
    FirmwareDistribution,
    ModelDistribution,
)
from routers.sz.zone_cache import RefreshMode

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Audit all zones - collect APs, WLANs, groups

    Supports caching modes:
    - full: Refresh all zones, update cache
    - incremental: Use cached zones if fresh, only audit new/stale zones
    - cached_only: Return only cached data, no API calls

    Args:
        context: Workflow context containing sz_client and domains

    Returns:
        List with single task containing all zone audits
    """
    logger.info("Phase 3: Audit Zones - Collecting AP and WLAN data")

    sz_client = context.get('sz_client')
    if not sz_client:
        raise ValueError("sz_client not found in context")

    update_activity = context.get('update_activity')
    zone_cache = context.get('zone_cache')  # ZoneCacheManager
    refresh_mode = context.get('refresh_mode', RefreshMode.FULL)
    force_refresh_zones = context.get('force_refresh_zones', set())  # Zone IDs to always refresh
    job = context.get('job')
    state_manager = context.get('state_manager')
    job_id = context.get('job_id')

    # Helper to check if job has been cancelled
    async def is_cancelled() -> bool:
        if state_manager and job_id:
            return await state_manager.is_cancelled(job_id)
        return False

    # Get domains and prefetched zones from initialize phase
    phase_results = context.get('phase_results', {})
    init_data = phase_results.get('initialize', {})
    domains_raw = init_data.get('domains_raw', [])
    prefetched_zones = init_data.get('prefetched_zones')  # Set when fallback was used

    if not domains_raw:
        logger.warning("No domains found, skipping zone audit")
        task = Task(
            id="audit_zones",
            name="Audit Zones",
            task_type="audit_zones",
            status=TaskStatus.COMPLETED,
            input_data={},
            output_data={
                'all_zones_audit': [],
                'partial_errors': ["No domains found"]
            }
        )
        return [task]

    partial_errors = []
    all_zones_audit = []
    seen_zone_ids = set()
    total_zones_processed = 0

    # Cache statistics
    zones_from_cache = 0
    zones_refreshed = 0

    # Zone progress tracking
    total_zones_expected = 0  # Set when we know how many zones to process

    # Helper to update zone progress in job summary (for progress bar)
    async def update_zone_progress(completed: int, total: int):
        if job and state_manager:
            job.summary['zone_progress'] = {
                'completed': completed,
                'total': total
            }
            await state_manager.save_job(job)

    # Helper to update cache stats in job summary
    async def update_cache_stats():
        if job and state_manager:
            total = zones_from_cache + zones_refreshed
            hit_rate = (zones_from_cache / total * 100) if total > 0 else 0
            job.summary['cache_stats'] = {
                'refresh_mode': refresh_mode.value if hasattr(refresh_mode, 'value') else str(refresh_mode),
                'zones_from_cache': zones_from_cache,
                'zones_refreshed': zones_refreshed,
                'cache_hit_rate': round(hit_rate, 1)
            }
            await state_manager.save_job(job)

    # Handle cached_only and switches_only modes - use cached zone data
    # For switches_only, we clear matched_switch_groups so they can be re-matched with fresh switch data
    if refresh_mode in (RefreshMode.CACHED_ONLY, RefreshMode.SWITCHES_ONLY) and zone_cache:
        mode_name = "switches_only" if refresh_mode == RefreshMode.SWITCHES_ONLY else "cached_only"
        logger.info(f"Audit: {mode_name} mode - returning cached zones")
        if update_activity:
            await update_activity("Loading zones from cache...")

        cache_meta = await zone_cache.get_cache_meta()
        if cache_meta and cache_meta.get('zone_ids'):
            cached_zones = await zone_cache.get_cached_zones(cache_meta['zone_ids'])

            for zone_id, zone_data in cached_zones.items():
                # Remove cache metadata before returning
                zone_data.pop('_cached_at', None)
                # For switches_only mode, clear cached switch matches so they're re-computed
                if refresh_mode == RefreshMode.SWITCHES_ONLY:
                    zone_data.pop('matched_switch_groups', None)
                all_zones_audit.append(zone_data)
                zones_from_cache += 1

            logger.info(f"Audit: Loaded {len(all_zones_audit)} zones from cache")
            if update_activity:
                await update_activity(f"Loaded {len(all_zones_audit)} zones from cache")
        else:
            partial_errors.append("No cached data available - run a full audit first")
            logger.warning(f"{mode_name} mode requested but no cache data available")

        await update_cache_stats()

        task = Task(
            id="audit_zones",
            name="Audit Zones",
            task_type="audit_zones",
            status=TaskStatus.COMPLETED,
            input_data={},
            output_data={
                'all_zones_audit': all_zones_audit,
                'partial_errors': partial_errors
            }
        )
        return [task]

    # For incremental mode, pre-fetch all cached zones to check freshness
    cached_zone_data = {}
    if refresh_mode == RefreshMode.INCREMENTAL and zone_cache:
        logger.info("Audit: incremental mode - checking for cached zones")
        if update_activity:
            await update_activity("Checking zone cache...")

        cache_meta = await zone_cache.get_cache_meta()
        if cache_meta and cache_meta.get('zone_ids'):
            cached_zone_data = await zone_cache.get_cached_zones(cache_meta['zone_ids'])
            logger.info(f"Audit: Found {len(cached_zone_data)} zones in cache")

    # Track if audit was cancelled (for cache metadata update)
    was_cancelled = False

    # Check if we have prefetched zones (from fallback in initialize phase)
    if prefetched_zones:
        total_zones_expected = len(prefetched_zones)
        logger.info(f"Audit: Using {total_zones_expected} prefetched zones (fallback mode)")
        await update_zone_progress(0, total_zones_expected)
        if update_activity:
            await update_activity(f"Auditing {total_zones_expected} zones...")

        for zone in prefetched_zones:
            # Check for cancellation
            if await is_cancelled():
                logger.info("Audit: Cancellation detected, stopping zone processing")
                partial_errors.append("Audit cancelled by user")
                was_cancelled = True
                break

            zone_id = zone.get("id")
            zone_name = zone.get("name", "")

            # Skip system zones
            if zone_name.lower() == "staging zone":
                logger.debug(f"Skipping system zone: {zone_name}")
                continue

            # Skip duplicates
            if zone_id in seen_zone_ids:
                continue

            seen_zone_ids.add(zone_id)

            # Use domain info from zone if available, otherwise use placeholder
            domain_id = zone.get("domainId") or "_prefetched_"
            domain_name = zone.get("domainName") or "Accessible Domain"

            # Check cache for incremental mode (unless zone is in force_refresh list)
            if refresh_mode == RefreshMode.INCREMENTAL and zone_id in cached_zone_data and zone_id not in force_refresh_zones:
                cached = cached_zone_data[zone_id]
                cached.pop('_cached_at', None)  # Remove cache metadata
                all_zones_audit.append(cached)
                zones_from_cache += 1
                total_zones_processed += 1

                if update_activity:
                    await update_activity(
                        f"Zone {total_zones_processed}/{len(prefetched_zones)}: {zone_name} (cached)"
                    )
                logger.debug(f"Audit: Using cached data for zone '{zone_name}'")
                continue

            # Check if this is a force-refresh zone
            is_force_refresh = zone_id in force_refresh_zones
            if is_force_refresh:
                logger.info(f"Audit: Force-refreshing zone '{zone_name}' (user requested)")

            # Update progress before auditing
            if update_activity:
                suffix = " (force refresh)" if is_force_refresh else ""
                await update_activity(f"Auditing zone {total_zones_processed + 1}/{len(prefetched_zones)}: {zone_name}{suffix}")

            # Audit this zone
            zone_audit, zone_errors = await _audit_zone(
                sz_client, zone, domain_id, domain_name
            )
            zone_audit_dict = zone_audit.model_dump()
            all_zones_audit.append(zone_audit_dict)
            partial_errors.extend(zone_errors)
            zones_refreshed += 1

            # Cache the zone data
            if zone_cache:
                await zone_cache.cache_zone(zone_id, zone_audit_dict.copy())

            total_zones_processed += 1

            # Update with result summary
            if update_activity:
                await update_activity(
                    f"Zone {total_zones_processed}/{len(prefetched_zones)}: {zone_name} - "
                    f"{zone_audit.ap_status.total} APs, {zone_audit.wlan_count} WLANs"
                )

            logger.info(
                f"Audit: Completed zone {total_zones_processed}: '{zone_name}' "
                f"({zone_audit.ap_status.total} APs, {zone_audit.wlan_count} WLANs)"
            )

            # Update progress after each zone
            await update_zone_progress(total_zones_processed, total_zones_expected)

            # Update cache stats periodically
            if total_zones_processed % 10 == 0:
                await update_cache_stats()
    else:
        # Normal path: fetch zones per domain
        logger.info(f"Audit: Starting zone collection across {len(domains_raw)} domains")

        if update_activity:
            await update_activity("Discovering zones...")

        # For normal path, we'll track zones as we discover them
        zones_discovered = 0
        await update_zone_progress(0, 0)

        cancelled = False
        for domain in domains_raw:
            if cancelled:
                break

            # Check for cancellation before each domain
            if await is_cancelled():
                logger.info("Audit: Cancellation detected, stopping domain processing")
                partial_errors.append("Audit cancelled by user")
                was_cancelled = True
                break

            domain_id = domain.get("id")
            domain_name = domain.get("name", "Unknown")

            try:
                zones = await sz_client.zones.get_zones(domain_id=domain_id)
                # Update discovered count (excluding system zones)
                non_system_zones = [z for z in zones if z.get("name", "").lower() != "staging zone"]
                zones_discovered += len(non_system_zones)
                await update_zone_progress(total_zones_processed, zones_discovered)

                for zone in zones:
                    # Check for cancellation before each zone
                    if await is_cancelled():
                        logger.info("Audit: Cancellation detected, stopping zone processing")
                        partial_errors.append("Audit cancelled by user")
                        cancelled = True
                        was_cancelled = True
                        break

                    zone_id = zone.get("id")
                    zone_name = zone.get("name", "")

                    # Skip system zones
                    if zone_name.lower() == "staging zone":
                        logger.debug(f"Skipping system zone: {zone_name}")
                        continue

                    # Skip duplicates
                    if zone_id in seen_zone_ids:
                        continue

                    seen_zone_ids.add(zone_id)

                    # Check cache for incremental mode (unless zone is in force_refresh list)
                    if refresh_mode == RefreshMode.INCREMENTAL and zone_id in cached_zone_data and zone_id not in force_refresh_zones:
                        cached = cached_zone_data[zone_id]
                        cached.pop('_cached_at', None)  # Remove cache metadata
                        all_zones_audit.append(cached)
                        zones_from_cache += 1
                        total_zones_processed += 1

                        if update_activity:
                            await update_activity(
                                f"Zone {total_zones_processed}: {zone_name} (cached)"
                            )
                        logger.debug(f"Audit: Using cached data for zone '{zone_name}'")
                        continue

                    # Check if this is a force-refresh zone
                    is_force_refresh = zone_id in force_refresh_zones
                    if is_force_refresh:
                        logger.info(f"Audit: Force-refreshing zone '{zone_name}' (user requested)")

                    # Update progress before auditing
                    if update_activity:
                        suffix = " (force refresh)" if is_force_refresh else ""
                        await update_activity(f"Auditing zone {total_zones_processed + 1}: {zone_name}{suffix}")

                    # Audit this zone
                    zone_audit, zone_errors = await _audit_zone(
                        sz_client, zone, domain_id, domain_name
                    )
                    zone_audit_dict = zone_audit.model_dump()
                    all_zones_audit.append(zone_audit_dict)
                    partial_errors.extend(zone_errors)
                    zones_refreshed += 1

                    # Cache the zone data
                    if zone_cache:
                        await zone_cache.cache_zone(zone_id, zone_audit_dict.copy())

                    total_zones_processed += 1

                    # Update with result summary
                    if update_activity:
                        await update_activity(
                            f"Zone {total_zones_processed}: {zone_name} - "
                            f"{zone_audit.ap_status.total} APs, {zone_audit.wlan_count} WLANs"
                        )

                    logger.info(
                        f"Audit: Completed zone {total_zones_processed}: '{zone_name}' "
                        f"({zone_audit.ap_status.total} APs, {zone_audit.wlan_count} WLANs)"
                    )

                    # Update progress after each zone
                    await update_zone_progress(total_zones_processed, zones_discovered)

                    # Update cache stats periodically
                    if total_zones_processed % 10 == 0:
                        await update_cache_stats()

            except Exception as e:
                partial_errors.append(f"Failed to get zones for domain {domain_name}: {str(e)}")

    logger.info(f"Audit: Zone collection complete - {len(all_zones_audit)} zones processed")
    logger.info(f"Audit: Cache stats - {zones_from_cache} from cache, {zones_refreshed} refreshed")

    # Update cache metadata
    # Mark as partial if incremental mode OR if cancelled (to preserve existing cached zones)
    if zone_cache and zones_refreshed > 0:
        zone_ids = [z.get('zone_id') for z in all_zones_audit if z.get('zone_id')]
        is_partial = (refresh_mode == RefreshMode.INCREMENTAL) or was_cancelled
        await zone_cache.update_cache_meta(
            zone_ids=zone_ids,
            partial=is_partial
        )
        if was_cancelled:
            logger.info(f"Audit cancelled - cached {zones_refreshed} zones that completed")

    # Final cache stats update
    await update_cache_stats()

    task = Task(
        id="audit_zones",
        name="Audit Zones",
        task_type="audit_zones",
        status=TaskStatus.COMPLETED,
        input_data={},
        output_data={
            'all_zones_audit': all_zones_audit,
            'partial_errors': partial_errors,
            'cache_stats': {
                'zones_from_cache': zones_from_cache,
                'zones_refreshed': zones_refreshed
            }
        }
    )

    return [task]


async def _audit_zone(
    sz_client,
    zone: Dict[str, Any],
    domain_id: str,
    domain_name: str
) -> tuple[ZoneAudit, List[str]]:
    """Audit a single zone and return ZoneAudit data."""
    zone_id = zone.get("id")
    zone_name = zone.get("name", "Unknown")
    partial_errors = []

    # Initialize defaults
    ap_status = ApStatusBreakdown(online=0, offline=0, flagged=0, total=0)
    ap_model_distribution = []
    ap_firmware_distribution = []
    ap_groups = []
    wlans = []
    wlan_groups = []
    wlan_type_breakdown = {}
    external_ips = set()

    # Fetch APs
    aps = []
    try:
        aps_result = await sz_client.aps.get_aps_by_zone(zone_id)
        aps = aps_result.get("list", []) if isinstance(aps_result, dict) else aps_result
        logger.info(f"Zone '{zone_name}': Fetched {len(aps)} APs")

        online = 0
        offline = 0
        flagged = 0
        model_counter = Counter()
        firmware_counter = Counter()

        for ap in aps:
            status = (
                ap.get("connectionStatus") or
                ap.get("status") or
                ap.get("apStatus") or
                ""
            ).lower()

            if status in ["connect", "connected", "online"]:
                online += 1
            elif status in ["disconnect", "disconnected", "offline"]:
                offline += 1
            else:
                flagged += 1

            model = (
                ap.get("model") or
                ap.get("apModel") or
                ap.get("deviceModel") or
                "Unknown"
            )
            if model and model != "Unknown":
                model_counter[model] += 1

            firmware = (
                ap.get("firmwareVersion") or
                ap.get("apFirmwareVersion") or
                ap.get("version") or
                ap.get("swVersion") or
                "Unknown"
            )
            if firmware and firmware != "Unknown":
                firmware_counter[firmware] += 1

            ext_ip = (
                ap.get("extIp") or
                ap.get("externalIp") or
                ap.get("externalIpAddress")
            )
            if ext_ip:
                external_ips.add(ext_ip)

        ap_status = ApStatusBreakdown(
            online=online,
            offline=offline,
            flagged=flagged,
            total=len(aps)
        )

        ap_model_distribution = [
            ModelDistribution(model=model, count=count)
            for model, count in model_counter.most_common()
        ]

        ap_firmware_distribution = [
            FirmwareDistribution(version=ver, count=count)
            for ver, count in firmware_counter.most_common()
        ]

    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch APs: {str(e)}")

    # Fetch AP Groups
    try:
        ap_groups_raw = await sz_client.apgroups.get_ap_groups_by_zone(zone_id)
        ap_group_counts = {}
        for ap in aps:
            gid = ap.get("apGroupId")
            if gid:
                ap_group_counts[gid] = ap_group_counts.get(gid, 0) + 1

        ap_groups = [
            ApGroupSummary(
                id=g.get("id", ""),
                name=g.get("name", "Unknown"),
                ap_count=ap_group_counts.get(g.get("id"), 0)
            )
            for g in ap_groups_raw
        ]
    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch AP Groups: {str(e)}")

    # Fetch WLANs - parallelize detail fetching for performance
    try:
        wlans_list = await sz_client.wlans.get_wlans_by_zone(zone_id)
        logger.info(f"Zone '{zone_name}': Fetching details for {len(wlans_list)} WLANs (parallel)")
        wlan_type_counter = Counter()

        # Fetch all WLAN details in parallel (rate limiter handles throttling)
        async def fetch_wlan_detail(wlan_basic):
            wlan_id = wlan_basic.get("id")
            wlan_name = wlan_basic.get("name", "Unknown")
            try:
                w = await sz_client.wlans.get_wlan_details(zone_id, wlan_id)
                return {
                    "success": True,
                    "data": w,
                    "wlan_id": wlan_id,
                    "wlan_name": wlan_name,
                    "ssid": wlan_basic.get("ssid", wlan_name)
                }
            except Exception as e:
                logger.debug(f"Zone {zone_name}: Failed to get WLAN details for {wlan_name}: {e}")
                return {
                    "success": False,
                    "wlan_id": wlan_id,
                    "wlan_name": wlan_name,
                    "ssid": wlan_basic.get("ssid", wlan_name)
                }

        # Batch fetch with asyncio.gather (rate limiter handles throttling)
        wlan_results = await asyncio.gather(*[fetch_wlan_detail(w) for w in wlans_list])

        for result in wlan_results:
            if result["success"]:
                w = result["data"]
                auth_type = WlanService.extract_auth_type(w)
                encryption = WlanService.extract_encryption(w)
                vlan = WlanService.extract_vlan(w)

                wlans.append(WlanSummary(
                    id=w.get("id", result["wlan_id"]),
                    name=w.get("name", result["wlan_name"]),
                    ssid=w.get("ssid", result["ssid"]),
                    auth_type=auth_type,
                    encryption=encryption,
                    vlan=vlan
                ))
                wlan_type_counter[auth_type] += 1
            else:
                wlans.append(WlanSummary(
                    id=result["wlan_id"],
                    name=result["wlan_name"],
                    ssid=result["ssid"],
                    auth_type="Unknown",
                    encryption="Unknown",
                    vlan=None
                ))
                wlan_type_counter["Unknown"] += 1

        wlan_type_breakdown = dict(wlan_type_counter)

    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch WLANs: {str(e)}")

    # Fetch WLAN Groups
    try:
        wlan_groups_raw = await sz_client.wlans.get_wlan_groups_by_zone(zone_id)
        wlan_groups = [
            WlanGroupSummary(
                id=g.get("id", ""),
                name=g.get("name", "Unknown"),
                wlan_count=len(g.get("members", []))
            )
            for g in wlan_groups_raw
        ]
    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch WLAN Groups: {str(e)}")

    zone_audit = ZoneAudit(
        zone_id=zone_id,
        zone_name=zone_name,
        domain_id=domain_id,
        domain_name=domain_name,
        external_ips=sorted(external_ips),
        ap_status=ap_status,
        ap_model_distribution=ap_model_distribution,
        ap_groups=ap_groups,
        ap_firmware_distribution=ap_firmware_distribution,
        wlan_count=len(wlans),
        wlan_groups=wlan_groups,
        wlans=wlans,
        wlan_type_breakdown=wlan_type_breakdown
    )

    return zone_audit, partial_errors
