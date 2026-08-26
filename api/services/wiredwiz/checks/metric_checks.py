"""
Metric and state checks — what an engineer looks at on a live switch.

Counters from R1 are cumulative since reboot and refresh only about every 300s,
so anything phrased as a rate needs the `rates` block (two snapshots far enough
apart). Checks that only need current state work off a single snapshot.
"""

import re
from collections import Counter, defaultdict

from .framework import Finding, check, _as_int, _is_up, _norm_mac

CAT_LOOP = "loop-evidence"
CAT_PHY = "physical-layer"
CAT_CAP = "capacity"
CAT_STAB = "stability"
CAT_TOPO = "topology"

# A loop shows sustained broadcast in BOTH directions on the same port. Normal
# uplinks on this scale of campus sit in the hundreds/sec; four figures both ways
# is not normal aggregation.
BROADCAST_STORM_PPS = 1000
BROADCAST_WARN_PPS = 300


def _rates(ctx):
    """Every compared port, not the display slice — a failing optic is rarely top-25."""
    return (ctx.rates or {}).get("rows", []) if (ctx.rates or {}).get("available") else []


# ── Loop evidence ────────────────────────────────────────────────────────────

@check("broadcast-bidirectional", "Sustained broadcast in both directions", CAT_LOOP,
       needs="rates",
       trigger="min(broadcast in/s, out/s) >= 300 on one port; critical at >= 1000. "
               "Ports on the same switch are grouped into one finding once 3 or more "
               "fire, because the concentration is the signal")
def broadcast_bidirectional(ctx):
    """
    The distinguishing fingerprint. A chatty host floods one way; a loop pushes
    the same frames round and round, so the port sees heavy broadcast INBOUND and
    OUTBOUND at once.

    Grouped by switch rather than emitted per port. A loop lights up many ports
    at once, and 46 separate critical findings hide the one thing that matters —
    that they cluster on two distribution switches.
    """
    hits = []
    for r in _rates(ctx):
        lo = min(r["broadcastIn"], r["broadcastOut"])
        if lo < BROADCAST_WARN_PPS:
            continue
        hits.append((lo, r))
    if not hits:
        return

    by_switch = defaultdict(list)
    for lo, r in hits:
        by_switch[r.get("switchName") or "?"].append((lo, r))

    window = ctx.rates["windowSeconds"]
    for switch, rows in sorted(by_switch.items(), key=lambda kv: -len(kv[1])):
        rows.sort(key=lambda x: -x[0])
        worst = rows[0][1]
        severe = rows[0][0] >= BROADCAST_STORM_PPS

        if len(rows) < 3:
            for lo, r in rows:
                yield Finding(
                    "broadcast-bidirectional",
                    f"{r['label']} — broadcast heavy in both directions",
                    "critical" if lo >= BROADCAST_STORM_PPS else "warning", CAT_LOOP,
                    r["label"],
                    f"{r['broadcastIn']:,.0f} broadcast/s in and {r['broadcastOut']:,.0f}/s "
                    f"out over a {window}s window. Traffic circulating in both directions "
                    "on one port is the classic loop signature — a single talkative host "
                    "only pushes one way.",
                    {"broadcastInPerSec": r["broadcastIn"],
                     "broadcastOutPerSec": r["broadcastOut"],
                     "vlan": r["vlan"] or "trunk", "lldpNeighbour": r["lldp"] or None,
                     "windowSeconds": window,
                     "vlanMedianMultipleIn": r.get("xIn"),
                     "vlanMedianMultipleOut": r.get("xOut")},
                    "Trace this port. If it has no LLDP neighbour, something unmanaged is "
                    "on the far end — shut the port and see whether the rate collapses.",
                    confidence="high" if lo >= BROADCAST_STORM_PPS else "medium",
                )
            continue

        yield Finding(
            "broadcast-bidirectional",
            f"{switch} — {len(rows)} ports with heavy bidirectional broadcast "
            f"(peak {rows[0][1]['broadcastIn']:,.0f} in / "
            f"{rows[0][1]['broadcastOut']:,.0f} out per second)",
            "critical" if severe else "warning", CAT_LOOP, switch,
            f"{len(rows)} ports on this switch are carrying heavy broadcast in BOTH "
            f"directions over a {window}s window. One port doing that can be a chatty "
            "host; many ports on the same switch doing it means the switch is flooding "
            "the same traffic out of everything, which is what a loop in its broadcast "
            "domain looks like from here. Ports with no untagged VLAN are trunks, where "
            "aggregated broadcast from every VLAN is expected to be higher.",
            {"switch": switch, "portsAffected": len(rows), "windowSeconds": window,
             "ports": [{"port": r["port"], "vlan": r["vlan"] or "trunk",
                        "broadcastInPerSec": round(r["broadcastIn"]),
                        "broadcastOutPerSec": round(r["broadcastOut"]),
                        "lldpNeighbour": r["lldp"] or None} for _, r in rows[:15]],
             "worstPort": worst["port"]},
            "Start at the worst port. If these are all trunks on a distribution switch, "
            "the loop is downstream of it — follow the highest-rate trunk toward the "
            "access layer.",
        )


@check("vlan-domain-elevated", "Whole VLAN broadcast domain elevated", CAT_LOOP, needs="rates",
       trigger="a VLAN with >= 5 up ports whose MEDIAN broadcast-in >= 50/s; critical at >= 200/s")
