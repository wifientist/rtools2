"""
Claims -> Links. One physical cable, however many sources saw it.

Merging happens strictly BEFORE scoring. If scoring influenced merging, the
output would depend on the order claims arrived in, and two runs over the same
data could disagree.

Three passes, most specific first:

  1. EXACT      -- claims naming the same two ports are the same link.
  2. PAIRING    -- the important one. Switch-to-switch LLDP is mutual 220 times
                   in 221 on the probe venue, but the near side usually cannot
                   name the far PORT (neighborPortMacAddress mostly echoes the
                   chassis MAC). So each end names its own port and points at
                   the other device. Two such half-observations between the same
                   device pair are ONE cable, and this pass joins them.
  3. ABSORPTION -- a device-level claim folds into a port-level link between the
                   same pair, when it is consistent and unambiguous.

Then LAG members roll up into a bundle.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .model import INDEPENDENT_CHANNELS, Endpoint, Evidence, Link, stable_id

logger = logging.getLogger(__name__)


def _link_id(a: Endpoint, b: Endpoint) -> str:
    return "lnk:" + stable_id(*sorted([a.key(), b.key()]))


def _orient(a: Endpoint, b: Endpoint) -> Tuple[Endpoint, Endpoint]:
    """Stable end order, so a link's id and rendering do not flip between runs."""
    return (a, b) if a.key() <= b.key() else (b, a)


def _merge_endpoint(into: Endpoint, other: Endpoint) -> Endpoint:
    """Keep the more specific of two views of the same end."""
    if into.port_id is None and other.port_id is not None:
        return Endpoint(device_id=into.device_id or other.device_id,
                        port_id=other.port_id, ident=other.ident,
                        resolved_via=other.resolved_via or into.resolved_via,
                        discovered_as={**other.discovered_as, **into.discovered_as})
    if not into.discovered_as and other.discovered_as:
        into.discovered_as = dict(other.discovered_as)
    return into


