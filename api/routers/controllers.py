# api/routers/controllers.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from models.user import User
from models.controller import Controller
from dependencies import get_db, get_current_user
from schemas.controller import (
    RuckusONEControllerCreate,
    SmartZoneControllerCreate,
    ControllerResponse,
    SetActiveControllerRequest,
    SetSecondaryControllerRequest,
    UserControllerInfo
)
from security import create_access_token


router = APIRouter(prefix="/controllers", tags=["Controllers"])


# ===== List Controllers =====

@router.get("/mine", response_model=List[ControllerResponse])
def get_my_controllers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all controllers owned by the current user.
    Returns both RuckusONE and SmartZone controllers.
    """
    controllers = db.query(Controller).filter(Controller.user_id == current_user.id).all()

    return [
        ControllerResponse(
            id=controller.id,
            name=controller.name,
            controller_type=controller.controller_type,
            controller_subtype=controller.controller_subtype,
            r1_tenant_id=controller.r1_tenant_id,
            r1_region=controller.r1_region,
            sz_host=controller.sz_host,
            sz_port=controller.sz_port,
            sz_version=controller.sz_version,
        )
        for controller in controllers
    ]


# ===== Create Controller =====

@router.post("/new/ruckusone", response_model=ControllerResponse)
def create_ruckusone_controller(
    controller_data: RuckusONEControllerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new RuckusONE controller.
    Supports both MSP and EC subtypes.
    """
    # Create new controller
    new_controller = Controller(
        name=controller_data.name,
        user_id=current_user.id,
        controller_type="RuckusONE",
        controller_subtype=controller_data.controller_subtype,
        r1_tenant_id=controller_data.r1_tenant_id,
        r1_region=controller_data.r1_region,
    )

    # Encrypt credentials
    new_controller.set_r1_client_id(controller_data.r1_client_id)
    new_controller.set_r1_shared_secret(controller_data.r1_shared_secret)

    db.add(new_controller)
    db.commit()
    db.refresh(new_controller)

    return ControllerResponse(
        id=new_controller.id,
        name=new_controller.name,
        controller_type=new_controller.controller_type,
        controller_subtype=new_controller.controller_subtype,
        r1_tenant_id=new_controller.r1_tenant_id,
        r1_region=new_controller.r1_region,
    )


