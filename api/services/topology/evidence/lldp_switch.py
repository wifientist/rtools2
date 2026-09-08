"""
LLDP as reported on switch port rows.

This is the backbone of the wired map. On the probe venue, 2997 of 4746 ports
carried a neighbour MAC: 221 named another managed switch and 2757 named a
managed AP, with only 19 unresolved.

Two measured facts shape the code:

  * Switch-to-switch LLDP is essentially always mutual -- 220 of 221 ports had
    the far switch naming them back. So the far PORT is knowable even when the
    near side does not name it: the two one-sided observations pair up in
    merge.py. That is why this source is happy to emit a device-level claim and
    let merging finish the job.

  * `neighborName` is truncated to 32 characters on 65% of ports, but only 2 of
    1915 distinct prefixes matched more than one managed device. Name resolution
    is therefore safe *provided* ambiguity is checked -- which is why an
    ambiguous prefix emits a contradiction instead of a guess.
"""

from typing import Iterator

from ..model import Endpoint, Evidence
from ..normalize import mac_display
from . import Claim, source


def _endpoint(index, device_id, port_id=None, via="", **discovered):
    port = index.port(port_id)
    return Endpoint(device_id=device_id, port_id=port_id,
                    ident=port.ident if port else None,
                    resolved_via=via,
                    discovered_as={k: v for k, v in discovered.items() if v})


@source("lldp.switch.mac", "LLDP neighbour by MAC",
        emits=["lldp.switch.mac", "lldp.switch.portmac"],
        tier_cap="strong", weight=2.0,
        proves="A port's LLDP neighbour MAC names a managed device",
        reads="switchPorts/query: neighborMacAddress, neighborPortMacAddress",
        caveats="One-sided on its own. Two sides naming each other is what "
                "reaches 'confirmed', and that promotion happens in merge.")
def lldp_by_mac(bundle, index) -> Iterator[Claim]:
    """A port's LLDP neighbour MAC resolves to a device we manage."""
    for port in index.ports.values():
        if not port.neighbor_mac:
            continue
        far_device_id = index.device_of_mac(port.neighbor_mac)
        if not far_device_id or far_device_id == port.device_id:
            continue                        # unmanaged peer, or the switch itself

        far_port_id = index.resolve_far_port(port)
        near = _endpoint(index, port.device_id, port.id, "self")
        far = _endpoint(index, far_device_id, far_port_id,
                        "port-mac" if far_port_id else "mac",
                        neighborName=port.neighbor_name,
                        neighborMac=mac_display(port.neighbor_mac),
                        neighborPortMac=mac_display(port.neighbor_port_mac))

        far_device = index.device(far_device_id)
        near_device = index.device(port.device_id)
        near_name = near_device.display_name if near_device else port.device_id
        far_name = (far_device.display_name if far_device
                    else mac_display(port.neighbor_mac))
        # Only a formed stack link is a stack link. Keying off the capability
        # flag labelled live uplinks on stacking-capable ports as stack cables.
        kind = "stack" if port.stack_peer_ident else "ethernet"

        evidence = [Evidence(
            source="lldp.switch.mac", kind="support",
            claim=f"{near_name} port {port.ident} reports an LLDP neighbour whose "
                  f"MAC is {far_name}.",
            a=port.id, b=far_port_id or f"dev:{far_device_id}",
            fields={"nearPort": port.ident,
                    "neighborName": port.neighbor_name,
                    "neighborMac": mac_display(port.neighbor_mac),
                    "neighborPortMac": mac_display(port.neighbor_port_mac),
                    "portSpeed": port.speed_mbps, "operUp": port.oper_up},
        )]
        if far_port_id:
            # The per-port MAC differed from the chassis MAC, so it genuinely
            # identifies a far port rather than echoing the chassis.
            evidence.append(Evidence(
                source="lldp.switch.portmac", kind="support",
                claim=f"The neighbour's port MAC identifies far port "
                      f"{index.port(far_port_id).ident}.",
                a=port.id, b=far_port_id,
                fields={"neighborPortMac": mac_display(port.neighbor_port_mac)},
            ))
        yield Claim(a=near, b=far, kind=kind, evidence=evidence,
                    channel="switch-lldp", observer=port.device_id)


@source("lldp.switch.name", "LLDP neighbour by name",
        emits=["lldp.switch.name", "lldp.switch.name.ambiguous"],
        tier_cap="probable", weight=1.8,
        proves="A port's LLDP neighbour name uniquely matches a managed device",
        reads="switchPorts/query: neighborName",
        caveats="LLDP truncates sysName at 32 chars (65% of names on the probe "
                "venue). Only used when the prefix matches exactly one device; "
                "an ambiguous prefix emits a contradiction instead.")
