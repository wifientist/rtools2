"""
Bulk AP Tagging Router

Bulk tool for managing AP tags with three modes:
1. Set: Replace all tags on selected APs
2. Add: Merge new tags into existing tags
3. Remove: Remove specific tags from existing tags

Workflow:
1. GET /{controller_id}/venue/{venue_id}/aps - List APs with current tags
2. POST /preview - Preview tag changes (dry run)
3. POST /apply - Apply tag changes with batch processing
"""

import asyncio
import logging
import uuid
from typing import List, Optional
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from clients.r1_client import create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.user import User
from models.controller import Controller
from sqlalchemy.orm import Session
from redis_client import get_redis_client

from workflow.v2.models import WorkflowJobV2, JobStatus, PhaseStatus, PhaseDefinitionV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk-ap-tagging", tags=["Bulk AP Tagging"])


# ============================================================================
# Enums and Models
# ============================================================================

class TagMode(str, Enum):
    SET = "set"       # Replace all tags
    ADD = "add"       # Merge into existing
    REMOVE = "remove" # Remove specific tags


class APTagPreview(BaseModel):
    """Preview of tag changes for a single AP"""
    serial_number: str
    ap_name: str
    current_tags: List[str]
    new_tags: List[str]
    tags_added: List[str] = Field(default_factory=list)
    tags_removed: List[str] = Field(default_factory=list)
    changed: bool = True
    error: Optional[str] = None


class TagPreviewRequest(BaseModel):
    """Request to preview bulk tag operations"""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    mode: TagMode
    tags: List[str] = Field(description="Tags to set/add/remove")
    ap_serials: List[str] = Field(description="Selected AP serial numbers")


class TagPreviewResponse(BaseModel):
    """Response from tag preview endpoint"""
    mode: TagMode
    total_aps: int
    changed_count: int
    unchanged_count: int
    error_count: int
    previews: List[APTagPreview]
    warnings: List[str] = Field(default_factory=list)


class TagApplyRequest(BaseModel):
    """Request to apply bulk tag operations"""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    mode: TagMode
    tags: List[str] = Field(description="Tags to set/add/remove")
    ap_serials: List[str] = Field(description="Selected AP serial numbers")
    max_concurrent: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max concurrent API calls"
    )


# ============================================================================
# Constants
# ============================================================================

WORKFLOW_NAME = "bulk_ap_tagging"
MAX_TAGS_PER_AP = 24


# ============================================================================
# Helper Functions
# ============================================================================

def parse_tags(raw_tags: List[str]) -> List[str]:
    """Clean and deduplicate tag input."""
    cleaned = []
    seen = set()
    for tag in raw_tags:
        t = tag.strip()
        if t and t not in seen:
            cleaned.append(t)
            seen.add(t)
    return cleaned


def normalize_ap_tags(tags) -> List[str]:
    """Normalize tags from R1 API response. Handles None, strings, lists with empty strings."""
    if not tags:
        return []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(";") if t.strip()]
    if isinstance(tags, list):
        return [t.strip() for t in tags if isinstance(t, str) and t.strip()]
    return []


