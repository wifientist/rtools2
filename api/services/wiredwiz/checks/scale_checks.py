"""
System-max / scale-limit checks.

The point of these: a recommendation you cannot actually execute is worse than
no recommendation. WiredWiz was advising "add `spanning-tree 802-1w` to every
VLAN" on switches that had already hit the platform's spanning-tree instance
ceiling, where that command returns:

    Start spanning tree on this VLAN/VLAN-group would exceed the maximum
    stp instance 32 limit

Verified on the estate this was written against: eight ICX8200-24FX switches
carry 44-45 VLANs and have exactly 32 STP instances each. Stopping precisely at
32 across eight independent switches is the ceiling, not a coincidence.

FastIron 10.0.x defaults (confirmed against the command reference):
  * `system-max spanning-tree`  default 32,   range 1-254
  * `system-max vlan`           default 1024, max 4095

Defaults are not written to the running config, so an absent `system-max`
line means the default is in force. `show default-values` on the switch prints
the full table and is the way to confirm any of this per platform.
"""

import re
from collections import defaultdict

from .framework import Finding, check

CAT_SCALE = "scale-limits"

# (default, hard max) per FastIron 10.0.x. Only entries verified against the
# command reference are listed -- a guessed limit here would produce exactly the
# kind of unactionable advice this module exists to prevent.
SYSTEM_MAX_DEFAULTS = {
    "spanning-tree": (32, 254),
    "vlan": (1024, 4095),
}

SYSTEM_MAX_RE = re.compile(r"^\s*system-max\s+([a-z0-9-]+)\s+(\d+)", re.M | re.I)


def _system_max(cfg, name: str):
    """Configured value for a system-max knob, or its documented default."""
    for m in SYSTEM_MAX_RE.finditer(cfg.text):
        if m.group(1).lower() == name:
            return int(m.group(2)), "configured"
    default = SYSTEM_MAX_DEFAULTS.get(name, (None, None))[0]
    return default, "default"


def _counts(cfg):
    """(vlan count, vlans carrying a spanning-tree instance)."""
    vlans = cfg.vlans()
    stp = sum(1 for body in vlans.values()
              if any(re.match(r"spanning-tree", l, re.I) for l in body))
    return len(vlans), stp


@check("stp-instance-limit", "VLANs exceed the spanning-tree instance limit", CAT_SCALE,
       needs="configs",
       trigger="VLAN count on a switch exceeds `system-max spanning-tree` (default 32). "
               "Critical when STP instances are already AT the ceiling, because further "
               "`spanning-tree` commands will be rejected")
def stp_instance_limit(ctx):
    """
    A switch cannot run more per-VLAN spanning-tree instances than
    `system-max spanning-tree` allows. Once it is at the ceiling, enabling STP on
    another VLAN is refused outright — so "put spanning tree on every VLAN" stops
    being possible, and the uncovered VLANs stay uncovered until either the limit
    is raised (needs a reload) or the design moves to MSTP.
    """
    for cfg in ctx.configs.values():
        vlan_count, stp_count = _counts(cfg)
        limit, source = _system_max(cfg, "spanning-tree")
        if not limit or vlan_count <= limit:
            continue

        at_ceiling = stp_count >= limit
        uncovered = vlan_count - stp_count
        yield Finding(
            "stp-instance-limit",
            f"{cfg.name} — {vlan_count} VLANs but only {limit} spanning-tree instances "
            f"available ({stp_count} in use)",
            "critical" if at_ceiling else "warning", CAT_SCALE, cfg.name,
            f"This switch carries {vlan_count} VLANs against a spanning-tree instance "
            f"limit of {limit} ({source}). "
            + (f"It is already at the ceiling with {stp_count} instances in use, so "
               f"`spanning-tree 802-1w` on any of the remaining {uncovered} VLAN(s) will be "
               "REJECTED with 'would exceed the maximum stp instance limit'. Those VLANs "
               "cannot be protected by per-VLAN STP on this hardware as configured — a "
               "loop in one of them will not be broken."
               if at_ceiling else
               f"Only {stp_count} instances are in use so there is headroom today, but the "
               f"VLAN count already exceeds the limit — you cannot protect all "
               f"{vlan_count} of them.")
            + " Defaults are not written to the running config, so confirm with "
              "`show default-values`.",
            {"switch": cfg.name, "model": cfg.model,
             "vlanCount": vlan_count, "stpInstancesInUse": stp_count,
             "stpInstanceLimit": limit, "limitSource": source,
             "vlansWithoutStp": uncovered, "atCeiling": at_ceiling,
             "documentedDefault": SYSTEM_MAX_DEFAULTS["spanning-tree"][0],
             "documentedMax": SYSTEM_MAX_DEFAULTS["spanning-tree"][1]},
            f"Three options, in order of preference: (1) move this switch to MSTP, which "
            f"maps many VLANs onto a few instances and is the designed answer to this "
            f"problem; (2) raise the ceiling with `system-max spanning-tree <n>` (up to "
            f"{SYSTEM_MAX_DEFAULTS['spanning-tree'][1]}) — needs `write-memory` and a "
            "reload, and costs memory; (3) reduce the number of VLANs trunked to this "
            "switch to only those it actually needs.",
        )


@check("stp-instance-near-limit", "Spanning-tree instances close to the limit", CAT_SCALE,
       needs="configs",
       trigger="STP instances in use >= 80% of `system-max spanning-tree`, on a switch "
               "not already over the VLAN limit")
