from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/msp",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_msp_details(r1_client: R1Client = Depends(dynamic_r1_client)):
    ecs = await r1_client.msp.get_msp_ecs()
    labels = await r1_client.msp.get_msp_labels()
    tech_partners = await r1_client.msp.get_msp_tech_partners()
    entitlements = await r1_client.msp.get_entitlements()
    msp_entitlements = await r1_client.msp.get_msp_entitlements()
    msp_admins = await r1_client.msp.get_msp_admins()

    # print(ecs)
    # print(labels)
    # print(tech_partners)
    # print(entitlements)
    # print(msp_entitlements)
    # print(msp_admins)

    #msp_customer_admins = r1_client.msp.get_msp_customer_admins()
    
    answer =  {
         "ecs": ecs,
         "labels": labels,
         "tech_partners": tech_partners,
         "entitlements": entitlements,
         "msp_entitlements": msp_entitlements,
         "msp_admins": msp_admins,
         #"msp_customer_admins": msp_customer_admins
    }
    print(answer)
    return {'status': 'success', 'data': answer}