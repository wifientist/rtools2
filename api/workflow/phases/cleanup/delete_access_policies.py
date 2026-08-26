"""
V2 Phase: Delete Access Policy Resources

Removes the three entities an import creates when access policies are enabled:

  1. Adaptive policies       — the per-identity rate-limit rules
  2. Adaptive policy sets    — the container holding those policies
  3. RADIUS attribute groups — the rate tiers policies point at (fast, gigabit)

Order matters. A policy references its RADIUS attribute group, and a policy set
contains policies, so they come apart in that order: policies, then sets, then
RADIUS groups. Deleting a RADIUS group still referenced by a policy fails.

These are REAL entities. The /policyTemplates/{id}/policies path names a policy
type (100 = DPSK), not the MSP template system — this phase never touches
/templates/*.

Until this phase existed nothing collected them, so every re-import into a
venue that had been "cleaned" hit
    409 — A policy with this name already exists
on each rate-limit policy.
"""

import asyncio
import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.cleanup.inventory import (
    DPSK_POLICY_TEMPLATE_ID,
    ResourceInventory,
)

logger = logging.getLogger(__name__)

# Deleting is per-entity. Each policy now costs an unassign plus a delete
# (plus retries), and a venue can hold hundreds — at 8-wide the burst
# exhausted the Redis pool and the phase died with "No connection available".
MAX_CONCURRENT_DELETES = 4


