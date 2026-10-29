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

M1b (Destination venue):
  GET  /sz-migration/venue-draft/{job_id}     — create-form pre-fill + zone settings plan
  GET  /sz-migration/venue-tags               — tags already used on the tenant's venues
  POST /sz-migration/venues                   — create the destination venue
  POST /sz-migration/venue-config/plan        — stage: zone settings → venue (job; then /workflow/{id}/...)
  GET  /sz-migration/venue-config/{id}/report — that stage's apply + read-back results
  POST /sz-migration/ap-mapping/plan          — stage: AP Groups, AP membership, activations (job)
  GET  /sz-migration/ap-mapping/{id}/report   — that stage's mapping, activation and read-back results

M2b (R1 placement):
  POST /sz-migration/layout/{job_id}          — All AP Groups vs derived AP Groups, for given choices

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
import re
import logging
import time
import uuid
from datetime import datetime
from routers.tenant_scope import resolve_tenant_id
from typing import Dict, Any, Optional, List, Literal, Tuple

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field, SecretStr, model_validator
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from decorators import require_alpha, require_beta
from models.user import User
from models.controller import Controller
from models.sz_migration_session import SZMigrationSession
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client
from services.sz_migration.extractor import extract_zone_snapshot
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.r1_layout import build_layout
from services.sz_migration.mapper import map_all_wlans, wlan_vlans
from services.sz_migration.radius_choices import (
    RADIUS_SECRET_KEY, RADIUS_SECRET_TTL, r1_profile_summary, referenced_services, suggest,
)
from services.sz_migration.dpsk_choices import (
    MAX_DEVICES, MAX_PASSPHRASE_LENGTH, MIN_PASSPHRASE_LENGTH, PASSPHRASE_FORMATS,
    MAX_WORD_COUNT, MIN_WORD_COUNT,
    default_plan, load_r1_dpsk,
)
from services.sz_migration.auditor import run_audit as run_migration_audit
from services.sz_migration.auth_probe import collect_auth_facts
from services.sz_migration.audit_portal import KEY_CHECK_MAX_NETWORKS, check_portal_key
from services.sz_migration.venue_settings import fetch_r1_venue_settings
from services.sz_migration import audit_cache, venue_create
from services.sz_migration.audit_redaction import (
    CredentialLeakError, assert_clean, collect_secrets, scrub_report,
)
from services.r1_inventory import capture_venue_inventory
from schemas.sz_migration import SZMigrationSnapshot, ResolverResult
from schemas.r1_inventory import R1VenueInventory

# M4: Workflow engine imports
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.failsafe import fail_job
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.graph import DependencyGraph
from workflow.workflows.sz_to_r1_migration import SZtoR1MigrationWorkflow
from workflow.workflows.sz_venue_config import SZVenueConfigWorkflow
from workflow.workflows.sz_ap_mapping import SZApMappingWorkflow
from workflow.phases.sz_migration.ap_mapping import load_report as load_ap_mapping_report
from services.sz_migration.ap_mapping import plan_ap_mapping
from services.sz_migration.r1_layout import r1_group_name
from services import ap_group_ops
from workflow.phases.sz_migration.venue_config import load_stage_report
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sz-migration")

# Redis key prefixes and TTL
EXTRACTION_KEY_PREFIX = "sz_migration:extraction"
R1_SNAPSHOT_KEY_PREFIX = "sz_migration:r1_snapshot"
SNAPSHOT_TTL = 60 * 60 * 24  # 24 hours
# An extraction runs as a background task in the API process: a restart
# (deploy, dev reload) kills it without a word, leaving its state "running"
# forever. No progress for this long means it is dead.
EXTRACTION_STALE_SECONDS = 300
PROGRESS_HEARTBEAT_SECONDS = 20
INTERRUPTED_MESSAGE = "Extraction was interrupted (the server restarted while it ran) — run it again"


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
    state = {**state, "updated_at": datetime.utcnow().isoformat()}
    await redis.set(key, json.dumps(state, default=str), ex=SNAPSHOT_TTL)


async def _get_extraction_state(job_id: str) -> Optional[Dict[str, Any]]:
    """Get extraction job state from Redis; a dead "running" job reads as failed."""
    redis = await get_redis_client()
    key = f"{EXTRACTION_KEY_PREFIX}:{job_id}:state"
    raw = await redis.get(key)
    if raw:
        return _interrupted_if_stale(json.loads(raw))
    return None


