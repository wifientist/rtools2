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

from workflow.v2.models import Task, TaskStatus
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
    # Build switch groups dict from domains if API dict is empty but we have switch-derived groups
    switch_groups_for_matching = all_switch_groups
    if not switch_groups_for_matching and all_switch_groups_flat:
        # Build dict from domain audits which have switch groups derived from switches
        switch_groups_for_matching = {}
        for domain_audit in domains_audit:
            if domain_audit.switch_groups:
                switch_groups_for_matching[domain_audit.domain_id] = [
                    {
                        "id": sg.id,
                        "name": sg.name,
                        "switch_count": sg.switch_count,
                        "switches_online": sg.switches_online,
                        "switches_offline": sg.switches_offline
                    }
                    for sg in domain_audit.switch_groups
                ]

    if switch_groups_for_matching and all_zones_audit:
        _match_switch_groups_to_zones(
            all_zones_audit,
            switch_groups_for_matching,
            all_switch_groups_flat
        )

    # Persist matched_switch_groups back to zone cache so matches survive cache reload
    zone_cache = context.get('zone_cache')
    if zone_cache and all_zones_audit:
        zones_with_matches = [z for z in all_zones_audit if z.matched_switch_groups]
        if zones_with_matches:
            logger.info(f"Persisting {len(zones_with_matches)} zone matches to cache")
            for zone in zones_with_matches:
                # Update cached zone with matched_switch_groups
                cached = await zone_cache.get_cached_zone(zone.zone_id)
                if cached:
                    # Preserve user_set_mapping flag if it exists (never overwrite user selections)
                    # Only update matches if this is NOT a user-set zone
                    if cached.get('user_set_mapping') and not zone.user_set_mapping:
                        # User set a mapping manually - don't overwrite with auto-matched
                        logger.debug(f"Zone '{zone.zone_name}': Preserving user-set mapping in cache")
                        continue

                    # Store switch group matches as serializable dicts
                    cached['matched_switch_groups'] = [
                        sg.model_dump() if hasattr(sg, 'model_dump') else sg
                        for sg in zone.matched_switch_groups
                    ]
                    # Preserve the user_set_mapping flag from the zone
                    if zone.user_set_mapping:
                        cached['user_set_mapping'] = True
                    await zone_cache.cache_zone(zone.zone_id, cached)

    # Cache all switch groups (with switch counts) for retrieval by cache endpoint
    # Use all_switch_groups_flat which is enriched from actual switch data
    if zone_cache and all_switch_groups_flat:
        # Build a dict keyed by "_all_" since switch groups may span domains
        sg_dict = {"_all_": [
            sg.model_dump() if hasattr(sg, 'model_dump') else {
                "id": sg.id,
                "name": sg.name,
                "switch_count": sg.switch_count,
                "switches_online": sg.switches_online,
                "switches_offline": sg.switches_offline,
                "firmware_versions": [
                    {"version": fv.version, "count": fv.count}
                    for fv in sg.firmware_versions
                ] if sg.firmware_versions else []
            }
            for sg in all_switch_groups_flat
        ]}
        # Don't pass switches again - counts are already computed in all_switch_groups_flat
        await zone_cache.cache_switch_groups(sg_dict, None)

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

    # Check if this is a synthetic domain (from fallback/cache modes)
    is_synthetic_domain = domain_id in ("_prefetched_", "_cached_", "_all_")

    # Get zones for this domain
    # For synthetic domains, include all zones
    if is_synthetic_domain:
        domain_zones = all_zones
    else:
        domain_zones = [z for z in all_zones if z.domain_id == domain_id]

    # Aggregate stats from zones
    total_aps = sum(z.ap_status.total for z in domain_zones)
    total_wlans = sum(z.wlan_count for z in domain_zones)

    # Build switch groups
    switch_groups = []
    total_switches = 0
    switch_firmware_distribution = []

    # For synthetic domains, include switch groups from "_all_" key or all keys
    if is_synthetic_domain:
        domain_switch_groups = all_switch_groups.get("_all_", [])
        # If no "_all_" key, flatten all switch groups
        if not domain_switch_groups:
            domain_switch_groups = [sg for sgs in all_switch_groups.values() for sg in sgs]
    else:
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
        # For synthetic domains, include all switches
        if is_synthetic_domain:
            domain_switches = all_switches
        else:
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


