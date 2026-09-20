#!/usr/bin/env python3
"""
Shadowed-local-import regression test.

WHAT HAPPENED

create_passphrase() in r1api/services/dpsk.py had, deep in its 202-polling
branch, a redundant:

    if passphrase:
        import asyncio

asyncio was already imported at module scope, so this looked like tidy-up
noise. It is not. A binding anywhere in a function makes that name local to
the WHOLE function, so the two calls that actually post the passphrase --

    response = await asyncio.to_thread(self.client.post, ...)

-- sitting ~45 lines EARLIER, stopped resolving and raised:

    UnboundLocalError: cannot access local variable 'asyncio' where it is
    not associated with a value

The local import was added 2026-08-18, when nothing in the function touched
asyncio before it and it was genuinely harmless. It detonated on 2026-09-16
when the to_thread offload was added above it. From then on EVERY call to
create_passphrase raised before issuing the POST -- so no passphrase was
created, and a 215-resident import failed all 215.

The failure was near-invisible because it was caught as a per-passphrase
error and reported as a count, and because the next phase then found nothing
to work with and said so in terms of its own skip counters:

    No policies to create - skipped 0 (no SSID mapping), 0 (no unit SSIDs)

Three zeros, all accurate, describing an import that created nothing.

WHAT THIS GUARDS

  1. No function in the API uses a name before a local import of that name
     binds it -- the exact UnboundLocalError shape, swept across the tree.
     Annotations are excluded: they evaluate in the enclosing scope, and
     under `from __future__ import annotations` are never evaluated at all.
  2. create_passphrase specifically issues its POST rather than raising,
     since that is the call the regression silently disabled.
  3. create_access_policies reports upstream passphrase failures instead of
     printing three zeros about its own skip counters.

Usage:
    docker compose exec backend python scripts/test_import_shadowing.py

Exits non-zero on failure.
"""

import ast
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

API_ROOT = Path(__file__).parent.parent
SKIP_PARTS = ("__pycache__", "node_modules", ".venv", "site-packages")


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


# ==================== 1. the structural sweep ====================


def _annotation_nodes(fn):
    """Every node living in an annotation, which is NOT function-local scope."""
    nodes = []
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        if arg.annotation:
            nodes.append(arg.annotation)
    if fn.returns:
        nodes.append(fn.returns)
    return nodes


def shadowed_import_uses(path: Path):
    """(lineno, name, import_lineno, func) for each use-before-local-import."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []

    findings = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Names bound by an import statement anywhere inside this function.
        local_imports = {}
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    local_imports.setdefault(name, node.lineno)
        if not local_imports:
            continue

        skip = set()
        for ann in _annotation_nodes(fn):
            for node in ast.walk(ann):
                skip.add(id(node))

        for node in ast.walk(fn):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            if id(node) in skip:
                continue
            bound_at = local_imports.get(node.id)
            if bound_at is not None and node.lineno < bound_at:
                findings.append((node.lineno, node.id, bound_at, fn.name))
    return findings


def sweep() -> int:
    offenders = []
    for path in sorted(API_ROOT.rglob("*.py")):
        if any(part in str(path) for part in SKIP_PARTS):
            continue
        for lineno, name, bound_at, func in shadowed_import_uses(path):
            offenders.append(
                f"{path.relative_to(API_ROOT)}:{lineno} uses '{name}' before "
                f"its local import on line {bound_at} (in {func}())"
            )
    return check(
        "no function uses a name before a local import binds it",
        not offenders,
        "; ".join(offenders),
    )


# ==================== 2. create_passphrase actually posts ====================


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "pp-1", "identityId": "id-1"}

    @property
    def text(self):
        return "{}"


class FakeR1Client:
    ec_type = "MSP"

    def __init__(self):
        self.posted = []

    def post(self, path, payload=None, override_tenant_id=None, **kw):
        self.posted.append(path)
        return FakeResponse()

    def safe_json(self, response):
        return response.json()


async def create_passphrase_check() -> int:
    from r1api.services.dpsk import DpskService

    client = FakeR1Client()
    service = DpskService.__new__(DpskService)
    service.client = client

    try:
        result = await service.create_passphrase(
            pool_id="pool-1",
            user_name="4021_ultrafast",
            passphrase="correct-horse",
            tenant_id="t-1",
        )
    except UnboundLocalError as e:
        return check("create_passphrase issues its POST", False, f"UnboundLocalError: {e}")
    except Exception as e:
        return check("create_passphrase issues its POST", False, f"{type(e).__name__}: {e}")

    return check(
        "create_passphrase issues its POST",
        client.posted == ["/dpskServices/pool-1/passphrases"]
        and result.get("id") == "pp-1",
        f"posted={client.posted}, result={result}",
    )


# ==================== 3. upstream failures are reported ====================


async def upstream_failure_check() -> int:
    from workflow.phases.create_access_policies import CreateAccessPoliciesPhase

    messages = []

    phase = CreateAccessPoliciesPhase.__new__(CreateAccessPoliciesPhase)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"

    async def emit(msg, level="info", details=None):
        messages.append((level, msg))

    phase.emit = emit

    # Exactly the shape the broken run produced: every passphrase failed
    # upstream, so nothing reaches the SSID or unit-SSID checks.
    failed = [
        {
            "username": f"815540050223409{i}_superfast",
            "success": False,
            "error": "cannot access local variable 'asyncio' where it is not "
                     "associated with a value",
        }
        for i in range(215)
    ]
    inputs = CreateAccessPoliciesPhase.Inputs(
        created_passphrases=failed,
        passphrases=[
            {"name": f["username"], "ssid_list": ["403@The_durant"]} for f in failed
        ],
        identity_group_id="ig-1",
        options={"enable_access_policies": True, "policy_set_name": "The durant"},
    )

    out = await phase.execute(inputs)

    text = " ".join(m for _, m in messages)
    errors = [m for lvl, m in messages if lvl == "error"]

    failures = check(
        "an all-failed-upstream run names create_passphrases, not its own counters",
        out.failed_upstream == 215
        and bool(errors)
        and "failed upstream" in text
        and "create_passphrases" in text,
        f"failed_upstream={out.failed_upstream}, messages={[m for _, m in messages][-2:]}",
    )
    failures += check(
        "the misleading three-zeros line is not what gets printed",
        "skipped 0 (no SSID mapping), 0 (no unit SSIDs)" not in text,
        text[-160:],
    )
    failures += check(
        "the underlying error text is surfaced",
        "asyncio" in text,
        text[-160:],
    )
    return failures


async def main() -> int:
    print("Shadowed local imports\n")
    failures = sweep()
    failures += await create_passphrase_check()
    failures += await upstream_failure_check()
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
