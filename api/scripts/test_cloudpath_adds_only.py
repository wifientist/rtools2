#!/usr/bin/env python3
"""
Cloudpath import, Adds only: which file rows R1 already has.

WHAT THIS GUARDS

  1. A row is "already in R1" when its identity matches by Cloudpath GUID,
     same username, the stripped name a previous run left ("4021_gigabit"
     -> "4021"), or the account number under a different tier
     ("4021_ultrafast" -> "4021_gigabit") -- or when its passphrase value is
     already in the pool. Only rows matching none of these are imported.
  2. An account carried by two R1 identities is never treated as new.
  3. A new row with no Cloudpath GUID is still imported, and flagged.
  4. Every match weaker than a GUID is listed on its own line in the plan.
  5. Dropped rows reach no phase: the unit's passphrase list holds adds only.
  6. Outside Adds only, the account-number match resolves an identity whose
     tier changed instead of minting a second one; an ambiguous account
     does not resolve that way.

Runs validate_and_plan against an in-memory fake R1; no tenant is touched.

Usage:
    docker compose exec backend python scripts/test_cloudpath_adds_only.py

Exits non-zero on failure.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.disable(logging.CRITICAL)

from workflow.phases.phase_executor import PhaseContext
from workflow.phases.cloudpath.validate import ValidateCloudpathPhase

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


IDENTITIES = [
    {"id": "i1001", "name": "1001", "description": "guid-1001"},     # GUID
    {"id": "i1002", "name": "1002", "description": ""},              # stripped
    {"id": "i1003", "name": "1003_gigabit", "description": ""},      # account
    {"id": "i1004a", "name": "1004", "description": ""},             # two identities
    {"id": "i1004b", "name": "1004_gigabit", "description": ""},
    {"id": "iauto", "name": "DPSK_User_abc", "description": ""},     # holds 1005's value
    {"id": "i1006", "name": "1006_gigabit", "description": ""},      # same username
    {"id": "i1007a", "name": "1007_gigabit", "description": ""},     # account on two
    {"id": "i1007b", "name": "1007_speed", "description": ""},
]


class _NS:
    pass


def fake_r1():
    c = _NS()
    c.identity = _NS()
    c.dpsk = _NS()

    async def query_identity_groups(tenant_id=None, **kw):
        return {"content": [{"id": "ig1", "name": "Prop-IDG"}]}

    async def get_identities_in_group(group_id, tenant_id, page, size):
        return {"content": IDENTITIES if page == 0 else []}

    async def query_dpsk_pools(*a, **kw):
        return {"content": [{"id": "p1", "name": "Prop-DPSK"}]}

    async def query_passphrases(pool_id, tenant_id, page, limit):
        if page > 1:
            return {"data": []}
        return {"data": [{"id": "pp-auto", "passphrase": "pw-1005-xxxxxx",
                          "username": "DPSK_User_abc"}]}

    c.identity.query_identity_groups = query_identity_groups
    c.identity.get_identities_in_group = get_identities_in_group
    c.dpsk.query_dpsk_pools = query_dpsk_pools
    c.dpsk.query_passphrases = query_passphrases
    return c


def dpsk(name, guid, pw):
    return {"name": name, "guid": guid, "passphrase": pw,
            "ssidList": ["Prop WiFi"], "status": "ACTIVE"}


EXPORT = {
    "pool": {"displayName": "Prop", "ssidList": ["Prop WiFi"]},
    "dpsks": [
        dpsk("1001_gigabit", "guid-1001", "pw-1001-xxxxxx"),
        dpsk("1002_gigabit", "guid-1002", "pw-1002-xxxxxx"),
        dpsk("1003_ultrafast", "guid-1003", "pw-1003-xxxxxx"),
        dpsk("1004_blast", "guid-1004", "pw-1004-xxxxxx"),
        dpsk("1005_gigabit", "guid-1005", "pw-1005-xxxxxx"),
        dpsk("1006_gigabit", "guid-1006", "pw-1006-xxxxxx"),
        dpsk("1007_ultrafast", "guid-1007", "pw-1007-xxxxxx"),
        dpsk("2001_gigabit", "guid-2001", "pw-2001-xxxxxx"),
        dpsk("2002", "", "pw-2002-xxxxxx"),
    ],
}


async def validate(adds_only):
    ctx = PhaseContext(job_id="test", r1_client=fake_r1(), venue_id="v", tenant_id="t")
    phase = ValidateCloudpathPhase(ctx)
    return await phase.execute(phase.Inputs(
        cloudpath_data=EXPORT,
        options={"ssid_mode": "none", "passphrases_only": True,
                 "adds_only": adds_only,
                 "identity_group_name": "Prop-IDG",
                 "dpsk_service_name": "Prop-DPSK"},
    ))


def unit_rows(out):
    unit = next(iter(out.unit_mappings.values()))
    return unit.input_config["passphrases"]


async def main():
    print("Adds only")
    out = await validate(True)
    kept = [p.name for p in out.passphrases]
    check("only rows R1 lacks are kept", kept == ["2001_gigabit", "2002"], str(kept))
    check("dropped rows reach no phase",
          [p["name"] for p in unit_rows(out)] == ["2001_gigabit", "2002"])
    s = out.validation_result.summary
    check("plan counts adds only", s.passphrases_to_create == 2 and s.passphrases_existing == 0,
          f"create={s.passphrases_to_create} existing={s.passphrases_existing}")

    actions = {a.resource_type: a for a in out.validation_result.actions}
    new = actions.get("new residents (adds only)")
    kept_lines = actions.get("residents left untouched (adds only)")
    check("new rows listed", new is not None and len(new.notes) == 2)
    check("row with no GUID flagged",
          new is not None and any(n.startswith("2002") and "no Cloudpath GUID" in n for n in new.notes))
    lines = "\n".join(kept_lines.notes) if kept_lines else ""
    check("GUID matches counted", "1 matched by Cloudpath GUID" in lines)
    for needle in (
        "1002_gigabit → 1002 (tier already stripped",
        "1003_ultrafast → 1003_gigabit (same account number",
        "1004_blast → 1004 (tier already stripped, no GUID match) — account also on 1004_gigabit",
        "1005_gigabit → passphrase already in the pool",
        "1006_gigabit → 1006_gigabit (same username",
        "1007_ultrafast → 1007_gigabit, 1007_speed (account is on 2 identities",
    ):
        check(f"listed: {needle.split(' →')[0]}", needle in lines, needle)

    print("Unfinished policy work")
    check("no backfill unless policies are on", out.policy_backfill == [])
    ctx = PhaseContext(job_id="test", r1_client=fake_r1(), venue_id="v", tenant_id="t")
    ctx.r1_client.radius_attributes = _NS()
    ctx.r1_client.policy_sets = _NS()

    async def no_rows(*a, **kw):
        return {"content": []}

    ctx.r1_client.radius_attributes.query_radius_attribute_groups = no_rows
    ctx.r1_client.policy_sets.query_policy_sets = no_rows
    phase = ValidateCloudpathPhase(ctx)
    unit_export = {
        "pool": EXPORT["pool"],
        "dpsks": [{**d, "ssidList": ["101@Prop", "Prop WiFi"]} for d in EXPORT["dpsks"]],
    }
    out_p = await phase.execute(phase.Inputs(
        cloudpath_data=unit_export,
        options={"ssid_mode": "none", "passphrases_only": True, "adds_only": True,
                 "enable_access_policies": True, "policy_set_name": "Prop",
                 "identity_group_name": "Prop-IDG", "dpsk_service_name": "Prop-DPSK"},
    ))
    backfilled = {r["username"]: r["identity_id"] for r in out_p.policy_backfill}
    check("resolved residents are handed to the policy phase",
          backfilled.get("1003_ultrafast") == "i1003" and "1001_gigabit" in backfilled)
    check("new rows are not backfilled", "2001_gigabit" not in backfilled)
    check("an ambiguous account is not backfilled", "1007_ultrafast" not in backfilled)
    check("the import list still holds adds only",
          [p.name for p in out_p.passphrases] == ["2001_gigabit", "2002"])
    check("the plan counts the policies to finish",
          out_p.validation_result.summary.policies_to_create > 2,
          str(out_p.validation_result.summary.policies_to_create))

    print("Where each resident stands")
    # 1001 is finished unless a description is pending; 1003 has a policy and an
    # old name; 1006 has nothing done. Names as fake_r1 serves them.
    policies = {
        "1001": {"name": "1001", "conditionsCount": 2, "policySetNames": ["Prop"]},
        "1003": {"name": "1003", "conditionsCount": 1, "policySetNames": []},
    }

    async def template_rows(*a, **kw):
        return {"content": list(policies.values())}

    ctx.r1_client.policy_sets.query_template_policies = template_rows
    phase = ValidateCloudpathPhase(ctx)
    status_options = {"ssid_mode": "none", "passphrases_only": True, "adds_only": True,
                      "enable_access_policies": True, "policy_set_name": "Prop",
                      "identity_group_name": "Prop-IDG", "dpsk_service_name": "Prop-DPSK"}
    out_s = await phase.execute(phase.Inputs(cloudpath_data=unit_export, options=status_options))
    statuses = [a for a in out_s.validation_result.actions
                if a.resource_type == "existing residents (adds only)"]
    check("the plan reports where residents stand", len(statuses) == 1,
          str([a.name for a in statuses]))
    remaining = {r["username"] for r in out_s.policy_backfill}
    check("the partly done stay on the work list", "1003_ultrafast" in remaining, str(remaining))
    check("the not started stay on the work list", "1006_gigabit" in remaining, str(remaining))

    # Everyone finished as far as R1 can show: renamed, policy complete, in the set.
    async def finished_rows(*a, **kw):
        return {"content": [
            {"name": r["username"].rsplit("_", 1)[0], "conditionsCount": 2, "policySetNames": ["Prop"]}
            for r in out_p.policy_backfill
        ]}

    ctx.r1_client.policy_sets.query_template_policies = finished_rows
    phase = ValidateCloudpathPhase(ctx)
    out_f = await phase.execute(phase.Inputs(cloudpath_data=unit_export, options=status_options))
    remaining = {r["username"] for r in out_f.policy_backfill}
    check("a blank description still counts as unfinished",
          "1002_gigabit" in remaining, str(sorted(remaining)))
    no_desc = await phase.execute(phase.Inputs(
        cloudpath_data=unit_export,
        options={**status_options, "skip_identity_descriptions": True},
    ))
    check("a renamed resident with its policy complete leaves the work list",
          "1002_gigabit" not in {r["username"] for r in no_desc.policy_backfill},
          str(sorted(r["username"] for r in no_desc.policy_backfill)))
    check("one still suffixed stays on it despite a complete policy",
          "1003_ultrafast" in remaining, str(sorted(remaining)))
    check("the plan no longer calls every existing policy new",
          out_f.validation_result.summary.policies_to_create < out_p.validation_result.summary.policies_to_create,
          f"{out_f.validation_result.summary.policies_to_create} vs {out_p.validation_result.summary.policies_to_create}")

    # A short read must not mark anyone finished.
    async def broken_rows(*a, **kw):
        raise RuntimeError("R1 down")

    ctx.r1_client.policy_sets.query_template_policies = broken_rows
    phase = ValidateCloudpathPhase(ctx)
    out_b = await phase.execute(phase.Inputs(cloudpath_data=unit_export, options=status_options))
    check("if the read fails nobody is judged finished",
          len(out_b.policy_backfill) == len(out_p.policy_backfill))

    print("Blank descriptions")
    filled = {r["identity_id"]: r["cloudpath_guid"] for r in
              next(iter(out.unit_mappings.values())).input_config.get("description_backfill", [])}
    check("a blank description gets its GUID", filled.get("i1006") == "guid-1006", str(filled))
    check("a set description is never overwritten", "i1001" not in filled, str(filled))
    check("an account on two identities is left alone", "i1004a" not in filled, str(filled))
    check("new rows are not in the backfill", not any(g == "guid-2001" for g in filled.values()))
    check("only one unit carries the list", sum(
        1 for u in out.unit_mappings.values() if u.input_config.get("description_backfill")
    ) == 1)
    full = await validate(False)
    check("a full import has no description backfill", not any(
        u.input_config.get("description_backfill") for u in full.unit_mappings.values()))

    print("Full import")
    rows = {p["name"]: p for p in unit_rows(await validate(False))}
    check("tier change resolves by account number",
          rows["1003_ultrafast"]["existing_identity_id"] == "i1003"
          and rows["1003_ultrafast"]["match_via"] == "account")
    check("ambiguous account does not resolve",
          rows["1007_ultrafast"]["existing_identity_id"] is None)
    check("all rows still planned", len(rows) == len(EXPORT["dpsks"]))

    print(f"\n{'All checks passed' if not FAILS else f'{FAILS} check(s) failed'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
