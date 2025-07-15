from fastapi import APIRouter, Depends
from clients.r1_client import dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/msp",
    tags=["r1-msp"],
)

@router.get("/mspLabels")
async def get_msp_labels(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_labels()

@router.get("/mspEcs")
async def get_msp_ecs(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_ecs() 

@router.get("/mspTechPartners")
async def get_msp_tech_partners(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_tech_partners()

@router.get("/entitlements")
async def get_entitlements(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_entitlements()  

@router.get("/mspEntitlements")
async def get_msp_entitlements(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_entitlements() 

@router.get("/mspAdmins")
async def get_msp_admins(r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_admins() 

@router.get("/mspCustomers/{tenant_id}/admins")
async def get_msp_customer_admins(tenant_id: str, r1_client: R1Client = Depends(dynamic_r1_client)):
    return await r1_client.msp.get_msp_customer_admins(tenant_id)  


