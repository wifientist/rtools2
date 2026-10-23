"""
V2 WLAN Activator Endpoints

Put APs into AP Groups and activate each group's WLAN directly on it, with
the standard plan/confirm flow:
- GET  /wlan-activator/v2/{controller_id}/venue/{venue_id}/suggest
                                      -> best-guess rows from what R1 holds
- POST /wlan-activator/v2/plan        -> resolve the rows, return a plan
- GET  /wlan-activator/v2/{id}/plan   -> plan results
- POST /wlan-activator/v2/{id}/confirm -> confirm and execute
- GET  /wlan-activator/v2/{id}/graph  -> workflow graph

Uses WlanActivatorWorkflow:
1. validate_wlan_activator (global) -> resolve APs, groups, WLANs
2. create_ap_group (per group)      -> create any AP Group that is missing
3. activate_wlans  (per group)      -> direct AP Group activation
4. assign_aps      (per group)      -> move the APs in
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.user import User
from redis_client import get_redis_client
from routers.per_unit_ssid.per_unit_ssid_router import validate_controller_access
from routers.tenant_scope import resolve_tenant_id
from services.dpsk_pool_context import list_dpsk_pools, venue_pool_suggestion
from services.wlan_activator import (
    DEFAULT_GROUP_PATTERN, DEFAULT_WLAN_PATTERN, build_suggestions, detect_property_suffix,
)
from workflow.events import WorkflowEventPublisher
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.failsafe import fail_job
from workflow.v2.graph import DependencyGraph
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.workflows.wlan_activator import WlanActivatorWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/wlan-activator/v2",
    tags=["WLAN Activator V2"],
)

VALIDATE_PHASE = "validate_wlan_activator"


# ==================== Request / Response Models ====================

class WlanActivatorRequest(BaseModel):
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    # [{"ap_identifier": "101-R550", "ap_group_name": "101", "wlan": "101@Fieldhouse"}]
    # ap_identifier matches by serial OR name and may be blank (activate only);
    # wlan matches a network by name, then SSID.
    activations: List[Dict[str, str]] = Field(default_factory=list)
    # A pool for DPSK networks that have none linked (R1 will not activate them).
    # None: such networks block the plan, as before.
    dpsk_link_pool_id: Optional[str] = None


class V2PlanResponse(BaseModel):
    job_id: str
    status: str
    message: str


class V2PlanResult(BaseModel):
    job_id: str
    status: str
    valid: bool = False
    unit_count: int = 0
    total_aps: int = 0
    total_activations: int = 0
    summary: Dict[str, Any] = {}
    conflicts: list = []
    estimated_api_calls: int = 0
    actions: list = []
    unmatched_identifiers: List[str] = []
    unresolved_wlans: List[Dict[str, str]] = []
    ap_groups_to_create: List[str] = []
    ap_groups_to_reuse: List[str] = []


class V2ConfirmResponse(BaseModel):
    job_id: str
    status: str
    message: str


class V2GraphResponse(BaseModel):
    workflow_name: str
    nodes: list = []
    edges: list = []
    levels: Dict[str, list] = {}


# ==================== Background Tasks ====================

def _brain(r1_client, state_manager, redis_client, tenant_id) -> WorkflowBrain:
    return WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(r1_client, state_manager, tenant_id=tenant_id),
        event_publisher=WorkflowEventPublisher(redis_client),
        r1_client=r1_client,
    )


async def run_v2_validation_background(job_id: str, controller_id: int):
    """Phase 0 validation."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[WLAN Activator] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        await _brain(r1_client, state_manager, redis_client, job.tenant_id).run_validation(job)
        logger.info(f"[WLAN Activator] Validation complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[WLAN Activator] Validation failed for job {job_id}: {e}")
    finally:
        db.close()


async def run_v2_execution_background(job_id: str, controller_id: int):
    """Workflow execution."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[WLAN Activator] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        brain = _brain(r1_client, state_manager, redis_client, job.tenant_id)
        await brain.tracker.start()
        await brain.execute_workflow(job)
        await brain.tracker.stop()
        logger.info(f"[WLAN Activator] Execution complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[WLAN Activator] Execution failed for job {job_id}: {e}")
        # Same as AP Regroup: record the death if the engine could not.
        await fail_job(None, job_id, e, source="WLAN Activator")
    finally:
        db.close()


# ==================== Plan / Confirm ====================

@router.post("/plan", response_model=V2PlanResponse)
async def create_plan(
    request: WlanActivatorRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resolve the rows against the venue and return a plan to confirm."""
    controller = validate_controller_access(request.controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller must be RuckusONE, got {controller.controller_type}")
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    if not request.activations:
        raise HTTPException(status_code=400, detail="activations is empty - nothing to do")

    logger.info(
        f"[WLAN Activator] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}, rows: {len(request.activations)}"
    )

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=tenant_id),
    )
    job = await brain.create_job(
        workflow=WlanActivatorWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options={'dpsk_link_pool_id': request.dpsk_link_pool_id} if request.dpsk_link_pool_id else {},
        input_data={'activations': request.activations},
    )
    background_tasks.add_task(run_v2_validation_background, job.id, request.controller_id)

    return V2PlanResponse(
        job_id=job.id,
        status="VALIDATING",
        message=(
            f"Resolving {len(request.activations)} rows. "
            f"Poll GET /wlan-activator/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(job_id: str, current_user: User = Depends(get_current_user)):
    """The plan; poll after POST /plan until status is AWAITING_CONFIRMATION."""
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
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
        outputs = job.global_phase_results.get(VALIDATE_PHASE, {}) or {}
        result.unmatched_identifiers = outputs.get("unmatched_identifiers", []) or []
        result.unresolved_wlans = outputs.get("unresolved_wlans", []) or []
        result.ap_groups_to_create = outputs.get("ap_groups_to_create", []) or []
        result.ap_groups_to_reuse = outputs.get("ap_groups_to_reuse", []) or []
        result.total_aps = sum(len(u.plan.ap_serial_numbers or []) for u in job.units.values())
        result.total_activations = sum(
            len(u.input_config.get("wlans") or []) for u in job.units.values()
        )

    return result


@router.post("/{job_id}/confirm", response_model=V2ConfirmResponse)
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """Confirm the plan and start. Job must be AWAITING_CONFIRMATION with a valid plan."""
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != JobStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f"Job is in '{job.status.value}' state. Expected AWAITING_CONFIRMATION.",
        )
    if job.validation_result and not job.validation_result.valid:
        # The modal already blocks this; the API must too.
        raise HTTPException(status_code=400, detail="The plan has blocking issues - fix the list and re-plan")

    background_tasks.add_task(run_v2_execution_background, job.id, job.controller_id)
    return V2ConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=f"WLAN activation started for {len(job.units)} AP Groups. Poll /jobs/{job.id}/status for progress.",
    )


