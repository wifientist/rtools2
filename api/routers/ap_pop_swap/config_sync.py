"""
Config sync — applies stored AP settings to a new AP.

Applies settings sequentially (each PUT returns 202, awaited before next)
to respect R1 rate limits and ordering requirements.
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Settings to apply, in order (setting_key, service_method_name)
# Core settings first, then secondary settings
CONFIG_APPLY_ORDER = [
    ("radio_settings", "set_ap_radio_settings"),
    ("network_settings", "set_ap_network_settings"),
    ("management_vlan_settings", "set_ap_management_vlan_settings"),
    ("antenna_type_settings", "set_ap_antenna_type_settings"),
    ("band_mode_settings", "set_ap_band_mode_settings"),
    ("bss_coloring_settings", "set_ap_bss_coloring_settings"),
    ("external_antenna_settings", "set_ap_external_antenna_settings"),
    ("dhcp_settings", "set_ap_dhcp_settings"),
    ("iot_settings", "set_ap_iot_settings"),
    ("mesh_settings", "set_ap_mesh_settings"),
    ("smart_monitor_settings", "set_ap_smart_monitor_settings"),
    ("sticky_client_steering_settings", "set_ap_sticky_client_steering_settings"),
    ("usb_port_settings", "set_ap_usb_port_settings"),
    ("client_admission_control_settings", "set_ap_client_admission_control_settings"),
    ("led_settings", "set_ap_led_settings"),
]

# Core settings that must succeed for the swap to be considered successful
CORE_SETTINGS = {"radio_settings", "network_settings"}


async def apply_config_to_ap(
    venues_service,
    tenant_id: str,
    venue_id: str,
    serial_number: str,
    config_data: dict,
) -> Dict[str, Any]:
    """
    Apply all stored config settings to a new AP.

    Settings are applied sequentially — each PUT is awaited before the next.

    Args:
        venues_service: VenueService instance
        tenant_id: Tenant/EC ID
        venue_id: Venue ID
        serial_number: New AP serial number
        config_data: The stored config snapshot dict

    Returns:
        Dict with per-setting results and overall status:
        {
            "results": {"radio_settings": "success", "led_settings": "failed: ...", ...},
            "success": True/False (based on core settings),
            "applied": 12,
            "failed": 3,
        }
    """
    logger.info(f"Applying config to AP {serial_number} ({len(config_data)} settings in snapshot)")

    results: Dict[str, str] = {}
    applied = 0
    failed = 0

    # Apply standard settings in order
    for setting_key, method_name in CONFIG_APPLY_ORDER:
        payload = config_data.get(setting_key)
        if payload is None:
            continue

        try:
            method = getattr(venues_service, method_name)
            await method(tenant_id, venue_id, serial_number, payload, wait_for_completion=True)
            results[setting_key] = "success"
            applied += 1
            logger.info(f"  Applied {setting_key} to {serial_number}")
        except Exception as e:
            error_msg = str(e)[:200]
            results[setting_key] = f"failed: {error_msg}"
            failed += 1
            logger.warning(f"  Failed {setting_key} for {serial_number}: {error_msg}")

    # Apply LAN port settings (more complex — multi-step)
    lan_settings = config_data.get("lan_port_settings")
    if lan_settings:
        lan_results = await _apply_lan_port_settings(
            venues_service, tenant_id, venue_id, serial_number, lan_settings
        )
        results["lan_port_settings"] = lan_results["status"]
        if lan_results["status"] == "success":
            applied += 1
        else:
            failed += 1

    # Determine overall success based on core settings
    core_ok = all(
        results.get(s) == "success"
        for s in CORE_SETTINGS
        if s in config_data
    )

    return {
        "results": results,
        "success": core_ok,
        "applied": applied,
        "failed": failed,
    }


async def _apply_lan_port_settings(
    venues_service,
    tenant_id: str,
    venue_id: str,
    serial_number: str,
    lan_settings: dict,
) -> Dict[str, Any]:
    """Apply LAN port settings: first set useVenueSettings, then per-port overrides."""
    try:
        use_venue = lan_settings.get("useVenueSettings", True)

        # Step 1: Set AP-level LAN port specific settings
        if not use_venue:
            await venues_service.set_ap_lan_port_specific_settings(
                tenant_id, venue_id, serial_number,
                use_venue_settings=False,
                wait_for_completion=True,
            )

        # Step 2: Apply per-port overrides
        ports = lan_settings.get("ports", [])
        port_errors = []
        for port in ports:
            port_id = port.get("portId", "")
            untag_id = port.get("untagId")
            vlan_members = port.get("vlanMembers")

            if untag_id is not None:
                try:
                    await venues_service.set_ap_lan_port_settings(
                        tenant_id, venue_id, serial_number,
                        port_id=port_id,
                        untagged_vlan=untag_id,
                        vlan_members=vlan_members if vlan_members else None,
                        wait_for_completion=True,
                    )
                except Exception as e:
                    port_errors.append(f"{port_id}: {str(e)[:100]}")

            # Apply enabled/disabled state
            enabled = port.get("enabled")
            if enabled is not None and not enabled:
                try:
                    await venues_service.set_ap_lan_port_enabled(
                        tenant_id, venue_id, serial_number,
                        port_id=port_id,
                        enabled=False,
                        wait_for_completion=True,
                    )
                except Exception as e:
                    port_errors.append(f"{port_id} enable: {str(e)[:100]}")

        if port_errors:
            return {"status": f"partial: {'; '.join(port_errors)}"}
        return {"status": "success"}

    except Exception as e:
        return {"status": f"failed: {str(e)[:200]}"}
