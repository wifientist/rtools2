"""
Bulk WLAN Edit Router

Bulk tool for editing WiFi network advanced settings across a tenant.
Settings are modified via GET (current state) → merge changes → PUT (full object).

Workflow:
1. GET /networks - List all WiFi networks for selection
2. POST /fetch-settings - Fetch current advanced settings for selected networks
3. POST /preview - Preview changes (diff) before applying
4. POST /apply - Apply the changes with batch processing
"""

import asyncio
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from dependencies import get_current_user, get_db
from models.user import User
from models.controller import Controller
from sqlalchemy.orm import Session
from redis_client import get_redis_client

from workflow.v2.models import WorkflowJobV2, JobStatus, PhaseStatus, PhaseDefinitionV2
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.events import WorkflowEventPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bulk-wlan", tags=["Bulk WLAN Edit"])


# ============================================================================
# Constants
# ============================================================================

WORKFLOW_NAME = "bulk_wlan_edit"

# MVP settings paths within wlan.advancedCustomization
MVP_SETTINGS = [
    "clientIsolation",
    "clientIsolationPacketsType",  # nested in clientIsolationOptions
    "applicationVisibilityEnabled",
    "bssMinimumPhyRate",       # nested in radioCustomization
    "phyTypeConstraint",       # nested in radioCustomization (OFDM Only)
    "enableJoinRSSIThreshold",
    "joinRSSIThreshold",
    "dtimInterval",
    "qosMirroringEnabled",
    "qosMirroringScope",
    "enableApHostNameAdvertisement",
]


# ============================================================================
# Pydantic Models
# ============================================================================

class WlanChanges(BaseModel):
    """Settings changes to apply. Only include fields that should change."""
    clientIsolation: Optional[bool] = None
    clientIsolationPacketsType: Optional[str] = None  # "UNICAST", "MULTICAST", "UNICAST_MULTICAST"
    applicationVisibilityEnabled: Optional[bool] = None
    bssMinimumPhyRate: Optional[str] = None  # "1", "2", "5.5", "12", "24", "default"
    phyTypeConstraint: Optional[str] = None  # "OFDM" or "NONE"
    enableJoinRSSIThreshold: Optional[bool] = None
    joinRSSIThreshold: Optional[int] = None  # -90 to -60
    dtimInterval: Optional[int] = None
    qosMirroringEnabled: Optional[bool] = None
    qosMirroringScope: Optional[str] = None  # "MSCS_REQUESTS_ONLY" or "ALL_CLIENTS"
    enableApHostNameAdvertisement: Optional[bool] = None


class FetchSettingsRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    network_ids: List[str] = Field(min_length=1, max_length=50)


class ApplyRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    network_ids: List[str] = Field(min_length=1)
    changes: WlanChanges
    max_concurrent: int = Field(default=5, ge=1, le=20)


class FieldDiff(BaseModel):
    field: str
    old_value: Any
    new_value: Any



# ============================================================================
# Helpers
# ============================================================================

def extract_mvp_settings(network: dict) -> dict:
    """Extract the MVP settings from a full network object."""
    adv = (network.get("wlan") or {}).get("advancedCustomization") or {}
    radio = adv.get("radioCustomization") or {}

    isolation_opts = adv.get("clientIsolationOptions") or {}

    return {
        "clientIsolation": adv.get("clientIsolation"),
        "clientIsolationPacketsType": isolation_opts.get("packetsType"),
        "applicationVisibilityEnabled": adv.get("applicationVisibilityEnabled"),
        "bssMinimumPhyRate": radio.get("bssMinimumPhyRate"),
        "phyTypeConstraint": radio.get("phyTypeConstraint"),
        "enableJoinRSSIThreshold": adv.get("enableJoinRSSIThreshold"),
        "joinRSSIThreshold": adv.get("joinRSSIThreshold"),
        "dtimInterval": adv.get("dtimInterval"),
        "qosMirroringEnabled": adv.get("qosMirroringEnabled"),
        "qosMirroringScope": adv.get("qosMirroringScope"),
        "enableApHostNameAdvertisement": adv.get("enableApHostNameAdvertisement"),
    }


