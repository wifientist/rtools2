#!/usr/bin/env python3
"""
AP assignment matching regression test.

The Cloudpath import's AP-to-AP-Group assignment silently did nothing for
every run up to 2026-09-08, and the phase reported success while doing it.

THE BUG

There are two AP dictionaries in this codebase. R1 raw records --
/venues/aps/query, which is what `all_venue_aps` holds -- spell the serial
"serialNumber". Our own routers reshape APs for the frontend and spell it
"serial". Cloudpath's validate read the frontend spelling off the raw list:

    serial_to_ap = {ap.get('serial', ''): ap for ap in all_venue_aps}
    ...
    serial = matched_ap.get('serial', '')
    if serial and serial not in unit_to_aps[unit_num]:
        unit_to_aps[unit_num].append(serial)

Neither line raises. The index collapsed EVERY AP onto the key "", and the
extracted serial was "", so `if serial` dropped each assignment on the floor.
`unit_to_aps` came out empty, assign_aps received no identifiers, took its
"No APs specified" branch, and returned success.

Nothing in a run said otherwise: the plan showed AP Groups being created,
the phase went green, and only R1 itself showed the groups were empty.

WHAT THIS GUARDS

  1. Serials index under their real value, not "".
  2. A CSV row matched by NAME still yields a usable serial (the default
     pattern maps unit -> AP *name*, so this is the common path).
  3. A CSV row matched by SERIAL yields the same.
  4. An AP with no serial is skipped, never indexed under "" where a blank
     identifier would match it.
  5. assign_aps matches what validate produced -- the two halves agree.

Usage:
    docker compose exec backend python scripts/test_ap_assignment_matching.py

Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.ap_fields import (
    ap_serial,
    index_aps_by_serial,
    index_aps_by_name,
)
from workflow.phases.assign_aps import AssignAPsPhase


# Shaped exactly like /venues/aps/query returns (see
# venues.get_aps_by_tenant_venue: it requests "serialNumber" and dedups on it).
VENUE_APS = [
    {"serialNumber": "302140000001", "name": "101@Maple", "apGroupId": "grp-old"},
    {"serialNumber": "302140000002", "name": "102@Maple", "apGroupId": None},
    {"serialNumber": "302140000003", "name": "103@Maple", "apGroupId": None},
    # R1 has been seen returning records without one; it must not become "".
    {"name": "spare-ap-no-serial", "apGroupId": None},
]

# The tool pre-populates "unit_number,unit_number@propertyName", so the
# identifier is normally the AP NAME. Serials are also allowed.
CSV_ROWS = [
    {"unit_number": "101", "ap_identifier": "101@Maple"},
    {"unit_number": "102", "ap_identifier": "102@Maple"},
    {"unit_number": "103", "ap_identifier": "302140000003"},
    {"unit_number": "104", "ap_identifier": "104@Maple"},  # AP not in venue
]

EXPECTED = {
    "101": ["302140000001"],
    "102": ["302140000002"],
    "103": ["302140000003"],
}


def resolve_assignments(venue_aps, rows):
    """The serial resolution cloudpath/validate.py performs, in miniature."""
    by_serial = index_aps_by_serial(venue_aps)
    by_name = index_aps_by_name(venue_aps)

    unit_to_aps, unmatched = {}, []
    for row in rows:
        ap = by_serial.get(row["ap_identifier"]) or by_name.get(row["ap_identifier"])
        if not ap:
            unmatched.append(row["ap_identifier"])
            continue
        serial = ap_serial(ap)
        if not serial:
            unmatched.append(f"{row['ap_identifier']} (no serial)")
            continue
        unit_to_aps.setdefault(row["unit_number"], []).append(serial)
    return unit_to_aps, unmatched


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


def main() -> int:
    failures = 0
    print("AP assignment matching\n")

    # 1. serials index under their real value
    idx = index_aps_by_serial(VENUE_APS)
    failures += check(
        "serials index under their own value, not ''",
        "" not in idx and len(idx) == 3,
        f"keys={sorted(idx)}",
    )

    # 2/3. name- and serial-matched rows both resolve to a usable serial
    unit_to_aps, unmatched = resolve_assignments(VENUE_APS, CSV_ROWS)
    failures += check(
        "CSV rows resolve to serials", unit_to_aps == EXPECTED, f"got {unit_to_aps}"
    )
    failures += check(
        "an AP absent from the venue is reported, not dropped silently",
        unmatched == ["104@Maple"],
        f"got {unmatched}",
    )

    # 4. the serial-less AP is skipped everywhere
    failures += check(
        "serial-less AP is never indexed",
        "spare-ap-no-serial" in index_aps_by_name(VENUE_APS)
        and ap_serial(VENUE_APS[3]) == "",
    )

    # 5. the two halves agree: what validate produced, assign_aps matches
    phase = AssignAPsPhase.__new__(AssignAPsPhase)  # pure function, no context
    for unit, serials in unit_to_aps.items():
        matched = phase._find_matching_aps(serials, VENUE_APS)
        failures += check(
            f"assign_aps matches unit {unit}'s serials",
            [ap_serial(ap) for ap in matched] == serials,
            f"got {[ap_serial(ap) for ap in matched]}",
        )

    # and a blank identifier must never match anything
    failures += check(
        "blank identifier matches no AP",
        phase._find_matching_aps(["", " "], VENUE_APS) == [],
    )

    # 6. The checks above exercise ap_fields, so they cannot see a phase that
    # goes back to reading the key directly -- which is the mistake that
    # actually shipped, twice. Read the source and forbid it.
    offenders = []
    phases_dir = Path(__file__).parent.parent / "workflow" / "phases"
    for path in sorted(phases_dir.rglob("*.py")):
        if path.name == "ap_fields.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "get('serial'" in code or 'get("serial"' in code:
                offenders.append(f"{path.relative_to(phases_dir.parent.parent)}:{n}")
    failures += check(
        "no phase reads the 'serial' key directly (use ap_fields.ap_serial)",
        not offenders,
        ", ".join(offenders),
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
