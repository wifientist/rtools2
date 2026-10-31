"""
Bulk WLAN Delete Router

Bulk tool for deleting WiFi networks across a tenant. Shares the network
selector with Bulk WLAN Edit (GET /bulk-wlan/{controller_id}/networks).

Workflow:
1. GET /bulk-wlan/{controller_id}/networks - List networks for selection (edit router)
2. POST /bulk-wlan/delete/check - Fresh venue / AP / client counts for the selection
3. POST /bulk-wlan/delete - Deactivate or delete in the background, returns a job_id

action="deactivate" only removes each network from its venues; "delete"
deactivates where needed and then deletes. Running deactivate first and
delete second splits the job into two passes when that is wanted.

A network still in use (any venue, AP or client) is only touched when its id
is in override_ids — the user confirmed it one by one in the UI. The job
re-reads the counts when it starts, so a network that picked up clients
between the check and the run is skipped rather than acted on unconfirmed.
"""

import asyncio
import logging
import uuid
from typing import List, Literal, Optional, Dict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from dependencies import get_current_user, get_db
from models.user import User
from sqlalchemy.orm import Session
from redis_client import get_redis_client

from workflow.v2.models import WorkflowJobV2, JobStatus, PhaseStatus, PhaseDefinitionV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher
from routers.bulk_wlan.bulk_wlan_router import _get_effective_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk-wlan", tags=["Bulk WLAN Delete"])


# ============================================================================
# Constants
# ============================================================================

WORKFLOW_NAME = "bulk_wlan_delete"

# R1 needs a moment after venue deactivations before the network DELETE
# sticks (same eventual-consistency retry as the cleanup delete_networks phase).
DELETE_RETRY_DELAYS = [3, 6, 9]

PHASE_NAMES = {"deactivate": "Deactivate WLANs", "delete": "Delete WLANs"}


# ============================================================================
# Pydantic Models
# ============================================================================

class DeleteCheckRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    network_ids: List[str] = Field(min_length=1)


class DeleteRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    network_ids: List[str] = Field(min_length=1)
    action: Literal["deactivate", "delete"] = "delete"
    # In-use networks the user explicitly confirmed. Anything in use that is
    # not listed here is skipped.
    override_ids: List[str] = Field(default_factory=list)
    max_concurrent: int = Field(default=3, ge=1, le=10)
    stagger_seconds: float = Field(default=5, ge=0, le=60)


# ============================================================================
# Helpers
# ============================================================================

def network_usage(network: dict) -> dict:
    """Venue / AP / client usage of a network from the /wifiNetworks/query row."""
    venue_ids = sorted({
        g.get("venueId")
        for g in (network.get("venueApGroups") or [])
        if g.get("venueId")
    })
    return {
        "id": network.get("id"),
        "name": network.get("name"),
        "ssid": network.get("ssid"),
        "venue_ids": venue_ids,
        "venues": len(venue_ids),
        "aps": network.get("apCount") or 0,
        "clients": network.get("clientCount") or 0,
    }


def is_in_use(usage: dict, action: str = "delete") -> bool:
    """
    Whether acting on this network needs the user's explicit confirmation.

    Deactivating is by definition aimed at networks that are on a venue, so
    only live APs or clients count against it; a delete wants all three at 0.
    """
    if action == "deactivate":
        return bool(usage["aps"] or usage["clients"])
    return bool(usage["venues"] or usage["aps"] or usage["clients"])


def describe_usage(usage: dict) -> str:
    return f"{usage['venues']} venue(s), {usage['aps']} AP(s), {usage['clients']} client(s)"


async def fetch_usage(r1_client, tenant_id: Optional[str]) -> Dict[str, dict]:
    """Current usage for every network in the tenant, keyed by network id."""
    result = await r1_client.networks.get_wifi_networks(tenant_id)
    return {
        n["id"]: network_usage(n)
        for n in result.get("data", [])
        if n.get("id")
    }


async def delete_network(r1_client, network_id: str, tenant_id: Optional[str]) -> None:
    """
    DELETE one network and wait for R1's async task.

    networks.delete_wifi_network returns a 4xx body as if it were a result,
    so a refused delete would read as success; this raises instead. A 404
    means it is already gone, which is the outcome we wanted.
    """
    if r1_client.ec_type == "MSP" and tenant_id:
        response = await asyncio.to_thread(r1_client.delete, f"/wifiNetworks/{network_id}", override_tenant_id=tenant_id)
    else:
        response = await asyncio.to_thread(r1_client.delete, f"/wifiNetworks/{network_id}")

    if response.status_code == 404:
        return
    if response.status_code >= 400:
        response.raise_for_status()
    if response.status_code == 202:
        request_id = (response.json() if response.content else {}).get("requestId")
        if request_id:
            await r1_client.await_task_completion(request_id, override_tenant_id=tenant_id)