def apply_changes_to_network(network: dict, changes: dict) -> dict:
    """
    Apply settings changes to a full network object (mutates in place).

    Handles nested paths (radioCustomization) and top-level mirrors.
    """
    adv = network.setdefault("wlan", {}).setdefault("advancedCustomization", {})
    radio = adv.setdefault("radioCustomization", {})

    for field, value in changes.items():
        if value is None:
            continue

        # Fields nested in radioCustomization
        if field == "bssMinimumPhyRate":
            radio["bssMinimumPhyRate"] = value
            # Mirror at top level
            network["bssMinimumPhyRate"] = value
        elif field == "phyTypeConstraint":
            radio["phyTypeConstraint"] = value
            # Mirror: enableOfdmOnly at top level
            network["enableOfdmOnly"] = (value == "OFDM")
        # Client isolation packets type (nested in clientIsolationOptions)
        elif field == "clientIsolationPacketsType":
            iso_opts = adv.setdefault("clientIsolationOptions", {})
            iso_opts["packetsType"] = value
        # Fields directly in advancedCustomization
        elif field in (
            "clientIsolation", "applicationVisibilityEnabled",
            "enableJoinRSSIThreshold", "joinRSSIThreshold",
            "dtimInterval", "qosMirroringEnabled", "qosMirroringScope",
            "enableApHostNameAdvertisement",
        ):
            adv[field] = value

    return network


def compute_diff(current_settings: dict, changes: dict) -> List[FieldDiff]:
    """Compare current settings against desired changes, return diffs."""
    diffs = []
    for field, new_value in changes.items():
        if new_value is None:
            continue
        old_value = current_settings.get(field)
        if old_value != new_value:
            diffs.append(FieldDiff(field=field, old_value=old_value, new_value=new_value))
    return diffs