def vlan_domain_elevated(ctx):
    """
    A loop lifts every port in the affected VLAN, not one. A high median across
    the domain separates a real loop from one noisy device.
    """
    for v in (ctx.rates or {}).get("vlanSummary", []):
        if v["ports"] < 5 or v["median"] < 50:
            continue
        yield Finding(
            "vlan-domain-elevated",
            f"VLAN {v['vlan'] or 'trunk'} — broadcast elevated across the whole domain",
            "critical" if v["median"] >= 200 else "warning", CAT_LOOP,
            f"VLAN {v['vlan'] or 'trunk'}",
            f"Median broadcast across {v['ports']} ports in this VLAN is {v['median']:,.0f}/s "
            f"(peak {v['max']:,.0f}/s). A single misbehaving host raises one port; raising the "
            "median of the entire domain means the frames are being flooded to everyone, "
            "which is what a loop does.",
            {"vlan": v["vlan"], "ports": v["ports"], "medianPerSec": v["median"],
             "maxPerSec": v["max"]},
            "Treat this VLAN as the affected broadcast domain and hunt the loop inside it.",
        )


@check("mac-flapping", "MAC address moving between ports", CAT_LOOP, needs="snapshot",
       trigger="a MAC on more than one NON-UPLINK port in a snapshot, or changing port >= 2 times across snapshots")
def mac_flapping(ctx):
    """
    A MAC learned on two non-uplink ports, or moving between snapshots, means the
    switch is seeing the same device's frames arrive by two paths.
    """
    seen = defaultdict(list)
    for i, s in enumerate(ctx.snapshots):
        known = {_norm_mac(x.get("switchMac") or x.get("id")) for x in s["switches"]} - {""}
        uplinks = {(p.get("switchName"), p.get("portIdentifier")) for p in s["ports"]
                   if _norm_mac(p.get("neighborMacAddress")) in known}
        per = defaultdict(set)
        for m in s["macs"]:
            mac = _norm_mac(m.get("clientMac"))
            if mac:
                per[mac].add((m.get("switchName"), m.get("switchPort")))
        for mac, places in per.items():
            access = {p for p in places if p not in uplinks}
            seen[mac].append((i, sorted(places), len(access) > 1))

    for mac, hist in seen.items():
        dup_in = sum(1 for _, _, dup in hist if dup)
        moves = sum(1 for (_, a, _), (_, b, _) in zip(hist, hist[1:]) if a != b)
        if not dup_in and moves < 2:
            continue
        yield Finding(
            "mac-flapping", f"MAC {mac} learned on multiple ports",
            "critical" if dup_in else "warning", CAT_LOOP, mac,
            (f"Seen on more than one non-uplink port in {dup_in} of {len(hist)} snapshots"
             if dup_in else f"Changed port {moves} times across {len(hist)} snapshots") +
            ". Uplink-explained duplicates are already excluded, so this is the same device "
            "reachable by two distinct paths — either a loop or a duplicated MAC.",
            {"mac": mac, "duplicateSnapshots": dup_in, "moves": moves,
             "lastSeenAt": hist[-1][1]},
            "Check whether the two ports lead to the same physical area. If they do, you "
            "have found the loop.",
        )


@check("dense-blind-port", "Many MACs behind a port LLDP cannot see", CAT_LOOP,
       needs="snapshot",
       trigger=">= 5 MACs learned on a port with no LLDP neighbour; critical at >= 20 or if the port is down")
def dense_blind_port(ctx):
    """
    The signature of an unmanaged device. Density behind a visible managed switch
    is an uplink and expected; density behind nothing means a dumb switch, a
    hub, or a cable patched back into the same network.
    """
    # Deliberately not restricted to up ports: a port that has just dropped still
    # carries its learned MACs, and that is the most interesting case of all.
    for p in ctx.ports:
        if ctx.is_uplink(p) or p.get("neighborName") or p.get("neighborMacAddress"):
            continue
        n = ctx.mac_count(p)
        if n < 5:
            continue
        down = not _is_up(p)
        yield Finding(
            "dense-blind-port",
            f"{ctx.port_label(p)} — {n} MACs, no LLDP neighbour"
            + (" (port is DOWN)" if down else ""),
            "critical" if (n >= 20 or down) else "warning", CAT_LOOP, ctx.port_label(p),
            f"{n} MAC addresses are learned through this port but LLDP sees nothing on the "
            "far end. Every other dense port in a managed estate resolves to a switch you "
            "can see; this one does not, which means an unmanaged device is bridging "
            "traffic there."
            + (" The port is currently DOWN while still holding those MACs, so it was "
               "carrying traffic very recently — an unmanaged device on a link that drops "
               "is a strong flapping candidate." if down else ""),
            {"macCount": n, "status": p.get("status"), "vlan": p.get("unTaggedVlan"),
             "speed": p.get("portSpeed"),
             "sampleMacs": [m.get("clientMac") for m in ctx.macs_by_port.get(p["id"], [])[:5]]},
            "Physically identify what is plugged in here. This is the highest-yield port "
            "to inspect when hunting an invisible loop.",
        )


# ── Physical layer ───────────────────────────────────────────────────────────

