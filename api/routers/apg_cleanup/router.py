"""
APG Cleanup Router

One R1 venue's AP Groups: membership, WLAN mapping, rename and delete.

- POST /apg-cleanup/inventory -> read the venue; one row per AP Group (read-only)
- POST /apg-cleanup/delete    -> delete the chosen groups
- POST /apg-cleanup/rename    -> rename groups; dry_run returns the checked
                                 before/after without writing
- POST /apg-cleanup/move      -> move APs into groups (creating missing ones);
                                 dry_run likewise. AP Regroup's job, inline.

Statuses, notes and the rename rules: services/apg_cleanup.py.

Every write re-reads the venue first and re-checks against what R1 says now,
not what the page loaded: a group that gained APs since is refused.

Rename is GET -> set name -> PUT -> GET. The PUT body (VenueApGroup) carries
apSerialNumbers, so a body with only a name risks emptying the group; the
current members go back with it, and the read-back confirms the name took
and the membership did not move.

Move reads only groups and APs (not the per-network settings the inventory
needs), submits every PUT .../apGroups/{g}/aps/{serial}, waits at most
MOVE_WAIT_SECONDS for R1's tasks, then reads the APs again: each AP is
moved (R1 shows it there), failed (R1 said so), or unconfirmed (submitted,
not yet visible -- never counted as failed; AP Regroup learned that on
2026-09-16). The whole request has to fit under the proxy's 60s.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.user import User
from routers.tenant_scope import resolve_tenant_id
from services.apg_cleanup import build_inventory, check_delete, plan_moves, plan_renames

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apg-cleanup", tags=["APG Cleanup"])

READ_CONCURRENCY = 10
WRITE_CONCURRENCY = 3
MOVE_CONCURRENCY = 5
MOVE_WAIT_SECONDS = 30
MAX_MOVES = 300

GROUP_FIELDS = ["id", "name", "venueId", "description", "isDefault",
                "apSerialNumbers", "wifiNetworkIds", "clientCount"]


# ==================== Models ====================

class VenueRequest(BaseModel):
    controller_id: int
    venue_id: str
    tenant_id: Optional[str] = None


class DeleteRequest(VenueRequest):
    group_ids: List[str] = Field(..., min_length=1)


class Rename(BaseModel):
    id: str
    new_name: str


class Move(BaseModel):
    ap: str                        # serial or exact AP name
    group_id: Optional[str] = None
    group_name: Optional[str] = None


class MoveRequest(VenueRequest):
    moves: List[Move] = Field(..., min_length=1, max_length=MAX_MOVES)
    allow_create: bool = True
    dry_run: bool = False


class RenameRequest(VenueRequest):
    renames: List[Rename] = Field(..., min_length=1)
    dry_run: bool = False


# ==================== Helpers ====================

def _check_controller(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be RuckusONE, "
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


def _call(r1_client, method: str, path: str, tenant_id: str, **kw):
    fn = getattr(r1_client, method)
    if r1_client.ec_type == "MSP":
        return fn(path, override_tenant_id=tenant_id, **kw)
    return fn(path, **kw)


async def _list_groups(r1_client, tenant_id: str, venue_id: str) -> List[Dict[str, Any]]:
    """Every AP Group in the venue, with members and networks, paged to completion."""
    groups: List[Dict[str, Any]] = []
    page, page_size = 1, 100
    while page <= 100:
        resp = await r1_client.venues.query_ap_groups(
            tenant_id=tenant_id, venue_id=venue_id, fields=GROUP_FIELDS, page=page, limit=page_size,
        )
        items = resp.get("data") or []
        groups.extend(items)
        total = resp.get("totalCount")
        if len(items) < page_size or (isinstance(total, int) and len(groups) >= total):
            break
        page += 1
    # The venue filter is trusted no further than the rest of R1's filters.
    return [g for g in groups if g.get("venueId") in (None, venue_id)]


async def _read_venue(r1_client, tenant_id: str, venue_id: str) -> Dict[str, Any]:
    """The R1 poll: groups, APs, networks and each network's venue activation."""
    venue, groups, aps_resp, nets_resp = await asyncio.gather(
        r1_client.venues.get_venue(tenant_id, venue_id),
        _list_groups(r1_client, tenant_id, venue_id),
        r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id),
        r1_client.networks.get_wifi_networks(tenant_id, venue_id=venue_id),
    )
    networks = [n for n in (nets_resp.get("data") or []) if n.get("id")]

    sem = asyncio.Semaphore(READ_CONCURRENCY)
    settings: Dict[str, Optional[Dict[str, Any]]] = {}

    async def read_settings(nid: str):
        async with sem:
            try:
                resp = await asyncio.to_thread(
                    _call, r1_client, "get", f"/venues/{venue_id}/wifiNetworks/{nid}/settings", tenant_id)
                resp.raise_for_status()
                settings[nid] = resp.json() if resp.content else {}
            except Exception as e:
                logger.warning(f"[APG Cleanup] Could not read venue settings of network {nid}: {e}")
                settings[nid] = None

    await asyncio.gather(*[read_settings(n["id"]) for n in networks])

    inv = build_inventory(groups, aps_resp.get("data") or [], networks, settings, venue_id)
    return {"venue_id": venue_id, "venue_name": (venue or {}).get("name") or "", **inv}


