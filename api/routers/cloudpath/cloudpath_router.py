"""
Cloudpath DPSK Migration FastAPI Router

Workflow-specific endpoints for Cloudpath DPSK migration.
For generic job management, see /jobs router.

Endpoints:
- POST /audit - Audit existing DPSK configuration at a venue
- POST /import - Start migration workflow
"""

import logging
import uuid
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime

from dependencies import get_db, get_current_user
from models.user import User, RoleEnum
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller
from redis_client import get_redis_client

from workflow.models import WorkflowJob, Phase, JobStatus, TaskStatus
from workflow.state_manager import RedisStateManager
from workflow.executor import TaskExecutor
from workflow.engine import WorkflowEngine
from workflow.events import WorkflowEventPublisher
from routers.cloudpath.workflow_definition import get_workflow_definition
from routers.cloudpath.cleanup_workflow_definition import get_cleanup_workflow_definition
from routers.cloudpath.utils.cleanup import cleanup_job_resources

# Import phase executors - Import workflow
from routers.cloudpath.phases import parse
from routers.cloudpath.phases import identity_groups
from routers.cloudpath.phases import dpsk_pools
from routers.cloudpath.phases import policy_sets
from routers.cloudpath.phases import attach_policies
from routers.cloudpath.phases import passphrases
from routers.cloudpath.phases import activate
from routers.cloudpath.phases import audit

# Import phase executors - Cleanup workflow
from routers.cloudpath.cleanup_phases import inventory
from routers.cloudpath.cleanup_phases import delete_passphrases
from routers.cloudpath.cleanup_phases import delete_dpsk_pools
from routers.cloudpath.cleanup_phases import delete_identities
from routers.cloudpath.cleanup_phases import delete_identity_groups
from routers.cloudpath.cleanup_phases import verify

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cloudpath-dpsk",
    tags=["Cloudpath DPSK Migration"]
)


# ==================== Request/Response Models ====================

