"""
Hardware, PoE, link-consistency and capacity checks.

These lean on fields the crawl collects but nothing used until now: PSU and fan
status (JSON-encoded strings on the switch record), stack member health, PoE
budget at chassis and port level, optics/connector type, and the err-disable
state that finally started reporting values.

The link-consistency checks are the interesting ones: because LLDP tells us both
ends of a switch-to-switch link, we can compare how each side is configured and
catch mismatches that no single-sided view reveals.
"""

import ast
import json
import re
from collections import defaultdict

from .framework import Finding, check, _as_int, _is_up, _norm_mac

CAT_HW = "hardware"
CAT_POE = "poe"
CAT_LINK = "link-consistency"
CAT_CAP = "capacity"
CAT_META = "coverage"


def _loose_parse(value):
    """
    R1 returns these as strings — some JSON, some Python repr (single quotes).
    Try both rather than assuming, and give up quietly rather than throwing.
    """
    if isinstance(value, (list, dict)):
        return value
    if not value or not isinstance(value, str):
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except (ValueError, SyntaxError):
            continue
    return None


# R1 reports slot status as OK / Failed / Other. "Other" is NOT a fault: it is an
# unpopulated slot. Verified on an ICX7150-48ZP whose PSU slot 1 (AC) reads OK and
# slot 2 (DC) reads Other — the switch runs on AC and the DC bay is simply empty.
# Treating "Other" as a fault produced two critical findings for hardware that was
# working exactly as installed. Only an explicit failure word counts.
FAULT_WORDS = ("FAIL", "FAULT", "ERROR", "BAD", "DOWN", "CRITICAL", "ALARM", "ABSENT_FAULT")
ABSENT_WORDS = ("OTHER", "NOTPRESENT", "NOT PRESENT", "ABSENT", "EMPTY",
                "NOTINSTALLED", "NOT INSTALLED", "UNKNOWN", "NONE", "")


def _slot_state(entry) -> str:
    """'ok' | 'failed' | 'absent' for one PSU or fan slot."""
    st = str(entry.get("status") or entry.get("state") or "").strip().upper()
    if any(w in st for w in FAULT_WORDS):
        return "failed"
    if st in ABSENT_WORDS:
        return "absent"
    return "ok"


def _bad_status(entry) -> bool:
    return _slot_state(entry) == "failed"


# ── Hardware health ──────────────────────────────────────────────────────────

@check("psu-fault", "Power supply not healthy", CAT_HW, needs="snapshot",
       trigger="a power-supply slot reporting a FAILURE. An empty bay (status \"Other\") is reported separately as loss of redundancy, at info, not as a fault")
def psu_fault(ctx):
    """
    A switch on one working PSU is one failure from dropping every AP behind it.
    `powerSupplyGroups` is a JSON string of per-slot status.
    """
    for s in ctx.switches:
        groups = _loose_parse(s.get("powerSupplyGroups"))
        if not groups:
            continue
        failed, absent, ok = [], [], []
        for g in groups if isinstance(groups, list) else [groups]:
            for slot in (g.get("powerSupplySlotList") or g.get("slotList") or []):
                entry = {"slot": slot.get("slotNumber"), "type": slot.get("type"),
                         "status": slot.get("status")}
                {"failed": failed, "absent": absent, "ok": ok}[_slot_state(slot)].append(entry)

        if failed:
            yield Finding(
                "psu-fault", f"{s.get('name')} — {len(failed)} power supply slot(s) FAILED",
                "critical", CAT_HW, s.get("name"),
                f"{len(failed)} power supply slot(s) report a failure. On a PoE switch this "
                "is urgent twice over: the switch is one supply from going dark, and a "
                "reduced power budget can silently stop powering APs at the bottom of the "
                "priority list — which presents as APs randomly dropping, not as a power "
                "alarm.",
                {"switch": s.get("name"), "model": s.get("model"), "failedSlots": failed,
                 "workingSlots": len(ok), "emptySlots": len(absent),
                 "poeTotalMilliwatts": s.get("poeTotal"), "venue": s.get("venueName")},
                "Replace the failed supply. Until then treat this switch as single-corded.",
            )
        elif len(ok) == 1 and absent:
            # An empty bay is not a fault, but it does mean there is no second
            # supply to fail over to. Worth knowing, not worth alarming about.
            yield Finding(
                "psu-fault", f"{s.get('name')} — single power supply, no redundancy",
                "info", CAT_HW, s.get("name"),
                f"One supply installed and working; {len(absent)} bay(s) empty "
                f"({', '.join(str(a.get('type') or 'unknown') for a in absent)}). Nothing "
                "is faulty — but there is no second supply, so a PSU failure takes this "
                "switch and everything powered by it offline.",
                {"switch": s.get("name"), "model": s.get("model"),
                 "workingSlots": len(ok), "emptySlots": absent,
                 "venue": s.get("venueName")},
                "Fit a second supply if this switch feeds anything that must stay up.",
                confidence="high",
            )


