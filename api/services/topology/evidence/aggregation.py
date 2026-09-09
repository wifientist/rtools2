"""
Structural links: stack interconnects and LAG bundles.

These differ from every other source in this tool. LLDP and MAC tables are
observations that might be stale or wrong; a stack interconnect is a fact about
how a chassis is assembled, reported by the device about itself. That is why
`stack.interconnect` is one of only three things that can reach `confirmed`
without a human.

LAG is handled from PORT rows rather than the per-switch /lags endpoint. Probing
found /lags returned 200 on every switch and zero rows on all of them, while
port rows carry `lagId`/`lagName`/`lagStatus` directly -- so the endpoint costs
N calls to learn nothing the ports do not already say.
"""

from collections import defaultdict
from typing import Iterator

from ..model import Endpoint, Evidence
from ..normalize import canon_port_ident
from . import Claim, source


@source("stack.interconnect", "Stack interconnect",
        tier_cap="confirmed", weight=4.0,
        proves="Two units of one stack are cabled together",
        reads="switchPorts/query: usedInFormingStack, stackingNeighborPort",
        caveats="Structural, not observational -- the chassis reports its own "
                "assembly. Only rendered when a stack node is expanded.")
def stack_interconnects(bundle, index) -> Iterator[Claim]:
    """
    Ports that form a stack, linked to the unit they connect to.

    R1 models a stack as ONE switch row, so these are links between unit
    sub-entities of a single device rather than between devices.
    """
    for port in index.ports.values():
        # `usedInFormingStack` alone is NOT enough -- it is a capability flag,
        # true on ordinary uplinks of stacking-capable models. Only a populated
        # `stackingNeighborPort` shows a stack link that actually exists.
        if not port.stack_peer_ident:
            continue
        far_ident = port.stack_peer_ident
        device = index.device(port.device_id)
        near_unit = port.unit or (int(port.ident.split("/")[0]) if port.ident else None)
        far_unit = int(far_ident.split("/")[0]) if far_ident else None

        near = Endpoint(device_id=port.device_id, port_id=port.id,
                        ident=port.ident, resolved_via="self")
        far_port_id = f"{port.device_id}#{far_ident}" if far_ident else None
        far = Endpoint(device_id=port.device_id, port_id=far_port_id,
                       ident=far_ident or None,
                       resolved_via="stack" if far_ident else "unresolved",
                       discovered_as={"stackingNeighborPort":
                                      port.attrs.get("stackingNeighborPort"),
                                      "unit": far_unit})
        if far_port_id and far_port_id == port.id:
            continue

        yield Claim(a=near, b=far, kind="stack", channel="stack",
                    observer=port.device_id, evidence=[Evidence(
            source="stack.interconnect", kind="support",
            claim=f"Port {port.ident} on "
                  f"{device.display_name if device else port.device_id} forms a "
                  f"stack, cabled to unit port {far_ident}.",
            a=port.id, b=far_port_id,
            fields={"nearPort": port.ident, "farPort": far_ident,
                    "nearUnit": near_unit, "farUnit": far_unit,
                    "unitState": port.attrs.get("unitState")},
        )])


@source("lag.membership", "LAG bundle membership",
        emits=["lag.membership", "lag.bundle"],
        tier_cap="strong", weight=0.5,
        proves="Several physical links between the same pair are one logical link",
        reads="switchPorts/query: lagId, lagName, lagStatus",
        caveats="Does not create links. It groups links that other sources found, "
                "so a bundle is drawn once rather than as N parallel edges that "
                "look like a loop.")
def lag_membership(bundle, index) -> Iterator[Claim]:
    """
    Ports belonging to a link aggregation group.

    Emitted as context on each member port. merge.py rolls the members up into
    one bundle whose confidence is the MAX of its members, never the sum -- the
    members are one physical bundle, and summing would count the same fact
    several times.
    """
    by_lag = defaultdict(list)
    for port in index.ports.values():
        if port.lag_key:
            by_lag[port.lag_key].append(port)

    for lag_key, ports in by_lag.items():
        if len(ports) < 2:
            continue                        # a one-member LAG is just a port
        device = index.device(ports[0].device_id)
        idents = sorted(p.ident for p in ports)
        name = ports[0].attrs.get("lagName") or ports[0].lag_id
        status = ports[0].attrs.get("lagStatus")
        for port in ports:
            yield Claim(
                a=Endpoint(device_id=port.device_id, port_id=port.id,
                           ident=port.ident, resolved_via="self"),
                b=Endpoint(device_id=port.device_id, port_id=None,
                           resolved_via="lag",
                           discovered_as={"lagKey": lag_key, "lagName": name}),
                kind="lag", context_only=True,
                evidence=[Evidence(
                    source="lag.membership", kind="context",
                    claim=f"Port {port.ident} is a member of LAG '{name}' on "
                          f"{device.display_name if device else port.device_id}, "
                          f"together with {', '.join(i for i in idents if i != port.ident)}.",
                    a=port.id, b=None,
                    fields={"lagKey": lag_key, "lagName": name, "lagStatus": status,
                            "members": idents, "memberCount": len(idents)},
                )])
