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
   - 1 shared DPSK pool → N SSIDs (one per unit)
   - Each unit gets its own SSID (e.g., "108@Property")
   - AP Groups created per-unit for targeted broadcast
   - APs assigned to groups, SSIDs configured per-group
   - Passphrases work on any unit's SSID (roaming enabled)

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
        )

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

            passphrases.append(ParsedPassphrase(
                guid=dpsk.get('guid', ''),
                name=dpsk.get('name', ''),
                passphrase=passphrase_str,
                status=dpsk.get('status', 'ACTIVE'),
                ssid_list=ssid_list,
                unit_number=unit_number,
                device_count=dpsk.get('deviceCount', 0),
                vlan_id=vlan_id,
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

        # Allow user to override detected scenario
        forced_scenario = options.get('import_scenario')
        if forced_scenario and forced_scenario in ("A", "B"):
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

        # Check SSID creation mode: 'none', 'single', 'per_unit'
        ssid_mode = options.get('ssid_mode', 'none')
        passphrases_only = ssid_mode == 'none'
        create_networks = ssid_mode in ('single', 'per_unit')
        per_unit_ssid = ssid_mode == 'per_unit'

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
        # Paginate through ALL passphrases in the pool - no artificial limit
        # Store passphrase_value -> {id, vlan_id} for update detection
        existing_passphrases: Dict[str, Dict[str, Any]] = {}
        if pool_id:
            try:
                page = 1
                page_size = 500
                total_fetched = 0

                while True:
                    result = await self.r1_client.dpsk.query_passphrases(
                        pool_id=pool_id,
                        tenant_id=self.tenant_id,
                        page=page,
                        limit=page_size
                    )
                    existing_pps = result.get('data', result.get('content', []))

                    if not existing_pps:
                        break  # No more passphrases

                    for pp_entry in existing_pps:
                        passphrase_val = pp_entry.get('passphrase', '')
                        if passphrase_val:
                            # Parse existing VLAN ID
                            existing_vlan = pp_entry.get('vlanId')
                            if existing_vlan is not None:
                                try:
                                    existing_vlan = int(existing_vlan)
                                except (ValueError, TypeError):
                                    existing_vlan = None

                            existing_passphrases[passphrase_val] = {
                                'id': pp_entry.get('id'),
                                'vlan_id': existing_vlan,
                            }
                            logger.debug(f"Existing passphrase: id={pp_entry.get('id')}, vlan={existing_vlan}")

                    total_fetched += len(existing_pps)

                    # Check if we got a full page - if not, we're done
                    if len(existing_pps) < page_size:
                        break

                    page += 1

                    # Safety limit to prevent infinite loops
                    if page > 1000:
                        logger.warning("Passphrase pagination safety limit reached (500k passphrases)")
                        break

                if existing_passphrases:
                    await self.emit(
                        f"Found {len(existing_passphrases)} existing passphrases in pool"
                    )
            except Exception as e:
                logger.warning(f"Error checking existing passphrases: {e}")

        # Count new vs existing passphrases by comparing passphrase values
        # Also build a list of passphrase dicts with 'exists' flag for create_passphrases phase
        # Track VLAN mismatches for updates
        passphrases_existing = 0
        passphrases_to_create_count = 0
        passphrases_to_update_count = 0
        passphrases_with_exists: List[Dict[str, Any]] = []

        for pp in passphrases:
            pp_dict = pp.model_dump()
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
                passphrases_to_create_count += 1
            passphrases_with_exists.append(pp_dict)

        if passphrases_to_update_count > 0:
            await self.emit(
                f"VLAN updates needed: {passphrases_to_update_count} passphrases"
            )

        # =====================================================================
        # Fetch Venue APs (needed for per_unit SSID mode with AP assignment)
        # =====================================================================
        all_venue_aps: List[Dict[str, Any]] = []
        if per_unit_ssid and ap_assignment_mode != 'skip':
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
                ap_groups_response = await self.r1_client.venues.get_venue_ap_groups(
                    self.tenant_id, self.venue_id
                )
                for ap_group in ap_groups_response.get('data', []):
                    existing_ap_groups[ap_group.get('name', '')] = ap_group.get('id', '')
                await self.emit(f"Found {len(existing_ap_groups)} existing AP Groups")
            except Exception as e:
                logger.warning(f"Error checking AP groups: {e}")

        # =====================================================================
        # Count venue-wide SSIDs (for activation slot limit calculation)
        # R1 limits 15 SSIDs per AP Group. When activating SSIDs, they
        # temporarily broadcast to ALL AP Groups until assigned specifically.
        # We count existing venue-wide SSIDs to calculate a safe concurrent limit.
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
                        f"Found {venue_wide_ssid_count} venue-wide SSIDs, "
                        f"limiting concurrent activations to {calculated_max_activation_slots}",
                        "info"
                    )
                else:
                    await self.emit(
                        f"No venue-wide SSIDs found, using default limit of {calculated_max_activation_slots}"
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

            # Count policies to create (one per passphrase with unit SSID)
            for pp in passphrases:
                for ssid in pp.ssid_list:
                    if UNIT_SSID_PATTERN.match(ssid):
                        # Build expected policy name
                        account = pp.name.rsplit("_", 1)[0] if "_" in pp.name else pp.name
                        policy_name = f"{account}_{ssid.replace('@', '_at_')}"
                        if policy_name in existing_policy_names:
                            policies_existing += 1
                        else:
                            policies_to_create += 1

        await self.emit(
            f"Resource check: IDG={'exists' if ig_exists else 'new'}, "
            f"Pool={'exists' if pool_exists else 'new'}, "
            f"Passphrases={passphrases_to_create_count} new/{passphrases_existing} existing"
        )

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
            # SCENARIO B: Per-unit with shared pool
            # 1 DPSK pool shared across all unit networks
            identity_groups.append({'name': ig_name, 'exists': ig_exists, 'id': ig_id})
            dpsk_pools.append({
                'name': pool_name,
                'identity_group_name': ig_name,
                'shared_across_units': True,
                'exists': pool_exists,
                'id': pool_id,
            })

            # Group passphrases by unit for tracking
            by_unit: Dict[str, List[ParsedPassphrase]] = defaultdict(list)
            for pp in passphrases:
                unit_num = pp.unit_number or "shared"
                by_unit[unit_num].append(pp)

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

            if create_networks:
                # B1 with networks: Create unit mappings for each unit
                # All units share the same pool, but each gets its own network
                # First unit creates ALL passphrases (they go to shared pool)
                #
                # IMPORTANT: Use all_unit_ssids (merged master + DPSK list) as the
                # authoritative source of units, not just by_unit (DPSK-based).
                # This ensures we create networks for SSIDs in the pool master list
                # even if no individual DPSK has that unit in its ssidList.
                is_first_unit = True

                # Build the set of all units to process (from master list + DPSK entries)
                all_units_to_process = set(all_unit_ssids.keys()) | set(by_unit.keys())

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
                        ap_group_name = f"{ap_group_prefix}{unit_num}{ap_group_postfix}"
                        if ap_group_name in existing_ap_groups:
                            ap_group_exists = True
                            ap_group_id = existing_ap_groups[ap_group_name]
                            ap_groups_to_reuse += 1
                        else:
                            will_create_ap_group = True
                            ap_groups_to_create += 1

                    # Get AP serial numbers from CSV assignments (processed above)
                    ap_serial_numbers: List[str] = unit_to_aps.get(unit_num, [])

                    unit_mappings[unit_id] = UnitMapping(
                        unit_id=unit_id,
                        unit_number=unit_num,
                        plan=UnitPlan(
                            identity_group_name=ig_name,
                            dpsk_pool_name=pool_name,
                            # Only first unit creates IDG and pool if they don't exist
                            will_create_identity_group=is_first_unit and not ig_exists,
                            will_create_dpsk_pool=is_first_unit and not pool_exists,
                            identity_group_exists=ig_exists,
                            dpsk_pool_exists=pool_exists,
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
                            identity_group_id=ig_id,
                            dpsk_pool_id=pool_id,
                            ap_group_id=ap_group_id,
                        ),
                        status=UnitStatus.PENDING,
                        input_config={
                            'scenario': 'B',
                            # First unit creates ALL passphrases (shared pool)
                            'passphrases': passphrases_with_exists if is_first_unit else [],
                            'passphrase_count': len(passphrases) if is_first_unit else 0,
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
            if passphrases_to_create_count > 0:
                notes = ["Site-wide pool with roaming"]
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
                actions.append(ResourceAction(
                    action="create",
                    resource_type="wifi_networks",
                    name=f"{networks_to_create} unit networks",
                    notes=["All linked to single DPSK pool"],
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
                f"Scenario B: 1 shared pool → {len(unit_mappings)} units, "
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

        # Build validation result
        validation_result = ValidationResult(
            valid=True,
            summary=ValidationSummary(
                total_units=actual_unit_count if actual_unit_count > 0 else len(unit_mappings),
                ap_groups_to_create=ap_groups_create_count,
                ap_groups_to_reuse=ap_groups_reuse_count,
                identity_groups_to_create=0 if ig_exists else 1,
                identity_groups_to_reuse=1 if ig_exists else 0,
                dpsk_pools_to_create=0 if pool_exists else 1,
                dpsk_pools_to_reuse=1 if pool_exists else 0,
                networks_to_create=networks_to_create,
                passphrases_to_create=passphrases_to_create_count,
                passphrases_to_update=passphrases_to_update_count,
                passphrases_existing=passphrases_existing,
                policies_to_create=policies_to_create,
                policies_existing=policies_existing,
                radius_groups_to_create=radius_groups_to_create,
                radius_groups_existing=radius_groups_existing,
                total_api_calls=self._estimate_api_calls(
                    import_mode, 0 if ig_exists else 1, 0 if pool_exists else 1,
                    passphrases_to_create_count, passphrases_only, policies_to_create,
                    ap_groups_create_count, per_unit_ssid,
                ),
            ),
            actions=actions,
        )

        await self.emit(
            f"Validation complete: Scenario {import_mode}, "
            f"IDG={'reuse' if ig_exists else 'create'}, "
            f"Pool={'reuse' if pool_exists else 'create'}, "
            f"{passphrases_to_create_count} passphrases to create",
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
