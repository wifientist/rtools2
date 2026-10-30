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
from typing import Any, Dict, List, Optional

from models.controller import Controller
from schemas.sz_audit import BasicAuditResult, LicenseEntitlement, LicenseUsage
from szapi.client import SZClient

logger = logging.getLogger(__name__)

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


async def fetch_basic_audit(sz_client: SZClient, controller: Controller) -> BasicAuditResult:
    """Read one controller's licenses and counts. The client must be logged in."""
    result = BasicAuditResult(
        controller_id=controller.id,
        controller_name=controller.name,
        host=controller.sz_host,
    )

    summary, entitlements, devices, ap_online, switch_counts = await asyncio.gather(
        sz_client.system.get_licenses_summary(),
        sz_client.system.get_licenses(),
        sz_client.system.get_devices_summary(),
        sz_client.system.get_connected_ap_count(),
        sz_client.switches.get_switch_status_counts(),
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