def _split_camel_case(name: str) -> List[str]:
    """
    Split camelCase/PascalCase into separate words.
    Examples:
        MigrateMe -> ['Migrate', 'Me']
        buildingA -> ['building', 'A']
        ICXSwitch -> ['ICX', 'Switch']
        AP_Zone1 -> ['AP', 'Zone', '1']
    """
    if not name:
        return []

    # Insert space before uppercase letters that follow lowercase
    # Also handle transitions like "ICXSwitch" -> "ICX Switch"
    result = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    result = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', result)

    # Replace common delimiters with spaces
    result = re.sub(r'[_\-./\\]+', ' ', result)

    # Split and filter empty strings
    words = [w for w in result.split() if w]
    return words


def _extract_words(name: str) -> List[str]:
    """
    Extract meaningful words from a name, handling camelCase and delimiters.
    Returns lowercase words.
    """
    words = _split_camel_case(name)
    return [w.lower() for w in words if len(w) >= 2]


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


def _longest_common_prefix(s1: str, s2: str) -> str:
    """Find the longest common prefix between two strings."""
    min_len = min(len(s1), len(s2))
    for i in range(min_len):
        if s1[i] != s2[i]:
            return s1[:i]
    return s1[:min_len]


# Stop words for matching - common words that cause false positives
_STOP_WORDS = {
    # Articles and prepositions
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    # Common naming patterns
    "staging", "stg", "sg", "new", "old", "test", "testing",
    "apartments", "apts", "apartment", "apt",
    "village", "place", "station", "center", "phase",
    "ii", "iii", "iv", "north", "south", "east", "west"
}


def score_switch_group_match(
    zone_name: str,
    zone_domain_id: str,
    sg_name: str,
    sg_domain_id: str
) -> tuple[int, str, str]:
    """
    Score how well a switch group matches a zone.

    Returns:
        tuple of (score, match_type, match_reason)
        - score: 0-100+ (higher is better match)
        - match_type: "exact", "normalized", "contains", "word-overlap", "prefix", "none"
        - match_reason: Human-readable explanation
    """
    # Normalize inputs
    zone_lower = zone_name.lower()
    sg_lower = sg_name.lower()
    zone_normalized = _normalize_name(zone_name)
    sg_normalized = _normalize_name(sg_name)
    zone_words = set(_extract_words(zone_name))
    sg_words = set(_extract_words(sg_name))
    zone_significant = zone_words - _STOP_WORDS
    sg_significant = sg_words - _STOP_WORDS

    # Domain bonus
    domain_bonus = 10 if zone_domain_id == sg_domain_id else 0

    # Priority 1: Exact name match (100 points)
    if zone_lower == sg_lower:
        return (100 + domain_bonus, "exact", f"Exact name match: '{zone_name}'")

    # Priority 2: Normalized name match (85 points)
    if zone_normalized and sg_normalized and zone_normalized == sg_normalized:
        return (85 + domain_bonus, "normalized", f"Names match after normalization: '{zone_normalized}'")

    # Priority 3: Contains match
    if len(zone_lower) >= 3 and len(sg_lower) >= 3:
        if zone_lower in sg_lower:
            return (70 + domain_bonus, "contains", f"Zone name '{zone_name}' found in '{sg_name}'")
        if sg_lower in zone_lower:
            return (65 + domain_bonus, "contains", f"Switch group name '{sg_name}' found in '{zone_name}'")

    # Priority 4: Word overlap
    if zone_significant and sg_significant:
        common = zone_significant & sg_significant
        if len(common) >= 3:
            return (60 + domain_bonus, "word-overlap", f"3+ common words: {', '.join(sorted(common)[:5])}")
        if len(common) == 2:
            return (50 + domain_bonus, "word-overlap", f"2 common words: {', '.join(sorted(common))}")
        if len(common) == 1:
            word = list(common)[0]
            if len(word) >= 5:
                return (40 + domain_bonus, "word-overlap", f"Significant common word: '{word}'")

    # Priority 5: Common prefix
    if len(zone_normalized) >= 6 and len(sg_normalized) >= 6:
        prefix = _longest_common_prefix(zone_normalized, sg_normalized)
        if len(prefix) >= 8:
            return (35 + domain_bonus, "prefix", f"Common prefix (8+ chars): '{prefix}'")
        if len(prefix) >= 6:
            return (25 + domain_bonus, "prefix", f"Common prefix (6+ chars): '{prefix}'")

    # No significant match
    return (0, "none", "No significant match")


