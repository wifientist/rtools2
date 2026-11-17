"""
V2 Phases: AP Port Grabber -- give R1 APs the LAN ports and PoE they have on SZ.

    validate_ap_port_grabber  (global)   re-check the profile choices, one unit per AP
    grabber_port_profiles     (global)   create the Ethernet Port Profiles chosen as "create"
    grabber_apply_ap_ports    (per AP)   write the AP's own NON-UPLINK ports (uplink re-checked live)

The plan endpoint (routers/ap_port_grabber) reads SmartZone and R1 -- phases
get no SZ client -- and hands each AP over as services.ap_port_grabber.unit_spec.

Per AP, R1 calls (all through lan_ports._write, which waits on R1's task):
    PUT  /venues/{v}/aps/{serial}/lanPortSpecificSettings      useVenueSettings, poeMode, poeOut
    PUT  /venues/{v}/aps/{serial}/lanPorts/{p}/ethernetPortProfiles/{id}
    PUT  /venues/{v}/aps/{serial}/lanPorts/{p}/settings         enabled + overwriteUntagId / overwriteVlanMembers
The VLANs are always written as the port's override, so the result is SZ's
whichever profile (of the same type) the port got. Activating a profile
clears the port's override, so settings are read after it.

An AP that comes off venue settings has every port written; one already on
its own settings only the ports that differ. Enabled trunk ports go first:
some models need one enabled trunk port at all times.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from redis_client import get_redis_client
from services.ap_port_grabber import (
    AUTH_LABEL, TYPE_LABEL, _name_key, creatable, norm_serial, port_sig, sig, uplink_ports,
)
from services.sz_migration import lan_ports
from services.sz_migration.venue_create import model_capabilities
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.registry import register_phase
from workflow.v2.models import (
    ConflictDetail, ResourceAction, UnitMapping, UnitPlan, UnitStatus,
    ValidationResult, ValidationSummary,
)

logger = logging.getLogger(__name__)


def _sample(items: List[str], n: int = 6) -> str:
    return ", ".join(items[:n]) + (f" …+{len(items) - n}" if len(items) > n else "")


def _used_profiles(aps: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """SZ profile id -> names of the APs whose written ports use it."""
    used: Dict[str, List[str]] = {}
    for ap in aps:
        flip = ap.get("r1_use_venue") is not False
        for p in ap.get("ports") or []:
            if p.get("sz_profile_id") and (flip or p.get("changed")):
                used.setdefault(p["sz_profile_id"], [])
                if ap["sz_name"] not in used[p["sz_profile_id"]]:
                    used[p["sz_profile_id"]].append(ap["sz_name"])
    return used


def _sz_sig(d: Dict[str, Any]):
    return sig(d.get("type"), d.get("untagId"), d.get("vlanMembers"))


# ── Validate & plan ────────────────────────────────────────────────────

@register_phase("validate_ap_port_grabber", "Validate & Plan AP Port Grabber")
class ValidateApPortGrabberPhase(PhaseExecutor):
    """One unit per AP; every profile its ports need resolves to an R1 one."""

    class Inputs(BaseModel):
        aps: List[Dict[str, Any]] = Field(default_factory=list)
        profiles: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: ValidationResult
        profiles_resolved: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        await self.emit(f"Planning {len(inputs.aps)} AP(s)...")
        try:
            r1_profiles = await lan_ports.list_profiles(self.r1_client, self.tenant_id)
        except Exception as e:
            raise RuntimeError(f"Could not list Ethernet Port Profiles: {e}")
        by_id = {p.get("id"): p for p in r1_profiles if p.get("id")}
        by_name = {_name_key(p.get("name")): p for p in r1_profiles if p.get("name")}

        conflicts: List[ConflictDetail] = []
        actions: List[ResourceAction] = []
        resolved: Dict[str, Dict[str, Any]] = {}
        used = _used_profiles(inputs.aps)
        to_create = 0

        for sz_id, ap_names in used.items():
            d = inputs.profiles.get(sz_id) or {}
            label = d.get("sz_name") or sz_id[:8]
            want = _sz_sig(d)
            auth = d.get("auth") or "DISABLED"
            if d.get("action") == "existing":
                prof = by_id.get(d.get("r1_profile_id"))
                if not prof:
                    conflicts.append(ConflictDetail(resource_type="ethernet_port_profile", resource_name=label,
                                                    description="The R1 profile chosen for it no longer exists"))
                    continue
                if port_sig(prof)[0] != want[0]:
                    conflicts.append(ConflictDetail(
                        resource_type="ethernet_port_profile", resource_name=label,
                        description=f"R1 profile '{prof.get('name')}' is not a "
                                    f"{TYPE_LABEL.get(want[0], want[0])} profile"))
                    continue
                if (prof.get("authType") or "DISABLED") != auth:
                    conflicts.append(ConflictDetail(
                        resource_type="ethernet_port_profile", resource_name=label, severity="warning",
                        description=f"R1 profile '{prof.get('name')}' has {AUTH_LABEL.get(prof.get('authType'), prof.get('authType'))}; "
                                    f"SZ's has {AUTH_LABEL.get(auth, auth)}"))
                same = port_sig(prof) == want
                resolved[sz_id] = {"r1_profile_id": prof["id"], "name": prof.get("name"), "create": False}
                actions.append(ResourceAction(
                    action="reuse", resource_type="ethernet_port_profile", name=prof.get("name") or prof["id"],
                    existing_id=prof["id"],
                    notes=[f"For SZ '{label}'" + ("" if same else "; VLANs set on each port")
                           + f" — {_sample(ap_names, 3)}"]))
            elif d.get("action") == "create":
                name = (d.get("name") or "").strip()
                if not 2 <= len(name) <= lan_ports.NAME_MAX:
                    conflicts.append(ConflictDetail(resource_type="ethernet_port_profile", resource_name=label,
                                                    description="A new profile needs a name of 2-32 characters"))
                    continue
                taken = by_name.get(_name_key(name))
                if taken:
                    # By name: a profile of this name and type is the one to use.
                    if port_sig(taken)[0] == want[0]:
                        resolved[sz_id] = {"r1_profile_id": taken["id"], "name": taken.get("name"), "create": False}
                        actions.append(ResourceAction(
                            action="reuse", resource_type="ethernet_port_profile", name=taken.get("name"),
                            existing_id=taken["id"], notes=["Already in R1 under this name — reused; VLANs set on each port"]))
                    else:
                        conflicts.append(ConflictDetail(
                            resource_type="ethernet_port_profile", resource_name=name,
                            description="This name is taken in R1 by a profile of another port type"))
                    continue
                if not creatable(auth):
                    conflicts.append(ConflictDetail(
                        resource_type="ethernet_port_profile", resource_name=name,
                        description=f"Can't create a profile with {AUTH_LABEL.get(auth, auth)} (needs RADIUS settings) — "
                                    "create it in R1 first"))
                    continue
                resolved[sz_id] = {"r1_profile_id": None, "name": name, "create": True, "auth": auth,
                                   "type": want[0], "untagId": want[1], "vlanMembers": want[2]}
                to_create += 1
                actions.append(ResourceAction(
                    action="create", resource_type="ethernet_port_profile", name=name,
                    notes=[f"{TYPE_LABEL.get(want[0], want[0])}, {AUTH_LABEL.get(auth, auth)}, untagged {want[1]}"
                           + (f", members {want[2]}" if want[0] != "ACCESS" else "")
                           + f" — {_sample(ap_names, 3)}"]))
            else:
                conflicts.append(ConflictDetail(resource_type="ethernet_port_profile", resource_name=label,
                                                description="No R1 profile chosen for this SZ profile"))

        units: Dict[str, UnitMapping] = {}
        overrides: List[str] = []
        calls = 0
        for ap in inputs.aps:
            serial = ap.get("serial")
            if not serial:
                continue
            unit_id = f"ap_{serial}"
            if not ap.get("uplink_ports"):
                conflicts.append(ConflictDetail(unit_id=unit_id, resource_type="ap_lan_ports",
                                                resource_name=ap["sz_name"],
                                                description="No uplink port identified — not planned"))
                continue
            overrides.append(ap["sz_name"])
            flip = ap.get("r1_use_venue") is not False
            n_ports = sum(1 for p in ap.get("ports") or [] if flip or p.get("changed"))
            calls += 6 + 3 * n_ports
            units[unit_id] = UnitMapping(
                unit_id=unit_id, unit_number=ap["sz_name"],
                plan=UnitPlan(will_create_ap_group=False, will_create_network=False,
                              lan_port_config={"changes": ap.get("changes") or [],
                                               "uplink_untouched": ap["uplink_ports"]}),
                status=UnitStatus.PENDING,
                input_config={"ap": ap},
            )

        if overrides:
            actions.insert(0, ResourceAction(
                action="configure", resource_type="ap_lan_ports",
                name=f"{len(overrides)} AP(s): non-uplink LAN ports set",
                notes=[_sample(overrides), "Uplink ports are never written; each is re-checked live before "
                       "and after, and any change halts the job"]))
        if not units:
            conflicts.append(ConflictDetail(resource_type="ap_lan_ports", resource_name="nothing to do",
                                            description="None of the chosen APs needs a change."))

        errors = [c for c in conflicts if c.severity == "error"]
        await self.emit(f"Plan: {len(overrides)} AP(s), {to_create} profile(s) to create",
                        "warning" if errors else "success")
        return self.Outputs(
            unit_mappings=units,
            profiles_resolved=resolved,
            validation_result=ValidationResult(
                valid=bool(units) and not errors,
                conflicts=conflicts,
                summary=ValidationSummary(total_units=len(units), total_api_calls=calls + 2 * to_create),
                actions=actions,
            ),
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=bool(inputs.aps), estimated_api_calls=1,
                               notes=["Re-check the chosen Ethernet Port Profiles"])


# ── Ethernet Port Profiles ─────────────────────────────────────────────

@register_phase("grabber_port_profiles", "Create Ethernet Port Profiles")
class GrabberPortProfilesPhase(PhaseExecutor):
    """SZ profile id -> R1 profile id; creates the ones chosen as new."""

    class Inputs(BaseModel):
        profiles_resolved: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    class Outputs(BaseModel):
        profile_ids: Dict[str, str] = Field(default_factory=dict)
        profile_errors: Dict[str, str] = Field(default_factory=dict)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        ids: Dict[str, str] = {}
        errors: Dict[str, str] = {}
        existing: Optional[List[Dict[str, Any]]] = None
        for sz_id, r in inputs.profiles_resolved.items():
            if not r.get("create"):
                ids[sz_id] = r["r1_profile_id"]
                continue
            name = r["name"]
            want = (r.get("type"), r.get("untagId"), r.get("vlanMembers"))
            try:
                if existing is None:
                    existing = await lan_ports.list_profiles(self.r1_client, self.tenant_id)
                # Made by an earlier run of this job? Then it is reused, not duplicated.
                found = next((p for p in existing if _name_key(p.get("name")) == _name_key(name)), None)
                if found and port_sig(found)[0] == want[0]:
                    ids[sz_id] = found["id"]
                    await self.track_resource("ethernet_port_profiles",
                                              {"id": found["id"], "name": name, "reused": True})
                    continue
                if found:
                    raise RuntimeError(f"'{name}' was created in R1 as another port type since the plan")
                body: Dict[str, Any] = {"name": name, "type": want[0], "authType": r.get("auth") or "DISABLED"}
                if want[1] is not None:
                    body["untagId"] = want[1]
                if want[2]:
                    body["vlanMembers"] = want[2]
                with self.r1_wait_notices(f"[{name}] "):
                    data = await lan_ports._write(self.r1_client, self.tenant_id, "post",
                                                  "/ethernetPortProfiles", body)
                new_id = (data.get("response") or {}).get("id") or data.get("id")
                if not new_id:
                    existing = await lan_ports.list_profiles(self.r1_client, self.tenant_id)
                    new_id = next((p["id"] for p in existing if p.get("name") == name), None)
                if not new_id:
                    raise RuntimeError("R1 accepted the profile but it could not be found afterwards")
                ids[sz_id] = new_id
                await self.track_resource("ethernet_port_profiles", {"id": new_id, "name": name})
                await self.emit(f"Created Ethernet Port Profile '{name}'", "success")
            except Exception as e:
                errors[sz_id] = str(e)
                await self.emit(f"Profile '{name}' failed: {e} — the ports that need it will fail", "error")
        return self.Outputs(profile_ids=ids, profile_errors=errors)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        n = sum(1 for r in inputs.profiles_resolved.values() if r.get("create"))
        return PhaseValidation(valid=True, will_create=n > 0, estimated_api_calls=2 * n)


# ── One AP ─────────────────────────────────────────────────────────────
#
# THE UPLINK IS NEVER WRITTEN, and this phase does not take the plan's word
# for which port that is. Before touching an AP it re-reads, live, the AP's
# poePort and R1's model data, and unions them with the plan's uplink ports
# (services.ap_port_grabber.uplink_ports). Any port it was asked to write that
# is in that set fails the AP before a single write.
#
# Coming off venue settings is the one step that could move the uplink
# indirectly (R1 decides what the AP's own ports start as). So the uplink's
# effective setup (on/off, type, VLANs) is read before, re-read right after,
# and on any difference the AP is put straight back on venue settings -- what
# it had -- and the whole job halts. The first AP of a job to come off venue
# settings does so alone; the rest wait until it has been verified.

HALT_KEY = "ap_port_grabber:halt:{job_id}"

# Per job, in-process (every unit of a job runs in the one Brain that executes it).
_ctx: Dict[str, Dict[str, Any]] = {}
_ctx_locks: Dict[str, asyncio.Lock] = {}


async def _halted(job_id: str) -> Optional[str]:
    raw = await (await get_redis_client()).get(HALT_KEY.format(job_id=job_id))
    return raw.decode() if isinstance(raw, bytes) else raw


async def _halt(job_id: str, reason: str) -> None:
    await (await get_redis_client()).set(HALT_KEY.format(job_id=job_id), reason, ex=86400)


@register_phase("grabber_apply_ap_ports", "Apply AP LAN Ports")
class GrabberApplyApPortsPhase(PhaseExecutor):
    """Make one R1 AP's non-uplink LAN ports and PoE what SZ gives it."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        ap: Dict[str, Any]
        profile_ids: Dict[str, str] = Field(default_factory=dict)
        profile_errors: Dict[str, str] = Field(default_factory=dict)

    class Outputs(BaseModel):
        ports_written: int = 0
        aps_updated: int = 0

    # ── job-wide context: read once ──

    async def _job_ctx(self) -> Dict[str, Any]:
        lock = _ctx_locks.setdefault(self.job_id, asyncio.Lock())
        async with lock:
            if self.job_id not in _ctx:
                kw = lan_ports._kw(self.r1_client, self.tenant_id)
                caps = await model_capabilities(self.r1_client, self.venue_id, kw)
                if caps is None:
                    raise RuntimeError("Could not read the venue's AP model capabilities — nothing written")
                aps = (await self.r1_client.venues.get_aps_by_tenant_venue(self.tenant_id, self.venue_id)
                       ).get("data") or []
                _ctx[self.job_id] = {
                    "caps": caps,
                    "aps": {norm_serial(a.get("serialNumber")): a for a in aps if a.get("serialNumber")},
                    "profiles": await lan_ports.list_profiles(self.r1_client, self.tenant_id),
                    "gate": asyncio.Lock(), "proven": False,
                }
            return _ctx[self.job_id]

    # ── the uplink's effective setup ──

    async def _uplink_state(self, ctx, serial: str, model: str, pid: str, on_venue: bool) -> Tuple:
        """(enabled, type, untag, members) as R1 gives it to this AP's port now."""
        cap_port = next((p for p in ctx["caps"][model].get("lanPorts") or [] if str(p.get("id")) == pid), {})
        venue_prof = next((p for p in ctx["profiles"] for a in p.get("venueActivations") or []
                           if a.get("venueId") == self.venue_id and a.get("apModel") == model
                           and str(a.get("portId")) == pid), None)
        venue_base = venue_prof or {"type": cap_port.get("defaultType"), "untagId": cap_port.get("untagId"),
                                    "vlanMembers": cap_port.get("vlanMembers")}
        if on_venue:
            resp = await lan_ports._call(self.r1_client, self.tenant_id, "get",
                                         f"/venues/{self.venue_id}/apModels/{model}/lanPorts/{pid}/settings")
            if resp.status_code != 200:
                raise RuntimeError(f"Can't read the venue's LAN{pid} (the uplink): {lan_ports._error(resp)}")
            s = resp.json() or {}
            return (s.get("enabled", True) is not False, *port_sig(venue_base))
        resp = await lan_ports._call(self.r1_client, self.tenant_id, "get",
                                     f"/venues/{self.venue_id}/aps/{serial}/lanPorts/{pid}/settings")
        if resp.status_code != 200:
            raise RuntimeError(f"Can't read LAN{pid} (the uplink): {lan_ports._error(resp)}")
        s = resp.json() or {}
        q = await lan_ports._call(self.r1_client, self.tenant_id, "post", "/ethernetPortProfiles/query",
                                  payload={"page": 1, "pageSize": 500, "filters": {"apSerialNumbers": [serial]}})
        if q.status_code != 200:
            raise RuntimeError(f"Can't read LAN{pid}'s (the uplink's) profile: {lan_ports._error(q)}")
        # Checked here too, in case R1 ever ignores the filter.
        prof = next((p for p in (q.json() or {}).get("data") or [] for a in p.get("apActivations") or []
                     if norm_serial(a.get("apSerialNumber")) == norm_serial(serial)
                     and str(a.get("portId")) == pid), None)
        base = prof or venue_base
        untag = s.get("overwriteUntagId") if s.get("overwriteUntagId") is not None else base.get("untagId")
        return (s.get("enabled", True) is not False,
                *sig(base.get("type"), untag, s.get("overwriteVlanMembers") or base.get("vlanMembers")))

    async def _uplinks(self, ctx, serial, model, uplinks, on_venue) -> Dict[str, Tuple]:
        return {pid: await self._uplink_state(ctx, serial, model, pid, on_venue) for pid in uplinks}

    # ── the unit ──

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        ap = inputs.ap
        name, serial, model = ap.get("r1_name") or ap["sz_name"], ap["serial"], ap.get("model")
        base = f"/venues/{self.venue_id}/aps/{serial}"
        halted = await _halted(self.job_id)
        if halted:
            raise RuntimeError(f"Job halted, nothing written to this AP: {halted}")

        ctx = await self._job_ctx()
        live = ctx["aps"].get(norm_serial(serial))
        cap = ctx["caps"].get(live.get("model") if live else model)
        if not live or not cap:
            raise RuntimeError("AP or its model is no longer in the venue — nothing written")
        model = live.get("model") or model
        up = uplink_ports(model, cap, live)
        if up["blocker"]:
            raise RuntimeError(up["blocker"])
        if not ap.get("uplink_ports"):
            raise RuntimeError("The plan carries no uplink for this AP — re-plan; nothing written")
        uplinks = sorted(set(up["ports"]) | {str(p) for p in ap["uplink_ports"]}, key=int)
        if up["sources"]["live"] and up["sources"]["live"][0] not in ap["uplink_ports"]:
            raise RuntimeError(f"The AP is now connected on LAN{up['sources']['live'][0]}, which the plan "
                               "didn't treat as its uplink — re-plan; nothing written")

        with self.r1_wait_notices(f"[{name}] "):
            resp = await lan_ports._call(self.r1_client, self.tenant_id, "get", f"{base}/lanPortSpecificSettings")
            if resp.status_code != 200:
                raise RuntimeError(f"R1 can't read the AP's LAN port settings: {lan_ports._error(resp)}")
            cur = resp.json() or {}
            cur_poe = {k: cur.get(k) for k in ("poeMode", "poeOut") if cur.get(k) is not None}
            flip = cur.get("useVenueSettings") is not False

            ports = [p for p in ap.get("ports") or [] if flip or p.get("changed")]
            bad = [str(p["portId"]) for p in ports if str(p["portId"]) in uplinks]
            if bad:
                raise RuntimeError(f"Refused: asked to write LAN{', LAN'.join(bad)}, the AP's uplink — "
                                   "nothing written")
            ports.sort(key=lambda p: not (p.get("enabled") and p.get("type") == "TRUNK"))

            before = await self._uplinks(ctx, serial, model, uplinks, on_venue=flip)
            poe = {**cur_poe, **(ap.get("poe") or {})}
            if flip:
                if ctx["proven"]:
                    await self._flip(ctx, base, name, serial, model, uplinks, before, poe, cur_poe)
                else:
                    # The job's first flip runs alone and is verified before any other may start.
                    async with ctx["gate"]:
                        halted = await _halted(self.job_id)
                        if halted:
                            raise RuntimeError(f"Job halted, nothing written to this AP: {halted}")
                        await self._flip(ctx, base, name, serial, model, uplinks, before, poe, cur_poe)
                        ctx["proven"] = True
            elif poe != cur_poe:
                await lan_ports._write(self.r1_client, self.tenant_id, "put", f"{base}/lanPortSpecificSettings",
                                       {**poe, "useVenueSettings": False})

            written = 0
            for p in ports:
                written += await self._port(base, p, uplinks, flip, inputs)

            after = await self._uplinks(ctx, serial, model, uplinks, on_venue=False)
            if after != before:
                reason = (f"{name}: uplink LAN{', LAN'.join(uplinks)} changed while its other ports were "
                          f"written ({before} → {after})")
                await _halt(self.job_id, reason)
                raise RuntimeError(reason + " — job halted; check this AP")

        detail = "; ".join(ap.get("changes") or []) or "own LAN ports"
        await self.record_change("updated", "ap_lan_ports", name, serial,
                                 f"{detail} (uplink LAN{', LAN'.join(uplinks)} untouched)")
        await self.emit(f"[{name}] LAN ports set ({written} port write(s)); uplink "
                        f"LAN{', LAN'.join(uplinks)} verified untouched", "success")
        return self.Outputs(ports_written=written, aps_updated=1)

    async def _flip(self, ctx, base, name, serial, model, uplinks, before, poe, cur_poe) -> None:
        """useVenueSettings off, then prove the uplink kept its setup; else undo and halt."""
        await lan_ports._write(self.r1_client, self.tenant_id, "put", f"{base}/lanPortSpecificSettings",
                               {**poe, "useVenueSettings": False})
        try:
            after = await self._uplinks(ctx, serial, model, uplinks, on_venue=False)
        except Exception as e:
            after, why = None, f"could not be re-read ({e})"
        else:
            why = f"{before} → {after}"
        if after == before:
            return
        reason = f"{name}: coming off venue settings changed or hid the uplink LAN{', LAN'.join(uplinks)}: {why}"
        await _halt(self.job_id, reason)
        try:
            await lan_ports._write(self.r1_client, self.tenant_id, "put", f"{base}/lanPortSpecificSettings",
                                   {**cur_poe, "useVenueSettings": True})
            undo = "put back on venue settings"
        except Exception as e:
            undo = f"COULD NOT be put back on venue settings ({e}) — check it now"
        await self.emit(f"[{name}] {reason}; {undo}. Job halted.", "error")
        raise RuntimeError(f"{reason}; {undo}. Job halted")

    async def _port(self, base: str, p: Dict[str, Any], uplinks: List[str], flip: bool, inputs: 'Inputs') -> int:
        pid = str(p["portId"])
        if pid in uplinks:   # last line: execute() has refused this already
            raise RuntimeError(f"Refused to write LAN{pid}: it is the AP's uplink")
        writes = 0
        sz_id = p.get("sz_profile_id")
        target = inputs.profile_ids.get(sz_id) if sz_id else None
        if sz_id and not target:
            raise RuntimeError(f"LAN{pid}: no R1 profile for SZ '{p.get('sz_profile_name')}' "
                               f"({inputs.profile_errors.get(sz_id) or 'not mapped'})")
        # Off venue settings R1 decides what each port starts on, so every written port gets its profile.
        if target and (flip or target != p.get("current_profile_id")):
            await lan_ports._write(self.r1_client, self.tenant_id, "put",
                                   f"{base}/lanPorts/{pid}/ethernetPortProfiles/{target}")
            writes += 1

        resp = await lan_ports._call(self.r1_client, self.tenant_id, "get", f"{base}/lanPorts/{pid}/settings")
        if resp.status_code != 200:
            raise RuntimeError(f"LAN{pid} settings unreadable: {lan_ports._error(resp)}")
        now = resp.json() or {}
        body = dict(now)
        body["enabled"] = bool(p.get("enabled"))
        # The exact VLANs, always as the port's override: right whatever the profile carries.
        t, u, m = sig(p.get("type"), p.get("untagId"), p.get("vlanMembers"))
        if u is not None:
            body["overwriteUntagId"] = u
        if m:
            body["overwriteVlanMembers"] = m
        if body != now:
            await lan_ports._write(self.r1_client, self.tenant_id, "put", f"{base}/lanPorts/{pid}/settings", body)
            writes += 1
        return writes

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=False, estimated_api_calls=8)
