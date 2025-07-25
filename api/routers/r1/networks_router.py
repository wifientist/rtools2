from fastapi import APIRouter, Depends
from clients.r1_client import create_r1_client_from_tenant, dynamic_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/networks",
    tags=["r1-networks"],
)

# @router.get("/networks/{tenant_id}")
# async def get_tenant_networks(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
#     response = r1_client.get("/networks", override_tenant_id=tenant_id)  #no function, just a GET endpoint
#     return response.json()
    
@router.get("/{tenant_id}")
async def get_wifi_networks(tenant_id: str, r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.networks.get_wifi_networks(tenant_id)  # Use the R1Client's network method to get networks