@check("crc-errors-rising", "CRC errors actively incrementing", CAT_PHY, needs="rates",
       trigger="crcErr/s or inErr/s above zero between snapshots; critical above 1 CRC/s")
def crc_errors_rising(ctx):
    """
    A rising CRC count is a failing cable, connector or optic. Distinct from a
    loop: it degrades one link rather than flooding a domain — but it also causes
    flapping, so it competes as an explanation.
    """
    for r in _rates(ctx):
        if r["crcErr"] <= 0 and r["inErr"] <= 0:
            continue
        yield Finding(
            "crc-errors-rising", f"{r['label']} — CRC/input errors incrementing now",
            "critical" if r["crcErr"] > 1 else "warning", CAT_PHY, r["label"],
            f"{r['crcErr']:.2f} CRC/s and {r['inErr']:.2f} input errors/s over "
            f"{ctx.rates['windowSeconds']}s. These are corrupted frames arriving on the wire — "
            "a physical fault, not a configuration one. Errors at this layer cause "
            "retransmits and can look like intermittent flapping.",
            {"crcPerSec": r["crcErr"], "inErrPerSec": r["inErr"],
             "speed": r.get("vlan"), "lldpNeighbour": r["lldp"] or None},
            "Reseat or replace the patch lead; if fibre, clean and check the optic. "
            "Swap the far-end port to confirm which side owns the fault.",
        )


@check("discards-rising", "Input discards incrementing", CAT_PHY, needs="rates",
       trigger="inDiscard/s > 1; critical above 1000/s")
def discards_rising(ctx):
    """Discards mean frames arrived and were thrown away — congestion or a full table."""
    for r in _rates(ctx):
        if r["inDiscard"] <= 1:
            continue
        yield Finding(
            "discards-rising", f"{r['label']} — discarding {r['inDiscard']:,.0f} frames/s",
            "critical" if r["inDiscard"] > 1000 else "warning", CAT_PHY, r["label"],
            f"The port is dropping {r['inDiscard']:,.0f} received frames per second. Frames "
            "arrived intact and were discarded anyway, which points at congestion, a buffer "
            "limit, or traffic hitting a port that cannot forward it.",
            {"discardsPerSec": r["inDiscard"], "broadcastInPerSec": r["broadcastIn"],
             "vlan": r["vlan"], "lldpNeighbour": r["lldp"] or None},
            "Compare against utilisation. Sustained discards with low utilisation suggest "
            "the drops are control-plane or STP-related rather than bandwidth.",
        )


@check("half-duplex", "Link running half duplex", CAT_PHY, needs="snapshot",
       trigger="portSpeed contains 'half'")
def half_duplex(ctx):
    """Half duplex on a modern switch is almost always a failed autonegotiation."""
    for p in ctx.up_ports:
        speed = str(p.get("portSpeed") or "")
        if "half" not in speed.lower():
            continue
        yield Finding(
            "half-duplex", f"{ctx.port_label(p)} — half duplex", "warning", CAT_PHY,
            ctx.port_label(p),
            f"Negotiated {speed}. On modern hardware this nearly always means "
            "autonegotiation failed or one end is hard-coded, and it produces late "
            "collisions and CRC errors that look like a cable fault.",
            {"portSpeed": speed, "configured": p.get("portSpeedConfig"),
             "neighbour": p.get("neighborName")},
            "Set both ends to auto, or hard-code both ends identically — never one of each.",
        )


@check("copper-link-underspeed", "Copper link negotiated below 1G", CAT_PHY,
       needs="snapshot",
       trigger="a COPPER port below 1 Gb/s on gigabit-capable hardware; warning at 10 Mb/s, info at 100 Mb/s. Fibre and multi-gig are excluded on purpose")
