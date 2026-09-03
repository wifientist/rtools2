"""
Config-based checks — what a senior ICX engineer reads a running config for.

SYNTAX TARGET: RUCKUS FastIron 10.0.x on ICX (validated against 10.0.10, the
build running on the estate this was written against: ICX8200 `RDR10010g_cd6`,
ICX7150 `SPR10010g_cd6`, ICX7850 `TNR10010g_cd6`, all reporting
`ver 10.0.10g_cd6`).

Every pattern below was checked two ways, because a regex that cannot match is
indistinguishable from a check that passed:
  * against 192 real running configs, to confirm what the platform emits; and
  * against the FastIron 10.0.10 command reference, for commands that are
    ABSENT from those configs — absence proves nothing about syntax, only that
    the feature is unconfigured.

Corrections that came out of that pass are marked inline with `# 10.0.x:`.

Ordered roughly by how often each one turns out to be the actual cause of
"the network is flapping": loop containment first, then forensics (can you even
tell what happened?), then hygiene.

Every check reads only the REDACTED config, so no check can depend on a secret.
"""

import re
from collections import Counter, defaultdict

from .framework import Finding, check

CAT_STP = "loop-containment"
CAT_FORENSICS = "forensics"
CAT_L2 = "layer-2"
CAT_MGMT = "management"


def _cfgs(ctx):
    return list(ctx.configs.values())


# ── Loop containment ─────────────────────────────────────────────────────────

@check("stp-global-missing", "Spanning tree not enabled", CAT_STP, needs="configs",
       trigger="no `spanning-tree` line anywhere in the running config")
def stp_global_missing(ctx):
    """A switch with no spanning-tree at all cannot break a loop."""
    bad = [c for c in _cfgs(ctx) if not c.has(r"^\s*spanning-tree")]
    for c in bad:
        yield Finding(
            "stp-global-missing", "Spanning tree not enabled anywhere in this config",
            "critical", CAT_STP, c.name,
            "No spanning-tree statement appears in the running config. If this switch "
            "sits on a redundant path, nothing will break the loop.",
            {"switch": c.name, "model": c.model},
            "Enable RSTP: `spanning-tree 802-1w` globally, and per VLAN if running PVST.",
        )


@check("stp-vlan-uncovered", "VLAN with no spanning-tree instance", CAT_STP, needs="configs",
       trigger="a `vlan N` block with no `spanning-tree` line inside it; critical when no switch covers that VLAN at all")
def stp_vlan_uncovered(ctx):
    """
    ICX runs per-VLAN spanning tree. A VLAN defined without a `spanning-tree`
    line inside its block has NO instance -- a loop in that VLAN is uncontained
    even though the switch looks protected globally.
    """
    covered, uncovered = Counter(), defaultdict(list)
    for c in _cfgs(ctx):
        for vid, body in c.vlans().items():
            if any(re.match(r"spanning-tree", line, re.I) for line in body):
                covered[vid] += 1
            else:
                uncovered[vid].append(c.name)

    # Which switches physically CANNOT take another STP instance. Without this,
    # the remediation below tells someone to run a command the switch will
    # refuse with "would exceed the maximum stp instance limit".
    at_ceiling = {}
    for c in _cfgs(ctx):
        m = re.search(r"^\s*system-max\s+spanning-tree\s+(\d+)", c.text, re.M | re.I)
        limit = int(m.group(1)) if m else 32          # FastIron 10.0.x default
        used = sum(1 for body in c.vlans().values()
                   if any(re.match(r"spanning-tree", l, re.I) for l in body))
        if used >= limit:
            at_ceiling[c.name] = {"limit": limit, "inUse": used}

    for vid, switches in sorted(uncovered.items(), key=lambda kv: -len(kv[1])):
        never = covered[vid] == 0
        blocked = [n for n in switches if n in at_ceiling]
        yield Finding(
            "stp-vlan-uncovered",
            f"VLAN {vid} has no spanning-tree instance on {len(switches)} switch(es)",
            "critical" if never else "warning", CAT_STP, f"VLAN {vid}",
            (f"VLAN {vid} is defined on {len(switches) + covered[vid]} switches but carries a "
             f"spanning-tree instance on only {covered[vid]}. "
             + ("No switch protects it at all — a loop in this VLAN will not be broken."
                if never else
                "Coverage is inconsistent, so the VLAN is protected on some paths and not "
                "others, which is worse than uniformly off: the topology STP computes will "
                "not match the topology traffic actually takes.")
             + (f" NOTE: {len(blocked)} of these switch(es) are already at their "
                f"spanning-tree instance ceiling, so the usual fix will not work there — "
                "see the stp-instance-limit finding."
                if blocked else "")),
            {"vlan": vid, "switchesWithStp": covered[vid],
             "switchesWithout": len(switches), "switchesMissingStp": sorted(switches),
             "switchesAtInstanceCeiling": sorted(blocked),
             "blockedCount": len(blocked)},
            (f"Add `spanning-tree 802-1w` inside `vlan {vid}` on every switch that carries "
             "it." if not blocked else
             f"On the {len(switches) - len(blocked)} switch(es) with headroom, add "
             f"`spanning-tree 802-1w` inside `vlan {vid}`. On the {len(blocked)} already at "
             "the instance ceiling this command WILL BE REFUSED — those need MSTP, a higher "
             "`system-max spanning-tree` (reload required), or fewer VLANs trunked to them."),
        )


