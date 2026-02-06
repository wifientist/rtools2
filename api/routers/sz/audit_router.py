"""
SmartZone Audit Router

Provides comprehensive audit endpoints for SmartZone controllers.
Collects data on domains, zones, APs, WLANs, switches, and firmware.

Supports both sync (blocking) and async (background job) audit modes.
"""
import asyncio
import csv
import io
import logging
import uuid
from collections import Counter
from datetime import datetime
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from szapi.client import SZClient
from szapi.services.wlans import WlanService
from schemas.sz_audit import (
    SZAuditResult,
    BatchAuditRequest,
    BatchAuditResponse,
    ExportAuditRequest,
    ZoneAudit,
    DomainAudit,
    ApStatusBreakdown,
    ApGroupSummary,
    WlanGroupSummary,
    WlanSummary,
    SwitchGroupSummary,
    FirmwareDistribution,
    ModelDistribution,
    MatchCandidate,
    ZoneMatchCandidates,
    ControllerMatchCandidates,
)
from redis_client import get_redis_client
from workflow.v2.models import WorkflowJobV2, JobStatus, PhaseStatus, PhaseDefinitionV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher
from routers.sz.audit_workflow_definition import get_workflow_definition
from routers.sz.phases import initialize, fetch_switches, audit_zones, finalize
from routers.sz.phases.finalize import get_match_candidates
from routers.sz.zone_cache import ZoneCacheManager, RefreshMode
import re

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """
    Normalize a name for matching by:
    - Converting to lowercase
    - Removing common prefixes/suffixes (SG_, SW_, _switches, etc.)
    - Removing special characters and extra whitespace
    """
    if not name:
        return ""

    normalized = name.lower().strip()

    # Remove common switch-related prefixes/suffixes
    prefixes = ["sg_", "sw_", "switch_", "switches_", "icx_", "icx-"]
    suffixes = ["_switches", "_switch", "-switches", "-switch", "_sg", "-sg"]

    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
            break

    # Remove special characters, keep alphanumeric and spaces
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def match_switch_groups_to_zones(
    zones: List[ZoneAudit],
    switch_groups_raw: Dict[str, List[Dict[str, Any]]],
    switch_groups_with_counts: List[SwitchGroupSummary]
) -> None:
    """
    Match switch groups to zones by name similarity.
    Updates the zones in-place by populating matched_switch_groups.

    Matching strategy (in order of priority):
    1. Exact match (case-insensitive)
    2. Normalized exact match
    3. Contains match (zone name in switch group name or vice versa)
    4. Word overlap (at least 2 significant words match)

    Args:
        zones: List of ZoneAudit objects (will be modified in-place)
        switch_groups_raw: Dict of domain_id -> [switch_group_details from API]
        switch_groups_with_counts: List of SwitchGroupSummary with actual switch counts
    """
    # Build lookup for switch group counts by ID
    sg_counts_map = {sg.id: sg for sg in switch_groups_with_counts}

    # Build flat list of all switch groups with their domain info
    all_switch_groups = []
    for domain_id, sgs in switch_groups_raw.items():
        for sg in sgs:
            sg_id = sg.get("id")
            all_switch_groups.append({
                "id": sg_id,
                "name": sg.get("name", ""),
                "domain_id": domain_id,
                "normalized": normalize_name(sg.get("name", "")),
                "summary": sg_counts_map.get(sg_id)  # SwitchGroupSummary with counts
            })

    # Track which switch groups have been matched
    matched_sg_ids = set()

    def get_sg_summary(sg_info: Dict) -> SwitchGroupSummary:
        """Get SwitchGroupSummary, using cached counts if available."""
        if sg_info["summary"]:
            return sg_info["summary"]
        # Fallback if not in counts map
        return SwitchGroupSummary(
            id=sg_info["id"],
            name=sg_info["name"],
            switch_count=0,
            switches_online=0,
            switches_offline=0
        )

    for zone in zones:
        zone_name = zone.zone_name
        zone_domain_id = zone.domain_id
        zone_normalized = normalize_name(zone_name)
        zone_words = set(zone_normalized.split()) if zone_normalized else set()

        # Priority 1: Same domain, exact name match (case-insensitive)
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                if sg["name"].lower() == zone_name.lower():
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])
                    logger.debug(f"Zone '{zone_name}': Matched switch group '{sg['name']}' (exact match)")

        # Priority 2: Same domain, normalized exact match
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                if sg["normalized"] == zone_normalized and zone_normalized:
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])
                    logger.debug(f"Zone '{zone_name}': Matched switch group '{sg['name']}' (normalized match)")

        # Priority 3: Same domain, contains match
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                sg_lower = sg["name"].lower()
                zone_lower = zone_name.lower()
                # Check if one contains the other (minimum 3 chars to avoid false positives)
                if len(zone_lower) >= 3 and len(sg_lower) >= 3:
                    if zone_lower in sg_lower or sg_lower in zone_lower:
                        zone.matched_switch_groups.append(get_sg_summary(sg))
                        matched_sg_ids.add(sg["id"])
                        logger.debug(f"Zone '{zone_name}': Matched switch group '{sg['name']}' (contains match)")

        # Priority 4: Same domain, significant word overlap (at least 2 words)
        for sg in all_switch_groups:
            if sg["id"] in matched_sg_ids:
                continue
            if sg["domain_id"] == zone_domain_id:
                sg_words = set(sg["normalized"].split()) if sg["normalized"] else set()
                # Remove common filler words
                stop_words = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for"}
                zone_significant = zone_words - stop_words
                sg_significant = sg_words - stop_words
                common_words = zone_significant & sg_significant
                if len(common_words) >= 2:
                    zone.matched_switch_groups.append(get_sg_summary(sg))
                    matched_sg_ids.add(sg["id"])
                    logger.debug(f"Zone '{zone_name}': Matched switch group '{sg['name']}' (word overlap: {common_words})")

    # Log summary
    total_matched = len(matched_sg_ids)
    total_sgs = len(all_switch_groups)
    zones_with_matches = sum(1 for z in zones if z.matched_switch_groups)
    logger.info(f"Switch group matching: {total_matched}/{total_sgs} switch groups matched to {zones_with_matches}/{len(zones)} zones")

router = APIRouter(
    prefix="/sz",
    tags=["SmartZone Audit"]
)

# TTL for audit results in Redis (24 hours)
AUDIT_RESULT_TTL = 60 * 60 * 24


# ============================================================================
# Async Audit Response Models
# ============================================================================

class AsyncAuditRequest(BaseModel):
    """Request to start async audit"""
    controller_id: int
    refresh_mode: RefreshMode = RefreshMode.FULL  # full, incremental, cached_only, switches_only
    force_refresh_zones: List[str] = []  # Zone IDs to refresh even if cached (for incremental mode)


class AsyncAuditResponse(BaseModel):
    """Response when starting async audit"""
    job_id: str
    status: str
    message: str


class AuditJobStatus(BaseModel):
    """Status of an audit job"""
    job_id: str
    status: str
    controller_id: Optional[int] = None
    controller_name: Optional[str] = None
    current_phase: Optional[str] = None
    current_activity: Optional[str] = None  # Detailed progress message
    phases: List[Dict[str, Any]] = []
    progress: Dict[str, Any] = {}
    errors: List[str] = []
    created_at: Optional[str] = None
    refresh_mode: Optional[str] = None  # full, incremental, cached_only
    cache_stats: Optional[Dict[str, Any]] = None  # zones_from_cache, zones_refreshed, etc.
    api_stats: Optional[Dict[str, Any]] = None  # total_calls, avg_rate_per_second, etc.


class AuditHistoryItem(BaseModel):
    """Summary of a past audit job"""
    job_id: str
    controller_id: int
    controller_name: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    total_zones: Optional[int] = None
    total_aps: Optional[int] = None
    errors: List[str] = []


class AuditHistoryResponse(BaseModel):
    """Response for audit history"""
    audits: List[AuditHistoryItem]
    total: int


# ============================================================================
# Async Workflow Functions
# ============================================================================

