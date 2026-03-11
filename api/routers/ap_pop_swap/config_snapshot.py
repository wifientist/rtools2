"""
Config snapshot capture — reads all AP-level settings from RuckusONE.

Captures settings in parallel where possible, returns a dict keyed by setting name.
"""
import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# All AP-level settings to capture (setting_key, service_method_name)
AP_SETTINGS = [
    ("radio_settings", "get_ap_radio_settings"),
    ("network_settings", "get_ap_network_settings"),
    ("management_vlan_settings", "get_ap_management_vlan_settings"),
    ("antenna_type_settings", "get_ap_antenna_type_settings"),
    ("band_mode_settings", "get_ap_band_mode_settings"),
    ("bss_coloring_settings", "get_ap_bss_coloring_settings"),
    ("external_antenna_settings", "get_ap_external_antenna_settings"),
    ("dhcp_settings", "get_ap_dhcp_settings"),
    ("iot_settings", "get_ap_iot_settings"),
    ("mesh_settings", "get_ap_mesh_settings"),
    ("smart_monitor_settings", "get_ap_smart_monitor_settings"),
    ("sticky_client_steering_settings", "get_ap_sticky_client_steering_settings"),
    ("usb_port_settings", "get_ap_usb_port_settings"),
    ("client_admission_control_settings", "get_ap_client_admission_control_settings"),
    ("led_settings", "get_ap_led_settings"),
]


async def capture_ap_snapshot(
    venues_service,
    tenant_id: str,
    venue_id: str,
    serial_number: str,
    ap_info: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Capture a full config snapshot from an AP.

    Args:
        venues_service: VenueService instance
        tenant_id: Tenant/EC ID
        venue_id: Venue ID
        serial_number: AP serial number
        ap_info: Optional pre-fetched AP info dict (name, apGroupId, etc.)

    Returns:
        Dict with all captured settings, keyed by setting name.
    """
    logger.info(f"Capturing config snapshot for AP {serial_number}")

    snapshot: Dict[str, Any] = {
        "serial_number": serial_number,
        "captured_settings": [],
        "failed_settings": [],
    }

    # Copy core identity from AP info
    if ap_info:
        snapshot["ap_name"] = ap_info.get("name", "")
        snapshot["ap_group_id"] = ap_info.get("apGroupId", "")
        snapshot["ap_group_name"] = ap_info.get("apGroupName", "")
        snapshot["ap_model"] = ap_info.get("model", "")

    # Capture all settings in parallel (read-only GETs, safe to parallelize)
    semaphore = asyncio.Semaphore(5)  # Limit concurrent R1 API calls

    async def fetch_setting(setting_key: str, method_name: str):
        async with semaphore:
            try:
                method = getattr(venues_service, method_name)
                result = await method(tenant_id, venue_id, serial_number)
                return setting_key, result
            except Exception as e:
                logger.debug(f"Failed to capture {setting_key} for {serial_number}: {e}")
                return setting_key, None

    tasks = [fetch_setting(key, method) for key, method in AP_SETTINGS]
    results = await asyncio.gather(*tasks)

    for setting_key, value in results:
        if value is not None:
            snapshot[setting_key] = value
            snapshot["captured_settings"].append(setting_key)
        else:
            snapshot["failed_settings"].append(setting_key)

    # Capture LAN port settings (uses existing composite method)
    try:
        lan_settings = await venues_service.get_ap_all_lan_port_settings(
            tenant_id, venue_id, serial_number,
            model=ap_info.get("model") if ap_info else None,
        )
        if lan_settings:
            snapshot["lan_port_settings"] = lan_settings
            snapshot["captured_settings"].append("lan_port_settings")
    except Exception as e:
        logger.debug(f"Failed to capture LAN port settings for {serial_number}: {e}")
        snapshot["failed_settings"].append("lan_port_settings")

    total = len(snapshot["captured_settings"])
    failed = len(snapshot["failed_settings"])
    logger.info(f"Snapshot for {serial_number}: {total} captured, {failed} failed")

    return snapshot
