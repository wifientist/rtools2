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

from workflow.models import WorkflowJob, Phase, JobStatus
from workflow.state_manager import RedisStateManager
from workflow.executor import TaskExecutor
from workflow.engine import WorkflowEngine
from workflow.events import WorkflowEventPublisher
from workflow.parallel_orchestrator import ParallelJobOrchestrator
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


async def run_workflow_background(
    job: WorkflowJob,
    controller_id: int,
):
    """Background task to run per-unit-ssid workflow"""
    from database import SessionLocal

    # Create a fresh database session for this background task
    db = SessionLocal()

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
            event_publisher=event_publisher,
            state_manager=state_manager
        )
        workflow_engine = WorkflowEngine(state_manager, task_executor, event_publisher)

        # === SSID Limit Pre-flight Check ===
        # Check if venue has too many "All AP" SSIDs - new AP Groups would inherit them all
        tenant_id = job.tenant_id
        venue_id = job.venue_id

        await event_publisher.message(job.id, "Checking venue SSID configuration...", "info")

        audit_result = await count_all_ap_ssids_for_venue(r1_client, tenant_id, venue_id)
        all_ap_count = audit_result['all_ap_ssids_count']

        # Check if there are too many "All AP" SSIDs, but be smart about it:
        # If the "All AP" SSIDs are the SAME ones we're trying to configure (re-run scenario),
        # Phase 4 can fix them by narrowing to specific AP Groups. Only fail if they're
        # DIFFERENT SSIDs that would block us.
        if all_ap_count >= SSID_LIMIT_PER_AP_GROUP:
            # Get the SSID names we're trying to configure
            units = job.input_data.get('units', [])
            our_ssid_names = set(u.get('ssid_name') or u.get('network_name') for u in units)
            existing_all_ap_names = set(audit_result['all_ap_ssid_names'])

            # How many of the "All AP" SSIDs are ones we're configuring vs unrelated?
            our_ssids_in_all_ap = our_ssid_names & existing_all_ap_names
            unrelated_all_ap = existing_all_ap_names - our_ssid_names

            if len(unrelated_all_ap) >= SSID_LIMIT_PER_AP_GROUP:
                # Too many UNRELATED "All AP" SSIDs - we can't proceed
                error_msg = (
                    f"Cannot proceed: venue has {len(unrelated_all_ap)} unrelated SSIDs set to 'All AP Groups'. "
                    f"New AP Groups would immediately inherit all of them, exceeding the {SSID_LIMIT_PER_AP_GROUP} SSID limit. "
                    f"Please go to RuckusONE and change these SSIDs to specific AP Groups first: "
                    f"{', '.join(list(unrelated_all_ap)[:10])}"
                    f"{'...' if len(unrelated_all_ap) > 10 else ''}"
                )
                logger.error(error_msg)
                await event_publisher.message(job.id, error_msg, "error")

                job.status = JobStatus.FAILED
                job.errors.append(error_msg)
                job.completed_at = datetime.utcnow()
                await state_manager.save_job(job)
                await event_publisher.job_failed(job)
                return
            else:
                # The "All AP" SSIDs are mostly ours from a previous run - Phase 4 can fix this!
                await event_publisher.message(
                    job.id,
                    f"Found {len(our_ssids_in_all_ap)} of our SSIDs still set to 'All AP Groups' (likely from previous run). "
                    f"Phase 4 will narrow them to specific AP Groups.",
                    "warning"
                )
        elif all_ap_count > 0:
            await event_publisher.message(
                job.id,
                f"Found {all_ap_count} 'All AP' SSIDs in venue (limit is {SSID_LIMIT_PER_AP_GROUP})",
                "info"
            )

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

    finally:
        # Always close the database session
        db.close()


