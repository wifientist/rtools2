from fastapi import APIRouter, Depends
from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/tenant",
    tags=["r1-tenant"],
)

@router.get("/self")
async def get_tenant_self(r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.tenant.get_tenant_self()

@router.get("/userProfiles")
async def get_tenant_user_profiles(r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.tenant.get_tenant_user_profiles()

@router.get("/{tenant_id}/aps")
async def get_tenant_aps(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.tenant.get_tenant_aps(tenant_id) 

