"""
Physical-layer corroboration and contradiction.

None of these create links. They weigh links other sources proposed, using facts
about the ports at each end: is the port actually up, do both ends agree on
speed, is the far device drawing power from this one.

The contradictions matter more than the corroborations. An LLDP cache that
outlives a link drop is exactly the "this link just failed" signal a
troubleshooter wants -- so a down port does not delete a link, it drags its
confidence down and says why. "Why is there no edge between A and B?" has to be
answerable, which means near-misses stay visible.
"""

from typing import Iterator

from ..model import Endpoint, Evidence
from . import Claim, source

# Ports whose STP state means they are not forwarding. R1 reports these on ~11%
# of ports; matching is substring and case-insensitive because the exact
# vocabulary varies by firmware.
STP_NOT_FORWARDING = ("blocking", "discarding", "listening", "learning", "broken")


def _pair(index, port, other_port_id):
    other = index.port(other_port_id)
    return (Endpoint(device_id=port.device_id, port_id=port.id, ident=port.ident,
                     resolved_via="self"),
            Endpoint(device_id=other.device_id if other else "",
                     port_id=other_port_id,
                     ident=other.ident if other else None,
                     resolved_via="self"))


@source("port.down", "Endpoint port is down",
        emits=["port.admin.down", "port.oper.down"],
        tier_cap="rejected", weight=-2.5,
        proves="A port a link claims is operationally or administratively down",
        reads="switchPorts/query: status, adminStatus",
        caveats="Deliberately NOT fatal. LLDP outliving a link drop is the "
                "signal that the link just failed, so the edge stays visible "
                "and carries the contradiction.")
def port_is_down(bundle, index) -> Iterator[Claim]:
    """A port carrying an LLDP neighbour is itself down."""
    for port in index.ports.values():
        if not port.neighbor_mac:
            continue
        far_device_id = index.device_of_mac(port.neighbor_mac)
        if not far_device_id:
            continue
        admin_down = port.admin_up is False
        oper_down = port.oper_up is False
        if not (admin_down or oper_down):
            continue

        near, far = _pair(index, port, index.resolve_far_port(port))
        if not far.device_id:
            far = Endpoint(device_id=far_device_id, resolved_via="mac")
        reason = ("administratively disabled" if admin_down
                  else "operationally down")
        yield Claim(a=near, b=far, evidence=[Evidence(
            source="port.admin.down" if admin_down else "port.oper.down",
            kind="contradict",
            claim=f"Port {port.ident} is {reason}, yet still reports an LLDP "
                  f"neighbour -- the cached neighbour may have outlived the link.",
            a=port.id, b=far.port_id or f"dev:{far.device_id}",
            fields={"port": port.ident, "adminUp": port.admin_up,
                    "operUp": port.oper_up, "neighborName": port.neighbor_name},
        )])


@source("port.stp.blocked", "Endpoint port is not forwarding",
        tier_cap="weak", weight=-0.5,
        proves="A link exists but spanning tree is not forwarding on it",
        reads="switchPorts/query: spanningTreeStatus",
        caveats="The link is REAL -- STP blocking proves a redundant path "
                "exists. Small negative weight only so a forwarding path wins "
                "ties; the edge must stay drawn.")
def stp_not_forwarding(bundle, index) -> Iterator[Claim]:
    """
    Spanning tree is blocking this port.

    This is context a troubleshooter needs and the v2 path tracer depends on:
    a blocked port is a real cable that traffic does not currently take.
    """
    for port in index.ports.values():
        state = str(port.stp_state or "").lower()
        if not state or not any(word in state for word in STP_NOT_FORWARDING):
            continue
        if not port.neighbor_mac or not index.device_of_mac(port.neighbor_mac):
            continue
        near, far = _pair(index, port, index.resolve_far_port(port))
        if not far.device_id:
            far = Endpoint(device_id=index.device_of_mac(port.neighbor_mac),
                           resolved_via="mac")
        yield Claim(a=near, b=far, evidence=[Evidence(
            source="port.stp.blocked", kind="contradict",
            claim=f"Spanning tree has port {port.ident} in '{port.stp_state}', so "
                  f"the cable is there but traffic is not crossing it.",
            a=port.id, b=far.port_id or f"dev:{far.device_id}",
            fields={"port": port.ident, "spanningTreeStatus": port.stp_state},
        )])


@source("poe.draw", "Far device draws PoE from this port",
        tier_cap="strong", weight=0.5,
        proves="The port is delivering power, so something is physically plugged in",
        reads="switchPorts/query: poeEnabled, poeUsed",
        caveats="Corroboration only. Power proves attachment, not identity -- it "
                "cannot tell you WHICH device is drawing it.")
