"""
Evidence sources: the registry, and the contract every source obeys.

A source reads the collected data and emits CLAIMS -- "these two endpoints are
connected, and here is why". A source never computes confidence and never
decides a tier. That separation is the whole design: scoring happens once, in
one place, after merging, so two sources cannot argue about a number and the
arithmetic a user sees is the arithmetic that ran.

Mirrors the @check registry in wiredwiz/checks/framework.py, which works well
and is already familiar in this codebase.
"""

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..model import INDEPENDENT_CHANNELS, Endpoint, Evidence  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass
class Claim:
    """
    One source's assertion that two endpoints are connected.

    `evidence` carries the reason. The claim itself has no score -- merge.py
    combines claims about the same physical link and score.py weighs the
    resulting pile once.
    """
    a: Endpoint
    b: Endpoint
    kind: str = "ethernet"          # ethernet|lag|stack|mesh|wireless-client|wan
    evidence: List[Evidence] = field(default_factory=list)
    directional: bool = True        # True when observed from `a` looking at `b`
    context_only: bool = False
    # WHICH MECHANISM saw this, and from which device. Mutual agreement is only
    # meaningful between INDEPENDENT channels -- see INDEPENDENT_CHANNELS.
    channel: str = "unknown"
    observer: str = ""              # device id that made the observation

    def pair(self) -> tuple:
        """Device pair, order-independent."""
        return tuple(sorted([self.a.device_id, self.b.device_id]))


@dataclass
class Source:
    """
    A registered source's descriptive metadata.

    Note what is NOT here: the tier cap and weight the engine actually applies.
    Those live in score.WEIGHTS, keyed by the evidence `source` string, and are
    read back for the catalogue. One source may emit several evidence kinds with
    different weights (lldp.switch.mac also emits lldp.switch.portmac), so a
    per-function cap could not describe it -- and two copies of a number that
    must agree is how the catalogue silently starts lying about the engine.
    """
    id: str
    label: str
    fn: Callable
    emits: List[str] = field(default_factory=list)
    proves: str = ""
    reads: str = ""
    caveats: str = ""
    enabled_by_default: bool = True

    def to_dict(self) -> Dict[str, Any]:
        from ..model import TIER_RANK
        from ..score import WEIGHTS

        # A source may emit several evidence kinds at different weights --
        # port.down emits port.admin.down AND port.oper.down, lldp.switch.mac
        # also emits lldp.switch.portmac. Report each, plus the best tier any of
        # them can justify. Reporting one number here would have shown
        # port.down as weight 0.0, which is simply untrue.
        emitted = [{"source": name, "tierCap": WEIGHTS.get(name, (None, 0.0))[0],
                    "weight": WEIGHTS.get(name, (None, 0.0))[1]}
                   for name in (self.emits or [self.id])]
        caps = [e["tierCap"] for e in emitted if e["tierCap"]]
        best = max(caps, key=lambda c: TIER_RANK[c]) if caps else None
        return {"id": self.id, "label": self.label, "tierCap": best,
                "emits": emitted, "proves": self.proves, "reads": self.reads,
                "caveats": self.caveats, "enabledByDefault": self.enabled_by_default}


REGISTRY: List[Source] = []


def source(id: str, label: str, emits: Optional[List[str]] = None,
           proves: str = "", reads: str = "",
           caveats: str = "", enabled_by_default: bool = True, **_ignored):
    """
    Register an evidence source.

    The decorated function takes (bundle, index) and yields Claims. It must not
    raise on bad data -- a source that cannot say anything says nothing.

    Weights and tier caps are NOT declared here; they live in score.WEIGHTS.
    `**_ignored` absorbs them if passed, so a stale decorator argument cannot
    quietly contradict the real table.
    """
    def decorator(fn):
        doc = inspect.cleandoc(fn.__doc__ or "")
        REGISTRY.append(Source(id=id, label=label, fn=fn,
                               emits=list(emits or [id]),
                               proves=proves or doc.split("\n")[0],
                               reads=reads, caveats=caveats,
                               enabled_by_default=enabled_by_default))
        return fn
    return decorator


def run_sources(bundle, index, only: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Run every registered source. One that raises is recorded and skipped -- a
    broken source must not cost the whole map.
    """
    wanted = set(only) if only else None
    claims: List[Claim] = []
    ran, skipped, failed = [], [], []

    for src in REGISTRY:
        if wanted is not None and src.id not in wanted:
            skipped.append({"id": src.id, "reason": "not requested"})
            continue
        if wanted is None and not src.enabled_by_default:
            skipped.append({"id": src.id, "reason": "off by default"})
            continue
        try:
            produced = list(src.fn(bundle, index) or [])
        except Exception as exc:                                    # noqa: BLE001
            logger.exception("topology: source %s failed", src.id)
            failed.append({"id": src.id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        claims.extend(produced)
        ran.append({"id": src.id, "claims": len(produced)})

    return {"claims": claims, "sourcesRun": ran,
            "sourcesSkipped": skipped, "sourcesFailed": failed}


def catalogue() -> List[Dict[str, Any]]:
    """The weight table, for the UI's source catalogue."""
    return [src.to_dict() for src in REGISTRY]


# Importing the modules is what registers them.
from . import (aggregation, ap_side, lldp_switch, mactable,   # noqa: E402,F401
               physical, r1_topology)

__all__ = ["Claim", "Source", "REGISTRY", "source", "run_sources", "catalogue"]
