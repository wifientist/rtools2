#!/usr/bin/env python3
"""
R1 client factory call-signature regression test.

WHAT WENT WRONG

create_r1_client_from_controller takes (controller_id, db). WiredWiz called
it with (controller_id, user, db) at five sites:

    TypeError: create_r1_client_from_controller() takes 2 positional
               arguments but 3 were given

so every WiredWiz endpoint that builds an R1 client raised the moment it was
reached. The venue picker showed no venues with switches on a tenant holding
799 of them across 34 venues.

The extra argument was copied from the line directly above it --

    c = _controller(controller_id, user, db)          # 3 args, correct
    r1 = create_r1_client_from_controller(controller_id, user, db)   # wrong

-- where validate_controller_access really does take a user. The ownership
check was already done; the client call just inherited its shape.

Introduced 2026-09-08 by "Scope WiredWiz to the controller's owner". It is a
runtime TypeError in an async endpoint, so nothing at import time catches it
and no type checker runs on this code.

WHAT THIS GUARDS

  1. The factory still takes exactly (controller_id, db).
  2. No caller anywhere passes a third positional argument.
  3. validate_controller_access still takes (controller_id, user, db) --
     they differ by one argument, which is how they got confused.

Usage:
    docker compose exec backend python scripts/test_client_factory_arity.py
"""

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.r1_client import (
    create_r1_client_from_controller,
    validate_controller_access,
)

API_ROOT = Path(__file__).parent.parent

# create_r1_client_from_controller(a, b, c) -- three top-level arguments.
THREE_ARGS = re.compile(
    r"create_r1_client_from_controller\(\s*[^),]+,[^),]+,[^),]+\)"
)


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


def main() -> int:
    failures = 0
    print("R1 client factory arity\n")

    params = list(inspect.signature(create_r1_client_from_controller).parameters)
    failures += check(
        "the factory takes exactly (controller_id, db)",
        params == ["controller_id", "db"], str(params),
    )
    failures += check(
        "validate_controller_access still takes a user",
        list(inspect.signature(validate_controller_access).parameters)
        == ["controller_id", "user", "db"],
        str(list(inspect.signature(validate_controller_access).parameters)),
    )

    offenders = []
    for path in sorted(API_ROOT.rglob("*.py")):
        if "__pycache__" in str(path) or path.name == Path(__file__).name:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if THREE_ARGS.search(line):
                offenders.append(f"{path.relative_to(API_ROOT)}:{n}")
    failures += check(
        "no caller passes a third positional argument",
        not offenders, ", ".join(offenders[:6]),
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
