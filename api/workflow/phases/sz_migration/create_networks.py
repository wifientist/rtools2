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

from redis_client import get_redis_client
from services.ap_group_ops import api_error
from schemas.sz_migration import SZMigrationSnapshot
from services.wlan_grabber import build_spec as build_wispr_spec, build_wispr_payload, wispr_radius_settings
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
        # The plan's R1 name: the SZ name, or "<name>_<venue>" when another
        # property's network holds it. The SSID is always SZ's.
        r1_name = plan_extra.get("r1_network_name") or wlan_name
        r1_type = plan_extra.get("r1_network_type", inputs.r1_network_type or "psk")
        vlan_id = plan_extra.get("vlan_id", 1)

        # Fast path: already resolved from validation
        if inputs.network_id:
            await self.emit(f"[{wlan_name}] Network already exists (reused)")
            return self.Outputs(network_id=inputs.network_id, reused=True)

        # Check if network exists by name (idempotent)
        existing = await self.r1_client.networks.find_wifi_network_by_name(
            self.tenant_id, self.venue_id, r1_name,
        )
        if existing and existing.get("id"):
            net_id = existing["id"]
            await self.emit(f"[{wlan_name}] Found existing network (id={net_id[:12]})")
            await self.track_resource("wifi_networks", {
                "id": net_id, "name": r1_name, "reused": True,
            })
            return self.Outputs(network_id=net_id, reused=True)

        if r1_type == "wispr":
            return await self._create_wispr(plan_extra, r1_name, wlan_name)

        # ── Create network based on type ──────────────────────────────
        await self.emit(f"[{wlan_name}] Creating {r1_type} network"
                        + (f" as '{r1_name}' (SSID '{ssid}')" if r1_name != wlan_name else "") + "...")

        try:
            if r1_type == "psk":
                network = await self._create_psk_network(plan_extra, r1_name, ssid, vlan_id)
            elif r1_type == "open":
                network = await self._create_open_network(plan_extra, r1_name, ssid, vlan_id)
            elif r1_type == "aaa":
                network = await self._create_aaa_network(plan_extra, r1_name, ssid, vlan_id)
            elif r1_type == "dpsk":
                network = await self._create_dpsk_network(plan_extra, r1_name, ssid, vlan_id)
            elif r1_type == "dpsk_external":
                network = await self._create_dpsk_network(plan_extra, r1_name, ssid, vlan_id,
                                                          use_dpsk_service=False)
            else:
                raise RuntimeError(f"Unsupported R1 network type: {r1_type}")
        except RuntimeError:
            raise
        except Exception as e:
            # R1's reason, not the HTTP wrapper ("400 Client Error: Bad Request for url").
            raise RuntimeError(f"R1 refused the network: {api_error(e)}") from e

        net_id = network.get("id", "")
        if not net_id:
            raise RuntimeError(f"Network creation returned no ID for '{wlan_name}'")

        await self.track_resource("wifi_networks", {
            "id": net_id, "name": r1_name, "type": r1_type,
        })
        await self.emit(f"[{wlan_name}] Created {r1_type} network (id={net_id[:12]})", "success")

        # Link RADIUS profile: Enterprise, and external DPSK (keys checked by it)
        if r1_type in ("aaa", "dpsk_external"):
            await self._link_radius(plan_extra, net_id, wlan_name)

        return self.Outputs(network_id=net_id, reused=False)

    async def _radius_mappings(self) -> Dict[str, str]:
        job = await self.context.state_manager.get_job(self.job_id)
        return (job.global_phase_results.get("create_radius_profiles") or {}).get("radius_profile_mappings") or {}

    async def _create_wispr(self, extra, name, wlan_name) -> 'CreateNetworksPhase.Outputs':
        """
        An SZ hotspot WLAN as an R1 WISPr guest network, built exactly as WLAN
        Grabber builds it (services.wlan_grabber): portal / redirect URL,
        provider, walled garden, session times, bypass CNA, MAC bypass, VLAN
        and advanced settings; the SZ signing key as the integration key (MAC/
        IP encrypted when SZ signs); RADIUS auth + accounting linked with SZ's
        proxy settings, or Always Accept. The key is read from the snapshot
        here, used, and dropped -- it is never in the job's plan.
        """
        job = await self.context.state_manager.get_job(self.job_id)
        snapshot_id = (job.input_data or {}).get("sz_snapshot_job_id")
        raw = await (await get_redis_client()).get(f"sz_migration:extraction:{snapshot_id}:snapshot")
        if not raw:
            raise RuntimeError(f"[{wlan_name}] SZ snapshot not found — run the extraction again")
        snapshot = SZMigrationSnapshot.model_validate_json(raw)
        wlan = next((w for w in snapshot.wlans if w.id == extra.get("sz_wlan_id")), None)
        if wlan is None:
            raise RuntimeError(f"[{wlan_name}] SZ WLAN not in the snapshot")
        pid = ((wlan.raw or {}).get("portalServiceProfile") or {}).get("id")
        ref = snapshot.referenced_objects.get(f"portal_service_profile:{pid}")
        portal = None if ref is None or (ref.raw or {}).get("_error") else ref.raw
        spec = build_wispr_spec(wlan.raw or {}, "wispr", {pid: portal} if portal else {})
        snapshot = raw = None
        if spec["blockers"]:
            raise RuntimeError(f"[{wlan_name}] WISPr guest network can't be built: {'; '.join(spec['blockers'])}")

        facts = spec.get("portal") or {}
        radius_auth = facts.get("auth_type") == "RADIUS"
        mappings = await self._radius_mappings() if radius_auth else {}
        auth_profile = mappings.get(spec.get("auth_service_id") or "")
        if radius_auth and not auth_profile:
            # Created with RADIUS auth but no server, it would authenticate nobody.
            raise RuntimeError(f"[{wlan_name}] RADIUS profile for the SZ auth service isn't available — "
                               "choose it under RADIUS Servers")

        payload = build_wispr_payload(spec, integration_key=spec.get("wispr_key"),
                                      description=extra.get("sz_description") or None)
        payload["name"] = name                     # the plan's R1 name
        payload["wlan"]["ssid"] = extra.get("ssid") or payload["wlan"]["ssid"]   # SZ's, edges trimmed for R1
        encrypted = bool(spec.get("wispr_key"))
        spec["wispr_key"] = None
        await self.emit(f"[{wlan_name}] Creating WISPr guest network"
                        + (f" as '{name}'" if name != wlan_name else "")
                        + f" ({'RADIUS auth' if radius_auth else 'Always Accept'})...")
        try:
            network = await self.r1_client.networks.create_wifi_network_from_payload(self.tenant_id, payload)
        except Exception as e:
            raise RuntimeError(f"[{wlan_name}] R1 refused the WISPr network: {api_error(e)}") from e
        finally:
            payload = None                         # carries the integration key
        net_id = (network or {}).get("id")
        if not net_id:
            raise RuntimeError(f"[{wlan_name}] R1 accepted the create but the network was not found")
        await self.track_resource("wifi_networks", {"id": net_id, "name": name, "type": "wispr"})
        await self.emit(f"[{wlan_name}] Created WISPr guest network (id={net_id[:12]}; "
                        f"{'integration key set' if encrypted else 'no integration key'})", "success")

        if radius_auth:
            profiles = [("authentication", auth_profile)]
            acct = mappings.get(spec.get("accounting_service_id") or "")
            if acct and acct != auth_profile:
                profiles.append(("accounting", acct))
            for label, pid in profiles:
                try:
                    await self.r1_client.radius_profiles.link_radius_to_network(
                        network_id=net_id, radius_profile_id=pid, tenant_id=self.tenant_id)
                except Exception as e:
                    await self.emit(f"[{wlan_name}] RADIUS {label} profile not linked: {e}", "warning")
            settings = wispr_radius_settings(spec)
            if settings:
                try:
                    await self.r1_client.networks.update_radius_settings_on_network(net_id, settings, self.tenant_id)
                except Exception as e:
                    await self.emit(f"[{wlan_name}] RADIUS proxy setting not applied: {e}", "warning")
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
            description=extra.get("sz_description") or None,
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
            description=extra.get("sz_description") or None,
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
            description=extra.get("sz_description") or None,
            advanced_customization=extra.get("r1_advanced_settings"),
        )

    async def _create_dpsk_network(self, extra, name, ssid, vlan_id, use_dpsk_service=True):
        """Create a DPSK network: R1 DPSK service, or (external) keys checked by RADIUS."""
        return await self.r1_client.networks.create_dpsk_wifi_network(
            use_dpsk_service=use_dpsk_service,
            tenant_id=self.tenant_id,
            venue_id=self.venue_id,
            name=name,
            ssid=ssid,
            vlan_id=vlan_id,
            description=extra.get("sz_description") or None,
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

        # Accounting is a separate profile on the same network. Absent from
        # the mapping when its service was set to "don't migrate".
        acct_service_id = extra.get("accounting_service_id")
        acct_profile_id = mappings.get(acct_service_id) if acct_service_id else None
        if acct_profile_id and acct_profile_id != r1_profile_id:
            await self.r1_client.radius_profiles.link_radius_to_network(
                network_id=network_id,
                radius_profile_id=acct_profile_id,
                tenant_id=self.tenant_id,
            )
            await self.emit(f"[{wlan_name}] Accounting profile linked")
