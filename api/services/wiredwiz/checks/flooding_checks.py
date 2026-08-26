"""
Flooding, forwarding-profile and data-quality checks.

WHY UNKNOWN UNICAST IS HARD HERE
--------------------------------
Unknown unicast flooding — the failure where a switch's MAC table fills, it stops
knowing where destinations live, and starts flooding unicast to every port in the
VLAN — has no direct counter in the RUCKUS ONE API. There is no
`unknownUnicastIn/Out` field.

The obvious workaround, inferring it from `rx`/`tx` byte deltas, DOES NOT WORK on
this platform, and it fails in a way that manufactures dramatic false findings.
Measured on 4,653 up ports over a 4,789-second window:

  * utilisation implied by tx byte deltas: median 4.08%, max 42.1%
  * utilisation reported by R1's own signalOut: median 0.000%, max 1.0%
  * 4,474 of 4,653 ports (96%) disagreed by more than 5x

One core port read 614 MB/s, then 319 MB/s, then 23 MB/s across three
consecutive windows, while its lifetime average over 68 days of uptime was
148 KB/s. A port genuinely sustaining the implied rate would have moved 681 TB,
not the 875 GB its counter showed. The byte counters are evidently backfilled or
re-synced on a cadence unrelated to when they are read.

So this module does three things instead:
  1. detects the PRECONDITIONS for flooding, which are all reliably observable;
  2. uses the PACKET counters (broadcast/multicast), which behave sanely, to
     catch multicast flooding directly;
  3. ships a data-quality check that flags the byte-counter unreliability, so
     nobody — including a future version of this tool — builds a rate on it.

Measuring unknown unicast itself needs `show interface` on the switch.
"""

import re
import statistics
from collections import defaultdict

from .framework import Finding, check, _as_int, _is_up, _norm_mac

CAT_FLOOD = "flooding"
CAT_SCALE = "aggregation-scale"
CAT_QUALITY = "data-quality"

# Documented FastIron forwarding profiles. profile1 is the default and is what a
# switch runs when no `forwarding-profile` line appears in the config, since
# defaults are not written to the running config.
FORWARDING_PROFILES = {
    "profile1": {"mac": 32768, "note": "default profile"},
    "profile2": {"mac": 98304, "note": "non-default, larger MAC table"},
}
DEFAULT_PROFILE_MAC = 32768

# Models that sit at aggregation and therefore need the scale conversation.
AGGREGATION_MODELS = re.compile(r"ICX\s?7(650|750|850|550)|ICX\s?8[12]00", re.I)
AGGREGATION_NAMES = re.compile(r"CORE|DISTRO|DIST\b|AGG", re.I)


def _is_aggregation(s) -> bool:
    return bool(AGGREGATION_NAMES.search(str(s.get("name") or ""))
                or (s.get("model") or "").upper().startswith("ICX7850"))


# ── Preconditions for flooding ───────────────────────────────────────────────

@check("no-unknown-unicast-limit", "No unknown-unicast rate limiting", CAT_FLOOD,
       needs="configs",
       trigger="no `unknown-unicast limit` on a switch")
def no_unknown_unicast_limit(ctx):
    """
    `unknown-unicast limit` is the only thing that bounds the blast radius when a
    switch starts flooding unicast. Without it, a MAC table that fills at the core
    pushes every unknown destination out of every port in the VLAN at line rate,
    and the damage lands on access switches that are themselves perfectly healthy.
    """
    missing = [c.name for c in ctx.configs.values()
               if not c.has(r"unknown-unicast\s+limit")]
    if not missing:
        return
    agg = [s.get("name") for s in ctx.switches if _is_aggregation(s)]
    exposed = sorted(set(missing) & set(agg))
    yield Finding(
        "no-unknown-unicast-limit",
        f"No unknown-unicast limit on {len(missing)} of {len(ctx.configs)} switches",
        "warning", CAT_FLOOD, "fabric",
        "Nothing bounds unknown-unicast flooding on these switches. This is the failure "
        "mode where a full MAC table at an aggregation switch turns into traffic hitting "
        "every access port downstream: the core forgets where a destination lives, floods "
        "the frame everywhere, and closets that are working fine start seeing traffic that "
        "has nothing to do with them."
        + (f" {len(exposed)} of these are core or distribution switches, where the blast "
           "radius is the whole site." if exposed else ""),
        {"missingCount": len(missing), "switchesAudited": len(ctx.configs),
         "aggregationSwitchesAffected": exposed[:10]},
        "Apply `unknown-unicast limit <kbps>` on access ports, and treat the aggregation "
        "switches as the priority.",
    )


@check("forwarding-profile-default", "Aggregation switch on the default forwarding profile",
       CAT_SCALE, needs="configs",
       trigger="an aggregation switch with no `forwarding-profile` override, i.e. running default profile1 (32,768 MACs)")
