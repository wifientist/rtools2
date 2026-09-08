"""
The topology data model.

One physical network, expressed as Devices, Ports, Links and the Evidence that
argues for each link. Everything is JSON-serialisable and camelCase on the way
out, matching every other API shape in this repo.

Identity keys are chosen so they survive the things that actually change:

  * a switch is `sw:<mac12>` -- R1's switch `id` IS the MAC, and the MAC is the
    join key on ports, on the MAC table and in LLDP. The serial is not: LLDP
    never gives you a serial.
  * an AP is `ap:<serial>` -- the AP neighbour endpoint and the AP row's own
    `switchSerialNumber` are serial-keyed, while LLDP gives MACs. The alias
    index bridges the two.
  * a port is `<deviceId>#<ident>`, not R1's raw `<dashed-mac>_<u-s-p>`, so it
    survives a MAC being reformatted.

Confidence is TWO things on purpose. `tier` is a lattice the UI encodes and a
human can explain in one sentence; `score` is a log-odds sum used for sorting
and opacity. A source may never push a link above its own tier cap, which is
what stops a pile of weak evidence from manufacturing certainty.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Tier lattice, weakest first. Index order IS the ordering.
TIERS = ["rejected", "weak", "probable", "strong", "confirmed"]
TIER_RANK = {name: index for index, name in enumerate(TIERS)}

# Channels whose agreement constitutes genuine mutual confirmation: a device
# reporting what IT sees, by a mechanism that is not a restatement of another
# channel.
#
# MEASURED, and the reason this distinction exists at all: an AP's
# `switchSerialNumber` looks like an independent AP-side observation, but across
# 2657 APs it agreed with the far switch's own LLDP 2657 times and disagreed
# ZERO times, and it is a near-perfect subset of switch LLDP (3 APs self-report
# without LLDP backing; 101 are seen by LLDP without self-reporting). A
# genuinely separate observation would disagree sometimes and have roughly
# symmetric gaps. It is R1 restating the switch's LLDP -- so counting it as a
# second opinion would have promoted 2657 AP links to `confirmed` on one fact
# counted twice.
INDEPENDENT_CHANNELS = frozenset({"switch-lldp", "ap-lldp"})

# Mirrors Finding._cap() in wiredwiz/checks/framework.py: a link with 400
# supporting MAC rows must not put 400 rows in the response body.
MAX_EVIDENCE_PER_LINK = 200


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(word.capitalize() for word in rest)


def _clean(value: Any) -> Any:
    """Round floats for stable output; recurse into containers."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def stable_id(*parts: Any) -> str:
    """A content hash that does not move between runs."""
    joined = "|".join(str(part) for part in parts)
    return hashlib.sha1(joined.encode()).hexdigest()[:16]


@dataclass
class StackInfo:
    active_serial: str = ""
    units: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"activeSerial": self.active_serial, "units": _clean(self.units)}


@dataclass
class FloorplanRef:
    """Real physical placement, for the `geo` layout mode."""
    floorplan_id: str = ""
    x_percent: Optional[float] = None
    y_percent: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"floorplanId": self.floorplan_id,
                "xPercent": _clean(self.x_percent), "yPercent": _clean(self.y_percent)}


@dataclass
class Device:
    id: str
    kind: str                       # switch|stack|ap|client|external|wan|unknown
    name: str = ""
    venue_id: str = ""
    venue_name: str = ""
    mac: str = ""                   # mac12
    serial: str = ""
    model: str = ""
    ip: str = ""
    status: str = "unknown"         # online|offline|degraded|unknown
    raw_status: str = ""            # R1's own coded string, kept verbatim
    role: str = "unknown"           # wan|core|distribution|access|ap|client|endpoint
    managed: bool = True            # present in R1 inventory
    aliases: List[str] = field(default_factory=list)
    stack: Optional[StackInfo] = None
    floorplan: Optional[FloorplanRef] = None
    port_ids: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        from .normalize import mac_display
        return self.name or mac_display(self.mac) or self.serial or self.id

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "id": self.id, "kind": self.kind, "name": self.name,
            "displayName": self.display_name,
            "venueId": self.venue_id, "venueName": self.venue_name,
            "mac": self.mac, "serial": self.serial, "model": self.model,
            "ip": self.ip, "status": self.status, "rawStatus": self.raw_status,
            "role": self.role, "managed": self.managed,
            "aliases": self.aliases, "portIds": self.port_ids,
            "counts": _clean(self.counts), "attrs": _clean(self.attrs),
        }
        if self.stack:
            out["stack"] = self.stack.to_dict()
        if self.floorplan:
            out["floorplan"] = self.floorplan.to_dict()
        return out


