"""
WLAN Grabber Router

Create in R1 the WLANs of one SZ zone that the R1 tenant does not have yet:

- POST /wlan-grabber/preview          -> read both sides; one row per SZ WLAN,
  the RADIUS services the Enterprise ones use, the tenant's DPSK pools
  (read-only)
- POST /wlan-grabber/v2/plan           -> re-read the chosen WLANs, start a
  WlanGrabberWorkflow job and validate it
- GET  /wlan-grabber/v2/{id}/plan      -> plan results
- POST /wlan-grabber/v2/{id}/confirm   -> confirm and run
- GET  /wlan-grabber/v2/{id}/graph     -> workflow graph

Networks are created tenant-wide and left unactivated (WLAN Activator does
that). The optional venue is context only: the DPSK pool its DPSK networks
are tied to is suggested as the shared pool for new DPSK networks.

DPSK pool: one shared pool for every DPSK network (an existing R1 pool, or
a new one created with its identity group), and any row may name another
pool or none instead.

Rows, statuses and notes: services/wlan_grabber.py. Phases:
workflow/phases/wlan_grabber.py.

Secrets: the passphrase never goes to the browser -- the plan re-reads each
chosen WLAN from SZ. It and any typed RADIUS secret go to their own Redis
hash with a TTL (never into the job, whose options and input are stored and
shown); the phase that uses one deletes it. R1 profile rows are reduced by
r1_profile_summary before they leave.
"""

import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, model_validator
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import create_sz_client_from_controller, validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.user import User
from r1api.constants import DpskPassphraseFormat
from redis_client import get_redis_client
from routers.tenant_scope import resolve_tenant_id
from services.dpsk_pool_context import list_dpsk_pools, venue_pool_suggestion
from services.sz_migration.radius_choices import r1_profile_summary, sz_server_details
from services.portal_key import controller_has_portal_key
from services.wlan_grabber import (
    EXISTS, SZ_HOTSPOT_TYPES, build_rows, build_spec, is_wispr_network,
    radius_services, wispr_key_sources, wispr_r1_check, wispr_r1_norms,
)
from workflow.events import WorkflowEventPublisher
from workflow.phases.wlan_grabber import SECRET_KEY, SECRET_TTL
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.failsafe import fail_job
from workflow.v2.graph import DependencyGraph
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.workflows.wlan_grabber import WlanGrabberWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wlan-grabber", tags=["WLAN Grabber"])

READ_CONCURRENCY = 10
VALIDATE_PHASE = "validate_wlan_grabber"


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
    r1_type: Optional[Literal["psk", "open", "aaa", "dpsk", "wispr"]] = None   # None = its default mode
    # DPSK only: None = the shared pool, "none" = no pool, else an R1 pool id.
    dpsk_pool: Optional[str] = None


class SharedPool(BaseModel):
    """The DPSK pool every DPSK network uses unless its row says otherwise."""
    mode: Literal["existing", "new", "none"]
    pool_id: Optional[str] = None
    pool_name: Optional[str] = None     # display only, for an existing pool
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


class WisprChoice(BaseModel):
    """
    The integration key: by default each WLAN's SZ signature signing key
    ("sz"; none = no key, MAC/IP not encrypted). Overrides: the key saved on
    the R1 controller ("controller", read when each network is created), copy
    it from an R1 WISPr network ("network") or type it ("typed", write-only).
    encrypt_mac_ip None = encrypt exactly when there is a key.
    """
    key_source: Literal["sz", "controller", "network", "typed"] = "sz"
    key_network_id: Optional[str] = None
    integration_key: Optional[SecretStr] = None
    encrypt_mac_ip: Optional[bool] = None
    enable_dhcp: bool = False

    @model_validator(mode="after")
    def _consistent(self):
        if self.key_source == "network" and not self.key_network_id:
            raise ValueError("'network' needs key_network_id")
        if self.key_source == "typed":
            key = self.integration_key.get_secret_value() if self.integration_key else ""
            if not re.fullmatch(r"[a-zA-Z0-9]{16}", key):
                raise ValueError("The integration key must be 16 letters or digits")
        return self


class PlanRequest(BaseModel):
    sz_controller_id: int
    zone_id: str
    r1_controller_id: int
    tenant_id: Optional[str] = None
    venue_id: Optional[str] = None      # context only; recorded on the job
    wlans: List[WlanChoice] = Field(..., min_length=1)
    radius: Dict[str, RadiusDecision] = Field(default_factory=dict)
    dpsk_pool: SharedPool = Field(default_factory=lambda: SharedPool(mode="none"))
    wispr: Optional[WisprChoice] = None
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


