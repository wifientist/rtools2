"""
AP Rename Router

Bulk tool for renaming access points with three rename modes:
1. CSV mapping: Upload serial → new_name mappings
2. Regex: Find/replace patterns in existing names
3. Template: Generate names from template patterns

Workflow:
1. GET /download-csv - Download current APs as CSV to edit offline
2. POST /preview - Preview changes (dry run) before applying
3. POST /apply - Apply the renames with batch processing
"""

import asyncio
import csv
import io
import re
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from r1api.client import R1Client
from clients.r1_client import get_dynamic_r1_client, create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.user import User
from models.controller import Controller
from sqlalchemy.orm import Session
from redis_client import get_redis_client

# Workflow job framework imports (V2)
from workflow.v2.models import WorkflowJobV2, JobStatus, PhaseStatus, PhaseDefinitionV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap-rename", tags=["AP Rename"])


# ============================================================================
# Enums and Models
# ============================================================================

class RenameMode(str, Enum):
    CSV = "csv"           # Direct CSV mapping: serial -> new_name
    REGEX = "regex"       # Regex find/replace on current names
    TEMPLATE = "template" # Template pattern with variables


class APRenameItem(BaseModel):
    """Single AP rename operation"""
    serial_number: str
    current_name: str
    new_name: str


class CSVMappingInput(BaseModel):
    """CSV-based rename: direct serial → name mapping"""
    mappings: List[Dict[str, str]] = Field(
        description="List of {serial_number, new_name} mappings"
    )


class RegexRenameInput(BaseModel):
    """Regex-based rename: find/replace pattern"""
    pattern: str = Field(description="Regex pattern to match in current AP names")
    replacement: str = Field(
        description="Replacement string (supports backreferences like \\1, \\2)"
    )
    filter_pattern: Optional[str] = Field(
        default=None,
        description="Optional regex to filter which APs to rename (only rename matching APs)"
    )


class TemplateRenameInput(BaseModel):
    """Template-based rename: generate names from pattern"""
    template: str = Field(
        description="Template pattern, e.g., '{prefix}-{seq:03d}' or '{building}-FL{floor}-AP{seq}'"
    )
    variables: Dict[str, Any] = Field(
        default={},
        description="Variables to substitute in template (prefix, building, floor, etc.)"
    )
    start_seq: int = Field(
        default=1,
        description="Starting sequence number for {seq} variable"
    )
    filter_pattern: Optional[str] = Field(
        default=None,
        description="Optional regex to filter which APs to rename"
    )
    sort_by: str = Field(
        default="name",
        description="Sort APs by 'name' or 'serial' before applying sequence numbers"
    )


class PreviewRequest(BaseModel):
    """Request to preview AP rename operations"""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    mode: RenameMode
    csv_input: Optional[CSVMappingInput] = None
    regex_input: Optional[RegexRenameInput] = None
    template_input: Optional[TemplateRenameInput] = None


class ApplyRequest(BaseModel):
    """Request to apply AP rename operations"""
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None
    renames: List[APRenameItem] = Field(
        description="List of rename operations from preview"
    )
    max_concurrent: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max concurrent API calls"
    )


class PreviewResponse(BaseModel):
    """Response from preview endpoint"""
    mode: RenameMode
    total_aps: int
    rename_count: int
    unchanged_count: int
    renames: List[APRenameItem]
    unchanged: List[Dict[str, str]]
    errors: List[str] = []


# ============================================================================
# Workflow Constants
# ============================================================================

WORKFLOW_NAME = "ap_rename_batch"


# ============================================================================
# Helper Functions
# ============================================================================

def apply_regex_rename(
    aps: List[Dict],
    pattern: str,
    replacement: str,
    filter_pattern: Optional[str] = None
) -> tuple[List[APRenameItem], List[Dict], List[str]]:
    """Apply regex find/replace to AP names"""
    renames = []
    unchanged = []
    errors = []

    try:
        regex = re.compile(pattern)
        filter_regex = re.compile(filter_pattern) if filter_pattern else None
    except re.error as e:
        errors.append(f"Invalid regex pattern: {e}")
        return [], [{"serial": ap.get("serialNumber"), "name": ap.get("name")} for ap in aps], errors

    for ap in aps:
        serial = ap.get("serialNumber", "")
        current_name = ap.get("name", "")

        # Check filter if provided
        if filter_regex and not filter_regex.search(current_name):
            unchanged.append({"serial": serial, "name": current_name, "reason": "Did not match filter"})
            continue

        # Apply regex replacement
        new_name = regex.sub(replacement, current_name)

        if new_name != current_name:
            renames.append(APRenameItem(
                serial_number=serial,
                current_name=current_name,
                new_name=new_name
            ))
        else:
            unchanged.append({"serial": serial, "name": current_name, "reason": "Pattern did not match"})

    return renames, unchanged, errors


