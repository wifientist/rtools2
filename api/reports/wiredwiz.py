"""
WiredWiz report data source.

Builds the template context for the findings + check-catalogue PDF from what is
already stored — the last health run, the latest snapshot and the check registry.
It performs no API calls and re-runs no analysis, so exporting is cheap and the
PDF matches exactly what was on screen.

Deliberately NOT registered in reports.REPORT_REGISTRY: that registry drives
scheduled, emailed reports, and WiredWiz stays human-triggered. This is exported
on demand from the UI.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.wiredwiz import store
from services.wiredwiz.checks import REGISTRY as CHECK_REGISTRY

logger = logging.getLogger(__name__)

SEVERITY_ORDER = ["critical", "warning", "info"]

# Caveats that materially change how a reader should treat the numbers. These
# are conclusions the tool reached about its own inputs, and printing them beside
# the findings is the difference between a report and a list of assertions.
STANDING_CAVEATS = [
    ("Byte counters are not usable for throughput",
     "RUCKUS ONE's rx/tx byte counters do not increment on the cadence they are read. "
     "Measured across 4,653 ports, 96% showed a tx-implied utilisation more than five "
     "times what R1's own signalOut reported. No figure in this report is derived from "
     "byte deltas; utilisation comes from signalIn/signalOut and everything else from "
     "packet counters."),
    ("There is no port-flap history in the API",
     "R1 exposes no flap log, so port transitions here are reconstructed by comparing "
     "snapshots. The real transition count between two samples may be higher than shown."),
    ("Port counters refresh about every 5 minutes",
     "Rates are only computed over a window long enough to span several refreshes. A "
     "shorter window turns a single refresh tick into a rate that is far too high."),
    ("OFFLINE means 'not reporting to the cloud'",
     "It does not mean the switch is down. Where uptime is intact the device did not "
     "reboot and is most likely still forwarding."),
    ("MAC table capacity is assumed, not read",
     "No API field reports the hardware forwarding-table size. Where a capacity is "
     "quoted it comes from the documented default forwarding profile; confirm with "
     "`show forwarding-profile` on the switch."),
    ("Config checks target FastIron 10.0.x",
     "Command syntax differs between FastIron trains. Switches outside 10.0.x are "
     "flagged by the config-syntax-scope check and their config findings should be "
     "treated as unverified."),
]


def build_context(tenant_key: str, tenant_label: str, controller_name: str,
                  venue_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Assemble everything the template needs. Raises ValueError when there is
    nothing to export yet.
    """
    health = store.load_health(tenant_key)
    if not health:
        raise ValueError("No check run stored yet — run the checks before exporting.")

    snapshot = store.load(tenant_key)
    baselines = store.list_baselines(tenant_key)
    snapshots = store.list_snapshots(tenant_key)

    findings = health.get("findings", [])
    ran = {x["checkId"]: x for x in health.get("checksRun", [])}
    skipped = {x["checkId"]: x for x in health.get("checksSkipped", [])}
    failed = {x["checkId"]: x for x in health.get("checksFailed", [])}

    # Findings grouped by severity, then category, preserving the ranked order.
    by_severity = []
    for sev in SEVERITY_ORDER:
        rows = [f for f in findings if f.get("severity") == sev]
        if not rows:
            continue
        cats = defaultdict(list)
        for f in rows:
            cats[f.get("category", "other")].append(f)
        by_severity.append({
            "severity": sev,
            "count": len(rows),
            "categories": [{"category": c, "findings": v} for c, v in sorted(cats.items())],
        })

    # Catalogue: every registered check with what happened to it this run.
    catalogue = defaultdict(list)
    for chk in CHECK_REGISTRY:
        if chk.id in ran:
            status, note, n = "ran", None, ran[chk.id]["findings"]
        elif chk.id in skipped:
            status, note, n = "skipped", skipped[chk.id].get("reason"), None
        elif chk.id in failed:
            status, note, n = "errored", failed[chk.id].get("error"), None
        else:
            status, note, n = "not run", None, None
        catalogue[chk.category].append({
            "id": chk.id, "title": chk.title, "needs": chk.needs,
            "summary": chk.summary, "trigger": chk.trigger,
            "status": status, "note": note, "findings": n,
        })
    catalogue_sorted = [{"category": c, "checks": sorted(v, key=lambda x: x["id"])}
                        for c, v in sorted(catalogue.items())]

    scope = health.get("scope") or {}
    ctx = health.get("context") or {}
    venues = (snapshot or {}).get("venues") or {}
    scoped_ids = scope.get("venueIds") or list(venues)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        "controller_name": controller_name,
        "tenant_label": tenant_label,
        "ran_at": _fmt(health.get("ranAt")),
        "counts": health.get("counts", {}),
        "total_findings": len(findings),
        "context": ctx,
        "scope": scope,
        "venues": [{"id": vid, "name": venues.get(vid, vid)} for vid in scoped_ids],
        "config_audit": health.get("configAudit") or {},
        "by_severity": by_severity,
        "catalogue": catalogue_sorted,
        "checks_total": len(CHECK_REGISTRY),
        "checks_ran": len(ran),
        "checks_skipped": len(skipped),
        "checks_failed": len(failed),
        "skipped_list": sorted(skipped.values(), key=lambda x: x["checkId"]),
        "category_counts": dict(Counter(f.get("category") for f in findings).most_common()),
        "snapshots": snapshots[-8:],
        "baseline": baselines[-1] if baselines else None,
        "caveats": STANDING_CAVEATS,
    }


def _fmt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime(
            "%d %b %Y, %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return iso


def findings_csv(tenant_key: str) -> str:
    """
    Findings as CSV, for the cases a PDF is the wrong shape — pasting into a
    tracker, sorting by switch, handing a list to whoever does the patching.
    """
    import csv
    import io

    health = store.load_health(tenant_key)
    if not health:
        raise ValueError("No check run stored yet — run the checks before exporting.")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["severity", "category", "check_id", "entity", "title", "confidence",
                "detail", "remediation", "evidence"])
    for f in health.get("findings", []):
        w.writerow([
            f.get("severity"), f.get("category"), f.get("checkId"), f.get("entity"),
            f.get("title"), f.get("confidence"),
            " ".join((f.get("detail") or "").split()),
            " ".join((f.get("remediation") or "").split()),
            "; ".join(f"{k}={v}" for k, v in (f.get("evidence") or {}).items()),
        ])
    return buf.getvalue()
