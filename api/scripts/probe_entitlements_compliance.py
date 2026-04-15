"""
Probe: what does /entitlements/compliances/query actually return?

Spec says the response is `{"compliances": [TenantLicenseCompliance]}` where
each entry has `licenseType`, `self`, and `mspEcSummary`, but the inner
`LicenseCompliance` schema is effectively opaque. This script tries several
payload variants against a controller and pretty-prints every response.

Also queries /entitlements/utilizations/query without any filter and
/mspEntitlements for comparison.

Usage:
    docker compose exec backend python scripts/probe_entitlements_compliance.py <controller_id>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller


def dump(label: str, obj):
    print(f"\n===== {label} =====")
    try:
        print(json.dumps(obj, indent=2, default=str)[:6000])
    except Exception:
        print(repr(obj)[:6000])


def try_call(r1, method: str, path: str, payload=None):
    print(f"\n>>> {method} {path}")
    if payload is not None:
        print(f"    payload: {json.dumps(payload)}")
    try:
        if method == "POST":
            resp = r1.post(path, payload=payload)
        else:
            resp = r1.get(path)
        print(f"    HTTP {resp.status_code}")
        if not resp.ok:
            print(f"    error body: {resp.text[:500]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"    exception: {type(e).__name__}: {e}")
        return None


def probe(controller_id: int):
    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    print(f"[probe] controller={controller_id} ec_type={r1.ec_type}")

    # --- /entitlements/compliances/query with various filter shapes ---
    variants = [
        ("compliance empty filters", {"filters": {}}),
        ("compliance APSW only", {"filters": {"licenseType": ["APSW"]}}),
        ("compliance all known types", {"filters": {"licenseType": ["APSW", "URLF", "EDGE_SECS", "EDGE_SECL", "SLTN_TOKEN"]}}),
        ("compliance MSP_SUMMARY (deprecated)", {"filters": {"complianceType": "MSP_SUMMARY"}}),
        ("compliance SELF (deprecated)", {"filters": {"complianceType": "SELF"}}),
    ]

    for label, payload in variants:
        resp = try_call(r1, "POST", "/entitlements/compliances/query", payload)
        if resp is not None:
            dump(label, resp)

    # --- /entitlements/utilizations/query without the APSW-only filter ---
    print("\n\n########## UTILIZATIONS COMPARISON ##########")
    util_variants = [
        ("utilizations empty filters", {"filters": {}}),
        ("utilizations SELF usageType", {"filters": {"usageType": "SELF"}}),
        ("utilizations all license types", {"filters": {"licenseType": ["APSW", "URLF", "EDGE_SECS", "EDGE_SECL", "SLTN_TOKEN"]}}),
    ]
    for label, payload in util_variants:
        resp = try_call(r1, "POST", "/entitlements/utilizations/query", payload)
        if resp is not None:
            dump(label, resp)

    # --- /mspEntitlements for the raw entitlement pool ---
    print("\n\n########## MSP ENTITLEMENTS ##########")
    resp = try_call(r1, "GET", "/mspEntitlements")
    if resp is not None:
        dump("mspEntitlements (raw)", resp)

    # --- /entitlements (self) for comparison ---
    print("\n\n########## SELF ENTITLEMENTS ##########")
    resp = try_call(r1, "GET", "/entitlements")
    if resp is not None:
        dump("entitlements (self)", resp)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    probe(int(sys.argv[1]))
