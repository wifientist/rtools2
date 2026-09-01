"""
V2 Phase: Validate and Plan Cloudpath Import

Parses Cloudpath JSON export and builds execution plan based on ssid_mode option.

SSID MODES:

1. ssid_mode="none" (default)
   - Import passphrases only, no SSID creation
   - User configures SSIDs manually or uses existing ones

2. ssid_mode="single"
   - 1 shared DPSK pool → 1 property-wide SSID
   - All passphrases work on the single SSID

3. ssid_mode="per_unit"
   - 1 shared DPSK pool → N SSIDs (one per unit), up to 1024
   - Each unit gets its own SSID (e.g., "108@Property")
   - AP Groups created per-unit for targeted broadcast
   - APs assigned to groups, SSIDs configured per-group
   - Passphrases work on any unit's SSID (roaming enabled)
   - Plus the property-wide SSID from the export, riding on that same
     shared pool and activated venue-wide (create_property_ssid)

Every mode plans exactly ONE identity group and ONE DPSK pool. The 1:1
"per_unit_isolated" mode (a dedicated pool per unit, no roaming) has been
removed.

Detection logic (for auto-detecting import_mode A vs B):
1. Parse ssidList patterns (e.g., "108@Property" → unit 108)
2. Count unique units with unit-specific SSIDs
3. If >50% have unit-specific patterns → Scenario B, else → Scenario A

Output includes unit_mappings and all_venue_aps for the Brain's parallel execution.
"""

import logging
import re
from collections import defaultdict
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set

from r1api.constants import DpskPassphraseFormat, DpskScaleLimits
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.v2.models import (
    UnitMapping, UnitPlan, UnitResolved, UnitStatus, ValidationResult,
    ValidationSummary, ResourceAction,
)

logger = logging.getLogger(__name__)

# Pattern to detect unit-specific SSIDs like "108@Property_Name"
UNIT_SSID_PATTERN = re.compile(r'^(\d+)@(.+)$')


class CloudpathPoolConfig(BaseModel):
    """Configuration for creating a DPSK pool."""
    name: str
    description: str = ""
    phrase_length: int = 12
    phrase_type: str = "ALPHANUMERIC_MIXED"
    expiration_enabled: bool = False
    expiration_type: str = "MONTHS_AFTER_TIME"
    expiration_value: str = "24"
    device_limit_enabled: bool = False
    device_limit: int = 20

    # Passphrase generation overrides (from import options, not Cloudpath).
    # Cloudpath has no equivalent of DICTIONARY_WORDS, so when the user picks a
    # format here it wins over whatever phrase_type was imported.
    passphrase_format_override: Optional[str] = None
    word_count: int = DpskPassphraseFormat.DEFAULT_WORD_COUNT
    numeric_suffix_enabled: bool = False


class ParsedPassphrase(BaseModel):
    """Parsed passphrase from Cloudpath export."""
    guid: str
    name: str  # Username/identifier
    passphrase: str
    status: str
    ssid_list: List[str]
    unit_number: Optional[str] = None  # Extracted from SSID pattern
    device_count: int = 0
    expiration: Optional[str] = None
    vlan_id: Optional[int] = None  # Per-identity VLAN from Cloudpath
    ap_identifier: Optional[str] = None  # AP name/serial for unit assignment


class ScenarioDetection(BaseModel):
    """Result of analyzing ssidList patterns to detect import scenario."""
    detected_scenario: str  # "A" or "B"
    unit_count: int  # Number of unique units detected
    unit_coverage: float  # % of passphrases with unit-specific SSIDs
    unique_ssids: List[str]  # Sample of detected SSIDs
    recommendation: str  # Human-readable recommendation
    detected_ssid: Optional[str] = None  # For Scenario A: the common site-wide SSID


@register_phase("validate_and_plan", "Validate & Plan Cloudpath Import")
class ValidateCloudpathPhase(PhaseExecutor):
    """
    Parse Cloudpath JSON export and build execution plan.

    Detects import mode (property-wide vs per-unit) and creates
    appropriate unit_mappings for the Brain.
    """

    class Inputs(BaseModel):
        cloudpath_data: Dict[str, Any] = Field(
            ..., description="Raw Cloudpath JSON export"
        )
        options: Dict[str, Any] = Field(
            default_factory=dict, description="Import options"
        )

    class Outputs(BaseModel):
        import_mode: str  # "A" or "B" (always shared pool)
        scenario_detection: ScenarioDetection  # Detection analysis for UI
        pool_config: CloudpathPoolConfig
        identity_groups: List[Dict[str, Any]]
        dpsk_pools: List[Dict[str, Any]]
        passphrases: List[ParsedPassphrase]
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: ValidationResult
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)  # For assign_aps phase
        # Calculated activation slot limit based on existing venue-wide SSIDs
        max_activation_slots: int = 12
        venue_wide_ssid_count: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Parse and validate Cloudpath export data."""
        await self.emit("Parsing Cloudpath export...")

        data = inputs.cloudpath_data
        options = inputs.options

        # Extract pool configuration
        pool_data = data.get('pool', {})

        # Passphrase generation is an import-time choice, not a Cloudpath one.
        # Only accept a format R1 actually defines; anything else falls back to
        # the Cloudpath-derived mapping rather than being sent blindly.
        fmt_override = inputs.options.get('passphrase_format') or None
        if fmt_override and fmt_override not in DpskPassphraseFormat.ALL:
            await self.emit(
                f"Ignoring unknown passphrase format '{fmt_override}' "
                f"(expected one of {', '.join(DpskPassphraseFormat.ALL)})",
                "warning",
            )
            fmt_override = None

        pool_config = CloudpathPoolConfig(
            name=pool_data.get('displayName', 'Cloudpath Import'),
            description=pool_data.get('description', ''),
            phrase_length=pool_data.get('phraseDefaultLength', 12),
            phrase_type=pool_data.get('phraseRandomCharactersType', 'ALPHANUMERIC_MIXED'),
            expiration_enabled=pool_data.get('enforceExpirationDate', False),
            expiration_type=pool_data.get('expirationDateType', 'MONTHS_AFTER_TIME'),
            expiration_value=pool_data.get('expirationDateValue', '24'),
            device_limit_enabled=pool_data.get('enforceDeviceCountLimit', False),
            device_limit=pool_data.get('deviceCountLimit', 20),
            passphrase_format_override=fmt_override,
            word_count=int(inputs.options.get(
                'word_count', DpskPassphraseFormat.DEFAULT_WORD_COUNT
            )),
            numeric_suffix_enabled=bool(
                inputs.options.get('numeric_suffix_enabled', False)
            ),
        )

        if fmt_override == DpskPassphraseFormat.DICTIONARY_WORDS:
            await self.emit(
                f"Passphrases will be {pool_config.word_count} dictionary words"
                + (" with a numeric suffix" if pool_config.numeric_suffix_enabled else "")
            )
        elif fmt_override:
            await self.emit(f"Passphrase format override: {fmt_override}")

        # =====================================================================
        # Extract SSIDs from pool-level ssidList (master list)
        # This is the authoritative list - use these for creating networks/AP Groups
        # We'll also merge in any additional SSIDs from individual DPSK entries
        # =====================================================================
        pool_ssid_list: List[str] = pool_data.get('ssidList', [])
        master_unit_ssids: Dict[str, str] = {}  # unit_number -> ssid_name
        master_site_wide_ssid: Optional[str] = None

        for ssid in pool_ssid_list:
            match = UNIT_SSID_PATTERN.match(ssid)
            if match:
                unit_num = match.group(1)
                master_unit_ssids[unit_num] = ssid
            else:
                # Non-unit SSID is the site-wide one
                if not master_site_wide_ssid:
                    master_site_wide_ssid = ssid

        if pool_ssid_list:
            await self.emit(
                f"Pool master SSIDs: {len(pool_ssid_list)} total, "
                f"{len(master_unit_ssids)} unit-specific"
                f"{f', site-wide: {master_site_wide_ssid}' if master_site_wide_ssid else ''}"
            )

        # Parse passphrases (DPSKs)
        raw_dpsks = data.get('dpsks', [])
        passphrases: List[ParsedPassphrase] = []
        unit_ssid_counts: Dict[str, int] = defaultdict(int)
        min_passphrase_len = 999  # Track minimum passphrase length

        # Track all unique SSIDs seen in individual DPSKs (to merge with master list)
        dpsk_unit_ssids: Dict[str, str] = {}  # unit_number -> ssid_name (from DPSK entries)

        for dpsk in raw_dpsks:
            ssid_list = dpsk.get('ssidList', [])
            passphrase_str = dpsk.get('passphrase', '')

            # Track minimum passphrase length
            if passphrase_str:
                min_passphrase_len = min(min_passphrase_len, len(passphrase_str))

            # Try to extract unit number from SSID pattern
            # Also add any new unit SSIDs to dpsk_unit_ssids for merging
            unit_number = None
            for ssid in ssid_list:
                match = UNIT_SSID_PATTERN.match(ssid)
                if match:
                    unit_num = match.group(1)
                    if not unit_number:
                        unit_number = unit_num
                    unit_ssid_counts[unit_num] += 1
                    # Add to DPSK-level unit SSIDs if not already in master
                    if unit_num not in master_unit_ssids and unit_num not in dpsk_unit_ssids:
                        dpsk_unit_ssids[unit_num] = ssid

            # Extract VLAN ID (can be int or string in Cloudpath export)
            raw_vlan = dpsk.get('vlanid')
            vlan_id = None
            if raw_vlan is not None and raw_vlan != '' and raw_vlan != 0:
                try:
                    vlan_id = int(raw_vlan)
                except (ValueError, TypeError):
                    pass
            logger.debug(f"DPSK {dpsk.get('name')}: raw_vlan={raw_vlan}, parsed_vlan={vlan_id}")

            # Extract AP identifier (can be ap_identifier, apname, ap, ap_serial, etc.)
            ap_identifier = (
                dpsk.get('ap_identifier') or
                dpsk.get('apidentifier') or
                dpsk.get('ap_name') or
                dpsk.get('apname') or
                dpsk.get('ap_serial') or
                dpsk.get('apserial') or
                dpsk.get('ap') or
                None
            )
            if ap_identifier:
                ap_identifier = str(ap_identifier).strip()

            passphrases.append(ParsedPassphrase(
                guid=dpsk.get('guid', ''),
                name=dpsk.get('name', ''),
                passphrase=passphrase_str,
                status=dpsk.get('status', 'ACTIVE'),
                ssid_list=ssid_list,
                unit_number=unit_number,
                device_count=dpsk.get('deviceCount', 0),
                vlan_id=vlan_id,
                ap_identifier=ap_identifier,
            ))

        # Adjust pool_config.phrase_length to accommodate shortest passphrase
        # RuckusONE enforces passphraseLength as MINIMUM - shorter passphrases get rejected
        if min_passphrase_len < 999 and min_passphrase_len < pool_config.phrase_length:
            await self.emit(
                f"Adjusting DPSK pool min length from {pool_config.phrase_length} to "
                f"{min_passphrase_len} to accommodate shortest passphrase"
            )
            pool_config.phrase_length = min_passphrase_len

        # Warn if passphrases are very short (security concern)
        if min_passphrase_len < 8:
            await self.emit(
                f"Warning: Some passphrases are only {min_passphrase_len} characters",
                "warning"
            )

        # Detect suffix patterns for access policy phase
        suffix_patterns: Set[str] = set()
        users_with_suffix = 0
        users_without_suffix = 0

        for pp in passphrases:
            if "_" in pp.name:
                suffix = pp.name.rsplit("_", 1)[1]
                suffix_patterns.add(suffix)
                users_with_suffix += 1
            else:
                users_without_suffix += 1

        # Always include gigabit as the default for users without suffix
        if users_without_suffix > 0:
            suffix_patterns.add("gigabit")

        if suffix_patterns:
            await self.emit(
                f"Access policy suffixes: {suffix_patterns} "
                f"({users_with_suffix} explicit, {users_without_suffix} defaulting to gigabit)"
            )

        # Report VLAN detection
        vlans_detected = [pp.vlan_id for pp in passphrases if pp.vlan_id is not None]
        if vlans_detected:
            unique_vlans = sorted(set(vlans_detected))
            await self.emit(
                f"Per-identity VLANs detected: {len(vlans_detected)} passphrases with "
                f"{len(unique_vlans)} unique VLANs ({min(unique_vlans)}-{max(unique_vlans)})"
            )

        # Report AP identifier detection
        aps_detected = [pp for pp in passphrases if pp.ap_identifier]
        if aps_detected:
            unique_aps = sorted(set(pp.ap_identifier for pp in aps_detected))
            sample_aps = unique_aps[:5]
            await self.emit(
                f"AP identifiers detected: {len(aps_detected)} passphrases with "
                f"{len(unique_aps)} unique APs (sample: {', '.join(sample_aps)}"
                f"{'...' if len(unique_aps) > 5 else ''})"
            )

        await self.emit(f"Parsed {len(passphrases)} passphrases")

        # =====================================================================
        # Merge unit SSIDs from pool master list + individual DPSKs
        # The master list is authoritative, DPSK entries add any extras
        # =====================================================================
        all_unit_ssids: Dict[str, str] = {**master_unit_ssids}  # Start with master
        for unit_num, ssid in dpsk_unit_ssids.items():
            if unit_num not in all_unit_ssids:
                all_unit_ssids[unit_num] = ssid

        if dpsk_unit_ssids:
            new_from_dpsks = set(dpsk_unit_ssids.keys()) - set(master_unit_ssids.keys())
            if new_from_dpsks:
                await self.emit(
                    f"Additional units from DPSK entries: {sorted(new_from_dpsks)}"
                )

        await self.emit(
            f"Total unit SSIDs: {len(all_unit_ssids)} "
            f"(master: {len(master_unit_ssids)}, from DPSKs: {len(dpsk_unit_ssids)})"
        )

        # Analyze ssidList patterns for scenario detection
        # Include units from both master list AND individual DPSKs
        unique_units: Set[str] = set(all_unit_ssids.keys())
        # Also add any units detected from individual DPSK parsing
        for p in passphrases:
            if p.unit_number:
                unique_units.add(p.unit_number)

        unit_count = len(unique_units)
        total_passphrases = len(passphrases)
        unit_passphrases = sum(1 for p in passphrases if p.unit_number)
        unit_coverage = unit_passphrases / total_passphrases if total_passphrases > 0 else 0

        # Collect unique SSIDs for display (sample up to 10)
        # Include both pool-level and DPSK-level SSIDs
        all_ssids: Set[str] = set(pool_ssid_list)  # Start with master list
        for p in passphrases:
            all_ssids.update(p.ssid_list)
        unique_ssid_sample = sorted(list(all_ssids))[:10]

        # Detect site-wide SSID - prefer master list, then detect from DPSKs
        # Site-wide SSID is a non-unit pattern SSID (e.g., "Property WiFi")
        site_wide_ssid = master_site_wide_ssid  # Prefer pool-level if set

        if not site_wide_ssid and passphrases:
            # Count how often each SSID appears across all passphrases
            ssid_counts: Dict[str, int] = defaultdict(int)
            for p in passphrases:
                for ssid in p.ssid_list:
                    ssid_counts[ssid] += 1

            # Find the SSID that appears most often AND doesn't match unit pattern
            total = len(passphrases)
            for ssid, count in sorted(ssid_counts.items(), key=lambda x: -x[1]):
                # Site-wide SSID should appear in most DPSKs and NOT match unit pattern
                if count >= total * 0.5 and not UNIT_SSID_PATTERN.match(ssid):
                    site_wide_ssid = ssid
                    await self.emit(
                        f"Detected site-wide SSID: '{ssid}' (appears in {count}/{total} DPSKs)"
                    )
                    break

            # Fallback: if no common non-unit SSID, try first non-unit SSID from any passphrase
            if not site_wide_ssid:
                for p in passphrases:
                    for ssid in p.ssid_list:
                        if not UNIT_SSID_PATTERN.match(ssid):
                            site_wide_ssid = ssid
                            await self.emit(
                                f"Using fallback site-wide SSID: '{ssid}'"
                            )
                            break
                    if site_wide_ssid:
                        break
        elif site_wide_ssid:
            await self.emit(f"Using pool-level site-wide SSID: '{site_wide_ssid}'")

        # Detect scenario based on unit patterns
        # Scenario A: Property-wide (single SSID)
        # Scenario B: Per-unit (multiple SSIDs, shared pool)
        if unit_coverage < 0.5:
            detected_scenario = "A"
            recommendation = (
                f"Property-wide: {total_passphrases} passphrases into 1 pool. "
                f"Only {unit_coverage:.0%} have unit-specific SSIDs."
            )
        else:
            detected_scenario = "B"
            recommendation = (
                f"Per-unit with shared pool: {unit_count} units detected. "
                f"All SSIDs share the same DPSK pool (roaming enabled)."
            )

        scenario_detection = ScenarioDetection(
            detected_scenario=detected_scenario,
            unit_count=unit_count,
            unit_coverage=unit_coverage,
            unique_ssids=unique_ssid_sample,
            recommendation=recommendation,
            detected_ssid=site_wide_ssid,  # For Scenario A default
        )

        await self.emit(
            f"Scenario detection: {detected_scenario} - {unit_count} units, "
            f"{unit_coverage:.0%} coverage"
        )

        # Check SSID creation mode: 'none', 'single', 'per_unit'
        #   per_unit → 1 shared pool → N SSIDs (roaming across the property)
        ssid_mode = options.get('ssid_mode', 'none')
        passphrases_only = ssid_mode == 'none'
        create_networks = ssid_mode in ('single', 'per_unit')
        per_unit_ssid = ssid_mode == 'per_unit'
        # Hybrid: alongside the per-unit SSIDs, also stand up the property-wide
        # SSID the export carries. Most properties have one, and without it any
        # resident who is only on the property network is dropped. No-ops when
        # the export has no site-wide SSID.
        create_property_ssid = options.get('create_property_ssid', True)

        # Determine import_mode (Scenario A or B)
        # User's ssid_mode selection takes priority over auto-detection:
        #   ssid_mode="single" → Scenario A (property-wide)
        #   ssid_mode="per_unit" → Scenario B (per-unit)
        #   ssid_mode="none" → use auto-detection or explicit override
        forced_scenario = options.get('import_scenario')
        if ssid_mode == 'single':
            import_mode = "A"
            await self.emit(f"Using Scenario A (property-wide) per ssid_mode selection")
        elif per_unit_ssid:
            import_mode = "B"
            await self.emit(
                "Using Scenario B (per-unit, 1 shared pool, roaming) "
                "per ssid_mode selection"
            )
        elif forced_scenario and forced_scenario in ("A", "B"):
            import_mode = forced_scenario
            await self.emit(f"Using user-selected scenario: {import_mode}")
        else:
            import_mode = detected_scenario
            await self.emit(f"Using auto-detected scenario: {import_mode}")

        # Build identity groups and DPSK pools lists based on scenario
        identity_groups: List[Dict[str, Any]] = []
        dpsk_pools: List[Dict[str, Any]] = []
        unit_mappings: Dict[str, UnitMapping] = {}
        actions: List[ResourceAction] = []
        networks_to_create = 0  # Track networks to create for summary
        actual_unit_count = unit_count  # From scenario detection (unique units with unit SSIDs)

        # AP Group settings (only used when ssid_mode='per_unit')
        ap_group_prefix = options.get('ap_group_prefix', '')
        ap_group_postfix = options.get('ap_group_postfix', '')
        ap_assignment_mode = options.get('ap_assignment_mode', 'skip')  # 'skip' | 'csv'
        configure_lan_ports = options.get('configure_lan_ports', False)

        # Determine resource names upfront for both scenarios
        ig_name = options.get('identity_group_name') or f"{pool_config.name}-IDG"
        pool_name = options.get('dpsk_service_name') or f"{pool_config.name}-DPSK"

        # =====================================================================
        # Check existing resources in R1
        # =====================================================================
        await self.emit("Checking existing resources in R1...")

        # Track existing resources
        ig_exists = False
        ig_id: Optional[str] = None
        pool_exists = False
        pool_id: Optional[str] = None

        # --- Check Identity Group ---
        try:
            existing_igs = await self.r1_client.identity.query_identity_groups(
                tenant_id=self.tenant_id
            )
            ig_items = existing_igs.get('content', existing_igs.get('data', []))
            ig_match = next(
                (ig for ig in ig_items if ig.get('name') == ig_name),
                None
            )
            if ig_match:
                ig_exists = True
                ig_id = ig_match.get('id')
                await self.emit(f"Found existing Identity Group: {ig_name}")
        except Exception as e:
            logger.warning(f"Error checking identity group: {e}")

        # --- Check DPSK Pool ---
        try:
            existing_pools = await self.r1_client.dpsk.query_dpsk_pools(
                tenant_id=self.tenant_id
            )
            pool_items = (
                existing_pools
                if isinstance(existing_pools, list)
                else existing_pools.get('content', existing_pools.get('data', []))
            )
            pool_match = next(
                (p for p in pool_items if p.get('name') == pool_name),
                None
            )
            if pool_match:
                pool_exists = True
                pool_id = pool_match.get('id')
                await self.emit(f"Found existing DPSK Pool: {pool_name}")
        except Exception as e:
            logger.warning(f"Error checking DPSK pool: {e}")

        # --- Check existing passphrases by actual passphrase value ---
        # Store passphrase_value -> {id, vlan_id} for update detection, so a
        # re-run reuses what is already imported instead of re-creating it.
        existing_passphrases: Dict[str, Dict[str, Any]] = {}

        if pool_id:
            try:
                existing_passphrases = await self._fetch_pool_passphrases(pool_id)
            except Exception as e:
                logger.warning(
                    f"Error checking existing passphrases in '{pool_name}': {e}"
                )

        if existing_passphrases:
            await self.emit(
                f"Found {len(existing_passphrases)} existing passphrases "
                f"in '{pool_name}'"
            )

        # --- Fetch existing identities for GUID description check (idempotent re-runs) ---
        # Build username -> {identity_id, description} map so Phase 4 can skip
        # identities that already have the correct GUID in their description field.
        existing_identities: Dict[str, Dict[str, Any]] = {}  # username -> {id, description}
        # cloudpath guid -> identity. Stable across renames; see the scan below.
        existing_identities_by_guid: Dict[str, Dict[str, Any]] = {}
        # guid -> [names] for residents represented by MORE THAN ONE identity
        # in R1. Distinct from a collision inside the export file.
        duplicate_identities: Dict[str, List[str]] = {}
        # Scan whenever the group exists -- NOT only when passphrases do.
        # The case that mints duplicate identities is precisely the one where
        # the passphrases were deleted and the identities were left behind
        # (a partial cleanup). Gating on existing_passphrases skipped the scan
        # exactly when it was needed most.
        if ig_id:
            try:
                await self.emit("Fetching existing identities...")
                id_page = 0
                id_page_size = 100

                while True:
                    id_result = await self.r1_client.identity.get_identities_in_group(
                        group_id=ig_id,
                        tenant_id=self.tenant_id,
                        page=id_page,
                        size=id_page_size,
                    )
                    id_items = id_result.get('content', id_result.get('data', []))

                    if not id_items:
                        break

                    for identity in id_items:
                        uname = identity.get('name', '')
                        desc = identity.get('description', '') or ''
                        record = {
                            'id': identity.get('id'),
                            'description': desc,
                            'name': uname,
                        }
                        if uname:
                            existing_identities[uname] = record
                        # The Cloudpath GUID is written into the description by
                        # update_identity_descriptions, and unlike the name it
                        # never changes. Once a run strips the _tier suffix the
                        # identity no longer matches its Cloudpath username, so
                        # a name-only lookup misses it and the next import mints
                        # a SECOND identity for the same resident -- whose
                        # rename then collides with the first.
                        if desc:
                            prior = existing_identities_by_guid.get(desc)
                            if prior:
                                # Two identities in R1 carry the SAME Cloudpath
                                # GUID: this resident was duplicated by an
                                # earlier run. Record it, and deterministically
                                # keep the copy whose name has already had its
                                # suffix stripped -- renaming the suffixed copy
                                # onto that name would collide (GENERAL-010).
                                duplicate_identities.setdefault(
                                    desc, [prior.get('name', '')]
                                ).append(uname)
                                prior_suffixed = '_' in (prior.get('name') or '')
                                if prior_suffixed and '_' not in uname:
                                    existing_identities_by_guid[desc] = record
                            else:
                                existing_identities_by_guid[desc] = record

                    if len(id_items) < id_page_size:
                        break

                    id_page += 1

                    if id_page > 5000:
                        logger.warning("Identity pagination safety limit reached")
                        break

                if existing_identities:
                    await self.emit(
                        f"Found {len(existing_identities)} existing identities "
                        f"in group ({len(existing_identities_by_guid)} carry a "
                        f"Cloudpath GUID and can be matched after a rename)"
                    )
            except Exception as e:
                logger.warning(f"Error fetching existing identities: {e}")

        # Count new vs existing passphrases by comparing passphrase values
        # Also build a list of passphrase dicts with 'exists' flag for create_passphrases phase
        # Track VLAN mismatches for updates
        passphrases_existing = 0
        passphrases_to_create_count = 0
        passphrases_to_update_count = 0
        description_updates_needed = 0
        passphrases_with_exists: List[Dict[str, Any]] = []

        identities_matched_by_guid = 0

        for pp in passphrases:
            pp_dict = pp.model_dump()

            # ---------------------------------------------------------------
            # Resolve this resident's identity BEFORE deciding anything else,
            # and for every passphrase rather than only ones already imported.
            #
            # GUID first: it is stamped into the description and survives the
            # suffix rename, so it still matches after a previous run turned
            # "acct_gigabit" into "acct". Name second, for identities created
            # before their description was written (e.g. a run that died
            # before update_identity_descriptions).
            #
            # This must run for NEW passphrases too. Previously it only ran
            # for ones that already existed, so a renamed identity whose
            # passphrase had been deleted was invisible -- the import created
            # a second identity for that resident and every rename afterwards
            # collided with the first.
            # ---------------------------------------------------------------
            identity_info = None
            if pp.guid:
                identity_info = existing_identities_by_guid.get(pp.guid)
                if identity_info:
                    identities_matched_by_guid += 1
            if not identity_info:
                identity_info = existing_identities.get(pp.name)

            if identity_info:
                pp_dict['existing_identity_id'] = identity_info['id']
                pp_dict['existing_description'] = identity_info['description']
                pp_dict['needs_description_update'] = (
                    identity_info['description'] != pp.guid
                )
                if pp_dict['needs_description_update']:
                    description_updates_needed += 1
            else:
                pp_dict['existing_identity_id'] = None
                pp_dict['existing_description'] = None
                pp_dict['needs_description_update'] = False

            if pp.passphrase in existing_passphrases:
                existing_info = existing_passphrases[pp.passphrase]
                pp_dict['exists'] = True
                pp_dict['existing_id'] = existing_info['id']
                pp_dict['existing_vlan_id'] = existing_info['vlan_id']

                # Check if VLAN needs update
                if pp.vlan_id != existing_info['vlan_id']:
                    pp_dict['needs_vlan_update'] = True
                    passphrases_to_update_count += 1
                    logger.debug(
                        f"VLAN mismatch for {pp.name}: cloudpath={pp.vlan_id}, "
                        f"r1={existing_info['vlan_id']}"
                    )
                else:
                    pp_dict['needs_vlan_update'] = False

                passphrases_existing += 1
            else:
                pp_dict['exists'] = False
                pp_dict['needs_vlan_update'] = False
                pp_dict['needs_description_update'] = False
                passphrases_to_create_count += 1
            passphrases_with_exists.append(pp_dict)

        if duplicate_identities:
            sample = "; ".join(
                " / ".join(names) for names in
                list(duplicate_identities.values())[:3]
            )
            await self.emit(
                f"IN RUCKUSONE: {len(duplicate_identities)} resident(s) have "
                f"MORE THAN ONE identity in the group (same Cloudpath GUID) "
                f"— e.g. {sample}. The import will reuse the already-renamed "
                f"copy and leave the extra one untouched; the duplicates need "
                f"removing by hand.",
                "warning",
            )

        if identities_matched_by_guid:
            await self.emit(
                f"{identities_matched_by_guid} identities matched by Cloudpath "
                f"GUID rather than username — they were renamed by a previous "
                f"run and will be reused, not duplicated"
            )

        if description_updates_needed > 0:
            await self.emit(
                f"Identity description updates needed: {description_updates_needed} passphrases"
            )

        if passphrases_to_update_count > 0:
            await self.emit(
                f"VLAN updates needed: {passphrases_to_update_count} passphrases"
            )

        # =====================================================================
        # Fetch Venue APs (needed for per_unit SSID mode with AP assignment)
        # Fetch if: CSV assignments provided OR any DPSKs have ap_identifier
        # =====================================================================
        all_venue_aps: List[Dict[str, Any]] = []
        has_dpsk_ap_assignments = any(pp.ap_identifier for pp in passphrases)
        should_fetch_aps = per_unit_ssid and (
            ap_assignment_mode != 'skip' or has_dpsk_ap_assignments
        )

        if should_fetch_aps:
            await self.emit("Fetching venue APs for assignment...")
            try:
                aps_response = await self.r1_client.venues.get_aps_by_tenant_venue(
                    self.tenant_id, self.venue_id
                )
                all_venue_aps = aps_response.get('data', [])
                await self.emit(f"Found {len(all_venue_aps)} APs in venue")
            except Exception as e:
                logger.warning(f"Failed to fetch venue APs: {e}")
                await self.emit(f"Warning: Could not fetch venue APs: {e}", "warning")

        # =====================================================================
        # Check existing AP Groups (only for per_unit SSID mode)
        # =====================================================================
        existing_ap_groups: Dict[str, str] = {}  # name -> id
        if per_unit_ssid:
            await self.emit("Checking existing AP Groups...")
            try:
                ap_groups_response = await self.r1_client.venues.query_ap_groups(
                    tenant_id=self.tenant_id,
                    venue_id=self.venue_id,
                    fields=['id', 'name', 'venueId'],
                )
                for ap_group in ap_groups_response.get('data', []):
                    existing_ap_groups[ap_group.get('name', '')] = ap_group.get('id', '')
                await self.emit(f"Found {len(existing_ap_groups)} existing AP Groups")
            except Exception as e:
                logger.warning(f"Error checking AP groups: {e}")

        # =====================================================================
        # Count venue-wide SSIDs.
        #
        # R1 limits 15 SSIDs per AP Group. Per-unit imports no longer pass
        # through an "All AP Groups" state — activation binds straight to the
        # unit's AP Group — so this no longer throttles our own activations.
        # It still matters as a headroom check: SSIDs already broadcasting
        # venue-wide consume a slot on EVERY AP Group, including the ones we
        # are about to create, so a venue near the cap will reject them.
        # =====================================================================
        SSID_LIMIT_PER_AP_GROUP = 15
        SSID_SAFETY_BUFFER = 3
        venue_wide_ssid_count = 0
        calculated_max_activation_slots = SSID_LIMIT_PER_AP_GROUP - SSID_SAFETY_BUFFER

        if per_unit_ssid:
            await self.emit("Counting existing venue-wide SSIDs...")
            try:
                networks_response = await self.r1_client.networks.get_wifi_networks(
                    self.tenant_id
                )
                all_networks = networks_response.get('data', []) if isinstance(networks_response, dict) else networks_response

                for network in all_networks:
                    venue_ap_groups = network.get('venueApGroups', [])
                    for vag in venue_ap_groups:
                        if vag.get('venueId') != self.venue_id:
                            continue
                        # Check if this SSID broadcasts to all AP Groups
                        if vag.get('isAllApGroups', False):
                            venue_wide_ssid_count += 1
                            break  # Only count once per network

                # Calculate safe activation slot limit
                calculated_max_activation_slots = max(
                    1,  # Minimum of 1 for sequential processing
                    SSID_LIMIT_PER_AP_GROUP - venue_wide_ssid_count - SSID_SAFETY_BUFFER
                )

                if venue_wide_ssid_count > 0:
                    await self.emit(
                        f"Found {venue_wide_ssid_count} venue-wide SSIDs consuming "
                        f"a slot on every AP Group — {calculated_max_activation_slots} "
                        f"of {SSID_LIMIT_PER_AP_GROUP} remain for per-unit SSIDs",
                        "warning" if calculated_max_activation_slots <= 1 else "info"
                    )
                else:
                    await self.emit(
                        f"No venue-wide SSIDs found — full per-AP-Group headroom available"
                    )

                # Store in options for Brain to use
                options['max_activation_slots'] = calculated_max_activation_slots
                options['venue_wide_ssid_count'] = venue_wide_ssid_count

            except Exception as e:
                logger.warning(f"Error counting venue SSIDs: {e}")
                await self.emit(
                    f"Warning: Could not count venue SSIDs, using default limit",
                    "warning"
                )

        # --- Check access policy resources if enabled ---
        enable_access_policies = options.get('enable_access_policies', False)
        policy_set_name = options.get('policy_set_name') or None
        policies_to_create = 0
        policies_existing = 0
        policies_skipped_no_unit_ssid = 0
        colliding_accounts: List[str] = []
        radius_groups_to_create = 0
        radius_groups_existing = 0
        existing_radius_groups: Set[str] = set()
        existing_policy_names: Set[str] = set()

        if enable_access_policies:
            # Check existing RADIUS attribute groups
            try:
                all_radius_groups = await self.r1_client.radius_attributes.get_radius_attribute_groups(
                    tenant_id=self.tenant_id
                )
                if isinstance(all_radius_groups, dict):
                    all_radius_groups = all_radius_groups.get('content', all_radius_groups.get('data', []))
                for group in all_radius_groups:
                    existing_radius_groups.add(group.get('name', '').lower())
            except Exception as e:
                logger.warning(f"Error checking RADIUS groups: {e}")

            # Count new vs existing RADIUS groups
            for suffix in suffix_patterns:
                if suffix.lower() in existing_radius_groups:
                    radius_groups_existing += 1
                else:
                    radius_groups_to_create += 1

            # Check existing policies in the policy set
            if policy_set_name:
                try:
                    sets_response = await self.r1_client.policy_sets.query_policy_sets(
                        tenant_id=self.tenant_id,
                        search_string=policy_set_name,
                        limit=100
                    )
                    sets = sets_response.get('content', sets_response.get('data', []))
                    policy_set_match = next(
                        (s for s in sets if s.get('name') == policy_set_name),
                        None
                    )
                    if policy_set_match:
                        # Get policies in this set
                        set_id = policy_set_match.get('id')
                        policies_response = await self.r1_client.policy_sets.get_policies_in_set(
                            template_id="100",  # DPSK template
                            policy_set_id=set_id,
                            tenant_id=self.tenant_id
                        )
                        for policy in policies_response.get('content', policies_response.get('data', [])):
                            existing_policy_names.add(policy.get('name', ''))
                except Exception as e:
                    logger.warning(f"Error checking policy set: {e}")

            # Count policies to create (one per passphrase with unit SSID).
            # Must use the same name builder as create_access_policies —
            # these had drifted, so the plan counted every policy as new and
            # the run then hit 409s on the ones that already existed.
            from workflow.phases.create_access_policies import sanitize_policy_name

            # Count by DISTINCT policy name. R1 rejects a duplicate name with
            # 409, so two passphrases collapsing to one name yield ONE policy,
            # not two — counting per passphrase overstated the plan and hid
            # the collision until the run hit the conflict.
            planned_policy_names: Dict[str, List[str]] = defaultdict(list)
            for pp in passphrases:
                unit_ssids = [
                    s for s in pp.ssid_list if UNIT_SSID_PATTERN.match(s)
                ]
                if not unit_ssids:
                    # Property-wide only — nothing to scope a policy to.
                    policies_skipped_no_unit_ssid += 1
                    continue
                account = pp.name.rsplit("_", 1)[0] if "_" in pp.name else pp.name
                planned_policy_names[sanitize_policy_name(account)].append(pp.name)

            for policy_name, sources in planned_policy_names.items():
                if policy_name in existing_policy_names:
                    policies_existing += 1
                else:
                    policies_to_create += 1
                if len(sources) > 1:
                    colliding_accounts.append(policy_name)

            if policies_skipped_no_unit_ssid:
                await self.emit(
                    f"{policies_skipped_no_unit_ssid} residents are on the "
                    f"property-wide SSID only and will get no adaptive policy "
                    f"(their passphrases still import normally)",
                    "warning",
                )
            if colliding_accounts:
                sample = ", ".join(sorted(colliding_accounts)[:5])
                await self.emit(
                    f"IN THE EXPORT FILE: {len(colliding_accounts)} account(s) "
                    f"appear more than once with different tier suffixes ({sample}"
                    f"{'...' if len(colliding_accounts) > 5 else ''}). Each "
                    f"collapses to ONE identity and ONE policy, so the extra "
                    f"copies keep their suffix and get no policy of their own.",
                    "warning",
                )

        await self.emit(
            f"Resource check: IDG={'exists' if ig_exists else 'new'}, "
            f"Pool={'exists' if pool_exists else 'new'}, "
            f"Passphrases={passphrases_to_create_count} new/{passphrases_existing} existing"
        )

        # Every mode imports the whole export into the one shared pool, so
        # what the plan will do matches what the export contains.
        planned_passphrase_creates = passphrases_to_create_count
        planned_passphrase_existing = passphrases_existing
        property_ssid_planned = False

        if import_mode == "A":
            # SCENARIO A: Property-wide - single pool, single network
            # SSID: Allow user override, fall back to detected site-wide SSID, then pool name
            ssid_name = options.get('ssid_name') or site_wide_ssid or pool_config.name
            network_name = ssid_name

            # Log if SSID was detected from input vs defaulted
            if site_wide_ssid and not options.get('ssid_name'):
                await self.emit(f"Using detected SSID from input: '{ssid_name}'")

            identity_groups.append({'name': ig_name, 'exists': ig_exists, 'id': ig_id})
            dpsk_pools.append({
                'name': pool_name,
                'identity_group_name': ig_name,
                'exists': pool_exists,
                'id': pool_id,
            })

            # Create single "global" unit for Brain compatibility
            unit_mappings["global"] = UnitMapping(
                unit_id="global",
                unit_number="all",
                plan=UnitPlan(
                    identity_group_name=ig_name,
                    dpsk_pool_name=pool_name,
                    will_create_identity_group=not ig_exists,
                    will_create_dpsk_pool=not pool_exists,
                    identity_group_exists=ig_exists,
                    dpsk_pool_exists=pool_exists,
                    will_create_network=create_networks,
                    network_name=network_name if create_networks else None,
                    ssid_name=ssid_name if create_networks else None,
                ),
                resolved=UnitResolved(
                    identity_group_id=ig_id,
                    dpsk_pool_id=pool_id,
                ),
                status=UnitStatus.PENDING,
                input_config={
                    'scenario': 'A',
                    'passphrases': passphrases_with_exists,
                    'passphrase_count': len(passphrases),
                    'passphrases_only': passphrases_only,
                    'ssid_mode': ssid_mode,
                    'network_name': network_name,
                    'ssid_name': ssid_name,
                    # Access policy info
                    'suffix_patterns': list(suffix_patterns),
                    'users_with_suffix': users_with_suffix,
                    'users_without_suffix': users_without_suffix,
                },
            )

            # Identity Group action
            actions.append(ResourceAction(
                action="reuse" if ig_exists else "create",
                resource_type="identity_group",
                name=ig_name,
                existing_id=ig_id,
            ))
            # DPSK Pool action
            actions.append(ResourceAction(
                action="reuse" if pool_exists else "create",
                resource_type="dpsk_pool",
                name=pool_name,
                existing_id=pool_id,
            ))
            # Passphrases action
            if passphrases_to_create_count > 0:
                notes = []
                if passphrases_existing > 0:
                    notes.append(f"{passphrases_existing} already exist")
                actions.append(ResourceAction(
                    action="create",
                    resource_type="passphrases",
                    name=f"{passphrases_to_create_count} passphrases",
                    notes=notes,
                ))
            elif passphrases_existing > 0:
                actions.append(ResourceAction(
                    action="reuse",
                    resource_type="passphrases",
                    name=f"{passphrases_existing} passphrases",
                    notes=["All already imported"],
                ))

            if create_networks:
                networks_to_create = 1
                actions.append(ResourceAction(
                    action="create",
                    resource_type="wifi_network",
                    name=network_name,
                    notes=[f"SSID: {ssid_name}"],
                ))

            await self.emit(f"Scenario A: 1 pool, {passphrases_to_create_count} new / {passphrases_existing} existing passphrases")

        elif import_mode == "B":
            # SCENARIO B: Per-unit SSIDs on one shared pool.
            # A passphrase opens ANY unit's SSID, so residents roam across
            # the property. One identity group, one pool, N SSIDs.
            identity_groups.append(
                {'name': ig_name, 'exists': ig_exists, 'id': ig_id}
            )

            # Group passphrases by unit for tracking
            by_unit: Dict[str, List[ParsedPassphrase]] = defaultdict(list)
            for pp in passphrases:
                unit_num = pp.unit_number or "shared"
                by_unit[unit_num].append(pp)

            # Passphrases with no detectable unit number still import fine:
            # the shared pool holds them and they open every unit SSID.
            orphan_count = sum(
                1 for pp_dict in passphrases_with_exists
                if not pp_dict.get('unit_number')
            )
            if orphan_count:
                await self.emit(
                    f"{orphan_count} passphrases have no unit number; the "
                    f"shared pool holds them and they work on every SSID"
                )

            # The authoritative unit set: master SSID list merged with the units
            # seen on DPSK records, so a unit listed in the pool's master list
            # still gets planned even if no passphrase names it. "shared" is the
            # orphan bucket, not a unit — it never gets an SSID or a pool.
            all_units_to_process = set(all_unit_ssids.keys()) | set(by_unit.keys())
            all_units_to_process.discard("shared")

            dpsk_pools.append({
                'name': pool_name,
                'identity_group_name': ig_name,
                'shared_across_units': True,
                'exists': pool_exists,
                'id': pool_id,
            })

            # Track AP Groups for per_unit mode
            ap_groups_to_create = 0
            ap_groups_to_reuse = 0

            # =====================================================================
            # Process AP assignments from CSV input (flat list format)
            # Format: [{unit_number: "108", ap_identifier: "ABC123"}, ...]
            # ap_identifier can be serial number or AP name
            # =====================================================================
            unit_to_aps: Dict[str, List[str]] = defaultdict(list)  # unit_num -> [serial_numbers]
            ap_assignments = options.get('ap_assignments', [])

            if ap_assignments and all_venue_aps:
                # Build lookup tables for venue APs
                serial_to_ap = {ap.get('serial', ''): ap for ap in all_venue_aps}
                name_to_ap = {ap.get('name', ''): ap for ap in all_venue_aps}

                matched_count = 0
                unmatched = []

                for assignment in ap_assignments:
                    unit_num = str(assignment.get('unit_number', ''))
                    ap_id = str(assignment.get('ap_identifier', '')).strip()

                    if not unit_num or not ap_id:
                        continue

                    # Try to match by serial first, then by name
                    matched_ap = serial_to_ap.get(ap_id) or name_to_ap.get(ap_id)

                    if matched_ap:
                        serial = matched_ap.get('serial', '')
                        if serial and serial not in unit_to_aps[unit_num]:
                            unit_to_aps[unit_num].append(serial)
                            matched_count += 1
                    else:
                        unmatched.append(f"{unit_num}:{ap_id}")

                if matched_count > 0:
                    await self.emit(
                        f"AP assignments: {matched_count} APs matched to "
                        f"{len(unit_to_aps)} units"
                    )
                if unmatched:
                    await self.emit(
                        f"Warning: {len(unmatched)} AP assignments not matched: "
                        f"{', '.join(unmatched[:5])}{'...' if len(unmatched) > 5 else ''}",
                        "warning"
                    )

            # Also extract AP assignments from DPSK records that have ap_identifier
            # This allows embedding AP info directly in the Cloudpath export JSON
            if all_venue_aps:
                serial_to_ap = {ap.get('serial', ''): ap for ap in all_venue_aps}
                name_to_ap = {ap.get('name', ''): ap for ap in all_venue_aps}

                dpsk_ap_count = 0
                for pp in passphrases:
                    if pp.ap_identifier and pp.unit_number:
                        ap_id = pp.ap_identifier
                        # Try to match by serial first, then by name
                        matched_ap = serial_to_ap.get(ap_id) or name_to_ap.get(ap_id)
                        if matched_ap:
                            serial = matched_ap.get('serial', '')
                            if serial and serial not in unit_to_aps[pp.unit_number]:
                                unit_to_aps[pp.unit_number].append(serial)
                                dpsk_ap_count += 1

                if dpsk_ap_count > 0:
                    await self.emit(
                        f"AP assignments from DPSK records: {dpsk_ap_count} APs matched to "
                        f"{len(unit_to_aps)} units"
                    )

            if create_networks:
                # Per-unit with networks: one unit mapping per unit.
                #
                # The shared identity group and pool are created up front by
                # the global create_shared_resources phase; the first unit
                # then imports every passphrase into that one pool.
                #
                # all_units_to_process was built above from the merged master
                # SSID list + DPSK-derived units, so both the pool plan and
                # these mappings cover exactly the same units.
                is_first_unit = True

                # ---------------------------------------------------------
                # Enforce the platform ceilings before planning anything.
                # ---------------------------------------------------------
                unit_total = len(all_units_to_process)
                ceiling = DpskScaleLimits.SHARED_POOL_MAX_SSIDS
                if unit_total > ceiling:
                    raise ValueError(
                        f"{unit_total} units exceeds RuckusONE's limit of "
                        f"{ceiling} SSIDs on a shared DPSK pool. "
                        f"Split this property across multiple venues."
                    )

                for unit_num in sorted(all_units_to_process):
                    unit_id = f"unit_{unit_num}"
                    unit_pps = by_unit.get(unit_num, [])

                    # Get SSID from merged unit list (authoritative), fallback to DPSK or generated
                    ssid_name = all_unit_ssids.get(unit_num)
                    if not ssid_name:
                        # Try to find from passphrase's ssidList
                        sample_pp = unit_pps[0] if unit_pps else None
                        if sample_pp and sample_pp.ssid_list:
                            for ssid in sample_pp.ssid_list:
                                if UNIT_SSID_PATTERN.match(ssid):
                                    ssid_name = ssid
                                    break
                            if not ssid_name:
                                for ssid in sample_pp.ssid_list:
                                    if ssid != site_wide_ssid:
                                        ssid_name = ssid
                                        break
                    if not ssid_name:
                        ssid_name = f"{unit_num}@{pool_config.name}"
                    network_name = ssid_name

                    # Build AP Group name for per_unit SSID mode
                    ap_group_name = None
                    ap_group_id = None
                    ap_group_exists = False
                    will_create_ap_group = False

                    if per_unit_ssid:
                        if ap_group_prefix or ap_group_postfix:
                            # Explicit naming supplied by the user
                            ap_group_name = (
                                f"{ap_group_prefix}{unit_num}{ap_group_postfix}"
                            )
                        else:
                            # Default to the unit's SSID name (e.g. "101@CedarPoint")
                            # rather than a bare unit number, so the AP Group and
                            # the SSID it carries line up in the R1 UI.
                            ap_group_name = ssid_name
                        if ap_group_name in existing_ap_groups:
                            ap_group_exists = True
                            ap_group_id = existing_ap_groups[ap_group_name]
                            ap_groups_to_reuse += 1
                        else:
                            will_create_ap_group = True
                            ap_groups_to_create += 1

                    # Get AP serial numbers from CSV assignments (processed above)
                    ap_serial_numbers: List[str] = unit_to_aps.get(unit_num, [])

                    # -------------------------------------------------------
                    # Pool topology + passphrase routing.
                    #
                    # Isolated: this unit owns its pool and imports only its
                    # own passphrases. Shared: every unit points at the one
                    # pool, and the first unit alone imports the whole set.
                    # -------------------------------------------------------
                    # One shared identity group + pool, both created up front
                    # by create_shared_resources. The first unit carries the
                    # whole import into that pool; the rest import nothing.
                    unit_ig_name = ig_name
                    unit_ig_id = ig_id
                    unit_ig_exists = ig_exists
                    will_create_ig = False

                    unit_pool_name = pool_name
                    unit_pool_id = pool_id
                    unit_pool_exists = pool_exists
                    will_create_pool = False
                    unit_passphrases = (
                        passphrases_with_exists if is_first_unit else []
                    )

                    unit_mappings[unit_id] = UnitMapping(
                        unit_id=unit_id,
                        unit_number=unit_num,
                        plan=UnitPlan(
                            identity_group_name=unit_ig_name,
                            dpsk_pool_name=unit_pool_name,
                            will_create_identity_group=will_create_ig,
                            will_create_dpsk_pool=will_create_pool,
                            identity_group_exists=unit_ig_exists,
                            dpsk_pool_exists=unit_pool_exists,
                            will_create_network=True,
                            network_name=network_name,
                            ssid_name=ssid_name,
                            # AP Group fields (for per_unit SSID mode)
                            ap_group_name=ap_group_name,
                            ap_group_exists=ap_group_exists,
                            will_create_ap_group=will_create_ap_group,
                            ap_serial_numbers=ap_serial_numbers,
                        ),
                        resolved=UnitResolved(
                            identity_group_id=unit_ig_id,
                            dpsk_pool_id=unit_pool_id,
                            ap_group_id=ap_group_id,
                        ),
                        status=UnitStatus.PENDING,
                        input_config={
                            'scenario': 'B',
                            'passphrases': unit_passphrases,
                            'passphrase_count': len(unit_passphrases),
                            'passphrases_only': passphrases_only,
                            'ssid_mode': ssid_mode,
                            'network_name': network_name,
                            'ssid_name': ssid_name,
                            'is_first_unit': is_first_unit,
                            # AP Group info (for per_unit SSID mode)
                            'ap_group_name': ap_group_name,
                            'ap_serial_numbers': ap_serial_numbers,
                            'default_vlan': str(options.get('default_vlan', 1)),
                            # Access policy info
                            'suffix_patterns': list(suffix_patterns),
                            'users_with_suffix': users_with_suffix,
                            'users_without_suffix': users_without_suffix,
                        },
                    )
                    is_first_unit = False

                networks_to_create = len(all_units_to_process)

                # =====================================================================
                # Property-wide SSID (hybrid).
                #
                # Cloudpath scopes a passphrase to an SSID *list*; R1 scopes by
                # pool. A resident listed on both "401@Prop" and "Prop WiFi"
                # therefore cannot be served by a single pool without that pool
                # also reaching every other unit's SSID — which is exactly the
                # isolation these modes exist to provide.
                #
                # So the property SSID gets its own identity group + pool,
                # holding everyone who lists it. Residents with a unit SSID as
                # well end up in two pools (their unit's and the property's),
                # which is the faithful translation: their passphrase opens
                # their own unit and the shared property network, nothing else.
                #
                # Only fires when the export actually contains a site-wide
                # SSID, so properties without one are unaffected.
                # =====================================================================
                property_pp = [
                    p for p in passphrases_with_exists
                    if site_wide_ssid and site_wide_ssid in (p.get('ssid_list') or [])
                ]
                if create_property_ssid and site_wide_ssid and property_pp:
                    # Rides on the same shared identity group and pool as the
                    # unit SSIDs — its residents are already in there.
                    prop_ig_name = ig_name
                    prop_pool_name = pool_name
                    prop_ig_id = ig_id
                    prop_pool_id = pool_id

                    unit_mappings["property"] = UnitMapping(
                        unit_id="property",
                        unit_number="property",
                        plan=UnitPlan(
                            identity_group_name=prop_ig_name,
                            dpsk_pool_name=prop_pool_name,
                            will_create_identity_group=False,
                            will_create_dpsk_pool=False,
                            identity_group_exists=bool(prop_ig_id),
                            dpsk_pool_exists=bool(prop_pool_id),
                            will_create_network=True,
                            network_name=site_wide_ssid,
                            ssid_name=site_wide_ssid,
                            # Broadcast everywhere — no AP Group targeting.
                            ap_group_name=None,
                            ap_group_exists=False,
                            will_create_ap_group=False,
                            ap_serial_numbers=[],
                        ),
                        resolved=UnitResolved(
                            identity_group_id=prop_ig_id,
                            dpsk_pool_id=prop_pool_id,
                            ap_group_id=None,
                        ),
                        status=UnitStatus.PENDING,
                        input_config={
                            'scenario': 'B',
                            'passphrases': [],
                            'passphrase_count': 0,
                            'passphrases_only': passphrases_only,
                            'ssid_mode': ssid_mode,
                            'network_name': site_wide_ssid,
                            'ssid_name': site_wide_ssid,
                            'is_first_unit': False,
                            # Tells activate_ap_group to go venue-wide instead
                            # of binding to an AP Group.
                            'is_venue_wide': True,
                            'ap_group_name': None,
                            'ap_serial_numbers': [],
                            'default_vlan': str(options.get('default_vlan', 1)),
                            'suffix_patterns': list(suffix_patterns),
                            'users_with_suffix': users_with_suffix,
                            'users_without_suffix': users_without_suffix,
                        },
                    )
                    networks_to_create += 1
                    property_ssid_planned = True

                    await self.emit(
                        f"Property-wide SSID '{site_wide_ssid}': "
                        f"{len(property_pp)} passphrases (already in the "
                        f"shared pool)",
                        "success",
                    )
            else:
                # B1 without networks: Single global unit mapping
                unit_mappings["global"] = UnitMapping(
                    unit_id="global",
                    unit_number="site_wide",
                    plan=UnitPlan(
                        identity_group_name=ig_name,
                        dpsk_pool_name=pool_name,
                        will_create_identity_group=not ig_exists,
                        will_create_dpsk_pool=not pool_exists,
                        identity_group_exists=ig_exists,
                        dpsk_pool_exists=pool_exists,
                        will_create_network=False,
                    ),
                    resolved=UnitResolved(
                        identity_group_id=ig_id,
                        dpsk_pool_id=pool_id,
                    ),
                    status=UnitStatus.PENDING,
                    input_config={
                        'scenario': 'B',
                        'passphrases': passphrases_with_exists,
                        'passphrase_count': len(passphrases),
                        'unit_count': len(by_unit),
                        'units': list(by_unit.keys()),
                        'passphrases_only': passphrases_only,
                        'ssid_mode': ssid_mode,
                        # Access policy info
                        'suffix_patterns': list(suffix_patterns),
                        'users_with_suffix': users_with_suffix,
                        'users_without_suffix': users_without_suffix,
                    },
                )

            # Identity Group action
            actions.append(ResourceAction(
                action="reuse" if ig_exists else "create",
                resource_type="identity_group",
                name=ig_name,
                existing_id=ig_id,
            ))
            # DPSK Pool action
            actions.append(ResourceAction(
                action="reuse" if pool_exists else "create",
                resource_type="dpsk_pool",
                name=pool_name,
                existing_id=pool_id,
                notes=[f"Shared across {len(by_unit)} unit networks"],
            ))
            # Passphrases action
            pp_notes = ["Site-wide pool with roaming"]
            if planned_passphrase_creates > 0:
                if planned_passphrase_existing > 0:
                    pp_notes.append(
                        f"{planned_passphrase_existing} already exist"
                    )
                actions.append(ResourceAction(
                    action="create",
                    resource_type="passphrases",
                    name=f"{planned_passphrase_creates} passphrases",
                    notes=pp_notes,
                ))
            elif planned_passphrase_existing > 0:
                actions.append(ResourceAction(
                    action="reuse",
                    resource_type="passphrases",
                    name=f"{planned_passphrase_existing} passphrases",
                    notes=["All already imported"] + pp_notes[1:],
                ))

            if create_networks:
                # Unit SSIDs and the property-wide SSID are listed separately:
                # only the unit SSIDs get an AP Group, and lumping them
                # together made the AP Group count look one short.
                actions.append(ResourceAction(
                    action="create",
                    resource_type="wifi_networks",
                    name=f"{len(all_units_to_process)} unit SSIDs",
                    notes=["All linked to single DPSK pool"],
                ))
                if property_ssid_planned:
                    actions.append(ResourceAction(
                        action="create",
                        resource_type="wifi_network",
                        name=f"{site_wide_ssid} (property-wide)",
                        notes=[
                            "Broadcasts across the venue — no AP Group",
                            f"{len(property_pp)} passphrases "
                            f"(already in the shared pool)",
                        ],
                    ))

            # AP Group actions (for per_unit SSID mode)
            if per_unit_ssid:
                if ap_groups_to_create > 0:
                    actions.append(ResourceAction(
                        action="create",
                        resource_type="ap_groups",
                        name=f"{ap_groups_to_create} AP groups",
                        notes=["One per unit for targeted SSID broadcast"],
                    ))
                if ap_groups_to_reuse > 0:
                    actions.append(ResourceAction(
                        action="reuse",
                        resource_type="ap_groups",
                        name=f"{ap_groups_to_reuse} AP groups",
                    ))

            await self.emit(
                f"Scenario B: 1 shared identity group + 1 shared pool "
                f"→ {len(unit_mappings)} units, "
                f"{passphrases_to_create_count} new / {passphrases_existing} existing passphrases"
            )
            if per_unit_ssid:
                await self.emit(
                    f"Per-unit SSID mode: {ap_groups_to_create} AP groups to create, "
                    f"{ap_groups_to_reuse} to reuse"
                )

        # Access policy actions (if enabled)
        if enable_access_policies:
            actions.append(ResourceAction(
                action="create",
                resource_type="policy_set",
                name=policy_set_name or "Adaptive Policies",
            ))
            if policies_to_create > 0:
                notes = [f"Suffixes: {', '.join(sorted(suffix_patterns))}"]
                if policies_existing > 0:
                    notes.append(f"{policies_existing} already exist")
                # State the shortfall on the confirmation screen. A policy is
                # scoped by a unit-SSID condition, so these two are the only
                # reasons an imported resident ends up without one, and both
                # used to be invisible until someone counted rows in R1.
                if policies_skipped_no_unit_ssid > 0:
                    notes.append(
                        f"{policies_skipped_no_unit_ssid} residents on the "
                        f"property-wide SSID only get no policy"
                    )
                if colliding_accounts:
                    notes.append(
                        f"{len(colliding_accounts)} account(s) listed twice "
                        f"collapse to one policy and one identity"
                    )
                actions.append(ResourceAction(
                    action="create",
                    resource_type="adaptive_policies",
                    name=f"{policies_to_create} policies",
                    notes=notes,
                ))
            elif policies_existing > 0:
                actions.append(ResourceAction(
                    action="reuse",
                    resource_type="adaptive_policies",
                    name=f"{policies_existing} policies",
                    notes=["All already exist"],
                ))

            await self.emit(
                f"Access policies: {policies_to_create} new / {policies_existing} existing, "
                f"RADIUS groups: {radius_groups_to_create} new / {radius_groups_existing} existing"
            )

        # Calculate AP group counts from unit mappings (for per_unit mode)
        ap_groups_create_count = sum(
            1 for m in unit_mappings.values()
            if m.plan.will_create_ap_group
        )
        ap_groups_reuse_count = sum(
            1 for m in unit_mappings.values()
            if m.plan.ap_group_exists
        )

        # Always exactly one identity group and one pool.
        dpsk_pools_create_count = 0 if pool_exists else 1
        dpsk_pools_reuse_count = 1 if pool_exists else 0
        igs_create_count = 0 if ig_exists else 1
        igs_reuse_count = 1 if ig_exists else 0

        # Units the job will actually run, so the plan and the job monitor
        # agree. The property-wide SSID rides along as a pseudo-unit; it is
        # reported as its own resource, not as a unit.
        planned_unit_count = sum(
            1 for uid in unit_mappings if uid not in ("property", "global")
        ) or len(unit_mappings)

        # Build validation result
        validation_result = ValidationResult(
            valid=True,
            summary=ValidationSummary(
                total_units=planned_unit_count,
                ap_groups_to_create=ap_groups_create_count,
                ap_groups_to_reuse=ap_groups_reuse_count,
                identity_groups_to_create=igs_create_count,
                identity_groups_to_reuse=igs_reuse_count,
                dpsk_pools_to_create=dpsk_pools_create_count,
                dpsk_pools_to_reuse=dpsk_pools_reuse_count,
                networks_to_create=networks_to_create,
                property_ssid=site_wide_ssid if property_ssid_planned else "",
                passphrases_to_create=planned_passphrase_creates,
                passphrases_to_update=passphrases_to_update_count,
                passphrases_existing=planned_passphrase_existing,
                policies_to_create=policies_to_create,
                policies_existing=policies_existing,
                policies_skipped_no_unit_ssid=policies_skipped_no_unit_ssid,
                policy_name_collisions=len(colliding_accounts),
                duplicate_identities_in_r1=len(duplicate_identities),
                colliding_accounts=sorted(colliding_accounts)[:20],
                radius_groups_to_create=radius_groups_to_create,
                radius_groups_existing=radius_groups_existing,
                total_api_calls=self._estimate_api_calls(
                    import_mode, igs_create_count, dpsk_pools_create_count,
                    planned_passphrase_creates, passphrases_only, policies_to_create,
                    ap_groups_create_count, per_unit_ssid,
                ),
            ),
            actions=actions,
        )

        await self.emit(
            f"Validation complete: Scenario {import_mode}, "
            f"IDG={'reuse' if ig_exists else 'create'}, "
            f"Pool={'reuse' if pool_exists else 'create'}, "
            f"{planned_passphrase_creates} passphrases to create",
            "success"
        )

        return self.Outputs(
            import_mode=import_mode,
            scenario_detection=scenario_detection,
            pool_config=pool_config,
            identity_groups=identity_groups,
            dpsk_pools=dpsk_pools,
            passphrases=passphrases,
            unit_mappings=unit_mappings,
            validation_result=validation_result,
            all_venue_aps=all_venue_aps,
            max_activation_slots=calculated_max_activation_slots,
            venue_wide_ssid_count=venue_wide_ssid_count,
        )

    async def _fetch_pool_passphrases(
        self, pool_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Page through every passphrase in a pool.

        Returns passphrase_value -> {id, vlan_id}.
        """
        found: Dict[str, Dict[str, Any]] = {}
        page = 1
        page_size = 500

        while True:
            result = await self.r1_client.dpsk.query_passphrases(
                pool_id=pool_id,
                tenant_id=self.tenant_id,
                page=page,
                limit=page_size,
            )
            existing_pps = result.get('data', result.get('content', []))

            if not existing_pps:
                break  # No more passphrases

            for pp_entry in existing_pps:
                passphrase_val = pp_entry.get('passphrase', '')
                if not passphrase_val:
                    continue

                # Parse existing VLAN ID
                existing_vlan = pp_entry.get('vlanId')
                if existing_vlan is not None:
                    try:
                        existing_vlan = int(existing_vlan)
                    except (ValueError, TypeError):
                        existing_vlan = None

                found[passphrase_val] = {
                    'id': pp_entry.get('id'),
                    'vlan_id': existing_vlan,
                }

            # Partial page means we're done
            if len(existing_pps) < page_size:
                break

            page += 1

            # Safety limit to prevent infinite loops
            if page > 1000:
                logger.warning(
                    "Passphrase pagination safety limit reached (500k passphrases)"
                )
                break

        return found

    def _estimate_api_calls(
        self,
        scenario: str,
        num_groups: int,
        num_pools: int,
        num_passphrases: int,
        passphrases_only: bool = False,
        num_policies: int = 0,
        num_ap_groups: int = 0,
        per_unit_ssid: bool = False,
    ) -> int:
        """Estimate total API calls for the import."""
        # Identity groups: 1 call each (check existence) + 1 (create if needed)
        ig_calls = num_groups * 2
        # Pools: 1 call each (check) + 1 (create)
        pool_calls = num_pools * 2
        # Passphrases: 1 call each
        pp_calls = num_passphrases

        base_calls = ig_calls + pool_calls + pp_calls

        if passphrases_only:
            # Skip network/SSID creation
            pass
        else:
            # Add network creation estimates based on scenario
            if scenario == "A":
                # 1 network: check + create + activate
                base_calls += 3
            else:  # B - per-unit with shared pool
                # N networks all linked to 1 shared pool
                # Estimate based on num_groups (which represents unit count for B)
                base_calls += num_groups * 3

        # AP Groups (for per_unit SSID mode): create + assign APs + configure SSID
        if per_unit_ssid and num_ap_groups > 0:
            # 1 call to create AP group + 1 to assign APs + 3 for SSID config
            base_calls += num_ap_groups * 5

        # Access policies: policy_set (1) + per policy (create + 2 conditions + assign = 4)
        if num_policies > 0:
            base_calls += 1 + (num_policies * 4)

        return base_calls

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Pre-validate the Cloudpath data structure."""
        data = inputs.cloudpath_data

        errors = []
        warnings = []

        if 'pool' not in data:
            errors.append("Missing 'pool' in Cloudpath export")
        if 'dpsks' not in data:
            errors.append("Missing 'dpsks' in Cloudpath export")
        elif not data['dpsks']:
            warnings.append("No DPSKs found in export")

        return PhaseValidation(
            valid=len(errors) == 0,
            will_create=True,
            estimated_api_calls=0,  # Validation only
            errors=errors,
            warnings=warnings,
        )
