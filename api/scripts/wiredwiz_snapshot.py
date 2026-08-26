"""
WiredWiz: take one read-only snapshot of a tenant's switching estate.

Thin CLI wrapper over services.wiredwiz -- the API router runs the exact same
code, so the dashboard and the terminal can never disagree.

Two snapshots are what the analyzer needs: R1 exposes no flap history, its port
counters are cumulative, and it refreshes them only about every 300s. Rates only
exist as differences, and only over a window comfortably longer than that.

READ-ONLY. GETs and `*/query` POSTs only. Never creates a config backup, pushes
CLI, reboots, or syncs.

RUN IT WHEN YOU WANT A SNAPSHOT. This script takes exactly one and exits -- it
has no loop, no daemon mode and no scheduler hook. Do not wrap it in cron; if
you want a second sample, run it again.

Configuration is not collected. To read one switch's redacted config, open that
switch in the UI (Helpers -> WiredWiz -> Inventory), which fetches that device
alone.

Usage:
    docker compose exec backend python scripts/wiredwiz_snapshot.py <controller_id> [--tenant ID]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller
from services.wiredwiz import store
from services.wiredwiz.crawl import take_snapshot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("controller_id", type=int)
    ap.add_argument("--tenant", help="EC tenant id (required for MSP controllers)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(args.controller_id, db)
    finally:
        db.close()

    # MSP controllers address an EC via the override header; ECs address themselves.
    override = args.tenant if r1.ec_type == "MSP" else None
    key = args.tenant or r1.tenant_id
    if r1.ec_type == "MSP" and not override:
        sys.exit("This is an MSP controller — pass --tenant <ec_tenant_id>. "
                 "The MSP account itself owns no switches.")

    print(f"controller={args.controller_id} tenant={key}")
    snap = take_snapshot(r1, override, progress=lambda m: print(f"  {m}"))

    comp = snap["completeness"]
    if comp["incomplete"]:
        print(f"  !! INCOMPLETE CRAWL: {comp['incomplete']} of {comp['queries']} queries "
              f"came up short ({comp['collected']} of {comp['expected']} rows). "
              f"Do NOT diff this snapshot — re-run it.")
        for c in comp["shortfalls"][:10]:
            print(f"     {c['path']} {c['filters']}: {c['collected']}/{c['expected']}")
    else:
        print(f"  crawl complete: {comp['collected']} rows across {comp['queries']} "
              f"queries, no shortfalls")

    name = store.save(key, snap)
    path = store.SNAPSHOT_DIR / key / name
    print(f"  wrote {path} ({path.stat().st_size / 1_048_576:.1f} MB) "
          f"in {snap['elapsedSeconds']}s")
    print(f"\nnext: take another in ~15 minutes, then\n"
          f"  docker compose exec backend python scripts/wiredwiz_analyze.py {key}")


if __name__ == "__main__":
    main()