def apply_template_rename(
    aps: List[Dict],
    template: str,
    variables: Dict[str, Any],
    start_seq: int = 1,
    filter_pattern: Optional[str] = None,
    sort_by: str = "name"
) -> tuple[List[APRenameItem], List[Dict], List[str]]:
    """Apply template pattern to generate new AP names"""
    renames = []
    unchanged = []
    errors = []

    try:
        filter_regex = re.compile(filter_pattern) if filter_pattern else None
    except re.error as e:
        errors.append(f"Invalid filter regex: {e}")
        return [], [{"serial": ap.get("serialNumber"), "name": ap.get("name")} for ap in aps], errors

    # Filter APs first
    filtered_aps = []
    for ap in aps:
        current_name = ap.get("name", "")
        if filter_regex and not filter_regex.search(current_name):
            unchanged.append({"serial": ap.get("serialNumber"), "name": current_name, "reason": "Did not match filter"})
        else:
            filtered_aps.append(ap)

    # Sort filtered APs
    if sort_by == "serial":
        filtered_aps.sort(key=lambda x: x.get("serialNumber", ""))
    else:
        filtered_aps.sort(key=lambda x: x.get("name", ""))

    # Apply template to each AP
    seq = start_seq
    for ap in filtered_aps:
        serial = ap.get("serialNumber", "")
        current_name = ap.get("name", "")

        try:
            # Build format variables
            format_vars = {
                **variables,
                "seq": seq,
                "serial": serial,
                "current_name": current_name,
            }

            # Handle format specifiers like {seq:03d}
            new_name = template.format(**format_vars)

            if new_name != current_name:
                renames.append(APRenameItem(
                    serial_number=serial,
                    current_name=current_name,
                    new_name=new_name
                ))
            else:
                unchanged.append({"serial": serial, "name": current_name, "reason": "Name unchanged"})

            seq += 1

        except KeyError as e:
            errors.append(f"Missing template variable for AP {serial}: {e}")
            unchanged.append({"serial": serial, "name": current_name, "reason": f"Template error: {e}"})
        except Exception as e:
            errors.append(f"Template error for AP {serial}: {e}")
            unchanged.append({"serial": serial, "name": current_name, "reason": f"Error: {e}"})

    return renames, unchanged, errors


def apply_csv_mapping(
    aps: List[Dict],
    mappings: List[Dict[str, str]]
) -> tuple[List[APRenameItem], List[Dict], List[str]]:
    """Apply CSV serial → name mappings"""
    renames = []
    unchanged = []
    errors = []

    # Build lookup from serial to new_name
    serial_to_new_name = {}
    for m in mappings:
        serial = m.get("serial_number") or m.get("serial") or m.get("serialNumber")
        new_name = m.get("new_name") or m.get("name")
        if serial and new_name:
            serial_to_new_name[serial.strip()] = new_name.strip()

    # Build lookup from current APs
    ap_by_serial = {ap.get("serialNumber"): ap for ap in aps}

    # Process each mapping
    for serial, new_name in serial_to_new_name.items():
        ap = ap_by_serial.get(serial)
        if not ap:
            errors.append(f"AP with serial {serial} not found in venue")
            continue

        current_name = ap.get("name", "")
        if new_name != current_name:
            renames.append(APRenameItem(
                serial_number=serial,
                current_name=current_name,
                new_name=new_name
            ))
        else:
            unchanged.append({"serial": serial, "name": current_name, "reason": "Name unchanged"})

    # Mark APs not in mapping as unchanged
    for ap in aps:
        serial = ap.get("serialNumber")
        if serial not in serial_to_new_name:
            unchanged.append({
                "serial": serial,
                "name": ap.get("name", ""),
                "reason": "Not in mapping"
            })

    return renames, unchanged, errors


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/{controller_id}/venue/{venue_id}/download-csv")
async def download_csv(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Download current APs as CSV for offline editing.

    Returns CSV with columns: serial_number, current_name, new_name
    Edit the new_name column and use /preview with CSV mode to preview changes.
    """
    # Validate controller access
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == current_user.id
    ).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    # Get effective tenant ID
    effective_tenant_id = tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")

    # Create R1 client and fetch APs
    r1_client = create_r1_client_from_controller(controller_id, db)

    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(effective_tenant_id, venue_id)
        aps = aps_response.get("data", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {e}")

    # Sort by name
    aps.sort(key=lambda x: x.get("name", ""))

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["serial_number", "current_name", "new_name"])

    for ap in aps:
        writer.writerow([
            ap.get("serialNumber", ""),
            ap.get("name", ""),
            ap.get("name", "")  # Default new_name to current name
        ])

    output.seek(0)

    # Return as downloadable CSV
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=ap_names_{venue_id}.csv"
        }
    )


@router.get("/{controller_id}/venue/{venue_id}/aps")
async def get_venue_aps(
    controller_id: int,
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all APs in a venue for the rename tool.

    Returns AP list with serial, name, model, status.
    """
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

    # Sort by name
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
            }
            for ap in aps
        ]
    }