def forwarding_profile_default(ctx):
    """
    The forwarding profile sets the hardware table split — MAC addresses, IPv4/IPv6
    routes, IGMP/MLD groups, PIM mcache. `profile1` is the default and has the
    smallest MAC table; `profile2` roughly triples it.

    This matters most at the aggregation layer, because that is where the MAC
    table has to hold every address in the site. When it fills, the switch floods
    unknown unicast — and the symptom appears downstream, not on the switch that
    actually ran out.

    A default profile is NOT written to the running config, so the absence of a
    `forwarding-profile` line means the default is in effect. Changing it needs
    `write-memory` and a reload.
    """
    for cfg in ctx.configs.values():
        sw = next((s for s in ctx.switches if s.get("name") == cfg.name), None)
        if not sw or not _is_aggregation(sw):
            continue
        m = re.search(r"^\s*forwarding-profile\s+(\S+)", cfg.text, re.M | re.I)
        configured = m.group(1).lower() if m else None
        if configured and configured != "profile1":
            continue

        mac_now = ctx.mac_count_for_switch(sw) if hasattr(ctx, "mac_count_for_switch") else None
        yield Finding(
            "forwarding-profile-default",
            f"{cfg.name} — running the default forwarding profile at the aggregation layer",
            "info", CAT_SCALE, cfg.name,
            f"No `forwarding-profile` override is configured, so this switch runs "
            f"**profile1**, the default — the profile with the SMALLEST MAC table "
            f"(documented at {FORWARDING_PROFILES['profile1']['mac']:,} entries, against "
            f"{FORWARDING_PROFILES['profile2']['mac']:,} for profile2). On a core or "
            "distribution switch that table has to hold every MAC in the site. If it "
            "fills, the switch floods unknown unicast to every port in the VLAN, and the "
            "symptom shows up on downstream access switches rather than here — which is "
            "why this is worth knowing before it happens rather than after."
            + (f" It currently holds {mac_now:,} learned entries." if mac_now else ""),
            {"switch": cfg.name, "model": cfg.model,
             "configuredProfile": configured or "none (default profile1 in effect)",
             "defaultProfileMacCapacity": FORWARDING_PROFILES["profile1"]["mac"],
             "profile2MacCapacity": FORWARDING_PROFILES["profile2"]["mac"],
             "learnedMacsNow": mac_now,
             "note": "Defaults are not written to the running config, so absence of the "
                     "command means the default is active. Confirm with "
                     "`show forwarding-profile`."},
            "Confirm the active profile with `show forwarding-profile`. If this switch "
            "aggregates a large layer-2 domain, plan a move to profile2 — it needs "
            "`forwarding-profile profile2`, `write-memory` and a reload, so it is a "
            "maintenance-window change.",
            confidence="medium",
        )


@check("mac-table-headroom", "MAC table headroom against the forwarding profile", CAT_SCALE,
       needs="snapshot",
       trigger=">= 50% of the default forwarding profile's 32,768 MAC entries on an aggregation switch; critical at >= 80%")
def mac_table_headroom(ctx):
    """
    With the profile known, MAC table utilisation finally becomes computable —
    but only for switches where the default profile is in effect and the platform
    supports profiles at all. Reported as headroom, not as a bare percentage, and
    only when it is actually worth attention.
    """
    per_sw = defaultdict(int)
    for m in ctx.latest["macs"]:
        per_sw[_norm_mac(m.get("switchMac"))] += 1

    for s in ctx.switches:
        if s.get("deviceStatus") != "ONLINE" or not _is_aggregation(s):
            continue
        learned = per_sw.get(_norm_mac(s.get("switchMac") or s.get("id")), 0)
        pct = 100 * learned / DEFAULT_PROFILE_MAC
        if pct < 50:
            continue
        yield Finding(
            "mac-table-headroom",
            f"{s.get('name')} — MAC table at {pct:.0f}% of the default profile capacity",
            "critical" if pct >= 80 else "warning", CAT_SCALE, s.get("name"),
            f"{learned:,} learned entries against the default profile's documented "
            f"{DEFAULT_PROFILE_MAC:,}. As this fills, the switch starts flooding unknown "
            "unicast — and that damage lands on downstream access switches, not here.",
            {"learned": learned, "assumedCapacity": DEFAULT_PROFILE_MAC,
             "percent": round(pct), "model": s.get("model"),
             "caveat": "Capacity assumed from the documented default profile. Confirm the "
                       "real figure with `show forwarding-profile` on the switch."},
            "Confirm the active profile, then plan a move to profile2 if this keeps growing.",
        )


# ── Multicast flooding (packet counters ARE reliable) ────────────────────────

@check("multicast-flood-suspected", "Identical multicast volume across many ports",
       CAT_FLOOD, needs="rates",
       trigger=">= 6 ports on one switch+VLAN with multicast rates within 10% of each other and a mean >= 5 pkt/s")
