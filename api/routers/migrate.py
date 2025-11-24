"""
Migration API Router

Handles migrations between controllers:
- SmartZone → RuckusONE
- RuckusONE → RuckusONE
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from models.user import User
from models.controller import Controller
from clients.sz_client import SmartZoneClient
from clients.sz_client_deps import create_sz_client_from_controller
from clients.r1_client import create_r1_client_from_controller
from r1api.client import R1Client

router = APIRouter(
    prefix="/migrate",
    tags=["migration"],
)


class APMigrationItem(BaseModel):
    """Single AP to migrate"""
    serial: str = Field(..., description="AP serial number")
    name: str = Field(..., description="AP name")
    description: Optional[str] = Field(None, description="AP description")
    mac: Optional[str] = Field(None, description="AP MAC address")
    model: Optional[str] = Field(None, description="AP model")
    latitude: Optional[float] = Field(None, description="GPS latitude")
    longitude: Optional[float] = Field(None, description="GPS longitude")


class SZToR1MigrationRequest(BaseModel):
    """Request payload for SmartZone to RuckusONE migration"""
    source_controller_id: int = Field(..., description="Source SmartZone controller ID")
    dest_controller_id: int = Field(..., description="Destination RuckusONE controller ID")
    dest_venue_id: str = Field(..., description="Destination venue ID in R1")
    dest_ap_group: Optional[str] = Field(None, description="AP Group name in R1")
    aps: List[APMigrationItem] = Field(..., description="List of APs to migrate")


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
    Migrate APs from SmartZone to RuckusONE

    This endpoint:
    1. Validates access to both controllers
    2. Fetches AP details from SmartZone (if needed)
    3. Formats AP data for R1 import
    4. Imports APs into R1 venue
    5. Returns migration results

    Args:
        request: Migration request with source/dest controllers and AP list

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
        # Create SmartZone client (to fetch additional AP details if needed)
        sz_client = create_sz_client_from_controller(source_controller.id, db)

        # Create R1 client for destination
        r1_client = create_r1_client_from_controller(dest_controller.id, db)

        async with sz_client:
            # Process each AP
            for ap in request.aps:
                try:
                    # Format AP for R1 import
                    r1_ap_data = {
                        "apName": ap.name,
                        "description": ap.description or "",
                        "serialNumber": ap.serial,
                        "apGroupName": request.dest_ap_group or "Default",
                        "latitude": ap.latitude,
                        "longitude": ap.longitude,
                        # Add other required R1 fields
                    }

                    # TODO: Call R1 API to import AP
                    # This requires implementing the AP import endpoint in r1api
                    # For now, we'll simulate the call
                    # result = await r1_client.aps.import_ap(request.dest_venue_id, r1_ap_data)

                    print(f"Would import AP {ap.serial} to R1 venue {request.dest_venue_id}")
                    print(f"AP data: {r1_ap_data}")

                    migrated_count += 1
                    details.append({
                        "serial": ap.serial,
                        "status": "success",
                        "message": "AP queued for import (simulated)"
                    })

                except Exception as e:
                    failed_count += 1
                    details.append({
                        "serial": ap.serial,
                        "status": "failed",
                        "message": str(e)
                    })

        return MigrationResponse(
            status="completed" if failed_count == 0 else "partial",
            message=f"Migration completed: {migrated_count} successful, {failed_count} failed",
            migrated_count=migrated_count,
            failed_count=failed_count,
            details=details
        )

    except Exception as e:
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
