"""
Parse and Validate Phase

Parses Cloudpath JSON export and validates data.
Supports two grouping strategies:
1. Single pool - all DPSKs in one identity group/pool
2. Group by VLAN - one identity group/pool per VLAN
"""

import logging
import json
import re
from typing import Dict, Any, List, Union
from datetime import datetime, timezone
from workflow.models import Task, TaskStatus

logger = logging.getLogger(__name__)


async def execute(context: Dict[str, Any]) -> List[Task]:
    """
    Parse and validate Cloudpath DPSK export

    This phase parses the Cloudpath JSON export and stores the parsed data
    for subsequent phases to use.

    Args:
        context: Workflow context containing input_data with dpsk_data

    Returns:
        List containing single completed task with parsed data
    """
    logger.warning("Phase 1: Parse and Validate Cloudpath Data")

    dpsk_data = context.get('input_data', {}).get('dpsk_data', [])
    options = context.get('options', {})

    logger.warning(f"🔍 DEBUG PARSE - dpsk_data type: {type(dpsk_data)}, length: {len(dpsk_data) if isinstance(dpsk_data, list) else 'N/A'}")
    logger.warning(f"🔍 DEBUG PARSE - options: {options}")

    # Parse the Cloudpath export
    parsed = _parse_cloudpath_dpsks(dpsk_data, options)

    logger.warning(f"✅ Parsed {len(parsed['identity_groups'])} identity groups, "
                f"{len(parsed['dpsk_pools'])} DPSK pools, "
                f"{len(parsed['passphrases'])} passphrases")

    logger.warning(f"🔍 DEBUG PARSE - parsed keys: {list(parsed.keys())}")
    logger.warning(f"🔍 DEBUG PARSE - identity_groups sample: {parsed['identity_groups'][:2] if parsed['identity_groups'] else 'empty'}")

    # Return a single completed task with the parsed data
    task = Task(
        id="parse_validate",
        name="Parse and validate Cloudpath export",
        task_type="parse",
        status=TaskStatus.COMPLETED,
        input_data=context.get('input_data', {}),
        output_data={'parsed_data': parsed}
    )

    logger.warning(f"🔍 DEBUG PARSE - task.output_data keys: {list(task.output_data.keys())}")
    logger.warning(f"🔍 DEBUG PARSE - task.output_data['parsed_data'] type: {type(task.output_data['parsed_data'])}")

    return [task]

def _parse_cloudpath_dpsks(dpsk_data: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Cloudpath DPSK export into R1-compatible structure

    Supports two input formats:

    1. Legacy flat array format:
    [
        {
            "guid": "AccountDpsk-...",
            "name": "DPSK15",
            "passphrase": "cemqwzmzgit",
            ...
        }
    ]

    2. New nested format with pool metadata:
    {
        "metadata": { ... },
        "pool": {
            "displayName": "MyPoolOne",
            "description": "Pool description",
            "phraseDefaultLength": 8,
            "phraseRandomCharactersType": "ALPHANUMERIC_MIXED",
            ...
        },
        "dpsks": [ ... ]
    }

    Args:
        dpsk_data: Either a list of DPSK objects (legacy) or a dict with pool/dpsks (new)
        options: Migration options including 'group_by_vlan', 'identity_group_name'

    Returns:
        Dict with parsed structure for R1 import:
        {
            'identity_groups': [{'name': str, 'description': str}, ...],
            'dpsk_pools': [{'name': str, 'identity_group_name': str, 'vlan_id': str, ...}, ...],
            'passphrases': [{'userName': str, 'passphrase': str, 'dpsk_pool_name': str, ...}, ...],
            'cloudpath_pool': {...}  # Pool metadata from new format (if available)
        }
    """
    # Detect format and extract DPSK array and pool metadata
    dpsk_array = []
    pool_metadata = None

    if isinstance(dpsk_data, dict):
        # New nested format - check for 'dpsks' key
        if 'dpsks' in dpsk_data:
            logger.info("📋 Detected new nested Cloudpath export format")
            dpsk_array = dpsk_data.get('dpsks', [])
            pool_metadata = dpsk_data.get('pool', {})
            metadata = dpsk_data.get('metadata', {})

            if pool_metadata:
                logger.info(f"   Pool: {pool_metadata.get('displayName', 'Unknown')}")
                logger.info(f"   Description: {pool_metadata.get('description', 'N/A')}")
                logger.info(f"   Passphrase length: {pool_metadata.get('phraseDefaultLength', 'N/A')}")
                logger.info(f"   Passphrase format: {pool_metadata.get('phraseRandomCharactersType', 'N/A')}")
                if pool_metadata.get('ssidList'):
                    logger.info(f"   Associated SSIDs: {pool_metadata.get('ssidList')}")
            if metadata:
                logger.info(f"   Extracted from: {metadata.get('cloudpath_fqdn', 'Unknown')}")
                logger.info(f"   Extraction date: {metadata.get('extracted_at', 'Unknown')}")
        else:
            # Single dict without 'dpsks' key - wrap in array (legacy single item)
            logger.warning("dpsk_data is a dict without 'dpsks' key, treating as single DPSK")
            dpsk_array = [dpsk_data]
    elif isinstance(dpsk_data, list):
        # Legacy flat array format
        logger.info("📋 Detected legacy flat array Cloudpath export format")
        dpsk_array = dpsk_data
    else:
        logger.warning(f"Unexpected dpsk_data type: {type(dpsk_data)}, using empty array")
        dpsk_array = []

    logger.info(f"   Total DPSKs to import: {len(dpsk_array)}")

    group_by_vlan = options.get('group_by_vlan', False)
    custom_identity_group_name = options.get('identity_group_name')
    custom_dpsk_service_name = options.get('dpsk_service_name')

    # If no custom names provided and we have pool metadata, use the pool's displayName
    if pool_metadata:
        pool_display_name = pool_metadata.get('displayName')
        if pool_display_name:
            if not custom_identity_group_name:
                logger.info(f"   Using pool displayName as identity group name: {pool_display_name}")
                custom_identity_group_name = pool_display_name
            if not custom_dpsk_service_name:
                logger.info(f"   Using pool displayName as DPSK service name: {pool_display_name}")
                custom_dpsk_service_name = pool_display_name

    if group_by_vlan:
        result = _parse_by_vlan(dpsk_array, custom_identity_group_name, custom_dpsk_service_name, pool_metadata)
    else:
        result = _parse_single_pool(dpsk_array, custom_identity_group_name, custom_dpsk_service_name, pool_metadata)

    # Include pool metadata in result for reference
    if pool_metadata:
        result['cloudpath_pool'] = pool_metadata

    return result


def _parse_single_pool(dpsk_array: List[Dict[str, Any]], custom_identity_group_name: str = None, custom_dpsk_service_name: str = None, pool_metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Single pool strategy - all DPSKs in one identity group and pool

    Creates:
    - 1 identity group: custom name or pool displayName or "Cloudpath Import"
    - 1 DPSK pool: custom name or "{identity_group_name} - DPSKs"
    - All passphrases in that pool

    Args:
        dpsk_array: List of Cloudpath DPSK objects
        custom_identity_group_name: Optional custom name for the identity group
        custom_dpsk_service_name: Optional custom name for the DPSK service/pool
        pool_metadata: Optional pool metadata from new export format

    Returns:
        Parsed structure
    """
    logger.info(f"📦 Using single pool strategy for {len(dpsk_array)} DPSKs")

    identity_group_name = custom_identity_group_name or "Cloudpath Import"
    # Use custom DPSK service name if provided, otherwise derive from identity group
    dpsk_pool_name = custom_dpsk_service_name or f"{identity_group_name} - DPSKs"

    # Build description - use pool description if available
    if pool_metadata and pool_metadata.get('description'):
        identity_group_desc = pool_metadata['description']
        dpsk_pool_desc = pool_metadata['description']
    else:
        identity_group_desc = f'Imported from Cloudpath - {len(dpsk_array)} DPSKs'
        dpsk_pool_desc = f'Cloudpath DPSK import - {len(dpsk_array)} passphrases'

    # Create single identity group
    identity_groups = [
        {
            'name': identity_group_name,
            'description': identity_group_desc
        }
    ]

    # Map all passphrases to single pool
    passphrases = []
    for dpsk in dpsk_array:
        pp = _map_cloudpath_dpsk_to_passphrase(dpsk, dpsk_pool_name)
        if pp:
            passphrases.append(pp)

    # Analyze passphrase characteristics
    analysis = _analyze_passphrase_characteristics(passphrases)

    # Use pool metadata for settings if available, otherwise use analysis
    passphrase_format = analysis['passphrase_format']
    passphrase_length = analysis['passphrase_length']

    if pool_metadata:
        # Map Cloudpath format types to R1 format types
        cloudpath_format = pool_metadata.get('phraseRandomCharactersType', '')
        if cloudpath_format == 'ALPHANUMERIC_MIXED':
            passphrase_format = 'KEYBOARD_FRIENDLY'
        elif cloudpath_format == 'ALPHANUMERIC_UPPERCASE':
            passphrase_format = 'KEYBOARD_FRIENDLY'
        elif cloudpath_format == 'ALPHANUMERIC_LOWERCASE':
            passphrase_format = 'KEYBOARD_FRIENDLY'
        elif cloudpath_format == 'NUMERIC':
            passphrase_format = 'NUMBERS_ONLY'
        elif cloudpath_format == 'COMPLEX':
            passphrase_format = 'MOST_SECURED'

        # Use pool's default length if available
        if pool_metadata.get('phraseDefaultLength'):
            passphrase_length = pool_metadata['phraseDefaultLength']

    # Create single DPSK pool with analyzed settings
    dpsk_pools = [
        {
            'name': dpsk_pool_name,
            'identity_group_name': identity_group_name,
            'description': dpsk_pool_desc,
            'vlan_id': None,  # No specific VLAN
            'passphrase_format': passphrase_format,
            'passphrase_length': passphrase_length,
            'expiration_days': analysis['expiration_days'],
            'max_devices': analysis['max_devices'],
            'analysis': analysis  # Include full analysis for reference
        }
    ]

    return {
        'identity_groups': identity_groups,
        'dpsk_pools': dpsk_pools,
        'passphrases': passphrases,
        'analysis': analysis  # Include at top level for warnings
    }


def _parse_by_vlan(dpsk_array: List[Dict[str, Any]], custom_name_prefix: str = None, custom_dpsk_service_prefix: str = None, pool_metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Group by VLAN strategy - one identity group/pool per VLAN

    Creates:
    - 1 identity group per VLAN: "[prefix] VLAN 44", "[prefix] VLAN 50", etc.
    - 1 DPSK pool per VLAN: "[dpsk_prefix] - VLAN 44 - DPSKs" or derived from identity group
    - Passphrases distributed by VLAN

    Args:
        dpsk_array: List of Cloudpath DPSK objects
        custom_name_prefix: Optional prefix for identity group names
        custom_dpsk_service_prefix: Optional prefix for DPSK service names
        pool_metadata: Optional pool metadata from new export format

    Returns:
        Parsed structure
    """
    logger.info(f"🏷️  Using group by VLAN strategy for {len(dpsk_array)} DPSKs")

    # Group DPSKs by VLAN
    vlan_groups = {}
    for dpsk in dpsk_array:
        vlan_id = dpsk.get('vlanid', '')

        # Normalize VLAN ID (empty string or None -> "No VLAN")
        if not vlan_id or vlan_id == '0':
            vlan_id = 'No VLAN'

        if vlan_id not in vlan_groups:
            vlan_groups[vlan_id] = []

        vlan_groups[vlan_id].append(dpsk)

    logger.info(f"📊 Found {len(vlan_groups)} unique VLANs: {list(vlan_groups.keys())}")

    # Use custom prefixes or defaults
    id_group_prefix = f"{custom_name_prefix} - " if custom_name_prefix else ""
    dpsk_service_prefix = custom_dpsk_service_prefix or custom_name_prefix

    # Get pool description for use in groups
    pool_desc_suffix = ""
    if pool_metadata and pool_metadata.get('description'):
        pool_desc_suffix = f" - {pool_metadata['description']}"

    identity_groups = []
    dpsk_pools = []
    passphrases = []
    all_warnings = []

    # Create identity group and pool for each VLAN
    for vlan_id, dpsks in vlan_groups.items():
        identity_group_name = f"{id_group_prefix}VLAN {vlan_id}" if vlan_id != 'No VLAN' else f"{id_group_prefix}No VLAN"
        # Use custom DPSK service prefix if provided, otherwise derive from identity group
        if dpsk_service_prefix:
            dpsk_pool_name = f"{dpsk_service_prefix} - VLAN {vlan_id}" if vlan_id != 'No VLAN' else f"{dpsk_service_prefix} - No VLAN"
        else:
            dpsk_pool_name = f"{identity_group_name} - DPSKs"

        # Create identity group
        identity_groups.append({
            'name': identity_group_name,
            'description': f'Cloudpath import - VLAN {vlan_id} - {len(dpsks)} DPSKs{pool_desc_suffix}'
        })

        # Map passphrases for this VLAN
        vlan_passphrases = []
        for dpsk in dpsks:
            pp = _map_cloudpath_dpsk_to_passphrase(dpsk, dpsk_pool_name)
            if pp:
                vlan_passphrases.append(pp)
                passphrases.append(pp)

        # Analyze passphrase characteristics for this VLAN group
        analysis = _analyze_passphrase_characteristics(vlan_passphrases)
        all_warnings.extend(analysis.get('warnings', []))

        # Create DPSK pool with analyzed settings
        dpsk_pools.append({
            'name': dpsk_pool_name,
            'identity_group_name': identity_group_name,
            'description': f'Cloudpath DPSK import for VLAN {vlan_id} - {len(dpsks)} passphrases',
            'vlan_id': vlan_id if vlan_id != 'No VLAN' else None,
            'passphrase_format': analysis['passphrase_format'],
            'passphrase_length': analysis['passphrase_length'],
            'expiration_days': analysis['expiration_days'],
            'max_devices': analysis['max_devices'],
            'analysis': analysis  # Include full analysis for reference
        })

    # Create combined analysis for all VLANs
    combined_analysis = _analyze_passphrase_characteristics(passphrases)

    return {
        'identity_groups': identity_groups,
        'dpsk_pools': dpsk_pools,
        'passphrases': passphrases,
        'analysis': combined_analysis  # Overall analysis
    }


def _map_cloudpath_dpsk_to_passphrase(dpsk: Dict[str, Any], dpsk_pool_name: str) -> Dict[str, Any]:
    """
    Map a single Cloudpath DPSK object to R1 passphrase format

    Cloudpath fields:
    - guid: Unique identifier
    - name: DPSK name (used as userName in R1)
    - passphrase: The actual passphrase
    - status: "ACTIVE" or other
    - expirationDateTime: ISO datetime with timezone
    - useDeviceCountLimit: bool
    - deviceCountLimit: int
    - vlanid: VLAN ID string

    R1 fields:
    - userName: Display name
    - passphrase: The passphrase
    - dpsk_pool_name: Pool to assign to
    - expiration: Datetime (optional)
    - max_usage: Device count limit (optional)
    - cloudpath_guid: Original Cloudpath GUID for reference

    Args:
        dpsk: Cloudpath DPSK object
        dpsk_pool_name: Name of DPSK pool to assign to

    Returns:
        R1 passphrase dict or None if invalid
    """
    # Debug: Log incoming DPSK object keys to understand the format
    logger.warning(f"🔍 DEBUG PARSE - DPSK object keys: {list(dpsk.keys())}")
    logger.warning(f"🔍 DEBUG PARSE - DPSK guid value: {dpsk.get('guid')}")

    if not dpsk.get('passphrase'):
        logger.warning(f"DPSK {dpsk.get('name', 'unknown')} has no passphrase, skipping")
        return None

    # Parse expiration if present
    expiration = None
    if dpsk.get('expirationDateTime'):
        try:
            # Parse ISO datetime with timezone
            # Example: "2020-12-06T00:00-07:00[America/Denver]"
            exp_str = dpsk['expirationDateTime']

            # Strip timezone name in brackets if present
            if '[' in exp_str:
                exp_str = exp_str.split('[')[0]

            # Parse ISO format
            expiration = datetime.fromisoformat(exp_str.replace('Z', '+00:00'))
            expiration = expiration.isoformat()

        except Exception as e:
            logger.warning(f"Failed to parse expiration for {dpsk.get('name')}: {str(e)}")

    # Parse device count limit
    max_usage = None
    if dpsk.get('useDeviceCountLimit') and dpsk.get('deviceCountLimit', 0) > 0:
        max_usage = dpsk['deviceCountLimit']

    return {
        'userName': dpsk.get('name', f"DPSK-{dpsk.get('guid', 'unknown')}"),
        'passphrase': dpsk['passphrase'],
        'dpsk_pool_name': dpsk_pool_name,
        'expiration': expiration,
        'max_usage': max_usage,
        'cloudpath_guid': dpsk.get('guid'),
        'cloudpath_status': dpsk.get('status'),
        'vlan_id': dpsk.get('vlanid')
    }


def _analyze_passphrase_characteristics(passphrases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze passphrase characteristics to determine optimal DPSK pool settings

    Analyzes:
    - Passphrase format (numbers only, alphanumeric, alphanumeric+symbols)
    - Passphrase length (min/max)
    - Expiration dates (if any are in the past, longest future date)
    - Device limits

    Args:
        passphrases: List of passphrase dicts

    Returns:
        Dict with analyzed characteristics and recommended pool settings
    """
    if not passphrases:
        return {
            'passphrase_format': 'KEYBOARD_FRIENDLY',
            'passphrase_length': 18,
            'has_expiration': False,
            'expiration_days': None,
            'max_devices': 1,
            'warnings': []
        }

    # Analyze passphrase format and length
    min_length = float('inf')
    max_length = 0
    has_numbers_only = True
    has_symbols = False

    for pp in passphrases:
        passphrase = pp.get('passphrase', '')
        if not passphrase:
            continue

        pp_len = len(passphrase)
        min_length = min(min_length, pp_len)
        max_length = max(max_length, pp_len)

        # Check format
        if not passphrase.isdigit():
            has_numbers_only = False

        if re.search(r'[^a-zA-Z0-9]', passphrase):
            has_symbols = True

    # Determine passphrase format (using RuckusONE API enum values)
    if has_numbers_only:
        passphrase_format = 'NUMBERS_ONLY'
    elif has_symbols:
        passphrase_format = 'MOST_SECURED'  # Letters, numbers, symbols
    else:
        passphrase_format = 'KEYBOARD_FRIENDLY'  # Alphanumeric (a-z, A-Z, 0-9)

    # Analyze expiration dates
    now = datetime.now(timezone.utc)
    has_expiration = False
    expired_count = 0
    future_expirations = []

    for pp in passphrases:
        if pp.get('expiration'):
            has_expiration = True
            try:
                exp_dt = datetime.fromisoformat(pp['expiration'].replace('Z', '+00:00'))
                if exp_dt < now:
                    expired_count += 1
                else:
                    future_expirations.append(exp_dt)
            except:
                pass

    # Determine expiration setting
    expiration_days = None
    if has_expiration and future_expirations:
        # Use longest future expiration
        longest_exp = max(future_expirations)
        days_until = (longest_exp - now).days
        expiration_days = max(days_until, 1)  # At least 1 day

    # Analyze device limits
    # If ANY passphrase needs unlimited devices, the pool must support unlimited
    has_unlimited = any(
        pp.get('max_usage') is None or pp.get('max_usage') == 0
        for pp in passphrases
    )

    if has_unlimited:
        # Pool must allow unlimited devices
        max_devices = 0  # 0 means unlimited in the pool configuration
    else:
        # All passphrases have limits, use the maximum
        max_devices_values = [pp.get('max_usage') for pp in passphrases if pp.get('max_usage')]
        max_devices = max(max_devices_values) if max_devices_values else 1

    # Generate warnings
    warnings = []

    # Check for inconsistent passphrase lengths
    if min_length != max_length:
        warnings.append(
            f"⚠️  Passphrases have varying lengths ({min_length}-{max_length} chars). "
            f"DPSK pool will be configured for minimum length of {min_length} chars."
        )

    if expired_count > 0:
        warnings.append(
            f"⚠️  {expired_count}/{len(passphrases)} passphrases have expired. "
            f"They will be imported without expiration dates."
        )

    # Info about device limits
    if has_unlimited:
        unlimited_count = sum(1 for pp in passphrases if pp.get('max_usage') is None or pp.get('max_usage') == 0)
        limited_count = len(passphrases) - unlimited_count
        if limited_count > 0:
            warnings.append(
                f"ℹ️  Mixed device limits detected: {unlimited_count} unlimited, {limited_count} limited. "
                f"DPSK pool will be configured to allow unlimited devices (individual passphrases can still have limits)."
            )
        else:
            warnings.append(
                f"ℹ️  All {len(passphrases)} passphrases have unlimited devices. "
                f"DPSK pool will be configured for unlimited devices."
            )

    result = {
        'passphrase_format': passphrase_format,
        'passphrase_length': min_length,  # Set pool to match shortest Cloudpath passphrase
        'min_cloudpath_length': min_length,
        'max_cloudpath_length': max_length,
        'has_expiration': has_expiration,
        'expiration_days': expiration_days,
        'expired_count': expired_count,
        'max_devices': max_devices,
        'warnings': warnings,
        'total_passphrases': len(passphrases),
        'passphrases_importable': len(passphrases)  # All are importable!
    }

    logger.warning(f"📊 Passphrase Analysis:")
    logger.warning(f"   Format: {passphrase_format}")
    logger.warning(f"   Length: {min_length}-{max_length} chars (will configure pool for {min_length}+ chars)")
    logger.warning(f"   Expiration: {expiration_days} days" if expiration_days else "   Expiration: None")

    # Show device limit configuration
    if has_unlimited:
        logger.warning(f"   Device limit: UNLIMITED (pool configured to allow unlimited, individual passphrases may have limits)")
    else:
        logger.warning(f"   Device limit: {max_devices} devices max")

    logger.warning(f"   All {len(passphrases)} passphrases are importable")

    if warnings:
        for warning in warnings:
            logger.warning(f"   {warning}")

    return result