def copper_link_underspeed(ctx):
    """
    A COPPER port negotiating 10/100 on gigabit-capable hardware usually means
    damaged or mis-terminated pairs in the run — 1000BASE-T needs all four pairs,
    100BASE-TX needs two, so a run with two broken pairs still works, just ten
    times slower. That is a fault the link never reports.

    Deliberately excludes fibre and multi-gig: an earlier version of this check
    compared speed against capacity for every port and produced 535 findings, of
    which 329 were 10G optics sitting in 25G SFP28 cages — an entirely normal,
    intentional configuration. Flagging a design choice as a defect trains people
    to ignore the tool.
    """
    slow = []
    for p in ctx.up_ports:
        if str(p.get("portConnectorType") or "").upper() != "COPPER":
            continue
        cap = str(p.get("portSpeedCapacity") or "")
        speed = str(p.get("portSpeed") or "")
        cm = re.search(r"([\d.]+)\s*(G|M)", cap, re.I)
        sm = re.search(r"([\d.]+)\s*(G|M)", speed, re.I)
        if not (cm and sm):
            continue
        cap_mbps = float(cm.group(1)) * (1000 if cm.group(2).upper() == "G" else 1)
        spd_mbps = float(sm.group(1)) * (1000 if sm.group(2).upper() == "G" else 1)
        if cap_mbps < 1000 or spd_mbps >= 1000:
            continue
        slow.append({"label": ctx.port_label(p), "speed": speed, "capacity": cap,
                     "neighbour": p.get("neighborName") or None,
                     "crcErr": p.get("crcErr"), "mbps": spd_mbps})

    if not slow:
        return
    very_slow = [x for x in slow if x["mbps"] <= 10]
    if very_slow:
        yield Finding(
            "copper-link-underspeed",
            f"{len(very_slow)} copper link(s) negotiated at 10 Mb/s on gigabit hardware",
            "warning", CAT_PHY, "fabric",
            "10 Mb/s on a gigabit copper port is almost always a physically damaged run — "
            "it is the speed a link falls back to when only one pair is usable. Anything "
            "behind these ports is running at a hundredth of its available bandwidth.",
            {"ports": very_slow[:15], "count": len(very_slow)},
            "Test or replace these cable runs. Check the termination at both ends.",
        )
    hundred = [x for x in slow if x["mbps"] > 10]
    if hundred:
        with_errors = [x for x in hundred if _as_int(x["crcErr"])]
        yield Finding(
            "copper-link-underspeed",
            f"{len(hundred)} copper link(s) negotiated at 100 Mb/s on gigabit hardware",
            "info", CAT_PHY, "fabric",
            f"{len(hundred)} gigabit-capable copper ports are running at 100 Mb/s. Some of "
            "these will be genuinely old devices, but 1000BASE-T needs all four pairs while "
            "100BASE-TX needs two — so a cable run with two damaged pairs works perfectly at "
            "100 Mb/s and never reports a fault."
            + (f" {len(with_errors)} of them also have CRC errors, which points at the cable "
               "rather than the device." if with_errors else ""),
            {"ports": hundred[:20], "count": len(hundred),
             "alsoHaveCrcErrors": len(with_errors)},
            "Start with any that also show CRC errors — those are cable faults rather than "
            "slow devices.",
            confidence="medium",
        )


# ── Capacity ─────────────────────────────────────────────────────────────────

@check("cpu-memory-high", "Switch CPU or memory under pressure", CAT_CAP, needs="snapshot")
def cpu_memory_high(ctx):
    """
    High CPU on an L2 switch is itself a loop symptom: flooded broadcast gets
    punted to the control plane, and a saturated CPU then stops processing BPDUs.
    """
    for s in ctx.switches:
        cpu, mem = _as_int(s.get("cpu")), _as_int(s.get("memory"))
        if cpu < 75 and mem < 85:
            continue
        yield Finding(
            "cpu-memory-high", f"{s.get('name')} — CPU {cpu}%, memory {mem}%",
            "critical" if cpu >= 90 else "warning", CAT_CAP, s.get("name"),
            f"CPU at {cpu}% and memory at {mem}%. On a layer-2 switch, sustained high CPU "
            "usually means the control plane is being fed broadcast or unknown traffic. "
            "That matters twice over: it is a symptom of flooding, and a saturated CPU can "
            "stop processing STP BPDUs, which breaks the mechanism meant to stop the loop.",
            {"cpuPercent": cpu, "memoryPercent": mem, "model": s.get("model"),
             "uptime": s.get("uptime")},
            "Correlate with broadcast rates on this switch's ports. If both are high, treat "
            "the loop as the cause, not the CPU.",
        )


# ── Stability ────────────────────────────────────────────────────────────────

@check("port-flapping", "Port changed link state between snapshots", CAT_STAB,
       needs="snapshot",
       trigger="a port's link status differs between two snapshots; critical on more than one transition")
def port_flapping(ctx):
    """
    R1 offers no flap history, so this reconstructs it: any port whose status
    differs between two snapshots transitioned at least once in between.
    """
    if len(ctx.snapshots) < 2:
        return
    history = defaultdict(list)
    for s in ctx.snapshots:
        for p in s["ports"]:
            history[p["id"]].append((s["takenAt"], str(p.get("status", "")).lower(), p))

    for pid, hist in history.items():
        transitions = [(a[0], a[1], b[0], b[1]) for a, b in zip(hist, hist[1:]) if a[1] != b[1]]
        if not transitions:
            continue
        p = hist[-1][2]
        yield Finding(
            "port-flapping", f"{ctx.port_label(p)} — link state changed "
                             f"{len(transitions)} time(s)",
            "critical" if len(transitions) > 1 else "warning", CAT_STAB, ctx.port_label(p),
            f"Status changed {len(transitions)} time(s) across {len(hist)} snapshots "
            f"(now {hist[-1][1]}). RUCKUS ONE keeps no flap log, so this is reconstructed "
            "from the snapshots — the real transition count between samples may be higher.",
            {"transitions": [{"at": t[2], "from": t[1], "to": t[3]} for t in transitions],
             "currentStatus": hist[-1][1], "neighbour": p.get("neighborName"),
             "vlan": p.get("unTaggedVlan"), "crcErr": p.get("crcErr")},
            "Cross-check against CRC errors on the same port: errors plus flapping is a "
            "physical fault; clean flapping is more likely STP topology churn.",
        )


@check("recent-reboot", "Switch rebooted recently", CAT_STAB, needs="snapshot",
       trigger="uptime under 24 hours on an ONLINE switch")
