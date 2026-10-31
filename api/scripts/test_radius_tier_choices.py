#!/usr/bin/env python3
"""
A speed tier with no RADIUS group is the user's call, made in the plan.

It used to be created silently at 10 Gbps, or fall back to another tier's group
if that failed.

    docker compose exec -T -w /app backend python scripts/test_radius_tier_choices.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from routers.cloudpath.v2_endpoints import resolve_radius_tier_choices
from workflow.phases.create_access_policies import CreateAccessPoliciesPhase
from workflow.phases.dpsk_usernames import radius_tier_plan

failures = 0


def check(label, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))


print("Validation lists every tier")
names = ["101_speed", "102_Speed", "103_speed", "104_turbo", "105_turbo", "106"]
groups = [{"name": "speed", "attributeAssignments": [
    {"attributeName": "WISPr-Bandwidth-Max-Down", "attributeValue": "500000000"},
    {"attributeName": "WISPr-Bandwidth-Max-Up", "attributeValue": "100000000"},
]}, {"name": "Other"}]
plan = radius_tier_plan(names, groups, "gigabit")
check("missing first, then by residents",
      [t["tier"] for t in plan] == ["turbo", "gigabit", "speed"], str(plan))
speed = plan[-1]
check("spellings share one row", speed["residents"] == 3
      and speed["also_written_as"] == [{"tier": "Speed", "residents": 1}], str(speed))
check("with the group and its speed", speed["group"] == "speed"
      and speed["down_bps"] == "500000000" and speed["up_bps"] == "100000000", str(speed))
check("missing tiers have no group", plan[0]["group"] is None and plan[0]["residents"] == 2)
check("groups nobody uses are not listed", all(t["group"] != "Other" for t in plan))

print("Confirm needs a choice per tier")
summary = {"missing_radius_tiers": [{"tier": "Turbo", "residents": 2}],
           "available_radius_groups": ["speed"]}


def refused(choices):
    try:
        resolve_radius_tier_choices(summary, choices)
        return False
    except ValueError:
        return True


check("no choice is refused", refused({}))
check("a group that does not exist is refused", refused({"Turbo": {"action": "use", "group": "nope"}}))
check("an unknown action is refused", refused({"Turbo": {"action": "whatever"}}))
check("use is kept, keyed by lowercase tier",
      resolve_radius_tier_choices(summary, {"Turbo": {"action": "use", "group": "speed"}})
      == {"turbo": {"action": "use", "group": "speed"}})
check("create is kept",
      resolve_radius_tier_choices(summary, {"turbo": {"action": "create"}})
      == {"turbo": {"action": "create"}})
check("a plan with no gaps needs nothing",
      resolve_radius_tier_choices({"missing_radius_tiers": []}, {}) == {})


class Radius:
    def __init__(self, groups, refuse_create=False):
        self.groups = groups
        self.refuse_create = refuse_create
        self.created = []

    async def query_radius_attribute_groups(self, tenant_id=None, search_string=None, limit=100):
        return {"content": [g for g in self.groups
                            if (search_string or "").lower() in g["name"].lower()]}

    async def create_bandwidth_group(self, name, down_bps, up_bps, tenant_id=None):
        self.created.append(name)
        if self.refuse_create:
            raise Exception("R1 API error (500): no")
        return {"id": f"new-{name}"}

    async def get_radius_attribute_groups(self, tenant_id=None):
        return {"content": self.groups}


class PolicySets:
    def __init__(self):
        self.on_match = []

    async def query_policy_sets(self, **k):
        return {"content": [{"id": "set", "name": "Set"}]}

    async def query_template_policies(self, **k):
        return {"content": []}

    async def get_prioritized_policies(self, **k):
        return []

    async def create_template_policy(self, template_id, policy_data, tenant_id=None):
        self.on_match.append(policy_data["onMatchResponse"])
        return {"id": f"p{len(self.on_match)}"}

    async def await_policy_creation(self, **k):
        return {}

    async def create_string_condition(self, **k):
        return {}

    async def assign_policy_to_policy_set(self, **k):
        return {}


def run(radius, choices=None):
    policy_sets = PolicySets()
    phase = CreateAccessPoliciesPhase.__new__(CreateAccessPoliciesPhase)
    phase.tenant_id = "t"
    phase.r1_client = type("C", (), {"radius_attributes": radius, "policy_sets": policy_sets})()
    phase.emitted = []

    async def emit(message, *a, **k):
        phase.emitted.append(message)

    async def quiet(*a, **k):
        return None

    async def parallel_map(items, fn, **k):
        for item in items:
            await fn(item)

    phase.emit = emit
    phase.emit_progress = quiet
    phase.track_resource = quiet
    phase.parallel_map = parallel_map
    username = "101_turbo"
    out = asyncio.run(phase.execute(CreateAccessPoliciesPhase.Inputs(
        created_passphrases=[{"username": username, "success": True}],
        passphrases=[{"name": username, "ssid_list": ["101@Site"]}],
        options={
            "enable_access_policies": True, "policy_set_name": "Set",
            "radius_tier_choices": choices or {},
        },
    )))
    return out, policy_sets, phase


GROUPS = [{"id": "g-speed", "name": "speed"}, {"id": "g-gig", "name": "gigabit"}]

print("The phase does what was chosen")
radius = Radius(GROUPS)
out, policy_sets, _ = run(radius, {"turbo": {"action": "use", "group": "speed"}})
check("use: the policy gets the chosen group", policy_sets.on_match == ["g-speed"], str(policy_sets.on_match))
check("use: nothing is created", radius.created == [], str(radius.created))

radius = Radius(GROUPS)
out, policy_sets, _ = run(radius, {"turbo": {"action": "create"}})
check("create: named for the tier", radius.created == ["turbo"], str(radius.created))
check("create: the policy gets it", policy_sets.on_match == ["new-turbo"], str(policy_sets.on_match))

radius = Radius([{"id": "g-gig", "name": "gigabit"}])
out, policy_sets, phase = run(radius, {"turbo": {"action": "use", "group": "speed"}})
check("chosen group gone: not created instead", radius.created == [], str(radius.created))
check("chosen group gone: no policy, counted, said",
      policy_sets.on_match == [] and out.policies_failed == 1
      and any("not found" in m for m in phase.emitted))

radius = Radius([{"id": "g-gig", "name": "gigabit"}], refuse_create=True)
out, policy_sets, _ = run(radius, {"turbo": {"action": "create"}})
check("create refused: never another tier's group",
      policy_sets.on_match == [] and out.policies_failed == 1, str(policy_sets.on_match))

print("FAILED" if failures else "All checks passed")
sys.exit(1 if failures else 0)
