#!/usr/bin/env python3
"""
Per-identity audit regression test.

The audit's whole value is that it does not lie about a broken resident. Each
of these is a way it could report "fine" on someone who cannot connect:

  * The import renames 4021_ultrafast -> 4021, so a file row will never match
    an R1 identity by its raw name. Matching only exactly would report every
    processed identity as missing; stripping unconditionally is what the
    import itself does, so the audit must do the same.

  * An identity that STILL carries its suffix is not a match to celebrate --
    it means the rename never ran, and the policy (named for the stripped
    account) will not match that username at RADIUS time. The row has to say
    so rather than tick the box.

  * A policy that exists but belongs to no policy set does nothing at all.
    That is the exact failure mode that made a run report 215 policies while
    residents stayed offline, so "policy exists" and "policy is in the set"
    are separate columns and the row must not pass on the first alone.

  * The RADIUS group comes from the policy's onMatchResponse, which can drift
    from the tier in the username. A resident on the ultrafast list quietly
    sitting on the gigabit group is a real complaint, invisible in any count.

  * Identities in R1 but not in the file must appear -- and must never be
    presented as something to delete. This tool informs; it has no write path
    at all, which the last check enforces by reading the module.

WHAT THIS GUARDS

  1. A processed identity (suffix stripped by the import) matches its file row.
  2. An identity still carrying its suffix matches, but is FLAGGED.
  3. A file row with no identity anywhere is reported missing.
  4. A policy outside the named policy set does not count as in-set.
  5. A RADIUS group that disagrees with the username suffix is flagged.
  6. A blank description is flagged; a set one is not.
  7. Identities present only in R1 appear as rows with in_file False.
  8. A fully correct resident produces NO issues.
  9. Only pools actually serving the venue are scanned.
 10. The audit module contains no write calls.

Usage:
    docker compose exec backend python scripts/test_identity_audit.py

Exits non-zero on failure.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from routers.cloudpath.identity_audit import (
    FileIdentity,
    IdentityAuditRequest,
    run_identity_audit,
    split_account_suffix,
)


# ==================== A tenant, as R1 would report it ====================


class FakeVenues:
    def __init__(self, w):
        self.w = w

    async def get_venue(self, tenant_id, venue_id):
        return {"id": venue_id, "name": "Cedar Point"}


class FakeNetworks:
    def __init__(self, w):
        self.w = w

    async def get_wifi_networks(self, tenant_id):
        return {"data": self.w["networks"]}

    async def get_dpsk_services_on_network(self, network_id, tenant_id=None):
        return {"data": [{"id": p} for p in self.w["network_pools"].get(network_id, [])]}


class FakeIdentity:
    def __init__(self, w):
        self.w = w

    async def query_identity_groups(self, tenant_id=None, page=0, size=500, **kw):
        return {"content": self.w["identity_groups"]}

    async def get_identities_in_group(self, group_id, tenant_id=None, page=0, size=500):
        if page:
            return {"content": [], "last": True}
        return {"content": self.w["identities"].get(group_id, []), "last": True}


class FakeDpsk:
    def __init__(self, w):
        self.w = w

    async def get_dpsk_pool(self, pool_id, tenant_id=None):
        return {"id": pool_id, "name": self.w["pool_names"].get(pool_id, pool_id)}


class FakePolicySets:
    def __init__(self, w):
        self.w = w

    async def query_template_policies(self, template_id, tenant_id=None, page=0, limit=100, **kw):
        if page:
            return {"content": []}
        return {"content": self.w["policies"]}

    async def query_policy_sets(self, tenant_id=None, page=0, limit=100, **kw):
        return {"content": self.w["policy_sets"]}

    async def get_prioritized_policies(self, policy_set_id, tenant_id=None):
        return {"content": [{"policyId": p} for p in self.w["set_members"].get(policy_set_id, [])]}


class FakeRadius:
    def __init__(self, w):
        self.w = w

    async def get_radius_attribute_groups(self, tenant_id=None):
        return {"content": [{"id": gid, "name": n} for n, gid in self.w["radius_groups"].items()]}


class FakeClient:
    def __init__(self, w):
        self.venues = FakeVenues(w)
        self.networks = FakeNetworks(w)
        self.identity = FakeIdentity(w)
        self.dpsk = FakeDpsk(w)
        self.policy_sets = FakePolicySets(w)
        self.radius_attributes = FakeRadius(w)


def new_world():
    """
    Cedar Point: one venue network on pool-1, plus a second property's pool
    that must NOT be scanned.

    Residents, each a different failure:
        4021_ultrafast  -- fully correct, renamed and wired
        4022_fast       -- identity never renamed (still "4022_fast")
        4023_gigabit    -- policy exists but is in no set
        4024_ultrafast  -- policy points at the wrong RADIUS group
        4025_gigabit    -- no identity at all
        4026_fast       -- identity present, description blank
    and 4099, which is in R1 but not on the roster.
    """
    return {
        "networks": [
            {"id": "net-1", "venueApGroups": [{"venueId": "v-1"}]},
            {"id": "net-2", "venueApGroups": [{"venueId": "v-OTHER"}]},
        ],
        "network_pools": {"net-1": ["pool-1"], "net-2": ["pool-2"]},
        "pool_names": {"pool-1": "CedarPoint DPSK", "pool-2": "Other Property DPSK"},
        "identity_groups": [
            {"id": "ig-1", "name": "CedarPoint Residents", "dpskPoolId": "pool-1"},
            {"id": "ig-2", "name": "Other Residents", "dpskPoolId": "pool-2"},
        ],
        "identities": {
            "ig-1": [
                {"id": "i-1", "name": "4021", "description": "cp-guid-1"},
                {"id": "i-2", "name": "4022_fast", "description": "cp-guid-2"},
                {"id": "i-3", "name": "4023", "description": "cp-guid-3"},
                {"id": "i-4", "name": "4024", "description": "cp-guid-4"},
                {"id": "i-6", "name": "4026", "description": ""},
                {"id": "i-9", "name": "4099", "description": "cp-guid-9"},
            ],
            "ig-2": [
                {"id": "i-x", "name": "9001", "description": "elsewhere"},
            ],
        },
        "radius_groups": {"gigabit": "rg-gig", "fast": "rg-fast", "ultrafast": "rg-ultra"},
        "policies": [
            {"id": "pol-1", "name": "4021", "onMatchResponse": "rg-ultra"},
            {"id": "pol-2", "name": "4022", "onMatchResponse": "rg-fast"},
            {"id": "pol-3", "name": "4023", "onMatchResponse": "rg-gig"},
            {"id": "pol-4", "name": "4024", "onMatchResponse": "rg-gig"},  # wrong tier
            {"id": "pol-6", "name": "4026", "onMatchResponse": "rg-fast"},
            {"id": "pol-9", "name": "4099", "onMatchResponse": "rg-gig"},
        ],
        "policy_sets": [{"id": "set-1", "name": "CedarPoint"}],
        # pol-3 deliberately absent: created, never assigned.
        "set_members": {"set-1": ["pol-1", "pol-2", "pol-4", "pol-6", "pol-9"]},
    }


ROSTER = [
    "4021_ultrafast", "4022_fast", "4023_gigabit",
    "4024_ultrafast", "4025_gigabit", "4026_fast",
]


async def audit(world, roster=None, policy_set_name="CedarPoint"):
    request = IdentityAuditRequest(
        controller_id=1,
        tenant_id="t-1",
        venue_id="v-1",
        identities=[FileIdentity(name=n) for n in (roster or ROSTER)],
        policy_set_name=policy_set_name,
    )
    result = await run_identity_audit(FakeClient(world), request)
    return result, {r.username: r for r in result.rows}


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("Per-identity audit\n")

    # ---- name splitting mirrors create_access_policies ----
    failures += check(
        "username splits into account and suffix the way the import does",
        split_account_suffix("4021_ultrafast", "gigabit") == ("4021", "ultrafast")
        and split_account_suffix("4021", "gigabit") == ("4021", "gigabit")
        and split_account_suffix("a_b_c", "gigabit") == ("a_b", "c"),
        str(split_account_suffix("a_b_c", "gigabit")),
    )

    result, rows = await audit(new_world())

    # 1. processed identity matches its file row
    r = rows["4021_ultrafast"]
    failures += check(
        "a renamed identity matches its file row and reports no issues",
        r.in_identity_group and r.matched_as == "processed" and r.r1_name == "4021"
        and r.in_dpsk_service and r.dpsk_service_name == "CedarPoint DPSK"
        and r.in_adaptive_policy and r.policy_in_set
        and r.radius_group_name == "ultrafast" and r.radius_group_matches
        and r.has_description and not r.issues,
        f"issues={r.issues}",
    )

    # 2. an identity that kept its suffix matches, but is flagged
    r = rows["4022_fast"]
    failures += check(
        "an un-renamed identity is matched BUT flagged",
        r.in_identity_group and r.matched_as == "exact"
        and any("renamed" in i for i in r.issues),
        f"matched_as={r.matched_as}, issues={r.issues}",
    )

    # 3. a resident with no identity at all
    r = rows["4025_gigabit"]
    failures += check(
        "a resident with no identity anywhere is reported missing",
        not r.in_identity_group and not r.in_dpsk_service
        and any("No identity" in i for i in r.issues),
        f"issues={r.issues}",
    )

    # 4. a policy that is in no set does not pass
    r = rows["4023_gigabit"]
    failures += check(
        "a policy outside the policy set is NOT counted as in-set",
        r.in_adaptive_policy and not r.policy_in_set
        and any("no policy set" in i for i in r.issues),
        f"in_set={r.policy_in_set}, issues={r.issues}",
    )

    # 5. RADIUS group disagreeing with the username tier
    r = rows["4024_ultrafast"]
    failures += check(
        "a RADIUS group that disagrees with the username suffix is flagged",
        r.radius_group_name == "gigabit" and r.radius_group_expected == "ultrafast"
        and r.radius_group_matches is False
        and any("expected 'ultrafast'" in i for i in r.issues),
        f"group={r.radius_group_name}, issues={r.issues}",
    )

    # 6. description present vs blank
    failures += check(
        "a blank description is flagged, a set one is not",
        not rows["4026_fast"].has_description
        and any("description" in i for i in rows["4026_fast"].issues)
        and rows["4021_ultrafast"].has_description
        and not any("description" in i for i in rows["4021_ultrafast"].issues),
        f"4026 issues={rows['4026_fast'].issues}",
    )

    # 7. identities present only in R1
    extras = [r for r in result.rows if not r.in_file]
    failures += check(
        "an identity in R1 but not in the file appears as a row",
        len(extras) == 1 and extras[0].username == "4099"
        and extras[0].in_identity_group
        and "Present in R1 but not in the uploaded file" in extras[0].issues,
        f"extras={[e.username for e in extras]}",
    )

    # 9. the other property's pool is not scanned
    failures += check(
        "only pools serving this venue are scanned",
        result.dpsk_services_scanned == ["CedarPoint DPSK"]
        and result.identity_groups_scanned == ["CedarPoint Residents"]
        and not any(r.username == "9001" for r in result.rows),
        f"pools={result.dpsk_services_scanned}",
    )

    # totals line up with the rows
    failures += check(
        "totals agree with the rows",
        result.totals["in_file"] == 6 and result.totals["in_r1_only"] == 1
        and result.totals["missing_identity"] == 1
        and result.totals["with_policy"] == 5
        and result.totals["policy_in_set"] == 4
        and result.totals["radius_mismatch"] == 1
        and result.totals["clean"] == 1,
        str(result.totals),
    )

    # a policy set that does not exist is called out, not silently passed
    result_nosuch, _ = await audit(new_world(), policy_set_name="NoSuchProperty")
    failures += check(
        "a policy set that does not exist is reported as a warning",
        any("No policy set named" in w for w in result_nosuch.warnings)
        and result_nosuch.totals["policy_in_set"] == 0,
        f"warnings={result_nosuch.warnings}",
    )

    # 10. inform only -- no write path exists in the module
    source = (Path(__file__).parent.parent
              / "routers" / "cloudpath" / "identity_audit.py").read_text()
    writes = re.findall(
        r"\.(?:create|update|delete|assign|remove|attach|patch|put|post)_\w+\(",
        source,
    )
    failures += check(
        "the audit module contains no write calls",
        not writes, ", ".join(sorted(set(writes))),
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