class _Builder:
    """A link under construction, with de-duplicated evidence."""

    def __init__(self, a: Endpoint, b: Endpoint, kind: str):
        self.a, self.b = _orient(a, b)
        self.kind = kind
        self.evidence: List[Evidence] = []
        self._evidence_ids = set()
        self.directions = set()             # device ids that observed this link
        self.observers = set()              # (device_id, channel) pairs

    def absorb(self, a: Endpoint, b: Endpoint, kind: str,
               evidence: List[Evidence], observer: Optional[str] = None,
               channel: str = "unknown") -> None:
        # Line the incoming ends up with ours before merging them.
        #
        # Device id alone is not enough: a stack interconnect has BOTH ends on
        # the same device, so "does the incoming `a` match our `a`?" is always
        # true and two different ports get folded into one, producing a link
        # from a port to itself. Match on the port when we have one.
        if a.key() == self.a.key() or b.key() == self.b.key():
            straight = True
        elif a.key() == self.b.key() or b.key() == self.a.key():
            straight = False
        else:
            straight = a.device_id == self.a.device_id
        if straight:
            self.a, self.b = _merge_endpoint(self.a, a), _merge_endpoint(self.b, b)
        else:
            self.a, self.b = _merge_endpoint(self.a, b), _merge_endpoint(self.b, a)
        # A structural kind is more specific than the generic default.
        if kind != "ethernet" and self.kind == "ethernet":
            self.kind = kind
        for item in evidence:
            # The SAME fact read from both port rows must count once. Evidence.id
            # sorts its endpoints, so both readings hash identically.
            if item.id not in self._evidence_ids:
                self._evidence_ids.add(item.id)
                self.evidence.append(item)
        if observer:
            self.directions.add(observer)
            self.observers.add((observer, channel))

    def build(self) -> Link:
        link = Link(id=_link_id(self.a, self.b), a=self.a, b=self.b,
                    kind=self.kind, evidence=list(self.evidence))
        # `directionality` describes how many INDEPENDENT devices saw this link,
        # not how many rows mention it. Counting every observer would call an AP
        # link "bidirectional" because the switch's LLDP and R1's restatement of
        # that same LLDP are two rows -- which is the overstatement the channel
        # distinction exists to prevent.
        independent_observers = {device for device, channel in self.observers
                                 if channel in INDEPENDENT_CHANNELS}
        if len(independent_observers) >= 2:
            link.directionality = "bidirectional"
            # Mutual agreement is a property of the MERGED link, not of any one
            # source, so it is recorded here -- once, however the two halves
            # came together. Recording it only in the pairing pass left links
            # whose far port resolved directly stuck at 'strong' despite being
            # the best-evidenced links in the graph.
            #
            # But two OBSERVATIONS are not two OPINIONS. Only channels in
            # INDEPENDENT_CHANNELS count: a device reporting what it itself
            # sees, by a mechanism that is not a restatement of another
            # channel. An AP's `switchSerialNumber` agreed with the far
            # switch's LLDP 2657 times out of 2657 and never disagreed, which
            # is the signature of R1 restating that LLDP rather than a second
            # device having a look.
            independent = independent_observers
            if len(independent) >= 2 and not any(
                    e.source == "link.mutual" for e in self.evidence):
                link.evidence.append(Evidence(
                    source="link.mutual", kind="support",
                    claim=f"Both ends independently name each other: "
                          f"{self.a.ident or 'this device'} on one side and "
                          f"{self.b.ident or 'the other'} on the other.",
                    a=self.a.port_id or self.a.device_id,
                    b=self.b.port_id or self.b.device_id,
                    fields={"aPort": self.a.ident, "bPort": self.b.ident,
                            "independentObservers": sorted(independent),
                            "channels": sorted({c for _, c in self.observers})},
                ))
        elif independent_observers:
            observer = next(iter(independent_observers))
            link.directionality = "a-only" if observer == self.a.device_id else "b-only"
        elif self.directions:
            # Seen only through derived channels -- R1's own topology, an AP's
            # self-report. Real, but nobody at either end told us directly.
            link.directionality = "derived"
        return link


