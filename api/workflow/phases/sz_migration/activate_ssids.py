"""
SZ → R1 Migration: Activate SSIDs (Per-WLAN)

Activates each WLAN's network on the R1 AP Groups the plan chose, with the
radios SZ actually broadcast it on.

Mechanism: the Cloudpath import's direct AP-group activation
(venues.activate_ssid_for_ap_group_direct) -- three ordered, non-deprecated
PUTs that never put the network on All AP Groups. POST /networkActivations,
used here before, is deprecated past its removal date.

One network, several groups: the venue association (step 1) is made ONCE
with every target group in it; each group then gets steps 2-3. Re-sending
step 1 per group may replace the group list rather than extend it.

Truth comes from a read-back of /wifiNetworks/query venueApGroups, not from
the activities: the network must end up on every target group and not on
All AP Groups, or the unit FAILS. (Errors used to be logged as warnings and
the job reported COMPLETED with nothing activated.)

Runs after configure_dpsk (graph edge): R1 refuses to activate a DPSK
network that has no DPSK service yet (WIFI-20300).
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("activate_ssids", "Activate SSIDs")
class ActivateSSIDsPhase(PhaseExecutor):
    """Per-WLAN phase: activate the network on its target R1 AP Groups."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: Optional[str] = None
        dpsk_pool_id: Optional[str] = None

    class Outputs(BaseModel):
        activated: bool = False
        activations_attempted: int = 0
        activations_succeeded: int = 0
        already_active: int = 0

    async def execute(self, inputs: Inputs) -> Outputs:
        unit = await self.context.state_manager.get_unit(self.job_id, inputs.unit_id)
        plan_extra = unit.plan.extra if unit else {}
        wlan_name = plan_extra.get("sz_wlan_name", inputs.unit_number)

        if not inputs.network_id:
            raise RuntimeError(f"[{wlan_name}] No network id — the network was not created")

        targets = await self._targets(plan_extra.get("activations", []), wlan_name)
        if not targets:
            await self.emit(f"[{wlan_name}] Not broadcast on any AP Group with APs — nothing to activate")
            return self.Outputs(activated=True)

        state, bound = await self._venue_state(inputs.network_id)
        if state == "venue_wide":
            raise RuntimeError(
                f"[{wlan_name}] Network is on All AP Groups in this venue — "
                "remove that activation in R1, then re-run"
            )

        todo = [t for t in targets if t["id"] not in bound]
        already = len(targets) - len(todo)
        if not todo:
            await self.emit(f"[{wlan_name}] Already active on all {len(targets)} AP Group(s)")
            return self.Outputs(activated=True, activations_attempted=len(targets), already_active=already)

        await self.emit(f"[{wlan_name}] Activating on {len(todo)} AP Group(s)...")
        # The DPSK pool rides along on step 1 so R1 registers the association fully.
        dpsk_pool_id = inputs.dpsk_pool_id or None
        step1_groups = [
            {"apGroupId": t["id"], "radioTypes": t["radio_types"], "radio": "Both"} for t in todo
        ]
        errors: List[str] = []
        for i, t in enumerate(todo):
            try:
                await self.r1_client.venues.activate_ssid_for_ap_group_direct(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    wifi_network_id=inputs.network_id,
                    ap_group_id=t["id"],
                    radio_types=t["radio_types"],
                    vlan_id=None,                     # inherit the network VLAN (no "custom" override)
                    dpsk_service_id=dpsk_pool_id,
                    already_activated=state == "specific",
                    # Step 1 only once, carrying every group not yet bound.
                    step1_ap_groups=step1_groups if i == 0 else None,
                    skip_step1=i > 0 or state == "specific",
                )
                await self.emit(
                    f"[{wlan_name}] Activated on '{t['name']}' ({', '.join(t['radio_types'])})"
                )
            except Exception as e:
                errors.append(f"'{t['name']}': {e}")
                await self.emit(f"[{wlan_name}] Activation on '{t['name']}' reported: {e}", "warning")

        # The activities are not the truth -- R1's own record is.
        state, bound = await self._venue_state(inputs.network_id)
        missing = [t["name"] for t in targets if t["id"] not in bound]
        if state == "venue_wide":
            raise RuntimeError(f"[{wlan_name}] Ended up on All AP Groups instead of specific groups")
        if missing:
            detail = f"; errors: {'; '.join(errors)}" if errors else ""
            raise RuntimeError(f"[{wlan_name}] Not active on AP Group(s) {', '.join(missing)}{detail}")

        await self.emit(f"[{wlan_name}] Active on all {len(targets)} AP Group(s)", "success")
        return self.Outputs(
            activated=True,
            activations_attempted=len(targets),
            activations_succeeded=len(todo),
            already_active=already,
        )

    async def _targets(self, activations: List[Dict[str, Any]], wlan_name: str) -> List[Dict[str, Any]]:
        """De-duplicated {id, name, radio_types} per R1 AP Group; created groups resolved by name."""
        created: Dict[str, str] = {}
        if any(a.get("r1_ap_group_create") for a in activations):
            job = await self.context.state_manager.get_job(self.job_id)
            created = (job.global_phase_results.get("create_ap_groups") or {}).get("ap_group_ids") or {}

        out: Dict[str, Dict[str, Any]] = {}
        for a in activations:
            name = a.get("r1_ap_group_name") or a.get("ap_group_name", "")
            gid = a.get("r1_ap_group_id") or (created.get(name) if a.get("r1_ap_group_create") else None)
            if not gid:
                raise RuntimeError(f"[{wlan_name}] R1 AP Group '{name}' has no id — it was not created")
            entry = out.setdefault(gid, {"id": gid, "name": name, "radio_types": []})
            for r in a.get("r1_radio_types") or ["2.4-GHz", "5-GHz"]:
                if r not in entry["radio_types"]:
                    entry["radio_types"].append(r)
        return list(out.values())

    async def _venue_state(self, network_id: str) -> Tuple[str, Set[str]]:
        """('not_found' | 'specific' | 'venue_wide', bound AP Group ids) for this venue."""
        network = await self.r1_client.networks.query_wifi_network_by_id(network_id, self.tenant_id)
        for vag in (network or {}).get("venueApGroups") or []:
            if vag.get("venueId") != self.venue_id:
                continue
            if vag.get("isAllApGroups"):
                return "venue_wide", set()
            ids = set(vag.get("apGroupIds") or [])
            ids |= {g.get("apGroupId") for g in vag.get("apGroups") or [] if g.get("apGroupId")}
            return "specific", ids
        return "not_found", set()
