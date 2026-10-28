"""
Venue Checkup Router

One R1 venue's settings, read-only except for the per-AP-model ones.

- POST /venue-checkup/read          -> APs by model, every per-model setting list
                                       (venue and each AP Group), and the venue's
                                       other AP settings as raw JSON
- POST /venue-checkup/model-setting -> change or drop ONE model's entry in one
                                       per-model list
- POST /venue-checkup/model-lan-specific -> set or clear one model's PoE record
                                       by name, hidden models included

R1 can hold a record for a model missing from the venue's catalog (a beta
H375 on 2026-10-28): the lists and capabilities never show it, only
GET /venues/{v}/apModels/{model}/lanPortSpecificSettings by name does (empty
body = no record). The read probes HIDDEN_MODEL_CANDIDATES plus any names
the user adds. Writes to such a model fail R1's capability check
(WIFI-20067) -- the error is passed through as-is.

The per-model settings (LAN ports, LED, band mode, antennas, USB) are each one
list on the venue (or AP Group): GET returns [{model, ...}], PUT replaces the
whole list. So a change is GET -> swap/remove the model's entry -> PUT the full
list -> GET again. The response carries the entry before and after; a delete
whose model is still in the read-back is reported as such, not as success
(R1 may re-materialise a default entry -- unverified as of 2026-10-28).
"""

import asyncio
import logging
from collections import Counter
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.user import User
from routers.tenant_scope import resolve_tenant_id
from services import ap_group_ops

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/venue-checkup", tags=["Venue Checkup"])

READ_CONCURRENCY = 8

# key -> (label, venue path suffix, also on AP Groups)
MODEL_SETTINGS: Dict[str, tuple] = {
    "lan_ports": ("LAN ports", "apModelLanPortSettings", False),
    "led": ("LED", "apModelLedSettings", False),
    "band_mode": ("Band mode", "apModelBandModeSettings", True),
    "external_antenna": ("External antenna", "apModelExternalAntennaSettings", True),
    "antenna_type": ("Antenna type", "apModelAntennaTypeSettings", True),
    "usb": ("USB port", "apModelUsbPortSettings", False),
}

# Names probed by name for a stored record when absent from the venue's
# capability catalog. Not exhaustive; the page lets the user add more.
HIDDEN_MODEL_CANDIDATES = [
    "H375", "H360", "H370", "H570", "H670", "R370", "R570", "R575", "R670", "R675", "R770", "R775",
    "R860", "R870", "T370", "T570", "T670", "T770", "T750", "T750SE", "H500", "R300", "R500",
    "R600", "R700", "C110", "T504", "T811", "R500E", "R310", "R320",
]

# The venue's other AP settings, shown raw. Leases, channel tables, topology
# and capabilities are left out: large and not settings.
VENUE_SETTINGS: List[tuple] = [
    ("Radio", "apRadioSettings"),
    ("Cellular", "apCellularSettings"),
    ("Mesh", "apMeshSettings"),
    ("Management VLAN", "apManagementTrafficVlanSettings"),
    ("Load balancing", "apLoadBalancingSettings"),
    ("Client admission control", "apClientAdmissionControlSettings"),
    ("BSS coloring", "apBssColoringSettings"),
    ("Directed multicast", "apDirectedMulticastSettings"),
    ("mDNS fencing", "apMulticastDnsFencingSettings"),
    ("DoS protection", "apDosProtectionSettings"),
    ("RADIUS options", "apRadiusOptions"),
    ("Reboot timeout", "apRebootTimeoutSettings"),
    ("Smart monitor", "apSmartMonitorSettings"),
    ("TLS key enhanced", "apTlsKeyEnhancedSettings"),
    ("Rogue AP", "rogueApSettings"),
    ("Syslog", "syslogSettings"),
    ("SNMP agent", "snmpAgentSettings"),
    ("AAA", "aaaSettings"),
    ("Wi-Fi", "wifiSettings"),
]


# ==================== Models ====================

class VenueRequest(BaseModel):
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None


