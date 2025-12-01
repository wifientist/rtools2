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
    """
    Get comprehensive tenant details including venues with all WiFi settings.

    This aggregates:
    - Basic venue info
    - All venue WiFi settings (for each venue)
    - APs
    - WLANs
    - AP Groups
    """
    import asyncio

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

    # Enrich venues with detailed WiFi settings
    venues_list = venues if isinstance(venues, list) else []

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

    # Fetch detailed settings for all venues in parallel
    enriched_venues = []
    for venue in venues_list:
        venue_id = venue.get('id')
        if venue_id:
            # Build tasks for this venue
            tasks = []
            task_keys = []
            for key, method in settings_map.items():
                tasks.append(method(tenant_id, venue_id))
                task_keys.append(key)

            # Execute all calls for this venue concurrently
            try:
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                # Add settings to venue object
                enriched_venue = {**venue}  # Copy base venue data
                for key, response in zip(task_keys, responses):
                    if isinstance(response, Exception):
                        enriched_venue[key] = {'error': str(response)}
                    else:
                        enriched_venue[key] = response

                enriched_venues.append(enriched_venue)
            except Exception as e:
                print(f"Error enriching venue {venue_id}: {e}")
                enriched_venues.append(venue)  # Keep base venue data if enrichment fails
        else:
            enriched_venues.append(venue)

    answer =  {
        "venues": enriched_venues,
        "aps": aps,
        "wlans": wlans_list,
        "apGroups": apGroups_list,
    }
    print(f"Final answer keys: {answer.keys()}")
    print(f"WLANs count: {len(wlans_list) if isinstance(wlans_list, list) else 'not a list'}")
    print(f"Enriched venues count: {len(enriched_venues)}")
    return {'status': 'success', 'data': answer}