@dataclass
class Port:
    id: str                         # "<device_id>#<ident>"
    device_id: str
    ident: str                      # canonical "1/1/24"
    label: str = ""
    r1_port_id: str = ""            # raw "<dashed-mac>_1-1-24", joins the MAC table
    port_mac: str = ""              # mac12; lets an LLDP portId name this port
    unit: Optional[int] = None
    kind: str = "ethernet"          # ethernet|lag|ve|radio|virtual|stack
    admin_up: Optional[bool] = None
    oper_up: Optional[bool] = None
    speed_mbps: Optional[int] = None
    untagged_vlan: Optional[int] = None
    tagged_vlans: List[int] = field(default_factory=list)
    lag_id: Optional[str] = None
    lag_key: Optional[str] = None   # "<device_id>#lag:<lagId>"
    # MEASURED: `usedInFormingStack` is a CAPABILITY flag, not a state -- 196
    # ports across 91 devices carried it on a venue with only 2 stacks, and the
    # non-stack ones were ordinary uplinks with live LLDP neighbours. Only
    # `stack_peer_ident` proves a stack link actually exists.
    stack_capable: bool = False
    stack_peer_ident: str = ""
    is_cloud_port: bool = False
    stp_state: str = ""
    mac_count: int = 0
    neighbor_name: str = ""         # LLDP, kept raw for the evidence panel
    neighbor_mac: str = ""
    neighbor_port_mac: str = ""
    poe: Dict[str, Any] = field(default_factory=dict)
    attrs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "deviceId": self.device_id, "ident": self.ident,
            "label": self.label or self.ident, "r1PortId": self.r1_port_id,
            "portMac": self.port_mac, "unit": self.unit, "kind": self.kind,
            "adminUp": self.admin_up, "operUp": self.oper_up,
            "speedMbps": self.speed_mbps,
            "untaggedVlan": self.untagged_vlan, "taggedVlans": self.tagged_vlans,
            "lagId": self.lag_id, "lagKey": self.lag_key,
            "stackCapable": self.stack_capable,
            "stackPeerIdent": self.stack_peer_ident,
            "isCloudPort": self.is_cloud_port,
            "stpState": self.stp_state, "macCount": self.mac_count,
            "neighborName": self.neighbor_name, "neighborMac": self.neighbor_mac,
            "neighborPortMac": self.neighbor_port_mac,
            "poe": _clean(self.poe), "attrs": _clean(self.attrs),
        }


