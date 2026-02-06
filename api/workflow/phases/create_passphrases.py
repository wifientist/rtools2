"""
V2 Phase: Create DPSK Passphrases

Creates DPSK passphrases in a pool for one unit.
Each unit can have multiple passphrases (multiple CSV rows per unit_number).

Handles "already exists" errors gracefully for idempotency.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


class PassphraseInput(BaseModel):
    """A single passphrase to create."""
    passphrase: str
    username: str = ""
    email: Optional[str] = None
    description: Optional[str] = None
    vlan_id: Optional[str] = None


@register_phase("create_passphrases", "Create DPSK Passphrases")
class CreatePassphrasesPhase(PhaseExecutor):
    """
    Create DPSK passphrases in a pool for a single unit.

    Supports multiple passphrases per unit (one per CSV row).
    Handles "already exists" errors as success (idempotent).
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        dpsk_pool_id: str
        passphrases: List[PassphraseInput] = Field(default_factory=list)

    class Outputs(BaseModel):
        created_count: int = 0
        existed_count: int = 0
        failed_count: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create passphrases in the DPSK pool for this unit."""
        if not inputs.passphrases:
            await self.emit(
                f"[{inputs.unit_number}] No passphrases to create"
            )
            return self.Outputs()

        count = len(inputs.passphrases)
        await self.emit(
            f"[{inputs.unit_number}] Creating {count} "
            f"passphrase{'s' if count != 1 else ''}..."
        )

        created = 0
        existed = 0
        failed = 0

        skipped = 0
        for pp in inputs.passphrases:
            if not pp.passphrase:
                # Empty passphrases are allowed - user may import actual
                # passwords later via Cloudpath Import. Skip gracefully.
                logger.info(
                    f"[{inputs.unit_number}] Skipping entry for "
                    f"'{pp.username or '(no username)'}' - no passphrase"
                )
                skipped += 1
                continue

            try:
                result = await self.r1_client.dpsk.create_passphrase(
                    pool_id=inputs.dpsk_pool_id,
                    tenant_id=self.tenant_id,
                    passphrase=pp.passphrase,
                    user_name=pp.username,
                    user_email=pp.email,
                    description=pp.description,
                    vlan_id=pp.vlan_id,
                )

                passphrase_id = (
                    result.get('id') or result.get('passphraseId')
                )

                await self.track_resource('passphrases', {
                    'id': passphrase_id,
                    'username': pp.username,
                    'unit_number': inputs.unit_number,
                    'pool_id': inputs.dpsk_pool_id,
                })
                created += 1

            except Exception as e:
                error_str = str(e).lower()
                if 'already exists' in error_str:
                    logger.info(
                        f"[{inputs.unit_number}] Passphrase for "
                        f"{pp.username} already exists"
                    )
                    existed += 1
                else:
                    logger.error(
                        f"[{inputs.unit_number}] Failed to create "
                        f"passphrase for {pp.username}: {e}"
                    )
                    failed += 1

        # Summary emit
        parts = []
        if created:
            parts.append(f"{created} created")
        if existed:
            parts.append(f"{existed} existed")
        if skipped:
            parts.append(f"{skipped} skipped (empty)")
        if failed:
            parts.append(f"{failed} failed")
        summary = ", ".join(parts) or "no changes"

        level = "success" if failed == 0 else "warning"
        await self.emit(
            f"[{inputs.unit_number}] Passphrases: {summary}", level
        )

        logger.info(
            f"[{inputs.unit_number}] Passphrases: {summary}"
        )

        return self.Outputs(
            created_count=created,
            existed_count=existed,
            failed_count=failed,
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Passphrases are always created (no pre-check available)."""
        count = len(inputs.passphrases)
        return PhaseValidation(
            valid=True,
            will_create=count > 0,
            estimated_api_calls=count,
            notes=[f"{count} passphrases to create"],
        )