async def run_parallel_workflow_background(
    parent_job: WorkflowJob,
    controller_id: int,
    units: List[Dict],
    max_concurrent: int = 10,
):
    """
    Background task to run per-unit-ssid workflow in parallel mode.

    Each unit becomes a child job that runs through all phases independently.

    IMPORTANT: This function audits the venue before starting to count existing
    "all AP Groups" SSIDs and calculates a safe max_concurrent to avoid hitting
    the 15 SSID per AP Group limit. The user's requested max_concurrent may be
    reduced if the venue already has many SSIDs.
    """
    from database import SessionLocal

    # Create a fresh database session for this background task
    db = SessionLocal()

    try:
        logger.info(f"Starting parallel workflow for parent job {parent_job.id} with {len(units)} units (max_concurrent={max_concurrent})")

        # Create R1 client
        r1_client = create_r1_client_from_controller(controller_id, db)

        # Create workflow components
        redis_client = await get_redis_client()
        state_manager = RedisStateManager(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        # === SSID Limit Audit ===
        # Before starting, audit the venue to count existing "all AP" SSIDs
        # and calculate a safe max_concurrent to avoid the 15 SSID limit
        tenant_id = parent_job.tenant_id
        venue_id = parent_job.venue_id

        await event_publisher.message(
            parent_job.id,
            "Auditing venue for SSID limit safety...",
            "info"
        )

        audit_result = await count_all_ap_ssids_for_venue(r1_client, tenant_id, venue_id)
        safe_concurrent = audit_result['safe_concurrent']
        all_ap_count = audit_result['all_ap_ssids_count']

        # Check if there are too many "All AP" SSIDs, but be smart about it:
        # If the "All AP" SSIDs are the SAME ones we're trying to configure (re-run scenario),
        # Phase 4 can fix them by narrowing to specific AP Groups. Only fail if they're
        # DIFFERENT SSIDs that would block us.
        if all_ap_count >= SSID_LIMIT_PER_AP_GROUP:
            # Get the SSID names we're trying to configure
            our_ssid_names = set(u.get('ssid_name') or u.get('network_name') for u in units)
            existing_all_ap_names = set(audit_result['all_ap_ssid_names'])

            # How many of the "All AP" SSIDs are ones we're configuring vs unrelated?
            our_ssids_in_all_ap = our_ssid_names & existing_all_ap_names
            unrelated_all_ap = existing_all_ap_names - our_ssid_names

            if len(unrelated_all_ap) >= SSID_LIMIT_PER_AP_GROUP:
                # Too many UNRELATED "All AP" SSIDs - we can't proceed
                error_msg = (
                    f"Cannot proceed: venue has {len(unrelated_all_ap)} unrelated SSIDs set to 'All AP Groups'. "
                    f"New AP Groups would immediately inherit all of them, exceeding the {SSID_LIMIT_PER_AP_GROUP} SSID limit. "
                    f"Please go to RuckusONE and change these SSIDs to specific AP Groups first: "
                    f"{', '.join(list(unrelated_all_ap)[:10])}"
                    f"{'...' if len(unrelated_all_ap) > 10 else ''}"
                )
                logger.error(error_msg)
                await event_publisher.message(parent_job.id, error_msg, "error")

                parent_job.status = JobStatus.FAILED
                parent_job.errors.append(error_msg)
                parent_job.completed_at = datetime.utcnow()
                await state_manager.save_job(parent_job)
                await event_publisher.job_failed(parent_job)
                return
            else:
                # The "All AP" SSIDs are mostly ours from a previous run - Phase 4 can fix this!
                await event_publisher.message(
                    parent_job.id,
                    f"Found {len(our_ssids_in_all_ap)} of our SSIDs still set to 'All AP Groups' (likely from previous run). "
                    f"Phase 4 will narrow them to specific AP Groups.",
                    "warning"
                )
                # Reduce concurrency since we have some "All AP" overhead
                safe_concurrent = max(1, SSID_LIMIT_PER_AP_GROUP - len(unrelated_all_ap) - SSID_SAFETY_BUFFER)

        # Determine effective max_concurrent (minimum of user request and safe limit)
        effective_max_concurrent = min(max_concurrent, safe_concurrent)

        if effective_max_concurrent < max_concurrent:
            warning_msg = (
                f"SSID activation throttle: {effective_max_concurrent} concurrent "
                f"(venue has {all_ap_count} 'all AP' SSIDs, limit is {SSID_LIMIT_PER_AP_GROUP})"
            )
            logger.warning(warning_msg)
            await event_publisher.message(parent_job.id, warning_msg, "warning")
        else:
            await event_publisher.message(
                parent_job.id,
                f"SSID audit complete: {all_ap_count} existing 'all AP' SSIDs, activation throttle: {effective_max_concurrent}",
                "info"
            )

        # Store audit info in parent job for visibility
        parent_job.parallel_config['ssid_audit'] = audit_result
        parent_job.parallel_config['effective_max_concurrent'] = effective_max_concurrent
        parent_job.parallel_config['requested_max_concurrent'] = max_concurrent
        await state_manager.save_job(parent_job)

        # Create parallel orchestrator
        parallel_orchestrator = ParallelJobOrchestrator(state_manager, event_publisher)

        # Create a shared semaphore for Phase 3 (SSID activation) throttling
        # This is the critical phase that can hit the 15 SSID limit
        # Other phases (AP Group creation, SSID creation) can run freely in parallel
        activation_semaphore = asyncio.Semaphore(effective_max_concurrent)
        logger.info(f"Created activation semaphore with limit {effective_max_concurrent}")

        async def execute_child_job(child_job: WorkflowJob) -> WorkflowJob:
            """Execute a single child job through all phases"""
            logger.info(f"Starting child job {child_job.id} for unit {child_job.get_item_identifier()}")

            # Create phases for this child job
            workflow_def = get_workflow_definition(
                configure_lan_ports=child_job.options.get('configure_lan_ports', False)
            )
            child_job.phases = [
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

            # Save child job with phases
            await state_manager.save_job(child_job)

            # Create task executor for this child
            # Pass the shared activation_semaphore for Phase 3 throttling
            task_executor = TaskExecutor(
                max_retries=3,
                retry_backoff_base=2,
                r1_client=r1_client,
                event_publisher=event_publisher,
                state_manager=state_manager,
                activation_semaphore=activation_semaphore  # Shared across all children
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

            # Execute workflow for this child
            return await workflow_engine.execute_workflow(child_job, phase_executors)

        # Execute parallel workflow with controlled concurrency
        # Use effective_max_concurrent for both job-level AND phase-level throttling
        # This prevents overwhelming the frontend with SSE events and controls resource usage
        final_job = await parallel_orchestrator.execute_parallel_workflow(
            parent_job=parent_job,
            items=units,
            item_key='unit_number',
            child_workflow_executor=execute_child_job,
            max_concurrent=effective_max_concurrent  # Respect user's setting (or safe limit)
        )

        logger.info(f"Parallel workflow {parent_job.id} completed with status: {final_job.status}")

    except Exception as e:
        logger.exception(f"Parallel workflow {parent_job.id} failed: {str(e)}")

    finally:
        # Always close the database session
        db.close()


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

    # Build job options
    job_options = {
        'ap_group_prefix': request.ap_group_prefix,
        'ap_group_postfix': request.ap_group_postfix,
        'name_conflict_resolution': request.name_conflict_resolution,
        'configure_lan_ports': request.configure_lan_ports,
        'model_port_configs': model_port_configs_data,
        'debug_delay': request.debug_delay
    }

    # Build input data
    job_input_data = {
        'units': units_data,
        'ap_group_prefix': request.ap_group_prefix,
        'ap_group_postfix': request.ap_group_postfix,
        'name_conflict_resolution': request.name_conflict_resolution,
        'configure_lan_ports': request.configure_lan_ports,
        'model_port_configs': model_port_configs_data
    }

    # Save initial job state
    logger.info("Connecting to Redis...")
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)

    if request.parallel_execution and len(units_data) > 1:
        # Parallel execution mode: parent job spawns child jobs for each unit
        logger.info(f"Using PARALLEL execution mode (max_concurrent={request.max_concurrent})")

        # Create parent job (phases will be empty - children have the phases)
        job = WorkflowJob(
            id=job_id,
            workflow_name=workflow_def.name,
            user_id=current_user.id,
            controller_id=request.controller_id,
            venue_id=request.venue_id,
            tenant_id=tenant_id,
            options=job_options,
            input_data=job_input_data,
            phases=[],  # Parent has no phases - children do
            parallel_config={
                'max_concurrent': request.max_concurrent,
                'item_key': 'unit_number',
                'total_items': len(units_data)
            }
        )
        logger.info("Created parent WorkflowJob object (parallel mode)")

        logger.info("Saving parent job to Redis...")
        await state_manager.save_job(job)
        logger.info("Parent job saved to Redis")

        # Start parallel workflow in background
        logger.info(f"Starting parallel background workflow task with {len(units_data)} units...")
        background_tasks.add_task(
            run_parallel_workflow_background,
            job,
            request.controller_id,
            units_data,
            request.max_concurrent
        )

        message = f"Per-unit SSID configuration started for {len(request.units)} units (parallel mode, max {request.max_concurrent} concurrent). Poll /jobs/{job_id}/status for progress."
    else:
        # Sequential execution mode: single job processes all units
        logger.info("Using SEQUENTIAL execution mode")

        job = WorkflowJob(
            id=job_id,
            workflow_name=workflow_def.name,
            user_id=current_user.id,
            controller_id=request.controller_id,
            venue_id=request.venue_id,
            tenant_id=tenant_id,
            options=job_options,
            input_data=job_input_data,
            phases=phases
        )
        logger.info("Created WorkflowJob object (sequential mode)")

        logger.info("Saving job to Redis...")
        await state_manager.save_job(job)
        logger.info("Job saved to Redis")

        # Start workflow in background (creates its own db session)
        logger.info("Starting background workflow task...")
        background_tasks.add_task(run_workflow_background, job, request.controller_id)

        message = f"Per-unit SSID configuration started for {len(request.units)} units. Poll /jobs/{job_id}/status for progress."

    logger.info(f"Workflow job {job_id} created and queued")

    return ConfigureResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        message=message
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