async def _read_or_502(r1_client, tenant_id: str, venue_id: str) -> Dict[str, Any]:
    try:
        return await _read_venue(r1_client, tenant_id, venue_id)
    except Exception as e:
        logger.exception(f"[APG Cleanup] R1 read failed for venue {venue_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the venue from R1: {_api_error(e)}")


# ==================== Endpoints ====================

@router.post("/inventory")
@require_beta()
async def inventory(
    request: VenueRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every AP Group in the venue with its APs and WLANs. Read-only."""
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    return await _read_or_502(r1_client, tenant_id, request.venue_id)


@router.post("/delete")
@require_beta()
async def delete_groups(
    request: DeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete AP Groups that are still empty. One result per group."""
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    if len(set(request.group_ids)) != len(request.group_ids):
        raise HTTPException(status_code=400, detail="An AP Group appears more than once")

    r1_client = create_r1_client_from_controller(request.controller_id, db)
    current = await _read_or_502(r1_client, tenant_id, request.venue_id)
    rows = {g["id"]: g for g in current["groups"]}
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def delete_one(gid: str) -> Dict[str, Any]:
        row = rows.get(gid)
        out: Dict[str, Any] = {"id": gid, "name": row["name"] if row else gid}
        refusal = check_delete(row)
        if refusal:
            return {**out, "status": "refused", "error": refusal}
        async with sem:
            try:
                await r1_client.venues.delete_ap_group(
                    request.venue_id, gid, tenant_id=tenant_id, wait_for_completion=True)
                logger.info(f"[APG Cleanup] Deleted AP Group {out['name']} ({gid}) "
                            f"from venue {request.venue_id}")
                return {**out, "status": "deleted", "dropped_wlans": row["explicit_wlan_count"]}
            except Exception as e:
                logger.error(f"[APG Cleanup] Failed to delete AP Group {out['name']} ({gid}): {e}")
                return {**out, "status": "failed", "error": _api_error(e)}

    results = await asyncio.gather(*[delete_one(g) for g in request.group_ids])
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("deleted", "refused", "failed")}
    return {"results": results, "counts": counts}


