import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/networks",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_network_details(r1_client: R1Client = Depends(get_dynamic_r1_client)):
    """
    Fetches full details of WiFi networks from the R1 client.
    """
    networks = await r1_client.networks.get_wifi_networks(r1_client.tenant_id)

    answer = {
         "networks": networks,
    }
    logger.debug(f"Networks fulldetails: {len(networks.get('data', [])) if isinstance(networks, dict) else 'N/A'} networks")
    return {'status': 'success', 'data': answer}