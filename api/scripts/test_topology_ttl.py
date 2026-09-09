"""
The overrides guarantee: user intent must outlive every snapshot beside it.

A snapshot is regenerable in seconds. A human's confirmed links, pinned WAN
uplinks and hand-arranged layout are not. They share a directory with the
snapshots, which means one careless glob would delete them -- so this test
backdates every snapshot well past the TTL, forces a sweep, and asserts the
user's work is still there.

Also covers exact-set scope identity, which is the other thing that would fail
silently: a topology of {A,B} must not be served from a snapshot of {A,B,C}.

Usage:
    docker compose exec backend python scripts/test_topology_ttl.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Point the store at a scratch directory BEFORE importing it.
_TMP = tempfile.mkdtemp(prefix="topology-ttl-")
os.environ["TOPOLOGY_DATA_DIR"] = _TMP

from services.storekit import read_json, write_json_atomic          # noqa: E402
from services.topology import overrides, store                      # noqa: E402

TENANT = "test-tenant"
SCOPE_A = ["venue-a", "venue-b"]
SCOPE_B = ["venue-a", "venue-b", "venue-c"]

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def make_snapshot(venue_ids, stamp, age_days=0.0):
    """Write a snapshot directory directly, then backdate it."""
    scope_dir = store._scope_dir(TENANT, venue_ids)
    snap_dir = scope_dir / f"snap_{stamp}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    for part in ("devices", "ports", "links"):
        write_json_atomic(snap_dir / f"{part}.json", [])
    write_json_atomic(snap_dir / "evidence.json", {})
    write_json_atomic(snap_dir / "meta.json",
                      {"takenAt": stamp, "takenAtEpoch": time.time(),
                       "scopeVenueIds": sorted(venue_ids), "counts": {}})
    if age_days:
        old = time.time() - age_days * 86400
        for child in snap_dir.iterdir():
            os.utime(child, (old, old))
        os.utime(snap_dir, (old, old))
    return snap_dir


def main():
    print(f"scratch dir: {_TMP}")
    print(f"TTL: {store.SNAPSHOT_TTL_DAYS} days, max {store.MAX_SNAPSHOTS} snapshots\n")

    print("scope identity")
    check("a venue SET hashes stably",
          store.scope_key(SCOPE_A) == store.scope_key(list(reversed(SCOPE_A))))
    check("a different set is a different scope",
          store.scope_key(SCOPE_A) != store.scope_key(SCOPE_B))

    print("\nsetup")
    make_snapshot(SCOPE_A, "20260101T000000", age_days=400)
    make_snapshot(SCOPE_A, "20260102T000000", age_days=365)
    fresh = make_snapshot(SCOPE_A, "20260901T000000", age_days=0)
    make_snapshot(SCOPE_B, "20260901T000000", age_days=0)
    store.register_scope(TENANT, SCOPE_A, {"venue-a": "A", "venue-b": "B"})
    store.register_scope(TENANT, SCOPE_B, {"venue-a": "A", "venue-b": "B", "venue-c": "C"})

    # The work that must survive.
    overrides.set_link(TENANT, SCOPE_A, "sw1#1/1/1|sw2#1/1/24", "confirm",
                       by="tester", note="patched by hand, verified on site")
    overrides.set_wan(TENANT, SCOPE_A, "venue-a", "sw:aabbccddeeff", "1/1/48",
                      "confirm", by="tester")
    store.save_layout(TENANT, SCOPE_A, {"positions": {"sw:aabbccddeeff": {"x": 10, "y": 20}}})
    scope_dir = store._scope_dir(TENANT, SCOPE_A)
    # Backdate the protected files too -- age alone must not condemn them.
    old = time.time() - 400 * 86400
    for name in ("overrides.json", "layout.json"):
        os.utime(scope_dir / name, (old, old))
    check("overrides written", (scope_dir / "overrides.json").exists())
    check("layout written", (scope_dir / "layout.json").exists())

    print("\nsweep on read")
    listed = store.list_snapshots(TENANT, SCOPE_A)
    check("expired snapshots are gone", len(listed) == 1,
          f"got {[s['name'] for s in listed]}")
    check("the fresh snapshot survived", listed and listed[0]["name"] == fresh.name)
    check("expired directories removed from disk",
          not (scope_dir / "snap_20260101T000000").exists())

    print("\nTHE GUARANTEE: user intent outlives the snapshots")
    check("overrides.json survived a 400-day-old mtime",
          (scope_dir / "overrides.json").exists())
    check("layout.json survived", (scope_dir / "layout.json").exists())
    reloaded = overrides.load(TENANT, SCOPE_A)
    check("the confirmed link is intact",
          reloaded["links"].get("sw1#1/1/1|sw2#1/1/24", {}).get("verdict") == "confirm")
    check("the note is intact",
          "verified on site" in reloaded["links"]["sw1#1/1/1|sw2#1/1/24"]["note"])
    check("the WAN verdict is intact",
          (reloaded["wan"].get("venue-a") or {}).get("confirmed", {}).get("portIdent") == "1/1/48")
    check("the layout is intact",
          store.load_layout(TENANT, SCOPE_A).get("positions", {}).get(
              "sw:aabbccddeeff", {}).get("x") == 10)

    print("\nscope isolation")
    check("the other scope kept its own snapshot",
          len(store.list_snapshots(TENANT, SCOPE_B)) == 1)
    check("overrides do not leak between scopes",
          not overrides.load(TENANT, SCOPE_B)["links"])
    check("an unknown scope has no snapshots",
          store.list_snapshots(TENANT, ["venue-z"]) == [])

    print("\nnear misses are offered, not substituted")
    misses = store.near_misses(TENANT, SCOPE_A)
    check("the superset scope is suggested",
          any(set(m.get("venueIds") or []) == set(SCOPE_B) for m in misses))
    check("it is flagged as covering", any(m.get("covers") for m in misses))
    check("loading {A,B} never returns {A,B,C}'s data",
          store.load_part(TENANT, SCOPE_A, "meta") is not None
          and store.load_part(TENANT, SCOPE_A, "meta").get("scopeVenueIds") == sorted(SCOPE_A))

    print("\nlayout versioning")
    first = store.save_layout(TENANT, SCOPE_B, {"positions": {}}, expected_version=0)
    check("first write accepted", first.get("ok") and first["version"] == 1)
    stale = store.save_layout(TENANT, SCOPE_B, {"positions": {}}, expected_version=0)
    check("a stale write is refused, not silently applied",
          not stale.get("ok") and stale.get("conflict"))
    good = store.save_layout(TENANT, SCOPE_B, {"positions": {}}, expected_version=1)
    check("the correct version is accepted", good.get("ok") and good["version"] == 2)

    print("\ncount ceiling")
    many = ["venue-many"]
    for index in range(store.MAX_SNAPSHOTS + 5):
        make_snapshot(many, f"2026090{index // 10}T{index:06d}")
        time.sleep(0.002)                   # distinct mtimes
    store.save_layout(TENANT, many, {"positions": {"x": 1}})
    kept = store.list_snapshots(TENANT, many)
    check(f"pruned to at most {store.MAX_SNAPSHOTS}", len(kept) <= store.MAX_SNAPSHOTS,
          f"got {len(kept)}")
    check("layout survived pruning",
          store.load_layout(TENANT, many).get("positions") == {"x": 1})

    print("\natomic writes")
    target = Path(_TMP) / "atomic.json"
    write_json_atomic(target, {"a": 1})
    check("round-trips", read_json(target) == {"a": 1})
    check("no temp files left behind",
          not list(target.parent.glob(".atomic.json.*.tmp")))
    check("a corrupt file is skipped, not fatal",
          (target.write_text("{not json"), read_json(target) is None)[1])

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
