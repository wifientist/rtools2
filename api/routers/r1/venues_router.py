from fastapi import APIRouter, Depends
from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/venues",
    tags=["r1-venues"],
)

@router.get("/{tenant_id}")
async def get_venues(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.venues.get_venues(tenant_id)  # Use the R1Client's venues method to get venues

@router.get("/{tenant_id}/aps/{venue_id}")
async def get_venue_aps(tenant_id: str, venue_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.venues.get_venue_aps(tenant_id, venue_id)  # Use the R1Client's venues method to get aps

@router.get("/{tenant_id}/apgroups")
async def get_ap_groups(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    result = await r1_client.venues.get_ap_groups(tenant_id)  # Use the R1Client's venues method to get AP groups
    print(f"\n{'='*80}")
    print(f"🎯 AP GROUPS ENDPOINT RESPONSE")
    print(f"{'='*80}")
    print(f"Type: {type(result)}")
    print(f"Content: {result}")
    print(f"{'='*80}\n")
    return result