async def run_audit_workflow_background(
    job: WorkflowJobV2,
    controller_id: int,
    db: Session
):
    """
    Background task to run audit workflow.

    Uses a simplified execution model (not full WorkflowEngine) since audit
    doesn't need task-level parallelism or retries - just sequential phases.
    """
    redis_client = None
    state_manager = None

    try:
        logger.info(f"Starting background audit workflow for job {job.id}")

        # Create workflow components
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        # Update job status to running
        job.status = JobStatus.RUNNING
        await state_manager.save_job(job)

        if event_publisher:
            await event_publisher.job_started(job)

        # Create SmartZone client
        sz_client = create_sz_client_from_controller(controller_id, db)

        # Get controller info
        controller = db.query(Controller).filter(Controller.id == controller_id).first()

        # Create zone cache manager
        zone_cache = ZoneCacheManager(redis_client, controller_id)

        # Get refresh mode from job input
        refresh_mode_str = job.input_data.get('refresh_mode', 'full')
        refresh_mode = RefreshMode(refresh_mode_str)

        # Initialize cache stats tracking in options (V2 doesn't have summary)
        job.options['cache_stats'] = {
            'refresh_mode': refresh_mode_str,
            'zones_from_cache': 0,
            'zones_refreshed': 0,
            'cache_hit_rate': 0
        }
        await state_manager.save_job(job)

        # Phase executors in order
        phases_config = [
            ('initialize', initialize.execute),
            ('fetch_switches', fetch_switches.execute),
            ('audit_zones', audit_zones.execute),
            ('finalize', finalize.execute),
        ]

        # Shared context for all phases (results from previous phases)
        phase_results = {}

        # Helper to update activity message (callable from phases)
        async def update_activity(message: str):
            """Update the current activity message for frontend display."""
            job.options['current_activity'] = message
            # Also update API stats while we're at it
            job.options['api_stats'] = sz_client.get_api_stats()
            await state_manager.save_job(job)

        try:
            await sz_client.login()
        except ValueError as e:
            # Handle auth failures gracefully without noisy traceback
            error_msg = str(e)
            logger.warning(f"Audit workflow {job.id} - authentication failed: {error_msg}")
            job.status = JobStatus.FAILED
            job.errors.append(f"Authentication failed: {error_msg}")
            job.options['current_activity'] = "Authentication failed"
            await state_manager.save_job(job)
            return

        try:
            for phase_id, executor_func in phases_config:
                # Check for cancellation before each phase
                if await state_manager.is_cancelled(job.id):
                    logger.info(f"🛑 Audit job {job.id} cancelled - stopping before phase {phase_id}")
                    job.status = JobStatus.CANCELLED
                    job.errors.append("Audit cancelled by user")
                    job.options['current_activity'] = "Cancelled"
                    await state_manager.save_job(job)
                    return

                # Find phase definition
                phase_def = job.get_phase_definition(phase_id)
                phase_name = phase_def.name if phase_def else phase_id

                logger.info(f"▶️  Executing phase {phase_id} ({phase_name})")

                # Update phase status
                job.global_phase_status[phase_id] = PhaseStatus.RUNNING
                job.options['current_phase_id'] = phase_id
                job.options['current_activity'] = f"Starting {phase_name}..."
                await state_manager.save_job(job)

                if event_publisher:
                    await event_publisher.publish_event(job.id, "phase_started", {
                        "phase_id": phase_id,
                        "phase_name": phase_name,
                    })

                try:
                    # Get force_refresh_zones from input_data
                    force_refresh_zones = set(job.input_data.get('force_refresh_zones', []))

                    # Build context for this phase (not serialized - passed directly)
                    context = {
                        'job_id': job.id,
                        'controller_id': controller_id,
                        'sz_client': sz_client,
                        'controller': controller,
                        'redis_client': redis_client,
                        'phase_results': phase_results,  # Results from previous phases
                        'options': job.options,
                        'input_data': job.input_data,
                        'update_activity': update_activity,  # Progress callback
                        'zone_cache': zone_cache,  # Zone caching manager
                        'refresh_mode': refresh_mode,  # RefreshMode enum
                        'force_refresh_zones': force_refresh_zones,  # Zone IDs to always refresh
                        'job': job,  # For updating cache stats
                        'state_manager': state_manager,  # For saving job updates
                    }

                    # Execute phase
                    tasks = await executor_func(context)

                    # Extract output from tasks
                    phase_output = {}
                    for task in tasks:
                        if task.output_data:
                            phase_output.update(task.output_data)

                    # Store result for subsequent phases
                    phase_results[phase_id] = phase_output

                    # Update phase status
                    job.global_phase_status[phase_id] = PhaseStatus.COMPLETED
                    job.global_phase_results[phase_id] = {
                        'tasks': [t.model_dump() if hasattr(t, 'model_dump') else t for t in tasks],
                        'output': phase_output,
                    }
                    # Update API stats after each phase
                    job.options['api_stats'] = sz_client.get_api_stats()
                    await state_manager.save_job(job)

                    if event_publisher:
                        await event_publisher.publish_event(job.id, "phase_completed", {
                            "phase_id": phase_id,
                            "phase_name": phase_name,
                        })

                    logger.info(f"✅ Phase {phase_id} completed")

                except Exception as e:
                    logger.exception(f"Phase {phase_id} failed: {str(e)}")
                    job.global_phase_status[phase_id] = PhaseStatus.FAILED
                    job.global_phase_results[phase_id] = {'error': str(e)}
                    job.errors.append(f"Phase {phase_name} failed: {str(e)}")
                    await state_manager.save_job(job)

                    # Check if phase is critical
                    is_critical = phase_def.critical if phase_def else True
                    if is_critical:
                        raise  # Stop workflow on critical phase failure

            # Workflow complete
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            job.options['current_phase_id'] = None
            await state_manager.save_job(job)

            if event_publisher:
                await event_publisher.job_completed(job)

            logger.info(f"✅ Audit workflow {job.id} completed successfully")

        finally:
            # Always clean up the SmartZone client
            try:
                await sz_client.logout()
                await sz_client.client.aclose()
            except Exception:
                pass  # Ignore cleanup errors

    except ValueError as e:
        # Handle expected errors (connection issues, API errors) without noisy traceback
        error_msg = str(e)
        logger.warning(f"Audit workflow {job.id} failed: {error_msg}")
        try:
            if state_manager:
                job.status = JobStatus.FAILED
                if error_msg not in job.errors:
                    job.errors.append(error_msg)
                await state_manager.save_job(job)
        except Exception as save_error:
            logger.error(f"Failed to save error state: {save_error}")

    except Exception as e:
        # Unexpected errors - log full traceback for debugging
        logger.exception(f"Audit workflow {job.id} failed unexpectedly: {str(e)}")
        try:
            if state_manager:
                job.status = JobStatus.FAILED
                if str(e) not in job.errors:
                    job.errors.append(str(e))
                await state_manager.save_job(job)
        except Exception as save_error:
            logger.error(f"Failed to save error state: {save_error}")


# ============================================================================
# Async Audit Endpoints
# ============================================================================

@router.post("/audit/async", response_model=AsyncAuditResponse)
async def start_async_audit(
    request: AsyncAuditRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> AsyncAuditResponse:
    """
    Start an asynchronous audit of a SmartZone controller.

    Returns immediately with a job_id. Use /audit/jobs/{job_id}/status
    to poll for progress and /audit/jobs/{job_id}/result to get results.

    Results are stored in Redis for 24 hours.
    """
    # Validate access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Controller {controller.name} is not a SmartZone controller"
        )

    # Create job ID
    job_id = str(uuid.uuid4())

    # Get workflow definition and create V2 phase definitions
    workflow_def = get_workflow_definition()
    phase_definitions = [
        PhaseDefinitionV2(
            id=phase_def.id,
            name=phase_def.name,
            depends_on=phase_def.dependencies,
            executor=phase_def.executor,
            critical=phase_def.critical,
            per_unit=False,  # Audit is not per-unit
            skip_if=phase_def.skip_condition,
        )
        for phase_def in workflow_def.phases
    ]

    # Initialize global phase status
    global_phase_status = {phase_def.id: PhaseStatus.PENDING for phase_def in workflow_def.phases}

    # Create WorkflowJobV2
    job = WorkflowJobV2(
        id=job_id,
        workflow_name="sz_audit",
        user_id=current_user.id,
        controller_id=request.controller_id,
        input_data={
            'controller_id': request.controller_id,
            'controller_name': controller.name,
            'refresh_mode': request.refresh_mode.value,
            'force_refresh_zones': request.force_refresh_zones  # Zone IDs to always refresh
        },
        phase_definitions=phase_definitions,
        global_phase_status=global_phase_status,
    )

    # Save job to Redis
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    await state_manager.save_job(job)

    # Add to user-specific audit history index (sorted by created_at timestamp)
    user_index_key = f"sz_audit:user:{current_user.id}:jobs"
    await redis_client.zadd(user_index_key, {job_id: job.created_at.timestamp()})
    # Set TTL on user index (7 days - longer than individual jobs to allow listing)
    await redis_client.expire(user_index_key, 60 * 60 * 24 * 7)

    logger.info(f"Created async audit job {job_id} for controller {controller.name}")

    # Start background task
    background_tasks.add_task(
        run_audit_workflow_background,
        job,
        request.controller_id,
        db
    )

    return AsyncAuditResponse(
        job_id=job_id,
        status="RUNNING",
        message=f"Audit started for {controller.name}. Poll /audit/jobs/{job_id}/status for progress."
    )


