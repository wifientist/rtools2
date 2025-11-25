from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/tenant",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_tenant_details(tenant_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):

    venues = await r1_client.tenant.get_tenant_venues(tenant_id)
    aps = await r1_client.tenant.get_tenant_aps(tenant_id)

    # Fetch WLANs with error handling
    try:
        wlans = await r1_client.networks.get_wifi_networks(tenant_id)
        print(f"WLANs response type: {type(wlans)}")
        print(f"WLANs response: {wlans}")
        wlans_list = wlans.get('data', []) if isinstance(wlans, dict) else wlans  # Fixed: 'data' not 'list'
    except Exception as e:
        print(f"Error fetching WLANs: {e}")
        wlans_list = []

    # Fetch AP Groups with error handling
    try:
        apGroups = await r1_client.venues.get_ap_groups(tenant_id)
        print(f"AP Groups response type: {type(apGroups)}")
        apGroups_list = apGroups if isinstance(apGroups, list) else []
    except Exception as e:
        print(f"Error fetching AP Groups: {e}")
        apGroups_list = []

    answer =  {
        "venues": venues,
        "aps": aps,
        "wlans": wlans_list,
        "apGroups": apGroups_list,
    }
    print(f"Final answer keys: {answer.keys()}")
    print(f"WLANs count: {len(wlans_list) if isinstance(wlans_list, list) else 'not a list'}")
    return {'status': 'success', 'data': answer}