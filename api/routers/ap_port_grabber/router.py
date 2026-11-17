"""
AP Port Grabber Router

Give R1 APs the LAN ports and PoE their SZ counterparts have:

- POST /ap-port-grabber/preview          -> read both sides; one row per SZ AP of
  the zone, plus the SZ Ethernet Port Profiles to map to R1 ones (read-only)
- POST /ap-port-grabber/v2/plan           -> re-read, start an ApPortGrabberWorkflow
  job and validate it
- GET  /ap-port-grabber/v2/{id}/plan      -> plan results
- POST /ap-port-grabber/v2/{id}/confirm   -> confirm and run
- GET  /ap-port-grabber/v2/{id}/graph     -> workflow graph

The R1 venue is required: AP LAN port endpoints are per venue, and the venue's
per-model ports are what an AP on venue settings gets. Venue-level ports are
the migration's job (services/sz_migration/lan_ports.py); this tool covers
what SZ sets per AP and per AP Group, which R1 can only hold per AP.

Comparison and statuses: services/ap_port_grabber.py. Phases:
workflow/phases/ap_port_grabber.py. Nothing here is secret.
"""

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from dependencies import get_current_user, get_db
from models.user import User
from redis_client import get_redis_client
from routers.tenant_scope import resolve_tenant_id
from services.ap_port_grabber import (
    ACTIONABLE, REVIEW, UPDATE, build_rows, norm_serial, profiles_to_map,
    r1_profile_summary, suggest_profile, sz_ap_fields, unit_spec,
)
from services.ap_port_reads import read_r1_lan_state, read_sz_group_layers
from services.sz_migration import lan_ports
from workflow.events import WorkflowEventPublisher
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.failsafe import fail_job
from workflow.v2.graph import DependencyGraph
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.workflows.ap_port_grabber import ApPortGrabberWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap-port-grabber", tags=["AP Port Grabber"])

READ_CONCURRENCY = 10
VALIDATE_PHASE = "validate_ap_port_grabber"


# ==================== Models ====================

class PreviewRequest(BaseModel):
    sz_controller_id: int
    zone_id: str
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: str


class ProfileChoice(BaseModel):
    """Which R1 Ethernet Port Profile stands in for one SZ profile."""
    action: Literal["existing", "create"]
    r1_profile_id: Optional[str] = None
    name: Optional[str] = Field(None, max_length=lan_ports.NAME_MAX)


class PlanRequest(PreviewRequest):
    aps: List[str] = Field(..., min_length=1, description="SZ AP MACs")
    profiles: Dict[str, ProfileChoice] = Field(default_factory=dict)


# ==================== Reading ====================

def _check_controller(controller_id: int, kind: str, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != kind:
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be {kind}, "
                                                    f"got {controller.controller_type}")
    return controller


async def _read_sz(sz_client, zone_id: str, only: Optional[set] = None) -> Dict[str, Any]:
    """The zone's APs and everything that decides their LAN ports; `only` = just these SZ MACs (the plan)."""
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    base = f"/{sz_client.api_version}/rkszones/{zone_id}"
    zone = await sz_client.zones.get_zone_details(zone_id)
    aps = [sz_ap_fields(a) for a in await sz_client.aps.get_all_aps_by_zone(zone_id)]
    aps = [a for a in aps if a["mac"] and (only is None or a["mac"] in only)]
    if any(not a["group_name"] for a in aps):
        names = {g.get("id"): g.get("name") for g in await sz_client.apgroups.get_all_ap_groups_paginated(zone_id)}
        for a in aps:
            a["group_name"] = a["group_name"] or names.get(a["group_id"])

    async def get(path: str):
        async with sem:
            try:
                return await sz_client._request("GET", path) or {}
            except Exception as e:
                return {"_error": f"{e}"[:200] or type(e).__name__}

    macs = [a["mac"] for a in aps]
    details = dict(zip(macs, await asyncio.gather(*[get(f"/{sz_client.api_version}/aps/{m}") for m in macs])))
    models = sorted({a["model"] for a in aps if a["model"]})
    zone_models = dict(zip(models, await asyncio.gather(*[get(f"{base}/apmodel/{m}") for m in models])))
    group_models, profiles = await read_sz_group_layers(
        sz_client, zone_id, aps,
        [*zone_models.values(), *[(d or {}).get("specific") for d in details.values()]],
        concurrency=READ_CONCURRENCY)
    return {"zone": zone or {}, "sz_aps": aps, "sz_details": details, "zone_models": zone_models,
            "group_models": group_models, "sz_profiles": profiles}


