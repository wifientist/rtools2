"""
Regression test: the ES-window fan-out in the WiredWiz crawl.

R1's query layer caps `from + size` at 10000 rows and reports a totalCount that
SATURATES at that ceiling, so a venue holding more learned MACs than the window
returns exactly 10000 rows and no error. crawl_ports had a per-switch fan-out for
this; crawl_mac_table did not, and worse, the completeness check scored the
truncated result as complete -- so a cut-off MAC table looked like a full one.

No live tenant reproduces this (the estates on hand sit well under the ceiling),
so the client is faked. Run it after touching _query_all, _fanout_by_switch or
either crawl_* method.

Read-only by construction: nothing here talks to R1 at all.

Usage:
    docker compose exec backend python scripts/test_wiredwiz_overflow.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services.switches import ES_WINDOW, SwitchService


class FakeResp:
    def __init__(self, data):
        self.ok, self._d = True, data

    def json(self):
        return self._d


class FakeClient:
    """
    One venue whose MAC table is `switches * per_switch` rows deep. Reproduces the
    three R1 behaviours that make this bug possible: the 10000-row read ceiling, a
    totalCount that saturates at it, and `page: 1` aliasing `page: 0`.
    """

    def __init__(self, per_switch=2500, switches=5, mac_filter_works=True):
        self.switches = [f"sw:{i:02d}" for i in range(switches)]
        self.mac_filter_works = mac_filter_works
        self.all_rows = [
            {"clientMac": f"m{s}-{n:05d}", "switchPortId": f"{s}-p{n % 48}",
             "switchMac": s, "venueId": "V1"}
            for s in self.switches for n in range(per_switch)
        ]

    def post(self, path, payload=None, override_tenant_id=None):
        f = payload.get("filters") or {}
        if path.endswith("/switches/query"):
            return FakeResp({"data": [{"id": s, "switchMac": s, "venueId": "V1",
                                       "deviceStatus": "ONLINE"} for s in self.switches],
                             "totalCount": len(self.switches)})
        if "switchMac" in f:
            # The failure mode worth guarding: a filter key that is silently
            # ignored or unindexed returns nothing rather than erroring.
            if not self.mac_filter_works:
                return FakeResp({"data": [], "totalCount": 0})
            rows = [r for r in self.all_rows if r["switchMac"] == f["switchMac"][0]]
        else:
            rows = self.all_rows
        rows = sorted(rows, key=lambda r: r["clientMac"])
        total = min(len(rows), ES_WINDOW)          # totalCount saturates at the window
        page, size = payload.get("page", 0), payload.get("pageSize", 1000)
        start = 0 if page in (0, 1) else (page - 1) * size
        if start >= ES_WINDOW:
            return FakeResp({"data": [], "totalCount": total})
        return FakeResp({"data": rows[start:min(start + size, ES_WINDOW)],
                         "totalCount": total})


def run(label, **kw):
    client = FakeClient(**kw)
    sv = SwitchService(client)
    sv.reset_completeness()
    rows = sv.crawl_mac_table(tenant_id=None, venue_ids=["V1"])
    report = sv.completeness_report()
    unique = len({(r["clientMac"], r["switchPortId"]) for r in rows})
    print(f"\n=== {label} ===")
    print(f"  ground truth      : {len(client.all_rows)}")
    print(f"  collected         : {len(rows)} (unique {unique})")
    print(f"  duplicates        : {len(rows) - unique}")
    print(f"  incomplete queries: {report['incomplete']} of {report['queries']}")
    return client, rows, report


failures = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        failures.append(msg)


client, rows, report = run("venue over the window, switchMac filter works")
check(len(rows) == len(client.all_rows),
      f"fan-out recovers every row ({len(rows)}/{len(client.all_rows)})")
check(len(rows) == len({(r["clientMac"], r["switchPortId"]) for r in rows}),
      "no duplicates reintroduced by the fan-out")
check(report["incomplete"] == 0, "a cleared cap reports no shortfall")

client, rows, report = run("venue over the window, switchMac filter BROKEN",
                           mac_filter_works=False)
check(len(rows) == ES_WINDOW, "capped rows are kept, not discarded")
check(report["incomplete"] >= 1,
      "an UNCLEARED cap reports incomplete instead of hiding the truncation")
check(any(c.get("windowCapped") for c in report["shortfalls"]),
      "the shortfall is labelled windowCapped so the UI can explain it")

client, rows, report = run("venue under the window", per_switch=300, switches=3)
check(len(rows) == len(client.all_rows), "normal path still returns everything")
check(report["incomplete"] == 0, "normal path reports no shortfall")

print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