def get_match_candidates(
    zone_name: str,
    zone_domain_id: str,
    all_switch_groups: list,
    top_n: int = 3,
    min_score: int = 20
) -> list:
    """
    Get top N match candidates for a zone.

    Args:
        zone_name: Name of the zone
        zone_domain_id: Domain ID of the zone
        all_switch_groups: List of dicts with 'id', 'name', 'domain_id', etc.
        top_n: Number of top candidates to return
        min_score: Minimum score to include (filters out weak matches)

    Returns:
        List of candidate dicts sorted by score descending
    """
    candidates = []

    for sg in all_switch_groups:
        sg_id = sg.get("id", "")
        sg_name = sg.get("name", "")
        sg_domain_id = sg.get("domain_id", "")

        score, match_type, match_reason = score_switch_group_match(
            zone_name, zone_domain_id, sg_name, sg_domain_id
        )

        if score >= min_score:
            candidates.append({
                "switch_group_id": sg_id,
                "switch_group_name": sg_name,
                "switch_count": sg.get("switch_count", 0),
                "switches_online": sg.get("switches_online", 0),
                "switches_offline": sg.get("switches_offline", 0),
                "score": score,
                "match_type": match_type,
                "match_reason": match_reason,
                "same_domain": zone_domain_id == sg_domain_id
            })

    # Sort by score descending, then by name for consistency
    candidates.sort(key=lambda c: (-c["score"], c["switch_group_name"]))

    return candidates[:top_n]


