"""
V2 Phase: Create Access Policies

Creates adaptive policies for DPSK access control based on username suffix patterns.

REUSABLE across workflows:
- Cloudpath Import: Parse suffixes from imported identities
- Per-Unit DPSK: Apply policies to newly created passphrases
- Any future workflow with DPSK + rate limiting needs

Shared setup first: RADIUS attribute groups and the policy set. Then one chain
per resident, each finished before it counts as done:
1. Strip suffix and rename identity in R1
2. Create policy matching username + unit SSID
3. Assign policy to property-level policy set

Every step checks live R1 before writing, so a run that stops part way is
finished by running it again.
"""

import asyncio
import logging
import re
import statistics
import time
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set, Tuple

from workflow.phases.registry import register_phase
# One split rule for the whole import; see dpsk_usernames for why it is
# a leaf module rather than a function here.
from workflow.phases.dpsk_usernames import split_account_suffix
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)

# Constants for DPSK policy template
DPSK_POLICY_TEMPLATE_ID = "100"
ATTR_DPSK_USERNAME = 1012
ATTR_WIRELESS_SSID = 1013
DEFAULT_BANDWIDTH = 10_000_000_000  # 10Gbps
DEFAULT_SUFFIX = "gigabit"
MAX_POLICY_WORKERS = 25
MAX_SET_ADD_WORKERS = 8
# R1 answers 500 on this call under parallel load, and the call is safe to repeat.
SET_ADD_ATTEMPTS = 3
SET_ADD_RETRY_SECONDS = 2
# Set adds slow as the set grows, so tuning runs compare at the same size.
SET_SIZE_BUCKET = 50
# R1 can fail a create on its side ("Assign radius attribute group failed") and
# take the same request a moment later.
POLICY_CREATE_ATTEMPTS = 2
POLICY_CREATE_RETRY_SECONDS = 2

