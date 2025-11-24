from fastapi import APIRouter, Depends
from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/networks",
    tags=["r1-networks"],
)

@router.get("/{tenant_id}")
async def get_wifi_networks(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    return await r1_client.networks.get_wifi_networks(tenant_id)  # Use the R1Client's network method to get networks
