#!/usr/bin/env python3
"""
wifiNetworks/query pagination regression test.

WHAT FAILED IN PRODUCTION

Venue Audit on a large MSP-EC:

    WiFi Networks: first page returned 500, total count: 3839
    WiFi Networks pagination: page_size=500, pages_needed=8
    POST /wifiNetworks/query --> 503
    upstream connect error or disconnect/reset before headers
    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

Two faults, one symptom.

  * get_wifi_networks called .json() on every page with no status check. A
    503 carries an HTML body, so json() raised JSONDecodeError and the audit
    died with a stack trace about "column 1 char 0" -- which says nothing
    about what went wrong. 3700 already-fetched rows went with it.

  * It fetched the whole TENANT to find the networks at ONE venue. Eight
    pages, eight chances for a blip to kill the run, to end up with seven
    rows.

R1 can filter server-side, but only under the nested key. Verified live on
the failing tenant: `venueApGroups.venueId` returns exactly the same 7 ids
the client-side filter produces, from a single page. `venues` and `venueIds`
return HTTP 200 with totalCount 0 -- silently ignored, the usual trap --
and `venueId` alone is a 400.

WHAT THIS GUARDS

  1. A transient status mid-pagination is retried, not fatal.
  2. Rows already fetched survive a retried page.
  3. A persistent failure raises a message naming the page and status --
     never a JSONDecodeError about column 1.
  4. A non-transient status (401/404) fails immediately, without burning
     retries on something that will not改.
  5. venue_id produces the nested filter key, and its absence sends none.

Usage:
    docker compose exec backend python scripts/test_network_query_resilience.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services import networks as networks_mod
from r1api.services.networks import NetworksService


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        if self.status_code >= 400:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


class FakeClient:
    """`script` is the status sequence to hand back, per call."""
    ec_type = "EC"

    def __init__(self, script, total=1200):
        self.script = list(script)
        self.total = total
        self.calls = []

    def post(self, path, payload=None, **kw):
        # A COPY: get_wifi_networks reuses one body dict and mutates `page`
        # between pages, so keeping the reference makes every recorded call
        # look like the last one.
        self.calls.append(dict(payload or {}))
        status = self.script.pop(0) if self.script else 200
        if status != 200:
            return FakeResponse(status)
        page = payload.get("page", 1)
        size = payload.get("pageSize", 500)
        start = (page - 1) * size
        rows = [{"id": f"n-{i}"} for i in range(start, min(start + size, self.total))]
        return FakeResponse(200, {"data": rows, "totalCount": self.total})

    def safe_json(self, response):
        return response.json()


def svc(client):
    s = NetworksService.__new__(NetworksService)
    s.client = client
    return s


async def main() -> int:
    failures = 0
    print("wifiNetworks/query pagination\n")
    networks_mod.PAGE_RETRY_BACKOFF = 0  # no real sleeping in a test

    # 1 + 2. a 503 on page 2 is retried and the run completes
    client = FakeClient([200, 503, 200, 200], total=1200)
    out = await svc(client).get_wifi_networks("t-1")
    failures += check(
        "a transient 503 mid-pagination is retried, not fatal",
        len(out["data"]) == 1200 and out["totalCount"] == 1200,
        f"got {len(out['data'])} rows",
    )
    failures += check(
        "the retry re-requests the SAME page, losing nothing",
        [c["page"] for c in client.calls] == [1, 2, 2, 3],
        str([c["page"] for c in client.calls]),
    )

    # 3. a persistent failure says what happened
    client = FakeClient([200, 503, 503, 503], total=1200)
    try:
        await svc(client).get_wifi_networks("t-1")
        failures += check("a persistent failure raises a clear error", False, "no raise")
    except RuntimeError as e:
        failures += check(
            "a persistent failure names the page and status, not column 1",
            "page 2" in str(e) and "503" in str(e) and "column 1" not in str(e),
            str(e)[:90],
        )
    except Exception as e:
        failures += check(
            "a persistent failure names the page and status, not column 1",
            False, f"{type(e).__name__}: {e}",
        )

    # 4. a non-transient status does not burn retries
    client = FakeClient([401, 401, 401], total=100)
    try:
        await svc(client).get_wifi_networks("t-1")
        failures += check("a 401 fails immediately", False, "no raise")
    except RuntimeError as e:
        failures += check(
            "a 401 fails immediately rather than retrying",
            len(client.calls) == 1 and "401" in str(e),
            f"{len(client.calls)} call(s)",
        )

    # 5. the venue filter
    client = FakeClient([200], total=7)
    await svc(client).get_wifi_networks("t-1", venue_id="v-1")
    failures += check(
        "venue_id sends the nested venueApGroups.venueId filter",
        client.calls[0].get("filters") == {"venueApGroups.venueId": ["v-1"]},
        str(client.calls[0].get("filters")),
    )
    client = FakeClient([200], total=7)
    await svc(client).get_wifi_networks("t-1")
    failures += check(
        "no venue_id sends no filter at all",
        "filters" not in client.calls[0],
        str(client.calls[0].get("filters")),
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
