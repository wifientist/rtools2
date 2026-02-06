"""
V2 Cloudpath Import Endpoints

New endpoints using the V2 workflow engine with:
- Phase 0 validation / dry-run with user confirmation
- Auto-detection of property-wide vs per-unit mode
- Per-unit parallel execution via the Brain
- Intra-phase parallelism for bulk passphrase creation

Flow:
1. POST /cloudpath-import/v2/plan    → Upload JSON, run validation, return plan
2. GET  /cloudpath-import/v2/{id}/plan → Get validation result
3. POST /cloudpath-import/v2/{id}/confirm → Confirm & start execution
4. GET  /cloudpath-import/v2/{id}/graph  → Get workflow graph for visualization
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
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
from workflow.workflows import get_workflow
from workflow.workflows.cloudpath_import import CloudpathImportWorkflow
from workflow.events import WorkflowEventPublisher

# Reuse validation from existing router
from routers.cloudpath.cloudpath_router import validate_controller_access

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cloudpath-import/v2",
    tags=["Cloudpath Import V2"],
)


# ==================== Request/Response Models ====================

class V2ImportRequest(BaseModel):
    """Request to start V2 Cloudpath DPSK import"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")
    cloudpath_data: Dict[str, Any] = Field(
        ...,
        description="Cloudpath JSON export (with 'pool' and 'dpsks' keys)"
    )
    options: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_concurrent_passphrases": 10,
            "skip_expired_dpsks": False,
            "renew_expired_dpsks": False,
            "renewal_days": 365,
        },
        description="Import options"
    )


class V2PlanResponse(BaseModel):
    """Response from plan endpoint"""
    job_id: str
    status: str
    message: str


class ScenarioDetectionResponse(BaseModel):
    """Scenario detection result for frontend display"""
    detected_scenario: str = ""  # "A", "B1", or "B2"
    unit_count: int = 0
    unit_coverage: float = 0.0  # % of passphrases with unit-specific SSIDs
    unique_ssids: list = []  # Sample of detected SSIDs
    recommendation: str = ""
    can_use_b1: bool = True  # True if unit_count < 64


class V2PlanResult(BaseModel):
    """Validation plan result"""
    job_id: str
    status: str
    valid: bool = False
    import_mode: str = ""  # Selected scenario: "A", "B1", or "B2"
    scenario_detection: Optional[ScenarioDetectionResponse] = None
    message: str = ""
    summary: Dict[str, Any] = {}
    unit_count: int = 0
    passphrase_count: int = 0
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
        logger.info(f"[Cloudpath V2] Starting validation for job {job_id}")

        # Restore job from Redis
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cloudpath V2] Job {job_id} not found in Redis")
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
            f"[Cloudpath V2] Validation complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[Cloudpath V2] Validation failed for job {job_id}: {e}")
        # Update job status to failed
        try:
            redis_client = await get_redis_client()
            state_manager = RedisStateManagerV2(redis_client)
            job = await state_manager.get_job(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.errors.append(str(e))
                await state_manager.save_job(job)
        except Exception:
            pass

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
        logger.info(f"[Cloudpath V2] Starting execution for job {job_id}")

        # Restore job from Redis
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cloudpath V2] Job {job_id} not found in Redis")
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
            f"[Cloudpath V2] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[Cloudpath V2] Execution failed for job {job_id}: {e}")

    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/plan", response_model=V2PlanResponse)