def recent_reboot(ctx):
    """A switch that keeps rebooting explains flapping everywhere downstream of it."""
    for s in ctx.switches:
        up = str(s.get("uptime") or "")
        if s.get("deviceStatus") != "ONLINE" or not up:
            continue
        days = re.search(r"(\d+)\s*day", up, re.I)
        hours = re.search(r"(\d+)\s*hour", up, re.I)
        total_h = (int(days.group(1)) * 24 if days else 0) + (int(hours.group(1)) if hours else 0)
        if days or total_h >= 24:
            continue
        yield Finding(
            "recent-reboot", f"{s.get('name')} — up only {up}", "warning", CAT_STAB,
            s.get("name"),
            f"Uptime is {up}. Everything downstream of this switch lost link when it "
            "restarted, so unexplained flaps elsewhere may simply be this reboot.",
            {"uptime": up, "model": s.get("model"), "firmware": s.get("firmwareVersion")},
            "Check the reboot reason on the switch and whether it is a repeat.",
        )


@check("switch-offline", "Switch not reporting to RUCKUS ONE", CAT_STAB,
       needs="snapshot",
       trigger="deviceStatus OFFLINE or INITIALIZING; critical only when uptime is under 2h (a real reboot), warning when uptime is intact (cloud visibility loss)")
def switch_offline(ctx):
    """
    `deviceStatus: OFFLINE` means R1 has lost management contact — NOT that the
    switch is down. The distinction matters enormously during an incident, and
    uptime settles it: a switch that rebooted has a small or reset uptime, while
    one that merely stopped reporting keeps counting.

    Observed live: 10 switches flipped to OFFLINE within ~80 minutes while
    reporting 13, 43 and 68 days of uptime. Nothing had rebooted; the cloud had
    simply lost sight of them.
    """
    def uptime_hours(s):
        up = str(s.get("uptime") or "")
        d = re.search(r"(\d+)\s*day", up, re.I)
        h = re.search(r"(\d+)\s*hour", up, re.I)
        if not (d or h):
            return None
        return (int(d.group(1)) * 24 if d else 0) + (int(h.group(1)) if h else 0)

    previous = {x["id"]: x for x in ctx.snapshots[0]["switches"]} if len(ctx.snapshots) > 1 else {}

    for s in ctx.switches:
        st = s.get("deviceStatus")
        if st not in ("OFFLINE", "INITIALIZING"):
            continue
        hours = uptime_hours(s)
        was = previous.get(s["id"], {})
        was_status = was.get("deviceStatus")
        rebooted = hours is not None and hours < 2

        if rebooted:
            detail = ("Uptime is under two hours, so this switch has actually restarted. "
                      "Everything downstream of it lost link when it did.")
            remediation = "Check the reboot reason on the switch and whether it repeats."
        elif hours:
            detail = (f"Uptime still reads {s.get('uptime')}, so the switch has NOT rebooted "
                      "— RUCKUS ONE has lost management contact with it. That is a "
                      "management-plane problem (path to the cloud, DNS, or the cloud's own "
                      "view), and the switch is most likely still forwarding traffic "
                      "normally. Do not treat this as an outage without confirming from the "
                      "data plane.")
            remediation = ("Ping the switch's management IP and check its uplink path. If it "
                           "answers, this is a cloud-visibility issue, not a device failure.")
        else:
            detail = ("No uptime is reported, so whether this switch rebooted or merely lost "
                      "cloud contact cannot be told from the API alone.")
            remediation = "Confirm reachability directly."

        yield Finding(
            "switch-offline", f"{s.get('name')} — {st}"
            + ("" if rebooted else " (uptime intact — did not reboot)" if hours else ""),
            "critical" if rebooted else "warning", CAT_STAB, s.get("name"),
            f"Reporting {st} to RUCKUS ONE. " + detail +
            " Its ports contribute no data to any other check, so this switch is a blind "
            "spot in the rest of this report.",
            {"status": st, "previousStatus": was_status, "uptime": s.get("uptime"),
             "uptimeHours": hours, "rebooted": rebooted, "model": s.get("model"),
             "venue": s.get("venueName"), "ip": s.get("ipAddress"),
             "clientCount": s.get("clientCount")},
            remediation,
            confidence="high" if hours is not None else "medium",
        )


@check("mass-visibility-loss", "Many switches stopped reporting at once", CAT_STAB,
       needs="snapshot",
       trigger=">= 3 switches moving ONLINE -> OFFLINE within the snapshot window")
def mass_visibility_loss(ctx):
    """
    Several switches going OFFLINE together is a different incident from one
    switch failing: it points at a shared path, a cloud-side event, or an upstream
    outage. Worth surfacing as one finding rather than N unrelated ones.
    """
    if len(ctx.snapshots) < 2:
        return
    first, last = ctx.snapshots[0], ctx.snapshots[-1]
    before = {x["id"]: x.get("deviceStatus") for x in first["switches"]}
    newly = [x for x in last["switches"]
             if x.get("deviceStatus") == "OFFLINE" and before.get(x["id"]) == "ONLINE"]
    if len(newly) < 3:
        return
    intact = [x for x in newly if re.search(r"\d+\s*day", str(x.get("uptime") or ""), re.I)]
    yield Finding(
        "mass-visibility-loss",
        f"{len(newly)} switches stopped reporting between "
        f"{first['takenAt'][11:19]} and {last['takenAt'][11:19]}",
        "critical", CAT_STAB, "fabric",
        f"{len(newly)} switches went from ONLINE to OFFLINE within this window"
        + (f", and {len(intact)} of them still report multi-day uptime — meaning they did "
           "not reboot. Several devices losing cloud contact together, without restarting, "
           "points at a shared upstream path, a DNS or firewall change, or an event on the "
           "RUCKUS ONE side rather than at the switches themselves."
           if intact else ". Check whether they share an upstream path."),
        {"count": len(newly), "withIntactUptime": len(intact),
         "from": first["takenAt"], "to": last["takenAt"],
         "switches": [{"name": x.get("name"), "uptime": x.get("uptime"),
                       "venue": x.get("venueName"), "ip": x.get("ipAddress")}
                      for x in newly[:15]]},
        "Find what these switches have in common — venue, uplink path, or IP range. If "
        "their uptime is intact they are probably still forwarding; confirm from the data "
        "plane before declaring an outage.",
    )


