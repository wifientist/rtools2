"""
Phase: Create DPSK Passphrases (Batch)

Creates a batch of DPSK passphrases in parallel mode.
This phase is used by child jobs in parallel execution.
"""

import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


def _validate_expiration(expiration: str = None, username: str = None, renew_expired: bool = False) -> tuple[str, str, bool]:
    """
    Validate expiration date (same logic as main passphrases.py)
    """
    if not expiration:
        return None, "", False

    try:
        exp_dt = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        if exp_dt < now:
            if renew_expired:
                new_expiration = now + timedelta(days=365)
                new_exp_str = new_expiration.isoformat()
                return new_exp_str, f"Expired DPSK renewed with 1-year expiration (was {expiration})", True
            else:
                return None, f"Expiration date {expiration} is in the past, will be imported without expiration", True

        return expiration, "", False

    except Exception as e:
        return None, f"Invalid expiration format: {str(e)}", False


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create a batch of DPSK passphrases in RuckusONE.

    This is called by child jobs in parallel mode. The passphrases
    already have dpsk_pool_id and identity_group_id mapped.

    Args:
        context: Execution context with batch data

    Returns:
        Single completed task with created passphrase data
    """
    logger.info("Batch Phase: Create DPSK Passphrases")

    # Get batch data from input_data (from ParallelJobOrchestrator)
    input_data = context.get('input_data', {})
    item_data = input_data.get('item', {})

    passphrases = item_data.get('passphrases', [])
    batch_number = item_data.get('batch_number', 0)

    if not passphrases:
        logger.warning(f"  Batch {batch_number}: No passphrases to create")
        return []

    # Get R1 client and tenant_id from context
    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')

    if not r1_client:
        raise Exception("R1 client not available in context")
    if not tenant_id:
        raise Exception("tenant_id not available in context")

    logger.info(f"  Batch {batch_number}: Creating {len(passphrases)} passphrases...")

    # Get migration options
    options = context.get('options', {})
    skip_expired_dpsks = options.get('skip_expired_dpsks', False)
    renew_expired_dpsks = options.get('renew_expired_dpsks', True)

    created_passphrases = []
    failed_passphrases = []
    skipped_passphrases = []

    for pp_data in passphrases:
        dpsk_pool_id = pp_data.get('dpsk_pool_id')
        username = pp_data.get('userName')
        passphrase = pp_data.get('passphrase')

        if not dpsk_pool_id:
            logger.warning(f"    Skipping {username} - no DPSK pool ID")
            failed_passphrases.append({
                'userName': username,
                'reason': 'no_pool_id',
                'can_retry': False
            })
            continue

        if not passphrase:
            logger.warning(f"    Skipping {username} - no passphrase")
            failed_passphrases.append({
                'userName': username,
                'reason': 'no_passphrase',
                'can_retry': False
            })
            continue

        # Validate expiration date
        expiration, exp_warning, is_expired = _validate_expiration(
            pp_data.get('expiration'),
            username,
            renew_expired=renew_expired_dpsks
        )

        # Skip expired DPSKs if option is enabled
        if is_expired and skip_expired_dpsks:
            skipped_passphrases.append({
                'userName': username,
                'reason': 'expired',
                'original_expiration': pp_data.get('expiration')
            })
            continue

        # Get optional fields
        max_usage = pp_data.get('max_usage')
        vlan_id = pp_data.get('vlan_id')
        cloudpath_guid = pp_data.get('cloudpath_guid')

        identity_group_id = pp_data.get('identity_group_id')

        try:
            result = await r1_client.dpsk.create_passphrase(
                pool_id=dpsk_pool_id,
                tenant_id=tenant_id,
                passphrase=passphrase,
                user_name=username,
                description=cloudpath_guid,
                expiration_date=expiration,
                max_devices=max_usage,
                vlan_id=vlan_id
            )

            passphrase_id = result.get('id') or result.get('passphraseId') or result.get('dpskId')

            # Update the identity with the cloudpath_guid as description
            identity_updated = False
            if identity_group_id and cloudpath_guid:
                try:
                    # Query identities in the group to find the one matching username
                    identities_response = await r1_client.identity.get_identities_in_group(
                        group_id=identity_group_id,
                        tenant_id=tenant_id,
                        page=0,
                        size=1000
                    )

                    # Find the identity matching this username
                    identities = identities_response.get('content', []) if isinstance(identities_response, dict) else identities_response
                    matching_identity = None
                    for identity in identities:
                        if identity.get('name') == username:
                            matching_identity = identity
                            break

                    if matching_identity:
                        identity_id = matching_identity.get('id')
                        # Update the identity with the cloudpath_guid as description
                        await r1_client.identity.update_identity(
                            group_id=identity_group_id,
                            identity_id=identity_id,
                            tenant_id=tenant_id,
                            description=cloudpath_guid
                        )
                        identity_updated = True
                    else:
                        logger.debug(f"    Could not find identity for {username} in group {identity_group_id}")

                except Exception as identity_error:
                    logger.warning(f"    Failed to update identity description for {username}: {identity_error}")

            created_passphrases.append({
                'dpsk_id': passphrase_id,
                'userName': username,
                'dpsk_pool_id': dpsk_pool_id,
                'created': True,
                'identity_updated': identity_updated,
                'cloudpath_guid': cloudpath_guid if identity_updated else None,
                'expiration_warning': exp_warning if exp_warning else None
            })

        except Exception as e:
            logger.warning(f"    Failed to create {username}: {str(e)}")
            failed_passphrases.append({
                'userName': username,
                'reason': str(e),
                'can_retry': True
            })

    total = len(created_passphrases) + len(failed_passphrases) + len(skipped_passphrases)
    logger.info(f"  Batch {batch_number}: Created {len(created_passphrases)}/{total} passphrases")

    if failed_passphrases:
        logger.warning(f"  Batch {batch_number}: {len(failed_passphrases)} failed")

    # Track created resources
    state_manager = context.get('state_manager')
    job_id = context.get('job_id')
    if state_manager and job_id:
        for pp in created_passphrases:
            await state_manager.add_created_resource(job_id, 'passphrases', pp)

    # Return completed task
    task = Task(
        id=f"create_passphrases_batch_{batch_number}",
        name=f"Created {len(created_passphrases)} passphrases (batch {batch_number})",
        task_type="create_resources",
        status=TaskStatus.COMPLETED,
        input_data={'passphrases': passphrases},
        output_data={
            'created_passphrases': created_passphrases,
            'failed_passphrases': failed_passphrases,
            'skipped_passphrases': skipped_passphrases
        }
    )

    return [task]