def _api_error_message(e: Exception) -> str:
    """The R1 error message from an httpx error, else str(e)."""
    error_msg = str(e)
    if hasattr(e, "response") and e.response is not None:
        try:
            body = e.response.json()
            api_errors = body.get("errors", [])
            if api_errors:
                error_msg = "; ".join(
                    err.get("message", err.get("reason", str(err)))
                    for err in api_errors
                )
        except Exception:
            pass
    return error_msg


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/delete/check")
async def check_delete(
    request: DeleteCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fresh usage counts for the selected networks.

    The selector's counts are as old as the page load; this re-reads them
    right before the user decides what to delete.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    effective_tenant_id = _get_effective_tenant(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)

    usage = await fetch_usage(r1_client, effective_tenant_id)

    networks = []
    missing = []
    for nid in request.network_ids:
        u = usage.get(nid)
        if u is None:
            missing.append(nid)
            continue
        networks.append({
            **u,
            "in_use": is_in_use(u, "delete"),
            "in_use_for_deactivate": is_in_use(u, "deactivate"),
        })

    return {"networks": networks, "missing": missing}


@router.post("/delete")
async def delete_networks(
    request: DeleteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Deactivate or delete the selected networks.

    Starts a background job that deactivates each confirmed in-use network
    from its venues and, for action="delete", then deletes it. Returns a
    job_id for progress tracking.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    effective_tenant_id = _get_effective_tenant(controller, request.tenant_id)

    network_ids = list(dict.fromkeys(request.network_ids))
    override_ids = sorted(set(request.override_ids) & set(network_ids))

    job_id = str(uuid.uuid4())

    job = WorkflowJobV2(
        id=job_id,
        workflow_name=WORKFLOW_NAME,
        user_id=current_user.id,
        controller_id=request.controller_id,
        tenant_id=effective_tenant_id,
        options={
            "max_concurrent": request.max_concurrent,
            "stagger_seconds": request.stagger_seconds,
        },
        input_data={
            "action": request.action,
            "network_ids": network_ids,
            "override_ids": override_ids,
            "total_networks": len(network_ids),
        },
        phase_definitions=[
            PhaseDefinitionV2(
                id="delete_wlans",
                name=PHASE_NAMES[request.action],
                executor="routers.bulk_wlan.bulk_wlan_delete_router.run_bulk_wlan_delete",
                critical=True,
                per_unit=False,
            )
        ],
        global_phase_status={"delete_wlans": PhaseStatus.PENDING},
    )

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    await state_manager.save_job(job)

    logger.info(
        f"Created bulk WLAN {request.action} job {job_id} for {len(network_ids)} networks "
        f"({len(override_ids)} in-use overrides)"
    )

    background_tasks.add_task(
        run_bulk_wlan_delete,
        job,
        request.controller_id,
        network_ids,
        override_ids,
        request.action,
        request.max_concurrent,
        request.stagger_seconds,
    )

    return {
        "job_id": job_id,
        "status": JobStatus.RUNNING,
        "message": f"Bulk WLAN {request.action} started for {len(network_ids)} networks.",
    }


# ============================================================================
# Background Task
# ============================================================================

async def run_bulk_wlan_delete(
    job: WorkflowJobV2,
    controller_id: int,
    network_ids: List[str],
    override_ids: List[str],
    action: str,
    max_concurrent: int,
    stagger_seconds: float,
):
    """
    Background task to deactivate or delete WLANs.

    The deletes don't depend on each other, so this is a plain launcher loop
    rather than a workflow graph: one delete starts every stagger_seconds,
    with at most max_concurrent in flight (an in-use network's venue
    deactivations can outlast the stagger).
    """
    from database import SessionLocal

    db = SessionLocal()
    overrides = set(override_ids)

    try:
        logger.info(f"Starting bulk WLAN {action} job {job.id} for {len(network_ids)} networks")

        r1_client = create_r1_client_from_controller(controller_id, db)
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.global_phase_status["delete_wlans"] = PhaseStatus.RUNNING
        await state_manager.save_job(job)

        await event_publisher.publish_event(job.id, "phase_started", {
            "phase_id": "delete_wlans",
            "phase_name": PHASE_NAMES[action],
        })

        # Re-read usage now: the user confirmed against counts that may be stale.
        usage = await fetch_usage(r1_client, job.tenant_id)

        # "done" is deleted for action=delete, deactivated for action=deactivate.
        results = {"action": action, "done": [], "failed": [], "skipped": []}
        semaphore = asyncio.Semaphore(max_concurrent)
        completed = 0
        total = len(network_ids)

        async def report_progress():
            nonlocal completed
            completed += 1
            await event_publisher.publish_event(job.id, "progress", {
                "total_tasks": total,
                "completed": completed,
                "done": len(results["done"]),
                "failed": len(results["failed"]),
                "skipped": len(results["skipped"]),
                "percent": int((completed / total) * 100),
            })
            job.global_phase_results["delete_wlans"] = results
            await state_manager.save_job(job)

        async def delete_single_network(network_id: str):
            """Runs holding a semaphore slot the launcher acquired."""
            u = usage.get(network_id)
            name = (u or {}).get("name") or network_id
            ssid = (u or {}).get("ssid") or ""
            try:
                if u is None:
                    results["skipped"].append({
                        "network_id": network_id, "name": name, "ssid": ssid,
                        "reason": "Not found (already deleted?)",
                    })
                    return

                if is_in_use(u, action) and network_id not in overrides:
                    results["skipped"].append({
                        "network_id": network_id, "name": name, "ssid": ssid,
                        "reason": f"In use and not confirmed: {describe_usage(u)}",
                    })
                    return

                if action == "deactivate" and not u["venue_ids"]:
                    results["skipped"].append({
                        "network_id": network_id, "name": name, "ssid": ssid,
                        "reason": "Not activated on any venue",
                    })
                    return

                # R1 won't delete a network still activated on a venue.
                for venue_id in u["venue_ids"]:
                    await r1_client.venues.deactivate_ssid_from_venue(
                        tenant_id=job.tenant_id,
                        venue_id=venue_id,
                        wifi_network_id=network_id,
                        wait_for_completion=True,
                    )

                if action == "deactivate":
                    results["done"].append({
                        "network_id": network_id, "name": name, "ssid": ssid,
                        "was_in_use": is_in_use(u, action),
                        "usage": describe_usage(u),
                    })
                    logger.info(f"Deactivated network {name} ({network_id}) from {u['venues']} venue(s)")
                    return

                last_error = None
                for attempt, delay in enumerate([0] + DELETE_RETRY_DELAYS):
                    if delay:
                        await asyncio.sleep(delay)
                    try:
                        await delete_network(r1_client, network_id, job.tenant_id)
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        # Only a just-deactivated network is worth retrying.
                        if not u["venue_ids"]:
                            break
                        logger.info(f"Delete of {name} retry {attempt + 1}: {e}")
                if last_error is not None:
                    raise last_error

                results["done"].append({
                    "network_id": network_id, "name": name, "ssid": ssid,
                    "was_in_use": is_in_use(u, action),
                    "usage": describe_usage(u),
                })
                logger.info(f"Deleted network {name} ({network_id})")

            except Exception as e:
                logger.error(f"Failed to {action} network {network_id}: {e}")
                results["failed"].append({
                    "network_id": network_id, "name": name, "ssid": ssid,
                    "error": _api_error_message(e),
                })
            finally:
                semaphore.release()
                await report_progress()

        tasks = []
        for i, nid in enumerate(network_ids):
            await semaphore.acquire()
            if i > 0 and stagger_seconds:
                await asyncio.sleep(stagger_seconds)
            tasks.append(asyncio.create_task(delete_single_network(nid)))
        await asyncio.gather(*tasks)

        # Finalize
        job.global_phase_status["delete_wlans"] = PhaseStatus.COMPLETED
        job.global_phase_results["delete_wlans"] = results
        job.completed_at = datetime.utcnow()

        for f in results["failed"]:
            label = f.get("ssid") or f.get("name") or f.get("network_id", "unknown")
            job.errors.append(f"{label}: {f.get('error', 'Unknown error')}")
        for s in results["skipped"]:
            label = s.get("ssid") or s.get("name") or s.get("network_id", "unknown")
            job.errors.append(f"{label}: skipped — {s['reason']}")

        if results["failed"] or results["skipped"]:
            job.status = JobStatus.PARTIAL
            if not results["done"]:
                job.global_phase_status["delete_wlans"] = PhaseStatus.FAILED
        else:
            job.status = JobStatus.COMPLETED

        await state_manager.save_job(job)
        await event_publisher.job_completed(job)

        logger.info(
            f"Bulk WLAN {action} job {job.id} completed: "
            f"done={len(results['done'])}, "
            f"failed={len(results['failed'])}, "
            f"skipped={len(results['skipped'])}"
        )

    except Exception as e:
        logger.error(f"Bulk WLAN {action} job {job.id} failed: {e}", exc_info=True)

        job.status = JobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.errors.append(str(e))
        job.global_phase_status["delete_wlans"] = PhaseStatus.FAILED

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        await state_manager.save_job(job)
        await event_publisher.job_failed(job)

    finally:
        db.close()
