"""
R1's own topology graph, as evidence.

`GET /venues/{v}/topologies` is the richest single read in the whole tool: on the
probe venue it returned 1106 nodes and 1093 edges with both endpoints' MAC, name
and serial, the switch-side port, the port's VLANs, link speed, PoE draw and a
link health status -- all 100% populated.

It is treated as ONE source among several rather than as the answer, for two
reasons:

  * it is almost certainly derived from the same switch LLDP the port rows carry,
    so its channel is `r1-derived` and it does NOT count toward mutual
    agreement. Counting R1's restatement of a fact as a second opinion is the
    same double-count that the AP self-report measurement exposed.
  * `correspondingPort` is populated on only ~9% of edges -- exactly the
    switch-to-switch subset, because an AP has a single uplink and only the
    switch end of an AP edge has a meaningful port. So most of its edges name
    one port, like every other one-sided source.

What it adds that nothing else does: `connectionStatus` (Good / Disconnected /
Degraded / Unknown), which is a genuine link-health signal, and the per-port
VLAN lists the v2 overlay needs.
"""

from typing import Dict, Iterator, Optional

from ..model import Endpoint, Evidence
from ..normalize import canon_port_ident, mac_display, norm_mac, parse_vlan_list
from . import Claim, source


def _resolve(index, node_id, mac, serial, name) -> Optional[str]:
    """
    An edge endpoint -> a device id.

    R1's node ids are already the identities this tool uses -- the AP serial for
    APs, the lowercase colon MAC for switches -- so the MAC and serial paths do
    almost all the work and no translation layer is needed.
    """
    resolution = index.aliases.resolve(mac=mac, serial=serial, name=name)
    if resolution:
        return resolution.device_id
    # Node id doubles as a serial (APs) or a MAC (switches).
    resolution = index.aliases.resolve(mac=node_id, serial=node_id)
    return resolution.device_id if resolution else None


def _port_id(index, device_id: str, raw_ident) -> Optional[str]:
    ident = canon_port_ident(raw_ident)
    if not ident:
        return None
    port_id = f"{device_id}#{ident}"
    return port_id if index.port(port_id) else None


@source("r1.topology.edge", "R1's own topology graph",
        emits=["r1.topology.edge", "r1.topology.edge.noports",
               "r1.topology.status.disconnected", "r1.topology.status.degraded"],
        proves="R1 itself reports a link between these two devices",
        reads="GET /venues/{venueId}/topologies?meshOnly=false",
        caveats="Rich and fully populated, but almost certainly derived from the "
                "same switch LLDP -- so it corroborates and never counts as an "
                "independent second opinion. correspondingPort is present on "
                "only the switch-to-switch ~9%.")