@check("fan-fault", "Fan not healthy", CAT_HW, needs="snapshot",
       trigger="a fan slot reporting a FAILURE. Status \"Other\" means an unpopulated slot and is not treated as a fault")
def fan_fault(ctx):
    """Thermal problems present as random reboots long before anything says 'overheat'."""
    for s in ctx.switches:
        groups = _loose_parse(s.get("fanGroups"))
        if not groups:
            continue
        bad = []
        for g in groups if isinstance(groups, list) else [groups]:
            for slot in (g.get("fanSlotList") or g.get("slotList") or []):
                if _slot_state(slot) == "failed":
                    bad.append({"slot": slot.get("slotNumber"),
                                "status": slot.get("status")})
        if not bad:
            continue
        yield Finding(
            "fan-fault", f"{s.get('name')} — {len(bad)} fan slot(s) FAILED", "critical",
            CAT_HW, s.get("name"),
            f"Fan status is not OK on {len(bad)} slot(s). Thermal faults show up as "
            "unexplained reboots and flapping links well before anything reports an "
            "over-temperature condition, so this is worth correlating against any switch "
            "on the flapping list.",
            {"switch": s.get("name"), "model": s.get("model"), "slots": bad,
             "uptime": s.get("uptime"), "venue": s.get("venueName")},
            "Replace the fan tray. Check the switch's reboot history while you are there.",
        )


@check("stack-member-unhealthy", "Stack member not online", CAT_HW, needs="snapshot",
       trigger="any stack member whose unitState is not ONLINE")
def stack_member_unhealthy(ctx):
    """
    A stack that has lost a member keeps forwarding, so nothing looks broken --
    but its uplink redundancy and port capacity are gone, and a second loss takes
    the whole stack.
    """
    for s in ctx.switches:
        members = _loose_parse(s.get("stackMembersStatus"))
        if not members or not isinstance(members, list):
            continue
        bad = [m for m in members
               if str(m.get("unitState") or m.get("status") or "").upper()
               not in ("", "ONLINE", "OK", "ACTIVE", "READY")]
        if not bad:
            continue
        yield Finding(
            "stack-member-unhealthy",
            f"{s.get('name')} — {len(bad)} of {len(members)} stack member(s) not online",
            "critical", CAT_HW, s.get("name"),
            f"{len(bad)} stack unit(s) report a state other than online. The stack keeps "
            "forwarding on its remaining units, so this rarely raises an alarm — but the "
            "ports on the missing unit are down, and anything single-homed to that unit "
            "is offline right now.",
            {"switch": s.get("name"), "members": len(members),
             "unhealthy": [{"unit": m.get("unitName") or m.get("unitId"),
                            "state": m.get("unitState") or m.get("status")} for m in bad]},
            "Check `show stack` on the switch — most often a stacking cable or a unit "
            "that failed to rejoin after a reload.",
        )


# ── Err-disable ──────────────────────────────────────────────────────────────

@check("port-err-disabled", "Port shut down by a protection mechanism", "loop-evidence",
       needs="snapshot",
       trigger="errorDisableStatus is set on a port; critical when the reason mentions BPDU or LOOP")
