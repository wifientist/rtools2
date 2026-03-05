"""
One-off script to populate venue_migration_history from known migration dates.

Run inside the backend container:
    docker compose exec backend python scripts/backfill_venue_data.py

Fetches the current venue list from R1 (for IDs, tenant mappings, AP counts),
then inserts a row per venue with transition dates based on known migration
windows and current live status.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from datetime import datetime, date, timedelta

from database import SessionLocal
from models.venue_migration_history import VenueMigrationHistory

CONTROLLER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 23

# Known migration window start dates (Pending → In Progress)
MIGRATION_DATES = {
    "Rue Ferrari": date(2025, 12, 3),
    "McGregor Square": date(2026, 1, 13),
    "Sentral Union Station": date(2026, 1, 27),
    "Pura Vida": date(2026, 2, 10),
    "Compark Village (Alder)": date(2026, 2, 11),
    "Overture Proscenium": date(2026, 2, 11),
    "The Mera": date(2026, 2, 11),
    "The Farm": date(2026, 2, 24),
    "Ascend at Little Valley": date(2026, 2, 24),
    "16 Tech Complex": date(2026, 2, 26),
    "Emblem Cane Bay": date(2026, 2, 26),
    "ONA": date(2026, 3, 2),
    "85 Cleveland": date(2026, 3, 2),
    "Axis at Davis": date(2026, 3, 2),
    "Novell": date(2026, 3, 2),
    "1510 Webster St": date(2026, 3, 3),
    "Avalon Pleasanton": date(2026, 3, 3),
    "Allaso Olivine": date(2026, 3, 3),
    "Eames (Arden Gateway Village)": date(2026, 3, 3),
    "Allaso Peak": date(2026, 3, 3),
}


def _match_migration_date(venue_name: str):
    """Try to match a venue name to a migration date (fuzzy substring match)."""
    if venue_name in MIGRATION_DATES:
        return MIGRATION_DATES[venue_name]
    name_lower = venue_name.lower()
    for known_name, mig_date in MIGRATION_DATES.items():
        if known_name.lower() in name_lower or name_lower in known_name.lower():
            return mig_date
    return None


async def main():
    from routers.migration_dashboard import fetch_controller_progress, _compute_venue_status

    db = SessionLocal()
    try:
        # 1. Fetch current live venue data
        print(f"Fetching live data for controller {CONTROLLER_ID}...")
        progress_data, tenants, _ = await fetch_controller_progress(CONTROLLER_ID, db)

        # Build current venue list
        current_venues = []
        for t in tenants:
            if t.get("ignored"):
                continue
            for v in t.get("venue_stats", []):
                current_venues.append({
                    "venue_id": v["venue_id"],
                    "venue_name": v["venue_name"],
                    "tenant_id": t["id"],
                    "tenant_name": t["name"],
                    "ap_count": v.get("ap_count", 0),
                    "operational": v.get("operational", 0),
                })

        print(f"Found {len(current_venues)} venues in live data\n")

        # 2. Clear any existing rows for this controller (fresh backfill)
        deleted = (
            db.query(VenueMigrationHistory)
            .filter(VenueMigrationHistory.controller_id == CONTROLLER_ID)
            .delete()
        )
        if deleted:
            print(f"Cleared {deleted} existing venue history rows")

        # 3. Insert rows with known migration dates
        now = datetime.utcnow()
        inserted = 0
        for v in current_venues:
            mig_date = _match_migration_date(v["venue_name"])
            live_status = _compute_venue_status(v["ap_count"], v["operational"])

            if mig_date:
                # Known migration window — use provided date
                pending_dt = datetime.combine(mig_date - timedelta(days=1), datetime.min.time())
                in_progress_dt = datetime.combine(mig_date, datetime.min.time())
                migrated_dt = in_progress_dt if live_status == "Migrated" else None
                status = live_status
                label = f"mig={mig_date}"
            else:
                # Unknown — use current live status with now as pending_at
                pending_dt = now
                in_progress_dt = now if live_status in ("In Progress", "Migrated") else None
                migrated_dt = now if live_status == "Migrated" else None
                status = live_status
                label = "no known date, using live status"

            row = VenueMigrationHistory(
                controller_id=CONTROLLER_ID,
                venue_id=v["venue_id"],
                venue_name=v["venue_name"],
                tenant_id=v["tenant_id"],
                tenant_name=v["tenant_name"],
                ap_count=v["ap_count"],
                operational=v["operational"],
                status=status,
                pending_at=pending_dt,
                in_progress_at=in_progress_dt,
                migrated_at=migrated_dt,
            )
            db.add(row)
            inserted += 1
            print(f"  {v['venue_name']}: {status} ({label})")

        db.commit()

        # Summary
        statuses = {}
        for v in current_venues:
            s = _compute_venue_status(v["ap_count"], v["operational"])
            statuses[s] = statuses.get(s, 0) + 1
        matched = sum(1 for v in current_venues if _match_migration_date(v["venue_name"]))

        print(f"\nInserted {inserted} venue history rows")
        print(f"Matched {matched}/{len(current_venues)} to known migration dates")
        print(f"Status breakdown: {statuses}")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