@check("stp-mode-mixed", "Mixed spanning-tree modes across the fleet", CAT_STP, needs="configs",
       trigger="both RSTP (802-1w) and legacy STP present across the audited configs")
def stp_mode_mixed(ctx):
    """RSTP and legacy STP in one L2 domain converge at legacy speed, or not cleanly."""
    modes = defaultdict(list)
    for c in _cfgs(ctx):
        if c.has(r"^\s*spanning-tree\s+(802-1w|rstp)"):
            modes["rstp"].append(c.name)
        elif c.has(r"^\s*spanning-tree"):
            modes["legacy-stp"].append(c.name)
    if len(modes) > 1:
        yield Finding(
            "stp-mode-mixed", "Both RSTP and legacy STP are in use", "warning", CAT_STP,
            "fabric",
            "Switches in the same layer-2 domain run different spanning-tree modes. "
            "Convergence falls back to the slower protocol's timers (up to 50s), which "
            "is long enough for a transient loop to flood the domain.",
            {k: {"count": len(v), "switches": sorted(v)} for k, v in modes.items()},
            "Standardise on `spanning-tree 802-1w` (RSTP) fleet-wide.",
        )


@check("stp-root-undefined", "No deterministic root bridge", CAT_STP, needs="configs",
       trigger="no switch anywhere sets a `spanning-tree ... priority`")
def stp_root_undefined(ctx):
    """
    If nobody sets a bridge priority, the root is elected by lowest MAC address --
    which usually means a random access switch in a closet becomes root, and the
    root moves whenever that switch reboots.
    """
    with_priority = [c.name for c in _cfgs(ctx)
                     if c.has(r"spanning-tree\s+(?:\S+\s+)*priority\s+\d+")]
    total = len(_cfgs(ctx))
    if total and not with_priority:
        yield Finding(
            "stp-root-undefined", "No switch sets a spanning-tree priority", "warning",
            CAT_STP, "fabric",
            f"None of the {total} configs set a bridge priority, so the root bridge is "
            "elected purely by lowest MAC address. The root is then effectively random, "
            "usually lands on an old access switch, and moves on every reboot — each move "
            "is a full topology recalculation and a burst of flooding.",
            {"switchesAudited": total, "withPriority": 0},
            "Set a low priority on the core (`spanning-tree priority 4096`) and a "
            "secondary on the distribution layer (`8192`) so the root is deterministic.",
        )


@check("no-loop-detection", "Loop detection not configured", CAT_STP, needs="configs",
       trigger="no `loop-detection` on an interface or VLAN; critical when no switch has it")