async def create_plan(
    request: V2ImportRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a V2 cloudpath import plan (Phase 0 validation).

    Validates the Cloudpath JSON export, auto-detects import mode
    (property-wide vs per-unit), and builds a plan for user confirmation.

    After this returns, poll GET /v2/{job_id}/plan for the validation result.
    Once validated, call POST /v2/{job_id}/confirm to start execution.
    """
    logger.info(
        f"[Cloudpath V2] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}"
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

    # Validate cloudpath data structure
    if 'pool' not in request.cloudpath_data:
        raise HTTPException(
            status_code=400,
            detail="Cloudpath export must contain 'pool' key",
        )
    if 'dpsks' not in request.cloudpath_data:
        raise HTTPException(
            status_code=400,
            detail="Cloudpath export must contain 'dpsks' key",
        )

    dpsk_count = len(request.cloudpath_data.get('dpsks', []))
    logger.info(f"[Cloudpath V2] Cloudpath export has {dpsk_count} DPSKs")

    # Debug: log first DPSK's keys to verify vlanid is present
    if dpsk_count > 0:
        first_dpsk = request.cloudpath_data['dpsks'][0]
        logger.debug(f"[Cloudpath V2] First DPSK keys: {list(first_dpsk.keys())}")
        logger.debug(f"[Cloudpath V2] First DPSK vlanid: {first_dpsk.get('vlanid')}")

    # Build options and input data
    options = {
        **CloudpathImportWorkflow.default_options,
        **request.options,
    }

    input_data = {
        'cloudpath_data': request.cloudpath_data,
        'options': options,
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
        workflow=CloudpathImportWorkflow,
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
            f"Validating Cloudpath import ({dpsk_count} DPSKs). "
            f"Poll GET /cloudpath-import/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the validation plan for a V2 cloudpath import job.

    Returns the dry-run results including detected import mode,
    passphrase count, and estimated API calls.

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
        result.message = "; ".join(job.errors) if job.errors else "Validation failed"
        return result

    if job.validation_result:
        vr = job.validation_result
        result.valid = vr.valid
        result.summary = vr.summary.model_dump() if vr.summary else {}
        result.estimated_api_calls = vr.summary.total_api_calls if vr.summary else 0
        result.actions = [a.model_dump() for a in vr.actions]
        result.unit_count = vr.summary.total_units if vr.summary else len(job.units)

    # Extract import mode and passphrase count from global results
    global_results = job.global_phase_results or {}
    result.import_mode = global_results.get('import_mode', 'unknown')

    # Extract scenario detection from global results
    scenario_data = global_results.get('scenario_detection')
    if scenario_data:
        result.scenario_detection = ScenarioDetectionResponse(
            detected_scenario=scenario_data.get('detected_scenario', ''),
            unit_count=scenario_data.get('unit_count', 0),
            unit_coverage=scenario_data.get('unit_coverage', 0.0),
            unique_ssids=scenario_data.get('unique_ssids', []),
            recommendation=scenario_data.get('recommendation', ''),
            can_use_b1=scenario_data.get('can_use_b1', True),
        )

    # Get passphrase count from input data or unit mappings
    input_passphrases = job.input_data.get('cloudpath_data', {}).get('dpsks', [])
    result.passphrase_count = len(input_passphrases)

    # Build descriptive message based on scenario
    scenario_names = {
        "A": "Property-Wide (1 pool → 1 network)",
        "B1": "Per-Unit Site-Wide (1 pool → N networks)",
        "B2": "Per-Unit Individual (N pools → N networks)",
    }
    scenario_desc = scenario_names.get(result.import_mode, result.import_mode)

    if result.valid:
        result.message = (
            f"Ready to import {result.passphrase_count} passphrases "
            f"using Scenario {result.import_mode}: {scenario_desc}"
        )

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
            detail="Cannot confirm: validation found errors",
        )

    # Start execution in background
    background_tasks.add_task(
        run_v2_execution_background,
        job.id,
        job.controller_id,
    )

    unit_count = len(job.units)
    return V2ConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=(
            f"Execution started for {unit_count} unit(s). "
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

    Returns nodes and edges suitable for rendering as a DAG.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Build graph from phase definitions (exclude validate for display)
    execution_phases = [
        p for p in job.phase_definitions if p.id != "validate_and_plan"
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


@router.get("/graph", response_model=V2GraphResponse)
async def get_static_workflow_graph(
    current_user: User = Depends(get_current_user),
):
    """
    Get the static cloudpath import workflow graph.

    Returns the DAG without needing a job - useful for displaying
    the workflow structure before running anything.
    """
    definitions = CloudpathImportWorkflow.get_phase_definitions()

    # Exclude validate phases for the execution graph
    execution_defs = [
        d for d in definitions
        if not d.id.startswith("validate")
    ]
    graph = DependencyGraph(execution_defs)
    graph_data = graph.to_graph_data()

    return V2GraphResponse(
        workflow_name=CloudpathImportWorkflow.name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )
