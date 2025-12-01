"""
SmartZone API Router

Provides endpoints for interacting with SmartZone controllers,
including AP inventory queries and migration support.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import httpx
from szapi.client import SZClient
from clients.sz_client_deps import get_dynamic_sz_client

router = APIRouter(
    prefix="/sz/{controller_id}",
)


@router.get("/zones")
async def get_zones(
    sz_client: SZClient = Depends(get_dynamic_sz_client)
) -> Dict[str, Any]:
    """
    Get all zones (domains) from the SmartZone controller

    Returns:
        Dictionary with zones list
    """
    try:
        async with sz_client:
            zones = await sz_client.zones.get_zones()
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
        import traceback
        traceback.print_exc()  # Print full traceback to logs
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
