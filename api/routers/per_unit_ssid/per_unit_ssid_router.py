"""
Per-Unit SSID Configuration Router

Automates the creation of per-unit SSIDs in RuckusONE by:
1. Creating SSIDs for each unit
2. Activating SSIDs on the venue
3. Creating AP Groups for each unit
4. Assigning APs to their unit's AP Group
5. Activating SSIDs on their corresponding AP Groups

Uses the workflow engine for background execution with real-time progress tracking.
"""

import asyncio
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher
from routers.per_unit_ssid.workflow_definition import get_workflow_definition

# V1 workflow engine has been removed - use V2 endpoints instead
# /per-unit-ssid/v2/plan, /per-unit-ssid/v2/{id}/confirm

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/per-unit-ssid",
    tags=["Per-Unit SSID"]
)


# ==================== Request/Response Models ====================

class PortConfig(BaseModel):
    """Configuration for a single LAN port on APs with configurable LAN ports"""
    mode: str = Field(default="ignore", description="Port mode: 'ignore' (no changes), 'match' (use unit VLAN), 'specific' (custom VLAN), 'disable' (disable port)")
    vlan: Optional[int] = Field(default=None, description="Custom VLAN ID when mode is 'specific'")


class ModelPortConfigs(BaseModel):
    """Port configurations organized by AP model type"""
    # 1-port models split by uplink location
    one_port_lan1_uplink: List[PortConfig] = Field(
        default_factory=lambda: [PortConfig(mode="uplink"), PortConfig(mode="ignore")],
        description="Config for 1-port models where LAN1 is uplink (R650, R750, etc.): [LAN1=uplink, LAN2=access]"
    )
    one_port_lan2_uplink: List[PortConfig] = Field(
        default_factory=lambda: [PortConfig(mode="ignore"), PortConfig(mode="uplink")],
        description="Config for 1-port models where LAN2 is uplink (R550, R560): [LAN1=access, LAN2=uplink]"
    )
    # 2-port and 4-port models (wall-plate H-series and outdoor T750)
    two_port: List[PortConfig] = Field(
        default_factory=lambda: [PortConfig(mode="ignore"), PortConfig(mode="ignore"), PortConfig(mode="uplink")],
        description="Config for 2-port models (H320/H350): LAN1, LAN2, LAN3=uplink"
    )
    four_port: List[PortConfig] = Field(
        default_factory=lambda: [PortConfig(mode="ignore"), PortConfig(mode="ignore"), PortConfig(mode="ignore"), PortConfig(mode="ignore"), PortConfig(mode="uplink")],
        description="Config for 4-port models (H510/H550/H670): LAN1-4, LAN5=uplink"
    )


class DpskPoolSettings(BaseModel):
    """DPSK pool configuration settings"""
    passphrase_length: int = Field(default=12, description="Length for auto-generated passphrases (not used when CSV provides passphrase)")
    passphrase_format: str = Field(default="KEYBOARD_FRIENDLY", description="Format: NUMBERS_ONLY, KEYBOARD_FRIENDLY, or MOST_SECURED")
    max_devices_per_passphrase: int = Field(default=0, description="Max devices per passphrase (0 = unlimited)")
    expiration_days: Optional[int] = Field(default=None, description="Passphrase expiration in days (None = no expiration)")


class UnitConfig(BaseModel):
    """Configuration for a single unit (or DPSK passphrase row in DPSK mode)"""
    unit_number: str = Field(..., description="Unit number (e.g., '101', '102')")
    ap_identifiers: List[str] = Field(default_factory=list, description="List of AP serial numbers or names in this unit")
    ssid_name: str = Field(..., description="SSID broadcast name for this unit (what clients see)")
    network_name: Optional[str] = Field(default=None, description="Internal network name in R1 (defaults to ssid_name if not provided)")
    ssid_password: Optional[str] = Field(default=None, description="Password for PSK mode (required), or passphrase for DPSK mode (optional - can be in CSV)")
    security_type: str = Field(default="WPA3", description="Security type: WPA2, WPA3, WPA2/WPA3, or DPSK")
    default_vlan: str = Field(default="1", description="Default VLAN ID for this SSID (e.g., '1', '10', '100')")
    # DPSK-specific optional fields (used when security_type is DPSK)
    username: Optional[str] = Field(default=None, description="Identity/passphrase username (DPSK mode)")
    email: Optional[str] = Field(default=None, description="User email for passphrase (DPSK mode)")
    description: Optional[str] = Field(default=None, description="Description for passphrase (DPSK mode)")


