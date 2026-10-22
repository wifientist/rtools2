"""
WLAN Grabber Router

Create in R1 the WLANs of one SZ zone that the R1 tenant does not have yet:

- POST /wlan-grabber/preview -> read both sides; one row per SZ WLAN, the
  RADIUS services the Enterprise ones use, and the tenant's DPSK pools
  (read-only)
- POST /wlan-grabber/apply   -> create the chosen networks

Networks are created tenant-wide and left unactivated (WLAN Activator does
that). The optional venue is context only: the DPSK pool its DPSK networks
are tied to is suggested as the shared pool for new DPSK networks.

DPSK pool: one shared pool for every DPSK network (an existing R1 pool, or
a new one created here together with its identity group), and any row may
name another pool or none instead.

Rows, statuses and notes: services/wlan_grabber.py.

Apply re-reads each chosen WLAN from SZ: the passphrase never goes to the
browser, so it cannot come back from it either. A RADIUS shared secret is
typed by the user, used for one create call, and never stored or returned;
R1 profile rows are reduced by r1_profile_summary before they leave.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, model_validator
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.user import User
from routers.tenant_scope import resolve_tenant_id
from services.sz_migration.radius_choices import r1_profile_summary, sz_server_details
from services.wlan_grabber import (
    EXISTS, build_rows, build_spec, dpsk_pool_suggestion, radius_services,
)
from r1api.constants import DpskPassphraseFormat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wlan-grabber", tags=["WLAN Grabber"])

READ_CONCURRENCY = 10
WRITE_CONCURRENCY = 3


# ==================== Models ====================

class PreviewRequest(BaseModel):
    sz_controller_id: int
    zone_id: str
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: Optional[str] = Field(None, description="Context only: suggests a DPSK pool")


class RadiusDecision(BaseModel):
    """What to do with one SZ RADIUS service."""
    action: Literal["create", "existing", "skip"]
    r1_profile_id: Optional[str] = None
    # Write-only; SecretStr keeps it out of repr and logs.
    shared_secret: Optional[SecretStr] = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.action == "existing" and not self.r1_profile_id:
            raise ValueError("'existing' needs r1_profile_id")
        if self.action == "create" and not (self.shared_secret and self.shared_secret.get_secret_value()):
            raise ValueError("'create' needs a shared secret")
        return self


class WlanChoice(BaseModel):
    sz_wlan_id: str
    r1_type: Optional[Literal["psk", "open", "aaa", "dpsk"]] = None   # None = its default mode
    # DPSK only: None = the shared pool, "none" = no pool, else an R1 pool id.
    dpsk_pool: Optional[str] = None


class SharedPool(BaseModel):
    """The DPSK pool every DPSK network uses unless its row says otherwise."""
    mode: Literal["existing", "new", "none"]
    pool_id: Optional[str] = None
    name: Optional[str] = Field(None, min_length=2, max_length=64)
    passphrase_format: str = DpskPassphraseFormat.KEYBOARD_FRIENDLY
    passphrase_length: int = Field(12, ge=8, le=64)
    devices_per_passphrase: Optional[int] = Field(None, ge=1)   # None = unlimited

    @model_validator(mode="after")
    def _consistent(self):
        if self.mode == "existing" and not self.pool_id:
            raise ValueError("'existing' needs pool_id")
        if self.mode == "new" and not self.name:
            raise ValueError("'new' needs a name")
        if self.passphrase_format not in DpskPassphraseFormat.ALL:
            raise ValueError(f"passphrase_format must be one of {DpskPassphraseFormat.ALL}")
        return self


class ApplyRequest(BaseModel):
    sz_controller_id: int
    zone_id: str
    r1_controller_id: int
    tenant_id: Optional[str] = None
    wlans: List[WlanChoice] = Field(..., min_length=1)
    radius: Dict[str, RadiusDecision] = Field(default_factory=dict)
    dpsk_pool: SharedPool = Field(default_factory=lambda: SharedPool(mode="none"))
    # Off by default: the R1 network is created with no description.
    add_description: bool = False


# ==================== Helpers ====================

def _check_controller(controller_id: int, kind: str, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != kind:
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be {kind}, "
                                                    f"got {controller.controller_type}")
    return controller


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


def _rows_of(resp: Any) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("content") or []
    return []


async def _sz_wlans(sz_client, zone_id: str, wlan_ids: Optional[List[str]] = None):
    """SZ WLAN detail objects for the zone (or just wlan_ids), and the zone."""
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    zone = await sz_client.zones.get_zone_details(zone_id)
    if wlan_ids is None:
        wlan_ids = [w["id"] for w in await sz_client.wlans.get_all_wlans_paginated(zone_id) if w.get("id")]

    async def detail(wid):
        async with sem:
            return await sz_client.wlans.get_wlan_details(zone_id, wid)

    return zone, list(await asyncio.gather(*[detail(w) for w in wlan_ids]))


async def _sz_service_refs(sz_client, services: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """{service_id: role} -> {"auth_service:{id}" | "accounting_service:{id}": raw}."""
    async def one(sid, role):
        if role == "auth":
            return f"auth_service:{sid}", await sz_client.aaa.get_auth_service(sid)
        return f"accounting_service:{sid}", await sz_client.aaa.get_accounting_service(sid)

    return dict(await asyncio.gather(*[one(s, r) for s, r in services.items()]))


async def _dpsk_pools(r1_client, tenant_id: str) -> List[Dict[str, Any]]:
    pools: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id, page=page, limit=100)
        rows = _rows_of(resp)
        pools.extend(rows)
        total = resp.get("totalCount", len(pools)) if isinstance(resp, dict) else len(pools)
        if not rows or len(pools) >= total or page > 50:
            return [{"id": p["id"], "name": p.get("name")} for p in pools if p.get("id")]
        page += 1


async def _create_pool(r1_client, tenant_id: str, spec: "SharedPool"):
    """
    The new shared DPSK pool and its identity group (same name), as
    Config Migration's create_dpsk_pools does it. A pool by that name
    already in R1 is linked unchanged. Returns (pool_id, already_existed).
    """
    def find(resp):
        for p in _rows_of(resp):
            if p.get("name") == spec.name and p.get("id"):
                return p["id"]
        return None

    found = find(await r1_client.dpsk.query_dpsk_pools(
        tenant_id=tenant_id, search_string=spec.name, page=1, limit=100))
    if found:
        return found, True

    group = await r1_client.identity.create_identity_group(
        tenant_id=tenant_id, name=spec.name,
    )
    group_id = (group or {}).get("id")
    if not group_id:
        raise RuntimeError(f"Identity group '{spec.name}' was not created (no id returned)")

    pool = await r1_client.dpsk.create_dpsk_pool(
        identity_group_id=group_id, name=spec.name, tenant_id=tenant_id,
        passphrase_length=spec.passphrase_length, passphrase_format=spec.passphrase_format,
        # 0 = unlimited: create_dpsk_pool then omits deviceCountLimit.
        max_devices_per_passphrase=spec.devices_per_passphrase or 0,
        word_count=4 if spec.passphrase_format == DpskPassphraseFormat.DICTIONARY_WORDS else None,
    )
    if (pool or {}).get("requestId"):
        await r1_client.await_task_completion(request_id=pool["requestId"], override_tenant_id=tenant_id)
    if (pool or {}).get("id"):
        logger.info(f"[WLAN Grabber] Created DPSK pool '{spec.name}' ({pool['id']})")
        return pool["id"], False
    found = find(await r1_client.dpsk.query_dpsk_pools(
        tenant_id=tenant_id, search_string=spec.name, page=1, limit=100))
    if not found:
        raise RuntimeError(f"DPSK pool '{spec.name}' was created but could not be found")
    logger.info(f"[WLAN Grabber] Created DPSK pool '{spec.name}' ({found})")
    return found, False


# ==================== Endpoints ====================

@router.post("/preview")
@require_beta()
async def preview(
    request: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the zone's WLANs and the tenant's networks; one row per SZ WLAN. Read-only."""
    _check_controller(request.sz_controller_id, "SmartZone", current_user, db)
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)

    # ── R1: every network in the tenant (names are unique tenant-wide) ──
    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    try:
        resp = await r1_client.networks.get_wifi_networks(tenant_id)
        tenant_networks = [n for n in (resp.get("data") or []) if n.get("id")]
        venue = await r1_client.venues.get_venue(tenant_id, request.venue_id) if request.venue_id else None
    except Exception as e:
        logger.exception("[WLAN Grabber] R1 read failed")
        raise HTTPException(status_code=502, detail=f"Failed to read networks from R1: {e}")

    # ── R1: DPSK pools, and the ones the venue's DPSK networks use ──
    sem = asyncio.Semaphore(READ_CONCURRENCY)
    dpsk_pools: List[Dict[str, Any]] = []
    dpsk_suggestion = None
    warnings: List[str] = []
    try:
        dpsk_pools = await _dpsk_pools(r1_client, tenant_id)
    except Exception as e:
        logger.warning(f"[WLAN Grabber] Could not list DPSK pools: {e}")
        warnings.append("Could not list R1 DPSK pools; DPSK networks can only be created without one")

    if request.venue_id:
        venue_dpsk = [
            n for n in tenant_networks
            if str(n.get("nwSubType") or "").lower() == "dpsk"
            and request.venue_id in {v.get("venueId") for v in n.get("venueApGroups") or []}
        ]
        links: Dict[str, List[Dict[str, Any]]] = {}

        async def pools_on(nid: str):
            async with sem:
                try:
                    links[nid] = _rows_of(await r1_client.networks.get_dpsk_services_on_network(nid, tenant_id))
                except Exception as e:
                    logger.warning(f"[WLAN Grabber] Could not read DPSK pools on {nid}: {e}")

        await asyncio.gather(*[pools_on(n["id"]) for n in venue_dpsk])
        dpsk_suggestion = dpsk_pool_suggestion(links, (venue or {}).get("name") or "this venue")

    # ── SZ: the list carries no security or VLAN, so detail each WLAN ──
    sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
    try:
        async with sz_client:
            zone, sz_wlans = await _sz_wlans(sz_client, request.zone_id)
            result = build_rows(sz_wlans, tenant_networks, request.venue_id)
            needed: Dict[str, str] = {}
            for r in result["rows"]:
                if "aaa" in r["modes"] and r["status"] != EXISTS and not r["modes"]["aaa"]["blockers"]:
                    if r["auth_service_id"]:
                        needed.setdefault(r["auth_service_id"], "auth")
                    if r["accounting_service_id"]:
                        needed.setdefault(r["accounting_service_id"], "acct")
            refs = await _sz_service_refs(sz_client, needed) if needed else {}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[WLAN Grabber] SZ read failed for zone {request.zone_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the zone from SmartZone: {e}")

    r1_profiles: List[Dict[str, Any]] = []
    if refs:
        try:
            rows = await asyncio.to_thread(r1_client.radius_profiles.get_radius_profiles, tenant_id)
            # Reduce immediately: these rows carry sharedSecret.
            r1_profiles = [r1_profile_summary(p) for p in rows if p.get("id")]
        except Exception as e:
            logger.warning(f"[WLAN Grabber] Could not list RADIUS profiles: {type(e).__name__}")
            warnings.append("Could not list R1 RADIUS profiles; 'Use existing' is unavailable")

    return {
        "zone_id": request.zone_id,
        "zone_name": (zone or {}).get("name") or "",
        "venue_name": (venue or {}).get("name") if venue else None,
        **result,
        "radius_services": radius_services(result["rows"], refs, r1_profiles),
        "r1_radius_profiles": r1_profiles,
        "dpsk_pools": dpsk_pools,
        "dpsk_suggestion": dpsk_suggestion,
        "warnings": warnings,
    }


@router.post("/apply")
@require_beta()
async def apply(
    request: ApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create the chosen networks. One result per WLAN, one per RADIUS profile created."""
    _check_controller(request.sz_controller_id, "SmartZone", current_user, db)
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    if len({w.sz_wlan_id for w in request.wlans}) != len(request.wlans):
        raise HTTPException(status_code=400, detail="A WLAN appears more than once")
    choices = {w.sz_wlan_id: w for w in request.wlans}

    # ── SZ: re-read the chosen WLANs (the passphrase lives only here) ──
    sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
    try:
        async with sz_client:
            _, raws = await _sz_wlans(sz_client, request.zone_id, list(choices))
            # _sz_wlans keeps the order of the ids it was given.
            specs = [build_spec(raw, choices[wid].r1_type) for wid, raw in zip(choices, raws)]
            to_create = {
                sid: role for s in specs if s["r1_type"] == "aaa" and not s["blockers"]
                for sid, role in ((s["auth_service_id"], "auth"), (s["accounting_service_id"], "acct"))
                if sid and (request.radius.get(sid) and request.radius[sid].action == "create")
            }
            refs = await _sz_service_refs(sz_client, to_create) if to_create else {}
    except Exception as e:
        logger.exception(f"[WLAN Grabber] SZ re-read failed for zone {request.zone_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the zone from SmartZone: {e}")

    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)

    # ── RADIUS profiles first: SZ service id -> R1 profile id ──
    profile_for: Dict[str, str] = {}
    radius_results: List[Dict[str, Any]] = []
    snapshot = SimpleNamespace(referenced_objects={
        k: SimpleNamespace(name=(v or {}).get("name"), raw=v or {}) for k, v in refs.items()
    })
    for sid, d in request.radius.items():
        if d.action == "existing":
            profile_for[sid] = d.r1_profile_id
        elif d.action == "create" and sid in to_create:
            details = sz_server_details(snapshot, sid)
            out = {"sz_service_id": sid, "name": details["name"]}
            try:
                if details["error"] or not details["primary"]:
                    radius_results.append({**out, "status": "failed",
                                           "error": "SZ service has no server address"})
                    continue
                existing = await asyncio.to_thread(
                    r1_client.radius_profiles.find_radius_profile_by_name, tenant_id, details["name"])
                if existing and existing.get("id"):
                    profile_for[sid] = existing["id"]
                    radius_results.append({**out, "status": "exists",
                                           "error": "Linked the existing profile; its secret was left unchanged"})
                    continue
                secret = d.shared_secret.get_secret_value()
                primary, secondary = details["primary"], details["secondary"]
                created = await r1_client.radius_profiles.create_radius_profile(
                    tenant_id=tenant_id, name=details["name"],
                    primary_ip=primary["ip"], primary_port=primary["port"], primary_secret=secret,
                    secondary_ip=secondary["ip"] if secondary else None,
                    secondary_port=secondary["port"] if secondary else 1812,
                    secondary_secret=secret if secondary else "",
                    profile_type=details["role"],
                )
                secret = None
                if not (created or {}).get("id"):
                    raise RuntimeError("R1 returned no profile id")
                profile_for[sid] = created["id"]
                radius_results.append({**out, "status": "created"})
                logger.info(f"[WLAN Grabber] Created RADIUS profile '{details['name']}'")
            except Exception as e:
                logger.error(f"[WLAN Grabber] RADIUS profile '{details['name']}' failed: {_api_error(e)}")
                radius_results.append({**out, "status": "failed", "error": _api_error(e)})

    # ── The shared DPSK pool, created once if it is new and some row uses it ──
    def pool_choice(spec):
        return choices[spec["sz_wlan_id"]].dpsk_pool

    shared = request.dpsk_pool
    shared_pool_id: Optional[str] = shared.pool_id if shared.mode == "existing" else None
    pool_result: Optional[Dict[str, Any]] = None
    wants_shared = any(s["r1_type"] == "dpsk" and not s["blockers"] and pool_choice(s) is None for s in specs)
    if shared.mode == "new" and wants_shared:
        pool_result = {"name": shared.name}
        try:
            shared_pool_id, existed = await _create_pool(r1_client, tenant_id, shared)
            pool_result["status"] = "exists" if existed else "created"
            if existed:
                pool_result["error"] = "A pool by this name already exists — linked it unchanged"
        except Exception as e:
            logger.error(f"[WLAN Grabber] DPSK pool '{shared.name}' failed: {_api_error(e)}")
            pool_result.update(status="failed", error=_api_error(e))

    # ── Networks ──
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def create_one(spec: Dict[str, Any]) -> Dict[str, Any]:
        name, r1_type = spec["sz_name"], spec["r1_type"]
        out: Dict[str, Any] = {"sz_wlan_id": spec["sz_wlan_id"], "name": name, "type": r1_type, "warnings": []}
        if spec["blockers"]:
            return {**out, "status": "failed", "error": "; ".join(spec["blockers"])}
        auth_profile = profile_for.get(spec["auth_service_id"]) if r1_type == "aaa" else None
        if r1_type == "aaa" and not auth_profile:
            return {**out, "status": "failed",
                    "error": "Its RADIUS authentication service is not mapped to an R1 profile"}

        async with sem:
            try:
                existing = await r1_client.networks.find_wifi_network_by_name(tenant_id, None, name)
                if existing and existing.get("id"):
                    return {**out, "status": "exists", "network_id": existing["id"]}

                common = dict(
                    tenant_id=tenant_id, venue_id=None, name=name, ssid=spec["sz_ssid"],
                    vlan_id=spec["vlan_id"], description=f"Migrated from SZ: {name}" if request.add_description else None,
                    advanced_customization=spec["advanced"] or None,
                )
                if r1_type == "psk":
                    network = await r1_client.networks.create_wifi_network(
                        passphrase=spec["passphrase"], security_type=spec["security"], **common)
                elif r1_type == "open":
                    network = await r1_client.networks.create_open_wifi_network(**common)
                elif r1_type == "aaa":
                    network = await r1_client.networks.create_aaa_wifi_network(
                        security_type=spec["security"], **common)
                elif r1_type == "dpsk":
                    network = await r1_client.networks.create_dpsk_wifi_network(**common)
                else:
                    return {**out, "status": "failed", "error": f"Unsupported type {r1_type}"}
                net_id = (network or {}).get("id")
                if not net_id:
                    return {**out, "status": "failed", "error": "R1 accepted the create but the network was not found"}
                out["network_id"] = net_id
                logger.info(f"[WLAN Grabber] Created {r1_type} network '{name}' ({net_id})")
            except Exception as e:
                logger.error(f"[WLAN Grabber] Create '{name}' failed: {_api_error(e)}")
                return {**out, "status": "failed", "error": _api_error(e)}

            # Links: the network exists now, so a failure here is a warning, not a failed row.
            links = []
            if r1_type == "aaa":
                links.append(("RADIUS authentication", r1_client.radius_profiles.link_radius_to_network,
                              auth_profile))
                acct = profile_for.get(spec["accounting_service_id"])
                if acct and acct != auth_profile:
                    links.append(("RADIUS accounting", r1_client.radius_profiles.link_radius_to_network, acct))
            for label, fn, pid in links:
                try:
                    await fn(network_id=net_id, radius_profile_id=pid, tenant_id=tenant_id)
                except Exception as e:
                    out["warnings"].append(f"{label} profile not linked: {_api_error(e)}")
            if r1_type == "dpsk":
                choice = pool_choice(spec)
                pool_id = shared_pool_id if choice is None else (None if choice == "none" else choice)
                if choice is None and shared.mode == "new" and not pool_id:
                    out["warnings"].append("Shared DPSK pool was not created — no pool linked")
                elif pool_id:
                    try:
                        await r1_client.networks.activate_dpsk_service_on_network(
                            tenant_id=tenant_id, network_id=net_id, dpsk_service_id=pool_id)
                    except Exception as e:
                        out["warnings"].append(f"DPSK pool not linked: {_api_error(e)}")
                else:
                    out["warnings"].append("No DPSK pool linked — R1 will not activate it until one is")
            return {**out, "status": "created"}

    results = await asyncio.gather(*[create_one(s) for s in specs])
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("created", "exists", "failed")}
    return {"results": results, "radius_results": radius_results, "pool_result": pool_result,
            "counts": counts}
