import logging
from fastapi import APIRouter, Depends, Query
from typing import Optional, Dict
from clients.r1_client import get_dynamic_r1_client
from r1api.client import R1Client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dpsk",
    tags=["r1-dpsk"],
)


def transform_pool_data(pool: dict, identity_group_map: Dict[str, str] = None) -> dict:
    """
    Transform RuckusONE pool data to consistent frontend format.

    RuckusONE may return identity group info in different formats:
    - identityGroup: { id, name } (nested object)
    - identityGroupId, identityGroupName (flat fields)
    - Only identityGroupId (need to look up name)

    This ensures the frontend always gets identityGroupId and identityGroupName.
    """
    result = dict(pool)  # Copy to avoid modifying original

    # Handle nested identityGroup object
    if "identityGroup" in pool and isinstance(pool["identityGroup"], dict):
        ig = pool["identityGroup"]
        if "id" in ig and "identityGroupId" not in result:
            result["identityGroupId"] = ig["id"]
        if "name" in ig and "identityGroupName" not in result:
            result["identityGroupName"] = ig["name"]

    # If we have identityGroupId but no name, try to look it up
    ig_id = result.get("identityGroupId")
    if ig_id and not result.get("identityGroupName") and identity_group_map:
        result["identityGroupName"] = identity_group_map.get(ig_id)

    # Normalize passphrase count field (API may use different names)
    if "passphraseCount" not in result:
        result["passphraseCount"] = (
            pool.get("totalPassphrases") or
            pool.get("passphraseTotal") or
            pool.get("numPassphrases") or
            None
        )

    # Ensure fields exist (even if null) for consistent frontend handling
    result.setdefault("identityGroupId", None)
    result.setdefault("identityGroupName", None)
    result.setdefault("passphraseCount", None)

    return result


async def fetch_identity_group_map(r1_client: R1Client, tenant_id: str = None) -> Dict[str, str]:
    """
    Fetch all identity groups and create an id -> name mapping.
    This enables enriching DPSK pools with identity group names.
    """
    try:
        # Fetch identity groups (first page should be enough for most cases)
        response = await r1_client.identity.query_identity_groups(
            tenant_id=tenant_id,
            page=1,
            size=500
        )

        id_map = {}

        # Handle different response formats from RuckusONE
        if isinstance(response, dict):
            # Try common response structures: { data: [...] }, { content: [...] }, or direct array at root
            data = (
                response.get("data") or
                response.get("content") or
                response.get("identityGroups") or
                []
            )
        elif isinstance(response, list):
            data = response
        else:
            data = []

        for group in data:
            if isinstance(group, dict) and "id" in group and "name" in group:
                id_map[group["id"]] = group["name"]

        logger.debug(f"Loaded {len(id_map)} identity groups for enrichment (response keys: {list(response.keys()) if isinstance(response, dict) else 'list'})")
        return id_map
    except Exception as e:
        logger.warning(f"Failed to fetch identity groups for enrichment: {e}")
        return {}


@router.get("/pools")
async def query_dpsk_pools(
    tenant_id: Optional[str] = Query(None, description="Tenant ID for MSP controllers"),
    search_string: Optional[str] = Query(None, description="Search string to filter pools"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(100, ge=1, le=500),
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """
    Query all DPSK pools (services) for a controller.

    Returns all DPSK pools without venue filtering - pools are not necessarily
    tied to a specific venue.
    """
    response = await r1_client.dpsk.query_dpsk_pools(
        tenant_id=tenant_id,
        search_string=search_string,
        page=page,
        limit=limit
    )

    # Fetch identity groups for name enrichment
    identity_group_map = await fetch_identity_group_map(r1_client, tenant_id)

    # Transform pool data to ensure consistent field names for frontend
    if isinstance(response, dict) and "data" in response:
        pools = response.get("data", [])
        # Log first pool's keys to understand the response structure
        if pools:
            logger.debug(f"DPSK pool fields: {list(pools[0].keys())}")
        response["data"] = [
            transform_pool_data(pool, identity_group_map)
            for pool in pools
        ]
    elif isinstance(response, list):
        if response:
            logger.debug(f"DPSK pool fields: {list(response[0].keys())}")
        response = [transform_pool_data(pool, identity_group_map) for pool in response]

    return response


@router.get("/pools/{pool_id}")
async def get_dpsk_pool(
    pool_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant ID for MSP controllers"),
    include_passphrase_count: bool = Query(False, description="Include live passphrase count from RuckusONE"),
    r1_client: R1Client = Depends(get_dynamic_r1_client)
):
    """Get details of a specific DPSK pool."""
    response = await r1_client.dpsk.get_dpsk_pool(pool_id=pool_id, tenant_id=tenant_id)

    if isinstance(response, dict):
        # Fetch identity groups for name enrichment
        identity_group_map = await fetch_identity_group_map(r1_client, tenant_id)
        result = transform_pool_data(response, identity_group_map)

        # Optionally fetch live passphrase count
        if include_passphrase_count:
            try:
                pp_result = await r1_client.dpsk.query_passphrases(
                    pool_id=pool_id,
                    tenant_id=tenant_id,
                    page=1,
                    limit=1
                )
                # Try various field names the API might use
                total = pp_result.get('totalElements')
                if total is None:
                    total = pp_result.get('total')
                if total is None:
                    total = pp_result.get('totalCount')
                if total is None:
                    total = len(pp_result.get('data', pp_result.get('content', [])))
                result['passphraseCount'] = total
                logger.debug(f"Pool {pool_id}: {total} passphrases")
            except Exception as e:
                logger.warning(f"Failed to get passphrase count for pool {pool_id}: {e}")
                result['passphraseCount'] = None

        return result

    return response
