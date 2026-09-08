#!/usr/bin/env python3
"""
Access policy idempotency regression test.

Re-running an import used to rewrite every policy whether or not anything
had changed. For each entry it fired:

    update_template_policy      (always, even with an identical RADIUS group)
    get_policy_conditions
    create_string_condition     (for anything it thought was missing)
    assign_policy_to_policy_set (always, swallowing "already assigned")

so a repeat import over an unchanged property of 200 residents made ~600
writes that changed nothing, and every one of them was a chance to fail.

Two correctness bugs rode along with that:

  * The policy name is the ACCOUNT alone, so several parsed entries -- one
    per (account x unit SSID) -- named the SAME policy. Each took its turn
    writing to it, so the last entry's RADIUS group silently won.

  * The condition check asked "is there an SSID condition" rather than "is
    it THIS SSID", so a second SSID was never added and a stale username or
    SSID pattern was never corrected.

Now entries are collapsed to the policies that should exist, collisions are
reported instead of silently resolved, and nothing is written unless it
actually differs.

WHAT THIS GUARDS

  1. A first run creates the policy and its two conditions.
  2. A second run over unchanged state performs NO writes at all.
  3. A changed RADIUS group is the only thing rewritten.
  4. A stale condition pattern is corrected, not left in place.
  5. Policy-set assignment happens only for policies not already members.
  6. Two SSIDs for one account are reported, not silently dropped.
  7. Identities are renamed once; a re-run skips them instead of PATCHing
     every identity it already renamed.

Usage:
    docker compose exec backend python scripts/test_access_policy_idempotency.py

Exits non-zero on failure.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.create_access_policies import (
    CreateAccessPoliciesPhase,
    DPSK_POLICY_TEMPLATE_ID,
    ATTR_DPSK_USERNAME,
    ATTR_WIRELESS_SSID,
    regex_pattern_for_value,
)

WRITE_CALLS = {
    "create_template_policy", "update_template_policy",
    "create_string_condition", "update_policy_condition",
    "delete_policy_condition", "assign_policy_to_policy_set",
    "create_policy_set", "create_bandwidth_group",
    "update_identity",
}


class FakePolicySets:
    def __init__(self, world, calls):
        self.w, self.calls = world, calls

    async def query_policy_sets(self, **kw):
        self.calls.append("query_policy_sets")
        return {"content": [{"id": "set-1", "name": self.w["policy_set_name"]}]}

    async def create_policy_set(self, **kw):
        self.calls.append("create_policy_set")
        return {"id": "set-1"}

    async def query_template_policies(self, **kw):
        self.calls.append("query_template_policies")
        return {"content": list(self.w["policies"].values())}

    async def get_template_policy(self, policy_id, **kw):
        self.calls.append("get_template_policy")
        return self.w["policies_by_id"][policy_id]

    async def get_prioritized_policies(self, **kw):
        self.calls.append("get_prioritized_policies")
        return {"content": [{"policyId": p} for p in self.w["assigned"]]}

    async def create_template_policy(self, policy_data, **kw):
        self.calls.append("create_template_policy")
        pid = f"pol-{len(self.w['policies']) + 1}"
        rec = {"id": pid, "name": policy_data["name"],
               "onMatchResponse": policy_data.get("onMatchResponse")}
        self.w["policies"][rec["name"]] = rec
        self.w["policies_by_id"][pid] = rec
        self.w["conditions"][pid] = []
        return rec

    async def await_policy_creation(self, **kw):
        return True

    async def update_template_policy(self, policy_id, policy_data, **kw):
        self.calls.append("update_template_policy")
        self.w["policies_by_id"][policy_id].update(policy_data)
        return True

    async def get_policy_conditions(self, policy_id, **kw):
        self.calls.append("get_policy_conditions")
        return {"content": self.w["conditions"].get(policy_id, [])}

    async def create_string_condition(self, policy_id, attribute_id, regex_pattern, **kw):
        self.calls.append("create_string_condition")
        self.w["conditions"].setdefault(policy_id, []).append({
            "id": f"cond-{len(self.w['conditions'][policy_id]) + 1}",
            "templateAttributeId": attribute_id,
            "evaluationRule": {"criteriaType": "StringCriteria",
                               "regexStringCriteria": regex_pattern},
        })
        return True

    async def update_policy_condition(self, policy_id, condition_id, condition_data, **kw):
        self.calls.append("update_policy_condition")
        for c in self.w["conditions"][policy_id]:
            if c["id"] == condition_id:
                c["evaluationRule"] = condition_data["evaluationRule"]
        return True

    async def assign_policy_to_policy_set(self, policy_id, **kw):
        self.calls.append("assign_policy_to_policy_set")
        self.w["assigned"].add(policy_id)
        return True


class FakeRadius:
    def __init__(self, world, calls):
        self.w, self.calls = world, calls

    async def query_radius_attribute_groups(self, search_string, **kw):
        self.calls.append("query_radius_attribute_groups")
        return {"content": [{"id": gid, "name": n}
                            for n, gid in self.w["radius_groups"].items()
                            if n.lower() == search_string.lower()]}

    async def get_radius_attribute_groups(self, **kw):
        return {"content": [{"id": g, "name": n}
                            for n, g in self.w["radius_groups"].items()]}

    async def create_bandwidth_group(self, **kw):
        self.calls.append("create_bandwidth_group")
        return {"id": "rg-new"}


class FakeIdentity:
    def __init__(self, world, calls):
        self.w, self.calls = world, calls

    async def get_identities_in_group(self, group_id, page=0, size=100, **kw):
        self.calls.append("get_identities_in_group")
        if page:
            return {"content": []}
        return {"content": [{"id": i, "name": n}
                            for i, n in self.w["identities"].items()]}

    async def update_identity(self, identity_id, name, **kw):
        self.calls.append("update_identity")
        if name in self.w["identities"].values():
            raise RuntimeError("GENERAL-010 duplicate identity name")
        self.w["identities"][identity_id] = name
        return True


class FakeClient:
    def __init__(self, world, calls):
        self.policy_sets = FakePolicySets(world, calls)
        self.radius_attributes = FakeRadius(world, calls)
        self.identity = FakeIdentity(world, calls)


def new_world():
    return {
        "policy_set_name": "TestProperty",
        "radius_groups": {"gigabit": "rg-gig", "fast": "rg-fast"},
        "policies": {}, "policies_by_id": {}, "conditions": {},
        "assigned": set(),
        # identity id -> current name, as create_passphrases left them
        "identities": {"id-2001_gigabit": "2001_gigabit",
                       "id-2002_fast": "2002_fast",
                       "id-3001_gigabit": "3001_gigabit"},
    }


def make_phase(world, calls, messages):
    phase = CreateAccessPoliciesPhase.__new__(CreateAccessPoliciesPhase)
    phase.r1_client = FakeClient(world, calls)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"

    async def emit(msg, level="info", details=None):
        messages.append((level, msg))

    async def track_resource(kind, data):
        return None

    phase.emit = emit
    phase.track_resource = track_resource
    return phase


def inputs_for(pairs, suffix_by_user=None):
    """pairs: [(username, [ssids])]"""
    created = [{"username": u, "identity_id": f"id-{u}", "success": True}
               for u, _ in pairs]
    originals = [{"name": u, "ssid_list": ss} for u, ss in pairs]
    return CreateAccessPoliciesPhase.Inputs(
        created_passphrases=created,
        passphrases=originals,
        identity_group_id="ig-1",
        options={"enable_access_policies": True,
                 "policy_set_name": "TestProperty",
                 "default_suffix": "gigabit"},
    )


async def run(world, pairs):
    calls, messages = [], []
    phase = make_phase(world, calls, messages)
    out = await phase.execute(inputs_for(pairs))
    writes = [c for c in calls if c in WRITE_CALLS]
    return out, calls, writes, messages


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("Access policy idempotency\n")

    PAIRS = [("2001_gigabit", ["101@Prop"]), ("2002_fast", ["102@Prop"])]

    # 1. first run creates
    world = new_world()
    out, calls, writes, _ = await run(world, PAIRS)
    failures += check(
        "first run creates both policies and their conditions",
        out.policies_created == 2 and out.policies_failed == 0
        and calls.count("create_template_policy") == 2
        and calls.count("create_string_condition") == 4,
        f"created={out.policies_created}, writes={len(writes)}",
    )

    # 2. second run over unchanged state writes nothing
    out, calls, writes, _ = await run(world, PAIRS)
    failures += check(
        "re-run over unchanged state performs NO writes",
        writes == [] and out.policies_unchanged == 2
        and out.policies_created == 0 and out.policies_updated == 0,
        f"writes={writes}, unchanged={out.policies_unchanged}",
    )

    # 3. a changed RADIUS group is the only thing rewritten
    world["policies_by_id"]["pol-1"]["onMatchResponse"] = "rg-WRONG"
    out, calls, writes, _ = await run(world, PAIRS)
    failures += check(
        "a changed RADIUS group rewrites only that policy",
        writes == ["update_template_policy"] and out.policies_updated == 1
        and out.policies_unchanged == 1
        and world["policies_by_id"]["pol-1"]["onMatchResponse"] == "rg-gig",
        f"writes={writes}",
    )

    # 4. a stale condition pattern is corrected
    world["conditions"]["pol-2"][1]["evaluationRule"]["regexStringCriteria"] = "^stale@Prop$"
    out, calls, writes, _ = await run(world, PAIRS)
    fixed = world["conditions"]["pol-2"][1]["evaluationRule"]["regexStringCriteria"]
    failures += check(
        "a stale condition pattern is corrected in place",
        writes == ["update_policy_condition"]
        and fixed == regex_pattern_for_value("102@Prop"),
        f"writes={writes}, pattern={fixed}",
    )

    # 5. assignment only for non-members
    world["assigned"].discard("pol-1")
    out, calls, writes, _ = await run(world, PAIRS)
    failures += check(
        "only the unassigned policy is assigned",
        writes == ["assign_policy_to_policy_set"],
        f"writes={writes}",
    )

    # 6. two unit SSIDs for one account are reported, not silently dropped
    world2 = new_world()
    out, calls, writes, messages = await run(
        world2, [("3001_gigabit", ["101@Prop", "102@Prop"])]
    )
    warned = [m for lvl, m in messages if "more than one unit SSID" in m]
    failures += check(
        "an account with two unit SSIDs is reported",
        bool(warned) and out.policies_created == 1,
        warned[0] if warned else "no warning emitted",
    )

    # 7. and that run is still idempotent
    out, calls, writes, _ = await run(world2, [("3001_gigabit", ["101@Prop", "102@Prop"])])
    failures += check(
        "the multi-SSID case is idempotent too", writes == [], f"writes={writes}"
    )

    # 8. renames happen once, then are skipped
    world3 = new_world()
    out, calls, writes, _ = await run(world3, PAIRS)
    failures += check(
        "first run strips the suffix from both identities",
        out.identities_renamed == 2 and calls.count("update_identity") == 2
        and world3["identities"]["id-2001_gigabit"] == "2001",
        f"renamed={out.identities_renamed}, names={world3['identities']}",
    )

    out, calls, writes, _ = await run(world3, PAIRS)
    failures += check(
        "re-run renames nothing and writes nothing at all",
        "update_identity" not in calls and writes == []
        and out.renames_already_done == 2,
        f"writes={writes}, already_done={out.renames_already_done}",
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