def port_err_disabled(ctx):
    """
    The switch itself deciding to shut a port is the strongest evidence available.
    `errorDisableStatus` is empty on most ports but does populate — a BPDUGUARD
    value means a device speaking STP appeared on a port configured as an edge
    port, i.e. somebody plugged a switch into an access port.
    """
    for p in ctx.ports:
        st = str(p.get("errorDisableStatus") or "").strip()
        if not st or st.lower() in ("none", "null"):
            continue
        reason = st.upper()
        loop_related = "BPDU" in reason or "LOOP" in reason
        yield Finding(
            "port-err-disabled",
            f"{ctx.port_label(p)} — err-disabled ({reason})",
            "critical" if loop_related else "warning", "loop-evidence",
            ctx.port_label(p),
            f"The switch has error-disabled this port with reason {reason}. "
            + ("BPDU guard fires when a device sending spanning-tree BPDUs appears on a "
               "port configured as an edge port — in plain terms, somebody plugged a "
               "switch into an access port. That is the single most direct evidence of an "
               "unauthorised switch on the network, and a prime loop source."
               if "BPDU" in reason else
               "Loop detection fires when the switch receives its own test frame back on "
               "a port, which is a loop by definition."
               if "LOOP" in reason else
               "The port was disabled by a protection mechanism rather than going down "
               "on its own."),
            {"reason": reason, "status": p.get("status"),
             "adminStatus": p.get("adminStatus"), "vlan": p.get("unTaggedVlan"),
             "neighbour": ctx.neighbour(p),
             "learnedMacs": ctx.mac_count(p)},
            "Find out what is physically connected here before re-enabling. Clearing it "
            "without removing the cause just repeats the event.",
        )


# ── Link consistency (both ends known via LLDP) ──────────────────────────────

def _switch_links(ctx):
    """
    Yield (near_port, far_port) for switch-to-switch links where BOTH ends are in
    the snapshot, so the two sides' configuration can be compared.
    """
    by_mac_port = {}
    for p in ctx.ports:
        by_mac_port[(_norm_mac(p.get("switchMac")), p.get("portIdentifier"))] = p

    known = set(ctx.switch_by_mac) - {""}
    seen = set()
    for p in ctx.ports:
        if not _is_up(p):
            continue
        nb = _norm_mac(p.get("neighborMacAddress"))
        me = _norm_mac(p.get("switchMac"))
        if not nb or nb not in known or nb == me:
            continue
        # Find the far end: a port on the neighbour whose own neighbour is us.
        for q in ctx.ports_by_switch.get(nb, []):
            if _norm_mac(q.get("neighborMacAddress")) == me and _is_up(q):
                key = tuple(sorted([p["id"], q["id"]]))
                if key in seen:
                    continue
                seen.add(key)
                yield p, q
                break


@check("vlan-mismatch-across-link", "Untagged VLAN differs on the two ends of a link",
       CAT_LINK, needs="snapshot",
       trigger="the two ends of a switch-to-switch link have different untagged (native) VLANs")
def vlan_mismatch_across_link(ctx):
    """
    Classic and nasty: each switch puts untagged traffic from that link into a
    different VLAN, silently bridging two broadcast domains together. It does not
    break connectivity outright, which is why it survives for months.
    """
    for near, far in _switch_links(ctx):
        a, b = str(near.get("unTaggedVlan") or ""), str(far.get("unTaggedVlan") or "")
        if not a or not b or a == b:
            continue
        yield Finding(
            "vlan-mismatch-across-link",
            f"{ctx.port_label(near)} (VLAN {a}) ↔ {ctx.port_label(far)} (VLAN {b})",
            "critical", CAT_LINK, f"{ctx.port_label(near)} ↔ {ctx.port_label(far)}",
            f"The two ends of this link put untagged traffic into different VLANs — "
            f"{a} on one side, {b} on the other. That silently merges two broadcast "
            "domains: hosts in VLAN "
            f"{a} and VLAN {b} can reach each other at layer 2 without any router "
            "involved, spanning tree computes a topology per VLAN that does not match "
            "where traffic actually flows, and a loop in one domain floods both.",
            {"nearSwitch": near.get("switchName"), "nearPort": near.get("portIdentifier"),
             "nearUntaggedVlan": a, "nearTaggedVlans": near.get("vlanIds"),
             "farSwitch": far.get("switchName"), "farPort": far.get("portIdentifier"),
             "farUntaggedVlan": b, "farTaggedVlans": far.get("vlanIds")},
            "Make the untagged (native) VLAN identical on both ends, or make the link a "
            "pure tagged trunk with no untagged VLAN.",
        )