@dataclass
class Endpoint:
    """One end of a link. May know the device but not the port."""
    device_id: str
    port_id: Optional[str] = None
    ident: Optional[str] = None
    resolved_via: str = ""          # mac|serial|name|name-prefix|port-mac|unresolved
    discovered_as: Dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Merge key: the port when known, else the device."""
        return self.port_id or f"dev:{self.device_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {"deviceId": self.device_id, "portId": self.port_id,
                "ident": self.ident, "resolvedVia": self.resolved_via,
                "discoveredAs": _clean(self.discovered_as)}


@dataclass
class Evidence:
    source: str                     # "lldp.switch.bidir.mac"
    kind: str                       # support | contradict | context
    claim: str                      # one plain sentence, no jargon
    weight: float = 0.0             # signed log-odds, as applied
    base_weight: float = 0.0        # the catalogue weight, before modifiers
    a: Optional[str] = None
    b: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)
    observed_at: str = ""

    @property
    def id(self) -> str:
        """
        Stable across runs AND across the two sides a link is seen from -- the
        endpoints are sorted, so one LLDP fact read from both port rows produces
        one id and is de-duplicated on merge instead of counting twice.
        """
        ends = "|".join(sorted([str(self.a or ""), str(self.b or "")]))
        return stable_id(self.source, ends, sorted(self.fields.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "source": self.source, "kind": self.kind,
                "claim": self.claim, "weight": _clean(self.weight),
                "baseWeight": _clean(self.base_weight),
                "a": self.a, "b": self.b, "fields": _clean(self.fields),
                "observedAt": self.observed_at}


@dataclass
class Override:
    """A human verdict. Outranks every machine source."""
    key: str
    verdict: str                    # confirm | reject | assert
    by: str = ""
    at: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "verdict": self.verdict, "by": self.by,
                "at": self.at, "note": self.note}


@dataclass
class Link:
    id: str
    a: Endpoint
    b: Endpoint
    kind: str = "ethernet"          # ethernet|lag|stack|mesh|wireless-client|wan
    logical_of: Optional[str] = None
    members: List[str] = field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.0
    tier: str = "weak"
    tier_reason: str = ""
    directionality: str = "none"    # bidirectional|a-only|b-only|none
    evidence: List[Evidence] = field(default_factory=list)
    evidence_capped: Optional[Dict[str, int]] = None
    override: Optional[Override] = None
    # What the engine concluded BEFORE a human verdict was applied. Present only
    # on overridden links. Two jobs: the panel can say "the engine scored this
    # Probable, you called it Confirmed", and the read path can lift a baked
    # verdict off a stored snapshot to re-apply the current one.
    machine: Optional[Dict[str, Any]] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def user_asserted(self) -> bool:
        return self.override is not None

    def summary(self) -> Dict[str, Any]:
        """Counts for the collapsed view, so /graph needn't ship every row."""
        counts: Dict[str, int] = {}
        for item in self.evidence:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {"total": len(self.evidence),
                "support": counts.get("support", 0),
                "contradict": counts.get("contradict", 0),
                "context": counts.get("context", 0),
                "sources": sorted({item.source for item in self.evidence})}

    def to_dict(self, with_evidence: bool = False) -> Dict[str, Any]:
        out = {
            "id": self.id, "a": self.a.to_dict(), "b": self.b.to_dict(),
            "kind": self.kind, "logicalOf": self.logical_of, "members": self.members,
            "score": _clean(self.score), "confidence": _clean(self.confidence),
            "tier": self.tier, "tierReason": self.tier_reason,
            "directionality": self.directionality,
            "userAsserted": self.user_asserted,
            "evidenceSummary": self.summary(),
            "attrs": _clean(self.attrs),
        }
        if self.override:
            out["override"] = self.override.to_dict()
        if self.machine:
            out["machine"] = _clean(self.machine)
        if with_evidence:
            shown = self.evidence[:MAX_EVIDENCE_PER_LINK]
            out["evidence"] = [item.to_dict() for item in shown]
            if len(self.evidence) > len(shown):
                out["evidenceCapped"] = {"shown": len(shown), "total": len(self.evidence)}
        return out


@dataclass
class Snapshot:
    """
    One discovery run over one venue set.

    `scope_venue_ids` is the identity: a topology of {A,B} is NOT a subset of
    one of {A,B,C}, because the inter-venue links differ. Counts are recorded
    rather than recomputed -- R1's topology is live and drifts between runs.
    """
    taken_at: str
    taken_at_epoch: float
    tenant_id: str
    scope_venue_ids: List[str]
    venues: Dict[str, str] = field(default_factory=dict)
    devices: List[Device] = field(default_factory=list)
    ports: List[Port] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    completeness: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    deep: bool = False
    # WAN candidates and verdicts, per venue. Attached by correlate().
    wan: Optional[Dict[str, Any]] = None

    def counts(self) -> Dict[str, int]:
        by_kind: Dict[str, int] = {}
        for device in self.devices:
            by_kind[device.kind] = by_kind.get(device.kind, 0) + 1
        by_tier: Dict[str, int] = {}
        for link in self.links:
            by_tier[link.tier] = by_tier.get(link.tier, 0) + 1
        return {"devices": len(self.devices), "ports": len(self.ports),
                "links": len(self.links), **{f"devices.{k}": v for k, v in by_kind.items()},
                **{f"links.{k}": v for k, v in by_tier.items()}}

    def meta(self) -> Dict[str, Any]:
        out = {
            "takenAt": self.taken_at, "takenAtEpoch": self.taken_at_epoch,
            "tenantId": self.tenant_id, "scopeVenueIds": self.scope_venue_ids,
            "venues": self.venues, "counts": self.counts(),
            "sources": _clean(self.sources), "completeness": _clean(self.completeness),
            "warnings": self.warnings,
            "elapsedSeconds": _clean(self.elapsed_seconds), "deep": self.deep,
        }
        # The correlation report is attached by discover() rather than being a
        # declared field, because collection and correlation are separate steps
        # and a snapshot is valid without one. It still has to reach the client:
        # the tier breakdown and the unattached-AP count are both read from here.
        correlation = getattr(self, "correlation", None)
        if correlation:
            out["correlation"] = _clean(correlation)
        return out
