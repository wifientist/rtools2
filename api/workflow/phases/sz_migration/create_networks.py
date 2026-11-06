"""
SZ → R1 Migration: Phase 2 — Create WiFi Networks (Per-WLAN)

For each WLAN unit, create or reuse the corresponding WiFi network in R1.
Network type (PSK/Open/AAA/DPSK) is determined by the security mapper output
stored in unit.plan.extra['r1_network_type'].

For AAA networks, links the RADIUS profile created in Phase 1.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel

from redis_client import get_redis_client
from services.ap_group_ops import api_error, is_transient, still_running
from schemas.sz_migration import SZMigrationSnapshot
from services.portal_key import controller_portal_key
from services.sz_migration.passphrase_check import compare_passphrases
from services.wlan_grabber import build_spec as build_wispr_spec, build_wispr_payload, wispr_radius_settings
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor

logger = logging.getLogger(__name__)

# R1 lags on big runs (hundreds of networks): a create whose task finished
# isn't findable by name yet ("returned no ID"), a task runs for minutes
# and lands later, or R1 answers 503 / "System might be busy". Every such
# WLAN worked on a re-run. So: wait on the task itself (up to ~5 min, its
# activity is the truth), look again by name for a while before failing,
# and retry only a transient refusal -- after checking it didn't land anyway.
FIND_DELAYS = (2, 3, 5, 5, 10, 10, 15, 15)       # ~65s of re-reads by name
RETRY_DELAYS = (10, 30)                         # pauses before a 2nd / 3rd create


def psk_security(encryption: Dict[str, Any]):
    """
    (R1 security type, passphrase, separate SAE passphrase or None) from SZ's
    encryption block -- by SZ's method, not the auth-type label (WPA23_Mixed
    reads as "WPA3" there, and would become WPA3-only in R1, locking out
    WPA2 clients). Passphrases are SZ's own; None when SZ has none.
    """
    method = str(encryption.get("method") or "").upper()
    pp, sae = encryption.get("passphrase") or None, encryption.get("saePassphrase") or None
    if method == "WPA23_MIXED":
        main = pp or sae
        return "WPA2/WPA3", main, (sae if sae and sae != main else None)
    if method == "WPA3":
        return "WPA3", sae or pp, None
    return "WPA2", pp or sae, None       # WPA2, WPA_Mixed (R1 has no WPA1)


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
            await self.emit(f"[{wlan_name}] Reusing R1's '{r1_name}'")
            if plan_extra.get("set_passphrase"):
                with self.r1_wait_notices(f"[{wlan_name}] "):
                    await self._set_sz_passphrase(inputs.network_id, plan_extra, r1_name, wlan_name)
            await self.record_change("reused", "wifi_networks", r1_name, resource_id=inputs.network_id)
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
            with self.r1_wait_notices(f"[{wlan_name}] "):
                return await self._create_wispr(plan_extra, r1_name, wlan_name)

        # ── Create network based on type ──────────────────────────────
        await self.emit(f"[{wlan_name}] Creating {r1_type} network"
                        + (f" as '{r1_name}' (SSID '{ssid}')" if r1_name != wlan_name else "") + "...")

        async def create():
            if r1_type == "psk":
                return await self._create_psk_network(plan_extra, r1_name, ssid, vlan_id)
            if r1_type == "open":
                return await self._create_open_network(plan_extra, r1_name, ssid, vlan_id)
            if r1_type == "aaa":
                return await self._create_aaa_network(plan_extra, r1_name, ssid, vlan_id)
            if r1_type == "dpsk":
                return await self._create_dpsk_network(plan_extra, r1_name, ssid, vlan_id)
            if r1_type == "dpsk_external":
                return await self._create_dpsk_network(plan_extra, r1_name, ssid, vlan_id, use_dpsk_service=False)
            raise ValueError(f"Unsupported R1 network type: {r1_type}")

        if r1_type not in ("psk", "open", "aaa", "dpsk", "dpsk_external"):
            raise RuntimeError(f"Unsupported R1 network type: {r1_type}")
        # R1's reason, not the HTTP wrapper ("400 Client Error: Bad Request for url").
        with self.r1_wait_notices(f"[{wlan_name}] "):
            net_id = await self._create_resilient(create, r1_name, wlan_name,
                                                  refused=lambda e: f"R1 refused the network: {api_error(e)}")
        if not net_id:
            raise RuntimeError(f"Network creation returned no ID for '{wlan_name}' — R1 accepted it but it "
                               "wasn't findable by name after ~65s; a re-run will pick it up if it landed")

        await self.track_resource("wifi_networks", {
            "id": net_id, "name": r1_name, "type": r1_type,
        })
        await self.emit(f"[{wlan_name}] Created {r1_type} network (id={net_id[:12]})", "success")

        # Link RADIUS profile: Enterprise, and external DPSK (keys checked by it)
        if r1_type in ("aaa", "dpsk_external"):
            await self._link_radius(plan_extra, net_id, wlan_name)

        return self.Outputs(network_id=net_id, reused=False)

    async def _set_sz_passphrase(self, network_id: str, extra: Dict[str, Any], r1_name: str, wlan_name: str):
        """
        Put SZ's passphrase on a reused R1 PSK network (chosen on Review), then
        read it back: the WLAN fails unless R1 then carries SZ's passphrase.
        R1's security type is left as it is; only the key fields change.
        """
        encryption = extra.get("encryption") or {}
        _, main, sae = psk_security(encryption)
        if not main:
            raise RuntimeError(f"[{wlan_name}] SZ gave no passphrase to set on R1's '{r1_name}' — left unchanged")
        network = await self.r1_client.networks.get_wifi_network_by_id(network_id, self.tenant_id)
        wlan = (network or {}).get("wlan")
        if not network or wlan is None:
            raise RuntimeError(f"[{wlan_name}] Couldn't read R1's '{r1_name}' to set its passphrase")
        security = str(wlan.get("wlanSecurity") or "")
        if security == "WPA3":
            wlan["saePassphrase"] = main
            if wlan.get("passphrase"):
                wlan["passphrase"] = main
        else:
            wlan["passphrase"] = main
            if "WPA23" in security.upper():
                wlan["saePassphrase"] = sae or main
        await self.emit(f"[{wlan_name}] Setting SZ's passphrase on R1's '{r1_name}'...")
        try:
            await self.r1_client.networks.update_wifi_network(
                network_id, network, tenant_id=self.tenant_id, wait_for_completion=True)
        except Exception as e:
            raise RuntimeError(f"[{wlan_name}] R1 refused SZ's passphrase on '{r1_name}': {api_error(e)}")
        finally:
            network = wlan = None
        after = await self.r1_client.networks.get_wifi_network_by_id(network_id, self.tenant_id)
        check = compare_passphrases(encryption, (after or {}).get("wlan") or {})
        after = None
        if check["status"] != "match":
            raise RuntimeError(f"[{wlan_name}] Set SZ's passphrase on R1's '{r1_name}', but R1 doesn't show it "
                               f"({check['note'] or check['status']}) — check the network in R1")
        await self.emit(f"[{wlan_name}] R1's '{r1_name}' now has SZ's passphrase", "success")
        await self.record_change("updated", "wifi_networks", r1_name, resource_id=network_id,
                                 detail="passphrase set to SZ's")

    async def _find_settled(self, r1_name: str) -> Optional[str]:
        """The network's id by exact name, re-read while R1's query index catches up."""
        for d in (0,) + FIND_DELAYS:
            if d:
                await asyncio.sleep(d)
            found = await self.r1_client.networks.find_wifi_network_by_name(self.tenant_id, self.venue_id, r1_name)
            if found and found.get("id"):
                return found["id"]
        return None

    async def _create_resilient(self, create, r1_name: str, wlan_name: str, refused) -> Optional[str]:
        """
        create() -> the new network's id, or None if R1 accepted it but it never
        became findable. A task still running when the ~5 min wait ended is
        never re-sent: R1 has the create, it's only slow -- found by name, or
        the WLAN is skipped for a re-run to pick up. A transient refusal (503,
        busy, a task R1 FAILED as busy) is checked against R1 first -- the
        create may have landed -- then retried; any other refusal raises
        refused(e) at once.
        """
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                network = await create()
            except Exception as e:
                if still_running(e):
                    net_id = await self._find_settled(r1_name)
                    if net_id:
                        await self.emit(f"[{wlan_name}] R1 finished it while we looked (id={net_id[:12]})")
                        return net_id
                    raise RuntimeError(
                        f"R1 accepted the network but was still creating it after 5 min ({api_error(e)}) — "
                        "skipped, not re-sent; re-run once R1 shows it and it will be reused") from e
                if not is_transient(e):
                    # A retry refused because the timed-out first create landed meanwhile ("already exists")
                    if attempt:
                        found = await self.r1_client.networks.find_wifi_network_by_name(
                            self.tenant_id, self.venue_id, r1_name)
                        if found and found.get("id"):
                            await self.emit(f"[{wlan_name}] The earlier create landed (id={found['id'][:12]})")
                            return found["id"]
                    raise RuntimeError(refused(e)) from e
                await self.emit(f"[{wlan_name}] R1 was slow or busy ({api_error(e)[:120]}) — checking whether "
                                "the network landed anyway...", "warning")
                net_id = await self._find_settled(r1_name)
                if net_id:
                    await self.emit(f"[{wlan_name}] It did (id={net_id[:12]})")
                    return net_id
                if attempt == len(RETRY_DELAYS):
                    raise RuntimeError(refused(e) + f" (tried {attempt + 1} times)") from e
                await self.emit(f"[{wlan_name}] Not there — trying again in {RETRY_DELAYS[attempt]}s", "warning")
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            net_id = (network or {}).get("id")
            if net_id:
                return net_id
            # Task done, but the name lookup inside create() ran before R1's index had it.
            return await self._find_settled(r1_name)
        return None

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

        key_note = ""
        if not spec.get("wispr_key"):
            # SZ's API never returns the signing key; use the customer's key
            # saved on the R1 controller, if any (none = no key, as before).
            spec["wispr_key"] = controller_portal_key(job.controller_id)
            key_note = ", integration key from the R1 controller" if spec["wispr_key"] else ", no integration key"
        payload = build_wispr_payload(spec, integration_key=spec.get("wispr_key"),
                                      description=extra.get("sz_description") or None)
        payload["name"] = name                     # the plan's R1 name
        payload["wlan"]["ssid"] = extra.get("ssid") or payload["wlan"]["ssid"]   # SZ's, edges trimmed for R1
        encrypted = bool(spec.get("wispr_key"))
        spec["wispr_key"] = None
        await self.emit(f"[{wlan_name}] Creating WISPr guest network"
                        + (f" as '{name}'" if name != wlan_name else "")
                        + f" ({'RADIUS auth' if radius_auth else 'Always Accept'}{key_note})...")
        try:
            net_id = await self._create_resilient(
                lambda: self.r1_client.networks.create_wifi_network_from_payload(self.tenant_id, payload),
                name, wlan_name, refused=lambda e: f"[{wlan_name}] R1 refused the WISPr network: {api_error(e)}")
        finally:
            payload = None                         # carries the integration key
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
            # Like for like: a portal network without SZ's RADIUS (or its proxy
            # setting) authenticates differently from SZ -- fail the WLAN, loudly.
            # (A re-run finds the network by name and won't redo this.)
            missing = []
            for label, pid in profiles:
                try:
                    await self.r1_client.radius_profiles.link_radius_to_network(
                        network_id=net_id, radius_profile_id=pid, tenant_id=self.tenant_id)
                except Exception as e:
                    missing.append(f"RADIUS {label} profile not linked ({api_error(e)})")
            settings = wispr_radius_settings(spec)
            if settings:
                try:
                    await self.r1_client.networks.update_radius_settings_on_network(net_id, settings, self.tenant_id)
                except Exception as e:
                    missing.append(f"RADIUS proxy setting not applied ({api_error(e)})")
            if missing:
                raise RuntimeError(f"[{wlan_name}] WISPr network '{name}' was created, but: {'; '.join(missing)} — "
                                   "guests can't authenticate as on SZ. Fix it on the network in R1, or delete the "
                                   "network and run Create WLANs again")
        return self.Outputs(network_id=net_id, reused=False)

    async def _create_psk_network(self, extra, name, ssid, vlan_id):
        """
        Create a PSK network with SZ's own passphrase. Never a placeholder:
        no passphrase from SZ fails this WLAN for the operator to resolve.
        """
        encryption = extra.get("encryption") or {}
        label = extra.get("sz_wlan_name") or name
        security_type, passphrase, sae = psk_security(encryption)
        if not passphrase:
            raise RuntimeError(
                f"[{label}] SZ gave no passphrase for this PSK WLAN — not created. "
                "Re-run the extraction (the SZ admin must be allowed to read WLAN passphrases), "
                "or set the WLAN's passphrase in SZ, then run Create WLANs again")

        return await self.r1_client.networks.create_wifi_network(
            sae_passphrase=sae,
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
