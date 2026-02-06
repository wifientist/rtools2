"""
V2 Phase: Create Access Policies

Creates adaptive policies for DPSK access control based on username suffix patterns.

REUSABLE across workflows:
- Cloudpath Import: Parse suffixes from imported identities
- Per-Unit DPSK: Apply policies to newly created passphrases
- Any future workflow with DPSK + rate limiting needs

For each identity:
1. Parse suffix from username (default: "gigabit" if no suffix)
2. Strip suffix and rename identity in R1
3. Validate/create RADIUS attribute group matching suffix
4. Create policy matching username + unit SSID
5. Assign policy to property-level policy set
"""

import logging
import re
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

logger = logging.getLogger(__name__)

# Constants for DPSK policy template
DPSK_POLICY_TEMPLATE_ID = "100"
ATTR_DPSK_USERNAME = 1012
ATTR_WIRELESS_SSID = 1013
DEFAULT_BANDWIDTH = 10_000_000_000  # 10Gbps
DEFAULT_SUFFIX = "gigabit"

# Pattern to detect unit-specific SSIDs like "108@Property_Name"
UNIT_SSID_PATTERN = re.compile(r'^(\d+)@(.+)$')


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


def sanitize_policy_name(account: str, unit_number: str = None) -> str:
    """Create a safe policy name from account (unit_number no longer used)."""
    # Remove any non-alphanumeric except @ and _
    safe_account = re.sub(r'[^a-zA-Z0-9_]', '', account)
    return safe_account


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

    For each identity with a suffix pattern (name_suffix):
    1. Strip suffix from username
    2. Validate/create RADIUS attribute group matching suffix
    3. Create policy matching username + unit SSID
    4. Assign policy to property-level policy set
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
        policies_failed: int = 0
        identities_renamed: int = 0
        policy_set_id: Optional[str] = None
        policy_results: List[PolicyResult] = Field(default_factory=list)
        rename_results: List[IdentityRenameResult] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Create access policies for DPSK passphrases."""
        options = inputs.options

        # Check if access policies are enabled
        if not options.get('enable_access_policies', False):
            await self.emit("Access policies not enabled, skipping phase")
            return self.Outputs()

        created_passphrases = inputs.created_passphrases
        original_passphrases = inputs.passphrases
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
                name = pp.get('name', '')
                ssid_list = pp.get('ssid_list', pp.get('ssidList', []))
            else:
                logger.warning(f"Unknown passphrase format: {type(pp)}")
                continue

            if name and ssid_list:
                username_to_ssids[name] = ssid_list
                logger.debug(f"Mapped {name} -> {len(ssid_list)} SSIDs: {ssid_list[:3]}...")

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

        for pp in created_passphrases:
            # Handle both dict and Pydantic model (PassphraseResult)
            if hasattr(pp, 'success'):
                # Pydantic model
                success = pp.success
                skipped = getattr(pp, 'skipped', False)
                username = getattr(pp, 'username', '')
                identity_id = getattr(pp, 'identity_id', '')
            elif isinstance(pp, dict):
                success = pp.get('success', False)
                skipped = pp.get('skipped', False)
                username = pp.get('username', '')
                identity_id = pp.get('identity_id', '')
            else:
                logger.warning(f"Unknown created_passphrase format: {type(pp)}")
                continue

            # Skip only failed passphrases (not skipped ones - they exist and need policies too)
            if not success:
                continue

            if not username:
                continue

            # Parse username: "12345_fast" → ("12345", "fast")
            #                 "67890"      → ("67890", "gigabit")
            if "_" in username:
                parts = username.rsplit("_", 1)
                account = parts[0]
                suffix = parts[1]
                # Track for identity rename
                identities_to_rename.append({
                    "identity_id": identity_id,
                    "old_name": username,
                    "new_name": account,
                })
            else:
                account = username
                suffix = default_suffix

            unique_suffixes.add(suffix)

            # Get SSIDs for this passphrase
            ssid_list = username_to_ssids.get(username, [])

            if not ssid_list:
                skipped_no_ssid += 1
                logger.warning(f"No SSID mapping found for username: {username}")
                continue

            # Filter to unit SSIDs only (e.g., "101@PropertyName")
            unit_ssids = [s for s in ssid_list if UNIT_SSID_PATTERN.match(s)]

            if not unit_ssids:
                skipped_no_unit_ssid += 1
                logger.debug(f"No unit SSIDs for {username} (SSIDs: {ssid_list}), skipping policy creation")
                continue

            for ssid in unit_ssids:
                # Extract unit number: "101@Sunrise" → "101"
                unit_match = UNIT_SSID_PATTERN.match(ssid)
                unit_num = unit_match.group(1) if unit_match else "unknown"

                parsed_entries.append({
                    "account": account,
                    "suffix": suffix,
                    "ssid": ssid,
                    "unit_number": unit_num,
                    "original_username": username,
                    "identity_id": identity_id,
                })

        if not parsed_entries:
            await self.emit(
                f"No policies to create - skipped {skipped_no_ssid} (no SSID mapping), "
                f"{skipped_no_unit_ssid} (no unit SSIDs)",
                "warning"
            )
            return self.Outputs()

        await self.emit(
            f"Found {len(parsed_entries)} policy entries, "
            f"{len(unique_suffixes)} unique suffixes: {unique_suffixes}"
        )

        # Step 2: Validate/create RADIUS attribute groups
        suffix_to_group_id: Dict[str, str] = {}
        groups_created = 0
        groups_existing = 0

        for suffix in unique_suffixes:
            await self.emit(f"Checking RADIUS attribute group: {suffix}")

            existing_group = None

            # Try to query existing groups first
            try:
                groups_response = await self.r1_client.radius_attributes.query_radius_attribute_groups(
                    tenant_id=self.tenant_id,
                    search_string=suffix,
                    limit=100
                )

                groups = groups_response.get('content', groups_response.get('data', []))

                for group in groups:
                    if group.get('name', '').lower() == suffix.lower():
                        existing_group = group
                        break

            except Exception as query_err:
                logger.warning(f"Query for RADIUS group '{suffix}' failed: {query_err}")
                # Continue to creation attempt - group may still exist

            if existing_group:
                suffix_to_group_id[suffix] = existing_group['id']
                groups_existing += 1
                await self.emit(f"Found existing RADIUS group: {suffix} ({existing_group['id']})")
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

                # Use default suffix as fallback if we still don't have this one
                if suffix not in suffix_to_group_id:
                    if suffix != default_suffix and default_suffix in suffix_to_group_id:
                        suffix_to_group_id[suffix] = suffix_to_group_id[default_suffix]
                        await self.emit(f"Using {default_suffix} as fallback for {suffix}", "warning")
                    else:
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

        # Build lookup of existing policies by name
        existing_policies: Dict[str, dict] = {}
        try:
            policies_response = await self.r1_client.policy_sets.query_template_policies(
                template_id=DPSK_POLICY_TEMPLATE_ID,
                tenant_id=self.tenant_id,
                limit=1000
            )
            for policy in policies_response.get('content', policies_response.get('data', [])):
                name = policy.get('name', '')
                if name:
                    existing_policies[name] = policy
            if existing_policies:
                await self.emit(f"Found {len(existing_policies)} existing policies in template")
        except Exception as e:
            logger.warning(f"Could not query existing policies: {e}")

        policy_results: List[PolicyResult] = []
        policies_created = 0
        policies_updated = 0
        policies_failed = 0

        for entry in parsed_entries:
            account = entry["account"]
            suffix = entry["suffix"]
            ssid = entry["ssid"]
            unit_num = entry["unit_number"]

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
                continue

            policy_name = sanitize_policy_name(account, unit_num)

            try:
                # Check if policy already exists
                existing_policy = existing_policies.get(policy_name)
                policy_id = None

                if existing_policy:
                    # Policy exists - update it and add conditions
                    policy_id = existing_policy.get('id')
                    logger.info(f"Policy '{policy_name}' exists ({policy_id}), updating...")

                    # Update RADIUS group response if different
                    await self.r1_client.policy_sets.update_template_policy(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        policy_data={"onMatchResponse": radius_group_id},
                        tenant_id=self.tenant_id
                    )

                    # Check existing conditions to avoid duplicates
                    existing_conditions = await self.r1_client.policy_sets.get_policy_conditions(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        tenant_id=self.tenant_id
                    )

                    # Handle various response formats
                    if isinstance(existing_conditions, dict):
                        existing_conditions = existing_conditions.get('content', existing_conditions.get('data', []))

                    has_username_cond = False
                    has_ssid_cond = False
                    for cond in existing_conditions:
                        # Handle both dict and unexpected formats
                        if isinstance(cond, dict):
                            attr_id = cond.get('templateAttributeId')
                            if attr_id == ATTR_DPSK_USERNAME:
                                has_username_cond = True
                            elif attr_id == ATTR_WIRELESS_SSID:
                                has_ssid_cond = True
                        else:
                            logger.warning(f"Unexpected condition format: {type(cond)} - {cond}")

                    # Add missing conditions
                    if not has_username_cond:
                        await self.r1_client.policy_sets.create_string_condition(
                            template_id=DPSK_POLICY_TEMPLATE_ID,
                            policy_id=policy_id,
                            attribute_id=ATTR_DPSK_USERNAME,
                            regex_pattern=regex_pattern_for_value(account),
                            tenant_id=self.tenant_id
                        )

                    if not has_ssid_cond:
                        await self.r1_client.policy_sets.create_string_condition(
                            template_id=DPSK_POLICY_TEMPLATE_ID,
                            policy_id=policy_id,
                            attribute_id=ATTR_WIRELESS_SSID,
                            regex_pattern=regex_pattern_for_value(ssid),
                            tenant_id=self.tenant_id
                        )

                    policies_updated += 1

                else:
                    # Create new policy
                    policy_data = {
                        "name": policy_name,
                        "onMatchResponse": radius_group_id
                    }

                    policy_response = await self.r1_client.policy_sets.create_template_policy(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_data=policy_data,
                        tenant_id=self.tenant_id
                    )

                    policy_id = policy_response.get('id')

                    if not policy_id:
                        raise ValueError("No policy ID returned")

                    # Wait for policy creation to complete (202 = async)
                    await self.r1_client.policy_sets.await_policy_creation(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        tenant_id=self.tenant_id
                    )

                    # Add username condition with ^ and $ anchors for exact match
                    await self.r1_client.policy_sets.create_string_condition(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        attribute_id=ATTR_DPSK_USERNAME,
                        regex_pattern=regex_pattern_for_value(account),
                        tenant_id=self.tenant_id
                    )

                    # Add SSID condition
                    await self.r1_client.policy_sets.create_string_condition(
                        template_id=DPSK_POLICY_TEMPLATE_ID,
                        policy_id=policy_id,
                        attribute_id=ATTR_WIRELESS_SSID,
                        regex_pattern=regex_pattern_for_value(ssid),
                        tenant_id=self.tenant_id
                    )

                    policies_created += 1

                # Assign to policy set (idempotent - will skip if already assigned)
                try:
                    await self.r1_client.policy_sets.assign_policy_to_policy_set(
                        policy_set_id=policy_set_id,
                        policy_id=policy_id,
                        tenant_id=self.tenant_id
                    )
                except Exception as assign_err:
                    # Ignore "already assigned" errors
                    if 'already' not in str(assign_err).lower():
                        logger.warning(f"Failed to assign policy to set: {assign_err}")

                policy_results.append(PolicyResult(
                    account=account,
                    ssid=ssid,
                    suffix=suffix,
                    policy_id=policy_id,
                    policy_name=policy_name,
                    success=True
                ))

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to create/update policy {policy_name}: {error_msg}")
                policy_results.append(PolicyResult(
                    account=account,
                    ssid=ssid,
                    suffix=suffix,
                    policy_name=policy_name,
                    success=False,
                    error=error_msg
                ))
                policies_failed += 1

        await self.emit(
            f"Policies: {policies_created} created, {policies_updated} updated, {policies_failed} failed",
            "success" if policies_failed == 0 else "warning"
        )

        # Step 5: Rename identities (strip suffix)
        rename_results: List[IdentityRenameResult] = []
        identities_renamed = 0

        if identities_to_rename:
            await self.emit(f"Renaming {len(identities_to_rename)} identities to remove suffix")

            if not identity_group_id:
                logger.warning("No identity group ID available, skipping identity renames")
                await self.emit("Skipping identity renames: no group ID available", "warning")
            else:
                for identity in identities_to_rename:
                    identity_id = identity["identity_id"]
                    old_name = identity["old_name"]
                    new_name = identity["new_name"]

                    if not identity_id:
                        continue

                    try:
                        await self.r1_client.identity.update_identity(
                            group_id=identity_group_id,
                            identity_id=identity_id,
                            name=new_name,
                            tenant_id=self.tenant_id
                        )

                        rename_results.append(IdentityRenameResult(
                            identity_id=identity_id,
                            old_name=old_name,
                            new_name=new_name,
                            success=True
                        ))
                        identities_renamed += 1

                    except Exception as e:
                        logger.warning(f"Failed to rename identity {old_name}: {e}")
                        rename_results.append(IdentityRenameResult(
                            identity_id=identity_id,
                            old_name=old_name,
                            new_name=new_name,
                            success=False,
                            error=str(e)
                        ))

                await self.emit(f"Renamed {identities_renamed} identities")

        await self.emit(
            f"Access policies complete: {policies_created} policies, "
            f"{groups_created} new RADIUS groups, {identities_renamed} identities renamed",
            "success"
        )

        return self.Outputs(
            radius_groups_created=groups_created,
            radius_groups_existing=groups_existing,
            policies_created=policies_created,
            policies_failed=policies_failed,
            identities_renamed=identities_renamed,
            policy_set_id=policy_set_id,
            policy_results=policy_results,
            rename_results=rename_results,
        )

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
            unit_ssids = [s for s in ssid_list if UNIT_SSID_PATTERN.match(s)]
            policy_count += len(unit_ssids)

        return PhaseValidation(
            valid=True,
            will_create=policy_count > 0,
            estimated_api_calls=policy_count * 4,  # policy + 2 conditions + assign
            notes=[f"Estimated {policy_count} policies to create"],
        )
