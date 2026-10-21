"""
V2 Phase: Activate WLANs on an AP Group (WLAN Activator)

Per AP Group: activate each of its planned WLANs directly on the group with
the Cloudpath import's non-deprecated 3-step path
(venues.activate_ssid_for_ap_group_direct). The network never passes through
All AP Groups. POST /networkActivations is deprecated and not used.

Step 1 (the venue association) runs ONCE per network:
  * not on the venue  -> PUT .../wifiNetworks/{id} with this group;
  * on All AP Groups  -> PUT .../settings with this group, which pulls it
                         off All AP Groups (validation warned about this);
  * already associated with other groups -> skipped; steps 2-3 bind this
                         group (the SZ migration's skip_step1 path).
Re-sending step 1 with a one-group list may REPLACE the association's group
list, so it must never run twice for a network. Units are AP Groups and run
in parallel, so when one network targets several groups, a per-network lock
serialises them and the first one through records that step 1 is done -- the
query index may not show the association yet when the next unit reads it.

A WLAN with a `vlan` gets it as this group's VLAN override (steps 1 and 3
carry it). One already bound to the group is only touched when its current
override differs: then step 3 alone sets it, keeping the group's radios.
No vlan means inherit -- nothing is sent, and an existing override is left.

Truth is a read-back of venueApGroups, not the activities: a WLAN that is not
bound to this group afterwards (or ended up on All AP Groups, or does not
carry the requested VLAN) fails the unit,
and assign_aps -- which depends on this phase -- leaves the APs where they
were instead of moving them into a group with no WLAN.
"""

import asyncio
import logging
from typing import Any, Dict, List, Set

from pydantic import BaseModel, Field

from services.wlan_activator import venue_state
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)

# "{job_id}:{network_id}" -> lock / step-1-done. In-process: every unit of a
# job runs in the one Brain that executes it.
_network_locks: Dict[str, asyncio.Lock] = {}
_associated: Set[str] = set()

READBACK_RETRY_DELAYS = [2, 4]


def _lock_for(key: str) -> asyncio.Lock:
    lock = _network_locks.get(key)
    if lock is None:
        lock = _network_locks[key] = asyncio.Lock()
    return lock