@router.post("/rename")
@require_beta()
async def rename_groups(
    request: RenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Rename AP Groups. Checked against the venue as R1 has it now; with
    dry_run, only the check. Renames run in waves so no name is taken
    before its current holder has moved off it.
    """
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)

    current = await _read_or_502(r1_client, tenant_id, request.venue_id)
    plan = plan_renames(current["groups"], [r.model_dump() for r in request.renames])
    if request.dry_run:
        return {"dry_run": True, **plan}

    rows = {g["id"]: g for g in current["groups"]}
    planned = {r["id"]: r for r in plan["rows"]}
    path = lambda gid: f"/venues/{request.venue_id}/apGroups/{gid}"
    sem = asyncio.Semaphore(WRITE_CONCURRENCY)

    async def get_group(gid: str) -> Dict[str, Any]:
        resp = await asyncio.to_thread(_call, r1_client, "get", path(gid), tenant_id)
        resp.raise_for_status()
        return resp.json()

    async def rename_one(gid: str) -> Dict[str, Any]:
        p = planned[gid]
        out = {"id": gid, "old_name": p["old_name"], "new_name": p["new_name"]}
        async with sem:
            try:
                group = await get_group(gid)
                serials = group.get("apSerialNumbers")
                if serials is None:
                    # Spec says the GET carries them; if it ever does not, send what the AP query saw.
                    serials = [a["serial"] for a in rows[gid]["aps"]]
                body: Dict[str, Any] = {"name": p["new_name"], "apSerialNumbers": serials}
                if len(group.get("description") or "") >= 2:
                    body["description"] = group["description"]

                resp = await asyncio.to_thread(_call, r1_client, "put", path(gid), tenant_id, payload=body)
                if resp.status_code not in (200, 201, 202):
                    resp.raise_for_status()
                result = resp.json() if resp.content else {}
                if resp.status_code == 202 and result.get("requestId"):
                    await r1_client.await_task_completion(result["requestId"], override_tenant_id=tenant_id)

                after = await get_group(gid)
                out["aps_before"] = len(serials)
                out["aps_after"] = len(after.get("apSerialNumbers") or [])
                if after.get("name") != p["new_name"]:
                    return {**out, "status": "failed",
                            "error": f"R1 accepted the change but still reports '{after.get('name')}'"}
                if set(after.get("apSerialNumbers") or []) != set(serials):
                    logger.error(f"[APG Cleanup] Membership of {gid} changed on rename: "
                                 f"{len(serials)} -> {out['aps_after']} APs")
                    return {**out, "status": "renamed_membership_changed",
                            "error": f"Renamed, but membership went {len(serials)} → {out['aps_after']} APs"}
                logger.info(f"[APG Cleanup] Renamed AP Group {p['old_name']} -> {p['new_name']} ({gid})")
                return {**out, "status": "renamed"}
            except Exception as e:
                logger.error(f"[APG Cleanup] Failed to rename {p['old_name']} -> {p['new_name']}: {e}")
                return {**out, "status": "failed", "error": _api_error(e)}

    results: List[Dict[str, Any]] = []
    failed_names: set = set()
    for wave in plan["waves"]:
        # A rename whose target was held by a group that failed to move off it would collide.
        runnable, waiting = [], []
        for gid in wave:
            (waiting if planned[gid]["new_name"].casefold() in failed_names else runnable).append(gid)
        for gid in waiting:
            p = planned[gid]
            results.append({"id": gid, "old_name": p["old_name"], "new_name": p["new_name"],
                            "status": "skipped", "error": "The group holding this name was not renamed"})
        done = await asyncio.gather(*[rename_one(g) for g in runnable])
        results.extend(done)
        failed_names |= {r["old_name"].casefold() for r in done if r["status"] == "failed"}

    for r in plan["rows"]:
        if r["status"] == "blocked":
            results.append({**{k: r[k] for k in ("id", "old_name", "new_name")},
                            "status": "blocked", "error": r["error"]})

    statuses = ("renamed", "renamed_membership_changed", "failed", "skipped", "blocked")
    counts = {s: sum(1 for r in results if r["status"] == s) for s in statuses}
    return {"dry_run": False, "results": results, "counts": counts}


@router.post("/move")
@require_beta()
async def move_aps(
    request: MoveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Move APs into AP Groups, creating any group that does not exist. Checked
    against the venue as R1 has it now; with dry_run, only the check.
    """
    controller = _check_controller(request.controller_id, current_user, db)
    tenant_id = resolve_tenant_id(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)
    venue_id = request.venue_id

    async def read_aps() -> List[Dict[str, Any]]:
        return (await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)).get("data") or []

    try:
        groups, aps = await asyncio.gather(_list_groups(r1_client, tenant_id, venue_id), read_aps())
    except Exception as e:
        logger.exception(f"[APG Cleanup] R1 read failed for venue {venue_id}")
        raise HTTPException(status_code=502, detail=f"Failed to read the venue from R1: {_api_error(e)}")

    plan = plan_moves(groups, aps, [m.model_dump() for m in request.moves], request.allow_create)
    if request.dry_run:
        return {"dry_run": True, **plan}

    rows = [r for r in plan["rows"] if r["status"] == "ok"]
    results: List[Dict[str, Any]] = [{**r, "status": r["status"]} for r in plan["rows"] if r["status"] != "ok"]

    # ── Missing groups, one at a time (each create re-finds the group by name) ──
    created: Dict[str, str] = {}
    create_errors: Dict[str, str] = {}
    for name in plan["groups_to_create"]:
        try:
            group = await r1_client.venues.create_ap_group(tenant_id, venue_id, name, wait_for_completion=True)
            if not (group or {}).get("id"):
                raise RuntimeError("created, but R1 did not return it by name")
            created[name.casefold()] = group["id"]
            logger.info(f"[APG Cleanup] Created AP Group {name} ({group['id']}) in venue {venue_id}")
        except Exception as e:
            create_errors[name.casefold()] = _api_error(e)
            logger.error(f"[APG Cleanup] Failed to create AP Group {name}: {e}")
    for r in rows:
        if r["create"]:
            key = r["to_group_name"].casefold()
            r["to_group_id"] = created.get(key)
            if not r["to_group_id"]:
                r.update(status="failed", error=f"Could not create the group: {create_errors.get(key)}")

    # ── Submit every move, then wait for R1's tasks together ──
    sem = asyncio.Semaphore(MOVE_CONCURRENCY)
    request_of: Dict[str, str] = {}

    async def submit(r: Dict[str, Any]):
        async with sem:
            try:
                resp = await asyncio.to_thread(
                    _call, r1_client, "put",
                    f"/venues/{venue_id}/apGroups/{r['to_group_id']}/aps/{r['serial']}", tenant_id)
                if resp.status_code not in (200, 201, 202):
                    resp.raise_for_status()
                body = resp.json() if resp.content else {}
                if body.get("requestId"):
                    request_of[r["serial"]] = body["requestId"]
                r["status"] = "submitted"
            except Exception as e:
                r.update(status="failed", error=_api_error(e))
                logger.error(f"[APG Cleanup] Move of {r['ap_name']} -> {r['to_group_name']} refused: {e}")

    pending = [r for r in rows if r["status"] == "ok"]
    await asyncio.gather(*[submit(r) for r in pending])

    task_results: Dict[str, Dict[str, Any]] = {}
    if request_of:
        try:
            task_results = await r1_client.await_task_completion_bulk(
                list(request_of.values()), override_tenant_id=tenant_id,
                global_timeout_seconds=MOVE_WAIT_SECONDS,
            ) or {}
        except Exception as e:
            logger.warning(f"[APG Cleanup] Waiting on {len(request_of)} move tasks stopped early: {e}")

    # ── Verify against R1, not against the task answers ──
    try:
        where = {a.get("serialNumber"): a.get("apGroupId") for a in await read_aps()}
    except Exception as e:
        logger.warning(f"[APG Cleanup] Could not re-read APs after moving: {e}")
        where = {}
    for r in rows:
        if r["status"] == "submitted":
            task = task_results.get(request_of.get(r["serial"], ""), {})
            if where.get(r["serial"]) == r["to_group_id"]:
                r["status"] = "moved"
                logger.info(f"[APG Cleanup] Moved {r['ap_name']} ({r['serial']}): "
                            f"{r['from_group']} -> {r['to_group_name']}")
            elif task and not task.get("success") and "timeout" not in str(task.get("error", "")).lower():
                r.update(status="failed", error=task.get("error"))
            else:
                r.update(status="unconfirmed",
                         error="Submitted; R1 does not show it in the group yet — re-read in a minute")
        results.append(r)

    statuses = ("moved", "already", "unconfirmed", "failed", "blocked")
    counts = {s: sum(1 for r in results if r["status"] == s) for s in statuses}
    counts["groups_created"] = len(created)
    return {"dry_run": False, "results": results, "counts": counts}
