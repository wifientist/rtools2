import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/msp",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_msp_details(r1_client: R1Client = Depends(get_dynamic_r1_client)):
    ecs = await r1_client.msp.get_msp_ecs()
    labels = await r1_client.msp.get_msp_labels()
    tech_partners = await r1_client.msp.get_msp_tech_partners()
    entitlements = await r1_client.msp.get_entitlements()
    msp_entitlements = await r1_client.msp.get_msp_entitlements()
    msp_admins = await r1_client.msp.get_msp_admins()

    answer = {
         "ecs": ecs,
         "labels": labels,
         "tech_partners": tech_partners,
         "entitlements": entitlements,
         "msp_entitlements": msp_entitlements,
         "msp_admins": msp_admins,
    }
    logger.debug(f"MSP fulldetails fetched")
    return {'status': 'success', 'data': answer}


@router.get("/licensing")
async def get_msp_licensing(
    include_ecs: bool = True,
    r1_client: R1Client = Depends(get_dynamic_r1_client),
):
    """
    License pool + per-EC assignment positions for the MSP Licensing tool.

    Set include_ecs=false to skip the per-EC fanout (one call per MSP_EC) when
    only the pool timeline is needed.
    """
    data = await r1_client.entitlements.get_msp_licensing_overview(
        include_ecs=include_ecs
    )
    if data.get("error"):
        return JSONResponse(status_code=400, content={'status': 'error', 'detail': data['error']})
    return {'status': 'success', 'data': data}