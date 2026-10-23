"""
DPSK pools in a tenant, and the one a venue's DPSK networks use.

Usually one DPSK pool serves every DPSK network in a venue, so the pool the
venue's existing DPSK networks are linked to is the natural choice for a
new or unlinked one. Used by WLAN Grabber (new DPSK networks) and WLAN
Activator (DPSK networks that lost or never got their link).

Reads only.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.wlan_grabber import dpsk_pool_suggestion

logger = logging.getLogger(__name__)


def _rows(resp: Any) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("content") or []
    return []


def is_dpsk(network: Dict[str, Any]) -> bool:
    return str(network.get("nwSubType") or "").lower() == "dpsk"


async def list_dpsk_pools(r1_client, tenant_id: str) -> List[Dict[str, Any]]:
    """Every DPSK pool in the tenant as {id, name}. The query is 1-indexed (page=0 is a 500)."""
    pools: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await r1_client.dpsk.query_dpsk_pools(tenant_id=tenant_id, page=page, limit=100)
        rows = _rows(resp)
        pools.extend(rows)
        total = resp.get("totalCount", len(pools)) if isinstance(resp, dict) else len(pools)
        if not rows or len(pools) >= total or page > 50:
            return [{"id": p["id"], "name": p.get("name")} for p in pools if p.get("id")]
        page += 1


async def venue_pool_links(r1_client, tenant_id: str, venue_id: str,
                           networks: List[Dict[str, Any]], concurrency: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """network id -> the pools linked to it, for the DPSK networks active in the venue."""
    sem = asyncio.Semaphore(concurrency)
    links: Dict[str, List[Dict[str, Any]]] = {}
    venue_dpsk = [n for n in networks if is_dpsk(n) and n.get("id")
                  and venue_id in {v.get("venueId") for v in n.get("venueApGroups") or []}]

    async def one(nid: str):
        async with sem:
            try:
                links[nid] = _rows(await r1_client.networks.get_dpsk_services_on_network(nid, tenant_id))
            except Exception as e:
                logger.warning(f"Could not read DPSK pools on network {nid}: {e}")

    await asyncio.gather(*[one(n["id"]) for n in venue_dpsk])
    return links


async def venue_pool_suggestion(r1_client, tenant_id: str, venue_id: str, venue_name: str,
                                networks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{pool_id, pool_name, reason} for the pool the venue's DPSK networks use most, or None."""
    links = await venue_pool_links(r1_client, tenant_id, venue_id, networks)
    return dpsk_pool_suggestion(links, venue_name or "this venue")