class ReadRequest(VenueRequest):
    probe_models: List[str] = []


class LanSpecificRequest(VenueRequest):
    model: str
    poe_mode: Optional[str] = None     # None clears
    poe_out: Optional[bool] = False


class ModelSettingRequest(VenueRequest):
    setting: str
    model: str
    action: Literal["update", "delete"]
    ap_group_id: Optional[str] = None
    entry: Optional[Dict[str, Any]] = None


# ==================== Helpers ====================

def _check_controller(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be RuckusONE, "
                                                    f"got {controller.controller_type}")
    return controller


_api_error = ap_group_ops.api_error
_call = ap_group_ops.r1_call


def _setting_path(venue_id: str, setting: str, ap_group_id: Optional[str]) -> str:
    _, suffix, on_groups = MODEL_SETTINGS[setting]
    if ap_group_id:
        if not on_groups:
            raise HTTPException(status_code=400, detail=f"{setting} is venue-level only")
        return f"/venues/{venue_id}/apGroups/{ap_group_id}/{suffix}"
    return f"/venues/{venue_id}/{suffix}"


async def _get(r1_client, tenant_id: str, path: str) -> Dict[str, Any]:
    """{ok, data} or {ok: False, error} -- one failed read never sinks the page."""
    try:
        resp = await asyncio.to_thread(_call, r1_client, "get", path, tenant_id)
        if not resp.ok:
            return {"ok": False, "status": resp.status_code, "error": _api_error(_HttpError(resp))}
        return {"ok": True, "data": resp.json() if resp.content else None}
    except Exception as e:
        return {"ok": False, "error": _api_error(e)}


class _HttpError(Exception):
    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}: {response.text[:300]}")
        self.response = response


def _list_key(data: Any) -> Optional[str]:
    """
    AP Group per-model settings come back as {useVenueSettings, <x>Settings: [...]},
    the venue ones as a bare list. The key holding the list, or None for a list.
    """
    if isinstance(data, dict):
        return next((k for k, v in data.items() if isinstance(v, list)), None)
    return None


