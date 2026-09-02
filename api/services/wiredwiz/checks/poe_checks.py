"""
PoE budget and allocation checks.

The distinction that matters on ICX is **how power is reserved**, not how much is
drawn:

  * `inline power allocation dynamic [all]` (global) reserves against what the
    powered device actually consumes.
  * `inline power power-limit <mW>` (interface) reserves a fixed amount from the
    chassis budget whether the device draws it or not.

Both facts are verifiable here rather than assumed. On the estate this was built
against, the API's per-port `poeTotal` matched the configured `power-limit` on
410 of 411 ports — so the configured limit IS the reservation. And across 4,234
powered ports, 83.8 kW was allocated while only 29.7 kW was actually drawn:
**65% of allocated power reserved and unused.**

The switches sitting closest to their budget ceiling were precisely the ones
without dynamic allocation configured.
"""

import re
from collections import defaultdict

from .framework import Finding, check, _as_int, _is_up, _norm_mac

CAT_POE = "poe"

DYNAMIC_RE = re.compile(r"inline\s+power\s+allocation\s+dynamic", re.I)
LIMIT_RE = re.compile(r"^\s*inline\s+power\s+power-limit\s+(\d+)", re.M | re.I)
PRIORITY_RE = re.compile(r"inline\s+power\s+priority", re.I)


def _switch_poe(ctx):
    """(switch record, capacity mW, free mW, allocated mW, allocated %) per switch."""
    for s in ctx.switches:
        total, free = _as_int(s.get("poeTotal")), _as_int(s.get("poeFree"))
        if not total:
            continue
        allocated = total - free
        yield s, total, free, allocated, 100 * allocated / total


def _port_draw(ctx, switch_mac):
    """(allocated, drawn) in mW across ports on one switch that are powering something."""
    alloc = drawn = 0
    for p in ctx.ports_by_switch.get(switch_mac, []):
        used = _as_int(p.get("poeUsed"))
        if used <= 0:
            continue
        alloc += _as_int(p.get("poeTotal"))
        drawn += used
    return alloc, drawn


@check("poe-budget-exhausted", "PoE budget allocated to its ceiling", CAT_POE,
       needs="snapshot",
       trigger="chassis PoE >= 85% allocated; critical at >= 95%")
def poe_budget_exhausted(ctx):
    """
    A switch with no free budget cannot power another device, and under load it
    can drop already-powered ports to stay inside its envelope. That presents as
    APs rebooting at random, not as a power alarm.
    """
    for s, total, free, allocated, pct in _switch_poe(ctx):
        if pct < 85:
            continue
        mac = _norm_mac(s.get("switchMac") or s.get("id"))
        port_alloc, drawn = _port_draw(ctx, mac)
        waste = port_alloc - drawn
        yield Finding(
            "poe-budget-exhausted",
            f"{s.get('name')} — PoE {pct:.0f}% allocated, {free / 1000:.0f}W free of "
            f"{total / 1000:.0f}W",
            "critical" if pct >= 95 else "warning", CAT_POE, s.get("name"),
            f"{allocated / 1000:.0f}W of {total / 1000:.0f}W is reserved, leaving "
            f"{free / 1000:.0f}W. Nothing else can be powered here"
            + (f", and of what is reserved only {drawn / 1000:.0f}W is actually being "
               f"drawn — {waste / 1000:.0f}W is reserved and idle."
               if port_alloc and waste > 0 else "."),
            {"capacityWatts": round(total / 1000), "freeWatts": round(free / 1000),
             "allocatedWatts": round(allocated / 1000), "allocatedPercent": round(pct),
             "portAllocatedWatts": round(port_alloc / 1000),
             "portDrawnWatts": round(drawn / 1000),
             "reservedButIdleWatts": round(waste / 1000) if waste > 0 else 0,
             "model": s.get("model"), "venue": s.get("venueName")},
            "If the reserved-but-idle figure is large, this is a reservation problem rather "
            "than a real power shortage — see the static-allocation finding for this "
            "switch. Otherwise redistribute devices or move to a higher-budget PSU.",
        )