@check("speed-mismatch-across-link", "Link ends differ in capability or negotiated speed",
       CAT_LINK, needs="snapshot",
       trigger="the two ends of a link report different negotiated speed or connector type")
def speed_mismatch_across_link(ctx):
    """Mismatched optics or a copper/fibre confusion shows up as errors, not a clean fail."""
    for near, far in _switch_links(ctx):
        ns, fs = str(near.get("portSpeed") or ""), str(far.get("portSpeed") or "")
        nc, fc = str(near.get("portConnectorType") or ""), str(far.get("portConnectorType") or "")
        speed_bad = ns and fs and ns != fs
        conn_bad = nc and fc and nc != fc
        if not (speed_bad or conn_bad):
            continue
        yield Finding(
            "speed-mismatch-across-link",
            f"{ctx.port_label(near)} ↔ {ctx.port_label(far)} — "
            + ("speed" if speed_bad else "connector") + " mismatch",
            "warning", CAT_LINK, f"{ctx.port_label(near)} ↔ {ctx.port_label(far)}",
            f"The two ends report different {'speeds' if speed_bad else 'connector types'}: "
            f"{ns or nc} vs {fs or fc}. Mismatched optics and half-negotiated links pass "
            "traffic while dropping frames, so the symptom is errors and retransmits "
            "rather than a link that is cleanly down.",
            {"nearSwitch": near.get("switchName"), "nearPort": near.get("portIdentifier"),
             "nearSpeed": ns, "nearCapacity": near.get("portSpeedCapacity"),
             "nearOptics": near.get("opticsType"), "nearConnector": nc,
             "farSwitch": far.get("switchName"), "farPort": far.get("portIdentifier"),
             "farSpeed": fs, "farCapacity": far.get("portSpeedCapacity"),
             "farOptics": far.get("opticsType"), "farConnector": fc,
             "nearCrcErr": near.get("crcErr"), "farCrcErr": far.get("crcErr")},
            "Match the optics at both ends. Check CRC counters on both sides — they are "
            "usually already climbing.",
        )


@check("lag-member-inconsistent", "LAG has members that are not all up", CAT_LINK,
       needs="snapshot",
       trigger="a named LAG with >= 2 members where not all members are up")
def lag_member_inconsistent(ctx):
    """
    A LAG running on fewer members than configured still forwards, so it does not
    alarm — it just quietly loses half its bandwidth and all its redundancy.
    """
    lags = defaultdict(list)
    for p in ctx.ports:
        name = (p.get("lagName") or "").strip()
        if name:
            lags[(_norm_mac(p.get("switchMac")), name)].append(p)

    for (sw, name), members in lags.items():
        up = [p for p in members if _is_up(p)]
        if len(members) < 2 or len(up) == len(members):
            continue
        sw_name = ctx.switch_by_mac.get(sw, {}).get("name", sw)
        yield Finding(
            "lag-member-inconsistent",
            f"{sw_name} — LAG '{name}': {len(up)} of {len(members)} members up",
            "warning" if up else "critical", CAT_LINK, f"{sw_name} / {name}",
            f"LAG '{name}' has {len(members)} member ports but only {len(up)} are up. "
            "The aggregate keeps forwarding on its surviving members, so nothing alarms — "
            "you have simply lost the bandwidth and the redundancy the LAG was built for, "
            "and one more failure takes the link down entirely.",
            {"lag": name, "switch": sw_name, "membersTotal": len(members),
             "membersUp": len(up),
             "down": [{"port": p.get("portIdentifier"), "status": p.get("status"),
                       "adminStatus": p.get("adminStatus"), "crcErr": p.get("crcErr")}
                      for p in members if not _is_up(p)]},
            "Check the down member's cable and the far end. A LAG member that has been "
            "down since install usually means the second cable was never patched.",
        )


# ── PoE ──────────────────────────────────────────────────────────────────────

@check("poe-port-overdraw", "PoE port near its configured limit", CAT_POE, needs="snapshot",
       trigger="a port drawing >= 90% of its own PoE allocation")
