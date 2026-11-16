"""
V2 Phase: Create the WLANs the list names but R1 does not have (WLAN Activator).

For a venue whose AP Groups were built out but whose unit WLANs never
existed: validation plans each missing WLAN (networks_to_create) when the
user ticked "create missing WLANs", and this phase creates them -- once each,
before anything is linked or activated -- so activate_wlans finds them.

    DPSK  network + link the chosen pool (existing, or one made here first:
          identity group + pool of the same name, as the WLAN Grabber does)
    PSK   network with the row's passphrase (WPA2 / WPA3 / mixed)
    Open  network

Name = SSID = the list's wlan value. The network VLAN is the first row's
vlan for it, else the default from the create options.

PSK passphrases never enter the job: the plan endpoint puts them in their
own Redis hash (SECRET_KEY, TTL), each read here once and deleted.

A WLAN that fails to create fails only itself: its name goes to
create_failures and activate_wlans fails the AP Groups that wanted it, with
the reason. A create R1 accepted but had not finished after the wait is NOT
re-sent (re-planning finds it once it lands).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from r1api.constants import DpskPassphraseFormat
from redis_client import get_redis_client
from services.ap_group_ops import api_error, still_running
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.registry import register_phase

logger = logging.getLogger(__name__)

# Redis hash per job: "psk:{wlan name}" -> passphrase.
SECRET_KEY = "wlan_activator:secrets:{job_id}"
SECRET_TTL = 6 * 3600

CREATE_CONCURRENCY = 5

CREATE_TYPES = ("dpsk", "psk", "open")
PSK_SECURITY = ("WPA2", "WPA3", "WPA2/WPA3")


def _rows(resp: Any) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("content") or []
    return []


async def store_psk_secrets(job_id: str, passphrases: Dict[str, str]) -> None:
    """Called by the plan endpoint: wlan name -> passphrase, kept out of the job."""
    if not passphrases:
        return
    redis = await get_redis_client()
    key = SECRET_KEY.format(job_id=job_id)
    await redis.hset(key, mapping={f"psk:{name}": pp for name, pp in passphrases.items()})
    await redis.expire(key, SECRET_TTL)


async def _take_secret(job_id: str, field: str) -> Optional[str]:
    redis = await get_redis_client()
    key = SECRET_KEY.format(job_id=job_id)
    raw = await redis.hget(key, field)
    await redis.hdel(key, field)
    return raw.decode() if isinstance(raw, bytes) else raw


async def drop_secrets(job_id: str) -> None:
    redis = await get_redis_client()
    await redis.delete(SECRET_KEY.format(job_id=job_id))


async def find_pool(r1_client, tenant_id: str, name: str) -> Optional[str]:
    resp = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id, search_string=name, page=1, limit=100)
    for p in _rows(resp):
        if p.get("name") == name and p.get("id"):
            return p["id"]
    return None


@register_phase("create_missing_wlans", "Create Missing WLANs")
class CreateMissingWlansPhase(PhaseExecutor):
    """Create each planned network once; DPSK ones get their pool linked."""

    class Inputs(BaseModel):
        # [{name, ssid, type, vlan}] from validation
        networks_to_create: List[Dict[str, Any]] = Field(default_factory=list)
        # Job option: {type, security, dpsk_pool: {mode: existing|new, pool_id, name}}
        create_missing: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        created_networks: Dict[str, str] = Field(default_factory=dict)   # name -> network id
        create_failures: Dict[str, str] = Field(default_factory=dict)    # name -> reason
        created_dpsk_pool_id: Optional[str] = None
        networks_created: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        todo = inputs.networks_to_create
        if not todo:
            return self.Outputs()
        try:
            return await self._run(inputs, todo)
        finally:
            await drop_secrets(self.job_id)

    async def _run(self, inputs: 'Inputs', todo: List[Dict[str, Any]]) -> 'Outputs':
        opts = inputs.create_missing or {}
        failures: Dict[str, str] = {}
        created: Dict[str, str] = {}

        # ---- the DPSK pool, once ---------------------------------------
        pool_id: Optional[str] = None
        if any(n["type"] == "dpsk" for n in todo):
            try:
                pool_id = await self._pool(opts.get("dpsk_pool") or {})
            except Exception as e:
                reason = f"DPSK pool not available: {api_error(e)}"
                await self.emit(reason + " — its DPSK WLANs are not created", "error")
                for n in todo:
                    if n["type"] == "dpsk":
                        failures[n["name"]] = reason
                todo = [n for n in todo if n["type"] != "dpsk"]

        # ---- one tenant read: anything that appeared since the plan -----
        try:
            resp = await self.r1_client.networks.get_wifi_networks(self.tenant_id)
            have = {n.get("name"): n.get("id") for n in _rows(resp) if n.get("name")}
        except Exception as e:
            raise RuntimeError(f"Could not list WiFi networks: {api_error(e)}")

        total = len(todo)
        await self.emit(f"Creating {total} WLAN(s)...")
        sem = asyncio.Semaphore(CREATE_CONCURRENCY)
        done = 0

        async def one(n: Dict[str, Any]) -> None:
            nonlocal done
            name = n["name"]
            async with sem:
                try:
                    if have.get(name):
                        created[name] = have[name]
                        await self.emit(f"[{name}] Already in R1 — using it")
                        await self.record_change("reused", "wifi_networks", name, resource_id=have[name])
                    else:
                        created[name] = await self._create(n, opts, pool_id)
                except Exception as e:
                    reason = api_error(e)
                    if still_running(e):
                        reason = "R1 accepted the create but had not finished; re-plan once it lands"
                    failures[name] = reason
                    await self.emit(f"[{name}] Not created: {reason}", "error")
                    await self.record_change("failed", "wifi_networks", name, detail=reason)
                finally:
                    done += 1
                    await self.emit_progress(done, total, failed=len(failures), item_name="WLAN")

        with self.r1_wait_notices("Creating WLANs: "):
            await asyncio.gather(*(one(n) for n in todo))

        made = len(created) - sum(1 for n in todo if have.get(n["name"]))
        await self.emit(
            f"{made} WLAN(s) created" + (f", {len(failures)} failed" if failures else ""),
            "warning" if failures else "success",
        )
        return self.Outputs(
            created_networks=created,
            create_failures=failures,
            created_dpsk_pool_id=pool_id,
            networks_created=made,
        )

    async def _pool(self, pool: Dict[str, Any]) -> str:
        if pool.get("mode") == "existing":
            if not pool.get("pool_id"):
                raise RuntimeError("no pool chosen")
            return pool["pool_id"]
        name = (pool.get("name") or "").strip()
        if not name:
            raise RuntimeError("no name for the new pool")
        found = await find_pool(self.r1_client, self.tenant_id, name)
        if found:
            await self.emit(f"DPSK pool '{name}' already exists — linking it unchanged", "warning")
            return found

        await self.emit(f"Creating identity group '{name}'...")
        group = await self.r1_client.identity.create_identity_group(tenant_id=self.tenant_id, name=name)
        group_id = (group or {}).get("id")
        if not group_id:
            raise RuntimeError(f"identity group '{name}' was not created (no id returned)")
        await self.track_resource("identity_groups", {"id": group_id, "name": name})

        await self.emit(f"Creating DPSK pool '{name}'...")
        created = await self.r1_client.dpsk.create_dpsk_pool(
            identity_group_id=group_id, name=name, tenant_id=self.tenant_id,
            passphrase_length=12, passphrase_format=DpskPassphraseFormat.KEYBOARD_FRIENDLY,
            max_devices_per_passphrase=0,   # unlimited
        )
        if (created or {}).get("requestId"):
            await self.r1_client.await_task_completion(
                request_id=created["requestId"], override_tenant_id=self.tenant_id)
        pool_id = (created or {}).get("id") or await find_pool(self.r1_client, self.tenant_id, name)
        if not pool_id:
            raise RuntimeError(f"DPSK pool '{name}' was created but could not be found")
        await self.track_resource("dpsk_pools", {"id": pool_id, "name": name})
        await self.emit(f"Created DPSK pool '{name}'", "success")
        return pool_id

    async def _create(self, n: Dict[str, Any], opts: Dict[str, Any], pool_id: Optional[str]) -> str:
        name, kind = n["name"], n["type"]
        common = dict(tenant_id=self.tenant_id, venue_id=None, name=name,
                      ssid=n.get("ssid") or name, vlan_id=n.get("vlan") or 1)
        nets = self.r1_client.networks
        if kind == "dpsk":
            network = await nets.create_dpsk_wifi_network(**common)
        elif kind == "psk":
            passphrase = await _take_secret(self.job_id, f"psk:{name}")
            if not passphrase:
                raise RuntimeError("passphrase no longer available (it expires 6 hours after planning) — re-plan")
            try:
                network = await nets.create_wifi_network(
                    passphrase=passphrase, security_type=opts.get("security") or "WPA2", **common)
            finally:
                passphrase = None
        elif kind == "open":
            network = await nets.create_open_wifi_network(**common)
        else:
            raise RuntimeError(f"unsupported WLAN type {kind}")
        net_id = (network or {}).get("id")
        if not net_id:
            raise RuntimeError("R1 accepted the create but the network was not found")
        await self.track_resource("wifi_networks", {"id": net_id, "name": name, "type": kind})

        if kind == "dpsk":
            # Without the link R1 refuses to activate it (WIFI-20300), so a
            # failed link fails this WLAN, though the network now exists.
            try:
                await nets.activate_dpsk_service_on_network(
                    tenant_id=self.tenant_id, network_id=net_id, dpsk_service_id=pool_id)
            except Exception as e:
                raise RuntimeError(f"created, but the DPSK pool was not linked: {api_error(e)}")
        await self.emit(f"[{name}] Created {kind.upper()} network", "success")
        return net_id

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(
            valid=True,
            will_create=bool(inputs.networks_to_create),
            estimated_api_calls=2 * len(inputs.networks_to_create),
            notes=["Create each missing WLAN once, before any activation"],
        )
