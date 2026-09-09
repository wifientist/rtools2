"""
Where does this site reach the internet?

Nothing in R1 answers that directly, so this proposes RANKED CANDIDATES with
their reasons and lets a human settle it. The verdict then persists per venue
and outranks every automated signal, because being wrong about the WAN edge
poisons the hierarchy layout and, later, every traced client path.

MEASURED on the probe venue (112 switches), which is why the ranking is shaped
the way it is:

  * `cloudPort` is populated on ALL 112 switches -- but only via
    `/venues/{v}/topologies`, NOT the switch query, where it is 0%. Eleven
    switches additionally report `isConnectedCloud: true`. This is the best
    signal available by default.
  * `defaultGateway` is populated on all 112, and 111 of them agree on
    10.240.0.1 -- which is NOT a managed device. So the WAN edge is something
    R1 does not manage, exactly as expected, and the useful question is which
    switch PORT faces it.
  * static routes and VE ports would settle it outright, but they are per-switch
    reads that only happen under `deep`. Without them the ranking leans on the
    cloud-port chain.
  * a foreign-OUI LLDP neighbour scored ZERO here, and the MAC table is too
    sparse for a density signal (p95 was 1 MAC per port). Both are kept because
    they are decisive where they do fire, but neither can be relied on.
"""

import ipaddress
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .normalize import mac_display, norm_mac

logger = logging.getLogger(__name__)


def _reason(source: str, claim: str, **fields) -> Dict[str, Any]:
    return {"source": source, "claim": claim,
            "fields": {k: v for k, v in fields.items() if v not in (None, "")}}


def _in_subnet(ip: str, address: str, mask: str) -> bool:
    try:
        net = ipaddress.ip_network(f"{address}/{mask}", strict=False)
        return ipaddress.ip_address(ip) in net
    except (ValueError, TypeError):
        return False


class _Candidate:
    def __init__(self, venue_id: str, device_id: str, port_ident: Optional[str]):
        self.venue_id = venue_id
        self.device_id = device_id
        self.port_ident = port_ident
        self.score = 0.0
        self.reasons: List[Dict[str, Any]] = []

    @property
    def key(self) -> str:
        return f"{self.device_id}#{self.port_ident or ''}"

    def add(self, weight: float, reason: Dict[str, Any]) -> None:
        self.score += weight
        self.reasons.append({**reason, "weight": weight})

    def to_dict(self, index) -> Dict[str, Any]:
        device = index.device(self.device_id)
        return {
            "key": self.key,
            "venueId": self.venue_id,
            "deviceId": self.device_id,
            "deviceName": device.display_name if device else self.device_id,
            "portIdent": self.port_ident,
            "score": round(self.score, 3),
            "reasons": self.reasons,
        }