@router.get("/audit/jobs/{job_id}/status", response_model=AuditJobStatus)
async def get_audit_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user)
) -> AuditJobStatus:
    """
    Get the status of an audit job.

    Returns current phase, progress, and any errors.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Verify user owns this job
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Build phase info from V2 structure
    phases_info = []
    for p in job.phase_definitions:
        status = job.global_phase_status.get(p.id, PhaseStatus.PENDING)
        result = job.global_phase_results.get(p.id, {})
        tasks = result.get('tasks', [])
        phases_info.append({
            'id': p.id,
            'name': p.name,
            'status': status.value if hasattr(status, 'value') else str(status),
            'tasks_total': len(tasks),
            'tasks_completed': sum(1 for t in tasks if isinstance(t, dict) and t.get('status') == 'COMPLETED')
        })

    # Calculate progress with weighted phases
    # Phase weights: initialize=5%, fetch_switches=10%, audit_zones=75%, finalize=10%
    phase_weights = {
        'initialize': 5,
        'fetch_switches': 10,
        'audit_zones': 75,
        'finalize': 10
    }

    total_phases = len(job.phase_definitions)
    completed_phases = sum(1 for p in job.phase_definitions if job.global_phase_status.get(p.id) == PhaseStatus.COMPLETED)

    # Calculate weighted percent
    percent = 0
    for p in job.phase_definitions:
        status = job.global_phase_status.get(p.id, PhaseStatus.PENDING)
        weight = phase_weights.get(p.id, 25)  # Default 25% if unknown phase
        if status == PhaseStatus.COMPLETED:
            percent += weight
        elif status == PhaseStatus.RUNNING:
            # For audit_zones, use zone-level progress
            if p.id == 'audit_zones':
                zone_progress = job.options.get('zone_progress', {})
                zones_total = zone_progress.get('total', 0)
                zones_completed = zone_progress.get('completed', 0)
                if zones_total > 0:
                    percent += int(weight * zones_completed / zones_total)
            # For other phases, count as 50% done if running
            else:
                percent += weight // 2

    progress = {
        'phases_total': total_phases,
        'phases_completed': completed_phases,
        'percent': min(percent, 100),  # Cap at 100%
        'zone_progress': job.options.get('zone_progress')  # Include zone details if available
    }

    return AuditJobStatus(
        job_id=job_id,
        status=job.status.value if hasattr(job.status, 'value') else str(job.status),
        controller_id=job.input_data.get('controller_id'),
        controller_name=job.input_data.get('controller_name'),
        current_phase=job.options.get('current_phase_id'),
        current_activity=job.options.get('current_activity'),  # Detailed progress message
        phases=phases_info,
        progress=progress,
        errors=job.errors,
        created_at=job.created_at.isoformat() if job.created_at else None,
        refresh_mode=job.input_data.get('refresh_mode', 'full'),
        cache_stats=job.options.get('cache_stats'),  # zones_from_cache, zones_refreshed
        api_stats=job.options.get('api_stats')  # total_calls, avg_rate
    )


@router.post("/audit/jobs/{job_id}/cancel")
async def cancel_audit_job(
    job_id: str,
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Cancel a running audit job.

    The job will stop at the next phase boundary.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Verify user owns this job
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if job is in a cancellable state
    if job.status not in [JobStatus.PENDING, JobStatus.RUNNING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in {job.status} state"
        )

    # Set cancellation flag
    await state_manager.set_cancelled(job_id)

    logger.info(f"Audit job {job_id} cancellation requested by user {current_user.id}")

    return {
        "job_id": job_id,
        "status": "cancelling",
        "message": "Cancellation requested. Job will stop at the next phase."
    }


class CacheStatusResponse(BaseModel):
    """Cache status for a controller"""
    controller_id: int
    has_cache: bool
    cached_zones: int
    last_audit_time: Optional[str] = None
    cache_age_minutes: Optional[int] = None
    zone_count: Optional[int] = None
    # Switch group match info
    zones_with_switch_matches: int = 0
    total_switch_groups_matched: int = 0


@router.get("/audit/cache/{controller_id}")
async def get_cache_status(
    controller_id: int,
    current_user: User = Depends(get_current_user)
) -> CacheStatusResponse:
    """
    Get cache status for a controller.

    Returns information about cached audit data availability.
    """
    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    stats = await zone_cache.get_cache_stats()
    meta = await zone_cache.get_cache_meta()

    # Calculate cache age if we have last audit time
    cache_age_minutes = None
    if stats.get('last_audit_time'):
        try:
            from datetime import datetime
            last_audit = datetime.fromisoformat(stats['last_audit_time'])
            age_seconds = (datetime.utcnow() - last_audit).total_seconds()
            cache_age_minutes = int(age_seconds / 60)
        except Exception:
            pass

    # Count zones with switch group matches
    zones_with_switch_matches = 0
    total_switch_groups_matched = 0
    if meta and meta.get('zone_ids'):
        cached_zones = await zone_cache.get_cached_zones(meta['zone_ids'])
        for zone_data in cached_zones.values():
            matches = zone_data.get('matched_switch_groups', [])
            if matches:
                zones_with_switch_matches += 1
                total_switch_groups_matched += len(matches)

    return CacheStatusResponse(
        controller_id=controller_id,
        has_cache=stats.get('has_cache', False),
        cached_zones=stats.get('cached_zones', 0),
        last_audit_time=stats.get('last_audit_time'),
        cache_age_minutes=cache_age_minutes,
        zone_count=meta.get('zone_count') if meta else None,
        zones_with_switch_matches=zones_with_switch_matches,
        total_switch_groups_matched=total_switch_groups_matched
    )


@router.delete("/audit/cache/{controller_id}")
async def clear_cache(
    controller_id: int,
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Clear cached audit data for a controller.

    Use this to force a full refresh on the next audit.
    """
    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    deleted = await zone_cache.invalidate_all()

    logger.info(f"User {current_user.id} cleared cache for controller {controller_id}: {deleted} keys deleted")

    return {
        "controller_id": controller_id,
        "keys_deleted": deleted,
        "message": f"Cache cleared. {deleted} cached items removed."
    }


class CachedZoneInfo(BaseModel):
    """Summary info about a cached zone"""
    zone_id: str
    zone_name: str
    domain_name: str
    cached_at: Optional[str] = None
    ap_count: int = 0
    wlan_count: int = 0


@router.get("/audit/cache/{controller_id}/zones")
async def list_cached_zones(
    controller_id: int,
    current_user: User = Depends(get_current_user)
) -> List[CachedZoneInfo]:
    """
    List all cached zones for a controller.

    Returns zone summaries for the UI to display zone selection
    when running incremental audits with force-refresh.
    """
    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    zones = await zone_cache.list_cached_zones()

    return [CachedZoneInfo(**z) for z in zones]


@router.get("/audit/result/{controller_id}")
async def get_audit_result_from_cache(
    controller_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> SZAuditResult:
    """
    Get audit result from cache.

    This is the primary endpoint for retrieving audit data.
    All refresh modes (full, incremental, switches_only) update the cache,
    and this endpoint reads from that cache to return the result.

    Returns 404 if no cached data exists for the controller.
    """
    # Validate controller access
    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")
    if controller.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    # Check if cache exists
    stats = await zone_cache.get_cache_stats()
    if not stats.get('has_cache'):
        raise HTTPException(
            status_code=404,
            detail="No cached data available. Run an audit first."
        )

    # Get cache metadata and zones
    meta = await zone_cache.get_cache_meta()
    if not meta or not meta.get('zone_ids'):
        raise HTTPException(
            status_code=404,
            detail="Cache metadata not found. Run a new audit."
        )

    # Load all cached zones
    cached_zones = await zone_cache.get_cached_zones(meta['zone_ids'])
    if not cached_zones:
        raise HTTPException(
            status_code=404,
            detail="No zones found in cache. Run a new audit."
        )

    # Build ZoneAudit objects from cached data
    all_zones_audit = []
    for zone_id, zone_data in cached_zones.items():
        # Remove cache metadata before converting
        zone_data.pop('_cached_at', None)
        try:
            all_zones_audit.append(ZoneAudit(**zone_data))
        except Exception as e:
            logger.warning(f"Failed to parse cached zone {zone_id}: {e}")
            continue

    # Derive domains from zones
    domain_map = {}
    for zone in all_zones_audit:
        if zone.domain_id and zone.domain_id not in domain_map:
            domain_map[zone.domain_id] = {
                'id': zone.domain_id,
                'name': zone.domain_name or 'Unknown Domain'
            }
    domains_raw = list(domain_map.values())

    # Load cached switch groups (ALL switch groups, not just matched ones)
    cached_sg_data = await zone_cache.get_cached_switch_groups()
    all_switch_groups_by_domain = cached_sg_data.get('switch_groups', {}) if cached_sg_data else {}

    # Helper to convert cached switch group dict to SwitchGroupSummary
    def to_sg_summary(sg: Dict) -> SwitchGroupSummary:
        return SwitchGroupSummary(
            id=sg.get('id'),
            name=sg.get('name'),
            switch_count=sg.get('switch_count', 0),
            switches_online=sg.get('switches_online', 0),
            switches_offline=sg.get('switches_offline', 0),
            firmware_versions=[
                FirmwareDistribution(version=fv.get('version'), count=fv.get('count', 0))
                for fv in sg.get('firmware_versions', [])
            ]
        )

    # Build flat list of ALL switch groups (deduped) for firmware aggregation
    all_switch_groups_flat = []
    seen_sg_ids_global = set()
    for domain_id_key, groups in all_switch_groups_by_domain.items():
        for sg in groups:
            sg_id = sg.get('id')
            if sg_id and sg_id not in seen_sg_ids_global:
                seen_sg_ids_global.add(sg_id)
                all_switch_groups_flat.append(to_sg_summary(sg))

    # Build domain audit objects
    domains_audit = []
    for domain in domains_raw:
        domain_id = domain.get('id')
        domain_name = domain.get('name', 'Unknown')

        # Get zones for this domain
        domain_zones = [z for z in all_zones_audit if z.domain_id == domain_id]

        # Aggregate stats from zones
        total_aps = sum(z.ap_status.total for z in domain_zones)
        total_wlans = sum(z.wlan_count for z in domain_zones)

        # Get switch groups for this domain from cache
        # First check for domain-specific groups
        domain_switch_groups_raw = list(all_switch_groups_by_domain.get(domain_id, []))

        # If no domain-specific groups, check synthetic keys (but only for first domain to avoid dups)
        if not domain_switch_groups_raw and domain == domains_raw[0]:
            for special_key in ['_all_', '_prefetched_', '_cached_']:
                if special_key in all_switch_groups_by_domain:
                    domain_switch_groups_raw.extend(all_switch_groups_by_domain[special_key])

        # Convert to SwitchGroupSummary objects (dedup by id)
        seen_sg_ids = set()
        switch_groups = []
        for sg in domain_switch_groups_raw:
            sg_id = sg.get('id')
            if sg_id and sg_id not in seen_sg_ids:
                seen_sg_ids.add(sg_id)
                switch_groups.append(to_sg_summary(sg))

        total_switches = sum(sg.switch_count for sg in switch_groups)

        domain_audit = DomainAudit(
            domain_id=domain_id,
            domain_name=domain_name,
            zone_count=len(domain_zones),
            total_aps=total_aps,
            total_wlans=total_wlans,
            switch_groups=switch_groups,
            total_switches=total_switches,
            children=[]
        )
        domains_audit.append(domain_audit)

    # Aggregate global stats
    total_aps = sum(z.ap_status.total for z in all_zones_audit)
    total_wlans = sum(z.wlan_count for z in all_zones_audit)
    total_switches = sum(d.total_switches for d in domains_audit)

    # Aggregate AP models
    model_counter = Counter()
    for zone in all_zones_audit:
        for md in zone.ap_model_distribution:
            model_counter[md.model] += md.count
    ap_model_summary = [
        ModelDistribution(model=m, count=c)
        for m, c in model_counter.most_common()
    ]

    # Aggregate AP firmware
    firmware_counter = Counter()
    for zone in all_zones_audit:
        for fd in zone.ap_firmware_distribution:
            firmware_counter[fd.version] += fd.count
    ap_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in firmware_counter.most_common()
    ]

    # Aggregate switch firmware from all switch groups
    switch_firmware_counter = Counter()
    for sg in all_switch_groups_flat:
        for fd in sg.firmware_versions:
            switch_firmware_counter[fd.version] += fd.count
    switch_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in switch_firmware_counter.most_common()
    ]

    # Aggregate WLAN types
    wlan_type_counter = Counter()
    for zone in all_zones_audit:
        for wlan_type, count in zone.wlan_type_breakdown.items():
            wlan_type_counter[wlan_type] += count
    wlan_type_summary = dict(wlan_type_counter)

    # Build final result
    result = SZAuditResult(
        controller_id=controller.id,
        controller_name=controller.name,
        host=controller.sz_host,
        timestamp=datetime.fromisoformat(stats['last_audit_time']) if stats.get('last_audit_time') else datetime.utcnow(),
        cluster_ip=meta.get('cluster_ip'),
        controller_firmware=meta.get('controller_firmware'),
        domains=domains_audit,
        zones=all_zones_audit,
        total_domains=len(domains_audit),
        total_zones=len(all_zones_audit),
        total_aps=total_aps,
        total_wlans=total_wlans,
        total_switches=total_switches,
        ap_model_summary=ap_model_summary,
        ap_firmware_summary=ap_firmware_summary,
        switch_firmware_summary=switch_firmware_summary,
        wlan_type_summary=wlan_type_summary,
        error=None,
        partial_errors=[]
    )

    return result


