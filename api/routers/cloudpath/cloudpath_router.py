"""
Cloudpath Import FastAPI Router

Workflow-specific endpoints for Cloudpath DPSK import.
For generic job management, see /jobs router.

Endpoints:
- POST /audit - Audit existing DPSK configuration at a venue
- POST /import - Start import workflow (redirects to V2)
- POST /cleanup - Start cleanup workflow (uses V2)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User, RoleEnum
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.events import WorkflowEventPublisher
from workflow.workflows.cleanup import VenueCleanupWorkflow
from workflow.workflows.cloudpath_import import CloudpathImportWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cloudpath-import",
    tags=["Cloudpath Import"]
)


# ==================== Request/Response Models ====================

class ImportRequest(BaseModel):
    """Request to start Cloudpath DPSK migration"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")
    dpsk_data: Any = Field(..., description="Cloudpath JSON export data (with pool/dpsks keys)")
    options: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_concurrent_passphrases": 10,
            "skip_expired_dpsks": False,
            "renew_expired_dpsks": False,
            "renewal_days": 365,
        },
        description="Migration options"
    )


class ImportResponse(BaseModel):
    """Response from import endpoint"""
    job_id: str
    status: str
    message: str = ""


class DPSKAuditRequest(BaseModel):
    """Request to audit DPSK configuration at a venue"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID to audit")


class DPSKAuditResponse(BaseModel):
    """Response from DPSK audit"""
    venue_id: str
    venue_name: str
    total_ssids: int
    total_dpsk_ssids: int
    total_dpsk_pools: int
    total_identity_groups: int
    ssids: List[Dict[str, Any]]
    identity_groups: List[Dict[str, Any]]
    dpsk_pools: List[Dict[str, Any]]


class CleanupRequest(BaseModel):
    """Request to start cleanup workflow"""
    job_id: Optional[str] = Field(None, description="Original migration job ID to clean up")
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")
    nuclear: bool = Field(False, description="Nuclear mode - delete ALL DPSK resources")


class CleanupResponse(BaseModel):
    """Response from cleanup endpoint"""
    cleanup_job_id: str
    original_job_id: Optional[str]
    status: str
    nuclear_mode: bool


class PreviewCleanupRequest(BaseModel):
    """Request to preview what would be deleted"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")


class PreviewCleanupResponse(BaseModel):
    """Response from preview cleanup endpoint"""
    venue_id: str
    inventory: Dict[str, List[Dict[str, Any]]]
    total_resources: int


class DPSKSsidsRequest(BaseModel):
    """Request to fetch DPSK-enabled SSIDs at a venue"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: str = Field(..., description="Venue ID")


class DPSKSsidInfo(BaseModel):
    """Info about a DPSK-enabled SSID"""
    id: str
    name: str
    ssid: str
    dpsk_pool_ids: List[str] = []


class DPSKSsidsResponse(BaseModel):
    """Response with list of DPSK-enabled SSIDs"""
    venue_id: str
    dpsk_ssids: List[DPSKSsidInfo]
    total_count: int


class ExportIdentitiesRequest(BaseModel):
    """Request to export identities/passphrases"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    venue_id: Optional[str] = Field(None, description="Optional: Venue ID to filter by")
    dpsk_pool_id: Optional[str] = Field(None, description="Optional: Filter to a specific DPSK pool")


class IdentityExportRow(BaseModel):
    """Single row in identity export"""
    cloudpath_guid: str = ""
    identity_id: str = ""
    passphrase_id: str = ""
    username: str = ""
    passphrase: str = ""
    identity_group_name: str = ""
    dpsk_pool_id: str = ""
    dpsk_pool_name: str = ""


class ExportIdentitiesResponse(BaseModel):
    """Response with identity/passphrase data for preview"""
    venue_id: Optional[str] = None
    total_count: int
    data: List[IdentityExportRow]


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


# ==================== V2 Background Tasks ====================

