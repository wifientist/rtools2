"""
V2 Per-Unit SSID Endpoints

New endpoints using the V2 workflow engine with:
- Phase 0 validation / dry-run with user confirmation
- Per-unit parallel execution via the Brain
- Typed phase contracts
- Workflow graph visualization

Flow:
1. POST /per-unit-ssid/v2/plan    → Create job, run validation, return plan
2. GET  /per-unit-ssid/v2/{id}/plan → Get validation result
3. POST /per-unit-ssid/v2/{id}/confirm → Confirm & start execution
4. GET  /per-unit-ssid/v2/{id}/graph  → Get workflow graph for visualization
"""

import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from pydantic import BaseModel

from dependencies import get_db, get_current_user
from models.user import User
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.v2.models import JobStatus, WorkflowJobV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.graph import DependencyGraph
from workflow.workflows import get_workflow
from workflow.workflows.per_unit_psk import PerUnitPSKWorkflow
from workflow.workflows.per_unit_dpsk import PerUnitDPSKWorkflow
from workflow.events import WorkflowEventPublisher

# Reuse existing request model
from routers.per_unit_ssid.per_unit_ssid_router import (
    PerUnitSSIDRequest,
    validate_controller_access,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/per-unit-ssid/v2",
    tags=["Per-Unit SSID V2"],
)


# ==================== Response Models ====================

class V2PlanResponse(BaseModel):
    """Response from plan endpoint"""
    job_id: str
    status: str
    message: str


class V2PlanResult(BaseModel):
    """Validation plan result"""
    job_id: str
    status: str
    valid: bool = False
    message: str = ""
    summary: Dict[str, Any] = {}
    conflicts: list = []
    unit_count: int = 0
    estimated_api_calls: int = 0
    actions: list = []


class V2ConfirmResponse(BaseModel):
    """Response from confirm endpoint"""
    job_id: str
    status: str
    message: str


class V2GraphResponse(BaseModel):
    """Workflow graph for visualization"""
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
        logger.info(f"[V2] Starting validation for job {job_id}")

        # Restore job from Redis
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[V2] Job {job_id} not found in Redis")
            return

        # Create R1 client
        r1_client = create_r1_client_from_controller(controller_id, db)

        # Create Brain
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

        # Run validation
        await brain.run_validation(job)

        logger.info(
            f"[V2] Validation complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[V2] Validation failed for job {job_id}: {e}")

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
        logger.info(f"[V2] Starting execution for job {job_id}")

        # Restore job from Redis
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[V2] Job {job_id} not found in Redis")
            return

        # Create R1 client
        r1_client = create_r1_client_from_controller(controller_id, db)

        # Create Brain
        event_publisher = WorkflowEventPublisher(redis_client)
        activity_tracker = ActivityTracker(
            r1_client, state_manager, tenant_id=job.tenant_id
        )

        # Start activity tracker
        await activity_tracker.start()

        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=activity_tracker,
            event_publisher=event_publisher,
            r1_client=r1_client,
        )

        # Execute workflow
        await brain.execute_workflow(job)

        # Stop activity tracker
        await activity_tracker.stop()

        logger.info(
            f"[V2] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[V2] Execution failed for job {job_id}: {e}")

    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/plan", response_model=V2PlanResponse)
