"""
V2 Cleanup Endpoints

Endpoints for the V2 venue cleanup workflow:
- POST /cleanup/v2/plan    → Scan venue for resources (inventory)
- GET  /cleanup/v2/{id}/plan → Get inventory results
- POST /cleanup/v2/{id}/confirm → Confirm & start deletion
- GET  /cleanup/v2/{id}/graph  → Workflow graph for visualization
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
# IMPORTANT: Import from workflow.workflows to trigger phase registration in __init__.py
from workflow.workflows import get_workflow  # noqa: F401 - triggers phase imports
from workflow.workflows.cleanup import VenueCleanupWorkflow
from workflow.events import WorkflowEventPublisher

from routers.per_unit_ssid.per_unit_ssid_router import validate_controller_access

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cleanup/v2",
    tags=["Venue Cleanup V2"],
)


# ==================== Request / Response Models ====================

class CleanupRequest(BaseModel):
    """Request to plan a venue cleanup."""
    controller_id: int
    venue_id: Optional[str] = None  # Optional - if not set, scans tenant-wide
    tenant_id: Optional[str] = None
    nuclear_mode: bool = True
    name_pattern: Optional[str] = None
    all_networks: bool = True  # Default to all networks


class CleanupPlanResponse(BaseModel):
    """Response from plan endpoint."""
    job_id: str
    status: str
    message: str


class ResourceInventoryResponse(BaseModel):
    """Inventory of discovered resources."""
    passphrases: List[Dict[str, Any]] = Field(default_factory=list)
    dpsk_pools: List[Dict[str, Any]] = Field(default_factory=list)
    identities: List[Dict[str, Any]] = Field(default_factory=list)
    identity_groups: List[Dict[str, Any]] = Field(default_factory=list)
    wifi_networks: List[Dict[str, Any]] = Field(default_factory=list)
    ap_groups: List[Dict[str, Any]] = Field(default_factory=list)


class CleanupPlanResult(BaseModel):
    """Inventory/plan result from polling."""
    job_id: str
    status: str
    inventory: Optional[ResourceInventoryResponse] = None
    total_resources: int = 0


class CleanupConfirmRequest(BaseModel):
    """Request to confirm a cleanup plan."""
    selected_categories: Optional[List[str]] = None  # None = all categories


class CleanupConfirmResponse(BaseModel):
    """Response from confirm endpoint."""
    job_id: str
    status: str
    message: str


class CleanupGraphResponse(BaseModel):
    """Workflow graph for visualization."""
    workflow_name: str
    nodes: list = []
    edges: list = []
    levels: Dict[str, list] = {}


# ==================== Background Tasks ====================

async def run_cleanup_validation_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run cleanup inventory (Phase 0)."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[Cleanup V2] Starting inventory for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cleanup V2] Job {job_id} not found in Redis")
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
            f"[Cleanup V2] Inventory complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[Cleanup V2] Inventory failed for job {job_id}: {e}")

    finally:
        db.close()


async def run_cleanup_execution_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run cleanup deletion phases."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[Cleanup V2] Starting execution for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cleanup V2] Job {job_id} not found in Redis")
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

        await brain.execute_workflow(job)

        logger.info(
            f"[Cleanup V2] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[Cleanup V2] Execution failed for job {job_id}: {e}")

    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/plan", response_model=CleanupPlanResponse)
async def create_cleanup_plan(
    request: CleanupRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a cleanup plan (inventory scan).

    Scans the venue for resources to delete, then returns an inventory
    for user confirmation before any deletions happen.
    """
    logger.info(
        f"[Cleanup V2] Plan request - controller: {request.controller_id}, "
        f"venue: {request.venue_id}, nuclear: {request.nuclear_mode}"
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
        'nuclear_mode': request.nuclear_mode,
        'name_pattern': request.name_pattern,
        'all_networks': request.all_networks,
    }

    input_data = {
        'nuclear_mode': request.nuclear_mode,
        'name_pattern': request.name_pattern,
        'all_networks': request.all_networks,
    }

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    activity_tracker = ActivityTracker(None, state_manager, tenant_id=tenant_id)

    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=activity_tracker,
    )

    job = await brain.create_job(
        workflow=VenueCleanupWorkflow,
        venue_id=request.venue_id or "",  # Empty string if no venue
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options=options,
        input_data=input_data,
    )

    background_tasks.add_task(
        run_cleanup_validation_background,
        job.id,
        request.controller_id,
    )

    return CleanupPlanResponse(
        job_id=job.id,
        status="VALIDATING",
        message=(
            f"Scanning venue for resources. "
            f"Poll GET /cleanup/v2/{job.id}/plan for results."
        ),
    )


