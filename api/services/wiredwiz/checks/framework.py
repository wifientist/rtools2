"""
WiredWiz check engine.

A registry of the checks a senior ICX/FastIron engineer would actually run,
each one a small pure function over a CheckContext that yields Findings.

Design rules, learned from the analysis work that came before this:

  * Every finding carries EVIDENCE -- the actual values that triggered it --
    because "high broadcast on port X" is unactionable without the number, the
    baseline it is being compared to, and the window it was measured over.
  * Every finding says why it matters and what to do. A checklist that only
    reports state makes the reader do the expert part.
  * Checks declare what data they need (`needs`). A check that needs configs is
    skipped, and reported as skipped, when configs were not collected -- it is
    never silently absent, because a missing check reads like a passing check.
  * Confidence is explicit. Some checks prove a defect; others flag a smell.
    Ranking them identically is how a tool trains its user to ignore it.
"""

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

# Severity drives ordering. "critical" means it can take the network down or is
# actively degrading it; "warning" means it will bite under some condition;
# "info" is hygiene worth knowing but not urgent.
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    check_id: str
    title: str
    severity: str                      # critical | warning | info
    category: str
    entity: str                        # what it is about: switch, port, VLAN, or "fabric"
    detail: str                        # what was found, in one or two sentences
    evidence: Dict[str, Any] = field(default_factory=dict)
    remediation: str = ""
    confidence: str = "high"           # high | medium -- medium means "smell, verify"

    @staticmethod
    def _tidy(value):
        """
        Round floats in evidence. Rates are computed by division and carry full
        binary precision, so raw evidence reads `broadcastInPerSec=3320.713677115661`
        — noise that makes a report look unconsidered and is harder to scan.
        """
        if isinstance(value, float):
            return round(value, 2)
        if isinstance(value, dict):
            return {k: Finding._tidy(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [Finding._tidy(v) for v in value]
        return value

    def as_dict(self):
        return {
            "checkId": self.check_id, "title": self.title, "severity": self.severity,
            "category": self.category, "entity": self.entity, "detail": self.detail,
            "evidence": self._tidy(self.evidence), "remediation": self.remediation,
            "confidence": self.confidence,
        }


@dataclass
class Check:
    id: str
    title: str
    category: str
    needs: str                         # "snapshot" | "rates" | "configs"
    fn: Callable
    description: str = ""
    trigger: str = ""                  # the condition that makes it fire, in plain terms

    @property
    def summary(self) -> str:
        """First sentence of the description — the one-line 'what it checks'."""
        text = " ".join((self.description or "").split())
        if not text:
            return ""
        for end in (". ", "? ", "! "):
            i = text.find(end)
            if i > 0:
                return text[:i + 1]
        return text if len(text) < 220 else text[:217] + "…"


REGISTRY: List[Check] = []


def check(id: str, title: str, category: str, needs: str = "snapshot",
          description: str = "", trigger: str = ""):
    """
    Register a check.

    `description` defaults to the function docstring, which is where the reasoning
    lives. `trigger` states the actual firing condition — thresholds and all —
    because "checks broadcast rates" does not tell an engineer whether 400 pps on
    their uplink would have been reported.
    """
    def deco(fn):
        doc = inspect.cleandoc(fn.__doc__ or "") if fn.__doc__ else ""
        REGISTRY.append(Check(id=id, title=title, category=category, needs=needs,
                              fn=fn, description=description or doc, trigger=trigger))
        return fn
    return deco


# ── FastIron config parsing ──────────────────────────────────────────────────
# Configs are line-oriented with one level of indentation for block members.
# That is enough structure for every check here; nothing needs a real grammar.

class IcxConfig:
    """One switch's running config, pre-indexed for the checks."""

    def __init__(self, switch_id: str, name: str, model: str, text: str):
        self.switch_id = switch_id
        self.name = name or switch_id
        self.model = model or ""
        self.text = text or ""
        self.lines = self.text.splitlines()
        self.blocks = self._parse_blocks()

    def _parse_blocks(self):
        """Top-level command -> list of its indented member lines."""
        blocks = []
        current_head, current_body = None, []
        for raw in self.lines:
            if not raw.strip() or raw.lstrip().startswith("!"):
                continue
            if raw[:1].isspace():
                current_body.append(raw.strip())
            else:
                if current_head is not None:
                    blocks.append((current_head, current_body))
                current_head, current_body = raw.strip(), []
        if current_head is not None:
            blocks.append((current_head, current_body))
        return blocks

    def has(self, pattern: str) -> bool:
        return re.search(pattern, self.text, re.M | re.I) is not None

    def find(self, pattern: str) -> List[str]:
        return [m.group(0).strip() for m in re.finditer(pattern, self.text, re.M | re.I)]

    def blocks_matching(self, head_pattern: str):
        rx = re.compile(head_pattern, re.I)
        return [(h, b) for h, b in self.blocks if rx.match(h)]

    def vlans(self) -> Dict[str, List[str]]:
        """VLAN id -> its block body."""
        out = {}
        for head, body in self.blocks_matching(r"vlan\s+\d+"):
            m = re.match(r"vlan\s+(\d+)", head, re.I)
            if m:
                out[m.group(1)] = body
        return out

    def interfaces(self) -> Dict[str, List[str]]:
        """`ethernet 1/1/5` -> its block body."""
        out = {}
        for head, body in self.blocks_matching(r"interface\s+ethernet"):
            m = re.match(r"interface\s+ethernet\s+(\S+)", head, re.I)
            if m:
                out[m.group(1)] = body
        return out


# ── Context ──────────────────────────────────────────────────────────────────

def _norm_mac(m) -> str:
    return (m or "").lower().replace("-", ":").replace(".", "")


def _as_int(v) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


def _is_up(p) -> bool:
    return str(p.get("status", "")).lower() == "up"


@dataclass
class CheckContext:
    """Everything the checks read. Built once per run."""
    snapshots: List[dict]
    latest: dict
    rates: Optional[dict] = None                  # analyze.py "rates" block
    configs: Dict[str, IcxConfig] = field(default_factory=dict)
    # A previously stored baseline, when one exists, so drift can be computed.
    baseline: Optional[dict] = None

    # derived, filled by build()
    switches: List[dict] = field(default_factory=list)
    ports: List[dict] = field(default_factory=list)
    up_ports: List[dict] = field(default_factory=list)
    switch_by_mac: Dict[str, dict] = field(default_factory=dict)
    ports_by_switch: Dict[str, List[dict]] = field(default_factory=dict)
    macs_by_port: Dict[str, List[dict]] = field(default_factory=dict)
    uplink_ports: set = field(default_factory=set)
    rate_by_port_id: Dict[str, dict] = field(default_factory=dict)

    @staticmethod
    def build(snapshots, rates=None, configs=None, baseline=None):
        latest = snapshots[-1]
        ctx = CheckContext(snapshots=snapshots, latest=latest, rates=rates,
                           configs=configs or {}, baseline=baseline)
        ctx.switches = latest["switches"]
        ctx.ports = latest["ports"]
        ctx.up_ports = [p for p in ctx.ports if _is_up(p)]
        ctx.switch_by_mac = {_norm_mac(s.get("switchMac") or s.get("id")): s
                             for s in ctx.switches}
        for p in ctx.ports:
            ctx.ports_by_switch.setdefault(_norm_mac(p.get("switchMac")), []).append(p)
        for m in latest["macs"]:
            ctx.macs_by_port.setdefault(m.get("switchPortId"), []).append(m)
        known = set(ctx.switch_by_mac) - {""}
        ctx.uplink_ports = {p["id"] for p in ctx.ports
                            if _norm_mac(p.get("neighborMacAddress")) in known}
        if rates and rates.get("available"):
            for r in rates.get("rows", []):
                if r.get("portId"):
                    ctx.rate_by_port_id[r["portId"]] = r
        return ctx

    # helpers the checks share
    def port_label(self, p) -> str:
        return f"{p.get('switchName')} {p.get('portIdentifier')}"

    def is_uplink(self, p) -> bool:
        return p["id"] in self.uplink_ports

    def mac_count(self, p) -> int:
        return len(self.macs_by_port.get(p["id"], []))

    def mac_count_for_switch(self, s) -> int:
        """Learned MAC entries on one switch, across all its ports."""
        mac = _norm_mac(s.get("switchMac") or s.get("id"))
        return sum(1 for m in self.latest["macs"]
                   if _norm_mac(m.get("switchMac")) == mac)


def run_checks(ctx: CheckContext, categories: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Run every registered check whose data is available.

    Skipped checks are reported explicitly. A check that quietly does not run
    looks exactly like a check that passed, and that is how tools lie.
    """
    have = {"snapshot": True,
            "rates": bool(ctx.rates and ctx.rates.get("available")),
            "configs": bool(ctx.configs)}

    findings: List[Finding] = []
    ran, skipped, failed = [], [], []

    for c in REGISTRY:
        if categories and c.category not in categories:
            continue
        if not have.get(c.needs, False):
            skipped.append({
                "checkId": c.id, "title": c.title, "category": c.category,
                "needs": c.needs, "summary": c.summary, "trigger": c.trigger,
                "reason": {
                    "configs": "no config data — capture a baseline or tick 'Re-read "
                               "configs live'",
                    "rates": "needs two snapshots at least the rate window apart",
                }.get(c.needs, f"needs {c.needs}"),
            })
            continue
        try:
            produced = list(c.fn(ctx) or [])
        except Exception as e:                       # a broken check must not kill the run
            failed.append({"checkId": c.id, "error": f"{type(e).__name__}: {e}"})
            continue
        findings.extend(produced)
        ran.append({"checkId": c.id, "title": c.title, "category": c.category,
                    "needs": c.needs, "summary": c.summary, "trigger": c.trigger,
                    "findings": len(produced)})

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.category, f.check_id))
    counts = {s: sum(1 for f in findings if f.severity == s)
              for s in ("critical", "warning", "info")}
    return {
        "findings": [f.as_dict() for f in findings],
        "counts": counts,
        "checksRun": ran,
        "checksSkipped": skipped,
        "checksFailed": failed,
        "dataAvailable": have,
    }