async def _read_r1(r1_client, tenant_id: str, venue_id: str, sz_aps: List[Dict[str, Any]]) -> Dict[str, Any]:
    return await read_r1_lan_state(r1_client, tenant_id, venue_id, sz_aps, concurrency=READ_CONCURRENCY)


async def _compare(request: PreviewRequest, user: User, db: Session, only: Optional[set] = None) -> Dict[str, Any]:
    """
    Read both sides and build the rows. Shared by preview (the whole zone)
    and plan (`only` the ticked SZ MACs: their SZ detail and their R1 ports,
    plus the few zone- and venue-wide reads).
    """
    _check_controller(request.sz_controller_id, "SmartZone", user, db)
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)

    sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
    try:
        async with sz_client:
            sz = await _read_sz(sz_client, request.zone_id, only)
    except Exception as e:
        logger.exception(f"[AP Port Grabber] SZ read failed for zone {request.zone_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the zone from SmartZone: {e}")

    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    try:
        r1 = await _read_r1(r1_client, tenant_id, request.venue_id, sz["sz_aps"])
    except Exception as e:
        logger.exception(f"[AP Port Grabber] R1 read failed for venue {request.venue_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the venue from R1: {e}")

    result = build_rows({**sz, **{k: r1[k] for k in ("r1_aps", "caps", "venue_entries", "ap_states", "activations",
                                                    "venue_profiles")}})
    return {"tenant_id": tenant_id, "sz": sz, "r1": r1, **result}


# ==================== Endpoints ====================

@router.post("/preview")
async def preview(
    request: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One row per SZ AP of the zone, and the SZ profiles to map. Read-only."""
    c = await _compare(request, current_user, db)
    venue_name = c["r1"]["venue"].get("name") or ""
    r1_profiles = [r1_profile_summary(p) for p in c["r1"]["profiles"] if p.get("id")]
    to_map = profiles_to_map(c["rows"], c["sz"]["sz_profiles"])
    sz_profiles = [{**e, "suggestion": suggest_profile(e, r1_profiles, venue_name)}
                   for e in sorted(to_map.values(), key=lambda e: (e["name"] or "").lower())]
    for r in c["rows"]:
        # Which SZ profiles this AP's written ports need (the plan needs each mapped).
        flip = r.get("r1_use_venue") is not False
        r["profile_ids"] = sorted({p["sz_profile_id"] for p in r.get("port_plan") or []
                                   if p.get("sz_profile_id") and (flip or p.get("changed"))})
        r.pop("port_plan", None)
        r.pop("poe_plan", None)
    return {
        "zone_name": c["sz"]["zone"].get("name") or "",
        "venue_name": venue_name,
        "rows": c["rows"],
        "model_notes": c["model_notes"],
        "sz_profiles": sz_profiles,
        "r1_profiles": r1_profiles,
    }


class V2PlanResponse(BaseModel):
    job_id: str
    status: str
    message: str


class V2PlanResult(BaseModel):
    job_id: str
    status: str
    valid: bool = False
    unit_count: int = 0
    summary: Dict[str, Any] = {}
    conflicts: list = []
    estimated_api_calls: int = 0
    actions: list = []


def _brain(r1_client, state_manager, redis_client, tenant_id) -> WorkflowBrain:
    return WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(r1_client, state_manager, tenant_id=tenant_id),
        event_publisher=WorkflowEventPublisher(redis_client),
        r1_client=r1_client,
    )


async def _run_validation(job_id: str, controller_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[AP Port Grabber] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        await _brain(r1_client, state_manager, redis_client, job.tenant_id).run_validation(job)
        logger.info(f"[AP Port Grabber] Validation complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[AP Port Grabber] Validation failed for job {job_id}: {e}")
    finally:
        db.close()


async def _run_execution(job_id: str, controller_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[AP Port Grabber] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        brain = _brain(r1_client, state_manager, redis_client, job.tenant_id)
        await brain.tracker.start()
        await brain.execute_workflow(job)
        await brain.tracker.stop()
        logger.info(f"[AP Port Grabber] Execution complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[AP Port Grabber] Execution failed for job {job_id}: {e}")
        await fail_job(None, job_id, e, source="AP Port Grabber")
    finally:
        db.close()


@router.post("/v2/plan", response_model=V2PlanResponse)
async def create_plan(
    request: PlanRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-read both sides for the chosen APs and start a job to validate. Nothing is written yet."""
    chosen = set(request.aps)
    if len(chosen) != len(request.aps):
        raise HTTPException(status_code=400, detail="An AP appears more than once")
    c = await _compare(request, current_user, db, only=chosen)

    aps, changed_since = [], []
    for r in c["rows"]:
        if r["sz_mac"] not in chosen:
            continue
        if r["status"] in ACTIONABLE:
            aps.append(unit_spec(r))
        else:
            changed_since.append(f"{r['sz_name']}: now {r['status']}")
    if not aps:
        raise HTTPException(status_code=400, detail="None of the chosen APs needs a change any more"
                            + (f" ({'; '.join(changed_since[:5])})" if changed_since else ""))

    # The SZ side of each profile goes with the choice, so validation can check the type.
    to_map = profiles_to_map([r for r in c["rows"] if r["sz_mac"] in chosen and r["status"] in (UPDATE, REVIEW)],
                             c["sz"]["sz_profiles"])
    profiles: Dict[str, Dict[str, Any]] = {}
    for sz_id, e in to_map.items():
        choice = request.profiles.get(sz_id)
        profiles[sz_id] = {
            "action": choice.action if choice else None,
            "r1_profile_id": choice.r1_profile_id if choice else None,
            "name": (choice.name or "").strip() if choice else None,
            "sz_name": e["name"], "type": e["type"], "untagId": e["untagId"], "vlanMembers": e["vlanMembers"],
            "auth": e["auth"],
        }

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=c["tenant_id"]),
    )
    job = await brain.create_job(
        workflow=ApPortGrabberWorkflow,
        venue_id=request.venue_id,
        tenant_id=c["tenant_id"],
        controller_id=request.r1_controller_id,
        user_id=current_user.id,
        options={},
        input_data={
            "aps": aps,
            "profiles": profiles,
            "sz_controller_id": request.sz_controller_id,
            "zone_id": request.zone_id,
        },
    )
    background_tasks.add_task(_run_validation, job.id, request.r1_controller_id)
    logger.info(f"[AP Port Grabber] Plan job {job.id}: {len(aps)} APs from zone {request.zone_id}")
    return V2PlanResponse(
        job_id=job.id, status="VALIDATING",
        message=f"Checking {len(aps)} APs. Poll GET /ap-port-grabber/v2/{job.id}/plan for results.",
    )


