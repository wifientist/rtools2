"""
Probe: what licensing data can we actually pull at the MSP level?

Goal is to find the data needed to visualize, per MSP_EC tenant:
  - which license assignments it holds (quantity + term)
  - the spread of terms (1yr vs 5yr etc.)
  - the effective expiration = earliest expiration across assignments

Hits, in order:
  GET  /mspEntitlements              -- the MSP's bulk license pool (per-SKU terms)
  GET  /mspEntitlements/summaries    -- aggregated pool w/ remainingLicenses/remainingDays
  GET  /entitlements                 -- the MSP's own (self) entitlements
  POST /entitlements/query           -- v2 replacement for GET /entitlements
  POST /mspecs/query                 -- list of MSP_EC tenants
  POST /tenants/{tenantId}/entitlements/assignments/query  -- per-EC assignments (the good stuff)

Writes the full payloads to a JSON file so we can shape the tool offline.

Usage:
    docker compose exec backend python scripts/probe_msp_licensing.py <controller_id> [max_ecs]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller

OUT = Path("/tmp/msp_licensing_probe.json")


def call(r1, method: str, path: str, payload=None, **kw):
    print(f"\n>>> {method} {path}" + (f"  {json.dumps(payload)}" if payload else ""))
    try:
        resp = r1.post(path, payload=payload, **kw) if method == "POST" else r1.get(path, **kw)
    except Exception as e:
        print(f"    exception: {type(e).__name__}: {e}")
        return {"__error__": f"{type(e).__name__}: {e}"}
    print(f"    HTTP {resp.status_code}")
    if not resp.ok:
        print(f"    body: {resp.text[:400]}")
        return {"__error__": f"HTTP {resp.status_code}", "__body__": resp.text[:1000]}
    try:
        return resp.json()
    except ValueError:
        return {"__error__": "non-JSON", "__body__": resp.text[:1000]}


def preview(label, obj, n=2):
    print(f"--- {label} ---")
    if isinstance(obj, list):
        print(f"    list of {len(obj)}")
        for item in obj[:n]:
            print("    " + json.dumps(item, default=str)[:900])
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                print(f"    {k}: list[{len(v)}]")
                for item in v[:n]:
                    print("      " + json.dumps(item, default=str)[:900])
            else:
                print(f"    {k}: {json.dumps(v, default=str)[:400]}")
    else:
        print(f"    {obj!r}")


def probe(controller_id: int, max_ecs: int):
    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    print(f"[probe] controller={controller_id} ec_type={r1.ec_type} tenant={r1.tenant_id}")
    out = {"controller_id": controller_id, "ec_type": r1.ec_type}

    print("\n########## MSP BULK POOL ##########")
    out["mspEntitlements"] = call(r1, "GET", "/mspEntitlements")
    preview("mspEntitlements", out["mspEntitlements"], n=3)

    out["mspEntitlementSummaries"] = call(r1, "GET", "/mspEntitlements/summaries")
    preview("mspEntitlements/summaries", out["mspEntitlementSummaries"], n=3)

    print("\n########## MSP SELF ENTITLEMENTS ##########")
    out["entitlements"] = call(r1, "GET", "/entitlements")
    preview("entitlements", out["entitlements"], n=3)

    # v2 replacement for GET /entitlements — richer filters, paginated
    out["entitlementsQuery"] = call(
        r1, "POST", "/entitlements/query",
        {"page": 1, "pageSize": 100, "sortField": "expirationDate",
         "sortOrder": "ASC", "filters": {}},
    )
    preview("entitlements/query", out["entitlementsQuery"], n=3)

    print("\n########## MSP_EC TENANT LIST ##########")
    ecs_resp = call(
        r1, "POST", "/mspecs/query",
        {"fields": ["id", "name", "tenantType"], "sortField": "name",
         "sortOrder": "ASC", "filters": {"tenantType": ["MSP_EC"]}},
    )
    out["mspecs"] = ecs_resp
    ecs = (ecs_resp or {}).get("data") or []
    print(f"    {len(ecs)} MSP_ECs")

    print("\n########## PER-EC ASSIGNMENTS ##########")
    per_ec = []
    for ec in ecs[:max_ecs]:
        tid, name = ec.get("id"), ec.get("name")
        body = {
            "page": 1, "pageSize": 100,
            "sortField": "expirationDate", "sortOrder": "ASC",
            "filters": {},
        }
        # NB: no override_tenant_id — the call must stay in MSP context, the
        # target EC goes in the path. Overriding makes R1 treat the EC as the
        # logged-in tenant and it rejects with ENTITLEMENT-10200.
        res = call(r1, "POST", f"/tenants/{tid}/entitlements/assignments/query", body)
        preview(f"assignments {name}", res, n=4)
        per_ec.append({"tenant_id": tid, "name": name, "assignments": res})
    out["per_ec_assignments"] = per_ec

    # Same query from the MSP's own perspective, for comparison
    print("\n########## SELF ASSIGNMENTS (MSP) ##########")
    out["selfAssignments"] = call(
        r1, "POST", "/tenants/self/entitlements/assignments/query",
        {"page": 1, "pageSize": 100, "sortField": "expirationDate",
         "sortOrder": "ASC", "filters": {}},
    )
    preview("self assignments", out["selfAssignments"], n=3)

    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n[probe] wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    probe(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 5)
