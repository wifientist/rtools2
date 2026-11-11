"""
Venue per-AP-model LAN ports and PoE, on R1's current API.

Until 2026-11-11 the migration wrote these with one PUT of
/venues/{v}/apModelLanPortSettings, a list of
    {model, poeMode, poeOut, lanPorts: [{portId, enabled, type, untagId, vlanMembers}]}
and read them back with the matching GET. Both are deprecated (removal
allowed since 2026-08-31). In R1's current model:

    PoE                      PUT /venues/{v}/apModels/{m}/lanPortSpecificSettings  {poeMode, poeOut}
    port on/off              PUT /venues/{v}/apModels/{m}/lanPorts/{p}/settings     (whole body: read, change `enabled`, write)
    type, untag, members     live on a tenant-wide Ethernet Port Profile, attached with
                             PUT /venues/{v}/apModels/{m}/lanPorts/{p}/ethernetPortProfiles/{id}

The entries above stay the working shape: customise() builds them, this
module writes them, and read_entries() rebuilds them so diff_section compares
as before.

Which profile a port gets (decided 2026-11-11, "mirror SZ names"):
  * the port is at R1's default for that model/port -> R1's own default
    profile of that type/VLANs (isDefault)
  * otherwise the SZ zone's Ethernet Port Profile name: an R1 profile of that
    exact name with the same type/VLANs is reused; the name taken with other
    settings -> "<name>_<venue>"; nothing of that name -> created under it
  * no SZ profile name known -> only on/off is written, as before
No description is put on created profiles (no auto-descriptions on R1
objects). Names are 2-32 characters in R1.

R1 has no GENERAL port type any more: ACCESS, TRUNK and SELECTIVE_TRUNK.
SZ's General port is written as SELECTIVE_TRUNK and read back as GENERAL, so
the plan and the comparison keep SZ's word.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NAME_MAX = 32
PORT_SETTINGS_FIELD = "enabled"
_TO_R1_TYPE = {"GENERAL": "SELECTIVE_TRUNK"}
_FROM_R1_TYPE = {"SELECTIVE_TRUNK": "GENERAL"}


def _kw(r1_client, tenant_id: Optional[str]) -> Dict[str, Any]:
    return {"override_tenant_id": tenant_id} if getattr(r1_client, "ec_type", None) == "MSP" and tenant_id else {}


def _error(resp) -> str:
    try:
        body = resp.json()
        errs = body.get("errors") if isinstance(body, dict) else None
        if errs:
            return "; ".join(str(e.get("message") or e.get("reason") or e) for e in errs)[:400]
        return (body.get("message") if isinstance(body, dict) else None) or resp.text[:400]
    except Exception:
        return (getattr(resp, "text", "") or "")[:400] or f"HTTP {resp.status_code}"


async def _call(r1_client, tenant_id, method: str, path: str, **kwargs):
    kw = _kw(r1_client, tenant_id)
    return await asyncio.to_thread(lambda: getattr(r1_client, method)(path, **kwargs, **kw))


async def _write(r1_client, tenant_id, method: str, path: str, payload=None) -> Dict[str, Any]:
    """Send and wait for R1's task. Returns the 2xx body; raises RuntimeError with R1's reason."""
    kwargs = {"payload": payload} if payload is not None else {}
    resp = await _call(r1_client, tenant_id, method, path, **kwargs)
    if resp.status_code not in (200, 201, 202, 204):
        raise RuntimeError(_error(resp))
    data = resp.json() if resp.content else {}
    if resp.status_code == 202 and isinstance(data, dict) and data.get("requestId"):
        await r1_client.await_task_completion(data["requestId"], override_tenant_id=tenant_id)
    return data if isinstance(data, dict) else {}


def _sig(p: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """What a port's profile has to match: type (in R1's words), untagged VLAN, members."""
    t = p.get("type")
    return (_TO_R1_TYPE.get(t, t), int(p["untagId"]) if p.get("untagId") is not None else None,
            str(p.get("vlanMembers") or "").replace(" ", ""))


def _plain(profile: Dict[str, Any]) -> bool:
    """No 802.1X, MAC auth or proxying: a profile that is only type and VLANs."""
    return (profile.get("authType") in (None, "DISABLED")
            and not profile.get("dynamicVlanEnabled"))


# ---------- reading ----------

async def list_profiles(r1_client, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every Ethernet Port Profile in the tenant (1-indexed query)."""
    rows: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await _call(r1_client, tenant_id, "post", "/ethernetPortProfiles/query",
                           payload={"page": page, "pageSize": 500})
        if resp.status_code != 200:
            raise RuntimeError(f"Listing Ethernet Port Profiles: {_error(resp)}")
        body = resp.json() or {}
        batch = body.get("data") or []
        rows.extend(batch)
        if not batch or len(rows) >= (body.get("totalCount") or 0):
            return rows
        page += 1


