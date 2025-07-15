from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/venue",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_venue_details(tenant_id: str, venue_id: str, r1_client: R1Client = Depends(dynamic_r1_client)):
    
    aps = await r1_client.venues.get_aps_by_tenant_venue(tenant_id=tenant_id, venue_id=venue_id)
    
    answer =  {
         "aps": aps,
    }
    print(answer)
    return {'status': 'success', 'data': answer}