class ImportRequest(BaseModel):
    """Request to start Cloudpath DPSK migration"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")
    dpsk_data: Any = Field(..., description="Cloudpath JSON export data (array of DPSK objects or nested format with pool/dpsks)")
    options: Dict[str, Any] = Field(
        default_factory=lambda: {
            "just_copy_dpsks": True,
            "include_adaptive_policy_sets": False,
            "group_by_vlan": False,
            "skip_expired_dpsks": False,
            "renew_expired_dpsks": True,
            "simulate_delay": False,
            "identity_group_name": None,  # Custom name for identity group (optional)
            "dpsk_service_name": None,  # Custom name for DPSK service/pool (optional)
            "ssid_mode": "none",  # "none", "create_new", or "link_existing"
            "link_to_ssid_id": None  # SSID ID to link to when ssid_mode="link_existing"
        },
        description="Migration options"
    )


class ImportResponse(BaseModel):
    """Response from import endpoint"""
    job_id: str
    status: str
    estimated_duration_seconds: int = 300


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
    job_id: Optional[str] = Field(None, description="Original migration job ID to clean up (optional - if not provided, will cleanup ALL DPSK resources in venue)")
    controller_id: int = Field(..., description="RuckusONE controller ID")
    venue_id: str = Field(..., description="Venue ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (for MSP)")
    nuclear: bool = Field(False, description="Nuclear mode - delete ALL DPSK resources in venue regardless of job")


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


async def run_workflow_background(
    job: WorkflowJob,
    controller_id: int,
    db: Session
):
    """Background task to run workflow"""
    try:
        logger.info(f"🚀 Starting background workflow for job {job.id}")

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

        # Phase executor mapping - map phase IDs to executor functions based on workflow type
        if job.workflow_name == "cloudpath_dpsk_cleanup":
            # Cleanup workflow phase executors
            phase_executors = {
                'inventory_resources': inventory.execute,
                'delete_passphrases': delete_passphrases.execute,
                'delete_dpsk_pools': delete_dpsk_pools.execute,
                'delete_identities': delete_identities.execute,
                'delete_identity_groups': delete_identity_groups.execute,
                'verify_cleanup': verify.execute
            }
        else:
            # Import workflow phase executors (default)
            phase_executors = {
                'parse_validate': parse.execute,
                'create_identity_groups': identity_groups.execute,
                'create_dpsk_pools': dpsk_pools.execute,
                'create_policy_sets': policy_sets.execute,
                'attach_policies': attach_policies.execute,
                'create_passphrases': passphrases.execute,
                'activate_networks': activate.execute,
                'audit_results': audit.execute
            }

        logger.info(f"📋 Workflow: {job.workflow_name}")
        logger.info(f"📋 Phase executors mapped: {list(phase_executors.keys())}")

        # Execute workflow
        final_job = await workflow_engine.execute_workflow(job, phase_executors)

        logger.info(f"✅ Workflow {job.id} completed with status: {final_job.status}")

    except Exception as e:
        logger.exception(f"Workflow {job.id} failed: {str(e)}")


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
    - Policy sets (if attached)

    This helps understand the current state before importing from Cloudpath.
    """
    logger.info(f"🔍 DPSK audit request - controller: {request.controller_id}, venue: {request.venue_id}")

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

    logger.info(f"✅ Controller validated - type: {controller.controller_type}, tenant: {tenant_id}")

    try:
        logger.info(f"DPSK AUDIT - Starting for venue {request.venue_id}")

        # STEP 1: Get venue details
        logger.debug(f"STEP 1: Fetching venue details...")
        venue = await r1_client.venues.get_venue(tenant_id, request.venue_id)
        venue_name = venue.get('name', 'Unknown')
        logger.debug(f"Venue: {venue_name} (ID: {request.venue_id})")

        # STEP 2: Get SSIDs activated at this venue
        logger.debug(f"STEP 2: Fetching SSIDs activated at venue...")
        try:
            # Get all WiFi networks for the tenant
            wifi_networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
            all_networks = wifi_networks_response.get('data', [])
            logger.debug(f"Found {len(all_networks)} total WiFi networks across tenant")

            # Filter networks that are activated at this venue
            # Each network has a venueApGroups array showing which venues it's on
            venue_ssids = []
            for network in all_networks:
                network_name = network.get('name', 'Unknown')
                network_id = network.get('id')
                venue_ap_groups = network.get('venueApGroups', [])

                # Check if this network is activated at our target venue
                for vag in venue_ap_groups:
                    if vag.get('venueId') == request.venue_id:
                        venue_ssids.append({
                            'name': network_name,
                            'id': network_id
                        })
                        logger.debug(f"Found SSID: {network_name} (ID: {network_id})")
                        break

            logger.debug(f"Found {len(venue_ssids)} SSIDs activated at this venue")
            all_ssids = venue_ssids

        except Exception as e:
            logger.exception(f"Error fetching SSIDs: {str(e)}")
            raise

        # STEP 3: Get all DPSK pools (query by tenant)
        logger.debug(f"STEP 3: Fetching all DPSK pools...")
        try:
            dpsk_pools_response = await r1_client.dpsk.query_dpsk_pools(
                tenant_id=tenant_id,
                search_string="",
                page=0,
                limit=1000  # Get all pools
            )
            all_dpsk_pools = dpsk_pools_response.get('data', [])
            logger.debug(f"Found {len(all_dpsk_pools)} DPSK pools")

            # Log pool details for debugging
            for pool in all_dpsk_pools:
                pool_name = pool.get('name', 'Unknown')
                pool_id = pool.get('id', 'Unknown')
                identity_group_id = pool.get('identityGroupId', 'N/A')
                logger.debug(f"Pool: {pool_name} (ID: {pool_id}, Identity Group: {identity_group_id})")
        except Exception as e:
            logger.warning(f"Error fetching DPSK pools: {str(e)} - DPSK feature may not be enabled")
            all_dpsk_pools = []
            logger.debug("DPSK pools exception details", exc_info=True)

        # STEP 4: Get all Identity Groups
        logger.debug(f"STEP 4: Fetching all Identity Groups...")
        try:
            identity_groups_response = await r1_client.identity.query_identity_groups(
                tenant_id=tenant_id,
                search_string="",
                page=0,
                size=1000
            )
            logger.debug(f"Identity groups response keys: {identity_groups_response.keys()}")
            # Identity Groups uses Spring Data pagination format (content, not data)
            all_identity_groups = identity_groups_response.get('content', identity_groups_response.get('data', []))
            total_count = identity_groups_response.get('totalElements', identity_groups_response.get('totalCount', 'N/A'))
            logger.debug(f"Found {len(all_identity_groups)} Identity Groups (total from API: {total_count})")

            # Log identity group details and collect pool IDs
            dpsk_pool_ids = []
            for group in all_identity_groups:
                group_name = group.get('name', 'Unknown')
                group_id = group.get('id', 'Unknown')
                dpsk_pool_id = group.get('dpskPoolId')
                if dpsk_pool_id:
                    dpsk_pool_ids.append(dpsk_pool_id)
                logger.debug(f"Group: {group_name} (ID: {group_id}, DPSK Pool: {dpsk_pool_id or 'N/A'})")
        except Exception as e:
            logger.warning(f"Error fetching Identity Groups: {str(e)} - feature may not be enabled")
            all_identity_groups = []
            dpsk_pool_ids = []
            logger.debug("Identity Groups exception details", exc_info=True)

        # STEP 4.5: Fetch DPSK pool details individually (workaround for broken query endpoint)
        logger.debug(f"STEP 4.5: Fetching DPSK pool details from Identity Groups...")
        all_dpsk_pools_from_groups = []
        for pool_id in dpsk_pool_ids:
            try:
                logger.debug(f"Fetching DPSK pool: {pool_id}")
                pool = await r1_client.dpsk.get_dpsk_pool(pool_id, tenant_id)
                all_dpsk_pools_from_groups.append(pool)
                pool_name = pool.get('name', 'Unknown')
                logger.debug(f"Pool: {pool_name} (ID: {pool_id})")
            except Exception as e:
                logger.warning(f"Error fetching pool {pool_id}: {str(e)}")

        # Use pools from individual fetches if query failed
        if not all_dpsk_pools and all_dpsk_pools_from_groups:
            all_dpsk_pools = all_dpsk_pools_from_groups
            logger.debug(f"Using {len(all_dpsk_pools)} pools from individual fetches")

        # STEP 5: Get detailed SSID information to identify DPSK networks
        logger.debug(f"STEP 5: Fetching detailed SSID information...")
        dpsk_ssids = []
        all_ssids_detailed = []

        for ssid in all_ssids:
            ssid_name = ssid.get('name', 'Unknown')
            ssid_id = ssid.get('id', 'Unknown')

            try:
                # Get full WiFi network details
                logger.debug(f"Fetching details for: {ssid_name}")
                ssid_details = await r1_client.networks.get_wifi_network_by_id(ssid_id, tenant_id)
                all_ssids_detailed.append(ssid_details)

                # Check if this SSID has DPSK enabled
                # Look for DPSK fields in the detailed response
                has_dpsk = False
                dpsk_pool_ids = []

                # Primary DPSK detection fields
                if ssid_details.get('type') == 'dpsk':
                    has_dpsk = True
                    logger.debug(f"  {ssid_name}: Detected via type='dpsk'")

                if ssid_details.get('useDpskService') == True:
                    has_dpsk = True
                    logger.debug(f"  {ssid_name}: Detected via useDpskService=true")

                if ssid_details.get('nwSubType') == 'DPSK':
                    has_dpsk = True
                    logger.debug(f"  {ssid_name}: Detected via nwSubType='DPSK'")

                # Check for DPSK pool/service references
                if 'dpskPool' in ssid_details:
                    has_dpsk = True
                    dpsk_pool_ids.append(ssid_details['dpskPool'])
                    logger.debug(f"  {ssid_name}: Found dpskPool: {ssid_details['dpskPool']}")

                if 'dpskService' in ssid_details:
                    has_dpsk = True
                    if isinstance(ssid_details['dpskService'], list):
                        dpsk_pool_ids.extend(ssid_details['dpskService'])
                    else:
                        dpsk_pool_ids.append(ssid_details['dpskService'])
                    logger.debug(f"  {ssid_name}: Found dpskService: {ssid_details['dpskService']}")

                if 'dpsk' in ssid_details:
                    has_dpsk = True
                    logger.debug(f"  {ssid_name}: Found 'dpsk' field")

                if has_dpsk:
                    logger.debug(f"DPSK SSID: {ssid_name} (ID: {ssid_id})")
                    dpsk_ssids.append(ssid_details)
                else:
                    logger.debug(f"Regular SSID: {ssid_name} (ID: {ssid_id})")

            except Exception as e:
                logger.warning(f"Error fetching details for {ssid_name}: {str(e)}")
                # Keep the basic info if detail fetch fails
                all_ssids_detailed.append(ssid)

        logger.info(f"DPSK Audit Summary: SSIDs={len(all_ssids)}, DPSK SSIDs={len(dpsk_ssids)}, "
                    f"DPSK Pools={len(all_dpsk_pools)}, Identity Groups={len(all_identity_groups)}")

        # Build response
        response = DPSKAuditResponse(
            venue_id=request.venue_id,
            venue_name=venue_name,
            total_ssids=len(all_ssids_detailed),
            total_dpsk_ssids=len(dpsk_ssids),
            total_dpsk_pools=len(all_dpsk_pools),
            total_identity_groups=len(all_identity_groups),
            ssids=all_ssids_detailed,  # Use detailed SSID info
            identity_groups=all_identity_groups,
            dpsk_pools=all_dpsk_pools
        )

        return response

    except Exception as e:
        logger.exception(f"DPSK audit failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to audit DPSK configuration: {str(e)}"
        )


async def _fetch_identity_passphrase_data(
    r1_client,
    venue_id: str = None,
    tenant_id: str = None,
    dpsk_pool_id: str = None
) -> List[Dict[str, Any]]:
    """
    Helper function to fetch and join identity/passphrase data

    Args:
        r1_client: RuckusONE API client
        venue_id: Optional Venue ID to filter by (if None, fetches all)
        tenant_id: Tenant ID
        dpsk_pool_id: Optional DPSK pool ID to filter to a specific pool

    Returns list of dicts with: cloudpath_guid, identity_id, passphrase_id, username,
    passphrase, identity_group_name, dpsk_pool_name
    """
    # 1. Get identity groups (optionally filtered by venue)
    if venue_id:
        logger.info(f"Fetching identity groups for venue {venue_id}...")
        ig_response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id,
            filters={"venueId": [venue_id]},
            page=1,
            size=1000
        )
    else:
        logger.info("Fetching all identity groups (no venue filter)...")
        ig_response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id,
            page=1,
            size=1000
        )
    identity_groups = ig_response.get('content', []) if isinstance(ig_response, dict) else ig_response
    logger.info(f"  Found {len(identity_groups)} identity groups")

    # 1.5. If filtering by specific pool, only keep identity groups linked to that pool
    if dpsk_pool_id:
        identity_groups = [ig for ig in identity_groups if ig.get('dpskPoolId') == dpsk_pool_id]
        logger.info(f"  Filtered to {len(identity_groups)} groups linked to pool {dpsk_pool_id}")

    # 2. Get DPSK pools linked to these identity groups
    # Strategy: Query pools both from identity groups AND directly
    # because identity group's dpskPoolId may not always be set
    dpsk_pool_ids = set()
    ig_to_pool_id = {}  # identity_group_id -> dpsk_pool_id
    ig_id_to_name = {ig.get('id'): ig.get('name') for ig in identity_groups}
    ig_id_set = set(ig_id_to_name.keys())

    # 2a. Get pool IDs from identity groups that have dpskPoolId set
    for ig in identity_groups:
        pool_id = ig.get('dpskPoolId')
        if pool_id:
            dpsk_pool_ids.add(pool_id)
            ig_to_pool_id[ig.get('id')] = pool_id
    logger.info(f"  Found {len(dpsk_pool_ids)} pools from identity groups' dpskPoolId")

    # 2b. Also query DPSK pools directly and find linked identity groups
    # This catches pools where identity group doesn't have dpskPoolId set
    all_pools_from_query = []
    try:
        logger.info("  Querying DPSK pools directly...")
        pools_response = await r1_client.dpsk.query_dpsk_pools(
            tenant_id=tenant_id,
            page=1,
            limit=1000
        )
        all_pools_from_query = pools_response.get('data', []) if isinstance(pools_response, dict) else []
        logger.info(f"  Found {len(all_pools_from_query)} total DPSK pools in tenant")

        for pool in all_pools_from_query:
            pool_id = pool.get('id')
            pool_ig_id = pool.get('identityGroupId')
            if pool_id and pool_ig_id:
                # Only include if we have the identity group
                if pool_ig_id in ig_id_set:
                    if pool_id not in dpsk_pool_ids:
                        logger.info(f"    Adding pool {pool.get('name')} (found via pool's identityGroupId)")
                    dpsk_pool_ids.add(pool_id)
                    ig_to_pool_id[pool_ig_id] = pool_id
    except Exception as pool_query_error:
        logger.warning(f"  ⚠️  Direct pool query failed: {str(pool_query_error)}")
        # Continue with pools found from identity groups

    logger.info(f"  Total DPSK pools to process: {len(dpsk_pool_ids)}")

    # 3. Fetch details of each DPSK pool
    # Use pools from query if available (already have details), otherwise fetch individually
    dpsk_pools = []
    pool_id_to_name = {}  # pool_id -> pool_name
    pools_from_query_by_id = {p.get('id'): p for p in all_pools_from_query}

    for pool_id in dpsk_pool_ids:
        try:
            # Use cached pool from query if available
            if pool_id in pools_from_query_by_id:
                pool = pools_from_query_by_id[pool_id]
            else:
                pool = await r1_client.dpsk.get_dpsk_pool(pool_id, tenant_id)
            dpsk_pools.append(pool)
            pool_id_to_name[pool_id] = pool.get('name', 'Unknown')
            logger.info(f"  Fetched pool: {pool.get('name')} ({pool_id})")
        except Exception as e:
            logger.warning(f"  Failed to fetch pool {pool_id}: {str(e)}")

    # 4. Build identity map: (username, pool_id) -> identity data
    # We use composite key to handle same username across different pools
    identity_map = {}  # (username, pool_id) -> {identity_id, cloudpath_guid, identity_group_name, dpsk_pool_id}
    for ig in identity_groups:
        ig_id = ig.get('id')
        ig_name = ig.get('name', 'Unknown')
        # Get pool_id from ig_to_pool_id mapping (more reliable than ig.dpskPoolId)
        pool_id = ig_to_pool_id.get(ig_id) or ig.get('dpskPoolId')

        logger.info(f"  Fetching identities from group: {ig_name} ({ig_id}), pool={pool_id}...")
        identities_response = await r1_client.identity.get_identities_in_group(
            group_id=ig_id,
            tenant_id=tenant_id,
            page=0,
            size=10000  # Get all identities
        )
        identities = identities_response.get('content', []) if isinstance(identities_response, dict) else identities_response
        logger.info(f"    Found {len(identities)} identities")

        for identity in identities:
            username = identity.get('name')
            if username and pool_id:
                # Use composite key (username, pool_id) to handle duplicates across pools
                identity_map[(username, pool_id)] = {
                    'identity_id': identity.get('id') or '',
                    'cloudpath_guid': identity.get('description') or '',  # GUID stored in description
                    'identity_group_name': ig_name,
                    'dpsk_pool_id': pool_id
                }

    # 5. Build passphrase map: (username, pool_id) -> passphrase data
    passphrase_map = {}  # (username, pool_id) -> {passphrase_id, passphrase, dpsk_pool_id, dpsk_pool_name}
    for pool in dpsk_pools:
        pool_id = pool.get('id')
        pool_name = pool.get('name', 'Unknown')

        logger.info(f"  Fetching passphrases from pool: {pool_name} ({pool_id})...")

        # Try GET endpoint first
        passphrases_response = await r1_client.dpsk.get_passphrases(
            pool_id=pool_id,
            tenant_id=tenant_id,
            page=1,
            size=10000  # Get all passphrases
        )

        # Response may have 'content' or 'data' key depending on API version
        if isinstance(passphrases_response, dict):
            passphrases = passphrases_response.get('content', passphrases_response.get('data', []))
            total_elements = passphrases_response.get('totalElements', len(passphrases))
        else:
            passphrases = passphrases_response
            total_elements = len(passphrases)

        logger.info(f"    GET found {len(passphrases)} passphrases (totalElements: {total_elements})")

        # If GET returned empty, try POST query endpoint as fallback
        if not passphrases:
            try:
                logger.info(f"    Trying POST query endpoint as fallback...")
                query_response = await r1_client.dpsk.query_passphrases(
                    pool_id=pool_id,
                    tenant_id=tenant_id,
                    page=1,
                    limit=10000
                )
                if isinstance(query_response, dict):
                    passphrases = query_response.get('content', query_response.get('data', []))
                else:
                    passphrases = query_response
                logger.info(f"    POST query found {len(passphrases)} passphrases")
            except Exception as query_error:
                logger.warning(f"    POST query failed: {str(query_error)}")

        for pp in passphrases:
            username = pp.get('username') or pp.get('userName')
            if username:
                # Use composite key (username, pool_id) to handle duplicates across pools
                passphrase_map[(username, pool_id)] = {
                    'passphrase_id': pp.get('id') or '',
                    'passphrase': pp.get('passphrase') or '',
                    'dpsk_pool_id': pool_id or '',
                    'dpsk_pool_name': pool_name
                }

    # 6. Join identities and passphrases by (username, pool_id)
    # Keys are tuples of (username, pool_id)
    all_keys = set(identity_map.keys()) | set(passphrase_map.keys())
    logger.info(f"  Total unique (username, pool_id) combinations: {len(all_keys)}")

    # 7. Build result list - ensure all values are strings (not None)
    result = []
    for key in sorted(all_keys, key=lambda x: (x[0], x[1] or '')):
        username, pool_id = key
        identity_data = identity_map.get(key, {})
        passphrase_data = passphrase_map.get(key, {})

        # Get pool ID from key or data
        final_pool_id = pool_id or passphrase_data.get('dpsk_pool_id') or identity_data.get('dpsk_pool_id') or ''

        # Get pool name from passphrase data, or look it up from the pool_id_to_name map
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
    """
    Export identities and passphrases as JSON (for modal preview)

    Returns JSON with columns:
    - cloudpath_guid: From identity description field (set during migration)
    - identity_id: RuckusONE identity ID
    - passphrase_id: RuckusONE passphrase ID
    - username: Identity/passphrase username
    - passphrase: The actual passphrase value
    - identity_group_name: Name of the identity group
    - dpsk_pool_name: Name of the DPSK pool
    """
    logger.info(f"📥 Export identities request - controller: {request.controller_id}, venue: {request.venue_id}, pool: {request.dpsk_pool_id or 'all'}")

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

    try:
        data = await _fetch_identity_passphrase_data(
            r1_client,
            request.venue_id,
            tenant_id,
            dpsk_pool_id=request.dpsk_pool_id
        )
        logger.info(f"✅ Fetched {len(data)} identities/passphrases")

        return ExportIdentitiesResponse(
            venue_id=request.venue_id,
            total_count=len(data),
            data=data
        )

    except Exception as e:
        logger.exception(f"Export identities failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to export identities: {str(e)}"
        )


@router.post("/export-identities-csv")
async def export_identities_csv(
    request: ExportIdentitiesRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Export identities and passphrases as CSV file download
    """
    import csv
    import io

    logger.info(f"📥 Export identities CSV request - controller: {request.controller_id}, venue: {request.venue_id}")

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

    try:
        data = await _fetch_identity_passphrase_data(
            r1_client,
            request.venue_id,
            tenant_id,
            dpsk_pool_id=request.dpsk_pool_id
        )

        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            'cloudpath_guid',
            'identity_id',
            'passphrase_id',
            'username',
            'passphrase',
            'identity_group_name',
            'dpsk_pool_id',
            'dpsk_pool_name'
        ])

        # Data rows
        for row in data:
            writer.writerow([
                row['cloudpath_guid'],
                row['identity_id'],
                row['passphrase_id'],
                row['username'],
                row['passphrase'],
                row['identity_group_name'],
                row['dpsk_pool_id'],
                row['dpsk_pool_name']
            ])

        csv_content = output.getvalue()
        output.close()

        logger.info(f"✅ Exported {len(data)} identities/passphrases to CSV")

        # Return as CSV file download
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=identities_export_{request.venue_id}.csv"
            }
        )

    except Exception as e:
        logger.exception(f"Export identities CSV failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to export identities: {str(e)}"
        )


@router.post("/dpsk-ssids", response_model=DPSKSsidsResponse)
async def get_dpsk_ssids(
    request: DPSKSsidsRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get DPSK-enabled SSIDs at a venue

    Returns list of WiFi networks that have DPSK enabled,
    which can be used to link imported DPSKs to existing networks.
    """
    logger.info(f"🔍 DPSK SSIDs request - controller: {request.controller_id}, venue: {request.venue_id}")

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

    try:
        # Get all WiFi networks for the tenant
        wifi_networks_response = await r1_client.networks.get_wifi_networks(tenant_id)
        all_networks = wifi_networks_response.get('data', [])

        # Filter networks that are activated at this venue and have DPSK enabled
        dpsk_ssids = []
        for network in all_networks:
            network_name = network.get('name', 'Unknown')
            network_id = network.get('id')
            ssid = network.get('ssid', network_name)
            venue_ap_groups = network.get('venueApGroups', [])

            # Check if this network is activated at our target venue
            is_at_venue = any(
                vag.get('venueId') == request.venue_id
                for vag in venue_ap_groups
            )

            if not is_at_venue:
                continue

            # Get full network details to check for DPSK
            try:
                network_details = await r1_client.networks.get_wifi_network_by_id(network_id, tenant_id)

                # Check if this SSID has DPSK enabled
                has_dpsk = False
                dpsk_pool_ids = []

                if network_details.get('type') == 'dpsk':
                    has_dpsk = True
                if network_details.get('useDpskService') == True:
                    has_dpsk = True
                if network_details.get('nwSubType') == 'DPSK':
                    has_dpsk = True

                # Check for DPSK pool/service references
                if 'dpskPool' in network_details:
                    has_dpsk = True
                    dpsk_pool_ids.append(network_details['dpskPool'])
                if 'dpskService' in network_details:
                    has_dpsk = True
                    if isinstance(network_details['dpskService'], list):
                        dpsk_pool_ids.extend(network_details['dpskService'])
                    else:
                        dpsk_pool_ids.append(network_details['dpskService'])
                if 'dpsk' in network_details:
                    has_dpsk = True

                if has_dpsk:
                    dpsk_ssids.append(DPSKSsidInfo(
                        id=network_id,
                        name=network_name,
                        ssid=ssid,
                        dpsk_pool_ids=dpsk_pool_ids
                    ))

            except Exception as e:
                logger.warning(f"Error fetching details for {network_name}: {str(e)}")

        logger.info(f"Found {len(dpsk_ssids)} DPSK SSIDs at venue {request.venue_id}")

        return DPSKSsidsResponse(
            venue_id=request.venue_id,
            dpsk_ssids=dpsk_ssids,
            total_count=len(dpsk_ssids)
        )

    except Exception as e:
        logger.exception(f"DPSK SSIDs fetch failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch DPSK SSIDs: {str(e)}"
        )


@router.post("/import", response_model=ImportResponse)
async def start_migration(
    request: ImportRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start Cloudpath DPSK migration workflow

    Creates a workflow job and starts background execution.
    Returns immediately with job_id for status polling.
    """
    logger.info(f"📥 Import request from user {current_user.id}")
    logger.info(f"   Controller ID: {request.controller_id}")
    logger.info(f"   Venue ID: {request.venue_id}")
    logger.info(f"   Tenant ID: {request.tenant_id}")
    logger.info(f"   DPSK count: {len(request.dpsk_data) if isinstance(request.dpsk_data, list) else 'N/A'}")
    logger.info(f"   Options: {request.options}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)
    logger.info(f"✅ Controller validated: {controller.name} (type={controller.controller_type}, subtype={controller.controller_subtype})")

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

    # Create workflow job
    job_id = str(uuid.uuid4())
    logger.info(f"🆔 Generated job ID: {job_id}")

    workflow_def = get_workflow_definition()
    logger.info(f"📋 Workflow definition loaded: {workflow_def.name} ({len(workflow_def.phases)} phases)")

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
    logger.info(f"✅ Created {len(phases)} phases")

    job = WorkflowJob(
        id=job_id,
        workflow_name=workflow_def.name,
        user_id=current_user.id,
        controller_id=request.controller_id,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        options=request.options,
        input_data={'dpsk_data': request.dpsk_data},
        phases=phases
    )
    logger.info(f"✅ Created WorkflowJob object")

    # Save initial job state
    logger.info(f"💾 Connecting to Redis...")
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)
    logger.info(f"💾 Saving job to Redis...")
    await state_manager.save_job(job)
    logger.info(f"✅ Job saved to Redis")

    # Start workflow in background
    logger.info(f"🚀 Starting background workflow task...")
    background_tasks.add_task(run_workflow_background, job, request.controller_id, db)

    logger.info(f"✅ Workflow job {job_id} created and queued")

    return ImportResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        estimated_duration_seconds=300
    )


@router.post("/preview-cleanup", response_model=PreviewCleanupResponse)
async def preview_cleanup(
    request: PreviewCleanupRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Preview what would be deleted in a nuclear cleanup

    Runs an inventory audit of the venue to show all DPSK resources
    without actually deleting anything. Returns categorized list of resources.
    """
    logger.info(f"🔍 Preview cleanup request from user {current_user.id}")
    logger.info(f"   Venue ID: {request.venue_id}")

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

    try:
        # Create R1 client
        logger.info(f"Creating R1 client for controller {request.controller_id}...")
        r1_client = create_r1_client_from_controller(request.controller_id, db)
        logger.info(f"✅ R1 client created successfully")

        # Use the inventory phase to audit the venue
        logger.info(f"Starting venue resource audit...")
        from routers.cloudpath.cleanup_phases.inventory import _audit_venue_resources

        inventory = await _audit_venue_resources(
            r1_client=r1_client,
            venue_id=request.venue_id,
            tenant_id=tenant_id
        )

        total_resources = sum(len(items) for items in inventory.values())

        logger.info(f"✅ Preview complete: {total_resources} total resources found")
        logger.info(f"   - Passphrases: {len(inventory.get('passphrases', []))}")
        logger.info(f"   - DPSK Pools: {len(inventory.get('dpsk_pools', []))}")
        logger.info(f"   - Identity Groups: {len(inventory.get('identity_groups', []))}")
        logger.info(f"   - Identities: {len(inventory.get('identities', []))}")

        return PreviewCleanupResponse(
            venue_id=request.venue_id,
            inventory=inventory,
            total_resources=total_resources
        )

    except Exception as e:
        logger.exception(f"Preview cleanup failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to preview cleanup: {str(e)}"
        )


@router.post("/cleanup", response_model=CleanupResponse)
async def start_cleanup(
    request: CleanupRequest = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start cleanup workflow to delete DPSK resources

    Two modes:
    1. Job cleanup: Provide job_id to cleanup resources from specific migration
    2. Nuclear cleanup: Set nuclear=true to delete ALL DPSK resources in venue

    Creates a cleanup workflow job that will:
    1. Inventory resources (from job OR from venue audit)
    2. Delete DPSK passphrases
    3. Delete DPSK pools
    4. Delete identities (placeholder - requires manual deletion)
    5. Delete identity groups (placeholder - requires manual deletion)
    6. Verify cleanup completion

    Returns immediately with cleanup_job_id for status polling.
    """
    logger.info(f"🧹 Cleanup request from user {current_user.id}")
    logger.info(f"   Mode: {'☢️  NUCLEAR' if request.nuclear else 'Job-specific'}")
    logger.info(f"   Original Job ID: {request.job_id or 'N/A'}")
    logger.info(f"   Controller ID: {request.controller_id}")
    logger.info(f"   Venue ID: {request.venue_id}")

    # Validate controller access
    controller = validate_controller_access(request.controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    # Get the original job to extract created_resources (if job_id provided)
    redis_client = await get_redis_client()
    state_manager = RedisStateManager(redis_client)

    created_resources = {}
    original_job = None

    if request.job_id and not request.nuclear:
        # Job-specific cleanup mode
        try:
            original_job = await state_manager.get_job(request.job_id)
            if not original_job:
                raise HTTPException(
                    status_code=404,
                    detail=f"Original job {request.job_id} not found"
                )

            # Verify user owns the job or is admin
            if original_job.user_id != current_user.id and current_user.role not in [RoleEnum.ADMIN, RoleEnum.SUPER]:
                raise HTTPException(
                    status_code=403,
                    detail="You don't have permission to cleanup this job"
                )

            created_resources = original_job.created_resources
            logger.info(f"📦 Using resources from job {request.job_id}")

        except Exception as e:
            logger.error(f"Failed to retrieve original job: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve original job: {str(e)}"
            )
    elif request.nuclear:
        # Nuclear mode - will audit venue in inventory phase
        logger.warning(f"☢️  NUCLEAR MODE - Will delete ALL DPSK resources in venue {request.venue_id}")
        created_resources = {}  # Will be populated by inventory phase via audit
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either job_id or set nuclear=true"
        )

    # Determine tenant_id
    tenant_id = request.tenant_id or controller.r1_tenant_id

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required for MSP controllers"
        )

    # Create cleanup workflow job
    cleanup_job_id = str(uuid.uuid4())
    logger.info(f"🆔 Generated cleanup job ID: {cleanup_job_id}")

    workflow_def = get_cleanup_workflow_definition()
    logger.info(f"📋 Cleanup workflow definition loaded: {workflow_def.name} ({len(workflow_def.phases)} phases)")

    # Create phases from definition
    phases = [
        Phase(
            id=phase_def.id,
            name=phase_def.name,
            status=TaskStatus.PENDING,
            dependencies=phase_def.dependencies,
            parallelizable=phase_def.parallelizable,
            critical=phase_def.critical,
            executor=phase_def.executor,
            skip_condition=phase_def.skip_condition
        )
        for phase_def in workflow_def.phases
    ]

    # Create job with created_resources (from job or empty for nuclear mode)
    job = WorkflowJob(
        id=cleanup_job_id,
        workflow_name=workflow_def.name,
        status=JobStatus.RUNNING,
        user_id=current_user.id,
        controller_id=request.controller_id,
        venue_id=request.venue_id,
        tenant_id=tenant_id,
        phases=phases,
        created_resources=created_resources,  # Pass resources to cleanup (empty if nuclear)
        input_data={
            "nuclear_mode": request.nuclear  # Pass nuclear_mode to phase context
        },
        metadata={
            "original_job_id": request.job_id,
            "cleanup_started_by": current_user.email,
            "nuclear_mode": request.nuclear
        }
    )

    # Save job
    await state_manager.save_job(job)
    logger.info(f"💾 Cleanup job {cleanup_job_id} saved to Redis")

    # Start workflow in background (using same pattern as import endpoint)
    background_tasks.add_task(run_workflow_background, job, request.controller_id, db)

    logger.info(f"✅ Cleanup workflow job {cleanup_job_id} created and queued")

    return CleanupResponse(
        cleanup_job_id=cleanup_job_id,
        original_job_id=request.job_id,
        status=JobStatus.RUNNING,
        nuclear_mode=request.nuclear
    )