@router.post("/new/smartzone", response_model=ControllerResponse)
def create_smartzone_controller(
    controller_data: SmartZoneControllerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new SmartZone controller (stubbed for future implementation).
    """
    # Create new controller
    new_controller = Controller(
        name=controller_data.name,
        user_id=current_user.id,
        controller_type="SmartZone",
        controller_subtype=None,  # No subtype for SmartZone
        sz_host=controller_data.sz_host,
        sz_port=controller_data.sz_port,
        sz_use_https=controller_data.sz_use_https,
        sz_version=controller_data.sz_version,
    )

    # Encrypt credentials
    new_controller.set_sz_username(controller_data.sz_username)
    new_controller.set_sz_password(controller_data.sz_password)

    db.add(new_controller)
    db.commit()
    db.refresh(new_controller)

    return ControllerResponse(
        id=new_controller.id,
        name=new_controller.name,
        controller_type=new_controller.controller_type,
        sz_host=new_controller.sz_host,
        sz_port=new_controller.sz_port,
        sz_version=new_controller.sz_version,
    )


# ===== Update Controller =====

@router.put("/{controller_id}", response_model=ControllerResponse)
def update_controller(
    controller_id: int,
    controller_data: dict,  # Generic dict to handle both types
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update an existing controller's details.
    Users can only update their own controllers.
    """
    # Fetch the controller and verify ownership
    controller = db.query(Controller).filter_by(id=controller_id, user_id=current_user.id).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found or not owned by user.")

    # Update based on controller type
    if controller.controller_type == "RuckusONE":
        if "name" in controller_data:
            controller.name = controller_data["name"]
        if "controller_subtype" in controller_data:
            controller.controller_subtype = controller_data["controller_subtype"]
        if "r1_tenant_id" in controller_data:
            controller.r1_tenant_id = controller_data["r1_tenant_id"]
        if "r1_region" in controller_data:
            controller.r1_region = controller_data["r1_region"]
        if "r1_client_id" in controller_data:
            controller.set_r1_client_id(controller_data["r1_client_id"])
        if "r1_shared_secret" in controller_data:
            controller.set_r1_shared_secret(controller_data["r1_shared_secret"])

    elif controller.controller_type == "SmartZone":
        if "name" in controller_data:
            controller.name = controller_data["name"]
        if "sz_host" in controller_data:
            controller.sz_host = controller_data["sz_host"]
        if "sz_port" in controller_data:
            controller.sz_port = controller_data["sz_port"]
        if "sz_use_https" in controller_data:
            controller.sz_use_https = controller_data["sz_use_https"]
        if "sz_version" in controller_data:
            controller.sz_version = controller_data["sz_version"]
        if "sz_username" in controller_data:
            controller.set_sz_username(controller_data["sz_username"])
        if "sz_password" in controller_data:
            controller.set_sz_password(controller_data["sz_password"])

    db.commit()
    db.refresh(controller)

    return ControllerResponse(
        id=controller.id,
        name=controller.name,
        controller_type=controller.controller_type,
        controller_subtype=controller.controller_subtype,
        r1_tenant_id=controller.r1_tenant_id,
        r1_region=controller.r1_region,
        sz_host=controller.sz_host,
        sz_port=controller.sz_port,
        sz_version=controller.sz_version,
    )


# ===== Delete Controller =====

@router.delete("/{controller_id}")
def delete_controller(
    controller_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a controller. Users can only delete their own controllers.
    """
    # Fetch the controller and verify ownership
    controller = db.query(Controller).filter_by(id=controller_id, user_id=current_user.id).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found or not owned by user.")

    # Clear active/secondary controller references if they point to this controller
    if current_user.active_controller_id == controller_id:
        current_user.active_controller_id = None
    if current_user.secondary_controller_id == controller_id:
        current_user.secondary_controller_id = None

    db.delete(controller)
    db.commit()

    return {"status": "success", "message": "Controller deleted successfully"}


# ===== Set Active Controller =====

@router.post("/set-active")
def set_active_controller(
    payload: SetActiveControllerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Set the user's active controller.
    This controller will be used for primary operations.
    """
    controller_id = payload.controller_id

    # Fetch the controller and verify ownership
    controller = db.query(Controller).filter_by(id=controller_id, user_id=current_user.id).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found or not owned by user.")

    current_user.active_controller_id = controller.id
    db.commit()
    db.refresh(current_user)

    # Create a new token with the updated active controller info
    access_token = create_access_token({
        "sub": current_user.email,
        "id": current_user.id,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "active_controller_id": current_user.active_controller_id,
        "secondary_controller_id": current_user.secondary_controller_id,
    })

    # Return new token as cookie
    response = JSONResponse(content={"message": "Active controller updated"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in production!
        samesite="Strict",
    )

    return response


# ===== Set Secondary Controller =====

@router.post("/set-secondary")
def set_secondary_controller(
    payload: SetSecondaryControllerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Set the user's secondary controller.
    This controller can be used for comparison features.
    """
    controller_id = payload.controller_id

    # Fetch the controller and verify ownership
    controller = db.query(Controller).filter_by(id=controller_id, user_id=current_user.id).first()

    if not controller:
        raise HTTPException(status_code=404, detail="Controller not found or not owned by user.")

    current_user.secondary_controller_id = controller.id
    db.commit()
    db.refresh(current_user)

    # Create a new token with the updated secondary controller info
    access_token = create_access_token({
        "sub": current_user.email,
        "id": current_user.id,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "active_controller_id": current_user.active_controller_id,
        "secondary_controller_id": current_user.secondary_controller_id,
    })

    # Return new token as cookie
    response = JSONResponse(content={"message": "Secondary controller updated"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in production!
        samesite="Strict",
    )

    return response


# ===== Clear Secondary Controller =====

@router.post("/clear-secondary")
def clear_secondary_controller(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Clear the user's secondary controller.
    """
    current_user.secondary_controller_id = None
    db.commit()
    db.refresh(current_user)

    # Create a new token with the updated secondary controller info
    from security import is_production
    access_token = create_access_token({
        "sub": current_user.email,
        "id": current_user.id,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "active_controller_id": current_user.active_controller_id,
        "secondary_controller_id": current_user.secondary_controller_id,
    })

    # Return new token as cookie
    response = JSONResponse(content={"message": "Secondary controller cleared"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=is_production(),
        samesite="Strict",
    )

    return response


# ===== Get Available Controllers (for routing) =====

@router.get("/available", response_model=List[UserControllerInfo])
async def get_user_available_controllers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all controllers that the current user has access to.
    This helps the frontend know which controllers are valid for dynamic routing.
    """
    user_controllers = []

    # Get active controller
    if current_user.active_controller_id:
        active_controller = db.query(Controller).filter(Controller.id == current_user.active_controller_id).first()
        if active_controller:
            user_controllers.append(UserControllerInfo(
                controller_id=active_controller.id,
                name=active_controller.name,
                controller_type=active_controller.controller_type,
                controller_subtype=active_controller.controller_subtype,
                is_active=True,
                is_secondary=False
            ))

    # Get secondary controller
    if current_user.secondary_controller_id:
        secondary_controller = db.query(Controller).filter(Controller.id == current_user.secondary_controller_id).first()
        if secondary_controller:
            user_controllers.append(UserControllerInfo(
                controller_id=secondary_controller.id,
                name=secondary_controller.name,
                controller_type=secondary_controller.controller_type,
                controller_subtype=secondary_controller.controller_subtype,
                is_active=False,
                is_secondary=True
            ))

    return user_controllers
