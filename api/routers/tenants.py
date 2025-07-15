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
