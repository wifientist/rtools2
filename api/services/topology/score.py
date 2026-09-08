"""
Scoring: turn a pile of evidence into a tier and a number.

TWO layers, deliberately.

  TIER is what the UI encodes and what a human reads. It is a LATTICE, not
  arithmetic: a link's tier is the highest tier any single supporting source can
  justify, then demoted by contradictions. That means it can always be explained
  in one sentence naming the deciding evidence.

  SCORE is a signed log-odds sum, used for sorting, opacity and tie-breaks. The
  evidence panel prints the running total line by line, so the number summarises
  arithmetic the reader can see rather than acting as an oracle.

The rule that makes this honest: A SOURCE MAY NEVER PUSH A LINK ABOVE ITS OWN
TIER CAP. Ten MAC-table observations still top out at `probable`. Only mutual
LLDP, a structural fact, or a human reaches `confirmed`. Without that rule,
piling up weak evidence manufactures certainty, which is the exact failure this
tool exists to avoid.

Weights are PRIORS, revised once against measured agreement -- see
plans/topology.md §3. They are not physical constants.
"""

import math
from typing import Any, Dict, List, Optional

from .model import TIER_RANK, TIERS, Evidence, Link, Override

# id -> (tier cap, weight). One table, served by GET /topology/{cid}/sources so
# the UI's catalogue and the engine can never drift apart.
WEIGHTS: Dict[str, tuple] = {
    # -- human verdicts: absorbing --------------------------------------------
    "override.user.confirm": ("confirmed", 99.0),
    "override.user.assert": ("confirmed", 99.0),
    "override.user.reject": ("rejected", -99.0),

    # -- mutual agreement: the only automated route to 'confirmed' ------------
    # Two INDEPENDENT devices naming each other. Emitted by merge, not by any
    # single source -- see merge._Builder.build and model.INDEPENDENT_CHANNELS.
    "link.mutual": ("confirmed", 4.0),
    "stack.interconnect": ("confirmed", 4.0),

    # -- one-sided but strong -------------------------------------------------
    "lldp.switch.mac": ("strong", 2.0),
    "lldp.switch.portmac": ("strong", 1.0),
    "r1.topology.edge": ("strong", 2.0),
    "r1.topology.status.disconnected": (None, -2.0),
    "r1.topology.status.degraded": (None, -0.5),
    # Deliberately NOT an independent channel, so it corroborates without
    # promoting: measured 2657/2657 agreement with the far switch's own LLDP and
    # zero disagreement, which means R1 is restating that LLDP.
    "ap.selfreport.switchserial": ("strong", 1.5),
    "mac.single.managed": ("strong", 1.8),
    "lldp.ap.tlv": ("strong", 2.5),
    "mesh.uplink": ("strong", 2.5),
    "mesh.r1.edge": ("strong", 2.5),

    # -- plausible ------------------------------------------------------------
    "lldp.switch.name": ("probable", 1.8),
    "lldp.unmanaged": ("probable", 1.5),
    "r1.topology.edge.noports": ("probable", 1.5),
    "mac.single.endpoint": ("probable", 1.0),

    # -- corroboration: never raises a tier, only the score -------------------
    "poe.draw": (None, 0.5),
    "speed.match": (None, 0.3),
    "lag.membership": (None, 0.5),
    "lag.bundle": (None, 0.0),

    # -- context: no peer, no tier, no weight ---------------------------------
    "mac.multi.uplink": (None, 0.0),
    "merge.ambiguous": (None, -0.5),

    # -- contradictions -------------------------------------------------------
    "port.admin.down": (None, -3.0),
    "port.oper.down": (None, -2.5),
    "port.stp.blocked": (None, -0.5),
    "speed.mismatch": (None, -1.0),
    "mac.device.multiport": (None, -1.5),
    "lldp.switch.name.ambiguous": (None, -0.5),
    "venue.cross": (None, -2.0),
    "identity.collision": (None, -2.0),
    "port.contention": (None, -3.0),
    "uplink.contention": (None, -3.0),
}

# A link needs at least this score to escape 'weak', whatever its sources claim.
# Stops a lone corroboration (PoE, speed) from presenting as a real link.
MIN_SCORE_FOR_TIER = 0.5


def weight_of(source: str) -> float:
    return WEIGHTS.get(source, (None, 0.0))[1]


def cap_of(source: str) -> Optional[str]:
    return WEIGHTS.get(source, (None, None))[0]


def _demote(tier: str, steps: int = 1) -> str:
    return TIERS[max(0, TIER_RANK[tier] - steps)]


