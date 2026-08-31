"""
Regression test: age-based retention in WiredWiz storage.

The count caps (MAX_PER_TENANT, MAX_BASELINES) bound how MUCH is kept, never how
LONG. A baseline therefore survived until ten newer ones displaced it, which for
occasional use is indefinitely -- and a baseline is redacted device configuration.
TTL closes that, and it runs on READ as well as write, because retention that only
fires on write keeps stale configs forever the moment someone stops crawling.

Read-only by construction: builds its own files in a temp dir, never touches R1
or the real data directory.

Usage:
    docker compose exec backend python scripts/test_wiredwiz_ttl.py
"""
import json
import os
import sys
import time
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wiredwiz import store

failures = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        failures.append(msg)


DAY = 86400
tmp = tempfile.mkdtemp(prefix="wiredwiz-ttl-")
store.SNAPSHOT_DIR = Path(tmp)
store.SNAPSHOT_TTL_DAYS = 7
store.BASELINE_TTL_DAYS = 7
store.HEALTH_TTL_DAYS = 7
TENANT = "t1"
now = time.time()


def write(path: Path, payload: dict, age_days: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    t = now - age_days * DAY
    os.utime(path, (t, t))
    return path


def snap(stamp, venue="A"):
    return {"takenAt": stamp, "takenAtEpoch": now, "scopeVenueIds": [venue],
            "venues": {venue: "V"}, "switches": [], "ports": [], "macs": [],
            "completeness": {"incomplete": 0}}


d = store._tenant_dir(TENANT)
bd = d / "baselines"

print("\n=== snapshots: only the young survive ===")
write(d / "snap_fresh.json", snap("fresh"), age_days=1)
write(d / "snap_edge.json", snap("edge"), age_days=6.9)
write(d / "snap_stale.json", snap("stale"), age_days=8)
write(d / "snap_ancient.json", snap("ancient"), age_days=90)
names = [f.name for f in store._snap_files(d)]
check(set(names) == {"snap_edge.json", "snap_fresh.json"},
      f"only sub-7d snapshots survive (got {sorted(names)})")
check(not (d / "snap_stale.json").exists(), "the expired file is actually DELETED, not just hidden")
check(not (d / "snap_ancient.json").exists(), "a 90-day-old snapshot is gone too")

print("\n=== the sweep runs on READ, with no write anywhere ===")
write(d / "snap_written_then_aged.json", snap("aged"), age_days=30)
check((d / "snap_written_then_aged.json").exists(), "precondition: the stale file is on disk")
store.list_snapshots(TENANT)                      # a pure read path
check(not (d / "snap_written_then_aged.json").exists(),
      "list_snapshots() alone expired it — no crawl needed")

print("\n=== baselines: configs age out, and cannot be fetched by name ===")
write(bd / "baseline_old.json", {"takenAt": "old", "configs": {"a": {"config": "x"}}}, age_days=10)
write(bd / "baseline_new.json", {"takenAt": "new", "configs": {"a": {"config": "x"}}}, age_days=2)
listed = store.list_baselines(TENANT)
check([b["file"] for b in listed] == ["baseline_new.json"],
      f"expired baseline dropped from the listing (got {[b['file'] for b in listed]})")
check(not (bd / "baseline_old.json").exists(), "expired baseline is deleted from disk")
check(store.load_baseline(TENANT, "baseline_old.json") is None,
      "an expired baseline cannot be resurrected by naming its file")
check(store.load_baseline(TENANT)["takenAt"] == "new", "the surviving baseline still loads")
check(listed[0]["expiresAtEpoch"] > now, "listing reports when the survivor expires")

print("\n=== health: config-derived findings age out too ===")
write(d / "health.json", {"findings": [{"title": "vlan 1000 has no spanning-tree"}]}, age_days=9)
check(store.load_health(TENANT) is None, "an expired health result reads as absent")
check(not (d / "health.json").exists(), "and is deleted")
write(d / "health.json", {"findings": [{"title": "recent"}]}, age_days=1)
check(store.load_health(TENANT) is not None, "a recent health result still loads")

print("\n=== count cap still applies inside the TTL window ===")
store.MAX_BASELINES = 3
for i in range(5):
    write(bd / f"baseline_k{i}.json", {"takenAt": f"k{i}", "configs": {}}, age_days=1 + i * 0.01)
store.save_baseline(TENANT, {"takenAt": "2026-08-30T00:00:00", "configs": {}})
survivors = [f.name for f in store._baseline_files(TENANT)]
check(len(survivors) <= store.MAX_BASELINES,
      f"count cap still enforced within the window (kept {len(survivors)})")

print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
