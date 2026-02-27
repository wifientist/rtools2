"""
SZ Migration Router — M0 + M1 + M2 + M4 + M6d Endpoints

Prefix: /sz-migration, alpha-gated

M0 (SZ Extraction):
  POST /sz-migration/census                  — quick zone census
  POST /sz-migration/extract                 — trigger SZ deep extraction
  GET  /sz-migration/extract/{job_id}/status  — poll extraction progress
  GET  /sz-migration/extract/{job_id}/sse     — SSE stream for real-time progress
  GET  /sz-migration/snapshot/{job_id}        — full SZ snapshot as JSON
  GET  /sz-migration/snapshot/{job_id}/download — SZ snapshot file download

M1 (R1 Inventory):
  POST /sz-migration/r1-snapshot              — capture R1 venue state
  GET  /sz-migration/r1-snapshot/{job_id}     — full R1 inventory as JSON
  GET  /sz-migration/r1-snapshot/{job_id}/download — R1 inventory file download

M2 (Resolution):
  POST /sz-migration/resolve/{job_id}         — resolve WLAN Groups + map security types

M4 (Workflow):
  POST /sz-migration/workflow/plan             — create migration job, run validation
  GET  /sz-migration/workflow/{job_id}/plan     — get validation result
  POST /sz-migration/workflow/{job_id}/confirm  — confirm plan & start execution
  GET  /sz-migration/workflow/{job_id}/graph    — workflow DAG for visualization

M6d (Audit):
  POST /sz-migration/audit                    — cross-controller migration audit
"""

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from decorators import require_alpha
from models.user import User
from models.controller import Controller
from models.sz_migration_session import SZMigrationSession
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client
from services.sz_migration.extractor import extract_zone_snapshot
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.mapper import map_all_wlans
from services.sz_migration.auditor import run_audit as run_migration_audit
from services.r1_inventory import capture_venue_inventory
from schemas.sz_migration import SZMigrationSnapshot, ResolverResult
from schemas.r1_inventory import R1VenueInventory

# M4: Workflow engine imports
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.graph import DependencyGraph
from workflow.workflows.sz_to_r1_migration import SZtoR1MigrationWorkflow
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sz-migration")

# Redis key prefixes and TTL
EXTRACTION_KEY_PREFIX = "sz_migration:extraction"
R1_SNAPSHOT_KEY_PREFIX = "sz_migration:r1_snapshot"
SNAPSHOT_TTL = 60 * 60 * 24  # 24 hours


# ── Request/Response Models ──────────────────────────────────────────

class CensusRequest(BaseModel):
    """Quick zone census request"""
    controller_id: int
    zone_id: str


class CensusResponse(BaseModel):
    """Quick zone census — cheap list calls, no full detail"""
    zone_id: str
    zone_name: str
    wlan_count: int
    wlan_group_count: int
    ap_group_count: int
    ap_count: int


class ExtractRequest(BaseModel):
    """Trigger deep extraction of a single SZ zone"""
    controller_id: int
    zone_id: str


class ExtractResponse(BaseModel):
    """Response from triggering extraction"""
    job_id: str
    status: str
    message: str


class ExtractionStatus(BaseModel):
    """Extraction job status for polling"""
    job_id: str
    status: str  # pending, running, completed, failed
    progress: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    snapshot_summary: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class R1SnapshotRequest(BaseModel):
    """Capture R1 venue state"""
    controller_id: int
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str


# ── Helper: Redis state for extraction jobs ──────────────────────────

async def _save_extraction_state(job_id: str, state: Dict[str, Any]):
    """Save extraction job state to Redis."""
    redis = await get_redis_client()
    key = f"{EXTRACTION_KEY_PREFIX}:{job_id}:state"
    await redis.set(key, json.dumps(state, default=str), ex=SNAPSHOT_TTL)


async def _get_extraction_state(job_id: str) -> Optional[Dict[str, Any]]:
    """Get extraction job state from Redis."""
    redis = await get_redis_client()
    key = f"{EXTRACTION_KEY_PREFIX}:{job_id}:state"
    raw = await redis.get(key)
    if raw:
        return json.loads(raw)
    return None


async def _save_snapshot(job_id: str, snapshot: SZMigrationSnapshot):
    """Persist the full snapshot JSON to Redis."""
    redis = await get_redis_client()
    key = f"{EXTRACTION_KEY_PREFIX}:{job_id}:snapshot"
    await redis.set(key, snapshot.model_dump_json(), ex=SNAPSHOT_TTL)


async def _get_snapshot_raw(job_id: str) -> Optional[str]:
    """Get the raw snapshot JSON string from Redis."""
    redis = await get_redis_client()
    key = f"{EXTRACTION_KEY_PREFIX}:{job_id}:snapshot"
    return await redis.get(key)


