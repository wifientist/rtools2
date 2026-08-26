"""
Probe: why does DELETE /radiusAttributeGroups/{id} return 409 when the UI
shows the group has zero associated policies?

The 409 body says "The Attribute Group is still in use by another service."
The spec calls the link an *external assignment*
(GET /radiusAttributeGroups/{groupId}/assignments), which is a different table
from the adaptive policies the UI counts — so a group can read as unused there
and still be pinned here.

Read-only. Hits, per group:
  GET /radiusAttributeGroups                       -- list, to resolve names
  GET /radiusAttributeGroups/{id}                  -- the group itself
  GET /radiusAttributeGroups/{id}/assignments      -- the suspected blocker

Usage:
    docker compose exec backend python scripts/probe_radius_group_refs.py <controller_id> [group_id ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller


def show(label, resp):
    print(f"    HTTP {resp.status_code}  {label}")
    if not resp.text:
        print("    (empty body)")
        return None
    try:
        body = resp.json()
    except ValueError:
        print(f"    non-JSON: {resp.text[:300]}")
        return None
    print("    " + json.dumps(body, indent=2, default=str)[:2500])
    return body


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    controller_id = int(sys.argv[1])
    wanted = sys.argv[2:]

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    print(f"=== GET /radiusAttributeGroups (tenant {r1.tenant_id}) ===")
    resp = r1.get("/radiusAttributeGroups")
    groups = show("list", resp) or []
    if isinstance(groups, dict):
        groups = groups.get("content", groups.get("data", []))

    targets = [g for g in groups if not wanted or g.get("id") in wanted]
    if wanted:
        known = {g.get("id") for g in groups}
        for gid in wanted:
            if gid not in known:
                targets.append({"id": gid, "name": "(not in list)"})

    for g in targets:
        gid, name = g.get("id"), g.get("name", "?")
        print(f"\n=== group {name} ({gid}) ===")
        show("group", r1.get(f"/radiusAttributeGroups/{gid}"))
        print(f"--- assignments ---")
        show("assignments", r1.get(f"/radiusAttributeGroups/{gid}/assignments"))


if __name__ == "__main__":
    main()
