import logging

from fastapi import APIRouter, Depends
from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/venues",
    tags=["r1-venues"],
)

@router.get("/{tenant_id}")
async def get_venues(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.venues.get_venues(tenant_id)

@router.get("/{tenant_id}/aps/{venue_id}")
async def get_venue_aps(tenant_id: str, venue_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.venues.get_venue_aps(tenant_id, venue_id)

@router.get("/{tenant_id}/apgroups")
async def get_ap_groups(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    result = await r1_client.venues.get_ap_groups(tenant_id)
    logger.debug(f"AP Groups endpoint response: type={type(result)}, content={result}")
    return result