def no_loop_detection(ctx):
    """
    STP only protects paths that speak STP. A dumb switch or a patch cable between
    two wall ports creates a loop STP never sees; `loop-detection` catches exactly
    that case and err-disables the port.
    """
    # 10.0.x: `loop-detection` at interface level (strict mode) or VLAN level
    # (loose mode); disabled by default. `loop-detection-interval` is global.
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*loop-detection\b")]
    if missing and len(missing) == len(_cfgs(ctx)):
        yield Finding(
            "no-loop-detection", "Loop detection is not enabled on any switch", "critical",
            CAT_STP, "fabric",
            f"None of the {len(missing)} switches run `loop-detection`. Spanning tree only "
            "protects links that participate in it — an unmanaged switch or a cable "
            "patched between two access ports forms a loop STP cannot see, and nothing "
            "here will shut it down.",
            {"switchesAudited": len(missing), "switches": sorted(missing)},
            "Enable `loop-detection` globally and `loop-detection-shutdown <seconds>` so "
            "an offending port disables itself instead of melting the VLAN.",
        )
    elif missing:
        yield Finding(
            "no-loop-detection", f"Loop detection missing on {len(missing)} switch(es)",
            "warning", CAT_STP, "fabric",
            "Loop detection is configured on some switches but not others, leaving "
            "unprotected edges.",
            {"missing": sorted(missing), "missingCount": len(missing)},
            "Enable `loop-detection` on the remaining switches.",
        )


@check("no-storm-control", "No broadcast/multicast rate limiting", CAT_STP, needs="configs",
       trigger="no `broadcast|multicast|unknown-unicast limit` on any switch")
def no_storm_control(ctx):
    """Storm control is the blast-radius limiter when a loop does form."""
    # 10.0.x: `broadcast limit`, `multicast limit`, `unknown-unicast limit` —
    # all interface level. Verified against the 10.0.10 command reference.
    missing = [c.name for c in _cfgs(ctx)
               if not c.has(r"(broadcast|multicast|unknown-unicast)\s+limit")]
    if missing and len(missing) == len(_cfgs(ctx)):
        yield Finding(
            "no-storm-control", "No storm control anywhere in the fleet", "warning",
            CAT_STP, "fabric",
            f"None of the {len(missing)} switches limit broadcast, multicast or "
            "unknown-unicast rates. Without it a loop saturates every port in the VLAN "
            "at line rate before anyone can react, and CPU-bound control traffic "
            "(including STP BPDUs) starts getting dropped — which can prevent STP from "
            "converging and break the very mechanism meant to stop the loop.",
            {"switchesAudited": len(missing), "switches": sorted(missing)},
            "Apply `broadcast limit <kbps>` and `multicast limit <kbps>` on access ports.",
        )


@check("no-bpdu-guard", "BPDU guard missing on edge switches", CAT_STP, needs="configs",
       trigger="no `bpdu-guard` / `stp-bpdu-guard` in the config")
def no_bpdu_guard(ctx):
    """BPDU guard stops a rogue switch plugged into an access port from becoming root."""
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"bpdu-guard")]
    if missing:
        yield Finding(
            "no-bpdu-guard", f"BPDU guard missing on {len(missing)} switch(es)",
            "warning" if len(missing) < len(_cfgs(ctx)) else "critical", CAT_STP, "fabric",
            "Without BPDU guard, anyone plugging a switch into an access port can inject "
            "BPDUs, win the root election and re-shape the whole topology.",
            {"missing": sorted(missing), "missingCount": len(missing),
             "switchesAudited": len(_cfgs(ctx))},
            "Apply `spanning-tree 802-1w admin-edge-port` plus BPDU guard on access ports.",
        )


@check("no-root-guard", "Root guard not configured", CAT_STP, needs="configs",
       trigger="no `spanning-tree root-protect` on any switch")
def no_root_guard(ctx):
    """Root guard pins the root where you put it, even if a downstream device argues."""
    # 10.0.x: `spanning-tree root-protect`, interface level, disabled by default.
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"root-protect|root-guard")]
    if missing and len(missing) == len(_cfgs(ctx)):
        yield Finding(
            "no-root-guard", "Root guard is not configured anywhere", "info", CAT_STP,
            "fabric",
            "No switch uses root guard, so the root bridge can be taken over by any device "
            "advertising a better bridge ID.",
            {"switchesAudited": len(missing), "switches": sorted(missing)},
            "Apply `spanning-tree root-protect` on downstream-facing distribution ports "
            "(interface level; disabled by default).",
            confidence="medium",
        )


# ── Forensics: can you tell what happened? ───────────────────────────────────

@check("no-syslog", "No syslog server configured", CAT_FORENSICS, needs="configs",
       trigger="no `logging host` (local `logging buffered`/`persistence` does not count)")
def no_syslog(ctx):
    """
    This is the one that matters most for a flapping problem. R1 exposes no
    port-flap history at all, so if the switches are not shipping syslog either,
    link transitions are simply not recorded anywhere.
    """
    # 10.0.x: remote destination is `logging host {ipv4 | name | ipv6 addr}`,
    # global config, up to six servers. `logging buffered` / `logging persistence`
    # are LOCAL only and do not ship anything off the box.
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*logging\s+host\b")]
    if not missing:
        return
    total = len(_cfgs(ctx))
    buffered = sum(1 for c in _cfgs(ctx) if c.has(r"^\s*logging\s+buffered"))
    persistent = sum(1 for c in _cfgs(ctx) if c.has(r"^\s*logging\s+persistence"))
    yield Finding(
        "no-syslog", f"No remote syslog destination on {len(missing)} of {total} switches",
        "critical" if len(missing) == total else "warning", CAT_FORENSICS, "fabric",
        f"No `logging host` is configured, so nothing is shipped off the switch. "
        f"({buffered} switch(es) do have a local `logging buffered` ring and "
        f"{persistent} have `logging persistence`, so some history survives locally — but "
        "it is per-switch, small, and rolls over.) RUCKUS ONE exposes no port-flap history "
        "through its API either, so there is no central record of when a link went down or "
        "why, and every investigation has to catch the fault live.",
        {"missing": sorted(missing), "missingCount": len(missing),
         "switchesAudited": total, "withLocalBuffer": buffered,
         "withPersistence": persistent},
        "Point them at a collector: `logging host <ip>` plus `logging buffered 4096`. "
        "This is the single highest-value change for diagnosing intermittent flapping.",
    )


@check("no-ntp", "No NTP server configured", CAT_FORENSICS, needs="configs",
       trigger="no `ntp` or `sntp` statement")
def no_ntp(ctx):
    """Log timestamps you cannot correlate across switches are barely evidence."""
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*(ntp|sntp)\b")]
    if missing:
        yield Finding(
            "no-ntp", f"No NTP on {len(missing)} switch(es)", "warning", CAT_FORENSICS,
            "fabric",
            "Without synchronised clocks, log lines from different switches cannot be put "
            "in order, so you cannot tell which end of a link dropped first — which is "
            "exactly the question a flap investigation turns on.",
            {"missing": sorted(missing), "missingCount": len(missing)},
            "Configure `ntp server <ip>` (two sources) and set the timezone consistently.",
        )


# ── Layer 2 hygiene ──────────────────────────────────────────────────────────

@check("no-igmp-snooping", "IGMP snooping disabled", CAT_L2, needs="configs",
       trigger="no `ip multicast` line on any switch")
def no_igmp_snooping(ctx):
    """Without snooping, all multicast floods like broadcast to every port in the VLAN."""
    # 10.0.x: `ip multicast version [2 | 3]` in global config (defaults to v2 when
    # no version is given). The older `ip multicast active|passive` form is not
    # what 10.0 emits, so match the bare prefix.
    missing = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*ip\s+multicast\b")]
    if missing and len(missing) == len(_cfgs(ctx)):
        yield Finding(
            "no-igmp-snooping", "IGMP snooping not enabled on any switch", "warning",
            CAT_L2, "fabric",
            "With snooping off, every multicast frame is flooded to every port in the "
            "VLAN exactly like broadcast. On a campus with wireless APs this can be the "
            "dominant traffic class and it makes broadcast-rate analysis much noisier.",
            {"switchesAudited": len(missing), "switches": sorted(missing)},
            "Enable IGMP snooping globally with `ip multicast version 2` (or `3`).",
        )


@check("jumbo-inconsistent", "Inconsistent jumbo frame setting", CAT_L2, needs="configs",
       trigger="`jumbo` present on some switches and absent on others")
def jumbo_inconsistent(ctx):
    """An MTU mismatch across a trunk shows up as sporadic large-packet loss."""
    on = [c.name for c in _cfgs(ctx) if c.has(r"^\s*jumbo")]
    off = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*jumbo")]
    if on and off:
        yield Finding(
            "jumbo-inconsistent", "Jumbo frames enabled on some switches only", "warning",
            CAT_L2, "fabric",
            f"{len(on)} switch(es) have jumbo enabled and {len(off)} do not. Across a trunk "
            "this drops oversized frames silently — the symptom is 'some things work, big "
            "transfers fail', not a clean link failure.",
            {"jumboOn": sorted(on), "jumboOff": sorted(off),
             "onCount": len(on), "offCount": len(off)},
            "Set `jumbo` consistently across every switch in the L2 domain, then reload.",
        )


