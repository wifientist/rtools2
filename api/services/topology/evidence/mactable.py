"""
Evidence from the switch MAC forwarding table.

The MAC table answers a different question from LLDP: not "who says they are
next to me" but "whose traffic comes out of this port". That makes it the only
source that sees devices which do not speak LLDP at all -- and the source most
easily fooled, because an unmanaged switch in the middle makes two devices look
adjacent when they are two hops apart.

So the ceiling here is deliberately `probable`. No amount of MAC-table evidence
alone may draw a `confirmed` infrastructure link; only mutual LLDP, a structural
fact, or a human can do that.

`isRuckusAP` is not used: probing showed R1 silently drops the field. APs are
identified by joining the learned MAC against AP inventory, which resolved 2757
of 2788 APs -- better than the flag would have been anyway.
"""

from typing import Iterator

from ..model import Endpoint, Evidence
from ..normalize import mac_display
from . import Claim, source

# A port carrying at least this many learned MACs is carrying traffic for more
# network behind it, not for one attached device. Chosen conservatively: on the
# probe venue 2916 of 4746 ports had learned MACs and the overwhelming majority
# had exactly one, so the tail above this is genuinely different in kind.
TRUNK_MAC_THRESHOLD = 8


@source("mac.single.managed", "Managed device's MAC on one port",
        tier_cap="strong", weight=1.8,
        proves="A managed device's MAC is learned on exactly one switch port",
        reads="venues/switches/clients/query joined to AP and switch inventory",
        caveats="An unmanaged switch in the path makes a two-hop device look "
                "adjacent, which is why this cannot reach 'confirmed' alone.")
def managed_mac_on_single_port(bundle, index) -> Iterator[Claim]:
    """A device we manage is learned on exactly one port of one switch."""
    for mac, port_ids in index.ports_for_mac.items():
        device_id = index.device_of_mac(mac)
        if not device_id:
            continue
        if len(port_ids) != 1:
            continue                        # handled by the contradiction source
        port = index.port(port_ids[0])
        if port is None or port.device_id == device_id:
            continue                        # the switch's own MAC on its own port

        far_device = index.device(device_id)
        # A dense port is carrying a network, not an endpoint -- the MAC being
        # there says nothing about what is directly attached.
        if port.mac_count >= TRUNK_MAC_THRESHOLD:
            continue

        yield Claim(
            a=Endpoint(device_id=port.device_id, port_id=port.id,
                       ident=port.ident, resolved_via="self"),
            b=Endpoint(device_id=device_id, resolved_via="mac-table",
                       discovered_as={"mac": mac_display(mac)}),
            channel="mac-table", observer=port.device_id,
            evidence=[Evidence(
                source="mac.single.managed", kind="support",
                claim=f"{far_device.display_name if far_device else mac_display(mac)}'s "
                      f"MAC is learned on port {port.ident} and nowhere else, and "
                      f"that port has {port.mac_count} address"
                      f"{'' if port.mac_count == 1 else 'es'} on it.",
                a=port.id, b=f"dev:{device_id}",
                fields={"mac": mac_display(mac), "port": port.ident,
                        "macCountOnPort": port.mac_count,
                        "deviceKind": far_device.kind if far_device else "unknown"},
            )])


@source("mac.device.multiport", "Same device's MAC on several ports",
        tier_cap="rejected", weight=-1.5,
        proves="A device's MAC appears on more than one port of a switch",
        reads="venues/switches/clients/query",
        caveats="Contradiction only. Usually a loop, a redundant path, or a MAC "
                "learned through an intermediate device.")
def mac_on_multiple_ports(bundle, index) -> Iterator[Claim]:
    """
    One managed device's MAC learned on several ports of the same switch.

    At most one of those ports can be the device's actual attachment, so each
    candidate is weakened rather than any one being chosen.
    """
    for mac, port_ids in index.ports_for_mac.items():
        device_id = index.device_of_mac(mac)
        if not device_id or len(port_ids) < 2:
            continue
        by_switch = {}
        for port_id in port_ids:
            port = index.port(port_id)
            if port is not None:
                by_switch.setdefault(port.device_id, []).append(port)
        for switch_id, ports in by_switch.items():
            if len(ports) < 2 or switch_id == device_id:
                continue
            idents = sorted(p.ident for p in ports)
            far_device = index.device(device_id)
            for port in ports:
                yield Claim(
                    a=Endpoint(device_id=port.device_id, port_id=port.id,
                               ident=port.ident, resolved_via="self"),
                    b=Endpoint(device_id=device_id, resolved_via="mac-table"),
                    evidence=[Evidence(
                        source="mac.device.multiport", kind="contradict",
                        claim=f"{far_device.display_name if far_device else mac_display(mac)}'s "
                              f"MAC is learned on {len(ports)} ports of this "
                              f"switch ({', '.join(idents)}), so at most one of "
                              f"them is where it is actually attached.",
                        a=port.id, b=f"dev:{device_id}",
                        fields={"mac": mac_display(mac), "ports": idents},
                    )])


@source("mac.multi.uplink", "Port carries many MACs",
        tier_cap="weak", weight=0.0,
        proves="A port leads toward more network, without naming a peer",
        reads="venues/switches/clients/query, counted per port",
        caveats="Context only, weight zero. It names no peer -- its job is to "
                "suppress endpoint claims on trunk ports and to feed WAN "
                "inference.")
def dense_port_is_an_uplink(bundle, index) -> Iterator[Claim]:
    """
    A port with many learned MACs faces more network.

    Emitted as context on the port itself rather than as a link: knowing a port
    is a trunk is useful even when nothing identifies what is on the other end.
    """
    for port in index.ports.values():
        if port.mac_count < TRUNK_MAC_THRESHOLD:
            continue
        yield Claim(
            a=Endpoint(device_id=port.device_id, port_id=port.id,
                       ident=port.ident, resolved_via="self"),
            b=Endpoint(device_id=f"ext:dense:{port.id}", resolved_via="inferred"),
            context_only=True,
            evidence=[Evidence(
                source="mac.multi.uplink", kind="context",
                claim=f"Port {port.ident} has {port.mac_count} learned MAC "
                      f"addresses on it, so it leads toward more network rather "
                      f"than to a single attached device.",
                a=port.id, b=None,
                fields={"port": port.ident, "macCount": port.mac_count,
                        "threshold": TRUNK_MAC_THRESHOLD,
                        "hasLldpNeighbour": bool(port.neighbor_mac)},
            )])
