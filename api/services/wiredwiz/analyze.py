"""
WiredWiz loop analysis.

Pure functions over stored snapshots -- no API calls, no network. Shared by the
CLI script and the API router so the dashboard and the terminal always agree.

Four independent signals, because no single R1 field says "loop here":

  1. Broadcast RATE. Port counters are cumulative since reboot, so a big number
     mostly means long uptime. R1 refreshes them in a batch about every 300s
     (measured), so rates need a window of >= MIN_WINDOW or one refresh tick
     divided by a short interval reads as a huge fake rate.
  2. MAC moves -- a MAC on two non-uplink ports at once, or ping-ponging
     between ports. Uplinks are excluded via LLDP: a MAC on an access port and
     on the trunk carrying it is ordinary forwarding, not a loop.
  3. MAC density on a port with NO LLDP neighbour. Density behind a visible
     switch is an uplink; density behind nothing is an unmanaged device.
  4. LLDP topology cycles, minus LAG members and stack links.
"""

import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

COUNTER_FIELDS = ["broadcastIn", "broadcastOut", "multicastIn", "multicastOut",
                  "rx", "tx", "crcErr", "inErr", "outErr", "inDiscard"]

# R1 batches counter updates roughly every 300s; 900s spans 3 +/-1 of them.
MIN_WINDOW = 900


_MAC_JUNK = re.compile(r"[^0-9a-fA-F]")
_MAC_HEX = re.compile(r"^[0-9a-f]{12}$")


def norm_mac(m) -> str:
    """
    A MAC as 12 lowercase hex characters, or "" if it is not a MAC.

    STRICT on purpose. The previous version did
    `.lower().replace("-", ":").replace(".", "")`, which produced TWO different
    canonical forms depending on the separator it was given: colon-separated and
    dash-separated input came back colon-form, while Cisco-style dotted and bare
    input came back as plain hex. Two values for the same address never compare
    equal, so a MAC written one way silently failed to match the same MAC
    written the other. Every R1 field WiredWiz reads happens to be colon-form,
    which is why it never bit -- but it is a trap for the next caller, and the
    Topology tool hit exactly this.

    Anything that will not normalise returns "" rather than falling through as
    a truthy value: a malformed MAC must never become a join key.
    """
    s = _MAC_JUNK.sub("", str(m or "")).lower()
    return s if _MAC_HEX.match(s) else ""


def mac_display(m) -> str:
    """Colon form, for anywhere a MAC is shown rather than compared."""
    s = norm_mac(m)
    return ":".join(s[i:i + 2] for i in range(0, 12, 2)) if s else str(m or "")


def as_int(v) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


def is_up(p) -> bool:
    return str(p.get("status", "")).lower() == "up"


def port_label(p) -> str:
    return f"{p.get('switchName')} {p.get('portIdentifier')}"


