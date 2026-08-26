"""
Probe: why does PISR think an AP group is empty when RUCKUS ONE shows members?

PISR counts APs per group by matching each AP's `apGroupId` against the
`apGroupId` an SSID activation names. On a per-unit-SSID property that join has
been seen to return zero for every group while the console clearly shows two
APs in each — and because the group NAME still renders correctly, the
activation side of the join is fine and the AP side is the half that failed.

This script takes that join apart on a real venue and prints which half breaks:

  1. How many APs the venue query returns, and whether it truncated.
  2. How many of those APs report an `apGroupId` at all, and an `apGroupName`.
  3. The venue's AP groups, and whether the ids the APs claim are among them.
  4. For one named group, every AP that should be in it by any measure.
  5. The same AP list fetched a second way (tenant-wide, filtered locally) to
     show whether the venue filter is dropping rows.

Usage:
    docker compose exec backend python scripts/probe_ap_group_membership.py \\
        <controller_id> <venue_id_or_name> [ap_group_name] [--tenant <tenant_id>]

`--tenant` is required for an MSP controller; without it every call runs in
MSP scope and the venue will not be found.

Example:
    docker compose exec backend python scripts/probe_ap_group_membership.py \\
        14 "The Ross" "1-1001@The_ross"
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller
from services.pisr import fetch

SEP = "=" * 78


def resolve_venue(r1, wanted, tenant):
    venues = fetch.venue_rows(r1, tenant)
    for v in venues:
        if v.get("id") == wanted or (v.get("name") or "").lower() == wanted.lower():
            return v
    print(f"No venue matched {wanted!r}. Available:")
    for v in venues[:40]:
        print(f"   {v.get('id')}  {v.get('name')}")
    sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    argv = list(sys.argv[1:])
    tenant = None
    if "--tenant" in argv:
        i = argv.index("--tenant")
        tenant = argv[i + 1]
        del argv[i:i + 2]
    controller_id = int(argv[0])
    wanted_venue = argv[1]
    wanted_group = argv[2] if len(argv) > 2 else None

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    venue = resolve_venue(r1, wanted_venue, tenant)
    vid = venue["id"]
    print(f"venue: {venue.get('name')}  ({vid})")

    # ── 1. the raw AP query, with its own totalCount ──────────
    print(SEP)
    print("STEP 1  /venues/aps/query for this venue")
    body = {"fields": ["name", "serialNumber", "status", "apGroupId", "apGroupName", "venueId"],
            "filters": {"venueId": [vid]}, "page": 0, "pageSize": 10000}
    resp = fetch._post(r1, "/venues/aps/query", body, tenant)
    if not resp.ok:
        print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)
    payload = resp.json() or {}
    rows = payload.get("data") or []
    total = payload.get("totalCount")
    print(f"  totalCount={total}  rows returned={len(rows)}")
    if isinstance(total, int) and total > len(rows):
        print(f"  *** TRUNCATED: {total - len(rows)} AP(s) were not returned. Every group")
        print(f"      whose APs fell off the end will look empty.")
    if rows:
        print(f"  first row keys: {sorted(rows[0].keys())}")

    with_id = sum(1 for a in rows if a.get("apGroupId"))
    with_name = sum(1 for a in rows if a.get("apGroupName"))
    print(f"  APs reporting apGroupId:   {with_id}/{len(rows)}")
    print(f"  APs reporting apGroupName: {with_name}/{len(rows)}")
    if rows and not with_id:
        print("  *** No AP reports an apGroupId. The id join cannot work at all;")
        print("      PISR falls back to matching on group NAME.")

    # ── 2. the venue's AP groups ─────────────────────────────
    print(SEP)
    print("STEP 2  /venues/{id}/apGroups")
    groups = fetch.ap_groups(r1, tenant, vid)
    print(f"  groups returned: {len(groups)}")
    known_ids = {g.get("id") for g in groups}
    ap_ids = {a.get("apGroupId") for a in rows if a.get("apGroupId")}
    print(f"  distinct apGroupId across APs: {len(ap_ids)}")
    stray = ap_ids - known_ids
    print(f"  AP group ids NOT in the venue's group list: {len(stray)}")
    for s in list(stray)[:5]:
        print(f"     {s}")

    # ── 3. activations ───────────────────────────────────────
    print(SEP)
    print("STEP 3  activations and the groups they name")
    acts = fetch.venue_activations(r1, tenant, vid)
    act_ids = {g.get("apGroupId") for a in acts for g in (a.get("apGroups") or [])}
    print(f"  activations={len(acts)}  distinct group ids named={len(act_ids)}")
    print(f"  named ids that ARE in the venue group list: {len(act_ids & known_ids)}")
    print(f"  named ids that APs actually claim:          {len(act_ids & ap_ids)}")
    if act_ids and not (act_ids & ap_ids):
        print("  *** No activation group id is claimed by any AP. This is the failure:")
        print("      the activation side resolves (names render) but no AP matches.")

    # ── 4. one named group in detail ─────────────────────────
    if wanted_group:
        print(SEP)
        print(f"STEP 4  the group named {wanted_group!r}")
        match = [g for g in groups if (g.get("name") or "") == wanted_group]
        if not match:
            near = [g.get("name") for g in groups
                    if wanted_group.lower() in (g.get("name") or "").lower()]
            print(f"  not found by exact name. Similar: {near[:10]}")
        for g in match:
            gid = g.get("id")
            print(f"  id={gid}  name={g.get('name')!r}  isDefault={g.get('isDefault')}")
            by_id = [a for a in rows if a.get("apGroupId") == gid]
            by_name = [a for a in rows if (a.get("apGroupName") or "") == g.get("name")]
            print(f"  APs matching this group by ID:   {len(by_id)}")
            for a in by_id[:6]:
                print(f"     {a.get('name')}  serial={a.get('serialNumber')}")
            print(f"  APs matching this group by NAME: {len(by_name)}")
            for a in by_name[:6]:
                print(f"     {a.get('name')}  serial={a.get('serialNumber')} "
                      f"apGroupId={a.get('apGroupId')}")
            if not by_id and not by_name:
                print("  *** Neither join finds an AP, yet R1's console shows members.")
                print("      The AP rows below are what the venue query actually returned;")
                print("      look for the member AP names among them.")
                for a in rows[:10]:
                    print(f"     {a.get('name')!r} apGroupId={a.get('apGroupId')} "
                          f"apGroupName={a.get('apGroupName')!r}")

    # ── 5. second opinion: tenant-wide, filtered locally ─────
    print(SEP)
    print("STEP 5  same APs fetched tenant-wide, then filtered locally")
    body = {"fields": ["name", "serialNumber", "apGroupId", "apGroupName", "venueId"],
            "page": 0, "pageSize": 10000}
    resp = fetch._post(r1, "/venues/aps/query", body, tenant)
    wide = (resp.json() or {}).get("data") or [] if resp.ok else []
    local = [a for a in wide if a.get("venueId") == vid]
    print(f"  tenant-wide rows={len(wide)}  of which this venue={len(local)}")
    print(f"  venue-filtered query returned={len(rows)}")
    if len(local) > len(rows):
        print(f"  *** The venue filter is DROPPING {len(local) - len(rows)} AP(s) that the")
        print(f"      tenant-wide query does return. PISR should fetch wide and filter.")

    print(SEP)
    print("Group membership as PISR would count it (top 10 by AP count):")
    counts = Counter(a.get("apGroupId") for a in rows if a.get("apGroupId"))
    names = {g.get("id"): g.get("name") for g in groups}
    for gid, n in counts.most_common(10):
        print(f"   {str(names.get(gid)):32} {n} AP(s)")
    if not counts:
        print("   (none — every group would be reported empty)")


if __name__ == "__main__":
    main()
