"""
Finalize Phase

Aggregates stats, matches switch groups to zones, and stores final result in Redis.
"""

import logging
import json
import re
from collections import Counter
from datetime import datetime
from typing import Dict, Any, List

from workflow.models import Task, TaskStatus
from schemas.sz_audit import (
    SZAuditResult,
    ZoneAudit,
    DomainAudit,
    SwitchGroupSummary,
    FirmwareDistribution,
    ModelDistribution,
)

logger = logging.getLogger(__name__)

# 24 hour TTL for audit results
AUDIT_RESULT_TTL = 60 * 60 * 24  # 24 hours in seconds


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Finalize audit - aggregate stats and store results

    Args:
        context: Workflow context with results from previous phases

    Returns:
        List with single task indicating completion
    """
    logger.info("Phase 4: Finalize - Aggregating results and storing in Redis")

    update_activity = context.get('update_activity')
    if update_activity:
        await update_activity("Aggregating results...")

    # Get data from previous phases
    phase_results = context.get('phase_results', {})
    init_data = phase_results.get('initialize', {})
    switch_data = phase_results.get('fetch_switches', {})
    zone_data = phase_results.get('audit_zones', {})

    controller = context.get('controller')
    redis_client = context.get('redis_client')
    job_id = context.get('job_id')

    # Extract data
    cluster_ip = init_data.get('cluster_ip')
    controller_firmware = init_data.get('controller_firmware')
    domains_raw = init_data.get('domains_raw', [])

    all_switches = switch_data.get('all_switches', [])
    all_switch_groups = switch_data.get('all_switch_groups', {})

    # Reconstruct ZoneAudit objects from dicts
    all_zones_audit_dicts = zone_data.get('all_zones_audit', [])
    all_zones_audit = [ZoneAudit(**z) for z in all_zones_audit_dicts]

    # In cached_only mode, domains_raw contains a synthetic entry "_cached_"
    # We need to derive actual domains from the cached zones
    if len(domains_raw) == 1 and domains_raw[0].get('id') == '_cached_':
        logger.info("Finalize: Deriving domains from cached zones")
        domain_map = {}
        for zone in all_zones_audit:
            if zone.domain_id and zone.domain_id not in domain_map:
                domain_map[zone.domain_id] = {
                    'id': zone.domain_id,
                    'name': zone.domain_name or 'Unknown Domain'
                }
        domains_raw = list(domain_map.values())
        logger.info(f"Finalize: Derived {len(domains_raw)} domains from {len(all_zones_audit)} cached zones")

    # Collect all partial errors
    partial_errors = []
    partial_errors.extend(init_data.get('partial_errors', []))
    partial_errors.extend(switch_data.get('partial_errors', []))
    partial_errors.extend(zone_data.get('partial_errors', []))

    # Audit domains (build domain audit objects)
    domains_audit = []
    all_switch_groups_flat = []

    for domain in domains_raw:
        domain_audit, switch_groups_summary = _audit_domain(
            domain, all_zones_audit, all_switches, all_switch_groups
        )
        domains_audit.append(domain_audit)
        all_switch_groups_flat.extend(switch_groups_summary)

    # Match switch groups to zones
    if all_switch_groups and all_zones_audit:
        _match_switch_groups_to_zones(
            all_zones_audit,
            all_switch_groups,
            all_switch_groups_flat
        )

    # Build domain hierarchy
    domain_map = {d.domain_id: d for d in domains_audit}
    root_domains = []

    for domain in domains_audit:
        if domain.parent_domain_id and domain.parent_domain_id in domain_map:
            parent = domain_map[domain.parent_domain_id]
            parent.children.append(domain)
        else:
            root_domains.append(domain)

    # Aggregate global stats
    total_aps = sum(z.ap_status.total for z in all_zones_audit)
    total_wlans = sum(z.wlan_count for z in all_zones_audit)
    total_switches = sum(d.total_switches for d in domains_audit)

    # Aggregate AP models
    model_counter = Counter()
    for zone in all_zones_audit:
        for md in zone.ap_model_distribution:
            model_counter[md.model] += md.count

    ap_model_summary = [
        ModelDistribution(model=m, count=c)
        for m, c in model_counter.most_common()
    ]

    # Aggregate AP firmware
    firmware_counter = Counter()
    for zone in all_zones_audit:
        for fd in zone.ap_firmware_distribution:
            firmware_counter[fd.version] += fd.count

    ap_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in firmware_counter.most_common()
    ]

    # Aggregate switch firmware
    switch_firmware_counter = Counter()
    for domain in domains_audit:
        for fd in domain.switch_firmware_distribution:
            switch_firmware_counter[fd.version] += fd.count

    switch_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in switch_firmware_counter.most_common()
    ]

    # Aggregate WLAN types
    wlan_type_counter = Counter()
    for zone in all_zones_audit:
        for wlan_type, count in zone.wlan_type_breakdown.items():
            wlan_type_counter[wlan_type] += count

    wlan_type_summary = dict(wlan_type_counter)

    logger.info(
        f"Audit complete: {len(domains_audit)} domains, {len(all_zones_audit)} zones, "
        f"{total_aps} APs, {total_wlans} WLANs, {total_switches} switches"
    )

    if update_activity:
        await update_activity(
            f"Complete: {len(all_zones_audit)} zones, {total_aps} APs, "
            f"{total_wlans} WLANs, {total_switches} switches"
        )

    # Build final result
    result = SZAuditResult(
        controller_id=controller.id,
        controller_name=controller.name,
        host=controller.sz_host,
        timestamp=datetime.utcnow(),
        cluster_ip=cluster_ip,
        controller_firmware=controller_firmware,
        domains=root_domains,
        zones=all_zones_audit,
        total_domains=len(domains_audit),
        total_zones=len(all_zones_audit),
        total_aps=total_aps,
        total_wlans=total_wlans,
        total_switches=total_switches,
        ap_model_summary=ap_model_summary,
        ap_firmware_summary=ap_firmware_summary,
        switch_firmware_summary=switch_firmware_summary,
        wlan_type_summary=wlan_type_summary,
        partial_errors=partial_errors
    )

    # Store result in Redis with 24hr TTL
    if redis_client and job_id:
        result_key = f"sz_audit:results:{job_id}"
        await redis_client.setex(
            result_key,
            AUDIT_RESULT_TTL,
            result.model_dump_json()
        )
        logger.info(f"Stored audit result in Redis: {result_key} (TTL: 24 hours)")

    task = Task(
        id="finalize",
        name="Finalize Results",
        task_type="finalize",
        status=TaskStatus.COMPLETED,
        input_data={},
        output_data={
            'result_stored': True,
            'result_key': f"sz_audit:results:{job_id}" if job_id else None,
            'total_domains': len(domains_audit),
            'total_zones': len(all_zones_audit),
            'total_aps': total_aps,
            'total_wlans': total_wlans,
            'total_switches': total_switches
        }
    )

    return [task]


def _audit_domain(
    domain: Dict[str, Any],
    all_zones: List[ZoneAudit],
    all_switches: List[Dict[str, Any]],
    all_switch_groups: Dict[str, List[Dict[str, Any]]]
) -> tuple[DomainAudit, List[SwitchGroupSummary]]:
    """Build DomainAudit from raw data."""
    domain_id = domain.get("id")
    domain_name = domain.get("name", "Unknown")
    parent_domain_id = domain.get("parentDomainId")
    parent_domain_name = domain.get("parentDomainName")

    # Get zones for this domain
    domain_zones = [z for z in all_zones if z.domain_id == domain_id]

    # Aggregate stats from zones
    total_aps = sum(z.ap_status.total for z in domain_zones)
    total_wlans = sum(z.wlan_count for z in domain_zones)

    # Build switch groups
    switch_groups = []
    total_switches = 0
    switch_firmware_distribution = []

    domain_switch_groups = all_switch_groups.get(domain_id, [])
    switch_group_map = {}

    for sg in domain_switch_groups:
        sg_id = sg.get("id")
        sg_name = sg.get("name", sg_id)
        switch_group_map[sg_id] = {
            "id": sg_id,
            "name": sg_name,
            "online": 0,
            "offline": 0,
            "total": 0,
            "firmware_counter": Counter()
        }

    firmware_counter = Counter()

    if all_switches:
        domain_switches = [s for s in all_switches if s.get("domainId") == domain_id]
        total_switches = len(domain_switches)

        for s in domain_switches:
            sg_id = s.get("switchGroupId") or s.get("groupId") or "ungrouped"
            sg_name = s.get("switchGroupName") or s.get("groupName") or "Ungrouped"

            if sg_id not in switch_group_map:
                switch_group_map[sg_id] = {
                    "id": sg_id,
                    "name": sg_name,
                    "online": 0,
                    "offline": 0,
                    "total": 0,
                    "firmware_counter": Counter()
                }

            switch_group_map[sg_id]["total"] += 1
            status = (s.get("status") or "").lower()
            if status in ["online", "connected"]:
                switch_group_map[sg_id]["online"] += 1
            else:
                switch_group_map[sg_id]["offline"] += 1

            firmware = s.get("firmwareVersion") or "Unknown"
            if firmware != "Unknown":
                firmware_counter[firmware] += 1
                switch_group_map[sg_id]["firmware_counter"][firmware] += 1

    switch_groups = [
        SwitchGroupSummary(
            id=sg["id"],
            name=sg["name"],
            switch_count=sg["total"],
            switches_online=sg["online"],
            switches_offline=sg["offline"],
            firmware_versions=[
                FirmwareDistribution(version=ver, count=count)
                for ver, count in sg["firmware_counter"].most_common()
            ]
        )
        for sg in switch_group_map.values()
    ]

    switch_firmware_distribution = [
        FirmwareDistribution(version=ver, count=count)
        for ver, count in firmware_counter.most_common()
    ]

    domain_audit = DomainAudit(
        domain_id=domain_id,
        domain_name=domain_name,
        parent_domain_id=parent_domain_id,
        parent_domain_name=parent_domain_name,
        zone_count=len(domain_zones),
        total_aps=total_aps,
        total_wlans=total_wlans,
        switch_groups=switch_groups,
        total_switches=total_switches,
        switch_firmware_distribution=switch_firmware_distribution,
        children=[]
    )

    return domain_audit, switch_groups


def _normalize_name(name: str) -> str:
    """Normalize a name for matching."""
    if not name:
        return ""

    normalized = name.lower().strip()
    prefixes = ["sg_", "sw_", "switch_", "switches_", "icx_", "icx-"]
    suffixes = ["_switches", "_switch", "-switches", "-switch", "_sg", "-sg"]

    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
            break

    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def _match_switch_groups_to_zones(
    zones: List[ZoneAudit],
    switch_groups_raw: Dict[str, List[Dict[str, Any]]],
    switch_groups_with_counts: List[SwitchGroupSummary]
) -> None:
    """Match switch groups to zones by name similarity."""
    sg_counts_map = {sg.id: sg for sg in switch_groups_with_counts}

    all_switch_groups = []
    for domain_id, sgs in switch_groups_raw.items():
        for sg in sgs:
            sg_id = sg.get("id")
            all_switch_groups.append({
                "id": sg_id,
                "name": sg.get("name", ""),
                "domain_id": domain_id,
                "normalized": _normalize_name(sg.get("name", "")),
                "summary": sg_counts_map.get(sg_id)
            })

    matched_sg_ids = set()

    def get_sg_summary(sg_info: Dict) -> SwitchGroupSummary:
        if sg_info["summary"]:
            return sg_info["summary"]
        return SwitchGroupSummary(
            id=sg_info["id"],
            name=sg_info["name"],
            switch_count=0,
            switches_online=0,
            switches_offline=0
        )

    for zone in zones:
        zone_name = zone.zone_name
        zone_domain_id = zone.domain_id
        zone_normalized = _normalize_name(zone_name)
        zone_words = set(zone_normalized.split()) if zone_normalized else set()

        # Priority 1: Exact match
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                if sg["name"].lower() == zone_name.lower():
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])

        # Priority 2: Normalized match
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                if sg["normalized"] == zone_normalized and zone_normalized:
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])

        # Priority 3: Contains match
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                sg_lower = sg["name"].lower()
                zone_lower = zone_name.lower()
                if len(zone_lower) >= 3 and len(sg_lower) >= 3:
                    if zone_lower in sg_lower or sg_lower in zone_lower:
                        zone.matched_switch_groups.append(get_sg_summary(sg))
                        matched_sg_ids.add(sg["id"])

        # Priority 4: Word overlap
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                sg_words = set(sg["normalized"].split()) if sg["normalized"] else set()
                stop_words = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for"}
                zone_significant = zone_words - stop_words
                sg_significant = sg_words - stop_words
                common_words = zone_significant & sg_significant
                if len(common_words) >= 2:
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])

    total_matched = len(matched_sg_ids)
    total_sgs = len(all_switch_groups)
    zones_with_matches = sum(1 for z in zones if z.matched_switch_groups)
    logger.info(f"Switch group matching: {total_matched}/{total_sgs} matched to {zones_with_matches}/{len(zones)} zones")