def candidates(snapshot, bundle, index) -> List[Dict[str, Any]]:
    """Ranked WAN-uplink candidates, each carrying the reasons behind it."""
    found: Dict[str, _Candidate] = {}

    def get(venue_id: str, device_id: str, port_ident: Optional[str]) -> _Candidate:
        key = f"{device_id}#{port_ident or ''}"
        if key not in found:
            found[key] = _Candidate(venue_id, device_id, port_ident)
        return found[key]

    devices = {d.id: d for d in snapshot.devices}
    ports_by_device: Dict[str, List] = defaultdict(list)
    for port in snapshot.ports:
        ports_by_device[port.device_id].append(port)

    # What each port connects to, per the links the correlator settled on.
    peer_of_port: Dict[str, str] = {}
    for link in snapshot.links:
        if link.tier == "rejected":
            continue
        for near, far in ((link.a, link.b), (link.b, link.a)):
            if near.port_id:
                peer_of_port[near.port_id] = far.device_id

    # ── 1. a default route, resolved to the switch that owns its next hop ────
    for switch_id, routes in (getattr(bundle, "static_routes", None) or {}).items():
        for route in routes:
            destination = str(route.get("destinationIp") or "")
            if not destination.startswith("0.0.0.0"):
                continue
            next_hop = str(route.get("nextHop") or "")
            device_id = f"sw:{norm_mac(switch_id)}"
            device = devices.get(device_id)
            if device is None:
                continue
            # Which of this switch's VEs is the next hop on? That names the VLAN,
            # and therefore the ports that could be facing it.
            ve_match = None
            for ve in (getattr(bundle, "ve_ports", None) or {}).get(switch_id, []):
                if _in_subnet(next_hop, str(ve.get("ipAddress") or ""),
                              str(ve.get("ipSubnetMask") or "")):
                    ve_match = ve
                    break
            candidate = get(device.venue_id, device_id, None)
            candidate.add(5.0, _reason(
                "route.default",
                f"{device.display_name} has a default route to {next_hop}"
                + (f", which is on its VLAN {ve_match.get('vlanId')} interface."
                   if ve_match else ", though no interface on it matches that subnet."),
                nextHop=next_hop, vlanId=(ve_match or {}).get("vlanId"),
                veAddress=(ve_match or {}).get("ipAddress")))

    # ── 2. the cloud-port chain ─────────────────────────────────────────────
    # Every switch names the port through which it reaches R1. Follow those and
    # the chain ends at whichever switch faces something R1 does not manage --
    # which is the edge.
    cloud_port: Dict[str, str] = {}
    connected_cloud: set = set()
    for node in getattr(bundle, "r1_nodes", None) or []:
        node_id = node.get("id") or ""
        mac = norm_mac(node.get("mac"))
        device_id = f"sw:{mac}" if mac and f"sw:{mac}" in devices else None
        if device_id is None:
            resolution = index.aliases.resolve(mac=node.get("mac"),
                                               serial=node.get("serial"),
                                               name=node.get("name"))
            device_id = resolution.device_id if resolution else None
        if not device_id or device_id not in devices:
            continue
        if node.get("cloudPort"):
            cloud_port[device_id] = str(node["cloudPort"])
        if node.get("isConnectedCloud"):
            connected_cloud.add(device_id)

    for device_id, ident in cloud_port.items():
        device = devices.get(device_id)
        if device is None:
            continue
        port_id = f"{device_id}#{ident}"
        peer = peer_of_port.get(port_id)
        peer_device = devices.get(peer) if peer else None
        # A terminus: the cloud-facing port leads to nothing we manage.
        terminus = peer_device is None or not peer_device.managed or \
            peer_device.kind == "external"
        if not terminus:
            continue
        candidate = get(device.venue_id, device_id, ident)
        candidate.add(3.0, _reason(
            "cloud.terminus",
            f"{device.display_name} reaches RUCKUS ONE through port {ident}, and "
            f"that port faces "
            + (f"{peer_device.display_name}, which R1 does not manage."
               if peer_device else "nothing R1 manages.")
            + " The chain toward the internet ends here.",
            cloudPort=ident,
            peer=peer_device.display_name if peer_device else None))
        if device_id in connected_cloud:
            candidate.add(1.5, _reason(
                "cloud.connected",
                f"R1 reports {device.display_name} as directly cloud-connected.",
                isConnectedCloud=True))

    # ── 3. an LLDP neighbour we do not manage, from an unfamiliar vendor ────
    for port in snapshot.ports:
        if not port.neighbor_mac or index.device_of_mac(port.neighbor_mac):
            continue
        if not index.aliases.is_foreign_oui(port.neighbor_mac):
            continue
        device = devices.get(port.device_id)
        if device is None:
            continue
        candidate = get(device.venue_id, port.device_id, port.ident)
        candidate.add(3.5, _reason(
            "lldp.foreign",
            f"Port {port.ident} sees '{port.neighbor_name or mac_display(port.neighbor_mac)}', "
            f"which R1 does not manage and whose vendor prefix is used by nothing "
            f"else in this tenant -- typically a firewall or router.",
            neighborName=port.neighbor_name,
            neighborMac=mac_display(port.neighbor_mac)))

    # ── 4. the switch whose own interface sits on the common gateway ────────
    gateways = Counter(
        str(d.attrs.get("defaultGateway")) for d in snapshot.devices
        if d.kind in ("switch", "stack") and d.attrs.get("defaultGateway"))
    if gateways:
        gateway, agreeing = gateways.most_common(1)[0]
        for switch_id, ve_rows in (getattr(bundle, "ve_ports", None) or {}).items():
            device_id = f"sw:{norm_mac(switch_id)}"
            device = devices.get(device_id)
            if device is None:
                continue
            for ve in ve_rows:
                if not _in_subnet(gateway, str(ve.get("ipAddress") or ""),
                                  str(ve.get("ipSubnetMask") or "")):
                    continue
                candidate = get(device.venue_id, device_id, None)
                candidate.add(2.0, _reason(
                    "gateway.subnet",
                    f"{agreeing} switches use {gateway} as their default gateway, and "
                    f"{device.display_name} has an interface on that subnet "
                    f"(VLAN {ve.get('vlanId')}).",
                    gateway=gateway, agreeing=agreeing, vlanId=ve.get("vlanId")))
                break

    # ── 5. structural: what does the rest of the network hang off? ──────────
    # Weak, and last, but it is the only signal that always exists. Degree over
    # infrastructure links only -- a switch with 200 APs is not a core switch.
    degree: Counter = Counter()
    for link in snapshot.links:
        if link.tier == "rejected" or link.logical_of:
            continue
        a, b = devices.get(link.a.device_id), devices.get(link.b.device_id)
        if not a or not b:
            continue
        if a.kind in ("switch", "stack") and b.kind in ("switch", "stack"):
            degree[a.id] += 1
            degree[b.id] += 1
    for device_id, count in degree.most_common(3):
        device = devices.get(device_id)
        if device is None:
            continue
        candidate = get(device.venue_id, device_id, None)
        candidate.add(1.0, _reason(
            "structural.hub",
            f"{device.display_name} connects to {count} other switches, more than "
            f"almost anything else here -- the shape of a core.",
            switchLinks=count))

    ranked = sorted(found.values(), key=lambda c: -c.score)
    return [c.to_dict(index) for c in ranked]


