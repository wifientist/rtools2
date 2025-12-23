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
from typing import Dict, Any, List
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

def _parse_cloudpath_dpsks(dpsk_array: List[Dict[str, Any]], options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Cloudpath DPSK export into R1-compatible structure

    Cloudpath format is a flat array:
    [
        {
            "guid": "AccountDpsk-...",
            "name": "DPSK15",
            "passphrase": "cemqwzmzgit",
            "status": "ACTIVE",
            "ssidList": [],
            "expirationDateTime": "2020-12-06T00:00-07:00[America/Denver]",
            "useDeviceCountLimit": false,
            "deviceCountLimit": 0,
            "vlanid": "44"
        }
    ]

    Args:
        dpsk_array: List of Cloudpath DPSK objects
        options: Migration options including 'group_by_vlan'

    Returns:
        Dict with parsed structure for R1 import:
        {
            'identity_groups': [{'name': str, 'description': str}, ...],
            'dpsk_pools': [{'name': str, 'identity_group_name': str, 'vlan_id': str, ...}, ...],
            'passphrases': [{'userName': str, 'passphrase': str, 'dpsk_pool_name': str, ...}, ...]
        }
    """
    if not isinstance(dpsk_array, list):
        logger.warning("dpsk_data is not a list, wrapping in array")
        dpsk_array = [dpsk_array] if dpsk_array else []

    group_by_vlan = options.get('group_by_vlan', False)

    if group_by_vlan:
        return _parse_by_vlan(dpsk_array)
    else:
        return _parse_single_pool(dpsk_array)


def _parse_single_pool(dpsk_array: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Single pool strategy - all DPSKs in one identity group and pool

    Creates:
    - 1 identity group: "Cloudpath Import"
    - 1 DPSK pool: "Cloudpath DPSKs"
    - All passphrases in that pool

    Args:
        dpsk_array: List of Cloudpath DPSK objects

    Returns:
        Parsed structure
    """
    logger.info(f"📦 Using single pool strategy for {len(dpsk_array)} DPSKs")

    identity_group_name = "Cloudpath Import"
    dpsk_pool_name = "Cloudpath DPSKs"

    # Create single identity group
    identity_groups = [
        {
            'name': identity_group_name,
            'description': f'Imported from Cloudpath - {len(dpsk_array)} DPSKs'
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

    # Create single DPSK pool with analyzed settings
    dpsk_pools = [
        {
            'name': dpsk_pool_name,
            'identity_group_name': identity_group_name,
            'description': f'Cloudpath DPSK import - {len(dpsk_array)} passphrases',
            'vlan_id': None,  # No specific VLAN
            'passphrase_format': analysis['passphrase_format'],
            'passphrase_length': analysis['passphrase_length'],
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


def _parse_by_vlan(dpsk_array: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Group by VLAN strategy - one identity group/pool per VLAN

    Creates:
    - 1 identity group per VLAN: "VLAN 44", "VLAN 50", etc.
    - 1 DPSK pool per VLAN
    - Passphrases distributed by VLAN

    Args:
        dpsk_array: List of Cloudpath DPSK objects

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

    identity_groups = []
    dpsk_pools = []
    passphrases = []
    all_warnings = []

    # Create identity group and pool for each VLAN
    for vlan_id, dpsks in vlan_groups.items():
        identity_group_name = f"VLAN {vlan_id}" if vlan_id != 'No VLAN' else "No VLAN"
        dpsk_pool_name = f"VLAN {vlan_id} - Cloudpath DPSKs" if vlan_id != 'No VLAN' else "No VLAN - Cloudpath DPSKs"

        # Create identity group
        identity_groups.append({
            'name': identity_group_name,
            'description': f'Cloudpath import - VLAN {vlan_id} - {len(dpsks)} DPSKs'
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
