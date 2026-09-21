#!/usr/bin/env python3
"""
MSP tenant-delegation regression test.

WHAT WENT WRONG

R1 keys come in two shapes: a direct EC key that belongs to one tenant, and
an MSP key that belongs to the MSP and delegates to a chosen customer per
request via an override-tenant header. Both are stored with an
`r1_tenant_id` on the controller row -- and on an MSP that column holds the
MSP'S OWN tenant id.

That id is a real, queryable scope. It answers, it authenticates, and it
contains the MSP's own handful of venues. So:

    tenant_id = request.tenant_id or controller.r1_tenant_id

on an MSP with no EC chosen silently answered against the MSP instead of the
customer. The venue list came from the EC, the work ran against the MSP, and
the result was not an error -- it was an empty venue. Reported as "I'm not
seeing APs in a production site. Or SSIDs. Or DPSK services."

The guard meant to catch it,

    if controller.controller_subtype == "MSP" and not tenant_id:
        raise HTTPException(400, ...)

sat AFTER the fallback, so it could never fire. Reading the code, it looked
handled. Verified live 2026-09-20: the MSP's own tenant returned 3 venues, a
delegated EC returned 8, and neither raised.

WHAT THIS GUARDS

  1. On MSP with no EC, resolving a tenant RAISES rather than falling back.
  2. On MSP with an EC, that EC is used verbatim.
  3. On a direct EC controller, r1_tenant_id is still the default, and an
     explicit tenant still wins.
  4. The cloudpath routers contain no `or controller.r1_tenant_id` fallback.
  5. The rest of the suite is inventoried, so the count can only go down --
     this shape exists in other routers and every one is a tool that
     silently reports on the wrong company.

Usage:
    docker compose exec backend python scripts/test_msp_tenant_scope.py

Exits non-zero on failure.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import HTTPException

from routers.tenant_scope import resolve_tenant_id

API_ROOT = Path(__file__).parent.parent
FALLBACK = re.compile(r"tenant_id\s+or\s+\w*controller\.r1_tenant_id")

# Routers still carrying the silent fallback. Fixing one means deleting it
# from here; the test fails if the list grows, so this can only shrink.
KNOWN_UNFIXED = {
    "routers/bulk_ap_tagging/bulk_ap_tagging_router.py",
    "routers/cleanup_v2_router.py",
    "routers/migrate.py",
    "routers/ap_port_config/ap_port_config_router.py",
    "routers/ap_port_config/v2_endpoints.py",
    "routers/per_unit_ssid/v2_endpoints.py",
    "routers/per_unit_ssid/per_unit_ssid_router.py",
    "routers/bulk_wlan/bulk_wlan_router.py",
    "routers/ap_regroup/v2_endpoints.py",
    "routers/ap_rename/ap_rename_router.py",
    "routers/maps/maps_router.py",
    "routers/sz_migration/router.py",
}

# These must stay clean: they are the tool this was found in.
MUST_BE_CLEAN = {
    "routers/cloudpath/cloudpath_router.py",
    "routers/cloudpath/v2_endpoints.py",
}


class FakeController:
    def __init__(self, subtype, tenant):
        self.controller_subtype = subtype
        self.r1_tenant_id = tenant


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


def behaviour() -> int:
    failures = 0
    msp = FakeController("MSP", "msp-own-tenant")
    ec = FakeController("EC", "ec-tenant")

    try:
        got = resolve_tenant_id(msp, None)
        failures += check(
            "MSP with no EC raises instead of using the MSP's own tenant",
            False, f"returned {got!r}",
        )
    except HTTPException as e:
        failures += check(
            "MSP with no EC raises instead of using the MSP's own tenant",
            e.status_code == 400 and "MSP-EC" in str(e.detail),
            str(e.detail)[:80],
        )

    failures += check(
        "MSP with an EC delegates to that EC",
        resolve_tenant_id(msp, "customer-ec") == "customer-ec",
    )
    failures += check(
        "a direct EC controller defaults to its own tenant",
        resolve_tenant_id(ec, None) == "ec-tenant",
    )
    failures += check(
        "an explicit tenant still wins on a direct EC controller",
        resolve_tenant_id(ec, "other") == "other",
    )
    return failures


def sweep() -> int:
    offenders = set()
    for path in sorted(API_ROOT.rglob("routers/**/*.py")):
        if "__pycache__" in str(path):
            continue
        # tenant_scope.py quotes the bad pattern in its own docstring, which
        # is the point of it existing.
        if path.name == "tenant_scope.py":
            continue
        if FALLBACK.search(path.read_text()):
            offenders.add(str(path.relative_to(API_ROOT)))

    failures = check(
        "the cloudpath routers carry no silent MSP fallback",
        not (offenders & MUST_BE_CLEAN),
        ", ".join(sorted(offenders & MUST_BE_CLEAN)),
    )

    regressed = offenders - KNOWN_UNFIXED - MUST_BE_CLEAN
    failures += check(
        "no NEW router has picked up the silent MSP fallback",
        not regressed,
        ", ".join(sorted(regressed)),
    )

    fixed = KNOWN_UNFIXED - offenders
    if fixed:
        failures += check(
            "KNOWN_UNFIXED is stale -- remove what has been fixed",
            False, ", ".join(sorted(fixed)),
        )

    remaining = sorted(offenders & KNOWN_UNFIXED)
    if remaining:
        print(f"\n  {len(remaining)} router(s) still resolve an MSP tenant by")
        print("  falling back to the MSP's own id. Each is a tool that will")
        print("  silently report on the wrong company:")
        for r in remaining:
            print(f"    - {r}")
    return failures


def main() -> int:
    print("MSP tenant delegation\n")
    failures = behaviour()
    failures += sweep()
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
