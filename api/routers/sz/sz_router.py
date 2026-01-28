"""
SmartZone API Router

Provides endpoints for interacting with SmartZone controllers,
including AP inventory queries and migration support.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import httpx
from szapi.client import SZClient
from clients.sz_client_deps import get_dynamic_sz_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sz/{controller_id}",
)


@router.get("/domains")
async def get_domains(
    index: int = 0,
    list_size: int = 1000,
    recursively: bool = False,
    include_self: bool = False,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get domains from the SmartZone controller

    Args:
        index: The index of the first entry to be retrieved (default: 0)
        list_size: The maximum number of entries to be retrieved (default: 1000, max: 1000)
        recursively: Get domain list recursively (default: False)
        include_self: Get domain list include self (default: False)

    Returns:
        Dictionary with domains list
    """
    try:
        async with sz_client:
            domains = await sz_client.zones.get_domains(
                index=index,
                list_size=list_size,
                recursively=recursively,
                include_self=include_self
            )
            return {
                "status": "success",
                "data": domains,
                "count": len(domains)
            }
    except ValueError as e:
        # Authentication or connection errors from SmartZone client
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch domains: {str(e)}")


@router.get("/zones")
async def get_zones(
    domain_id: str = None,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get zones from the SmartZone controller

    Args:
        domain_id: Optional domain ID. If provided, returns zones within that domain.
                  If not provided, returns all top-level domains.

    Returns:
        Dictionary with zones list
    """
    try:
        async with sz_client:
            zones = await sz_client.zones.get_zones(domain_id=domain_id)
            return {
                "status": "success",
                "data": zones,
                "count": len(zones)
            }
    except ValueError as e:
        # Authentication or connection errors from SmartZone client
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch zones: {str(e)}")


@router.get("/zones/{zone_id}/aps")
async def get_zone_aps(
    zone_id: str,
    page: int = 0,
    limit: int = 1000,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all APs in a specific zone

    Args:
        zone_id: Zone/domain UUID
        page: Page number (default 0)
        limit: Results per page (default 1000, max 1000)

    Returns:
        Dictionary with APs list and pagination info
    """
    try:
        async with sz_client:
            result = await sz_client.aps.get_aps_by_zone(zone_id, page, limit)
            return {
                "status": "success",
                "data": result.get("list", []),
                "totalCount": result.get("totalCount", 0),
                "hasMore": result.get("hasMore", False)
            }
    except ValueError as e:
        # Authentication or connection errors
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        # HTTP errors from SmartZone
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        logger.exception(f"Failed to fetch APs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch APs: {str(e)}")


@router.get("/aps")
async def get_all_aps(
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all APs across all zones in the SmartZone controller.
    This is useful for migration workflows where you need to see
    all available APs.

    Returns:
        Dictionary with all APs list
    """
    try:
        async with sz_client:
            aps = await sz_client.aps.get_all_aps()
            return {
                "status": "success",
                "data": aps,
                "count": len(aps)
            }
    except ValueError as e:
        # Authentication or connection errors from SmartZone client
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch all APs: {str(e)}")


@router.get("/aps/{ap_mac}")
async def get_ap_details(
    ap_mac: str,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get detailed information for a specific AP

    Args:
        ap_mac: AP MAC address

    Returns:
        Dictionary with AP details
    """
    try:
        async with sz_client:
            ap = await sz_client.aps.get_ap_details(ap_mac)
            return {
                "status": "success",
                "data": ap
            }
    except ValueError as e:
        # Authentication or connection errors from SmartZone client
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"AP not found: {str(e)}")


@router.get("/aps-formatted")
async def get_aps_for_r1_import(
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all APs formatted for RuckusONE import.
    This endpoint returns AP data in the format needed for migration to R1.

    Returns:
        Dictionary with formatted APs ready for R1 import
    """
    try:
        async with sz_client:
            aps = await sz_client.aps.get_all_aps()

            # Format each AP for R1 import
            formatted_aps = [
                sz_client.aps.format_ap_for_r1_import(ap)
                for ap in aps
            ]

            return {
                "status": "success",
                "data": formatted_aps,
                "count": len(formatted_aps)
            }
    except ValueError as e:
        # Authentication or connection errors from SmartZone client
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to format APs: {str(e)}")


# ============================================================================
# Switch Endpoints
# ============================================================================

@router.get("/domains/{domain_id}/switchgroups")
async def get_domain_switchgroups(
    domain_id: str,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all switch groups in a specific domain by querying switches
    and extracting unique switch group IDs

    Args:
        domain_id: Domain UUID

    Returns:
        Dictionary with switch groups list
    """
    try:
        async with sz_client:
            switchgroups = await sz_client.switches.get_switch_groups_by_domain(domain_id)
            return {
                "status": "success",
                "data": switchgroups,
                "count": len(switchgroups)
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch switch groups: {str(e)}")


@router.get("/switchgroups/{switchgroup_id}")
async def get_switchgroup_details(
    switchgroup_id: str,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get details for a specific switch group

    Args:
        switchgroup_id: Switch Group UUID

    Returns:
        Dictionary with switch group details
    """
    try:
        async with sz_client:
            switchgroup = await sz_client.switches.get_switchgroup_details(switchgroup_id)
            return {
                "status": "success",
                "data": switchgroup
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Switch group not found: {str(e)}")


@router.get("/switches")
async def get_all_switches(
    page: int = 1,
    limit: int = 1000,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all switches managed by SmartZone

    Args:
        page: Page number (default 1, SmartZone uses 1-based pagination)
        limit: Results per page (default 1000, max 1000)

    Returns:
        Dictionary with switches list and pagination info
    """
    try:
        async with sz_client:
            result = await sz_client.switches.get_all_switches(page=page, limit=limit)
            return {
                "status": "success",
                "data": result.get("list", []),
                "totalCount": result.get("totalCount", 0),
                "hasMore": result.get("hasMore", False)
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch switches: {str(e)}")


@router.get("/domains/{domain_id}/switches")
async def get_domain_switches(
    domain_id: str,
    page: int = 1,
    limit: int = 1000,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all switches in a specific domain

    Args:
        domain_id: Domain UUID
        page: Page number (default 1, SmartZone uses 1-based pagination)
        limit: Results per page (default 1000, max 1000)

    Returns:
        Dictionary with switches list filtered by domain
    """
    try:
        async with sz_client:
            domain_switches = await sz_client.switches.get_switches_by_domain(
                domain_id=domain_id,
                page=page,
                limit=limit
            )
            return {
                "status": "success",
                "data": domain_switches,
                "count": len(domain_switches)
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch domain switches: {str(e)}")


@router.get("/switchgroups/{switchgroup_id}/switches")
async def get_switchgroup_switches(
    switchgroup_id: str,
    page: int = 1,
    limit: int = 1000,
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all switches in a specific switch group

    Args:
        switchgroup_id: Switch Group UUID
        page: Page number (default 1, SmartZone uses 1-based pagination)
        limit: Results per page (default 1000, max 1000)

    Returns:
        Dictionary with switches list filtered by switch group
    """
    try:
        async with sz_client:
            switchgroup_switches = await sz_client.switches.get_switches_by_switchgroup(
                switchgroup_id=switchgroup_id,
                page=page,
                limit=limit
            )
            return {
                "status": "success",
                "data": switchgroup_switches,
                "count": len(switchgroup_switches)
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        raise HTTPException(status_code=status_code, detail=f"SmartZone API error: {status_code} - {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch switch group switches: {str(e)}")
