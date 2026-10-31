"""
Venue cleanup: policy removals take turns per set, retry R1's 500s, a set delete
can make them unnecessary, passphrases go in batches, and a stop is honoured
mid-phase. Fake R1 only.

    docker compose exec -T -w /app backend python scripts/test_cleanup_delete.py
"""

import asyncio
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from workflow.phases.cleanup import delete_access_policies as dap
from workflow.phases.cleanup import delete_passphrases as dpp
from workflow.phases.cleanup.inventory import ResourceInventory

dap.REMOVE_RETRY_SECONDS = 0

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'ok' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
    failures += 0 if ok else 1


class FakeState:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled

    async def is_cancelled(self, job_id):
        return self.cancelled


class FakePolicySets:
    def __init__(self, set_delete_error=None, fail_first_remove=False, always_500=False):
        self.calls, self.now, self.peak_by_set = [], {}, {}
        self.set_delete_error = set_delete_error
        self.fail_first = fail_first_remove
        self.always_500 = always_500
        self.removed = set()

    async def delete_policy_set(self, policy_set_id, **kw):
        self.calls.append("delete_set")
        if self.set_delete_error:
            raise RuntimeError(self.set_delete_error)

    async def remove_policy_from_policy_set(self, policy_set_id, policy_id, **kw):
        self.calls.append("remove")
        self.now[policy_set_id] = self.now.get(policy_set_id, 0) + 1
        self.peak_by_set[policy_set_id] = max(
            self.peak_by_set.get(policy_set_id, 0), self.now[policy_set_id]
        )
        await asyncio.sleep(0)
        self.now[policy_set_id] -= 1
        if self.always_500 or self.fail_first:
            self.fail_first = False
            raise RuntimeError("R1 API error (500): Internal Server Error")
        self.removed.add(policy_id)

    async def delete_template_policy(self, template_id, policy_id, **kw):
        self.calls.append("delete_policy")
        return True


def make(cls, r1, state=None, tracker=None):
    phase = cls.__new__(cls)
    phase.r1_client, phase.tenant_id, phase.job_id = r1, "t", "j"
    phase.phase_id = "p"
    phase.context = SimpleNamespace(state_manager=state or FakeState(), activity_tracker=tracker)
    phase.messages, phase.progress = [], []

    async def emit(message, level="info", *a, **k):
        phase.messages.append(message)

    async def emit_progress(current, total, failed=0, item_name="", detail=""):
        phase.progress.append((item_name, current, total, failed))

    phase.emit, phase.emit_progress = emit, emit_progress
    return phase


def policies(n, set_id="s1"):
    return [{"id": f"p{i}", "name": f"name{i}", "policy_set_id": set_id, "template_id": "100"}
            for i in range(n)]


def inv(pols, sets=()):
    return dap.DeleteAccessPoliciesPhase.Inputs(inventory=ResourceInventory(
        policies=pols, policy_sets=list(sets)))


