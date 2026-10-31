#!/usr/bin/env python3
"""
The import waits on R1's tasks: a failed policy create is seen at once and tried
again, a task still running is never re-sent, and a rename R1 confirmed is not
sent twice.

    docker compose exec -T -w /app backend python scripts/test_import_task_waits.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.create_access_policies import CreateAccessPoliciesPhase

failures = 0


def check(label, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))


class Radius:
    async def query_radius_attribute_groups(self, **k):
        return {"content": [{"id": "g", "name": "speed"}]}


class PolicySets:
    def __init__(self, members=()):
        self.creates = 0
        self.conditions = 0
        self.priorities = []
        self.members = members

    async def query_policy_sets(self, **k):
        return {"content": [{"id": "set", "name": "Set"}]}

    async def query_template_policies(self, **k):
        return {"content": []}

    async def get_prioritized_policies(self, **k):
        if isinstance(self.members, Exception):
            raise self.members
        return list(self.members)

    async def create_template_policy(self, **k):
        self.creates += 1
        return {"id": f"p{self.creates}", "requestId": f"r{self.creates}"}

    async def await_policy_creation(self, **k):
        raise AssertionError("the create's task should be waited on instead")

    async def create_string_condition(self, **k):
        self.conditions += 1

    async def assign_policy_to_policy_set(self, priority=None, **k):
        self.priorities.append(priority)


class Identity:
    def __init__(self):
        self.reads = 0
        self.renames = []

    async def get_identities_in_group(self, **k):
        self.reads += 1
        return {"content": [{"id": "i2", "name": "102_speed"}]}

    async def update_identity(self, identity_id=None, name=None, **k):
        self.renames.append(identity_id)


def run(wait, members=(), renamed=(), residents=("101_speed",)):
    policy_sets, identity = PolicySets(members), Identity()
    phase = CreateAccessPoliciesPhase.__new__(CreateAccessPoliciesPhase)
    phase.tenant_id = "t"
    phase.r1_client = type("C", (), {
        "radius_attributes": Radius(), "policy_sets": policy_sets,
        "identity": identity, "await_task_completion": staticmethod(wait),
    })()

    async def quiet(*a, **k):
        return None

    async def parallel_map(items, fn, **k):
        for item in items:
            await fn(item)

    phase.emit = phase.emit_progress = phase.track_resource = quiet
    phase.parallel_map = parallel_map
    out = asyncio.run(phase.execute(CreateAccessPoliciesPhase.Inputs(
        created_passphrases=[
            {"username": name, "identity_id": f"i{i}", "success": True}
            for i, name in enumerate(residents, 1)
        ],
        passphrases=[{"name": name, "ssid_list": [f"{name[:3]}@Site"]} for name in residents],
        identity_group_id="ig",
        renamed_identity_ids=list(renamed),
        options={"enable_access_policies": True, "policy_set_name": "Set"},
    )))
    return out, policy_sets, identity


async def ok(**k):
    return {"status": "SUCCESS"}


print("A create R1 failed")
seen = []


async def fail_once(request_id=None, **k):
    seen.append(request_id)
    if len(seen) == 1:
        raise Exception(f"Task {request_id} failed: Assign radius attribute group failed")
    return {"status": "SUCCESS"}


out, policy_sets, _ = run(fail_once, renamed=("i1",))
check("is created again, on the new task", seen == ["r1", "r2"] and policy_sets.creates == 2, str(seen))
check("and finishes", out.policies_created == 1 and out.policies_failed == 0
      and policy_sets.conditions == 2 and len(policy_sets.priorities) == 1)
check("and is counted", out.tuning["policy_create_retries"] == 1)


async def always_fail(request_id=None, **k):
    raise Exception(f"Task {request_id} failed: Assign radius attribute group failed")


out, policy_sets, _ = run(always_fail, renamed=("i1",))
check("twice: fails with R1's own error, no third try",
      policy_sets.creates == 2 and out.policies_failed == 1
      and "Assign radius" in out.policy_results[0].error, str(out.policy_results))


print("A create still running")


async def pending(**k):
    raise TimeoutError("still pending")


out, policy_sets, _ = run(pending, renamed=("i1",))
check("is never sent again", policy_sets.creates == 1 and out.policies_failed == 1)

print("Renames")
out, _, identity = run(ok, renamed=("i1",))
check("confirmed: no PATCH and no group read", identity.renames == [] and identity.reads == 0,
      f"{identity.renames} reads={identity.reads}")
check("and reported as done", out.renames_already_done == 1 and out.identities_renamed == 0)
out, _, identity = run(ok, residents=("101_speed", "102_speed"), renamed=("i1",))
check("not confirmed: still renamed, after reading the group",
      identity.renames == ["i2"] and identity.reads == 1, f"{identity.renames} reads={identity.reads}")

print("Priorities")
_, policy_sets, _ = run(ok, renamed=("i1",), members=[{"policyId": "x", "priority": 6}])
check("numbered after what the set holds", policy_sets.priorities == [7], str(policy_sets.priorities))
_, policy_sets, _ = run(ok, renamed=("i1",), members=Exception("R1 API error (500)"))
check("set unreadable: R1 chooses rather than a clashing number",
      policy_sets.priorities == [None], str(policy_sets.priorities))

print("FAILED" if failures else "All checks passed")
sys.exit(1 if failures else 0)