async def _publish_progress(job_id: str, phase: str, message: str, data: Dict[str, Any]):
    """Publish progress event via Redis pub/sub for SSE."""
    redis = await get_redis_client()
    channel = f"{EXTRACTION_KEY_PREFIX}:{job_id}:progress"
    event = json.dumps({"phase": phase, "message": message, "data": data}, default=str)
    await redis.publish(channel, event)


# ── Background extraction task ───────────────────────────────────────

async def _run_extraction(
    job_id: str,
    controller_id: int,
    zone_id: str,
    db: Session,
):
    """Background task that runs the full SZ extraction."""
    try:
        await _save_extraction_state(job_id, {
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
            "progress": {"phase": "starting", "message": "Initializing..."},
        })

        # Create SZ client
        sz_client = create_sz_client_from_controller(controller_id, db)

        async def on_progress(phase: str, message: str, data: Dict[str, Any]):
            """Progress callback — updates state and publishes SSE event."""
            await _save_extraction_state(job_id, {
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
                "progress": {"phase": phase, "message": message, "data": data},
            })
            await _publish_progress(job_id, phase, message, data)

        try:
            await sz_client.login()
        except ValueError as e:
            error_msg = str(e)
            logger.warning(f"Extraction {job_id} auth failed: {error_msg}")
            await _save_extraction_state(job_id, {
                "status": "failed",
                "error": f"Authentication failed: {error_msg}",
            })
            return

        try:
            snapshot = await extract_zone_snapshot(sz_client, zone_id, on_progress=on_progress)

            # Persist snapshot
            await _save_snapshot(job_id, snapshot)

            await _save_extraction_state(job_id, {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "snapshot_summary": snapshot.summary(),
            })

            # Publish completion event
            await _publish_progress(job_id, "complete", "Extraction complete", snapshot.summary())

            logger.info(f"Extraction {job_id} completed: {snapshot.summary()}")

        finally:
            try:
                await sz_client.logout()
                await sz_client.client.aclose()
            except Exception:
                pass

    except Exception as e:
        logger.exception(f"Extraction {job_id} failed: {e}")
        await _save_extraction_state(job_id, {
            "status": "failed",
            "error": str(e),
        })
        await _publish_progress(job_id, "error", str(e), {})


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/census", response_model=CensusResponse)
@require_alpha()
async def zone_census(
    request: CensusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Quick zone census — cheap list calls to show counts before committing
    to a full deep extraction.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    if controller.controller_type != "SmartZone":
        raise HTTPException(status_code=400, detail="Controller must be SmartZone")

    sz_client = create_sz_client_from_controller(request.controller_id, db)

    async with sz_client:
        # Parallel cheap list calls
        zone_detail, wlans, wlan_groups, ap_groups, aps = await asyncio.gather(
            sz_client.zones.get_zone_details(request.zone_id),
            sz_client.wlans.get_wlans_by_zone(request.zone_id),
            sz_client.wlans.get_wlan_groups_by_zone(request.zone_id),
            sz_client.apgroups.get_ap_groups_by_zone(request.zone_id),
            sz_client.aps.get_aps_by_zone(request.zone_id),
        )

    ap_list = aps.get("list", []) if isinstance(aps, dict) else aps

    return CensusResponse(
        zone_id=request.zone_id,
        zone_name=zone_detail.get("name", ""),
        wlan_count=len(wlans),
        wlan_group_count=len(wlan_groups),
        ap_group_count=len(ap_groups),
        ap_count=len(ap_list),
    )


@router.post("/extract", response_model=ExtractResponse)
@require_alpha()
async def start_extraction(
    request: ExtractRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Trigger deep extraction of a single SZ zone.
    Returns immediately with a job_id for polling.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    if controller.controller_type != "SmartZone":
        raise HTTPException(status_code=400, detail="Controller must be SmartZone")

    job_id = str(uuid.uuid4())

    # Save initial state
    await _save_extraction_state(job_id, {
        "status": "pending",
        "started_at": datetime.utcnow().isoformat(),
    })

    # Track job in user's extraction history
    redis = await get_redis_client()
    user_index_key = f"{EXTRACTION_KEY_PREFIX}:user:{current_user.id}:jobs"
    await redis.zadd(user_index_key, {job_id: time.time()})
    await redis.expire(user_index_key, SNAPSHOT_TTL)

    # Launch background extraction
    background_tasks.add_task(
        _run_extraction,
        job_id,
        request.controller_id,
        request.zone_id,
        db,
    )

    logger.info(f"Started extraction {job_id} for zone {request.zone_id} on controller {controller.name}")

    return ExtractResponse(
        job_id=job_id,
        status="running",
        message=f"Extraction started. Poll /sz-migration/extract/{job_id}/status for progress.",
    )


@router.get("/extract/{job_id}/status", response_model=ExtractionStatus)
@require_alpha()
async def get_extraction_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Poll extraction progress."""
    state = await _get_extraction_state(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Extraction job not found")

    return ExtractionStatus(
        job_id=job_id,
        status=state.get("status", "unknown"),
        progress=state.get("progress"),
        error=state.get("error"),
        snapshot_summary=state.get("snapshot_summary"),
        started_at=state.get("started_at"),
        completed_at=state.get("completed_at"),
    )


@router.get("/snapshot/{job_id}")
@require_alpha()
async def get_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return the full SZMigrationSnapshot as JSON."""
    raw = await _get_snapshot_raw(job_id)
    if not raw:
        # Check if extraction is still running
        state = await _get_extraction_state(job_id)
        if state and state.get("status") in ("pending", "running"):
            raise HTTPException(status_code=202, detail="Extraction still in progress")
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return Response(
        content=raw,
        media_type="application/json",
    )


@router.get("/snapshot/{job_id}/download")
@require_alpha()
async def download_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download the full SZMigrationSnapshot as a JSON file."""
    raw = await _get_snapshot_raw(job_id)
    if not raw:
        state = await _get_extraction_state(job_id)
        if state and state.get("status") in ("pending", "running"):
            raise HTTPException(status_code=202, detail="Extraction still in progress")
        raise HTTPException(status_code=404, detail="Snapshot not found")

    # Parse to get zone name for filename
    try:
        data = json.loads(raw)
        zone_name = data.get("zone", {}).get("name", "unknown")
        # Sanitize for filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in zone_name)
    except Exception:
        safe_name = "unknown"

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"sz_snapshot_{safe_name}_{timestamp}.json"

    return Response(
        content=raw,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/extract/{job_id}/sse")
@require_alpha()
async def extraction_sse(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    SSE stream for real-time extraction progress.
    Subscribes to Redis pub/sub for the extraction job.
    """
    # Verify job exists
    state = await _get_extraction_state(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Extraction job not found")

    async def event_generator():
        redis = await get_redis_client()
        pubsub = redis.pubsub()
        channel = f"{EXTRACTION_KEY_PREFIX}:{job_id}:progress"

        try:
            await pubsub.subscribe(channel)

            # Send initial state
            current_state = await _get_extraction_state(job_id)
            if current_state:
                yield f"data: {json.dumps(current_state, default=str)}\n\n"

            # If already completed/failed, send that and close
            if current_state and current_state.get("status") in ("completed", "failed"):
                return

            # Listen for progress events
            timeout_seconds = 600  # 10 minute max for SSE connection
            start = time.time()

            while time.time() - start < timeout_seconds:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=5.0,
                )

                if message and message["type"] == "message":
                    yield f"data: {message['data']}\n\n"

                    # Check if this was the completion event
                    try:
                        event_data = json.loads(message["data"])
                        if event_data.get("phase") in ("complete", "error"):
                            return
                    except Exception:
                        pass
                else:
                    # Send keepalive
                    yield ": keepalive\n\n"

        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════════════
# M1: R1 Venue Inventory Endpoints
# ══════════════════════════════════════════════════════════════════════

async def _save_r1_snapshot(job_id: str, inventory: R1VenueInventory):
    """Persist R1 inventory JSON to Redis."""
    redis = await get_redis_client()
    key = f"{R1_SNAPSHOT_KEY_PREFIX}:{job_id}:snapshot"
    await redis.set(key, inventory.model_dump_json(), ex=SNAPSHOT_TTL)


async def _get_r1_snapshot_raw(job_id: str) -> Optional[str]:
    """Get raw R1 inventory JSON from Redis."""
    redis = await get_redis_client()
    key = f"{R1_SNAPSHOT_KEY_PREFIX}:{job_id}:snapshot"
    return await redis.get(key)


@router.post("/r1-snapshot", response_model=ExtractResponse)
@require_alpha()
async def capture_r1_snapshot(
    request: R1SnapshotRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Capture the complete state of an R1 venue.
    Synchronous — returns the snapshot directly (R1 queries are fast).
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    r1_client = create_r1_client_from_controller(request.controller_id, db)

    try:
        inventory = await capture_venue_inventory(
            r1_client,
            tenant_id,
            request.venue_id,
        )
    except Exception as e:
        logger.exception(f"R1 snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=f"R1 snapshot failed: {e}")

    # Persist with a job_id for later retrieval/download
    job_id = str(uuid.uuid4())
    await _save_r1_snapshot(job_id, inventory)

    # Track in user history
    redis = await get_redis_client()
    user_index_key = f"{R1_SNAPSHOT_KEY_PREFIX}:user:{current_user.id}:jobs"
    await redis.zadd(user_index_key, {job_id: time.time()})
    await redis.expire(user_index_key, SNAPSHOT_TTL)

    logger.info(f"R1 snapshot {job_id} captured: {inventory.summary()}")

    return ExtractResponse(
        job_id=job_id,
        status="completed",
        message=f"R1 venue snapshot captured. {inventory.summary()}",
    )


@router.get("/r1-snapshot/{job_id}")
@require_alpha()
async def get_r1_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return the full R1VenueInventory as JSON."""
    raw = await _get_r1_snapshot_raw(job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="R1 snapshot not found")

    return Response(
        content=raw,
        media_type="application/json",
    )


@router.get("/r1-snapshot/{job_id}/download")
@require_alpha()
async def download_r1_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download the full R1VenueInventory as a JSON file."""
    raw = await _get_r1_snapshot_raw(job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="R1 snapshot not found")

    # Parse to get venue name for filename
    try:
        data = json.loads(raw)
        venue_name = data.get("venue_name", "unknown")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in venue_name)
    except Exception:
        safe_name = "unknown"

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"r1_snapshot_{safe_name}_{timestamp}.json"

    return Response(
        content=raw,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ══════════════════════════════════════════════════════════════════════
# M2: WLAN Group Resolution + Security Type Mapping
# ══════════════════════════════════════════════════════════════════════

@router.post("/resolve/{job_id}")
@require_alpha()
async def resolve_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Resolve WLAN Groups into per-AP-Group activations and map security types.

    Reads the SZ snapshot from Redis (produced by /extract), runs the resolver
    and mapper, and returns the combined result.
    """
    raw = await _get_snapshot_raw(job_id)
    if not raw:
        state = await _get_extraction_state(job_id)
        if state and state.get("status") in ("pending", "running"):
            raise HTTPException(status_code=202, detail="Extraction still in progress")
        raise HTTPException(status_code=404, detail="Snapshot not found")

    snapshot = SZMigrationSnapshot.model_validate_json(raw)

    # Resolve WLAN Groups → activations
    resolver_result = resolve_wlan_activations(snapshot)

    # Map security types
    type_mappings = map_all_wlans(snapshot.wlans)

    return {
        "job_id": job_id,
        "zone_name": snapshot.zone.name,
        "resolver": resolver_result.model_dump(),
        "type_mappings": {
            wlan_id: {
                "wlan_name": next((w.name for w in snapshot.wlans if w.id == wlan_id), ""),
                "sz_auth_type": m.sz_auth_type,
                "r1_network_type": m.r1_network_type,
                "notes": m.notes,
                "needs_user_decision": m.needs_user_decision,
                "dpsk_type": m.dpsk_type,
            }
            for wlan_id, m in type_mappings.items()
        },
    }


# ══════════════════════════════════════════════════════════════════════
# M4: V2 Workflow Endpoints
# ══════════════════════════════════════════════════════════════════════


class MigrationPlanRequest(BaseModel):
    """Request to create a migration workflow plan."""
    sz_controller_id: int = Field(..., description="SZ controller ID (for snapshot reference)")
    r1_controller_id: int = Field(..., description="R1 controller ID (target)")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="R1 venue ID (target)")
    sz_snapshot_job_id: str = Field(..., description="SZ extraction job ID (from M0)")


class MigrationPlanResponse(BaseModel):
    """Response from plan endpoint."""
    job_id: str
    status: str
    message: str


class MigrationPlanResult(BaseModel):
    """Validation plan result."""
    job_id: str
    status: str
    valid: bool = False
    message: str = ""
    summary: Dict[str, Any] = {}
    conflicts: list = []
    unit_count: int = 0
    estimated_api_calls: int = 0
    actions: list = []
    resolver_result: Dict[str, Any] = {}
    type_mappings: Dict[str, Any] = {}
    r1_inventory_summary: Dict[str, Any] = {}


class MigrationConfirmResponse(BaseModel):
    """Response from confirm endpoint."""
    job_id: str
    status: str
    message: str


class MigrationGraphResponse(BaseModel):
    """Workflow graph for visualization."""
    workflow_name: str
    nodes: list = []
    edges: list = []
    levels: Dict[str, list] = {}


# ── Background Tasks ─────────────────────────────────────────────────

async def _run_migration_validation_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run migration validation (Phase 0)."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[SZ→R1] Starting validation for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[SZ→R1] Job {job_id} not found in Redis")
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
            f"[SZ→R1] Validation complete for job {job_id} "
            f"(status={job.status.value})"
        )

    except Exception as e:
        logger.exception(f"[SZ→R1] Validation failed for job {job_id}: {e}")

    finally:
        db.close()


async def _run_migration_execution_background(
    job_id: str,
    controller_id: int,
):
    """Background task to run migration workflow execution."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[SZ→R1] Starting execution for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[SZ→R1] Job {job_id} not found in Redis")
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
            f"[SZ→R1] Execution complete for job {job_id} "
            f"(status={job.status.value})"
        )

        # Persist execution results to DB so reports survive Redis TTL
        try:
            completed_job = await state_manager.get_job(job_id)
            if completed_job:
                _persist_execution_results(db, job_id, completed_job)
        except Exception as persist_err:
            logger.warning(f"[SZ→R1] Failed to persist execution results: {persist_err}")

    except Exception as e:
        logger.exception(f"[SZ→R1] Execution failed for job {job_id}: {e}")

    finally:
        db.close()


def _persist_execution_results(db, job_id: str, job) -> None:
    """Save execution results to the session's summary_json for report generation."""
    progress = job.get_progress()
    phase_stats = progress.get("phase_stats", {})

    # Build per-phase summary
    phases_summary = []
    for defn in job.phase_definitions:
        stats = phase_stats.get(defn.id, {})
        phases_summary.append({
            "id": defn.id,
            "name": defn.name,
            "status": stats.get("status", "PENDING"),
            "completed": stats.get("completed", 0),
            "failed": stats.get("failed", 0),
            "total": stats.get("total", 0),
        })

    # Build per-unit details for CSV/report
    unit_details = []
    for unit in job.units.values():
        cfg = unit.input_config
        unit_details.append({
            "unit_id": unit.unit_id,
            "wlan_name": cfg.get("wlan_name", unit.unit_number),
            "ssid": cfg.get("ssid", ""),
            "r1_network_type": cfg.get("r1_network_type", ""),
            "status": unit.status.value,
            "network_name": unit.plan.network_name,
            "network_id": unit.resolved.network_id,
            "reused": unit.plan.network_exists,
            "dpsk_pool_id": unit.resolved.dpsk_pool_id,
            "identity_group_id": unit.resolved.identity_group_id,
            "activated": unit.resolved.activated,
            "error": next(iter(unit.phase_errors.values()), None),
        })

    execution_summary = {
        "status": job.status.value,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "progress": {
            "total_tasks": progress.get("total_work", 0),
            "completed": progress.get("completed_work", 0),
            "failed": progress.get("units_failed", 0),
            "percent": progress.get("percent", 0),
        },
        "created_resources": job.created_resources,
        "errors": job.errors,
        "phases": phases_summary,
        "units": unit_details,
    }

    # Update DB session
    session = (
        db.query(SZMigrationSession)
        .filter(SZMigrationSession.execution_job_id == job_id)
        .first()
    )
    if session:
        existing = session.summary_json or {}
        existing["execution"] = execution_summary
        session.summary_json = existing
        final_status = "completed" if job.status == JobStatus.COMPLETED else "failed"
        session.status = final_status
        session.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"[SZ→R1] Saved execution results to session {session.id} (status={final_status})")
    else:
        logger.warning(f"[SZ→R1] No session found for execution job {job_id}")


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/workflow/plan", response_model=MigrationPlanResponse)
@require_alpha()
async def create_migration_plan(
    request: MigrationPlanRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a migration workflow plan (Phase 0: validate & plan).

    Reads the SZ snapshot, resolves WLAN Groups, maps security types,
    inventories the R1 venue, and builds per-WLAN unit mappings.

    After this returns, poll GET /sz-migration/workflow/{job_id}/plan
    for the validation result. Once validated, call
    POST /sz-migration/workflow/{job_id}/confirm to start execution.
    """
    # Verify SZ snapshot exists
    raw = await _get_snapshot_raw(request.sz_snapshot_job_id)
    if not raw:
        raise HTTPException(
            status_code=404,
            detail=f"SZ snapshot not found for job {request.sz_snapshot_job_id}",
        )

    # Validate R1 controller access
    controller = validate_controller_access(request.r1_controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="R1 controller must be RuckusONE")

    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400, detail="tenant_id is required for MSP controllers"
        )

    # Build job options
    options = {
        "sz_snapshot_job_id": request.sz_snapshot_job_id,
        "sz_controller_id": request.sz_controller_id,
    }

    input_data = {
        "sz_snapshot_job_id": request.sz_snapshot_job_id,
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
        workflow=SZtoR1MigrationWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.r1_controller_id,
        user_id=current_user.id,
        options=options,
        input_data=input_data,
    )

    # Start validation in background
    background_tasks.add_task(
        _run_migration_validation_background,
        job.id,
        request.r1_controller_id,
    )

    return MigrationPlanResponse(
        job_id=job.id,
        status="VALIDATING",
        message=(
            f"Validating migration plan for SZ snapshot {request.sz_snapshot_job_id}. "
            f"Poll GET /sz-migration/workflow/{job.id}/plan for results."
        ),
    )


@router.get("/workflow/{job_id}/plan", response_model=MigrationPlanResult)
@require_alpha()
async def get_migration_plan(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the migration validation plan.

    Returns the dry-run results: what networks will be created/reused,
    RADIUS profiles needed, DPSK configuration, and activation map.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = MigrationPlanResult(
        job_id=job_id,
        status=job.status.value,
        unit_count=len(job.units),
    )

    if job.status == JobStatus.VALIDATING:
        result.message = "Validation in progress..."
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
        result.estimated_api_calls = vr.summary.total_api_calls if vr.summary else 0
        result.actions = [a.model_dump() for a in vr.actions]

    # Include extra results from validation phase
    validate_results = job.global_phase_results.get("sz_validate_and_plan", {})
    result.resolver_result = validate_results.get("resolver_result", {})
    result.type_mappings = validate_results.get("type_mappings", {})
    result.r1_inventory_summary = validate_results.get("r1_inventory_summary", {})

    return result


@router.post("/workflow/{job_id}/confirm", response_model=MigrationConfirmResponse)
@require_alpha()
async def confirm_migration(
    job_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    Confirm the migration plan and start execution.

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

    if job.validation_result and not job.validation_result.valid:
        raise HTTPException(
            status_code=400,
            detail="Cannot confirm: validation found blocking conflicts",
        )

    # Start execution in background
    background_tasks.add_task(
        _run_migration_execution_background,
        job.id,
        job.controller_id,
    )

    return MigrationConfirmResponse(
        job_id=job.id,
        status="RUNNING",
        message=(
            f"Migration started for {len(job.units)} WLANs. "
            f"Poll /jobs/{job.id}/status for progress."
        ),
    )


@router.get("/workflow/{job_id}/graph", response_model=MigrationGraphResponse)
@require_alpha()
async def get_migration_graph(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the migration workflow dependency graph for visualization.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    execution_phases = [
        p for p in job.phase_definitions if p.id != "sz_validate_and_plan"
    ]
    graph = DependencyGraph(execution_phases)
    graph_data = graph.to_graph_data()

    return MigrationGraphResponse(
        workflow_name=job.workflow_name,
        nodes=graph_data["nodes"],
        edges=graph_data["edges"],
        levels={
            str(k): v for k, v in graph.compute_levels().items()
        },
    )


# ── M6b: Migration Session Persistence ────────────────────────────────


class SessionCreateRequest(BaseModel):
    """Create a new migration session."""
    sz_controller_id: int
    sz_domain_id: Optional[str] = None
    sz_zone_id: Optional[str] = None
    sz_zone_name: Optional[str] = None


class SessionUpdateRequest(BaseModel):
    """Partial update for a migration session."""
    status: Optional[str] = None
    current_step: Optional[int] = None
    sz_domain_id: Optional[str] = None
    sz_zone_id: Optional[str] = None
    sz_zone_name: Optional[str] = None
    r1_controller_id: Optional[int] = None
    r1_tenant_id: Optional[str] = None
    r1_venue_id: Optional[str] = None
    r1_venue_name: Optional[str] = None
    extraction_job_id: Optional[str] = None
    r1_snapshot_job_id: Optional[str] = None
    plan_job_id: Optional[str] = None
    execution_job_id: Optional[str] = None
    wlan_count: Optional[int] = None
    summary_json: Optional[dict] = None


class SessionResponse(BaseModel):
    """Session response — serializable snapshot of DB row."""
    id: int
    status: str
    created_at: str
    updated_at: str
    current_step: int
    sz_controller_id: Optional[int] = None
    sz_domain_id: Optional[str] = None
    sz_zone_id: Optional[str] = None
    sz_zone_name: Optional[str] = None
    r1_controller_id: Optional[int] = None
    r1_tenant_id: Optional[str] = None
    r1_venue_id: Optional[str] = None
    r1_venue_name: Optional[str] = None
    extraction_job_id: Optional[str] = None
    r1_snapshot_job_id: Optional[str] = None
    plan_job_id: Optional[str] = None
    execution_job_id: Optional[str] = None
    wlan_count: Optional[int] = None
    summary_json: Optional[dict] = None


def _session_to_response(session: SZMigrationSession) -> SessionResponse:
    """Convert DB model to response dict."""
    return SessionResponse(
        id=session.id,
        status=session.status,
        created_at=session.created_at.isoformat() if session.created_at else "",
        updated_at=session.updated_at.isoformat() if session.updated_at else "",
        current_step=session.current_step,
        sz_controller_id=session.sz_controller_id,
        sz_domain_id=session.sz_domain_id,
        sz_zone_id=session.sz_zone_id,
        sz_zone_name=session.sz_zone_name,
        r1_controller_id=session.r1_controller_id,
        r1_tenant_id=session.r1_tenant_id,
        r1_venue_id=session.r1_venue_id,
        r1_venue_name=session.r1_venue_name,
        extraction_job_id=session.extraction_job_id,
        r1_snapshot_job_id=session.r1_snapshot_job_id,
        plan_job_id=session.plan_job_id,
        execution_job_id=session.execution_job_id,
        wlan_count=session.wlan_count,
        summary_json=session.summary_json,
    )


@router.post("/sessions", response_model=SessionResponse)
@require_alpha()
def create_session(
    body: SessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new migration session."""
    validate_controller_access(body.sz_controller_id, current_user, db)

    session = SZMigrationSession(
        user_id=current_user.id,
        status="draft",
        sz_controller_id=body.sz_controller_id,
        sz_domain_id=body.sz_domain_id,
        sz_zone_id=body.sz_zone_id,
        sz_zone_name=body.sz_zone_name,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    logger.info(f"[sessions] Created session {session.id} for user {current_user.email}")
    return _session_to_response(session)


@router.get("/sessions", response_model=List[SessionResponse])
@require_alpha()
def list_sessions(
    status: Optional[str] = None,
    sz_controller_id: Optional[int] = None,
    r1_controller_id: Optional[int] = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List migration sessions for the current user."""
    query = (
        db.query(SZMigrationSession)
        .filter(SZMigrationSession.user_id == current_user.id)
    )
    if status == "active":
        query = query.filter(
            SZMigrationSession.status.notin_(["completed", "failed"])
        )
    elif status:
        query = query.filter(SZMigrationSession.status == status)

    if sz_controller_id is not None:
        query = query.filter(SZMigrationSession.sz_controller_id == sz_controller_id)
    if r1_controller_id is not None:
        query = query.filter(SZMigrationSession.r1_controller_id == r1_controller_id)

    sessions = (
        query
        .order_by(SZMigrationSession.updated_at.desc())
        .limit(min(limit, 50))
        .all()
    )
    return [_session_to_response(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
@require_alpha()
def get_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single migration session."""
    session = (
        db.query(SZMigrationSession)
        .filter(
            SZMigrationSession.id == session_id,
            SZMigrationSession.user_id == current_user.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session)


VALID_STATUSES = {"draft", "extracting", "reviewing", "executing", "completed", "failed"}


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
@require_alpha()
def update_session(
    session_id: int,
    body: SessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Partial update of a migration session."""
    session = (
        db.query(SZMigrationSession)
        .filter(
            SZMigrationSession.id == session_id,
            SZMigrationSession.user_id == current_user.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    updates = body.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )

    for field, value in updates.items():
        setattr(session, field, value)

    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    logger.info(f"[sessions] Updated session {session.id} fields={list(updates.keys())}")
    return _session_to_response(session)


# ── Migration Report ───────────────────────────────────────────────────

@router.get("/sessions/{session_id}/report.csv")
@require_alpha()
def export_migration_report_csv(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export migration report as CSV with per-WLAN rows."""
    session = (
        db.query(SZMigrationSession)
        .filter(
            SZMigrationSession.id == session_id,
            SZMigrationSession.user_id == current_user.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    summary = session.summary_json or {}
    execution = summary.get("execution")
    if not execution:
        raise HTTPException(
            status_code=404,
            detail="No execution results available for this session",
        )

    output = io.StringIO()
    writer = csv.writer(output)

    # Metadata header
    writer.writerow(["Migration Report"])
    writer.writerow(["Source Zone", session.sz_zone_name or ""])
    writer.writerow(["Destination Venue", session.r1_venue_name or ""])
    writer.writerow(["Status", execution.get("status", "")])
    writer.writerow(["Started", execution.get("started_at", "")])
    writer.writerow(["Completed", execution.get("completed_at", "")])
    progress = execution.get("progress", {})
    writer.writerow(["WLANs Total", summary.get("unit_count", "")])
    writer.writerow(["WLANs Completed", progress.get("completed", "")])
    writer.writerow(["WLANs Failed", progress.get("failed", "")])
    writer.writerow([])  # blank separator

    # Column headers
    headers = [
        "WLAN Name", "SSID", "Network Type", "Status", "Action",
        "R1 Network Name", "R1 Network ID",
        "DPSK Pool ID", "Identity Group ID",
        "SSID Activated", "Error",
    ]
    writer.writerow(headers)

    # Data rows
    for unit in execution.get("units", []):
        writer.writerow([
            unit.get("wlan_name", ""),
            unit.get("ssid", ""),
            unit.get("r1_network_type", ""),
            unit.get("status", ""),
            "Reused" if unit.get("reused") else "Created",
            unit.get("network_name", ""),
            unit.get("network_id", ""),
            unit.get("dpsk_pool_id", ""),
            unit.get("identity_group_id", ""),
            "Yes" if unit.get("activated") else "No",
            unit.get("error", ""),
        ])

    output.seek(0)
    safe_zone = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (session.sz_zone_name or "unknown")
    )
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"migration_report_{safe_zone}_{timestamp}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── M6d: Cross-Controller Audit ──────────────────────────────────


class AuditRequest(BaseModel):
    """Request for cross-controller migration audit.

    SZ source — provide ONE of:
      (A) sz_snapshot_job_id  — use an existing extraction snapshot (fast if still in Redis)
      (B) sz_controller_id + zone_id — inline extraction (10-30s)
    """
    # Path A: existing snapshot
    sz_snapshot_job_id: Optional[str] = Field(None, description="SZ extraction job ID (fast path)")
    # Path B: fresh inline extraction
    sz_controller_id: Optional[int] = Field(None, description="SZ controller ID (for inline extraction)")
    zone_id: Optional[str] = Field(None, description="SZ zone ID (for inline extraction)")

    # R1 side
    r1_snapshot_job_id: Optional[str] = Field(
        None, description="Existing R1 snapshot job ID (if omitted, captures fresh)"
    )
    r1_controller_id: Optional[int] = Field(None, description="R1 controller ID (for fresh capture)")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP controllers)")
    venue_id: Optional[str] = Field(None, description="R1 venue ID (for fresh capture)")


@router.post("/audit")
@require_alpha()
async def run_audit_endpoint(
    request: AuditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Run a three-way migration audit: SZ source → Expected R1 → Actual R1.

    SZ source — provide ONE of:
      (A) sz_snapshot_job_id — use an existing extraction (fast if still in Redis)
      (B) sz_controller_id + zone_id — inline extraction (10-30s)

    R1 destination — provide ONE of:
      (A) r1_snapshot_job_id — use an existing R1 snapshot
      (B) r1_controller_id + venue_id — capture fresh R1 inventory

    Returns a structured audit report with per-WLAN comparison,
    AP Group coverage, and resource checks.
    """
    # ── SZ snapshot: load existing or extract inline ──
    if request.sz_snapshot_job_id:
        # Path A: existing snapshot
        sz_raw = await _get_snapshot_raw(request.sz_snapshot_job_id)
        if not sz_raw:
            state = await _get_extraction_state(request.sz_snapshot_job_id)
            if state and state.get("status") in ("pending", "running"):
                raise HTTPException(status_code=202, detail="SZ extraction still in progress")
            raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
        snapshot = SZMigrationSnapshot.model_validate_json(sz_raw)
        sz_job_id = request.sz_snapshot_job_id

    elif request.sz_controller_id and request.zone_id:
        # Path B: inline extraction
        sz_controller = validate_controller_access(request.sz_controller_id, current_user, db)
        if sz_controller.controller_type != "SmartZone":
            raise HTTPException(status_code=400, detail="SZ controller must be SmartZone")

        sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
        try:
            await sz_client.login()
            snapshot = await extract_zone_snapshot(sz_client, request.zone_id)
        except Exception as e:
            logger.exception(f"Inline SZ extraction failed: {e}")
            raise HTTPException(status_code=500, detail=f"SZ extraction failed: {e}")
        finally:
            try:
                await sz_client.logout()
            except Exception:
                pass

        # Cache the snapshot for potential reuse
        sz_job_id = str(uuid.uuid4())
        await _save_snapshot(sz_job_id, snapshot)
        logger.info(f"Inline extraction for audit saved as {sz_job_id}: {snapshot.summary()}")

    else:
        raise HTTPException(
            status_code=400,
            detail="Provide sz_snapshot_job_id OR (sz_controller_id + zone_id)",
        )

    # ── R1 inventory: load existing or capture fresh ──
    r1_client = None
    tenant_id = None
    if request.r1_snapshot_job_id:
        r1_raw = await _get_r1_snapshot_raw(request.r1_snapshot_job_id)
        if not r1_raw:
            raise HTTPException(status_code=404, detail="R1 snapshot not found or expired")
        r1_inventory = R1VenueInventory.model_validate_json(r1_raw)
        r1_job_id = request.r1_snapshot_job_id
        # Create R1 client for full detail fetches if controller info available
        if request.r1_controller_id:
            try:
                r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
                r1_controller = db.query(Controller).filter(Controller.id == request.r1_controller_id).first()
                tenant_id = request.tenant_id or (r1_controller.r1_tenant_id if r1_controller else None)
            except Exception:
                pass  # Non-critical — audit works without full details
    else:
        # Capture fresh R1 snapshot
        if not request.r1_controller_id or not request.venue_id:
            raise HTTPException(
                status_code=400,
                detail="r1_controller_id and venue_id required when r1_snapshot_job_id is not provided",
            )
        r1_controller = validate_controller_access(request.r1_controller_id, current_user, db)
        if r1_controller.controller_type != "RuckusONE":
            raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

        tenant_id = request.tenant_id or r1_controller.r1_tenant_id
        if r1_controller.controller_subtype == "MSP" and not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

        r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
        try:
            r1_inventory = await capture_venue_inventory(r1_client, tenant_id, request.venue_id)
        except Exception as e:
            logger.exception(f"R1 snapshot failed during audit: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to capture R1 inventory: {e}")

        r1_job_id = str(uuid.uuid4())
        await _save_r1_snapshot(r1_job_id, r1_inventory)

    # ── Fetch full R1 network details for deep field comparison ──
    r1_network_details: Dict[str, Dict] = {}
    if r1_client is not None:
        for net in r1_inventory.wifi_networks:
            net_id = net.get("id")
            if net_id:
                try:
                    full_detail = await r1_client.networks.get_wifi_network_by_id(
                        net_id, tenant_id
                    )
                    if full_detail:
                        r1_network_details[net_id] = full_detail
                except Exception as e:
                    logger.debug(f"Could not fetch full detail for R1 network {net_id}: {e}")

    # ── Run audit ──
    report = run_migration_audit(
        snapshot=snapshot,
        r1_inventory=r1_inventory,
        sz_snapshot_job_id=sz_job_id,
        r1_snapshot_job_id=r1_job_id,
        r1_network_details=r1_network_details,
    )

    return report.model_dump()