class PerUnitSSIDRequest(BaseModel):
    """Request to configure per-unit SSIDs"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID where APs are located")
    units: List[UnitConfig] = Field(..., description="List of unit configurations")
    ap_group_prefix: str = Field(default="", description="Prefix for AP group names (e.g., 'APG-' creates 'APG-101')")
    ap_group_postfix: str = Field(default="", description="Postfix for AP group names (e.g., '-APG' creates '101-APG')")
    # SSID/Network name conflict resolution
    name_conflict_resolution: Literal['keep', 'overwrite'] = Field(
        default='overwrite',
        description="When SSID exists but network name differs: 'keep' (use existing R1 name) or 'overwrite' (update to ruckus.tools name)"
    )
    # DPSK Mode options - single shared pool for entire venue
    dpsk_mode: bool = Field(
        default=False,
        description="Enable DPSK mode - creates one shared Identity Group and DPSK Pool for all unit SSIDs"
    )
    identity_group_name: str = Field(
        default="",
        description="Name for the shared identity group (DPSK mode). Required if dpsk_mode=True."
    )
    dpsk_pool_name: str = Field(
        default="",
        description="Name for the shared DPSK pool (DPSK mode). Required if dpsk_mode=True."
    )
    dpsk_pool_settings: DpskPoolSettings = Field(
        default_factory=DpskPoolSettings,
        description="DPSK pool settings (DPSK mode only)"
    )
    # Phase 5: LAN port configuration for APs with configurable ports
    configure_lan_ports: bool = Field(default=False, description="Configure LAN port VLANs on APs with configurable LAN ports")
    model_port_configs: ModelPortConfigs = Field(
        default_factory=ModelPortConfigs,
        description="LAN port configuration matrix organized by model type (2-port vs 4-port)"
    )
    # Parallel execution options
    parallel_execution: bool = Field(
        default=False,
        description="Execute each unit as a separate parallel workflow (recommended for 5+ units)"
    )
    max_concurrent: int = Field(
        default=10,
        description="Maximum number of units to process in parallel (only used with parallel_execution=True)"
    )
    # Debug options
    debug_delay: float = Field(
        default=0,
        description="Seconds to wait between API calls in Phase 4 (for debugging). Set to 2-5 to slow down and observe."
    )


class ConfigureResponse(BaseModel):
    """Response from configure endpoint"""
    job_id: str
    status: str
    message: str


class VenueAuditRequest(BaseModel):
    """Request to audit a venue's network configuration"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID to audit")


class VenueAuditResponse(BaseModel):
    """Response from venue audit"""
    venue_id: str
    venue_name: str
    total_ap_groups: int
    total_aps: int
    total_ssids: int
    venue_lan_port_settings: List[Dict[str, Any]] = []  # Venue-level default LAN port settings per model
    ap_groups: List[Dict[str, Any]]


class AuditJobStartResponse(BaseModel):
    """Response from starting an audit job"""
    job_id: str
    status: str
    message: str


class AuditJobStatusResponse(BaseModel):
    """Response from audit job status check"""
    job_id: str
    status: str  # PENDING, RUNNING, COMPLETED, FAILED
    message: Optional[str] = None
    progress: Optional[str] = None  # e.g., "Fetching LAN port settings for 30/50 APs..."
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ==================== Constants ====================