# ── Topology ─────────────────────────────────────────────────────────────────

@check("unblocked-redundant-path", "Redundant path with no visible blocking", CAT_TOPO,
       needs="snapshot",
       trigger="two or more non-LAG, non-stack links between the same switch pair with no blocking port observed")
def unblocked_redundant_path(ctx):
    """
    Parallel links between the same switch pair that are not a LAG and not
    stacking form a physical loop. STP should block one — but on this platform
    `spanningTreeStatus` is populated on only a few percent of ports, so a
    blocked state usually cannot be confirmed from the API.
    """
    known = set(ctx.switch_by_mac) - {""}
    pairs = defaultdict(list)
    for p in ctx.up_ports:
        nb, me = _norm_mac(p.get("neighborMacAddress")), _norm_mac(p.get("switchMac"))
        if nb and me and nb in known and nb != me:
            pairs[tuple(sorted((me, nb)))].append(p)

    unconfirmed, confirmed, stp_reported, total_ports = [], [], 0, 0
    for (a, b), ports in pairs.items():
        idents = {p.get("portIdentifier") for p in ports}
        if len(idents) < 2:
            continue
        if any(_as_int(p.get("lagId")) for p in ports):
            continue
        if any(str(p.get("usedInFormingStack")).lower() == "true" for p in ports):
            continue
        blocking = any(str(p.get("spanningTreeStatus") or "").lower()
                       in ("blocking", "discarding") for p in ports)
        stp_reported += sum(1 for p in ports if p.get("spanningTreeStatus"))
        total_ports += len(ports)
        entry = {"switchA": ctx.switch_by_mac.get(a, {}).get("name", a),
                 "switchB": ctx.switch_by_mac.get(b, {}).get("name", b),
                 "ports": sorted(idents)}
        (confirmed if blocking else unconfirmed).append(entry)

    # One finding, not one per pair. On a campus these run to dozens of sibling
    # closet daisy-chains, and 55 identical warnings bury everything else in the
    # report -- the remediation is the same for all of them anyway.
    if unconfirmed:
        yield Finding(
            "unblocked-redundant-path",
            f"{len(unconfirmed)} switch pair(s) have parallel links with no confirmed "
            f"blocking port",
            "warning", CAT_TOPO, "fabric",
            f"{len(unconfirmed)} pairs of switches are joined by more than one link that is "
            "neither a LAG member nor a stack link. Each is a physical loop that spanning "
            f"tree must be blocking. No blocking port was observed on any of them — but "
            f"spanningTreeStatus is only populated on {stp_reported} of {total_ports} of "
            "these ports on this platform, so this is unconfirmed, not proof of a fault. "
            "Most will be intentional closet daisy-chains.",
            {"unconfirmedPairs": unconfirmed[:25], "unconfirmedCount": len(unconfirmed),
             "confirmedBlockingCount": len(confirmed),
             "stpStatusReportedOn": stp_reported, "totalPorts": total_ports},
            "Spot-check a few with `show spanning-tree` on the CLI. If both ends of any "
            "pair are forwarding, that pair is your loop.",
            confidence="medium",
        )


@check("lldp-neighbour-changed", "LLDP neighbour changed on a port", CAT_TOPO,
       needs="snapshot",
       trigger="a port's LLDP neighbour MAC differs between the first and last snapshot")
def lldp_neighbour_changed(ctx):
    """A neighbour that changes identity means something was re-patched or replaced."""
    if len(ctx.snapshots) < 2:
        return
    first, last = ctx.snapshots[0], ctx.snapshots[-1]
    before = {p["id"]: p.get("neighborMacAddress") for p in first["ports"]}
    for p in last["ports"]:
        old, new = before.get(p["id"]), p.get("neighborMacAddress")
        if old is None or old == new or (not old and not new):
            continue
        yield Finding(
            "lldp-neighbour-changed",
            f"{ctx.port_label(p)} — LLDP neighbour changed", "warning", CAT_TOPO,
            ctx.port_label(p),
            f"Neighbour went from {old or 'none'} to {new or 'none'} between "
            f"{first['takenAt'][11:19]} and {last['takenAt'][11:19]}. Either the cable was "
            "moved, the far-end device was replaced, or the link is unstable enough that "
            "LLDP is timing out.",
            {"was": old or None, "now": new or None, "neighbourName": p.get("neighborName"),
             "status": p.get("status")},
            "If nobody re-patched this port, treat it as an unstable link.",
        )


