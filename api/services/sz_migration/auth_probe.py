"""
R1 auth-path facts for the migration audit.

The auditor is pure and works on snapshots; this module is the I/O that feeds
its auth-path checks. For each R1 network the audit matched to a RADIUS- or
DPSK-backed SZ WLAN, it answers two questions:

    Does the network point at an external RADIUS server?
        POST /radiusServerProfiles/query once for the tenant. Each profile row
        lists the wifiNetworkIds using it, which is the only cheap way to see
        the link -- the network object does not carry it.

    Does the network authenticate against R1's own DPSK service?
        GET /wifiNetworks/{id}/dpskServices per DPSK network, then the identity
        groups feeding each pool (dpskPoolId on the group) and their counts.

WHAT NEVER LEAVES THIS MODULE

Passphrases and shared secrets. Everything returned here is ids, names,
"ip:port" strings, counts, and booleans. The count helpers on the R1 client
drop the rows they read, and the placeholder-secret check compares inside the
client and returns yes/no. The report is still put through
audit_redaction.scrub_report() before it is returned, so a mistake here is
caught rather than shipped.
"""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

# Re-exported for the audit: the migration writes it when SZ will not give
# up a shared secret, and a profile still carrying it rejects every request.
from services.sz_migration.radius_choices import PLACEHOLDER_RADIUS_SECRET

logger = logging.getLogger(__name__)



DETAIL_CONCURRENCY = 5


async def collect_auth_facts(
    r1_client,
    tenant_id: Optional[str],
    networks: Iterable[Dict[str, Any]],
    identity_groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Gather auth-path facts for the given R1 network rows.

    Args:
        networks: R1 network rows (from the venue inventory) to probe.
        identity_groups: The tenant's identity groups from the inventory; each
            carries dpskPoolId, which is how a pool finds its groups.

    Returns:
        {
          "radius_by_network": {net_id: [{id, name, type, primary, secondary,
                                          placeholder_secret}]},
          "radius_settings":   {net_id: {enable_auth_proxy, enable_accounting_proxy}},
          "pools_by_network":  {net_id: {"pools": [{id, name, passphrase_count,
                                          identity_groups: [{id, name, identity_count}]}],
                                         "error": str | None}},
          "errors": [str],
        }
    """
    networks = [n for n in networks if n.get("id")]
    errors: List[str] = []
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
    wanted = {n["id"] for n in networks}

    # ── External RADIUS links (one tenant-wide query) ────────────────
    radius_by_network: Dict[str, List[Dict[str, Any]]] = {}
    try:
        profiles = await r1_client.radius_profiles.query_radius_profiles(tenant_id)
    except Exception as e:
        logger.warning(f"Could not query RADIUS profiles: {e}")
        errors.append(f"Could not read RADIUS server profiles: {e}")
        profiles = []

    for p in profiles:
        for net_id in p.get("wifiNetworkIds") or []:
            if net_id in wanted:
                radius_by_network.setdefault(net_id, []).append({
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "type": (p.get("type") or "").upper() or None,
                    "primary": p.get("primary"),
                    "secondary": p.get("secondary"),
                    "placeholder_secret": None,
                })

    linked_profile_ids = {
        p["id"] for rows in radius_by_network.values() for p in rows if p.get("id")
    }

    async def placeholder_check(profile_id: str):
        async with semaphore:
            return profile_id, await r1_client.radius_profiles.shared_secret_equals(
                profile_id, PLACEHOLDER_RADIUS_SECRET, tenant_id
            )

    placeholder = dict(await asyncio.gather(
        *(placeholder_check(pid) for pid in linked_profile_ids)
    ))
    for rows in radius_by_network.values():
        for p in rows:
            p["placeholder_secret"] = placeholder.get(p["id"])

    async def settings_for(net_id: str):
        async with semaphore:
            try:
                s = await r1_client.networks.get_radius_settings_on_network(net_id, tenant_id)
            except Exception as e:
                logger.warning(f"Could not read RADIUS settings on {net_id}: {e}")
                return net_id, None
            if not isinstance(s, dict):
                return net_id, None
            return net_id, {
                "enable_auth_proxy": s.get("enableAuthProxy"),
                "enable_accounting_proxy": s.get("enableAccountingProxy"),
            }

    radius_settings = {
        net_id: s for net_id, s in await asyncio.gather(
            *(settings_for(n) for n in radius_by_network)
        ) if s is not None
    }

    # ── Internal DPSK links (per DPSK network) ───────────────────────
    dpsk_networks = [
        n["id"] for n in networks
        if str(n.get("nwSubType") or n.get("type") or "").lower() == "dpsk"
    ]

    async def pools_for(net_id: str):
        async with semaphore:
            try:
                linked = await r1_client.networks.get_dpsk_services_on_network(
                    network_id=net_id, tenant_id=tenant_id
                )
            except Exception as e:
                logger.warning(f"Could not read DPSK link on {net_id}: {e}")
                return net_id, {"pools": [], "error": str(e)}
        rows = (
            linked.get("data", linked.get("content", []))
            if isinstance(linked, dict) else (linked or [])
        )
        return net_id, {
            "pools": [
                {"id": r["id"], "name": r.get("name"), "passphrase_count": None,
                 "identity_groups": []}
                for r in rows if isinstance(r, dict) and r.get("id")
            ],
            "error": None,
        }

    pools_by_network = dict(await asyncio.gather(*(pools_for(n) for n in dpsk_networks)))

    pool_ids = {
        p["id"] for v in pools_by_network.values() for p in v["pools"]
    }
    groups_by_pool: Dict[str, List[Dict[str, Any]]] = {}
    for ig in identity_groups:
        if ig.get("dpskPoolId") in pool_ids and ig.get("id"):
            groups_by_pool.setdefault(ig["dpskPoolId"], []).append(ig)

    async def passphrase_count(pool_id: str):
        async with semaphore:
            return pool_id, await r1_client.dpsk.count_passphrases(pool_id, tenant_id)

    async def identity_count(group_id: str):
        async with semaphore:
            return group_id, await r1_client.identity.count_identities_in_group(
                group_id, tenant_id
            )

    pp_counts = dict(await asyncio.gather(*(passphrase_count(p) for p in pool_ids)))
    group_ids = {ig["id"] for rows in groups_by_pool.values() for ig in rows}
    id_counts = dict(await asyncio.gather(*(identity_count(g) for g in group_ids)))

    for v in pools_by_network.values():
        for pool in v["pools"]:
            pool["passphrase_count"] = pp_counts.get(pool["id"])
            pool["identity_groups"] = [
                {"id": ig["id"], "name": ig.get("name"),
                 "identity_count": id_counts.get(ig["id"])}
                for ig in groups_by_pool.get(pool["id"], [])
            ]

    return {
        "radius_by_network": radius_by_network,
        "radius_settings": radius_settings,
        "pools_by_network": pools_by_network,
        "errors": errors,
    }
