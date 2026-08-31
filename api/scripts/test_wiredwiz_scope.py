"""
Regression test: snapshot selection in WiredWiz storage.

Two bugs this pins down.

1. **Ordering.** The snapshot directory holds two naming schemes -- `snap_<stamp>`
   from the API and `snap_<tenantprefix>_<stamp>` from the CLI script. Sorted by
   name, every `snap_0...` sorts ahead of every `snap_2...`, so "the newest
   snapshot" could be a months-old CLI file. Ordering is by mtime now.

2. **Scope blindness.** `load()` returns the newest snapshot whatever venues it
   covers, and the detail endpoints filtered it down to the venues being viewed.
   Crawl venue A, open venue B, and the inventory silently emptied out. That is
   what `load_covering()` exists to prevent.

Read-only by construction: builds its own snapshots in a temp dir, never touches
R1 or the real data directory.

Usage:
    docker compose exec backend python scripts/test_wiredwiz_scope.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wiredwiz import store

failures = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        failures.append(msg)


def snapshot(venue_ids, stamp, scoped=True):
    """A snapshot covering `venue_ids`, with one switch/port/mac per venue."""
    return {
        "takenAt": stamp,
        "takenAtEpoch": time.time(),
        "scopeVenueIds": sorted(venue_ids) if scoped else None,
        "venues": {v: f"Venue {v}" for v in venue_ids},
        "switches": [{"id": f"sw-{v}", "switchMac": f"sw-{v}", "venueId": v,
                      "deviceStatus": "ONLINE", "name": f"switch-{v}"} for v in venue_ids],
        "ports": [{"id": f"p-{v}", "switchMac": f"sw-{v}", "venueId": v} for v in venue_ids],
        "macs": [{"clientMac": f"m-{v}", "switchPortId": f"p-{v}", "switchMac": f"sw-{v}",
                  "venueId": v} for v in venue_ids],
        "completeness": {"incomplete": 0},
    }


def write(d: Path, name: str, snap: dict, mtime: float):
    p = d / name
    p.write_text(json.dumps(snap))
    os.utime(p, (mtime, mtime))
    return p


tmp = tempfile.mkdtemp(prefix="wiredwiz-scope-")
store.SNAPSHOT_DIR = Path(tmp)
TENANT = "t1"
d = store._tenant_dir(TENANT)
d.mkdir(parents=True, exist_ok=True)
now = time.time()

# CLI-named file is the OLDEST, API-named files are newer. Sorted by name the
# CLI one wins, which is exactly the bug.
write(d, "snap_02e1dc33_20260818T194400.json", snapshot(["A"], "old-cli"), now - 3000)
write(d, "snap_20260818T200000.json", snapshot(["A"], "mid-A"), now - 2000)
write(d, "snap_20260819T100000.json", snapshot(["B"], "new-B"), now - 1000)

print("\n=== ordering: mixed naming schemes ===")
names = [f.name for f in store._snap_files(d)]
check(names[0] == "snap_02e1dc33_20260818T194400.json",
      f"oldest file is the CLI-named one (got {names[0]})")
check(store.load(TENANT)["takenAt"] == "new-B",
      f"load() returns the genuinely newest (got {store.load(TENANT)['takenAt']})")
check([s["takenAt"] for s in store.load_all(TENANT)] == ["old-cli", "mid-A", "new-B"],
      "load_all() returns oldest-first across both naming schemes")

print("\n=== scope: newest snapshot does not cover the requested venue ===")
snap, scope = store.load_covering(TENANT, ["A"])
check(snap is not None and snap["takenAt"] == "mid-A",
      f"skips the newest (venue B) for the newest that covers A (got {snap and snap['takenAt']})")
check(scope["insufficientCoverage"] is False, "coverage satisfied, so not flagged")
check(len(snap["macs"]) == 1 and snap["macs"][0]["venueId"] == "A",
      "returned snapshot holds venue A's rows")
check(any(e["takenAt"] == "new-B" for e in scope["excluded"]),
      "the passed-over venue-B snapshot is reported in `excluded`")

print("\n=== scope: NOTHING covers the requested venue ===")
snap, scope = store.load_covering(TENANT, ["C"])
check(scope["insufficientCoverage"] is True,
      "an uncoverable request is flagged, not silently thinned")
check(bool(scope.get("reason")), "a human-readable reason is supplied for the UI")

print("\n=== scope: wide snapshot narrowed to one venue ===")
write(d, "snap_20260820T100000.json", snapshot(["A", "B", "C"], "wide"), now - 500)
snap, scope = store.load_covering(TENANT, ["B"])
check(snap["takenAt"] == "wide", f"picks the covering snapshot (got {snap['takenAt']})")
check(scope["scoped"] is True and scope["insufficientCoverage"] is False,
      "narrowing is reported as scoped, not as a coverage failure")
check([m["venueId"] for m in snap["macs"]] == ["B"],
      "venues outside the request are filtered out")
check(len(snap["switches"]) == 1 and snap["switches"][0]["venueId"] == "B",
      "switch list narrowed too, so per-switch counts cannot come from elsewhere")

print("\n=== scope: no venue filter asked for ===")
snap, scope = store.load_covering(TENANT, None)
check(snap["takenAt"] == "wide", "unscoped request gets the newest snapshot")
check(scope["insufficientCoverage"] is not True, "unscoped is never a coverage failure")

print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
