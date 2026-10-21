#!/usr/bin/env python3
"""
SZ migration snapshots: only the user who captured one may read it.

Snapshots (SZ extractions, R1 inventories) and migration workflow jobs are
addressed by job id. Every job-id endpoint used to check only that someone
was logged in, so any user -- any company -- holding an id (they travel in
URLs and screenshots) could read a full SZ extraction, passphrases included,
or confirm someone else's migration.

WHAT THIS GUARDS

  1. Recorded owner == caller -> allowed; anyone else -> 404 (existence not
     confirmed), even if they also hold a session row for the same id.
  2. Older snapshots without an owner record: allowed via the user's own job
     index or migration session, refused otherwise.
  3. Workflow jobs: only their creator; a missing job is the same 404.
  4. Snapshot JSON sent to the browser or downloaded carries no passphrase.
  5. Structural: every route that reads a snapshot, extraction state or
     workflow job by id checks ownership before the read.

Usage:
    docker compose exec backend python scripts/test_snapshot_ownership.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import HTTPException

import routers.sz_migration.router as R

PSK = "unit-101-secret"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


class FakeRedis:
    def __init__(self, kv=None, z=None):
        self.kv, self.z = kv or {}, z or {}

    async def get(self, k):
        return self.kv.get(k)

    async def zscore(self, k, m):
        return 1.0 if m in self.z.get(k, ()) else None

    async def set(self, k, v, ex=None):
        self.kv[k] = v


class FakeQuery:
    def __init__(self, hit):
        self.hit = hit

    def filter(self, *a):
        return self

    def first(self):
        return object() if self.hit else None


class FakeDb:
    def __init__(self, session_hit=False):
        self.session_hit = session_hit

    def query(self, *a):
        return FakeQuery(self.session_hit)


async def allowed(kind, job, user_id, redis, db):
    async def g():
        return redis
    R.get_redis_client = g
    try:
        await R._require_owner(kind, job, SimpleNamespace(id=user_id), db)
        return True
    except HTTPException as e:
        assert e.status_code == 404, e.status_code
        return False


async def main():
    failures = 0
    print("Snapshot ownership")

    owned = FakeRedis({R._owner_key("sz", "job-1"): "7"})
    # 1
    failures += check("owner may read", await allowed("sz", "job-1", 7, owned, FakeDb()))
    failures += check("another user gets 404", not await allowed("sz", "job-1", 8, owned, FakeDb()))
    failures += check("recorded owner beats a session row",
                      not await allowed("sz", "job-1", 8, owned, FakeDb(session_hit=True)))
    # 2
    legacy = FakeRedis(z={f"{R.EXTRACTION_KEY_PREFIX}:user:7:jobs": {"old-job"}})
    failures += check("older snapshot: owner via job index", await allowed("sz", "old-job", 7, legacy, FakeDb()))
    failures += check("older snapshot: owner via session", await allowed("r1", "old-r1", 7, FakeRedis(), FakeDb(True)))
    failures += check("older snapshot: stranger refused", not await allowed("sz", "old-job", 8, legacy, FakeDb()))
    # 3
    async def job_ok(job, uid):
        try:
            await R._require_job_owner(job, SimpleNamespace(id=uid))
            return True
        except HTTPException as e:
            return e.status_code != 404
    failures += check("workflow job: creator allowed", await job_ok(SimpleNamespace(user_id=7), 7))
    failures += check("workflow job: others 404", not await job_ok(SimpleNamespace(user_id=7), 8))
    failures += check("workflow job: missing 404", not await job_ok(None, 7))
    # 4
    raw = json.dumps({"wlans": [{"encryption": {"passphrase": PSK}, "raw": {"encryption": {"saePassphrase": PSK}}}],
                      "zone": {"raw": {"login": {"apLoginPassword": "ap-admin-pw"}}}})
    red = R._redacted_snapshot_json(raw)
    failures += check("browser/download JSON holds no passphrase or password",
                      PSK not in red and "ap-admin-pw" not in red)

    # 5 structural
    src = (Path(__file__).parent.parent / "routers" / "sz_migration" / "router.py").read_text()
    reads = re.compile(r"_get_snapshot_raw\(|_get_r1_snapshot_raw\(|_get_extraction_state\(|state_manager\.get_job\(")
    unguarded = []
    for m in re.finditer(r"@router\.\w+\(\"([^\"]+)\"[^\n]*\n(?:@[^\n]*\n)*(?:async )?def (\w+)\(", src):
        start = m.end()
        nxt = src.find("\n@router.", start)
        body = src[start: nxt if nxt != -1 else len(src)]
        first_read = reads.search(body)
        if not first_read:
            continue
        if first_read.group(0).startswith("state_manager.get_job"):
            # A workflow job must be loaded to know its owner: the check has to
            # come immediately after the load, before anything else uses it.
            after = body[first_read.end():].split("\n", 2)
            if len(after) < 2 or "_require_job_owner(job" not in after[1]:
                unguarded.append(f"{m.group(2)} ({m.group(1)})")
            continue
        guard = re.search(r"_require_owner\(", body)
        # The audit endpoint reads by id only on its "existing snapshot" paths; each is guarded inline.
        if guard is None or guard.start() > first_read.start():
            unguarded.append(f"{m.group(2)} ({m.group(1)})")
    failures += check("every route reading by job id checks ownership first", not unguarded, ", ".join(unguarded))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