@router.post("/preview")
async def preview_renames(
    request: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Preview AP rename operations without applying.

    Supports three modes:
    - **csv**: Direct serial → name mapping from uploaded/pasted CSV
    - **regex**: Find/replace pattern applied to current names
    - **template**: Generate names from template pattern

    Returns list of proposed renames for review before applying.
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

    r1_client = create_r1_client_from_controller(request.controller_id, db)

    # Fetch current APs
    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(effective_tenant_id, request.venue_id)
        aps = aps_response.get("data", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {e}")

    if not aps:
        raise HTTPException(status_code=404, detail="No APs found in venue")

    # Apply rename logic based on mode
    renames: List[APRenameItem] = []
    unchanged: List[Dict] = []
    errors: List[str] = []

    if request.mode == RenameMode.CSV:
        if not request.csv_input:
            raise HTTPException(status_code=400, detail="csv_input required for CSV mode")
        renames, unchanged, errors = apply_csv_mapping(aps, request.csv_input.mappings)

    elif request.mode == RenameMode.REGEX:
        if not request.regex_input:
            raise HTTPException(status_code=400, detail="regex_input required for REGEX mode")
        renames, unchanged, errors = apply_regex_rename(
            aps,
            request.regex_input.pattern,
            request.regex_input.replacement,
            request.regex_input.filter_pattern
        )

    elif request.mode == RenameMode.TEMPLATE:
        if not request.template_input:
            raise HTTPException(status_code=400, detail="template_input required for TEMPLATE mode")
        renames, unchanged, errors = apply_template_rename(
            aps,
            request.template_input.template,
            request.template_input.variables,
            request.template_input.start_seq,
            request.template_input.filter_pattern,
            request.template_input.sort_by
        )

    return PreviewResponse(
        mode=request.mode,
        total_aps=len(aps),
        rename_count=len(renames),
        unchanged_count=len(unchanged),
        renames=renames,
        unchanged=unchanged,
        errors=errors
    )


@router.post("/apply")
async def apply_renames(
    request: ApplyRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Apply AP rename operations.

    Takes the renames list from /preview and applies them using batch processing.
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

    if not request.renames:
        raise HTTPException(status_code=400, detail="No renames provided")

    # Generate job ID
    job_id = str(uuid.uuid4())

    # Create V2 workflow job
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
            "renames": [r.dict() for r in request.renames],
            "total_renames": len(request.renames),
        },
        phase_definitions=[
            PhaseDefinitionV2(
                id="rename_aps",
                name="Rename APs",
                executor='routers.ap_rename.ap_rename_router.run_rename_workflow_background',
                critical=True,
                per_unit=False,
            )
        ],
        global_phase_status={'rename_aps': PhaseStatus.PENDING},
    )

    # Save to Redis
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    await state_manager.save_job(job)

    logger.info(f"Created AP rename job {job_id} for {len(request.renames)} APs")

    # Start background workflow
    background_tasks.add_task(
        run_rename_workflow_background,
        job,
        request.controller_id,
        request.renames,
        request.max_concurrent,
    )

    return {
        "job_id": job_id,
        "status": JobStatus.RUNNING,
        "message": f"AP rename started for {len(request.renames)} APs. Poll /jobs/{job_id}/status for progress."
    }


# ============================================================================
# Background Workflow
# ============================================================================

async def run_rename_workflow_background(
    job: WorkflowJobV2,
    controller_id: int,
    renames: List[APRenameItem],
    max_concurrent: int,
):
    """Background task to execute AP rename operations"""
    from database import SessionLocal

    db = SessionLocal()

    try:
        logger.info(f"Starting AP rename job {job.id}")

        r1_client = create_r1_client_from_controller(controller_id, db)

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        # Update job status
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.global_phase_status['rename_aps'] = PhaseStatus.RUNNING
        await state_manager.save_job(job)

        await event_publisher.publish_event(job.id, "phase_started", {
            "phase_id": "rename_aps",
            "phase_name": "Rename APs",
        })

        # Track results
        results = {
            "renamed": [],
            "failed": [],
        }

        # Semaphore for rate limiting
        semaphore = asyncio.Semaphore(max_concurrent)
        completed = 0
        total = len(renames)

        async def rename_single_ap(rename: APRenameItem, task_index: int):
            nonlocal completed

            async with semaphore:
                try:
                    logger.debug(f"Renaming AP {rename.serial_number}: {rename.current_name} -> {rename.new_name}")

                    result = await r1_client.venues.update_ap(
                        tenant_id=job.tenant_id,
                        venue_id=job.venue_id,
                        serial_number=rename.serial_number,
                        name=rename.new_name,
                        wait_for_completion=True
                    )

                    results["renamed"].append({
                        "serial": rename.serial_number,
                        "old_name": rename.current_name,
                        "new_name": rename.new_name,
                    })

                except Exception as e:
                    logger.error(f"Failed to rename AP {rename.serial_number}: {e}")
                    results["failed"].append({
                        "serial": rename.serial_number,
                        "old_name": rename.current_name,
                        "new_name": rename.new_name,
                        "error": str(e),
                    })

                finally:
                    completed += 1

                    # Publish progress
                    percent = int((completed / total) * 100)
                    await event_publisher.publish_event(job.id, "progress", {
                        "total_tasks": total,
                        "completed": completed,
                        "failed": len(results["failed"]),
                        "pending": total - completed,
                        "percent": percent,
                    })

                    # Update phase results
                    job.global_phase_results['rename_aps'] = results
                    await state_manager.save_job(job)

        # Run all renames with concurrency limit
        tasks = [
            rename_single_ap(rename, i)
            for i, rename in enumerate(renames)
        ]
        await asyncio.gather(*tasks)

        # Update job with results
        job.global_phase_status['rename_aps'] = PhaseStatus.COMPLETED
        job.global_phase_results['rename_aps'] = results
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()

        await state_manager.save_job(job)
        await event_publisher.job_completed(job)

        logger.info(f"AP rename job {job.id} completed: renamed={len(results['renamed'])}, failed={len(results['failed'])}")

    except Exception as e:
        logger.error(f"AP rename job {job.id} failed: {e}", exc_info=True)

        job.status = JobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.errors.append(str(e))
        job.global_phase_status['rename_aps'] = PhaseStatus.FAILED

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        await state_manager.save_job(job)
        await event_publisher.job_failed(job)

    finally:
        db.close()


@router.post("/parse-csv")
async def parse_csv_content(
    csv_content: str,
):
    """
    Parse CSV content and return structured mappings.

    Accepts CSV with columns: serial_number, current_name, new_name
    (or variations like serial, name)

    Returns parsed mappings ready for preview endpoint.
    """
    if not csv_content or not csv_content.strip():
        raise HTTPException(status_code=400, detail="CSV content required")

    try:
        reader = csv.DictReader(io.StringIO(csv_content))
        mappings = []

        for row in reader:
            # Handle various column name formats
            serial = (
                row.get("serial_number") or
                row.get("serial") or
                row.get("serialNumber") or
                row.get("Serial Number") or
                row.get("Serial")
            )
            new_name = (
                row.get("new_name") or
                row.get("name") or
                row.get("Name") or
                row.get("New Name")
            )

            if serial and new_name:
                mappings.append({
                    "serial_number": serial.strip(),
                    "new_name": new_name.strip()
                })

        if not mappings:
            raise HTTPException(
                status_code=400,
                detail="No valid mappings found. Expected columns: serial_number, new_name"
            )

        return {
            "parsed_count": len(mappings),
            "mappings": mappings
        }

    except csv.Error as e:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {e}")