async def run_v2_import_background(job_id: str, controller_id: int):
    """Background task to run V2 cloudpath import workflow."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[Cloudpath V2] Starting import for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cloudpath V2] Job {job_id} not found")
            return

        r1_client = create_r1_client_from_controller(controller_id, db)
        event_publisher = WorkflowEventPublisher(redis_client)
        activity_tracker = ActivityTracker(r1_client, state_manager, tenant_id=job.tenant_id)

        await activity_tracker.start()

        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=activity_tracker,
            event_publisher=event_publisher,
            r1_client=r1_client,
        )

        # Run validation first
        await brain.run_validation(job)

        # If validation passed, auto-confirm and execute
        if job.status == JobStatus.AWAITING_CONFIRMATION:
            if job.validation_result and job.validation_result.valid:
                await brain.execute_workflow(job)

        await activity_tracker.stop()
        logger.info(f"[Cloudpath V2] Import complete for job {job_id} (status={job.status.value})")

    except Exception as e:
        logger.exception(f"[Cloudpath V2] Import failed for job {job_id}: {e}")
    finally:
        db.close()


async def run_v2_cleanup_background(job_id: str, controller_id: int):
    """Background task to run V2 cleanup workflow."""
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"[Cleanup V2] Starting cleanup for job {job_id}")

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)

        if not job:
            logger.error(f"[Cleanup V2] Job {job_id} not found")
            return

        r1_client = create_r1_client_from_controller(controller_id, db)
        event_publisher = WorkflowEventPublisher(redis_client)
        activity_tracker = ActivityTracker(r1_client, state_manager, tenant_id=job.tenant_id)

        await activity_tracker.start()

        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=activity_tracker,
            event_publisher=event_publisher,
            r1_client=r1_client,
        )

        # Run validation (inventory phase)
        await brain.run_validation(job)

        # Auto-confirm and execute cleanup
        if job.status == JobStatus.AWAITING_CONFIRMATION:
            await brain.execute_workflow(job)

        await activity_tracker.stop()
        logger.info(f"[Cleanup V2] Cleanup complete for job {job_id} (status={job.status.value})")

    except Exception as e:
        logger.exception(f"[Cleanup V2] Cleanup failed for job {job_id}: {e}")
    finally:
        db.close()


# ==================== API Endpoints ====================

@router.post("/audit", response_model=DPSKAuditResponse)
async def audit_venue_dpsk(
    request: DPSKAuditRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Audit DPSK configuration at a venue

    Returns comprehensive view of:
    - All SSIDs at the venue
    - Which SSIDs have DPSK enabled
    - DPSK pools and their associations
    - Identity groups
    """
    logger.info(f"DPSK audit request - controller: {request.controller_id}, venue: {request.venue_id}")

    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller must be RuckusONE")

    r1_client = create_r1_client_from_controller(controller.id, db)
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    try:
        # Get venue details
        venue = await r1_client.venues.get_venue(tenant_id, request.venue_id)
        venue_name = venue.get('name', 'Unknown')

        # Get SSIDs at venue
        wifi_networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = wifi_networks_response.get('data', [])

        venue_ssids = []
        for network in all_networks:
            venue_ap_groups = network.get('venueApGroups', [])
            for vag in venue_ap_groups:
                if vag.get('venueId') == request.venue_id:
                    venue_ssids.append({'name': network.get('name'), 'id': network.get('id')})
                    break

        # Get DPSK pools
        dpsk_pools_response = await r1_client.dpsk.query_dpsk_pools(
            tenant_id=tenant_id, search_string="", page=0, limit=1000
        )
        all_dpsk_pools = dpsk_pools_response.get('data', [])

        # Get identity groups
        ig_response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id, search_string="", page=0, size=1000
        )
        all_identity_groups = ig_response.get('content', ig_response.get('data', []))

        # Get detailed SSID info to identify DPSK networks
        dpsk_ssids = []
        all_ssids_detailed = []

        for ssid in venue_ssids:
            try:
                ssid_details = await r1_client.networks.get_wifi_network_by_id(ssid['id'], tenant_id)
                all_ssids_detailed.append(ssid_details)

                has_dpsk = (
                    ssid_details.get('type') == 'dpsk' or
                    ssid_details.get('useDpskService') == True or
                    ssid_details.get('nwSubType') == 'DPSK' or
                    'dpskPool' in ssid_details or
                    'dpskService' in ssid_details
                )

                if has_dpsk:
                    dpsk_ssids.append(ssid_details)
            except Exception as e:
                logger.warning(f"Error fetching details for {ssid['name']}: {e}")
                all_ssids_detailed.append(ssid)

        return DPSKAuditResponse(
            venue_id=request.venue_id,
            venue_name=venue_name,
            total_ssids=len(all_ssids_detailed),
            total_dpsk_ssids=len(dpsk_ssids),
            total_dpsk_pools=len(all_dpsk_pools),
            total_identity_groups=len(all_identity_groups),
            ssids=all_ssids_detailed,
            identity_groups=all_identity_groups,
            dpsk_pools=all_dpsk_pools
        )

    except Exception as e:
        logger.exception(f"DPSK audit failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to audit DPSK configuration: {e}")