async def create_plan(
    request: PerUnitSSIDRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a V2 workflow plan (Phase 0 validation).

    Validates the workflow, pre-checks existing R1 resources, builds
    unit mappings, and returns a plan for user confirmation.

    After this returns, poll GET /v2/{job_id}/plan for the validation result.
    Once validated, call POST /v2/{job_id}/confirm to start execution.
    """
    logger.info(
        f"[V2] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}, units: {len(request.units)}"
    )

    # Validate controller access
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

    # Detect DPSK mode
    dpsk_mode = request.dpsk_mode or any(
        u.security_type.upper() == "DPSK" for u in request.units
    )
    selected_workflow = PerUnitDPSKWorkflow if dpsk_mode else PerUnitPSKWorkflow

    if dpsk_mode:
        logger.info(
            "[V2] DPSK mode - using PerUnitDPSKWorkflow"
        )

    # Build options and input data
    units_data = [unit.model_dump() for unit in request.units]
    model_port_configs_data = request.model_port_configs.model_dump()

    options = {
        'ap_group_prefix': request.ap_group_prefix,
        'ap_group_postfix': request.ap_group_postfix,
        'name_conflict_resolution': request.name_conflict_resolution,
        'configure_lan_ports': request.configure_lan_ports,
        'model_port_configs': model_port_configs_data,
        'debug_delay': request.debug_delay,
        'dpsk_mode': dpsk_mode,
        # Single shared pool for all units (no more per-unit pools)
        'identity_group_name': request.identity_group_name,
        'dpsk_pool_name': request.dpsk_pool_name,
        'dpsk_pool_settings': (
            request.dpsk_pool_settings.model_dump()
            if dpsk_mode else {}
        ),
    }

    input_data = {
        'units': units_data,
        'ap_group_prefix': request.ap_group_prefix,
        'ap_group_postfix': request.ap_group_postfix,
        'name_conflict_resolution': request.name_conflict_resolution,
        'configure_lan_ports': request.configure_lan_ports,
        # Single shared pool for all units
        'identity_group_name': request.identity_group_name,
        'dpsk_pool_name': request.dpsk_pool_name,
        'dpsk_pool_settings': (
            request.dpsk_pool_settings.model_dump()
            if dpsk_mode else {}
        ),
    }

    # Create V2 job via Brain
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    activity_tracker = ActivityTracker(None, state_manager, tenant_id=tenant_id)

    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=activity_tracker,
    )

    job = await brain.create_job(
        workflow=selected_workflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options=options,
        input_data=input_data,
    )

    # Start validation in background
    background_tasks.add_task(
        run_v2_validation_background,
        job.id,
        request.controller_id,
    )

    return V2PlanResponse(
        job_id=job.id,
        status="VALIDATING",
        message=(
            f"Validating plan for {len(request.units)} units. "
            f"Poll GET /per-unit-ssid/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the validation plan for a V2 job.

    Returns the dry-run results: what will be created, what already exists,
    any conflicts, and the estimated API call count.

    Call this after POST /v2/plan to check if validation is complete.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = V2PlanResult(
        job_id=job_id,
        status=job.status.value,
        unit_count=len(job.units),
    )

    if job.status == JobStatus.VALIDATING:
        result.message = "Validation in progress..."
        return result

    if job.status == JobStatus.FAILED:
        result.valid = False
        result.conflicts = [
            {"description": e} for e in job.errors
        ]
        return result

    if job.validation_result:
        vr = job.validation_result
        result.valid = vr.valid
        result.summary = vr.summary.model_dump() if vr.summary else {}
        result.conflicts = [c.model_dump() for c in vr.conflicts]
        result.estimated_api_calls = vr.summary.total_api_calls if vr.summary else 0
        result.actions = [a.model_dump() for a in vr.actions]

    return result


@router.post("/{job_id}/confirm", response_model=V2ConfirmResponse)
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm the plan and start V2 workflow execution.

    Must be called after validation completes successfully.
    The job must be in AWAITING_CONFIRMATION status.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status != JobStatus.AWAITING_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Job is in '{job.status.value}' state. "
                f"Expected AWAITING_CONFIRMATION."
            ),
        )

    # Check validation passed
    if job.validation_result and not job.validation_result.valid:
        raise HTTPException(
            status_code=400,
            detail="Cannot confirm: validation found blocking conflicts",
        )

    # Start execution in background
    background_tasks.add_task(
        run_v2_execution_background,
        job.id,
        job.controller_id,
    )

    return V2ConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=(
            f"Execution started for {len(job.units)} units. "
            f"Poll /jobs/{job.id}/status for progress."
        ),
    )


@router.get("/{job_id}/graph", response_model=V2GraphResponse)
async def get_workflow_graph(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the workflow dependency graph for visualization.

    Returns nodes and edges suitable for rendering as a DAG
    (e.g., with React Flow or a similar library).
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Build graph from phase definitions (exclude validate for display)
    execution_phases = [
        p for p in job.phase_definitions if p.id != "validate"
    ]
    graph = DependencyGraph(execution_phases)
    graph_data = graph.to_graph_data()

    return V2GraphResponse(
        workflow_name=job.workflow_name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )


@router.get("/graph/{workflow_name}", response_model=V2GraphResponse)
async def get_static_workflow_graph(
    workflow_name: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the static workflow graph for any registered workflow.

    Returns the DAG without needing a job - useful for displaying
    the workflow structure before running anything.

    Supported: per_unit_psk, per_unit_dpsk, venue_cleanup,
    ap_lan_port_config
    """
    try:
        wf = get_workflow(workflow_name)
    except ValueError:
        from workflow.workflows import list_workflows
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workflow '{workflow_name}' not found. "
                f"Available: {list_workflows()}"
            ),
        )

    definitions = wf.get_phase_definitions()

    # Exclude validate phases for the execution graph
    execution_defs = [
        d for d in definitions
        if not d.id.startswith("validate")
    ]
    graph = DependencyGraph(execution_defs)
    graph_data = graph.to_graph_data()

    return V2GraphResponse(
        workflow_name=wf.name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )
