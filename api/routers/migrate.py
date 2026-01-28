"""
Migration API Router

Handles migrations between controllers:
- SmartZone → RuckusONE
- RuckusONE → RuckusONE
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Body

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from szapi.client import SZClient
from clients.sz_client_deps import create_sz_client_from_controller
from clients.r1_client import create_r1_client_from_controller
from r1api.client import R1Client

router = APIRouter(
    prefix="/migrate",
)


class LicenseCheckRequest(BaseModel):
    """Request to check license availability"""
    controller_id: int = Field(..., description="RuckusONE controller ID")
    tenant_id: Optional[str] = Field(None, description="Tenant/EC ID (required for MSP)")
    ap_count: int = Field(..., description="Number of APs to check licenses for")


class LicenseCheckResponse(BaseModel):
    """License availability response"""
    available: int
    required: int
    sufficient: bool
    remaining: int
    message: str
    total: int = 0  # Total licenses allocated
    used: int = 0   # Currently used licenses


@router.post("/check-license", response_model=LicenseCheckResponse)
async def check_license_availability(
    request: LicenseCheckRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Check license availability before migration

    This endpoint checks if there are sufficient AP licenses available
    for the planned migration without actually performing the migration.

    Args:
        request: License check request with controller ID, tenant ID, and AP count

    Returns:
        License availability information
    """
    logger.info(f"License check request - controller_id: {request.controller_id}, tenant_id: {request.tenant_id}, ap_count: {request.ap_count}")

    # Validate access to controller
    controller = validate_controller_access(request.controller_id, current_user, db)
    logger.info(f"✅ Controller validated - type: {controller.controller_type}, subtype: {controller.controller_subtype}")

    # Verify it's a RuckusONE controller
    if controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Controller must be RuckusONE, got {controller.controller_type}"
        )

    try:
        # Create R1 client
        logger.info(f"🔧 Creating R1 client for controller {controller.id}")
        r1_client = create_r1_client_from_controller(controller.id, db)
        logger.info(f"✅ R1 client created - ec_type: {r1_client.ec_type}, tenant_id: {r1_client.tenant_id}")

        # Determine tenant_id
        tenant_id = request.tenant_id or controller.r1_tenant_id
        logger.info(f"🎯 Effective tenant_id: {tenant_id}")

        # Validate tenant_id for MSP controllers
        if controller.controller_subtype == "MSP" and not tenant_id:
            raise HTTPException(
                status_code=400,
                detail="tenant_id is required for MSP controllers"
            )

        # Check license availability
        logger.info(f"📊 Checking license availability via entitlements service...")
        license_data = await r1_client.entitlements.get_available_ap_licenses(
            tenant_id=tenant_id
        )
        logger.info(f"✅ License check result: {license_data}")

        available_licenses = license_data['available']
        total_licenses = license_data['total']
        used_licenses = license_data['used']

        required_licenses = request.ap_count
        sufficient = available_licenses >= required_licenses
        remaining = available_licenses - required_licenses if sufficient else 0

        # Build message
        if sufficient:
            if remaining == 0:
                message = f"Exactly enough licenses available for {required_licenses} APs"
            else:
                message = f"Sufficient licenses available. {remaining} will remain after migration"
        else:
            shortage = required_licenses - available_licenses
            message = f"Insufficient licenses. Need {shortage} more AP licenses"

        logger.info(f"📝 Response: available={available_licenses}, required={required_licenses}, sufficient={sufficient}")

        return LicenseCheckResponse(
            available=available_licenses,
            required=required_licenses,
            sufficient=sufficient,
            remaining=remaining if sufficient else available_licenses,
            message=message,
            total=total_licenses,
            used=used_licenses
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ License check failed with exception: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"License check failed: {str(e)}"
        )


class APMigrationItem(BaseModel):
    """Single AP to migrate"""
    serial: str = Field(..., description="AP serial number")
    name: Optional[str] = Field(None, description="AP name (auto-generated if not provided)")
    description: Optional[str] = Field(None, description="AP description")
    mac: Optional[str] = Field(None, description="AP MAC address")
    model: Optional[str] = Field(None, description="AP model")
    latitude: Optional[float] = Field(None, description="GPS latitude")
    longitude: Optional[float] = Field(None, description="GPS longitude")


class SwitchMigrationItem(BaseModel):
    """Single Switch to migrate"""
    serial: str = Field(..., description="Switch serial number")
    name: Optional[str] = Field(None, description="Switch name (auto-generated if not provided)")
    description: Optional[str] = Field(None, description="Switch description")
    mac: Optional[str] = Field(None, description="Switch MAC address")
    model: Optional[str] = Field(None, description="Switch model")
    switchGroupId: Optional[str] = Field(None, description="SmartZone Switch Group ID")
    switchGroupName: Optional[str] = Field(None, description="SmartZone Switch Group Name")


class SZToR1MigrationRequest(BaseModel):
    """Request payload for SmartZone to RuckusONE migration"""
    source_controller_id: int = Field(..., description="Source SmartZone controller ID")
    dest_controller_id: int = Field(..., description="Destination RuckusONE controller ID")
    dest_tenant_id: Optional[str] = Field(None, description="Destination tenant/EC ID (required for MSP)")
    dest_venue_id: str = Field(..., description="Destination venue ID in R1")
    dest_ap_group: Optional[str] = Field(None, description="AP Group name in R1")
    aps: List[APMigrationItem] = Field(default_factory=list, description="List of APs to migrate")
    switches: List[SwitchMigrationItem] = Field(default_factory=list, description="List of Switches to migrate")


class R1ToR1MigrationRequest(BaseModel):
    """Request payload for RuckusONE to RuckusONE migration"""
    source_controller_id: int = Field(..., description="Source R1 controller ID")
    dest_controller_id: int = Field(..., description="Destination R1 controller ID")
    source_tenant_id: str = Field(..., description="Source tenant/EC ID")
    dest_tenant_id: str = Field(..., description="Destination tenant/EC ID")
    dest_venue_id: str = Field(..., description="Destination venue ID")
    ap_serials: List[str] = Field(..., description="List of AP serial numbers to migrate")


class MigrationResponse(BaseModel):
    """Migration operation response"""
    status: str
    message: str
    migrated_count: int
    failed_count: int
    details: Optional[List[Dict[str, Any]]] = None
    license_info: Optional[Dict[str, Any]] = None


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


@router.post("/sz-to-r1", response_model=MigrationResponse)
async def migrate_sz_to_r1(
    request: SZToR1MigrationRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Migrate APs and/or Switches from SmartZone to RuckusONE

    This endpoint:
    1. Validates access to both controllers
    2. Fetches device details from SmartZone (if needed)
    3. Formats device data for R1 import
    4. Imports APs and Switches into R1 venue
    5. Returns migration results

    Args:
        request: Migration request with source/dest controllers and device lists

    Returns:
        Migration results with success/failure counts
    """
    # Validate access to both controllers
    source_controller = validate_controller_access(request.source_controller_id, current_user, db)
    dest_controller = validate_controller_access(request.dest_controller_id, current_user, db)

    # Verify controller types
    if source_controller.controller_type != "SmartZone":
        raise HTTPException(
            status_code=400,
            detail=f"Source controller must be SmartZone, got {source_controller.controller_type}"
        )

    if dest_controller.controller_type != "RuckusONE":
        raise HTTPException(
            status_code=400,
            detail=f"Destination controller must be RuckusONE, got {dest_controller.controller_type}"
        )

    migrated_count = 0
    failed_count = 0
    details = []

    try:
        # Create SmartZone client (only needed if we want to fetch additional details)
        logger.info(f"Creating SZ client for controller {source_controller.id}")
        sz_client = create_sz_client_from_controller(source_controller.id, db)

        # Create R1 client for destination
        logger.info(f"Creating R1 client for controller {dest_controller.id}")
        r1_client = create_r1_client_from_controller(dest_controller.id, db)
        logger.info(f"R1 client created successfully")

        # Determine tenant_id for destination
        # Use provided tenant_id from request, or fall back to controller's tenant_id
        dest_tenant_id = request.dest_tenant_id or dest_controller.r1_tenant_id

        # Validate tenant_id for MSP controllers
        if dest_controller.controller_subtype == "MSP" and not dest_tenant_id:
            raise HTTPException(
                status_code=400,
                detail="dest_tenant_id is required for MSP controllers"
            )

        # Check license availability before starting migration
        # Total devices = APs + Switches (both consume licenses)
        total_devices = len(request.aps) + len(request.switches)

        license_info = {}
        try:
            logger.info(f"Checking license availability for tenant {dest_tenant_id}")
            license_data = await r1_client.entitlements.get_available_ap_licenses(
                tenant_id=dest_tenant_id
            )
            logger.info(f"License data received: {license_data}")
            available_licenses = license_data['available']

            required_licenses = total_devices

            license_info = {
                "available": available_licenses,
                "required": required_licenses,
                "sufficient": available_licenses >= required_licenses
            }

            if available_licenses < required_licenses:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient device licenses. Required: {required_licenses} ({len(request.aps)} APs + {len(request.switches)} Switches), Available: {available_licenses}. "
                           f"Please purchase additional licenses or reduce the number of devices to migrate."
                )

            logger.info(f"License check passed: {available_licenses} available, {required_licenses} required ({len(request.aps)} APs + {len(request.switches)} Switches)")

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            logger.warning(f"License check failed: {str(e)} - proceeding with migration anyway")
            license_info = {
                "available": "unknown",
                "required": total_devices,
                "error": str(e)
            }
            # Don't fail the migration if license check fails - just warn
            # The actual device creation will fail if there are no licenses

        logger.info(f"Entering SZ client context...")
        async with sz_client:
            logger.info(f"SZ client context entered successfully")
            # Bulk import APs if there are any
            if request.aps:
                # Build list of APs for bulk import
                bulk_aps = []
                for idx, ap in enumerate(request.aps, start=1):
                    # Use provided name, or generate unique name like "AP1", "AP2", etc.
                    ap_name = ap.name if ap.name else f"AP{idx}"
                    bulk_ap = {
                        'name': ap_name,
                        'serial': ap.serial,
                        'description': ap.description or '',
                        'ap_group': request.dest_ap_group or '',
                        'tags': '',  # Tags handled separately from AP Group
                        'latitude': str(ap.latitude) if ap.latitude else '',
                        'longitude': str(ap.longitude) if ap.longitude else '',
                        'vlan': ''  # Global untagged VLAN - not used in migration
                    }
                    bulk_aps.append(bulk_ap)

                try:
                    logger.info(f"Bulk importing {len(bulk_aps)} APs to venue {request.dest_venue_id}")
                    result = await r1_client.venues.bulk_add_aps_to_venue(
                        venue_id=request.dest_venue_id,
                        aps=bulk_aps,
                        tenant_id=dest_tenant_id,
                        wait_for_completion=True
                    )

                    logger.info(f"Bulk import result: {result}")

                    # Parse bulk import results
                    # R1 returns: {"requestId": "...", "success": N, "failed": N, "errors": [...]}
                    bulk_success = result.get('success', 0)
                    bulk_failed = result.get('failed', 0)
                    bulk_errors = result.get('errors', [])

                    # If we got a simple success response without counts, assume all succeeded
                    if bulk_success == 0 and bulk_failed == 0 and not bulk_errors:
                        bulk_success = len(bulk_aps)

                    migrated_count += bulk_success
                    failed_count += bulk_failed

                    # Add success details for migrated APs
                    for i, ap in enumerate(request.aps):
                        if i < bulk_success:
                            details.append({
                                "serial": ap.serial,
                                "status": "success",
                                "message": "AP successfully added to venue (bulk import)"
                            })

                    # Add failure details from errors
                    for error in bulk_errors:
                        serial = error.get('serialNumber', error.get('serial', 'unknown'))
                        error_msg = error.get('message', error.get('error', 'Unknown error'))
                        details.append({
                            "serial": serial,
                            "status": "failed",
                            "message": error_msg
                        })

                except Exception as e:
                    # If bulk import fails entirely, mark all APs as failed
                    error_msg = str(e)
                    logger.error(f"Bulk import failed: {error_msg}")
                    failed_count += len(request.aps)
                    for ap in request.aps:
                        details.append({
                            "serial": ap.serial,
                            "status": "failed",
                            "message": f"Bulk import failed: {error_msg}"
                        })

            # Bulk import switches if there are any
            if request.switches:
                # Build list of switches for bulk import
                bulk_switches = []
                for idx, switch in enumerate(request.switches, start=1):
                    # Use provided name, or generate unique name like "Switch1", "Switch2", etc.
                    switch_name = switch.name if switch.name else f"Switch{idx}"
                    bulk_switch = {
                        'name': switch_name,
                        'serial': switch.serial,
                        'reason': ''  # Optional reason field - leave blank for migration
                    }
                    bulk_switches.append(bulk_switch)

                try:
                    logger.info(f"Bulk importing {len(bulk_switches)} switches to venue {request.dest_venue_id}")
                    result = await r1_client.venues.bulk_add_switches_to_venue(
                        venue_id=request.dest_venue_id,
                        switches=bulk_switches,
                        tenant_id=dest_tenant_id,
                        wait_for_completion=True
                    )

                    logger.info(f"Bulk switch import result: {result}")

                    # Parse bulk import results
                    bulk_success = result.get('success', 0)
                    bulk_failed = result.get('failed', 0)
                    bulk_errors = result.get('errors', [])

                    # If we got a simple success response without counts, assume all succeeded
                    if bulk_success == 0 and bulk_failed == 0 and not bulk_errors:
                        bulk_success = len(bulk_switches)

                    migrated_count += bulk_success
                    failed_count += bulk_failed

                    # Add success details for migrated switches
                    for i, switch in enumerate(request.switches):
                        if i < bulk_success:
                            details.append({
                                "serial": switch.serial,
                                "device_type": "switch",
                                "status": "success",
                                "message": "Switch successfully added to venue (bulk import)"
                            })

                    # Add failure details from errors
                    for error in bulk_errors:
                        serial = error.get('serialNumber', error.get('serial', 'unknown'))
                        error_msg = error.get('message', error.get('error', 'Unknown error'))
                        details.append({
                            "serial": serial,
                            "device_type": "switch",
                            "status": "failed",
                            "message": error_msg
                        })

                except Exception as e:
                    # If bulk import fails entirely, mark all switches as failed
                    error_msg = str(e)
                    logger.error(f"Bulk switch import failed: {error_msg}")
                    failed_count += len(request.switches)
                    for switch in request.switches:
                        details.append({
                            "serial": switch.serial,
                            "device_type": "switch",
                            "status": "failed",
                            "message": f"Bulk import failed: {error_msg}"
                        })

        return MigrationResponse(
            status="completed" if failed_count == 0 else "partial",
            message=f"Migration completed: {migrated_count} successful, {failed_count} failed",
            migrated_count=migrated_count,
            failed_count=failed_count,
            details=details,
            license_info=license_info
        )

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"Migration failed with unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


@router.post("/r1-to-r1", response_model=MigrationResponse)
async def migrate_r1_to_r1(
    request: R1ToR1MigrationRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Migrate APs from one RuckusONE controller/tenant to another

    This is for R1→R1 migrations within same or different controllers.

    Args:
        request: Migration request with source/dest info and AP list

    Returns:
        Migration results
    """
    # Validate access to both controllers
    source_controller = validate_controller_access(request.source_controller_id, current_user, db)
    dest_controller = validate_controller_access(request.dest_controller_id, current_user, db)

    # Verify both are RuckusONE
    if source_controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Source must be RuckusONE controller")

    if dest_controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Destination must be RuckusONE controller")

    # TODO: Implement R1→R1 migration logic
    # This would involve:
    # 1. Fetch AP details from source R1
    # 2. Export AP configuration
    # 3. Import to destination R1
    # 4. Optionally remove from source

    raise HTTPException(
        status_code=501,
        detail="R1→R1 migration not yet implemented. Use existing Migrate page for now."
    )