def usable(snaps: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    Split snapshots into usable and rejected. A crawl that came up short must
    never be diffed -- the missing rows read downstream as ports disappearing
    from the network.
    """
    ok, bad = [], []
    for s in snaps:
        c = s.get("completeness")
        if c and c.get("incomplete"):
            bad.append({"takenAt": s.get("takenAt"),
                        "reason": f"incomplete crawl ({c.get('collected')}/{c.get('expected')} rows)"})
        else:
            ok.append(s)
    return ok, bad


def pick_pair(snaps: List[Dict], min_window: int = MIN_WINDOW) -> Optional[Tuple[Dict, Dict]]:
    """Most recent snapshot pair separated by at least `min_window` seconds."""
    for i in range(len(snaps) - 1, 0, -1):
        for j in range(i - 1, -1, -1):
            if snaps[i]["takenAtEpoch"] - snaps[j]["takenAtEpoch"] >= min_window:
                return snaps[j], snaps[i]
    return None


def is_stack_link(p) -> bool:
    """
    Whether a port is ACTUALLY part of a formed stack.

    `usedInFormingStack` is a CAPABILITY flag, not a state: it is true on every
    stacking-capable port of a stacking-capable model, whether or not a stack
    exists. Measured on a 195-switch estate: 332 ports carried the flag but only
    18 named a stacking peer, and 178 of the rest were live switch-to-switch
    uplinks -- including a distribution switch's uplink to the core.

    Loop and redundancy detection excluded all 332, so it was blind to any ring
    running through those 178 links. Only a populated `stackingNeighborPort`
    shows a stack interconnect that exists.
    """
    return bool(str(p.get("stackingNeighborPort") or "").strip())


def uplink_ports(snap: Dict) -> set:
    """Ports whose LLDP neighbour is another managed switch in this tenant."""
    switch_macs = {norm_mac(x.get("switchMac") or x.get("id")) for x in snap["switches"]}
    switch_macs.discard("")
    return {(p.get("switchName"), p.get("portIdentifier")) for p in snap["ports"]
            if norm_mac(p.get("neighborMacAddress")) in switch_macs}


def broadcast_rates(prev: Dict, curr: Dict) -> Tuple[List[Dict], float, int]:
    """Per-port counter deltas per second. Ports whose counters went backwards
    (reboot or counter clear) are dropped, not reported as negative rates."""
    dt = curr["takenAtEpoch"] - prev["takenAtEpoch"]
    if dt <= 0:
        return [], dt, 0
    before = {p["id"]: p for p in prev["ports"]}

    out, resets = [], 0
    for p in curr["ports"]:
        old = before.get(p["id"])
        if not old or not is_up(p):
            continue
        rates, reset = {}, False
        for f in COUNTER_FIELDS:
            d = as_int(p.get(f)) - as_int(old.get(f))
            if d < 0:
                reset = True
                break
            rates[f] = d / dt
        if reset:
            resets += 1
            continue
        out.append({"port": p, "rates": rates})
    return out, dt, resets


def score_against_vlan(rows: List[Dict]) -> List[Dict]:
    """
    Score each port's broadcast rate against the median for up ports in the same
    untagged VLAN -- a busy VLAN's normal is a quiet VLAN's emergency.

    A median of 0 is the common case (most ports are quiet). Dividing by it, or
    by a 1.0 floor, would just echo the raw rate back as a fake multiple, so the
    score is None there and the raw rate has to speak for itself.
    """
    by_vlan = defaultdict(list)
    for r in rows:
        by_vlan[r["port"].get("unTaggedVlan") or "?"].append(r)

    for group in by_vlan.values():
        for direction in ("broadcastIn", "broadcastOut"):
            vals = sorted(x["rates"][direction] for x in group)
            med = statistics.median(vals) if vals else 0
            for x in group:
                x.setdefault("scores", {})[direction] = (
                    x["rates"][direction] / med if med > 0 else None)
    return rows


def mac_analysis(snaps: List[Dict]) -> Dict[str, Any]:
    seen = defaultdict(list)
    dupes, dupe_detail = Counter(), defaultdict(set)
    suppressed = 0

    for i, s in enumerate(snaps):
        uplinks = uplink_ports(s)
        per_mac = defaultdict(set)
        for m in s["macs"]:
            mac = norm_mac(m.get("clientMac"))
            if mac:
                per_mac[mac].add((m.get("switchName"), m.get("switchPort"), m.get("clientVlan")))
        for mac, keys in per_mac.items():
            seen[mac].append((i, sorted(keys)))
            if len(keys) > 1:
                access = {k for k in keys if (k[0], k[1]) not in uplinks}
                if len(access) > 1:
                    dupes[mac] += 1
                    dupe_detail[mac].update(keys)
                else:
                    suppressed += 1

    moves, move_detail = Counter(), defaultdict(list)
    for mac, hist in seen.items():
        for (_, a), (_, b) in zip(hist, hist[1:]):
            if a != b:
                moves[mac] += 1
                move_detail[mac].append((a, b))

    return {
        "duplicates": [{"mac": mac_display(m), "snapshots": n,
                        "places": sorted(dupe_detail[m])}
                       for m, n in dupes.most_common(50)],
        "suppressedUplinkDuplicates": suppressed,
        "moves": [{"mac": mac_display(m), "count": n, "last": move_detail[m][-1]}
                  for m, n in moves.most_common(50)],
    }


def mac_density(snap: Dict) -> List[Dict]:
    per_port = Counter(m.get("switchPortId") for m in snap["macs"])
    ports = {p["id"]: p for p in snap["ports"]}
    rows = []
    for pid, n in per_port.items():
        p = ports.get(pid)
        if not p:
            continue
        rows.append({
            "label": port_label(p),
            "switchName": p.get("switchName"),
            "port": p.get("portIdentifier"),
            "macs": n,
            "lldp": p.get("neighborName") or p.get("neighborMacAddress") or "",
            "vlan": p.get("unTaggedVlan"),
        })
    rows.sort(key=lambda r: -r["macs"])
    return rows


def topology(snap: Dict) -> Dict[str, Any]:
    names = {norm_mac(s.get("switchMac") or s.get("id")): s.get("name")
             for s in snap["switches"]}
    switch_macs = set(names) - {""}

    edges = []
    for p in snap["ports"]:
        if not is_up(p):
            continue
        nb, me = norm_mac(p.get("neighborMacAddress")), norm_mac(p.get("switchMac"))
        if nb and me and nb in switch_macs and nb != me:
            edges.append((me, nb, p))

    pairs = defaultdict(list)
    # Ports each switch has facing the other, kept PER SIDE. `edges` holds one
    # entry per port row, so a single cable between A:1/3/1 and B:1/2/3 appears
    # twice -- once from each end. Counting distinct port identifiers across
    # both ends therefore called every ordinary link with mismatched port
    # numbers a "redundant pair", which on a 195-switch estate was most of them.
    # Redundancy means ONE switch has TWO ports facing the same neighbour.
    per_side = defaultdict(lambda: defaultdict(set))
    for me, nb, p in edges:
        key = tuple(sorted((me, nb)))
        pairs[key].append(p)
        per_side[key][me].add(p.get("portIdentifier"))

    redundant = [
        {"a": names.get(a) or mac_display(a), "b": names.get(b) or mac_display(b),
         "ports": sorted({ident for side in per_side[(a, b)].values()
                          for ident in side})}
        for (a, b), ports in pairs.items()
        if max((len(side) for side in per_side[(a, b)].values()), default=0) > 1
        and not any(as_int(p.get("lagId")) for p in ports)
        and not any(is_stack_link(p) for p in ports)
    ]

    # Back edges in the LLDP graph, excluding LAG members and stack links.
    adj = defaultdict(set)
    for me, nb, p in edges:
        if as_int(p.get("lagId")) or is_stack_link(p):
            continue
        adj[me].add(nb)
        adj[nb].add(me)

    # Sorted iteration, deliberately. The walk below reports one edge per ring
    # as the "back edge", and which one it picks depends entirely on traversal
    # order -- so iterating sets made the answer depend on string hashing and
    # shift between runs for the same data. Any edge of a ring is a defensible
    # answer; silently reporting a different one each time is not.
    seen, parent, back = set(), {}, []
    for root in sorted(adj):
        if root in seen:
            continue
        seen.add(root)
        parent[root] = None
        stack = [root]
        while stack:
            node = stack.pop()
            for nb in sorted(adj[node]):
                if nb not in seen:
                    seen.add(nb)
                    parent[nb] = node
                    stack.append(nb)
                elif parent.get(node) != nb:
                    pair = tuple(sorted((node, nb)))
                    if pair not in {tuple(sorted(x)) for x in back}:
                        back.append((node, nb))

    return {
        "linkCount": len(edges),
        "switchCount": len(snap["switches"]),
        "cycles": [{"a": names.get(a) or mac_display(a),
                    "b": names.get(b) or mac_display(b)} for a, b in back],
        "redundantPairs": redundant,
    }


def analyze(snaps: List[Dict], min_window: int = MIN_WINDOW, top: int = 25) -> Dict[str, Any]:
    """Full report over a list of snapshots (oldest first)."""
    good, rejected = usable(snaps)
    if not good:
        return {"error": "every snapshot is incomplete -- re-run the crawl",
                "rejected": rejected}

    latest = good[-1]
    result: Dict[str, Any] = {
        "rejected": rejected,
        "snapshots": [{"takenAt": s["takenAt"], "ports": len(s["ports"]),
                       "macs": len(s["macs"]), "switches": len(s["switches"])}
                      for s in good],
        "latest": {
            "takenAt": latest["takenAt"],
            "switches": len(latest["switches"]),
            "ports": len(latest["ports"]),
            "upPorts": sum(1 for p in latest["ports"] if is_up(p)),
            "macs": len(latest["macs"]),
            "venues": latest.get("venues", {}),
            "configs": len(latest.get("configs") or {}),
        },
        "minWindow": min_window,
    }

    pair = pick_pair(good, min_window)
    if pair is None:
        gap = (good[-1]["takenAtEpoch"] - good[-2]["takenAtEpoch"]) if len(good) > 1 else 0
        result["rates"] = {
            "available": False,
            "gapSeconds": round(gap),
            "reason": (f"no snapshot pair spans {min_window}s. R1 refreshes port counters "
                       f"about every 300s, so a shorter window divides one refresh tick by "
                       f"the interval and reports a rate far too high."),
        }
    else:
        prev, curr = pair
        rows, dt, resets = broadcast_rates(prev, curr)
        rows = score_against_vlan(rows)
        # Rank on the weaker direction: a loop pushes broadcast both ways on the
        # same port, whereas a chatty host only pushes one way.
        rows.sort(key=lambda r: -min(r["rates"]["broadcastIn"], r["rates"]["broadcastOut"]))

        by_vlan = defaultdict(list)
        for r in rows:
            by_vlan[r["port"].get("unTaggedVlan") or "?"].append(r["rates"]["broadcastIn"])

        result["rates"] = {
            "available": True,
            "windowSeconds": round(dt),
            "from": prev["takenAt"],
            "to": curr["takenAt"],
            "portsCompared": len(rows),
            "counterResets": resets,
            "top": [{
                "label": port_label(r["port"]),
                "switchName": r["port"].get("switchName"),
                "port": r["port"].get("portIdentifier"),
                "vlan": r["port"].get("unTaggedVlan"),
                "lldp": r["port"].get("neighborName") or "",
                "broadcastIn": round(r["rates"]["broadcastIn"], 1),
                "broadcastOut": round(r["rates"]["broadcastOut"], 1),
                "multicastIn": round(r["rates"]["multicastIn"], 1),
                "inDiscard": round(r["rates"]["inDiscard"], 1),
                "crcErr": round(r["rates"]["crcErr"], 2),
                "inErr": round(r["rates"]["inErr"], 2),
                "xIn": round(r["scores"]["broadcastIn"], 1) if r["scores"]["broadcastIn"] else None,
                "xOut": round(r["scores"]["broadcastOut"], 1) if r["scores"]["broadcastOut"] else None,
            } for r in rows[:top]],
            "vlanSummary": sorted(
                [{"vlan": v, "ports": len(vals),
                  "median": round(statistics.median(vals), 1), "max": round(max(vals), 1)}
                 for v, vals in by_vlan.items()],
                key=lambda x: -x["median"]),
        }

    result["macs"] = mac_analysis(good)
    density = mac_density(latest)
    result["density"] = {
        "top": density[:top],
        "blindPorts": [d for d in density if not d["lldp"]][:top],
        "blindCount": sum(1 for d in density if not d["lldp"]),
        "totalPorts": len(density),
    }
    result["topology"] = topology(latest)

    ups = [p for p in latest["ports"] if is_up(p)]
    result["errors"] = {
        f: {"nonzeroPorts": sum(1 for p in ups if as_int(p.get(f))),
            "upPorts": len(ups),
            "top": [{"label": port_label(p), "value": as_int(p.get(f))}
                    for p in sorted(ups, key=lambda p: -as_int(p.get(f)))[:10]
                    if as_int(p.get(f))]}
        for f in ("crcErr", "inErr", "inDiscard")
    }
    return result
