"""
SZ → R1 Migration: Activate SSIDs (Per-WLAN)

Activates each WLAN's network on the R1 AP Groups the plan chose, with the
radios SZ actually broadcast it on.

Mechanism: services.r1_activation (shared with AP Mapping) -- the direct
AP-group activation, venue association once for every target group, then
steps 2-3 per group. POST /networkActivations, used here before, is
deprecated past its removal date.

WLANs the plan put on All AP Groups (property-wide) take the venue-wide
activation instead (venues.activate_ssid_on_venue) and must read back as
isAllApGroups. A network already bound to specific groups is not silently
widened -- that's a different broadcast than the plan showed.

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

from services import r1_activation
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

        mode = plan_extra.get("activation_mode", "ap_groups")
        if mode == "none":
            await self.emit(f"[{wlan_name}] Migrated without activation (no SZ AP broadcasts it)")
            return self.Outputs(activated=True)
        if mode == "all_ap_groups":
            return await self._activate_all_ap_groups(inputs, plan_extra, wlan_name)

        targets = await self._targets(plan_extra.get("activations", []), wlan_name)
        if not targets:
            await self.emit(f"[{wlan_name}] No R1 AP Group to activate on (its groups were not chosen "
                            "for creation) — network created, not activated")
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
        errors = await r1_activation.activate_on_groups(
            self.r1_client, self.tenant_id, self.venue_id, inputs.network_id, todo, state,
            dpsk_pool_id=inputs.dpsk_pool_id or None, emit=self.emit, label=f"[{wlan_name}] ",
        )

        # The activities are not the truth -- R1's own record is, once its
        # query index has caught up.
        want = {t["id"] for t in targets}
        state, bound = await r1_activation.settled_state(
            self.r1_client, self.tenant_id, self.venue_id, inputs.network_id,
            lambda st, b: st == "venue_wide" or want <= b)
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

    async def _activate_all_ap_groups(self, inputs: "ActivateSSIDsPhase.Inputs",
                                      plan_extra: Dict[str, Any], wlan_name: str) -> "ActivateSSIDsPhase.Outputs":
        state, bound = await self._venue_state(inputs.network_id)
        if state == "venue_wide":
            await self.emit(f"[{wlan_name}] Already on All AP Groups")
            return self.Outputs(activated=True, activations_attempted=1, already_active=1)
        if state == "specific":
            raise RuntimeError(
                f"[{wlan_name}] Network is already on {len(bound)} specific AP Group(s) in this venue; "
                "the plan puts it on All AP Groups — remove those activations in R1, then re-run"
            )

        radio_types = plan_extra.get("all_ap_radio_types") or ["2.4-GHz", "5-GHz"]
        await self.emit(f"[{wlan_name}] Activating on All AP Groups ({', '.join(radio_types)})...")
        use_tracker = self.context.activity_tracker is not None
        result = await r1_activation.activate_all_ap_groups(
            self.r1_client, self.tenant_id, self.venue_id, inputs.network_id, radio_types,
            dpsk_pool_id=inputs.dpsk_pool_id or None, wait_for_completion=not use_tracker,
        )
        request_id = result.get("requestId") if isinstance(result, dict) else None
        if use_tracker and request_id:
            activity = await self.fire_and_wait(request_id)
            if not activity.success:
                raise RuntimeError(f"[{wlan_name}] All AP Groups activation failed: {activity.error}")

        state, _ = await r1_activation.settled_state(
            self.r1_client, self.tenant_id, self.venue_id, inputs.network_id,
            lambda st, b: st == "venue_wide")
        if state != "venue_wide":
            raise RuntimeError(f"[{wlan_name}] Not on All AP Groups after activation (R1 still reports '{state}' "
                               "after ~45s)")
        await self.emit(f"[{wlan_name}] Active on All AP Groups", "success")
        return self.Outputs(activated=True, activations_attempted=1, activations_succeeded=1)

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
        return await r1_activation.venue_state(self.r1_client, self.tenant_id, self.venue_id, network_id)
