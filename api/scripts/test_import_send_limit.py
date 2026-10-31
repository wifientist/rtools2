"""
Passphrase sends free their place while R1 works, and the shared activity poller
no longer reads each success a second time. No R1 involved.

    docker compose exec -T -w /app backend python scripts/test_import_send_limit.py
"""

import asyncio
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from r1api.client import _ActivityWatch
from r1api.services.dpsk import DpskService
from workflow.phases.cloudpath.passphrases import with_send_limit

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'ok' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
    failures += 0 if ok else 1


async def test_send_limit():
    print("Send limit")
    state = {"sending": 0, "peak_sending": 0, "waiting": 0, "peak_waiting": 0}

    async def create(item, release):
        state["sending"] += 1
        state["peak_sending"] = max(state["peak_sending"], state["sending"])
        await asyncio.sleep(0)
        state["sending"] -= 1
        release()
        state["waiting"] += 1
        state["peak_waiting"] = max(state["peak_waiting"], state["waiting"])
        await asyncio.sleep(0.01)
        state["waiting"] -= 1
        return item

    limited = with_send_limit(create, 2)
    results = await asyncio.gather(*[limited(i) for i in range(20)])
    check("sends stay within the limit", state["peak_sending"] <= 2, str(state))
    check("waiting does not hold a place", state["peak_waiting"] > 2, str(state))
    check("every item finishes", sorted(results) == list(range(20)))

    held = {"now": 0, "peak": 0}

    async def never_releases(item, release):
        held["now"] += 1
        held["peak"] = max(held["peak"], held["now"])
        await asyncio.sleep(0)
        held["now"] -= 1

    limited = with_send_limit(never_releases, 2)
    await asyncio.gather(*[limited(i) for i in range(10)])
    check("work that never releases keeps its place to the end", held["peak"] == 2, str(held))

    async def explodes(item, release):
        release()
        release()
        raise RuntimeError("no")

    limited = with_send_limit(explodes, 1)
    outcome = await asyncio.gather(*[limited(i) for i in range(3)], return_exceptions=True)
    check("a failure or a double release cannot lose or add a place",
          all(isinstance(o, RuntimeError) for o in outcome))

    after = {"now": 0, "peak": 0}
    calls = {"n": 0}

    async def fails_then_works(item, release):
        calls["n"] += 1
        if calls["n"] <= 3:
            release()
            release()
            raise RuntimeError("no")
        after["now"] += 1
        after["peak"] = max(after["peak"], after["now"])
        await asyncio.sleep(0)
        after["now"] -= 1

    limited = with_send_limit(fails_then_works, 1)
    await asyncio.gather(*[limited(i) for i in range(8)], return_exceptions=True)
    check("and the limit still holds afterwards", after["peak"] == 1, str(after))


async def test_on_accepted():
    print("create_passphrase")
    order = []

    class FakeResponse:
        status_code = 400
        ok = False

    class FakeClient:
        ec_type = "EC"

        def post(self, path, payload=None, override_tenant_id=None):
            order.append("post")
            return FakeResponse()

        def safe_json(self, response):
            order.append("read")
            raise RuntimeError("R1 said no")

    service = DpskService(FakeClient())
    try:
        await service.create_passphrase(
            pool_id="p", user_name="u", passphrase="x", on_accepted=lambda: order.append("freed")
        )
    except RuntimeError:
        pass
    check("the place is freed after the POST answers, even a refusal",
          order == ["post", "freed", "read"], str(order))


async def test_poller():
    print("Activity poller")
    reads = []
    rows = {
        "ok": {"requestId": "ok", "status": "SUCCESS", "useCase": "A"},
        "bad": {"requestId": "bad", "status": "FAIL", "error": "row"},
        "busy": {"requestId": "busy", "status": "INPROGRESS"},
    }

    class FakeClient:
        def _query_activities_since(self, ids, from_time, tenant_id):
            return {rid: rows[rid] for rid in ids if rid in rows}

        def _read_activity(self, rid, tenant_id):
            reads.append(rid)
            return {"requestId": rid, "status": "FAIL", "error": "full"}

    watch = _ActivityWatch(FakeClient(), "t", asyncio.get_running_loop())
    now = watch.loop.time()
    watch.waiting = {"ok": now, "bad": now, "busy": now, "unseen": now}
    await watch._poll()
    check("a success is not read a second time", "ok" not in reads, str(reads))
    check("and is answered from the bulk row", watch.latest["ok"]["useCase"] == "A")
    check("a failure still gets the full read", reads == ["bad"] and watch.latest["bad"]["error"] == "full", str(reads))
    check("something still running is left alone", watch.latest["busy"]["status"] == "INPROGRESS")
    check("one not yet seen is not read until it has been missing a while", "unseen" not in reads)


async def main():
    await test_send_limit()
    await test_on_accepted()
    await test_poller()
    print(f"\n{'FAILED (' + str(failures) + ')' if failures else 'All checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
