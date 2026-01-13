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

import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.models import WorkflowJob, Phase, JobStatus
from workflow.state_manager import RedisStateManager
from workflow.executor import TaskExecutor
from workflow.engine import WorkflowEngine
from workflow.events import WorkflowEventPublisher
from routers.per_unit_ssid.workflow_definition import get_workflow_definition

# Import phase executors
from routers.per_unit_ssid.phases import create_ssids
from routers.per_unit_ssid.phases import activate_ssids
from routers.per_unit_ssid.phases import create_ap_groups
from routers.per_unit_ssid.phases import process_units
from routers.per_unit_ssid.phases import configure_lan_ports

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/per-unit-ssid",
    tags=["Per-Unit SSID Configuration"]
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


class UnitConfig(BaseModel):
    """Configuration for a single unit"""
    unit_number: str = Field(..., description="Unit number (e.g., '101', '102')")
    ap_identifiers: List[str] = Field(default_factory=list, description="List of AP serial numbers or names in this unit")
    ssid_name: str = Field(..., description="SSID broadcast name for this unit (what clients see)")
    network_name: Optional[str] = Field(default=None, description="Internal network name in R1 (defaults to ssid_name if not provided)")
    ssid_password: str = Field(..., description="Unique password for this unit's SSID")
    security_type: str = Field(default="WPA3", description="Security type: WPA2, WPA3, or WPA2/WPA3")
    default_vlan: str = Field(default="1", description="Default VLAN ID for this SSID (e.g., '1', '10', '100')")


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
    # Phase 5: LAN port configuration for APs with configurable ports
    configure_lan_ports: bool = Field(default=False, description="Configure LAN port VLANs on APs with configurable LAN ports")
    model_port_configs: ModelPortConfigs = Field(
        default_factory=ModelPortConfigs,
        description="LAN port configuration matrix organized by model type (2-port vs 4-port)"
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


# ==================== Helper Functions ====================

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


async def run_workflow_background(
    job: WorkflowJob,
    controller_id: int,
    db: Session
):
    """Background task to run per-unit-ssid workflow"""
    try:
        logger.info(f"Starting background workflow for job {job.id}")

        # Create R1 client
        r1_client = create_r1_client_from_controller(controller_id, db)

        # Create workflow components
        redis_client = await get_redis_client()
        state_manager = RedisStateManager(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)
        task_executor = TaskExecutor(
            max_retries=3,
            retry_backoff_base=2,
            r1_client=r1_client,
            event_publisher=event_publisher
        )
        workflow_engine = WorkflowEngine(state_manager, task_executor, event_publisher)

        # Phase executor mapping
        phase_executors = {
            'create_ssids': create_ssids.execute,
            'activate_ssids': activate_ssids.execute,
            'create_ap_groups': create_ap_groups.execute,
            'process_units': process_units.execute,
            'configure_lan_ports': configure_lan_ports.execute
        }

        logger.info(f"Workflow: {job.workflow_name}")
        logger.info(f"Phase executors mapped: {list(phase_executors.keys())}")

        # Execute workflow
        final_job = await workflow_engine.execute_workflow(job, phase_executors)

        logger.info(f"Workflow {job.id} completed with status: {final_job.status}")

    except Exception as e:
        logger.exception(f"Workflow {job.id} failed: {str(e)}")


# ==================== API Endpoints ====================

@router.post("/configure", response_model=ConfigureResponse)
async def configure_per_unit_ssids(
    request: PerUnitSSIDRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Configure per-unit SSIDs in RuckusONE

    This endpoint automates the SmartZone -> RuckusONE migration pattern for per-unit SSIDs:
    - In SmartZone: Used WLAN Groups to assign different SSIDs to different APs
    - In RuckusONE: Uses AP Groups + SSID assignments to achieve the same result

    Process (runs as background workflow):
    1. Create SSID for each unit (if it doesn't exist)
    2. Activate SSID on venue (required before AP Group activation)
    3. Create AP Group for each unit (if it doesn't exist)
    4. Find and assign APs to their unit's AP Group, then activate SSID on the group

    Returns immediately with job_id for status polling via /jobs/{job_id}/status
    """
    logger.info(f"Per-unit SSID configuration request - controller: {request.controller_id}, venue: {request.venue_id}, units: {len(request.units)}")

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

    logger.info(f"Controller validated - type: {controller.controller_type}, tenant: {tenant_id}")

    # Create workflow job
    job_id = str(uuid.uuid4())
    logger.info(f"Generated job ID: {job_id}")

    workflow_def = get_workflow_definition(configure_lan_ports=request.configure_lan_ports)
    logger.info(f"Workflow definition loaded: {workflow_def.name} ({len(workflow_def.phases)} phases)")

    # Create phases from definition
    phases = [
        Phase(
            id=phase_def.id,
            name=phase_def.name,
            dependencies=phase_def.dependencies,
            parallelizable=phase_def.parallelizable,
            critical=phase_def.critical,
            skip_condition=phase_def.skip_condition
        )
        for phase_def in workflow_def.phases
    ]
    logger.info(f"Created {len(phases)} phases")

    # Convert units to dict format for workflow
    units_data = [unit.model_dump() for unit in request.units]

    # Convert model port configs to dict format for workflow
    model_port_configs_data = request.model_port_configs.model_dump()

    job = WorkflowJob(
        id=job_id,
        workflow_name=workflow_def.name,
        user_id=current_user.id,
        controller_id=request.controller_id,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        options={
            'ap_group_prefix': request.ap_group_prefix,
            'ap_group_postfix': request.ap_group_postfix,
            'name_conflict_resolution': request.name_conflict_resolution,
            'configure_lan_ports': request.configure_lan_ports,
            'model_port_configs': model_port_configs_data
        },
        input_data={
            'units': units_data,
            'ap_group_prefix': request.ap_group_prefix,
            'ap_group_postfix': request.ap_group_postfix,
            'name_conflict_resolution': request.name_conflict_resolution,
            'configure_lan_ports': request.configure_lan_ports,
            'model_port_configs': model_port_configs_data
        },
        phases=phases
    )
    logger.info("Created WorkflowJob object")

    # Save initial job state
    logger.info("Connecting to Redis...")
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)
    logger.info("Saving job to Redis...")
    await state_manager.save_job(job)
    logger.info("Job saved to Redis")

    # Start workflow in background
    logger.info("Starting background workflow task...")
    background_tasks.add_task(run_workflow_background, job, request.controller_id, db)

    logger.info(f"Workflow job {job_id} created and queued")

    return ConfigureResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        message=f"Per-unit SSID configuration started for {len(request.units)} units. Poll /jobs/{job_id}/status for progress."
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
    from routers.per_unit_ssid.phases.configure_lan_ports import (
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