# RuckusONE has a default limit of 15 SSIDs per AP Group
# We use a buffer to avoid hitting this limit during parallel execution
SSID_LIMIT_PER_AP_GROUP = 15
SSID_SAFETY_BUFFER = 3  # Safety margin to avoid edge cases


# ==================== Helper Functions ====================

async def count_all_ap_ssids_for_venue(r1_client, tenant_id: str, venue_id: str) -> dict:
    """
    Count SSIDs that are configured for "All AP Groups" in a venue.

    These SSIDs broadcast to ALL AP Groups and count toward the 15 SSID limit
    on every AP Group. During parallel execution, each "in-flight" SSID
    (activated on venue but not yet assigned to specific AP Group) also
    broadcasts to all AP Groups temporarily.

    Args:
        r1_client: R1 API client
        tenant_id: Tenant/EC ID
        venue_id: Venue ID to audit

    Returns:
        Dict with:
        - all_ap_ssids_count: Number of SSIDs with isAllApGroups=True
        - all_ap_ssid_names: List of SSID names with isAllApGroups=True
        - safe_concurrent: Calculated safe max_concurrent value
    """
    try:
        # Fetch all WiFi networks
        networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = networks_response.get('data', []) if isinstance(networks_response, dict) else networks_response

        all_ap_ssids = []

        for network in all_networks:
            venue_ap_groups = network.get('venueApGroups', [])

            for vag in venue_ap_groups:
                if vag.get('venueId') != venue_id:
                    continue

                # Check if this SSID is configured for all AP Groups
                if vag.get('isAllApGroups', False):
                    all_ap_ssids.append({
                        'id': network.get('id'),
                        'name': network.get('name'),
                        'ssid': network.get('ssid')
                    })
                    break  # Only count once per network

        all_ap_count = len(all_ap_ssids)

        # Calculate safe concurrent: 15 - existing_all_ap - buffer
        # Minimum of 1 to always allow at least sequential processing
        safe_concurrent = max(1, SSID_LIMIT_PER_AP_GROUP - all_ap_count - SSID_SAFETY_BUFFER)

        logger.info(f"Venue SSID audit: {all_ap_count} SSIDs with isAllApGroups=True, safe_concurrent={safe_concurrent}")

        return {
            'all_ap_ssids_count': all_ap_count,
            'all_ap_ssid_names': [s.get('name') or s.get('ssid') for s in all_ap_ssids],
            'safe_concurrent': safe_concurrent,
            'ssid_limit': SSID_LIMIT_PER_AP_GROUP,
            'safety_buffer': SSID_SAFETY_BUFFER
        }

    except Exception as e:
        logger.warning(f"Failed to audit venue SSIDs: {str(e)} - defaulting to safe_concurrent=3")
        return {
            'all_ap_ssids_count': -1,  # Unknown
            'all_ap_ssid_names': [],
            'safe_concurrent': 3,  # Conservative default
            'ssid_limit': SSID_LIMIT_PER_AP_GROUP,
            'safety_buffer': SSID_SAFETY_BUFFER,
            'error': str(e)
        }


def validate_controller_access(controller_id: int, user: User, db: Session) -> Controller:
    """Validate user has access to controller"""
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == user.id
    ).first()

    if not controller:
        controller_exists = db.query(Controller).filter(Controller.id == controller_id).first()
        if not controller_exists:
            raise HTTPException(status_code=404, detail=f"Controller {controller_id} not found")
        else:
            raise HTTPException(status_code=403, detail=f"Access denied to controller {controller_id}")

    return controller


# V1 workflow background tasks have been removed
# Use V2 endpoints: /per-unit-ssid/v2/plan and /per-unit-ssid/v2/{id}/confirm


# Redis keys for audit jobs
AUDIT_JOB_PREFIX = "audit_job:"
AUDIT_RESULT_PREFIX = "audit_result:"
AUDIT_TTL_SECONDS = 300  # 5 minutes


