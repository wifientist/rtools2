"""
R1 service code must not block the event loop, and an awaited to_thread must be
parenthesised before its result is used:

    await asyncio.to_thread(f).json()     # .json() runs on the coroutine
    (await asyncio.to_thread(f)).json()   # right

    docker compose exec -T -w /app backend python scripts/test_r1_services_off_event_loop.py
"""

import ast
import glob
import sys

VERBS = {"get", "post", "put", "patch", "delete", "post_multipart"}


def is_to_thread(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to_thread"
    )


def chain_root(node):
    while not is_to_thread(node):
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None
    return node


def own_nodes(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.Lambda)):
            continue
        yield child
        yield from own_nodes(child)


def problems_in(path):
    tree = ast.parse(open(path).read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and not is_to_thread(node.value) and chain_root(node.value):
            found.append(f"{path}:{node.lineno} result used before the await")
        if isinstance(node, ast.AsyncFunctionDef):
            for inner in own_nodes(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in VERBS
                    and isinstance(inner.func.value, ast.Attribute)
                    and inner.func.value.attr == "client"
                ):
                    found.append(f"{path}:{inner.lineno} blocking R1 call in async code")
    return found


def main():
    problems = []
    for path in sorted(glob.glob("r1api/services/*.py")):
        problems += problems_in(path)
    for problem in problems:
        print(f"  FAIL  {problem}")
    print("All checks passed" if not problems else f"FAILED ({len(problems)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
