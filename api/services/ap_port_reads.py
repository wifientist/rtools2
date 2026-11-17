"""
Reads behind the per-AP LAN port comparison (services/ap_port_grabber.build_rows).

Shared by AP Port Grabber (which writes) and the Migration Audit (which only
reports), so both see an AP's ports the same way:

    read_sz_group_layers  SZ AP-Group per-model settings + the Ethernet Port
                          Profiles any layer points at
    read_r1_lan_state     the R1 venue's APs, model capabilities, per-model
                          ports, profiles, and each matching AP's own ports

Read-only. Nothing here is secret.
"""

import asyncio
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from services.ap_port_grabber import norm_serial
from services.sz_migration import lan_ports
from services.sz_migration.venue_create import model_capabilities

READ_CONCURRENCY = 10


def _err(e: Exception) -> str:
    return f"{e}"[:200] or type(e).__name__


def _is_404(e: Exception) -> bool:
    cause = e.__cause__ if isinstance(e.__cause__, httpx.HTTPStatusError) else e
    return isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 404


def profile_ids(layers: Iterable[Optional[Dict[str, Any]]]) -> set:
    """Ethernet Port Profile ids the lanPorts of these SZ settings layers point at."""
    ids = set()
    for layer in layers:
        for p in (layer or {}).get("lanPorts") or []:
            if (p.get("ethPortProfile") or {}).get("id"):
                ids.add(p["ethPortProfile"]["id"])
    return ids


async def read_sz_group_layers(
    sz_client, zone_id: str, aps: List[Dict[str, Any]], layers: Iterable[Optional[Dict[str, Any]]],
    known_profiles: Optional[Dict[str, Any]] = None, concurrency: int = READ_CONCURRENCY,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    aps: sz_ap_fields rows. layers: the zone-model and AP `specific` settings
    already read. -> (group_models {"group_id|model": settings | None | {"_error"}},
    profiles {id: profile | {"error"}}) -- profiles only for ids not in known_profiles.

    Only the (group, model) pairs the APs sit in are read. A group that does
    not override the model answers with an EMPTY body (seen live on vSZ
    2026-11-17; the spec says nulls) -- read, like a 404, as no override.
    """
    sem = asyncio.Semaphore(concurrency)
    base = f"/{sz_client.api_version}/rkszones/{zone_id}"

    async def get(path: str, missing_is_none: bool = False):
        async with sem:
            try:
                return await sz_client._request("GET", path) or {}
            except Exception as e:
                if missing_is_none and (_is_404(e) or isinstance(e, json.JSONDecodeError)):
                    return None
                return {"_error": _err(e)}

    pairs = sorted({(a["group_id"], a["model"]) for a in aps if a.get("group_id") and a.get("model")})
    group_models = dict(zip(
        [f"{g}|{m}" for g, m in pairs],
        await asyncio.gather(*[get(f"{base}/apgroups/{g}/apmodel/{m}", missing_is_none=True) for g, m in pairs])))

    ids = sorted(profile_ids([*layers, *[g for g in group_models.values() if isinstance(g, dict)]])
                 - set(known_profiles or {}))
    profiles = dict(zip(ids, await asyncio.gather(*[get(f"{base}/profile/ethernetPort/{i}") for i in ids])))
    profiles = {k: ({"error": v["_error"]} if v.get("_error") else v) for k, v in profiles.items()}
    return group_models, profiles


async def read_r1_lan_state(r1_client, tenant_id: str, venue_id: str, sz_aps: List[Dict[str, Any]],
                            concurrency: int = READ_CONCURRENCY) -> Dict[str, Any]:
    """The venue's APs, model defaults and per-model ports, profiles, and each matching AP's own ports."""
    sem = asyncio.Semaphore(concurrency)
    kw = lan_ports._kw(r1_client, tenant_id)
    venue = await r1_client.venues.get_venue(tenant_id, venue_id)
    r1_aps = (await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)).get("data") or []
    caps = await model_capabilities(r1_client, venue_id, kw)
    if caps is None:
        raise RuntimeError("Could not read the venue's AP model capabilities")
    profiles = await lan_ports.list_profiles(r1_client, tenant_id)

    # Only the R1 APs an SZ AP of the zone matches, and only models with ports.
    wanted = {norm_serial(a.get("serial")) for a in sz_aps} | {
        (a.get("mac") or "").replace(":", "").replace("-", "").upper() for a in sz_aps}
    matched = [a for a in r1_aps
               if (norm_serial(a.get("serialNumber")) in wanted
                   or (a.get("macAddress") or "").replace(":", "").replace("-", "").upper() in wanted)
               and (caps.get(a.get("model")) or {}).get("lanPorts")]
    models = sorted({a.get("model") for a in matched})
    venue_entries = {e["model"]: e for e in await lan_ports.read_entries(
        r1_client, tenant_id, venue_id, models, caps, profiles)} if models else {}

    # The profile each model's port has at the venue (its auth is part of the comparison).
    venue_profiles: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for prof in profiles:
        for act in prof.get("venueActivations") or []:
            if act.get("venueId") == venue_id and act.get("apModel"):
                venue_profiles.setdefault(act["apModel"], {})[str(act.get("portId"))] = prof

    activations: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for prof in profiles:
        for act in prof.get("apActivations") or []:
            if act.get("venueId") in (None, venue_id) and act.get("apSerialNumber"):
                activations.setdefault(norm_serial(act["apSerialNumber"]), {})[str(act.get("portId"))] = prof

    async def state(ap: Dict[str, Any]):
        serial = ap["serialNumber"]
        path = f"/venues/{venue_id}/aps/{serial}"
        async with sem:
            resp = await lan_ports._call(r1_client, tenant_id, "get", f"{path}/lanPortSpecificSettings")
        if resp.status_code != 200:
            return {"_error": lan_ports._error(resp)[:160] or f"HTTP {resp.status_code}"}
        specific = resp.json() or {}
        ports: Dict[str, Any] = {}
        if specific.get("useVenueSettings") is False:
            for spec in caps[ap["model"]].get("lanPorts") or []:
                pid = str(spec.get("id"))
                async with sem:
                    r = await lan_ports._call(r1_client, tenant_id, "get", f"{path}/lanPorts/{pid}/settings")
                if r.status_code != 200:
                    return {"_error": f"LAN{pid}: {lan_ports._error(r)[:160]}"}
                ports[pid] = r.json() or {}
        return {"specific": specific, "ports": ports}

    states = await asyncio.gather(*[state(a) for a in matched])
    return {
        "venue": venue or {}, "r1_aps": r1_aps, "caps": caps, "venue_entries": venue_entries,
        "profiles": profiles, "activations": activations, "venue_profiles": venue_profiles,
        "ap_states": {norm_serial(a["serialNumber"]): s for a, s in zip(matched, states)},
    }