@router.get("/v2/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(job_id: str, current_user: User = Depends(get_current_user)):
    """The plan; poll after POST /v2/plan until status is AWAITING_CONFIRMATION."""
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    result = V2PlanResult(job_id=job_id, status=job.status.value, unit_count=len(job.units))
    if job.status == JobStatus.VALIDATING:
        return result
    if job.status == JobStatus.FAILED:
        result.conflicts = [{"description": e} for e in job.errors]
        return result
    if job.validation_result:
        vr = job.validation_result
        result.valid = vr.valid
        result.summary = vr.summary.model_dump() if vr.summary else {}
        result.conflicts = [c.model_dump() for c in vr.conflicts]
        result.estimated_api_calls = vr.summary.total_api_calls if vr.summary else 0
        result.actions = [
            {**a.model_dump(), "details": "; ".join(a.notes) if a.notes else None}
            for a in vr.actions
        ]
    return result


@router.post("/v2/{job_id}/confirm")
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """Confirm and run. Job must be AWAITING_CONFIRMATION with a valid plan."""
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != JobStatus.AWAITING_CONFIRMATION:
        raise HTTPException(status_code=400,
                            detail=f"Job is in '{job.status.value}' state. Expected AWAITING_CONFIRMATION.")
    if job.validation_result and not job.validation_result.valid:
        raise HTTPException(status_code=400, detail="The plan has blocking issues - fix the choices and re-plan")
    background_tasks.add_task(_run_execution, job.id, job.controller_id)
    return {"job_id": job.id, "status": "RUNNING",
            "message": f"Setting LAN ports on {len(job.units)} APs. Poll /jobs/{job.id}/status for progress."}


@router.get("/v2/{job_id}/graph")
async def get_workflow_graph(job_id: str, current_user: User = Depends(get_current_user)):
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    graph = DependencyGraph(job.phase_definitions)
    data = graph.to_graph_data()
    return {"workflow_name": job.workflow_name, "nodes": data["nodes"], "edges": data["edges"],
            "levels": {str(k): v for k, v in graph.compute_levels().items()}}
