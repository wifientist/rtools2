"""
SZ → R1 AP Mapping stage: the APs themselves, last.

Create WLANs has built the configuration -- networks, every AP Group
(SZ-derived and staged), activations. This stage only puts APs where they
belong, and is skippable: config-only runs stop before it.

    ap_mapping_plan     read SZ snapshot + the venue now; list APs to move /
                        add (adding is opt-in), and anything configuration-
                        side that is missing (fix: re-run Create WLANs); pause
    map_aps             move APs into their group; add the ones not in the
                        venue when opted in (non-critical)
    verify_ap_mapping   read the venue back: membership, activations and the
                        audit's per-AP broadcast comparison

Each phase re-plans (services.sz_migration.ap_mapping) against a fresh
capture of the venue, so a re-run does only what is still missing. Moves go
through services.ap_group_ops, the same code APG Cleanup uses.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from redis_client import get_redis_client
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot
from services import ap_group_ops
from services.r1_inventory import capture_venue_inventory
from services.sz_migration.ap_mapping import plan_ap_mapping
from services.sz_migration.audit_matching import build_matching
from services.sz_migration.broadcast_model import build_broadcast_audit
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.v2.models import ConflictDetail, ResourceAction, ValidationResult, ValidationSummary

logger = logging.getLogger(__name__)

REPORT_KEY = "sz_migration:ap_mapping:{job_id}:report"
MAP_MAX_WAIT_SECONDS = 1200   # per wait, for the largest per-unit sites
REPORT_TTL = 7 * 24 * 3600
SAMPLE = 200


async def save_report(job_id: str, part: str, data: Any) -> None:
    redis = await get_redis_client()
    await redis.hset(REPORT_KEY.format(job_id=job_id), part, json.dumps(data, default=str))
    await redis.expire(REPORT_KEY.format(job_id=job_id), REPORT_TTL)


async def load_report(job_id: str) -> Dict[str, Any]:
    redis = await get_redis_client()
    raw = await redis.hgetall(REPORT_KEY.format(job_id=job_id))
    return {k: json.loads(v) for k, v in (raw or {}).items()}


class _Stage(PhaseExecutor):
    """Shared: the snapshot, a fresh capture of the venue, and the plan against it."""

    async def fresh(self, snapshot_job_id: str) -> Tuple[SZMigrationSnapshot, R1VenueInventory, Dict[str, Any]]:
        redis = await get_redis_client()
        raw = await redis.get(f"sz_migration:extraction:{snapshot_job_id}:snapshot")
        if not raw:
            raise RuntimeError("SZ snapshot not found — run the extraction again")
        snapshot = SZMigrationSnapshot.model_validate_json(raw)
        inv = await capture_venue_inventory(self.r1_client, self.tenant_id, self.venue_id)
        plan = plan_ap_mapping(snapshot, inv, self.context.options.get("layout_choices") or {},
                               add_new_aps=bool(self.context.options.get("add_new_aps")),
                               selected=self.context.options.get("selected_serials"),
                               name_policy=self.context.options.get("ap_name_policy") or "serial",
                               renames=self.context.options.get("ap_renames") or {})
        return snapshot, inv, plan

    async def read_aps(self) -> List[Dict[str, Any]]:
        return (await self.r1_client.venues.get_aps_by_tenant_venue(self.tenant_id, self.venue_id)).get("data") or []


@register_phase("ap_mapping_plan", "Plan AP Mapping")
class ApMappingPlanPhase(_Stage):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        validation_result: Optional[ValidationResult] = None
        counts: Dict[str, Any] = {}

    async def execute(self, inputs: Inputs) -> Outputs:
        snapshot, inv, plan = await self.fresh(inputs.sz_snapshot_job_id)
        c = plan["counts"]
        await self.emit(f"Zone '{snapshot.zone.name}' → {inv.venue_name}: {c['aps']} APs "
                        f"({c['aps_already']} already mapped, {c['aps_move']} to move, {c['aps_add']} to add, "
                        f"{c['aps_not_in_r1']} not in R1 and not added, "
                        f"{c['aps_group_missing']} waiting on an AP Group)")

        actions: List[ResourceAction] = []
        for g in plan["groups"]:
            members = [a for a in plan["aps"] if a["group_key"] == g["key"]]
            moves = sum(1 for a in members if a["action"] == "move")
            adds = sum(1 for a in members if a["action"] == "add")
            if not g["exists"]:
                continue   # reported above as waiting on Create WLANs
            notes = [f"{len(members)} AP(s): {moves} to move, {adds} to add, "
                     f"{len(members) - moves - adds} already in it", "SSIDs: " + ", ".join(g["ssids"])]
            if g["staged"]:
                notes.append("Staged: its WLAN(s) are created but NOT activated — no AP broadcasts them in SZ")
            actions.append(ResourceAction(resource_type="ap_group", name=g["name"],
                                          action="update" if moves or adds else "reuse", notes=notes))
        dflt = [a for a in plan["aps"] if a["group_key"] == "__default__"]
        if dflt:
            moves = sum(1 for a in dflt if a["action"] in ("move", "add"))
            actions.append(ResourceAction(resource_type="ap_group", name="(default AP Group)",
                                          action="update" if moves else "reuse",
                                          notes=[f"{len(dflt)} AP(s) that broadcast only the All AP Groups WLANs "
                                                 f"(or nothing) in SZ; {moves} to place"]))

        conflicts = [ConflictDetail(resource_type="layout", resource_name="", description=w["message"],
                                    severity=w["severity"]) for w in plan["warnings"]]
        # Configuration is Create WLANs' job: anything missing here is reported, not fixed.
        for a in plan["activations"]:
            if a["status"] in ("no_network", "conflict"):
                conflicts.append(ConflictDetail(resource_type="wifi_network", resource_name=a["name"],
                                                description=a["error"], severity="warning"))
        if c["wlans_needed"]:
            names = [a["name"] for a in plan["activations"] if a["status"] == "needed"]
            conflicts.append(ConflictDetail(
                resource_type="wifi_network", resource_name=", ".join(names[:8]),
                description=f"{c['wlans_needed']} WLAN(s) not active where the plan puts them — re-run Create WLANs "
                            "(AP Mapping doesn't activate)", severity="warning"))
        if c["aps_group_missing"]:
            missing = sorted({a["group_name"] for a in plan["aps"] if a["action"] == "group_missing"})
            conflicts.append(ConflictDetail(
                resource_type="ap_group", resource_name=", ".join(missing[:8]),
                description=f"{len(missing)} AP Group(s) for {c['aps_group_missing']} AP(s) aren't in R1 yet — "
                            "re-run Create WLANs (it builds every AP Group)", severity="warning"))
        if c["aps_unmapped"]:
            conflicts.append(ConflictDetail(
                resource_type="ap_group", resource_name=", ".join(plan["unmapped_groups"][:8]),
                description=f"{c['aps_unmapped']} AP(s) belong in AP Groups neither in R1 nor ticked to create "
                            "— they stay where they are", severity="warning"))
        if c["aps_name_clash"]:
            dup = [a for a in plan["aps"] if a.get("name_clash")]
            renamed = [a for a in dup if a.get("r1_name") != a["name"]]
            conflicts.append(ConflictDetail(
                resource_type="ap", resource_name=", ".join(sorted({a["name"] for a in dup})[:8]),
                description=(f"{len(dup)} AP(s) share a name R1 needs to be unique — "
                             + (f"added as <name>_<serial>, e.g. '{renamed[0]['r1_name']}'" if renamed
                                else "names kept: R1 will refuse adding the duplicates")),
                severity="warning"))
        if c["aps_not_in_r1"]:
            conflicts.append(ConflictDetail(
                resource_type="ap", resource_name="",
                description=f"{c['aps_not_in_r1']} SZ AP(s) are not in this R1 venue and adding them is off — they are "
                            "not added. Turn on 'Add APs not yet in R1' to add them into their AP Groups.",
                severity="warning"))
        if c["aps_keep"]:
            kept = [a for a in plan["aps"] if a["action"] == "keep"]
            conflicts.append(ConflictDetail(
                resource_type="ap", resource_name=", ".join(a["name"] or a["serial"] for a in kept[:8]),
                description=f"{c['aps_keep']} AP(s) broadcast only the All AP Groups WLANs in SZ, but R1 has them "
                            "in groups with SSIDs of their own (a fix made in R1?) — kept where they are",
                severity="warning"))
        for msg in plan["staged"]["conflicts"]:
            conflicts.append(ConflictDetail(resource_type="ap_group", resource_name="staging",
                                            description=msg, severity="warning"))
        if plan["staged"]["unpaired"]:
            un = plan["staged"]["unpaired"]
            conflicts.append(ConflictDetail(
                resource_type="ap_group", resource_name=", ".join(un[:8]),
                description=f"{len(un)} staged WLAN(s) paired with no AP — created, but no AP Group is built for them",
                severity="warning"))
        if c["aps_blocked"]:
            conflicts.append(ConflictDetail(resource_type="ap", resource_name="",
                                            description=f"{c['aps_blocked']} SZ AP(s) have no serial — not mapped",
                                            severity="warning"))
        if c["aps_silent"]:
            conflicts.append(ConflictDetail(
                resource_type="ap", resource_name="",
                description=f"{c['aps_silent']} AP(s) broadcast nothing in SZ; in the default group they get the "
                            "All AP Groups WLANs", severity="warning"))
        if plan["blocked"]:
            conflicts.append(ConflictDetail(resource_type="layout", resource_name="",
                                            description="Placement is blocked — fix it on this step first",
                                            severity="error"))

        calls = c["aps_move"] + c["aps_add"]
        await save_report(self.job_id, "before", plan["counts"])
        return self.Outputs(
            validation_result=ValidationResult(
                valid=not any(x.severity == "error" for x in conflicts),
                conflicts=conflicts,
                summary=ValidationSummary(total_units=0, total_api_calls=calls),
                unit_plans={},
                actions=actions,
            ),
            counts=plan["counts"],
        )


@register_phase("map_aps", "Map APs")
class MapApsPhase(_Stage):

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        counts: Dict[str, int] = {}

    async def execute(self, inputs: Inputs) -> Outputs:
        _, _, plan = await self.fresh(inputs.sz_snapshot_job_id)
        todo = [a for a in plan["aps"] if a["action"] in ("move", "add")]
        if not todo:
            await self.emit("Every mappable AP is already in its AP Group")
            await save_report(self.job_id, "map", {"counts": {}, "problems": []})
            return self.Outputs()

        await self.emit(f"Mapping {len(todo)} AP(s): "
                        f"{sum(1 for a in todo if a['action'] == 'move')} to move, "
                        f"{sum(1 for a in todo if a['action'] == 'add')} to add...")
        rows = [{"serial": a["serial"], "ap_name": a.get("r1_name") or a["name"], "to_group_id": a["group_id"],
                 "to_group_name": a["group_name"], "from_group": a["current_group"], "op": a["action"]}
                for a in todo]
        # Hundreds of adds keep landing in R1 long after their tasks are queued:
        # wait in proportion to the batch, then keep re-reading until every AP
        # shows in its group (or the deadline), so verify sees the result.
        budget = min(MAP_MAX_WAIT_SECONDS, max(120, 3 * len(rows)))
        await ap_group_ops.place_aps(self.r1_client, self.tenant_id, self.venue_id, rows, self.read_aps,
                                     log_label="[SZ AP Mapping]", wait_seconds=budget,
                                     confirm_seconds=budget, progress=self.emit)
        counts: Dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        problems = [r for r in rows if r["status"] in ("failed", "unconfirmed")]
        await save_report(self.job_id, "map", {"counts": counts, "problems": problems[:SAMPLE]})
        await self.emit(", ".join(f"{v} {k}" for k, v in sorted(counts.items())),
                        "success" if not problems else "warning")
        for r in problems[:20]:
            await self.emit(f"{r['ap_name']} ({r['serial']}) → {r['to_group_name']}: {r['status']} — {r['error']}",
                            "warning")
        failed = [r for r in problems if r["status"] == "failed"]
        if failed:
            raise RuntimeError(f"{len(failed)} AP(s) not mapped: "
                               + "; ".join(f"{r['ap_name']}: {r['error']}" for r in failed[:5]))
        return self.Outputs(counts=counts)


@register_phase("verify_ap_mapping", "Verify AP Mapping")
class VerifyApMappingPhase(_Stage):
    """The venue read back: membership, activations, and what every AP broadcasts."""

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        counts: Dict[str, Any] = {}

    async def execute(self, inputs: Inputs) -> Outputs:
        snapshot, inv, plan = await self.fresh(inputs.sz_snapshot_job_id)
        c = plan["counts"]
        broadcast = build_broadcast_audit(snapshot, inv, build_matching(snapshot, inv))
        # Kept as R1 has them, or placed in the default group because their AP
        # Group wasn't built (the operator's call): listed, not failed.
        kept = {a["serial"] for a in plan["aps"] if a["action"] == "keep" or a.get("fallback_default")}
        # Kept on purpose (R1 adds SSIDs SZ doesn't broadcast there): listed, not failed.
        differing = [a for a in broadcast.aps if a.status == "differs" and (a.serial or "").upper() not in kept]
        not_mapped = [a for a in plan["aps"] if a["action"] in ("move", "add")]
        not_active = [a for a in plan["activations"] if a["status"] in ("needed", "conflict")]

        report = {
            "counts": c,
            "broadcast": broadcast.counts,
            "not_mapped": not_mapped[:SAMPLE],
            "not_active": [{"name": a["name"], "status": a["status"], "error": a["error"],
                            "targets": [t for t in a["targets"] if t["status"] != "already"]}
                           for a in not_active],
            "differing": [{"name": a.name, "serial": a.serial, "missing_in_r1": a.missing_in_r1,
                           "extra_in_r1": a.extra_in_r1, "sz_ap_group": a.sz_ap_group,
                           "r1_ap_group": a.r1_ap_group} for a in differing[:SAMPLE]],
            "differing_total": len(differing),
            "not_in_r1": c["aps_not_in_r1"],
            "fallback_default": [{"name": a["name"], "serial": a["serial"], "wanted_group": a.get("wanted_group")}
                                 for a in plan["aps"] if a.get("fallback_default")][:SAMPLE],
            "groups_missing": sorted({a["group_name"] for a in plan["aps"] if a["action"] == "group_missing"}),
            "kept": [{"name": a["name"], "serial": a["serial"], "current_group": a["current_group"],
                      "extra_ssids": a.get("extra_ssids") or []} for a in plan["aps"] if a["action"] == "keep"],
            "no_network": [a["name"] for a in plan["activations"] if a["status"] == "no_network"],
            "staged": [{"name": g["name"], "ssids": g["ssids"], "ap_count": g["ap_count"]}
                       for g in plan["groups"] if g["staged"]][:SAMPLE],
        }
        await save_report(self.job_id, "verify", report)

        await self.emit(f"APs in their AP Group: {c['aps_already']} of "
                        f"{c['aps'] - c['aps_unmapped'] - c['aps_blocked'] - c['aps_not_in_r1'] - c['aps_keep']}"
                        f"; WLANs active as planned: {c['wlans_already']} of {c['wlans'] - c['wlans_no_network']}")
        await self.emit(f"Broadcast: {len(differing)} AP(s) differ from SZ "
                        f"(of {broadcast.counts.get('aps_compared', 0)} compared)",
                        "success" if not differing else "warning")
        problems = []
        if c["aps_group_missing"]:
            problems.append(f"{c['aps_group_missing']} AP(s) waiting on AP Groups not in R1 (re-run Create WLANs)")
        if not_mapped:
            problems.append(f"{len(not_mapped)} AP(s) not in their AP Group")
        if not_active:
            problems.append(f"{len(not_active)} WLAN(s) not active as planned (re-run Create WLANs)")
        if differing:
            problems.append(f"{len(differing)} AP(s) broadcast differently from SZ")
        if problems:
            raise RuntimeError("; ".join(problems))
        return self.Outputs(counts=c)