def _match_switch_groups_to_zones(
    zones: List[ZoneAudit],
    switch_groups_raw: Dict[str, List[Dict[str, Any]]],
    switch_groups_with_counts: List[SwitchGroupSummary]
) -> None:
    """
    Match switch groups to zones by name similarity.

    Matching priority (highest to lowest):
    1. Exact name match (case-insensitive)
    2. Normalized name match (strips prefixes/suffixes)
    3. Contains match (one name contains the other)
    4. Word overlap (2+ significant words in common)

    Within each priority level, same-domain matches are preferred but
    cross-domain matches are allowed if no same-domain match exists.
    """
    sg_counts_map = {sg.id: sg for sg in switch_groups_with_counts}

    all_switch_groups = []
    for domain_id, sgs in switch_groups_raw.items():
        for sg in sgs:
            sg_id = sg.get("id")
            sg_name = sg.get("name", "")
            all_switch_groups.append({
                "id": sg_id,
                "name": sg_name,
                "domain_id": domain_id,
                "normalized": _normalize_name(sg_name),
                "name_lower": sg_name.lower(),
                "words": _extract_words(sg_name),  # camelCase-aware word extraction
                "summary": sg_counts_map.get(sg_id)
            })

    matched_sg_ids = set()

    # Pre-register switch groups that are already matched from cache
    # to avoid duplicate assignments
    # Track user-set vs auto-matched zones separately
    zones_with_cached_matches = 0
    zones_with_user_set_matches = 0
    for zone in zones:
        if zone.matched_switch_groups:
            zones_with_cached_matches += 1
            # Check if this is a user-set mapping (should never be overwritten)
            if zone.user_set_mapping:
                zones_with_user_set_matches += 1
            for sg in zone.matched_switch_groups:
                sg_id = sg.id if hasattr(sg, 'id') else sg.get('id')
                if sg_id:
                    matched_sg_ids.add(sg_id)

    if zones_with_cached_matches:
        logger.info(
            f"Found {zones_with_cached_matches} zones with cached matches "
            f"({zones_with_user_set_matches} user-set, {len(matched_sg_ids)} switch groups)"
        )

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

    def find_match(zone, match_fn, match_type: str):
        """
        Find a matching switch group using the given match function.
        Prefers same-domain matches, falls back to cross-domain.
        Returns True if a match was found.
        """
        zone_domain_id = zone.domain_id

        # First pass: same-domain matches
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id and match_fn(sg):
                zone.matched_switch_groups.append(get_sg_summary(sg))
                matched_sg_ids.add(sg["id"])
                logger.debug(f"Zone '{zone.zone_name}': Matched '{sg['name']}' ({match_type}, same domain)")
                return True

        # Second pass: cross-domain matches
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if match_fn(sg):
                zone.matched_switch_groups.append(get_sg_summary(sg))
                matched_sg_ids.add(sg["id"])
                logger.debug(f"Zone '{zone.zone_name}': Matched '{sg['name']}' ({match_type}, cross-domain)")
                return True

        return False

    for zone in zones:
        # Skip zones with user-set mappings (never override manual selections)
        if zone.user_set_mapping:
            logger.debug(f"Zone '{zone.zone_name}': Preserving user-set mapping ({len(zone.matched_switch_groups)} groups)")
            continue

        # Skip zones that already have matches from cache (auto-matched)
        if zone.matched_switch_groups:
            logger.debug(f"Zone '{zone.zone_name}': Using {len(zone.matched_switch_groups)} cached matches")
            continue

        zone_name = zone.zone_name
        zone_name_lower = zone_name.lower()
        zone_normalized = _normalize_name(zone_name)
        zone_words = _extract_words(zone_name)  # camelCase-aware extraction
        zone_words_set = set(zone_words)
        # Stop words: common articles/prepositions + common naming suffixes that cause false matches
        stop_words = {
            # Articles and prepositions
            "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
            # Common naming patterns that appear in many zone/switch group names
            "staging", "stg", "sg", "new", "old", "test", "testing",
            "apartments", "apts", "apartment", "apt",
            "village", "place", "station", "center", "phase",
            "ii", "iii", "iv", "north", "south", "east", "west"
        }
        zone_significant = zone_words_set - stop_words

        # Priority 1: Exact name match
        if find_match(zone, lambda sg: sg["name_lower"] == zone_name_lower, "exact"):
            continue

        # Priority 2: Normalized name match
        if zone_normalized and find_match(
            zone,
            lambda sg: sg["normalized"] == zone_normalized,
            "normalized"
        ):
            continue

        # Priority 3: Contains match (one name contains the other, min 3 chars)
        if len(zone_name_lower) >= 3:
            if find_match(
                zone,
                lambda sg: (len(sg["name_lower"]) >= 3 and
                           (zone_name_lower in sg["name_lower"] or sg["name_lower"] in zone_name_lower)),
                "contains"
            ):
                continue

        # Priority 4: Word overlap (2+ significant words in common, OR 1 word if 5+ chars)
        # e.g., "MigrateMe" and "MigrateThis" share "migrate" (7 chars)
        # This prevents short generic words from causing false matches
        if zone_significant:
            def word_overlap_match(sg):
                sg_words_set = set(sg["words"])
                sg_significant = sg_words_set - stop_words
                common = zone_significant & sg_significant
                if len(common) >= 2:
                    return True
                # Single word match only if the word is 5+ chars (more specific)
                if len(common) == 1:
                    word = list(common)[0]
                    return len(word) >= 5
                return False
            if find_match(zone, word_overlap_match, "word-overlap"):
                continue

        # Priority 5: Common prefix match (6+ char prefix in normalized form)
        if len(zone_normalized) >= 6:
            def prefix_match(sg):
                if len(sg["normalized"]) < 6:
                    return False
                prefix = _longest_common_prefix(zone_normalized, sg["normalized"])
                return len(prefix) >= 6
            find_match(zone, prefix_match, "common-prefix")

    total_matched = len(matched_sg_ids)
    total_sgs = len(all_switch_groups)
    zones_with_matches = sum(1 for z in zones if z.matched_switch_groups)
    new_matches = zones_with_matches - zones_with_cached_matches
    logger.info(
        f"Switch group matching: {total_matched}/{total_sgs} switch groups matched to "
        f"{zones_with_matches}/{len(zones)} zones ({zones_with_cached_matches} cached, {new_matches} new)"
    )