def score_link(link: Link) -> Link:
    """
    Assign score, confidence, tier and a one-sentence reason.

    Every Evidence row gets its applied weight written back, so the panel's
    arithmetic is literally the arithmetic that ran here.
    """
    total = 0.0
    best_cap, deciding = "weak", None
    contradictions: List[Evidence] = []

    for item in link.evidence:
        cap, weight = WEIGHTS.get(item.source, (None, 0.0))
        item.base_weight = weight
        item.weight = weight
        total += weight
        if item.kind == "contradict":
            contradictions.append(item)
        elif item.kind == "support" and cap:
            if TIER_RANK[cap] > TIER_RANK[best_cap]:
                best_cap, deciding = cap, item

    link.score = round(total, 4)
    link.confidence = round(1.0 / (1.0 + math.exp(-total)), 4)

    tier = best_cap
    reason = (deciding.claim if deciding else
              "Nothing directly supports this link; it comes from corroborating "
              "signals only.")

    # A tier is a claim about certainty, so it must answer to the score too.
    if TIER_RANK[tier] > TIER_RANK["weak"] and total < MIN_SCORE_FOR_TIER:
        tier = "weak"
        reason = ("Supporting evidence is outweighed by what contradicts it "
                  f"(net {total:+.1f}).")

    if contradictions:
        hard = [c for c in contradictions if WEIGHTS.get(c.source, (None, 0))[1] <= -2.0]
        if hard and TIER_RANK[tier] >= TIER_RANK["strong"]:
            tier = _demote(tier)
            reason = f"{reason} Demoted: {hard[0].claim}"
        elif hard and total < 0:
            tier = "rejected"
            reason = hard[0].claim

    link.tier = tier
    link.tier_reason = reason
    return link


VERB = {"confirm": "confirmed", "reject": "rejected", "assert": "drew"}


def override_claim(verdict: str, by: str, at: str, note: str) -> str:
    """The evidence line a human verdict writes about itself."""
    return (f"{by or 'A user'} {VERB[verdict]} this link"
            + (f" on {at[:10]}" if at else "")
            + (f": {note}" if note else "."))


def override_reason(machine_tier: str, machine_score: float, by: str,
                    disagreeing: int) -> str:
    return (f"Overridden by {by or 'a user'}. The engine scored this "
            f"{machine_tier.title()} ({machine_score:+.1f})"
            + (f"; {disagreeing} piece{'' if disagreeing == 1 else 's'} of evidence "
               f"disagree{'s' if disagreeing == 1 else ''}." if disagreeing else "."))


def override_verdict(verdict: str, machine_tier: str, machine_score: float,
                     by: str, disagreeing: int) -> Dict[str, Any]:
    """
    What a human verdict does to a link, as plain values.

    ONE definition, called by both paths that apply a verdict: the object path
    below (discovery) and the dict path in overrides.relens (read). They must
    not be allowed to drift -- test_topology_overrides.py asserts they agree.
    """
    cap, weight = WEIGHTS[f"override.user.{verdict}"]
    return {
        "tier": cap,
        "score": round(machine_score + weight, 4),
        "confidence": 1.0 if verdict != "reject" else 0.0,
        "tierReason": override_reason(machine_tier, machine_score, by, disagreeing),
        "weight": weight,
    }


def apply_overrides(links: List[Link], overrides: Dict[str, Override],
                    key_for) -> List[Link]:
    """
    Human verdicts, applied LAST -- after merging, scoring and roll-up.

    Absorbing: an override sets the tier outright. But the machine evidence is
    kept and shown beneath it, including evidence that disagrees, so the panel
    can say plainly "you confirmed this; one source disagrees." Hiding the
    disagreement would make the override look like a fact.
    """
    if not overrides:
        return links
    for link in links:
        override = overrides.get(key_for(link))
        if override is None:
            continue
        link.override = override
        # The pre-override verdict, kept in full. That is what lets the panel say
        # "the engine scored this Probable, you called it Confirmed", and what
        # lets the read path lift a baked verdict off a stored snapshot to
        # re-apply the current one.
        link.machine = {"tier": link.tier, "score": link.score,
                        "confidence": link.confidence,
                        "tierReason": link.tier_reason}
        source_id = f"override.user.{override.verdict}"
        _, weight = WEIGHTS[source_id]
        link.evidence.insert(0, Evidence(
            source=source_id,
            kind="support" if override.verdict != "reject" else "contradict",
            claim=override_claim(override.verdict, override.by, override.at,
                                 override.note),
            weight=weight, base_weight=weight,
            a=link.a.port_id or link.a.device_id,
            b=link.b.port_id or link.b.device_id,
            fields={"verdict": override.verdict, "by": override.by,
                    "at": override.at, "machine": dict(link.machine)},
            observed_at=override.at,
        ))
        disagreeing = sum(1 for e in link.evidence
                          if e.kind == "contradict" and not e.source.startswith("override"))
        applied = override_verdict(override.verdict, link.machine["tier"],
                                   link.machine["score"], override.by, disagreeing)
        link.tier = applied["tier"]
        link.score = applied["score"]
        link.confidence = applied["confidence"]
        link.tier_reason = applied["tierReason"]
    return links