def build(snapshot, bundle, index, stored: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Candidates plus whatever the user has already decided.

    A rejected candidate is remembered and filtered out, so a wrong guess stops
    being offered rather than being re-proposed on every discovery.
    """
    stored = stored or {}
    all_candidates = candidates(snapshot, bundle, index)

    per_venue: Dict[str, Dict[str, Any]] = {}
    for venue_id in snapshot.scope_venue_ids:
        entry = (stored.get(venue_id) or {}) if isinstance(stored, dict) else {}
        rejected = set(entry.get("rejected") or [])
        offered = [c for c in all_candidates
                   if c["venueId"] == venue_id and c["key"] not in rejected]
        per_venue[venue_id] = {
            "venueId": venue_id,
            "venueName": snapshot.venues.get(venue_id, venue_id),
            "confirmed": entry.get("confirmed"),
            "rejected": sorted(rejected),
            "candidates": offered[:6],
        }
    return {"venues": per_venue,
            "totalCandidates": len(all_candidates)}


def wan_devices_and_links(snapshot, wan_state: Dict[str, Any]):
    """
    Materialise a `wan:<venueId>` node for each confirmed uplink, and the link
    to the port that was confirmed.

    Created only on confirmation, never on a guess: an unconfirmed WAN drawn on
    the map would be indistinguishable from a fact, and it anchors the whole
    hierarchy.
    """
    from .model import Device, Endpoint, Evidence, Link, stable_id

    devices, links = [], []
    for venue_id, entry in (wan_state or {}).items():
        confirmed = (entry or {}).get("confirmed")
        if not confirmed:
            continue
        device_id = confirmed.get("deviceId")
        ident = confirmed.get("portIdent")
        wan_id = f"wan:{venue_id}"
        devices.append(Device(
            id=wan_id, kind="wan",
            name=f"Internet ({snapshot.venues.get(venue_id, venue_id)})",
            venue_id=venue_id, venue_name=snapshot.venues.get(venue_id, ""),
            managed=False, status="unknown", role="wan",
            attrs={"confirmedBy": confirmed.get("by"), "confirmedAt": confirmed.get("at")},
        ))
        near = Endpoint(device_id=device_id,
                        port_id=f"{device_id}#{ident}" if ident else None,
                        ident=ident, resolved_via="user")
        far = Endpoint(device_id=wan_id, resolved_via="user")
        links.append(Link(
            id="lnk:wan:" + stable_id(venue_id, device_id, ident or ""),
            a=near, b=far, kind="wan",
            tier="confirmed", score=99.0, confidence=1.0,
            directionality="none",
            tier_reason=f"{confirmed.get('by') or 'A user'} confirmed this as the "
                        f"way out of this venue.",
            evidence=[Evidence(
                source="override.user.confirm", kind="support",
                claim=f"{confirmed.get('by') or 'A user'} confirmed "
                      + (f"port {ident} " if ident else "")
                      + "as this venue's internet uplink.",
                weight=99.0, base_weight=99.0,
                a=near.port_id or device_id, b=wan_id,
                fields={"confirmedAt": confirmed.get("at")},
                observed_at=str(confirmed.get("at") or ""))],
        ))
    return devices, links
