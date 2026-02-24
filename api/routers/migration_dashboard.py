"""
Migration Dashboard — SZ to R1 Progress Tracking

Single endpoint that queries all EC tenants under an MSP controller
and returns aggregate AP/venue counts for a progress dashboard.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from models.user import User, RoleEnum
from models.controller import Controller
from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from constants.access import MIGRATION_DASHBOARD_DOMAINS
from dependencies import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/migration-dashboard",
    tags=["Migration Dashboard"],
)


@router.get("/progress/{controller_id}")
async def get_migration_progress(
    controller_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get migration progress across all EC tenants for an MSP controller.

    Returns AP counts and venue counts per EC tenant, plus totals.
    Requires an MSP-type RuckusONE controller.
    Access restricted to specific companies and super users.
    """
    user_domain = current_user.company.domain if current_user.company else None
    if current_user.role != RoleEnum.super and user_domain not in MIGRATION_DASHBOARD_DOMAINS:
        raise HTTPException(status_code=403, detail="Access restricted")

    controller = validate_controller_access(controller_id, current_user, db)

    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Requires a RuckusONE controller")
    if controller.controller_subtype != "MSP":
        raise HTTPException(
            status_code=400,
            detail=f"Requires an MSP controller. '{controller.name}' is type '{controller.controller_subtype}'.",
        )

    r1_client = create_r1_client_from_controller(controller_id, db)

    # 1. Get all EC tenants
    ecs_response = await r1_client.msp.get_msp_ecs()
    ec_list = ecs_response.get("data", [])

    if not ec_list:
        return {
            "status": "success",
            "data": {
                "total_aps": 0,
                "total_venues": 0,
                "total_ecs": 0,
                "errors": 0,
                "tenants": [],
            },
        }

    # 2. Query each EC for AP count and venue count in parallel
    semaphore = asyncio.Semaphore(10)

    async def fetch_ec_stats(ec: dict) -> dict:
        tenant_id = ec["id"]
        tenant_name = ec.get("name", "Unknown")
        ap_count = 0
        venue_count = 0
        error = None

        async with semaphore:
            try:
                # AP count: lightweight query with pageSize=1, just read totalCount
                ap_result, venue_result = await asyncio.gather(
                    asyncio.to_thread(
                        r1_client.post,
                        "/venues/aps/query",
                        payload={
                            "fields": ["name"],
                            "page": 0,
                            "pageSize": 1,
                        },
                        override_tenant_id=tenant_id,
                    ),
                    asyncio.to_thread(
                        r1_client.get,
                        "/venues",
                        override_tenant_id=tenant_id,
                    ),
                    return_exceptions=True,
                )

                # Parse AP count
                if not isinstance(ap_result, Exception) and ap_result.ok:
                    ap_data = ap_result.json()
                    ap_count = ap_data.get("totalCount", len(ap_data.get("data", [])))
                elif isinstance(ap_result, Exception):
                    error = str(ap_result)

                # Parse venue count
                if not isinstance(venue_result, Exception) and venue_result.ok:
                    venue_data = venue_result.json()
                    if isinstance(venue_data, list):
                        venue_count = len(venue_data)
                    elif isinstance(venue_data, dict):
                        venue_count = venue_data.get(
                            "totalCount", len(venue_data.get("data", []))
                        )
                elif isinstance(venue_result, Exception) and not error:
                    error = str(venue_result)

            except Exception as e:
                logger.warning(f"Error fetching stats for EC '{tenant_name}': {e}")
                error = str(e)

        return {
            "id": tenant_id,
            "name": tenant_name,
            "ap_count": ap_count,
            "venue_count": venue_count,
            "error": error,
        }

    # 3. Execute all EC queries in parallel
    ec_stats = await asyncio.gather(
        *[fetch_ec_stats(ec) for ec in ec_list],
        return_exceptions=True,
    )

    # 4. Process results
    tenants = []
    for i, result in enumerate(ec_stats):
        if isinstance(result, Exception):
            tenants.append({
                "id": ec_list[i]["id"],
                "name": ec_list[i].get("name", "Unknown"),
                "ap_count": 0,
                "venue_count": 0,
                "error": str(result),
            })
        else:
            tenants.append(result)

    total_aps = sum(t["ap_count"] for t in tenants)
    total_venues = sum(t["venue_count"] for t in tenants)
    errors = sum(1 for t in tenants if t.get("error"))

    return {
        "status": "success",
        "data": {
            "total_aps": total_aps,
            "total_venues": total_venues,
            "total_ecs": len(tenants),
            "errors": errors,
            "tenants": sorted(tenants, key=lambda t: t["ap_count"], reverse=True),
        },
    }
