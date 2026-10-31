"""
R1 call timing: route labels, async seconds, Redis totals, and that a metrics
failure cannot reach a real call. Uses a throwaway Redis key hour-space only
through a fake client, so it writes nothing real.

    docker compose exec -T -w /app backend python scripts/test_r1_call_metrics.py
"""

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")

from r1api import call_metrics as m


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code, self._body = status_code, body
        self.ok = status_code < 400

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a):
            self.ops.append((name, a))
        return op

    def execute(self):
        return [getattr(self.r, n)(*a) for n, a in self.ops]


class FakeRedis:
    def __init__(self):
        self.h, self.expired = {}, set()

    def pipeline(self, transaction=False):
        return FakePipe(self)

    def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = str(v)

    def hincrby(self, k, f, n):
        d = self.h.setdefault(k, {})
        d[f] = str(int(d.get(f, 0)) + n)

    def hincrbyfloat(self, k, f, n):
        d = self.h.setdefault(k, {})
        d[f] = str(float(d.get(f, 0)) + n)

    def expire(self, k, s):
        self.expired.add(k)

    def hgetall(self, k):
        return dict(self.h.get(k, {}))


failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'ok' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail and not ok else ""))
    failures += 0 if ok else 1


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    m._flusher = object()   # no background thread in a test
    fake = FakeRedis()
    m._client = fake

    print("Route labels")
    uuid = "68a8fbf1-b302-4b53-a89a-a1a441a33d61"
    check("uuids go, template numbers stay",
          m.route_label(f"/policyTemplates/100/policies/{uuid}") == "/policyTemplates/100/policies/{id}")
    check("32-hex ids and serials go",
          m.route_label("/venues/2d238dabb2524bc8be70396ae356a906/aps/8220116012780558")
          == "/venues/{id}/aps/{id}")
    check("a name in a path does not reach Redis",
          "gmail" not in m.route_label("/identities/someone@gmail.com")
          and "gmail" not in m.route_label("/identities/someone%40gmail.com"))
    check("query strings go", m.route_label("/venues?a=1") == "/venues")

    print("Status groups")
    check("429 and 401 keep their own rows",
          m.status_group(429) == "429" and m.status_group(401) == "401" and m.status_group(404) == "4xx")

    print("Async seconds")
    m._sent.clear(), m._totals.clear()
    m.record_call("post", "/venues", FakeResponse(202, {"requestId": "r1"}), 0.2)
    m._sent["r1"] = (time.time() - 40, m._sent["r1"][1])
    sent_at = m._sent["r1"][0]
    m.record_call("post", "/activities/query", FakeResponse(200, {"data": [
        {"requestId": "r1", "status": "INPROGRESS", "useCase": "ADD_VENUE"},
    ]}), 0.1)
    check("an unfinished activity stays tracked", "r1" in m._sent)
    m.record_call("post", "/activities/query", FakeResponse(200, {"data": [
        {"requestId": "other", "status": "SUCCESS", "useCase": "X"},
        {"requestId": "r1", "status": "SUCCESS", "useCase": "ADD_VENUE",
         "startDatetime": iso(sent_at + 10), "endDatetime": iso(sent_at + 30)},
    ]}), 0.1)
    hour = m._hour_now()
    a = m._totals.get((hour, m.KIND_ASYNC, "ADD_VENUE", "success"))
    w = m._totals.get((hour, m.KIND_ASYNC_WORK, "ADD_VENUE", "success"))
    check("202 to endDatetime is the headline", a and 28 <= a["total"] <= 31, str(a))
    check("R1's own start to end is kept beside it", w and w["total"] == 20, str(w))
    s = m._totals.get((hour, m.KIND_ASYNC_SEEN, "ADD_VENUE", "success"))
    check("202 to when we read it done counts the time since R1 ended",
          s and 39 <= s["total"] <= 42, str(s))
    check("a finished request is not counted twice", "r1" not in m._sent)
    check("an activity we did not send is ignored",
          (hour, m.KIND_ASYNC, "X", "success") not in m._totals)

    m.record_call("get", "/activities/r2", FakeResponse(200, {"id": "x"}), 0.1)
    m.record_call("post", "/x", FakeResponse(202, {"requestId": "r2"}), 0.1)
    m.record_call("get", "/activities/r2", FakeResponse(200, {
        "requestId": "r2", "status": "FAIL", "useCase": "BROKEN"}), 0.1)
    check("a single GET read closes one out too, as a failure",
          (hour, m.KIND_ASYNC, "BROKEN", "fail") in m._totals)
    m.record_call("post", "/x", FakeResponse(202, {"accepted": True}), 0.1)
    m.record_call("post", "/x", FakeResponse(202), 0.1)
    check("a 202 with no requestId is just a call", len(m._sent) == 0, str(m._sent))

    print("Totals")
    m._sent.clear(), m._totals.clear()
    for seconds in (0.1, 0.4, 8):
        m.record_call("put", f"/policySets/{uuid}/prioritizedPolicies/{uuid}",
                      FakeResponse(200, {}), seconds)
    m.record_call("put", f"/policySets/{uuid}/prioritizedPolicies/{uuid}", FakeResponse(500, {}), 12)
    m.flush()
    check("flushing empties memory", not m._totals)
    check("the hour key expires", f"{m.KEY_PREFIX}{hour}" in fake.expired)
    m.record_call("put", f"/policySets/{uuid}/prioritizedPolicies/{uuid}", FakeResponse(200, {}), 1)
    m.flush()
    rows = m.read_summary(1)
    label = "PUT /policySets/{id}/prioritizedPolicies/{id}"
    ok = next(r for r in rows if r["label"] == label and r["status"] == "2xx")
    bad = next(r for r in rows if r["label"] == label and r["status"] == "5xx")
    check("two flushes add up", ok["count"] == 4 and abs(ok["total_seconds"] - 9.5) < 0.2, str(ok))
    check("a later smaller call keeps the max", ok["max_seconds"] == 8, str(ok))
    check("500s have their own row and do not blur the 200s", bad["count"] == 1 and bad["max_seconds"] == 12)
    check("median and p90 read from the buckets",
          ok["median_under_seconds"] == 0.5 and ok["p90_under_seconds"] == 8, str(ok))

    print("UTC hours")
    by_hour = m.read_hourly(m.KIND_CALL, label, "2xx", 2)
    check("one row per UTC hour, labelled in UTC",
          len(by_hour) == 1 and by_hour[0]["count"] == 4
          and by_hour[0]["hour_utc"] == time.strftime("%Y-%m-%dT%H:00Z", time.gmtime()),
          str(by_hour))
    check("an hour with no calls is left out",
          m.read_hourly(m.KIND_CALL, "GET /never", "2xx", 2) == [])

    print("Row cap")
    m._totals.clear()
    for i in range(m.MAX_ROWS_PER_HOUR + 5):
        m.record(m.KIND_CALL, f"GET /r{i}", "2xx", 0.1)
    check("rows past the cap fold into one",
          len(m._totals) == m.MAX_ROWS_PER_HOUR + 1
          and m._totals[(hour, m.KIND_CALL, "other", "2xx")]["count"] == 5, str(len(m._totals)))
    m._totals.clear()

    print("Failures stay here")

    class Broken(FakeResponse):
        @property
        def status_code(self):
            raise RuntimeError("boom")

        @status_code.setter
        def status_code(self, v):
            pass

    try:
        m.record_call("get", "/x", Broken(200, {}), 0.1)
        m.record("call", None, "2xx", "not a number")
        check("a bad record never raises", True)
    except Exception as e:
        check("a bad record never raises", False, str(e))

    class Down:
        def pipeline(self, transaction=False):
            raise ConnectionError("redis down")

    m._client = Down()
    m.record(m.KIND_CALL, "GET /y", "2xx", 0.1)
    try:
        m.flush()
        check("Redis being down never raises", True)
    except Exception as e:
        check("Redis being down never raises", False, str(e))
    m._client = None

    print(f"\n{'FAILED (' + str(failures) + ')' if failures else 'All checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
