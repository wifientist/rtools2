"""
V2 Phase: Configure LAN Ports

Configures LAN port VLANs on APs with configurable LAN ports to match unit VLANs.

Uses the shared ap_port_config service which handles:
1. Finding the existing "Default ACCESS Port" profile (built-in to every venue)
2. Disabling venue settings inheritance for each AP's LAN ports
3. Setting VLAN overrides on each port
4. Activating the default ACCESS profile on the port

This phase is optional and non-critical - the workflow succeeds even if
LAN port configuration fails.
"""

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation

# Import shared service
from services.ap_port_config import (
    configure_ap_ports,
    APPortRequest,
    PortConfig,
    PortMode,
)

# Import centralized AP model metadata
from r1api.models import (
    has_configurable_lan_ports as has_configurable_ports,
    get_port_count,
    get_uplink_port,
)

logger = logging.getLogger(__name__)

# Default port configurations per model type
DEFAULT_MODEL_PORT_CONFIGS = {
    'one_port_lan1_uplink': [{'mode': 'uplink'}, {'mode': 'ignore'}],
    'one_port_lan2_uplink': [{'mode': 'ignore'}, {'mode': 'uplink'}],
    'two_port': [{'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'uplink'}],
    'four_port': [
        {'mode': 'ignore'}, {'mode': 'ignore'}, {'mode': 'ignore'},
        {'mode': 'ignore'}, {'mode': 'uplink'}
    ],
}


@register_phase("configure_lan_ports", "Configure LAN Ports")
class ConfigureLANPortsPhase(PhaseExecutor):
    """
    Configure LAN ports on APs assigned to this unit.

    Finds APs in this unit's AP group that have configurable LAN ports,
    and sets their VLAN to match the unit's VLAN.

    Non-critical: workflow succeeds even if this phase fails.
    """

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        default_vlan: str = "1"
        ap_serial_numbers: List[str] = Field(default_factory=list)
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)
        model_port_configs: Optional[Dict[str, List]] = None

    class Outputs(BaseModel):
        configured_aps: int = 0
        already_configured_aps: int = 0
        failed_aps: int = 0
        skipped_aps: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        """Configure LAN ports on this unit's APs."""
        try:
            vlan_id = int(inputs.default_vlan)
        except ValueError:
            logger.warning(
                f"[{inputs.unit_number}] Invalid VLAN '{inputs.default_vlan}', "
                f"skipping LAN port config"
            )
            return self.Outputs()

        model_port_configs = inputs.model_port_configs or DEFAULT_MODEL_PORT_CONFIGS

        # Build lookup for this unit's APs
        ap_lookup_by_serial = {
            ap.get('serialNumber', '').upper(): ap
            for ap in inputs.all_venue_aps
        }
        ap_lookup_by_name = {
            ap.get('name', '').lower(): ap
            for ap in inputs.all_venue_aps
        }

        # Find this unit's APs that have configurable ports
        ap_requests: List[APPortRequest] = []

        for identifier in inputs.ap_serial_numbers:
            ap = (
                ap_lookup_by_serial.get(identifier.upper())
                or ap_lookup_by_name.get(identifier.lower())
            )
            if not ap:
                logger.debug(
                    f"[{inputs.unit_number}] AP '{identifier}' not found in venue"
                )
                continue

            model = ap.get('model', '')
            if not has_configurable_ports(model):
                logger.debug(
                    f"[{inputs.unit_number}] Skipping {ap.get('name')} "
                    f"({model}) - no configurable LAN ports"
                )
                continue

            port_configs_for_model = self._get_port_configs(
                model_port_configs, model
            )
            if not port_configs_for_model:
                continue

            port_configs = self._build_port_configs(
                port_configs_for_model, vlan_id
            )

            ap_requests.append(APPortRequest(
                ap_identifier=ap.get('name') or identifier,
                ports=port_configs,
            ))

        if not ap_requests:
            logger.info(
                f"[{inputs.unit_number}] No APs with configurable "
                f"LAN ports to configure"
            )
            await self.emit(
                f"[{inputs.unit_number}] No APs with configurable LAN ports"
            )
            return self.Outputs()

        await self.emit(
            f"[{inputs.unit_number}] Configuring LAN ports on "
            f"{len(ap_requests)} APs (VLAN {vlan_id})..."
        )

        # Call the shared service
        result = await configure_ap_ports(
            r1_client=self.r1_client,
            venue_id=self.venue_id,
            tenant_id=self.tenant_id,
            ap_configs=ap_requests,
            dry_run=False,
            emit_message=lambda msg, lvl, details=None: self.emit(msg, lvl, details),
        )

        summary = result.get('summary', {})
        configured = summary.get('configured', 0)
        already_configured = summary.get('already_configured', 0)
        failed = summary.get('failed', 0)
        skipped = summary.get('skipped', 0)

        # Build summary message
        parts = []
        if configured > 0:
            parts.append(f"{configured} configured")
        if already_configured > 0:
            parts.append(f"{already_configured} already correct")
        if failed > 0:
            parts.append(f"{failed} failed")
        if skipped > 0:
            parts.append(f"{skipped} skipped")
        summary_str = ", ".join(parts) if parts else "no changes"

        level = "success" if failed == 0 else "warning"
        await self.emit(
            f"[{inputs.unit_number}] LAN ports: {summary_str}", level
        )

        return self.Outputs(
            configured_aps=configured,
            already_configured_aps=already_configured,
            failed_aps=failed,
            skipped_aps=skipped,
        )

    def _get_port_configs(
        self,
        model_port_configs: Dict[str, List],
        model: str,
    ) -> List[Dict]:
        """Get the appropriate port config list for an AP model."""
        port_count = get_port_count(model)
        uplink_port = get_uplink_port(model)

        if port_count == 1:
            if uplink_port == 'LAN2':
                return model_port_configs.get('one_port_lan2_uplink', [])
            return model_port_configs.get('one_port_lan1_uplink', [])
        elif port_count == 2:
            return model_port_configs.get('two_port', [])
        elif port_count == 4:
            return model_port_configs.get('four_port', [])
        return []

    def _build_port_configs(
        self,
        model_port_configs: List[Dict],
        default_vlan: int,
    ) -> Dict[str, PortConfig]:
        """Convert model port config to shared service format."""
        port_configs = {}

        for idx, config in enumerate(model_port_configs):
            port_id = f"LAN{idx + 1}"
            mode_str = config.get('mode', 'ignore')

            if mode_str == 'uplink':
                port_configs[port_id] = PortConfig(mode=PortMode.UPLINK)
            elif mode_str == 'ignore':
                port_configs[port_id] = PortConfig(mode=PortMode.IGNORE)
            elif mode_str == 'match':
                port_configs[port_id] = PortConfig(
                    mode=PortMode.SPECIFIC, vlan=default_vlan
                )
            elif mode_str == 'specific':
                port_configs[port_id] = PortConfig(
                    mode=PortMode.SPECIFIC,
                    vlan=config.get('vlan', 1),
                )
            elif mode_str == 'disable':
                port_configs[port_id] = PortConfig(mode=PortMode.DISABLE)

        return port_configs

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        """Estimate LAN port configuration work."""
        configurable_count = 0
        for identifier in inputs.ap_serial_numbers:
            for ap in inputs.all_venue_aps:
                if (
                    ap.get('serialNumber') == identifier
                    or ap.get('name') == identifier
                ):
                    if has_configurable_ports(ap.get('model', '')):
                        configurable_count += 1
                    break

        if configurable_count == 0:
            return PhaseValidation(
                valid=True,
                will_create=False,
                estimated_api_calls=0,
                notes=["No APs with configurable LAN ports"],
            )

        return PhaseValidation(
            valid=True,
            will_create=True,
            estimated_api_calls=configurable_count * 3,
            notes=[f"{configurable_count} APs with configurable LAN ports"],
        )