async def read_entries(r1_client, tenant_id: Optional[str], venue_id: str, models: List[str],
                       caps: Dict[str, Dict[str, Any]],
                       profiles: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    What R1 holds for these models at the venue, in the old
    apModelLanPortSettings shape. A port with no profile attached here is on
    its model's default (from the capability row).
    """
    profiles = profiles if profiles is not None else await list_profiles(r1_client, tenant_id)
    attached: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for prof in profiles:
        for act in prof.get("venueActivations") or []:
            if act.get("venueId") == venue_id:
                attached[(act.get("apModel"), str(act.get("portId")))] = prof

    async def one(model: str) -> Dict[str, Any]:
        cap = caps.get(model) or {}
        entry: Dict[str, Any] = {"model": model}
        resp = await _call(r1_client, tenant_id, "get", f"/venues/{venue_id}/apModels/{model}/lanPortSpecificSettings")
        if resp.status_code == 200:
            poe = resp.json() or {}
            if poe.get("poeMode") is not None:
                entry["poeMode"] = poe["poeMode"]
            if "poeOut" in poe:
                entry["poeOut"] = bool(poe["poeOut"])
        ports = []
        for spec in cap.get("lanPorts") or []:
            pid = str(spec.get("id"))
            prof = attached.get((model, pid))
            port = {"portId": pid,
                    "type": _FROM_R1_TYPE.get(prof["type"], prof["type"]) if prof else spec.get("defaultType"),
                    "untagId": prof.get("untagId") if prof else spec.get("untagId"),
                    "vlanMembers": prof.get("vlanMembers") if prof else spec.get("vlanMembers")}
            s = await _call(r1_client, tenant_id, "get", f"/venues/{venue_id}/apModels/{model}/lanPorts/{pid}/settings")
            if s.status_code == 200:
                port["enabled"] = bool((s.json() or {}).get("enabled", True))
            ports.append(port)
        if ports:
            entry["lanPorts"] = ports
        return entry

    return list(await asyncio.gather(*(one(m) for m in models)))


# ---------- choosing a profile ----------

def _clash_name(name: str, venue_name: str) -> str:
    suffix = f"_{venue_name}"
    if len(name) + len(suffix) <= NAME_MAX:
        return name + suffix
    room = NAME_MAX - len(name) - 1
    return f"{name}_{venue_name[:room]}" if room >= 4 else (name[:NAME_MAX - 5] + "_" + venue_name[:4])


class ProfileChooser:
    """Picks (and if needed creates) the profile for each port; shared across a venue's models."""

    def __init__(self, r1_client, tenant_id: Optional[str], venue_name: str,
                 profiles: List[Dict[str, Any]], match_any: bool = False):
        """
        match_any: for a hand edit with no SZ profile name (Venue Checkup),
        reuse any existing plain profile with the same type/VLANs and never
        create one -- a tenant-wide object is not named on the user's behalf.
        """
        self.r1, self.tenant_id, self.venue_name = r1_client, tenant_id, venue_name
        self.profiles = profiles
        self.match_any = match_any
        self.created: List[str] = []
        self._lock = asyncio.Lock()

    def _by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.profiles if p.get("name") == name), None)

    def default_for(self, sig) -> Optional[Dict[str, Any]]:
        hits = [p for p in self.profiles if p.get("isDefault") and _plain(p) and _sig(p) == sig]
        # Several defaults can share a signature (WAN and LAN trunk): the one in use.
        hits.sort(key=lambda p: -len(p.get("venueActivations") or []))
        return hits[0] if hits else None

    async def choose(self, port: Dict[str, Any], at_default: bool, notes: List[str]) -> Optional[str]:
        """Profile id for this port, or None to leave its profile alone."""
        sig = _sig(port)
        if at_default:
            prof = self.default_for(sig)
            return prof["id"] if prof else None
        name = (port.get("profileName") or "").strip()
        if self.match_any and not name:
            same = [p for p in self.profiles if _plain(p) and _sig(p) == sig]
            same.sort(key=lambda p: (not p.get("isDefault"), -len(p.get("venueActivations") or [])))
            if not same:
                t, u, m = sig
                raise RuntimeError(f"port {port['portId']}: no Ethernet Port Profile in R1 is {t} untagged {u} "
                                   f"members {m}; create one in R1 first")
            return same[0]["id"]
        if not name:
            notes.append(f"port {port['portId']}: no SZ profile name, so only on/off is written")
            return None
        async with self._lock:
            for candidate in (name[:NAME_MAX], _clash_name(name, self.venue_name)):
                found = self._by_name(candidate)
                if found is None:
                    return await self._create(candidate, sig)
                if _plain(found) and _sig(found) == sig:
                    return found["id"]
            raise RuntimeError(f"profiles '{name}' and '{_clash_name(name, self.venue_name)}' both exist in R1 "
                               "with other settings")

    async def _create(self, name: str, sig) -> str:
        r1_type, untag, members = sig
        body: Dict[str, Any] = {"name": name, "type": r1_type, "authType": "DISABLED"}
        if untag is not None:
            body["untagId"] = untag
        if members:
            body["vlanMembers"] = members
        data = await _write(self.r1, self.tenant_id, "post", "/ethernetPortProfiles", body)
        new_id = (data.get("response") or {}).get("id") or data.get("id")
        if not new_id:
            fresh = await list_profiles(self.r1, self.tenant_id)
            new_id = next((p["id"] for p in fresh if p.get("name") == name), None)
        if not new_id:
            raise RuntimeError(f"R1 accepted profile '{name}' but it could not be found afterwards")
        self.profiles.append({"id": new_id, "name": name, "type": r1_type, "untagId": untag,
                              "vlanMembers": members, "authType": "DISABLED", "venueActivations": []})
        self.created.append(name)
        logger.info(f"[SZ Migration] Created Ethernet Port Profile '{name}' ({new_id})")
        return new_id


