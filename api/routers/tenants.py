# api/routers/tenants.py (additional endpoints for tenant management)

from typing import List
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from models.user import User
from models.tenant import Tenant
from dependencies import get_db, get_current_user
from schemas.tenant import TenantCreate, SetActiveTenantRequest, SetSecondaryTenantRequest
from security import create_access_token


router = APIRouter(prefix="/tenants", tags=["Tenants"])

@router.get("/mine")
def get_my_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Fetch all API keys (tenants) belonging to this user
    tenants = db.query(Tenant).filter(Tenant.user_id == current_user.id).all()

    return [
        {
            "id": tenant.id,
            "name": tenant.name,
        }
        for tenant in tenants
    ]

@router.post("/new")
def create_tenant(tenant: TenantCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):

    # Create a new tenant
    new_tenant = Tenant(
        name=tenant.name,
        user_id=current_user.id,
        tenant_id=tenant.tenant_id,
        ec_type=tenant.ec_type, # returns None if not provided
    )

    new_tenant.set_client_id(tenant.client_id)
    new_tenant.set_shared_secret(tenant.shared_secret)

    db.add(new_tenant)
    db.commit()
    db.refresh(new_tenant)

    return {
        "status": "success",
        "id": new_tenant.id,
        "name": new_tenant.name,
        "tenant_id": new_tenant.tenant_id,
    }


@router.post("/set-active-tenant")
def set_active_tenant(
    payload: SetActiveTenantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = payload.tenant_id

    # Fetch the requested API key and make sure it belongs to the current user
    tenant = db.query(Tenant).filter_by(id=tenant_id, user_id=current_user.id).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found or not owned by user.")

    current_user.active_tenant_id = tenant.id
    db.commit()
    db.refresh(current_user)

    # Create a new token with the updated active tenant info
    access_token = create_access_token({
        "sub": current_user.email,
        "id": current_user.id,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "active_tenant_id": current_user.active_tenant_id,
        "secondary_tenant_id": current_user.secondary_tenant_id,
    })

    # Return new token as cookie
    response = JSONResponse(content={"message": "Active tenant updated"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in production!
        samesite="Strict",
    )

    return response


@router.post("/set-secondary-tenant")
def set_secondary_tenant(
    payload: SetSecondaryTenantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = payload.tenant_id

    # Fetch the requested API key and make sure it belongs to the current user
    tenant = db.query(Tenant).filter_by(id=tenant_id, user_id=current_user.id).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found or not owned by user.")

    current_user.secondary_tenant_id = tenant.id
    db.commit()
    db.refresh(current_user)

    # Create a new token with the updated secondary tenant info
    access_token = create_access_token({
        "sub": current_user.email,
        "id": current_user.id,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "active_tenant_id": current_user.active_tenant_id,
        "secondary_tenant_id": current_user.secondary_tenant_id,
    })

    # Return new token as cookie
    response = JSONResponse(content={"message": "Secondary tenant updated"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in production!
        samesite="Strict",
    )

    return response


class TenantResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    region: str = None
    
    class Config:
        from_attributes = True

class UserTenantInfo(BaseModel):
    tenant_id: str
    name: str
    is_active: bool
    is_secondary: bool

@router.get("/available", response_model=List[UserTenantInfo])
async def get_user_available_tenants(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all tenants that the current user has access to.
    This helps the frontend know which tenant_ids are valid for dynamic routing.
    """
    user_tenants = []
    
    # Get active tenant
    if current_user.active_tenant_id:
        active_tenant = db.query(Tenant).filter(Tenant.id == current_user.active_tenant_id).first()
        if active_tenant:
            user_tenants.append(UserTenantInfo(
                tenant_id=active_tenant.tenant_id,
                name=active_tenant.name,
                is_active=True,
                is_secondary=False
            ))
    
    # Get secondary tenant
    if current_user.secondary_tenant_id:
        secondary_tenant = db.query(Tenant).filter(Tenant.id == current_user.secondary_tenant_id).first()
        if secondary_tenant:
            user_tenants.append(UserTenantInfo(
                tenant_id=secondary_tenant.tenant_id,
                name=secondary_tenant.name,
                is_active=False,
                is_secondary=True
            ))
    
    # You might want to add logic here to get other tenants the user has access to
    # based on company membership, permissions, etc.
    
    return user_tenants

@router.get("/validate/{tenant_id}")
async def validate_tenant_access(
    tenant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Validate if the current user has access to a specific tenant.
    Useful for frontend validation before making API calls.
    """
    from clients.r1_client import validate_tenant_access
    
    try:
        tenant = validate_tenant_access(tenant_id, current_user, db)
        return {
            "valid": True,
            "tenant_id": tenant.tenant_id,
            "tenant_name": tenant.name,
            "message": f"Access granted to tenant '{tenant_id}'"
        }
    except HTTPException as e:
        return {
            "valid": False,
            "tenant_id": tenant_id,
            "message": e.detail
        }

@router.get("/{tenant_id}/info", response_model=TenantResponse)
async def get_tenant_info(
    tenant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get basic information about a specific tenant.
    Requires user to have access to the tenant.
    """
    from clients.r1_client import validate_tenant_access
    
    tenant = validate_tenant_access(tenant_id, current_user, db)
    
    return TenantResponse(
        id=tenant.id,
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        region=getattr(tenant, 'region', None)
    )