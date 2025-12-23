"""
Cleanup Utilities

Handles cleanup of partially created resources on workflow failure
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


async def cleanup_job_resources(
    job_id: str,
    created_resources: Dict[str, List[Dict]],
    r1_client,
    tenant_id: str
) -> Dict[str, int]:
    """
    Clean up all resources created by a job

    Deletes in reverse dependency order:
    1. Passphrases
    2. DPSK Pools
    3. Identity Groups
    4. Policy Sets

    Args:
        job_id: Job ID
        created_resources: Dict of resource type -> list of resources
        r1_client: R1Client instance
        tenant_id: Tenant ID

    Returns:
        Dict of resource_type -> count deleted
    """
    logger.info(f"🧹 Cleaning up resources for job {job_id}")

    deleted_counts = {
        'passphrases': 0,
        'dpsk_pools': 0,
        'identity_groups': 0,
        'policy_sets': 0
    }

    # Step 1: Delete passphrases
    passphrases = created_resources.get('passphrases', [])
    for pp in passphrases:
        try:
            pp_id = pp.get('passphrase_id') or pp.get('id')
            pool_id = pp.get('dpsk_pool_id')
            if pp_id and pool_id:
                await r1_client.dpsk.delete_passphrase(pp_id, pool_id, tenant_id)
                deleted_counts['passphrases'] += 1
                logger.info(f"  🗑️  Deleted passphrase: {pp.get('userName')}")
        except Exception as e:
            logger.error(f"  ❌ Failed to delete passphrase {pp_id}: {str(e)}")

    # Step 2: Delete DPSK pools
    dpsk_pools = created_resources.get('dpsk_pools', [])
    for pool in dpsk_pools:
        try:
            # Only delete if we created it (not if it already existed)
            if pool.get('created'):
                pool_id = pool.get('dpsk_pool_id') or pool.get('id')
                if pool_id:
                    await r1_client.dpsk.delete_dpsk_pool(pool_id, tenant_id)
                    deleted_counts['dpsk_pools'] += 1
                    logger.info(f"  🗑️  Deleted DPSK pool: {pool.get('name')}")
        except Exception as e:
            logger.error(f"  ❌ Failed to delete DPSK pool {pool_id}: {str(e)}")

    # Step 3: Delete identity groups
    identity_groups = created_resources.get('identity_groups', [])
    for ig in identity_groups:
        try:
            # Only delete if we created it
            if ig.get('created'):
                ig_id = ig.get('identity_group_id') or ig.get('id')
                if ig_id:
                    await r1_client.identity.delete_identity_group(ig_id, tenant_id)
                    deleted_counts['identity_groups'] += 1
                    logger.info(f"  🗑️  Deleted identity group: {ig.get('name')}")
        except Exception as e:
            logger.error(f"  ❌ Failed to delete identity group {ig_id}: {str(e)}")

    # Step 4: Delete policy sets (if any)
    policy_sets = created_resources.get('policy_sets', [])
    for ps in policy_sets:
        try:
            if ps.get('created'):
                ps_id = ps.get('policy_set_id') or ps.get('id')
                if ps_id:
                    await r1_client.policy_sets.delete_policy_set(ps_id, tenant_id)
                    deleted_counts['policy_sets'] += 1
                    logger.info(f"  🗑️  Deleted policy set: {ps.get('name')}")
        except Exception as e:
            logger.error(f"  ❌ Failed to delete policy set {ps_id}: {str(e)}")

    total_deleted = sum(deleted_counts.values())
    logger.info(f"✅ Cleanup complete: {total_deleted} resources deleted")

    return deleted_counts
