from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/networks",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_network_details(r1_client: R1Client = Depends(get_dynamic_r1_client)):
    """
    Fetches full details of WiFi networks from the R1 client.
    """    
    # tenant_id = '276690ec1c95400f917ff7c4ba0fcbf8'  # Example tenant ID, can be removed or made dynamic

    networks = await r1_client.networks.get_wifi_networks(r1_client.tenant_id)
    
    answer =  {
         "networks": networks,
    }
    print(answer)
    return {'status': 'success', 'data': answer}