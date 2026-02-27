"""
SZ Firmware → API Version Mapping

Maps SmartZone zone firmware versions to the correct API version string.
Zone firmware is reported as a 5-part version (e.g., "7.1.1.0.123").
We match on the major.minor prefix to determine the API version.

The controller-level version is used for login and system calls.
Zone-specific calls use the zone's firmware-derived API version.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Map major.minor firmware prefix → SZ API version
# Ordered from newest to oldest for documentation clarity
FIRMWARE_TO_API = {
    "7.1": "v13_1",
    "7.0": "v13_0",
    "6.1": "v11_1",
    "6.0": "v11_0",
    "5.2": "v9_1",
    "5.1": "v8_0",
}


def detect_zone_api_version(zone_firmware: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    """
    Determine the correct SZ API version for a zone based on its firmware.

    Args:
        zone_firmware: Zone firmware version string (e.g., "7.1.1.0.123" or "6.1.2.0.456")
        fallback: API version to return if firmware can't be mapped (e.g., the controller's version)

    Returns:
        API version string (e.g., "v13_1") or fallback if unmapped
    """
    if not zone_firmware:
        if fallback:
            logger.debug(f"No zone firmware provided, using fallback: {fallback}")
            return fallback
        return None

    # Extract major.minor from the firmware string
    parts = zone_firmware.strip().split(".")
    if len(parts) < 2:
        logger.warning(f"Unexpected zone firmware format: '{zone_firmware}', using fallback: {fallback}")
        return fallback

    major_minor = f"{parts[0]}.{parts[1]}"

    api_version = FIRMWARE_TO_API.get(major_minor)
    if api_version:
        logger.info(f"Zone firmware {zone_firmware} → API {api_version}")
        return api_version

    # Try just major version match as last resort
    for prefix, version in FIRMWARE_TO_API.items():
        if prefix.startswith(parts[0] + "."):
            logger.warning(
                f"No exact match for firmware {zone_firmware} (major.minor={major_minor}), "
                f"using closest match: {prefix} → {version}"
            )
            return version

    logger.warning(f"Unknown zone firmware {zone_firmware}, using fallback: {fallback}")
    return fallback
