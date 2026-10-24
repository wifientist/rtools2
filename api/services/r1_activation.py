"""
Activating a WLAN (R1 network) in a venue: on specific AP Groups, or on All
AP Groups. Shared by the SZ migration (activate_ssids, and AP Mapping) so
there is one way to do it and one way to check it.

Specific groups use the direct AP-group activation
(venues.activate_ssid_for_ap_group_direct): three ordered, non-deprecated
PUTs that never put the network on All AP Groups. The venue association
(step 1) is made ONCE carrying every group not yet bound; each group then
gets steps 2-3 -- re-sending step 1 per group may replace the group list
rather than extend it.

Truth is R1's own record (venue_state), not the activities: callers read it
back after activating.
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

Emit = Optional[Callable[..., Awaitable[None]]]


async def venue_state(r1_client, tenant_id: str, venue_id: str, network_id: str) -> Tuple[str, Set[str]]:
    """('not_found' | 'specific' | 'venue_wide', bound AP Group ids) for this venue."""
    network = await r1_client.networks.query_wifi_network_by_id(network_id, tenant_id)
    return state_from_network(network, venue_id)


async def settled_state(r1_client, tenant_id: str, venue_id: str, network_id: str,
                        done: Callable[[str, Set[str]], bool],
                        delays: Tuple[float, ...] = (0, 2, 3, 5, 8, 12, 15)) -> Tuple[str, Set[str]]:
    """
    venue_state, re-read until done(state, bound) or the delays run out (about
    45s). The read-back goes through R1's query index, which lags the write:
    a venue-wide activation that had worked read back 'not_found' and failed
    the WLAN. Returns the last state read.
    """
    state, bound = "not_found", set()
    for d in delays:
        if d:
            await asyncio.sleep(d)
        state, bound = await venue_state(r1_client, tenant_id, venue_id, network_id)
        if done(state, bound):
            break
    return state, bound


def state_from_network(network: Optional[Dict[str, Any]], venue_id: str) -> Tuple[str, Set[str]]:
    """venue_state from a network record already in hand (e.g. an inventory capture)."""
    for vag in (network or {}).get("venueApGroups") or []:
        if vag.get("venueId") != venue_id:
            continue
        if vag.get("isAllApGroups"):
            return "venue_wide", set()
        ids = set(vag.get("apGroupIds") or [])
        ids |= {g.get("apGroupId") for g in vag.get("apGroups") or [] if g.get("apGroupId")}
        return "specific", ids
    return "not_found", set()


async def activate_on_groups(r1_client, tenant_id: str, venue_id: str, network_id: str,
                             todo: List[Dict[str, Any]], state: str,
                             dpsk_pool_id: Optional[str] = None,
                             emit: Emit = None, label: str = "") -> List[str]:
    """
    Activate the network on each group in todo ({id, name, radio_types}),
    none of them bound yet. state is venue_state's answer from just before.
    Returns the errors R1 reported, per group; the caller reads back.
    """
    step1_groups = [{"apGroupId": t["id"], "radioTypes": t["radio_types"], "radio": "Both"} for t in todo]
    errors: List[str] = []
    for i, t in enumerate(todo):
        try:
            await r1_client.venues.activate_ssid_for_ap_group_direct(
                tenant_id=tenant_id,
                venue_id=venue_id,
                wifi_network_id=network_id,
                ap_group_id=t["id"],
                radio_types=t["radio_types"],
                vlan_id=None,                     # inherit the network VLAN (no "custom" override)
                # The DPSK pool rides along on step 1 so R1 registers the association fully.
                dpsk_service_id=dpsk_pool_id,
                already_activated=state == "specific",
                step1_ap_groups=step1_groups if i == 0 else None,
                skip_step1=i > 0 or state == "specific",
            )
            if emit:
                await emit(f"{label}Activated on '{t['name']}' ({', '.join(t['radio_types'])})")
        except Exception as e:
            errors.append(f"'{t['name']}': {e}")
            if emit:
                await emit(f"{label}Activation on '{t['name']}' reported: {e}", "warning")
    return errors


async def activate_all_ap_groups(r1_client, tenant_id: str, venue_id: str, network_id: str,
                                 radio_types: List[str], dpsk_pool_id: Optional[str] = None,
                                 wait_for_completion: bool = True) -> Any:
    """Venue-wide activation (All AP Groups). Returns R1's response (requestId when not waiting)."""
    return await r1_client.venues.activate_ssid_on_venue(
        tenant_id=tenant_id,
        venue_id=venue_id,
        wifi_network_id=network_id,
        dpsk_service_id=dpsk_pool_id,
        radio_types=radio_types,
        wait_for_completion=wait_for_completion,
    )
