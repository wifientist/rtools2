#!/usr/bin/env python3
"""
Re-run identity matching regression test.

THE SHAPE OF THE BUG

The import takes a Cloudpath username apart and spreads the pieces around:

    file        4021_ultrafast
    identity    4021              (create_access_policies renames it)
    policy      4021
    RADIUS      ultrafast

So the name in R1 after run 1 is NOT the name in the file. validate matched
existing identities on the raw file name and, failing that, on the Cloudpath
GUID stamped into the description. Both miss for any identity that was
renamed but never got a description -- a run that died before
update_identity_descriptions, or descriptions never enabled.

A miss is not benign. The resident looks new, so the import mints a SECOND
identity named "4021_ultrafast", and create_access_policies then tries to
rename that onto "4021", which the first identity already holds. R1 refuses
with GENERAL-010 and the property is left with two identities for one
resident, the newer one still carrying its tier.

WHERE THE "_1", "_2" NAMES COME FROM

When we ask R1 to rename an identity onto a name another identity already
holds, R1 does not always refuse -- it can deduplicate by appending "_1",
"_2", and so on. So the duplicate-resident case above does not just fail
loudly; it can quietly manufacture a third name nobody chose. Checking for
the collision before issuing the write is what stops that, and it is the
only thing here that can: once R1 has renamed, we cannot tell its dedup
suffix from a real tier.

WHAT THIS GUARDS

  1. One split rule: validate, the rename and the audit all use the same
     function, so the import can recognise its own previous output.
  2. A renamed identity is matched on a re-run by its stripped name, and
     reused rather than duplicated -- even with no Cloudpath GUID.
  3. The raw name still wins when it is present (an identity not yet
     renamed must not be confused with one that was).
  4. GUID matching still takes precedence over both.
  5. A rename onto a name another identity already holds is skipped and
     reported, never fired at R1 -- which may refuse it OR silently
     deduplicate it into "name_1".
  6. A clean rename still happens exactly once and is idempotent.

Usage:
    docker compose exec backend python scripts/test_rerun_identity_matching.py

Exits non-zero on failure.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.dpsk_usernames import known_suffixes, split_account_suffix
from workflow.phases.create_access_policies import CreateAccessPoliciesPhase


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


# ==================== the shared split rule ====================


def split_rule_checks() -> int:
    failures = 0

    failures += check(
        "the split rule is shared, not reimplemented per caller",
        __import__("workflow.phases.cloudpath.validate", fromlist=["x"]).split_account_suffix
        is split_account_suffix
        and __import__("routers.cloudpath.identity_audit", fromlist=["x"]).split_account_suffix
        is split_account_suffix,
        "validate or the audit is using a private copy",
    )

    failures += check(
        "tiers come from the file, not a hardcoded list",
        known_suffixes(["4021_ultrafast", "4022_fast", "4023"], "gigabit")
        == {"ultrafast", "fast", "gigabit"},
        str(known_suffixes(["4021_ultrafast", "4022_fast", "4023"], "gigabit")),
    )
    return failures


# ==================== validate's re-run matching ====================


def resolve_identity(file_name, guid, existing_by_name, existing_by_guid, default="gigabit"):
    """
    The resolution order validate uses, lifted so it can be exercised without
    standing up the whole phase: GUID, then raw name, then stripped name.
    """
    info = None
    matched = None
    if guid:
        info = existing_by_guid.get(guid)
        if info:
            matched = "guid"
    if not info:
        info = existing_by_name.get(file_name)
        if info:
            matched = "raw"
    if not info:
        stripped, _ = split_account_suffix(file_name, default)
        if stripped != file_name:
            info = existing_by_name.get(stripped)
            if info:
                matched = "stripped"
    return info, matched


def matching_checks() -> int:
    failures = 0

    # R1 after a previous run: renamed, and with NO description to match on.
    renamed = {"4021": {"id": "i-1", "name": "4021", "description": ""}}

    info, how = resolve_identity("4021_ultrafast", "guid-1", renamed, {})
    failures += check(
        "a renamed identity with no GUID is still matched on a re-run",
        info is not None and info["id"] == "i-1" and how == "stripped",
        f"matched={how}",
    )

    # Not yet renamed: the raw name must win, not the stripped one.
    both = {
        "4021_ultrafast": {"id": "i-raw", "name": "4021_ultrafast", "description": ""},
        "4021": {"id": "i-stripped", "name": "4021", "description": ""},
    }
    info, how = resolve_identity("4021_ultrafast", "", both, {})
    failures += check(
        "the raw name wins when the identity has not been renamed yet",
        info["id"] == "i-raw" and how == "raw",
        f"matched={how} id={info['id']}",
    )

    # GUID beats both.
    by_guid = {"guid-1": {"id": "i-guid", "name": "4021", "description": "guid-1"}}
    info, how = resolve_identity("4021_ultrafast", "guid-1", both, by_guid)
    failures += check(
        "the Cloudpath GUID still takes precedence",
        info["id"] == "i-guid" and how == "guid",
        f"matched={how}",
    )

    # A genuinely new resident stays new.
    info, how = resolve_identity("9999_fast", "guid-new", renamed, {})
    failures += check(
        "a resident who really is new is not matched to anything",
        info is None, f"matched={how}",
    )
    return failures


# ==================== the rename guardrails ====================


class FakeIdentitySvc:
    def __init__(self, world):
        self.w = world
        self.renames = []

    async def get_identities_in_group(self, group_id, tenant_id=None, page=0, size=100):
        if page:
            return {"content": []}
        return {"content": [{"id": i, "name": n} for i, n in self.w["identities"].items()]}

    async def update_identity(self, group_id, identity_id, name, tenant_id=None):
        if name in self.w["identities"].values():
            raise RuntimeError("GENERAL-010 identity with this name already exists")
        self.renames.append((identity_id, name))
        self.w["identities"][identity_id] = name
        return True


class FakeRadius:
    async def query_radius_attribute_groups(self, search_string, **kw):
        return {"content": [{"id": f"rg-{search_string}", "name": search_string}]}

    async def get_radius_attribute_groups(self, **kw):
        return {"content": []}

    async def create_bandwidth_group(self, name, **kw):
        return {"id": f"rg-{name}"}


class FakePolicySets:
    def __init__(self):
        self.policies = {}

    async def query_policy_sets(self, **kw):
        return {"content": [{"id": "set-1", "name": "Durant"}]}

    async def query_template_policies(self, **kw):
        return {"content": list(self.policies.values())}

    async def get_prioritized_policies(self, **kw):
        return {"content": []}

    async def create_template_policy(self, policy_data, **kw):
        pid = f"pol-{len(self.policies) + 1}"
        rec = {"id": pid, "name": policy_data["name"],
               "onMatchResponse": policy_data.get("onMatchResponse")}
        self.policies[rec["name"]] = rec
        return rec

    async def await_policy_creation(self, **kw):
        return True

    async def create_string_condition(self, **kw):
        return True

    async def assign_policy_to_policy_set(self, **kw):
        return True

    async def get_policy_conditions(self, **kw):
        return {"content": []}


class FakeClient:
    def __init__(self, world):
        self.identity = FakeIdentitySvc(world)
        self.radius_attributes = FakeRadius()
        self.policy_sets = FakePolicySets()


async def run_phase(world, roster):
    messages = []
    phase = CreateAccessPoliciesPhase.__new__(CreateAccessPoliciesPhase)
    phase.r1_client = FakeClient(world)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"

    async def emit(msg, level="info", details=None):
        messages.append((level, msg))

    async def track_resource(kind, data):
        return None

    phase.emit = emit
    phase.track_resource = track_resource

    created = [{"username": u, "identity_id": i, "success": True}
               for u, i in roster]
    originals = [{"name": u, "ssid_list": ["403@Durant"]} for u, _ in roster]

    out = await phase.execute(CreateAccessPoliciesPhase.Inputs(
        created_passphrases=created,
        passphrases=originals,
        identity_group_id="ig-1",
        options={"enable_access_policies": True,
                 "policy_set_name": "Durant",
                 "default_suffix": "gigabit"},
    ))
    return out, phase.r1_client.identity, " ".join(m for _, m in messages)


async def rename_checks() -> int:
    failures = 0

    world = {"identities": {"i-1": "4021_ultrafast"}}
    out, svc, text = await run_phase(world, [("4021_ultrafast", "i-1")])

    failures += check(
        "the real tiered username is renamed",
        ("i-1", "4021") in svc.renames,
        f"renames={svc.renames}",
    )

    # The duplicate-resident case: "4021" already exists, held by someone else.
    world = {"identities": {"i-old": "4021", "i-new": "4021_ultrafast"}}
    out, svc, text = await run_phase(world, [("4021_ultrafast", "i-new")])
    failures += check(
        "a rename onto a name another identity holds is skipped, not attempted",
        svc.renames == [] and out.renames_would_collide == 1
        and out.renames_failed == 0,
        f"renames={svc.renames}, collide={out.renames_would_collide}, "
        f"failed={out.renames_failed}",
    )
    failures += check(
        "and the collision is explained as a duplicated resident",
        "already another identity" in text and "TWICE" in text,
        text[-200:],
    )

    # The healthy case still works and is idempotent.
    world = {"identities": {"i-1": "4021_ultrafast"}}
    out, svc, _ = await run_phase(world, [("4021_ultrafast", "i-1")])
    first = list(svc.renames)
    out2, svc2, _ = await run_phase(world, [("4021_ultrafast", "i-1")])
    failures += check(
        "a clean rename happens once, then is skipped",
        first == [("i-1", "4021")] and svc2.renames == []
        and out2.renames_already_done == 1,
        f"first={first}, second={svc2.renames}",
    )
    return failures


async def main() -> int:
    print("Re-run identity matching\n")
    failures = split_rule_checks()
    failures += matching_checks()
    failures += await rename_checks()
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
