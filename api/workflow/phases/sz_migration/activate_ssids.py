"""
SZ → R1 Migration: Phase 4 — Activate SSIDs (Per-WLAN)

For each WLAN, activates the SSID on the R1 AP Groups determined by the
resolver's activation map (stored in unit.plan.extra['activations']).

Uses POST /networkActivations for direct activation to specific AP Groups.
"""

import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("activate_ssids", "Activate SSIDs")
class ActivateSSIDsPhase(PhaseExecutor):
    """Per-WLAN phase: activate SSID on target AP Groups."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        network_id: Optional[str] = None

    class Outputs(BaseModel):
        activated: bool = False
        activations_attempted: int = 0
        activations_succeeded: int = 0
        already_active: int = 0

    async def execute(self, inputs: Inputs) -> Outputs:
        unit = None
        if self.context.state_manager:
            unit = await self.context.state_manager.get_unit(self.job_id, inputs.unit_id)

        plan_extra = unit.plan.extra if unit else {}
        wlan_name = plan_extra.get("sz_wlan_name", inputs.unit_number)
        activations = plan_extra.get("activations", [])

        if not inputs.network_id:
            await self.emit(f"[{wlan_name}] No network_id — cannot activate", "warning")
            return self.Outputs()

        if not activations:
            await self.emit(f"[{wlan_name}] No activations from resolver — skipping")
            return self.Outputs(activated=True)

        # Get unique R1 AP Group IDs from activations (mapped during validation)
        ap_group_targets = {}
        for act in activations:
            # Use R1 AP Group ID (resolved during validate phase), fall back to SZ ID
            apg_id = act.get("r1_ap_group_id") or act.get("ap_group_id")
            apg_name = act.get("r1_ap_group_name") or act.get("ap_group_name", "")
            if apg_id and apg_id not in ap_group_targets:
                ap_group_targets[apg_id] = apg_name

        await self.emit(
            f"[{wlan_name}] Activating on {len(ap_group_targets)} AP Group(s)..."
        )

        vlan_id = plan_extra.get("vlan_id", 1)

        succeeded = 0
        already_active = 0

        # Check current activations to avoid duplicates
        existing_ap_groups = set()
        try:
            existing_ap_groups = await self._get_active_ap_groups(inputs.network_id)
        except Exception:
            pass  # If check fails, try activation anyway

        for apg_id, apg_name in ap_group_targets.items():
            try:
                if apg_id in existing_ap_groups:
                    already_active += 1
                    await self.emit(
                        f"[{wlan_name}] Already active on AP Group '{apg_name}'"
                    )
                    continue

                # Direct activation via POST /networkActivations
                await self.r1_client.venues.activate_network_direct(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    network_id=inputs.network_id,
                    ap_group_id=apg_id,
                    ap_group_name=apg_name,
                    vlan_id=vlan_id,
                    wait_for_completion=True,
                )
                succeeded += 1
                await self.emit(
                    f"[{wlan_name}] Activated on AP Group '{apg_name}'"
                )
            except Exception as e:
                await self.emit(
                    f"[{wlan_name}] Failed to activate on '{apg_name}': {e}",
                    "warning",
                )

        total = len(ap_group_targets)
        await self.emit(
            f"[{wlan_name}] Activation complete: {succeeded} new, "
            f"{already_active} already active, "
            f"{total - succeeded - already_active} failed",
            "success" if succeeded + already_active == total else "warning",
        )

        return self.Outputs(
            activated=succeeded + already_active > 0,
            activations_attempted=total,
            activations_succeeded=succeeded,
            already_active=already_active,
        )

    async def _get_active_ap_groups(self, network_id: str) -> set:
        """
        Query current network activations to find already-active AP Groups.

        Uses POST /networkActivations/query filtered by venueId + networkId.
        Returns set of AP Group IDs that are already activated.
        """
        query_body = {
            "venueId": self.venue_id,
            "networkId": network_id,
            "page": 1,
            "pageSize": 100,
        }

        if self.r1_client.ec_type == "MSP" and self.tenant_id:
            response = self.r1_client.post(
                "/networkActivations/query",
                payload=query_body,
                override_tenant_id=self.tenant_id,
            )
        else:
            response = self.r1_client.post(
                "/networkActivations/query",
                payload=query_body,
            )

        if response.status_code != 200:
            return set()

        data = response.json()
        activations = data.get("data", [])

        active_groups = set()
        for act in activations:
            for ag in act.get("apGroups", []):
                apg_id = ag.get("apGroupId")
                if apg_id:
                    active_groups.add(apg_id)

        return active_groups
