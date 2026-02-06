"""
V2 Phase: Update Identity Descriptions and VLANs

After passphrases are created (which auto-creates identities),
this phase updates each identity with:
1. Description field with the Cloudpath GUID for traceability
2. VLAN ID from the Cloudpath export (if present)

The GUID in the identity description allows:
- Identifying which Cloudpath DPSK maps to which R1 identity
- Cross-referencing during audits or migrations
- Rollback/cleanup operations

The VLAN on the identity allows:
- Per-identity VLAN assignment from Cloudpath
- Identity-based network segmentation in R1
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)


class IdentityUpdateResult(BaseModel):
    """Result of updating a single identity's description and VLAN."""
    identity_id: str
    cloudpath_guid: str
    vlan_id: Optional[int] = None
    success: bool
    error: Optional[str] = None


@register_phase("update_identity_descriptions", "Update Identity Descriptions")
class UpdateIdentityDescriptionsPhase(PhaseExecutor):
    """
    Update identity descriptions with Cloudpath GUIDs and set VLANs.

    When passphrases are created via the DPSK pool API, R1 auto-creates
    identities. This phase updates each identity with:
    - description: Cloudpath GUID for traceability
    - vlan: Per-identity VLAN from Cloudpath (if present)
    """

    class Inputs(BaseModel):
        import_mode: str = "property_wide"
        identity_group_id: Optional[str] = None
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of group names to IDs"
        )
        created_passphrases: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Passphrases created in previous phase"
        )
        options: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        updated_count: int = 0
        vlan_updated_count: int = 0
        failed_count: int = 0
        skipped_count: int = 0
        update_results: List[IdentityUpdateResult] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Update identity descriptions with Cloudpath GUIDs and set VLANs."""
        passphrases = inputs.created_passphrases

        # Filter to only passphrases that have identity_id
        to_update = [
            p for p in passphrases
            if p.get('identity_id') and p.get('cloudpath_guid')
            and not p.get('skipped', False)
        ]

        # Count how many have VLANs to set
        with_vlan = [p for p in to_update if p.get('vlan_id') is not None]

        if not to_update:
            await self.emit("No identities to update")
            return self.Outputs(skipped_count=len(passphrases))

        # Determine which identity group to use
        group_id = inputs.identity_group_id
        if not group_id and inputs.identity_group_ids:
            # Get the first (only) group for property-wide mode
            group_id = next(iter(inputs.identity_group_ids.values()), None)

        if not group_id:
            await self.emit("No identity group ID available", "error")
            return self.Outputs(
                failed_count=len(to_update),
                update_results=[
                    IdentityUpdateResult(
                        identity_id=p.get('identity_id', ''),
                        cloudpath_guid=p.get('cloudpath_guid', ''),
                        success=False,
                        error="No identity group ID"
                    ) for p in to_update
                ]
            )

        max_concurrent = inputs.options.get('max_concurrent_passphrases', 10)

        vlan_msg = f", {len(with_vlan)} with VLANs" if with_vlan else ""
        await self.emit(
            f"Updating {len(to_update)} identities{vlan_msg} "
            f"(max {max_concurrent} concurrent)"
        )

        async def update_one(pp: Dict[str, Any]) -> IdentityUpdateResult:
            identity_id = pp.get('identity_id')
            guid = pp.get('cloudpath_guid')
            vlan_id = pp.get('vlan_id')

            try:
                # Update identity with description and VLAN (if present)
                await self.r1_client.identity.update_identity(
                    group_id=group_id,
                    identity_id=identity_id,
                    tenant_id=self.tenant_id,
                    description=guid,
                    vlan=vlan_id,  # Will be None if not present, which is fine
                )

                return IdentityUpdateResult(
                    identity_id=identity_id,
                    cloudpath_guid=guid,
                    vlan_id=vlan_id,
                    success=True,
                )

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    f"Failed to update identity {identity_id}: {error_msg}"
                )
                return IdentityUpdateResult(
                    identity_id=identity_id,
                    cloudpath_guid=guid,
                    vlan_id=vlan_id,
                    success=False,
                    error=error_msg,
                )

        # Execute with parallel_map
        results = await self.parallel_map(
            items=to_update,
            fn=update_one,
            max_concurrent=max_concurrent,
            item_name="identity",
            emit_progress=True,
            progress_interval=max(1, len(to_update) // 20),
        )

        # Categorize results
        updated: List[IdentityUpdateResult] = []
        failed: List[IdentityUpdateResult] = []

        for result in results.succeeded:
            if result is None:
                continue
            if result.success:
                updated.append(result)
            else:
                failed.append(result)

        for failure in results.failed:
            item = failure.get('item', {})
            failed.append(IdentityUpdateResult(
                identity_id=item.get('identity_id', ''),
                cloudpath_guid=item.get('cloudpath_guid', ''),
                success=False,
                error=failure.get('error', 'Unknown error'),
            ))

        skipped = len(passphrases) - len(to_update)
        vlan_updated = sum(1 for r in updated if r.vlan_id is not None)

        vlan_msg = f", {vlan_updated} with VLAN" if vlan_updated else ""
        await self.emit(
            f"Identities: {len(updated)} updated{vlan_msg}, "
            f"{skipped} skipped, {len(failed)} failed",
            "success" if not failed else "warning"
        )

        return self.Outputs(
            updated_count=len(updated),
            vlan_updated_count=vlan_updated,
            failed_count=len(failed),
            skipped_count=skipped,
            update_results=updated + failed,
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate identity update inputs."""
        passphrases = inputs.created_passphrases

        to_update = [
            p for p in passphrases
            if p.get('identity_id') and p.get('cloudpath_guid')
            and not p.get('skipped', False)
        ]

        with_vlan = [p for p in to_update if p.get('vlan_id') is not None]

        notes = [f"{len(to_update)} identities to update (description + GUID)"]
        if with_vlan:
            notes.append(f"{len(with_vlan)} with VLAN assignments")

        return PhaseValidation(
            valid=True,
            will_create=False,
            estimated_api_calls=len(to_update),
            notes=notes,
        )
