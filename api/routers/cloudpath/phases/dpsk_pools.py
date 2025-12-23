"""
Phase 3: Create DPSK Pools

Creates DPSK pools in RuckusONE linked to identity groups
"""

import logging
import uuid
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from workflow.idempotent import IdempotentHelper

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create DPSK pools in RuckusONE

    Args:
        context: Execution context with parsed data and identity group results

    Returns:
        Single completed task with created DPSK pool IDs and forwarded data
    """
    logger.warning("Phase 3: Create DPSK Pools")

    # Get data from Phase 2
    phase2_results = context.get('previous_phase_results', {}).get('create_identity_groups', {})
    aggregated = phase2_results.get('aggregated', {})

    # Phase 2 forwards data with keys: dpsk_pools (with identity_group_id mapped), passphrases
    dpsk_pools_list = aggregated.get('dpsk_pools', [])
    passphrases_list = aggregated.get('passphrases', [])

    dpsk_pools = dpsk_pools_list[0] if dpsk_pools_list else []
    passphrases = passphrases_list[0] if passphrases_list else []

    if not dpsk_pools:
        logger.warning("  ⚠️  No DPSK pools to create")
        return []

    # Get R1 client and tenant_id from context
    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')

    if not r1_client:
        raise Exception("R1 client not available in context")
    if not tenant_id:
        raise Exception("tenant_id not available in context")

    logger.warning(f"Creating {len(dpsk_pools)} DPSK pools in RuckusONE...")

    # Create all DPSK pools
    created_pools = []
    helper = IdempotentHelper(r1_client)

    for pool_data in dpsk_pools:
        name = pool_data.get('name')
        identity_group_id = pool_data.get('identity_group_id')

        if not identity_group_id:
            logger.warning(f"  ⚠️  Skipping pool {name} - no identity group ID")
            continue

        description = pool_data.get('description', 'Migrated from Cloudpath')

        # Get analyzed pool settings
        passphrase_length = pool_data.get('passphrase_length', 18)
        passphrase_format = pool_data.get('passphrase_format', 'KEYBOARD_FRIENDLY')
        max_devices = pool_data.get('max_devices', 1)
        expiration_days = pool_data.get('expiration_days')
        analysis = pool_data.get('analysis', {})

        logger.warning(f"  Creating DPSK pool: {name}")
        logger.warning(f"    Settings: Format={passphrase_format}, Length={passphrase_length}, MaxDevices={max_devices}, Expiration={expiration_days or 'None'}")

        # Log warnings from analysis
        if analysis.get('warnings'):
            for warning in analysis['warnings']:
                logger.warning(f"    {warning}")

        result = await helper.find_or_create_dpsk_pool(
            tenant_id=tenant_id,
            name=name,
            identity_group_id=identity_group_id,
            description=description,
            passphrase_length=passphrase_length,
            passphrase_format=passphrase_format,
            max_devices_per_passphrase=max_devices,
            expiration_days=expiration_days
        )

        logger.warning(f"    {'✅ Existed' if result.get('existed') else '✅ Created'}: {name} (ID: {result.get('id')})")

        created_pools.append({
            'dpsk_pool_id': result.get('id'),
            'name': name,
            'identity_group_id': identity_group_id,
            'existed': result.get('existed', False),
            'created': result.get('created', False)
        })

    # Update passphrases with dpsk_pool_id mappings
    pool_name_to_id = {p['name']: p['dpsk_pool_id'] for p in created_pools}
    for pp in passphrases:
        pool_name = pp.get('dpsk_pool_name')
        if pool_name in pool_name_to_id:
            pp['dpsk_pool_id'] = pool_name_to_id[pool_name]

    logger.warning(f"✅ Created {len(created_pools)} DPSK pools. Forwarding to next phase...")

    # Return single completed task with created resources and forwarded data
    task = Task(
        id="create_dpsk_pools",
        name=f"Created {len(created_pools)} DPSK pools",
        task_type="create_resources",
        status=TaskStatus.COMPLETED,
        input_data={'dpsk_pools': dpsk_pools},
        output_data={
            'created_dpsk_pools': created_pools,
            'passphrases': passphrases  # Updated with dpsk_pool_id
        }
    )

    return [task]


async def create_dpsk_pool_task(task: Task, context: Dict[str, Any], r1_client) -> Dict[str, Any]:
    """
    Task executor function for creating a DPSK pool
    """
    tenant_id = context['tenant_id']
    pool_data = task.input_data

    name = pool_data.get('name')
    identity_group_id = pool_data.get('identity_group_id')
    description = pool_data.get('description', 'Migrated from Cloudpath')

    logger.info(f"Creating DPSK pool: {name}")

    # Use idempotent helper
    helper = IdempotentHelper(r1_client)
    result = await helper.find_or_create_dpsk_pool(
        tenant_id=tenant_id,
        name=name,
        identity_group_id=identity_group_id,
        description=description,
        # Additional DPSK pool fields from Cloudpath data
        passphraseFormat=pool_data.get('passphraseFormat', 'RANDOM'),
        passphraseLength=pool_data.get('passphraseLength', 12),
        expirationInDays=pool_data.get('expirationInDays', 0),
        maxDevicesPerPassphrase=pool_data.get('maxDevicesPerPassphrase', 1)
    )

    logger.info(f"  {'Existed' if result.get('existed') else 'Created'}: {name} (ID: {result.get('id')})")

    return {
        'dpsk_pool_id': result.get('id'),
        'name': name,
        'identity_group_id': identity_group_id,
        'existed': result.get('existed', False),
        'created': result.get('created', False),
        'cloudpath_mapping': pool_data
    }