# ── Cross-referencing: config × metrics ──────────────────────────────────────

@check("broadcast-in-unprotected-vlan", "Broadcast load in a VLAN with no STP instance",
       CAT_LOOP, needs="rates",
       trigger="a VLAN with no spanning-tree instance on ANY switch that is also carrying >= 50 broadcast/s")
def broadcast_in_unprotected_vlan(ctx):
    """
    The combination that actually matters: real broadcast volume landing in a
    VLAN that has no spanning-tree instance to break a loop in it. Either signal
    alone is survivable; together they are how a campus melts.
    """
    if not ctx.configs:
        return
    covered, defined = Counter(), Counter()
    for c in ctx.configs.values():
        for vid, body in c.vlans().items():
            defined[vid] += 1
            if any(re.match(r"spanning-tree", line, re.I) for line in body):
                covered[vid] += 1

    unprotected = {v for v in defined if covered[v] == 0}
    if not unprotected:
        return

    for v in (ctx.rates or {}).get("vlanSummary", []):
        vid = str(v["vlan"] or "")
        if vid not in unprotected or v["max"] < 50:
            continue
        yield Finding(
            "broadcast-in-unprotected-vlan",
            f"VLAN {vid} carries broadcast traffic and has no spanning-tree instance",
            "critical", CAT_LOOP, f"VLAN {vid}",
            f"VLAN {vid} is defined on {defined[vid]} switch(es), none of which run a "
            f"spanning-tree instance for it, and it is currently carrying up to "
            f"{v['max']:,.0f} broadcast/s across {v['ports']} ports. A loop formed in this "
            "VLAN would not be broken by anything.",
            {"vlan": vid, "switchesDefining": defined[vid], "switchesWithStp": 0,
             "maxBroadcastPerSec": v["max"], "medianBroadcastPerSec": v["median"],
             "ports": v["ports"]},
            f"Add `spanning-tree 802-1w` under `vlan {vid}` on every switch that carries it. "
            "This is the highest-priority configuration fix in this report.",
        )


@check("macs-on-down-port", "MAC addresses learned on a port that is now down", CAT_STAB,
       needs="snapshot",
       trigger=">= 2 MACs still associated with a port that is down; critical at >= 10")
def macs_on_down_port(ctx):
    """
    A down port whose MAC table entries are still present was forwarding traffic
    very recently. On a network with a flapping complaint this is one of the few
    direct pieces of evidence available, since R1 keeps no flap log.

    Confidence is medium on purpose: R1's MAC view is a periodic cloud snapshot,
    so some of this is ordinary aging rather than a flap.
    """
    for p in ctx.ports:
        if _is_up(p):
            continue
        n = ctx.mac_count(p)
        if n < 2:
            continue
        yield Finding(
            "macs-on-down-port",
            f"{ctx.port_label(p)} — down, but still holding {n} learned MAC(s)",
            "warning" if n < 10 else "critical", CAT_STAB, ctx.port_label(p),
            f"The port reports {p.get('status')} yet {n} MAC addresses are still associated "
            "with it. Those entries were learned while the link was up, so this port "
            "carried traffic recently and has since dropped — the shape of a flap. "
            "R1's MAC view is a periodic snapshot, so some of this can be normal aging.",
            {"macCount": n, "status": p.get("status"), "adminStatus": p.get("adminStatus"),
             "vlan": p.get("unTaggedVlan"), "crcErr": p.get("crcErr"),
             "neighbour": p.get("neighborName") or None,
             "sampleMacs": [m.get("clientMac") for m in ctx.macs_by_port.get(p["id"], [])[:5]]},
            "Check this port's link history on the switch CLI (`show interface`) for its "
            "last-change timestamp and error counters.",
            confidence="medium",
        )


# ── MAC table sizing ─────────────────────────────────────────────────────────
# There is no discoverable hardware table capacity (see services/wiredwiz/
# mactable.py), so these checks are relative and growth-based rather than
# utilisation-based. That is the honest framing and also the more useful one:
# exhaustion is rare, but a table out of line with its peers or growing fast is
# how flooding and loops actually announce themselves.

CAT_MAC = "mac-table"


@check("mac-table-outlier", "MAC table far larger than same-model peers", CAT_MAC,
       needs="snapshot",
       trigger=">= 20 MACs and >= 5x the median for the same switch MODEL (needs >= 3 peers); critical at >= 10x")