def _interrupted_if_stale(state: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    if state.get("status") not in ("pending", "running"):
        return state
    last = state.get("updated_at") or state.get("started_at")
    try:
        age = ((now or datetime.utcnow()) - datetime.fromisoformat(last)).total_seconds()
    except (TypeError, ValueError):
        return state
    if age <= EXTRACTION_STALE_SECONDS:
        return state
    return {**state, "status": "failed", "error": INTERRUPTED_MESSAGE}


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


# ── Snapshot ownership ───────────────────────────────────────────────
#
# Snapshots (SZ extractions, R1 inventories) are addressed by job id, and
# until now any logged-in user holding an id could read one -- including the
# raw SZ extraction. Job ids travel (URLs, screenshots), so each snapshot
# now records the user who created it, and every job-id endpoint checks it.
# Someone else's snapshot is a 404, so its existence isn't confirmed.

def _owner_key(kind: str, job_id: str) -> str:
    return f"sz_migration:owner:{kind}:{job_id}"


async def _record_owner(kind: str, job_id: str, user_id: int) -> None:
    redis = await get_redis_client()
    await redis.set(_owner_key(kind, job_id), str(user_id), ex=SNAPSHOT_TTL)


async def _require_owner(kind: str, job_id: str, user: User, db: Session) -> None:
    """
    kind: "sz" (extraction job: state + snapshot) or "r1" (R1 inventory snapshot).

    Snapshots written before ownership was recorded fall back to the user's
    own migration sessions, which already tie job ids to a user -- so an
    in-progress wizard isn't locked out by the upgrade.
    """
    redis = await get_redis_client()
    owner = await redis.get(_owner_key(kind, job_id))
    if owner is not None:
        if owner == str(user.id):
            return
    else:
        # Older snapshots: the creating endpoints already kept a per-user job index...
        prefix = EXTRACTION_KEY_PREFIX if kind == "sz" else R1_SNAPSHOT_KEY_PREFIX
        if await redis.zscore(f"{prefix}:user:{user.id}:jobs", job_id) is not None:
            return
        # ...and migration sessions tie job ids to their user.
        column = (SZMigrationSession.extraction_job_id if kind == "sz"
                  else SZMigrationSession.r1_snapshot_job_id)
        if db.query(SZMigrationSession).filter(
            SZMigrationSession.user_id == user.id, column == job_id,
        ).first():
            return
    raise HTTPException(status_code=404, detail="Snapshot not found or expired")


async def _require_job_owner(job, user: User) -> None:
    """Migration workflow jobs: only their creator may read, confirm or graph them."""
    if job is None or getattr(job, "user_id", None) != user.id:
        raise HTTPException(status_code=404, detail="Job not found")


def _redacted_snapshot_json(raw: str, indent: Optional[int] = None) -> str:
    """Snapshot JSON for the browser or a download: passphrases become keyed
    fingerprints, other credentials a marker -- never the values."""
    return json.dumps(audit_cache.strip_credentials(json.loads(raw)), indent=indent)


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

        # The extractor calls on_progress synchronously. It used to be an
        # async function, so every call made a coroutine nobody awaited: the
        # page sat on "Initializing..." until the end, and a long extraction
        # (no updates for EXTRACTION_STALE_SECONDS) read as interrupted while
        # it was still running. Now the callback only records the latest
        # update and one writer task saves it, in order, coalescing bursts
        # and re-saving at least every PROGRESS_HEARTBEAT_SECONDS.
        started_at = datetime.utcnow().isoformat()
        latest: Dict[str, Any] = {"progress": {"phase": "starting", "message": "Initializing..."}, "seq": 0}
        changed = asyncio.Event()
        finished = asyncio.Event()

        def on_progress(phase: str, message: str, data: Dict[str, Any]):
            if phase == "complete":
                return   # announced below, once the snapshot is saved
            latest["progress"] = {"phase": phase, "message": message, "data": data}
            latest["seq"] += 1
            changed.set()

        async def write_progress():
            saved = -1
            while not finished.is_set():
                try:
                    await asyncio.wait_for(changed.wait(), timeout=PROGRESS_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    pass
                changed.clear()
                if finished.is_set():
                    break
                try:
                    p = latest["progress"]
                    await _save_extraction_state(job_id, {"status": "running", "started_at": started_at,
                                                          "progress": p})
                    if latest["seq"] != saved:
                        saved = latest["seq"]
                        await _publish_progress(job_id, p["phase"], p["message"], p.get("data") or {})
                except Exception as e:
                    logger.warning(f"Extraction {job_id}: progress write failed: {e}")


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
            writer = asyncio.create_task(write_progress())
            try:
                snapshot = await extract_zone_snapshot(sz_client, zone_id, on_progress=on_progress)
            finally:
                # Stop the writer before the final state, so it can't overwrite it.
                finished.set()
                changed.set()
                await writer

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
    await _record_owner("sz", job_id, current_user.id)

    # Save initial state
    await _save_extraction_state(job_id, {
        "status": "pending",
        "started_at": datetime.utcnow().isoformat(),
    })

    # Track job in user's extraction history, with what it read (the state
    # blob is rewritten by every progress save, so this lives beside it).
    redis = await get_redis_client()
    user_index_key = f"{EXTRACTION_KEY_PREFIX}:user:{current_user.id}:jobs"
    await redis.zadd(user_index_key, {job_id: time.time()})
    await redis.expire(user_index_key, SNAPSHOT_TTL)
    await redis.set(f"{EXTRACTION_KEY_PREFIX}:{job_id}:meta",
                    json.dumps({"controller_id": request.controller_id, "zone_id": request.zone_id}),
                    ex=SNAPSHOT_TTL)

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


@router.get("/extractions")
@require_alpha()
async def list_recent_extractions(
    controller_id: int,
    zone_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    The caller's own completed extractions of this zone on this controller that
    are still kept (SNAPSHOT_TTL), newest first -- so step 1 can reuse one
    instead of reading SZ again. Only the user's own jobs are listed.
    """
    validate_controller_access(controller_id, current_user, db)
    redis = await get_redis_client()
    since = time.time() - SNAPSHOT_TTL
    ids = await redis.zrevrangebyscore(f"{EXTRACTION_KEY_PREFIX}:user:{current_user.id}:jobs", "+inf", since)
    out = []
    for job_id in ids:
        job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
        try:
            await _require_owner("sz", job_id, current_user, db)   # the index is the user's own; belt and braces
        except HTTPException:
            continue
        state = await _get_extraction_state(job_id)
        if not state or state.get("status") != "completed":
            continue
        meta_raw = await redis.get(f"{EXTRACTION_KEY_PREFIX}:{job_id}:meta")
        meta = json.loads(meta_raw) if meta_raw else None
        if meta is not None:
            if meta.get("zone_id") != zone_id or meta.get("controller_id") != controller_id:
                continue
        else:
            # Older jobs have no meta: match on the snapshot's zone id.
            raw = await _get_snapshot_raw(job_id)
            if not raw or (json.loads(raw).get("zone") or {}).get("id") != zone_id:
                continue
        if not await redis.exists(f"{EXTRACTION_KEY_PREFIX}:{job_id}:snapshot"):
            continue
        out.append({"job_id": job_id, "completed_at": state.get("completed_at"),
                    "snapshot_summary": state.get("snapshot_summary") or {}})
    return {"extractions": out, "kept_hours": SNAPSHOT_TTL // 3600}


@router.get("/extract/{job_id}/status", response_model=ExtractionStatus)
@require_alpha()
async def get_extraction_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Poll extraction progress."""
    await _require_owner("sz", job_id, current_user, db)
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
    db: Session = Depends(get_db),
):
    """Return the SZMigrationSnapshot as JSON, credentials redacted."""
    await _require_owner("sz", job_id, current_user, db)
    raw = await _get_snapshot_raw(job_id)
    if not raw:
        # Check if extraction is still running
        state = await _get_extraction_state(job_id)
        if state and state.get("status") in ("pending", "running"):
            raise HTTPException(status_code=202, detail="Extraction still in progress")
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return Response(
        content=_redacted_snapshot_json(raw),
        media_type="application/json",
    )


@router.get("/snapshot/{job_id}/download")
@require_alpha()
async def download_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Download the SZMigrationSnapshot as a JSON file, credentials redacted
    (passphrases become keyed fingerprints -- still comparable, never readable).
    """
    await _require_owner("sz", job_id, current_user, db)
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
        content=_redacted_snapshot_json(raw, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/extract/{job_id}/sse")
@require_alpha()
async def extraction_sse(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    SSE stream for real-time extraction progress.
    Subscribes to Redis pub/sub for the extraction job.
    """
    await _require_owner("sz", job_id, current_user, db)
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
            # Cancellation-safe cleanup: close() ends the subscription
            # server-side, so skip the interruptible network unsubscribe and
            # shield the release so the pooled connection always returns to the
            # pool even while this generator is being cancelled on disconnect.
            cleanup = pubsub.aclose() if hasattr(pubsub, "aclose") else pubsub.close()
            try:
                await asyncio.shield(cleanup)
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
    tenant_id = resolve_tenant_id(controller, request.tenant_id)

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
    await _record_owner("r1", job_id, current_user.id)
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
    db: Session = Depends(get_db),
):
    """Return the R1VenueInventory as JSON, credentials redacted."""
    await _require_owner("r1", job_id, current_user, db)
    raw = await _get_r1_snapshot_raw(job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="R1 snapshot not found")

    return Response(
        content=_redacted_snapshot_json(raw),
        media_type="application/json",
    )


@router.get("/r1-snapshot/{job_id}/download")
@require_alpha()
async def download_r1_snapshot(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download the R1VenueInventory as a JSON file, credentials redacted."""
    await _require_owner("r1", job_id, current_user, db)
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
        content=_redacted_snapshot_json(raw, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ══════════════════════════════════════════════════════════════════════
# M1b: Destination venue — create from the zone, carry its settings
# ══════════════════════════════════════════════════════════════════════

class VenueAddress(BaseModel):
    addressLine: Optional[str] = None
    city: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    countryCode: Optional[str] = None
    timezone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CreateVenueRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    name: str = Field(..., min_length=2, max_length=venue_create.VENUE_NAME_MAX)
    description: Optional[str] = None
    address: VenueAddress
    tags: List[str] = Field(default_factory=list)


async def _owned_snapshot(job_id: str, user: User, db: Session) -> SZMigrationSnapshot:
    await _require_owner("sz", job_id, user, db)
    raw = await _get_snapshot_raw(job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
    return SZMigrationSnapshot.model_validate_json(raw)


def _r1_for(controller_id: int, tenant_id: Optional[str], user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")
    return create_r1_client_from_controller(controller_id, db), resolve_tenant_id(controller, tenant_id)


@router.get("/venue-draft/{sz_snapshot_job_id}")
@require_alpha()
async def get_venue_draft(
    sz_snapshot_job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create-form pre-fill from the zone, and the zone settings the Venue Configuration stage carries."""
    snapshot = await _owned_snapshot(sz_snapshot_job_id, current_user, db)
    zone = snapshot.zone.raw
    return {
        "draft": venue_create.venue_draft_from_zone(zone),
        "settings": venue_create.public_plan(venue_create.build_settings_plan(zone, snapshot.zone_ap_models)),
        # False on extractions made before per-model settings were read
        "models_read": bool(snapshot.zone_ap_models) or not snapshot.aps,
    }


class VenueConfigPlanRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: str
    sz_snapshot_job_id: str
    # Section keys; None = every section the zone sets.
    venue_settings: Optional[List[str]] = None


@router.post("/venue-config/plan")
@require_alpha()
async def create_venue_config_plan(
    request: VenueConfigPlanRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage: Venue Configuration. Creates the job and runs its plan phase;
    poll / confirm / monitor it with the usual /workflow/{job_id}/... endpoints.
    """
    await _owned_snapshot(request.sz_snapshot_job_id, current_user, db)
    controller = validate_controller_access(request.r1_controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="R1 controller must be RuckusONE")
    tenant_id = resolve_tenant_id(controller, request.tenant_id)

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=tenant_id),
    )
    job = await brain.create_job(
        workflow=SZVenueConfigWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.r1_controller_id,
        user_id=current_user.id,
        options={"sz_snapshot_job_id": request.sz_snapshot_job_id, "venue_settings": request.venue_settings},
        input_data={"sz_snapshot_job_id": request.sz_snapshot_job_id},
    )
    background_tasks.add_task(_run_migration_validation_background, job.id, request.r1_controller_id)
    return {"job_id": job.id, "status": "VALIDATING",
            "message": f"Planning venue configuration. Poll GET /sz-migration/workflow/{job.id}/plan"}


@router.get("/venue-config/{job_id}/report")
@require_alpha()
async def get_venue_config_report(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """The stage's outcome: job status, per-section apply results and the read-back verification."""
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
    await _require_job_owner(job, current_user)
    report = await load_stage_report(job_id)
    plan_sections = (job.global_phase_results.get("venue_config_plan") or {}).get("sections") or []
    return {
        "job_id": job_id,
        "status": job.status.value,
        "errors": job.errors,
        "before": plan_sections,
        "apply": report.get("apply") or [],
        "verify": report.get("verify") or [],
    }


class ApMappingPlanRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: str
    sz_snapshot_job_id: str
    layout_choices: Optional["LayoutChoices"] = None
    # Add SZ APs not in the venue into their groups (claims the serials for this tenant).
    add_new_aps: bool = False
    # The APs picked on the AP table: only these are moved / added. None = all moves, adds per add_new_aps.
    selected_serials: Optional[List[str]] = None
    # Duplicate AP names: "serial" (add as <name>_<serial>) or "keep"
    ap_name_policy: Literal["serial", "keep"] = "serial"
    # serial -> R1 name the operator typed for a duplicate-named AP
    ap_renames: Dict[str, str] = Field(default_factory=dict)
    # SZ had APs in the wrong AP Groups: send each AP to the R1 group its name points at
    place_by_name: bool = False
    # serial -> R1 AP Group name the operator typed; beats SZ and by-name
    ap_group_overrides: Dict[str, str] = Field(default_factory=dict)


@router.post("/ap-mapping/plan")
@require_alpha()
async def create_ap_mapping_plan(
    request: ApMappingPlanRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stage: AP Mapping. Creates the job and runs its plan phase; poll / confirm /
    monitor it with the usual /workflow/{job_id}/... endpoints.
    """
    await _owned_snapshot(request.sz_snapshot_job_id, current_user, db)
    controller = validate_controller_access(request.r1_controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="R1 controller must be RuckusONE")
    tenant_id = resolve_tenant_id(controller, request.tenant_id)

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=tenant_id),
    )
    choices = request.layout_choices.model_dump() if request.layout_choices else {}
    job = await brain.create_job(
        workflow=SZApMappingWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.r1_controller_id,
        user_id=current_user.id,
        options={"sz_snapshot_job_id": request.sz_snapshot_job_id, "layout_choices": choices,
                 "add_new_aps": request.add_new_aps, "selected_serials": request.selected_serials,
                 "ap_name_policy": request.ap_name_policy, "ap_renames": request.ap_renames,
                 "place_by_name": request.place_by_name, "ap_group_overrides": request.ap_group_overrides},
        input_data={"sz_snapshot_job_id": request.sz_snapshot_job_id},
    )
    background_tasks.add_task(_run_migration_validation_background, job.id, request.r1_controller_id)
    return {"job_id": job.id, "status": "VALIDATING",
            "message": f"Planning AP mapping. Poll GET /sz-migration/workflow/{job.id}/plan"}


@router.post("/ap-mapping/preview")
@require_alpha()
async def preview_ap_mapping(
    request: ApMappingPlanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Every SZ AP with its target R1 AP Group, where it is in R1 now and what
    mapping would do -- read-only (one fresh read of the venue), for the AP
    table. The job plans the same way with the operator's picks.
    """
    snapshot = await _owned_snapshot(request.sz_snapshot_job_id, current_user, db)
    r1_client, tenant = _r1_for(request.r1_controller_id, request.tenant_id, current_user, db)
    try:
        inv = await capture_venue_inventory(r1_client, tenant, request.venue_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read the venue from R1: {e}")
    choices = request.layout_choices.model_dump() if request.layout_choices else {}
    plan = plan_ap_mapping(snapshot, inv, choices, add_new_aps=request.add_new_aps,
                           selected=request.selected_serials, name_policy=request.ap_name_policy,
                           renames=request.ap_renames, place_by_name=request.place_by_name,
                           overrides=request.ap_group_overrides)
    return {
        "venue_name": inv.venue_name,
        "r1_ap_count": len(inv.aps),
        "counts": plan["counts"],
        "aps": plan["aps"],
        "groups": plan["groups"],
        # every named R1 AP Group in the venue (manual override suggestions), and typed names R1 lacks
        "r1_groups": sorted((g.get("name") for g in inv.ap_groups if (g.get("name") or "").strip()), key=str.casefold),
        "manual_missing": plan["manual_missing"],
        "warnings": plan["warnings"],
        "staged": plan["staged"],
        "activations": [{"name": a["name"], "status": a["status"], "error": a["error"]}
                        for a in plan["activations"] if a["status"] != "already"],
    }


class ApGroupCreateRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: str
    names: List[str]


@router.post("/ap-mapping/create-groups")
@require_alpha()
async def create_manual_ap_groups(
    request: ApGroupCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Find-or-create the (empty) AP Groups named in manual overrides, so the
    APs can be mapped into them. Nothing is activated on them.
    """
    names = list(dict.fromkeys(r1_group_name(n) for n in request.names if (n or "").strip()))
    if not names:
        raise HTTPException(status_code=400, detail="No AP Group names given")
    if len(names) > 200:
        raise HTTPException(status_code=400, detail="At most 200 AP Groups at a time")
    r1_client, tenant = _r1_for(request.r1_controller_id, request.tenant_id, current_user, db)
    ids, errors, created = await ap_group_ops.ensure_groups(r1_client, tenant, request.venue_id, names)
    return {"created": created, "existing": [n for n in names if n.casefold() in ids and n not in created],
            "errors": [{"name": n, "error": errors[n.casefold()]} for n in names if n.casefold() in errors]}


@router.get("/ap-mapping/{job_id}/report")
@require_alpha()
async def get_ap_mapping_report(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """The stage's outcome: plan counts, AP mapping, activations and the read-back verification."""
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
    await _require_job_owner(job, current_user)
    report = await load_ap_mapping_report(job_id)
    return {
        "job_id": job_id,
        "status": job.status.value,
        "errors": job.errors,
        "before": report.get("before") or {},
        "map": report.get("map") or {},
        "activate": report.get("activate") or [],
        "verify": report.get("verify") or {},
    }


@router.get("/venue-tags")
@require_alpha()
async def get_venue_tags(
    controller_id: int,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tags already used on the tenant's venues, for the tag picker."""
    r1_client, tenant = _r1_for(controller_id, tenant_id, current_user, db)
    try:
        return {"tags": await venue_create.list_venue_tags(r1_client, tenant)}
    except Exception as e:
        logger.warning(f"Could not list R1 venue tags: {e}")
        return {"tags": [], "error": "Could not list existing venue tags"}


@router.post("/venues")
@require_alpha()
async def create_destination_venue(
    request: CreateVenueRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create the destination venue in R1 (name, address, description, tags)."""
    r1_client, tenant = _r1_for(request.controller_id, request.tenant_id, current_user, db)
    address = {k: v for k, v in request.address.model_dump().items() if v not in (None, "")}
    body: Dict[str, Any] = {"name": request.name.strip(), "address": address}
    if request.description:
        body["description"] = request.description
    tags = sorted({t.strip() for t in request.tags if t and t.strip()})
    if tags:
        body["tags"] = tags
    try:
        return await venue_create.create_venue(r1_client, tenant, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"R1 venue create failed: {e}")
        raise HTTPException(status_code=502, detail=f"R1 refused the venue: {e}")


# ══════════════════════════════════════════════════════════════════════
# M2b: R1 placement — All AP Groups vs AP Groups, derived from SZ broadcast
# ══════════════════════════════════════════════════════════════════════

class LayoutChoices(BaseModel):
    all_ap_wlans: Optional[List[str]] = None      # None = the suggestion
    create_groups: List[str] = Field(default_factory=list)
    group_names: Dict[str, str] = Field(default_factory=dict)
    include_unbroadcast: List[str] = Field(default_factory=list)
    stage_unbroadcast: List[str] = Field(default_factory=list)
    pairing: Dict[str, str] = Field(default_factory=dict)   # mode: wlan_group | pattern; wlan_pattern; ap_pattern
    excluded_wlans: List[str] = Field(default_factory=list)  # not migrated at all (test / temp SSIDs...)


class LayoutRequest(BaseModel):
    r1_snapshot_job_id: Optional[str] = None
    choices: LayoutChoices = Field(default_factory=LayoutChoices)


ApMappingPlanRequest.model_rebuild()   # its layout_choices names LayoutChoices, defined just above


@router.post("/layout/{sz_snapshot_job_id}")
@require_alpha()
async def get_r1_layout(
    sz_snapshot_job_id: str,
    request: LayoutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Where every WLAN lands in R1 for the given choices: All AP Groups or the
    AP Groups derived from what each AP broadcasts in SZ. Recomputed on every
    change of choice on Review; the plan recomputes it the same way.
    """
    await _require_owner("sz", sz_snapshot_job_id, current_user, db)
    raw = await _get_snapshot_raw(sz_snapshot_job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
    inventory = None
    if request.r1_snapshot_job_id:
        await _require_owner("r1", request.r1_snapshot_job_id, current_user, db)
        r1_raw = await _get_r1_snapshot_raw(request.r1_snapshot_job_id)
        if r1_raw:
            inventory = R1VenueInventory.model_validate_json(r1_raw)
    snapshot = SZMigrationSnapshot.model_validate_json(raw)
    return build_layout(snapshot, inventory, request.choices.model_dump())


# ══════════════════════════════════════════════════════════════════════
# M2: WLAN Group Resolution + Security Type Mapping
# ══════════════════════════════════════════════════════════════════════

@router.post("/resolve/{job_id}")
@require_alpha()
async def resolve_snapshot(
    job_id: str,
    r1_snapshot_job_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Resolve WLAN Groups into per-AP-Group activations and map security types.

    Reads the SZ snapshot from Redis (produced by /extract), runs the resolver
    and mapper, and returns the combined result.
    """
    await _require_owner("sz", job_id, current_user, db)
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

    # R1 side of each WLAN's VLAN: an existing same-named network is reused as is
    inventory = None
    if r1_snapshot_job_id:
        await _require_owner("r1", r1_snapshot_job_id, current_user, db)
        r1_raw = await _get_r1_snapshot_raw(r1_snapshot_job_id)
        if r1_raw:
            inventory = R1VenueInventory.model_validate_json(r1_raw)
    wlan_by_id = {w.id: w for w in snapshot.wlans}

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
                **wlan_vlans(wlan_by_id[wlan_id],
                             inventory.network_by_name(wlan_by_id[wlan_id].name) if inventory else None),
            }
            for wlan_id, m in type_mappings.items()
        },
    }


# ══════════════════════════════════════════════════════════════════════
# M4: V2 Workflow Endpoints
# ══════════════════════════════════════════════════════════════════════


class RadiusDecision(BaseModel):
    """What to do with one SZ RADIUS service referenced by the zone."""
    action: Literal["create", "existing", "skip"]
    r1_profile_id: Optional[str] = Field(None, description="Required for 'existing'")
    # Write-only. SecretStr keeps it out of repr/logs; the plan endpoint moves
    # it to its own Redis key and it never enters job options.
    shared_secret: Optional[SecretStr] = Field(None, description="Only for 'create'")

    @model_validator(mode="after")
    def _consistent(self):
        if self.action == "existing" and not self.r1_profile_id:
            raise ValueError("'existing' needs r1_profile_id")
        if self.action != "create" and self.shared_secret is not None:
            raise ValueError("shared_secret only applies to 'create'")
        return self


class DpskIdentityGroupSpec(BaseModel):
    mode: Literal["new", "existing"]
    name: Optional[str] = None   # for "new"
    id: Optional[str] = None     # for "existing"


class DpskPoolSpec(BaseModel):
    """
    One DPSK service on Review. Field-level rules (lengths, R1 name clashes,
    a group already bound to a pool) are checked by validation, where they
    become plan conflicts the user can see and fix, rather than a 422 here.
    """
    mode: Literal["new", "existing"]
    existing_pool_id: Optional[str] = None
    name: Optional[str] = None
    identity_group: Optional[DpskIdentityGroupSpec] = None
    passphrase_length: int = 12
    passphrase_format: str = "KEYBOARD_FRIENDLY"
    # DICTIONARY_WORDS only: R1 requires wordCount (3-6) for that format.
    word_count: int = 4
    numeric_suffix_enabled: bool = False
    devices_per_passphrase: Optional[int] = None   # None = unlimited
    expiration_days: Optional[int] = None          # None = never


class MigrationPlanRequest(BaseModel):
    """Request to create a migration workflow plan."""
    sz_controller_id: int = Field(..., description="SZ controller ID (for snapshot reference)")
    r1_controller_id: int = Field(..., description="R1 controller ID (target)")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="R1 venue ID (target)")
    sz_snapshot_job_id: str = Field(..., description="SZ extraction job ID (from M0)")
    # Keyed by SZ service id. Absent = legacy behaviour (create every referenced service).
    radius_decisions: Optional[Dict[str, RadiusDecision]] = None
    # Keyed by SZ WLAN id: what happens to a WLAN whose RADIUS service is 'skip'.
    wlan_fallbacks: Dict[str, Literal["skip", "dpsk"]] = Field(default_factory=dict)
    # DPSK services by key, and which WLAN uses which. Absent = legacy
    # per-WLAN {wlan}-pool behaviour.
    dpsk_pools: Optional[Dict[str, DpskPoolSpec]] = None
    dpsk_assignments: Dict[str, str] = Field(default_factory=dict)
    # Placement chosen on Review. Absent = the suggestion, no AP Groups created.
    layout_choices: Optional[LayoutChoices] = None
    # Keyed by SZ WLAN id: external-auth WLANs (external DPSK, Enterprise) to
    # move onto R1's own DPSK service. Absent = keep them external.
    auth_conversions: Dict[str, Literal["dpsk"]] = Field(default_factory=dict)


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
        # The engine could not record its own death -- usually because
        # Redis is what broke. Without this the job stays RUNNING until
        # the stranded-job reaper clears it, up to five minutes later,
        # and with a generic message instead of this exception.
        await fail_job(None, job_id, e, source="SZ→R1")

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

@router.get("/radius-choices/{sz_snapshot_job_id}")
@require_alpha()
async def get_radius_choices(
    sz_snapshot_job_id: str,
    r1_controller_id: int,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    The RADIUS services this zone's WLANs reference, the R1 profiles they
    could map to, and a suggested choice for each -- for the Review step,
    before a plan is built.

    Only services referenced by WLANs in the extracted zone are listed: R1
    profiles are tenant-wide, but nothing outside this zone is migrated.
    Shared secrets are never returned (SZ doesn't expose them, and R1
    profiles are reduced to name/type/ip:port).
    """
    await _require_owner("sz", sz_snapshot_job_id, current_user, db)
    raw = await _get_snapshot_raw(sz_snapshot_job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
    snapshot = SZMigrationSnapshot.model_validate_json(raw)
    services = referenced_services(snapshot)

    r1_profiles: List[Dict[str, Any]] = []
    error = None
    if services:
        controller = validate_controller_access(r1_controller_id, current_user, db)
        if controller.controller_type != "RuckusONE":
            raise HTTPException(status_code=400, detail="R1 controller must be RuckusONE")
        resolved_tenant = resolve_tenant_id(controller, tenant_id)
        r1_client = create_r1_client_from_controller(r1_controller_id, db)
        try:
            rows = await asyncio.to_thread(
                r1_client.radius_profiles.get_radius_profiles, resolved_tenant
            )
            # Reduce immediately: these rows carry sharedSecret.
            r1_profiles = [r1_profile_summary(p) for p in rows if p.get("id")]
        except Exception as e:
            logger.warning(f"Could not list R1 RADIUS profiles: {type(e).__name__}")
            error = "Could not list RADIUS profiles in R1; 'Use existing' is unavailable"

    for s in services:
        s["suggestion"] = suggest(s, r1_profiles)

    return {
        "services": services,
        "r1_profiles": sorted(r1_profiles, key=lambda p: (p.get("name") or "").lower()),
        "error": error,
    }


@router.get("/dpsk-choices/{sz_snapshot_job_id}")
@require_alpha()
async def get_dpsk_choices(
    sz_snapshot_job_id: str,
    r1_controller_id: int,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    What the Review step needs to choose DPSK services: the tenant's pools
    (settings, identity groups, counts) and identity groups (which pool, if
    any, each already serves), plus a suggested new shared pool.

    Counts only -- no passphrase is read or returned.
    """
    await _require_owner("sz", sz_snapshot_job_id, current_user, db)
    raw = await _get_snapshot_raw(sz_snapshot_job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
    snapshot = SZMigrationSnapshot.model_validate_json(raw)

    controller = validate_controller_access(r1_controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="R1 controller must be RuckusONE")
    resolved_tenant = resolve_tenant_id(controller, tenant_id)
    r1_client = create_r1_client_from_controller(r1_controller_id, db)

    error = None
    try:
        pools, groups = await load_r1_dpsk(r1_client, resolved_tenant)
    except Exception as e:
        logger.warning(f"Could not list R1 DPSK pools/identity groups: {type(e).__name__}: {e}")
        pools, groups, error = {}, {}, "Could not list DPSK pools in R1; only 'Create new' is available"

    default = default_plan(snapshot.zone.name, [])["pools"]["shared"]
    return {
        "r1_pools": sorted(pools.values(), key=lambda p: (p.get("name") or "").lower()),
        "r1_identity_groups": sorted(groups.values(), key=lambda g: (g.get("name") or "").lower()),
        "default_pool": default,
        "formats": list(PASSPHRASE_FORMATS),
        "limits": {
            "word_count": [MIN_WORD_COUNT, MAX_WORD_COUNT],
            "passphrase_length": [MIN_PASSPHRASE_LENGTH, MAX_PASSPHRASE_LENGTH],
            "devices_per_passphrase": [1, MAX_DEVICES],
        },
        "error": error,
    }


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
    await _require_owner("sz", request.sz_snapshot_job_id, current_user, db)
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

    tenant_id = resolve_tenant_id(controller, request.tenant_id)

    # Build job options
    options = {
        "sz_snapshot_job_id": request.sz_snapshot_job_id,
        "sz_controller_id": request.sz_controller_id,
        "wlan_fallbacks": dict(request.wlan_fallbacks),
        "dpsk_assignments": dict(request.dpsk_assignments),
        "layout_choices": request.layout_choices.model_dump() if request.layout_choices else None,
        "auth_conversions": dict(request.auth_conversions),
    }
    if request.dpsk_pools is not None:
        options["dpsk_pools"] = {k: v.model_dump() for k, v in request.dpsk_pools.items()}
    # Decisions go into options WITHOUT their secrets: options are persisted
    # with the job and come back through job/status endpoints.
    secrets: Dict[str, str] = {}
    if request.radius_decisions is not None:
        options["radius_decisions"] = {}
        for sid, d in request.radius_decisions.items():
            secret = d.shared_secret.get_secret_value() if d.shared_secret else ""
            if secret:
                secrets[sid] = secret
            options["radius_decisions"][sid] = {
                "action": d.action,
                "r1_profile_id": d.r1_profile_id,
                "secret_provided": bool(secret),
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

    if secrets:
        key = RADIUS_SECRET_KEY.format(job_id=job.id)
        await redis_client.hset(key, mapping=secrets)
        await redis_client.expire(key, RADIUS_SECRET_TTL)

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
    await _require_job_owner(job, current_user)

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
    await _require_job_owner(job, current_user)

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


_TASK_PREFIX = re.compile(r"^Task [0-9a-f-]{36} failed: ")
_JOB_PREFIX = re.compile(r"^Job [0-9a-f-]{36}: ")
_COUNTS = re.compile(r"after \d+ conflicting writes")


def _error_key(err: str) -> str:
    """Identical failures read identically: R1 task / job ids and retry counts dropped."""
    e = _JOB_PREFIX.sub("", _TASK_PREFIX.sub("", str(err)))
    return _COUNTS.sub("after N conflicting writes", e)[:300]


@router.get("/workflow/{job_id}/outcome")
@require_alpha()
async def get_migration_outcome(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    What a migration job actually did, for the step that ran it: status,
    per-phase done/failed counts, and failures grouped by message (R1's task
    ids stripped so identical errors group) with the WLANs each hit. A PARTIAL
    job is not a pass -- this is what says so.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    job = await state_manager.get_job(job_id)
    await _require_job_owner(job, current_user)
    units = list((await state_manager.get_all_units(job_id)).values())
    staged_ids = set(((job.options or {}).get("layout_choices") or {}).get("stage_unbroadcast") or [])
    # Only this workflow's phases (units also carry ids of phases it doesn't run).
    known = {p.id for p in SZtoR1MigrationWorkflow.phases}
    phases: Dict[str, Dict[str, int]] = {p.id: {"done": 0, "failed": 0} for p in SZtoR1MigrationWorkflow.phases
                                         if p.per_unit}
    groups: Dict[Tuple[str, str], List[str]] = {}
    for u in units:
        for ph in u.completed_phases or []:
            if ph in phases:
                phases[ph]["done"] += 1
        for ph in u.failed_phases or []:
            if ph in phases:
                phases[ph]["failed"] += 1
        for ph, err in (u.phase_errors or {}).items():
            if ph in known:
                groups.setdefault((ph, _error_key(err)), []).append(u.unit_number)
    for ph, st in (job.global_phase_status or {}).items():
        status = st.value if hasattr(st, "value") else str(st)
        if status == "FAILED" and ph in known:
            phases.setdefault(ph, {"done": 0, "failed": 0})["failed"] += 1
            err = (job.global_phase_results.get(ph) or {}).get("error") if isinstance(
                job.global_phase_results.get(ph), dict) else None
            groups.setdefault((ph, _error_key(err or "global phase failed")), []).append("(whole job)")
    # AP Groups: a global phase that carries on past refusals, so its own
    # results say what wasn't created.
    apg = job.global_phase_results.get("create_ap_groups") or {}
    if isinstance(apg, dict) and not apg.get("skipped") and ("created" in apg or apg.get("failed")):
        phases["create_ap_groups"] = {"done": len(apg.get("ap_group_ids") or {}),
                                      "failed": len(apg.get("failed") or {})}
        for name, err in (apg.get("failed") or {}).items():
            groups.setdefault(("create_ap_groups", _error_key(err)), []).append(name)
    failures = [{"phase": ph, "message": msg, "count": len(names), "wlans": sorted(names)[:50]}
                for (ph, msg), names in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return {
        "job_id": job_id,
        "status": job.status.value,
        "units": len(units),
        "units_with_failures": sum(1 for u in units if u.failed_phases),
        "phases": phases,
        "failures": failures,
        "errors": job.errors,
        "duration_seconds": int((job.completed_at - (job.started_at or job.created_at)).total_seconds())
        if job.completed_at else None,
        # Created on purpose without activation (no SZ AP broadcasts them):
        # they're in R1 but not on any AP Group -- say so, so the count of
        # activated networks in R1 adds up.
        "created_only": sorted(u.unit_number for u in units if (u.plan.extra or {}).get("activation_mode") == "none"
                               and u.unit_id not in staged_ids),
        "staged": sorted(u.unit_number for u in units if (u.plan.extra or {}).get("activation_mode") == "none"
                         and u.unit_id in staged_ids),
        "activated": sum(1 for u in units if (u.plan.extra or {}).get("activation_mode") in ("all_ap_groups", "ap_groups")
                         and "activate_ssids" in (u.completed_phases or [])),
        "to_activate": sum(1 for u in units if (u.plan.extra or {}).get("activation_mode") in ("all_ap_groups", "ap_groups")),
        "networks": {
            "reused": sum(1 for u in units if (u.resolved.extra or {}).get("network_reused")),
            "created": sum(1 for u in units if "create_networks" in (u.completed_phases or [])
                           and not (u.resolved.extra or {}).get("network_reused")),
        },
        "ap_groups": {
            "created": int(apg.get("created") or 0) if isinstance(apg, dict) else 0,
            # Groups the plan found already in R1 never reach create_ap_groups; count them from the plan.
            "existing": sum(1 for a in (getattr(job.validation_result, "actions", None) or [])
                            if a.resource_type == "ap_group" and a.action == "reuse")
                        + max(0, len((apg or {}).get("ap_group_ids") or {}) - int((apg or {}).get("created") or 0)),
            "failed": sorted((apg.get("failed") or {}).keys()) if isinstance(apg, dict) else [],
        },
        "resources": {k: len(v or []) for k, v in (job.created_resources or {}).items()
                      if k in ("dpsk_pools", "identity_groups", "radius_profiles")},
    }


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
    await _require_job_owner(job, current_user)

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
@require_beta()
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

    # Capture cache (7 days, per SZ zone / R1 venue). With use_cache, a side
    # with a current capture is read from the cache unless its refresh flag
    # is set; a side without one is captured live and cached.
    use_cache: bool = True
    sz_refresh: bool = False
    r1_refresh: bool = False


@router.get("/audit-cache")
@require_beta()
async def get_audit_cache_status(
    sz_controller_id: Optional[int] = None,
    zone_id: Optional[str] = None,
    r1_controller_id: Optional[int] = None,
    venue_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What the capture cache holds for this zone / venue: age, time left, usable or stale."""
    resolved_tenant = None
    if sz_controller_id:
        validate_controller_access(sz_controller_id, current_user, db)
    if r1_controller_id:
        r1c = validate_controller_access(r1_controller_id, current_user, db)
        resolved_tenant = resolve_tenant_id(r1c, tenant_id)
    return await audit_cache.status(sz_controller_id, zone_id, r1_controller_id, resolved_tenant, venue_id)


@router.delete("/audit-cache")
@require_beta()
async def clear_audit_cache(
    side: Literal["sz", "r1", "both"] = "both",
    sz_controller_id: Optional[int] = None,
    zone_id: Optional[str] = None,
    r1_controller_id: Optional[int] = None,
    venue_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Drop the cached capture(s) for this zone / venue; the next audit captures live."""
    keys = []
    if side in ("sz", "both") and sz_controller_id and zone_id:
        validate_controller_access(sz_controller_id, current_user, db)
        keys.append(audit_cache.sz_key(sz_controller_id, zone_id))
    if side in ("r1", "both") and r1_controller_id and venue_id:
        r1c = validate_controller_access(r1_controller_id, current_user, db)
        keys.append(audit_cache.r1_key(r1_controller_id, resolve_tenant_id(r1c, tenant_id), venue_id))
    return {"cleared": await audit_cache.clear(*keys)}


class PortalKeyCheckRequest(BaseModel):
    # Every field defaulted: a missing-field 422 echoes the whole body, key included.
    r1_controller_id: Optional[int] = None
    tenant_id: Optional[str] = None
    network_ids: List[str] = []
    key: str = ""


@router.post("/audit/portal-key-check")
@require_beta()
async def portal_key_check(
    request: PortalKeyCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Does each R1 guest network hold this portal key? SZ's API never returns
    its signing key, so the user supplies it (from the SZ UI or the portal
    provider) and R1 is read live. Only match / differs comes back; the key
    is neither stored, logged nor returned.
    """
    key = request.key.strip()
    if not key or len(key) > 256:
        raise HTTPException(status_code=400, detail="Enter the portal key to check")
    ids = list(dict.fromkeys(i for i in request.network_ids if i))[:KEY_CHECK_MAX_NETWORKS]
    if not request.r1_controller_id or not ids:
        raise HTTPException(status_code=400, detail="r1_controller_id and network_ids are required")
    r1_controller = validate_controller_access(request.r1_controller_id, current_user, db)
    if r1_controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    sem = asyncio.Semaphore(8)

    async def one(net_id: str):
        async with sem:
            try:
                r = await asyncio.to_thread(
                    r1_client.get, f"/wifiNetworks/{net_id}",
                    **({"override_tenant_id": tenant_id} if r1_client.ec_type == "MSP" and tenant_id else {}),
                )
                net = r.json() if r.status_code == 200 else None
            except Exception as e:
                logger.warning(f"Portal key check: could not read R1 network {net_id}: {type(e).__name__}")
                net = None
            return {"network_id": net_id, **check_portal_key(key, net)}

    results = await asyncio.gather(*(one(i) for i in ids))
    fmt_ok = bool(re.fullmatch(r"[a-zA-Z0-9]{16}", key))
    return {"results": results,
            "format_note": "" if fmt_ok else "R1 integration keys are exactly 16 letters or digits — this one isn't"}


@router.post("/audit")
@require_beta()
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
    # Where each side's data came from (cache or live), shown on the report.
    data_sources: Dict[str, Any] = {}
    snapshot: Optional[SZMigrationSnapshot] = None

    # ── SZ snapshot: existing snapshot, else capture cache, else extract inline ──
    if request.sz_snapshot_job_id:
        # Path A: existing snapshot
        await _require_owner("sz", request.sz_snapshot_job_id, current_user, db)
        sz_raw = await _get_snapshot_raw(request.sz_snapshot_job_id)
        if not sz_raw:
            state = await _get_extraction_state(request.sz_snapshot_job_id)
            if state and state.get("status") in ("pending", "running"):
                raise HTTPException(status_code=202, detail="SZ extraction still in progress")
            raise HTTPException(status_code=404, detail="SZ snapshot not found or expired")
        snapshot = SZMigrationSnapshot.model_validate_json(sz_raw)
        sz_job_id = request.sz_snapshot_job_id
        data_sources["sz"] = {"cached": False, "source": "migration snapshot"}

    elif request.sz_controller_id and request.zone_id:
        sz_controller = validate_controller_access(request.sz_controller_id, current_user, db)
        if sz_controller.controller_type != "SmartZone":
            raise HTTPException(status_code=400, detail="SZ controller must be SmartZone")

        # Path B1: capture cache
        if request.use_cache and not request.sz_refresh:
            snapshot, sz_meta = await audit_cache.load_sz(request.sz_controller_id, request.zone_id)
            if snapshot is not None:
                sz_job_id = f"cache:{audit_cache.sz_key(request.sz_controller_id, request.zone_id)}"
                data_sources["sz"] = {"cached": True, **(sz_meta or {})}

    if snapshot is None and request.sz_controller_id and request.zone_id and not request.sz_snapshot_job_id:
        # Path B2: inline extraction (then cached)
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
        await _record_owner("sz", sz_job_id, current_user.id)
        await _save_snapshot(sz_job_id, snapshot)
        logger.info(f"Inline extraction for audit saved as {sz_job_id}: {snapshot.summary()}")
        data_sources["sz"] = {"cached": False, "captured_at": datetime.utcnow().isoformat() + "Z"}
        if request.use_cache:
            try:
                data_sources["sz"] = {"cached": False, **await audit_cache.save_sz(
                    request.sz_controller_id, request.zone_id, snapshot)}
            except Exception as e:
                logger.warning(f"Could not cache SZ capture: {type(e).__name__}: {e}")

    if snapshot is None:
        raise HTTPException(
            status_code=400,
            detail="Provide sz_snapshot_job_id OR (sz_controller_id + zone_id)",
        )

    # ── R1 inventory: load existing or capture fresh ──
    r1_client = None
    cached_r1: Optional[Dict[str, Any]] = None
    tenant_id = None
    if request.r1_snapshot_job_id:
        await _require_owner("r1", request.r1_snapshot_job_id, current_user, db)
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
                tenant_id = resolve_tenant_id(r1_controller, request.tenant_id) if r1_controller else request.tenant_id
            except Exception:
                pass  # Non-critical — audit works without full details
        data_sources["r1"] = {"cached": False, "source": "migration snapshot"}
    else:
        if not request.r1_controller_id or not request.venue_id:
            raise HTTPException(
                status_code=400,
                detail="r1_controller_id and venue_id required when r1_snapshot_job_id is not provided",
            )
        r1_controller = validate_controller_access(request.r1_controller_id, current_user, db)
        if r1_controller.controller_type != "RuckusONE":
            raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

        tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
        if r1_controller.controller_subtype == "MSP" and not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

        # Capture cache: everything the R1 side needs, so a cached run makes no R1 calls
        bundle, r1_meta = (None, None)
        if request.use_cache and not request.r1_refresh:
            bundle, r1_meta = await audit_cache.load_r1(request.r1_controller_id, tenant_id, request.venue_id)
        if bundle:
            r1_inventory = R1VenueInventory.model_validate(bundle["inventory"])
            r1_job_id = f"cache:{audit_cache.r1_key(request.r1_controller_id, tenant_id, request.venue_id)}"
            cached_r1 = bundle
            data_sources["r1"] = {"cached": True, **(r1_meta or {})}
        else:
            r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
            try:
                r1_inventory = await capture_venue_inventory(r1_client, tenant_id, request.venue_id)
            except Exception as e:
                logger.exception(f"R1 snapshot failed during audit: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to capture R1 inventory: {e}")
            r1_job_id = str(uuid.uuid4())
            await _record_owner("r1", r1_job_id, current_user.id)
            await _save_r1_snapshot(r1_job_id, r1_inventory)

    r1_network_details: Dict[str, Dict] = {}
    auth_facts: Optional[Dict[str, Any]] = None
    r1_venue_settings: Optional[Dict[str, Any]] = None

    if cached_r1 is not None:
        r1_network_details = cached_r1.get("network_details") or {}
        auth_facts = cached_r1.get("auth_facts")
        r1_venue_settings = cached_r1.get("venue_settings")
    elif r1_client is not None:
        # ── Full R1 network details for deep field comparison ──
        # Concurrent and off the event loop: the R1 client is synchronous,
        # and 600+ sequential GETs both took minutes and blocked the server.
        sem = asyncio.Semaphore(8)

        async def detail(net_id: str):
            async with sem:
                try:
                    d = await asyncio.to_thread(
                        r1_client.get, f"/wifiNetworks/{net_id}",
                        **({"override_tenant_id": tenant_id} if r1_client.ec_type == "MSP" and tenant_id else {}),
                    )
                    if d.status_code == 200:
                        r1_network_details[net_id] = d.json()
                except Exception as e:
                    logger.debug(f"Could not fetch full detail for R1 network {net_id}: {e}")

        await asyncio.gather(*(detail(n["id"]) for n in r1_inventory.wifi_networks if n.get("id")))

        # ── Auth-path facts: external RADIUS links, DPSK pools, counts ──
        try:
            auth_facts = await collect_auth_facts(
                r1_client, tenant_id,
                networks=r1_inventory.wifi_networks,
                identity_groups=r1_inventory.identity_groups,
            )
        except Exception as e:
            logger.exception(f"Auth-path probe failed during audit: {e}")

        # ── Venue-level Wi-Fi settings (as the EC tenant for MSP) ──
        try:
            r1_venue_settings = await fetch_r1_venue_settings(r1_client, tenant_id, r1_inventory.venue_id)
        except Exception as e:
            logger.exception(f"Venue settings read failed during audit: {e}")

        if not request.r1_snapshot_job_id:
            data_sources["r1"] = {"cached": False, "captured_at": datetime.utcnow().isoformat() + "Z"}
            if request.use_cache:
                try:
                    data_sources["r1"] = {"cached": False, **await audit_cache.save_r1(
                        request.r1_controller_id, tenant_id, request.venue_id, {
                            "inventory": r1_inventory.model_dump(mode="json"),
                            "network_details": r1_network_details,
                            "auth_facts": auth_facts,
                            "venue_settings": r1_venue_settings,
                        })}
                except Exception as e:
                    logger.warning(f"Could not cache R1 capture: {type(e).__name__}: {e}")

    # ── Run audit ──
    report = run_migration_audit(
        snapshot=snapshot,
        r1_inventory=r1_inventory,
        sz_snapshot_job_id=sz_job_id,
        r1_snapshot_job_id=r1_job_id,
        r1_network_details=r1_network_details,
        auth_facts=auth_facts,
        r1_venue_settings=r1_venue_settings,
    )
    report.data_sources = data_sources
    if auth_facts is None:
        report.warnings.append(
            "Authentication paths were not audited (no live R1 connection for this run)"
        )

    # ── Credential gate: nothing that is a passphrase or secret leaves ──
    secrets = collect_secrets(
        [w.raw for w in snapshot.wlans],
        [w.encryption for w in snapshot.wlans],
        [r.raw for r in snapshot.referenced_objects.values()],
        snapshot.zone.raw,          # mesh passphrase, AP login password
        r1_network_details,
        r1_inventory.wifi_networks,
    )
    body, withheld = scrub_report(report.model_dump(mode="json"), secrets)
    if withheld:
        body["warnings"].append(
            f"{withheld} value(s) withheld from this report because they matched a "
            "passphrase or shared secret"
        )
    try:
        assert_clean(body, secrets)
    except CredentialLeakError as e:
        # The message names the check, never the value.
        logger.error(f"Audit report withheld by credential gate: {e}")
        raise HTTPException(
            status_code=500,
            detail="Audit report withheld: it still contained credential material after "
                   "scrubbing. This is a bug in the audit, not in your configuration.",
        )

    return body