@register_phase("delete_access_policies", "Delete Access Policies")
class DeleteAccessPoliciesPhase(PhaseExecutor):
    """Delete adaptive policies, policy sets, and RADIUS attribute groups."""

    class Inputs(BaseModel):
        inventory: ResourceInventory = Field(default_factory=ResourceInventory)

    class Outputs(BaseModel):
        policies_deleted: int = 0
        policy_sets_deleted: int = 0
        radius_groups_deleted: int = 0
        failed: List[Dict[str, Any]] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        inv = inputs.inventory
        failed: List[Dict[str, Any]] = []

        total = (
            len(inv.policies)
            + len(inv.policy_sets)
            + len(inv.radius_attribute_groups)
        )
        if total == 0:
            await self.emit("No access policy resources to delete")
            return self.Outputs()

        await self.emit(
            f"Deleting {len(inv.policies)} policies, "
            f"{len(inv.policy_sets)} policy sets, "
            f"{len(inv.radius_attribute_groups)} RADIUS attribute groups"
        )

        sem = asyncio.Semaphore(MAX_CONCURRENT_DELETES)

        async def run(label: str, name: str, coro_factory) -> bool:
            async with sem:
                try:
                    await coro_factory()
                    logger.info(f"Deleted {label}: {name}")
                    return True
                except Exception as e:
                    msg = str(e)
                    # Already gone is a success for cleanup purposes.
                    if '404' in msg or 'not found' in msg.lower():
                        logger.info(f"{label} '{name}' already absent")
                        return True
                    logger.warning(f"Failed to delete {label} '{name}': {msg}")
                    failed.append({
                        'type': label, 'name': name, 'error': msg[:200]
                    })
                    return False

        # --- 1a. Unassign policies from their policy sets ---------------------
        # R1 refuses to delete an assigned policy:
        #   409 "The policy is still assigned to a policy set"
        # and the spec says as much: "Deletes the policy and conditions, may
        # not be assigned to a service." So break the link first. Policies with
        # no policy_set_id are already free and skip straight to deletion.
        assigned = [
            p for p in inv.policies if p.get('id') and p.get('policy_set_id')
        ]
        if assigned:
            await self.emit(
                f"Unassigning {len(assigned)} policies from their policy sets"
            )
            unassign_results = await asyncio.gather(*[
                run(
                    "policy assignment",
                    p.get('name', p.get('id', '?')),
                    lambda p=p: self.r1_client.policy_sets.remove_policy_from_policy_set(
                        policy_set_id=p['policy_set_id'],
                        policy_id=p['id'],
                        tenant_id=self.tenant_id,
                    ),
                )
                for p in assigned
            ])
            freed = sum(1 for r in unassign_results if r)
            await self.emit(
                f"Unassigned {freed}/{len(assigned)} policies",
                "success" if freed == len(assigned) else "warning",
            )

        # --- 1b. Adaptive policies -------------------------------------------
        # Even with the unassignment awaited, R1 can still report the policy as
        # assigned for a moment afterwards. Treat that specific conflict as
        # retryable rather than terminal — re-issuing the unassign and backing
        # off clears it. Any other error fails the policy immediately.
        async def delete_policy(p: Dict[str, Any]) -> bool:
            pid = p['id']
            pname = p.get('name', pid)
            delays = [0, 2, 5, 10]

            for attempt, delay in enumerate(delays):
                if delay:
                    await asyncio.sleep(delay)
                async with sem:
                    try:
                        await self.r1_client.policy_sets.delete_template_policy(
                            template_id=p.get('template_id', '100'),
                            policy_id=pid,
                            tenant_id=self.tenant_id,
                        )
                        if attempt:
                            logger.info(
                                f"Deleted policy '{pname}' on attempt {attempt + 1}"
                            )
                        else:
                            logger.info(f"Deleted policy: {pname}")
                        return True
                    except Exception as e:
                        msg = str(e)
                        if '404' in msg or 'not found' in msg.lower():
                            return True
                        still_assigned = 'still assigned' in msg.lower()
                        if not still_assigned or attempt == len(delays) - 1:
                            logger.warning(
                                f"Failed to delete policy '{pname}': {msg}"
                            )
                            failed.append({
                                'type': 'policy', 'name': pname,
                                'error': msg[:200],
                            })
                            return False

                # Outside the semaphore: nudge the assignment again before the
                # next attempt, in case the first unassign never landed.
                if p.get('policy_set_id'):
                    try:
                        await self.r1_client.policy_sets.remove_policy_from_policy_set(
                            policy_set_id=p['policy_set_id'],
                            policy_id=pid,
                            tenant_id=self.tenant_id,
                        )
                    except Exception:
                        pass
            return False

        results = await asyncio.gather(*[
            delete_policy(p) for p in inv.policies if p.get('id')
        ])
        policies_deleted = sum(1 for r in results if r)
        if inv.policies:
            await self.emit(
                f"Deleted {policies_deleted}/{len(inv.policies)} adaptive policies",
                "success" if policies_deleted == len(inv.policies) else "warning",
            )

        # --- 2. Policy sets --------------------------------------------------
        # A set carries the same constraint ("may not be assigned to a
        # service"): it can be attached to DPSK pools and identity groups.
        # Rather than issue pools x sets detach calls up front, try the delete
        # and only detach when R1 objects — the usual case needs neither.
        async def delete_set(ps: Dict[str, Any]) -> bool:
            ps_id, ps_name = ps.get('id'), ps.get('name', ps.get('id', '?'))
            try:
                await self.r1_client.policy_sets.delete_policy_set(
                    policy_set_id=ps_id, tenant_id=self.tenant_id
                )
                logger.info(f"Deleted policy set: {ps_name}")
                return True
            except Exception as e:
                msg = str(e)
                if '404' in msg or 'not found' in msg.lower():
                    return True
                if '409' not in msg and 'assigned' not in msg.lower():
                    logger.warning(f"Failed to delete policy set '{ps_name}': {msg}")
                    failed.append({
                        'type': 'policy set', 'name': ps_name, 'error': msg[:200]
                    })
                    return False

            # Still attached — detach from this venue's pools and identity
            # groups, then retry once.
            await self.emit(
                f"Policy set '{ps_name}' is still attached; detaching first"
            )
            for pool in inv.dpsk_pools:
                if not pool.get('id'):
                    continue
                try:
                    await self.r1_client.dpsk.remove_policy_set_from_pool(
                        pool_id=pool['id'],
                        policy_set_id=ps_id,
                        tenant_id=self.tenant_id,
                    )
                except Exception:
                    pass  # not attached to this pool
            for ig in inv.identity_groups:
                if not ig.get('id'):
                    continue
                try:
                    await self.r1_client.identity.remove_policy_set_from_identity_group(
                        group_id=ig['id'],
                        policy_set_id=ps_id,
                        tenant_id=self.tenant_id,
                    )
                except Exception:
                    pass  # not attached to this identity group

            try:
                await self.r1_client.policy_sets.delete_policy_set(
                    policy_set_id=ps_id, tenant_id=self.tenant_id
                )
                logger.info(f"Deleted policy set after detaching: {ps_name}")
                return True
            except Exception as e:
                logger.warning(
                    f"Policy set '{ps_name}' still undeletable after detach: {e}"
                )
                failed.append({
                    'type': 'policy set', 'name': ps_name, 'error': str(e)[:200]
                })
                return False

        results = await asyncio.gather(*[
            delete_set(ps) for ps in inv.policy_sets if ps.get('id')
        ])
        sets_deleted = sum(1 for r in results if r)
        if inv.policy_sets:
            await self.emit(
                f"Deleted {sets_deleted}/{len(inv.policy_sets)} policy sets",
                "success" if sets_deleted == len(inv.policy_sets) else "warning",
            )

        # --- 3. RADIUS attribute groups --------------------------------------
        # Last: a policy still referencing one blocks its deletion, with
        #   409 "The Attribute Group is still in use by another service."
        # These groups are shared tenant-wide, so the blocking policy is often
        # one this venue-scoped cleanup never inventoried — the tiers every
        # other property points at ("fast", "gigabit") survive by design. Name
        # them in the message so a 409 reads as a fact about the tenant rather
        # than a failure of the run.
        # R1 does not clean up the /assignments row when a policy is deleted,
        # so the group stays pinned by a link to a policy that 404s. The UI
        # counts policies, not assignment rows, so such a group reads "0
        # associated policies" and still refuses to delete — by hand or by API.
        # On a 409, walk the rows, drop only the ones whose policy is really
        # gone, and retry. Rows pointing at a live policy are left alone.
        async def clear_orphan_assignments(gid: str, name: str) -> int:
            try:
                resp = await self.r1_client.radius_attributes.get_group_assignments(
                    group_id=gid, tenant_id=self.tenant_id
                )
            except Exception as e:
                logger.warning(f"Could not list assignments for '{name}': {e}")
                return 0

            rows = (
                resp if isinstance(resp, list)
                else resp.get('content', resp.get('data', []))
            )
            cleared = 0
            for row in rows:
                assignment_id = row.get('id')
                policy_id = row.get('externalAssignmentIdentifier')
                if not assignment_id or not policy_id:
                    continue
                try:
                    await self.r1_client.policy_sets.get_template_policy(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        tenant_id=self.tenant_id,
                    )
                    logger.info(
                        f"Assignment on '{name}' points at live policy "
                        f"{policy_id} — leaving it"
                    )
                    continue
                except Exception as e:
                    if '404' not in str(e) and 'not found' not in str(e).lower():
                        logger.warning(
                            f"Could not check policy {policy_id} for '{name}': {e}"
                        )
                        continue

                try:
                    await self.r1_client.radius_attributes.delete_group_assignment(
                        group_id=gid,
                        assignment_id=assignment_id,
                        tenant_id=self.tenant_id,
                    )
                    cleared += 1
                    logger.info(
                        f"Cleared orphaned assignment {assignment_id} on "
                        f"'{name}' (policy {policy_id} no longer exists)"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to clear assignment {assignment_id} on "
                        f"'{name}': {e}"
                    )
            return cleared

        async def delete_group(g: Dict[str, Any]) -> bool:
            gid = g['id']
            name = g.get('name', gid)

            if await run(
                "RADIUS attribute group", name,
                lambda: self.r1_client.radius_attributes
                .delete_radius_attribute_group(
                    group_id=gid, tenant_id=self.tenant_id
                ),
            ):
                return True

            # run() recorded the failure; if it was the in-use conflict, try to
            # unpin and go again. Drop that first entry either way so a
            # recovered group is not reported as failed.
            first_error = failed[-1]['error'] if failed else ''
            if 'in use' not in first_error.lower() and '409' not in first_error:
                return False
            failed.pop()

            cleared = await clear_orphan_assignments(gid, name)
            if not cleared:
                failed.append({
                    'type': 'RADIUS attribute group', 'name': name,
                    'error': first_error,
                })
                return False

            await self.emit(
                f"Cleared {cleared} orphaned assignment(s) pinning "
                f"'{name}'; retrying delete"
            )
            return await run(
                "RADIUS attribute group", name,
                lambda: self.r1_client.radius_attributes
                .delete_radius_attribute_group(
                    group_id=gid, tenant_id=self.tenant_id
                ),
            )

        groups = [g for g in inv.radius_attribute_groups if g.get('id')]
        results = await asyncio.gather(*[delete_group(g) for g in groups])
        radius_deleted = sum(1 for r in results if r)
        if inv.radius_attribute_groups:
            await self.emit(
                f"Deleted {radius_deleted}/{len(inv.radius_attribute_groups)} "
                f"RADIUS attribute groups",
                "success" if radius_deleted == len(inv.radius_attribute_groups)
                else "warning",
            )
            in_use = [
                g.get('name', g.get('id', '?'))
                for g, ok in zip(groups, results) if not ok
            ]
            if in_use:
                await self.emit(
                    f"Still in use elsewhere in the tenant, left in place: "
                    f"{', '.join(in_use)}. Delete the policies that reference "
                    f"them first.",
                    "warning",
                )

        if failed:
            await self.emit(
                f"{len(failed)} access policy resources could not be deleted",
                "warning",
            )

        return self.Outputs(
            policies_deleted=policies_deleted,
            policy_sets_deleted=sets_deleted,
            radius_groups_deleted=radius_deleted,
            failed=failed,
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        inv = inputs.inventory
        count = (
            len(inv.policies)
            + len(inv.policy_sets)
            + len(inv.radius_attribute_groups)
        )
        return PhaseValidation(
            valid=True,
            will_create=False,
            estimated_api_calls=count,
            notes=[f"{count} access policy resource(s) to delete"],
        )