def multicast_flood_suspected(ctx):
    """
    Flooded traffic is by definition identical on every port receiving it. Multicast
    packet counters are trustworthy here (unlike the byte counters), so near-identical
    multicastOut rates across many ports in one VLAN is direct evidence of flooding
    rather than of many hosts independently sending similar volumes.

    With IGMP snooping disabled — which is the case fleet-wide on this estate — all
    multicast is flooded by design, so this quantifies an already-known exposure.
    """
    groups = defaultdict(list)
    for r in (ctx.rates or {}).get("rows", []):
        rate = r.get("multicastIn", 0)
        if rate > 1:
            groups[(r.get("switchName"), r.get("vlan") or "?")].append((rate, r))

    for (switch, vlan), rows in groups.items():
        if len(rows) < 6:
            continue
        rates = [x[0] for x in rows]
        mean = statistics.mean(rates)
        spread = (max(rates) - min(rates)) / max(rates) if max(rates) else 1
        if spread > 0.10 or mean < 5:
            continue
        yield Finding(
            "multicast-flood-suspected",
            f"{switch} — {len(rows)} ports in VLAN {vlan} carrying identical multicast "
            f"({mean:,.0f} pkt/s, {spread * 100:.1f}% spread)",
            "warning", CAT_FLOOD, f"{switch} / VLAN {vlan}",
            f"{len(rows)} ports on this switch in VLAN {vlan} are each receiving multicast "
            f"at {mean:,.0f} packets per second, and the rates differ by only "
            f"{spread * 100:.1f}%. Traffic that identical across many ports is the same "
            "stream being flooded to all of them, not many independent sources. With IGMP "
            "snooping disabled this is expected behaviour — every multicast frame goes to "
            "every port in the VLAN — but it means multicast is consuming access-port "
            "capacity everywhere and inflating any broadcast-domain analysis.",
            {"switch": switch, "vlan": vlan, "ports": len(rows),
             "meanPacketsPerSec": round(mean),
             "spreadPercent": round(spread * 100, 1),
             "examplePorts": [x[1].get("port") for x in rows[:8]]},
            "Enable IGMP snooping (`ip multicast version 2`) so multicast is delivered only "
            "to ports that asked for it.",
        )


# ── Data quality ─────────────────────────────────────────────────────────────

@check("byte-counters-unreliable", "Byte counters disagree with reported utilisation",
       CAT_QUALITY, needs="rates",
       trigger="more than half of ports show tx-implied utilisation over 5x what signalOut reports")
def byte_counters_unreliable(ctx):
    """
    Guard against a whole class of wrong conclusions.

    R1 exposes both cumulative `rx`/`tx` byte counters and its own `signalIn`/
    `signalOut` utilisation figures. When byte deltas imply a utilisation wildly
    above what R1 itself reports, the byte counters are not incrementing in real
    time and any rate derived from them is fiction — including a very convincing
    looking "flooding" finding.
    """
    prev = ctx.snapshots[0]
    curr = ctx.latest
    dt = curr["takenAtEpoch"] - prev["takenAtEpoch"]
    if dt <= 0:
        return
    before = {p["id"]: p for p in prev["ports"]}

    compared = disagreeing = 0
    worst = None
    for p in curr["ports"]:
        o = before.get(p["id"])
        if not o or not _is_up(p):
            continue
        dtx = _as_int(p.get("tx")) - _as_int(o.get("tx"))
        if dtx <= 0:
            continue
        m = re.match(r"([\d.]+)\s*(G|M)", str(p.get("portSpeed") or ""), re.I)
        if not m:
            continue
        cap = float(m.group(1)) * (1e9 if m.group(2).upper() == "G" else 1e6)
        implied = 100 * (dtx * 8 / dt) / cap
        reported = _as_int(p.get("signalOut")) / 100
        compared += 1
        if implied > 5 * max(reported, 0.01):
            disagreeing += 1
            if not worst or implied > worst[0]:
                worst = (implied, reported, ctx.port_label(p))

    if not compared or disagreeing / compared < 0.5:
        return
    yield Finding(
        "byte-counters-unreliable",
        f"rx/tx byte counters disagree with reported utilisation on "
        f"{disagreeing} of {compared} ports",
        "warning", CAT_QUALITY, "fabric",
        f"On {round(100 * disagreeing / compared)}% of comparable ports, the utilisation "
        "implied by the change in the rx/tx byte counters is more than five times what R1's "
        "own signalIn/signalOut utilisation reports"
        + (f" — worst case {worst[0]:.1f}% implied against {worst[1]:.2f}% reported on "
           f"{worst[2]}" if worst else "")
        + ". The byte counters are evidently not incrementing on the same cadence as they "
        "are read, so any throughput or flooding conclusion drawn from byte deltas will be "
        "wrong, often by orders of magnitude. WiredWiz therefore does not use them for "
        "rates: utilisation comes from signalIn/signalOut, and the packet counters "
        "(broadcast, multicast, errors, discards) are used for everything else.",
        {"portsCompared": compared, "portsDisagreeing": disagreeing,
         "percentDisagreeing": round(100 * disagreeing / compared),
         "worstImpliedPercent": round(worst[0], 1) if worst else None,
         "worstReportedPercent": round(worst[1], 2) if worst else None,
         "worstPort": worst[2] if worst else None},
        "Treat any byte-based throughput figure from this API as unusable. For real "
        "throughput use signalIn/signalOut, or read the switch directly.",
    )
