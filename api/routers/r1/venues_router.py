from fastapi import APIRouter, Depends
from clients.r1_client import dynamic_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/venues",
    tags=["r1-venues"],
)

@router.get("/{tenant_id}")
async def get_venues(tenant_id: str, r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.venues.get_venues(tenant_id)  # Use the R1Client's venues method to get venues

@router.get("/{tenant_id}/aps/{venue_id}")
async def get_venue_aps(tenant_id: str, venue_id: str, r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.venues.get_venue_aps(tenant_id, venue_id)  # Use the R1Client's venues method to get aps

