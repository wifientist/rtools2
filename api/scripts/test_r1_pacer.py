"""
The per-tenant R1 rate limit: holds the rate, pauses and halves on a 429, climbs
back slowly, keeps tenants apart. Uses its own clock, so nothing sleeps.

    docker compose exec -T -w /app backend python scripts/test_r1_pacer.py
"""

import asyncio
import sys

from requests.structures import CaseInsensitiveDict

sys.path.insert(0, ".")

from r1api import pacer as p
from r1api.client import R1Client, _rate_limit_delay
from r1api.services.policy_sets import PolicySetService

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'ok' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
    failures += 0 if ok else 1


def main():
    ceiling = p.DEFAULT_CALLS_PER_MINUTE / 60

    print("Rate")
    t = p.TenantPacer(ceiling, now=0)
    waits = [t.reserve(now=0) for _ in range(p.BURST_CALLS)]
    check("a burst goes straight through", max(waits) == 0, str(max(waits)))
    wait = t.reserve(now=0)
    check("the next call waits one token's time", abs(wait - 1 / ceiling) < 0.001, str(wait))

    t = p.TenantPacer(ceiling, now=0)
    last = 0
    for _ in range(p.BURST_CALLS + p.DEFAULT_CALLS_PER_MINUTE):
        last = t.reserve(now=0)
    check("a minute's calls past the burst finish about a minute later", 59 <= last <= 61, str(last))

    print("429")
    t = p.TenantPacer(ceiling, now=0)
    check("the first 429 halves the rate", t.rate_limited(None, now=10) and t.rate == ceiling / 2, str(t.rate))
    check("everyone waits out the pause", t.reserve(now=10) >= p.DEFAULT_PAUSE_SECONDS)
    check("workers in the same burst do not halve again",
          not t.rate_limited(None, now=11) and t.rate == ceiling / 2, str(t.rate))
    t2 = p.TenantPacer(ceiling, now=0)
    t2.rate_limited(30, now=1)
    check("Retry-After is honoured", t2.reserve(now=2) >= 28)
    t3 = p.TenantPacer(ceiling, now=0)
    now = 0
    for _ in range(12):
        now += p.CUT_COOLDOWN_SECONDS + 1
        t3.rate_limited(None, now=now)
    check("the rate has a floor", t3.rate == p.MIN_CALLS_PER_SECOND, str(t3.rate))

    print("Recovery")
    t = p.TenantPacer(ceiling, now=0)
    t.rate_limited(None, now=0)
    t.reserve(now=20)
    check("no climbing right after a 429", t.rate == ceiling / 2, str(t.rate))
    t.reserve(now=100)
    climbing = t.rate
    check("then a gradual climb", ceiling / 2 < climbing < ceiling, str(climbing))
    t.reserve(now=2000)
    check("back to the ceiling, not past it", t.rate == ceiling, str(t.rate))

    print("Tenants")
    p._pacers.clear()
    check("each tenant has its own budget", p.pacer_for("a") is not p.pacer_for("b"))
    check("and a tenant keeps it", p.pacer_for("a") is p.pacer_for("a"))
    p.pacer_for("a").rate_limited(None)
    check("a 429 on one tenant leaves the other alone", p.pacer_for("b").rate == ceiling)

    print("Event loop")
    p._pacers.clear()
    pacer = p.pacer_for("loop")
    pacer.tokens = -100
    check("a call on the event loop never sleeps", p.wait_for_turn("loop", may_sleep=False) == 0)

    print("R1's 429")

    class Response:
        def __init__(self, status_code, headers=None, content=b""):
            self.status_code, self.content = status_code, content
            self.headers = CaseInsensitiveDict(headers or {})
            self.ok = status_code < 400
            self.text = content.decode()

        def json(self):
            raise ValueError("no body")

    r1_header = "x-rate-limit-retry-after-seconds"
    check("waits as long as R1 asks", _rate_limit_delay(Response(429, {r1_header: "14"}), 1) == 14)
    check("still reads Retry-After", _rate_limit_delay(Response(429, {"Retry-After": "9"}), 1) == 9)
    check("backs off without either", _rate_limit_delay(Response(429), 2) == 4)
    check("a zero is still a short wait", _rate_limit_delay(Response(429, {r1_header: "0"}), 3) == 1)

    class Client:
        ec_type = "MSP"

        def __init__(self, response):
            self.response = response

        def put(self, *a, **k):
            return self.response

        safe_json = R1Client.safe_json

    def add_to_set(response):
        return asyncio.run(PolicySetService(Client(response)).assign_policy_to_policy_set(
            "set", "policy", tenant_id="t", priority=1,
        ))

    check("an empty 200 is assigned", add_to_set(Response(200)) == {"status": "assigned"})
    try:
        result = add_to_set(Response(429))
    except Exception as err:
        result = str(err)
    check("an empty 429 is not assigned", "(429)" in str(result), str(result))

    print(f"\n{'FAILED (' + str(failures) + ')' if failures else 'All checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