@check("no-dhcp-snooping", "DHCP snooping not configured", CAT_L2, needs="configs",
       trigger="no `ip dhcp snooping` (note: space, not hyphen)")
def no_dhcp_snooping(ctx):
    """
    Rogue DHCP is a top-3 cause of 'the network is broken' on campus edges.

    10.0.x: the command is `ip dhcp snooping` — SPACE, not a hyphen. An earlier
    version of this check matched `ip dhcp-snooping` and therefore matched
    nothing, reporting "not enabled anywhere" on an estate where 183 of 192
    switches had it configured. A pattern that cannot match is worse than no
    check at all.
    """
    have, missing = [], []
    for c in _cfgs(ctx):
        (have if c.has(r"ip\s+dhcp[\s-]snooping") else missing).append(c.name)
    if not missing:
        return
    all_missing = not have
    yield Finding(
        "no-dhcp-snooping",
        "DHCP snooping not enabled anywhere" if all_missing
        else f"DHCP snooping missing on {len(missing)} of {len(_cfgs(ctx))} switches",
        "warning" if all_missing else "info", CAT_L2, "fabric",
        "These switches do not filter DHCP offers from access ports, so a rogue or "
        "misconfigured device can hand out addresses to the segment."
        + ("" if all_missing else
           f" It is configured on the other {len(have)}, so this is a coverage gap "
           "rather than a policy decision."),
        {"missing": sorted(missing), "missingCount": len(missing),
         "configuredCount": len(have), "switchesAudited": len(_cfgs(ctx))},
        "Enable `ip dhcp snooping` globally and `ip dhcp snooping vlan <list>`, trusting "
        "only uplink ports.",
    )


# ── Management plane ─────────────────────────────────────────────────────────

@check("telnet-not-disabled", "Telnet not explicitly disabled", CAT_MGMT, needs="configs",
       trigger="no explicit `no telnet server` — Telnet is default-ON on most ICX and defaults are not written to config")
def telnet_not_disabled(ctx):
    """
    Cleartext management on a switch that also carries user VLANs.

    10.0.x: Telnet is enabled BY DEFAULT on most ICX models (a few ICX7150
    variants ship with it off), and a running config does not echo defaults. So
    the finding is the ABSENCE of an explicit `no telnet server`, not the
    presence of an enable line. The earlier version of this check looked for
    `telnet server` and could therefore never fire.
    """
    bad = [c.name for c in _cfgs(ctx) if not c.has(r"^\s*no\s+telnet\s+server\b")]
    if not bad:
        return
    yield Finding(
        "telnet-not-disabled",
        f"Telnet not explicitly disabled on {len(bad)} of {len(_cfgs(ctx))} switches",
        "warning", CAT_MGMT, "fabric",
        "No `no telnet server` appears in these configs. Telnet is enabled by default on "
        "most ICX models and defaults are not echoed into the running config, so these "
        "switches are most likely accepting cleartext management sessions on the same "
        "wire as user traffic. A few ICX7150 variants ship with Telnet off, which is why "
        "this is worth confirming rather than assuming.",
        {"switches": sorted(bad), "count": len(bad),
         "switchesAudited": len(_cfgs(ctx))},
        "Confirm with `show telnet`, then `no telnet server` and manage over SSH only.",
        confidence="medium",
    )


@check("snmp-v2c", "SNMP v1/v2c community in use", CAT_MGMT, needs="configs",
       trigger="any `snmp-server community` line present")
def snmp_v2c(ctx):
    """v2c communities are a cleartext shared password with no per-user accountability."""
    bad = [c.name for c in _cfgs(ctx) if c.has(r"^\s*snmp-server\s+community")]
    if bad:
        yield Finding(
            "snmp-v2c", f"SNMP v1/v2c community configured on {len(bad)} switch(es)",
            "info", CAT_MGMT, "fabric",
            "Community strings are sent in cleartext and shared across devices. "
            "(WiredWiz never stores the value — it is redacted before the config leaves "
            "the backend.)",
            {"switches": sorted(bad), "count": len(bad)},
            "Move to SNMPv3 with auth+priv, then remove the v2c communities.",
        )


