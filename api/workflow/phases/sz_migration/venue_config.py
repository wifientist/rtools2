"""
SZ → R1 Venue Configuration stage: plan and verify phases.

The apply phase in between is apply_venue_settings. All three read the zone
from the extraction snapshot and build the same section plan
(venue_create.build_settings_plan), so what is shown, written and checked
is one list.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from redis_client import get_redis_client
from schemas.sz_migration import SZMigrationSnapshot
from services.sz_migration.venue_create import build_settings_plan, verify_settings
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.v2.models import ConflictDetail, ResourceAction, ValidationResult, ValidationSummary

logger = logging.getLogger(__name__)

# The stage's report (apply + verify results), kept apart from the job: a
# phase that fails stores no outputs, and the verification is wanted most
# exactly when it fails.
REPORT_KEY = "sz_migration:venue_config:{job_id}:report"
REPORT_TTL = 7 * 24 * 3600


async def save_stage_report(job_id: str, part: str, data: Any) -> None:
    redis = await get_redis_client()
    await redis.hset(REPORT_KEY.format(job_id=job_id), part, json.dumps(data, default=str))
    await redis.expire(REPORT_KEY.format(job_id=job_id), REPORT_TTL)


async def load_stage_report(job_id: str) -> Dict[str, Any]:
    redis = await get_redis_client()
    raw = await redis.hgetall(REPORT_KEY.format(job_id=job_id))
    return {k: json.loads(v) for k, v in (raw or {}).items()}


async def load_venue_plan(snapshot_job_id: str, chosen: Optional[List[str]]):
    """(plan, chosen keys, snapshot) -- chosen None = every section."""
    redis = await get_redis_client()
    raw = await redis.get(f"sz_migration:extraction:{snapshot_job_id}:snapshot")
    if not raw:
        raise RuntimeError("SZ snapshot not found — run the extraction again")
    snapshot = SZMigrationSnapshot.model_validate_json(raw)
    plan = build_settings_plan(snapshot.zone.raw, snapshot.zone_ap_models)
    known = [s.key for s in plan["sections"]]
    keys = known if chosen is None else [k for k in known if k in set(chosen)]
    return plan, keys, snapshot


@register_phase("venue_config_plan", "Plan Venue Configuration")
class VenueConfigPlanPhase(PhaseExecutor):
    """Phase 0: what the zone sets, section by section, and how much of it R1 doesn't hold yet."""

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        validation_result: Optional[ValidationResult] = None
        sections: List[Dict[str, Any]] = []

    async def execute(self, inputs: Inputs) -> Outputs:
        plan, keys, snapshot = await load_venue_plan(
            inputs.sz_snapshot_job_id, self.context.options.get("venue_settings"))
        await self.emit(f"Zone '{snapshot.zone.name}': {len(keys)} of {len(plan['sections'])} section(s) chosen")

        # How far R1 is from the zone now -- the same check the job ends with.
        before = {r["key"]: r for r in await verify_settings(
            self.r1_client, self.tenant_id, self.venue_id, plan, keys)}

        actions: List[ResourceAction] = []
        conflicts: List[ConflictDetail] = []
        for sec in plan["sections"]:
            if sec.key not in keys:
                actions.append(ResourceAction(resource_type="venue_settings", name=sec.label, action="skip",
                                              notes=["Not chosen — R1's setting stays"]))
                continue
            b = before.get(sec.key) or {}
            n = len(b.get("differences") or [])
            models_note = []
            if sec.by_model and "pushed" in b:
                # Only the zone's models that differ from R1's defaults are written.
                models_note = [f"Writes: {', '.join(b['pushed']) or 'nothing'}"
                               + (f"; R1 defaults kept for {', '.join(b['defaults'])}" if b.get("defaults") else "")]
            if b.get("status") == "verified":
                actions.append(ResourceAction(resource_type="venue_settings", name=sec.label, action="reuse",
                                              notes=(["Every zone model is on R1's defaults — nothing to write"]
                                                     if sec.by_model and not b.get("pushed")
                                                     else ["R1 already matches the zone"]) + models_note))
            else:
                note = (f"{n} value(s) differ from the zone" if b.get("status") == "differs"
                        else f"Current R1 value unreadable: {b.get('error')}")
                actions.append(ResourceAction(resource_type="venue_settings", name=sec.label, action="update",
                                              notes=[note] + models_note + [f"{len(sec.rows)} setting(s) from the zone"]
                                              + sec.notes))
        if not snapshot.zone_ap_models and snapshot.aps:
            conflicts.append(ConflictDetail(
                resource_type="venue_settings", resource_name="AP model settings",
                description="This extraction predates per-AP-model settings (PoE, LAN ports, LEDs) — "
                            "run the extraction again to include them",
                severity="warning"))
        for n in plan["not_carried"]:
            conflicts.append(ConflictDetail(resource_type="venue_settings", resource_name="",
                                            description=f"Not carried: {n}", severity="warning"))
        if not keys:
            conflicts.append(ConflictDetail(resource_type="venue_settings", resource_name="",
                                            description="No settings section is chosen", severity="error"))

        updates = sum(1 for a in actions if a.action == "update")
        return self.Outputs(
            validation_result=ValidationResult(
                valid=not any(c.severity == "error" for c in conflicts),
                conflicts=conflicts,
                summary=ValidationSummary(total_units=0, total_api_calls=2 * updates + len(keys)),
                unit_plans={},
                actions=actions,
            ),
            sections=list(before.values()),
        )


@register_phase("verify_venue_settings", "Verify Venue Settings")
class VerifyVenueSettingsPhase(PhaseExecutor):
    """Read the venue back; fail with every field R1 doesn't hold."""

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        results: List[Dict[str, Any]] = []
        verified: int = 0

    async def execute(self, inputs: Inputs) -> Outputs:
        plan, keys, _ = await load_venue_plan(inputs.sz_snapshot_job_id, self.context.options.get("venue_settings"))
        if not keys:
            return self.Outputs()
        results = await verify_settings(self.r1_client, self.tenant_id, self.venue_id, plan, keys)
        bad = [r for r in results if r["status"] != "verified"]
        for r in results:
            if r["status"] == "verified":
                await self.emit(f"{r['label']}: verified", "success")
            elif r["status"] == "differs":
                fields = ", ".join(d["field"] for d in r["differences"][:6])
                await self.emit(f"{r['label']}: R1 differs from the zone — {fields}", "warning")
            else:
                await self.emit(f"{r['label']}: could not read back — {r.get('error')}", "warning")
        await save_stage_report(self.job_id, "verify", results)
        if bad:
            detail = "; ".join(
                f"{r['label']}: " + (", ".join(f"{d['field']} (wanted {d['wanted']}, R1 has {d['actual']})"
                                               for d in r["differences"][:4]) or r.get("error", "unreadable"))
                for r in bad)
            raise RuntimeError(f"{len(bad)} of {len(results)} section(s) not as the zone sets them — {detail}")
        return self.Outputs(results=results, verified=len(results))