def _get_effective_tenant(controller, tenant_id):
    """Resolve effective tenant ID, raising for MSP without tenant."""
    effective = tenant_id or controller.r1_tenant_id
    if controller.controller_subtype == "MSP" and not effective:
        raise HTTPException(status_code=400, detail="tenant_id required for MSP controllers")
    return effective


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/{controller_id}/networks")
async def list_networks(
    controller_id: int,
    tenant_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all WiFi networks for network selection table."""
    controller = validate_controller_access(controller_id, current_user, db)
    effective_tenant_id = _get_effective_tenant(controller, tenant_id)
    r1_client = create_r1_client_from_controller(controller_id, db)

    result = await r1_client.networks.get_wifi_networks(effective_tenant_id)
    networks = result.get("data", [])

    return {
        "total": len(networks),
        "networks": [
            {
                "id": n.get("id"),
                "name": n.get("name"),
                "ssid": n.get("ssid"),
                "securityProtocol": n.get("securityProtocol"),
                "vlan": n.get("vlan"),
                "type": n.get("nwSubType"),
                "venues": len(n.get("venues") or []),
                "aps": n.get("aps"),
                "clients": n.get("clients"),
            }
            for n in networks
        ],
    }


@router.post("/fetch-settings")
async def fetch_settings(
    request: FetchSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Batch-fetch current advanced settings for selected networks.

    Fetches full network details via concurrent GET calls and extracts
    the MVP settings from wlan.advancedCustomization.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    effective_tenant_id = _get_effective_tenant(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(request.controller_id, db)

    semaphore = asyncio.Semaphore(10)
    results = {}
    errors = []

    async def fetch_one(network_id: str):
        async with semaphore:
            try:
                network = await r1_client.networks.get_wifi_network_by_id(
                    network_id, effective_tenant_id
                )
                results[network_id] = {
                    "name": network.get("name"),
                    "ssid": (network.get("wlan") or {}).get("ssid"),
                    "settings": extract_mvp_settings(network),
                }
            except Exception as e:
                logger.warning(f"Failed to fetch network {network_id}: {e}")
                errors.append({"network_id": network_id, "error": str(e)})

    await asyncio.gather(*[fetch_one(nid) for nid in request.network_ids])

    return {"networks": results, "errors": errors}


@router.post("/apply")
async def apply_changes(
    request: ApplyRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Apply WLAN settings changes to selected networks.

    Starts a background job that GETs each network, merges changes,
    and PUTs back the full object. Returns a job_id for progress tracking.
    """
    controller = validate_controller_access(request.controller_id, current_user, db)
    effective_tenant_id = _get_effective_tenant(controller, request.tenant_id)

    changes_dict = request.changes.dict(exclude_none=True)
    if not changes_dict:
        raise HTTPException(status_code=400, detail="No changes specified")

    job_id = str(uuid.uuid4())

    job = WorkflowJobV2(
        id=job_id,
        workflow_name=WORKFLOW_NAME,
        user_id=current_user.id,
        controller_id=request.controller_id,
        tenant_id=effective_tenant_id,
        options={"max_concurrent": request.max_concurrent},
        input_data={
            "network_ids": request.network_ids,
            "changes": changes_dict,
            "total_networks": len(request.network_ids),
        },
        phase_definitions=[
            PhaseDefinitionV2(
                id="update_wlans",
                name="Update WLAN Settings",
                executor="routers.bulk_wlan.bulk_wlan_router.run_bulk_wlan_update",
                critical=True,
                per_unit=False,
            )
        ],
        global_phase_status={"update_wlans": PhaseStatus.PENDING},
    )

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    await state_manager.save_job(job)

    logger.info(f"Created bulk WLAN edit job {job_id} for {len(request.network_ids)} networks")

    background_tasks.add_task(
        run_bulk_wlan_update,
        job,
        request.controller_id,
        request.network_ids,
        changes_dict,
        request.max_concurrent,
    )

    return {
        "job_id": job_id,
        "status": JobStatus.RUNNING,
        "message": f"Bulk WLAN update started for {len(request.network_ids)} networks.",
    }


# ============================================================================
# Background Task
# ============================================================================

async def run_bulk_wlan_update(
    job: WorkflowJobV2,
    controller_id: int,
    network_ids: List[str],
    changes: dict,
    max_concurrent: int,
):
    """Background task to apply WLAN settings changes."""
    from database import SessionLocal

    db = SessionLocal()

    try:
        logger.info(f"Starting bulk WLAN update job {job.id} for {len(network_ids)} networks")

        r1_client = create_r1_client_from_controller(controller_id, db)
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.global_phase_status["update_wlans"] = PhaseStatus.RUNNING
        await state_manager.save_job(job)

        await event_publisher.publish_event(job.id, "phase_started", {
            "phase_id": "update_wlans",
            "phase_name": "Update WLAN Settings",
        })

        results = {"updated": [], "failed": [], "unchanged": []}
        semaphore = asyncio.Semaphore(max_concurrent)
        completed = 0
        total = len(network_ids)

        async def update_single_network(network_id: str):
            nonlocal completed

            async with semaphore:
                try:
                    # GET current network
                    network = await r1_client.networks.get_wifi_network_by_id(
                        network_id, job.tenant_id
                    )
                    if not network:
                        results["failed"].append({
                            "network_id": network_id,
                            "error": "Network not found",
                        })
                        return

                    name = network.get("name", network_id)
                    ssid = (network.get("wlan") or {}).get("ssid", "")

                    # Check if changes actually differ
                    current = extract_mvp_settings(network)
                    field_diffs = compute_diff(current, changes)

                    if not field_diffs:
                        results["unchanged"].append({
                            "network_id": network_id,
                            "name": name,
                            "ssid": ssid,
                        })
                        return

                    # Merge changes into full network object
                    apply_changes_to_network(network, changes)

                    # PUT updated network
                    result = await r1_client.networks.update_wifi_network(
                        network_id=network_id,
                        payload=network,
                        tenant_id=job.tenant_id,
                        wait_for_completion=True,
                    )

                    results["updated"].append({
                        "network_id": network_id,
                        "name": name,
                        "ssid": ssid,
                        "changes": [d.dict() for d in field_diffs],
                    })

                    logger.debug(f"Updated network {name} ({network_id})")

                except Exception as e:
                    logger.error(f"Failed to update network {network_id}: {e}")
                    results["failed"].append({
                        "network_id": network_id,
                        "error": str(e),
                    })

                finally:
                    completed += 1
                    percent = int((completed / total) * 100)

                    await event_publisher.publish_event(job.id, "progress", {
                        "total_tasks": total,
                        "completed": completed,
                        "updated": len(results["updated"]),
                        "failed": len(results["failed"]),
                        "unchanged": len(results["unchanged"]),
                        "percent": percent,
                    })

                    job.global_phase_results["update_wlans"] = results
                    await state_manager.save_job(job)

        await asyncio.gather(*[update_single_network(nid) for nid in network_ids])

        # Finalize
        job.global_phase_status["update_wlans"] = PhaseStatus.COMPLETED
        job.global_phase_results["update_wlans"] = results
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()

        await state_manager.save_job(job)
        await event_publisher.job_completed(job)

        logger.info(
            f"Bulk WLAN update job {job.id} completed: "
            f"updated={len(results['updated'])}, "
            f"failed={len(results['failed'])}, "
            f"unchanged={len(results['unchanged'])}"
        )

    except Exception as e:
        logger.error(f"Bulk WLAN update job {job.id} failed: {e}", exc_info=True)

        job.status = JobStatus.FAILED
        job.completed_at = datetime.utcnow()
        job.errors.append(str(e))
        job.global_phase_status["update_wlans"] = PhaseStatus.FAILED

        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        event_publisher = WorkflowEventPublisher(redis_client)

        await state_manager.save_job(job)
        await event_publisher.job_failed(job)

    finally:
        db.close()
