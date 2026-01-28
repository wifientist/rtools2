"""
AP Port Configuration Router

Standalone API for configuring AP LAN ports without the Per-Unit SSID workflow.
Useful for:
- Bulk VLAN configuration on APs
- MDU/venue-wide port standardization
- Post-deployment port adjustments

Supports two modes:
- Standard: Sequential processing for small batches (< 10 APs)
- Batch: Parallel processing with workflow job framework for large batches (10+ APs)

Uses the shared ap_port_config service that's also used by Per-Unit SSID Phase 5.
Batch mode uses the same WorkflowJob framework as Per-Unit SSID for consistency.
"""

import asyncio
import logging
import uuid
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from r1api.client import R1Client
from clients.r1_client import get_dynamic_r1_client
from dependencies import get_current_user, get_db
from models.user import User
from models.controller import Controller
from sqlalchemy.orm import Session
from services.ap_port_config import (
    configure_ap_ports,
    audit_ap_ports,
    get_port_metadata,
    APPortRequest,
    PortConfig,
    PortMode,
)
from services.ap_port_config_batch import (
    configure_ap_ports_batch,
    BatchConfig,
    BatchProgress,
)
from redis_client import get_redis_client

# Workflow job framework imports
from workflow.models import WorkflowJob, Phase, Task, JobStatus, TaskStatus, PhaseStatus
from workflow.state_manager import RedisStateManager
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap-port-config/{controller_id}", tags=["AP Port Config"])


# ============================================================================
# Pydantic Models for API
# ============================================================================

class PortConfigInput(BaseModel):
    """Configuration for a single port"""
    mode: str = Field(
        default="ignore",
        description="Port mode: ignore, specific, disable, uplink"
    )
    vlan: Optional[int] = Field(
        default=None,
        description="VLAN ID (required for 'specific' mode)"
    )

    def to_port_config(self) -> PortConfig:
        return PortConfig(
            mode=PortMode(self.mode),
            vlan=self.vlan
        )


class APConfigInput(BaseModel):
    """Configuration for a single AP's ports"""
    ap_identifier: str = Field(
        description="AP name or serial number"
    )
    lan1: Optional[PortConfigInput] = Field(
        default=None,
        description="LAN1 port configuration"
    )
    lan2: Optional[PortConfigInput] = Field(
        default=None,
        description="LAN2 port configuration"
    )
    lan3: Optional[PortConfigInput] = Field(
        default=None,
        description="LAN3 port configuration"
    )
    lan4: Optional[PortConfigInput] = Field(
        default=None,
        description="LAN4 port configuration"
    )
    lan5: Optional[PortConfigInput] = Field(
        default=None,
        description="LAN5 port configuration (usually uplink)"
    )

    def to_ap_port_request(self) -> APPortRequest:
        return APPortRequest(
            ap_identifier=self.ap_identifier,
            lan1=self.lan1.to_port_config() if self.lan1 else None,
            lan2=self.lan2.to_port_config() if self.lan2 else None,
            lan3=self.lan3.to_port_config() if self.lan3 else None,
            lan4=self.lan4.to_port_config() if self.lan4 else None,
            lan5=self.lan5.to_port_config() if self.lan5 else None,
        )


class BulkConfigInput(BaseModel):
    """Bulk configuration - apply same settings to multiple APs"""
    ap_identifiers: List[str] = Field(
        description="List of AP names or serial numbers"
    )
    lan1: Optional[PortConfigInput] = None
    lan2: Optional[PortConfigInput] = None
    lan3: Optional[PortConfigInput] = None
    lan4: Optional[PortConfigInput] = None
    lan5: Optional[PortConfigInput] = None


class ConfigureRequest(BaseModel):
    """Request to configure AP ports"""
    venue_id: str = Field(description="Venue ID")
    tenant_id: Optional[str] = Field(default=None, description="Tenant/EC ID (required for MSP controllers)")

    # Option 1: Individual AP configurations
    ap_configs: Optional[List[APConfigInput]] = Field(
        default=None,
        description="Individual configurations per AP"
    )

    # Option 2: Bulk configuration (same settings for multiple APs)
    bulk_config: Optional[BulkConfigInput] = Field(
        default=None,
        description="Apply same configuration to multiple APs"
    )

    dry_run: bool = Field(
        default=False,
        description="If true, preview changes without applying"
    )