def resolve_port_contention(links: List[Link]) -> List[Link]:
    """
    A physical port has at most one managed-infrastructure peer.

    Applies ONLY when both ends are managed infrastructure. An access port
    legitimately shows many endpoints, and an unmanaged hub legitimately puts
    several devices behind one port -- treating those as contention would delete
    correct links wholesale.

    The loser is demoted and told which link beat it, not deleted: "why is there
    no edge between A and B?" has to remain answerable.
    """
    by_port: Dict[str, List[Link]] = {}
    for link in links:
        if link.kind == "lag" or link.logical_of:
            continue
        for end in (link.a, link.b):
            if end.port_id and not end.device_id.startswith("ext:"):
                by_port.setdefault(end.port_id, []).append(link)

    for port_id, contenders in by_port.items():
        infra = [l for l in contenders
                 if not (l.a.device_id.startswith("ext:") or l.b.device_id.startswith("ext:"))]
        if len(infra) < 2:
            continue
        infra.sort(key=lambda l: (TIER_RANK[l.tier], l.score), reverse=True)
        winner = infra[0]
        for loser in infra[1:]:
            if loser.override is not None:
                continue                    # a human already ruled on this one
            # A link is visited once per endpoint port, so one that loses on
            # BOTH of its ports would otherwise be penalised twice for what is
            # really one finding.
            if any(e.source == "port.contention" for e in loser.evidence):
                continue
            loser.evidence.append(Evidence(
                source="port.contention", kind="contradict",
                claim=f"Port {port_id.split('#')[-1]} is already claimed by a "
                      f"stronger link ({winner.tier}), and a physical port has "
                      f"only one infrastructure peer.",
                weight=WEIGHTS["port.contention"][1],
                base_weight=WEIGHTS["port.contention"][1],
                a=port_id, b=winner.id,
                fields={"winningLink": winner.id, "winningTier": winner.tier,
                        "winningScore": winner.score},
            ))
            loser.score = round(loser.score + WEIGHTS["port.contention"][1], 4)
            loser.tier = "rejected"
            loser.tier_reason = (
                f"Rejected: port {port_id.split('#')[-1]} is already claimed by a "
                f"better-supported link.")
    return links


def resolve_single_uplink(links: List[Link], kind_of) -> List[Link]:
    """
    A device with exactly one wired uplink may only have one drawn.

    Port contention is per-PORT: one port, several links. This is the
    complementary case -- one DEVICE, several links to infrastructure -- and it
    needs its own rule, because an AP whose MAC is learned on a second port
    otherwise appears to have two uplinks to the same switch.

    Applies only to APs, where the constraint is real: an AP has one wired
    uplink, and a genuine second one would be a LAG, which roll-up would already
    have bundled. Switches are deliberately excluded -- multiple uplinks are
    normal and often the point.

    The loser is demoted, not deleted, and told what beat it: "why is there no
    edge here?" has to stay answerable.
    """
    by_device: Dict[str, List[Link]] = {}
    for link in links:
        if link.kind in ("lag", "stack", "mesh") or link.logical_of:
            continue
        for near, far in ((link.a, link.b), (link.b, link.a)):
            if kind_of(near.device_id) != "ap":
                continue
            if kind_of(far.device_id) not in ("switch", "stack", "external"):
                continue
            by_device.setdefault(near.device_id, []).append(link)

    for device_id, contenders in by_device.items():
        unique = {l.id: l for l in contenders}
        if len(unique) < 2:
            continue
        ranked = sorted(unique.values(),
                        key=lambda l: (TIER_RANK[l.tier], l.score), reverse=True)
        winner = ranked[0]
        for loser in ranked[1:]:
            if loser.override is not None:
                continue                    # a human already ruled on this one
            if any(e.source == "uplink.contention" for e in loser.evidence):
                continue
            far_ident = (winner.a.ident if winner.b.device_id == device_id
                         else winner.b.ident)
            loser.evidence.append(Evidence(
                source="uplink.contention", kind="contradict",
                claim=f"This device already has a better-supported uplink"
                      + (f" on port {far_ident}" if far_ident else "")
                      + f" ({winner.tier}), and an access point has only one.",
                weight=WEIGHTS["uplink.contention"][1],
                base_weight=WEIGHTS["uplink.contention"][1],
                a=device_id, b=winner.id,
                fields={"winningLink": winner.id, "winningTier": winner.tier,
                        "winningScore": winner.score, "winningPort": far_ident},
            ))
            loser.score = round(loser.score + WEIGHTS["uplink.contention"][1], 4)
            loser.tier = "rejected"
            loser.tier_reason = (
                "Rejected: this access point already has a better-supported "
                "uplink, and it can only have one.")
    return links


def catalogue() -> List[Dict]:
    """The weight table as data, for the UI."""
    return [{"source": source, "tierCap": cap, "weight": weight}
            for source, (cap, weight) in sorted(WEIGHTS.items())]