async def main():
    print("Removing from a set")
    ps = FakePolicySets(set_delete_error="R1 API error (409): still has policies")
    r1 = SimpleNamespace(policy_sets=ps, radius_attributes=None, dpsk=None, identity=None)
    phase = make(dap.DeleteAccessPoliciesPhase, r1)
    pols = policies(6, "s1") + policies(6, "s2")
    for i, p in enumerate(pols):
        p["id"] = f"x{i}"
    out = await phase.execute(inv(pols, [{"id": "s1", "name": "A"}, {"id": "s2", "name": "B"}]))
    check("one at a time within each set", ps.peak_by_set == {"s1": 1, "s2": 1}, str(ps.peak_by_set))
    check("every policy removed and deleted",
          len(ps.removed) == 12 and ps.calls.count("delete_policy") == 12 and out.policies_deleted == 12)
    check("counts reach the monitor",
          ("policy removed from set", 12, 12, 0) in phase.progress
          and ("policy deleted", 12, 12, 0) in phase.progress)

    ps = FakePolicySets(fail_first_remove=True, set_delete_error="R1 API error (409): no")
    r1.policy_sets = ps
    out = await make(dap.DeleteAccessPoliciesPhase, r1).execute(inv(policies(2), [{"id": "s1", "name": "A"}]))
    check("a 500 is retried and leaves no removal failure",
          len(ps.removed) == 2 and not [f for f in out.failed if f["type"] == "policy assignment"],
          str(out.failed))

    print("Deleting the set first")
    ps = FakePolicySets()
    r1.policy_sets = ps
    phase = make(dap.DeleteAccessPoliciesPhase, r1)
    out = await phase.execute(inv(policies(5), [{"id": "s1", "name": "A"}]))
    check("a set that deletes whole needs no removals", ps.calls.count("remove") == 0, str(ps.calls[:8]))
    check("its policies still get deleted", ps.calls.count("delete_policy") == 5)
    check("and the set reads as deleted", out.policy_sets_deleted == 1, str(out))

    ps = FakePolicySets(set_delete_error="R1 API error (500): boom")
    r1.policy_sets = ps
    out = await make(dap.DeleteAccessPoliciesPhase, r1).execute(inv(policies(2), [{"id": "s1", "name": "A"}]))
    check("a refused first try is not reported until the real attempt",
          sum(1 for f in out.failed if f["type"] == "policy set") == 1 and len(ps.removed) == 2, str(out.failed))

    print("Stop")
    ps = FakePolicySets(set_delete_error="R1 API error (409): no")
    r1.policy_sets = ps
    phase = make(dap.DeleteAccessPoliciesPhase, r1, FakeState(cancelled=True))
    await phase.execute(inv(policies(5), [{"id": "s1", "name": "A"}]))
    check("a stop stops removals and deletes mid-phase",
          ps.calls.count("remove") == 0 and ps.calls.count("delete_policy") == 0, str(ps.calls))

    print("Passphrases")

    class FakeDpsk:
        def __init__(self, fail_batches=False, missing=()):
            self.batches, self.fail_batches, self.missing = [], fail_batches, set(missing)

        async def delete_passphrases(self, pool_id, passphrase_ids, **kw):
            self.batches.append((pool_id, list(passphrase_ids)))
            if self.fail_batches and len(passphrase_ids) > 1:
                raise RuntimeError("R1 API error (400): too many")
            if self.missing & set(passphrase_ids):
                raise RuntimeError("Passphrase not found")
            return {"requestId": f"r{len(self.batches)}"}

    class Tracker:
        def __init__(self, fail_first=False):
            self.fail = fail_first

    def pps(n, pool):
        return [{"id": f"{pool}-{i}", "pool_id": pool, "username": f"user{i}@x.com"} for i in range(n)]

    def pp_inputs(items):
        return dpp.DeletePassphrasesPhase.Inputs(inventory=ResourceInventory(passphrases=items))

    d = FakeDpsk()
    phase = make(dpp.DeletePassphrasesPhase, SimpleNamespace(dpsk=d))
    out = await phase.execute(pp_inputs(pps(250, "a") + pps(30, "b")))
    check("batched per pool", sorted(len(b[1]) for b in d.batches) == [30, 50, 100, 100], str([len(b[1]) for b in d.batches]))
    check("nothing lost", out.deleted_count == 280 and out.failed_count == 0, str(out))
    check("counts reach the monitor", phase.progress[-1][1] == 280 and phase.progress[-1][0] == "passphrase")

    d = FakeDpsk(fail_batches=True, missing=["a-1"])
    phase = make(dpp.DeletePassphrasesPhase, SimpleNamespace(dpsk=d))
    out = await phase.execute(pp_inputs(pps(3, "a")))
    check("a refused batch falls back to one at a time, and not found is success",
          out.deleted_count == 3 and out.failed_count == 0, str(out))

    responses = []

    async def waited_fail(self, req_id):
        responses.append(req_id)
        return SimpleNamespace(success=len(responses) > 1, error="R1 said no")

    original = dpp.DeletePassphrasesPhase.fire_and_wait
    dpp.DeletePassphrasesPhase.fire_and_wait = waited_fail
    d = FakeDpsk()
    phase = make(dpp.DeletePassphrasesPhase, SimpleNamespace(dpsk=d), tracker=object())
    out = await phase.execute(pp_inputs(pps(2, "a")))
    dpp.DeletePassphrasesPhase.fire_and_wait = original
    check("R1 failing a batch afterwards is noticed and retried singly",
          len(d.batches) == 3 and out.deleted_count == 2, f"{d.batches} {out}")

    d = FakeDpsk()
    phase = make(dpp.DeletePassphrasesPhase, SimpleNamespace(dpsk=d), FakeState(cancelled=True))
    out = await phase.execute(pp_inputs(pps(5, "a")))
    check("a stop sends nothing", d.batches == [] and out.failed_count == 0, str(d.batches))

    print("R1 failing a delete afterwards")
    ps = FakePolicySets()
    ps.set_delete_error = None

    async def accepted(template_id, policy_id, **kw):
        ps.calls.append("delete_policy")
        return {"requestId": f"req-{policy_id}"}

    ps.delete_template_policy = accepted
    r1.policy_sets = ps

    async def r1_says(self, req_id):
        # R1's real wording: it says "not found", and the policy is still there.
        if req_id == "req-p1":
            return SimpleNamespace(success=False, unverified=False, error=(
                "Un-assign Radius Attribute Group failed ... was not found"))
        return SimpleNamespace(success=True, unverified=False, error=None)

    original = dap.DeleteAccessPoliciesPhase.fire_and_wait
    dap.DeleteAccessPoliciesPhase.fire_and_wait = r1_says
    phase = make(dap.DeleteAccessPoliciesPhase, r1, tracker=object())
    out = await phase.execute(inv(policies(3), [{"id": "s1", "name": "A"}]))
    check("a policy R1 failed to delete is failed, even when R1 says 'not found'",
          out.policies_deleted == 2 and [f["name"] for f in out.failed] == ["name1"],
          f"{out.policies_deleted} {out.failed}")

    async def r1_unknown(self, req_id):
        return SimpleNamespace(success=False, unverified=True, error="never learned")

    dap.DeleteAccessPoliciesPhase.fire_and_wait = r1_unknown
    phase = make(dap.DeleteAccessPoliciesPhase, r1, tracker=object())
    out = await phase.execute(inv(policies(1), [{"id": "s1", "name": "A"}]))
    dap.DeleteAccessPoliciesPhase.fire_and_wait = original
    check("not learning the outcome is not reported as a failure", not out.failed, str(out.failed))

    print("Inventory paging")
    from workflow.phases.cleanup.inventory import _all_identities_in_group, IDENTITY_PAGE_SIZE

    class FakeIdentity:
        def __init__(self, total):
            self.total, self.pages = total, []

        async def get_identities_in_group(self, group_id, tenant_id, page, size):
            self.pages.append(page)
            return {"content": [{"id": i} for i in range(self.total)][page * size:(page + 1) * size]}

    fake = FakeIdentity(IDENTITY_PAGE_SIZE * 2 + 7)
    rows = await _all_identities_in_group(SimpleNamespace(identity=fake), "g", "t")
    check("every page is read", len(rows) == IDENTITY_PAGE_SIZE * 2 + 7 and fake.pages == [0, 1, 2], str(fake.pages))

    print("Verify")
    from workflow.phases.cleanup import verify as ver
    ver.SECOND_LOOK_SECONDS = 0

    def audit_returning(*snapshots):
        calls = []

        async def audit(self, pattern, all_networks=False):
            calls.append(1)
            return snapshots[min(len(calls), len(snapshots)) - 1]

        return audit, calls

    listed = ResourceInventory(
        policies=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        identity_groups=[{"id": "g"}],
    )
    original_audit = ver.InventoryPhase._audit_venue

    def verify_phase():
        phase = make(ver.VerifyCleanupPhase, SimpleNamespace())
        phase.venue_id = None
        phase.context.r1_client = None
        phase.context.venue_id = None
        phase.context.tenant_id = "t"
        phase.context.job_id = "j"
        return phase

    # Not ours: "other" was never in the inventory and must not be reported.
    leftover = ResourceInventory(policies=[{"id": "a"}, {"id": "other"}], identity_groups=[{"id": "g"}])
    audit, calls = audit_returning(leftover, leftover)
    ver.InventoryPhase._audit_venue = audit
    phase = verify_phase()
    out = await phase.execute(ver.VerifyCleanupPhase.Inputs(inventory=listed))
    check("counts what is really left of the inventory, and nothing else",
          out.total_deleted == 2 and out.total_failed == 2
          and out.summary["policies"] == {"deleted": 2, "failed": 1, "skipped": 0}, str(out))
    check("says so", any("still in R1" in m for m in phase.messages), str(phase.messages))
    check("takes a second look before believing it", len(calls) == 2)

    audit, calls = audit_returning(leftover, ResourceInventory())
    ver.InventoryPhase._audit_venue = audit
    out = await verify_phase().execute(ver.VerifyCleanupPhase.Inputs(inventory=listed))
    check("a lagging read clears on the second look", out.total_failed == 0 and out.total_deleted == 4, str(out))

    async def broken(self, pattern, all_networks=False):
        raise RuntimeError("R1 down")

    ver.InventoryPhase._audit_venue = broken
    out = await verify_phase().execute(ver.VerifyCleanupPhase.Inputs(inventory=listed))
    check("not being able to look is not reported as clean", out.verified is False)
    ver.InventoryPhase._audit_venue = original_audit

    print(f"\n{'FAILED (' + str(failures) + ')' if failures else 'All checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