async def run_audit_background(
    job_id: str,
    controller_id: int,
    tenant_id: str,
    venue_id: str,
):
    """Background task to run venue audit"""
    import json
    from datetime import datetime
    from database import SessionLocal

    # Immediate feedback that the background task has started
    logger.info(f"[Audit {job_id}] Background task started for venue {venue_id}")

    redis_client = await get_redis_client()

    async def update_progress(message: str):
        """Update job progress in Redis"""
        job_data = await redis_client.get(f"{AUDIT_JOB_PREFIX}{job_id}")
        if job_data:
            job = json.loads(job_data)
            job['progress'] = message
            await redis_client.setex(
                f"{AUDIT_JOB_PREFIX}{job_id}",
                AUDIT_TTL_SECONDS,
                json.dumps(job)
            )
        logger.info(f"[Audit {job_id}] {message}")

    # Create a fresh database session for this background task
    db = SessionLocal()

    try:
        logger.info(f"[Audit {job_id}] Updating status to RUNNING...")

        # Update status to RUNNING
        job_data = {
            'job_id': job_id,
            'status': 'RUNNING',
            'message': 'Audit in progress...',
            'progress': 'Starting audit...',
            'started_at': datetime.utcnow().isoformat(),
            'completed_at': None
        }
        await redis_client.setex(
            f"{AUDIT_JOB_PREFIX}{job_id}",
            AUDIT_TTL_SECONDS,
            json.dumps(job_data)
        )

        # Create R1 client
        logger.info(f"[Audit {job_id}] Creating R1 client...")
        r1_client = create_r1_client_from_controller(controller_id, db)
        logger.info(f"[Audit {job_id}] R1 client created, starting venue summary fetch...")

        await update_progress("Fetching venue details...")

        # Get comprehensive venue network summary
        summary = await r1_client.venues.get_venue_network_summary(tenant_id, venue_id)

        await update_progress(f"Audit complete: {len(summary['ap_groups'])} AP groups, {summary['total_aps']} APs")

        # Build response
        result = {
            'venue_id': venue_id,
            'venue_name': summary['venue'].get('name', 'Unknown'),
            'total_ap_groups': summary['total_ap_groups'],
            'total_aps': summary['total_aps'],
            'total_ssids': summary['total_ssids'],
            'venue_lan_port_settings': summary.get('venue_lan_port_settings', []),
            'ap_groups': summary['ap_groups']
        }

        # Store result in Redis
        await redis_client.setex(
            f"{AUDIT_RESULT_PREFIX}{job_id}",
            AUDIT_TTL_SECONDS,
            json.dumps(result)
        )

        # Update job status to COMPLETED
        job_data['status'] = 'COMPLETED'
        job_data['message'] = f"Audit complete: {summary['total_ap_groups']} AP groups, {summary['total_aps']} APs"
        job_data['progress'] = None
        job_data['completed_at'] = datetime.utcnow().isoformat()
        await redis_client.setex(
            f"{AUDIT_JOB_PREFIX}{job_id}",
            AUDIT_TTL_SECONDS,
            json.dumps(job_data)
        )

        logger.info(f"Audit job {job_id} completed successfully")

    except Exception as e:
        logger.exception(f"Audit job {job_id} failed: {str(e)}")

        # Update job status to FAILED
        job_data = {
            'job_id': job_id,
            'status': 'FAILED',
            'message': f"Audit failed: {str(e)}",
            'progress': None,
            'started_at': job_data.get('started_at') if 'job_data' in dir() else None,
            'completed_at': datetime.utcnow().isoformat()
        }
        await redis_client.setex(
            f"{AUDIT_JOB_PREFIX}{job_id}",
            AUDIT_TTL_SECONDS,
            json.dumps(job_data)
        )

    finally:
        # Always close the database session
        db.close()


# ==================== API Endpoints ====================