@check("poe-static-allocation", "PoE reserved statically instead of dynamically", CAT_POE,
       needs="configs",
       trigger="no `inline power allocation dynamic`, with the measured reserved-but-idle wattage")
def poe_static_allocation(ctx):
    """
    Without `inline power allocation dynamic`, the switch reserves per port by
    class or by an explicit `power-limit` — regardless of what the device draws.
    A 15.4W reservation for an AP pulling 5.6W wastes ~10W of budget per port,
    which is how a switch runs out of PoE with plenty of real headroom left.
    """
    for cfg in ctx.configs.values():
        if DYNAMIC_RE.search(cfg.text):
            continue
        sw = next((s for s in ctx.switches if s.get("name") == cfg.name), None)
        if not sw:
            continue
        total, free = _as_int(sw.get("poeTotal")), _as_int(sw.get("poeFree"))
        if not total:
            continue
        mac = _norm_mac(sw.get("switchMac") or sw.get("id"))
        port_alloc, drawn = _port_draw(ctx, mac)
        waste = port_alloc - drawn
        pct = 100 * (total - free) / total
        limits = LIMIT_RE.findall(cfg.text)

        severe = pct >= 85 or (waste > 0 and total and waste / total > 0.3)
        yield Finding(
            "poe-static-allocation",
            f"{cfg.name} — no dynamic PoE allocation, {pct:.0f}% of budget reserved",
            "warning" if severe else "info", CAT_POE, cfg.name,
            f"`inline power allocation dynamic` is not configured, so power is reserved per "
            f"port by class or by an explicit limit rather than by what the device actually "
            f"consumes. "
            + (f"{len(limits)} port(s) carry an explicit `power-limit`. " if limits else "")
            + (f"Right now {port_alloc / 1000:.0f}W is reserved across powered ports while "
               f"only {drawn / 1000:.0f}W is being drawn — {waste / 1000:.0f}W reserved and "
               f"idle, on a {total / 1000:.0f}W switch."
               if port_alloc else "")
            + " That gap is budget you cannot use for anything else.",
            {"switch": cfg.name, "model": cfg.model,
             "capacityWatts": round(total / 1000), "freeWatts": round(free / 1000),
             "allocatedPercent": round(pct),
             "portAllocatedWatts": round(port_alloc / 1000),
             "portDrawnWatts": round(drawn / 1000),
             "reservedButIdleWatts": round(waste / 1000) if waste > 0 else 0,
             "explicitPowerLimits": len(limits),
             "distinctLimits": sorted({int(x) for x in limits})},
            "Enable `inline power allocation dynamic all` so reservation tracks real draw. "
            "Review whether the explicit `power-limit` lines are still needed — each one "
            "opts that port back out of dynamic allocation.",
        )


@check("poe-limit-overrides-dynamic", "Explicit power-limit on a switch using dynamic "
       "allocation", CAT_POE, needs="configs",
       trigger="dynamic allocation enabled while interfaces still carry `inline power power-limit`")
def poe_limit_overrides_dynamic(ctx):
    """
    Both configured together is usually unintentional: somebody enabled dynamic
    allocation fleet-wide, but per-port limits set earlier are still in place and
    keep those ports on a fixed reservation.
    """
    for cfg in ctx.configs.values():
        if not DYNAMIC_RE.search(cfg.text):
            continue
        limits = LIMIT_RE.findall(cfg.text)
        if not limits:
            continue
        sw = next((s for s in ctx.switches if s.get("name") == cfg.name), None)
        mac = _norm_mac((sw or {}).get("switchMac") or (sw or {}).get("id") or "")
        port_alloc, drawn = _port_draw(ctx, mac) if sw else (0, 0)
        waste = port_alloc - drawn
        yield Finding(
            "poe-limit-overrides-dynamic",
            f"{cfg.name} — dynamic allocation enabled, but {len(limits)} port(s) still "
            "carry a fixed power-limit",
            "info", CAT_POE, cfg.name,
            f"This switch has `inline power allocation dynamic`, yet {len(limits)} "
            f"interface(s) still specify `inline power power-limit` "
            f"({sorted({int(x) for x in limits})} mW). Those ports keep a fixed reservation "
            "instead of tracking actual draw, so the benefit of dynamic allocation does not "
            "apply to them. Usually this is leftover configuration rather than intent."
            + (f" Across powered ports here, {port_alloc / 1000:.0f}W is reserved against "
               f"{drawn / 1000:.0f}W drawn." if port_alloc else ""),
            {"switch": cfg.name, "portsWithExplicitLimit": len(limits),
             "distinctLimits": sorted({int(x) for x in limits}),
             "portAllocatedWatts": round(port_alloc / 1000),
             "portDrawnWatts": round(drawn / 1000),
             "reservedButIdleWatts": round(waste / 1000) if waste > 0 else 0},
            "Remove the per-port `power-limit` lines unless a specific device needs a "
            "guaranteed reservation, and let dynamic allocation manage the budget.",
            confidence="medium",
        )


