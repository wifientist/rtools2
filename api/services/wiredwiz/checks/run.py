"""
Assemble a CheckContext and run the rule library.

Kept separate from framework.py so the checks stay pure functions over data and
this module owns all the "where does the data come from" concerns.
"""

import logging
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional

from ..analyze import MIN_WINDOW, broadcast_rates, pick_pair, port_label, score_against_vlan, usable
from .framework import CheckContext, IcxConfig, run_checks

logger = logging.getLogger(__name__)


def _lldp_name(p) -> str:
    """
    An LLDP neighbour name, with the 32-character cap made visible.

    RUCKUS APs cap the LLDP System Name TLV at 32 characters, so a name that is
    exactly 32 long is almost certainly a prefix. This runs before the
    CheckContext exists, so unlike CheckContext.neighbour() it cannot resolve the
    name against managed inventory -- it only marks the cut.
    """
    n = p.get("neighborName") or ""
    return f"{n}\u2026" if len(n) == 32 else n


def _full_rates(snaps: List[dict], min_window: int) -> Optional[Dict[str, Any]]:
    """
    Per-port rates for EVERY compared port, plus the per-VLAN summary.

    analyze() truncates to a display slice; the checks need the whole set,
    because a failing optic or a discarding port is rarely in the top 25 by
    broadcast.
    """
    pair = pick_pair(snaps, min_window)
    if pair is None:
        return {"available": False,
                "reason": f"no snapshot pair spans {min_window}s"}

    prev, curr = pair
    rows, dt, resets = broadcast_rates(prev, curr)
    rows = score_against_vlan(rows)

    by_vlan = defaultdict(list)
    for r in rows:
        by_vlan[r["port"].get("unTaggedVlan") or ""].append(r["rates"]["broadcastIn"])

    flat = [{
        "portId": r["port"]["id"],
        "label": port_label(r["port"]),
        "switchName": r["port"].get("switchName"),
        "port": r["port"].get("portIdentifier"),
        "vlan": r["port"].get("unTaggedVlan"),
        "lldp": _lldp_name(r["port"]),
        "broadcastIn": r["rates"]["broadcastIn"],
        "broadcastOut": r["rates"]["broadcastOut"],
        "multicastIn": r["rates"]["multicastIn"],
        "inDiscard": r["rates"]["inDiscard"],
        "crcErr": r["rates"]["crcErr"],
        "inErr": r["rates"]["inErr"],
        "xIn": r["scores"]["broadcastIn"],
        "xOut": r["scores"]["broadcastOut"],
    } for r in rows]

    return {
        "available": True,
        "windowSeconds": round(dt),
        "from": prev["takenAt"],
        "to": curr["takenAt"],
        "portsCompared": len(flat),
        "counterResets": resets,
        "rows": flat,
        "vlanSummary": sorted(
            [{"vlan": v, "ports": len(vals),
              "median": statistics.median(vals), "max": max(vals)}
             for v, vals in by_vlan.items()],
            key=lambda x: -x["median"]),
    }


def run_health_check(snapshots: List[dict],
                     configs: Optional[Dict[str, dict]] = None,
                     min_window: int = MIN_WINDOW,
                     categories: Optional[List[str]] = None,
                     baseline: Optional[dict] = None) -> Dict[str, Any]:
    """
    Run the whole rule library over the given snapshots and (optional) configs.

    `configs` maps switch_id -> {"switchName", "model", "config"} as returned by
    crawl.fetch_redacted_config. They are passed in rather than loaded here so
    the caller decides where they came from -- a fresh explicit read, or a stored
    baseline.

    `baseline` is a previously stored config set. When both it and fresh configs
    are supplied, the drift check reports what changed between them.
    """
    good, rejected = usable(snapshots)
    if not good:
        return {"error": "no complete snapshot to analyse — re-run the crawl",
                "rejected": rejected}

    rates = _full_rates(good, min_window) if len(good) > 1 else {
        "available": False,
        "reason": "only one complete snapshot — rate-based checks need two",
    }

    parsed = {
        sid: IcxConfig(sid, c.get("switchName"), c.get("model"), c.get("config", ""))
        for sid, c in (configs or {}).items() if c.get("config")
    }

    ctx = CheckContext.build(good, rates=rates, configs=parsed, baseline=baseline)
    result = run_checks(ctx, categories=categories)

    result["context"] = {
        "snapshots": len(good),
        "rejectedSnapshots": rejected,
        "switches": len(ctx.switches),
        "ports": len(ctx.ports),
        "upPorts": len(ctx.up_ports),
        "configsAudited": len(parsed),
        "baselineTakenAt": (baseline or {}).get("takenAt"),
        "rateWindowSeconds": rates.get("windowSeconds"),
        "rateReason": rates.get("reason"),
        "latestSnapshot": ctx.latest["takenAt"],
    }
    return result