@router.post("/configure", response_model=ConfigureResponse)
async def configure_per_unit_ssids(
    request: PerUnitSSIDRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    DEPRECATED: Use V2 endpoints instead.

    V1 workflow engine has been removed. Use the V2 workflow endpoints:
    - POST /per-unit-ssid/v2/plan - Create job and run validation
    - POST /per-unit-ssid/v2/{id}/confirm - Confirm and start execution

    V2 provides:
    - Per-unit parallel execution (faster)
    - Dry-run validation before execution
    - Better progress tracking
    - Workflow graph visualization
    """
    raise HTTPException(
        status_code=410,  # Gone
        detail={
            "error": "V1 workflow engine has been removed",
            "message": "Please use V2 endpoints for per-unit SSID configuration",
            "v2_plan_endpoint": "POST /per-unit-ssid/v2/plan",
            "v2_confirm_endpoint": "POST /per-unit-ssid/v2/{job_id}/confirm",
            "docs": "V2 provides dry-run validation, per-unit parallelism, and workflow visualization"
        }
    )


@router.post("/audit", response_model=VenueAuditResponse)
async def audit_venue(
    request: VenueAuditRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Audit a venue's network configuration

    Returns a comprehensive view of:
    - All AP Groups in the venue
    - APs assigned to each group
    - SSIDs activated on each group

    This is useful for understanding the current state before making changes.
    """
    logger.info(f"Venue audit request - controller: {request.controller_id}, venue: {request.venue_id}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    # Create R1 client
    r1_client = create_r1_client_from_controller(controller.id, db)

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers"
        )

    logger.info(f"Controller validated - type: {controller.controller_type}, tenant: {tenant_id}")

    try:
        # Get comprehensive venue network summary
        summary = await r1_client.venues.get_venue_network_summary(tenant_id, request.venue_id)

        logger.info(f"Summary contains {len(summary['ap_groups'])} AP Groups")

        # Build response
        response = VenueAuditResponse(
            venue_id=request.venue_id,
            venue_name=summary['venue'].get('name', 'Unknown'),
            total_ap_groups=summary['total_ap_groups'],
            total_aps=summary['total_aps'],
            total_ssids=summary['total_ssids'],
            venue_lan_port_settings=summary.get('venue_lan_port_settings', []),
            ap_groups=summary['ap_groups']
        )

        return response

    except Exception as e:
        logger.error(f"Audit failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to audit venue: {str(e)}"
        )


@router.post("/audit/start", response_model=AuditJobStartResponse)
async def start_audit_job(
    request: VenueAuditRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Start an asynchronous venue audit job.

    This endpoint starts the audit in the background and returns immediately
    with a job_id. Use /audit/{job_id}/status to check progress and
    /audit/{job_id}/result to get the results once completed.

    Results are cached in Redis for 5 minutes after completion.
    """
    import json

    logger.info(f"Starting audit job - controller: {request.controller_id}, venue: {request.venue_id}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers"
        )

    # Generate job ID
    job_id = str(uuid.uuid4())
    logger.info(f"[Audit {job_id}] Created audit job for venue {request.venue_id}")

    # Create initial job state in Redis
    redis_client = await get_redis_client()
    job_data = {
        'job_id': job_id,
        'status': 'PENDING',
        'message': 'Audit job queued...',
        'progress': None,
        'started_at': None,
        'completed_at': None
    }
    await redis_client.setex(
        f"{AUDIT_JOB_PREFIX}{job_id}",
        AUDIT_TTL_SECONDS,
        json.dumps(job_data)
    )

    # Start audit in background (creates its own db session)
    logger.info(f"[Audit {job_id}] Queuing background task...")
    background_tasks.add_task(
        run_audit_background,
        job_id,
        request.controller_id,
        tenant_id,
        request.venue_id,
    )
    logger.info(f"[Audit {job_id}] Background task queued, returning response")

    return AuditJobStartResponse(
        job_id=job_id,
        status="PENDING",
        message=f"Audit job started. Poll /per-unit-ssid/audit/{job_id}/status for progress."
    )


@router.get("/audit/{job_id}/status", response_model=AuditJobStatusResponse)
async def get_audit_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the status of an audit job.

    Returns the current status (PENDING, RUNNING, COMPLETED, FAILED)
    and any progress messages.
    """
    import json

    redis_client = await get_redis_client()
    job_data = await redis_client.get(f"{AUDIT_JOB_PREFIX}{job_id}")

    if not job_data:
        raise HTTPException(
            status_code=404,
            detail=f"Audit job {job_id} not found or expired"
        )

    job = json.loads(job_data)
    return AuditJobStatusResponse(**job)


@router.get("/audit/{job_id}/result", response_model=VenueAuditResponse)
async def get_audit_job_result(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get the result of a completed audit job.

    Returns the full audit data if the job is completed.
    Raises 404 if job not found or 400 if job not yet completed.
    """
    import json

    redis_client = await get_redis_client()

    # First check job status
    job_data = await redis_client.get(f"{AUDIT_JOB_PREFIX}{job_id}")
    if not job_data:
        raise HTTPException(
            status_code=404,
            detail=f"Audit job {job_id} not found or expired"
        )

    job = json.loads(job_data)

    if job['status'] == 'FAILED':
        raise HTTPException(
            status_code=400,
            detail=job.get('message', 'Audit failed')
        )

    if job['status'] != 'COMPLETED':
        raise HTTPException(
            status_code=400,
            detail=f"Audit job not yet completed (status: {job['status']})"
        )

    # Get the result
    result_data = await redis_client.get(f"{AUDIT_RESULT_PREFIX}{job_id}")
    if not result_data:
        raise HTTPException(
            status_code=404,
            detail=f"Audit result for job {job_id} not found or expired"
        )

    result = json.loads(result_data)
    return VenueAuditResponse(**result)


@router.get("/port-config-metadata")
async def get_port_config_metadata(
    current_user: User = Depends(get_current_user),
):
    """
    Get LAN port configuration metadata.

    Returns the authoritative mapping of:
    - MODEL_UPLINK_PORTS: Which port is the uplink for each AP model
    - MODEL_PORT_COUNTS: How many configurable LAN ports each model has

    This endpoint is the single source of truth for port configuration,
    used by the frontend to render the port configuration UI correctly.
    """
    from r1api.models import (
        MODEL_UPLINK_PORTS,
        MODEL_PORT_COUNTS,
    )

    # Group models by their uplink port for easier frontend rendering
    models_by_uplink = {}
    for model, uplink_port in MODEL_UPLINK_PORTS.items():
        if uplink_port not in models_by_uplink:
            models_by_uplink[uplink_port] = []
        models_by_uplink[uplink_port].append(model)

    # Group models by port count
    models_by_port_count = {}
    for model, count in MODEL_PORT_COUNTS.items():
        if count not in models_by_port_count:
            models_by_port_count[count] = []
        models_by_port_count[count].append(model)

    # All models in MODEL_PORT_COUNTS have configurable ports
    configurable_models = list(MODEL_PORT_COUNTS.keys())

    return {
        "model_uplink_ports": MODEL_UPLINK_PORTS,
        "model_port_counts": MODEL_PORT_COUNTS,
        "configurable_models": configurable_models,  # Replaces wall_plate_models
        "models_by_uplink": models_by_uplink,
        "models_by_port_count": models_by_port_count,
        # Derived info for frontend rendering
        "port_categories": {
            "lan1_uplink": [m for m, p in MODEL_UPLINK_PORTS.items() if p == "LAN1"],
            "lan2_uplink": [m for m, p in MODEL_UPLINK_PORTS.items() if p == "LAN2"],
            "lan3_uplink": [m for m, p in MODEL_UPLINK_PORTS.items() if p == "LAN3"],
            "lan5_uplink": [m for m, p in MODEL_UPLINK_PORTS.items() if p == "LAN5"],
        }
    }