def _hotspot_portal_ids(raws: List[Dict[str, Any]]) -> List[str]:
    return sorted({(r.get("portalServiceProfile") or {}).get("id") for r in raws
                   if r.get("type") in SZ_HOTSPOT_TYPES} - {None})


async def _sz_portals(sz_client, zone_id: str, portal_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """SZ hotspot (WISPr) portal profiles by id; a failed read is {"_error": ...}."""
    async def one(pid):
        try:
            return pid, await sz_client._request(
                "GET", f"/{sz_client.api_version}/rkszones/{zone_id}/portals/hotspot/{pid}")
        except Exception as e:
            logger.warning(f"[WLAN Grabber] Could not read SZ hotspot portal {pid}: {e}")
            return pid, {"id": pid, "_error": str(e)}

    return dict(await asyncio.gather(*[one(p) for p in portal_ids]))


async def _wispr_r1_context(r1_client, tenant_id: str, tenant_networks: List[Dict[str, Any]]):
    """The tenant's WISPr networks: key sources (no key leaves) and how most are set up."""
    sem = asyncio.Semaphore(READ_CONCURRENCY)

    async def full(n):
        async with sem:
            try:
                return await r1_client.networks.get_wifi_network_by_id(n["id"], tenant_id)
            except Exception as e:
                logger.warning(f"[WLAN Grabber] Could not read guest network {n['id']}: {e}")
                return None

    fulls = [f for f in await asyncio.gather(*[full(n) for n in tenant_networks if is_wispr_network(n)]) if f]
    return wispr_key_sources(fulls), wispr_r1_norms(fulls)



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
        dpsk_pools = await list_dpsk_pools(r1_client, tenant_id)
    except Exception as e:
        logger.warning(f"[WLAN Grabber] Could not list DPSK pools: {e}")
        warnings.append("Could not list R1 DPSK pools; DPSK networks can only be created without one")

    if request.venue_id:
        dpsk_suggestion = await venue_pool_suggestion(
            r1_client, tenant_id, request.venue_id, (venue or {}).get("name"), tenant_networks)

    # ── SZ: the list carries no security or VLAN, so detail each WLAN ──
    sz_client = create_sz_client_from_controller(request.sz_controller_id, db)
    try:
        async with sz_client:
            zone, sz_wlans = await _sz_wlans(sz_client, request.zone_id)
            portals = await _sz_portals(sz_client, request.zone_id, _hotspot_portal_ids(sz_wlans))
            result = build_rows(sz_wlans, tenant_networks, request.venue_id, portals)
            needed: Dict[str, str] = {}
            for r in result["rows"]:
                if r["status"] != EXISTS and any(
                        m in r["modes"] and not r["modes"][m]["blockers"] for m in ("aaa", "wispr")):
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

    key_sources: List[Dict[str, Any]] = []
    r1_norms: Dict[str, Any] = {}
    if any("wispr" in r["modes"] and r["status"] != EXISTS for r in result["rows"]):
        try:
            key_sources, r1_norms = await _wispr_r1_context(r1_client, tenant_id, tenant_networks)
        except Exception as e:
            logger.warning(f"[WLAN Grabber] Could not read the tenant's WISPr networks: {e}")
            warnings.append("Could not read the tenant's WISPr networks to compare SZ against")
        # Compare, never copy: each WISPr option notes where SZ differs from the tenant's norm.
        for r in result["rows"]:
            opt = r["modes"].get("wispr")
            if opt and opt.get("portal"):
                opt["notes"].extend(wispr_r1_check(opt["portal"], r1_norms))

    return {
        "zone_id": request.zone_id,
        "zone_name": (zone or {}).get("name") or "",
        "venue_name": (venue or {}).get("name") if venue else None,
        **result,
        "wispr_key_sources": key_sources,
        "controller_has_portal_key": controller_has_portal_key(request.r1_controller_id),
        "wispr_r1": r1_norms,
        "radius_services": radius_services(result["rows"], refs, r1_profiles),
        "r1_radius_profiles": r1_profiles,
        "dpsk_pools": dpsk_pools,
        "dpsk_suggestion": dpsk_suggestion,
        "warnings": warnings,
    }


class SecretCheckRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None
    r1_profile_id: str
    shared_secret: SecretStr


@router.post("/radius/check-secret")
@require_beta()
async def check_radius_secret(
    request: SecretCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Does an R1 RADIUS profile carry this shared secret (e.g. the one typed
    from SZ, which SZ never returns)? A verdict only: matches true/false, or
    null when the profile could not be read. The secret is not stored or logged.
    """
    r1_controller = _check_controller(request.r1_controller_id, "RuckusONE", current_user, db)
    tenant_id = resolve_tenant_id(r1_controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.r1_controller_id, db)
    matches = await r1_client.radius_profiles.shared_secret_equals(
        request.r1_profile_id, request.shared_secret.get_secret_value(), tenant_id)
    return {"r1_profile_id": request.r1_profile_id, "matches": matches}


# ==================== Workflow (plan / confirm) ====================

class V2PlanResponse(BaseModel):
    job_id: str
    status: str
    message: str


class V2PlanResult(BaseModel):
    job_id: str
    status: str
    valid: bool = False
    unit_count: int = 0
    summary: Dict[str, Any] = {}
    conflicts: list = []
    estimated_api_calls: int = 0
    actions: list = []
    networks_existing: List[str] = []
    wlans_skipped: List[Dict[str, str]] = []


def _brain(r1_client, state_manager, redis_client, tenant_id) -> WorkflowBrain:
    return WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(r1_client, state_manager, tenant_id=tenant_id),
        event_publisher=WorkflowEventPublisher(redis_client),
        r1_client=r1_client,
    )


async def _run_validation(job_id: str, controller_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[WLAN Grabber] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        await _brain(r1_client, state_manager, redis_client, job.tenant_id).run_validation(job)
        logger.info(f"[WLAN Grabber] Validation complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[WLAN Grabber] Validation failed for job {job_id}: {e}")
    finally:
        db.close()


async def _run_execution(job_id: str, controller_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[WLAN Grabber] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        brain = _brain(r1_client, state_manager, redis_client, job.tenant_id)
        await brain.tracker.start()
        await brain.execute_workflow(job)
        await brain.tracker.stop()
        logger.info(f"[WLAN Grabber] Execution complete for job {job_id} (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[WLAN Grabber] Execution failed for job {job_id}: {e}")
        await fail_job(None, job_id, e, source="WLAN Grabber")
    finally:
        # Whatever ran, nothing secret outlives the job.
        try:
            await (await get_redis_client()).delete(SECRET_KEY.format(job_id=job_id))
        except Exception:
            logger.warning(f"[WLAN Grabber] Could not clear secrets for job {job_id}")
        db.close()


@router.post("/v2/plan", response_model=V2PlanResponse)
@require_beta()
async def create_plan(
    request: PlanRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-read the chosen WLANs from SZ and start a job to validate. Nothing is created yet."""
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
            wispr_raws = [r for wid, r in zip(choices, raws) if choices[wid].r1_type == "wispr"]
            portals = await _sz_portals(sz_client, request.zone_id, _hotspot_portal_ids(wispr_raws))
            # _sz_wlans keeps the order of the ids it was given.
            specs = [build_spec(raw, choices[wid].r1_type, portals) for wid, raw in zip(choices, raws)]
            to_create = {
                sid: role for s in specs if s["r1_type"] in ("aaa", "wispr") and not s["blockers"]
                for sid, role in ((s["auth_service_id"], "auth"), (s["accounting_service_id"], "acct"))
                if sid and (request.radius.get(sid) and request.radius[sid].action == "create")
            }
            refs = await _sz_service_refs(sz_client, to_create) if to_create else {}
    except Exception as e:
        logger.exception(f"[WLAN Grabber] SZ re-read failed for zone {request.zone_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the zone from SmartZone: {e}")

    # ── Secrets out of the specs; the rest becomes the job's input ──
    secrets: Dict[str, str] = {}
    wlans = []
    sz_keys = not request.wispr or request.wispr.key_source == "sz"
    for spec in specs:
        passphrase = spec.pop("passphrase", None)
        if passphrase:
            secrets[f"psk:{spec['sz_wlan_id']}"] = passphrase
        sz_key = spec.pop("wispr_key", None)
        if sz_key and sz_keys:
            secrets[f"wispr_key:{spec['sz_wlan_id']}"] = sz_key
        wlans.append({"spec": spec, "dpsk_pool": choices[spec["sz_wlan_id"]].dpsk_pool})
    passphrase = sz_key = None

    snapshot = SimpleNamespace(referenced_objects={
        k: SimpleNamespace(name=(v or {}).get("name"), raw=v or {}) for k, v in refs.items()
    })
    radius: Dict[str, Dict[str, Any]] = {}
    for sid, d in request.radius.items():
        entry: Dict[str, Any] = {"action": d.action, "r1_profile_id": d.r1_profile_id}
        if d.action == "create" and sid in to_create:
            details = sz_server_details(snapshot, sid)
            entry.update(name=details["name"], primary=details["primary"],
                         secondary=details["secondary"], role=details["role"])
            secrets[f"radius:{sid}"] = d.shared_secret.get_secret_value()
        radius[sid] = entry
    if request.wispr and request.wispr.key_source == "typed":
        secrets["wispr_key"] = request.wispr.integration_key.get_secret_value()
    if (request.wispr and request.wispr.key_source == "controller"
            and not controller_has_portal_key(request.r1_controller_id)):
        raise HTTPException(status_code=400, detail="No portal integration key is saved on this RuckusONE controller")

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=tenant_id),
    )
    job = await brain.create_job(
        workflow=WlanGrabberWorkflow,
        venue_id=request.venue_id or "",
        tenant_id=tenant_id,
        controller_id=request.r1_controller_id,
        user_id=current_user.id,
        options={"add_description": request.add_description},
        input_data={
            "wlans": wlans,
            "radius": radius,
            "dpsk_pool": request.dpsk_pool.model_dump(),
            "wispr": request.wispr.model_dump(exclude={"integration_key"}) if request.wispr else {},
            "sz_controller_id": request.sz_controller_id,
            "zone_id": request.zone_id,
        },
    )
    secrets = {k: v for k, v in secrets.items() if v}
    if secrets:
        key = SECRET_KEY.format(job_id=job.id)
        await redis_client.hset(key, mapping=secrets)
        await redis_client.expire(key, SECRET_TTL)
    secrets = None
    background_tasks.add_task(_run_validation, job.id, request.r1_controller_id)

    logger.info(f"[WLAN Grabber] Plan job {job.id}: {len(wlans)} WLANs from zone {request.zone_id}")
    return V2PlanResponse(
        job_id=job.id, status="VALIDATING",
        message=f"Checking {len(wlans)} WLANs. Poll GET /wlan-grabber/v2/{job.id}/plan for results.",
    )


