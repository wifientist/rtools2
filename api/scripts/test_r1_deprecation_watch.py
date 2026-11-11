"""
R1 deprecation watch: the live-header recorder, the spec reading, and the
audit's severity rules. No R1, no real Redis.

    docker compose exec backend python scripts/test_r1_deprecation_watch.py
"""
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from r1api import deprecation_watch as dw  # noqa: E402
from services import r1_deprecation_audit as audit  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        failures += 1


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class FakeResponse:
    def __init__(self, status, headers, sent=None):
        self.status_code = status
        self.headers = headers
        self.request = FakeRequest(sent or {"Content-Type": "application/json"})


class FakeRedis:
    def __init__(self):
        self.h, self.kv = {}, {}

    def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    def hgetall(self, k):
        return dict(self.h.get(k, {}))

    def expire(self, *a):
        pass

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def smembers(self, k):
        return set(self.kv.get(k, set()))

    def sadd(self, k, *v):
        self.kv.setdefault(k, set()).update(v)


fake = FakeRedis()
import r1api.call_metrics as cm  # noqa: E402
cm._redis = lambda: fake

print("live recorder")
dw.observe("GET /entitlements", FakeResponse(200, {"ruckus-deprecated": "/entitlements/query"}))
seen = dw.read_seen()
check("the ruckus-deprecated header is kept with its replacement",
      seen.get("GET /entitlements", {}).get("replacement") == "/entitlements/query")
dw.observe("GET /venues", FakeResponse(405, {"Allow": "POST"}))
check("a 405 is kept as gone, with what R1 allows",
      dw.read_seen()["GET /venues"]["gone_status"] == 405 and dw.read_seen()["GET /venues"]["allow"] == "POST")
dw.observe("POST /venues/query", FakeResponse(200, {"content-type": "application/json"}))
check("a clean response records nothing", "POST /venues/query" not in dw.read_seen())
dw.observe("GET /mspLabels", FakeResponse(200, {"ruckus-current-version": "application/json"}))
check("current-version equal to what we sent is not a signal", "GET /mspLabels" not in dw.read_seen())
dw.observe("GET /x", FakeResponse(200, {"ruckus-current-version": "application/vnd.ruckus.v1.1+json"},
                                   sent={"Accept": "application/vnd.ruckus.v1+json"}))
check("a newer current-version than we sent is kept", dw.read_seen().get("GET /x", {}).get("current_version"))
dw.observe("GET /y", FakeResponse(200, {"ruckus-current-version": "application/vnd.ruckus.v1+json"}))
check("'current is v1' (what plain JSON gets) is not a signal", "GET /y" not in dw.read_seen())
before = dw.read_seen()["GET /entitlements"]["count"]
dw.observe("GET /entitlements", FakeResponse(200, {"ruckus-deprecated": "/entitlements/query"}))
check("repeat signals are throttled, not written per call", dw.read_seen()["GET /entitlements"]["count"] == before)


class Boom:
    @property
    def headers(self):
        raise RuntimeError("no")

dw.observe("GET /boom", Boom())
check("a broken response never raises out of observe", True)

print("spec reading")
spec = {"paths": {
    "/venues": {"get": {"deprecated": True, "description": "Access Venues. This method will be removed no sooner than 08/31/2026."},
                "post": {"summary": "Request Venue"}},
    "/venues/{venueId}": {"get": {"summary": "Access Venue by ID"}},
    "/venues/apGroups": {"get": {"deprecated": True, "description": "This method will be removed no sooner than 08/31/2026. The following URL /venues/apGroups/query can be used for this content."}},
    "/mspecs/query": {"post": {"deprecated": True, "description": "This method will be removed no sooner than 12/31/2026. The following URL POST /tenants/query can be used for this content."}},
    "/venues/{venueId}/apRadioSettings": {"get": {"description": "The application/vnd.ruckus.v1+json will be removed no sooner than 08/31/2026. Both application/json and application/vnd.ruckus.v1.1+json are now available."}},
    "/identityGroups": {"post": {"deprecated": True, "description": "This method will be removed no sooner than 08/31/2026 and application/json is currently tied to application/vnd.ruckus.v1+json which will be moved to application/vnd.ruckus.v1.1+json on 08/31/2026."}},
    "/later": {"get": {"deprecated": True, "description": "This method will be removed no sooner than 06/30/2027."}},
}}
tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump(spec, tmp)
tmp.close()
idx = audit._spec_index(tmp.name, os.path.getmtime(tmp.name))["ops"]
check("route removal date read", idx[("get", "/venues")]["removal_date"] == date(2026, 8, 31))
check("a media-type notice is not a route removal", idx[("get", "/venues/{venueId}/apRadioSettings")]["removal_date"] is None)
check("'The application/vnd... will be removed' is a media removal",
      idx[("get", "/venues/{venueId}/apRadioSettings")]["media_removals"][0]["media_type"] == "application/vnd.ruckus.v1+json")
