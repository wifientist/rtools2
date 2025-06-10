from fastapi import APIRouter, Depends
from clients.r1_client import get_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/tenant",
    tags=["r1-tenant"],
)

@router.get("/{tenant_id}/aps")
async def get_tenant_aps(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
    return await r1_client.tenant.get_tenant_aps(tenant_id)  # Use the R1Client's venues method to get aps

