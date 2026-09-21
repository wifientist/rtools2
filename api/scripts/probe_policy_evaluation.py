#!/usr/bin/env python3
"""
Ask R1 which policy an identity actually lands on, and what it gets.

WHY THIS EXISTS

The audit can say "a policy NAMED 8081081081 exists, pointing at gigabit",
and with --verify-conditions also "its DPSK username regex is ^8081081081$".
Neither answers the question that matters:

    which policy does this identity actually land on, and therefore which
    RADIUS attribute group does it really get?

Policies live in a SET and are evaluated in priority order -- first match
wins. A higher-priority policy with a looser regex can swallow a resident
whose own policy is perfectly correct, and nothing we read today would show
it. R1 answers this directly:

    POST /policySets/{policySetId}/evaluationReports

    "Evaluates the criteria provided and returns the matched response from
     the FIRST matching policy within the specified set, or it will indicate
     that no match was found."

THE SHAPE, FROM THE SPEC -- NOT GUESSED

RUCKUS_One_Consolidated_API.json (Feb 2026) has this endpoint with an EMPTY
request body and an empty 200. The NEWER spec in this repo,
RUCKUS_One_Consolidated_API_08122026.json, documents both ends properly as
`Policy_Evaluation_Evaluation Report V2`:

    request   {"evaluationCriteria": [{"matchName": ..., "value": {...}}]}
              minItems 1, both fields required
    response  the same object, plus wasMatched / policyId / policyName /
              onMatchResponse

and `matchName` is NOT the numeric attribute id:

    "The name of the attribute to match. This must be an exact match to the
     attribute text as defined in the policy template attribute"

which is `attributeTextMatch` on the template attribute, fetched live below
rather than hardcoded.

The one thing the spec does NOT pin down is the `type` discriminator literal
on the value object -- it declares `discriminator: {propertyName: "type"}`
with no mapping. So that, and only that, is probed: the conditions side of
this same API spells its discriminator "StringCriteria" (schema
`Adaptive_Policy_Management_StringCriteria`), so the candidates are the
same convention applied to `Policy_Evaluation_String Evaluation`.

READ-ONLY. It POSTs, but to an evaluation/report endpoint that returns a
verdict and changes no configuration.

Usage:
    docker compose exec backend python scripts/probe_policy_evaluation.py \\
        --controller 16 --policy-set "The durant" \\
        --username 8081081081 --ssid "101@The_durant" [--tenant <id>]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.create_access_policies import (
    ATTR_DPSK_USERNAME,
    ATTR_WIRELESS_SSID,
    DPSK_POLICY_TEMPLATE_ID,
)

# VERIFIED against SuperSandbox 2026-09-20: the discriminator literal is
# "StringEvaluation". The spec declares discriminator {propertyName: "type"}
# with no mapping, so this was the one thing that had to be confirmed live.
# The others are kept only so a future tenant rejecting this one re-probes
# rather than failing silently.
TYPE_LITERALS = [
    "StringEvaluation", "String Evaluation", "STRING", "StringCriteria", "string",
]

# VERIFIED: the attributeTextMatch values on template 100.
#
# These are NOT discoverable from GET /policyTemplates/100/attributes -- that
# endpoint returns ten attributes and omits 1013 (the SSID) entirely, which is
# the one a DPSK policy cannot work without. Conditions, however, embed the
# whole templateAttribute inline, so reading any existing policy's conditions
# is the reliable source. Hence the fallback below.
KNOWN_MATCH_NAMES = {
    ATTR_DPSK_USERNAME: "Dpsk_Username",
    ATTR_WIRELESS_SSID: "SSID",
}


async def template_attribute_text(r1, tenant):
    """id -> attributeTextMatch, which is what matchName must equal."""
    resp = await asyncio.to_thread(
        r1.policy_sets.client.get,
        f"/policyTemplates/{DPSK_POLICY_TEMPLATE_ID}/attributes",
        **({"override_tenant_id": tenant} if tenant else {}),
    )
    body = r1.policy_sets.client.safe_json(resp)
    items = body.get("content", body.get("data", [])) if isinstance(body, dict) else body
    out = {}
    for a in items or []:
        if isinstance(a, dict) and a.get("id") is not None:
            out[int(a["id"])] = {
                "match": a.get("attributeTextMatch"),
                "name": a.get("name"),
                "type": a.get("attributeType"),
            }
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--controller", type=int, required=True)
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--policy-set", required=True, help="policy set NAME")
    ap.add_argument("--username", required=True)
    ap.add_argument("--ssid", required=True)
    args = ap.parse_args()

    from database import SessionLocal
    from clients.r1_client import create_r1_client_from_controller

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(args.controller, db)
        tenant = args.tenant
        post_kw = {"override_tenant_id": tenant} if tenant else {}

        # ---- the policy set ----
        sets = await r1.policy_sets.query_policy_sets(tenant_id=tenant, limit=100)
        rows = sets.get("content", sets.get("data", [])) if isinstance(sets, dict) else []
        match = [s for s in rows if (s.get("name") or "").lower() == args.policy_set.lower()]
        if not match:
            print(f"No policy set named {args.policy_set!r}.")
            print("Available:", ", ".join(sorted(str(s.get('name')) for s in rows)) or "(none)")
            return 1
        set_id = match[0]["id"]
        print(f"Policy set {args.policy_set!r} -> {set_id}")

        # ---- the attribute text, from R1 rather than hardcoded ----
        attrs = await template_attribute_text(r1, tenant)
        user_attr = attrs.get(ATTR_DPSK_USERNAME)
        ssid_attr = attrs.get(ATTR_WIRELESS_SSID)
        print(f"attr {ATTR_DPSK_USERNAME}: {user_attr}")
        print(f"attr {ATTR_WIRELESS_SSID}: {ssid_attr}")
        # GET /policyTemplates/100/attributes omits 1013, so fall back to the
        # values verified from real conditions rather than giving up.
        user_match = (user_attr or {}).get("match") or KNOWN_MATCH_NAMES[ATTR_DPSK_USERNAME]
        ssid_match = (ssid_attr or {}).get("match") or KNOWN_MATCH_NAMES[ATTR_WIRELESS_SSID]
        if not user_attr or not ssid_attr:
            print("(the attributes endpoint omitted one; using verified values)")
        print(f"\nmatchName -> username={user_match!r} ssid={ssid_match!r}\n")

        # ---- the documented request, varying only the type literal ----
        for literal in TYPE_LITERALS:
            payload = {
                "policySetId": set_id,
                "evaluationCriteria": [
                    {"matchName": user_match,
                     "value": {"type": literal, "stringValue": args.username}},
                    {"matchName": ssid_match,
                     "value": {"type": literal, "stringValue": args.ssid}},
                ],
            }
            try:
                resp = await asyncio.to_thread(
                    r1.policy_sets.client.post,
                    f"/policySets/{set_id}/evaluationReports",
                    payload=payload, **post_kw,
                )
                status = getattr(resp, "status_code", "?")
                body = r1.policy_sets.client.safe_json(resp)
            except Exception as e:
                print(f"  ERR   type={literal!r}: {type(e).__name__}: {e}")
                continue

            if isinstance(status, int) and 200 <= status < 300:
                print(f"  OK    type={literal!r}\n")
                print("=" * 60)
                print("REQUEST"); print(json.dumps(payload, indent=2))
                print("RESPONSE"); print(json.dumps(body, indent=2)[:2000])
                print("=" * 60)
                if isinstance(body, dict):
                    print(f"\nwasMatched       : {body.get('wasMatched')}")
                    print(f"policyName       : {body.get('policyName')}")
                    print(f"policyId         : {body.get('policyId')}")
                    print(f"onMatchResponse  : {body.get('onMatchResponse')}"
                          "   <- the RADIUS attribute group actually applied")
                return 0

            msg = body.get("message") if isinstance(body, dict) else None
            print(f"  {status}   type={literal!r}"
                  + (f" — {msg}" if msg else ""))
            if isinstance(body, dict) and body.get("errors"):
                print("        ", json.dumps(body["errors"])[:300])

        print("\nNo type literal accepted. The request shape above is from the")
        print("08122026 spec and should be right; only the discriminator value")
        print("is in question. The errors printed above name the field R1 is")
        print("unhappy with — add the literal it wants to TYPE_LITERALS.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
