"""
V2 Phase: Create DPSK Passphrases

Creates passphrases in DPSK pools using parallel execution.

For PROPERTY_WIDE mode (many passphrases, single pool):
- Uses parallel_map() for intra-phase parallelism
- Configurable max_concurrent (default 10)
- Progress reporting during bulk creation

For PER_UNIT mode (few passphrases per unit):
- Brain handles per-unit parallelism
- This phase runs once per unit with 2-4 passphrases
"""

import logging
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


class PassphraseResult(BaseModel):
    """Result of creating a single passphrase."""
    cloudpath_guid: str
    username: str
    passphrase_id: Optional[str] = None
    identity_id: Optional[str] = None
    vlan_id: Optional[int] = None  # VLAN ID from Cloudpath (to set on identity)
    success: bool
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    updated: bool = False  # True if VLAN was updated on existing passphrase


@register_phase("create_passphrases", "Create DPSK Passphrases")
class CreatePassphrasesPhase(PhaseExecutor):
    """
    Create DPSK passphrases in the designated pool.

    Uses parallel_map for efficient bulk creation when processing
    many passphrases (property-wide mode).
    """

    class Inputs(BaseModel):
        import_mode: str = "property_wide"
        dpsk_pool_id: Optional[str] = None  # For per-unit mode
        dpsk_pool_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of pool names to IDs (property-wide)"
        )
        passphrases: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Passphrases to create"
        )
        options: Dict[str, Any] = Field(default_factory=dict)
        # From unit input_config (per-unit mode)
        unit_number: Optional[str] = None

    class Outputs(BaseModel):
        created_count: int = 0
        updated_count: int = 0  # Existing passphrases with VLAN updated
        failed_count: int = 0
        skipped_count: int = 0
        created_passphrases: List[PassphraseResult] = Field(default_factory=list)
        failed_passphrases: List[PassphraseResult] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create passphrases with parallel execution."""
        passphrases = inputs.passphrases
        options = inputs.options

        if not passphrases:
            await self.emit("No passphrases to create")
            return self.Outputs()

        # Determine which pool to use
        pool_id = inputs.dpsk_pool_id
        if not pool_id and inputs.dpsk_pool_ids:
            # Property-wide mode: get the first (only) pool
            pool_id = next(iter(inputs.dpsk_pool_ids.values()), None)

        if not pool_id:
            await self.emit("No DPSK pool ID available", "error")
            return self.Outputs(
                failed_count=len(passphrases),
                failed_passphrases=[
                    PassphraseResult(
                        cloudpath_guid=p.get('guid', ''),
                        username=p.get('name', ''),
                        success=False,
                        error="No DPSK pool ID"
                    ) for p in passphrases
                ]
            )

        # Get options
        max_concurrent = options.get('max_concurrent_passphrases', 10)
        skip_expired = options.get('skip_expired_dpsks', False)
        renew_expired = options.get('renew_expired_dpsks', False)
        renewal_days = options.get('renewal_days', 365)
        just_copy = options.get('just_copy_dpsks', False)

        await self.emit(
            f"Creating {len(passphrases)} passphrases in pool {pool_id} "
            f"(max {max_concurrent} concurrent)"
        )

        # Define the creation function for parallel_map
        async def create_one(pp: Dict[str, Any]) -> PassphraseResult:
            guid = pp.get('guid', '')
            username = pp.get('name', '')
            passphrase_value = pp.get('passphrase', '')
            status = pp.get('status', 'ACTIVE')
            already_exists = pp.get('exists', False)

            # Handle existing passphrases - check if VLAN needs update
            if already_exists:
                needs_vlan_update = pp.get('needs_vlan_update', False)
                existing_id = pp.get('existing_id')
                vlan_id = pp.get('vlan_id')

                if needs_vlan_update and existing_id and vlan_id is not None:
                    # Update VLAN on existing passphrase
                    try:
                        await self.r1_client.dpsk.update_passphrase(
                            pool_id=pool_id,
                            passphrase_id=existing_id,
                            tenant_id=self.tenant_id,
                            vlan_id=vlan_id,
                        )
                        logger.info(f"Updated VLAN to {vlan_id} for {username}")
                        return PassphraseResult(
                            cloudpath_guid=guid,
                            username=username,
                            passphrase_id=existing_id,
                            vlan_id=vlan_id,
                            success=True,
                            updated=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to update VLAN for {username}: {e}")
                        return PassphraseResult(
                            cloudpath_guid=guid,
                            username=username,
                            passphrase_id=existing_id,
                            vlan_id=vlan_id,
                            success=False,
                            error=f"VLAN update failed: {e}",
                        )
                else:
                    # No update needed, skip
                    return PassphraseResult(
                        cloudpath_guid=guid,
                        username=username,
                        passphrase_id=existing_id,
                        vlan_id=vlan_id,
                        success=True,
                        skipped=True,
                        skip_reason="Already exists in pool"
                    )

            # Check if expired/inactive
            if status != 'ACTIVE':
                if skip_expired:
                    return PassphraseResult(
                        cloudpath_guid=guid,
                        username=username,
                        success=True,
                        skipped=True,
                        skip_reason=f"Status: {status}"
                    )
                # Continue anyway if not skipping

            # Build expiration if renewing
            expiration = None
            if renew_expired:
                expiration = (
                    datetime.utcnow() + timedelta(days=renewal_days)
                ).isoformat() + "Z"

            try:
                # Extract VLAN ID if present (per-identity VLAN from Cloudpath)
                vlan_id = pp.get('vlan_id')

                # Create the passphrase in R1
                result = await self.r1_client.dpsk.create_passphrase(
                    pool_id=pool_id,
                    user_name=username,
                    passphrase=passphrase_value,
                    tenant_id=self.tenant_id,
                    expiration_date=expiration,
                    # Note: This sets passphrase description, not identity description
                    # Identity description is updated in the update_identity_descriptions phase
                    description=f"Imported from Cloudpath: {guid}",
                    vlan_id=vlan_id,
                )

                passphrase_id = result.get('id') if isinstance(result, dict) else None
                identity_id = result.get('identityId') if isinstance(result, dict) else None

                # Track the created resource
                await self.track_resource('passphrases', {
                    'id': passphrase_id,
                    'identity_id': identity_id,
                    'pool_id': pool_id,
                    'username': username,
                    'cloudpath_guid': guid,
                })

                return PassphraseResult(
                    cloudpath_guid=guid,
                    username=username,
                    passphrase_id=passphrase_id,
                    identity_id=identity_id,
                    vlan_id=vlan_id,
                    success=True,
                )

            except Exception as e:
                error_msg = str(e)
                # Check for duplicate
                if 'already exists' in error_msg.lower() or 'duplicate' in error_msg.lower():
                    return PassphraseResult(
                        cloudpath_guid=guid,
                        username=username,
                        success=True,
                        skipped=True,
                        skip_reason="Already exists"
                    )

                logger.error(f"Failed to create passphrase {username}: {error_msg}")
                return PassphraseResult(
                    cloudpath_guid=guid,
                    username=username,
                    success=False,
                    error=error_msg,
                )

        # Execute with parallel_map for intra-phase parallelism
        results = await self.parallel_map(
            items=passphrases,
            fn=create_one,
            max_concurrent=max_concurrent,
            item_name="passphrase",
            emit_progress=True,
            progress_interval=max(1, len(passphrases) // 20),  # ~5% intervals
        )

        # Categorize results
        created: List[PassphraseResult] = []
        failed: List[PassphraseResult] = []
        skipped_count = 0
        updated_count = 0

        for result in results.succeeded:
            if result is None:
                continue
            if result.skipped:
                skipped_count += 1
            elif result.updated:
                updated_count += 1
            created.append(result)

        for failure in results.failed:
            item = failure.get('item', {})
            failed.append(PassphraseResult(
                cloudpath_guid=item.get('guid', ''),
                username=item.get('name', ''),
                success=False,
                error=failure.get('error', 'Unknown error'),
            ))

        created_count = len(created) - skipped_count - updated_count

        # Build summary message
        parts = []
        if created_count > 0:
            parts.append(f"{created_count} created")
        if updated_count > 0:
            parts.append(f"{updated_count} VLAN updated")
        if skipped_count > 0:
            parts.append(f"{skipped_count} skipped")
        if len(failed) > 0:
            parts.append(f"{len(failed)} failed")
        summary = ", ".join(parts) if parts else "no changes"

        await self.emit(
            f"Passphrases: {summary}",
            "success" if not failed else "warning"
        )

        return self.Outputs(
            created_count=created_count,
            updated_count=updated_count,
            failed_count=len(failed),
            skipped_count=skipped_count,
            created_passphrases=created,
            failed_passphrases=failed,
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate passphrase creation inputs."""
        passphrases = inputs.passphrases

        return PhaseValidation(
            valid=True,
            will_create=len(passphrases) > 0,
            estimated_api_calls=len(passphrases),
            notes=[f"{len(passphrases)} passphrases to create"],
        )