@check("firmware-drift", "Firmware versions inconsistent", CAT_MGMT, needs="snapshot",
       trigger="more than one firmware version within a model family; warning above two")
def firmware_drift(ctx):
    """
    Reads inventory, not config. Mixed firmware within a model family is a common
    source of 'only these switches misbehave' — L2 protocol bugs are version-specific.
    """
    by_family = defaultdict(Counter)
    for s in ctx.switches:
        if s.get("deviceStatus") != "ONLINE":
            continue
        fam, fw = s.get("family") or s.get("model"), s.get("firmwareVersion")
        if fam and fw:
            by_family[fam][fw] += 1

    for fam, versions in by_family.items():
        if len(versions) > 1:
            ranked = versions.most_common()
            odd = sum(n for _, n in ranked[1:])
            yield Finding(
                "firmware-drift", f"{fam}: {len(versions)} firmware versions in use",
                "warning" if len(versions) > 2 else "info", CAT_MGMT, fam,
                f"{fam} switches run {len(versions)} different firmware versions "
                f"({odd} device(s) off the majority build). L2 behaviour — STP timers, "
                "loop detection, LLDP — is version-specific, so a fault that appears on "
                "only some switches of the same model is often really a firmware delta.",
                {"family": fam, "versions": dict(ranked),
                 "majority": ranked[0][0], "offMajority": odd},
                f"Align {fam} on {ranked[0][0]} unless there is a reason not to.",
            )


# ── Config drift (needs a stored baseline to compare against) ────────────────

@check("config-drift", "Running config changed since the baseline", CAT_MGMT,
       needs="configs",
       trigger="running config differs from the stored baseline; warning when the changed lines touch STP, VLANs, trunking or port state")
def config_drift(ctx):
    """
    Diff the configs read for this run against the stored baseline.

    "What changed?" is the first question a senior engineer asks when a network
    that worked yesterday does not today, and it is the question no live metric
    can answer. This is the whole reason baselines are kept.
    """
    baseline = getattr(ctx, "baseline", None)
    if not baseline:
        return

    base_configs = baseline.get("configs") or {}
    if not base_configs:
        return

    for sid, cfg in ctx.configs.items():
        old = base_configs.get(sid)
        if not old:
            yield Finding(
                "config-drift", f"{cfg.name} — no baseline on record", "info", CAT_MGMT,
                cfg.name,
                "This switch was not present in the baseline, so nothing can be compared. "
                "It is either new to the estate or was unreachable when the baseline was "
                "taken.",
                {"switch": cfg.name, "baselineTakenAt": baseline.get("takenAt")},
                "Re-take the baseline to include it.",
                confidence="medium",
            )
            continue

        old_lines = [l.rstrip() for l in (old.get("config") or "").splitlines()]
        new_lines = [l.rstrip() for l in cfg.text.splitlines()]
        if old_lines == new_lines:
            continue

        # Multiset diff, then dedupe for display. A line like `spanning-tree 802-1w`
        # appears dozens of times in one config; counting every occurrence as a
        # separate change turns one edit into "+23 lines" and reads as far more
        # churn than actually happened.
        old_count, new_count = Counter(old_lines), Counter(new_lines)
        def _changed(a, b):
            out, seen = [], set()
            for line in a:
                if not line or line.startswith("!") or line in seen:
                    continue
                if a[line] > b.get(line, 0):
                    seen.add(line)
                    out.append(line)
            return out
        added = _changed(new_count, old_count)
        removed = _changed(old_count, new_count)
        if not added and not removed:
            continue

        # Changes that can actually break or fix loop behaviour get ranked up.
        significant = re.compile(
            r"spanning-tree|loop-detection|vlan\s+\d+|lag\b|trunk|untagged|tagged|"
            r"bpdu|root-protect|(broadcast|multicast|unknown-unicast)\s+limit|"
            r"disable|enable\b|shutdown", re.I)
        hot = list(dict.fromkeys(l for l in added + removed if significant.search(l)))

        yield Finding(
            "config-drift",
            f"{cfg.name} — config changed since baseline "
            f"(+{len(added)} / -{len(removed)} distinct line(s))",
            "warning" if hot else "info", CAT_MGMT, cfg.name,
            f"The running config differs from the baseline taken {baseline.get('takenAt')}. "
            + (f"{len(hot)} of the changed lines touch layer-2 behaviour (spanning tree, "
               "VLANs, trunking, port state) — exactly the settings that decide whether a "
               "loop is contained."
               if hot else
               "None of the changed lines touch spanning tree, VLANs or port state."),
            {"switch": cfg.name, "baselineTakenAt": baseline.get("takenAt"),
             "addedCount": len(added), "removedCount": len(removed),
             "note": "counts are distinct changed lines, not occurrences",
             "significantChanges": hot,
             "added": added, "removed": removed},
            "Confirm the change was intentional. If it was not, this is the most likely "
            "cause of a network that behaved differently yesterday.",
        )


