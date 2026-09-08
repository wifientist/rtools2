"""
V2 AP Regroup Endpoints

Move APs into AP Groups from a flat CSV, with the standard plan/confirm flow:
- POST /ap-regroup/v2/plan          -> resolve the CSV, return a plan
- GET  /ap-regroup/v2/{id}/plan     -> plan results
- POST /ap-regroup/v2/{id}/confirm  -> confirm and execute
- GET  /ap-regroup/v2/{id}/graph    -> workflow graph

Uses APRegroupWorkflow (3 phases):
1. validate_ap_regroup (global)  -> match APs, resolve AP Groups, build units
2. create_ap_group   (per group) -> create any AP Group that is missing
3. assign_aps        (per group) -> move the APs in
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.v2.models import JobStatus, WorkflowJobV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.graph import DependencyGraph
from workflow.workflows.ap_regroup import APRegroupWorkflow
from workflow.events import WorkflowEventPublisher

from routers.per_unit_ssid.per_unit_ssid_router import validate_controller_access

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ap-regroup/v2",
    tags=["AP Regroup V2"],
)


# ==================== Request / Response Models ====================

class APRegroupV2Request(BaseModel):
    """Request to plan an AP regroup."""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    # [{"ap_identifier": "R350-ABC123", "ap_group_name": "Building-1"}, ...]
    # ap_identifier matches an AP by serial number OR name.
    ap_assignments: List[Dict[str, str]] = Field(default_factory=list)


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
    summary: Dict[str, Any] = {}
    conflicts: list = []
    estimated_api_calls: int = 0
    actions: list = []
    # Rows whose AP is not in this venue — surfaced so a bad CSV is obvious
    # before anything is created.
    unmatched_identifiers: List[str] = []
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

async def run_v2_validation_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run V2 validation (Phase 0)."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[AP Regroup] Starting validation for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[AP Regroup] Job {job_id} not found in Redis")
            return

        r1_client = create_r1_client_from_controller(controller_id, db)
        event_publisher = WorkflowEventPublisher(redis_client)
        activity_tracker = ActivityTracker(
            r1_client, state_manager, tenant_id=job.tenant_id
        )
        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=activity_tracker,
            event_publisher=event_publisher,
            r1_client=r1_client,
        )

        await brain.run_validation(job)

        logger.info(
            f"[AP Regroup] Validation complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(
            f"[AP Regroup] Validation failed for job {job_id}: {e}"
        )

    finally:
        db.close()


async def run_v2_execution_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run V2 workflow execution."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[AP Regroup] Starting execution for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[AP Regroup] Job {job_id} not found in Redis")
            return

        r1_client = create_r1_client_from_controller(controller_id, db)
        event_publisher = WorkflowEventPublisher(redis_client)
        activity_tracker = ActivityTracker(
            r1_client, state_manager, tenant_id=job.tenant_id
        )

        await activity_tracker.start()

        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=activity_tracker,
            event_publisher=event_publisher,
            r1_client=r1_client,
        )

        await brain.execute_workflow(job)

        await activity_tracker.stop()

        logger.info(
            f"[AP Regroup] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(
            f"[AP Regroup] Execution failed for job {job_id}: {e}"
        )

    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/plan", response_model=V2PlanResponse)
async def create_plan(
    request: APRegroupV2Request,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create an AP Regroup plan (Phase 0 validation).

    Resolves each CSV row against the venue's APs and AP Groups and returns
    a plan for confirmation before anything is created or moved.

    Validates the configuration, checks AP port capabilities,
    and returns a plan for user confirmation before applying changes.
    """
    logger.info(
        f"[AP Regroup] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}, rows: {len(request.ap_assignments)}"
    )

    controller = validate_controller_access(
        request.controller_id, current_user, db
    )

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}",
        )

    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers",
        )

    if not request.ap_assignments:
        raise HTTPException(
            status_code=400,
            detail="ap_assignments is empty — nothing to regroup",
        )

    options: Dict[str, Any] = {}
    input_data = {
        'ap_assignments': request.ap_assignments,
    }

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    activity_tracker = ActivityTracker(
        None, state_manager, tenant_id=tenant_id
    )

    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=activity_tracker,
    )

    job = await brain.create_job(
        workflow=APRegroupWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options=options,
        input_data=input_data,
    )

    background_tasks.add_task(
        run_v2_validation_background,
        job.id,
        request.controller_id,
    )

    return V2PlanResponse(
        job_id=job.id,
        status="VALIDATING",
        message=(
            f"Resolving {len(request.ap_assignments)} AP assignments. "
            f"Poll GET /ap-regroup/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the validation plan for an AP Regroup job.

    Poll this after POST /plan until status is AWAITING_CONFIRMATION.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Job {job_id} not found"
        )

    result = V2PlanResult(
        job_id=job_id,
        status=job.status.value,
        unit_count=len(job.units),
    )

    if job.status == JobStatus.VALIDATING:
        return result

    if job.status == JobStatus.FAILED:
        result.valid = False
        result.conflicts = [{"description": e} for e in job.errors]
        return result

    if job.validation_result:
        vr = job.validation_result
        result.valid = vr.valid
        result.summary = vr.summary.model_dump() if vr.summary else {}
        result.conflicts = [c.model_dump() for c in vr.conflicts]
        result.estimated_api_calls = (
            vr.summary.total_api_calls if vr.summary else 0
        )
        result.actions = [
            {
                **a.model_dump(),
                "details": "; ".join(a.notes) if a.notes else None,
            }
            for a in vr.actions
        ]

        # Extras from this workflow's own validation phase.
        phase_outputs = job.global_phase_results.get(
            "validate_ap_regroup", {}
        ) or {}
        result.unmatched_identifiers = phase_outputs.get(
            "unmatched_identifiers", []) or []
        result.ap_groups_to_create = phase_outputs.get(
            "ap_groups_to_create", []) or []
        result.ap_groups_to_reuse = phase_outputs.get(
            "ap_groups_to_reuse", []) or []
        # APs actually being moved = the sum of each group's member list.
        result.total_aps = sum(
            len(u.plan.ap_serial_numbers or []) for u in job.units.values()
        )

    return result


@router.post("/{job_id}/confirm", response_model=V2ConfirmResponse)
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm the plan and start moving APs into their AP Groups.

    Job must be in AWAITING_CONFIRMATION status.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Job {job_id} not found"
        )

    if job.status != JobStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Job is in '{job.status.value}' state. "
                f"Expected AWAITING_CONFIRMATION."
            ),
        )

    background_tasks.add_task(
        run_v2_execution_background,
        job.id,
        job.controller_id,
    )

    return V2ConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=(
            f"Port configuration started for {len(job.units)} units. "
            f"Poll /jobs/{job.id}/status for progress."
        ),
    )


