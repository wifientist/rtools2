#!/usr/bin/env python3
"""
Passphrase exposure regression test.

WHAT WAS LEAKING

Three places sent DPSK passphrase values to the browser:

  * /jobs/{id}/status returned each global phase's result verbatim, and
    validate_and_plan's result is the parsed roster -- every resident, with
    their passphrase. Measured on a real job: 153 plaintext passphrases,
    re-sent on every poll of a screen that polls continuously for the whole
    duration of an import.

  * /export-identities and its CSV carried a `passphrase` column.

  * The orchestrator sent passphrase_preview = value[:4] + "****" -- four
    characters of a twelve-character secret.

Nothing in the UI read any of them. That is not a defence. A value the page
ignores is still in the response body, in browser memory, in devtools, in
any proxy along the way, and in a support screenshot.

The rule: the backend may read passphrases freely -- to join identities to
pools, to confirm one exists, to match an orphaned identity back to a
resident -- but what crosses to the frontend is "checked and confirmed",
never the value.

WHAT THIS GUARDS

  1. redact_passphrases replaces the value with a presence boolean, at any
     nesting depth, without mutating the caller's data.
  2. Keys that are NOT secrets survive: passphrase_id, passphrase_format,
     passphrase_count.
  3. The job status response carries no passphrase value.
  4. The identity export row has no passphrase field.
  5. No router names a bare `passphrase` key in a response payload.

Usage:
    docker compose exec backend python scripts/test_passphrase_never_leaves.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.dpsk_redact import redact_passphrases

API_ROOT = Path(__file__).parent.parent


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


def main() -> int:
    failures = 0
    print("Passphrase exposure\n")

    # 1 + 2. the redactor
    src = {
        "passphrases": [
            {"name": "4021", "passphrase": "correct-horse", "passphrase_id": "p-1"},
            {"name": "4022", "passphrase": "", "passphrase_id": "p-2"},
        ],
        "pool": {"passphrase_format": "WORDS", "passphrase_count": 12,
                 "nested": [{"deep": {"passphrase": "hunter2"}}]},
    }
    out = redact_passphrases(src)
    blob = json.dumps(out)

    failures += check(
        "the value is replaced by a presence boolean at any depth",
        "correct-horse" not in blob and "hunter2" not in blob
        and out["passphrases"][0]["passphrase_set"] is True
        and out["passphrases"][1]["passphrase_set"] is False
        and out["pool"]["nested"][0]["deep"]["passphrase_set"] is True,
        blob[:120],
    )
    failures += check(
        "non-secret passphrase_* keys survive untouched",
        out["pool"]["passphrase_format"] == "WORDS"
        and out["pool"]["passphrase_count"] == 12
        and out["passphrases"][0]["passphrase_id"] == "p-1",
    )
    failures += check(
        "the caller's data is not mutated",
        src["passphrases"][0]["passphrase"] == "correct-horse",
    )

    # 3. the status endpoint redacts
    router = (API_ROOT / "routers" / "workflows_router.py").read_text()
    failures += check(
        "the job status endpoint redacts phase results",
        'phase_data["result"] = redact_passphrases(result)' in router,
        "phase results are returned verbatim",
    )

    # 4. the export row has no passphrase field
    from routers.cloudpath.cloudpath_router import IdentityExportRow
    fields = set(IdentityExportRow.model_fields)
    failures += check(
        "the identity export row carries no passphrase value",
        "passphrase" not in fields and "has_passphrase" in fields,
        ", ".join(sorted(fields)),
    )

    # 5. no router builds a response dict with a bare passphrase key
    offenders = []
    pattern = re.compile(r"""['"]passphrase['"]\s*:""")
    for path in sorted((API_ROOT / "routers").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "payload" in line or "body" in line:
                continue  # outbound to R1 is fine; inbound to the browser is not
            if pattern.search(line):
                offenders.append(f"{path.relative_to(API_ROOT)}:{n}")
    failures += check(
        "no router puts a bare passphrase key in a response payload",
        not offenders, ", ".join(offenders[:6]),
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
