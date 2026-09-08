"""
Reading fields off an AP record, whatever shaped it.

There are two AP dictionaries in this codebase and they spell the serial
number differently:

    R1 raw (venues.get_aps_by_tenant_venue, /venues/aps/query)
        {"serialNumber": "123456789012", "name": "...", "apGroupId": "..."}

    Our own routers, reshaping for the frontend
        {"serial": "123456789012", "name": "..."}          e.g. ap_rename,
        ap_port_config, per_unit_ssid, ap_regroup endpoints

Phases receive the RAW list (`all_venue_aps`), so `serialNumber` is the one
that is actually there. Reading `serial` off it does not raise -- it quietly
returns "". That cost us the entire AP-assignment feature in the Cloudpath
import: validate built `{ap.get('serial', ''): ap}`, every key collapsed to
"", and the serial it handed downstream was empty, so `if serial:` dropped
each assignment on the floor and the phase reported success having moved
nothing.

Read serials through ap_serial() rather than reaching for either key.
"""

from typing import Any, Dict


def ap_serial(ap: Dict[str, Any]) -> str:
    """
    The AP's serial number, from whichever key holds it.

    Returns "" when there is none, which callers must treat as "cannot act on
    this AP" -- never pass it into a URL, and never use it as a dict key.
    """
    if not ap:
        return ""
    return str(ap.get("serialNumber") or ap.get("serial") or "").strip()


def index_aps_by_serial(aps) -> Dict[str, Dict[str, Any]]:
    """Serial -> AP, skipping any record whose serial is missing."""
    return {s: ap for ap in aps if (s := ap_serial(ap))}


def index_aps_by_name(aps) -> Dict[str, Dict[str, Any]]:
    """Name -> AP, skipping any record whose name is missing."""
    return {n: ap for ap in aps if (n := str(ap.get("name") or "").strip())}