def default_entry(model: str, cap: Dict[str, Any]) -> Dict[str, Any]:
    """The model on R1's own defaults: default PoE, every port enabled on its default type/VLANs."""
    entry: Dict[str, Any] = {"model": model}
    modes = cap.get("poeModeCapabilities") or []
    if cap.get("canSupportPoeMode") and modes:
        entry["poeMode"] = "Auto" if "Auto" in modes else modes[0]
    if cap.get("canSupportPoeOut"):
        entry["poeOut"] = False
    entry["lanPorts"] = [{"portId": str(p["id"]), "enabled": True, "type": p.get("defaultType"),
                          "untagId": p.get("untagId"), "vlanMembers": p.get("vlanMembers")}
                         for p in cap.get("lanPorts") or [] if p.get("id") is not None]
    return entry


# ---------- writing ----------

async def write_entry(r1_client, tenant_id: Optional[str], venue_id: str, entry: Dict[str, Any],
                      cap: Dict[str, Any], current: Dict[str, Any], chooser: ProfileChooser,
                      notes: List[str]) -> None:
    """
    Make one model match `entry` (a complete customise() entry). `current` is
    read_entries' row for the model. Only what differs is sent. Raises
    RuntimeError with R1's reason on the first refusal.
    """
    model = entry["model"]
    base = f"/venues/{venue_id}/apModels/{model}"

    poe = {k: entry[k] for k in ("poeMode", "poeOut") if k in entry}
    if poe and any(current.get(k) != v for k, v in poe.items()):
        await _write(r1_client, tenant_id, "put", f"{base}/lanPortSpecificSettings",
                     {**{k: current.get(k) for k in ("poeMode", "poeOut") if k in current}, **poe})

    defaults = {str(p.get("id")): p for p in cap.get("lanPorts") or [] if p.get("id") is not None}
    have = {p["portId"]: p for p in current.get("lanPorts") or []}
    for port in entry.get("lanPorts") or []:
        pid = str(port["portId"])
        now = have.get(pid, {})
        spec = defaults.get(pid, {})
        if port.get("type") and _sig(port) != _sig(now):
            at_default = _sig(port) == _sig({"type": spec.get("defaultType"), "untagId": spec.get("untagId"),
                                             "vlanMembers": spec.get("vlanMembers")})
            port_notes: List[str] = []
            profile_id = await chooser.choose(port, at_default, port_notes)
            notes.extend(f"{model} {n}" for n in port_notes)
            if profile_id:
                await _write(r1_client, tenant_id, "put",
                             f"{base}/lanPorts/{pid}/ethernetPortProfiles/{profile_id}")
            elif at_default:
                notes.append(f"{model} port {pid}: no R1 default profile matches its default; left as is")
        if port.get("enabled") is not None and bool(port["enabled"]) != bool(now.get("enabled", True)):
            resp = await _call(r1_client, tenant_id, "get", f"{base}/lanPorts/{pid}/settings")
            if resp.status_code != 200:
                raise RuntimeError(f"port {pid} settings unreadable: {_error(resp)}")
            settings = resp.json() or {}
            settings[PORT_SETTINGS_FIELD] = bool(port["enabled"])
            await _write(r1_client, tenant_id, "put", f"{base}/lanPorts/{pid}/settings", settings)