def poe_port_overdraw(ctx):
    """A device drawing at its class ceiling will brown out under peak load."""
    for p in ctx.ports:
        used, total = _as_int(p.get("poeUsed")), _as_int(p.get("poeTotal"))
        if not total or not used:
            continue
        pct = 100 * used / total
        if pct < 90:
            continue
        yield Finding(
            "poe-port-overdraw",
            f"{ctx.port_label(p)} — drawing {pct:.0f}% of its PoE allocation",
            "warning", CAT_POE, ctx.port_label(p),
            f"{used / 1000:.1f}W drawn against a {total / 1000:.1f}W allocation "
            f"({pct:.0f}%). A powered device sitting at its ceiling has no headroom for "
            "peak draw — the usual symptom is an AP that reboots under load rather than a "
            "power alarm.",
            {"usedMilliwatts": used, "allocatedMilliwatts": total,
             "percent": round(pct), "poeType": p.get("poeType"),
             "device": ctx.neighbour(p)},
            "Raise the port's PoE class allocation, or move the device to a port with more "
            "budget.",
        )


# ── Capacity ─────────────────────────────────────────────────────────────────

@check("port-utilization-high", "Port utilisation elevated", CAT_CAP, needs="snapshot",
       trigger="signalIn or signalOut >= 70% (5-min average); critical at >= 90%")
def port_utilization_high(ctx):
    """
    `signalIn`/`signalOut` are capacity utilisation in HUNDREDTHS of a percent,
    averaged over five minutes -- so 5000 means 50%.
    """
    for p in ctx.up_ports:
        sin, sout = _as_int(p.get("signalIn")), _as_int(p.get("signalOut"))
        worst = max(sin, sout)
        if worst < 7000:                     # 70%
            continue
        yield Finding(
            "port-utilization-high",
            f"{ctx.port_label(p)} — {worst / 100:.0f}% utilised",
            "critical" if worst >= 9000 else "warning", CAT_CAP, ctx.port_label(p),
            f"Five-minute average utilisation is {sin / 100:.0f}% in / {sout / 100:.0f}% out "
            f"on a {p.get('portSpeed')} link. Sustained utilisation this high causes "
            "queuing delay and discards long before it shows as an outage.",
            {"inPercent": sin / 100, "outPercent": sout / 100,
             "portSpeed": p.get("portSpeed"), "capacity": p.get("portSpeedCapacity"),
             "neighbour": ctx.neighbour(p),
             "inDiscard": p.get("inDiscard")},
            "If this is an uplink, consider a LAG or a faster optic. Check discards on the "
            "same port to see whether it is already dropping.",
        )


@check("no-free-ports", "Switch has no spare ports", CAT_CAP, needs="snapshot",
       trigger="<= 2 ports not up and not reserved for stacking")
def no_free_ports(ctx):
    """Operationally relevant: nowhere to patch the next AP without displacing something."""
    for s in ctx.switches:
        if s.get("deviceStatus") != "ONLINE":
            continue
        ports = ctx.ports_by_switch.get(_norm_mac(s.get("switchMac") or s.get("id")), [])
        if not ports:
            continue
        free = [p for p in ports if not _is_up(p)
                and not str(p.get("usedInFormingStack")).lower() == "true"]
        if len(free) > 2:
            continue
        yield Finding(
            "no-free-ports",
            f"{s.get('name')} — {len(free)} spare port(s) of {len(ports)}",
            "info", CAT_CAP, s.get("name"),
            f"Only {len(free)} of {len(ports)} ports are free. There is nowhere to patch "
            "another device here, which matters when planning AP additions or when a port "
            "fails and needs to be moved.",
            {"totalPorts": len(ports), "freePorts": len(free),
             "model": s.get("model"), "venue": s.get("venueName")},
            "Plan capacity for this closet before the next install.",
            confidence="medium",
        )


# ── Meta: can these checks even see their data? ──────────────────────────────

