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
        self.w.setdefault("priorities", []).append(kw.get("priority"))
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

    async def emit_progress(current, total, failed=0, item_name="item", detail=""):
        messages.append(("progress", f"{current}/{total} {item_name}: {detail}"))

    phase.emit = emit
    phase.emit_progress = emit_progress
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

    # 9. Adds only found nothing new: the policies are still finished
    world4 = new_world()
    calls, messages = [], []
    backfill = [
        {"username": u, "identity_id": f"id-{u}", "ssid_list": ss,
         "success": True, "skipped": True}
        for u, ss in PAIRS
    ]
    backfill_inputs = CreateAccessPoliciesPhase.Inputs(
        policy_backfill=backfill,
        identity_group_id="ig-1",
        options={"enable_access_policies": True,
                 "policy_set_name": "TestProperty",
                 "default_suffix": "gigabit"},
    )
    out = await make_phase(world4, calls, messages).execute(backfill_inputs)
    failures += check(
        "a re-run with no new residents creates the missing policies and renames",
        out.policies_created == 2 and out.identities_renamed == 2
        and out.policy_set_id == "set-1" and len(world4["assigned"]) == 2,
        f"created={out.policies_created}, renamed={out.identities_renamed}",
    )

    # 10. and it adds what is missing without changing what exists
    world4["policies_by_id"]["pol-1"]["onMatchResponse"] = "rg-hand-set"
    world4["conditions"]["pol-1"][0]["evaluationRule"]["regexStringCriteria"] = "^hand$"
    world4["conditions"]["pol-2"].pop()
    world4["assigned"].discard("pol-2")
    calls, messages = [], []
    out = await make_phase(world4, calls, messages).execute(backfill_inputs)
    writes = [c for c in calls if c in WRITE_CALLS]
    failures += check(
        "backfill finishes a half-made policy and leaves existing values alone",
        sorted(writes) == ["assign_policy_to_policy_set", "create_string_condition"]
        and world4["policies_by_id"]["pol-1"]["onMatchResponse"] == "rg-hand-set"
        and world4["conditions"]["pol-1"][0]["evaluationRule"]["regexStringCriteria"] == "^hand$",
        f"writes={writes}",
    )

    # 11. a finished policy costs a backfill no per-policy read
    for rec in world4["policies"].values():
        rec["conditionsCount"] = 2
    calls, messages = [], []
    await make_phase(world4, calls, messages).execute(backfill_inputs)
    failures += check(
        "backfill reads no conditions when the policy list shows both exist",
        "get_policy_conditions" not in calls,
        f"reads={calls.count('get_policy_conditions')}",
    )

    # 12. the list saying one is missing still gets it repaired
    victim = world4["policies_by_id"]["pol-1"]
    victim["conditionsCount"] = 1
    world4["conditions"]["pol-1"].pop()
    calls, messages = [], []
    await make_phase(world4, calls, messages).execute(backfill_inputs)
    failures += check(
        "backfill still repairs a policy the list shows as incomplete",
        calls.count("get_policy_conditions") == 1
        and calls.count("create_string_condition") == 1,
        f"calls={[c for c in calls if 'condition' in c]}",
    )

    # 13. each resident is finished in order: rename, then policy, then assign
    world5 = new_world()
    calls, messages = [], []
    out = await make_phase(world5, calls, messages).execute(inputs_for(PAIRS))
    writes = [c for c in calls if c in (
        "update_identity", "create_template_policy", "assign_policy_to_policy_set",
    )]
    failures += check(
        "a resident's rename, policy and assignment run together, rename first",
        writes == ["update_identity", "create_template_policy", "assign_policy_to_policy_set"] * 2,
        f"writes={writes}",
    )
    progress = [m for level, m in messages if level == "progress"]
    failures += check(
        "progress counts residents with a breakdown",
        progress[0].startswith("0/2 resident") and progress[-1].startswith("2/2 resident")
        and "2 policies created" in progress[-1] and "2 renamed" in progress[-1],
        f"last={progress[-1]}",
    )

    # 14. a run cut off part way: the first resident is done, the second is not
    world6 = new_world()
    calls, messages = [], []
    await make_phase(world6, calls, messages).execute(inputs_for(PAIRS[:1]))
    calls, messages = [], []
    out = await make_phase(world6, calls, messages).execute(inputs_for(PAIRS))
    failures += check(
        "the next run picks up only the unfinished resident",
        out.policies_created == 1 and out.policies_unchanged == 1
        and out.identities_renamed == 1 and out.renames_already_done == 1
        and calls.count("update_identity") == 1
        and calls.count("assign_policy_to_policy_set") == 1,
        f"created={out.policies_created}, renamed={out.identities_renamed}",
    )

    # 15. an identity with no unit SSID still gets its rename
    world7 = new_world()
    calls, messages = [], []
    out = await make_phase(world7, calls, messages).execute(inputs_for(
        [("2001_gigabit", ["101@Prop"]), ("2002_fast", ["Prop WiFi"])]
    ))
    failures += check(
        "a resident with no policy to build is still renamed",
        out.policies_created == 1 and out.identities_renamed == 2,
        f"created={out.policies_created}, renamed={out.identities_renamed}",
    )

    # 16. workers finish everything, and a failed policy does not undo a rename
    world8 = new_world()
    pairs = [("2001_gigabit", ["101@Prop"]), ("2002_fast", ["102@Prop"]),
             ("3001_gigabit", ["103@Prop"])]
    calls, messages = [], []
    phase = make_phase(world8, calls, messages)
    create = phase.r1_client.policy_sets.create_template_policy

    async def flaky(policy_data, **kw):
        if policy_data["name"] == "2002":
            raise RuntimeError("R1 said no")
        return await create(policy_data=policy_data, **kw)

    phase.r1_client.policy_sets.create_template_policy = flaky
    inputs = inputs_for(pairs)
    inputs.options["policy_workers"] = 3
    out = await phase.execute(inputs)
    failures += check(
        "with 3 workers one failure stays with its own resident",
        out.policies_created == 2 and out.policies_failed == 1
        and out.identities_renamed == 3,
        f"created={out.policies_created}, failed={out.policies_failed}, "
        f"renamed={out.identities_renamed}",
    )

    print("\n17. Set adds: turns, retries, explicit priority, honest failure")
    pairs = [("2001_gigabit", ["101@Prop"]), ("2002_fast", ["102@Prop"]),
             ("3001_gigabit", ["103@Prop"])]
    world, calls, messages = new_world(), [], []
    phase = make_phase(world, calls, messages)
    real_assign = phase.r1_client.policy_sets.assign_policy_to_policy_set
    state = {"now": 0, "peak": 0, "fail_once": True}

    async def counted(policy_id, **kw):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0)
        state["now"] -= 1
        if state["fail_once"]:
            state["fail_once"] = False
            raise RuntimeError("R1 API error (500): Internal Server Error")
        return await real_assign(policy_id=policy_id, **kw)

    phase.r1_client.policy_sets.assign_policy_to_policy_set = counted
    inputs = inputs_for(pairs)
    inputs.options.update(policy_workers=3, set_add_priority="explicit")
    out = await phase.execute(inputs)
    failures += check(
        "adds take turns, a 500 is retried, and priorities are unique",
        state["peak"] == 1 and out.policies_failed == 0
        and out.tuning["set_add_500s"] == 1
        and sorted(world["priorities"]) == [0, 1, 2],
        f"peak={state['peak']}, failed={out.policies_failed}, "
        f"tuning={out.tuning.get('set_add_500s')}, p={world.get('priorities')}",
    )

    world, calls, messages = new_world(), [], []
    phase = make_phase(world, calls, messages)

    async def always_500(policy_id, **kw):
        raise RuntimeError("R1 API error (500): Internal Server Error")

    phase.r1_client.policy_sets.assign_policy_to_policy_set = always_500
    out = await phase.execute(inputs_for(pairs[:1]))
    failures += check(
        "an add that keeps failing counts as failed, not created-and-fine",
        out.policies_failed == 1 and out.policies_not_in_set == 1
        and out.policy_results[0].success is False,
        f"failed={out.policies_failed}, not_in_set={out.policies_not_in_set}",
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