def mac_table_outlier(ctx):
    """
    Compared against peers of the SAME MODEL, because a distribution switch is
    expected to hold far more than a 12-port closet switch and a fleet-wide
    average would flag the wrong devices.
    """
    from ..mactable import summarize_mac_tables
    summary = summarize_mac_tables(ctx.snapshots)
    peers = summary["byModel"]

    for r in summary["switches"]:
        if r["deviceStatus"] != "ONLINE" or r["learned"] < 20:
            continue
        st = peers.get(r["model"])
        if not st or st["count"] < 3 or not st["median"]:
            continue
        ratio = r["learned"] / st["median"]
        if ratio < 5:
            continue
        densest = r["densestPort"] or {}
        yield Finding(
            "mac-table-outlier",
            f"{r['name']} — {r['learned']} MACs, {ratio:.1f}× the {r['model']} median",
            "warning" if ratio < 10 else "critical", CAT_MAC, r["name"],
            f"This switch has learned {r['learned']} MAC addresses against a median of "
            f"{st['median']:.0f} across {st['count']} other {r['model']} switches. That is "
            "expected on a distribution or core switch aggregating many closets, but on an "
            "access switch it means traffic for a large part of the network is being "
            "learned here — which is what happens when a loop or a bridging device pulls "
            "foreign traffic onto the segment.",
            {"learned": r["learned"], "modelMedian": st["median"], "model": r["model"],
             "peerCount": st["count"], "ratio": round(ratio, 1),
             "upPorts": r["upPorts"], "macsPerUpPort": r["macsPerUpPort"],
             "densestPort": densest.get("port"), "densestPortMacs": densest.get("macs"),
             "densestPortNeighbour": densest.get("lldp") or None,
             "topVlans": r["vlans"][:4]},
            "Check the densest port. If it is an uplink to a bigger switch this is normal "
            "aggregation; if it is an access port, find out what is bridging behind it.",
            confidence="medium",
        )


@check("mac-table-growth", "MAC table grew sharply between snapshots", CAT_MAC,
       needs="snapshot",
       trigger="growth >= 40 entries AND at least a doubling between snapshots; critical when it is >= 50% of all estate growth")
def mac_table_growth(ctx):
    """
    Sudden growth means the switch started learning addresses it was not seeing
    before — a new path opened. That is either a legitimate topology change or
    the start of a loop pulling foreign traffic in.
    """
    if len(ctx.snapshots) < 2:
        return
    from ..mactable import summarize_mac_tables
    summary = summarize_mac_tables(ctx.snapshots)
    total_growth = sum(r["growth"] or 0 for r in summary["switches"] if (r["growth"] or 0) > 0)

    for r in summary["switches"]:
        g, prev = r["growth"], r["previousLearned"]
        if g is None or prev is None or g < 40:
            continue
        if g < prev:                      # require at least a doubling
            continue
        share = (100 * g / total_growth) if total_growth else 0
        yield Finding(
            "mac-table-growth",
            f"{r['name']} — MAC table grew {prev} → {r['learned']} (+{g})",
            "critical" if share >= 50 else "warning", CAT_MAC, r["name"],
            f"Learned entries went from {prev} to {r['learned']} between "
            f"{summary['previousTakenAt'][11:19]} and {summary['takenAt'][11:19]}"
            + (f", which is {share:.0f}% of all MAC table growth across the entire estate "
               "in that window. " if share >= 25 else ". ")
            + "A table that fills this fast is seeing traffic it was not seeing before. "
            "Some of this is ordinary learning after a quiet period, so treat it as a "
            "pointer rather than a verdict.",
            {"previous": prev, "current": r["learned"], "growth": g,
             "shareOfEstateGrowthPercent": round(share),
             "from": summary["previousTakenAt"], "to": summary["takenAt"],
             "densestPort": (r["densestPort"] or {}).get("port"),
             "densestPortMacs": (r["densestPort"] or {}).get("macs"),
             "topVlans": r["vlans"][:4]},
            "Compare against broadcast rates on the same switch. Growth plus elevated "
            "broadcast in the same VLAN is a strong loop indication.",
            confidence="medium",
        )


@check("mac-count-mismatch", "R1's two MAC counts disagree", CAT_MAC, needs="snapshot",
       trigger="R1's clientCount and the client-query row count differ by >= 10 entries and >= 15%")
def mac_count_mismatch(ctx):
    """
    R1 reports switch client counts twice: `clientCount` on the switch record,
    and the rows returned by the client query. Where they disagree materially,
    the MAC table view is incomplete — which matters because several other
    checks here are built on those rows.
    """
    from ..mactable import summarize_mac_tables
    summary = summarize_mac_tables(ctx.snapshots)
    bad = []
    for r in summary["switches"]:
        if r["deviceStatus"] != "ONLINE" or r["clientCount"] is None:
            continue
        diff = abs(r["clientCount"] - r["learned"])
        if diff < 10 or diff < 0.15 * max(r["clientCount"], 1):
            continue
        bad.append({"switch": r["name"], "queryRows": r["learned"],
                    "clientCount": r["clientCount"], "difference": diff})

    if not bad:
        return
    bad.sort(key=lambda x: -x["difference"])
    yield Finding(
        "mac-count-mismatch",
        f"MAC table view is incomplete on {len(bad)} switch(es)",
        "warning", CAT_MAC, "fabric",
        f"On {len(bad)} switch(es) the number of MAC rows returned by the client query "
        "differs materially from the `clientCount` R1 reports on the switch itself — the "
        f"largest gap is {bad[0]['difference']} entries on {bad[0]['switch']}. The crawl "
        "was verified complete, so this is R1 reporting two different numbers, not a "
        "collection failure. It means the MAC-table-derived checks are working from a "
        "partial view on these switches.",
        {"switches": bad[:15], "count": len(bad),
         "totalQueryRows": summary["totals"]["learnedTotal"],
         "totalClientCount": summary["totals"]["clientCountTotal"]},
        "Treat MAC counts on these switches as a floor, not an exact figure. Confirm on "
        "the CLI with `show mac-address count` if a specific switch matters.",
        confidence="high",
    )
