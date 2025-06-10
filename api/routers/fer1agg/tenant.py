from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/tenant",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_tenant_details(tenant_id: str, r1_client: R1Client = Depends(get_r1_client)):
    
    venues = await r1_client.tenant.get_tenant_venues(tenant_id)
    aps = await r1_client.tenant.get_tenant_aps(tenant_id)
    
    answer =  {
         "venues": venues,
        "aps": aps,
    }
    print(answer)
    return {'status': 'success', 'data': answer}