class AuditRequest(BaseModel):
    """Request to audit AP port configurations"""
    venue_id: str = Field(description="Venue ID")
    tenant_id: Optional[str] = Field(default=None, description="Tenant/EC ID (required for MSP controllers)")
    ap_identifiers: Optional[List[str]] = Field(
        default=None,
        description="Specific APs to audit (default: all APs in venue)"
    )


class BatchConfigInput(BaseModel):
    """Configuration for batch processing"""
    max_concurrent_aps: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max APs to process in parallel"
    )
    max_concurrent_api_calls: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Max concurrent API calls (rate limit)"
    )
    poll_interval_seconds: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Interval for polling activity completion"
    )
    max_poll_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        description="Max time to wait for all operations to complete"
    )

    def to_batch_config(self) -> BatchConfig:
        return BatchConfig(
            max_concurrent_aps=self.max_concurrent_aps,
            max_concurrent_api_calls=self.max_concurrent_api_calls,
            poll_interval_seconds=self.poll_interval_seconds,
            max_poll_seconds=self.max_poll_seconds,
        )


class BatchConfigureRequest(BaseModel):
    """Request to configure AP ports using batch/parallel processing"""
    venue_id: str = Field(description="Venue ID")
    tenant_id: Optional[str] = Field(default=None, description="Tenant/EC ID (required for MSP controllers)")

    # AP configurations (same as ConfigureRequest)
    ap_configs: Optional[List[APConfigInput]] = Field(
        default=None,
        description="Individual configurations per AP"
    )
    bulk_config: Optional[BulkConfigInput] = Field(
        default=None,
        description="Apply same configuration to multiple APs"
    )

    # Batch options
    batch_config: Optional[BatchConfigInput] = Field(
        default=None,
        description="Batch processing configuration (throttling, etc.)"
    )

    dry_run: bool = Field(
        default=False,
        description="If true, preview changes without applying"
    )


# ============================================================================
# Workflow Job Constants
# ============================================================================

