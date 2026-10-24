#!/usr/bin/env python3
"""
SZ extraction progress reaches the page while the extraction runs.

The extractor calls on_progress synchronously; the router's callback used to
be async, so every update was a coroutine nobody awaited -- the page sat on
"Initializing..." until the end, and an extraction longer than
EXTRACTION_STALE_SECONDS read as "interrupted" while still running.

WHAT THIS GUARDS
  1. Progress is saved and published while the extraction runs, in order.
  2. A long quiet stretch still refreshes updated_at (heartbeat).
  3. "completed" is the last state written -- the writer can't overwrite it.
  4. The extractor's own "complete" is not passed on before the snapshot is
     saved (the router announces it afterwards).
  5. A failed login leaves "failed" as the final state (no writer running).

Usage:
    docker compose exec backend python scripts/test_extraction_progress.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import routers.sz_migration.router as router


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


def run(login_fails=False):
    states, published, saved_snapshot = [], [], []

    async def save_state(job_id, state):
        states.append((len(saved_snapshot), state))

    async def publish(job_id, phase, message, data):
        published.append((len(saved_snapshot), phase, message))

    async def save_snapshot(job_id, snap):
        saved_snapshot.append(True)

    class Client:
        client = SimpleNamespace(aclose=lambda: asyncio.sleep(0))

        async def login(self):
            if login_fails:
                raise ValueError("bad password")

        async def logout(self):
            pass

    async def extract(sz_client, zone_id, on_progress=None):
        on_progress("zone", "Fetching zone details...", {})
        await asyncio.sleep(0.05)
        for i in range(30):                       # a burst, like per-AP radio reads
            on_progress("ap_radio", f"Per-AP radio config: {i + 1}/30", {})
        await asyncio.sleep(0.05)
        on_progress("references", "Scanning WLANs...", {})
        await asyncio.sleep(0.35)                 # a long quiet stretch
        on_progress("complete", "Extraction complete in 1s", {})
        return SimpleNamespace(summary=lambda: {"wlans": 1})

    router._save_extraction_state = save_state
    router._publish_progress = publish
    router._save_snapshot = save_snapshot
    router.create_sz_client_from_controller = lambda cid, db: Client()
    router.extract_zone_snapshot = extract
    router.PROGRESS_HEARTBEAT_SECONDS = 0.1

    asyncio.run(router._run_extraction("job", 1, "zone", db=None))
    return states, published


def main():
    f = 0
    print("\n1-4. Progress during a run")
    states, published = run()
    running = [s for _, s in states if s.get("status") == "running"]
    messages = [s["progress"]["message"] for s in running]
    f += check("progress saved while running (not just Initializing + done)",
               any("Per-AP radio" in m for m in messages) and any("Scanning" in m for m in messages), str(messages))
    order = [m for m in messages]
    f += check("in order", order.index(next(m for m in order if "Per-AP" in m)) <
               order.index(next(m for m in order if "Scanning" in m)), str(order))
    f += check("burst coalesced (fewer writes than updates)", len(running) < 33, str(len(running)))
    f += check("heartbeat re-saves during the quiet stretch",
               sum(1 for m in messages if "Scanning" in m) >= 2, str(messages))
    f += check("final state is completed", states[-1][1].get("status") == "completed", str(states[-1]))
    early_complete = [p for p in published if p[1] == "complete" and p[0] == 0]
    f += check("no 'complete' announced before the snapshot is saved", not early_complete, str(published))
    f += check("'complete' announced after it", any(p[1] == "complete" and p[0] == 1 for p in published),
               str(published))

    print("\n5. Failed login")
    states, _ = run(login_fails=True)
    f += check("final state is failed", states[-1][1].get("status") == "failed", str(states[-1]))

    print(f"\n{'All checks passed' if not f else f'{f} check(s) failed'}")
    return f


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
