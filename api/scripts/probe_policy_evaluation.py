#!/usr/bin/env python3
"""
Probe POST /policySets/{id}/evaluationReports to learn its payload shape.

WHY THIS EXISTS

The audit currently answers "is there a policy NAMED for this account, and
what RADIUS group does it point at", and with --verify-conditions also "what
does that policy actually match on". Neither answers the real question:

    which policy does this identity land on, and therefore which RADIUS
    attribute group does it actually get?

Policies live in a SET and are evaluated in priority order -- the first match
wins. So a higher-priority policy with a looser regex can swallow a resident
whose own policy is perfectly correct, and nothing we read today would show
it. R1 has an endpoint that answers exactly this:

    POST /policySets/{policySetId}/evaluationReports

    "Evaluates the criteria provided and returns the matched response from
     the first matching policy within the specified set, or it will indicate
     that no match was found."

THE PROBLEM

Its request and response schemas are EMPTY in the consolidated spec:

    "requestBody": {"content": {"application/json": {}}, "required": true}
    "responses": {"200": {"content": {}}}

So the payload shape is undocumented and has to be discovered. Rather than
guess inside a shipped feature, this tries a handful of plausible shapes
against a real policy set and reports which one R1 accepts.

READ-ONLY. It POSTs, but to an evaluation/report endpoint that returns a
verdict and changes no configuration. It writes nothing else, and it never
touches identities, policies or passphrases.

Usage:
    docker compose exec backend python scripts/probe_policy_evaluation.py \\
        --controller 3 --policy-set "The durant" \\
        --username 8081081081 --ssid "101@The_durant" [--tenant <id>]

Print the winning shape and wire it into identity_audit.py as the
authoritative RADIUS column.
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
)


def candidate_payloads(username: str, ssid: str):
    """
    Plausible shapes, most-likely first.

    Reasoning: every other condition API in this template speaks in
    templateAttributeId + evaluationRule/regexStringCriteria, so the
    evaluation side most likely speaks in templateAttributeId + a literal
    VALUE to test. The flatter shapes are cheap to try and would be the
    obvious design if the endpoint predates the template attributes.
    """
    return [
        ("attributes[] w/ templateAttributeId+value", {
            "attributes": [
                {"templateAttributeId": ATTR_DPSK_USERNAME, "value": username},
                {"templateAttributeId": ATTR_WIRELESS_SSID, "value": ssid},
            ]
        }),
        ("criteria[] w/ templateAttributeId+value", {
            "criteria": [
                {"templateAttributeId": ATTR_DPSK_USERNAME, "value": username},
                {"templateAttributeId": ATTR_WIRELESS_SSID, "value": ssid},
            ]
        }),
        ("bare list of templateAttributeId+value", [
            {"templateAttributeId": ATTR_DPSK_USERNAME, "value": username},
            {"templateAttributeId": ATTR_WIRELESS_SSID, "value": ssid},
        ]),
        ("attributeId instead of templateAttributeId", {
            "attributes": [
                {"attributeId": ATTR_DPSK_USERNAME, "value": username},
                {"attributeId": ATTR_WIRELESS_SSID, "value": ssid},
            ]
        }),
        ("flat named keys", {"dpskUsername": username, "wirelessSsid": ssid}),
        ("testCriteria map keyed by attribute id", {
            "testCriteria": {
                str(ATTR_DPSK_USERNAME): username,
                str(ATTR_WIRELESS_SSID): ssid,
            }
        }),
    ]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--controller", type=int, required=True)
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--policy-set", required=True, help="policy set NAME")
    ap.add_argument("--username", required=True, help="e.g. 8081081081")
    ap.add_argument("--ssid", required=True, help='e.g. "101@PropertyName"')
    args = ap.parse_args()

    from database import SessionLocal
    from clients.r1_client import create_r1_client_from_controller

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(args.controller, db)
        tenant = args.tenant

        sets = await r1.policy_sets.query_policy_sets(tenant_id=tenant, limit=100)
        rows = sets.get("content", sets.get("data", [])) if isinstance(sets, dict) else []
        match = [s for s in rows if (s.get("name") or "").lower() == args.policy_set.lower()]
        if not match:
            print(f"No policy set named {args.policy_set!r}.")
            print("Available:", ", ".join(sorted(s.get("name", "?") for s in rows)) or "(none)")
            return 1
        set_id = match[0]["id"]
        print(f"Policy set {args.policy_set!r} -> {set_id}")
        print(f"Testing username={args.username!r} ssid={args.ssid!r}\n")

        accepted = []
        for label, payload in candidate_payloads(args.username, args.ssid):
            try:
                result = await asyncio.to_thread(
                    r1.policy_sets.client.post,
                    f"/policySets/{set_id}/evaluationReports",
                    payload=payload,
                    **({"override_tenant_id": tenant} if tenant else {}),
                )
                status = getattr(result, "status_code", "?")
                body = r1.policy_sets.client.safe_json(result)
            except Exception as e:
                print(f"  ERR   {label}: {type(e).__name__}: {e}")
                continue

            ok = isinstance(status, int) and 200 <= status < 300
            print(f"  {'OK  ' if ok else str(status).ljust(4)}  {label}")
            if ok:
                accepted.append((label, payload, body))
                print("        response:", json.dumps(body)[:600])
            else:
                msg = body.get("message") if isinstance(body, dict) else None
                if msg:
                    print(f"        {msg}")

        print()
        if accepted:
            label, payload, body = accepted[0]
            print(f"ACCEPTED SHAPE: {label}")
            print("request: ", json.dumps(payload, indent=2))
            print("response:", json.dumps(body, indent=2)[:2000])
            print(
                "\nNext: add evaluate_policy_criteria() usage to "
                "routers/cloudpath/identity_audit.py using this shape, as an "
                "opt-in column alongside verify_conditions."
            )
            return 0

        print("No candidate shape was accepted. Capture one real call from the")
        print("RUCKUS One UI devtools (Policy Set -> Evaluate/Test) and add it")
        print("to candidate_payloads().")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