class SwitchGroupMappingInfo(BaseModel):
    """Switch group info for mapping (minimal required data)"""
    id: str
    name: str
    switch_count: int = 0
    switches_online: int = 0
    switches_offline: int = 0


class BulkMappingUpdate(BaseModel):
    """Bulk update zone-to-switchgroup mappings"""
    # zone_id -> switch group info (None to clear)
    mappings: Dict[str, Optional[SwitchGroupMappingInfo]]


@router.put("/audit/cache/{controller_id}/mappings")
async def update_zone_mappings(
    controller_id: int,
    update: BulkMappingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Update zone-to-switchgroup mappings in cache.

    This endpoint allows the frontend to persist manual mapping changes
    to the backend cache, ensuring consistency across sessions.
    """
    # Validate controller access
    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")
    if controller.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    # Get cache metadata
    meta = await zone_cache.get_cache_meta()
    if not meta or not meta.get('zone_ids'):
        raise HTTPException(
            status_code=404,
            detail="No cached data available. Run an audit first."
        )

    # Track updates
    updated_zones = 0
    errors = []

    for zone_id, switch_group_info in update.mappings.items():
        try:
            # Get cached zone
            cached = await zone_cache.get_cached_zone(zone_id)
            if not cached:
                errors.append(f"Zone {zone_id} not found in cache")
                continue

            if switch_group_info is None:
                # Clear mapping - remove user_set flag as well
                cached['matched_switch_groups'] = []
                cached.pop('user_set_mapping', None)
            else:
                # Set mapping with full switch group info
                # Mark as user_set so auto-matcher doesn't overwrite
                cached['matched_switch_groups'] = [{
                    'id': switch_group_info.id,
                    'name': switch_group_info.name,
                    'switch_count': switch_group_info.switch_count,
                    'switches_online': switch_group_info.switches_online,
                    'switches_offline': switch_group_info.switches_offline,
                    'firmware_versions': [],
                    'user_set': True
                }]
                cached['user_set_mapping'] = True

            # Save back to cache
            await zone_cache.cache_zone(zone_id, cached)
            updated_zones += 1

        except Exception as e:
            errors.append(f"Failed to update zone {zone_id}: {str(e)}")

    logger.info(f"Updated {updated_zones} zone mappings for controller {controller_id}")

    return {
        "controller_id": controller_id,
        "updated_zones": updated_zones,
        "errors": errors if errors else None
    }


@router.get("/audit/cache/{controller_id}/candidates")
async def get_zone_match_candidates(
    controller_id: int,
    top_n: int = 3,
    min_score: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ControllerMatchCandidates:
    """
    Get match candidates for all zones in a controller.

    Returns top N switch group candidates for each zone, scored by match quality.
    Uses cached zone and switch group data.

    Query Parameters:
        top_n: Number of candidates per zone (default: 3)
        min_score: Minimum score to include (default: 20)
    """
    # Validate controller access
    controller = db.query(Controller).filter(Controller.id == controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")
    if controller.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    redis_client = await get_redis_client()
    zone_cache = ZoneCacheManager(redis_client, controller_id)

    # Get cache metadata
    meta = await zone_cache.get_cache_meta()
    if not meta or not meta.get('zone_ids'):
        raise HTTPException(
            status_code=404,
            detail="No cached data available. Run an audit first."
        )

    # Load all cached zones
    cached_zones = await zone_cache.get_cached_zones(meta['zone_ids'])
    if not cached_zones:
        raise HTTPException(
            status_code=404,
            detail="No zones found in cache. Run a new audit."
        )

    # Load cached switch groups
    cached_sg_data = await zone_cache.get_cached_switch_groups()
    if not cached_sg_data or not cached_sg_data.get('switch_groups'):
        raise HTTPException(
            status_code=404,
            detail="No switch groups found in cache. Run an audit with switches."
        )

    # Build flat list of switch groups with domain info
    all_switch_groups = []
    for domain_id, groups in cached_sg_data['switch_groups'].items():
        for sg in groups:
            all_switch_groups.append({
                "id": sg.get("id"),
                "name": sg.get("name", ""),
                "domain_id": domain_id,
                "switch_count": sg.get("switch_count", 0),
                "switches_online": sg.get("switches_online", 0),
                "switches_offline": sg.get("switches_offline", 0)
            })

    # Get candidates for each zone
    zones_with_candidates = []

    for zone_id, zone_data in cached_zones.items():
        zone_name = zone_data.get('zone_name', '')
        zone_domain_id = zone_data.get('domain_id', '')
        domain_name = zone_data.get('domain_name', 'Unknown')

        # Get current match if any
        current_match = None
        matched_sgs = zone_data.get('matched_switch_groups', [])
        if matched_sgs:
            sg = matched_sgs[0]  # Take first match
            current_match = SwitchGroupSummary(
                id=sg.get('id', ''),
                name=sg.get('name', ''),
                switch_count=sg.get('switch_count', 0),
                switches_online=sg.get('switches_online', 0),
                switches_offline=sg.get('switches_offline', 0),
                firmware_versions=[],
                user_set=sg.get('user_set', False)
            )

        is_user_set = zone_data.get('user_set_mapping', False)

        # Get match candidates
        candidates = get_match_candidates(
            zone_name=zone_name,
            zone_domain_id=zone_domain_id,
            all_switch_groups=all_switch_groups,
            top_n=top_n,
            min_score=min_score
        )

        zones_with_candidates.append(ZoneMatchCandidates(
            zone_id=zone_id,
            zone_name=zone_name,
            domain_id=zone_domain_id,
            domain_name=domain_name,
            current_match=current_match,
            is_user_set=is_user_set,
            candidates=[MatchCandidate(**c) for c in candidates]
        ))

    # Sort by zone name
    zones_with_candidates.sort(key=lambda z: z.zone_name.lower())

    logger.info(
        f"Generated match candidates for {len(zones_with_candidates)} zones "
        f"(controller {controller_id}, top_n={top_n})"
    )

    return ControllerMatchCandidates(
        controller_id=controller_id,
        zones=zones_with_candidates
    )


@router.get("/audit/jobs/{job_id}/result")
async def get_audit_job_result(
    job_id: str,
    current_user: User = Depends(get_current_user)
) -> SZAuditResult:
    """
    Get the result of a completed audit job.

    Returns the full audit result if the job is complete.
    Results are available for 24 hours after completion.
    """
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    # Check job exists and is complete
    job = await state_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete. Current status: {job.status}"
        )

    # Get result from Redis
    result_key = f"sz_audit:results:{job_id}"
    result_json = await redis_client.get(result_key)

    if not result_json:
        raise HTTPException(
            status_code=404,
            detail="Result not found. Results expire after 24 hours."
        )

    # Parse and return result
    import json
    result_data = json.loads(result_json)
    return SZAuditResult(**result_data)


@router.get("/audit/history", response_model=AuditHistoryResponse)
async def get_audit_history(
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user)
) -> AuditHistoryResponse:
    """
    Get audit history for the current user.

    Returns a list of past audit jobs with their status and summary info.
    Results are available for up to 7 days.

    Args:
        limit: Maximum number of audits to return (default 20, max 100)
        offset: Offset for pagination (default 0)
    """
    import json

    # Clamp limit
    limit = min(limit, 100)

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    # Get job IDs from user's audit history index (newest first)
    user_index_key = f"sz_audit:user:{current_user.id}:jobs"
    job_ids = await redis_client.zrevrange(user_index_key, offset, offset + limit - 1)

    # Get total count
    total = await redis_client.zcard(user_index_key)

    if not job_ids:
        return AuditHistoryResponse(audits=[], total=total)

    # Fetch job details
    audits = []
    for job_id in job_ids:
        # Decode if bytes
        if isinstance(job_id, bytes):
            job_id = job_id.decode('utf-8')

        job = await state_manager.get_job(job_id)
        if not job:
            # Job expired but still in index - clean it up
            await redis_client.zrem(user_index_key, job_id)
            continue

        # Skip jobs that don't belong to this user (shouldn't happen, but safety check)
        if job.user_id != current_user.id:
            continue

        # Try to get summary from result if completed
        total_zones = None
        total_aps = None
        if job.status == JobStatus.COMPLETED:
            result_key = f"sz_audit:results:{job_id}"
            result_json = await redis_client.get(result_key)
            if result_json:
                try:
                    result_data = json.loads(result_json)
                    total_zones = result_data.get('total_zones')
                    total_aps = result_data.get('total_aps')
                except Exception:
                    pass

        audits.append(AuditHistoryItem(
            job_id=job_id,
            controller_id=job.input_data.get('controller_id', 0),
            controller_name=job.input_data.get('controller_name', 'Unknown'),
            status=job.status.value if hasattr(job.status, 'value') else str(job.status),
            created_at=job.created_at.isoformat() if job.created_at else '',
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            total_zones=total_zones,
            total_aps=total_aps,
            errors=job.errors[:3] if job.errors else []  # Limit errors in history view
        ))

    return AuditHistoryResponse(audits=audits, total=total)


# ============================================================================
# Original Sync Functions (kept for backward compatibility)
# ============================================================================

async def audit_zone(
    sz_client: SZClient,
    zone: Dict[str, Any],
    domain_id: str,
    domain_name: str
) -> tuple[ZoneAudit, List[str]]:
    """
    Audit a single zone and return ZoneAudit data.

    Args:
        sz_client: Authenticated SmartZone client
        zone: Zone object from SmartZone
        domain_id: Parent domain ID
        domain_name: Parent domain name

    Returns:
        Tuple of (ZoneAudit, list of partial errors)
    """
    zone_id = zone.get("id")
    zone_name = zone.get("name", "Unknown")
    partial_errors = []

    # Initialize default values
    ap_status = ApStatusBreakdown(online=0, offline=0, flagged=0, total=0)
    ap_model_distribution = []
    ap_firmware_distribution = []
    ap_groups = []
    wlans = []
    wlan_groups = []
    wlan_type_breakdown = {}
    external_ips = set()  # Collect unique external IPs from APs

    # Fetch APs
    aps = []
    try:
        aps_result = await sz_client.aps.get_aps_by_zone(zone_id)
        aps = aps_result.get("list", []) if isinstance(aps_result, dict) else aps_result
        logger.info(f"Zone '{zone_name}': Fetched {len(aps)} APs")

        # Log first AP's fields for debugging (only once per audit)
        if aps and logger.isEnabledFor(logging.DEBUG):
            first_ap = aps[0]
            logger.debug(f"Zone {zone_name}: First AP fields: {list(first_ap.keys())}")
            # Log any IP-related fields to help identify the right external IP field
            ip_fields = {k: v for k, v in first_ap.items() if 'ip' in k.lower() or 'address' in k.lower()}
            if ip_fields:
                logger.debug(f"Zone {zone_name}: IP-related fields: {ip_fields}")

        # Count AP statuses
        online = 0
        offline = 0
        flagged = 0
        model_counter = Counter()
        firmware_counter = Counter()

        for ap in aps:
            # Status field - try multiple possible field names
            status = (
                ap.get("connectionStatus") or
                ap.get("status") or
                ap.get("apStatus") or
                ""
            ).lower()

            if status in ["connect", "connected", "online"]:
                online += 1
            elif status in ["disconnect", "disconnected", "offline"]:
                offline += 1
            else:
                flagged += 1

            # Model field - try multiple possible field names
            model = (
                ap.get("model") or
                ap.get("apModel") or
                ap.get("deviceModel") or
                "Unknown"
            )
            if model and model != "Unknown":
                model_counter[model] += 1

            # Firmware field - try multiple possible field names
            firmware = (
                ap.get("firmwareVersion") or
                ap.get("apFirmwareVersion") or
                ap.get("version") or
                ap.get("swVersion") or
                "Unknown"
            )
            if firmware and firmware != "Unknown":
                firmware_counter[firmware] += 1

            # External IP - the public IP the AP is seen from
            # SmartZone uses 'extIp' for external IP
            ext_ip = (
                ap.get("extIp") or
                ap.get("externalIp") or
                ap.get("externalIpAddress")
            )
            if ext_ip:
                external_ips.add(ext_ip)

        ap_status = ApStatusBreakdown(
            online=online,
            offline=offline,
            flagged=flagged,
            total=len(aps)
        )

        # Only add distributions if we found data
        ap_model_distribution = [
            ModelDistribution(model=model, count=count)
            for model, count in model_counter.most_common()
        ] if model_counter else []

        ap_firmware_distribution = [
            FirmwareDistribution(version=ver, count=count)
            for ver, count in firmware_counter.most_common()
        ] if firmware_counter else []

        # If we have APs but no model/firmware data, log at debug level
        if aps and not model_counter:
            logger.debug(f"Zone {zone_name}: {len(aps)} APs found but no model data extracted. Sample AP keys: {list(aps[0].keys()) if aps else 'N/A'}")
        if aps and not firmware_counter:
            logger.debug(f"Zone {zone_name}: {len(aps)} APs found but no firmware data extracted.")

    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch APs: {str(e)}")

    # Fetch AP Groups
    try:
        ap_groups_raw = await sz_client.apgroups.get_ap_groups_by_zone(zone_id)

        # Count APs per group (if we have APs)
        ap_group_counts = {}
        if 'aps' in dir():
            for ap in aps:
                gid = ap.get("apGroupId")
                if gid:
                    ap_group_counts[gid] = ap_group_counts.get(gid, 0) + 1

        ap_groups = [
            ApGroupSummary(
                id=g.get("id", ""),
                name=g.get("name", "Unknown"),
                ap_count=ap_group_counts.get(g.get("id"), 0)
            )
            for g in ap_groups_raw
        ]
    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch AP Groups: {str(e)}")

    # Fetch WLANs - need to get details for each WLAN to get encryption/auth info
    try:
        wlans_list = await sz_client.wlans.get_wlans_by_zone(zone_id)
        logger.info(f"Zone '{zone_name}': Fetching details for {len(wlans_list)} WLANs")
        wlan_type_counter = Counter()

        # The list endpoint only returns basic info (id, name, ssid)
        # We need to fetch details for each WLAN to get encryption/dpsk settings
        for wlan_basic in wlans_list:
            wlan_id = wlan_basic.get("id")
            wlan_name = wlan_basic.get("name", "Unknown")

            try:
                # Fetch full WLAN details
                w = await sz_client.wlans.get_wlan_details(zone_id, wlan_id)

                # Log first WLAN's full structure for debugging
                if len(wlans) == 0 and logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Zone {zone_name}: First WLAN detail fields: {list(w.keys())}")
                    auth_fields = {
                        "encryption": w.get("encryption"),
                        "dpsk": w.get("dpsk"),
                        "authServiceOrProfile": w.get("authServiceOrProfile"),
                        "portalServiceProfile": w.get("portalServiceProfile"),
                        "authType": w.get("authType"),
                    }
                    logger.debug(f"Zone {zone_name}: First WLAN auth fields: {auth_fields}")

                auth_type = WlanService.extract_auth_type(w)
                encryption = WlanService.extract_encryption(w)
                vlan = WlanService.extract_vlan(w)

                # Log WLANs that resolve to Unknown for debugging
                if auth_type == "Unknown" and logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Zone {zone_name}: WLAN '{wlan_name}' resolved to Unknown. Auth fields: encryption={w.get('encryption')}, dpsk={w.get('dpsk')}, authServiceOrProfile={w.get('authServiceOrProfile')}")

                wlans.append(WlanSummary(
                    id=w.get("id", wlan_id),
                    name=w.get("name", wlan_name),
                    ssid=w.get("ssid", w.get("name", wlan_name)),
                    auth_type=auth_type,
                    encryption=encryption,
                    vlan=vlan
                ))

                wlan_type_counter[auth_type] += 1

            except Exception as e:
                # If we can't get details, use basic info with Unknown auth
                logger.debug(f"Zone {zone_name}: Failed to get WLAN details for {wlan_name}: {e}")
                wlans.append(WlanSummary(
                    id=wlan_id,
                    name=wlan_name,
                    ssid=wlan_basic.get("ssid", wlan_name),
                    auth_type="Unknown",
                    encryption="Unknown",
                    vlan=None
                ))
                wlan_type_counter["Unknown"] += 1

        wlan_type_breakdown = dict(wlan_type_counter)

    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch WLANs: {str(e)}")

    # Fetch WLAN Groups
    try:
        wlan_groups_raw = await sz_client.wlans.get_wlan_groups_by_zone(zone_id)
        wlan_groups = [
            WlanGroupSummary(
                id=g.get("id", ""),
                name=g.get("name", "Unknown"),
                wlan_count=len(g.get("members", []))
            )
            for g in wlan_groups_raw
        ]
    except Exception as e:
        partial_errors.append(f"Zone {zone_name}: Failed to fetch WLAN Groups: {str(e)}")

    zone_audit = ZoneAudit(
        zone_id=zone_id,
        zone_name=zone_name,
        domain_id=domain_id,
        domain_name=domain_name,
        external_ips=sorted(external_ips),  # Convert set to sorted list
        ap_status=ap_status,
        ap_model_distribution=ap_model_distribution,
        ap_groups=ap_groups,
        ap_firmware_distribution=ap_firmware_distribution,
        wlan_count=len(wlans),
        wlan_groups=wlan_groups,
        wlans=wlans,
        wlan_type_breakdown=wlan_type_breakdown
    )

    return zone_audit, partial_errors


async def audit_domain(
    sz_client: SZClient,
    domain: Dict[str, Any],
    all_zones: List[ZoneAudit],
    all_switches: List[Dict[str, Any]] = None,
    all_switch_groups: Dict[str, List[Dict[str, Any]]] = None
) -> tuple[DomainAudit, List[SwitchGroupSummary], List[str]]:
    """
    Audit a single domain and return DomainAudit data.

    Args:
        sz_client: Authenticated SmartZone client
        domain: Domain object from SmartZone
        all_zones: List of already-audited zones for this domain
        all_switches: Pre-fetched list of all switches (to avoid per-domain API calls)
        all_switch_groups: Pre-fetched dict of domain_id -> switch group details

    Returns:
        Tuple of (DomainAudit, switch_groups, list of partial errors)
    """
    domain_id = domain.get("id")
    domain_name = domain.get("name", "Unknown")
    parent_domain_id = domain.get("parentDomainId")
    parent_domain_name = domain.get("parentDomainName")
    partial_errors = []

    # Get zones for this domain
    domain_zones = [z for z in all_zones if z.domain_id == domain_id]

    # Aggregate stats from zones
    total_aps = sum(z.ap_status.total for z in domain_zones)
    total_wlans = sum(z.wlan_count for z in domain_zones)

    # Build switch groups from pre-fetched data
    # Switch groups are like zones - display them even if we can't enumerate switches
    switch_groups = []
    total_switches = 0
    switch_firmware_distribution = []

    # Start with switch groups fetched from API (these exist even if switches aren't enumerable)
    domain_switch_groups = (all_switch_groups or {}).get(domain_id, [])
    switch_group_map = {}

    for sg in domain_switch_groups:
        sg_id = sg.get("id")
        sg_name = sg.get("name", sg_id)
        switch_group_map[sg_id] = {
            "id": sg_id,
            "name": sg_name,
            "online": 0,
            "offline": 0,
            "total": 0,
            "firmware_counter": Counter()
        }

    # If we have switches, count them per group and extract firmware
    firmware_counter = Counter()

    if all_switches is not None:
        # Filter switches belonging to this domain
        domain_switches = [
            s for s in all_switches
            if s.get("domainId") == domain_id
        ]
        total_switches = len(domain_switches)

        for s in domain_switches:
            sg_id = s.get("switchGroupId") or s.get("groupId") or "ungrouped"
            sg_name = s.get("switchGroupName") or s.get("groupName") or "Ungrouped"

            # Create group entry if not from API (fallback)
            if sg_id not in switch_group_map:
                switch_group_map[sg_id] = {
                    "id": sg_id,
                    "name": sg_name,
                    "online": 0,
                    "offline": 0,
                    "total": 0,
                    "firmware_counter": Counter()
                }

            switch_group_map[sg_id]["total"] += 1
            status = (s.get("status") or "").lower()
            if status in ["online", "connected"]:
                switch_group_map[sg_id]["online"] += 1
            else:
                switch_group_map[sg_id]["offline"] += 1

            firmware = s.get("firmwareVersion") or "Unknown"
            if firmware != "Unknown":
                firmware_counter[firmware] += 1
                switch_group_map[sg_id]["firmware_counter"][firmware] += 1

        if domain_switches:
            logger.debug(f"Domain {domain_name}: {len(domain_switches)} switches in {len(switch_group_map)} groups")

    # Build switch group summaries
    switch_groups = [
        SwitchGroupSummary(
            id=sg["id"],
            name=sg["name"],
            switch_count=sg["total"],
            switches_online=sg["online"],
            switches_offline=sg["offline"],
            firmware_versions=[
                FirmwareDistribution(version=ver, count=count)
                for ver, count in sg["firmware_counter"].most_common()
            ]
        )
        for sg in switch_group_map.values()
    ]

    switch_firmware_distribution = [
        FirmwareDistribution(version=ver, count=count)
        for ver, count in firmware_counter.most_common()
    ]

    if switch_group_map and not all_switches:
        logger.debug(f"Domain {domain_name}: {len(switch_group_map)} switch groups found (switches not enumerable)")

    domain_audit = DomainAudit(
        domain_id=domain_id,
        domain_name=domain_name,
        parent_domain_id=parent_domain_id,
        parent_domain_name=parent_domain_name,
        zone_count=len(domain_zones),
        total_aps=total_aps,
        total_wlans=total_wlans,
        switch_groups=switch_groups,
        total_switches=total_switches,
        switch_firmware_distribution=switch_firmware_distribution,
        children=[]
    )

    return domain_audit, switch_groups, partial_errors


async def perform_audit(
    controller: Controller,
    sz_client: SZClient
) -> SZAuditResult:
    """
    Perform a complete audit of a SmartZone controller.

    Args:
        controller: Controller database record
        sz_client: Authenticated SmartZone client

    Returns:
        SZAuditResult with all audit data
    """
    partial_errors = []
    cluster_ip = None
    controller_firmware = None

    # Get system info
    try:
        audit_info = await sz_client.system.get_audit_info()
        cluster_ip = audit_info.get("cluster_ip")
        controller_firmware = audit_info.get("firmware_version")
    except Exception as e:
        partial_errors.append(f"Failed to get system info: {str(e)}")

    # Get all domains recursively FIRST (needed for switch fetching)
    domains_raw = []
    try:
        domains_raw = await sz_client.zones.get_domains(recursively=True, include_self=True)
        logger.info(f"Audit: Found {len(domains_raw)} domains")
    except Exception as e:
        partial_errors.append(f"Failed to get domains: {str(e)}")

    # Fetch switch groups and switches per-domain
    # Switch groups are like zones for switches - fetch them with details first
    # Deduplicate by ID since recursive domain queries may return duplicates
    all_switches = []
    seen_switch_ids = set()
    all_switch_groups = {}  # domain_id -> [switch_group_details]
    seen_switch_group_ids = set()

    for domain in domains_raw:
        domain_id = domain.get("id")
        domain_name = domain.get("name", "Unknown")

        # First, fetch switch groups with full details (like zones for APs)
        try:
            switch_groups = await sz_client.switches.get_switch_groups_by_domain(domain_id)
            if switch_groups:
                # Deduplicate switch groups by ID
                unique_groups = []
                for sg in switch_groups:
                    sg_id = sg.get("id")
                    if sg_id and sg_id not in seen_switch_group_ids:
                        seen_switch_group_ids.add(sg_id)
                        unique_groups.append(sg)

                if unique_groups:
                    logger.info(f"Domain {domain_name}: Found {len(unique_groups)} switch groups")
                    all_switch_groups[domain_id] = unique_groups
                    # Log first switch group for debugging
                    if logger.isEnabledFor(logging.DEBUG):
                        sg = unique_groups[0]
                        logger.debug(f"Sample switch group: id={sg.get('id')}, name={sg.get('name')}, fields={list(sg.keys())}")
        except Exception as e:
            if "404" not in str(e).lower():
                logger.debug(f"Domain {domain_name}: Switch groups not available: {e}")

        # Then try to get switches directly via POST /switch
        try:
            switches = await sz_client.switches.get_switches_by_domain(domain_id)
            if switches:
                # Deduplicate switches by ID
                new_switches = []
                for s in switches:
                    switch_id = s.get("id") or s.get("switchId") or s.get("serialNumber")
                    if switch_id and switch_id not in seen_switch_ids:
                        seen_switch_ids.add(switch_id)
                        new_switches.append(s)
                        # Log first switch's fields for debugging (only once)
                        if len(all_switches) == 0 and len(new_switches) == 1 and logger.isEnabledFor(logging.DEBUG):
                            logger.debug(f"Sample switch fields: {list(s.keys())}")
                            logger.debug(f"Sample switch: domainId={s.get('domainId')}, groupId={s.get('switchGroupId') or s.get('groupId')}, status={s.get('status')}")

                if new_switches:
                    logger.info(f"Domain {domain_name}: Fetched {len(new_switches)} switches via POST /switch")
                    all_switches.extend(new_switches)
        except Exception as e:
            # Only log warning for unexpected errors (not 404s which are handled in service)
            if "404" not in str(e).lower():
                partial_errors.append(f"Domain {domain_name}: Failed to fetch switches: {str(e)}")

    if all_switches:
        logger.info(f"Total switches fetched across all domains: {len(all_switches)}")
    if all_switch_groups:
        total_groups = sum(len(sgs) for sgs in all_switch_groups.values())
        logger.info(f"Total switch groups found across all domains: {total_groups}")

    # Get all zones with domain info - deduplicate by zone_id
    all_zones_audit = []
    all_zone_data = []
    seen_zone_ids = set()  # Track which zones we've already processed
    total_zones_processed = 0

    logger.info(f"Audit: Starting zone collection across {len(domains_raw)} domains")
    for domain in domains_raw:
        domain_id = domain.get("id")
        domain_name = domain.get("name", "Unknown")

        try:
            zones = await sz_client.zones.get_zones(domain_id=domain_id)

            # Audit each zone (could parallelize for performance)
            for zone in zones:
                zone_id = zone.get("id")
                zone_name = zone.get("name", "")

                # Skip "Staging Zone" - SmartZone system zone that doesn't support queries
                if zone_name.lower() == "staging zone":
                    logger.debug(f"Skipping system zone: {zone_name} ({zone_id})")
                    continue

                # Skip if we've already processed this zone (avoid duplicates)
                if zone_id in seen_zone_ids:
                    logger.debug(f"Skipping duplicate zone {zone_id} ({zone_name}) - already processed")
                    continue

                seen_zone_ids.add(zone_id)

                zone_audit, zone_errors = await audit_zone(sz_client, zone, domain_id, domain_name)
                all_zones_audit.append(zone_audit)
                partial_errors.extend(zone_errors)
                all_zone_data.append({"zone": zone, "domain_id": domain_id, "domain_name": domain_name})
                total_zones_processed += 1
                logger.info(f"Audit: Completed zone {total_zones_processed}: '{zone_name}' ({zone_audit.ap_status.total} APs, {zone_audit.wlan_count} WLANs)")

        except Exception as e:
            partial_errors.append(f"Failed to get zones for domain {domain_name}: {str(e)}")

    logger.info(f"Audit: Zone collection complete - {len(all_zones_audit)} zones processed")

    # Audit domains (pass pre-fetched switches and switch groups)
    domains_audit = []
    all_switch_groups_flat = []

    for domain in domains_raw:
        domain_audit, switch_groups_summary, domain_errors = await audit_domain(
            sz_client, domain, all_zones_audit, all_switches, all_switch_groups
        )
        domains_audit.append(domain_audit)
        all_switch_groups_flat.extend(switch_groups_summary)
        partial_errors.extend(domain_errors)

    # Match switch groups to zones by name similarity
    if all_switch_groups and all_zones_audit:
        match_switch_groups_to_zones(
            all_zones_audit,
            all_switch_groups,
            all_switch_groups_flat
        )

    # Build domain hierarchy (link children to parents)
    domain_map = {d.domain_id: d for d in domains_audit}
    root_domains = []

    for domain in domains_audit:
        if domain.parent_domain_id and domain.parent_domain_id in domain_map:
            parent = domain_map[domain.parent_domain_id]
            parent.children.append(domain)
        else:
            root_domains.append(domain)

    # Aggregate global stats
    total_aps = sum(z.ap_status.total for z in all_zones_audit)
    total_wlans = sum(z.wlan_count for z in all_zones_audit)
    total_switches = sum(d.total_switches for d in domains_audit)

    # Aggregate AP models
    model_counter = Counter()
    for zone in all_zones_audit:
        for md in zone.ap_model_distribution:
            model_counter[md.model] += md.count

    ap_model_summary = [
        ModelDistribution(model=m, count=c)
        for m, c in model_counter.most_common()
    ]

    # Aggregate AP firmware
    firmware_counter = Counter()
    for zone in all_zones_audit:
        for fd in zone.ap_firmware_distribution:
            firmware_counter[fd.version] += fd.count

    ap_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in firmware_counter.most_common()
    ]

    # Aggregate switch firmware
    switch_firmware_counter = Counter()
    for domain in domains_audit:
        for fd in domain.switch_firmware_distribution:
            switch_firmware_counter[fd.version] += fd.count

    switch_firmware_summary = [
        FirmwareDistribution(version=v, count=c)
        for v, c in switch_firmware_counter.most_common()
    ]

    # Aggregate WLAN types
    wlan_type_counter = Counter()
    for zone in all_zones_audit:
        for wlan_type, count in zone.wlan_type_breakdown.items():
            wlan_type_counter[wlan_type] += count

    wlan_type_summary = dict(wlan_type_counter)

    logger.info(f"Audit complete: {len(domains_audit)} domains, {len(all_zones_audit)} zones, {total_aps} APs, {total_wlans} WLANs, {total_switches} switches")

    return SZAuditResult(
        controller_id=controller.id,
        controller_name=controller.name,
        host=controller.sz_host,
        timestamp=datetime.utcnow(),
        cluster_ip=cluster_ip,
        controller_firmware=controller_firmware,
        domains=root_domains,
        zones=all_zones_audit,
        total_domains=len(domains_audit),
        total_zones=len(all_zones_audit),
        total_aps=total_aps,
        total_wlans=total_wlans,
        total_switches=total_switches,
        ap_model_summary=ap_model_summary,
        ap_firmware_summary=ap_firmware_summary,
        switch_firmware_summary=switch_firmware_summary,
        wlan_type_summary=wlan_type_summary,
        partial_errors=partial_errors
    )


@router.get("/{controller_id}/audit/debug")
async def debug_audit_data(
    controller_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Debug endpoint to see raw data from SmartZone API.
    Helps identify field names and data structure.
    """
    controller = validate_controller_access(controller_id, current_user, db)

    if controller.controller_type != "SmartZone":
        raise HTTPException(status_code=400, detail="Not a SmartZone controller")

    sz_client = create_sz_client_from_controller(controller_id, db)

    debug_data = {
        "api_version": sz_client.api_version,
        "sample_ap": None,
        "ap_fields": [],
        "sample_switch": None,
        "switch_fields": [],
        "sample_zone": None,
        "zone_fields": [],
        "domains": [],
        "zones_per_domain": {}
    }

    try:
        async with sz_client:
            # Get domains
            domains = await sz_client.zones.get_domains(recursively=True, include_self=True)
            debug_data["domains"] = [{"id": d.get("id"), "name": d.get("name"), "parentDomainId": d.get("parentDomainId")} for d in domains]

            # Get zones for first domain and count
            for domain in domains[:3]:  # Check first 3 domains
                domain_id = domain.get("id")
                domain_name = domain.get("name")
                zones = await sz_client.zones.get_zones(domain_id=domain_id)
                debug_data["zones_per_domain"][domain_name] = [z.get("id") for z in zones]

                # Get sample zone
                if zones and not debug_data["sample_zone"]:
                    zone = zones[0]
                    debug_data["sample_zone"] = zone
                    debug_data["zone_fields"] = list(zone.keys())

                    # Get APs from this zone
                    zone_id = zone.get("id")
                    aps_result = await sz_client.aps.get_aps_by_zone(zone_id)
                    aps = aps_result.get("list", []) if isinstance(aps_result, dict) else []

                    if aps:
                        debug_data["sample_ap"] = aps[0]
                        debug_data["ap_fields"] = list(aps[0].keys())
                        debug_data["ap_count_in_zone"] = len(aps)

            # Try to get switches using correct endpoint (POST /switch with filters)
            try:
                switches_result = await sz_client.switches.get_all_switches()
                switches = switches_result.get("list", []) if isinstance(switches_result, dict) else []
                debug_data["switch_count"] = len(switches)
                if switches:
                    debug_data["sample_switch"] = switches[0]
                    debug_data["switch_fields"] = list(switches[0].keys())
            except Exception as e:
                debug_data["switch_error"] = str(e)

    except Exception as e:
        debug_data["error"] = str(e)

    return debug_data


@router.post("/{controller_id}/audit", response_model=SZAuditResult)
async def audit_single_controller(
    controller_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> SZAuditResult:
    """
    Perform a comprehensive audit of a single SmartZone controller.

    Returns detailed information about:
    - Domains (hierarchical structure)
    - Zones with AP counts, status, models, firmware
    - WLANs with auth type breakdown
    - AP Groups and WLAN Groups
    - Switches with status and firmware
    """
    # Validate access
    controller = validate_controller_access(controller_id, current_user, db)

    if controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Controller {controller.name} is not a SmartZone controller"
        )

    # Create SZ client
    try:
        sz_client = create_sz_client_from_controller(controller_id, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create SmartZone client: {str(e)}"
        )

    # Perform audit
    try:
        async with sz_client:
            result = await perform_audit(controller, sz_client)
            return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Audit failed for controller {controller_id}")
        return SZAuditResult(
            controller_id=controller.id,
            controller_name=controller.name,
            host=controller.sz_host or "",
            timestamp=datetime.utcnow(),
            error=f"Audit failed: {str(e)}",
            domains=[],
            zones=[],
            total_domains=0,
            total_zones=0,
            total_aps=0,
            total_wlans=0,
            total_switches=0,
            ap_model_summary=[],
            ap_firmware_summary=[],
            switch_firmware_summary=[],
            wlan_type_summary={}
        )


@router.post("/audit/batch", response_model=BatchAuditResponse)
async def audit_multiple_controllers(
    request: BatchAuditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> BatchAuditResponse:
    """
    Audit multiple SmartZone controllers in parallel.

    Each controller audit is independent - failures don't affect others.
    Returns results for all requested controllers, with errors noted
    for any that failed.
    """
    results = []
    successful = 0
    failed = 0

    async def audit_one(controller_id: int) -> SZAuditResult:
        """Audit a single controller, catching all exceptions."""
        try:
            # Validate access
            controller = validate_controller_access(controller_id, current_user, db)

            if controller.controller_type != "SmartZone":
                return SZAuditResult(
                    controller_id=controller_id,
                    controller_name=controller.name,
                    host=controller.sz_host or "",
                    timestamp=datetime.utcnow(),
                    error=f"Not a SmartZone controller: {controller.controller_type}",
                    domains=[],
                    zones=[],
                    total_domains=0,
                    total_zones=0,
                    total_aps=0,
                    total_wlans=0,
                    total_switches=0,
                    ap_model_summary=[],
                    ap_firmware_summary=[],
                    switch_firmware_summary=[],
                    wlan_type_summary={}
                )

            sz_client = create_sz_client_from_controller(controller_id, db)

            async with sz_client:
                return await perform_audit(controller, sz_client)

        except HTTPException as e:
            return SZAuditResult(
                controller_id=controller_id,
                controller_name=f"Controller {controller_id}",
                host="",
                timestamp=datetime.utcnow(),
                error=e.detail,
                domains=[],
                zones=[],
                total_domains=0,
                total_zones=0,
                total_aps=0,
                total_wlans=0,
                total_switches=0,
                ap_model_summary=[],
                ap_firmware_summary=[],
                switch_firmware_summary=[],
                wlan_type_summary={}
            )
        except Exception as e:
            logger.exception(f"Batch audit failed for controller {controller_id}")
            return SZAuditResult(
                controller_id=controller_id,
                controller_name=f"Controller {controller_id}",
                host="",
                timestamp=datetime.utcnow(),
                error=f"Audit failed: {str(e)}",
                domains=[],
                zones=[],
                total_domains=0,
                total_zones=0,
                total_aps=0,
                total_wlans=0,
                total_switches=0,
                ap_model_summary=[],
                ap_firmware_summary=[],
                switch_firmware_summary=[],
                wlan_type_summary={}
            )

    # Run all audits in parallel
    results = await asyncio.gather(
        *[audit_one(cid) for cid in request.controller_ids]
    )

    # Count successes and failures
    for result in results:
        if result.error:
            failed += 1
        else:
            successful += 1

    return BatchAuditResponse(
        results=list(results),
        total_requested=len(request.controller_ids),
        successful=successful,
        failed=failed
    )


@router.post("/audit/export-csv")
async def export_audit_csv(
    request: ExportAuditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> StreamingResponse:
    """
    Export audit data as CSV with one row per zone.

    Columns include: Controller, Domain, Zone, AP counts, AP models,
    AP firmware, External IPs, WLAN counts, WLAN types, and mapped switch groups.

    Accepts optional switch_group_mappings to include manually mapped switch groups.
    """
    # First run the audit
    batch_request = BatchAuditRequest(controller_ids=request.controller_ids)
    batch_response = await audit_multiple_controllers(batch_request, current_user, db)

    # Build lookup for switch group mappings: controller_id -> {zone_id -> switch_group_id}
    sg_mappings: Dict[int, Dict[str, str]] = {}
    for mapping in request.switch_group_mappings:
        sg_mappings[mapping.controller_id] = mapping.mappings

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    headers = [
        "Controller Name",
        "Controller Host",
        "Controller Firmware",
        "Domain Name",
        "Zone Name",
        "Zone ID",
        # AP counts
        "APs Total",
        "APs Online",
        "APs Offline",
        "APs Flagged",
        "AP Groups",
        # AP models - top 3 + count
        "AP Model 1",
        "AP Model 1 Count",
        "AP Model 2",
        "AP Model 2 Count",
        "AP Model 3",
        "AP Model 3 Count",
        "AP Models Other",
        # AP firmware - top 3 + count
        "AP Firmware 1",
        "AP Firmware 1 Count",
        "AP Firmware 2",
        "AP Firmware 2 Count",
        "AP Firmware 3",
        "AP Firmware 3 Count",
        "AP Firmware Other",
        # External IPs
        "External IP Count",
        "External IPs",
        # WLANs
        "WLAN Count",
        "WLAN Group Count",
        # WLAN types
        "WLANs Open",
        "WLANs WPA2-PSK",
        "WLANs WPA2-Enterprise",
        "WLANs WPA3-SAE",
        "WLANs WPA3-Enterprise",
        "WLANs DPSK",
        "WLANs Other",
        # Switches (domain level)
        "Domain Switch Count",
        "Domain Switch Groups",
        # Mapped Switch Group (from manual mapping)
        "Mapped Switch Group",
        "Mapped Switch Group ID",
        "Mapped Switches Total",
        "Mapped Switches Online",
        "Mapped Switches Offline",
        "Mapped Switch Firmware 1",
        "Mapped Switch Firmware 1 Count",
        "Mapped Switch Firmware 2",
        "Mapped Switch Firmware 2 Count",
        "Mapped Switch Firmware Other",
    ]
    writer.writerow(headers)

    # Write data rows - one per zone
    for result in batch_response.results:
        if result.error:
            # Write error row
            writer.writerow([
                result.controller_name,
                result.host,
                f"ERROR: {result.error}",
                "", "", "", "", "", "", "", "",
                "", "", "", "", "", "", "",
                "", "", "", "", "", "", "",
                "", "",
                "", "",
                "", "", "", "", "", "", "",
                "", ""
            ])
            continue

        # Build domain -> switch count mapping
        domain_switch_counts = {}
        domain_switch_groups = {}
        # Build switch group lookup by ID
        all_switch_groups: Dict[str, SwitchGroupSummary] = {}

        def collect_switch_groups(domains):
            for domain in domains:
                domain_switch_counts[domain.domain_id] = domain.total_switches
                domain_switch_groups[domain.domain_id] = len(domain.switch_groups)
                for sg in domain.switch_groups:
                    all_switch_groups[sg.id] = sg
                if domain.children:
                    collect_switch_groups(domain.children)

        collect_switch_groups(result.domains)

        # Get manual mappings for this controller
        controller_mappings = sg_mappings.get(result.controller_id, {})

        for zone in result.zones:
            # AP models - get top 3
            ap_models = sorted(
                zone.ap_model_distribution,
                key=lambda x: x.count,
                reverse=True
            )
            model_1 = ap_models[0].model if len(ap_models) > 0 else ""
            model_1_count = ap_models[0].count if len(ap_models) > 0 else ""
            model_2 = ap_models[1].model if len(ap_models) > 1 else ""
            model_2_count = ap_models[1].count if len(ap_models) > 1 else ""
            model_3 = ap_models[2].model if len(ap_models) > 2 else ""
            model_3_count = ap_models[2].count if len(ap_models) > 2 else ""
            models_other = sum(m.count for m in ap_models[3:]) if len(ap_models) > 3 else ""

            # AP firmware - get top 3
            ap_firmware = sorted(
                zone.ap_firmware_distribution,
                key=lambda x: x.count,
                reverse=True
            )
            fw_1 = ap_firmware[0].version if len(ap_firmware) > 0 else ""
            fw_1_count = ap_firmware[0].count if len(ap_firmware) > 0 else ""
            fw_2 = ap_firmware[1].version if len(ap_firmware) > 1 else ""
            fw_2_count = ap_firmware[1].count if len(ap_firmware) > 1 else ""
            fw_3 = ap_firmware[2].version if len(ap_firmware) > 2 else ""
            fw_3_count = ap_firmware[2].count if len(ap_firmware) > 2 else ""
            fw_other = sum(f.count for f in ap_firmware[3:]) if len(ap_firmware) > 3 else ""

            # WLAN type counts
            wlan_types = zone.wlan_type_breakdown
            wlans_open = wlan_types.get("Open", 0) + wlan_types.get("Open + Portal", 0)
            wlans_wpa2_psk = wlan_types.get("WPA2-PSK", 0)
            wlans_wpa2_ent = wlan_types.get("WPA2-Enterprise", 0)
            wlans_wpa3_sae = wlan_types.get("WPA3-SAE", 0) + wlan_types.get("WPA3", 0)
            wlans_wpa3_ent = wlan_types.get("WPA3-Enterprise", 0)
            wlans_dpsk = wlan_types.get("DPSK", 0)
            # Other = everything else
            known_types = {"Open", "Open + Portal", "WPA2-PSK", "WPA2-Enterprise",
                          "WPA3-SAE", "WPA3", "WPA3-Enterprise", "DPSK"}
            wlans_other = sum(v for k, v in wlan_types.items() if k not in known_types)

            # Get mapped switch group for this zone
            mapped_sg_id = controller_mappings.get(zone.zone_id, "")
            mapped_sg = all_switch_groups.get(mapped_sg_id) if mapped_sg_id else None

            row = [
                result.controller_name,
                result.host,
                result.controller_firmware or "",
                zone.domain_name,
                zone.zone_name,
                zone.zone_id,
                # AP counts
                zone.ap_status.total,
                zone.ap_status.online,
                zone.ap_status.offline,
                zone.ap_status.flagged,
                len(zone.ap_groups),
                # AP models
                model_1, model_1_count,
                model_2, model_2_count,
                model_3, model_3_count,
                models_other,
                # AP firmware
                fw_1, fw_1_count,
                fw_2, fw_2_count,
                fw_3, fw_3_count,
                fw_other,
                # External IPs
                len(zone.external_ips),
                "; ".join(zone.external_ips) if zone.external_ips else "",
                # WLANs
                zone.wlan_count,
                len(zone.wlan_groups),
                # WLAN types
                wlans_open or "",
                wlans_wpa2_psk or "",
                wlans_wpa2_ent or "",
                wlans_wpa3_sae or "",
                wlans_wpa3_ent or "",
                wlans_dpsk or "",
                wlans_other or "",
                # Switches (domain level)
                domain_switch_counts.get(zone.domain_id, 0) or "",
                domain_switch_groups.get(zone.domain_id, 0) or "",
                # Mapped Switch Group
                mapped_sg.name if mapped_sg else "",
                mapped_sg_id,
                mapped_sg.switch_count if mapped_sg else "",
                mapped_sg.switches_online if mapped_sg else "",
                mapped_sg.switches_offline if mapped_sg else "",
                # Mapped Switch Firmware
                mapped_sg.firmware_versions[0].version if mapped_sg and len(mapped_sg.firmware_versions) > 0 else "",
                mapped_sg.firmware_versions[0].count if mapped_sg and len(mapped_sg.firmware_versions) > 0 else "",
                mapped_sg.firmware_versions[1].version if mapped_sg and len(mapped_sg.firmware_versions) > 1 else "",
                mapped_sg.firmware_versions[1].count if mapped_sg and len(mapped_sg.firmware_versions) > 1 else "",
                sum(fv.count for fv in mapped_sg.firmware_versions[2:]) if mapped_sg and len(mapped_sg.firmware_versions) > 2 else "",
            ]
            writer.writerow(row)

    # Prepare response
    output.seek(0)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"sz_audit_{timestamp}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )
