"""
Shared AP LAN Port Configuration Service

Provides core logic for configuring AP LAN ports that can be used by:
- Per-Unit SSID tool (Phase 5)
- Standalone AP Port Config tool

Uses Ethernet Port Profiles API approach (same as R1 GUI):
1. Find the existing "Default ACCESS Port" profile (built-in to every venue)
2. Disable venue settings inheritance for each AP's LAN ports
3. Set VLAN overrides on each port via lanPorts/{port}/settings
4. Activate the default ACCESS profile on the port
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# Import centralized AP model metadata
from r1api.models import (
    MODEL_PORT_COUNTS,
    MODEL_UPLINK_PORTS,
    has_configurable_lan_ports,
    get_port_count,
    get_uplink_port,
    get_configurable_ports,
    get_model_info,
)

logger = logging.getLogger(__name__)


class PortMode(str, Enum):
    """Port configuration modes"""
    IGNORE = "ignore"      # Don't touch this port
    SPECIFIC = "specific"  # Set specific VLAN
    MATCH = "match"        # Match a reference VLAN (used by per-unit SSID)
    DISABLE = "disable"    # Disable the port
    UPLINK = "uplink"      # Protected uplink port (skip)


@dataclass
class PortConfig:
    """Configuration for a single port"""
    mode: PortMode = PortMode.IGNORE
    vlan: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'mode': self.mode.value if isinstance(self.mode, PortMode) else self.mode,
            'vlan': self.vlan
        }


@dataclass
class APPortRequest:
    """Request to configure ports on a single AP"""
    ap_identifier: str  # Name or serial number
    ports: Dict[str, PortConfig] = field(default_factory=dict)  # e.g., {'LAN1': PortConfig(...)}

    # Alternative: per-port attributes for simpler API
    lan1: Optional[PortConfig] = None
    lan2: Optional[PortConfig] = None
    lan3: Optional[PortConfig] = None
    lan4: Optional[PortConfig] = None
    lan5: Optional[PortConfig] = None

    def get_port_configs(self) -> Dict[str, PortConfig]:
        """Get all port configs, merging explicit ports dict with lanX attributes"""
        configs = dict(self.ports) if self.ports else {}

        # Merge lanX attributes
        if self.lan1:
            configs['LAN1'] = self.lan1
        if self.lan2:
            configs['LAN2'] = self.lan2
        if self.lan3:
            configs['LAN3'] = self.lan3
        if self.lan4:
            configs['LAN4'] = self.lan4
        if self.lan5:
            configs['LAN5'] = self.lan5

        return configs


@dataclass
class APPortResult:
    """Result of configuring ports on a single AP"""
    ap_identifier: str
    ap_id: Optional[str] = None
    ap_serial: Optional[str] = None
    ap_model: Optional[str] = None
    success: bool = False
    status: str = "pending"  # pending, success, failed, skipped, already_configured
    ports_configured: List[Dict] = field(default_factory=list)
    ports_already_correct: List[Dict] = field(default_factory=list)
    ports_protected: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_uplink_port(model: str, port_id: str) -> bool:
    """Check if the given port is the uplink port for this model."""
    uplink = get_uplink_port(model)
    if not uplink:
        return False
    return port_id.upper() == uplink.upper()


def resolve_port_configs(
    port_configs: Dict[str, PortConfig],
    model: str,
    default_vlan: Optional[int] = None
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Process port configs and return list of ports to configure.

    Protects uplink ports from being disabled or having VLAN changed.

    Args:
        port_configs: Dict mapping port_id to PortConfig
        model: AP model to check for uplink port protection
        default_vlan: Default VLAN for 'match' mode

    Returns:
        Tuple of (ports_to_configure, protected_ports):
        - ports_to_configure: List of dicts with port_id, vlan, and action for each port
        - protected_ports: List of ports that were protected from changes (skipped)
    """
    ports_to_configure = []
    protected_ports = []
    uplink_port = get_uplink_port(model)

    for port_id, config in port_configs.items():
        port_id_upper = port_id.upper()
        mode = config.mode if isinstance(config.mode, PortMode) else PortMode(config.mode)

        # Check if this is the uplink port
        is_uplink = uplink_port and port_id_upper == uplink_port.upper()

        if mode == PortMode.UPLINK:
            # Explicitly marked as uplink - skip
            protected_ports.append({
                'port_id': port_id_upper,
                'requested_action': 'skip',
                'reason': 'uplink_port',
                'is_uplink': True
            })
            logger.debug(f"    SKIP: {port_id_upper} is marked as uplink - not configurable")

        elif mode == PortMode.IGNORE:
            # Ignore - don't make any changes to this port
            logger.debug(f"    IGNORE: {port_id_upper} - no changes requested")

        elif mode == PortMode.MATCH:
            # Use default VLAN
            if default_vlan is None:
                logger.warning(f"    SKIP: {port_id_upper} mode is 'match' but no default_vlan provided")
                continue
            ports_to_configure.append({
                'port_id': port_id_upper,
                'vlan': default_vlan,
                'action': 'configure',
                'is_uplink': is_uplink
            })

        elif mode == PortMode.SPECIFIC:
            # Use the specified VLAN
            specific_vlan = config.vlan or 1
            ports_to_configure.append({
                'port_id': port_id_upper,
                'vlan': specific_vlan,
                'action': 'configure',
                'is_uplink': is_uplink
            })

        elif mode == PortMode.DISABLE:
            # Protect uplink from being disabled
            if is_uplink:
                protected_ports.append({
                    'port_id': port_id_upper,
                    'requested_action': 'disable',
                    'reason': 'uplink_protected',
                    'is_uplink': True
                })
                logger.warning(f"    PROTECTED: {port_id_upper} is the uplink port for {model} - cannot disable")
            else:
                # Mark for disabling (non-uplink ports only)
                ports_to_configure.append({
                    'port_id': port_id_upper,
                    'vlan': None,
                    'action': 'disable',
                    'is_uplink': False
                })

    return ports_to_configure, protected_ports


async def configure_single_ap(
    r1_client,
    tenant_id: str,
    venue_id: str,
    ap: Dict[str, Any],
    port_configs: Dict[str, PortConfig],
    default_profile_id: str,
    default_vlan: Optional[int] = None,
    dry_run: bool = False
) -> APPortResult:
    """
    Configure LAN ports on a single AP.

    Args:
        r1_client: RuckusONE API client
        tenant_id: Tenant ID
        venue_id: Venue ID
        ap: AP data dict (must have serialNumber, model, name)
        port_configs: Dict mapping port_id to PortConfig
        default_profile_id: ID of the Default ACCESS Port profile
        default_vlan: Default VLAN for 'match' mode
        dry_run: If True, don't actually make changes

    Returns:
        APPortResult with configuration results
    """
    serial = ap.get('serialNumber')
    model = ap.get('model', '')
    ap_name = ap.get('name', serial)
    ap_id = ap.get('id')

    result = APPortResult(
        ap_identifier=ap_name,
        ap_id=ap_id,
        ap_serial=serial,
        ap_model=model
    )

    # Check if this AP has configurable LAN ports
    if not has_configurable_lan_ports(model):
        result.status = 'skipped'
        result.skipped_reason = f"Model {model} has no configurable LAN ports"
        logger.debug(f"  Skipping {ap_name} ({model}) - no configurable LAN ports")
        return result

    # Resolve port configs with uplink protection
    ports_to_configure, protected_ports = resolve_port_configs(
        port_configs, model, default_vlan
    )

    result.ports_protected = protected_ports

    if not ports_to_configure:
        result.status = 'skipped'
        result.skipped_reason = 'No ports to configure'
        logger.debug(f"  Skipping {ap_name} - no ports to configure")
        return result

    # Idempotency check: fetch current port settings
    current_port_settings = {}
    try:
        port_settings_response = await r1_client.venues.get_ap_all_lan_port_settings(
            tenant_id=tenant_id,
            venue_id=venue_id,
            serial_number=serial,
            model=model
        )
        # Response has 'ports' array: [{'portId': 'LAN1', 'untagId': 3001, 'enabled': True}, ...]
        # untagId comes from r1api normalization, but check overwriteUntagId too for safety
        ports_array = port_settings_response.get('ports', [])
        for port_data in ports_array:
            port_id = port_data.get('portId', '')
            if port_id:
                # Check both untagId (normalized) and overwriteUntagId (raw)
                vlan = port_data.get('untagId') or port_data.get('overwriteUntagId')
                port_type = port_data.get('type') or port_data.get('overwriteType') or 'ACCESS'
                current_port_settings[port_id] = {
                    'vlan': vlan,
                    'type': port_type,
                    'enabled': port_data.get('enabled', True)
                }
        logger.debug(f"  Parsed current port settings for {ap_name}: {current_port_settings}")
    except Exception as e:
        logger.debug(f"  Could not fetch current port settings for {ap_name}: {str(e)}")

    # Filter ports that need changes
    ports_needing_changes = []
    ports_already_correct = []

    for port_config in ports_to_configure:
        port_id = port_config['port_id']
        current = current_port_settings.get(port_id, {})

        if port_config['action'] == 'configure':
            target_vlan = port_config['vlan']
            current_vlan = current.get('vlan')
            current_type = current.get('type', '').upper() if current.get('type') else ''

            # Convert both to int for comparison (API may return string)
            try:
                current_vlan_int = int(current_vlan) if current_vlan is not None else None
            except (ValueError, TypeError):
                current_vlan_int = None

            logger.debug(f"    {port_id}: current_vlan={current_vlan} (as int: {current_vlan_int}), target_vlan={target_vlan}, current_type={current_type}")

            # Check if already correct: ACCESS type with matching VLAN
            if current_type == 'ACCESS' and current_vlan_int == target_vlan:
                ports_already_correct.append(port_config)
                logger.info(f"    {port_id} already VLAN {target_vlan} (ACCESS) - skipping")
            else:
                ports_needing_changes.append(port_config)
                logger.debug(f"    {port_id} needs change: type={current_type}, vlan {current_vlan_int} -> {target_vlan}")

        elif port_config['action'] == 'disable':
            current_enabled = current.get('enabled', True)
            logger.debug(f"    {port_id}: current_enabled={current_enabled}")

            if not current_enabled:
                ports_already_correct.append(port_config)
                logger.info(f"    {port_id} already disabled - skipping")
            else:
                ports_needing_changes.append(port_config)

    result.ports_already_correct = [{'port_id': p['port_id'], 'vlan': p.get('vlan')} for p in ports_already_correct]

    # If all ports already correct, we're done
    if not ports_needing_changes:
        result.status = 'already_configured'
        result.success = True
        logger.info(f"  {ap_name} - all {len(ports_already_correct)} ports already configured correctly")
        return result

    # Dry run - don't actually make changes
    if dry_run:
        result.status = 'dry_run'
        result.success = True
        result.ports_configured = [
            {'port_id': p['port_id'], 'vlan': p.get('vlan'), 'action': p['action']}
            for p in ports_needing_changes
        ]
        return result

    # Apply changes
    try:
        # Step 1: Disable venue settings inheritance for this AP's LAN ports
        await r1_client.venues.set_ap_lan_port_specific_settings(
            tenant_id=tenant_id,
            venue_id=venue_id,
            serial_number=serial,
            use_venue_settings=False,
            wait_for_completion=True
        )

        # Step 2: Configure each port
        configured_ports = []
        for port_config in ports_needing_changes:
            port_id = port_config['port_id']

            if port_config['action'] == 'configure':
                port_vlan = port_config['vlan']

                # First, activate the default ACCESS profile on the port
                await r1_client.ethernet_port_profiles.activate_profile_on_ap_lan_port(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    port_id=port_id,
                    profile_id=default_profile_id,
                    wait_for_completion=True
                )

                # Then set the VLAN override
                await r1_client.venues.set_ap_lan_port_settings(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    port_id=port_id,
                    untagged_vlan=port_vlan,
                    wait_for_completion=True
                )

                configured_ports.append({
                    'port_id': port_id,
                    'vlan': port_vlan,
                    'action': 'configure'
                })

            elif port_config['action'] == 'disable':
                await r1_client.venues.set_ap_lan_port_enabled(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    port_id=port_id,
                    enabled=False,
                    wait_for_completion=True
                )

                configured_ports.append({
                    'port_id': port_id,
                    'action': 'disable'
                })

        result.ports_configured = configured_ports
        result.status = 'success'
        result.success = True

        logger.info(f"  {ap_name}: Configured {len(configured_ports)} ports")

    except Exception as e:
        result.status = 'failed'
        result.success = False
        result.errors.append(str(e))
        logger.error(f"  Failed to configure {ap_name}: {str(e)}")

    return result


async def configure_ap_ports(
    r1_client,
    venue_id: str,
    tenant_id: str,
    ap_configs: List[APPortRequest],
    dry_run: bool = False,
    emit_message: Optional[callable] = None
) -> Dict[str, Any]:
    """
    Configure LAN ports on multiple APs.

    Args:
        r1_client: RuckusONE API client
        venue_id: Venue ID
        tenant_id: Tenant ID
        ap_configs: List of APPortRequest objects
        dry_run: If True, don't actually make changes
        emit_message: Optional callback for status messages

    Returns:
        Dict with results:
        {
            'configured': [...],
            'already_configured': [...],
            'failed': [...],
            'skipped': [...],
            'summary': {...},
            'dry_run': bool
        }
    """
    async def _emit(message: str, level: str = "info"):
        if emit_message:
            await emit_message(message, level)
        logger.info(message) if level != "error" else logger.error(message)

    await _emit(f"Configuring LAN ports on {len(ap_configs)} APs...")

    # Step 1: Fetch all APs in venue to get model information
    all_aps = []
    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
        logger.info(f"  Found {len(all_aps)} total APs in venue")
    except Exception as e:
        await _emit(f"Failed to fetch APs: {str(e)}", "error")
        return {
            'error': str(e),
            'configured': [],
            'already_configured': [],
            'failed': [],
            'skipped': [],
            'dry_run': dry_run
        }

    # Build AP lookup by name and serial
    ap_lookup_by_name = {ap.get('name', '').lower(): ap for ap in all_aps}
    ap_lookup_by_serial = {ap.get('serialNumber', '').upper(): ap for ap in all_aps}

    # Step 2: Find the Default ACCESS Port profile
    default_profile = await r1_client.ethernet_port_profiles.find_default_access_profile(tenant_id=tenant_id)

    if not default_profile:
        await _emit("Could not find 'Default ACCESS Port' profile", "error")
        return {
            'error': 'Could not find Default ACCESS Port profile',
            'configured': [],
            'already_configured': [],
            'failed': [],
            'skipped': [],
            'dry_run': dry_run
        }

    default_profile_id = default_profile.get('id')
    logger.info(f"  Using default ACCESS profile: {default_profile.get('name')}")

    # Step 3: Process each AP config
    results = {
        'configured': [],
        'already_configured': [],
        'failed': [],
        'skipped': [],
        'dry_run': dry_run,
        'default_profile_id': default_profile_id,
        'default_profile_name': default_profile.get('name')
    }

    for ap_config in ap_configs:
        identifier = ap_config.ap_identifier

        # Find AP by name or serial
        ap = ap_lookup_by_name.get(identifier.lower()) or ap_lookup_by_serial.get(identifier.upper())

        if not ap:
            result = APPortResult(
                ap_identifier=identifier,
                status='skipped',
                skipped_reason=f"AP '{identifier}' not found in venue"
            )
            results['skipped'].append(result.to_dict())
            logger.warning(f"  AP '{identifier}' not found in venue")
            continue

        # Get port configs from the request
        port_configs = ap_config.get_port_configs()

        if not port_configs:
            result = APPortResult(
                ap_identifier=identifier,
                ap_serial=ap.get('serialNumber'),
                ap_model=ap.get('model'),
                status='skipped',
                skipped_reason='No port configurations specified'
            )
            results['skipped'].append(result.to_dict())
            continue

        # Configure the AP
        result = await configure_single_ap(
            r1_client=r1_client,
            tenant_id=tenant_id,
            venue_id=venue_id,
            ap=ap,
            port_configs=port_configs,
            default_profile_id=default_profile_id,
            dry_run=dry_run
        )

        # Categorize result
        if result.status == 'success' or result.status == 'dry_run':
            results['configured'].append(result.to_dict())
        elif result.status == 'already_configured':
            results['already_configured'].append(result.to_dict())
        elif result.status == 'failed':
            results['failed'].append(result.to_dict())
        else:
            results['skipped'].append(result.to_dict())

    # Build summary
    results['summary'] = {
        'total_requested': len(ap_configs),
        'configured': len(results['configured']),
        'already_configured': len(results['already_configured']),
        'failed': len(results['failed']),
        'skipped': len(results['skipped'])
    }

    summary_parts = []
    if results['summary']['configured'] > 0:
        summary_parts.append(f"{results['summary']['configured']} configured")
    if results['summary']['already_configured'] > 0:
        summary_parts.append(f"{results['summary']['already_configured']} already correct")
    if results['summary']['failed'] > 0:
        summary_parts.append(f"{results['summary']['failed']} failed")
    if results['summary']['skipped'] > 0:
        summary_parts.append(f"{results['summary']['skipped']} skipped")

    summary_str = ", ".join(summary_parts) if summary_parts else "no changes"
    await _emit(f"LAN port configuration complete: {summary_str}")

    return results


async def audit_ap_ports(
    r1_client,
    venue_id: str,
    tenant_id: str,
    ap_identifiers: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Audit current port configurations for APs in venue.

    Args:
        r1_client: RuckusONE API client
        venue_id: Venue ID
        tenant_id: Tenant ID
        ap_identifiers: Optional list of AP names/serials. If None, audits all APs.

    Returns:
        Dict with audit results per AP
    """
    # Fetch all APs in venue
    try:
        aps_response = await r1_client.venues.get_aps_by_tenant_venue(tenant_id, venue_id)
        all_aps = aps_response.get('data', [])
    except Exception as e:
        return {'error': str(e), 'aps': []}

    # Filter to requested APs if specified
    if ap_identifiers:
        identifier_set = {id.lower() for id in ap_identifiers}
        serial_set = {id.upper() for id in ap_identifiers}
        all_aps = [
            ap for ap in all_aps
            if ap.get('name', '').lower() in identifier_set
            or ap.get('serialNumber', '').upper() in serial_set
        ]

    audit_results = []

    for ap in all_aps:
        serial = ap.get('serialNumber')
        model = ap.get('model', '')
        ap_name = ap.get('name', serial)

        model_info = get_model_info(model)

        ap_audit = {
            'ap_name': ap_name,
            'serial': serial,
            'model': model,
            'has_configurable_ports': model_info['has_lan_ports'],
            'uplink_port': model_info['uplink_port'],
            'configurable_ports': model_info['configurable_ports'],
            'port_settings': {}
        }

        if model_info['has_lan_ports']:
            try:
                port_settings = await r1_client.venues.get_ap_all_lan_port_settings(
                    tenant_id=tenant_id,
                    venue_id=venue_id,
                    serial_number=serial,
                    model=model
                )

                for port_id, settings in port_settings.items():
                    if isinstance(settings, dict):
                        ap_audit['port_settings'][port_id] = {
                            'vlan': settings.get('overwriteUntagId') or settings.get('untagId'),
                            'type': settings.get('overwriteType') or settings.get('type'),
                            'enabled': settings.get('enabled', True),
                            'is_uplink': port_id.upper() == model_info['uplink_port']
                        }
            except Exception as e:
                ap_audit['error'] = str(e)

        audit_results.append(ap_audit)

    return {
        'venue_id': venue_id,
        'total_aps': len(audit_results),
        'aps_with_configurable_ports': sum(1 for a in audit_results if a['has_configurable_ports']),
        'aps': audit_results
    }


def get_port_metadata() -> Dict[str, Any]:
    """
    Return AP model port metadata for frontend.

    Returns:
        Dict with model port counts, uplink ports, and port modes
    """
    # Categorize models by uplink port for frontend display
    port_categories = {
        'lan1_uplink': [],  # 1-port models with LAN1 as uplink
        'lan2_uplink': [],  # 1-port models with LAN2 as uplink
        'lan3_uplink': [],  # 2-port models with LAN3 as uplink
        'lan5_uplink': [],  # 4-port models with LAN5 as uplink
    }

    for model, uplink in MODEL_UPLINK_PORTS.items():
        if uplink == 'LAN1':
            port_categories['lan1_uplink'].append(model)
        elif uplink == 'LAN2':
            port_categories['lan2_uplink'].append(model)
        elif uplink == 'LAN3':
            port_categories['lan3_uplink'].append(model)
        elif uplink == 'LAN5':
            port_categories['lan5_uplink'].append(model)

    return {
        'model_port_counts': MODEL_PORT_COUNTS,
        'model_uplink_ports': MODEL_UPLINK_PORTS,
        'port_modes': [m.value for m in PortMode],
        'port_mode_descriptions': {
            PortMode.IGNORE.value: "Don't change this port",
            PortMode.SPECIFIC.value: "Set specific VLAN",
            PortMode.MATCH.value: "Match reference VLAN",
            PortMode.DISABLE.value: "Disable the port",
            PortMode.UPLINK.value: "Uplink port (protected)"
        },
        'port_categories': port_categories
    }
