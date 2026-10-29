"""
AP Group writes shared by APG Cleanup and the SZ migration's AP Mapping:
create groups, move APs into groups, add new APs straight into a group --
then confirm each against R1's own AP list, not against the task answers.

Moves are PUT /venues/{v}/apGroups/{g}/aps/{serial}; adds are
POST /venues/{v}/apGroups/{g}/aps {name, serialNumber}, which puts an AP
that isn't in the venue yet into both the venue and the group. Every
request is submitted (a few at a time), R1's tasks are awaited together for
at most wait_seconds, then the APs are read again: each row ends moved /
added (R1 shows it in the group), failed (R1 said so), or unconfirmed
(submitted, not visible yet).
"""

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONCURRENCY = 5
WAIT_SECONDS = 30
CONFIRM_POLL_SECONDS = 15


def api_error(e: Exception) -> str:
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


# R1 being slow or briefly down, not refusing the request: the write may
# still land (a timed-out task usually finishes), and the same call usually
# works a little later. Seen on big migrations: tasks not done after ~97s,
# 503 "no healthy upstream", RCG-10000 "System might be busy", and "Object
# not found" for a network created a moment before.
# Status codes only in R1's / requests' own wording: a bare "503" matches unit names ("5030@Site").
_TRANSIENT = ("did not complete after", "service unavailable", "bad gateway", "gateway time",
              "rcg-10000", "system might be busy", "no healthy upstream", "temporarily unavailable",
              "object not found", "timed out", "timeout", "connection aborted", "connection reset")
_TRANSIENT_STATUS = re.compile(r"\b50[234] (?:-|server error)")


def is_transient(e: Any) -> bool:
    text = (api_error(e) if isinstance(e, Exception) else str(e)).casefold()
    if isinstance(e, Exception):
        text += " " + str(e).casefold()
    return (isinstance(e, (TimeoutError, asyncio.TimeoutError)) or any(m in text for m in _TRANSIENT)
            or bool(_TRANSIENT_STATUS.search(text)))


def r1_call(r1_client, method: str, path: str, tenant_id: str, **kw):
    fn = getattr(r1_client, method)
    if r1_client.ec_type == "MSP":
        return fn(path, override_tenant_id=tenant_id, **kw)
    return fn(path, **kw)


async def ensure_groups(r1_client, tenant_id: str, venue_id: str,
                        names: List[str]) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
    """
    Find-or-create each group by name, one at a time (each create re-finds
    the group by name). Returns ({casefold name: id}, {casefold name: error},
    [names actually created]).
    """
    ids: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    created: List[str] = []
    for name in names:
        key = name.casefold()
        try:
            existing = await r1_client.venues.find_ap_group_by_name(tenant_id, venue_id, name)
            if existing and existing.get("id"):
                ids[key] = existing["id"]
                continue
            group = await r1_client.venues.create_ap_group(tenant_id, venue_id, name, wait_for_completion=True)
            if not (group or {}).get("id"):
                raise RuntimeError("created, but R1 did not return it by name")
            ids[key] = group["id"]
            created.append(name)
            logger.info(f"[AP Groups] Created {name} ({group['id']}) in venue {venue_id}")
        except Exception as e:
            errors[key] = api_error(e)
            logger.error(f"[AP Groups] Failed to create {name} in venue {venue_id}: {e}")
    return ids, errors, created