def poe_draw(bundle, index) -> Iterator[Claim]:
    """A port delivering power has something physically attached to it."""
    for port in index.ports.values():
        used = port.poe.get("used")
        try:
            milliwatts = float(used) if used not in (None, "") else 0.0
        except (TypeError, ValueError):
            continue
        if milliwatts <= 0:
            continue
        far_device_id = index.device_of_mac(port.neighbor_mac) if port.neighbor_mac else None
        if not far_device_id:
            continue
        far_device = index.device(far_device_id)
        near, far = _pair(index, port, index.resolve_far_port(port))
        if not far.device_id:
            far = Endpoint(device_id=far_device_id, resolved_via="mac")
        yield Claim(a=near, b=far, evidence=[Evidence(
            source="poe.draw", kind="support",
            claim=f"Port {port.ident} is delivering {milliwatts / 1000:.1f} W, so "
                  f"a powered device is physically attached"
                  + (f" -- consistent with {far_device.display_name} being a "
                     f"{far_device.kind}." if far_device else "."),
            a=port.id, b=far.port_id or f"dev:{far.device_id}",
            fields={"port": port.ident, "poeUsedMilliwatts": used,
                    "poeUsage": port.poe.get("usage")},
        )])


@source("speed.match", "Both ends agree on link speed",
        emits=["speed.match", "speed.mismatch"],
        tier_cap="strong", weight=0.3,
        proves="Two ports claimed to be connected report the same speed",
        reads="switchPorts/query: portSpeed",
        caveats="Weak corroboration -- most ports in an estate run at the same "
                "speed, so agreement is common and disagreement is the "
                "informative half.")
def speed_agreement(bundle, index) -> Iterator[Claim]:
    """Two ports claimed to be connected report the same negotiated speed."""
    seen = set()
    for port in index.ports.values():
        far_port_id = index.resolve_far_port(port)
        if not far_port_id:
            continue
        key = tuple(sorted([port.id, far_port_id]))
        if key in seen:
            continue
        seen.add(key)
        far_port = index.port(far_port_id)
        if far_port is None or port.speed_mbps is None or far_port.speed_mbps is None:
            continue
        agree = port.speed_mbps == far_port.speed_mbps
        near, far = _pair(index, port, far_port_id)
        yield Claim(a=near, b=far, evidence=[Evidence(
            source="speed.match" if agree else "speed.mismatch",
            kind="support" if agree else "contradict",
            claim=(f"Both ends negotiated {port.speed_mbps} Mb/s."
                   if agree else
                   f"The two ends disagree on speed: {port.ident} reports "
                   f"{port.speed_mbps} Mb/s, {far_port.ident} reports "
                   f"{far_port.speed_mbps} Mb/s."),
            a=port.id, b=far_port_id,
            fields={"nearSpeedMbps": port.speed_mbps,
                    "farSpeedMbps": far_port.speed_mbps},
        )])


@source("venue.cross", "Endpoints are in different venues",
        tier_cap="weak", weight=-2.0,
        proves="A claimed link crosses a venue boundary",
        reads="device venueId",
        caveats="Unusual but legitimate -- campuses are routinely split across "
                "venues in R1. A weight, never a veto.")
def cross_venue(bundle, index) -> Iterator[Claim]:
    """
    A link whose ends sit in different venues.

    Not impossible: one physical campus is often modelled as several R1 venues,
    and the links between them are real. Flagged so a genuinely wrong join
    stands out, not so a correct one is deleted.
    """
    seen = set()
    for port in index.ports.values():
        if not port.neighbor_mac:
            continue
        far_device_id = index.device_of_mac(port.neighbor_mac)
        if not far_device_id:
            continue
        near_device = index.device(port.device_id)
        far_device = index.device(far_device_id)
        if not (near_device and far_device):
            continue
        if not (near_device.venue_id and far_device.venue_id):
            continue
        if near_device.venue_id == far_device.venue_id:
            continue
        key = tuple(sorted([port.device_id, far_device_id]))
        if key in seen:
            continue
        seen.add(key)
        near, far = _pair(index, port, index.resolve_far_port(port))
        if not far.device_id:
            far = Endpoint(device_id=far_device_id, resolved_via="mac")
        yield Claim(a=near, b=far, evidence=[Evidence(
            source="venue.cross", kind="contradict",
            claim=f"This link joins '{near_device.venue_name}' to "
                  f"'{far_device.venue_name}'. Cross-venue links are legitimate "
                  f"but uncommon, so it is worth a look.",
            a=port.id, b=far.port_id or f"dev:{far.device_id}",
            fields={"nearVenue": near_device.venue_name,
                    "farVenue": far_device.venue_name},
        )])