@router.get("/{job_id}/graph", response_model=V2GraphResponse)
async def get_workflow_graph(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get the workflow graph for an AP Regroup job."""
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=404, detail=f"Job {job_id} not found"
        )

    graph = DependencyGraph(job.phase_definitions)
    graph_data = graph.to_graph_data()

    return V2GraphResponse(
        workflow_name=job.workflow_name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )


# ==================== Venue Inventory (for pre-populating) ====================

@router.get("/{controller_id}/venue/{venue_id}/inventory")
async def get_venue_inventory(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    APs and existing AP Groups for a venue.

    Read-only. Lets the page seed the CSV from what is actually there --
    which AP names exist, and which AP Groups are already defined -- instead
    of making the operator type them.
    """
    controller = validate_controller_access(controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}",
        )

    effective_tenant_id = tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(
            status_code=400, detail="tenant_id is required for MSP controllers"
        )

    r1_client = create_r1_client_from_controller(controller_id, db)

    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(
            effective_tenant_id, venue_id
        )
        raw_aps = aps_response.get("data", []) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {e}")

    groups = []
    try:
        groups_response = await r1_client.venues.query_ap_groups(
            tenant_id=effective_tenant_id,
            venue_id=venue_id,
            fields=["id", "name", "venueId"],
        )
        groups = [
            {"id": g.get("id"), "name": g.get("name")}
            for g in (groups_response.get("data", []) or [])
            if g.get("name")
        ]
    except Exception as e:
        logger.warning(f"[AP Regroup] Could not fetch AP groups: {e}")

    aps = sorted(
        (
            {
                "serial": ap.get("serialNumber") or ap.get("serial"),
                "name": ap.get("name"),
                "model": ap.get("model"),
                "status": ap.get("status"),
                "ap_group_id": ap.get("apGroupId") or ap.get("groupId"),
            }
            for ap in raw_aps
        ),
        key=lambda a: (a.get("name") or ""),
    )

    return {
        "venue_id": venue_id,
        "total_aps": len(aps),
        "aps": aps,
        "ap_groups": sorted(groups, key=lambda g: g["name"]),
    }
