from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/venue",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_venue_details(r1_client: R1Client = Depends(get_r1_client)):
    venue_id = '1'
    
    aps = await r1_client.venues.get_venue_aps(venue_id)
    
    answer =  {
         "aps": aps,
    }
    print(answer)
    return {'status': 'success', 'data': answer}