async def _fetch_identity_passphrase_data(
    r1_client,
    venue_id: str = None,
    tenant_id: str = None,
    dpsk_pool_id: str = None
) -> List[Dict[str, Any]]:
    """Fetch and join identity/passphrase data."""
    # Get identity groups
    if venue_id:
        ig_response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id, filters={"venueId": [venue_id]}, page=1, size=1000
        )
    else:
        ig_response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id, page=1, size=1000
        )
    identity_groups = ig_response.get('content', []) if isinstance(ig_response, dict) else ig_response

    if dpsk_pool_id:
        identity_groups = [ig for ig in identity_groups if ig.get('dpskPoolId') == dpsk_pool_id]

    # Get DPSK pools
    dpsk_pool_ids = set()
    ig_to_pool_id = {}
    ig_id_to_name = {ig.get('id'): ig.get('name') for ig in identity_groups}

    for ig in identity_groups:
        pool_id = ig.get('dpskPoolId')
        if pool_id:
            dpsk_pool_ids.add(pool_id)
            ig_to_pool_id[ig.get('id')] = pool_id

    # Fetch pool details
    dpsk_pools = []
    pool_id_to_name = {}

    for pool_id in dpsk_pool_ids:
        try:
            pool = await r1_client.dpsk.get_dpsk_pool(pool_id, tenant_id)
            dpsk_pools.append(pool)
            pool_id_to_name[pool_id] = pool.get('name', 'Unknown')
        except Exception:
            pass

    # Build identity map
    identity_map = {}
    for ig in identity_groups:
        ig_id = ig.get('id')
        ig_name = ig.get('name', 'Unknown')
        pool_id = ig_to_pool_id.get(ig_id) or ig.get('dpskPoolId')

        identities_response = await r1_client.identity.get_identities_in_group(
            group_id=ig_id, tenant_id=tenant_id, page=0, size=10000
        )
        identities = identities_response.get('content', []) if isinstance(identities_response, dict) else identities_response

        for identity in identities:
            username = identity.get('name')
            if username and pool_id:
                identity_map[(username, pool_id)] = {
                    'identity_id': identity.get('id') or '',
                    'cloudpath_guid': identity.get('description') or '',
                    'identity_group_name': ig_name,
                    'dpsk_pool_id': pool_id
                }

    # Build passphrase map
    passphrase_map = {}
    for pool in dpsk_pools:
        pool_id = pool.get('id')
        pool_name = pool.get('name', 'Unknown')

        passphrases_response = await r1_client.dpsk.get_passphrases(
            pool_id=pool_id, tenant_id=tenant_id, page=0, size=10000
        )
        passphrases = passphrases_response.get('content', passphrases_response.get('data', []))

        for pp in passphrases:
            username = pp.get('username') or pp.get('userName')
            if username:
                passphrase_map[(username, pool_id)] = {
                    'passphrase_id': pp.get('id') or '',
                    'passphrase': pp.get('passphrase') or '',
                    'dpsk_pool_id': pool_id or '',
                    'dpsk_pool_name': pool_name
                }

    # Join data
    all_keys = set(identity_map.keys()) | set(passphrase_map.keys())
    result = []

    for key in sorted(all_keys, key=lambda x: (x[0], x[1] or '')):
        username, pool_id = key
        identity_data = identity_map.get(key, {})
        passphrase_data = passphrase_map.get(key, {})
        final_pool_id = pool_id or passphrase_data.get('dpsk_pool_id') or identity_data.get('dpsk_pool_id') or ''
        pool_name = passphrase_data.get('dpsk_pool_name') or pool_id_to_name.get(final_pool_id, '') or ''

        result.append({
            'cloudpath_guid': identity_data.get('cloudpath_guid') or '',
            'identity_id': identity_data.get('identity_id') or '',
            'passphrase_id': passphrase_data.get('passphrase_id') or '',
            'username': username or '',
            'passphrase': passphrase_data.get('passphrase') or '',
            'identity_group_name': identity_data.get('identity_group_name') or '',
            'dpsk_pool_id': final_pool_id,
            'dpsk_pool_name': pool_name
        })

    return result