def compute_tag_changes(
    current_tags: List[str],
    input_tags: List[str],
    mode: TagMode,
) -> tuple:
    """
    Compute new tags based on mode.
    Returns (new_tags, tags_added, tags_removed, error).
    """
    current_set = set(current_tags)
    input_set = set(input_tags)

    if mode == TagMode.SET:
        new_tags = list(input_tags)  # preserve order from input
        added = list(input_set - current_set)
        removed = list(current_set - input_set)
        return new_tags, added, removed, None

    elif mode == TagMode.ADD:
        # Union: keep existing order, append new ones
        new_tags = list(current_tags) + [t for t in input_tags if t not in current_set]
        added = [t for t in input_tags if t not in current_set]
        if len(new_tags) > MAX_TAGS_PER_AP:
            return new_tags, added, [], f"Would exceed {MAX_TAGS_PER_AP}-tag limit ({len(new_tags)} tags)"
        return new_tags, added, [], None

    elif mode == TagMode.REMOVE:
        new_tags = [t for t in current_tags if t not in input_set]
        removed = [t for t in current_tags if t in input_set]
        return new_tags, [], removed, None

    return current_tags, [], [], "Unknown mode"


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/{controller_id}/venue/{venue_id}/aps")
async def get_venue_aps(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all APs in a venue with their current tags."""
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == current_user.id
    ).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    effective_tenant_id = tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

    r1_client = create_r1_client_from_controller(controller_id, db)

    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(effective_tenant_id, venue_id)
        aps = aps_response.get("data", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {e}")

    aps.sort(key=lambda x: x.get("name", ""))

    return {
        "venue_id": venue_id,
        "total_aps": len(aps),
        "aps": [
            {
                "serial": ap.get("serialNumber"),
                "name": ap.get("name"),
                "model": ap.get("model"),
                "status": ap.get("status"),
                "ap_group_name": ap.get("apGroupName"),
                "tags": normalize_ap_tags(ap.get("tags")),
            }
            for ap in aps
        ]
    }


@router.post("/preview")
async def preview_tag_changes(
    request: TagPreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Preview bulk tag operations without applying.

    Supports three modes:
    - **set**: Replace all tags on selected APs
    - **add**: Merge new tags into existing (validates 24-tag limit)
    - **remove**: Remove specific tags from selected APs
    """
    controller = db.query(Controller).filter(
        Controller.id == request.controller_id,
        Controller.user_id == current_user.id
    ).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    effective_tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

    # Clean input tags
    tags = parse_tags(request.tags)
    if not tags:
        raise HTTPException(status_code=400, detail="At least one tag is required")

    # Validate tag count for SET mode
    if request.mode == TagMode.SET and len(tags) > MAX_TAGS_PER_AP:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot set more than {MAX_TAGS_PER_AP} tags per AP (got {len(tags)})"
        )

    r1_client = create_r1_client_from_controller(request.controller_id, db)

    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(effective_tenant_id, request.venue_id)
        aps = aps_response.get("data", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {e}")

    # Build lookup for selected APs
    selected_set = set(request.ap_serials)
    ap_lookup = {
        ap.get("serialNumber"): ap
        for ap in aps
        if ap.get("serialNumber") in selected_set
    }

    previews = []
    warnings = []
    changed_count = 0
    unchanged_count = 0
    error_count = 0

    # Check for serials not found in venue
    missing = selected_set - set(ap_lookup.keys())
    if missing:
        warnings.append(f"{len(missing)} serial(s) not found in venue: {', '.join(sorted(missing)[:5])}")

    for serial in request.ap_serials:
        ap = ap_lookup.get(serial)
        if not ap:
            continue

        current_tags = normalize_ap_tags(ap.get("tags"))

        new_tags, added, removed, error = compute_tag_changes(current_tags, tags, request.mode)

        changed = sorted(new_tags) != sorted(current_tags)

        preview = APTagPreview(
            serial_number=serial,
            ap_name=ap.get("name", ""),
            current_tags=current_tags,
            new_tags=new_tags,
            tags_added=added,
            tags_removed=removed,
            changed=changed,
            error=error,
        )

        if error:
            error_count += 1
        elif changed:
            changed_count += 1
        else:
            unchanged_count += 1

        previews.append(preview)

    return TagPreviewResponse(
        mode=request.mode,
        total_aps=len(previews),
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        error_count=error_count,
        previews=previews,
        warnings=warnings,
    )


@router.post("/apply")
async def apply_tag_changes(
    request: TagApplyRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Apply bulk tag operations.

    Creates a background job to update tags on selected APs.
    Returns a job_id for tracking progress via the workflow job system.
    """
    controller = db.query(Controller).filter(
        Controller.id == request.controller_id,
        Controller.user_id == current_user.id
    ).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    effective_tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

    tags = parse_tags(request.tags)
    if not tags:
        raise HTTPException(status_code=400, detail="At least one tag is required")

    if not request.ap_serials:
        raise HTTPException(status_code=400, detail="No APs selected")

    job_id = str(uuid.uuid4())

    job = WorkflowJobV2(
        id=job_id,
        workflow_name=WORKFLOW_NAME,
        user_id=current_user.id,
        controller_id=request.controller_id,
        venue_id=request.venue_id,
        tenant_id=effective_tenant_id,
        options={
            "max_concurrent": request.max_concurrent,
        },
        input_data={
            "mode": request.mode.value,
            "tags": tags,
            "ap_serials": request.ap_serials,
            "total_aps": len(request.ap_serials),
        },
        phase_definitions=[
            PhaseDefinitionV2(
                id="update_tags",
                name="Update AP Tags",
                executor='routers.bulk_ap_tagging.bulk_ap_tagging_router.run_tagging_workflow_background',
                critical=True,
                per_unit=False,
            )
        ],
        global_phase_status={'update_tags': PhaseStatus.PENDING},
    )

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    await state_manager.save_job(job)

    logger.info(f"Created bulk AP tagging job {job_id} for {len(request.ap_serials)} APs (mode={request.mode.value})")

    background_tasks.add_task(
        run_tagging_workflow_background,
        job,
        request.controller_id,
        request.mode,
        tags,
        request.ap_serials,
        request.max_concurrent,
    )

    return {
        "job_id": job_id,
        "status": JobStatus.RUNNING,
        "message": f"Bulk AP tagging started for {len(request.ap_serials)} APs. Poll /jobs/{job_id}/status for progress."
    }


# ============================================================================
# Background Workflow
# ============================================================================

async def run_tagging_workflow_background(
    job: WorkflowJobV2,
    controller_id: int,
    mode: TagMode,
    tags: List[str],
    ap_serials: List[str],
    max_concurrent: int,
):
    """Background task to execute bulk AP tag operations."""
    from database import SessionLocal

    db = SessionLocal()

    try:
        logger.info(f"Starting bulk AP tagging job {job.id} (mode={mode.value}, {len(ap_serials)} APs)")

        r1_client = create_r1_client_from_controller(controller_id, db)

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        # Update job status
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.global_phase_status['update_tags'] = PhaseStatus.RUNNING
        await state_manager.save_job(job)

        await event_publisher.publish_event(job.id, "phase_started", {
            "phase_id": "update_tags",
            "phase_name": "Update AP Tags",
        })

        # Fetch all venue APs once to get current tags
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(job.tenant_id, job.venue_id)
        all_aps = aps_response.get("data", [])
        ap_lookup = {ap.get("serialNumber"): ap for ap in all_aps}

        results = {
            "updated": [],
            "failed": [],
            "unchanged": [],
        }

        semaphore = asyncio.Semaphore(max_concurrent)
        completed = 0
        total = len(ap_serials)

        async def update_single_ap(serial: str):
            nonlocal completed

            async with semaphore:
                try:
                    ap = ap_lookup.get(serial)
                    if not ap:
                        results["failed"].append({
                            "serial": serial,
                            "error": "AP not found in venue",
                        })
                        return

                    current_tags = ap.get("tags") or []
                    if isinstance(current_tags, str):
                        current_tags = [t.strip() for t in current_tags.split(";") if t.strip()]

                    new_tags, added, removed, error = compute_tag_changes(current_tags, tags, mode)

                    # Skip if error (e.g. would exceed limit)
                    if error:
                        results["failed"].append({
                            "serial": serial,
                            "ap_name": ap.get("name", ""),
                            "current_tags": current_tags,
                            "error": error,
                        })
                        return

                    # Skip if unchanged
                    if sorted(new_tags) == sorted(current_tags):
                        results["unchanged"].append({
                            "serial": serial,
                            "ap_name": ap.get("name", ""),
                            "tags": current_tags,
                        })
                        return

                    logger.debug(f"Updating tags for AP {serial}: {current_tags} -> {new_tags}")

                    await r1_client.venues.update_ap(
                        tenant_id=job.tenant_id,
                        venue_id=job.venue_id,
                        serial_number=serial,
                        name=ap.get("name"),
                        tags=new_tags,
                        wait_for_completion=True,
                    )

                    results["updated"].append({
                        "serial": serial,
                        "ap_name": ap.get("name", ""),
                        "old_tags": current_tags,
                        "new_tags": new_tags,
                    })

                except Exception as e:
                    logger.error(f"Failed to update tags for AP {serial}: {e}")
                    results["failed"].append({
                        "serial": serial,
                        "ap_name": ap_lookup.get(serial, {}).get("name", ""),
                        "error": str(e),
                    })

                finally:
                    completed += 1

                    percent = int((completed / total) * 100)
                    await event_publisher.publish_event(job.id, "progress", {
                        "total_tasks": total,
                        "completed": completed,
                        "updated": len(results["updated"]),
                        "failed": len(results["failed"]),
                        "unchanged": len(results["unchanged"]),
                        "pending": total - completed,
                        "percent": percent,
                    })

                    job.global_phase_results['update_tags'] = results
                    await state_manager.save_job(job)

        # Run all updates with concurrency limit
        tasks = [update_single_ap(serial) for serial in ap_serials]
        await asyncio.gather(*tasks)

        # Update job with final results
        job.global_phase_status['update_tags'] = PhaseStatus.COMPLETED
        job.global_phase_results['update_tags'] = results
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()

        await state_manager.save_job(job)
        await event_publisher.job_completed(job)

        logger.info(
            f"Bulk AP tagging job {job.id} completed: "
            f"updated={len(results['updated'])}, "
            f"failed={len(results['failed'])}, "
            f"unchanged={len(results['unchanged'])}"
        )

    except Exception as e:
        logger.error(f"Bulk AP tagging job {job.id} failed: {e}", exc_info=True)

        job.status = JobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.errors.append(str(e))
        job.global_phase_status['update_tags'] = PhaseStatus.FAILED

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        await state_manager.save_job(job)
        await event_publisher.job_failed(job)

    finally:
        db.close()
