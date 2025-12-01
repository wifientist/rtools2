from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

router = APIRouter(
    prefix="/venue",
    tags=["fer1agg"],
)

@router.get("/fulldetails")
async def get_venue_details(tenant_id: str, venue_id: str, r1_client: R1Client = Depends(get_dynamic_r1_client)):
    """
    Aggregate endpoint that gets comprehensive venue details including all WiFi settings and APs.

    Aggregates these R1 API calls (executed in parallel):
    - apLoadBalancingSettings
    - apRadioSettings
    - wifiAvailableChannels
    - apModelBandModeSettings
    - apModelExternalAntennaSettings
    - apClientAdmissionControlSettings
    - apModelCapabilities
    - apModelAntennaTypeSettings
    - All APs in venue
    """
    import asyncio

    # Map of settings to their corresponding service methods
    settings_map = {
        'apLoadBalancingSettings': r1_client.venues.get_ap_load_balancing_settings,
        'apRadioSettings': r1_client.venues.get_ap_radio_settings,
        'wifiAvailableChannels': r1_client.venues.get_wifi_available_channels,
        'apModelBandModeSettings': r1_client.venues.get_ap_model_band_mode_settings,
        'apModelExternalAntennaSettings': r1_client.venues.get_ap_model_external_antenna_settings,
        'apClientAdmissionControlSettings': r1_client.venues.get_ap_client_admission_control_settings,
        'apModelCapabilities': r1_client.venues.get_ap_model_capabilities,
        'apModelAntennaTypeSettings': r1_client.venues.get_ap_model_antenna_type_settings,
    }

    # Build tasks for parallel execution
    tasks = []
    task_keys = []
    for key, method in settings_map.items():
        tasks.append(method(tenant_id, venue_id))
        task_keys.append(key)

    # Also get APs in parallel
    tasks.append(r1_client.venues.get_aps_by_tenant_venue(tenant_id=tenant_id, venue_id=venue_id))
    task_keys.append('aps')

    # Execute all calls concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Build result dictionary
    result = {
        'venue_id': venue_id,
        'tenant_id': tenant_id
    }

    # Add results, handling any errors gracefully
    for key, response in zip(task_keys, responses):
        if isinstance(response, Exception):
            result[key] = {'error': str(response)}
        else:
            result[key] = response

    return {'status': 'success', 'data': result}