"""
Phase 2: Create Identity Groups

Creates identity groups in RuckusONE based on Cloudpath data
"""

import logging
import uuid
import json
from typing import Dict, Any, List
from workflow.models import Task, TaskStatus
from workflow.idempotent import IdempotentHelper

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create identity groups in RuckusONE

    Args:
        context: Execution context with parsed data from Phase 1

    Returns:
        Single completed task with created identity group IDs and forwarded data
    """
    logger.warning("Phase 2: Create Identity Groups")

    # Get parsed data from Phase 1
    phase1_results = context.get('previous_phase_results', {}).get('parse_validate', {})
    aggregated = phase1_results.get('aggregated', {})
    parsed_data_list = aggregated.get('parsed_data', [{}])

    if isinstance(parsed_data_list, list) and len(parsed_data_list) > 0:
        parsed_data = parsed_data_list[0]
    else:
        parsed_data = {}
        logger.warning("  ⚠️  No parsed data available")
        return []

    identity_groups = parsed_data.get('identity_groups', [])
    dpsk_pools = parsed_data.get('dpsk_pools', [])
    passphrases = parsed_data.get('passphrases', [])

    if not identity_groups:
        logger.warning("  ⚠️  No identity groups to create")
        return []

    # Get R1 client and tenant_id from context
    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')

    if not r1_client:
        raise Exception("R1 client not available in context")
    if not tenant_id:
        raise Exception("tenant_id not available in context")

    logger.warning(f"Creating {len(identity_groups)} identity groups in RuckusONE...")

    # Create all identity groups
    created_groups = []
    helper = IdempotentHelper(r1_client)

    for ig_data in identity_groups:
        name = ig_data.get('name')
        description = ig_data.get('description', 'Migrated from Cloudpath')

        logger.warning(f"  Creating identity group: {name}")

        result = await helper.find_or_create_identity_group(
            tenant_id=tenant_id,
            name=name,
            description=description
        )

        logger.warning(f"    {'✅ Existed' if result.get('existed') else '✅ Created'}: {name} (ID: {result.get('id')})")

        created_groups.append({
            'identity_group_id': result.get('id'),
            'name': name,
            'existed': result.get('existed', False),
            'created': result.get('created', False)
        })

    # Update dpsk_pools with identity_group_id mappings
    ig_name_to_id = {g['name']: g['identity_group_id'] for g in created_groups}
    for pool in dpsk_pools:
        ig_name = pool.get('identity_group_name')
        if ig_name in ig_name_to_id:
            pool['identity_group_id'] = ig_name_to_id[ig_name]

    logger.warning(f"✅ Created {len(created_groups)} identity groups. Forwarding to next phase...")

    # Return single completed task with created resources and forwarded data
    task = Task(
        id="create_identity_groups",
        name=f"Created {len(created_groups)} identity groups",
        task_type="create_resources",
        status=TaskStatus.COMPLETED,
        input_data={'identity_groups': identity_groups},
        output_data={
            'created_identity_groups': created_groups,
            'dpsk_pools': dpsk_pools,  # Updated with identity_group_id
            'passphrases': passphrases
        }
    )

    return [task]


async def create_identity_group_task(task: Task, context: Dict[str, Any], r1_client) -> Dict[str, Any]:
    """
    Task executor function for creating an identity group

    This function is called by the TaskExecutor for each task
    """
    tenant_id = context['tenant_id']
    ig_data = task.input_data

    name = ig_data.get('name')
    description = ig_data.get('description', f'Migrated from Cloudpath')

    logger.info(f"Creating identity group: {name}")

    # Use idempotent helper
    helper = IdempotentHelper(r1_client)
    result = await helper.find_or_create_identity_group(
        tenant_id=tenant_id,
        name=name,
        description=description
    )

    logger.info(f"  {'Existed' if result.get('existed') else 'Created'}: {name} (ID: {result.get('id')})")

    return {
        'identity_group_id': result.get('id'),
        'name': name,
        'existed': result.get('existed', False),
        'created': result.get('created', False),
        'cloudpath_mapping': ig_data  # Store original Cloudpath data for reference
    }