# Fields a check depends on, and the checks that read them. When a field is never
# populated, those checks CANNOT produce a finding -- and an absent finding is
# indistinguishable from a clean result. Two checks shipped dead before this
# existed (poe-exhausted and speed-below-capacity read fields the crawl was not
# requesting), which is exactly the failure this guards against.
FIELD_DEPENDENCIES = {
    ("switch", "poeTotal"): ["poe-exhausted"],
    ("switch", "poeFree"): ["poe-exhausted"],
    ("switch", "powerSupplyGroups"): ["psu-fault"],
    ("switch", "fanGroups"): ["fan-fault"],
    ("switch", "stackMembersStatus"): ["stack-member-unhealthy"],
    ("switch", "cpu"): ["cpu-memory-high"],
    ("port", "portSpeedCapacity"): ["speed-below-capacity", "speed-mismatch-across-link"],
    ("port", "errorDisableStatus"): ["port-err-disabled"],
    ("port", "signalIn"): ["port-utilization-high"],
    ("port", "poeUsed"): ["poe-port-overdraw"],
    ("port", "lagName"): ["lag-member-inconsistent"],
    ("port", "spanningTreeStatus"): ["unblocked-redundant-path"],
    ("port", "neighborMacAddress"): ["vlan-mismatch-across-link", "dense-blind-port"],
    ("port", "unTaggedVlan"): ["vlan-mismatch-across-link"],
}


@check("check-data-coverage", "Checks that cannot see their data", CAT_META,
       needs="snapshot",
       trigger="a field read by a check is empty on every row (disabled) or populated on under 5% (partial)")
def check_data_coverage(ctx):
    """
    Report checks whose input fields are empty across the whole snapshot.

    A check with no data produces no findings, which looks exactly like a check
    that passed. This makes that state visible instead.
    """
    def populated(rows, field):
        return sum(1 for r in rows if r.get(field) not in (None, "", [], {}, "None"))

    dead, thin = [], []
    for (kind, field), checks in FIELD_DEPENDENCIES.items():
        rows = ctx.switches if kind == "switch" else ctx.ports
        if not rows:
            continue
        n = populated(rows, field)
        pct = 100 * n / len(rows)
        entry = {"field": f"{kind}.{field}", "populatedOn": n, "of": len(rows),
                 "percent": round(pct, 1), "affectedChecks": checks}
        if n == 0:
            dead.append(entry)
        elif pct < 5:
            thin.append(entry)

    def _name(entries):
        """Spell out field -> check in the title. 'N fields are empty' is unactionable."""
        parts = [f"{e['field']} (disables {', '.join(e['affectedChecks'])})"
                 for e in entries]
        head = "; ".join(parts[:2])
        return head + (f"; and {len(parts) - 2} more" if len(parts) > 2 else "")

    if dead:
        disabled = sorted({c for d in dead for c in d["affectedChecks"]})
        yield Finding(
            "check-data-coverage",
            f"Not checked — {_name(dead)}",
            "warning", CAT_META, "check coverage",
            f"RUCKUS ONE returned nothing for {len(dead)} field(s) on every row of this "
            f"snapshot, so {len(disabled)} check(s) could not run: "
            + ", ".join(disabled) + ". "
            "That is NOT the same as those checks passing — those areas are simply "
            "unexamined. Either R1 does not populate the field for this hardware or "
            "firmware, or the crawl is not requesting it.",
            {"emptyFields": dead, "disabledChecks": disabled},
            "For anything important here, verify on the switch CLI — the API is not "
            "reporting it. " + "; ".join(
                f"{e['field']} is read by {', '.join(e['affectedChecks'])}" for e in dead[:4]),
        )
    if thin:
        affected = sorted({c for d in thin for c in d["affectedChecks"]})
        yield Finding(
            "check-data-coverage",
            f"Partial coverage — {_name(thin)}",
            "info", CAT_META, "check coverage",
            f"{len(thin)} field(s) are populated on under 5% of rows, so the "
            f"{len(affected)} check(s) reading them ("
            + ", ".join(affected) + ") see almost no data. They will produce findings for "
            "a small, arbitrary subset and stay silent elsewhere, which reads as a clean "
            "result for everything they could not examine.",
            {"sparseFields": thin, "affectedChecks": affected},
            "Treat conclusions from these fields as partial coverage, not as a pass.",
            confidence="medium",
        )
