"""
Inventory Resources Phase

Scans resources to determine what needs to be deleted.
Two modes:
1. Job-specific: Uses created_resources from original job
2. Nuclear: Audits entire venue to find ALL DPSK resources
"""

import logging
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from r1api.services.dpsk import DpskService
from r1api.services.identity import IdentityService

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Inventory resources that need to be cleaned up

    Args:
        context: Workflow context containing created_resources OR nuclear_mode flag

    Returns:
        List containing single completed task with inventory data
    """
    logger.info("Phase 1: Inventory Resources to Delete")

    nuclear_mode = context.get('nuclear_mode', False)
    created_resources = context.get('created_resources', {})
    venue_id = context.get('venue_id')
    tenant_id = context.get('tenant_id')
    r1_client = context.get('r1_client')

    logger.info(f"Inventorying resources for venue {venue_id}")
    logger.info(f"Mode: {'☢️  NUCLEAR (venue-wide audit)' if nuclear_mode else 'Job-specific'}")

    if nuclear_mode:
        # Nuclear mode - audit entire venue
        logger.warning(f"☢️  NUCLEAR MODE - Auditing ALL DPSK resources in venue {venue_id}")
        inventory = await _audit_venue_resources(r1_client, venue_id, tenant_id)
    else:
        # Job-specific mode - use created_resources
        logger.info(f"Created resources: {list(created_resources.keys())}")
        inventory = {
            'passphrases': created_resources.get('passphrases', []),
            'dpsk_pools': created_resources.get('dpsk_pools', []),
            'identities': created_resources.get('identities', []),
            'identity_groups': created_resources.get('identity_groups', []),
            'policy_sets': created_resources.get('policy_sets', []),
        }

    total_resources = sum(len(items) for items in inventory.values())

    logger.info(f"📋 Inventory Summary:")
    logger.info(f"  - Passphrases: {len(inventory['passphrases'])}")
    logger.info(f"  - DPSK Pools: {len(inventory['dpsk_pools'])}")
    logger.info(f"  - Identities: {len(inventory['identities'])}")
    logger.info(f"  - Identity Groups: {len(inventory['identity_groups'])}")
    logger.info(f"  - Policy Sets: {len(inventory['policy_sets'])}")
    logger.info(f"  - Total Resources: {total_resources}")

    if total_resources == 0:
        logger.warning("⚠️ No resources found to clean up")

    task = Task(
        id="inventory_resources",
        name=f"Inventory {total_resources} resources ({nuclear_mode and 'NUCLEAR' or 'Job-specific'})",
        task_type="inventory",
        status=TaskStatus.COMPLETED,
        output_data={
            'inventory': inventory,
            'total_resources': total_resources,
            'nuclear_mode': nuclear_mode
        }
    )

    return [task]


async def _audit_venue_resources(r1_client, venue_id: str, tenant_id: str = None) -> Dict[str, List]:
    """
    Audit ALL DPSK resources in a venue (for nuclear mode)

    Args:
        r1_client: R1Client instance for API calls
        venue_id: Venue ID to audit
        tenant_id: Tenant/EC ID (for MSP)

    Returns:
        Inventory dict in same format as created_resources
    """
    logger.info(f"🔍 Auditing venue {venue_id} for all DPSK resources")
    logger.info(f"   Tenant ID: {tenant_id}")

    dpsk_service = DpskService(r1_client)
    identity_service = IdentityService(r1_client)

    inventory = {
        'passphrases': [],
        'dpsk_pools': [],
        'identities': [],
        'identity_groups': [],
        'policy_sets': []
    }

    try:
        # STEP 1: Try to query DPSK pools directly (preferred method)
        logger.info(f"📡 Step 1: Attempting direct DPSK pool query...")
        dpsk_pools = []
        use_workaround = False

        try:
            pools_response = await dpsk_service.query_dpsk_pools(
                tenant_id=tenant_id,
                page=1,
                limit=1000
            )
            dpsk_pools = pools_response.get('content', pools_response.get('data', []))
            logger.info(f"   ✅ Direct query successful! Found {len(dpsk_pools)} DPSK pools")
        except Exception as e:
            logger.warning(f"   ⚠️  Direct DPSK pool query failed: {str(e)}")
            logger.info(f"   🔄 Falling back to identity group workaround...")
            use_workaround = True

        # BRANCH 1: Direct query worked - process pools directly
        if not use_workaround and dpsk_pools:
            logger.info(f"📡 Step 2: Processing {len(dpsk_pools)} DPSK pools...")

            for pool in dpsk_pools:
                pool_id = pool.get('id')
                pool_name = pool.get('name', pool_id)
                identity_group_id = pool.get('identityGroupId')

                # Add pool to inventory
                inventory['dpsk_pools'].append({
                    'id': pool_id,
                    'name': pool_name
                })
                logger.info(f"   📦 Pool: {pool_name}")

                # Try to get passphrases - try GET first, then POST query, then identity group
                passphrases_found = False

                # Method 1: Simple GET
                try:
                    logger.info(f"      📡 Getting passphrases (GET)...")
                    passphrases_response = await dpsk_service.get_passphrases(
                        pool_id=pool_id,
                        tenant_id=tenant_id,
                        page=1,
                        size=1000
                    )
                    passphrases = passphrases_response.get('content', passphrases_response.get('data', []))

                    if passphrases:
                        logger.info(f"      ✅ Found {len(passphrases)} passphrases via GET")
                        passphrases_found = True
                        for passphrase in passphrases:
                            passphrase_data = {
                                'id': passphrase.get('id'),
                                'pool_id': pool_id,
                                'username': passphrase.get('userName', passphrase.get('username', passphrase.get('id')))
                            }
                            inventory['passphrases'].append(passphrase_data)
                    else:
                        logger.warning(f"      ⚠️  GET returned 0 passphrases")
                except Exception as e1:
                    logger.warning(f"      ⚠️  GET passphrases failed: {str(e1)}")

                # Method 2: POST query (try if GET didn't find anything)
                if not passphrases_found:
                    try:
                        logger.info(f"      📡 Querying passphrases (POST)...")
                        passphrases_response = await dpsk_service.query_passphrases(
                            pool_id=pool_id,
                            tenant_id=tenant_id,
                            page=1,
                            limit=1000
                        )
                        passphrases = passphrases_response.get('content', passphrases_response.get('data', []))

                        if passphrases:
                            logger.info(f"      ✅ Found {len(passphrases)} passphrases via POST query")
                            passphrases_found = True
                            for passphrase in passphrases:
                                passphrase_data = {
                                    'id': passphrase.get('id'),
                                    'pool_id': pool_id,
                                    'username': passphrase.get('userName', passphrase.get('username', passphrase.get('id')))
                                }
                                inventory['passphrases'].append(passphrase_data)
                        else:
                            logger.warning(f"      ⚠️  POST query returned 0 passphrases")
                    except Exception as e2:
                        logger.warning(f"      ⚠️  POST query failed: {str(e2)}")

                # Fetch identities from identity group (regardless of whether we found passphrases)
                # We need to delete identities (which cascades to delete passphrases)
                if identity_group_id:
                    try:
                        logger.info(f"      📡 Fetching identities from identity group...")
                        identities_response = await identity_service.get_identities_in_group(
                            group_id=identity_group_id,
                            tenant_id=tenant_id
                        )
                        identities = []
                        if isinstance(identities_response, dict):
                            identities = identities_response.get('data', identities_response.get('content', []))

                        logger.info(f"      ✅ Found {len(identities)} identities in group")

                        for identity in identities:
                            identity_data = {
                                'id': identity.get('id'),  # This is a UUID
                                'pool_id': pool_id,
                                'username': identity.get('userName', identity.get('name', identity.get('id'))),
                                'group_id': identity_group_id
                            }
                            inventory['identities'].append(identity_data)

                    except Exception as e:
                        logger.error(f"      ❌ Error fetching identities: {str(e)}")

                # Note: We've already fetched identities above
                # Passphrases will be cascade-deleted when we delete identities
                # The "passphrases" array is just for the safety net phase

                # Track identity group if it has DPSK pattern
                if identity_group_id:
                    # Fetch identity group details to get name
                    try:
                        group = await identity_service.get_identity_group(identity_group_id, tenant_id)
                        group_name = group.get('name', '')

                        if 'dpsk' in group_name.lower() or 'cloudpath' in group_name.lower():
                            inventory['identity_groups'].append({
                                'id': identity_group_id,
                                'name': group_name
                            })
                    except:
                        pass  # Skip if we can't get group details

        # BRANCH 2: Fallback to identity group workaround
        elif use_workaround:
            logger.info(f"📡 Using identity group workaround...")
            identity_groups_response = await identity_service.query_identity_groups(
                tenant_id=tenant_id,
                search_string="",
                page=1,
                size=1000
            )

            # Extract identity groups from response
            identity_groups = []
            if isinstance(identity_groups_response, dict):
                identity_groups = identity_groups_response.get('content', identity_groups_response.get('data', []))

            logger.info(f"   Found {len(identity_groups)} identity groups")

            # STEP 2: Extract DPSK pool IDs and process each group
            dpsk_groups_with_pools = []
            for group in identity_groups:
                group_name = group.get('name', '')
                group_id = group.get('id')
                dpsk_pool_id = group.get('dpskPoolId')

                # Track DPSK-related identity groups
                if 'dpsk' in group_name.lower() or 'cloudpath' in group_name.lower():
                    inventory['identity_groups'].append({
                        'id': group_id,
                        'name': group_name
                    })
                    logger.info(f"   - {group_name} (matches DPSK pattern)")

                # Collect groups with DPSK pools
                if dpsk_pool_id:
                    dpsk_groups_with_pools.append({
                        'group_id': group_id,
                        'group_name': group_name,
                        'pool_id': dpsk_pool_id
                    })

            logger.info(f"📡 Step 2: Found {len(dpsk_groups_with_pools)} groups with DPSK pools")

            # STEP 3: For each group, fetch pool details and identities
            for group_info in dpsk_groups_with_pools:
                group_id = group_info['group_id']
                group_name = group_info['group_name']
                pool_id = group_info['pool_id']

                try:
                    # Fetch DPSK pool details
                    logger.info(f"   📡 Fetching DPSK pool: {pool_id}")
                    pool = await dpsk_service.get_dpsk_pool(pool_id, tenant_id)
                    pool_name = pool.get('name', pool_id)

                    # Add pool to inventory
                    inventory['dpsk_pools'].append({
                        'id': pool_id,
                        'name': pool_name
                    })
                    logger.info(f"      ✅ Pool: {pool_name}")

                    # Get identities from identity group (these are the DPSK passphrases)
                    # WORKAROUND: /passphrases/query endpoint returns 500, so get identities instead
                    try:
                        logger.info(f"      📡 Getting identities from group {group_name}...")
                        identities_response = await identity_service.get_identities_in_group(
                            group_id=group_id,
                            tenant_id=tenant_id
                        )

                        # Extract identities list from response
                        identities = []
                        if isinstance(identities_response, dict):
                            identities = identities_response.get('data', identities_response.get('content', []))

                        logger.info(f"      Found {len(identities)} identities in group")

                        # These are actual identities with UUID IDs
                        # Store them in identities array (deleting them will cascade to passphrases)
                        for identity in identities:
                            identity_data = {
                                'id': identity.get('id'),  # This is a UUID
                                'pool_id': pool_id,
                                'username': identity.get('userName', identity.get('name', identity.get('id'))),
                                'group_id': group_id
                            }
                            inventory['identities'].append(identity_data)

                    except Exception as e:
                        logger.error(f"      ❌ Error getting identities from group {group_name}: {str(e)}")

                except Exception as e:
                    logger.error(f"   ❌ Error processing group {group_name}: {str(e)}")

        logger.info(f"=" * 60)
        logger.info(f"✅ Audit complete:")
        logger.info(f"   - Passphrases: {len(inventory['passphrases'])}")
        logger.info(f"   - DPSK Pools: {len(inventory['dpsk_pools'])}")
        logger.info(f"   - Identity Groups: {len(inventory['identity_groups'])}")
        logger.info(f"   - Total: {sum(len(v) for v in inventory.values())}")
        logger.info(f"=" * 60)

    except Exception as e:
        logger.exception(f"Error auditing venue: {str(e)}")
        raise

    return inventory
