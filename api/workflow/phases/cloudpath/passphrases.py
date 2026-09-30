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
import asyncio
import time
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
    # For idempotent re-runs: carry identity info from validate phase through to Phase 4
    existing_identity_id: Optional[str] = None
    needs_description_update: bool = False
    # Written onto an identity that was already there, rather than created
    # together with a new one.
    attached_to_existing: bool = False
    # ...and that identity held a different passphrase, now replaced.
    replaced_existing: bool = False


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
        # Used only to backfill identity ids R1 was too slow to report
        identity_group_id: Optional[str] = None
        # The map form. Every OTHER consumer of a group id falls back to this
        # (update_identity_descriptions, create_access_policies); this phase
        # did not even declare it, so if the workflow supplied only the map the
        # backfill silently did not run -- and every identity R1 was slow to
        # report lost its Cloudpath GUID with it.
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of group names to IDs (property-wide)"
        )
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

        # Identity lookups, cached for this run.
        #
        # Every GENERAL-010 ("identity already exists") used to re-page the
        # entire identity group. On a property whose identities all exist
        # that is one ~24-request sweep PER passphrase: ~1,400 requests
        # re-reading a list that is not changing. And because the R1 client is
        # synchronous, each of those blocks the event loop -- long enough that
        # Redis connects for /jobs/{id}/status hit their 10s timeout and the
        # progress UI started 500ing while the import itself was fine.
        #
        # One sweep now serves every recovery. The lock collapses the ten
        # concurrent failures a wave produces into a single read, and the age
        # check still lets an identity created mid-run be picked up.
        # Resolved ONCE, here, because two things need it and they used to
        # disagree: the cache helper short-circuited on identity_group_id and
        # so did the backfill. A workflow that supplied only the map form got
        # an empty sweep and no recovery, silently.
        resolved_group_id = inputs.identity_group_id
        if not resolved_group_id and inputs.identity_group_ids:
            resolved_group_id = next(iter(inputs.identity_group_ids.values()), None)

        identity_cache: Dict[str, str] = {}
        identity_cache_at: float = 0.0
        identity_lock = asyncio.Lock()

        async def identities_by_name(max_age: Optional[float] = None) -> Dict[str, str]:
            """
            name -> identity id for the group.

            Cached. Re-read only if never loaded, or older than max_age
            seconds when the caller says it needs current data.
            """
            nonlocal identity_cache, identity_cache_at
            if not resolved_group_id:
                return {}
            async with identity_lock:
                stale = identity_cache_at == 0.0 or (
                    max_age is not None
                    and time.monotonic() - identity_cache_at > max_age
                )
                if stale:
                    identity_cache = await self._identities_by_name(
                        resolved_group_id
                    )
                    identity_cache_at = time.monotonic()
                return identity_cache

        # identity id -> the file entry whose passphrase it was given this run
        claimed_identities: Dict[str, str] = {}

        async def attach_to_identity(
            pp: Dict[str, Any], identity_id: str, expiration: Optional[str]
        ) -> PassphraseResult:
            """
            Put this resident's passphrase on the identity they already have.

            create_passphrase cannot do this: R1 ignores identityId on a
            passphrase create and mints a "DPSK_User_xxxx" identity instead,
            and rejects the username because the identity exists. Writing the
            passphrase onto the identity is the only route that lands it on
            the right one -- see IdentityService.set_identity_passphrase.

            If the identity already holds a different passphrase, this
            replaces it. R1 allows one per identity, so there is no way to
            add a second, and the import's file is the source of truth.
            """
            guid = pp.get('guid', '')
            username = pp.get('name', '')
            vlan_id = pp.get('vlan_id')

            if not resolved_group_id:
                raise RuntimeError(
                    f"{username} already has an identity, but no identity "
                    f"group id was supplied to write its passphrase through"
                )

            # One passphrase per identity, so two file entries that resolve
            # to the same identity ("4021_gigabit" and "4021_ultrafast" both
            # matching a renamed "4021") cannot both land: the second would
            # silently overwrite the first. Checked before the first await,
            # so concurrent entries cannot both pass.
            claimed_by = claimed_identities.setdefault(identity_id, username)
            if claimed_by != username:
                raise RuntimeError(
                    f"{username} resolves to the same identity as "
                    f"{claimed_by}, which already received its passphrase in "
                    f"this run. RuckusONE holds one passphrase per identity."
                )

            vlan = None
            if vlan_id is not None and vlan_id != '' and vlan_id != '0':
                try:
                    vlan = int(vlan_id)
                except (ValueError, TypeError):
                    vlan = None

            identity = await self.r1_client.identity.set_identity_passphrase(
                group_id=resolved_group_id,
                identity_id=identity_id,
                passphrase=pp.get('passphrase', ''),
                tenant_id=self.tenant_id,
                vlan=vlan,
            )
            passphrase_id = (
                identity.get('dpskGuid') if isinstance(identity, dict) else None
            )

            if expiration and passphrase_id:
                # The identity write has no expiry field; set it on the
                # passphrase it produced. A miss here leaves a working
                # passphrase with the pool's default expiry, so it is logged
                # rather than failing the resident.
                try:
                    await self.r1_client.dpsk.update_passphrase(
                        pool_id=pool_id,
                        passphrase_id=passphrase_id,
                        tenant_id=self.tenant_id,
                        expiration_date=expiration,
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not set expiration on passphrase for "
                        f"{username}: {e}"
                    )

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
                existing_identity_id=identity_id,
                needs_description_update=pp.get('needs_description_update', False),
                vlan_id=vlan_id,
                success=True,
                attached_to_existing=True,
                replaced_existing=bool(pp.get('existing_identity_passphrase_id')),
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
                            identity_id=pp.get('existing_identity_id'),
                            vlan_id=vlan_id,
                            success=True,
                            updated=True,
                            existing_identity_id=pp.get('existing_identity_id'),
                            needs_description_update=pp.get('needs_description_update', False),
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
                            existing_identity_id=pp.get('existing_identity_id'),
                            needs_description_update=pp.get('needs_description_update', False),
                        )
                else:
                    # Nothing to write to the passphrase, but this row still
                    # has repair work downstream: on a re-run after an aborted
                    # attempt its identity may still carry the _tier suffix.
                    # identity_id is what every consumer keys off, so populate
                    # it here exactly as the VLAN-update path above does —
                    # leaving it None is what made a second run skip these
                    # instead of finishing the job.
                    return PassphraseResult(
                        cloudpath_guid=guid,
                        username=username,
                        passphrase_id=existing_id,
                        identity_id=pp.get('existing_identity_id'),
                        vlan_id=vlan_id,
                        success=True,
                        skipped=True,
                        skip_reason="Already exists in pool",
                        existing_identity_id=pp.get('existing_identity_id'),
                        needs_description_update=pp.get('needs_description_update', False),
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

                # If validate already found this resident's identity, write
                # the passphrase onto it. Creating one would either be
                # rejected (the name is taken) or land on an identity R1
                # invents.
                known_identity = pp.get('existing_identity_id')
                if known_identity:
                    return await attach_to_identity(pp, known_identity, expiration)

                # New resident: R1 creates the identity with the passphrase.
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

                # The identity exists but we didn't know its id (created after
                # validation ran, or missed). R1 says so two ways: GENERAL-010
                # when that identity holds no passphrase, DPSK-020 ("Username
                # 'x' already exists") when it holds one. Resolve it by name
                # and write the passphrase onto it.
                #
                # DPSK-020 used to fall through to the duplicate check below
                # and be reported as "skipped, already exists" -- a success,
                # for a resident whose identity held some other passphrase.
                lowered = error_msg.lower()
                name_taken = (
                    'GENERAL-010' in error_msg
                    or 'identity with this name already exists' in lowered
                    or 'DPSK-020' in error_msg
                    or ('username' in lowered and 'already exists' in lowered)
                )
                if name_taken and resolved_group_id:
                    try:
                        by_name = await identities_by_name()
                        found = by_name.get(username)
                        if not found:
                            # Not in the cached view: it may have been created
                            # after we read it. One refresh, shared by everyone
                            # queued on the lock rather than one sweep each.
                            by_name = await identities_by_name(max_age=15.0)
                            found = by_name.get(username)
                        if found:
                            attached = await attach_to_identity(pp, found, expiration)
                            logger.info(
                                f"Attached passphrase for {username} to its "
                                f"existing identity {found}"
                            )
                            return attached
                    except Exception as retry_err:
                        logger.warning(
                            f"Could not attach {username} to its existing "
                            f"identity: {retry_err}"
                        )
                        return PassphraseResult(
                            cloudpath_guid=guid,
                            username=username,
                            success=False,
                            error=f"Could not attach to existing identity: {retry_err}",
                        )

                if name_taken:
                    # The name is taken and its identity is not in this
                    # group. That is not "already imported".
                    logger.error(f"Failed to create passphrase {username}: {error_msg}")
                    return PassphraseResult(
                        cloudpath_guid=guid,
                        username=username,
                        success=False,
                        error=error_msg,
                    )

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

        # ------------------------------------------------------------------
        # Backfill identity ids R1 did not report.
        #
        # create_passphrase resolves identityId by re-reading the pool, but R1
        # links the identity a moment after it writes the passphrase, so under
        # concurrency some records come back with identityId still null. Every
        # one of those silently loses BOTH its Cloudpath GUID description and
        # its suffix rename downstream, because each consumer just skips a
        # result with no identity id.
        #
        # The identity name is the username we supplied, so one paged read of
        # the group recovers all of them at once.
        # ------------------------------------------------------------------
        # Skipped rows are included deliberately: a passphrase that already
        # exists still needs its identity repaired if a previous run died
        # before stripping the suffix.
        missing = [
            r for r in results.succeeded
            if r is not None and not r.identity_id and r.username
        ]
        if missing and resolved_group_id:
            await self.emit(
                f"Resolving {len(missing)} identities R1 did not report at "
                f"creation time"
            )
            # Runs after creation, so it must see the group as it is now.
            by_name = await identities_by_name(max_age=0.0)
            recovered = 0
            already_correct = 0
            for r in missing:
                found = by_name.get(r.username)
                if found:
                    # Still filed under its Cloudpath name, suffix and all —
                    # hand downstream the id so it gets renamed and GUID'd.
                    r.identity_id = found
                    recovered += 1
                    continue
                # Absent under that name. If the stripped name is present, an
                # earlier run already renamed it and there is nothing to fix.
                base = (
                    r.username.rsplit('_', 1)[0]
                    if '_' in r.username else r.username
                )
                if base != r.username and base in by_name:
                    already_correct += 1
            unresolved = len(missing) - recovered - already_correct
            if recovered:
                await self.emit(
                    f"Recovered {recovered} identity id(s) for repair "
                    f"(created by an earlier run, still unfinished)",
                    "success",
                )
            if already_correct:
                await self.emit(
                    f"{already_correct} identities were already renamed by a "
                    f"previous run — nothing to repair"
                )
            if unresolved > 0:
                await self.emit(
                    f"{unresolved} identities could not be resolved — they "
                    f"will miss their Cloudpath GUID and keep their suffix",
                    "warning",
                )
        elif missing:
            await self.emit(
                f"{len(missing)} passphrases have no identity id and no "
                f"identity group to resolve them against",
                "warning",
            )

        # Categorize results
        created: List[PassphraseResult] = []
        failed: List[PassphraseResult] = []
        skipped_count = 0
        updated_count = 0

        attached_count = 0
        replaced_count = 0
        returned_failures = 0

        for result in results.succeeded:
            if result is None:
                continue
            if not result.success:
                # create_one returns its failures rather than raising, so
                # they arrive here among the "succeeded" calls, and were
                # counted as created. They stay in created_passphrases --
                # downstream phases filter on `success` and still use the
                # row's identity -- but they are reported as what they are.
                failed.append(result)
                returned_failures += 1
            elif result.skipped:
                skipped_count += 1
            elif result.updated:
                updated_count += 1
            if result.attached_to_existing:
                attached_count += 1
                if result.replaced_existing:
                    replaced_count += 1
            created.append(result)

        for failure in results.failed:
            item = failure.get('item', {})
            failed.append(PassphraseResult(
                cloudpath_guid=item.get('guid', ''),
                username=item.get('name', ''),
                success=False,
                error=failure.get('error', 'Unknown error'),
            ))

        created_count = (
            len(created) - skipped_count - updated_count - returned_failures
        )

        # Build summary message
        parts = []
        if created_count > 0:
            parts.append(f"{created_count} created")
        if attached_count > 0:
            parts.append(
                f"{attached_count} of those written onto identities that "
                f"already existed"
                + (
                    f" ({replaced_count} replacing the passphrase the "
                    f"identity held)" if replaced_count else ""
                )
            )
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

    async def _identities_by_name(self, group_id: str) -> Dict[str, str]:
        """Page the identity group once, returning name -> identity id."""
        by_name: Dict[str, str] = {}
        page, size = 0, 100
        try:
            while True:
                result = await self.r1_client.identity.get_identities_in_group(
                    group_id=group_id,
                    tenant_id=self.tenant_id,
                    page=page,
                    size=size,
                )
                items = result.get('content', result.get('data', []))
                if not items:
                    break
                for identity in items:
                    name, ident_id = identity.get('name'), identity.get('id')
                    if name and ident_id:
                        by_name[name] = ident_id
                if len(items) < size:
                    break
                page += 1
                if page > 5000:
                    logger.warning("Identity pagination safety limit reached")
                    break
        except Exception as e:
            logger.warning(f"Could not page identity group {group_id}: {e}")
        return by_name

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate passphrase creation inputs."""
        passphrases = inputs.passphrases

        return PhaseValidation(
            valid=True,
            will_create=len(passphrases) > 0,
            estimated_api_calls=len(passphrases),
            notes=[f"{len(passphrases)} passphrases to create"],
        )