@router.get("/{job_id}/graph", response_model=V2GraphResponse)
async def get_workflow_graph(job_id: str, current_user: User = Depends(get_current_user)):
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    graph = DependencyGraph(job.phase_definitions)
    graph_data = graph.to_graph_data()
    return V2GraphResponse(
        workflow_name=job.workflow_name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={str(k): v for k, v in graph.compute_levels().items()},
    )


# ==================== DPSK pools (read-only) ====================

@router.get("/{controller_id}/venue/{venue_id}/dpsk-pools")
async def dpsk_pools(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    The tenant's DPSK pools and the one the venue's DPSK networks use, for
    linking DPSK networks that have none. Read-only.
    """
    controller = validate_controller_access(controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller must be RuckusONE, got {controller.controller_type}")
    effective_tenant_id = resolve_tenant_id(controller, tenant_id)
    r1_client = create_r1_client_from_controller(controller_id, db)
    try:
        venue = await r1_client.venues.get_venue(effective_tenant_id, venue_id)
        net_resp = await r1_client.networks.get_wifi_networks(effective_tenant_id, venue_id=venue_id)
        networks = net_resp.get("data", []) if isinstance(net_resp, dict) else net_resp
        pools = await list_dpsk_pools(r1_client, effective_tenant_id)
        suggestion = await venue_pool_suggestion(
            r1_client, effective_tenant_id, venue_id, (venue or {}).get("name"), networks)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read DPSK pools from R1: {e}")
    return {"pools": pools, "suggestion": suggestion}


# ==================== Suggest (read-only) ====================

@router.get("/{controller_id}/venue/{venue_id}/suggest")
async def suggest_mapping(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    wlan_pattern: str = Query(DEFAULT_WLAN_PATTERN, description="WLAN name/SSID pattern, e.g. {unit}@{property}"),
    group_pattern: str = Query(DEFAULT_GROUP_PATTERN, description="AP Group name pattern, e.g. {unit}"),
    property_name: Optional[str] = Query(
        None, description="Fills {property}; defaults to the @suffix the venue's WLANs share, else the venue name"),
    extra_wlans: Optional[List[str]] = Query(None, description="Property-wide WLANs every group carries"),
    detect_extras: bool = Query(True, description="With no extra_wlans: detect them (true) or use none"),
    companion_patterns: Optional[List[str]] = Query(
        None, description="More per-unit WLAN patterns, e.g. iot@{unit}; off unless given"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Best-guess AP -> AP Group -> WLAN rows from what R1 already holds.

    Read-only. Units come from tenant networks whose name or SSID fits
    wlan_pattern; each AP is matched to the unit its name contains. Each row
    says whether it is already done in R1, so the page can show only the
    work that is left.
    """
    controller = validate_controller_access(controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller must be RuckusONE, got {controller.controller_type}")
    effective_tenant_id = resolve_tenant_id(controller, tenant_id)
    r1_client = create_r1_client_from_controller(controller_id, db)

    venue_name = ""
    try:
        venue = await r1_client.venues.get_venue(effective_tenant_id, venue_id)
        venue_name = (venue or {}).get("name") or ""
    except Exception as e:
        logger.warning(f"[WLAN Activator] Could not read venue {venue_id}: {e}")

    try:
        aps = (await r1_client.venues.get_aps_by_tenant_venue(effective_tenant_id, venue_id)).get("data", []) or []
        groups = await r1_client.venues.list_ap_groups_in_venue(effective_tenant_id, venue_id)
        net_resp = await r1_client.networks.get_wifi_networks(effective_tenant_id)
        networks = net_resp.get("data", []) if isinstance(net_resp, dict) else net_resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read the venue from R1: {e}")

    prop = property_name if property_name is not None else (
        detect_property_suffix(networks, venue_id) or venue_name)
    result = build_suggestions(
        aps, groups, networks, venue_id=venue_id,
        wlan_pattern=wlan_pattern, group_pattern=group_pattern, property_name=prop,
        extra_wlans=extra_wlans if extra_wlans else (None if detect_extras else []),
        companion_patterns=[p for p in (companion_patterns or []) if p.strip()],
    )
    return {
        "venue_id": venue_id,
        "venue_name": venue_name,
        "property_name": prop,
        "wlan_pattern": wlan_pattern,
        "group_pattern": group_pattern,
        "total_aps": len(aps),
        "total_networks": len(networks),
        "ap_groups": sorted(g.get("name") for g in groups if g.get("name")),
        **result,
    }
