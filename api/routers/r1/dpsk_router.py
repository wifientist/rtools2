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


def _normalize_id(uuid_str: str) -> str:
    """Strip dashes from UUID strings for consistent comparison.
    R1 APIs are inconsistent — some endpoints return UUIDs with dashes,
    others without."""
    return uuid_str.replace("-", "") if uuid_str else ""


def transform_pool_data(
    pool: dict,
    ig_id_to_name: Dict[str, str] = None,
    pool_id_to_ig: Dict[str, dict] = None
) -> dict:
    """
    Transform RuckusONE pool data to consistent frontend format.

    Identity group association can come from multiple sources:
    1. Pool has identityGroup: { id, name } (nested object)
    2. Pool has identityGroupId (flat field) — look up name in ig_id_to_name
    3. Identity group has dpskPoolId pointing to this pool — use pool_id_to_ig reverse map

    All ID lookups normalize UUIDs (strip dashes) since R1 is inconsistent.
    """
    result = dict(pool)  # Copy to avoid modifying original

    # Handle nested identityGroup object
    if "identityGroup" in pool and isinstance(pool["identityGroup"], dict):
        ig = pool["identityGroup"]
        if "id" in ig and "identityGroupId" not in result:
            result["identityGroupId"] = ig["id"]
        if "name" in ig and "identityGroupName" not in result:
            result["identityGroupName"] = ig["name"]

    # If we have identityGroupId but no name, try to look it up (normalized)
    ig_id = result.get("identityGroupId")
    if ig_id and not result.get("identityGroupName") and ig_id_to_name:
        result["identityGroupName"] = ig_id_to_name.get(_normalize_id(ig_id))

    # Reverse lookup: if pool still has no identity group info, check if any
    # identity group references this pool via dpskPoolId (normalized)
    if not result.get("identityGroupName") and pool_id_to_ig:
        pool_id = result.get("id")
        if pool_id:
            normalized_pool_id = _normalize_id(pool_id)
            if normalized_pool_id in pool_id_to_ig:
                ig_info = pool_id_to_ig[normalized_pool_id]
                if not result.get("identityGroupId"):
                    result["identityGroupId"] = ig_info["id"]
                result["identityGroupName"] = ig_info["name"]

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


async def fetch_identity_group_maps(r1_client: R1Client, tenant_id: str = None) -> tuple:
    """
    Fetch all identity groups and create two mappings:
    1. ig_id_to_name: identity group ID -> name (for pools that have identityGroupId)
    2. pool_id_to_ig: DPSK pool ID -> {id, name} (reverse map for pools whose identity
       group references them via dpskPoolId)
    """
    try:
        ig_id_to_name = {}
        pool_id_to_ig = {}

        # Paginate through all identity groups (0-based, Spring Data style)
        page = 0
        total_elements = None
        total_pages = None
        while True:
            response = await r1_client.identity.query_identity_groups(
                tenant_id=tenant_id,
                page=page,
                size=500
            )

            # Handle different response formats from RuckusONE
            if isinstance(response, dict):
                data = (
                    response.get("data") or
                    response.get("content") or
                    response.get("identityGroups") or
                    []
                )
                if page == 0:
                    total_elements = response.get("totalElements")
                    total_pages = response.get("totalPages")
            elif isinstance(response, list):
                data = response
            else:
                data = []

            prev_count = len(ig_id_to_name)
            for group in data:
                if isinstance(group, dict) and "id" in group and "name" in group:
                    group_id = group["id"]
                    group_name = group["name"]
                    # Store under both original and normalized keys for lookup flexibility
                    ig_id_to_name[group_id] = group_name
                    ig_id_to_name[_normalize_id(group_id)] = group_name
                    # Build reverse map: dpskPoolId -> identity group info
                    dpsk_pool_id = group.get("dpskPoolId")
                    if dpsk_pool_id:
                        ig_info = {"id": group_id, "name": group_name}
                        pool_id_to_ig[dpsk_pool_id] = ig_info
                        pool_id_to_ig[_normalize_id(dpsk_pool_id)] = ig_info

            # Stop if: no new unique groups found (R1 returning duplicates)
            if len(ig_id_to_name) == prev_count and page > 0:
                logger.debug(f"Page {page} returned no new identity groups, stopping pagination")
                break
            # Stop if: R1 tells us this is the last page
            if isinstance(response, dict) and response.get("last") is True:
                break
            # Stop if: we know totalPages and have fetched them all
            if total_pages and page >= total_pages:
                break
            if not data:
                break
            page += 1
            if page > 50:  # Safety limit
                break

        unique_groups = len(ig_id_to_name) // 2  # Each group stored under 2 keys
        unique_reverse = len(pool_id_to_ig) // 2
        logger.debug(
            f"Loaded {unique_groups} identity groups for enrichment "
            f"({unique_reverse} have dpskPoolId reverse mapping) "
            f"[totalElements={total_elements}, totalPages={total_pages}, pages_fetched={page}]"
        )
        return ig_id_to_name, pool_id_to_ig
    except Exception as e:
        logger.warning(f"Failed to fetch identity groups for enrichment: {e}")
        return {}, {}


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

    # Fetch identity groups for name enrichment (both forward and reverse maps)
    ig_id_to_name, pool_id_to_ig = await fetch_identity_group_maps(r1_client, tenant_id)

    # Transform pool data to ensure consistent field names for frontend
    # R1 API returns Spring Data format with "content" key, but also handle "data" for compatibility
    if isinstance(response, dict):
        pools_key = "content" if "content" in response else "data" if "data" in response else None
        if pools_key:
            pools = response.get(pools_key, [])
            if pools:
                logger.debug(f"DPSK pool fields: {list(pools[0].keys())}")
            response[pools_key] = [
                transform_pool_data(pool, ig_id_to_name, pool_id_to_ig)
                for pool in pools
            ]

            # Fallback: fetch individual identity groups for pools still unmatched
            # (the query endpoint may not return all groups)
            enriched = response[pools_key]
            unmatched = [p for p in enriched if not p.get("identityGroupName") and p.get("identityGroupId")]
            if unmatched:
                logger.debug(f"Fetching {len(unmatched)} identity groups individually (not in query results)")
                for p in unmatched:
                    ig_id = p["identityGroupId"]
                    try:
                        ig = await r1_client.identity.get_identity_group(
                            group_id=ig_id, tenant_id=tenant_id
                        )
                        if isinstance(ig, dict) and ig.get("name"):
                            p["identityGroupName"] = ig["name"]
                            logger.debug(f"Resolved identity group for pool '{p.get('name')}': {ig['name']}")
                    except Exception as e:
                        logger.debug(f"Failed to fetch identity group {ig_id}: {e}")

            matched = sum(1 for p in enriched if p.get("identityGroupName"))
            logger.debug(f"Enrichment result: {matched}/{len(enriched)} pools have identityGroupName")
    elif isinstance(response, list):
        if response:
            logger.debug(f"DPSK pool fields: {list(response[0].keys())}")
        response = [transform_pool_data(pool, ig_id_to_name, pool_id_to_ig) for pool in response]

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
        # Fetch identity groups for name enrichment (both forward and reverse maps)
        ig_id_to_name, pool_id_to_ig = await fetch_identity_group_maps(r1_client, tenant_id)
        result = transform_pool_data(response, ig_id_to_name, pool_id_to_ig)

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