@router.post("/export-identities", response_model=ExportIdentitiesResponse)
async def export_identities(
    request: ExportIdentitiesRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export identities and passphrases as JSON"""
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    r1_client = create_r1_client_from_controller(controller.id, db)
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    try:
        data = await _fetch_identity_passphrase_data(
            r1_client, request.venue_id, tenant_id, dpsk_pool_id=request.dpsk_pool_id
        )
        return ExportIdentitiesResponse(venue_id=request.venue_id, total_count=len(data), data=data)
    except Exception as e:
        logger.exception(f"Export identities failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export identities: {e}")


@router.post("/export-identities-csv")
async def export_identities_csv(
    request: ExportIdentitiesRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export identities and passphrases as CSV file download"""
    import csv
    import io

    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    r1_client = create_r1_client_from_controller(controller.id, db)
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    try:
        data = await _fetch_identity_passphrase_data(
            r1_client, request.venue_id, tenant_id, dpsk_pool_id=request.dpsk_pool_id
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'cloudpath_guid', 'identity_id', 'passphrase_id', 'username',
            'passphrase', 'identity_group_name', 'dpsk_pool_id', 'dpsk_pool_name'
        ])

        for row in data:
            writer.writerow([
                row['cloudpath_guid'], row['identity_id'], row['passphrase_id'],
                row['username'], row['passphrase'], row['identity_group_name'],
                row['dpsk_pool_id'], row['dpsk_pool_name']
            ])

        csv_content = output.getvalue()
        output.close()

        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=identities_export_{request.venue_id}.csv"}
        )

    except Exception as e:
        logger.exception(f"Export identities CSV failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export identities: {e}")