@register_phase("activate_wlans", "Activate WLANs on AP Group")
class ActivateWlansPhase(PhaseExecutor):

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        ap_group_id: str = ""
        ap_group_name: str = ""
        # [{network_id, name, ssid, dpsk_pool_id, radio_types, vlan}] from validation
        wlans: List[Dict[str, Any]] = Field(default_factory=list)

    class Outputs(BaseModel):
        activated: bool = True
        wlans_activated: int = 0
        wlans_already_active: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        group = inputs.ap_group_name or inputs.unit_number
        if not inputs.wlans:
            await self.emit(f"[{group}] WLANs already active on this AP Group")
            return self.Outputs()
        if not inputs.ap_group_id:
            raise RuntimeError(f"[{group}] No AP Group id - create_ap_group did not resolve one")

        done = already = 0
        errors: List[str] = []
        for w in inputs.wlans:
            label = w.get("ssid") or w.get("name") or w["network_id"]
            try:
                if await self._activate(inputs.ap_group_id, group, w, label):
                    done += 1
                else:
                    already += 1
            except Exception as e:
                errors.append(f"'{label}': {e}")
                await self.emit(f"[{group}] Activating '{label}' reported: {e}", "warning")

        # Read-back: R1's record decides, not the activities.
        missing = await self._not_bound(inputs.ap_group_id, inputs.wlans)
        for delay in READBACK_RETRY_DELAYS:
            if not missing:
                break
            # The query index can trail a completed activity by a moment;
            # re-read before calling a finished activation a failure.
            await asyncio.sleep(delay)
            missing = await self._not_bound(inputs.ap_group_id, inputs.wlans)
        if missing:
            detail = f"; errors: {'; '.join(errors)}" if errors else ""
            raise RuntimeError(f"[{group}] Not active on this AP Group: {', '.join(missing)}{detail}")

        await self.emit(
            f"[{group}] {len(inputs.wlans)} WLAN(s) active"
            + (f" ({already} already were)" if already else ""),
            "success",
        )
        return self.Outputs(activated=True, wlans_activated=done, wlans_already_active=already)

    async def _not_bound(self, ap_group_id: str, wlans: List[Dict[str, Any]]) -> List[str]:
        """WLANs (with why) that R1 does not show bound to this group."""
        missing = []
        for w in wlans:
            network = await self.r1_client.networks.query_wifi_network_by_id(w["network_id"], self.tenant_id)
            state, bound = venue_state(network, self.venue_id)
            label = w.get('ssid') or w.get('name')
            if state != "specific" or ap_group_id not in bound:
                where = "on All AP Groups" if state == "venue_wide" else "not bound"
                missing.append(f"{label} ({where})")
            elif w.get("vlan") is not None:
                current = await self._group_settings(w["network_id"], ap_group_id)
                if current.get("vlanId") != w["vlan"]:
                    missing.append(f"{label} (VLAN {current.get('vlanId') or 'inherited'}, wanted {w['vlan']})")
        return missing

    async def _group_settings(self, network_id: str, ap_group_id: str) -> Dict[str, Any]:
        try:
            return await self.r1_client.venues.get_ssid_ap_group_settings(
                self.tenant_id, self.venue_id, network_id, ap_group_id)
        except Exception as e:
            logger.warning(f"AP Group settings read failed ({network_id} on {ap_group_id}): {e}")
            return {}

    async def _activate(self, ap_group_id: str, group: str, w: Dict[str, Any], label: str) -> bool:
        """Activate one WLAN on this group. False if it was already bound."""
        network_id = w["network_id"]
        key = f"{self.job_id}:{network_id}"
        async with _lock_for(key):
            network = await self.r1_client.networks.query_wifi_network_by_id(network_id, self.tenant_id)
            state, bound = venue_state(network, self.venue_id)
            if state == "specific" and ap_group_id in bound:
                _associated.add(key)
                vlan = w.get("vlan")
                current = await self._group_settings(network_id, ap_group_id) if vlan is not None else {}
                if vlan is None or current.get("vlanId") == vlan:
                    await self.emit(f"[{group}] '{label}' already active on this AP Group")
                    return False
                await self.emit(
                    f"[{group}] '{label}': VLAN {current.get('vlanId') or 'inherited'} -> {vlan}..."
                )
                await self.r1_client.venues.set_ssid_ap_group_settings(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    wifi_network_id=network_id,
                    ap_group_id=ap_group_id,
                    radio_types=current.get("radioTypes") or w.get("radio_types") or ["2.4-GHz", "5-GHz"],
                    vlan_id=vlan,
                )
                return True

            step1_done = key in _associated or state == "specific"
            if step1_done:
                action = "binding to AP Group"
            elif state == "venue_wide":
                action = "moving off All AP Groups onto AP Group"
            else:
                action = "activating on AP Group"
            if w.get("vlan") is not None:
                action += f" (VLAN {w['vlan']})"
            await self.emit(f"[{group}] '{label}': {action}...")

            await self.r1_client.venues.activate_ssid_for_ap_group_direct(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                wifi_network_id=network_id,
                ap_group_id=ap_group_id,
                radio_types=w.get("radio_types") or None,
                # None = inherit the network VLAN; a value is this group's override.
                vlan_id=w.get("vlan"),
                dpsk_service_id=w.get("dpsk_pool_id") or None,
                # venue_wide: the association exists, so step 1 goes to /settings.
                already_activated=state == "venue_wide",
                skip_step1=step1_done,
                wait_for_completion=True,
            )
            _associated.add(key)
            return True

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(
            valid=True,
            will_create=bool(inputs.wlans),
            estimated_api_calls=3 * len(inputs.wlans),
            notes=["Direct AP Group activation (3 PUTs, never All AP Groups)"],
        )
