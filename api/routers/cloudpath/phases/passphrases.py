"""
Phase 6: Create DPSK Passphrases

Creates individual DPSK passphrases in pools
This is the most critical phase with potentially thousands of tasks
"""

import logging
import uuid
import asyncio
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


def _validate_passphrase(passphrase: str, username: str) -> tuple[bool, str]:
    """
    Validate passphrase is present

    Passphrases are imported as-is from Cloudpath. The DPSK pool is configured
    to accept the minimum length found in the Cloudpath data.

    Args:
        passphrase: Passphrase to validate
        username: Username for logging

    Returns:
        Tuple of (is_valid, reason_if_invalid)
    """
    if not passphrase:
        return False, "No passphrase provided"

    return True, ""


def _validate_expiration(expiration: str = None, username: str = None, renew_expired: bool = False) -> tuple[str, str, bool]:
    """
    Validate expiration date

    RuckusONE requires expiration dates to be in the future.
    Handling depends on options:
    - If renew_expired=True and expired: return new date 1 year from now
    - If expired and not renewing: return None with warning
    - If no expiration provided: return None

    Args:
        expiration: ISO format expiration date string
        username: Username for logging
        renew_expired: If True, renew expired DPSKs with 1-year expiration

    Returns:
        Tuple of (valid_expiration_or_none, warning_message, is_expired)
    """
    if not expiration:
        return None, "", False

    try:
        exp_dt = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        # If expired
        if exp_dt < now:
            if renew_expired:
                # Renew with 1 year from now
                new_expiration = now + timedelta(days=365)
                new_exp_str = new_expiration.isoformat()
                return new_exp_str, f"Expired DPSK renewed with 1-year expiration (was {expiration})", True
            else:
                # Don't renew, just note it's expired
                return None, f"Expiration date {expiration} is in the past, will be imported without expiration", True

        # Return valid future expiration
        return expiration, "", False

    except Exception as e:
        return None, f"Invalid expiration format: {str(e)}", False


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Create DPSK passphrases in RuckusONE

    Args:
        context: Execution context with parsed data and pool results

    Returns:
        Single completed task with created passphrase data
    """
    logger.warning("Phase 6: Create DPSK Passphrases")

    # Get data from Phase 3 (DPSK pools)
    phase3_results = context.get('previous_phase_results', {}).get('create_dpsk_pools', {})
    aggregated = phase3_results.get('aggregated', {})

    passphrases_list = aggregated.get('passphrases', [])
    passphrases = passphrases_list[0] if passphrases_list else []

    if not passphrases:
        logger.warning("  ⚠️  No passphrases to create")
        return []

    # Get R1 client and tenant_id from context
    r1_client = context.get('r1_client')
    tenant_id = context.get('tenant_id')

    if not r1_client:
        raise Exception("R1 client not available in context")
    if not tenant_id:
        raise Exception("tenant_id not available in context")

    logger.warning(f"Creating {len(passphrases)} DPSK passphrases in RuckusONE...")

    # Create all passphrases
    created_passphrases = []
    failed_passphrases = []
    skipped_passphrases = []

    # Get migration options
    options = context.get('options', {})
    simulate_delay = options.get('simulate_delay', False)
    skip_expired_dpsks = options.get('skip_expired_dpsks', False)
    renew_expired_dpsks = options.get('renew_expired_dpsks', True)

    for pp_data in passphrases:
        dpsk_pool_id = pp_data.get('dpsk_pool_id')
        username = pp_data.get('userName')
        passphrase = pp_data.get('passphrase')

        if not dpsk_pool_id:
            logger.warning(f"  ⚠️  Skipping {username} - no DPSK pool ID")
            failed_passphrases.append({
                'userName': username,
                'passphrase': passphrase,
                'reason': 'no_pool_id',
                'can_retry': False
            })
            continue

        # Validate passphrase is present
        is_valid, validation_error = _validate_passphrase(passphrase, username)
        if not is_valid:
            logger.warning(f"  ⚠️  Skipping {username} - {validation_error}")
            failed_passphrases.append({
                'userName': username,
                'passphrase': passphrase,
                'reason': validation_error,
                'can_retry': False,
                'action_needed': 'Passphrase must be provided in Cloudpath data'
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
            logger.warning(f"  ⏭️  Skipping expired DPSK: {username}")
            skipped_passphrases.append({
                'userName': username,
                'passphrase': passphrase,
                'reason': 'expired',
                'original_expiration': pp_data.get('expiration')
            })
            continue

        if exp_warning:
            logger.warning(f"  ⚠️  {username}: {exp_warning}")

        # Debug: Log what we're about to send
        max_usage = pp_data.get('max_usage')
        vlan_id = pp_data.get('vlan_id')
        cloudpath_guid = pp_data.get('cloudpath_guid')
        logger.warning(f"  Creating passphrase: {username}")
        logger.warning(f"    - passphrase length: {len(passphrase)} chars")
        logger.warning(f"    - max_usage: {max_usage}")
        logger.warning(f"    - vlan: {vlan_id}")
        logger.warning(f"    - cloudpath_guid: {cloudpath_guid}")
        logger.warning(f"    - pp_data keys: {list(pp_data.keys())}")

        try:
            # Create passphrase via R1 DPSK API
            # Use cloudpath_guid as the description field for reference
            result = await r1_client.dpsk.create_passphrase(
                pool_id=dpsk_pool_id,
                tenant_id=tenant_id,
                passphrase=passphrase,
                user_name=username,
                description=cloudpath_guid,  # Copy GUID to description field
                expiration_date=expiration,
                max_devices=max_usage,
                vlan_id=vlan_id
            )

            # Debug: Log the full response to understand the structure
            logger.warning(f"    🔍 DEBUG - create_passphrase response: {result}")
            passphrase_id = result.get('id') or result.get('passphraseId') or result.get('dpskId')
            logger.warning(f"    ✅ Created: {username} (ID: {passphrase_id})")

            # Now find and update the identity with the cloudpath_guid as description
            # The passphrase endpoint doesn't support description, but the identity endpoint does
            identity_group_id = pp_data.get('identity_group_id')
            identity_updated = False

            if identity_group_id and cloudpath_guid:
                try:
                    # Query identities in the group to find the one matching username
                    identities_response = await r1_client.identity.get_identities_in_group(
                        group_id=identity_group_id,
                        tenant_id=tenant_id,
                        page=0,
                        size=1000  # Should be enough for most pools
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
                        logger.warning(f"    🔍 Found identity {identity_id} for {username}, updating description...")

                        # Update the identity with the cloudpath_guid as description
                        await r1_client.identity.update_identity(
                            group_id=identity_group_id,
                            identity_id=identity_id,
                            tenant_id=tenant_id,
                            description=cloudpath_guid
                        )
                        logger.warning(f"    ✅ Updated identity description with GUID: {cloudpath_guid}")
                        identity_updated = True
                    else:
                        logger.warning(f"    ⚠️  Could not find identity for {username} in group {identity_group_id}")

                except Exception as identity_error:
                    logger.warning(f"    ⚠️  Failed to update identity description: {str(identity_error)}")

            created_passphrases.append({
                'dpsk_id': passphrase_id,
                'userName': username,
                'dpsk_pool_id': dpsk_pool_id,
                'passphrase': passphrase,
                'expiration_warning': exp_warning if exp_warning else None,
                'created': True,
                'identity_updated': identity_updated,
                'cloudpath_guid': cloudpath_guid if identity_updated else None
            })

            # Simulate delay for testing/demos
            if simulate_delay:
                await asyncio.sleep(0.3)  # 300ms delay per passphrase

        except Exception as e:
            logger.warning(f"    ❌ Failed to create {username}: {str(e)}")
            failed_passphrases.append({
                'userName': username,
                'passphrase': passphrase,
                'reason': str(e),
                'can_retry': True,
                'action_needed': 'Review error and retry if needed'
            })

    total_processed = len(created_passphrases) + len(failed_passphrases) + len(skipped_passphrases)
    logger.warning(f"✅ Created {len(created_passphrases)}/{total_processed} passphrases")

    if skipped_passphrases:
        logger.warning(f"⏭️  {len(skipped_passphrases)} expired passphrases skipped:")
        for sp in skipped_passphrases:
            logger.warning(f"    - {sp['userName']} (expired: {sp['original_expiration']})")

    if failed_passphrases:
        logger.warning(f"⚠️  {len(failed_passphrases)} passphrases failed:")

        # Group by reason
        validation_failures = [fp for fp in failed_passphrases if not fp.get('can_retry', False)]
        api_failures = [fp for fp in failed_passphrases if fp.get('can_retry', False)]

        if validation_failures:
            logger.warning(f"\n  📋 Validation Failures ({len(validation_failures)} passphrases - CANNOT BE IMPORTED):")
            for fp in validation_failures:
                logger.warning(f"    - {fp['userName']}: {fp['reason']}")
            logger.warning(f"  ⚠️  Action Required: Review validation failures and provide missing data")

        if api_failures:
            logger.warning(f"\n  🔄 API Failures ({len(api_failures)} passphrases - MAY BE RETRYABLE):")
            for fp in api_failures:
                logger.warning(f"    - {fp['userName']}: {fp['reason']}")

    # Track created resources in state_manager for cleanup/reference
    state_manager = context.get('state_manager')
    job_id = context.get('job_id')
    if state_manager and job_id:
        for pp in created_passphrases:
            await state_manager.add_created_resource(job_id, 'passphrases', pp)
        logger.info(f"  📝 Tracked {len(created_passphrases)} passphrases in job resources")

    # Return single completed task with created resources
    task = Task(
        id="create_passphrases",
        name=f"Created {len(created_passphrases)} passphrases",
        task_type="create_resources",
        status=TaskStatus.COMPLETED,
        input_data={'passphrases': passphrases},
        output_data={
            'created_passphrases': created_passphrases,
            'failed_passphrases': failed_passphrases
        }
    )

    return [task]


async def create_passphrase_task(task: Task, context: Dict[str, Any], r1_client) -> Dict[str, Any]:
    """
    Task executor function for creating a DPSK passphrase
    """
    tenant_id = context['tenant_id']
    pp_data = task.input_data

    dpsk_pool_id = pp_data.get('dpsk_pool_id')
    user_name = pp_data.get('userName') or pp_data.get('username')
    passphrase = pp_data.get('passphrase') or pp_data.get('password')

    logger.info(f"Creating passphrase for user: {user_name}")

    # Call R1 API to create passphrase
    try:
        result = await r1_client.dpsk.create_passphrase(
            dpsk_pool_id=dpsk_pool_id,
            tenant_id=tenant_id,
            userName=user_name,
            passphrase=passphrase,
            expirationDate=pp_data.get('expirationDate'),
            maxDevices=pp_data.get('maxDevices', 1)
        )

        logger.info(f"  ✅ Created passphrase for: {user_name}")

        return {
            'passphrase_id': result.get('id'),
            'userName': user_name,
            'dpsk_pool_id': dpsk_pool_id,
            'created': True
        }

    except Exception as e:
        logger.error(f"  ❌ Failed to create passphrase for {user_name}: {str(e)}")
        raise
