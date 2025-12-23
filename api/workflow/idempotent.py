"""
Idempotency Helpers

Provides find-or-create patterns for workflow operations:
- Check if resource already exists
- Create only if needed
- Safe retry on network failures
"""

import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class IdempotentHelper:
    """Helpers for idempotent resource creation"""

    def __init__(self, r1_client):
        """
        Initialize idempotent helper

        Args:
            r1_client: R1Client instance
        """
        self.r1_client = r1_client

    async def find_or_create_identity_group(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Find existing identity group by name or create new one

        Args:
            tenant_id: Tenant ID
            name: Identity group name
            description: Description
            **kwargs: Additional identity group fields

        Returns:
            Identity group data (with 'id' field)
        """
        logger.info(f"Finding or creating identity group: {name}")

        try:
            # Query for existing identity groups with this name
            response = await self.r1_client.identity.query_identity_groups(
                tenant_id=tenant_id,
                search_string=name,
                page=0,
                size=100
            )

            # Check content or data field (Spring Data pagination)
            existing_groups = response.get('content', response.get('data', []))

            # Find exact match by name
            for group in existing_groups:
                if group.get('name') == name:
                    logger.info(f"  ✅ Found existing identity group: {name} (ID: {group.get('id')})")
                    return {
                        'existed': True,
                        'created': False,
                        **group
                    }

            # Not found - create new one
            logger.info(f"  🆕 Creating new identity group: {name}")
            new_group = await self.r1_client.identity.create_identity_group(
                tenant_id=tenant_id,
                name=name,
                description=description,
                **kwargs
            )

            return {
                'existed': False,
                'created': True,
                **new_group
            }

        except Exception as e:
            logger.error(f"  ❌ Error in find_or_create_identity_group: {str(e)}")
            raise

    async def find_or_create_dpsk_pool(
        self,
        tenant_id: str,
        name: str,
        identity_group_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Find existing DPSK pool by name or create new one

        Args:
            tenant_id: Tenant ID
            name: DPSK pool name
            identity_group_id: Identity group ID
            **kwargs: Additional DPSK pool fields

        Returns:
            DPSK pool data (with 'id' field)
        """
        logger.info(f"Finding or creating DPSK pool: {name}")

        try:
            # Try to query for existing pools
            # Note: The query endpoint may be broken (returns 500)
            # So we rely on the identity group having a dpskPoolId
            try:
                response = await self.r1_client.dpsk.query_dpsk_pools(
                    tenant_id=tenant_id,
                    search_string=name,
                    page=0,
                    limit=100
                )

                existing_pools = response.get('data', [])

                # Find exact match by name
                for pool in existing_pools:
                    if pool.get('name') == name:
                        logger.info(f"  ✅ Found existing DPSK pool: {name} (ID: {pool.get('id')})")
                        return {
                            'existed': True,
                            'created': False,
                            **pool
                        }
            except Exception as query_error:
                logger.warning(f"  ⚠️  DPSK pool query failed (expected): {str(query_error)}")
                # This is expected - the query endpoint is broken

            # Not found - create new one
            logger.info(f"  🆕 Creating new DPSK pool: {name}")
            new_pool = await self.r1_client.dpsk.create_dpsk_pool(
                tenant_id=tenant_id,
                identity_group_id=identity_group_id,
                name=name,
                **kwargs
            )

            # Check if this was an async operation (202 Accepted)
            # RuckusONE returns 202 with a requestId for DPSK pool creation
            if 'requestId' in new_pool:
                request_id = new_pool['requestId']
                pool_id = new_pool.get('id')

                logger.warning(f"  ⏳ DPSK pool creation is async (requestId: {request_id})")
                logger.warning(f"  ⏳ Waiting for pool to be ready before creating passphrases...")

                try:
                    # Wait for the async task to complete
                    await self.r1_client.await_task_completion(
                        request_id=request_id,
                        override_tenant_id=tenant_id,
                        max_attempts=20,
                        sleep_seconds=3
                    )

                    logger.warning(f"  ✅ DPSK pool creation completed!")

                except Exception as task_error:
                    error_str = str(task_error)

                    # Check if this is a "Name must be unique" error
                    if "Name must be unique" in error_str or "name must be unique" in error_str.lower():
                        logger.warning(f"  ⚠️  Pool '{name}' already exists (name collision detected)")
                        logger.warning(f"  🔍 Attempting to fetch existing pool from identity group...")

                        # Try to get the identity group to find the pool
                        try:
                            ig_details = await self.r1_client.identity.get_identity_group(
                                group_id=identity_group_id,
                                tenant_id=tenant_id
                            )

                            # Check if identity group has a dpskPoolId
                            existing_pool_id = ig_details.get('dpskPoolId')

                            if existing_pool_id:
                                logger.info(f"  ✅ Found existing pool via identity group: {existing_pool_id}")
                                pool_details = await self.r1_client.dpsk.get_dpsk_pool(
                                    pool_id=existing_pool_id,
                                    tenant_id=tenant_id
                                )

                                return {
                                    'existed': True,
                                    'created': False,
                                    **pool_details
                                }
                            else:
                                logger.warning(f"  ⚠️  Identity group has no dpskPoolId, pool may be orphaned")
                                # Re-raise the original error
                                raise task_error

                        except Exception as fetch_error:
                            logger.error(f"  ❌ Failed to fetch existing pool: {str(fetch_error)}")
                            # Re-raise the original error
                            raise task_error
                    else:
                        # Not a name collision error, re-raise
                        raise

                # Now fetch the actual pool details
                if pool_id:
                    logger.info(f"  📥 Fetching pool details for ID: {pool_id}")
                    pool_details = await self.r1_client.dpsk.get_dpsk_pool(
                        pool_id=pool_id,
                        tenant_id=tenant_id
                    )

                    # Debug: Log the actual pool settings
                    logger.warning(f"  🔍 DEBUG - Pool details from API: {pool_details}")
                    logger.warning(f"  🔍 DEBUG - Actual passphraseLengthInCharacters: {pool_details.get('passphraseLengthInCharacters')}")
                    logger.warning(f"  🔍 DEBUG - Actual passphraseFormat: {pool_details.get('passphraseFormat')}")

                    return {
                        'existed': False,
                        'created': True,
                        **pool_details
                    }

            return {
                'existed': False,
                'created': True,
                **new_pool
            }

        except Exception as e:
            logger.error(f"  ❌ Error in find_or_create_dpsk_pool: {str(e)}")
            raise

    async def find_or_create_policy_set(
        self,
        tenant_id: str,
        name: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Find existing policy set by name or create new one

        Args:
            tenant_id: Tenant ID
            name: Policy set name
            **kwargs: Additional policy set fields

        Returns:
            Policy set data (with 'id' field)
        """
        logger.info(f"Finding or creating policy set: {name}")

        try:
            # Query for existing policy sets
            response = await self.r1_client.policy_sets.query_policy_sets(
                tenant_id=tenant_id,
                search_string=name,
                page=0,
                size=100
            )

            existing_sets = response.get('content', response.get('data', []))

            # Find exact match by name
            for policy_set in existing_sets:
                if policy_set.get('name') == name:
                    logger.info(f"  ✅ Found existing policy set: {name} (ID: {policy_set.get('id')})")
                    return {
                        'existed': True,
                        'created': False,
                        **policy_set
                    }

            # Not found - create new one
            logger.info(f"  🆕 Creating new policy set: {name}")
            new_policy_set = await self.r1_client.policy_sets.create_policy_set(
                tenant_id=tenant_id,
                name=name,
                **kwargs
            )

            return {
                'existed': False,
                'created': True,
                **new_policy_set
            }

        except Exception as e:
            logger.error(f"  ❌ Error in find_or_create_policy_set: {str(e)}")
            raise

    async def find_or_create_generic(
        self,
        finder_func: Callable,
        creator_func: Callable,
        match_field: str = 'name',
        match_value: Any = None,
        **create_kwargs
    ) -> Dict[str, Any]:
        """
        Generic find-or-create pattern

        Args:
            finder_func: Async function to query for existing resources
            creator_func: Async function to create new resource
            match_field: Field name to match on (default: 'name')
            match_value: Value to match
            **create_kwargs: Arguments for creator function

        Returns:
            Resource data with 'existed' and 'created' flags
        """
        logger.info(f"Generic find-or-create for {match_field}={match_value}")

        try:
            # Try to find existing
            existing_resources = await finder_func()

            # Find exact match
            if isinstance(existing_resources, dict):
                # Handle dict response (e.g., {'data': [...], 'content': [...]})
                existing_resources = (
                    existing_resources.get('data') or
                    existing_resources.get('content') or
                    []
                )

            for resource in existing_resources:
                if resource.get(match_field) == match_value:
                    logger.info(f"  ✅ Found existing resource with {match_field}={match_value}")
                    return {
                        'existed': True,
                        'created': False,
                        **resource
                    }

            # Not found - create new one
            logger.info(f"  🆕 Creating new resource with {match_field}={match_value}")
            new_resource = await creator_func(**create_kwargs)

            return {
                'existed': False,
                'created': True,
                **new_resource
            }

        except Exception as e:
            logger.error(f"  ❌ Error in generic find-or-create: {str(e)}")
            raise
