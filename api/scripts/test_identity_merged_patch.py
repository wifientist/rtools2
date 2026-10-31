#!/usr/bin/env python3
"""
The Cloudpath GUID and the suffix rename go in one PATCH.

Run 883d087e sent 4,069 identity PATCHes for 2,035 residents, two each, to the
same route. update_identity_descriptions now strips the suffix as it writes the
GUID; create_access_policies still renames whatever is left.

Usage:
    docker compose exec backend python scripts/test_identity_merged_patch.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.cloudpath.update_identities import UpdateIdentityDescriptionsPhase
from workflow.v2.brain import WorkflowBrain

failures = 0


def check(label, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


class FakeIdentity:
    def __init__(self, refuse_names=()):
        self.calls = []
        self.refuse_names = refuse_names

    async def update_identity(self, group_id, identity_id, tenant_id=None,
                              description=None, vlan=None, name=None,
                              wait_for_task=False, on_accepted=None):
        self.calls.append({"id": identity_id, "description": description, "name": name})
        self.waited = wait_for_task
        if name in self.refuse_names:
            raise Exception("R1 API error (400): GENERAL-010")


def run(rows, options, identity=None):
    identity = identity or FakeIdentity()
    phase = UpdateIdentityDescriptionsPhase.__new__(UpdateIdentityDescriptionsPhase)
    phase.tenant_id = "t"
    phase.r1_client = type("C", (), {"identity": identity})()

    async def emit(*a, **k):
        pass

    async def parallel_map(items, fn, **k):
        results = [await fn(item) for item in items]
        return type("R", (), {"succeeded": results, "failed": []})()

    phase.emit = emit
    phase.parallel_map = parallel_map
    out = asyncio.run(phase.execute(UpdateIdentityDescriptionsPhase.Inputs(
        identity_group_id="g", created_passphrases=rows, **options,
    )))
    return identity.calls, out


def row(identity_id, username, **extra):
    return {
        "identity_id": identity_id, "username": username,
        "cloudpath_guid": f"guid-{identity_id}", "success": True, **extra,
    }


POLICIES_ON = {"enable_access_policies": True}

print("The option reaches this per-unit phase the way the Brain really fills it")
# Job 54ae5e7f: inputs.options was empty, so every rename stayed separate.
unit = SimpleNamespace(
    resolved=SimpleNamespace(extra={}), plan=SimpleNamespace(extra={}),
    input_config={}, unit_id="u", unit_number="1",
)
job = SimpleNamespace(global_phase_results={}, options=POLICIES_ON)
check("enable_access_policies resolves",
      "enable_access_policies" in UpdateIdentityDescriptionsPhase.Inputs.model_fields
      and WorkflowBrain._resolve_field("enable_access_policies", unit, job) is True)

print("One PATCH carries both")
calls, out = run([row("a", "4021_gigabit")], POLICIES_ON)
check("one call", len(calls) == 1, str(calls))
check("with the GUID and the stripped name",
      calls[0] == {"id": "a", "description": "guid-a", "name": "4021"})
check("counted as renamed", out.updated_count == 1 and out.renamed_count == 1)
check("waits for R1, and reports what R1 confirmed",
      out.renamed_identity_ids == ["a"])

print("Policies off: the import has never renamed in that case")
calls, out = run([row("a", "4021_gigabit")], {})
check("description only", calls[0]["name"] is None and out.renamed_count == 0)

print("No suffix: nothing to rename")
calls, out = run([row("a", "4021")], POLICIES_ON)
check("description only", calls[0]["name"] is None and out.renamed_count == 0)

print("Two tiers of one account stay with the rename step")
calls, out = run([row("a", "4021_gigabit"), row("b", "4021_fast")], POLICIES_ON)
check("neither renamed here", all(c["name"] is None for c in calls), str(calls))
check("both still get a GUID", [c["description"] for c in calls] == ["guid-a", "guid-b"])

print("R1 refuses the name: the GUID still lands")
calls, out = run([row("a", "4021_gigabit")], POLICIES_ON, FakeIdentity(refuse_names=("4021",)))
check("retried without the name", [c["name"] for c in calls] == ["4021", None], str(calls))
check("GUID written, not counted as renamed",
      calls[-1]["description"] == "guid-a"
      and out.updated_count == 1 and out.renamed_count == 0 and out.failed_count == 0)

print("A failed passphrase is not renamed")
calls, out = run([row("a", "4021_gigabit", success=False)], POLICIES_ON)
check("description only", calls[0]["name"] is None)

print("FAILED" if failures else "All passed")
sys.exit(1 if failures else 0)