async def place_aps(r1_client, tenant_id: str, venue_id: str, rows: List[Dict[str, Any]],
                    read_aps: Callable[[], Awaitable[List[Dict[str, Any]]]],
                    concurrency: int = CONCURRENCY, wait_seconds: int = WAIT_SECONDS,
                    log_label: str = "[AP Groups]", confirm_seconds: int = 0,
                    progress: Optional[Callable[[str], Awaitable[None]]] = None,
                    retry_name_clash: bool = True) -> List[Dict[str, Any]]:
    """
    Submit and confirm. Each row: {serial, ap_name, to_group_id, to_group_name,
    op: "move" | "add"} (other keys are kept). Sets status
    moved | added | failed | unconfirmed and error, in place; returns rows.

    wait_seconds bounds the wait on R1's tasks. confirm_seconds keeps re-reading
    R1's AP list (every CONFIRM_POLL_SECONDS) until every submitted AP shows in
    its group or that long has passed -- a big batch (hundreds of adds) is
    still landing long after a short task wait. 0 = read once (APG Cleanup's
    request-sized moves).
    """
    sem = asyncio.Semaphore(concurrency)
    request_of: Dict[str, str] = {}

    async def submit(r: Dict[str, Any]):
        async with sem:
            try:
                if r.get("op") == "add":
                    resp = await asyncio.to_thread(
                        r1_call, r1_client, "post", f"/venues/{venue_id}/apGroups/{r['to_group_id']}/aps",
                        tenant_id, payload={"name": r.get("ap_name") or r["serial"], "serialNumber": r["serial"]})
                else:
                    resp = await asyncio.to_thread(
                        r1_call, r1_client, "put",
                        f"/venues/{venue_id}/apGroups/{r['to_group_id']}/aps/{r['serial']}", tenant_id)
                if resp.status_code not in (200, 201, 202):
                    resp.raise_for_status()
                body = resp.json() if resp.content else {}
                if isinstance(body, dict) and body.get("requestId"):
                    request_of[r["serial"]] = body["requestId"]
                r["status"] = "submitted"
            except Exception as e:
                r.update(status="failed", error=api_error(e))
                logger.error(f"{log_label} {r.get('op', 'move')} of {r.get('ap_name')} -> "
                             f"{r.get('to_group_name')} refused: {e}")

    await asyncio.gather(*[submit(r) for r in rows])

    task_results: Dict[str, Dict[str, Any]] = {}
    if request_of:
        try:
            task_results = await r1_client.await_task_completion_bulk(
                list(request_of.values()), override_tenant_id=tenant_id,
                global_timeout_seconds=wait_seconds,
            ) or {}
        except Exception as e:
            logger.warning(f"{log_label} Waiting on {len(request_of)} AP tasks stopped early: {e}")

    # Confirm against R1's AP list -- the truth, not the task answers -- re-reading
    # until everything submitted has landed, R1 refused it, or time runs out.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + confirm_seconds
    while True:
        try:
            where = {a.get("serialNumber"): a.get("apGroupId") for a in await read_aps()}
        except Exception as e:
            logger.warning(f"{log_label} Could not re-read APs after placing them: {e}")
            where = {}
        for r in rows:
            if r.get("status") != "submitted":
                continue
            task = task_results.get(request_of.get(r["serial"], ""), {})
            if where.get(r["serial"]) == r["to_group_id"]:
                r["status"] = "added" if r.get("op") == "add" else "moved"
                logger.info(f"{log_label} {r['status'].capitalize()} {r.get('ap_name')} ({r['serial']}) "
                            f"-> {r.get('to_group_name')}")
            elif task and not task.get("success") and "timeout" not in str(task.get("error", "")).lower():
                r.update(status="failed", error=task.get("error"))
        waiting = [r for r in rows if r.get("status") == "submitted"]
        if not waiting or loop.time() >= deadline:
            break
        if progress:
            done = sum(1 for r in rows if r.get("status") in ("added", "moved"))
            await progress(f"{done} AP(s) confirmed in their group, {len(waiting)} still landing in R1...")
        await asyncio.sleep(CONFIRM_POLL_SECONDS)
    for r in rows:
        if r.get("status") == "submitted":
            r.update(status="unconfirmed",
                     error="Submitted; R1 does not show it in the group yet — re-read in a minute")

    # R1 needs AP names unique within the venue. An add refused for a taken
    # name (one the plan didn't see -- the venue changed since it read it) is
    # retried once as "<name>_<serial>".
    clashed = [r for r in rows if retry_name_clash and r.get("op") == "add" and r.get("status") == "failed"
               and "already exists" in str(r.get("error") or "").lower()
               and not str(r.get("ap_name") or "").endswith(f"_{r['serial']}")]
    if clashed:
        for r in clashed:
            r["renamed_from"] = r.get("ap_name")
            r.update(ap_name=f"{r.get('ap_name') or 'AP'}_{r['serial']}", status=None, error=None)
            logger.info(f"{log_label} AP name '{r['renamed_from']}' is taken in R1 -- retrying as '{r['ap_name']}'")
        await place_aps(r1_client, tenant_id, venue_id, clashed, read_aps, concurrency, wait_seconds,
                        log_label, confirm_seconds, progress, retry_name_clash=False)
    return rows