def r1_topology_edges(bundle, index) -> Iterator[Claim]:
    """Every wired edge R1's own topology endpoint reports."""
    for edge in getattr(bundle, "r1_edges", None) or []:
        from_mac = norm_mac(edge.get("fromMac"))
        to_mac = norm_mac(edge.get("toMac"))
        a_device = _resolve(index, edge.get("from"), from_mac,
                            edge.get("fromSerial"), edge.get("fromName"))
        b_device = _resolve(index, edge.get("to"), to_mac,
                            edge.get("toSerial"), edge.get("toName"))
        if not a_device or not b_device or a_device == b_device:
            continue

        # `connectedPort` belongs to the `from` end, `correspondingPort` to `to`.
        a_port = _port_id(index, a_device, edge.get("connectedPort"))
        b_port = _port_id(index, b_device, edge.get("correspondingPort"))

        a_device_obj = index.device(a_device)
        b_device_obj = index.device(b_device)
        a_name = a_device_obj.display_name if a_device_obj else edge.get("fromName")
        b_name = b_device_obj.display_name if b_device_obj else edge.get("toName")

        both_ports = bool(a_port and b_port)
        status = str(edge.get("connectionStatus") or "")
        speed = edge.get("linkSpeed")

        evidence = [Evidence(
            source="r1.topology.edge" if both_ports else "r1.topology.edge.noports",
            kind="support",
            claim=f"R1's own topology reports a {str(edge.get('connectionType') or 'wired').lower()} "
                  f"link from {a_name}"
                  + (f" port {canon_port_ident(edge.get('connectedPort'))}"
                     if edge.get("connectedPort") else "")
                  + f" to {b_name}"
                  + (f" port {canon_port_ident(edge.get('correspondingPort'))}"
                     if edge.get("correspondingPort") else "")
                  + (f", at {speed}." if speed else "."),
            a=a_port or f"dev:{a_device}", b=b_port or f"dev:{b_device}",
            fields={"connectionType": edge.get("connectionType"),
                    "connectionStatus": status,
                    "connectedPort": edge.get("connectedPort"),
                    "correspondingPort": edge.get("correspondingPort"),
                    "linkSpeed": speed,
                    "poeEnabled": edge.get("poeEnabled"),
                    "poeUsedMilliwatts": edge.get("poeUsed"),
                    "taggedVlans": parse_vlan_list(edge.get("connectedPortTaggedVlan")),
                    "untaggedVlan": edge.get("connectedPortUntaggedVlan")},
        )]

        # The health signal nothing else in the API provides.
        lowered = status.lower()
        if "disconnect" in lowered:
            evidence.append(Evidence(
                source="r1.topology.status.disconnected", kind="contradict",
                claim=f"R1 reports this link's status as '{status}' -- it is known "
                      f"to R1 but not currently up.",
                a=a_port or f"dev:{a_device}", b=b_port or f"dev:{b_device}",
                fields={"connectionStatus": status},
            ))
        elif "degrad" in lowered:
            evidence.append(Evidence(
                source="r1.topology.status.degraded", kind="contradict",
                claim=f"R1 reports this link as '{status}'.",
                a=a_port or f"dev:{a_device}", b=b_port or f"dev:{b_device}",
                fields={"connectionStatus": status},
            ))

        yield Claim(
            a=Endpoint(device_id=a_device, port_id=a_port,
                       ident=canon_port_ident(edge.get("connectedPort")) or None,
                       resolved_via="r1-topology",
                       discovered_as={"name": edge.get("fromName"),
                                      "mac": mac_display(from_mac),
                                      "serial": edge.get("fromSerial")}),
            b=Endpoint(device_id=b_device, port_id=b_port,
                       ident=canon_port_ident(edge.get("correspondingPort")) or None,
                       resolved_via="r1-topology",
                       discovered_as={"name": edge.get("toName"),
                                      "mac": mac_display(to_mac),
                                      "serial": edge.get("toSerial")}),
            kind="ethernet",
            channel="r1-derived", observer=a_device,
            evidence=evidence,
        )


@source("mesh.r1.edge", "R1 mesh topology",
        proves="R1 reports a wireless mesh link between two APs",
        reads="GET /venues/{venueId}/meshTopologies",
        caveats="Empty on wired venues -- that is a property of the site, not of "
                "the endpoint. The probe venue had 995 AP nodes and zero mesh "
                "edges.")
def r1_mesh_edges(bundle, index) -> Iterator[Claim]:
    """Wireless mesh links, from R1's mesh topology view."""
    for edge in getattr(bundle, "mesh_edges", None) or []:
        a_device = _resolve(index, edge.get("from"), norm_mac(edge.get("fromMac")),
                            edge.get("fromSerial"), edge.get("fromName"))
        b_device = _resolve(index, edge.get("to"), norm_mac(edge.get("toMac")),
                            edge.get("toSerial"), edge.get("toName"))
        if not a_device or not b_device or a_device == b_device:
            continue
        a_obj, b_obj = index.device(a_device), index.device(b_device)
        yield Claim(
            a=Endpoint(device_id=a_device, resolved_via="r1-mesh",
                       discovered_as={"role": edge.get("fromRole")}),
            b=Endpoint(device_id=b_device, resolved_via="r1-mesh",
                       discovered_as={"role": edge.get("toRole")}),
            kind="mesh", channel="r1-derived", observer=a_device,
            evidence=[Evidence(
                source="mesh.r1.edge", kind="support",
                claim=f"R1 reports a wireless mesh link between "
                      f"{a_obj.display_name if a_obj else a_device} and "
                      f"{b_obj.display_name if b_obj else b_device}"
                      + (f" on channel {edge.get('channel')}"
                         if edge.get("channel") else "")
                      + (f" ({edge.get('band')})." if edge.get("band") else "."),
                a=f"dev:{a_device}", b=f"dev:{b_device}",
                fields={"band": edge.get("band"), "channel": edge.get("channel"),
                        "fromRole": edge.get("fromRole"), "toRole": edge.get("toRole"),
                        "fromSnr": edge.get("fromSNR"), "toSnr": edge.get("toSNR"),
                        "connectionStatus": edge.get("connectionStatus")},
            )])