@check("poe-no-priority", "No PoE priority configured", CAT_POE, needs="configs",
       trigger="no `inline power priority` anywhere; warning when a switch is already above 70% budget")
def poe_no_priority(ctx):
    """
    When the budget is exhausted the switch decides which ports to deny or drop.
    Without `inline power priority` that decision is made for you, and the device
    that loses power is arbitrary rather than the least important one.
    """
    missing = [c.name for c in ctx.configs.values() if not PRIORITY_RE.search(c.text)]
    if not missing:
        return
    # Only worth raising where budget pressure actually exists.
    tight = [s.get("name") for s, total, free, alloc, pct in _switch_poe(ctx) if pct >= 70]
    at_risk = sorted(set(missing) & set(tight))
    yield Finding(
        "poe-no-priority",
        f"PoE priority not set on {len(missing)} switch(es)"
        + (f", {len(at_risk)} of which are above 70% budget" if at_risk else ""),
        "warning" if at_risk else "info", CAT_POE, "fabric",
        "No switch sets `inline power priority` (1 = highest, 3 = lowest). When a switch "
        "runs out of budget it denies or removes power from ports on its own terms, so the "
        "device that goes dark is arbitrary — quite possibly the AP covering a lecture "
        "theatre rather than a desk phone."
        + (f" {len(at_risk)} of these switches are already above 70% allocation, so this is "
           "not hypothetical." if at_risk else ""),
        {"switchesWithoutPriority": len(missing), "switchesAudited": len(ctx.configs),
         "switchesMissingPriority": sorted(missing),
         "aboveSeventyPercent": at_risk},
        "Set `inline power priority 1` on uplink-critical APs and 3 on discretionary "
        "devices, starting with the switches nearest their ceiling.",
        confidence="medium" if not at_risk else "high",
    )


@check("poe-allocation-inconsistent", "PoE allocation mode differs across the fleet",
       CAT_POE, needs="configs",
       trigger="some switches use dynamic PoE allocation and others do not")
def poe_allocation_inconsistent(ctx):
    """Two power models in one estate makes capacity planning unpredictable."""
    dynamic = [c.name for c in ctx.configs.values() if DYNAMIC_RE.search(c.text)]
    static = [c.name for c in ctx.configs.values() if not DYNAMIC_RE.search(c.text)]
    if not (dynamic and static):
        return
    yield Finding(
        "poe-allocation-inconsistent",
        f"PoE allocation mode is inconsistent: {len(dynamic)} dynamic, {len(static)} static",
        "info", CAT_POE, "fabric",
        f"{len(dynamic)} switch(es) use dynamic PoE allocation and {len(static)} do not. "
        "The same model of switch with the same APs will therefore report very different "
        "available budget depending on which mode it is in, which makes capacity planning "
        "unreliable and hides real shortages behind reservation artefacts.",
        {"dynamicCount": len(dynamic), "staticCount": len(static),
         "staticSwitches": sorted(static)},
        "Standardise on dynamic allocation unless a site has a specific reason not to.",
    )
