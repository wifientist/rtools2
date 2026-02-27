"""
SZ → R1 Migration: Phase 2 — Create WiFi Networks (Per-WLAN)

For each WLAN unit, create or reuse the corresponding WiFi network in R1.
Network type (PSK/Open/AAA/DPSK) is determined by the security mapper output
stored in unit.plan.extra['r1_network_type'].

For AAA networks, links the RADIUS profile created in Phase 1.
"""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)


@register_phase("create_networks", "Create WiFi Networks")
class CreateNetworksPhase(PhaseExecutor):
    """Per-WLAN phase: create or reuse WiFi network in R1."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str  # WLAN name
        # Pre-resolved from validation
        network_id: Optional[str] = None
        # From plan.extra (via input_config)
        r1_network_type: Optional[str] = None

    class Outputs(BaseModel):
        network_id: str = ""
        reused: bool = False

    async def execute(self, inputs: Inputs) -> Outputs:
        unit = None
        if self.context.state_manager:
            unit = await self.context.state_manager.get_unit(self.job_id, inputs.unit_id)

        plan_extra = unit.plan.extra if unit else {}
        wlan_name = plan_extra.get("sz_wlan_name", inputs.unit_number)
        ssid = plan_extra.get("ssid", wlan_name)
        r1_type = plan_extra.get("r1_network_type", inputs.r1_network_type or "psk")
        vlan_id = plan_extra.get("vlan_id", 1)

        # Fast path: already resolved from validation
        if inputs.network_id:
            await self.emit(f"[{wlan_name}] Network already exists (reused)")
            return self.Outputs(network_id=inputs.network_id, reused=True)

        # Check if network exists by name (idempotent)
        existing = await self.r1_client.networks.find_wifi_network_by_name(
            self.tenant_id, self.venue_id, wlan_name,
        )
        if existing and existing.get("id"):
            net_id = existing["id"]
            await self.emit(f"[{wlan_name}] Found existing network (id={net_id[:12]})")
            await self.track_resource("wifi_networks", {
                "id": net_id, "name": wlan_name, "reused": True,
            })
            return self.Outputs(network_id=net_id, reused=True)

        # ── Create network based on type ──────────────────────────────
        await self.emit(f"[{wlan_name}] Creating {r1_type} network...")

        if r1_type == "psk":
            network = await self._create_psk_network(plan_extra, wlan_name, ssid, vlan_id)
        elif r1_type == "open":
            network = await self._create_open_network(plan_extra, wlan_name, ssid, vlan_id)
        elif r1_type == "aaa":
            network = await self._create_aaa_network(plan_extra, wlan_name, ssid, vlan_id)
        elif r1_type == "dpsk":
            network = await self._create_dpsk_network(plan_extra, wlan_name, ssid, vlan_id)
        else:
            raise RuntimeError(f"Unsupported R1 network type: {r1_type}")

        net_id = network.get("id", "")
        if not net_id:
            raise RuntimeError(f"Network creation returned no ID for '{wlan_name}'")

        await self.track_resource("wifi_networks", {
            "id": net_id, "name": wlan_name, "type": r1_type,
        })
        await self.emit(f"[{wlan_name}] Created {r1_type} network (id={net_id[:12]})", "success")

        # Link RADIUS profile if AAA
        if r1_type == "aaa":
            await self._link_radius(plan_extra, net_id, wlan_name)

        return self.Outputs(network_id=net_id, reused=False)

    async def _create_psk_network(self, extra, name, ssid, vlan_id):
        """Create a PSK network, extracting passphrase from SZ encryption config."""
        encryption = extra.get("encryption") or {}
        passphrase = (
            encryption.get("passphrase")
            or encryption.get("saePassphrase")
            or "changeme12345"
        )

        sz_auth = extra.get("sz_auth_type", "")
        if "WPA3" in sz_auth:
            security_type = "WPA3"
        elif "WPA2" in sz_auth:
            security_type = "WPA2"
        else:
            security_type = "WPA3"

        return await self.r1_client.networks.create_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=name,
            ssid=ssid,
            passphrase=passphrase,
            security_type=security_type,
            vlan_id=vlan_id,
            description=f"Migrated from SZ: {extra.get('sz_wlan_name', name)}",
            advanced_customization=extra.get("r1_advanced_settings"),
        )

    async def _create_open_network(self, extra, name, ssid, vlan_id):
        """Create an Open network."""
        return await self.r1_client.networks.create_open_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=name,
            ssid=ssid,
            vlan_id=vlan_id,
            description=f"Migrated from SZ: {name}",
            advanced_customization=extra.get("r1_advanced_settings"),
        )

    async def _create_aaa_network(self, extra, name, ssid, vlan_id):
        """Create an AAA (Enterprise) network."""
        sz_auth = extra.get("sz_auth_type", "")
        security_type = "WPA3" if "WPA3" in sz_auth else "WPA2Enterprise"

        return await self.r1_client.networks.create_aaa_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=name,
            ssid=ssid,
            vlan_id=vlan_id,
            security_type=security_type,
            description=f"Migrated from SZ: {name}",
            advanced_customization=extra.get("r1_advanced_settings"),
        )

    async def _create_dpsk_network(self, extra, name, ssid, vlan_id):
        """Create a DPSK network."""
        return await self.r1_client.networks.create_dpsk_wifi_network(
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=name,
            ssid=ssid,
            vlan_id=vlan_id,
            description=f"Migrated from SZ: {name}",
            advanced_customization=extra.get("r1_advanced_settings"),
        )

    async def _link_radius(self, extra, network_id, wlan_name):
        """Link RADIUS profile to AAA network."""
        auth_service_id = extra.get("auth_service_id")
        if not auth_service_id:
            await self.emit(
                f"[{wlan_name}] No RADIUS service reference — manual link needed",
                "warning",
            )
            return

        # Get mapping from global phase results
        job = await self.context.state_manager.get_job(self.job_id)
        radius_results = job.global_phase_results.get("create_radius_profiles", {})
        mappings = radius_results.get("radius_profile_mappings", {})
        r1_profile_id = mappings.get(auth_service_id)

        if not r1_profile_id:
            await self.emit(
                f"[{wlan_name}] RADIUS mapping not found for {auth_service_id[:12]} — "
                f"manual link needed",
                "warning",
            )
            return

        await self.emit(f"[{wlan_name}] Linking RADIUS profile...")
        await self.r1_client.radius_profiles.link_radius_to_network(
            network_id=network_id,
            radius_profile_id=r1_profile_id,
            tenant_id=self.tenant_id,
        )
        await self.emit(f"[{wlan_name}] RADIUS profile linked")