@router.post("/dpsk-ssids", response_model=DPSKSsidsResponse)
async def get_dpsk_ssids(
    request: DPSKSsidsRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get DPSK-enabled SSIDs at a venue"""
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    r1_client = create_r1_client_from_controller(controller.id, db)
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    try:
        wifi_networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = wifi_networks_response.get('data', [])

        dpsk_ssids = []
        for network in all_networks:
            network_name = network.get('name', 'Unknown')
            network_id = network.get('id')
            ssid = network.get('ssid', network_name)
            venue_ap_groups = network.get('venueApGroups', [])

            is_at_venue = any(vag.get('venueId') == request.venue_id for vag in venue_ap_groups)
            if not is_at_venue:
                continue

            try:
                network_details = await r1_client.networks.get_wifi_network_by_id(network_id, tenant_id)

                has_dpsk = (
                    network_details.get('type') == 'dpsk' or
                    network_details.get('useDpskService') == True or
                    network_details.get('nwSubType') == 'DPSK'
                )

                dpsk_pool_ids = []
                if 'dpskPool' in network_details:
                    has_dpsk = True
                    dpsk_pool_ids.append(network_details['dpskPool'])
                if 'dpskService' in network_details:
                    has_dpsk = True
                    svc = network_details['dpskService']
                    dpsk_pool_ids.extend(svc if isinstance(svc, list) else [svc])

                if has_dpsk:
                    dpsk_ssids.append(DPSKSsidInfo(
                        id=network_id, name=network_name, ssid=ssid, dpsk_pool_ids=dpsk_pool_ids
                    ))

            except Exception as e:
                logger.warning(f"Error fetching details for {network_name}: {e}")

        return DPSKSsidsResponse(
            venue_id=request.venue_id, dpsk_ssids=dpsk_ssids, total_count=len(dpsk_ssids)
        )

    except Exception as e:
        logger.exception(f"DPSK SSIDs fetch failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch DPSK SSIDs: {e}")


@router.post("/import", response_model=ImportResponse)
async def start_migration(
    request: ImportRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start Cloudpath DPSK migration workflow (V2)

    Uses the V2 workflow engine with:
    - Auto-detection of property-wide vs per-unit mode
    - Intra-phase parallelism for bulk passphrase creation
    - Plan/confirm flow (auto-confirmed for this endpoint)
    """
    logger.info(f"Import request - controller: {request.controller_id}, venue: {request.venue_id}")

    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    # Validate cloudpath data
    if not isinstance(request.dpsk_data, dict):
        raise HTTPException(status_code=400, detail="dpsk_data must be a Cloudpath JSON export object")
    if 'pool' not in request.dpsk_data or 'dpsks' not in request.dpsk_data:
        raise HTTPException(status_code=400, detail="dpsk_data must contain 'pool' and 'dpsks' keys")

    dpsk_count = len(request.dpsk_data.get('dpsks', []))
    logger.info(f"Cloudpath export has {dpsk_count} DPSKs")

    # Build options and input data
    options = {**CloudpathImportWorkflow.default_options, **request.options}
    input_data = {'cloudpath_data': request.dpsk_data, 'options': options}

    # Create V2 job
    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    activity_tracker = ActivityTracker(None, state_manager, tenant_id=tenant_id)

    brain = WorkflowBrain(state_manager=state_manager, activity_tracker=activity_tracker)

    job = await brain.create_job(
        workflow=CloudpathImportWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options=options,
        input_data=input_data,
    )

    # Start import in background (auto-confirms after validation)
    # NOTE: For the V2 plan/confirm flow, use /cloudpath-dpsk/v2/plan instead
    background_tasks.add_task(run_v2_import_background, job.id, request.controller_id)

    return ImportResponse(
        job_id=job.id,
        status="RUNNING",
        message=f"Importing {dpsk_count} DPSKs. Poll /jobs/{job.id}/status for progress."
    )


@router.post("/preview-cleanup", response_model=PreviewCleanupResponse)
async def preview_cleanup(
    request: PreviewCleanupRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Preview what would be deleted in a nuclear cleanup"""
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    try:
        r1_client = create_r1_client_from_controller(request.controller_id, db)

        from workflow.phases.cleanup.inventory import _audit_venue_for_cloudpath
        inventory = await _audit_venue_for_cloudpath(r1_client=r1_client, tenant_id=tenant_id)

        total_resources = sum(len(items) for items in inventory.values())

        return PreviewCleanupResponse(
            venue_id=request.venue_id, inventory=inventory, total_resources=total_resources
        )

    except Exception as e:
        logger.exception(f"Preview cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to preview cleanup: {e}")


@router.post("/cleanup", response_model=CleanupResponse)
async def start_cleanup(
    request: CleanupRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start cleanup workflow (V2)

    Two modes:
    1. Job cleanup: Provide job_id to cleanup resources from specific migration
    2. Nuclear cleanup: Set nuclear=true to delete ALL DPSK resources
    """
    logger.info(f"Cleanup request - mode: {'NUCLEAR' if request.nuclear else 'Job-specific'}, venue: {request.venue_id}")

    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Controller must be RuckusONE")

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)

    created_resources = {}

    if request.job_id and not request.nuclear:
        original_job = await state_manager.get_job(request.job_id)
        if not original_job:
            raise HTTPException(status_code=404, detail=f"Original job {request.job_id} not found")

        if original_job.user_id != current_user.id and current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
            raise HTTPException(status_code=403, detail="Permission denied")

        created_resources = getattr(original_job, 'created_resources', {}) or {}

    elif not request.nuclear:
        raise HTTPException(status_code=400, detail="Must provide either job_id or set nuclear=true")

    tenant_id = request.tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required for MSP controllers")

    # Create V2 cleanup job
    activity_tracker = ActivityTracker(None, state_manager, tenant_id=tenant_id)
    brain = WorkflowBrain(state_manager=state_manager, activity_tracker=activity_tracker)

    job = await brain.create_job(
        workflow=VenueCleanupWorkflow,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        controller_id=request.controller_id,
        user_id=current_user.id,
        options={'nuclear_mode': request.nuclear},
        input_data={
            'nuclear_mode': request.nuclear,
            'created_resources': created_resources,
        },
    )

    # Start cleanup in background
    background_tasks.add_task(run_v2_cleanup_background, job.id, request.controller_id)

    return CleanupResponse(
        cleanup_job_id=job.id,
        original_job_id=request.job_id,
        status="RUNNING",
        nuclear_mode=request.nuclear
    )
