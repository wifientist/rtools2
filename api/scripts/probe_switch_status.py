"""
Probe: what deviceStatus values do R1 switches actually return?

The OpenAPI spec defines `deviceStatus` on the switch DTO but documents no enum.
We need the real distribution to bucket switches into operational/offline for the
migration dashboard (mirroring the AP status logic). This script queries a handful
of EC tenants' switches with `deviceStatus` and prints the distinct values + counts.

Read-only. Usage:
    docker compose exec backend python scripts/probe_switch_status.py <controller_id>
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller


async def probe(controller_id: int):
    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
        ecs_response = await r1.msp.get_msp_ecs()
        ec_list = ecs_response.get("data", [])
        print(f"Found {len(ec_list)} EC tenants for controller={controller_id}")

        status_counter: Counter = Counter()
        sample = None
        tenants_with_switches = 0
        checked = 0

        for ec in ec_list:
            tenant_id = ec.get("id") or ec.get("tenantId")
            if not tenant_id:
                continue
            checked += 1
            resp = r1.post(
                "/venues/switches/query",
                payload={
                    "fields": ["serialNumber", "deviceStatus", "model", "venueId"],
                    "page": 0,
                    "pageSize": 1000,
                },
                override_tenant_id=tenant_id,
            )
            if not resp.ok:
                continue
            body = resp.json()
            rows = body.get("data", []) or []
            total = body.get("totalCount", 0)
            if total:
                tenants_with_switches += 1
            for row in rows:
                status_counter[row.get("deviceStatus", "<missing>")] += 1
                if sample is None:
                    sample = row
            # Stop once we have a decent sample across multiple tenants
            if tenants_with_switches >= 5 and sum(status_counter.values()) >= 200:
                break

        print(f"\nChecked {checked} tenants; {tenants_with_switches} had switches.")
        print("\n===== distinct deviceStatus values (count) =====")
        for status, count in status_counter.most_common():
            print(f"  {status!r}: {count}")
        print("\n===== sample switch row =====")
        print(json.dumps(sample, indent=2, default=str)[:2000])
    finally:
        db.close()


if __name__ == "__main__":
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(probe(cid))