check("replacement read", idx[("get", "/venues/apGroups")]["replacement"] == "/venues/apGroups/query")
check("replacement read past a method word", idx[("post", "/mspecs/query")]["replacement"] == "/tenants/query")
check("application/json moving to v1.1 read", idx[("post", "/identityGroups")]["default_move"]["to"] == "application/vnd.ruckus.v1.1+json")

print("matching")
check("an id matches the spec's id over a literal",
      audit.match_spec("get", "/venues/{x}", idx) == "/venues/{venueId}")
check("a literal beats an id", audit.match_spec("get", "/venues/apGroups", idx) == "/venues/apGroups")
check("method matters", audit.match_spec("delete", "/venues", idx) is None)

print("audit")
# The synthetic spec and calls are judged on their own: no hand-reviewed or
# retired entries from the real lists.
audit.REVIEWED, audit.RETIRED = {}, {}
audit.latest_spec = lambda: (tmp.name, date(2026, 11, 11))
audit.code_calls = lambda root=None: {
    ("get", "/venues"): {"locations": {"a.py:1"}, "tools": {"x"}, "media_types": set()},
    ("get", "/venues/{x}"): {"locations": {"a.py:2"}, "tools": {"x"}, "media_types": set()},
    ("post", "/mspecs/query"): {"locations": {"a.py:3"}, "tools": {"x"}, "media_types": set()},
    ("get", "/later"): {"locations": {"a.py:4"}, "tools": {"x"}, "media_types": set()},
    ("get", "/nowhere"): {"locations": {"a.py:5"}, "tools": {"x"}, "media_types": set()},
    ("get", "/venues/{x}/apRadioSettings"): {"locations": {"a.py:6"}, "tools": {"x"}, "media_types": {"application/vnd.ruckus.v1+json"}},
}
audit.observed_calls = lambda days=30: {}
report = audit.run_audit(today=date(2026, 11, 11))
by = {f"{f['method']} {f['path']}": f for f in report["findings"]}
check("gone live is critical", by["GET /venues"]["severity"] == "critical")
check("a clean route is not a finding", "GET /venues/{x}" not in by)
check("removal within 90 days is high", by["POST /mspecs/query"]["severity"] == "high")
check("removal beyond 90 days is medium", by["GET /later"]["severity"] == "medium")
check("a call missing from the spec is medium", by["GET /nowhere"]["severity"] == "medium")
check("sending a media type R1 is dropping is high", by["GET /venues/{x}/apRadioSettings"]["severity"] == "high")
saved = audit.save_report(report)
check("the first run reports everything it found as changed", saved["first_run"] and len(saved["changed"]) == len(report["findings"]))
check("an unchanged second run notifies nothing", audit.save_report(audit.run_audit(today=date(2026, 11, 11)))["changed"] == [])

pending = audit.unnotified(report)
check("Slack owes the critical and high findings", {f"{f['method']} {f['path']}" for f in pending} == {"GET /venues", "POST /mspecs/query", "GET /venues/{x}/apRadioSettings", "GET /entitlements"})
audit.mark_notified(pending)
check("once told, Slack owes nothing", audit.unnotified(report) == [])
report["findings"][-1]["severity"] = "high"  # a medium one gets worse
check("a finding that gets worse is owed again", len(audit.unnotified(report)) == 1)

print("banner: live flags on replaced calls")
audit.RETIRED = {("get", "/venues"): {"since": "2026-11-11T15:30:00+00:00", "replaced_by": "POST /venues/query"}}
audit.REVIEWED = {("get", "/venues/{x}/apRadioSettings"): {"date": "2026-11-11", "note": "pinned v1.1", "spec": "s.json"}}
gone = {"gone_status": 405}
check("a flag from before the replacement is history",
      audit.settled_note("GET", "/venues", {**gone, "last_seen": "2026-11-11T15:17:59+00:00"}) is not None)
check("a flag after the replacement still alarms (something still calls it)",
      audit.settled_note("GET", "/venues", {**gone, "last_seen": "2026-11-12T09:00:00+00:00"}) is None)
check("a media notice on a reviewed call is settled",
      audit.settled_note("GET", "/venues/{id}/apRadioSettings", {"current_version": "v1.1"}) is not None)
check("a reviewed call that R1 then deprecates alarms",
      audit.settled_note("GET", "/venues/{id}/apRadioSettings", {"replacement": "/x"}) is None)
check("an unknown call is never settled", audit.settled_note("GET", "/aps", {**gone}) is None)

os.unlink(tmp.name)
print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
