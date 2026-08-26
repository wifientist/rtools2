"""
MAC address table sizing across the estate.

Two things constrain how this can honestly be presented:

1. **Capacity is not discoverable.** Nothing in the R1 API or the running config
   reports the hardware forwarding-table size. The only `system-max` in these
   configs is `system-max vlan`, which is the VLAN table. So there is no
   utilisation percentage to show, and inventing one from published datasheet
   figures would be a guess dressed as a measurement. Sizing here is therefore
   RELATIVE -- against same-model peers -- and GROWTH-based across snapshots,
   which is what actually detects a problem anyway.

2. **R1 reports two different numbers.** The switch DTO carries `clientCount`,
   and the switch-clients query returns rows you can count. They agreed on 165
   of 195 switches on the estate this was built against; the rest differed,
   including one distribution switch by 98 entries on a verifiably complete
   crawl. Both numbers are reported side by side rather than picking a winner.
"""

import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

CAPACITY_NOTE = (
    "Neither the R1 API nor the running config exposes the hardware MAC table "
    "size, so there is no utilisation percentage here — only counts, peer "
    "comparison and growth. Counts far below any ICX table limit still matter: "
    "what signals trouble is a switch out of line with its peers, or a table "
    "that grows or churns between snapshots."
)


def _norm(m) -> str:
    return (m or "").lower()


def summarize_mac_tables(snapshots: List[dict]) -> Dict[str, Any]:
    """
    Per-switch MAC table size for the latest snapshot, with growth against the
    earliest one supplied.
    """
    latest = snapshots[-1]
    previous = snapshots[0] if len(snapshots) > 1 else None

    def counts_by_switch(snap):
        c = Counter()
        for m in snap["macs"]:
            c[_norm(m.get("switchMac"))] += 1
        return c

    now = counts_by_switch(latest)
    before = counts_by_switch(previous) if previous else {}

    # per-switch detail from the latest snapshot
    by_sw_port = defaultdict(Counter)
    by_sw_vlan = defaultdict(Counter)
    for m in latest["macs"]:
        sw = _norm(m.get("switchMac"))
        by_sw_port[sw][m.get("switchPort")] += 1
        by_sw_vlan[sw][m.get("clientVlan")] += 1

    up_ports = Counter()
    port_meta = {}
    for p in latest["ports"]:
        sw = _norm(p.get("switchMac"))
        if str(p.get("status", "")).lower() == "up":
            up_ports[sw] += 1
        port_meta[(sw, p.get("portIdentifier"))] = p

    rows = []
    for s in latest["switches"]:
        sw = _norm(s.get("switchMac") or s.get("id"))
        learned = now.get(sw, 0)
        prev = before.get(sw) if previous else None
        ports = by_sw_port[sw].most_common(1)
        densest = None
        if ports:
            pid, cnt = ports[0]
            meta = port_meta.get((sw, pid), {})
            densest = {"port": pid, "macs": cnt,
                       "lldp": meta.get("neighborName") or meta.get("neighborMacAddress") or ""}
        rows.append({
            "id": s.get("id"), "name": s.get("name"), "model": s.get("model"),
            "venueName": s.get("venueName"), "deviceStatus": s.get("deviceStatus"),
            "numOfPorts": s.get("numOfPorts"), "upPorts": up_ports.get(sw, 0),
            "learned": learned,
            "clientCount": s.get("clientCount"),
            "countsAgree": s.get("clientCount") == learned,
            "previousLearned": prev,
            "growth": (learned - prev) if prev is not None else None,
            "macsPerUpPort": round(learned / up_ports[sw], 1) if up_ports.get(sw) else None,
            "densestPort": densest,
            "vlans": [{"vlan": v, "macs": n} for v, n in by_sw_vlan[sw].most_common(8)],
        })

    online = [r for r in rows if r["deviceStatus"] == "ONLINE"]
    learned_vals = sorted(r["learned"] for r in online)

    # Peer baseline per model — an ICX7850 distribution switch is expected to
    # hold far more than a 12-port closet switch, so a fleet-wide average would
    # flag the wrong devices.
    per_model = defaultdict(list)
    for r in online:
        per_model[r["model"]].append(r["learned"])
    model_stats = {
        m: {"count": len(v), "median": statistics.median(v), "max": max(v)}
        for m, v in per_model.items()
    }
    for r in rows:
        st = model_stats.get(r["model"])
        r["modelMedian"] = st["median"] if st else None
        r["vsModelMedian"] = (
            round(r["learned"] / st["median"], 1) if st and st["median"] else None)

    rows.sort(key=lambda r: -r["learned"])

    return {
        "takenAt": latest["takenAt"],
        "previousTakenAt": previous["takenAt"] if previous else None,
        "capacityNote": CAPACITY_NOTE,
        "totals": {
            "switches": len(rows),
            "online": len(online),
            "learnedTotal": sum(r["learned"] for r in rows),
            "clientCountTotal": sum(r["clientCount"] or 0 for r in rows),
            "median": statistics.median(learned_vals) if learned_vals else 0,
            "max": max(learned_vals) if learned_vals else 0,
            "countsDisagreeOn": sum(1 for r in online if not r["countsAgree"]),
        },
        "byModel": model_stats,
        "switches": rows,
    }