def merge(claims) -> List[Link]:
    """Fold every claim about the same physical link into one Link."""
    builders: Dict[str, _Builder] = {}
    by_key: Dict[frozenset, str] = {}

    # Context claims describe a PORT, not a link -- "this port is in a LAG",
    # "this port carries 40 MACs". They must never become edges: a LAG-membership
    # claim names the same device at both ends, and left in the link stream it
    # would draw a switch connected to itself.
    context_by_port: Dict[str, List] = defaultdict(list)
    link_claims = []
    for claim in claims:
        if claim.context_only:
            if claim.a.port_id:
                context_by_port[claim.a.port_id].append(claim)
        else:
            link_claims.append(claim)
    claims = link_claims

    # Claims are bucketed by device pair so the pairing pass can see both halves
    # of a mutual observation together.
    by_pair: Dict[tuple, List] = defaultdict(list)
    for claim in claims:
        by_pair[claim.pair()].append(claim)

    for pair, pair_claims in by_pair.items():
        both_ports = [c for c in pair_claims if c.a.port_id and c.b.port_id]
        half = [c for c in pair_claims if c.a.port_id and not c.b.port_id]
        neither = [c for c in pair_claims if not c.a.port_id and not c.b.port_id]

        # ── pass 1: both ends named ─────────────────────────────────────────
        for claim in both_ports:
            key = frozenset([claim.a.key(), claim.b.key()])
            builder_id = by_key.get(key)
            if builder_id is None:
                builder = _Builder(claim.a, claim.b, claim.kind)
                builder_id = builder.a.key() + "||" + builder.b.key()
                builders[builder_id] = builder
                by_key[key] = builder_id
            builders[builder_id].absorb(claim.a, claim.b, claim.kind,
                                        claim.evidence, claim.a.device_id,
                                        claim.channel)

        # ── pass 2: pair up two one-sided observations ──────────────────────
        # Each half-claim names its own port and points at the other device.
        # Group them by which device is doing the observing.
        by_observer: Dict[str, List] = defaultdict(list)
        for claim in half:
            by_observer[claim.a.device_id].append(claim)

        consumed = set()
        if len(by_observer) == 2:
            left_id, right_id = sorted(by_observer)
            left, right = by_observer[left_id], by_observer[right_id]
            # Only pair when it is unambiguous. Several ports each way is a LAG
            # or a redundant pair, and guessing which cable goes to which would
            # invent adjacency that no source claimed.
            if len(left) == 1 and len(right) == 1:
                lc, rc = left[0], right[0]
                merged_a = Endpoint(device_id=lc.a.device_id, port_id=lc.a.port_id,
                                    ident=lc.a.ident, resolved_via=lc.a.resolved_via,
                                    discovered_as=dict(lc.a.discovered_as))
                merged_b = Endpoint(device_id=rc.a.device_id, port_id=rc.a.port_id,
                                    ident=rc.a.ident, resolved_via=rc.a.resolved_via,
                                    discovered_as=dict(rc.a.discovered_as))
                builder = _Builder(merged_a, merged_b,
                                   lc.kind if lc.kind != "ethernet" else rc.kind)
                builder_id = builder.a.key() + "||" + builder.b.key()
                builders.setdefault(builder_id, builder)
                builders[builder_id].absorb(merged_a, merged_b, lc.kind,
                                            lc.evidence, lc.a.device_id, lc.channel)
                builders[builder_id].absorb(merged_a, merged_b, rc.kind,
                                            rc.evidence, rc.a.device_id, rc.channel)
                by_key[frozenset([merged_a.key(), merged_b.key()])] = builder_id
                consumed.update({id(lc), id(rc)})


        # ── pass 3: absorb what is left ─────────────────────────────────────
        leftovers = [c for c in half if id(c) not in consumed] + neither
        for claim in leftovers:
            key = frozenset([claim.a.key(), claim.b.key()])
            builder_id = by_key.get(key)
            if builder_id is None:
                # Fold into an existing port-level link for this pair when there
                # is exactly one candidate. Two candidates means the claim is
                # consistent with both, and counting it toward either would
                # inflate precisely the link we are least sure about.
                candidates = [bid for k, bid in by_key.items()
                              if _pair_of(builders[bid]) == pair
                              and _consistent(builders[bid], claim)]
                if len(candidates) == 1:
                    builder_id = candidates[0]
                elif len(candidates) > 1:
                    # Consistent with several port-level links between the same
                    # pair, so it cannot pick one. Counting it toward any would
                    # inflate exactly the link we are least sure about -- but
                    # creating a NEW device-level link is worse still: it
                    # duplicates a cable that is already drawn, and an AP then
                    # appears to have two uplinks to one switch.
                    #
                    # So it becomes shared context on every candidate, carrying
                    # zero weight: the information is preserved and visible on
                    # each, and none of them gains a score it did not earn.
                    for candidate_id in candidates:
                        target = builders[candidate_id]
                        for item in claim.evidence:
                            if item.id in target._evidence_ids:
                                continue
                            target._evidence_ids.add(item.id)
                            target.evidence.append(Evidence(
                                source="merge.ambiguous", kind="context",
                                claim=f"{item.claim} It does not say which port, "
                                      f"and fits {len(candidates)} links between "
                                      f"this pair, so it counts toward none of "
                                      f"them.",
                                a=item.a, b=item.b,
                                fields={**item.fields,
                                        "originalSource": item.source,
                                        "candidateLinks": len(candidates)},
                            ))
                    continue
                else:
                    builder = _Builder(claim.a, claim.b, claim.kind)
                    builder_id = builder.a.key() + "||" + builder.b.key()
                    builders[builder_id] = builder
                    by_key[key] = builder_id
            builders[builder_id].absorb(claim.a, claim.b, claim.kind,
                                        claim.evidence, claim.a.device_id,
                                        claim.channel)

    # A link whose two ends are the same port is not a link. Sources guard
    # against emitting one, but merging can still produce it, so this is the
    # backstop rather than the only defence.
    links = [builder.build() for builder in builders.values()
             if builder.a.key() != builder.b.key()]
    _attach_port_context(links, context_by_port)
    return _roll_up_lags(links)


