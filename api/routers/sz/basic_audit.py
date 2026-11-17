"""
SZ Audit - Basic mode

Licenses and overall AP/switch counts for one controller, from a handful of
controller-wide calls. No zone walk, no job, no cache.

A section that can't be read stays None and says why in `errors`, so a failed
call never reads as a count of 0.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from models.controller import Controller
from schemas.sz_audit import (
    BasicAuditResult,
    BasicDomainNode,
    BasicZoneNode,
    LicenseEntitlement,
    LicenseUsage,
)
from szapi.client import SZClient

logger = logging.getLogger(__name__)

# Concurrent per-domain zone lists while placing zones in the domain tree
ZONE_LIST_CONCURRENCY = 8
UNKNOWN_DOMAIN_ID = "_unknown"

AP_LICENSE_TYPE = "ap capacity license"
SWITCH_LICENSE_TYPE = "switch capacity license"


def clean_text(value: Optional[str]) -> str:
    """SZ descriptions carry non-breaking spaces ("Feature License for\\u00a0URL Filtering")"""
    return " ".join((value or "").replace(" ", " ").split())


def parse_license_date(value: Optional[str]) -> Optional[str]:
    """
    "2021/07/01" -> "2021-07-01". None when SZ gave nothing usable.

    Perpetual licenses come back as the end of Java's date range,
    "292278994/08/17", so a year past 9999 is not a real date either.
    """
    match = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", (value or "").strip())
    return "-".join(match.groups()) if match else None


def is_perpetual(value: Optional[str]) -> bool:
    match = re.match(r"(\d+)/", (value or "").strip())
    return bool(match) and int(match.group(1)) > 9999


def build_license_usage(raw: Dict[str, Any]) -> List[LicenseUsage]:
    """Types with nothing licensed and nothing used are noise, so they're left out"""
    rows = []
    for item in raw.get("list", []):
        counts = item.get("capacityControlLicenseCount") or {}
        total = counts.get("totalCount") or 0
        used = counts.get("usedCount") or 0
        if total or used:
            rows.append(LicenseUsage(
                license_type=clean_text(item.get("licenseTypeDescription")),
                used=used,
                total=total,
            ))
    return rows


def build_entitlements(raw: Dict[str, Any]) -> List[LicenseEntitlement]:
    return [
        LicenseEntitlement(
            name=clean_text(item.get("name")),
            description=clean_text(item.get("description")),
            count=item.get("count") or 0,
            starts=parse_license_date(item.get("createTime")),
            expires=parse_license_date(item.get("expireDate")),
            perpetual=is_perpetual(item.get("expireDate")),
        )
        for item in raw.get("list", [])
    ]


def find_license(rows: List[LicenseUsage], license_type: str) -> Optional[LicenseUsage]:
    return next((r for r in rows if r.license_type.lower() == license_type), None)


async def fetch_zone_domains(sz_client: SZClient) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]:
    """
    Domains, and which domain each zone sits in directly.

    rkszones?domainId= may also list a subdomain's zones, so a zone listed by
    several domains goes to the deepest one. Raises if the domain list can't
    be read; a domain whose zones can't be read is reported in the errors.
    """
    domains = await sz_client.zones.get_domains(recursively=True, include_self=True)
    parents = {d.get("id"): d.get("parentDomainId") for d in domains}

    def depth(domain_id: str) -> int:
        seen = set()
        while parents.get(domain_id) in parents and domain_id not in seen:
            seen.add(domain_id)
            domain_id = parents[domain_id]
        return len(seen)

    # zoneCount 0 means nothing to list; a missing count is listed anyway
    to_list = [d for d in domains if d.get("zoneCount") != 0]
    semaphore = asyncio.Semaphore(ZONE_LIST_CONCURRENCY)

    async def list_zones(domain: Dict[str, Any]):
        async with semaphore:
            return await sz_client.zones.get_zones(domain_id=domain.get("id"), list_size=1000)

    listed = await asyncio.gather(*(list_zones(d) for d in to_list), return_exceptions=True)

    zone_domain: Dict[str, str] = {}
    failed = []
    for domain, zones in zip(to_list, listed):
        if isinstance(zones, Exception):
            failed.append(domain.get("name") or domain.get("id"))
            continue
        for zone in zones:
            current = zone_domain.get(zone.get("id"))
            if current is None or depth(domain.get("id")) > depth(current):
                zone_domain[zone.get("id")] = domain.get("id")

    errors = []
    if failed:
        errors.append(
            f"Zone hierarchy: zones of {len(failed)} domain(s) couldn't be listed "
            f"({', '.join(failed[:5])}{', ...' if len(failed) > 5 else ''}); "
            "their zones are under \"Domain not found\""
        )
    return domains, zone_domain, errors


