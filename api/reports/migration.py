"""
Migration Dashboard report data source.

Fetches dashboard data and transforms it into template context
for the migration report PDF.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "1_01_NeverContactedCloud": "Never Contacted Cloud",
    "1_07_Initializing": "Initializing",
    "1_09_Offline": "Offline (Setup)",
    "2_00_Operational": "Operational",
    "2_01_ApplyingFirmware": "Applying Firmware",
    "2_02_ApplyingConfiguration": "Applying Configuration",
    "3_02_FirmwareUpdateFailed": "Firmware Update Failed",
    "3_03_ConfigurationUpdateFailed": "Config Update Failed",
    "3_04_DisconnectedFromCloud": "Disconnected from Cloud",
    "4_01_Rebooting": "Rebooting",
    "4_04_HeartbeatLost": "Heartbeat Lost",
}

STATUS_COLORS = {
    "1_": "#6b7280",  # gray
    "2_": "#16a34a",  # green
    "3_": "#dc2626",  # red
    "4_": "#d97706",  # amber
}


def _get_message(pct: float) -> str:
    if pct >= 100: return "All APs have entered the building!"
    if pct >= 95: return "Can we just round up and call it done?"
    if pct >= 90: return "The last few are just being dramatic."
    if pct >= 85: return "SZ is starting to feel lonely."
    if pct >= 80: return "4 out of 5 APs recommend R1!"
    if pct >= 75: return "Three quarters baked!"
    if pct >= 70: return "SZ is losing this breakup badly."
    if pct >= 65: return "Two-thirds done — no turning back now!"
    if pct >= 60: return "The APs are voting with their feet."
    if pct >= 55: return "More APs in R1 than not!"
    if pct >= 50: return "Perfectly balanced... for now."
    if pct >= 45: return "Almost halfway — can you feel it?"
    if pct >= 40: return "SZ is starting to sweat."
    if pct >= 35: return "Over a third — past the point of no return!"
    if pct >= 30: return "R1 is becoming the cool kids' club."
    if pct >= 25: return "A quarter down, three quarters to go!"
    if pct >= 20: return "1 of 5 APs agree, R1 is the place to be!"
    if pct >= 15: return "The trickle is becoming a stream!"
    if pct >= 10: return "Double digits — now we're cooking!"
    if pct >= 5: return "First APs are settling into their new home!"
    return "The great migration begins!"


def _get_period_delta(snapshots, days: int):
    """Compute delta between latest snapshot and the one closest to `days` ago."""
    if len(snapshots) < 2:
        return None
    latest = snapshots[-1]
    cutoff = datetime.utcnow() - timedelta(days=days)
    baseline = min(snapshots, key=lambda s: abs(s.captured_at - cutoff))
    baseline_age = (datetime.utcnow() - baseline.captured_at).total_seconds() / 86400
    if baseline_age < days * 0.5:
        return None
    return {
        "aps": latest.total_aps - baseline.total_aps,
        "operational": latest.operational_aps - baseline.operational_aps,
        "venues": latest.total_venues - baseline.total_venues,
        "clients": latest.total_clients - baseline.total_clients,
    }


def _format_delta(val: int) -> str:
    if val == 0:
        return "\u2014"
    prefix = "+" if val > 0 else ""
    return f"{prefix}{val:,}"


def _delta_color(val: int) -> str:
    if val > 0:
        return "#16a34a"
    if val < 0:
        return "#dc2626"
    return "#9ca3af"


async def fetch_report_data(context_id: str, db: Session) -> dict:
    """
    Fetch migration dashboard data and return template context.

    Args:
        context_id: Controller ID as a string
        db: Database session

    Returns:
        Dictionary of template variables for reports/migration.html
    """
    from routers.migration_dashboard import fetch_controller_progress
    from models.controller import Controller
    from models.migration_dashboard_snapshot import MigrationDashboardSnapshot

    controller_id = int(context_id)
    progress_data, tenants, settings_data = await fetch_controller_progress(
        controller_id, db
    )

    controller = db.query(Controller).get(controller_id)
    controller_name = controller.name if controller else f"Controller {controller_id}"

    target_aps = settings_data.get("target_aps", 180000)
    total_aps = progress_data["total_aps"]
    operational = progress_data.get("status_summary", {}).get("operational", 0)
    offline = progress_data.get("status_summary", {}).get("offline", 0)
    percentage = (total_aps / target_aps * 100) if target_aps > 0 else 0

    # Quip message
    message = _get_message(percentage)

    # Compute per-tenant share percentage
    all_aps = sum(t["ap_count"] for t in tenants if not t.get("ignored"))
    for t in tenants:
        t["share_pct"] = (t["ap_count"] / all_aps * 100) if all_aps > 0 else 0

    # Flatten venues from non-ignored tenants
    venues = []
    for t in tenants:
        if t.get("ignored"):
            continue
        for v in t.get("venue_stats", []):
            pct = (v["operational"] / v["ap_count"] * 100) if v["ap_count"] > 0 else 0
            venues.append({
                "venue_name": v["venue_name"],
                "tenant_name": t["name"],
                "ap_count": v["ap_count"],
                "operational": v["operational"],
                "offline": v["offline"],
                "pct": pct,
            })
    venues.sort(key=lambda v: v["ap_count"], reverse=True)

    # AP Status Breakdown — transform raw codes into display-friendly list
    raw_status_counts = progress_data.get("status_counts", {})
    status_breakdown = []
    for code in sorted(raw_status_counts.keys()):
        count = raw_status_counts[code]
        label = STATUS_LABELS.get(code, code)
        prefix = code[:2]
        color = STATUS_COLORS.get(prefix, "#6b7280")
        status_breakdown.append({"label": label, "count": count, "color": color})

    # 30/60/90 day deltas from snapshots
    cutoff = datetime.utcnow() - timedelta(days=95)
    snapshots = (
        db.query(MigrationDashboardSnapshot)
        .filter(
            MigrationDashboardSnapshot.controller_id == controller_id,
            MigrationDashboardSnapshot.captured_at >= cutoff,
        )
        .order_by(MigrationDashboardSnapshot.captured_at.asc())
        .all()
    )

    period_cards = []
    for days, label in [(30, "Last 30 Days"), (60, "Last 60 Days"), (90, "Last 90 Days")]:
        delta = _get_period_delta(snapshots, days)
        if delta:
            period_cards.append({
                "label": label,
                "rows": [
                    {"label": "APs", "value": _format_delta(delta["aps"]), "color": _delta_color(delta["aps"])},
                    {"label": "Venues", "value": _format_delta(delta["venues"]), "color": _delta_color(delta["venues"])},
                    {"label": "Clients", "value": _format_delta(delta["clients"]), "color": _delta_color(delta["clients"])},
                ],
            })

    return {
        "controller_name": controller_name,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "total_aps": total_aps,
        "target_aps": target_aps,
        "percentage": percentage,
        "message": message,
        "operational": operational,
        "offline": offline,
        "total_switches": progress_data.get("total_switches", 0),
        "total_venues": progress_data["total_venues"],
        "total_clients": progress_data.get("total_clients", 0),
        "tenants": tenants,
        "venues": venues,
        "status_breakdown": status_breakdown,
        "period_cards": period_cards,
    }
