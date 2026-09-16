#!/usr/bin/env python3
"""
R1 session-expiry and activity-polling regression test.

From the 2026-09-16 import. Fifty minutes in, R1 killed the session:

    RCG-10003 "Please re-login, the session has expired."

Nothing renewed it. R1Client takes a token at construction and holds it, and
_request had no 401 handling, so every call 401'd from then on. The activity
tracker read that as evidence about the WORK: the bulk query threw, the
individual GETs flattened every non-OK status to None, each None counted as
an error, the circuit breaker tripped at ten, and 17 units were failed for AP
assignments R1 had already accepted with a 202. Job ended PARTIAL.

WHAT THIS GUARDS

  1. A 401 refreshes the token and retries the request once.
  2. The rejected token is evicted from the shared cache, so the next client
     built does not inherit a dead session.
  3. Concurrent 401s across threads collapse into ONE re-authentication.
  4. A 401 is not retried forever -- one retry, then the response stands.
  5. A 404 reading an activity means "not created yet", not an error, and
     cannot trip the breaker on its own.
  6. A 401 reading an activity DOES count as a read failure.
  7. Giving up -- breaker, expiry, timeout -- yields unverified=True, which
     is "outcome unknown", never a fabricated failure.
  8. assign_aps reports unverified APs and does NOT fail the unit.
  9. The bulk activity query pages until the pending set is covered, instead
     of reading only the newest 500 of a window that grows all run.

Usage:
    docker compose exec backend python scripts/test_r1_auth_and_polling.py
"""

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api import token_cache
from r1api.client import R1Client
from workflow.v2.activity_tracker import ActivityTracker, TransientActivityError
from workflow.v2.models import ActivityResult
from workflow.phases.assign_aps import AssignAPsPhase

TENANT = "tenant-test"


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload if payload is not None else {}
        self.text = text or str(self._payload)
        self.content = b"x"

    def json(self):
        return self._payload


class FakeSession:
    """Answers 401 until the client re-authenticates, then 200."""

    def __init__(self, auth_token="fresh-token", fail_first=True):
        self.calls = []
        self.auth_count = 0
        self.auth_token = auth_token
        self.fail_first = fail_first
        self.lock = threading.Lock()

    def post(self, url, headers=None, data=None, **kw):
        if "/oauth2/token/" in url:
            with self.lock:
                self.auth_count += 1
            return FakeResponse(200, {"access_token": self.auth_token, "expires_in": 3600})
        return self.request("post", url, headers=headers, **kw)

    def request(self, method, url, headers=None, **kw):
        token = (headers or {}).get("Authorization", "")
        self.calls.append((method, url, token))
        if self.fail_first and token.endswith("stale-token"):
            return FakeResponse(
                401,
                {"error": "Please re-login, the session has expired."},
                text='{"errors":[{"code":"RCG-10003"}]}',
            )
        return FakeResponse(200, {"ok": True})

    def mount(self, *a, **kw):
        pass


