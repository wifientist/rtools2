from fastapi import APIRouter, Depends
from clients.r1_client import get_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/venues",
    tags=["r1-venues"],
)

@router.get("/venues/{tenant_id}")
async def get_tenant_venues(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
    response = r1_client.get("/venues", override_tenant_id=tenant_id)
    return response.json()


@router.get("/venues/{tenant_id}/aps")
async def get_tenant_aps(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
    response = r1_client.post(f"/venues/{tenant_id}/aps", override_tenant_id=tenant_id)