from fastapi import APIRouter, Depends
from clients.r1_client import create_r1_client_from_tenant, get_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/msp",
    tags=["r1-networks"],
)

@router.get("/networks/{tenant_id}")
async def list_msp_ecs(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
    response = r1_client.get("/networks", override_tenant_id=tenant_id)  #no function, just a GET endpoint
    return response.json()