@router.get("/v2/{job_id}/plan", response_model=V2PlanResult)
async def get_plan(job_id: str, current_user: User = Depends(get_current_user)):
    """The plan; poll after POST /v2/plan until status is AWAITING_CONFIRMATION."""
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result = V2PlanResult(job_id=job_id, status=job.status.value, unit_count=len(job.units))
    if job.status == JobStatus.VALIDATING:
        return result
    if job.status == JobStatus.FAILED:
        result.conflicts = [{"description": e} for e in job.errors]
        return result
    if job.validation_result:
        vr = job.validation_result
        result.valid = vr.valid
        result.summary = vr.summary.model_dump() if vr.summary else {}
        result.conflicts = [c.model_dump() for c in vr.conflicts]
        result.estimated_api_calls = vr.summary.total_api_calls if vr.summary else 0
        result.actions = [
            {**a.model_dump(), "details": "; ".join(a.notes) if a.notes else None}
            for a in vr.actions
        ]
        outputs = job.global_phase_results.get(VALIDATE_PHASE, {}) or {}
        result.networks_existing = outputs.get("networks_existing", []) or []
        result.wlans_skipped = outputs.get("wlans_skipped", []) or []
    return result


@router.post("/v2/{job_id}/confirm")
async def confirm_plan(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
):
    """Confirm and run. Job must be AWAITING_CONFIRMATION with a valid plan."""
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != JobStatus.AWAITING_CONFIRMATION:
        raise HTTPException(status_code=400,
                            detail=f"Job is in '{job.status.value}' state. Expected AWAITING_CONFIRMATION.")
    if job.validation_result and not job.validation_result.valid:
        raise HTTPException(status_code=400, detail="The plan has blocking issues - fix the list and re-plan")
    background_tasks.add_task(_run_execution, job.id, job.controller_id)
    return {"job_id": job.id, "status": "RUNNING",
            "message": f"Creating {len(job.units)} networks. Poll /jobs/{job.id}/status for progress."}


@router.get("/v2/{job_id}/graph")
async def get_workflow_graph(job_id: str, current_user: User = Depends(get_current_user)):
    job = await RedisStateManagerV2(await get_redis_client()).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    graph = DependencyGraph(job.phase_definitions)
    data = graph.to_graph_data()
    return {"workflow_name": job.workflow_name, "nodes": data["nodes"], "edges": data["edges"],
            "levels": {str(k): v for k, v in graph.compute_levels().items()}}
