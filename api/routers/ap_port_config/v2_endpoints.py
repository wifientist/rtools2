"""
V2 AP Port Config Endpoints

Endpoints using the V2 workflow engine with plan/confirm flow:
- POST /ap-port-config/v2/plan    → Validate port config, return plan
- GET  /ap-port-config/v2/{id}/plan → Get validation results
- POST /ap-port-config/v2/{id}/confirm → Confirm & start execution
- GET  /ap-port-config/v2/{id}/graph  → Workflow graph for visualization

Uses APLanPortConfigWorkflow (2 phases):
1. validate_lan_ports (global) → Build unit mappings, fetch venue APs
2. configure_lan_ports (per-unit) → Configure ports per AP/unit
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
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
from workflow.workflows.ap_lan_ports import APLanPortConfigWorkflow
from workflow.events import WorkflowEventPublisher

from routers.per_unit_ssid.per_unit_ssid_router import validate_controller_access

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ap-port-config/v2",
    tags=["AP Port Config V2"],
)


# ==================== Request / Response Models ====================

class APPortConfigV2Request(BaseModel):
    """Request to plan AP port configuration via V2 workflow."""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    units: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of units. Each unit: "
            "{unit_number, ap_identifiers: [serial/name], default_vlan}"
        ),
    )
    model_port_configs: Optional[Dict[str, List]] = Field(
        default=None,
        description="Port config matrix per model type",
    )


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
        logger.info(f"[AP Port V2] Starting validation for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[AP Port V2] Job {job_id} not found in Redis")
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
            f"[AP Port V2] Validation complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(
            f"[AP Port V2] Validation failed for job {job_id}: {e}"
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
        logger.info(f"[AP Port V2] Starting execution for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[AP Port V2] Job {job_id} not found in Redis")
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
            f"[AP Port V2] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(
            f"[AP Port V2] Execution failed for job {job_id}: {e}"
        )

    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/plan", response_model=V2PlanResponse)
async def create_plan(
    request: APPortConfigV2Request,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a V2 AP port config plan (Phase 0 validation).

    Validates the configuration, checks AP port capabilities,
    and returns a plan for user confirmation before applying changes.
    """
    logger.info(
        f"[AP Port V2] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}, units: {len(request.units)}"
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

    options = {
        'configure_lan_ports': True,
        'model_port_configs': request.model_port_configs,
    }

    input_data = {
        'units': request.units,
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
        workflow=APLanPortConfigWorkflow,
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
            f"Validating port config for {len(request.units)} units. "
            f"Poll GET /ap-port-config/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the validation plan for an AP port config job.

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
        result.total_aps = (
            vr.summary.total_api_calls if vr.summary else 0
        )

    return result


@router.post("/{job_id}/confirm", response_model=V2ConfirmResponse)
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm the plan and start AP port configuration.

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
    """Get the workflow graph for an AP port config job."""
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