# Pattern to detect unit-specific SSIDs like "108@Property_Name"
from workflow.phases.cloudpath.unit_ssid import (  # noqa: F401
    UNIT_SSID_PATTERN, unit_from_ssid, is_unit_ssid,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_summary(samples: List[float]) -> Dict[str, Any]:
    if not samples:
        return {"count": 0, "median": None, "max": None}
    return {
        "count": len(samples),
        "median": round(statistics.median(samples), 2),
        "max": round(max(samples), 2),
    }


class PolicyResult(BaseModel):
    """Result of creating a single policy."""
    account: str
    ssid: str
    suffix: str
    policy_id: Optional[str] = None
    policy_name: Optional[str] = None
    success: bool
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None


class IdentityRenameResult(BaseModel):
    """Result of renaming an identity."""
    identity_id: str
    old_name: str
    new_name: str
    success: bool
    error: Optional[str] = None


def sanitize_policy_name(account: str) -> str:
    """
    Build a policy name from the account alone: "resident101a".

    This used to append the site from the SSID ("resident101a@CedarPoint")
    so a cleanup could tell one property's policies from another's. That is
    no longer load-bearing: cleanup/inventory.py collects adaptive policies
    tenant-wide (scoping by @site stranded most of them) and uses site tokens
    only to flag policy SETS, which are named from the policy_set_name option
    rather than from here.

    Note the names are tenant-scoped, so two properties importing the same
    account id now collide: the second import will find the existing policy
    by name and UPDATE it rather than create its own.
    """
    return re.sub(r'[^a-zA-Z0-9_]', '', account)


def regex_pattern_for_value(value: str) -> str:
    """
    Create regex pattern for exact value matching with explicit anchors.

    R1 API uses regexStringCriteria - we add ^ and $ anchors for
    strict exact matching to prevent partial matches.

    Only escape actual regex metacharacters that could affect matching.
    Characters like @ and _ are NOT regex special chars.
    """
    # Escape regex metacharacters: . * + ? [ ] \ | ( ) { }
    escaped = re.sub(r'([.*+?\[\]\\|(){}])', r'\\\1', value)
    # Add anchors for exact match
    return f"^{escaped}$"


@register_phase("create_access_policies", "Create Access Policies")
class CreateAccessPoliciesPhase(PhaseExecutor):
    """
    Create adaptive policies for DPSK access control.

    Resolves the shared RADIUS groups and policy set, then for each resident:
    1. Strip suffix from username
    2. Create policy matching username + unit SSID
    3. Assign policy to property-level policy set
    """

    class Inputs(BaseModel):
        # Core data - list of passphrase results from create_passphrases phase
        created_passphrases: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Passphrase results with username, identity_id"
        )

        # Original passphrases (for ssid_list lookup)
        passphrases: List[Dict[str, Any]] = Field(
            default_factory=list,
            description="Original passphrases with ssid_list"
        )

        # Adds only: residents already in R1. Missing policies are created and
        # leftover suffixes stripped; a policy that exists is not changed.
        policy_backfill: List[Dict[str, Any]] = Field(default_factory=list)

        # Renames R1 confirmed in update_identity_descriptions.
        renamed_identity_ids: List[str] = Field(default_factory=list)

        # Identity group ID (needed for identity renames)
        identity_group_id: Optional[str] = None
        identity_group_ids: Dict[str, str] = Field(
            default_factory=dict,
            description="Map of group names to IDs"
        )

        # Options
        options: Dict[str, Any] = Field(default_factory=dict)
        # Expected options:
        #   enable_access_policies: bool (default False)
        #   policy_set_name: str (default: venue/property name)
        #   default_suffix: str (default: "gigabit")

    class Outputs(BaseModel):
        radius_groups_created: int = 0
        radius_groups_existing: int = 0
        policies_created: int = 0
        # Reported so a re-run is legible: "0 created, 0 updated, 212 already
        # correct" is the shape of a healthy repeat import. policies_updated
        # was computed but never returned, so the audit could not show it.
        policies_updated: int = 0
        policies_unchanged: int = 0
        policies_failed: int = 0
        # Created, but the add to the set failed, so it does nothing yet. Also
        # counted in policies_failed.
        policies_not_in_set: int = 0
        identities_renamed: int = 0
        renames_no_identity: int = 0
        renames_failed: int = 0
        renames_already_done: int = 0
        skipped_no_ssid: int = 0
        skipped_no_unit_ssid: int = 0
        # Passphrases that never got created, so could never get a policy.
        failed_upstream: int = 0
        # Renames skipped because the target name is already another identity.
        renames_would_collide: int = 0
        policy_set_id: Optional[str] = None
        policy_results: List[PolicyResult] = Field(default_factory=list)
        rename_results: List[IdentityRenameResult] = Field(default_factory=list)
        # Seconds per step and per set size, kept on the job because the
        # backend log rolls off before a long run can be compared.
        tuning: Dict[str, Any] = Field(default_factory=dict)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create access policies for DPSK passphrases."""
        options = inputs.options

        # Check if access policies are enabled
        if not options.get('enable_access_policies', False):
            await self.emit("Access policies not enabled, skipping phase")
            return self.Outputs()

        created_passphrases = [*inputs.created_passphrases, *inputs.policy_backfill]
        original_passphrases = [*inputs.passphrases, *inputs.policy_backfill]
        finish_only_usernames = {row["username"] for row in inputs.policy_backfill}
        default_suffix = options.get('default_suffix', DEFAULT_SUFFIX)

        # Get identity group ID for renames
        identity_group_id = inputs.identity_group_id
        if not identity_group_id and inputs.identity_group_ids:
            identity_group_id = next(iter(inputs.identity_group_ids.values()), None)

        if not created_passphrases:
            await self.emit("No passphrases to create policies for")
            return self.Outputs()

        await self.emit(f"Processing {len(created_passphrases)} passphrases for access policies")

        # Build a lookup from username to original passphrase (for ssid_list)
        # Handle both dict and Pydantic model formats (ParsedPassphrase from validate phase)
        username_to_ssids: Dict[str, List[str]] = {}
        for pp in original_passphrases:
            # Handle Pydantic BaseModel (from ParsedPassphrase)
            if hasattr(pp, 'name') and hasattr(pp, 'ssid_list'):
                name = pp.name
                ssid_list = pp.ssid_list or []
            elif isinstance(pp, dict):
                # Dict format
                name = pp.get('name') or pp.get('username', '')
                ssid_list = pp.get('ssid_list', pp.get('ssidList', []))
            else:
                logger.warning(f"Unknown passphrase format: {type(pp)}")
                continue

            if name and ssid_list:
                username_to_ssids[name] = ssid_list

        await self.emit(f"Built SSID lookup with {len(username_to_ssids)} entries")
        if username_to_ssids:
            sample_key = next(iter(username_to_ssids))
            await self.emit(f"Sample lookup: '{sample_key}' -> {username_to_ssids[sample_key][:2]}...")

        # Step 1: Parse all passphrases to build policy plan
        parsed_entries = []
        identities_to_rename = []
        unique_suffixes: Set[str] = set()
        skipped_no_ssid = 0
        skipped_no_unit_ssid = 0
        failed_upstream = 0
        upstream_errors: List[str] = []

        for pp in created_passphrases:
            # Handle both dict and Pydantic model (PassphraseResult)
            if hasattr(pp, 'success'):
                # Pydantic model
                success = pp.success
                skipped = getattr(pp, 'skipped', False)
                username = getattr(pp, 'username', '')
                # Fall back to existing_identity_id so a re-run repairs the
                # identities an aborted run left half-finished, rather than
                # skipping them. update_identity_descriptions has always done
                # this; the rename never did, so suffixes survived a re-run.
                identity_id = (
                    getattr(pp, 'identity_id', '')
                    or getattr(pp, 'existing_identity_id', '')
                )
            elif isinstance(pp, dict):
                success = pp.get('success', False)
                skipped = pp.get('skipped', False)
                username = pp.get('username', '')
                identity_id = (
                    pp.get('identity_id', '') or pp.get('existing_identity_id', '')
                )
            else:
                logger.warning(f"Unknown created_passphrase format: {type(pp)}")
                continue

            # Skip only failed passphrases (not skipped ones - they exist and need policies too)
            if not success:
                # Counted, not swallowed. When create_passphrase broke, all
                # 215 arrived here with success=False and the phase reported
                # "No policies to create - skipped 0 (no SSID mapping), 0 (no
                # unit SSIDs)" -- three zeros that described the situation
                # perfectly and explained nothing. The upstream failure is the
                # only fact worth printing in that case.
                failed_upstream += 1
                if len(upstream_errors) < 3:
                    err = (
                        getattr(pp, 'error', None) if hasattr(pp, 'error')
                        else pp.get('error')
                    )
                    if err:
                        upstream_errors.append(f"{username or '?'}: {err}")
                continue

            if not username:
                continue

            # Parse username: "12345_fast" → ("12345", "fast")
            #                 "67890"      → ("67890", "gigabit")
            account, suffix = split_account_suffix(username, default_suffix)

            if account != username:
                identities_to_rename.append({
                    "identity_id": identity_id,
                    "old_name": username,
                    "new_name": account,
                })

            unique_suffixes.add(suffix)

            # Get SSIDs for this passphrase
            ssid_list = username_to_ssids.get(username, [])

            if not ssid_list:
                skipped_no_ssid += 1
                continue

            # Filter to unit SSIDs only (e.g., "101@PropertyName")
            unit_ssids = [s for s in ssid_list if is_unit_ssid(s)]

            if not unit_ssids:
                skipped_no_unit_ssid += 1
                continue

            for ssid in unit_ssids:
                # Extract unit number: "101@Sunrise" → "101"
                unit_num = unit_from_ssid(ssid) or "unknown"

                parsed_entries.append({
                    "account": account,
                    "suffix": suffix,
                    "ssid": ssid,
                    "unit_number": unit_num,
                    "original_username": username,
                    "identity_id": identity_id,
                    "finish_only": username in finish_only_usernames,
                })

        if not parsed_entries:
            if failed_upstream:
                # Name the real cause first. Nothing downstream can build a
                # policy for a passphrase that was never created, so this is
                # not a policy problem to investigate -- it is the passphrase
                # phase's failure arriving one phase late.
                await self.emit(
                    f"No policies to create: all {failed_upstream} passphrase(s) "
                    f"failed upstream in create_passphrases, so there is nothing "
                    f"to attach a policy to. Fix that phase first."
                    + (f" First errors: {'; '.join(upstream_errors)}"
                       if upstream_errors else ""),
                    "error",
                )
            else:
                await self.emit(
                    f"No policies to create - skipped {skipped_no_ssid} (no SSID mapping), "
                    f"{skipped_no_unit_ssid} (no unit SSIDs)",
                    "warning"
                )
            return self.Outputs(failed_upstream=failed_upstream)

        await self.emit(
            f"Found {len(parsed_entries)} policy entries, "
            f"{len(unique_suffixes)} unique suffixes: {unique_suffixes}"
        )
        # These were previously reported only when NOTHING was built, so a
        # partial run looked clean while dozens of passphrases got no policy.
        if skipped_no_ssid or skipped_no_unit_ssid or failed_upstream:
            await self.emit(
                f"No policy for "
                f"{skipped_no_ssid + skipped_no_unit_ssid + failed_upstream} "
                f"passphrases: {skipped_no_ssid} had no SSID mapping, "
                f"{skipped_no_unit_ssid} had no unit SSID (property-wide only), "
                f"{failed_upstream} failed to be created upstream",
                "warning",
            )

        # Step 2: Validate/create RADIUS attribute groups
        suffix_to_group_id: Dict[str, str] = {}
        groups_created = 0
        groups_existing = 0
        radius_choices = options.get('radius_tier_choices') or {}

        for suffix in unique_suffixes:
            await self.emit(f"Checking RADIUS attribute group: {suffix}")

            # What the user chose in the plan for a tier with no group.
            choice = radius_choices.get(suffix.lower()) or {}
            group_name = choice.get('group') if choice.get('action') == 'use' else suffix
            existing_group = None

            # Try to query existing groups first
            try:
                groups_response = await self.r1_client.radius_attributes.query_radius_attribute_groups(
                    tenant_id=self.tenant_id,
                    search_string=group_name,
                    limit=100
                )

                groups = groups_response.get('content', groups_response.get('data', []))

                for group in groups:
                    if group.get('name', '').lower() == group_name.lower():
                        existing_group = group
                        break

            except Exception as query_err:
                logger.warning(f"Query for RADIUS group '{suffix}' failed: {query_err}")
                # Continue to creation attempt - group may still exist

            if existing_group:
                suffix_to_group_id[suffix] = existing_group['id']
                groups_existing += 1
                await self.emit(f"Found existing RADIUS group: {group_name} ({existing_group['id']})")
                continue

            if choice.get('action') == 'use':
                await self.emit(
                    f"RADIUS group '{group_name}' chosen for tier '{suffix}' was "
                    f"not found, so those residents get no policy",
                    "error",
                )
                continue

            # Try to create the group
            try:
                await self.emit(f"Creating RADIUS group: {suffix}")
                new_group = await self.r1_client.radius_attributes.create_bandwidth_group(
                    name=suffix,
                    down_bps=DEFAULT_BANDWIDTH,
                    up_bps=DEFAULT_BANDWIDTH,
                    tenant_id=self.tenant_id
                )

                if new_group and 'id' in new_group:
                    suffix_to_group_id[suffix] = new_group['id']
                    groups_created += 1
                    await self.track_resource('radius_attribute_groups', {
                        'id': new_group['id'],
                        'name': suffix,
                    })
                    await self.emit(f"Created RADIUS group: {suffix} ({new_group['id']})")
                else:
                    raise ValueError("No ID in creation response")

            except Exception as create_err:
                error_str = str(create_err).lower()

                # Check if group already exists (409 conflict)
                if '409' in error_str or 'already exists' in error_str or 'conflict' in error_str:
                    logger.info(f"RADIUS group '{suffix}' already exists, querying to get ID")
                    await self.emit(f"RADIUS group '{suffix}' exists, fetching ID...")

                    # Query again to get the existing group's ID
                    try:
                        retry_response = await self.r1_client.radius_attributes.query_radius_attribute_groups(
                            tenant_id=self.tenant_id,
                            search_string=suffix,
                            limit=100
                        )

                        retry_groups = retry_response.get('content', retry_response.get('data', []))
                        for group in retry_groups:
                            if group.get('name', '').lower() == suffix.lower():
                                suffix_to_group_id[suffix] = group['id']
                                groups_existing += 1
                                await self.emit(f"Found existing RADIUS group: {suffix} ({group['id']})")
                                break
                    except Exception as retry_err:
                        logger.warning(f"Retry query for RADIUS group '{suffix}' failed: {retry_err}")

                    # Fallback: if query failed or didn't find, list all groups
                    if suffix not in suffix_to_group_id:
                        try:
                            all_groups = await self.r1_client.radius_attributes.get_radius_attribute_groups(
                                tenant_id=self.tenant_id
                            )
                            # Handle both list and dict responses
                            if isinstance(all_groups, dict):
                                all_groups = all_groups.get('content', all_groups.get('data', []))
                            for group in all_groups:
                                if group.get('name', '').lower() == suffix.lower():
                                    suffix_to_group_id[suffix] = group['id']
                                    groups_existing += 1
                                    await self.emit(f"Found existing RADIUS group: {suffix} ({group['id']})")
                                    break
                        except Exception as fallback_err:
                            logger.error(f"Failed to find existing RADIUS group '{suffix}': {fallback_err}")

                else:
                    logger.error(f"Failed to create RADIUS group {suffix}: {create_err}")

                # Never another tier's group: that is the wrong speed, quietly.
                if suffix not in suffix_to_group_id:
                    await self.emit(f"Could not resolve RADIUS group for {suffix}", "error")

        # Step 3: Get/create Policy Set
        policy_set_name = options.get('policy_set_name', 'Adaptive Policies')
        policy_set_id = None

        try:
            # Query existing policy sets
            sets_response = await self.r1_client.policy_sets.query_policy_sets(
                tenant_id=self.tenant_id,
                search_string=policy_set_name,
                limit=100
            )

            sets = sets_response.get('content', sets_response.get('data', []))
            existing_set = None

            for pset in sets:
                if pset.get('name', '').lower() == policy_set_name.lower():
                    existing_set = pset
                    break

            if existing_set:
                policy_set_id = existing_set['id']
                await self.emit(f"Using existing Policy Set: {policy_set_name}")
            else:
                # Create new policy set
                new_set = await self.r1_client.policy_sets.create_policy_set(
                    name=policy_set_name,
                    tenant_id=self.tenant_id,
                    description=f"Adaptive policies for {policy_set_name}"
                )
                policy_set_id = new_set['id']
                # Track so job-scoped cleanup can remove it. Without this the
                # set survives cleanup and the next import 409s on its policies.
                await self.track_resource('policy_sets', {
                    'id': policy_set_id,
                    'name': policy_set_name,
                })
                await self.emit(f"Created Policy Set: {policy_set_name}")

        except Exception as e:
            logger.error(f"Failed to get/create Policy Set: {e}")
            await self.emit(f"Failed to get/create Policy Set: {e}", "error")
            return self.Outputs(
                radius_groups_created=groups_created,
                radius_groups_existing=groups_existing,
            )

        # Step 4: Query existing policies to enable update-or-create pattern
        await self.emit(
            f"Creating policies: {len(parsed_entries)} entries, "
            f"RADIUS groups resolved: {list(suffix_to_group_id.keys())}"
        )

        # =====================================================================
        # Collapse entries to the policies we actually intend to exist.
        #
        # parsed_entries is one row per (account x unit SSID), but the policy
        # name is the account alone -- so several rows can name ONE policy.
        # The old loop processed each row against the same policy in turn,
        # which meant a second row silently overwrote the first's RADIUS group
        # and, because the condition check only asked "is there an SSID
        # condition" rather than "is it THIS SSID", quietly dropped the second
        # SSID. Collapsing first makes those collisions visible instead.
        # =====================================================================
        desired: Dict[str, Dict[str, Any]] = {}
        suffix_conflicts: List[str] = []
        multi_ssid: List[str] = []

        for entry in parsed_entries:
            name = sanitize_policy_name(entry["account"])
            plan = desired.get(name)
            if plan is None:
                desired[name] = {
                    "account": entry["account"],
                    "suffix": entry["suffix"],
                    "ssids": [entry["ssid"]],
                    "finish_only": entry["finish_only"],
                }
                continue
            plan["finish_only"] = plan["finish_only"] and entry["finish_only"]
            if entry["suffix"] != plan["suffix"]:
                suffix_conflicts.append(
                    f"{entry['account']} ({plan['suffix']} vs {entry['suffix']})"
                )
            if entry["ssid"] not in plan["ssids"]:
                plan["ssids"].append(entry["ssid"])

        for name, plan in desired.items():
            if len(plan["ssids"]) > 1:
                multi_ssid.append(f"{plan['account']} -> {plan['ssids']}")

        if suffix_conflicts:
            await self.emit(
                f"{len(suffix_conflicts)} account(s) appear with more than one "
                f"speed suffix; the policy can carry only one RADIUS group and "
                f"the first wins: {', '.join(suffix_conflicts[:5])}",
                "warning",
            )
        if multi_ssid:
            await self.emit(
                f"{len(multi_ssid)} account(s) map to more than one unit SSID; "
                f"a policy carries a single SSID condition, so only the first "
                f"is enforced: {', '.join(multi_ssid[:5])}",
                "warning",
            )

        await self.emit(
            f"{len(parsed_entries)} entries collapse to {len(desired)} "
            f"policies (named by account)"
        )

        # Build lookup of existing policies by name
        existing_policies: Dict[str, dict] = {}
        try:
            # Paged: the template is tenant-wide, so a tenant past one page would
            # try to re-create what it already has and fail on the duplicate name.
            page = 0
            while page < 50:
                policies_response = await self.r1_client.policy_sets.query_template_policies(
                    template_id=DPSK_POLICY_TEMPLATE_ID,
                    tenant_id=self.tenant_id,
                    page=page,
                    limit=1000
                )
                items = policies_response.get('content', policies_response.get('data', []))
                for policy in items:
                    name = policy.get('name', '')
                    if name:
                        existing_policies[name] = policy
                if len(items) < 1000:
                    break
                page += 1
            if existing_policies:
                await self.emit(f"Found {len(existing_policies)} existing policies in template")
        except Exception as e:
            logger.warning(f"Could not query existing policies: {e}")

        # Existing policy-set membership, fetched once. The old code fired an
        # assign call for every policy on every run and swallowed the
        # "already assigned" error, so a re-run of 200 policies made 200
        # pointless writes.
        assigned_policy_ids: Set[str] = set()
        next_priority = 0
        number_priorities = True
        try:
            prioritized = await self.r1_client.policy_sets.get_prioritized_policies(
                policy_set_id=policy_set_id,
                tenant_id=self.tenant_id,
            )
            if isinstance(prioritized, dict):
                prioritized = prioritized.get('content', prioritized.get('data', []))
            for row in prioritized or []:
                if isinstance(row, dict):
                    pid = row.get('policyId') or row.get('id')
                    if pid:
                        assigned_policy_ids.add(pid)
                    if isinstance(row.get('priority'), int):
                        next_priority = max(next_priority, row['priority'] + 1)
            await self.emit(
                f"Policy set already holds {len(assigned_policy_ids)} policies"
            )
        except Exception as e:
            # Fall back to attempting the assign, as before.
            logger.warning(f"Could not list policy set members: {e}")
            assigned_policy_ids = set()
            # The numbers in use are unknown, so R1 has to choose.
            number_priorities = False

        policy_results: List[PolicyResult] = []
        policies_created = 0
        policies_updated = 0
        policies_unchanged = 0
        policies_failed = 0
        policies_not_in_set = 0

        # Left to itself R1 hands parallel adds the same priority, slows as the
        # set grows and deadlocks: one site's 1,480 policies held 673 distinct
        # numbers, alongside the 500s. Numbered, adds hold about 0.2s at any size.
        set_add_workers = max(
            1, min(int(options.get('set_add_workers') or 1), MAX_SET_ADD_WORKERS)
        )
        set_add_slot = asyncio.Semaphore(set_add_workers)
        step_seconds: Dict[str, List[float]] = {"rename": [], "policy": [], "set_add": []}
        set_add_500s = 0
        policy_create_retries = 0
        by_set_size: Dict[int, Dict[str, Any]] = {}

        async def add_to_set(policy_id: str) -> None:
            """Add one policy to the set, in turn, retrying R1's 500s."""
            nonlocal next_priority, set_add_500s
            priority = None
            if number_priorities:
                priority = next_priority
                next_priority += 1
            async with set_add_slot:
                bucket = by_set_size.setdefault(
                    len(assigned_policy_ids) // SET_SIZE_BUCKET * SET_SIZE_BUCKET,
                    {
                        "seconds": [], "errors_500": 0, "first": time.monotonic(), "last": 0.0,
                        "started_utc": _utc_now(), "ended_utc": None,
                    },
                )
                for attempt in range(1, SET_ADD_ATTEMPTS + 1):
                    started = time.monotonic()
                    try:
                        await self.r1_client.policy_sets.assign_policy_to_policy_set(
                            policy_set_id=policy_set_id,
                            policy_id=policy_id,
                            tenant_id=self.tenant_id,
                            priority=priority,
                        )
                        break
                    except Exception as err:
                        if '(500)' not in str(err):
                            raise
                        set_add_500s += 1
                        bucket["errors_500"] += 1
                        if attempt == SET_ADD_ATTEMPTS:
                            raise
                        await asyncio.sleep(SET_ADD_RETRY_SECONDS * attempt)
                now = time.monotonic()
                step_seconds["set_add"].append(now - started)
                bucket["seconds"].append(now - started)
                bucket["last"] = now
                bucket["ended_utc"] = _utc_now()

        async def run_policy(policy_name: str, plan: Dict[str, Any]) -> None:
            nonlocal policies_created, policies_updated, policies_unchanged, policies_failed
            nonlocal policies_not_in_set, policy_create_retries
            account = plan["account"]
            suffix = plan["suffix"]
            ssid = plan["ssids"][0]
            finish_only = plan["finish_only"]

            # Get RADIUS group ID for this suffix
            radius_group_id = suffix_to_group_id.get(suffix)
            if not radius_group_id:
                policy_results.append(PolicyResult(
                    account=account,
                    ssid=ssid,
                    suffix=suffix,
                    success=False,
                    error=f"No RADIUS group for suffix: {suffix}"
                ))
                policies_failed += 1
                return

            want_user = regex_pattern_for_value(account)
            want_ssid = regex_pattern_for_value(ssid)

            policy_started = time.monotonic()
            try:
                existing_policy = existing_policies.get(policy_name)
                policy_id = None
                changes: List[str] = []

                if existing_policy:
                    # =========================================================
                    # Compare before writing.
                    #
                    # A re-run used to PATCH the policy, GET its conditions,
                    # and POST an assignment for every policy whether or not
                    # anything differed. Now nothing is written unless it is
                    # actually wrong, so a repeat import over an unchanged
                    # property makes reads only.
                    # =========================================================
                    policy_id = existing_policy.get('id')

                    current_response = existing_policy.get('onMatchResponse')
                    if (
                        current_response is None
                        and 'onMatchResponse' not in existing_policy
                        and not finish_only
                    ):
                        # The list payload does not carry it; ask for the policy.
                        try:
                            full = await self.r1_client.policy_sets.get_template_policy(
                                template_id=DPSK_POLICY_TEMPLATE_ID,
                                policy_id=policy_id,
                                tenant_id=self.tenant_id,
                            )
                            if isinstance(full, dict):
                                current_response = full.get('onMatchResponse')
                        except Exception as e:
                            logger.debug(
                                f"Could not read a policy for comparison "
                                f"({e}); treating the RADIUS group as unknown"
                            )
                            current_response = None

                    if current_response != radius_group_id and not finish_only:
                        await self.r1_client.policy_sets.update_template_policy(
                            template_id=DPSK_POLICY_TEMPLATE_ID,
                            policy_id=policy_id,
                            policy_data={"onMatchResponse": radius_group_id},
                            tenant_id=self.tenant_id
                        )
                        changes.append("RADIUS group")

                    # Finishing an earlier run only adds what is missing, and the
                    # policy list already says nothing is. One read per policy
                    # here cost a large property thousands of calls per re-run.
                    has_all_conditions = (
                        finish_only and existing_policy.get('conditionsCount') == 2
                    )
                    existing_conditions = []
                    if not has_all_conditions:
                        existing_conditions = await self.r1_client.policy_sets.get_policy_conditions(
                            template_id=DPSK_POLICY_TEMPLATE_ID,
                            policy_id=policy_id,
                            tenant_id=self.tenant_id
                        )
                    if isinstance(existing_conditions, dict):
                        existing_conditions = existing_conditions.get(
                            'content', existing_conditions.get('data', [])
                        )

                    # attribute id -> (condition id, current regex). Comparing
                    # the PATTERN matters: checking only that a condition of
                    # the right type existed left stale usernames and SSIDs in
                    # place forever.
                    by_attr: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
                    for cond in existing_conditions or []:
                        if not isinstance(cond, dict):
                            logger.warning(
                                f"Unexpected condition format: {type(cond)} - {cond}"
                            )
                            continue
                        rule = cond.get('evaluationRule') or {}
                        by_attr[cond.get('templateAttributeId')] = (
                            cond.get('id'),
                            rule.get('regexStringCriteria'),
                        )

                    for attr_id, want, label in (
                        () if has_all_conditions else (
                            (ATTR_DPSK_USERNAME, want_user, "username condition"),
                            (ATTR_WIRELESS_SSID, want_ssid, "SSID condition"),
                        )
                    ):
                        cond_id, current = by_attr.get(attr_id, (None, None))
                        if cond_id is None:
                            await self.r1_client.policy_sets.create_string_condition(
                                template_id=DPSK_POLICY_TEMPLATE_ID,
                                policy_id=policy_id,
                                attribute_id=attr_id,
                                regex_pattern=want,
                                tenant_id=self.tenant_id
                            )
                            changes.append(f"added {label}")
                        elif current != want and not finish_only:
                            await self.r1_client.policy_sets.update_policy_condition(
                                template_id=DPSK_POLICY_TEMPLATE_ID,
                                policy_id=policy_id,
                                condition_id=cond_id,
                                condition_data={
                                    "evaluationRule": {
                                        "criteriaType": "StringCriteria",
                                        "regexStringCriteria": want,
                                    }
                                },
                                tenant_id=self.tenant_id
                            )
                            changes.append(f"corrected {label}")

                    if changes:
                        policies_updated += 1
                        logger.info(
                            f"Policy {policy_id} updated: "
                            f"{', '.join(changes)}"
                        )
                    else:
                        policies_unchanged += 1

                else:
                    # Create new policy
                    policy_data = {
                        "name": policy_name,
                        "onMatchResponse": radius_group_id
                    }

                    for attempt in range(1, POLICY_CREATE_ATTEMPTS + 1):
                        policy_response = await self.r1_client.policy_sets.create_template_policy(
                            template_id=DPSK_POLICY_TEMPLATE_ID,
                            policy_data=policy_data,
                            tenant_id=self.tenant_id
                        )

                        policy_id = policy_response.get('id')

                        if not policy_id:
                            raise ValueError("No policy ID returned")

                        # Wait for R1's task (202 = async). A task that failed
                        # left no policy, so asking again is safe; one still
                        # running is never re-sent.
                        try:
                            request_id = policy_response.get('requestId')
                            if request_id:
                                await self.r1_client.await_task_completion(
                                    request_id=request_id,
                                    override_tenant_id=self.tenant_id,
                                )
                            else:
                                await self.r1_client.policy_sets.await_policy_creation(
                                    template_id=DPSK_POLICY_TEMPLATE_ID,
                                    policy_id=policy_id,
                                    tenant_id=self.tenant_id
                                )
                            break
                        except TimeoutError:
                            raise
                        except Exception as create_err:
                            if attempt == POLICY_CREATE_ATTEMPTS:
                                raise
                            policy_create_retries += 1
                            logger.warning(
                                f"Policy {policy_id} failed in R1, creating it "
                                f"again: {create_err}"
                            )
                            await asyncio.sleep(POLICY_CREATE_RETRY_SECONDS)

                    # Username and SSID conditions, anchored for exact match
                    await self.r1_client.policy_sets.create_string_condition(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        attribute_id=ATTR_DPSK_USERNAME,
                        regex_pattern=want_user,
                        tenant_id=self.tenant_id
                    )
                    await self.r1_client.policy_sets.create_string_condition(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        attribute_id=ATTR_WIRELESS_SSID,
                        regex_pattern=want_ssid,
                        tenant_id=self.tenant_id
                    )

                    policies_created += 1
                    await self.track_resource('policies', {
                        'id': policy_id,
                        'name': policy_name,
                        'template_id': DPSK_POLICY_TEMPLATE_ID,
                    })

                if not existing_policy or changes:
                    step_seconds["policy"].append(time.monotonic() - policy_started)

                # Assign to the policy set only if it is not already a member.
                if policy_id and policy_id not in assigned_policy_ids:
                    try:
                        await add_to_set(policy_id)
                        assigned_policy_ids.add(policy_id)
                    except Exception as assign_err:
                        if 'already' in str(assign_err).lower():
                            assigned_policy_ids.add(policy_id)
                        else:
                            # A policy that is not in the set does nothing, so
                            # this is a real failure, not a footnote.
                            logger.error(
                                f"Policy {policy_id} created but NOT assigned "
                                f"to the policy set: {assign_err}"
                            )
                            await self.emit(
                                f"Policy '{policy_name}' is not in the policy set "
                                f"and will not take effect: {assign_err}",
                                "error",
                            )
                            policies_failed += 1
                            policies_not_in_set += 1
                            policy_results.append(PolicyResult(
                                account=account,
                                ssid=ssid,
                                suffix=suffix,
                                policy_id=policy_id,
                                policy_name=policy_name,
                                success=False,
                                error=f"Not in the policy set: {assign_err}",
                            ))
                            return

                policy_results.append(PolicyResult(
                    account=account,
                    ssid=ssid,
                    suffix=suffix,
                    policy_id=policy_id,
                    policy_name=policy_name,
                    success=True,
                    skipped=bool(existing_policy) and not changes,
                    skip_reason=(
                        "already correct"
                        if existing_policy and not changes else None
                    ),
                ))

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to create/update a policy: {error_msg}")
                policy_results.append(PolicyResult(
                    account=account,
                    ssid=ssid,
                    suffix=suffix,
                    policy_name=policy_name,
                    success=False,
                    error=error_msg
                ))
                policies_failed += 1

        # Step 5: One chain per resident, renames before the policy.
        #
        # They used to be two passes, policies then renames, so a run cut off
        # during the policies left every identity suffixed: 2,296 of them on a
        # site whose policies were otherwise nearly done. Renaming first loses
        # nothing, since a suffixed identity cannot match its policy anyway.
        rename_results: List[IdentityRenameResult] = []
        identities_renamed = 0
        renames_no_identity = 0
        renames_failed = 0
        renames_already_done = 0
        renames_would_collide = 0
        collision_examples: List[str] = []

        # What the group holds right now, so a re-run does not PATCH every
        # identity it already renamed on the last one. One paged read replaces
        # N writes.
        current_names: Dict[str, str] = {}
        names_taken: Dict[str, str] = {}
        already_renamed = set(inputs.renamed_identity_ids)
        still_to_rename = [
            i for i in identities_to_rename if i["identity_id"] not in already_renamed
        ]
        if still_to_rename and identity_group_id:
            await self.emit(f"Checking {len(still_to_rename)} identities for suffix removal")
            current_names = await self._identity_names_by_id(identity_group_id)
            # name -> id, to see a collision BEFORE asking R1 to make one.
            names_taken = {n: i for i, n in current_names.items()}
        elif still_to_rename and not identity_group_id:
            logger.warning("No identity group ID available, skipping identity renames")
            await self.emit("Skipping identity renames: no group ID available", "warning")

        async def run_rename(identity: Dict[str, Any]) -> None:
            nonlocal identities_renamed, renames_no_identity, renames_failed
            nonlocal renames_already_done, renames_would_collide
            identity_id = identity["identity_id"]
            old_name = identity["old_name"]
            new_name = identity["new_name"]

            if identity_id in already_renamed or (
                identity_id and current_names.get(identity_id) == new_name
            ):
                renames_already_done += 1
                return

            # =====================================================
            # Look before renaming onto a name someone else holds.
            #
            # Usually that someone else is THIS resident, imported
            # on an earlier run and already stripped: the import
            # failed to recognise its own output upstream, minted a
            # second identity from the file name, and is now trying
            # to rename the copy onto the original. R1 answers
            # GENERAL-010 and the resident is left with a duplicate
            # identity still carrying its tier suffix.
            #
            # Checking here turns that from a failed write into a
            # named finding. The real repair is upstream -- validate
            # now matches the stripped name -- so anything still
            # reaching this point is a genuine duplicate worth
            # reporting rather than retrying.
            # =====================================================
            holder = names_taken.get(new_name)
            if holder and holder != identity_id:
                renames_would_collide += 1
                collision_examples.append(
                    f"{old_name} -> {new_name} (held by {holder})"
                )
                return

            if not identity_id:
                # No identity id means we never learned which identity
                # this passphrase belongs to, so the suffix stays on
                # the name. Count it — this used to vanish silently.
                renames_no_identity += 1
                return

            try:
                rename_started = time.monotonic()
                await self.r1_client.identity.update_identity(
                    group_id=identity_group_id,
                    identity_id=identity_id,
                    name=new_name,
                    tenant_id=self.tenant_id
                )
                step_seconds["rename"].append(time.monotonic() - rename_started)

                rename_results.append(IdentityRenameResult(
                    identity_id=identity_id,
                    old_name=old_name,
                    new_name=new_name,
                    success=True
                ))
                identities_renamed += 1
                current_names[identity_id] = new_name

            except Exception as e:
                # The common cause is a base-name collision: two
                # passphrases for one account on different tiers
                # (acct_gigabit + acct_superfast) both strip to the
                # same name, and R1 rejects the second with
                # GENERAL-010. Non-fatal — that identity keeps its
                # suffix, which is what keeps the two distinguishable.
                renames_failed += 1
                logger.warning(f"Failed to rename identity {identity_id}: {e}")
                rename_results.append(IdentityRenameResult(
                    identity_id=identity_id,
                    old_name=old_name,
                    new_name=new_name,
                    success=False,
                    error=str(e)
                ))

        # Keyed by policy name: two tiers of one account share a policy, and an
        # identity with no unit SSID has a rename and no policy.
        chains: Dict[str, Dict[str, Any]] = {
            name: {"policy": plan, "renames": []} for name, plan in desired.items()
        }
        if identity_group_id:
            for identity in identities_to_rename:
                chains.setdefault(
                    sanitize_policy_name(identity["new_name"]),
                    {"policy": None, "renames": []},
                )["renames"].append(identity)

        chains_done = 0

        async def run_chain(name: str) -> None:
            nonlocal chains_done
            chain = chains[name]
            for identity in chain["renames"]:
                await run_rename(identity)
            if chain["policy"] is not None:
                await run_policy(name, chain["policy"])
            chains_done += 1
            await self.emit_progress(
                current=chains_done,
                total=len(chains),
                failed=policies_failed + renames_failed,
                item_name="resident",
                detail=(
                    f"{policies_created} policies created, "
                    f"{policies_unchanged} already correct, "
                    f"{identities_renamed + renames_already_done} renamed"
                ),
            )

        # Adds to the set take their own turns, so workers speed up the other steps.
        workers = max(1, min(int(options.get('policy_workers') or 1), MAX_POLICY_WORKERS))
        await self.emit(
            f"Set adds: {set_add_workers} at a time, priority "
            f"{'set by this job' if number_priorities else 'chosen by R1'}"
        )
        chains_started_utc = _utc_now()
        await self.emit(f"Working through {len(chains)} residents with {workers} worker(s)")
        await self.emit_progress(0, len(chains), item_name="resident")
        await self.parallel_map(
            list(chains), run_chain, max_concurrent=workers,
            item_name="resident", emit_progress=False,
        )

        await self.emit(
            f"Policies: {policies_created} created, {policies_updated} updated, "
            f"{policies_unchanged} already correct, {policies_failed} failed",
            "success" if policies_failed == 0 else "warning"
        )

        if policies_not_in_set:
            await self.emit(
                f"{policies_not_in_set} policies are NOT in the policy set and "
                f"will not take effect. Run Adds only to finish them.",
                "error",
            )

        tuning: Dict[str, Any] = {
            "started_utc": chains_started_utc,
            "finished_utc": _utc_now(),
            "policy_workers": workers,
            "set_add_workers": set_add_workers,
            "priorities_numbered": number_priorities,
            "set_add_500s": set_add_500s,
            "policy_create_retries": policy_create_retries,
            "steps": {
                step: _seconds_summary(samples)
                for step, samples in step_seconds.items() if samples
            },
            "by_set_size": [
                {
                    "set_size_from": size,
                    "started_utc": bucket["started_utc"],
                    "ended_utc": bucket["ended_utc"],
                    **_seconds_summary(bucket["seconds"]),
                    "errors_500": bucket["errors_500"],
                    "adds_per_min": (
                        round(len(bucket["seconds"]) / (bucket["last"] - bucket["first"]) * 60, 1)
                        if bucket["last"] > bucket["first"] else None
                    ),
                }
                for size, bucket in sorted(by_set_size.items())
            ],
        }
        await self.emit(
            "Timing: "
            + ", ".join(
                f"{step} median {summary['median']}s"
                for step, summary in tuning["steps"].items()
            )
            + f", {set_add_500s} set-add 500s"
        )

        if renames_would_collide:
            await self.emit(
                f"{renames_would_collide} identity(s) NOT renamed: the "
                f"target name is already another identity in this group "
                f"— {'; '.join(collision_examples[:5])}"
                f"{'...' if len(collision_examples) > 5 else ''}. That "
                f"means this resident exists TWICE in R1: once from an "
                f"earlier run (already stripped) and once minted by this "
                f"one. The duplicate is left in place for you to review; "
                f"setting identity descriptions lets future runs match by "
                f"Cloudpath GUID and stop it recurring.",
                "warning",
            )

        if renames_no_identity or renames_failed:
            detail = []
            if renames_no_identity:
                detail.append(
                    f"{renames_no_identity} skipped with no identity id"
                )
            if renames_failed:
                detail.append(
                    f"{renames_failed} rejected by R1 (usually two "
                    f"tiers of one account colliding on the same "
                    f"stripped name)"
                )
            await self.emit(
                f"Renamed {identities_renamed} identities; "
                + "; ".join(detail)
                + " — these keep their username suffix",
                "warning",
            )
        elif identities_to_rename and identity_group_id:
            await self.emit(f"Renamed {identities_renamed} identities")

        await self.emit(
            f"Access policies complete: {policies_created} policies, "
            f"{groups_created} new RADIUS groups, {identities_renamed} identities renamed"
            + (f", {renames_already_done} already renamed" if renames_already_done else ""),
            "success"
        )

        return self.Outputs(
            radius_groups_created=groups_created,
            radius_groups_existing=groups_existing,
            policies_created=policies_created,
            policies_updated=policies_updated,
            policies_unchanged=policies_unchanged,
            policies_failed=policies_failed,
            policies_not_in_set=policies_not_in_set,
            identities_renamed=identities_renamed,
            renames_no_identity=renames_no_identity,
            renames_failed=renames_failed,
            renames_already_done=renames_already_done,
            skipped_no_ssid=skipped_no_ssid,
            skipped_no_unit_ssid=skipped_no_unit_ssid,
            failed_upstream=failed_upstream,
            renames_would_collide=renames_would_collide,
            policy_set_id=policy_set_id,
            policy_results=policy_results,
            rename_results=rename_results,
            tuning=tuning,
        )

    async def _identity_names_by_id(self, group_id: str) -> Dict[str, str]:
        """
        Page the identity group once, returning identity id -> current name.

        Used so a re-run only renames what still carries a suffix. Without it
        the phase PATCHed every identity it had already renamed, every time.
        On failure it returns {} -- which falls back to the old behaviour of
        attempting each rename, rather than skipping work that may be needed.
        """
        by_id: Dict[str, str] = {}
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
                    ident_id, name = identity.get('id'), identity.get('name')
                    if ident_id and name:
                        by_id[ident_id] = name
                if len(items) < size:
                    break
                page += 1
                if page > 5000:
                    logger.warning("Identity pagination safety limit reached")
                    break
        except Exception as e:
            logger.warning(f"Could not page identity group {group_id}: {e}")
        return by_id

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Validate access policy creation inputs."""
        options = inputs.options

        if not options.get('enable_access_policies', False):
            return PhaseValidation(
                valid=True,
                will_create=False,
                notes=["Access policies not enabled"],
            )

        created_passphrases = inputs.created_passphrases
        original_passphrases = inputs.passphrases

        # Estimate policy count
        policy_count = 0
        for pp in original_passphrases:
            ssid_list = pp.get('ssid_list', pp.get('ssidList', []))
            unit_ssids = [s for s in ssid_list if is_unit_ssid(s)]
            policy_count += len(unit_ssids)

        return PhaseValidation(
            valid=True,
            will_create=policy_count > 0,
            estimated_api_calls=policy_count * 4,  # policy + 2 conditions + assign
            notes=[f"Estimated {policy_count} policies to create"],
        )