# ── Validation scope ─────────────────────────────────────────────────────────

@check("config-syntax-scope", "Config checked against a firmware it was not validated for",
       CAT_MGMT, needs="configs",
       trigger="a switch whose `ver` is not 10.0.x, since the config patterns are validated for 10.0.x only")
def config_syntax_scope(ctx):
    """
    The config rules in this module are validated against FastIron 10.0.x.

    Command syntax moves between FastIron trains — `ip dhcp snooping` vs
    `ip dhcp-snooping` is a real example that silently broke a check here. If a
    switch runs a different train, a pattern may not match and the check will
    look like it passed. Say so rather than report a clean bill of health.
    """
    off_train = defaultdict(list)
    for c in _cfgs(ctx):
        m = re.search(r"^\s*ver\s+(\S+)", c.text, re.M | re.I)
        ver = m.group(1) if m else None
        if not ver:
            off_train["unknown"].append(c.name)
        elif not ver.startswith("10.0"):
            off_train[ver.split("_")[0]].append(c.name)

    if not off_train:
        return
    total = sum(len(v) for v in off_train.values())
    yield Finding(
        "config-syntax-scope",
        f"{total} switch(es) run firmware outside the validated 10.0.x range",
        "warning", CAT_MGMT, "fabric",
        "The config rules here were validated against FastIron 10.0.x syntax, both against "
        "real 10.0.10 running configs and the 10.0.10 command reference. On a different "
        "train a command may be spelled differently, in which case the pattern will not "
        "match and the check will look like it passed. Treat config findings for these "
        "switches as unverified rather than clean.",
        {"versions": {k: sorted(v) for k, v in off_train.items()},
         "affectedCount": total, "validatedFor": "FastIron 10.0.x"},
        "Either align these switches on 10.0.x, or verify the affected commands by hand "
        "on one of them.",
    )


@check("loop-detect-recovery-orphan", "Err-disable recovery set for loop detection that is "
       "not enabled", CAT_STP, needs="configs",
       trigger="`errdisable recovery cause loop-detect` present while `loop-detection` is absent")
def loop_detect_recovery_orphan(ctx):
    """
    `errdisable recovery cause loop-detect` only does something once
    `loop-detection` is actually enabled on an interface or VLAN. Configuring the
    recovery without the detection is a strong tell that somebody started
    enabling loop protection and stopped half way — the switch looks protected in
    a config review and is not.
    """
    for c in _cfgs(ctx):
        if not c.has(r"errdisable\s+recovery\s+cause\s+loop-detect"):
            continue
        if c.has(r"^\s*loop-detection\b"):
            continue
        interval = c.find(r"^\s*errdisable\s+recovery\s+interval\s+\d+")
        yield Finding(
            "loop-detect-recovery-orphan",
            f"{c.name} — err-disable recovery configured for loop detection, but loop "
            "detection is not enabled",
            "warning", CAT_STP, c.name,
            "`errdisable recovery cause loop-detect` is configured, which only takes effect "
            "once `loop-detection` is enabled on an interface (strict mode) or a VLAN "
            "(loose mode). Neither appears in this config, so nothing will ever err-disable "
            "a looped port here and nothing will ever be recovered. In a config review this "
            "switch reads as loop-protected; it is not.",
            {"switch": c.name, "model": c.model,
             "recoveryInterval": interval[0].strip() if interval else None},
            "Enable `loop-detection` on the access interfaces (or on the VLAN for loose "
            "mode). The recovery configuration is already in place and will then work.",
        )