def lldp_by_name(bundle, index) -> Iterator[Claim]:
    """
    A neighbour name resolves where the MAC did not.

    Weighted higher than first planned: prefix ambiguity measured 2 in 1915,
    because this estate's naming convention front-loads its entropy.
    """
    for port in index.ports.values():
        if not port.neighbor_name:
            continue
        if port.neighbor_mac and index.device_of_mac(port.neighbor_mac):
            continue                        # the MAC path already covered it

        near_device = index.device(port.device_id)
        near_name = near_device.display_name if near_device else port.device_id
        resolution = index.aliases.resolve(name=port.neighbor_name)
        if resolution.ambiguous:
            # Say why the link stayed weak rather than picking one.
            yield Claim(
                a=_endpoint(index, port.device_id, port.id, "self"),
                b=_endpoint(index, resolution.ambiguous[0], None, "unresolved",
                            neighborName=port.neighbor_name),
                evidence=[Evidence(
                    source="lldp.switch.name.ambiguous", kind="contradict",
                    claim=f"The truncated neighbour name "
                          f"'{port.neighbor_name}' matches "
                          f"{len(resolution.ambiguous)} managed devices, so it "
                          f"cannot identify one.",
                    a=port.id, b=f"dev:{resolution.ambiguous[0]}",
                    fields={"neighborName": port.neighbor_name,
                            "candidates": resolution.ambiguous[:10]},
                )])
            continue
        if not resolution or resolution.device_id == port.device_id:
            continue

        truncated = len(port.neighbor_name) == 32
        yield Claim(
            a=_endpoint(index, port.device_id, port.id, "self"),
            b=_endpoint(index, resolution.device_id, None, resolution.via,
                        neighborName=port.neighbor_name),
            kind="stack" if port.stack_peer_ident else "ethernet",
            channel="switch-lldp", observer=port.device_id,
            evidence=[Evidence(
                source="lldp.switch.name", kind="support",
                claim=f"{near_name} port {port.ident} reports LLDP neighbour "
                      f"'{port.neighbor_name}'"
                      + (", matched on its truncated 32-character prefix."
                         if truncated else ", matched exactly."),
                a=port.id, b=f"dev:{resolution.device_id}",
                fields={"nearPort": port.ident, "neighborName": port.neighbor_name,
                        "matchedVia": resolution.via, "truncated": truncated},
            )])


@source("lldp.unmanaged", "LLDP neighbour we do not manage",
        tier_cap="probable", weight=1.5,
        proves="A port faces a real device that is not in R1 inventory",
        reads="switchPorts/query: neighborName, neighborMacAddress",
        caveats="Creates an 'external' node. A foreign OUI here is the strongest "
                "cheap signal of a WAN edge or an unmanaged switch.")
def lldp_unmanaged(bundle, index) -> Iterator[Claim]:
    """
    An LLDP neighbour that resolves to nothing we manage.

    Worth a node rather than silence: these are firewalls, routers, third-party
    switches and the WAN edge -- exactly the things a map is missing when it
    only draws what the controller owns.
    """
    for port in index.ports.values():
        if not (port.neighbor_mac or port.neighbor_name):
            continue
        if port.neighbor_mac and index.device_of_mac(port.neighbor_mac):
            continue
        if port.neighbor_name and index.aliases.resolve(name=port.neighbor_name):
            continue

        if port.neighbor_mac:
            external_id = f"ext:mac:{port.neighbor_mac}"
            label = port.neighbor_name or mac_display(port.neighbor_mac)
        else:
            external_id = f"ext:name:{port.neighbor_name.strip().casefold()}"
            label = port.neighbor_name
        foreign = index.aliases.is_foreign_oui(port.neighbor_mac)
        near_device = index.device(port.device_id)
        near_name = near_device.display_name if near_device else port.device_id

        yield Claim(
            a=_endpoint(index, port.device_id, port.id, "self"),
            b=Endpoint(device_id=external_id, resolved_via="unmanaged",
                       discovered_as={"name": port.neighbor_name,
                                      "mac": mac_display(port.neighbor_mac),
                                      "foreignOui": foreign}),
            channel="switch-lldp", observer=port.device_id,
            evidence=[Evidence(
                source="lldp.unmanaged", kind="support",
                claim=f"{near_name} port {port.ident} sees '{label}', which is not "
                      f"a device "
                      f"R1 manages"
                      + (" and whose vendor prefix is used by nothing in this "
                         "tenant." if foreign else "."),
                a=port.id, b=f"dev:{external_id}",
                fields={"nearPort": port.ident, "neighborName": port.neighbor_name,
                        "neighborMac": mac_display(port.neighbor_mac),
                        "foreignOui": foreign, "macCount": port.mac_count},
            )])