def build_zone_tree(
    domains: List[Dict[str, Any]],
    zone_domain: Dict[str, str],
    by_zone_id: Dict[str, Dict[str, Any]],
) -> List[BasicDomainNode]:
    """Domain tree holding only zones with an AP online, and the domains above them"""
    nodes = {
        d.get("id"): BasicDomainNode(domain_id=d.get("id"), name=d.get("name") or d.get("id"))
        for d in domains
    }
    parents = {d.get("id"): d.get("parentDomainId") for d in domains}

    for zone_id, counts in by_zone_id.items():
        if counts["connected"] <= 0:
            continue
        domain_id = zone_domain.get(zone_id)
        if domain_id not in nodes:
            domain_id = UNKNOWN_DOMAIN_ID
            nodes.setdefault(domain_id, BasicDomainNode(domain_id=domain_id, name="Domain not found"))
        nodes[domain_id].zones.append(BasicZoneNode(
            zone_id=zone_id,
            name=counts["name"] or zone_id,
            aps_online=counts["connected"],
            aps_total=counts["total"],
            clients=counts["clients"],
        ))

    roots = []
    for domain_id, node in nodes.items():
        parent = parents.get(domain_id)
        if parent in nodes and parent != domain_id:
            nodes[parent].children.append(node)
        else:
            roots.append(node)

    def prune(node: BasicDomainNode, seen: set) -> bool:
        """Sum counts upward and drop branches without an online zone"""
        seen.add(node.domain_id)
        node.children = [c for c in node.children if c.domain_id not in seen and prune(c, seen)]
        node.zones.sort(key=lambda z: z.name.lower())
        node.children.sort(key=lambda c: c.name.lower())
        for item in [*node.zones, *node.children]:
            node.aps_online += item.aps_online
            node.aps_total += item.aps_total
            node.clients += item.clients
        return bool(node.zones or node.children)

    seen: set = set()
    kept = [r for r in roots if prune(r, seen)]
    return sorted(kept, key=lambda r: (r.domain_id == UNKNOWN_DOMAIN_ID, r.name.lower()))


async def fetch_basic_audit(sz_client: SZClient, controller: Controller) -> BasicAuditResult:
    """Read one controller's licenses and counts. The client must be logged in."""
    result = BasicAuditResult(
        controller_id=controller.id,
        controller_name=controller.name,
        host=controller.sz_host,
    )

    summary, entitlements, devices, ap_online, switch_counts, zone_domains = await asyncio.gather(
        sz_client.system.get_licenses_summary(),
        sz_client.system.get_licenses(),
        sz_client.system.get_devices_summary(),
        sz_client.system.get_connected_ap_count(),
        sz_client.switches.get_switch_status_counts(),
        fetch_zone_domains(sz_client),
        return_exceptions=True,
    )

    if isinstance(summary, Exception):
        result.errors.append(f"License usage: {summary}")
    else:
        result.licenses = build_license_usage(summary)
        result.ap_license = find_license(result.licenses, AP_LICENSE_TYPE)
        result.switch_license = find_license(result.licenses, SWITCH_LICENSE_TYPE)

    if isinstance(entitlements, Exception):
        result.errors.append(f"License list: {entitlements}")
    else:
        result.entitlements = build_entitlements(entitlements)

    # totalAps/totalSwitches are documented as "connected" but are the real
    # totals: a controller with every AP offline still reports all of them.
    if isinstance(devices, Exception):
        result.errors.append(f"Device totals: {devices}")
    else:
        result.total_aps = devices.get("totalAps")
        result.total_switches = devices.get("totalSwitches")

    if isinstance(ap_online, Exception):
        result.errors.append(f"AP online count: {ap_online}")
    else:
        result.aps_online = ap_online["connected"]
        result.total_zones = ap_online["zones_total"]
        result.zones_with_online_aps = ap_online["zones_with_connected"]
        if ap_online["zones_read"] < ap_online["zones_total"]:
            result.errors.append(
                f"AP online count covers {ap_online['zones_read']} of "
                f"{ap_online['zones_total']} zones, so it may be low"
            )

    # The tree needs both: per-zone counts from inventory, placement from domains
    if isinstance(zone_domains, Exception):
        result.errors.append(f"Zone hierarchy: {zone_domains}")
    elif not isinstance(ap_online, Exception):
        domains, zone_domain, tree_errors = zone_domains
        result.errors.extend(tree_errors)
        result.zone_tree = build_zone_tree(domains, zone_domain, ap_online["by_zone_id"])

    if isinstance(switch_counts, Exception):
        result.errors.append(f"Switch online count: {switch_counts}")
    else:
        result.switches_online = switch_counts["online"]
        result.switch_groups = switch_counts["groups"]
        result.switch_groups_with_online = switch_counts["groups_with_online"]
        if switch_counts["read"] < switch_counts["total"]:
            result.errors.append(
                f"Switch online count covers {switch_counts['read']} of "
                f"{switch_counts['total']} switches, so it may be low"
            )

    return result