def make_client(session, token="stale-token"):
    client = R1Client.__new__(R1Client)
    client.session = session
    client.host = "api.ruckus.cloud"
    client.ec_type = "EC"
    client.tenant_id = TENANT
    client.client_id = "cid"
    client.shared_secret = "secret"
    client.token = token
    client._auth_lock = threading.Lock()
    client.auth_failed = False
    client.auth_error = None
    return client


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("R1 session expiry and activity polling\n")

    # 1 + 2. 401 -> re-auth -> retry, and the dead token is evicted
    token_cache.store_token(TENANT, "stale-token", 3600)
    session = FakeSession()
    client = make_client(session)
    resp = client.get("/venues")
    failures += check(
        "a 401 re-authenticates and retries once, and the call succeeds",
        resp.status_code == 200 and session.auth_count == 1 and len(session.calls) == 2,
        f"status={resp.status_code} auths={session.auth_count} calls={len(session.calls)}",
    )
    failures += check(
        "the rejected token is evicted from the shared cache",
        token_cache.get_cached_token(TENANT) == "fresh-token",
        f"cached={token_cache.get_cached_token(TENANT)}",
    )

    # 3. concurrent 401s collapse into one re-authentication
    token_cache.invalidate_token(TENANT)
    session = FakeSession()
    client = make_client(session)
    threads = [threading.Thread(target=client.get, args=("/venues",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    failures += check(
        "8 concurrent 401s trigger exactly one re-authentication",
        session.auth_count == 1, f"auths={session.auth_count}",
    )

    # 4. a persistent 401 is retried once, not forever
    token_cache.invalidate_token(TENANT)
    session = FakeSession(auth_token="stale-token")   # re-auth returns the same dead token
    client = make_client(session)
    resp = client.get("/venues")
    failures += check(
        "a persistent 401 stops after one retry",
        resp.status_code == 401 and len(session.calls) <= 2,
        f"status={resp.status_code} calls={len(session.calls)}",
    )

    # 5 + 6. 404 is "not yet"; 401 is a read failure
    def fetch_with(status):
        tracker = ActivityTracker.__new__(ActivityTracker)
        tracker.tenant_id = TENANT

        class C:
            ec_type = "EC"

            def get(self, *a, **kw):
                return FakeResponse(status, {"requestId": "r1"}, text="nope")

        tracker.r1_client = C()
        return tracker

    failures += check(
        "a 404 reading an activity returns None (not created yet), no error",
        fetch_with(404)._fetch_activity_sync("abc") is None,
    )
    try:
        fetch_with(401)._fetch_activity_sync("abc")
        failures += check("a 401 reading an activity is a transient read failure", False)
    except TransientActivityError:
        failures += check("a 401 reading an activity is a transient read failure", True)

    # 7. giving up is "unknown", not "failed"
    failures += check(
        "ActivityResult carries unverified, defaulting to False",
        ActivityResult(activity_id="a", success=False).unverified is False
        and ActivityResult(activity_id="a", success=False, unverified=True).unverified,
    )

    # 8. assign_aps reports unverified and does not fail the unit
    phase = AssignAPsPhase.__new__(AssignAPsPhase)
    messages = []

    async def emit(msg, level="info", details=None):
        messages.append((level, msg))

    async def fire_and_wait(request_id):
        return ActivityResult(
            activity_id=request_id, success=False, unverified=True,
            error="Activity expired after 540s (R1 never returned a terminal status)",
        )

    class Ctx:
        activity_tracker = object()

    class Venues:
        async def assign_ap_to_group(self, **kw):
            return {"requestId": "req-1"}

    class R1:
        venues = Venues()

    phase.emit = emit
    phase.fire_and_wait = fire_and_wait
    phase.context = Ctx()
    phase.r1_client = R1()
    phase.tenant_id = TENANT
    phase.venue_id = "v-1"

    aps = [{"serialNumber": "SER1", "name": "AP-1", "apGroupId": None}]
    assigned, skipped, unverified = await phase._assign_aps_to_group(
        "101", aps, "apg-1", "Unit-101"
    )
    failures += check(
        "an unconfirmed AP assignment does not fail the unit",
        unverified == 1 and assigned == 0 and skipped == 0,
        f"assigned={assigned} skipped={skipped} unverified={unverified}",
    )
    failures += check(
        "and it is reported rather than swallowed",
        any("not confirmed" in m for _, m in messages),
        f"messages={[m for _, m in messages]}",
    )

    # 9. the bulk query pages until the pending set is covered.
    #
    # The window starts at job start and never shrinks, so a long run
    # outgrows one page: the 2026-09-16 job reported totalCount=1500 while
    # reading only the newest 500, leaving everything older permanently
    # unmatchable.
    class PagingClient:
        ec_type = "EC"

        def __init__(self, total=1500, size=500):
            self.pages, self.total, self.size = [], total, size

        def post(self, path, payload=None, **kw):
            page, size = payload["page"], payload["pageSize"]
            self.pages.append(page)
            start = (page - 1) * size
            rows = [
                {"requestId": f"act-{start + i}", "status": "SUCCESS"}
                for i in range(size) if start + i < self.total
            ]
            return FakeResponse(200, {"data": rows, "totalCount": self.total})

    def paging_tracker():
        t = ActivityTracker.__new__(ActivityTracker)
        t.tenant_id = TENANT
        t.r1_client = PagingClient()
        t._from_time = "2026-09-16T12:49:34Z"
        return t

    t = paging_tracker()
    got = await t._poll_activities_bulk_time(["act-1200"], cycle_id=1)
    failures += check(
        "an activity beyond the first page is still matched",
        "act-1200" in got, f"pages={t.r1_client.pages} matched={list(got)}",
    )

    t = paging_tracker()
    await t._poll_activities_bulk_time(["act-3"], cycle_id=2)
    failures += check(
        "paging stops as soon as the pending set is covered",
        t.r1_client.pages == [1], f"pages={t.r1_client.pages}",
    )

    t = paging_tracker()
    await t._poll_activities_bulk_time(["never-exists"], cycle_id=3)
    failures += check(
        "an unmatched id exhausts the window, then stops",
        t.r1_client.pages == [1, 2, 3], f"pages={t.r1_client.pages}",
    )

    token_cache.invalidate_token(TENANT)
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