def _entries(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    key = _list_key(data)
    return data.get(key) or [] if key else []


async def _get_raw(r1_client, tenant_id: str, path: str) -> Any:
    resp = await asyncio.to_thread(_call, r1_client, "get", path, tenant_id)
    if not resp.ok:
        raise _HttpError(resp)
    data = resp.json() if resp.content else []
    if not isinstance(data, (list, dict)):
        raise HTTPException(status_code=502, detail=f"Unexpected {type(data).__name__} from {path}")
    return data


async def _put_and_wait(r1_client, tenant_id: str, path: str, body: Any) -> None:
    """PUT, then wait out R1's async task; a failed task raises with R1's reason."""
    resp = await asyncio.to_thread(_call, r1_client, "put", path, tenant_id, payload=body)
    if resp.status_code not in (200, 201, 202, 204):
        raise _HttpError(resp)
    result = resp.json() if resp.content else {}
    if resp.status_code == 202 and isinstance(result, dict) and result.get("requestId"):
        try:
            await r1_client.await_task_completion(result["requestId"], override_tenant_id=tenant_id)
        except Exception as e:
            raise Exception(f"{e} (request {result['requestId']})")


# ==================== Endpoints ====================

@router.post("/read")
@require_beta()
async def read_venue(
    request: ReadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything the page shows. Read-only."""
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    venue_id = request.venue_id

    try:
        venue, aps_resp, groups_resp = await asyncio.gather(
            r1_client.venues.get_venue(tenant_id, venue_id),
            r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id),
            _get(r1_client, tenant_id, f"/venues/{venue_id}/apGroups"),
        )
    except Exception as e:
        logger.exception(f"[Venue Checkup] R1 read failed for venue {venue_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the venue from R1: {_api_error(e)}")

    aps = [{
        "serial": a.get("serialNumber"), "name": a.get("name"), "model": a.get("model"),
        "status": a.get("status"), "firmware": a.get("firmwareVersion"),
        "ap_group_id": a.get("apGroupId"), "ap_group_name": a.get("apGroupName"),
    } for a in (aps_resp.get("data") or [])]

    groups_raw = groups_resp.get("data") if groups_resp["ok"] else []
    if isinstance(groups_raw, dict):
        groups_raw = groups_raw.get("data") or []
    groups = [{"id": g.get("id"), "name": g.get("name") or ("(default)" if g.get("isDefault") else g.get("id")),
               "is_default": bool(g.get("isDefault"))} for g in (groups_raw or []) if g.get("id")]

    sem = asyncio.Semaphore(READ_CONCURRENCY)

    async def limited(path: str) -> Dict[str, Any]:
        async with sem:
            return await _get(r1_client, tenant_id, path)

    model_jobs = {k: limited(f"/venues/{venue_id}/{v[1]}") for k, v in MODEL_SETTINGS.items()}
    group_jobs = {(g["id"], k): limited(f"/venues/{venue_id}/apGroups/{g['id']}/{v[1]}")
                  for g in groups for k, v in MODEL_SETTINGS.items() if v[2]}
    venue_jobs = {suffix: limited(f"/venues/{venue_id}/{suffix}") for _, suffix in VENUE_SETTINGS}
    caps_job = limited(f"/venues/{venue_id}/apModelCapabilities")

    keys = list(model_jobs) + list(group_jobs) + list(venue_jobs) + ["__caps"]
    results = await asyncio.gather(*model_jobs.values(), *group_jobs.values(), *venue_jobs.values(), caps_job)
    by_key = dict(zip(keys, results))

    caps = by_key["__caps"]
    catalog = sorted({m.get("model") for m in ((caps.get("data") or {}).get("apModels") or [])
                      if m.get("model")}) if caps["ok"] and isinstance(caps.get("data"), dict) else []

    # Hidden models: names outside the catalog, probed one by one.
    probe = []
    for name in HIDDEN_MODEL_CANDIDATES + [m.strip() for m in request.probe_models]:
        if name and name not in catalog and name not in probe:
            probe.append(name)
    probed = await asyncio.gather(*[
        limited(f"/venues/{venue_id}/apModels/{name}/lanPortSpecificSettings") for name in probe])
    # A 4xx ("This AP model [R700] is not supported.") means no record, like an empty body.
    hidden_models = [{"model": name, **r} for name, r in zip(probe, probed)
                     if (r["ok"] and r.get("data") not in (None, "", {}))
                     or (not r["ok"] and not 400 <= (r.get("status") or 500) < 500)]

    model_settings = [{
        "key": k, "label": v[0], "path": v[1], "on_groups": v[2], **by_key[k],
    } for k, v in MODEL_SETTINGS.items()]

    group_settings = []
    for g in groups:
        for k, v in MODEL_SETTINGS.items():
            if not v[2]:
                continue
            r = dict(by_key[(g["id"], k)])
            if r["ok"]:
                raw = r.get("data")
                # A group on venue settings only echoes the venue list.
                if isinstance(raw, dict) and raw.get("useVenueSettings", False):
                    continue
                r["data"] = _entries(raw)
                if not r["data"]:
                    continue
            group_settings.append({"ap_group_id": g["id"], "ap_group_name": g["name"],
                                   "key": k, "label": v[0], **r})

    venue_settings = [{"label": label, "path": suffix, **by_key[suffix]} for label, suffix in VENUE_SETTINGS]

    counts = Counter(a["model"] or "?" for a in aps)
    models = [{"model": m, "ap_count": n,
               "firmware": sorted({a["firmware"] for a in aps if (a["model"] or "?") == m and a["firmware"]})}
              for m, n in sorted(counts.items())]

    return {
        "venue_id": venue_id,
        "venue_name": (venue or {}).get("name") or "",
        "venue": venue,
        "aps": aps,
        "models": models,
        "groups": groups,
        "model_settings": model_settings,
        "group_settings": group_settings,
        "venue_settings": venue_settings,
        "catalog": catalog,
        "hidden_models": hidden_models,
        "probed_models": probe,
    }


@router.post("/model-setting")
@require_beta()
async def change_model_setting(
    request: ModelSettingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Replace (update) or remove (delete) one model's entry in one per-model
    list. The rest of the list goes back exactly as R1 returned it.
    """
    if request.setting not in MODEL_SETTINGS:
        raise HTTPException(status_code=400, detail=f"Unknown setting {request.setting}")
    if request.action == "update" and not isinstance(request.entry, dict):
        raise HTTPException(status_code=400, detail="An update needs the new entry")

    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    path = _setting_path(request.venue_id, request.setting, request.ap_group_id)
    model = request.model

    try:
        raw = await _get_raw(r1_client, tenant_id, path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read {path}: {_api_error(e)}")
    list_key = _list_key(raw)
    if isinstance(raw, dict) and not list_key:
        raise HTTPException(status_code=502, detail=f"No settings list in {path}: {list(raw)}")
    current = _entries(raw)

    before = next((e for e in current if e.get("model") == model), None)
    if request.action == "delete":
        if before is None:
            raise HTTPException(status_code=409, detail=f"{model} has no entry in {path} any more")
        body = [e for e in current if e.get("model") != model]
    else:
        entry = {**request.entry, "model": model}
        body = [entry if e.get("model") == model else e for e in current]
        if before is None:
            body.append(entry)
    if list_key:
        # An AP Group keeps its own list only while it is off venue settings.
        body = {**raw, list_key: body, "useVenueSettings": False}

    logger.info(f"[Venue Checkup] {current_user.email} {request.action} {model} on {path} "
                f"(tenant {tenant_id}); before={before}")
    try:
        await _put_and_wait(r1_client, tenant_id, path, body)
    except Exception as e:
        logger.error(f"[Venue Checkup] PUT {path} failed: {e}")
        return {"status": "failed", "path": path, "model": model, "before": before, "after": before,
                "sent": body, "error": _api_error(e)}

    try:
        after_list = _entries(await _get_raw(r1_client, tenant_id, path))
    except Exception as e:
        return {"status": "unconfirmed", "path": path, "model": model, "before": before, "after": None,
                "sent": body, "error": f"Written, but the read-back failed: {_api_error(e)}"}
    after = next((e for e in after_list if e.get("model") == model), None)

    if request.action == "delete" and after is not None:
        status = "still_present"
    elif request.action == "update" and after is None:
        status = "unconfirmed"
    else:
        status = "done"
    return {"status": status, "path": path, "model": model, "before": before, "after": after,
            "sent": body, "list_after": after_list}


@router.post("/model-lan-specific")
@require_beta()
async def set_lan_specific(
    request: LanSpecificRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Set (or, with poe_mode None, clear) one model's PoE record by name --
    the only handle on a hidden model. Read back either way.
    """
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    path = f"/venues/{request.venue_id}/apModels/{request.model}/lanPortSpecificSettings"

    before = await _get(r1_client, tenant_id, path)
    body = {"poeMode": request.poe_mode, "poeOut": bool(request.poe_out)}
    logger.info(f"[Venue Checkup] {current_user.email} PUT {path} {body} (tenant {tenant_id}); "
                f"before={before.get('data')}")
    error = None
    try:
        await _put_and_wait(r1_client, tenant_id, path, body)
    except Exception as e:
        logger.error(f"[Venue Checkup] PUT {path} failed: {e}")
        error = _api_error(e)
    after = await _get(r1_client, tenant_id, path)

    now = after.get("data")
    if error:
        status = "failed"
    elif request.poe_mode is None:
        status = "done" if now in (None, "", {}) else "still_present"
    else:
        status = "done" if isinstance(now, dict) and now.get("poeMode") == request.poe_mode else "unconfirmed"
    return {"status": status, "path": path, "model": request.model, "sent": body,
            "before": before.get("data"), "after": after.get("data"), "error": error}