@router.get("/{job_id}/plan", response_model=CleanupPlanResult)
async def get_cleanup_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the inventory/plan for a cleanup job.

    Returns the discovered resources grouped by category.
    Poll this after POST /cleanup/v2/plan until status is
    AWAITING_CONFIRMATION.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = CleanupPlanResult(
        job_id=job_id,
        status=job.status.value,
    )

    logger.debug(
        f"[Cleanup V2] GET /plan - job status: {job.status.value}, "
        f"global_phase_status: {list(job.global_phase_status.keys())}"
    )

    # PENDING means job just created, validation hasn't started yet
    # VALIDATING means validation is in progress
    # Both should tell frontend to keep polling
    if job.status in (JobStatus.PENDING, JobStatus.VALIDATING):
        result.status = JobStatus.VALIDATING.value  # Normalize to VALIDATING for frontend
        return result

    if job.status == JobStatus.FAILED:
        return result

    # Extract inventory from global phase results
    logger.debug(
        f"[Cleanup V2] GET /plan - global_phase_results keys: "
        f"{list(job.global_phase_results.keys())}"
    )
    inventory_results = job.global_phase_results.get("inventory", {})
    logger.debug(
        f"[Cleanup V2] GET /plan - inventory_results keys: "
        f"{list(inventory_results.keys()) if isinstance(inventory_results, dict) else type(inventory_results)}"
    )
    inventory_data = inventory_results.get("inventory")
    logger.debug(
        f"[Cleanup V2] GET /plan - inventory_data type: {type(inventory_data)}, "
        f"truthy: {bool(inventory_data)}"
    )

    if inventory_data:
        result.inventory = ResourceInventoryResponse(**inventory_data)
        result.total_resources = inventory_results.get("total_resources", 0)
        logger.debug(
            f"[Cleanup V2] GET /plan - returning {result.total_resources} resources"
        )
    elif job.status == JobStatus.AWAITING_CONFIRMATION:
        # Race condition: status updated but inventory not yet visible
        # Tell frontend to keep polling
        logger.warning(
            f"[Cleanup V2] GET /plan - race condition detected: "
            f"status={job.status.value} but inventory missing, returning VALIDATING"
        )
        result.status = JobStatus.VALIDATING.value

    return result


@router.post("/{job_id}/confirm", response_model=CleanupConfirmResponse)
async def confirm_cleanup(
    job_id: str,
    request: CleanupConfirmRequest = CleanupConfirmRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm the cleanup plan and start deletion.

    Must be called after inventory completes. Job must be
    in AWAITING_CONFIRMATION status.

    Optionally pass selected_categories to only delete specific
    resource types (e.g. ["wifi_networks", "ap_groups"]).
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

    # Filter inventory to only include selected categories
    ALL_CATEGORIES = [
        "passphrases", "dpsk_pools", "identities",
        "identity_groups", "wifi_networks", "ap_groups",
    ]
    selected = request.selected_categories
    if selected is not None:
        selected_set = set(selected)
        inventory_results = job.global_phase_results.get("inventory", {})
        inv_data = inventory_results.get("inventory")

        if inv_data and isinstance(inv_data, dict):
            for cat in ALL_CATEGORIES:
                if cat not in selected_set:
                    inv_data[cat] = []

            # Recalculate total
            new_total = sum(
                len(inv_data.get(cat, []))
                for cat in ALL_CATEGORIES
            )
            inventory_results["total_resources"] = new_total
            inventory_results["inventory"] = inv_data
            job.global_phase_results["inventory"] = inventory_results

            await state_manager.save_job(job)
            logger.info(
                f"[Cleanup V2] Filtered inventory to categories: "
                f"{sorted(selected_set)} ({new_total} resources)"
            )

    background_tasks.add_task(
        run_cleanup_execution_background,
        job.id,
        job.controller_id,
    )

    return CleanupConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=(
            f"Cleanup execution started. "
            f"Poll /jobs/{job.id}/status for progress."
        ),
    )


@router.get("/{job_id}/graph", response_model=CleanupGraphResponse)
async def get_cleanup_graph(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get the workflow graph for a cleanup job."""
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    graph = DependencyGraph(job.phase_definitions)
    graph_data = graph.to_graph_data()

    return CleanupGraphResponse(
        workflow_name=job.workflow_name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )
