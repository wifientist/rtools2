"""
DPSK Orchestrator Management API Router.

This router provides CRUD operations and management endpoints for
DPSK orchestrators.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from clients.r1_client import get_dynamic_r1_client
from models.controller import Controller
from models.orchestrator import (
    DPSKOrchestrator,
    OrchestratorSourcePool,
    OrchestratorSyncEvent,
    PassphraseMapping
)
from models.user import User
from scheduler.service import get_scheduler
from routers.orchestrator.sync_engine import SyncEngine, run_scheduled_sync

logger = logging.getLogger(__name__)


# ========== RBAC Helpers ==========

def get_accessible_orchestrators_query(db: Session, current_user: User):
    """
    Build a query for orchestrators the user can access based on their role.

    - Super: All orchestrators
    - Admin: All orchestrators where the controller's owner is in the same company
    - User: Only orchestrators on controllers they own
    """
    if current_user.role.value == "super":
        # Super can see everything
        return db.query(DPSKOrchestrator)

    elif current_user.role.value == "admin":
        # Admin can see all orchestrators in their company
        # Join through Controller -> User to find company matches
        return db.query(DPSKOrchestrator).join(
            Controller, DPSKOrchestrator.controller_id == Controller.id
        ).join(
            User, Controller.user_id == User.id
        ).filter(
            User.company_id == current_user.company_id
        )

    else:
        # Regular user can only see their own orchestrators
        user_controller_ids = [c.id for c in current_user.controllers]
        return db.query(DPSKOrchestrator).filter(
            DPSKOrchestrator.controller_id.in_(user_controller_ids)
        )


def can_access_orchestrator(db: Session, current_user: User, orchestrator: DPSKOrchestrator) -> bool:
    """
    Check if the current user can access a specific orchestrator.

    - Super: Always yes
    - Admin: Yes if the controller's owner is in the same company
    - User: Yes only if they own the controller
    """
    if current_user.role.value == "super":
        return True

    controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
    if not controller:
        return False

    if current_user.role.value == "admin":
        # Admin can access if controller owner is in the same company
        controller_owner = db.query(User).filter_by(id=controller.user_id).first()
        return controller_owner and controller_owner.company_id == current_user.company_id

    # Regular user can only access their own
    return controller.user_id == current_user.id

router = APIRouter(prefix="/orchestrators", tags=["DPSK Orchestrators"])


# ========== Pydantic Models ==========

class SourcePoolCreate(BaseModel):
    pool_id: str
    pool_name: Optional[str] = None
    identity_group_id: Optional[str] = None


class OrchestratorCreate(BaseModel):
    name: str = Field(..., description="Friendly name for the orchestrator")
    # controller_id is optional - if not provided, uses user's active controller
    controller_id: Optional[int] = Field(None, description="ID of the controller (defaults to active controller)")
    tenant_id: Optional[str] = Field(None, description="Tenant ID (for MSP)")
    venue_id: Optional[str] = Field(None, description="Venue ID to scope discovery")

    site_wide_pool_id: str = Field(..., description="Target site-wide DPSK pool ID")
    site_wide_pool_name: Optional[str] = Field(None, description="Site-wide pool name")
    site_wide_identity_group_id: Optional[str] = Field(None, description="Site-wide identity group ID")

    source_pools: List[SourcePoolCreate] = Field(default=[], description="Initial source pools")
    sync_interval_minutes: int = Field(default=30, ge=5, le=1440)

    # Discovery patterns - used when manually triggering pool discovery
    include_patterns: List[str] = Field(default=["Unit*", "*PerUnit*"])
    exclude_patterns: List[str] = Field(default=["SiteWide*", "Guest*", "Visitor*"])


class OrchestratorUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    sync_interval_minutes: Optional[int] = Field(None, ge=5, le=1440)
    auto_delete: Optional[bool] = None
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None


class SourcePoolResponse(BaseModel):
    id: int
    pool_id: str
    pool_name: Optional[str]
    identity_group_id: Optional[str]
    identity_group_name: Optional[str] = None
    last_sync_at: Optional[datetime]
    passphrase_count: int
    discovered_at: Optional[datetime]

    # Pool details (populated when refresh_counts=true)
    passphrase_format: Optional[str] = None  # e.g., "KEYBOARD_FRIENDLY"
    passphrase_length: Optional[int] = None
    device_count_limit: Optional[int] = None  # null = unlimited
    expiration_type: Optional[str] = None  # e.g., "NEVER", "FIXED_DATE"

    class Config:
        from_attributes = True


class SyncEventResponse(BaseModel):
    id: int
    event_type: str
    status: str
    added_count: int
    updated_count: int
    flagged_for_removal: int
    orphans_found: int
    errors: List[str] = []
    started_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class OrchestratorResponse(BaseModel):
    id: int
    name: str
    controller_id: int
    tenant_id: Optional[str]
    venue_id: Optional[str]
    site_wide_pool_id: str
    site_wide_pool_name: Optional[str]
    sync_interval_minutes: int
    enabled: bool
    auto_delete: bool
    auto_discover_enabled: bool
    include_patterns: List[str]
    exclude_patterns: List[str]
    webhook_id: Optional[str]
    webhook_path: Optional[str] = None  # Path to configure in RuckusONE
    webhook_secret_configured: bool = False  # Whether a secret is set
    created_at: datetime
    last_sync_at: Optional[datetime]
    last_discovery_at: Optional[datetime]
    source_pool_count: int = 0
    flagged_count: int = 0
    orphan_count: int = 0

    class Config:
        from_attributes = True


class OrchestratorDetailResponse(OrchestratorResponse):
    source_pools: List[SourcePoolResponse] = []
    recent_sync_events: List[SyncEventResponse] = []


class PassphraseMappingResponse(BaseModel):
    id: int
    source_pool_id: str
    source_pool_name: Optional[str]
    source_passphrase_id: Optional[str]
    source_username: Optional[str]
    target_passphrase_id: Optional[str]
    sync_status: str
    vlan_id: Optional[int]
    passphrase_preview: Optional[str]
    suggested_source_pool_id: Optional[str]
    created_at: datetime
    last_synced_at: Optional[datetime]
    flagged_at: Optional[datetime]

    class Config:
        from_attributes = True


class CopyToSourceRequest(BaseModel):
    target_pool_id: str = Field(..., description="Pool ID to copy the orphan passphrase to")


# ========== Endpoints ==========

@router.get("/", response_model=List[OrchestratorResponse])
async def list_orchestrators(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all orchestrators accessible by the current user.

    RBAC:
    - Super: All orchestrators
    - Admin: All orchestrators in their company
    - User: Only their own orchestrators
    """
    query = get_accessible_orchestrators_query(db, current_user)
    orchestrators = query.order_by(DPSKOrchestrator.created_at.desc()).all()

    result = []
    for orch in orchestrators:
        # Count flagged passphrases (includes flagged_removal and target_missing)
        flagged_count = db.query(PassphraseMapping).filter(
            PassphraseMapping.orchestrator_id == orch.id,
            PassphraseMapping.sync_status.in_(["flagged_removal", "target_missing"])
        ).count()
        orphan_count = db.query(PassphraseMapping).filter_by(
            orchestrator_id=orch.id,
            sync_status="orphan"
        ).count()

        response = OrchestratorResponse(
            id=orch.id,
            name=orch.name,
            controller_id=orch.controller_id,
            tenant_id=orch.tenant_id,
            venue_id=orch.venue_id,
            site_wide_pool_id=orch.site_wide_pool_id,
            site_wide_pool_name=orch.site_wide_pool_name,
            sync_interval_minutes=orch.sync_interval_minutes,
            enabled=orch.enabled,
            auto_delete=orch.auto_delete,
            auto_discover_enabled=orch.auto_discover_enabled,
            include_patterns=orch.include_patterns or [],
            exclude_patterns=orch.exclude_patterns or [],
            webhook_id=orch.webhook_id,
            webhook_path="/api/orchestrator/webhook",
            webhook_secret_configured=bool(orch.webhook_secret),
            created_at=orch.created_at,
            last_sync_at=orch.last_sync_at,
            last_discovery_at=orch.last_discovery_at,
            source_pool_count=len(orch.source_pools),
            flagged_count=flagged_count,
            orphan_count=orphan_count
        )
        result.append(response)

    return result


@router.post("/", response_model=OrchestratorResponse)
async def create_orchestrator(
    request: OrchestratorCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new DPSK orchestrator.

    The orchestrator is created on the user's active controller by default.
    Only the controller owner can create orchestrators on their controllers.
    """
    # Determine which controller to use
    controller_id = request.controller_id
    if controller_id is None:
        # Use the user's active controller
        controller_id = current_user.active_controller_id
        if not controller_id:
            raise HTTPException(
                status_code=400,
                detail="No active controller set. Please select a controller first."
            )

    # Validate controller access - user must own the controller to create orchestrators on it
    controller = db.query(Controller).filter_by(id=controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    if controller.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only create orchestrators on your own controllers")

    # Auto-fetch identity group ID from the site-wide DPSK pool if not provided
    site_wide_identity_group_id = request.site_wide_identity_group_id
    if not site_wide_identity_group_id:
        try:
            r1_client = get_dynamic_r1_client(controller.id, current_user, db)
            pool_details = await r1_client.dpsk.get_dpsk_pool(
                pool_id=request.site_wide_pool_id,
                tenant_id=request.tenant_id
            )
            site_wide_identity_group_id = pool_details.get('identityGroupId')
            logger.info(f"Auto-fetched identity group ID: {site_wide_identity_group_id} for pool {request.site_wide_pool_id}")
        except Exception as e:
            logger.warning(f"Could not auto-fetch identity group ID: {e}")

    # Create orchestrator
    orchestrator = DPSKOrchestrator(
        name=request.name,
        controller_id=controller_id,
        tenant_id=request.tenant_id,
        venue_id=request.venue_id,
        site_wide_pool_id=request.site_wide_pool_id,
        site_wide_pool_name=request.site_wide_pool_name,
        site_wide_identity_group_id=site_wide_identity_group_id,
        sync_interval_minutes=request.sync_interval_minutes,
        auto_discover_enabled=False,  # Discovery is now manual only
        include_patterns=request.include_patterns,
        exclude_patterns=request.exclude_patterns
    )
    db.add(orchestrator)
    db.commit()
    db.refresh(orchestrator)

    # Add initial source pools
    for pool in request.source_pools:
        source_pool = OrchestratorSourcePool(
            orchestrator_id=orchestrator.id,
            pool_id=pool.pool_id,
            pool_name=pool.pool_name,
            identity_group_id=pool.identity_group_id
        )
        db.add(source_pool)

    db.commit()

    # Register scheduled job
    try:
        scheduler = get_scheduler()
        await scheduler.register_job(
            job_id=f"orchestrator_{orchestrator.id}_sync",
            name=f"DPSK Sync: {orchestrator.name}",
            callable_path="routers.orchestrator.sync_engine:run_scheduled_sync",
            trigger_type="interval",
            trigger_config={"minutes": orchestrator.sync_interval_minutes},
            callable_kwargs={"orchestrator_id": orchestrator.id},
            owner_type="orchestrator",
            owner_id=str(orchestrator.id),
            description=f"Periodic sync for {orchestrator.name}"
        )
    except Exception as e:
        logger.error(f"Failed to register scheduler job: {e}")

    logger.info(f"Created orchestrator {orchestrator.id}: {orchestrator.name}")

    return OrchestratorResponse(
        id=orchestrator.id,
        name=orchestrator.name,
        controller_id=orchestrator.controller_id,
        tenant_id=orchestrator.tenant_id,
        venue_id=orchestrator.venue_id,
        site_wide_pool_id=orchestrator.site_wide_pool_id,
        site_wide_pool_name=orchestrator.site_wide_pool_name,
        sync_interval_minutes=orchestrator.sync_interval_minutes,
        enabled=orchestrator.enabled,
        auto_delete=orchestrator.auto_delete,
        auto_discover_enabled=orchestrator.auto_discover_enabled,
        include_patterns=orchestrator.include_patterns or [],
        exclude_patterns=orchestrator.exclude_patterns or [],
        webhook_id=orchestrator.webhook_id,
        webhook_path="/api/orchestrator/webhook",
        webhook_secret_configured=bool(orchestrator.webhook_secret),
        created_at=orchestrator.created_at,
        last_sync_at=orchestrator.last_sync_at,
        last_discovery_at=orchestrator.last_discovery_at,
        source_pool_count=len(request.source_pools),
        flagged_count=0,
        orphan_count=0
    )


@router.get("/{orchestrator_id}", response_model=OrchestratorDetailResponse)
async def get_orchestrator(
    orchestrator_id: int,
    refresh_counts: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about an orchestrator.

    Args:
        refresh_counts: If True, fetch live passphrase counts from RuckusONE
                       and update the database. Default: False
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Store pool details for response (keyed by pool_id)
    pool_details_map = {}

    # Optionally refresh passphrase counts and pool details from RuckusONE
    if refresh_counts and orchestrator.source_pools:
        try:
            controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
            if controller:
                r1_client = get_dynamic_r1_client(controller.id, current_user, db)

                for source_pool in orchestrator.source_pools:
                    try:
                        # Query with limit=1 just to get totalElements
                        result = await r1_client.dpsk.query_passphrases(
                            pool_id=source_pool.pool_id,
                            tenant_id=orchestrator.tenant_id,
                            page=1,
                            limit=1
                        )
                        # Try various field names the API might use (using 'is not None' to handle 0 correctly)
                        total = result.get('totalElements')
                        if total is None:
                            total = result.get('total')
                        if total is None:
                            total = result.get('totalCount')
                        if total is None:
                            # Fall back to counting items in response
                            total = len(result.get('data', result.get('content', [])))
                        source_pool.passphrase_count = total
                        logger.debug(f"Pool {source_pool.pool_name}: {total} passphrases")
                    except Exception as e:
                        logger.warning(f"Failed to get count for pool {source_pool.pool_id}: {e}")

                    # Also fetch pool details
                    try:
                        pool_data = await r1_client.dpsk.get_dpsk_pool(
                            pool_id=source_pool.pool_id,
                            tenant_id=orchestrator.tenant_id
                        )
                        if isinstance(pool_data, dict):
                            # Handle multiple possible field names from RuckusONE API
                            pool_details_map[source_pool.pool_id] = {
                                'passphrase_format': (
                                    pool_data.get('passphraseFormat') or
                                    pool_data.get('passphraseType')
                                ),
                                'passphrase_length': pool_data.get('passphraseLength'),
                                'device_count_limit': (
                                    pool_data.get('deviceCountLimit') if pool_data.get('deviceCountLimit') is not None else
                                    pool_data.get('maxDevicesPerPassphrase') if pool_data.get('maxDevicesPerPassphrase') is not None else
                                    pool_data.get('maxDevices')
                                ),
                                'expiration_type': (
                                    pool_data.get('expirationType') or
                                    pool_data.get('passphraseExpiration')
                                ),
                                'identity_group_name': pool_data.get('identityGroupName') or (
                                    pool_data.get('identityGroup', {}).get('name') if isinstance(pool_data.get('identityGroup'), dict) else None
                                ),
                            }
                            logger.debug(f"Pool {source_pool.pool_name} details: {pool_details_map[source_pool.pool_id]}")
                    except Exception as e:
                        logger.warning(f"Failed to get details for pool {source_pool.pool_id}: {e}")

                db.commit()
        except Exception as e:
            logger.error(f"Failed to refresh counts: {e}")

    # Get counts (flagged includes both flagged_removal and target_missing)
    flagged_count = db.query(PassphraseMapping).filter(
        PassphraseMapping.orchestrator_id == orchestrator.id,
        PassphraseMapping.sync_status.in_(["flagged_removal", "target_missing"])
    ).count()
    orphan_count = db.query(PassphraseMapping).filter_by(
        orchestrator_id=orchestrator.id,
        sync_status="orphan"
    ).count()

    # Get recent sync events
    recent_events = db.query(OrchestratorSyncEvent).filter_by(
        orchestrator_id=orchestrator.id
    ).order_by(OrchestratorSyncEvent.started_at.desc()).limit(10).all()

    return OrchestratorDetailResponse(
        id=orchestrator.id,
        name=orchestrator.name,
        controller_id=orchestrator.controller_id,
        tenant_id=orchestrator.tenant_id,
        venue_id=orchestrator.venue_id,
        site_wide_pool_id=orchestrator.site_wide_pool_id,
        site_wide_pool_name=orchestrator.site_wide_pool_name,
        sync_interval_minutes=orchestrator.sync_interval_minutes,
        enabled=orchestrator.enabled,
        auto_delete=orchestrator.auto_delete,
        auto_discover_enabled=orchestrator.auto_discover_enabled,
        include_patterns=orchestrator.include_patterns or [],
        exclude_patterns=orchestrator.exclude_patterns or [],
        webhook_id=orchestrator.webhook_id,
        webhook_path="/api/orchestrator/webhook",
        webhook_secret_configured=bool(orchestrator.webhook_secret),
        created_at=orchestrator.created_at,
        last_sync_at=orchestrator.last_sync_at,
        last_discovery_at=orchestrator.last_discovery_at,
        source_pool_count=len(orchestrator.source_pools),
        flagged_count=flagged_count,
        orphan_count=orphan_count,
        source_pools=[
            SourcePoolResponse(
                id=sp.id,
                pool_id=sp.pool_id,
                pool_name=sp.pool_name,
                identity_group_id=sp.identity_group_id,
                identity_group_name=pool_details_map.get(sp.pool_id, {}).get('identity_group_name'),
                last_sync_at=sp.last_sync_at,
                passphrase_count=sp.passphrase_count,
                discovered_at=sp.discovered_at,
                passphrase_format=pool_details_map.get(sp.pool_id, {}).get('passphrase_format'),
                passphrase_length=pool_details_map.get(sp.pool_id, {}).get('passphrase_length'),
                device_count_limit=pool_details_map.get(sp.pool_id, {}).get('device_count_limit'),
                expiration_type=pool_details_map.get(sp.pool_id, {}).get('expiration_type'),
            )
            for sp in orchestrator.source_pools
        ],
        recent_sync_events=[
            SyncEventResponse(
                id=e.id,
                event_type=e.event_type,
                status=e.status,
                added_count=e.added_count,
                updated_count=e.updated_count,
                flagged_for_removal=e.flagged_for_removal,
                orphans_found=e.orphans_found,
                errors=e.errors or [],
                started_at=e.started_at,
                completed_at=e.completed_at
            )
            for e in recent_events
        ]
    )


@router.put("/{orchestrator_id}", response_model=OrchestratorResponse)
async def update_orchestrator(
    orchestrator_id: int,
    request: OrchestratorUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an orchestrator's configuration."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Update fields
    if request.name is not None:
        orchestrator.name = request.name
    if request.enabled is not None:
        orchestrator.enabled = request.enabled
    if request.sync_interval_minutes is not None:
        orchestrator.sync_interval_minutes = request.sync_interval_minutes
    if request.auto_delete is not None:
        orchestrator.auto_delete = request.auto_delete
    if request.include_patterns is not None:
        orchestrator.include_patterns = request.include_patterns
    if request.exclude_patterns is not None:
        orchestrator.exclude_patterns = request.exclude_patterns

    db.commit()
    db.refresh(orchestrator)

    # Update scheduler job if interval or enabled changed
    if request.sync_interval_minutes is not None or request.enabled is not None:
        try:
            scheduler = get_scheduler()
            await scheduler.update_job(
                f"orchestrator_{orchestrator.id}_sync",
                trigger_config={"minutes": orchestrator.sync_interval_minutes},
                enabled=orchestrator.enabled
            )
            logger.info(f"Updated scheduler job for orchestrator {orchestrator.id}: enabled={orchestrator.enabled}, interval={orchestrator.sync_interval_minutes}m")
        except Exception as e:
            logger.error(f"Failed to update scheduler job: {e}")

    # Get counts (flagged includes both flagged_removal and target_missing)
    flagged_count = db.query(PassphraseMapping).filter(
        PassphraseMapping.orchestrator_id == orchestrator.id,
        PassphraseMapping.sync_status.in_(["flagged_removal", "target_missing"])
    ).count()
    orphan_count = db.query(PassphraseMapping).filter_by(
        orchestrator_id=orchestrator.id,
        sync_status="orphan"
    ).count()

    return OrchestratorResponse(
        id=orchestrator.id,
        name=orchestrator.name,
        controller_id=orchestrator.controller_id,
        tenant_id=orchestrator.tenant_id,
        venue_id=orchestrator.venue_id,
        site_wide_pool_id=orchestrator.site_wide_pool_id,
        site_wide_pool_name=orchestrator.site_wide_pool_name,
        sync_interval_minutes=orchestrator.sync_interval_minutes,
        enabled=orchestrator.enabled,
        auto_delete=orchestrator.auto_delete,
        auto_discover_enabled=orchestrator.auto_discover_enabled,
        include_patterns=orchestrator.include_patterns or [],
        exclude_patterns=orchestrator.exclude_patterns or [],
        webhook_id=orchestrator.webhook_id,
        webhook_path="/api/orchestrator/webhook",
        webhook_secret_configured=bool(orchestrator.webhook_secret),
        created_at=orchestrator.created_at,
        last_sync_at=orchestrator.last_sync_at,
        last_discovery_at=orchestrator.last_discovery_at,
        source_pool_count=len(orchestrator.source_pools),
        flagged_count=flagged_count,
        orphan_count=orphan_count
    )


@router.delete("/{orchestrator_id}")
async def delete_orchestrator(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an orchestrator (does not delete actual DPSK pools in RuckusONE)."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Unregister scheduler job
    try:
        scheduler = get_scheduler()
        await scheduler.unregister_job(f"orchestrator_{orchestrator.id}_sync")
    except Exception as e:
        logger.error(f"Failed to unregister scheduler job: {e}")

    # Delete orchestrator (cascade deletes related records)
    db.delete(orchestrator)
    db.commit()

    logger.info(f"Deleted orchestrator {orchestrator_id}")

    return {"status": "deleted", "orchestrator_id": orchestrator_id}


# ========== Source Pool Management ==========

@router.post("/{orchestrator_id}/source-pools", response_model=SourcePoolResponse)
async def add_source_pool(
    orchestrator_id: int,
    request: SourcePoolCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a source pool to an orchestrator."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if pool already exists
    existing = db.query(OrchestratorSourcePool).filter_by(
        orchestrator_id=orchestrator_id,
        pool_id=request.pool_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Pool already exists as source")

    source_pool = OrchestratorSourcePool(
        orchestrator_id=orchestrator_id,
        pool_id=request.pool_id,
        pool_name=request.pool_name,
        identity_group_id=request.identity_group_id
    )
    db.add(source_pool)
    db.commit()
    db.refresh(source_pool)

    return SourcePoolResponse(
        id=source_pool.id,
        pool_id=source_pool.pool_id,
        pool_name=source_pool.pool_name,
        identity_group_id=source_pool.identity_group_id,
        last_sync_at=source_pool.last_sync_at,
        passphrase_count=source_pool.passphrase_count,
        discovered_at=source_pool.discovered_at
    )


class SourcePoolInfo(BaseModel):
    pool_id: str
    pool_name: Optional[str] = None


class BulkSourcePoolUpdate(BaseModel):
    pools: List[SourcePoolInfo] = Field(..., description="List of pools with IDs and names to set as source pools")


@router.put("/{orchestrator_id}/source-pools")
async def update_source_pools(
    orchestrator_id: int,
    request: BulkSourcePoolUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Bulk update source pools for an orchestrator.

    This replaces all existing source pools with the provided list.
    Pools that were previously configured but not in the new list will be removed.
    """
    logger.info(f"update_source_pools called for orchestrator {orchestrator_id}")
    logger.info(f"Request pools: {[(p.pool_id, p.pool_name) for p in request.pools]}")

    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Get current pool IDs
    current_pools = {p.pool_id: p for p in orchestrator.source_pools}
    new_pools_map = {p.pool_id: p.pool_name for p in request.pools}
    new_pool_ids = set(new_pools_map.keys())

    # Remove pools that are no longer in the list
    for pool_id, pool in current_pools.items():
        if pool_id not in new_pool_ids:
            db.delete(pool)

    # Add new pools that don't exist yet
    # First, try to get R1 client to fetch identity_group_id for new pools
    r1_client = None
    try:
        controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
        if controller:
            r1_client = get_dynamic_r1_client(controller.id, current_user, db)
    except Exception as e:
        logger.warning(f"Could not get R1 client for identity group lookup: {e}")

    for pool_id in new_pool_ids:
        if pool_id not in current_pools:
            identity_group_id = None
            # Try to fetch identity_group_id from the pool
            if r1_client:
                try:
                    pool_details = await r1_client.dpsk.get_dpsk_pool(
                        pool_id=pool_id,
                        tenant_id=orchestrator.tenant_id
                    )
                    identity_group_id = pool_details.get('identityGroupId')
                    logger.info(f"Fetched identity_group_id {identity_group_id} for pool {pool_id}")
                except Exception as e:
                    logger.warning(f"Could not fetch identity_group_id for pool {pool_id}: {e}")

            source_pool = OrchestratorSourcePool(
                orchestrator_id=orchestrator_id,
                pool_id=pool_id,
                pool_name=new_pools_map.get(pool_id),
                identity_group_id=identity_group_id
            )
            db.add(source_pool)
        else:
            # Update pool name if it changed
            existing = current_pools[pool_id]
            new_name = new_pools_map.get(pool_id)
            if new_name and existing.pool_name != new_name:
                existing.pool_name = new_name

            # Also try to populate identity_group_id if missing
            if not existing.identity_group_id and r1_client:
                try:
                    pool_details = await r1_client.dpsk.get_dpsk_pool(
                        pool_id=pool_id,
                        tenant_id=orchestrator.tenant_id
                    )
                    existing.identity_group_id = pool_details.get('identityGroupId')
                    logger.info(f"Updated identity_group_id for existing pool {pool_id}")
                except Exception as e:
                    logger.debug(f"Could not fetch identity_group_id for pool {pool_id}: {e}")

    db.commit()

    # Refresh and return updated count
    db.refresh(orchestrator)

    logger.info(f"After save - source pools: {[(p.pool_id, p.pool_name) for p in orchestrator.source_pools]}")

    return {
        "status": "success",
        "source_pool_count": len(orchestrator.source_pools),
        "added": len(new_pool_ids - set(current_pools.keys())),
        "removed": len(set(current_pools.keys()) - new_pool_ids)
    }


@router.delete("/{orchestrator_id}/source-pools/{pool_id}")
async def remove_source_pool(
    orchestrator_id: int,
    pool_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove a source pool from an orchestrator."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    source_pool = db.query(OrchestratorSourcePool).filter_by(
        orchestrator_id=orchestrator_id,
        pool_id=pool_id
    ).first()
    if not source_pool:
        raise HTTPException(status_code=404, detail="Source pool not found")

    db.delete(source_pool)
    db.commit()

    return {"status": "deleted", "pool_id": pool_id}


@router.post("/{orchestrator_id}/refresh-identity-groups")
async def refresh_identity_group_links(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Refresh identity group links for all pools in the orchestrator.

    This fetches the identityGroupId from R1 for each DPSK pool and updates
    the database. Use this if pools were added before identity group linking
    was implemented, or if the links are missing.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    r1_client = get_dynamic_r1_client(controller.id, current_user, db)

    updated = []
    errors = []

    # Refresh site-wide pool
    if orchestrator.site_wide_pool_id:
        try:
            pool_details = await r1_client.dpsk.get_dpsk_pool(
                pool_id=orchestrator.site_wide_pool_id,
                tenant_id=orchestrator.tenant_id
            )
            new_identity_group_id = pool_details.get('identityGroupId')
            if new_identity_group_id != orchestrator.site_wide_identity_group_id:
                orchestrator.site_wide_identity_group_id = new_identity_group_id
                updated.append({
                    "pool_id": orchestrator.site_wide_pool_id,
                    "pool_name": orchestrator.site_wide_pool_name or "Site-Wide Pool",
                    "identity_group_id": new_identity_group_id,
                    "type": "site_wide"
                })
                logger.info(f"Updated site-wide identity_group_id to {new_identity_group_id}")
        except Exception as e:
            errors.append({
                "pool_id": orchestrator.site_wide_pool_id,
                "error": str(e)
            })
            logger.warning(f"Failed to fetch identity group for site-wide pool: {e}")

    # Refresh source pools
    for source_pool in orchestrator.source_pools:
        try:
            pool_details = await r1_client.dpsk.get_dpsk_pool(
                pool_id=source_pool.pool_id,
                tenant_id=orchestrator.tenant_id
            )
            new_identity_group_id = pool_details.get('identityGroupId')
            if new_identity_group_id != source_pool.identity_group_id:
                source_pool.identity_group_id = new_identity_group_id
                updated.append({
                    "pool_id": source_pool.pool_id,
                    "pool_name": source_pool.pool_name,
                    "identity_group_id": new_identity_group_id,
                    "type": "source"
                })
                logger.info(f"Updated source pool {source_pool.pool_name} identity_group_id to {new_identity_group_id}")
        except Exception as e:
            errors.append({
                "pool_id": source_pool.pool_id,
                "pool_name": source_pool.pool_name,
                "error": str(e)
            })
            logger.warning(f"Failed to fetch identity group for pool {source_pool.pool_id}: {e}")

    db.commit()

    return {
        "status": "success",
        "updated_count": len(updated),
        "updated": updated,
        "errors": errors
    }


# ========== Sync Operations ==========

@router.post("/{orchestrator_id}/sync")
async def trigger_manual_sync(
    orchestrator_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually trigger a full sync."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    logger.info(f"Manual sync triggered for orchestrator {orchestrator_id} by user {current_user.email}")

    # Run sync in background
    async def run_sync():
        try:
            logger.info(f"Starting manual sync background task for orchestrator {orchestrator_id}")
            async with SyncEngine(orchestrator_id) as engine:
                result = await engine.full_sync(event_type="manual")
            logger.info(f"Manual sync completed for orchestrator {orchestrator_id}: added={result.added}, updated={result.updated}, flagged={result.flagged}")
        except Exception as e:
            logger.error(f"Manual sync failed for orchestrator {orchestrator_id}: {e}")

    background_tasks.add_task(run_sync)

    return {"status": "sync_started", "orchestrator_id": orchestrator_id}


@router.get("/{orchestrator_id}/history", response_model=List[SyncEventResponse])
async def get_sync_history(
    orchestrator_id: int,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get sync event history for an orchestrator."""
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    events = db.query(OrchestratorSyncEvent).filter_by(
        orchestrator_id=orchestrator_id
    ).order_by(OrchestratorSyncEvent.started_at.desc()).limit(limit).all()

    return [
        SyncEventResponse(
            id=e.id,
            event_type=e.event_type,
            status=e.status,
            added_count=e.added_count,
            updated_count=e.updated_count,
            flagged_for_removal=e.flagged_for_removal,
            orphans_found=e.orphans_found,
            errors=e.errors or [],
            started_at=e.started_at,
            completed_at=e.completed_at
        )
        for e in events
    ]


# ========== Flagged/Orphan Management ==========

@router.get("/{orchestrator_id}/flagged", response_model=List[PassphraseMappingResponse])
async def get_flagged_passphrases(
    orchestrator_id: int,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get passphrases flagged for removal, orphans, or with missing targets.

    Statuses:
    - flagged_removal: Source passphrase was deleted, awaiting decision
    - orphan: Exists in site-wide but not synced from any source pool
    - target_missing: We had a mapping but target was deleted externally
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    query = db.query(PassphraseMapping).filter_by(orchestrator_id=orchestrator_id)

    if status:
        query = query.filter_by(sync_status=status)
    else:
        # Default: show flagged, orphans, and target_missing
        query = query.filter(PassphraseMapping.sync_status.in_(["flagged_removal", "orphan", "target_missing"]))

    mappings = query.order_by(PassphraseMapping.flagged_at.desc()).all()

    return [
        PassphraseMappingResponse(
            id=m.id,
            source_pool_id=m.source_pool_id,
            source_pool_name=m.source_pool_name,
            source_passphrase_id=m.source_passphrase_id,
            source_username=m.source_username,
            target_passphrase_id=m.target_passphrase_id,
            sync_status=m.sync_status,
            vlan_id=m.vlan_id,
            passphrase_preview=m.passphrase_preview,
            suggested_source_pool_id=m.suggested_source_pool_id,
            created_at=m.created_at,
            last_synced_at=m.last_synced_at,
            flagged_at=m.flagged_at
        )
        for m in mappings
    ]


@router.post("/{orchestrator_id}/flagged/{mapping_id}/resolve")
async def resolve_flagged_passphrase(
    orchestrator_id: int,
    mapping_id: int,
    action: str,  # "delete", "keep", "ignore", "resync"
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Resolve a flagged passphrase.

    Actions:
    - "delete": Delete the passphrase from site-wide pool (or just remove mapping if target_missing)
    - "keep": Keep in site-wide, remove from tracking
    - "ignore": Mark as ignored (won't show in flagged list)
    - "resync": Re-create the passphrase in site-wide from source (only for target_missing)
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    mapping = db.query(PassphraseMapping).filter_by(
        id=mapping_id,
        orchestrator_id=orchestrator_id
    ).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    if action == "delete":
        # For target_missing, target already doesn't exist - just remove mapping
        if mapping.sync_status == "target_missing":
            db.delete(mapping)
            db.commit()
            return {"status": "deleted", "mapping_id": mapping_id, "note": "Target was already missing"}

        # Delete from site-wide pool via API
        try:
            async with SyncEngine(orchestrator_id, db) as engine:
                await engine._rate_limited(
                    engine.r1_client.dpsk.delete_passphrase(
                        passphrase_id=mapping.target_passphrase_id,
                        pool_id=orchestrator.site_wide_pool_id,
                        tenant_id=orchestrator.tenant_id
                    )
                )
        except Exception as e:
            logger.error(f"Failed to delete passphrase: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")

        # Remove mapping
        db.delete(mapping)
        db.commit()
        return {"status": "deleted", "mapping_id": mapping_id}

    elif action == "keep":
        # Remove from tracking but keep in site-wide
        db.delete(mapping)
        db.commit()
        return {"status": "kept", "mapping_id": mapping_id}

    elif action == "ignore":
        # Mark as ignored
        mapping.sync_status = "ignored"
        db.commit()
        return {"status": "ignored", "mapping_id": mapping_id}

    elif action == "resync":
        # Re-create the passphrase in site-wide from source (only for target_missing)
        if mapping.sync_status != "target_missing":
            raise HTTPException(
                status_code=400,
                detail="Resync is only available for target_missing items"
            )

        if not mapping.source_passphrase_id:
            raise HTTPException(
                status_code=400,
                detail="No source passphrase ID available for resync"
            )

        try:
            async with SyncEngine(orchestrator_id, db) as engine:
                # Fetch source passphrase
                source_pp = await engine._rate_limited(
                    engine.r1_client.dpsk.get_passphrase(
                        pool_id=mapping.source_pool_id,
                        passphrase_id=mapping.source_passphrase_id,
                        tenant_id=orchestrator.tenant_id
                    )
                )

                # Re-create in site-wide pool
                errors = []
                source_pp['_source_pool_id'] = mapping.source_pool_id
                source_pp['_source_pool_name'] = mapping.source_pool_name
                if await engine._sync_passphrase_add(source_pp, errors):
                    # Remove old stale mapping (new one was created by _sync_passphrase_add)
                    db.delete(mapping)
                    db.commit()
                    return {"status": "resynced", "mapping_id": mapping_id}
                else:
                    raise HTTPException(status_code=500, detail=f"Resync failed: {errors}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to resync passphrase: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to resync: {e}")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@router.post("/{orchestrator_id}/orphans/{mapping_id}/copy-to-source")
async def copy_orphan_to_source(
    orchestrator_id: int,
    mapping_id: int,
    request: CopyToSourceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Copy an orphan passphrase from site-wide to a per-unit source pool.

    This establishes it as a proper source → site-wide sync relationship.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    mapping = db.query(PassphraseMapping).filter_by(
        id=mapping_id,
        orchestrator_id=orchestrator_id,
        sync_status="orphan"
    ).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Orphan mapping not found")

    try:
        async with SyncEngine(orchestrator_id, db) as engine:
            # 1. Get the orphan passphrase from site-wide
            site_pp = await engine._rate_limited(
                engine.r1_client.dpsk.get_passphrase(
                    pool_id=orchestrator.site_wide_pool_id,
                    passphrase_id=mapping.target_passphrase_id,
                    tenant_id=orchestrator.tenant_id
                )
            )

            # 2. Create passphrase in the target source pool
            source_pp = await engine._rate_limited(
                engine.r1_client.dpsk.create_passphrase(
                    pool_id=request.target_pool_id,
                    tenant_id=orchestrator.tenant_id,
                    user_name=site_pp.get('userName'),
                    user_email=site_pp.get('userEmail'),
                    passphrase=site_pp.get('passphrase'),
                    vlan_id=site_pp.get('vlanId'),
                    max_devices=site_pp.get('maxDevices', 5)
                )
            )

            # 3. Update mapping to proper sync relationship
            # Find the source pool name
            source_pool = db.query(OrchestratorSourcePool).filter_by(
                orchestrator_id=orchestrator_id,
                pool_id=request.target_pool_id
            ).first()

            mapping.source_pool_id = request.target_pool_id
            mapping.source_pool_name = source_pool.pool_name if source_pool else None
            mapping.source_passphrase_id = source_pp.get('id')
            mapping.sync_status = "synced"
            mapping.last_synced_at = datetime.utcnow()
            db.commit()

            return {
                "status": "success",
                "source_passphrase_id": source_pp.get('id'),
                "target_pool_id": request.target_pool_id
            }

    except Exception as e:
        logger.error(f"Failed to copy orphan to source: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to copy: {e}")


# ========== Webhook Configuration ==========

@router.post("/{orchestrator_id}/webhook/generate-secret")
async def generate_webhook_secret(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Generate or regenerate a webhook secret for signature verification.

    Returns the newly generated secret. Store this securely as it cannot
    be retrieved again - only regenerated.
    """
    import secrets

    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Generate a new 32-character secret
    new_secret = secrets.token_hex(16)
    orchestrator.webhook_secret = new_secret
    db.commit()

    logger.info(f"Generated new webhook secret for orchestrator {orchestrator_id}")

    return {
        "status": "success",
        "webhook_secret": new_secret,
        "message": "Store this secret securely. It cannot be retrieved again."
    }


@router.post("/{orchestrator_id}/discover-pools")
async def discover_pools(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Manually trigger pool discovery using the orchestrator's include/exclude patterns.

    This is a one-time operation to find matching pools. New pools are added as source pools.
    Use this during initial setup or when new units are added.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        async with SyncEngine(orchestrator_id, db) as engine:
            result = await engine.auto_discover_source_pools()

        return {
            "status": "success",
            "pools_scanned": result.pools_scanned,
            "pools_discovered": result.pools_discovered,
            "errors": result.errors
        }
    except Exception as e:
        logger.error(f"Pool discovery failed: {e}")
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")


@router.delete("/{orchestrator_id}/webhook/secret")
async def clear_webhook_secret(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Clear the webhook secret, disabling signature verification.

    After clearing, webhook requests will be accepted without signature validation.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    # Check access using RBAC
    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    orchestrator.webhook_secret = None
    db.commit()

    logger.info(f"Cleared webhook secret for orchestrator {orchestrator_id}")

    return {"status": "success", "message": "Webhook secret cleared"}


# ========== Identity Audit ==========

class OrphanIdentityResponse(BaseModel):
    """An identity in an identity group with no passphrases"""
    id: str
    name: Optional[str]
    display_name: Optional[str]
    description: Optional[str]
    vlan: Optional[int]
    created_at: Optional[str]
    # Which other pools this identity exists in (helps identify origin)
    also_exists_in: List[str] = []  # List of pool names where this identity also exists

    class Config:
        from_attributes = True


class PoolIdentityAuditResult(BaseModel):
    """Identity audit results for a single pool"""
    pool_id: str
    pool_name: Optional[str]
    pool_type: str  # "site_wide" or "source"
    identity_group_id: Optional[str]
    identity_group_name: Optional[str]
    total_identities: int
    total_passphrases: int
    orphan_identities: int
    orphans: List[OrphanIdentityResponse]


class IdentityAuditResponse(BaseModel):
    """Results of an identity audit for an orchestrator - covering ALL pools"""
    orchestrator_id: int
    # Summary totals across all pools
    total_pools_audited: int
    total_identities: int
    total_passphrases: int
    total_orphan_identities: int
    # Per-pool breakdown
    site_wide_audit: Optional[PoolIdentityAuditResult]
    source_pool_audits: List[PoolIdentityAuditResult] = []
    # Legacy fields for backwards compatibility
    identity_group_id: Optional[str] = None  # Site-wide identity group
    identity_group_name: Optional[str] = None
    orphan_identities: int = 0  # Total orphans
    orphans: List[OrphanIdentityResponse] = []  # All orphans combined


@router.get("/{orchestrator_id}/identity-audit", response_model=IdentityAuditResponse)
async def audit_identities(
    orchestrator_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Audit identities across ALL pools (site-wide + source pools).

    Returns:
    - Per-pool breakdown of identities vs passphrases
    - Orphan identities: Identities with 0 passphrases (should be cleaned up)
    - Cross-pool existence info: Whether orphan identities exist in other pools

    This helps identify where orphan identities came from and clean them up.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    r1_client = get_dynamic_r1_client(controller.id, current_user, db)

    # Helper function to audit a single pool
    async def audit_pool(pool_id: str, pool_name: str, identity_group_id: str, pool_type: str) -> PoolIdentityAuditResult:
        """Audit identities vs passphrases for a single pool"""
        logger.info(f"audit_pool starting: pool_id={pool_id}, pool_name={pool_name}, identity_group_id={identity_group_id}")
        identities = []
        passphrases = []
        group_name = None

        # Fetch all identities in this pool's identity group
        if identity_group_id:
            try:
                logger.info(f"Fetching identities for group {identity_group_id}...")
                page = 0
                while True:
                    result = await r1_client.identity.get_identities_in_group(
                        group_id=identity_group_id,
                        tenant_id=orchestrator.tenant_id,
                        page=page,
                        size=500
                    )
                    items = result.get('data', result.get('content', []))
                    logger.info(f"  Page {page}: got {len(items)} identities")
                    if not items:
                        break
                    identities.extend(items)
                    total_pages = result.get('totalPages', 1)
                    if page >= total_pages - 1:
                        break
                    page += 1
                logger.info(f"Total identities fetched: {len(identities)}")

                # Get group name
                try:
                    group_details = await r1_client.identity.get_identity_group(
                        group_id=identity_group_id,
                        tenant_id=orchestrator.tenant_id
                    )
                    group_name = group_details.get('name')
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Failed to get identities for pool {pool_name}: {e}")

        # Fetch all passphrases in this pool
        try:
            logger.info(f"Fetching passphrases for pool {pool_id}...")
            page = 1
            while True:
                result = await r1_client.dpsk.query_passphrases(
                    pool_id=pool_id,
                    tenant_id=orchestrator.tenant_id,
                    page=page,
                    limit=500
                )
                items = result.get('data', [])
                logger.info(f"  Page {page}: got {len(items)} passphrases")
                if not items:
                    break
                passphrases.extend(items)
                total = result.get('totalCount', len(items))
                if len(passphrases) >= total:
                    break
                page += 1
            logger.info(f"Total passphrases fetched: {len(passphrases)}")
        except Exception as e:
            logger.warning(f"Failed to get passphrases for pool {pool_name}: {e}")

        # Find identities with passphrases
        identity_ids_with_pp = {pp.get('identityId') for pp in passphrases if pp.get('identityId')}

        # Find orphans (identities without passphrases)
        orphans = []
        for identity in identities:
            identity_id = identity.get('id')
            if identity_id and identity_id not in identity_ids_with_pp:
                orphans.append(OrphanIdentityResponse(
                    id=identity_id,
                    name=identity.get('name'),
                    display_name=identity.get('displayName'),
                    description=identity.get('description'),
                    vlan=identity.get('vlan'),
                    created_at=identity.get('createdAt'),
                    also_exists_in=[]  # Will be populated later
                ))

        logger.info(f"audit_pool complete: {len(identities)} identities, {len(passphrases)} passphrases, {len(orphans)} orphans")
        return PoolIdentityAuditResult(
            pool_id=pool_id,
            pool_name=pool_name,
            pool_type=pool_type,
            identity_group_id=identity_group_id,
            identity_group_name=group_name,
            total_identities=len(identities),
            total_passphrases=len(passphrases),
            orphan_identities=len(orphans),
            orphans=orphans
        )

    try:
        logger.info(f"Starting identity audit for orchestrator {orchestrator_id}")
        logger.info(f"  - site_wide_pool_id: {orchestrator.site_wide_pool_id}")
        logger.info(f"  - site_wide_identity_group_id: {orchestrator.site_wide_identity_group_id}")
        logger.info(f"  - source_pools count: {len(orchestrator.source_pools)}")

        # Audit site-wide pool
        site_wide_audit = None
        if orchestrator.site_wide_identity_group_id:
            logger.info(f"Auditing site-wide pool...")
            site_wide_audit = await audit_pool(
                pool_id=orchestrator.site_wide_pool_id,
                pool_name=orchestrator.site_wide_pool_name or "Site-Wide Pool",
                identity_group_id=orchestrator.site_wide_identity_group_id,
                pool_type="site_wide"
            )
            logger.info(f"Site-wide audit: {site_wide_audit.total_identities} identities, "
                       f"{site_wide_audit.total_passphrases} passphrases, "
                       f"{site_wide_audit.orphan_identities} orphans")
        else:
            logger.info(f"Skipping site-wide audit - no identity group configured")

        # Audit all source pools
        source_pool_audits = []
        logger.info(f"Auditing {len(orchestrator.source_pools)} source pools...")
        for i, source_pool in enumerate(orchestrator.source_pools):
            logger.info(f"  Source pool {i+1}: {source_pool.pool_name}, identity_group_id={source_pool.identity_group_id}")
            if source_pool.identity_group_id:
                audit = await audit_pool(
                    pool_id=source_pool.pool_id,
                    pool_name=source_pool.pool_name or source_pool.pool_id,
                    identity_group_id=source_pool.identity_group_id,
                    pool_type="source"
                )
                source_pool_audits.append(audit)
                logger.info(f"Source pool '{audit.pool_name}' audit: {audit.total_identities} identities, "
                           f"{audit.total_passphrases} passphrases, {audit.orphan_identities} orphans")
            else:
                logger.info(f"  Skipping source pool '{source_pool.pool_name}' - no identity group")

        # Build a map of identity_id -> pool_names for cross-referencing
        # This helps identify where orphan identities might have come from
        identity_to_pools = {}  # identity_id -> set of pool names
        all_audits = ([site_wide_audit] if site_wide_audit else []) + source_pool_audits

        # First pass: collect all identities and which pools they're in
        for audit in all_audits:
            for orphan in audit.orphans:
                if orphan.id not in identity_to_pools:
                    identity_to_pools[orphan.id] = set()
                identity_to_pools[orphan.id].add(audit.pool_name)

        # Second pass: populate also_exists_in for each orphan
        for audit in all_audits:
            for orphan in audit.orphans:
                # Show other pools where this identity exists (not the current pool)
                other_pools = identity_to_pools.get(orphan.id, set()) - {audit.pool_name}
                orphan.also_exists_in = list(other_pools)

        # Calculate totals
        total_pools = len(all_audits)
        total_identities = sum(a.total_identities for a in all_audits)
        total_passphrases = sum(a.total_passphrases for a in all_audits)
        total_orphans = sum(a.orphan_identities for a in all_audits)

        # Collect all orphans for legacy field
        all_orphans = []
        for audit in all_audits:
            all_orphans.extend(audit.orphans)

        logger.info(f"Identity audit complete. Returning response with {total_pools} pools, "
                   f"{total_identities} identities, {total_passphrases} passphrases, {total_orphans} orphans")

        return IdentityAuditResponse(
            orchestrator_id=orchestrator_id,
            total_pools_audited=total_pools,
            total_identities=total_identities,
            total_passphrases=total_passphrases,
            total_orphan_identities=total_orphans,
            site_wide_audit=site_wide_audit,
            source_pool_audits=source_pool_audits,
            # Legacy fields
            identity_group_id=orchestrator.site_wide_identity_group_id,
            identity_group_name=site_wide_audit.identity_group_name if site_wide_audit else None,
            orphan_identities=total_orphans,
            orphans=all_orphans
        )

    except Exception as e:
        logger.error(f"Identity audit failed: {e}")
        raise HTTPException(status_code=500, detail=f"Identity audit failed: {e}")


@router.delete("/{orchestrator_id}/identity-audit/{identity_id}")
async def delete_orphan_identity(
    orchestrator_id: int,
    identity_id: str,
    pool_id: Optional[str] = None,  # If provided, delete from this pool's identity group
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete an orphan identity from a pool's identity group.

    Args:
        orchestrator_id: The orchestrator ID
        identity_id: The identity ID to delete
        pool_id: Optional pool ID. If provided, deletes from that pool's identity group.
                 If not provided, deletes from site-wide identity group.

    Only use this for identities confirmed to be orphans (no passphrases).
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine which identity group to delete from
    identity_group_id = None
    pool_name = "site-wide"

    if pool_id:
        # Find the source pool
        source_pool = None
        for sp in orchestrator.source_pools:
            if sp.pool_id == pool_id:
                source_pool = sp
                break

        if not source_pool:
            raise HTTPException(status_code=404, detail=f"Source pool {pool_id} not found")

        if not source_pool.identity_group_id:
            raise HTTPException(status_code=400, detail=f"Source pool {source_pool.pool_name} has no identity group")

        identity_group_id = source_pool.identity_group_id
        pool_name = source_pool.pool_name or pool_id
    else:
        # Default to site-wide
        if not orchestrator.site_wide_identity_group_id:
            raise HTTPException(status_code=400, detail="No site-wide identity group configured")
        identity_group_id = orchestrator.site_wide_identity_group_id

    controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    try:
        r1_client = get_dynamic_r1_client(controller.id, current_user, db)

        await r1_client.identity.delete_identity(
            group_id=identity_group_id,
            identity_id=identity_id,
            tenant_id=orchestrator.tenant_id
        )

        logger.info(f"Deleted orphan identity {identity_id} from pool '{pool_name}' (orchestrator {orchestrator_id})")

        return {"status": "deleted", "identity_id": identity_id, "pool_name": pool_name}

    except Exception as e:
        logger.error(f"Failed to delete orphan identity: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete identity: {e}")


class BulkDeleteOrphansRequest(BaseModel):
    """Request to bulk delete orphan identities"""
    identity_ids: List[str] = Field(..., description="List of identity IDs to delete")
    pool_id: Optional[str] = Field(None, description="Pool ID (if None, uses site-wide)")


class BulkDeleteResult(BaseModel):
    """Result of bulk delete operation"""
    deleted: int
    failed: int
    errors: List[str] = []


@router.post("/{orchestrator_id}/identity-audit/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete_orphan_identities(
    orchestrator_id: int,
    request: BulkDeleteOrphansRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Bulk delete orphan identities from a pool's identity group.

    This is useful for cleaning up multiple orphan identities at once.
    """
    orchestrator = db.query(DPSKOrchestrator).filter_by(id=orchestrator_id).first()
    if not orchestrator:
        raise HTTPException(status_code=404, detail="Orchestrator not found")

    if not can_access_orchestrator(db, current_user, orchestrator):
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine which identity group to delete from
    identity_group_id = None
    pool_name = "site-wide"

    if request.pool_id:
        source_pool = None
        for sp in orchestrator.source_pools:
            if sp.pool_id == request.pool_id:
                source_pool = sp
                break

        if not source_pool:
            raise HTTPException(status_code=404, detail=f"Source pool {request.pool_id} not found")

        if not source_pool.identity_group_id:
            raise HTTPException(status_code=400, detail=f"Source pool {source_pool.pool_name} has no identity group")

        identity_group_id = source_pool.identity_group_id
        pool_name = source_pool.pool_name or request.pool_id
    else:
        if not orchestrator.site_wide_identity_group_id:
            raise HTTPException(status_code=400, detail="No site-wide identity group configured")
        identity_group_id = orchestrator.site_wide_identity_group_id

    controller = db.query(Controller).filter_by(id=orchestrator.controller_id).first()
    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found")

    r1_client = get_dynamic_r1_client(controller.id, current_user, db)

    deleted = 0
    failed = 0
    errors = []

    for identity_id in request.identity_ids:
        try:
            await r1_client.identity.delete_identity(
                group_id=identity_group_id,
                identity_id=identity_id,
                tenant_id=orchestrator.tenant_id
            )
            deleted += 1
            logger.info(f"Deleted orphan identity {identity_id} from pool '{pool_name}'")
        except Exception as e:
            failed += 1
            error_msg = f"Failed to delete {identity_id}: {str(e)}"
            errors.append(error_msg)
            logger.warning(error_msg)

    logger.info(f"Bulk delete complete for orchestrator {orchestrator_id}, pool '{pool_name}': {deleted} deleted, {failed} failed")

    return BulkDeleteResult(deleted=deleted, failed=failed, errors=errors)
