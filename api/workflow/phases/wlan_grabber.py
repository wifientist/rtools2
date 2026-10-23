"""
V2 Phases: WLAN Grabber -- create in R1 the SZ WLANs it does not have yet.

    validate_wlan_grabber    (global)   re-check names in R1, one unit per WLAN
    grabber_radius_profiles  (global)   RADIUS profiles for Enterprise WLANs
    grabber_dpsk_pool        (global)   the shared DPSK pool, created once
    grabber_create_network   (per WLAN) create the network, link RADIUS / pool

The plan endpoint (routers/wlan_grabber) reads SmartZone -- phases get no SZ
client -- and hands over each WLAN's spec (services/wlan_grabber.build_spec)
minus its passphrase. Secrets never enter the job: PSK passphrases and typed
RADIUS secrets sit in their own Redis hash (SECRET_KEY) with a TTL, and the
phase that uses one deletes it.

A RADIUS profile or the shared pool that fails to create does not fail the
job: the units that need it fail with the reason, the rest carry on.

WISPr guest networks are built from SZ (services/wlan_grabber.py). The
integration key is the WLAN's SZ signature signing key (put in the secret
hash by the plan; none = no key), unless the user chose to copy one from an
R1 WISPr network (read with the R1 client when the unit runs) or typed it.
Either way it never enters the job or its results.
Networks are left unactivated (WLAN Activator does that).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from r1api.constants import DpskPassphraseFormat
from redis_client import get_redis_client
from services.wlan_grabber import (
    build_wispr_payload, is_wispr_network, wispr_integration_key, wispr_radius_settings,
)
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.registry import register_phase
from workflow.v2.models import (
    ConflictDetail, ResourceAction, UnitMapping, UnitPlan, UnitStatus,
    ValidationResult, ValidationSummary,
)

logger = logging.getLogger(__name__)

# Redis hash per job: "psk:{sz_wlan_id}" and "radius:{sz_service_id}" -> secret.
SECRET_KEY = "wlan_grabber:secrets:{job_id}"
SECRET_TTL = 6 * 3600


def _sample(items: List[str], n: int = 6) -> str:
    return ", ".join(items[:n]) + (f" …+{len(items) - n}" if len(items) > n else "")


def _rows(resp: Any) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("content") or []
    return []


def _api_error(e: Exception) -> str:
    """The R1 error message, not the httpx wrapper around it."""
    response = getattr(e, "response", None)
    if response is not None:
        try:
            errors = response.json().get("errors") or []
            if errors:
                return "; ".join(err.get("message") or err.get("reason") or str(err) for err in errors)
        except Exception:
            pass
    return str(e)


async def _take_secret(job_id: str, field: str) -> Optional[str]:
    """Read a secret once: it is deleted as it is read."""
    redis = await get_redis_client()
    key = SECRET_KEY.format(job_id=job_id)
    raw = await redis.hget(key, field)
    await redis.hdel(key, field)
    return raw.decode() if isinstance(raw, bytes) else raw


async def _find_pool(r1_client, tenant_id: str, name: str) -> Optional[str]:
    resp = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id, search_string=name, page=1, limit=100)
    for p in _rows(resp):
        if p.get("name") == name and p.get("id"):
            return p["id"]
    return None


# "{job_id}" -> the integration key copied from an R1 network, read once per job.
# In-process: every unit of a job runs in the one Brain that executes it.
_keys: Dict[str, str] = {}
_key_locks: Dict[str, asyncio.Lock] = {}


async def _wispr_key(r1_client, tenant_id: str, job_id: str, wispr: Dict[str, Any],
                     wlan_id: str) -> Optional[str]:
    source = wispr.get("key_source") or "sz"
    if source == "sz":
        # The WLAN's own SZ signing key; absent = SZ does not sign = no key.
        return await _take_secret(job_id, f"wispr_key:{wlan_id}")
    if source == "typed":
        redis = await get_redis_client()
        raw = await redis.hget(SECRET_KEY.format(job_id=job_id), "wispr_key")   # shared by every unit
        key = raw.decode() if isinstance(raw, bytes) else raw
        if not key:
            raise RuntimeError("Integration key no longer available (it expires 6 hours after planning) — re-plan")
        return key
    lock = _key_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        if job_id not in _keys:
            source = await r1_client.networks.get_wifi_network_by_id(wispr.get("key_network_id"), tenant_id)
            key = wispr_integration_key(source or {})
            if not key:
                raise RuntimeError("The R1 network to copy the integration key from has none — re-plan")
            _keys[job_id] = key
        return _keys[job_id]


def _needs_radius(spec: Dict[str, Any]) -> bool:
    return spec.get("r1_type") == "aaa" or (
        spec.get("r1_type") == "wispr" and (spec.get("portal") or {}).get("auth_type") == "RADIUS")


def _uses_shared_pool(spec: Dict[str, Any], choice: Optional[str]) -> bool:
    return spec.get("r1_type") == "dpsk" and choice is None


# ── Validate & plan ────────────────────────────────────────────────────

@register_phase("validate_wlan_grabber", "Validate & Plan WLAN Grabber")
class ValidateWlanGrabberPhase(PhaseExecutor):
    """One unit per WLAN that R1 does not have yet."""

    class Inputs(BaseModel):
        wlans: List[Dict[str, Any]] = Field(default_factory=list)
        radius: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
        dpsk_pool: Dict[str, Any] = Field(default_factory=dict)
        wispr: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: ValidationResult
        networks_existing: List[str] = Field(default_factory=list)
        wlans_skipped: List[Dict[str, str]] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        await self.emit(f"Planning {len(inputs.wlans)} WLANs...")
        try:
            resp = await self.r1_client.networks.get_wifi_networks(self.tenant_id)
            networks = resp.get("data", []) if isinstance(resp, dict) else resp
        except Exception as e:
            # A partial view would create duplicates -- refuse instead.
            raise RuntimeError(f"Could not list WiFi networks: {e}")
        taken = {(n.get("name") or "").lower(): n for n in networks if n.get("name")}
        await self.emit(f"Tenant has {len(networks)} networks")

        conflicts: List[ConflictDetail] = []
        units: Dict[str, UnitMapping] = {}
        existing: List[str] = []
        skipped: List[Dict[str, str]] = []
        by_type: Dict[str, List[str]] = {}
        shared_users: List[str] = []
        radius_used: Dict[str, List[str]] = {}

        for w in inputs.wlans:
            spec, choice = w.get("spec") or {}, w.get("dpsk_pool")
            name = spec.get("sz_name") or ""
            if spec.get("blockers"):
                skipped.append({"wlan": name, "reason": "; ".join(spec["blockers"])})
                continue
            if name.lower() in taken:
                existing.append(name)
                continue
            if _needs_radius(spec):
                sid = spec.get("auth_service_id")
                d = inputs.radius.get(sid or "") or {}
                if d.get("action") not in ("create", "existing"):
                    skipped.append({"wlan": name, "reason": "Its RADIUS authentication service is not mapped"})
                    continue
                for s in (sid, spec.get("accounting_service_id")):
                    if s and (inputs.radius.get(s) or {}).get("action") in ("create", "existing"):
                        radius_used.setdefault(s, []).append(name)
            if _uses_shared_pool(spec, choice):
                shared_users.append(name)

            by_type.setdefault(spec.get("r1_type") or "?", []).append(name)
            unit_id = f"wlan_{spec.get('sz_wlan_id')}"
            units[unit_id] = UnitMapping(
                unit_id=unit_id,
                unit_number=name,
                plan=UnitPlan(
                    network_name=name,
                    ssid_name=spec.get("sz_ssid"),
                    will_create_ap_group=False,
                    will_create_network=True,
                ),
                status=UnitStatus.PENDING,
                input_config={"spec": spec, "dpsk_pool_choice": choice, "wispr": inputs.wispr},
            )

        actions: List[ResourceAction] = []
        labels = {"psk": "PSK", "open": "Open", "aaa": "Enterprise", "dpsk": "DPSK", "wispr": "WISPr guest"}
        for t, names in sorted(by_type.items()):
            actions.append(ResourceAction(
                action="create", resource_type="wifi_networks",
                name=f"{len(names)} {labels.get(t, t)} network{'s' if len(names) != 1 else ''}",
                notes=[_sample(names)],
            ))
        if existing:
            actions.append(ResourceAction(
                action="reuse", resource_type="wifi_networks",
                name=f"{len(existing)} already in R1 (skipped)", notes=[_sample(existing)],
            ))

        # ── WISPr: where the integration key comes from (SZ has none) ──
        wispr_units = [u for u in units.values() if u.input_config["spec"].get("r1_type") == "wispr"]
        if wispr_units:
            w = inputs.wispr or {}
            problem, source_name = None, None
            if (w.get("key_source") or "sz") == "sz":
                source_name = "from each WLAN's SZ signing key (none set = no key)"
            elif w.get("key_source") == "typed":
                redis = await get_redis_client()
                if not await redis.hexists(SECRET_KEY.format(job_id=self.job_id), "wispr_key"):
                    problem = "The typed integration key did not reach the plan — re-plan"
                source_name = "typed"
            elif w.get("key_network_id"):
                try:
                    source = await self.r1_client.networks.get_wifi_network_by_id(w["key_network_id"], self.tenant_id)
                except Exception as e:
                    source = None
                    logger.warning(f"[WLAN Grabber] Could not read key source {w['key_network_id']}: {e}")
                if not source or not wispr_integration_key(source):
                    problem = "The R1 network chosen to copy the integration key from has none"
                source_name = f"copied from '{(source or {}).get('name')}'"
            else:
                problem = "No integration key source chosen"
            if problem:
                conflicts.append(ConflictDetail(
                    resource_type="wispr", resource_name=f"{len(wispr_units)} WISPr network(s)",
                    description=problem, severity="error"))
            else:
                radius_auth = sum(1 for u in wispr_units
                                  if (u.input_config["spec"].get("portal") or {}).get("auth_type") == "RADIUS")
                actions.append(ResourceAction(
                    action="configure", resource_type="wispr_portal", name=f"{len(wispr_units)} WISPr portal(s)",
                    notes=[f"Portal, walled garden, sessions and VLAN from SZ; integration key {source_name}; "
                           f"MAC & IP encrypted when there is a key; "
                           f"{radius_auth} with RADIUS auth, {len(wispr_units) - radius_auth} Always Accept"],
                ))

        # ── RADIUS profiles: what the radius phase will create or link ──
        radius_create = 0
        for sid, names in radius_used.items():
            d = inputs.radius[sid]
            label = d.get("name") or sid[:8]
            if d["action"] == "existing":
                actions.append(ResourceAction(action="reuse", resource_type="radius_profile", name=label,
                                              existing_id=d.get("r1_profile_id"),
                                              notes=[f"Used by {_sample(names, 3)}"]))
            else:
                radius_create += 1
                actions.append(ResourceAction(action="create", resource_type="radius_profile", name=label,
                                              notes=[f"From SZ servers; used by {_sample(names, 3)}"]))

        # ── The shared DPSK pool ──
        pool = inputs.dpsk_pool or {}
        pools_to_create = 0
        if shared_users:
            mode = pool.get("mode")
            if mode == "new":
                found = None
                try:
                    found = await _find_pool(self.r1_client, self.tenant_id, pool.get("name") or "")
                except Exception as e:
                    logger.warning(f"[WLAN Grabber] Could not check DPSK pool names: {e}")
                if found:
                    actions.append(ResourceAction(action="reuse", resource_type="dpsk_pool", name=pool["name"],
                                                  existing_id=found,
                                                  notes=["A pool by this name already exists — linked unchanged"]))
                else:
                    pools_to_create = 1
                    actions.append(ResourceAction(action="create", resource_type="dpsk_pool", name=pool["name"],
                                                  notes=[f"Plus identity group '{pool['name']}'; "
                                                         f"shared by {len(shared_users)} DPSK network(s)"]))
            elif mode == "existing":
                actions.append(ResourceAction(action="reuse", resource_type="dpsk_pool",
                                              name=pool.get("pool_name") or pool.get("pool_id") or "",
                                              existing_id=pool.get("pool_id"),
                                              notes=[f"Shared by {len(shared_users)} DPSK network(s)"]))
            else:
                conflicts.append(ConflictDetail(
                    resource_type="dpsk_pool", resource_name=f"{len(shared_users)} DPSK network(s)",
                    description="No shared DPSK pool: these are created unlinked, and R1 will not "
                                "activate them until a pool is linked (WIFI-20300).",
                    severity="warning",
                ))

        if skipped:
            conflicts.append(ConflictDetail(
                resource_type="wifi_networks", resource_name=f"{len(skipped)} WLAN(s) skipped",
                description="; ".join(f"{s['wlan']}: {s['reason']}" for s in skipped[:5])
                            + (f" …+{len(skipped) - 5}" if len(skipped) > 5 else ""),
                severity="warning",
            ))
        if not units:
            conflicts.append(ConflictDetail(
                resource_type="wifi_networks", resource_name="nothing to create",
                description="Every chosen WLAN is already in R1 or was skipped.", severity="error",
            ))

        errors = [c for c in conflicts if c.severity == "error"]
        await self.emit(
            f"Plan: {len(units)} network(s) to create"
            + (f", {len(existing)} already in R1" if existing else "")
            + (f", {len(skipped)} skipped" if skipped else ""),
            "warning" if errors else "success",
        )
        return self.Outputs(
            unit_mappings=units,
            networks_existing=existing,
            wlans_skipped=skipped,
            validation_result=ValidationResult(
                valid=bool(units) and not errors,
                conflicts=conflicts,
                summary=ValidationSummary(
                    total_units=len(units),
                    networks_to_create=len(units),
                    networks_to_reuse=len(existing),
                    dpsk_pools_to_create=pools_to_create,
                    identity_groups_to_create=pools_to_create,
                    total_api_calls=3 * len(units) + 2 * radius_create + 3 * pools_to_create,
                ),
                actions=actions,
            ),
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=bool(inputs.wlans), estimated_api_calls=1,
                               notes=["Re-check the WLANs against the tenant's networks"])


# ── RADIUS profiles ────────────────────────────────────────────────────

@register_phase("grabber_radius_profiles", "Create RADIUS Profiles")
class GrabberRadiusProfilesPhase(PhaseExecutor):
    """SZ RADIUS service -> R1 profile id, for the Enterprise units."""

    class Inputs(BaseModel):
        radius: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    class Outputs(BaseModel):
        radius_profile_mappings: Dict[str, str] = Field(default_factory=dict)
        radius_errors: Dict[str, str] = Field(default_factory=dict)
        skipped: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        job = await self.context.state_manager.get_job(self.job_id)
        used = set()
        for u in (job.units.values() if job else []):
            spec = u.input_config.get("spec") or {}
            if _needs_radius(spec):
                used.update(s for s in (spec.get("auth_service_id"), spec.get("accounting_service_id")) if s)
        if not used:
            return self.Outputs(skipped=True)

        mappings: Dict[str, str] = {}
        errors: Dict[str, str] = {}
        for sid in sorted(used):
            d = inputs.radius.get(sid) or {}
            name = d.get("name") or f"radius-{sid[:8]}"
            if d.get("action") == "existing":
                mappings[sid] = d["r1_profile_id"]
                await self.emit(f"RADIUS '{name}' → existing R1 profile")
                continue
            if d.get("action") != "create":
                continue
            secret = await _take_secret(self.job_id, f"radius:{sid}")
            try:
                primary, secondary = d.get("primary"), d.get("secondary")
                if not primary:
                    raise RuntimeError("SZ service has no server address")
                existing = await asyncio.to_thread(
                    self.r1_client.radius_profiles.find_radius_profile_by_name, self.tenant_id, name)
                if existing and existing.get("id"):
                    mappings[sid] = existing["id"]
                    await self.emit(f"RADIUS '{name}' already exists in R1 — linked it; "
                                    "its shared secret was left unchanged", "warning")
                    continue
                if not secret:
                    raise RuntimeError("No shared secret (it expires 6 hours after planning) — re-plan")
                created = await self.r1_client.radius_profiles.create_radius_profile(
                    tenant_id=self.tenant_id, name=name,
                    primary_ip=primary["ip"], primary_port=primary["port"], primary_secret=secret,
                    secondary_ip=secondary["ip"] if secondary else None,
                    secondary_port=secondary["port"] if secondary else 1812,
                    secondary_secret=secret if secondary else "",
                    profile_type=d.get("role") or "AUTHENTICATION",
                )
                if not (created or {}).get("id"):
                    raise RuntimeError("R1 returned no profile id")
                mappings[sid] = created["id"]
                await self.track_resource("radius_profiles", {"id": created["id"], "name": name})
                await self.emit(f"Created RADIUS profile '{name}'", "success")
            except Exception as e:
                errors[sid] = _api_error(e)
                await self.emit(f"RADIUS '{name}' failed: {errors[sid]}", "error")
            finally:
                secret = None
        return self.Outputs(radius_profile_mappings=mappings, radius_errors=errors)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=bool(inputs.radius), estimated_api_calls=2)


# ── The shared DPSK pool ───────────────────────────────────────────────

@register_phase("grabber_dpsk_pool", "Create Shared DPSK Pool")
class GrabberDpskPoolPhase(PhaseExecutor):
    """Create (or find) the shared pool once, before any network needs it."""

    class Inputs(BaseModel):
        dpsk_pool: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        shared_dpsk_pool_id: Optional[str] = None
        shared_dpsk_pool_error: Optional[str] = None
        skipped: bool = False

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        pool = inputs.dpsk_pool or {}
        job = await self.context.state_manager.get_job(self.job_id)
        users = [u for u in (job.units.values() if job else [])
                 if _uses_shared_pool(u.input_config.get("spec") or {}, u.input_config.get("dpsk_pool_choice"))]
        if not users or pool.get("mode") == "none":
            return self.Outputs(skipped=True)
        if pool.get("mode") == "existing":
            return self.Outputs(shared_dpsk_pool_id=pool.get("pool_id"))

        name = pool.get("name") or ""
        try:
            found = await _find_pool(self.r1_client, self.tenant_id, name)
            if found:
                await self.emit(f"DPSK pool '{name}' already exists — linking it unchanged", "warning")
                return self.Outputs(shared_dpsk_pool_id=found)

            await self.emit(f"Creating identity group '{name}'...")
            group = await self.r1_client.identity.create_identity_group(tenant_id=self.tenant_id, name=name)
            group_id = (group or {}).get("id")
            if not group_id:
                raise RuntimeError(f"Identity group '{name}' was not created (no id returned)")
            await self.track_resource("identity_groups", {"id": group_id, "name": name})

            await self.emit(f"Creating DPSK pool '{name}'...")
            fmt = pool.get("passphrase_format") or DpskPassphraseFormat.KEYBOARD_FRIENDLY
            created = await self.r1_client.dpsk.create_dpsk_pool(
                identity_group_id=group_id, name=name, tenant_id=self.tenant_id,
                passphrase_length=pool.get("passphrase_length") or 12, passphrase_format=fmt,
                # 0 = unlimited: create_dpsk_pool then omits deviceCountLimit.
                max_devices_per_passphrase=pool.get("devices_per_passphrase") or 0,
                word_count=4 if fmt == DpskPassphraseFormat.DICTIONARY_WORDS else None,
            )
            if (created or {}).get("requestId"):
                await self.r1_client.await_task_completion(
                    request_id=created["requestId"], override_tenant_id=self.tenant_id)
            pool_id = (created or {}).get("id") or await _find_pool(self.r1_client, self.tenant_id, name)
            if not pool_id:
                raise RuntimeError(f"DPSK pool '{name}' was created but could not be found")
            await self.track_resource("dpsk_pools", {"id": pool_id, "name": name})
            await self.emit(f"Created DPSK pool '{name}'", "success")
            return self.Outputs(shared_dpsk_pool_id=pool_id)
        except Exception as e:
            await self.emit(f"DPSK pool '{name}' failed: {_api_error(e)} — its DPSK networks will fail", "error")
            return self.Outputs(shared_dpsk_pool_error=_api_error(e))

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=(inputs.dpsk_pool or {}).get("mode") == "new",
                               estimated_api_calls=3)


# ── One network per WLAN ───────────────────────────────────────────────

@register_phase("grabber_create_network", "Create WiFi Network")
class GrabberCreateNetworkPhase(PhaseExecutor):
    """Create one network, then link its RADIUS profile(s) or DPSK pool."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        spec: Dict[str, Any]
        dpsk_pool_choice: Optional[str] = None     # None = shared, "none", or a pool id
        radius_profile_mappings: Dict[str, str] = Field(default_factory=dict)
        radius_errors: Dict[str, str] = Field(default_factory=dict)
        shared_dpsk_pool_id: Optional[str] = None
        shared_dpsk_pool_error: Optional[str] = None
        wispr: Dict[str, Any] = Field(default_factory=dict)
        add_description: bool = False

    class Outputs(BaseModel):
        network_id: str = ""
        created: bool = False
        networks_created: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        spec = inputs.spec
        name, r1_type = spec["sz_name"], spec["r1_type"]
        passphrase = await _take_secret(self.job_id, f"psk:{spec['sz_wlan_id']}") if r1_type == "psk" else None
        try:
            return await self._create(inputs, spec, name, r1_type, passphrase)
        finally:
            passphrase = None

    async def _create(self, inputs, spec, name, r1_type, passphrase) -> 'Outputs':
        # Needs resolved before anything is created.
        auth_profile = None
        if r1_type == "aaa":
            sid = spec.get("auth_service_id")
            auth_profile = inputs.radius_profile_mappings.get(sid or "")
            if not auth_profile:
                raise RuntimeError(f"RADIUS profile not available: "
                                   f"{inputs.radius_errors.get(sid or '') or 'not mapped'}")
        pool_id = None
        if r1_type == "dpsk":
            choice = inputs.dpsk_pool_choice
            if choice is None:
                if inputs.shared_dpsk_pool_error:
                    raise RuntimeError(f"Shared DPSK pool was not created: {inputs.shared_dpsk_pool_error}")
                pool_id = inputs.shared_dpsk_pool_id
            elif choice != "none":
                pool_id = choice
        if r1_type == "psk" and not passphrase:
            raise RuntimeError("Passphrase no longer available (it expires 6 hours after planning) — re-plan")

        existing = await self.r1_client.networks.find_wifi_network_by_name(self.tenant_id, None, name)
        if existing and existing.get("id"):
            await self.emit(f"[{name}] Already in R1 — skipped")
            return self.Outputs(network_id=existing["id"])
        if r1_type == "wispr":
            return await self._create_wispr(inputs, spec, name)

        common = dict(
            tenant_id=self.tenant_id, venue_id=None, name=name, ssid=spec["sz_ssid"],
            vlan_id=spec["vlan_id"],
            description=f"Migrated from SZ: {name}" if inputs.add_description else None,
            advanced_customization=spec.get("advanced") or None,
        )
        nets = self.r1_client.networks
        if r1_type == "psk":
            network = await nets.create_wifi_network(passphrase=passphrase, security_type=spec["security"], **common)
        elif r1_type == "open":
            network = await nets.create_open_wifi_network(**common)
        elif r1_type == "aaa":
            network = await nets.create_aaa_wifi_network(security_type=spec["security"], **common)
        elif r1_type == "dpsk":
            network = await nets.create_dpsk_wifi_network(**common)
        else:
            raise RuntimeError(f"Unsupported network type {r1_type}")
        net_id = (network or {}).get("id")
        if not net_id:
            raise RuntimeError("R1 accepted the create but the network was not found")
        await self.track_resource("wifi_networks", {"id": net_id, "name": name, "type": r1_type})
        await self.emit(f"[{name}] Created {r1_type} network", "success")

        # The network exists now: a failed link is a warning, not a failed unit.
        if r1_type == "aaa":
            profiles = [("authentication", auth_profile)]
            acct = inputs.radius_profile_mappings.get(spec.get("accounting_service_id") or "")
            if acct and acct != auth_profile:
                profiles.append(("accounting", acct))
            for label, pid in profiles:
                try:
                    await self.r1_client.radius_profiles.link_radius_to_network(
                        network_id=net_id, radius_profile_id=pid, tenant_id=self.tenant_id)
                except Exception as e:
                    await self.emit(f"[{name}] RADIUS {label} profile not linked: {_api_error(e)}", "warning")
        if r1_type == "dpsk":
            if pool_id:
                try:
                    await nets.activate_dpsk_service_on_network(
                        tenant_id=self.tenant_id, network_id=net_id, dpsk_service_id=pool_id)
                    await self.emit(f"[{name}] Linked DPSK pool")
                except Exception as e:
                    await self.emit(f"[{name}] DPSK pool not linked: {_api_error(e)}", "warning")
            else:
                await self.emit(f"[{name}] No DPSK pool linked — R1 will not activate it until one is", "warning")
        return self.Outputs(network_id=net_id, created=True, networks_created=1)

    async def _create_wispr(self, inputs, spec, name) -> 'Outputs':
        w = inputs.wispr or {}
        facts = spec.get("portal") or {}
        radius_auth = facts.get("auth_type") == "RADIUS"
        auth_profile = None
        if radius_auth:
            sid = spec.get("auth_service_id")
            auth_profile = inputs.radius_profile_mappings.get(sid or "")
            if not auth_profile:
                # Created with RADIUS auth but no server, it would authenticate nobody.
                raise RuntimeError(f"RADIUS profile not available: "
                                   f"{inputs.radius_errors.get(sid or '') or 'not mapped'}")

        key = await _wispr_key(self.r1_client, self.tenant_id, self.job_id, w, spec["sz_wlan_id"])
        payload = build_wispr_payload(
            spec, integration_key=key,
            encrypt_mac_ip=w.get("encrypt_mac_ip"), enable_dhcp=w.get("enable_dhcp", False),
            description=f"Migrated from SZ: {name}" if inputs.add_description else None,
        )
        encrypted, key = bool(key), None
        network = await self.r1_client.networks.create_wifi_network_from_payload(self.tenant_id, payload)
        payload = None   # carries the integration key
        net_id = (network or {}).get("id")
        if not net_id:
            raise RuntimeError("R1 accepted the create but the network was not found")
        await self.track_resource("wifi_networks", {"id": net_id, "name": name, "type": "guest"})
        await self.emit(f"[{name}] Created WISPr guest network "
                        f"({'RADIUS auth' if radius_auth else 'Always Accept'}, "
                        f"{'integration key set' if encrypted else 'no integration key'})", "success")

        if radius_auth:
            profiles = [("authentication", auth_profile)]
            acct = inputs.radius_profile_mappings.get(spec.get("accounting_service_id") or "")
            if acct and acct != auth_profile:
                profiles.append(("accounting", acct))
            for label, pid in profiles:
                try:
                    await self.r1_client.radius_profiles.link_radius_to_network(
                        network_id=net_id, radius_profile_id=pid, tenant_id=self.tenant_id)
                except Exception as e:
                    await self.emit(f"[{name}] RADIUS {label} profile not linked: {_api_error(e)}", "warning")
            settings = wispr_radius_settings(spec)
            if settings:
                try:
                    await self.r1_client.networks.update_radius_settings_on_network(net_id, settings, self.tenant_id)
                except Exception as e:
                    await self.emit(f"[{name}] RADIUS proxy setting not applied: {_api_error(e)}", "warning")
        return self.Outputs(network_id=net_id, created=True, networks_created=1)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=True, estimated_api_calls=3)
