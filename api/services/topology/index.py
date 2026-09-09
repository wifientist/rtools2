"""
The lookups every evidence source shares, built once per correlation run.

Sources must not each walk 4700 ports to answer "whose MAC is this?". They also
must not each invent their own answer: one index means one definition of
identity, so two sources cannot disagree about which device a MAC belongs to and
silently produce two links where there is one.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .model import Device, Port
from .normalize import AliasIndex, norm_mac

logger = logging.getLogger(__name__)


class TopologyIndex:
    """Devices, ports and learned MACs, cross-referenced."""

    def __init__(self, devices: List[Device], ports: List[Port],
                 mac_rows: Optional[List[Dict]] = None):
        self.devices: Dict[str, Device] = {d.id: d for d in devices}
        self.ports: Dict[str, Port] = {p.id: p for p in ports}

        self.ports_by_device: Dict[str, List[Port]] = defaultdict(list)
        self.port_by_r1_id: Dict[str, Port] = {}
        for port in ports:
            self.ports_by_device[port.device_id].append(port)
            if port.r1_port_id:
                self.port_by_r1_id[port.r1_port_id] = port

        self.aliases = AliasIndex()
        self.switch_macs: Dict[str, str] = {}
        self.ap_macs: Dict[str, str] = {}
        for device in devices:
            names = [device.name]
            macs: List[Any] = [device.mac]
            serials: List[Any] = [device.serial]
            if device.stack:
                serials.append(device.stack.active_serial)
                for unit in device.stack.units:
                    macs.append(unit.get("switchMac"))
                    serials.append(unit.get("serialNumber"))
                    names.append(unit.get("unitName"))
            self.aliases.add_device(device.id, macs=macs, serials=serials,
                                    names=names, ips=[device.ip])
            if device.mac:
                if device.kind in ("switch", "stack"):
                    self.switch_macs[device.mac] = device.id
                elif device.kind == "ap":
                    self.ap_macs[device.mac] = device.id

        # Per-port MACs let an LLDP neighborPortMacAddress name a specific far
        # PORT rather than just the far chassis.
        for port in ports:
            if port.port_mac:
                self.aliases.add_port_mac(port.port_mac, port.id)

        # Learned MACs, per port and per MAC. A device's MAC appearing on
        # exactly one port is adjacency evidence; on several, it is a
        # contradiction.
        self.macs_on_port: Dict[str, List[Dict]] = defaultdict(list)
        self.ports_for_mac: Dict[str, List[str]] = defaultdict(list)
        for row in (mac_rows or []):
            port = self.port_by_r1_id.get(str(row.get("switchPortId") or ""))
            if port is None:
                continue
            self.macs_on_port[port.id].append(row)
            mac = norm_mac(row.get("clientMac"))
            if mac and port.id not in self.ports_for_mac[mac]:
                self.ports_for_mac[mac].append(port.id)

    # ── convenience ─────────────────────────────────────────────────────────

    def device(self, device_id: str) -> Optional[Device]:
        return self.devices.get(device_id)

    def port(self, port_id: Optional[str]) -> Optional[Port]:
        return self.ports.get(port_id) if port_id else None

    def device_of_mac(self, mac: Any) -> Optional[str]:
        """Which managed device owns this MAC, if any."""
        normalized = norm_mac(mac)
        if not normalized:
            return None
        return (self.switch_macs.get(normalized)
                or self.ap_macs.get(normalized)
                or self.aliases.by_mac.get(normalized))

    def is_managed_switch(self, mac: Any) -> bool:
        return norm_mac(mac) in self.switch_macs

    def infra_ports(self, device_id: str) -> List[Port]:
        """A device's ports that face other infrastructure, not endpoints."""
        return [p for p in self.ports_by_device.get(device_id, [])
                if p.neighbor_mac and self.device_of_mac(p.neighbor_mac)]

    def resolve_far_port(self, port: Port) -> Optional[str]:
        """
        The far end of `port`'s LLDP link, when the per-port MAC identifies it.

        MEASURED TRAP: `neighborPortMacAddress` usually just echoes
        `neighborMacAddress` -- the far chassis MAC, not a port MAC. Looking an
        echo up in the port-MAC index finds the far switch's port 1/1/1, because
        that port's MAC *is* the chassis MAC, and every such link would be drawn
        landing on 1/1/1.

        On the probe venue exactly 8 of 221 switch-facing ports echoed, and all
        8 of the 1/1/1 resolutions were those 8. So the echo is precisely
        detectable, and refusing it costs nothing: those links are still
        resolved by pairing the two ends' own observations in merge.py.
        """
        if not port.neighbor_port_mac:
            return None
        if port.neighbor_port_mac == port.neighbor_mac:
            return None                     # chassis echo -- not a port MAC
        far_port_id = self.aliases.resolve_port(port.neighbor_port_mac)
        if far_port_id is None:
            return None
        far_port = self.ports.get(far_port_id)
        if far_port is None:
            return None
        # It must belong to the device the neighbour MAC named, or the index
        # found something unrelated that happens to share a MAC.
        expected = self.device_of_mac(port.neighbor_mac)
        if expected and far_port.device_id != expected:
            return None
        return far_port_id

    def stats(self) -> Dict[str, int]:
        return {
            "devices": len(self.devices),
            "ports": len(self.ports),
            "switches": len(self.switch_macs),
            "aps": len(self.ap_macs),
            "portsWithMacs": len(self.macs_on_port),
            "aliasCollisions": len(self.aliases.collisions),
        }
