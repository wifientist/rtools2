"""
R1 deprecation watch: what R1 has deprecated, or says it will remove, among
the calls our tools make. See services/r1_deprecation_audit.py.

Reads only Redis, the code and the spec file; nothing here calls R1.
"""

import asyncio
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from constants.roles import role_hierarchy
from decorators import require_role
from dependencies import get_current_user
from models.user import RoleEnum, User
from r1api import deprecation_watch
from services import r1_deprecation_audit as audit

router = APIRouter(prefix="/r1-deprecations", tags=["R1 Deprecations"])

# Who sees the banner. "admin" (admins and supers) for now; set to "user" to
# show everyone, who then get "tell your admin" instead of the details.
BANNER_ROLE = os.getenv("R1_DEPRECATION_BANNER_ROLE", "admin")
BANNER_SEVERITIES = ("critical", "high")
BANNER_ITEMS = 12


async def _report() -> Dict[str, Any]:
    report = await asyncio.to_thread(audit.load_report)
    if report is None:
        report = await asyncio.to_thread(audit.run_audit)
        await asyncio.to_thread(audit.save_report, report)
    return report


def _live_items() -> List[Dict[str, Any]]:
    """What R1 has said on live responses, newest first."""
    items = []
    for label, row in deprecation_watch.read_seen().items():
        if row.get("gone_status"):
            severity, why = "critical", f"R1 answered {row['gone_status']}: gone"
        elif row.get("replacement") or row.get("sunset") or row.get("deprecation"):
            severity, why = "high", "R1 flags it deprecated"
        else:
            severity, why = "medium", f"R1's current media type is {row.get('current_version')}"
        method, path = label.split(" ", 1)
        settled = audit.settled_note(method, path, row)
        if settled:
            # Kept for the report's history, never for the banner.
            severity, why = "info", f"{why}; {settled}"
        items.append({
            "severity": severity, "method": method, "path": path, "why": why,
            "replacement": row.get("replacement"), "last_seen": row.get("last_seen"),
            "source": "live",
        })
    items.sort(key=lambda i: i.get("last_seen") or "", reverse=True)
    return items


@router.get("/banner")
async def deprecation_banner(current_user: User = Depends(get_current_user)):
    """The few calls worth a banner. Empty for roles below BANNER_ROLE."""
    level = role_hierarchy.get(current_user.role.value, 0)
    if level < role_hierarchy.get(BANNER_ROLE, 999):
        return {"items": [], "total": 0, "audience": "none"}
    is_admin = level >= role_hierarchy.get(RoleEnum.admin.value, 999)

    report = await _report()
    live = await asyncio.to_thread(_live_items)
    items: Dict[str, Dict[str, Any]] = {}
    for item in live:
        if item["severity"] in BANNER_SEVERITIES:
            items[f"{item['method']} {item['path'].replace('{id}', '{x}')}"] = item
    for f in report.get("findings", []):
        key = f"{f['method']} {f['path']}"
        if f["severity"] in BANNER_SEVERITIES and key not in items:
            items[key] = {
                "severity": f["severity"], "method": f["method"], "path": f["path"],
                "why": f["reasons"][0] if f["reasons"] else "", "replacement": f.get("replacement"),
                "days_left": f.get("days_left"), "source": "spec",
            }
    ordered = sorted(
        items.values(),
        key=lambda i: (audit.SEVERITY_ORDER[i["severity"]], i["source"] != "live", i["path"]),
    )
    return {
        "audience": "admin" if is_admin else "user",
        "total": len(ordered),
        "critical": sum(1 for i in ordered if i["severity"] == "critical"),
        "items": ordered[:BANNER_ITEMS] if is_admin else [
            {"method": i["method"], "path": i["path"], "severity": i["severity"]} for i in ordered[:BANNER_ITEMS]
        ],
        "generated_at": report.get("generated_at"),
        "spec_file": report.get("spec_file"),
        "spec_stale": report.get("spec_stale"),
    }


@router.get("/report")
@require_role(RoleEnum.admin)
async def deprecation_report(current_user: User = Depends(get_current_user)):
    """The full audit, plus everything R1 has flagged live."""
    report = await _report()
    return {**report, "live": await asyncio.to_thread(_live_items)}


@router.post("/run")
@require_role(RoleEnum.super)
async def run_deprecation_audit(current_user: User = Depends(get_current_user)):
    """Run the audit now (no R1 calls) and store it."""
    report = await asyncio.to_thread(audit.run_audit)
    saved = await asyncio.to_thread(audit.save_report, report)
    return {**report, "live": await asyncio.to_thread(_live_items), "changed": len(saved["changed"])}
