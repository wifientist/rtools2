"""
Take one topology snapshot from the CLI.

Reads and writes exactly what the API does -- same modules, same store, same
files -- so a snapshot taken here is visible in the UI and vice versa.

Phase 1 checkpoint: the device and port counts this prints must match WiredWiz's
inventory for the same venue. Two independent readers of the same network
agreeing is the cheapest strong validation available; a disagreement means one
of them is wrong.

Usage:
    docker compose exec backend python scripts/topology_snapshot.py <controller_id> \\
        --tenant <ec-id> --venue <venue-id> [--venue <venue-id> ...] [--deep] [--no-save]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.r1_client import create_r1_client_from_controller
from database import SessionLocal
from models.controller import Controller
from services.topology import collect as topo_collect
from services.topology import store


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("controller_id", type=int)
    parser.add_argument("--tenant")
    parser.add_argument("--venue", action="append", dest="venues")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    controller = db.query(Controller).filter(Controller.id == args.controller_id).first()
    if not controller:
        sys.exit(f"No controller {args.controller_id}")
    if controller.controller_type != "RuckusONE":
        sys.exit(f"Controller {controller.id} is {controller.controller_type}; R1 only.")

    if controller.controller_subtype == "MSP":
        if not args.tenant:
            sys.exit("MSP controller: pass --tenant <ec-tenant-id>")
        override_tenant = storage_key = args.tenant
    else:
        override_tenant = None
        storage_key = controller.r1_tenant_id or f"controller-{controller.id}"

    r1 = create_r1_client_from_controller(controller.id, db)
    if getattr(r1, "auth_failed", False):
        sys.exit(f"auth failed: {getattr(r1, 'auth_error', '?')}")

    venues = args.venues
    if not venues:
        rows = r1.switches.list_switches(override_tenant) or []
        counts = {}
        for row in rows:
            counts[row.get("venueId")] = counts.get(row.get("venueId"), 0) + 1
        if not counts:
            sys.exit("No switches under this tenant; nothing to map.")
        venues = [max(counts, key=counts.get)]
        print(f"no --venue given; using the busiest: {venues[0]} ({counts[venues[0]]} switches)")

    print(f"controller={controller.name} tenant={storage_key} venues={venues} deep={args.deep}")
    snapshot = await topo_collect.discover(r1, override_tenant, venues, deep=args.deep)

    if args.json:
        print(json.dumps(snapshot.meta(), indent=1, default=str))
    else:
        print(f"\nelapsed: {snapshot.elapsed_seconds}s")
        print("counts:", json.dumps(snapshot.counts()))
        print("\nsources:")
        for source in snapshot.sources:
            flag = "!!" if source["status"] == "error" else "  "
            print(f" {flag} {source['id']:<28} {source['rows']:>6} rows "
                  f"{source['elapsedMs']:>6}ms {source.get('error') or source.get('note') or ''}")
        if snapshot.warnings:
            print("\nwarnings:")
            for warning in snapshot.warnings:
                print(f"  - {warning}")
        print("\ncompleteness:", json.dumps(snapshot.completeness, default=str)[:300])

        by_kind = {}
        for device in snapshot.devices:
            by_kind.setdefault(device.kind, []).append(device)
        for kind, rows in sorted(by_kind.items()):
            sample = rows[0]
            print(f"\n{kind}: {len(rows)}  e.g. {sample.id} "
                  f"'{sample.display_name}' status={sample.status}")

        raw = getattr(snapshot, "raw", None)
        if raw is not None:
            print(f"\nR1 /topologies: {len(raw.r1_nodes)} nodes, {len(raw.r1_edges)} edges "
                  f"(mesh: {len(raw.mesh_edges)})")

    if args.no_save:
        print("\n--no-save: nothing written")
        return
    name = store.save(storage_key, snapshot)
    print(f"\nsaved {name} under {store.DATA_DIR}/{storage_key}/"
          f"{store.scope_key(venues)}/")


if __name__ == "__main__":
    asyncio.run(main())
