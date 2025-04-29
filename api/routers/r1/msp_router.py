from fastapi import APIRouter, Depends
from clients.r1_client import create_r1_client_from_tenant, get_r1_client
from sqlalchemy.orm import Session
from dependencies import get_db
from r1api.client import R1Client

router = APIRouter(
    prefix="/msp",
    tags=["r1-msp"],
)

@router.get("/ecs")
async def list_msp_ecs(r1_client: R1Client = Depends(get_r1_client)):
    return r1_client.msp.get_msp_ecs()  #actually built a function for this

@router.get("/entitlements")
async def get_entitlements(r1_client: R1Client = Depends(get_r1_client)):
    response = r1_client.get("/entitlements")  #no need for function, just passing in the path here
    return response.json()

@router.get("/mspEntitlements")
async def get_msp_entitlements(r1_client: R1Client = Depends(get_r1_client)):
    response = r1_client.get("/mspEntitlements")
    return response.json()