def _attach_port_context(links: List[Link], context_by_port: Dict[str, List]) -> None:
    """
    Fold port-level context onto whatever links touch that port.

    A LAG membership is what lets _roll_up_lags recognise a bundle, and a dense
    port explains why a link there is weaker than it looks. Both belong on the
    link as evidence -- but neither may create one.
    """
    for link in links:
        for end in (link.a, link.b):
            if not end.port_id:
                continue
            for claim in context_by_port.get(end.port_id, []):
                # Carry the LAG key onto the endpoint so roll-up can see it.
                for key, value in (claim.a.discovered_as or {}).items():
                    end.discovered_as.setdefault(key, value)
                for key, value in (claim.b.discovered_as or {}).items():
                    end.discovered_as.setdefault(key, value)
                existing = {e.id for e in link.evidence}
                for item in claim.evidence:
                    if item.id not in existing:
                        link.evidence.append(item)


def _pair_of(builder: _Builder) -> tuple:
    return tuple(sorted([builder.a.device_id, builder.b.device_id]))


def _consistent(builder: _Builder, claim) -> bool:
    """A claim is consistent with a link if it names no port the link contradicts."""
    for end in (claim.a, claim.b):
        if not end.port_id:
            continue
        if end.device_id == builder.a.device_id and builder.a.port_id \
                and builder.a.port_id != end.port_id:
            return False
        if end.device_id == builder.b.device_id and builder.b.port_id \
                and builder.b.port_id != end.port_id:
            return False
    return True


def _roll_up_lags(links: List[Link]) -> List[Link]:
    """
    Group links whose member ports belong to the same LAG at both ends.

    The bundle is drawn once instead of as N parallel edges -- which otherwise
    look exactly like a loop, and are the single most common way a topology
    picture misleads someone.

    Members stay in the model; the port strip needs them. Only the collapsed
    canvas draws the bundle.
    """
    by_lag: Dict[tuple, List[Link]] = defaultdict(list)
    passthrough: List[Link] = []

    for link in links:
        a_lag = _lag_of(link.a, links)
        b_lag = _lag_of(link.b, links)
        if a_lag and b_lag:
            by_lag[tuple(sorted([a_lag, b_lag]))].append(link)
        else:
            passthrough.append(link)

    out = list(passthrough)
    for lag_pair, members in by_lag.items():
        if len(members) < 2:
            out.extend(members)
            continue
        first = members[0]
        bundle = Link(
            id="lnk:lag:" + stable_id(*lag_pair),
            a=Endpoint(device_id=first.a.device_id, resolved_via="lag"),
            b=Endpoint(device_id=first.b.device_id, resolved_via="lag"),
            kind="lag",
            members=[m.id for m in members],
            attrs={"memberCount": len(members),
                   "memberPorts": sorted(
                       [m.a.ident for m in members if m.a.ident] +
                       [m.b.ident for m in members if m.b.ident])},
        )
        bundle.evidence.append(Evidence(
            source="lag.bundle", kind="context",
            claim=f"{len(members)} physical links between this pair are members of "
                  f"one aggregation group, so they are drawn as a single bundle.",
            a=first.a.device_id, b=first.b.device_id,
            fields={"memberCount": len(members)},
        ))
        for member in members:
            member.logical_of = bundle.id
        out.append(bundle)
        out.extend(members)
    return out


def _lag_of(endpoint: Endpoint, links) -> Optional[str]:
    """The LAG key an endpoint's port belongs to, from its evidence."""
    return endpoint.discovered_as.get("lagKey")