WORKFLOW_NAME = "ap_port_config_batch"


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/configure")
async def configure_ports(
    request: ConfigureRequest,
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Configure LAN ports on APs.

    Supports two modes:
    1. **Individual configs**: Specify different settings per AP
    2. **Bulk config**: Apply same settings to multiple APs

    Port modes:
    - `ignore`: Don't change this port
    - `specific`: Set specific VLAN (requires vlan field)
    - `disable`: Disable the port
    - `uplink`: Protected uplink port (skipped)

    Example request (individual):
    ```json
    {
        "venue_id": "xxx",
        "tenant_id": "yyy",
        "ap_configs": [
            {
                "ap_identifier": "AP-Lobby",
                "lan1": {"mode": "specific", "vlan": 100},
                "lan2": {"mode": "disable"}
            }
        ]
    }
    ```

    Example request (bulk):
    ```json
    {
        "venue_id": "xxx",
        "tenant_id": "yyy",
        "bulk_config": {
            "ap_identifiers": ["AP-101", "AP-102", "AP-103"],
            "lan1": {"mode": "specific", "vlan": 100}
        }
    }
    ```
    """
    # Build APPortRequest list
    ap_requests: List[APPortRequest] = []

    if request.ap_configs:
        # Individual configurations
        for config in request.ap_configs:
            ap_requests.append(config.to_ap_port_request())

    elif request.bulk_config:
        # Bulk configuration - same settings for all APs
        bulk = request.bulk_config
        for ap_id in bulk.ap_identifiers:
            ap_requests.append(APPortRequest(
                ap_identifier=ap_id,
                lan1=bulk.lan1.to_port_config() if bulk.lan1 else None,
                lan2=bulk.lan2.to_port_config() if bulk.lan2 else None,
                lan3=bulk.lan3.to_port_config() if bulk.lan3 else None,
                lan4=bulk.lan4.to_port_config() if bulk.lan4 else None,
                lan5=bulk.lan5.to_port_config() if bulk.lan5 else None,
            ))
    else:
        raise HTTPException(
            status_code=400,
            detail="Either ap_configs or bulk_config must be provided"
        )

    if not ap_requests:
        raise HTTPException(
            status_code=400,
            detail="No AP configurations provided"
        )

    logger.info(f"Configuring ports on {len(ap_requests)} APs (dry_run={request.dry_run})")

    result = await configure_ap_ports(
        r1_client=r1_client,
        venue_id=request.venue_id,
        tenant_id=request.tenant_id,
        ap_configs=ap_requests,
        dry_run=request.dry_run
    )

    return result


@router.post("/audit")
async def audit_ports(
    request: AuditRequest,
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Audit current port configurations for APs in a venue.

    Returns detailed port settings for each AP including:
    - Current VLAN assignments
    - Port type (ACCESS/TRUNK)
    - Port enabled status
    - Which port is the uplink

    If `ap_identifiers` is not provided, audits all APs in the venue.
    """
    logger.info(f"Auditing port configs for venue {request.venue_id}")

    result = await audit_ap_ports(
        r1_client=r1_client,
        venue_id=request.venue_id,
        tenant_id=request.tenant_id,
        ap_identifiers=request.ap_identifiers
    )

    return result


@router.get("/metadata")
async def get_metadata():
    """
    Get AP model port metadata for frontend.

    Returns:
    - Model port counts (configurable ports per model)
    - Model uplink ports (which port is uplink per model)
    - Available port modes and descriptions
    """
    return get_port_metadata()


@router.get("/venue/{venue_id}/aps")
async def get_venue_aps(
    venue_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID (required for MSP controllers)"),
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Get all APs in a venue with their port capabilities.

    Returns AP list with:
    - AP name, serial, model
    - Whether it has configurable ports
    - Which ports are configurable vs uplink
    """
    from r1api.models import get_model_info

    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {str(e)}")

    result = []
    for ap in all_aps:
        model = ap.get('model', '')
        model_info = get_model_info(model)

        result.append({
            'name': ap.get('name'),
            'serial': ap.get('serialNumber'),
            'model': model,
            'status': ap.get('status'),
            'has_configurable_ports': model_info['has_lan_ports'],
            'port_count': model_info['port_count'],
            'uplink_port': model_info['uplink_port'],
            'configurable_ports': model_info['configurable_ports'],
            'all_ports': model_info['all_ports']
        })

    return {
        'venue_id': venue_id,
        'total_aps': len(result),
        'aps_with_configurable_ports': sum(1 for ap in result if ap['has_configurable_ports']),
        'aps': result
    }


@router.post("/parse-csv")
async def parse_csv(
    csv_content: str = None,
):
    """
    Parse CSV content for bulk port configuration.

    Expected CSV format:
    ```
    ap_identifier,lan1_vlan,lan2_vlan,lan3_vlan,lan4_vlan
    AP-101,100,,,
    AP-102,100,200,,
    AP-103,100,,,disable
    ```

    - Empty cells = ignore (no change)
    - Number = set that VLAN
    - "disable" = disable the port

    Returns parsed configurations ready for /configure endpoint.
    """
    import csv
    from io import StringIO

    if not csv_content:
        raise HTTPException(status_code=400, detail="CSV content required")

    try:
        reader = csv.DictReader(StringIO(csv_content))
        ap_configs = []

        for row in reader:
            ap_id = row.get('ap_identifier') or row.get('ap_name') or row.get('serial')
            if not ap_id:
                continue

            config = {'ap_identifier': ap_id}

            # Process each LAN port column
            for port_num in range(1, 6):
                col_name = f'lan{port_num}_vlan'
                value = row.get(col_name, '').strip()

                if not value:
                    continue
                elif value.lower() == 'disable':
                    config[f'lan{port_num}'] = {'mode': 'disable'}
                elif value.lower() == 'ignore':
                    continue
                else:
                    try:
                        vlan = int(value)
                        config[f'lan{port_num}'] = {'mode': 'specific', 'vlan': vlan}
                    except ValueError:
                        logger.warning(f"Invalid VLAN value '{value}' for {ap_id} LAN{port_num}")

            ap_configs.append(config)

        return {
            'parsed_count': len(ap_configs),
            'ap_configs': ap_configs
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {str(e)}")


# ============================================================================
# Batch Processing Endpoints
# ============================================================================

@router.post("/configure-batch")
async def configure_ports_batch(
    request: BatchConfigureRequest,
    controller_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Configure LAN ports on multiple APs using parallel batch processing.

    Uses the same WorkflowJob framework as Per-Unit SSID for consistency.
    Returns immediately with a job_id. Use the generic /jobs/{job_id}/stream
    endpoint to receive real-time progress updates via Server-Sent Events (SSE).

    **Batch Configuration Options:**
    - `max_concurrent_aps`: Max APs processed in parallel (default: 20)
    - `max_concurrent_api_calls`: API rate limit (default: 20/sec)
    - `poll_interval_seconds`: Activity polling interval (default: 3s)
    - `max_poll_seconds`: Max wait time for completion (default: 120s)

    **Response:**
    ```json
    {
        "job_id": "abc-123",
        "status": "RUNNING",
        "message": "Port configuration started for 50 APs. Poll /jobs/{job_id}/status for progress."
    }
    ```
    """
    # Validate controller access
    controller = db.query(Controller).filter(
        Controller.id == controller_id,
        Controller.user_id == current_user.id
    ).first()

    if not controller:
        controller_exists = db.query(Controller).filter(Controller.id == controller_id).first()
        if not controller_exists:
            raise HTTPException(status_code=404, detail=f"Controller {controller_id} not found")
        else:
            raise HTTPException(status_code=403, detail=f"Access denied to controller {controller_id}")

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

    # Build APPortRequest list (same as configure_ports)
    ap_requests: List[APPortRequest] = []

    if request.ap_configs:
        for config in request.ap_configs:
            ap_requests.append(config.to_ap_port_request())
    elif request.bulk_config:
        bulk = request.bulk_config
        for ap_id in bulk.ap_identifiers:
            ap_requests.append(APPortRequest(
                ap_identifier=ap_id,
                lan1=bulk.lan1.to_port_config() if bulk.lan1 else None,
                lan2=bulk.lan2.to_port_config() if bulk.lan2 else None,
                lan3=bulk.lan3.to_port_config() if bulk.lan3 else None,
                lan4=bulk.lan4.to_port_config() if bulk.lan4 else None,
                lan5=bulk.lan5.to_port_config() if bulk.lan5 else None,
            ))
    else:
        raise HTTPException(
            status_code=400,
            detail="Either ap_configs or bulk_config must be provided"
        )

    if not ap_requests:
        raise HTTPException(
            status_code=400,
            detail="No AP configurations provided"
        )

    # Get batch config
    batch_config = request.batch_config.to_batch_config() if request.batch_config else BatchConfig()

    # Generate job ID
    job_id = str(uuid.uuid4())

    # Create workflow job with single phase
    job = WorkflowJob(
        id=job_id,
        workflow_name=WORKFLOW_NAME,
        user_id=current_user.id,
        controller_id=controller_id,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        options={
            'dry_run': request.dry_run,
            'batch_config': batch_config.to_dict(),
        },
        input_data={
            'ap_configs': [
                {
                    'ap_identifier': ap.ap_identifier,
                    'lan1': {'mode': ap.lan1.mode.value, 'vlan': ap.lan1.vlan} if ap.lan1 else None,
                    'lan2': {'mode': ap.lan2.mode.value, 'vlan': ap.lan2.vlan} if ap.lan2 else None,
                    'lan3': {'mode': ap.lan3.mode.value, 'vlan': ap.lan3.vlan} if ap.lan3 else None,
                    'lan4': {'mode': ap.lan4.mode.value, 'vlan': ap.lan4.vlan} if ap.lan4 else None,
                    'lan5': {'mode': ap.lan5.mode.value, 'vlan': ap.lan5.vlan} if ap.lan5 else None,
                }
                for ap in ap_requests
            ],
            'total_aps': len(ap_requests),
        },
        phases=[
            Phase(
                id='configure_ports',
                name='Configure AP Ports',
                dependencies=[],
                parallelizable=True,
                critical=True,
                tasks=[
                    Task(id=f'ap_{i}', name=f'Configure {ap.ap_identifier}')
                    for i, ap in enumerate(ap_requests)
                ]
            )
        ]
    )

    # Save to Redis
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)
    await state_manager.save_job(job)

    logger.info(f"Created batch job {job_id} for {len(ap_requests)} APs (dry_run={request.dry_run})")

    # Start background workflow
    background_tasks.add_task(
        run_batch_workflow_background,
        job,
        controller_id,
        ap_requests,
        batch_config,
        request.dry_run,
    )

    return {
        'job_id': job_id,
        'status': JobStatus.RUNNING,
        'message': f"Port configuration started for {len(ap_requests)} APs. Poll /jobs/{job_id}/status for progress."
    }


# ============================================================================
# Background Workflow Function
# ============================================================================

async def run_batch_workflow_background(
    job: WorkflowJob,
    controller_id: int,
    ap_requests: List[APPortRequest],
    batch_config: BatchConfig,
    dry_run: bool,
):
    """Background task to run AP port configuration batch workflow"""
    from database import SessionLocal
    from clients.r1_client import create_r1_client_from_controller

    # Create a fresh database session for this background task
    db = SessionLocal()

    try:
        logger.info(f"Starting background batch workflow for job {job.id}")

        # Create R1 client
        r1_client = create_r1_client_from_controller(controller_id, db)

        # Create workflow components
        redis_client = await get_redis_client()
        state_manager = RedisStateManager(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        # Update job status to running
        job.status = JobStatus.RUNNING
        job.current_phase_id = 'configure_ports'
        phase = job.get_phase_by_id('configure_ports')
        if phase:
            phase.status = PhaseStatus.RUNNING
            phase.started_at = datetime.utcnow()
        await state_manager.save_job(job)

        # Publish start event
        await event_publisher.publish_event(job.id, "phase_started", {
            "phase_id": "configure_ports",
            "phase_name": "Configure AP Ports",
        })

        # Progress callback that updates workflow job
        async def progress_callback(progress: BatchProgress):
            # Update phase tasks based on progress
            if phase:
                completed_count = len(progress.aps_configured) + len(progress.aps_already_correct) + len(progress.aps_failed) + len(progress.aps_skipped)
                for i, task in enumerate(phase.tasks):
                    if i < completed_count:
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = datetime.utcnow()

                # Store progress in phase result
                phase.result = {
                    'current_phase': progress.current_phase,
                    'processed_aps': progress.processed_aps,
                    'total_aps': progress.total_aps,
                    'configured': len(progress.aps_configured),
                    'already_correct': len(progress.aps_already_correct),
                    'failed': len(progress.aps_failed),
                    'skipped': len(progress.aps_skipped),
                    'pending_requests': progress.pending_requests,
                }

            await state_manager.save_job(job)

            # Calculate percent complete
            total = progress.total_aps or 1
            completed = len(progress.aps_configured) + len(progress.aps_already_correct) + len(progress.aps_failed) + len(progress.aps_skipped)
            percent = int((completed / total) * 100)

            # Publish message event for phase status
            phase_messages = {
                'preparing': 'Preparing batch configuration...',
                'fetching_settings': 'Fetching current port settings...',
                'configuring': 'Configuring ports...',
                'polling': f'Waiting for {progress.pending_requests} operations to complete...',
                'complete': 'Configuration complete!',
                'dry_run_complete': 'Dry run complete!',
            }
            message = phase_messages.get(progress.current_phase, progress.current_phase)
            await event_publisher.message(job.id, message, "info")

            # Publish progress event with format frontend expects
            await event_publisher.publish_event(job.id, "progress", {
                "total_tasks": progress.total_aps,
                "completed": completed,
                "failed": len(progress.aps_failed),
                "pending": progress.total_aps - completed,
                "percent": percent,
            })

        # Run batch configuration
        result = await configure_ap_ports_batch(
            r1_client=r1_client,
            venue_id=job.venue_id,
            tenant_id=job.tenant_id,
            ap_configs=ap_requests,
            batch_config=batch_config,
            dry_run=dry_run,
            progress_callback=progress_callback,
            job_id=job.id,
        )

        # Update job with results
        if phase:
            phase.status = PhaseStatus.COMPLETED
            phase.completed_at = datetime.utcnow()
            phase.result = result

        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()
        job.summary = result.get('summary', {})

        await state_manager.save_job(job)

        # Publish completion event
        await event_publisher.job_completed(job)

        logger.info(f"Batch job {job.id} completed: {result.get('summary', {})}")

    except Exception as e:
        logger.error(f"Batch job {job.id} failed: {e}", exc_info=True)

        # Update job with error
        job.status = JobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.errors.append(str(e))

        if job.phases and len(job.phases) > 0:
            job.phases[0].status = PhaseStatus.FAILED

        redis_client = await get_redis_client()
        state_manager = RedisStateManager(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        await state_manager.save_job(job)
        await event_publisher.job_failed(job)

    finally:
        db.close()