def stp_instance_near_limit(ctx):
    """
    Headroom warning. Hitting this ceiling is not gradual — the next
    `spanning-tree` command simply fails, usually while someone is adding a VLAN
    under time pressure.
    """
    for cfg in ctx.configs.values():
        vlan_count, stp_count = _counts(cfg)
        limit, source = _system_max(cfg, "spanning-tree")
        if not limit or vlan_count > limit or not stp_count:
            continue          # the over-limit case is reported by stp-instance-limit
        pct = 100 * stp_count / limit
        if pct < 80:
            continue
        yield Finding(
            "stp-instance-near-limit",
            f"{cfg.name} — {stp_count} of {limit} spanning-tree instances used "
            f"({pct:.0f}%)",
            "warning", CAT_SCALE, cfg.name,
            f"{stp_count} spanning-tree instances are in use against a limit of {limit} "
            f"({source}), leaving room for {limit - stp_count} more. There is no gradual "
            "degradation here — once the ceiling is reached the next `spanning-tree` "
            "command is simply refused, which tends to be discovered while adding a VLAN "
            "in a maintenance window.",
            {"switch": cfg.name, "model": cfg.model, "stpInstancesInUse": stp_count,
             "stpInstanceLimit": limit, "limitSource": source,
             "remaining": limit - stp_count, "vlanCount": vlan_count},
            "Plan for MSTP or a higher `system-max spanning-tree` before the next VLAN "
            "rollout on this switch.",
        )


@check("system-max-headroom", "Configured system-max nearing its usage", CAT_SCALE,
       needs="configs",
       trigger="actual VLAN count >= 80% of `system-max vlan`, or any system-max set "
               "above its documented hard maximum")
def system_max_headroom(ctx):
    """
    Compares what is actually configured against the system-max values in force,
    and sanity-checks those values against the documented range.
    """
    for cfg in ctx.configs.values():
        vlan_count, _ = _counts(cfg)
        limit, source = _system_max(cfg, "vlan")
        if not limit:
            continue

        hard = SYSTEM_MAX_DEFAULTS["vlan"][1]
        if limit > hard:
            yield Finding(
                "system-max-headroom",
                f"{cfg.name} — `system-max vlan {limit}` exceeds the documented maximum "
                f"of {hard}",
                "warning", CAT_SCALE, cfg.name,
                f"The configured VLAN maximum is {limit}, above the documented ceiling of "
                f"{hard}. Either the platform accepts more than the reference states, or "
                "this value is not being applied as written. Worth confirming with "
                "`show default-values` before relying on it.",
                {"switch": cfg.name, "configured": limit, "documentedMax": hard},
                "Confirm the effective value on the switch.",
                confidence="medium",
            )
            continue

        pct = 100 * vlan_count / limit
        if pct < 80:
            continue
        yield Finding(
            "system-max-headroom",
            f"{cfg.name} — {vlan_count} VLANs against a `system-max vlan` of {limit} "
            f"({pct:.0f}%)",
            "warning" if pct < 95 else "critical", CAT_SCALE, cfg.name,
            f"{vlan_count} VLANs are defined against a configured maximum of {limit} "
            f"({source}). Adding VLANs beyond the limit is refused, and raising "
            "`system-max vlan` requires a reload.",
            {"switch": cfg.name, "vlanCount": vlan_count, "systemMaxVlan": limit,
             "limitSource": source, "percent": round(pct),
             "documentedDefault": SYSTEM_MAX_DEFAULTS["vlan"][0]},
            "Raise `system-max vlan` in a maintenance window, or trim VLANs trunked to "
            "this switch.",
        )


@check("vlan-trunking-breadth", "More VLANs on a switch than it plausibly needs", CAT_SCALE,
       needs="configs",
       trigger="a switch carrying >= 3x the estate median VLAN count. Being well above "
               "peers is what drives a switch into the STP instance ceiling")
def vlan_trunking_breadth(ctx):
    """
    The root cause behind most instance-limit problems: every VLAN is trunked
    everywhere by habit. Trimming the trunk is usually easier than raising a
    platform limit, and it shrinks the broadcast domains at the same time.
    """
    counts = {c.name: _counts(c)[0] for c in ctx.configs.values()}
    if len(counts) < 5:
        return
    ordered = sorted(counts.values())
    median = ordered[len(ordered) // 2]
    if median < 1:
        return

    for cfg in ctx.configs.values():
        n = counts[cfg.name]
        if n < 3 * median or n < 12:
            continue
        limit, _ = _system_max(cfg, "spanning-tree")
        yield Finding(
            "vlan-trunking-breadth",
            f"{cfg.name} — {n} VLANs, {n / median:.0f}x the estate median of {median}",
            "info", CAT_SCALE, cfg.name,
            f"This switch carries {n} VLANs where the median across the audited estate is "
            f"{median}. Carrying far more VLANs than peers is usually the result of "
            "trunking everything everywhere rather than a real requirement, and it is what "
            "pushes a switch into the spanning-tree instance ceiling"
            + (f" (limit {limit})." if limit else ".")
            + " Trimming the trunk to the VLANs actually terminated here also shrinks every "
              "broadcast domain it currently participates in.",
            {"switch": cfg.name, "model": cfg.model, "vlanCount": n,
             "estateMedian": median, "stpInstanceLimit": limit},
            "Review which VLANs genuinely terminate on this switch and prune the uplink "
            "trunk to that set.",
            confidence="medium",
        )
