#!/usr/bin/env python3
"""
SZ -> R1 migration: DPSK service choices on Review.

Every WLAN that ends up DPSK in R1 -- native SZ DPSK, or a RADIUS WLAN
converted on Review -- needs a pool and an identity group. Before this they
were find-or-created per WLAN by name with fixed settings (1 device per
passphrase), which also broke when a same-named group already served
another pool (DPSK-10032) and orphaned the group when a same-named pool was
reused.

WHAT THIS GUARDS

  1. A DPSK WLAN without a chosen service blocks the plan.
  2. Converted WLANs are DPSK WLANs too (they need a service).
  3. A new pool or group whose name already exists in R1 blocks.
  4. An identity group already serving a pool blocks (DPSK-10032), as does
     one group chosen for two new pools.
  5. Settings outside R1's bounds block; valid ones pass.
  6. A chosen existing pool that no longer exists blocks.
  7. A shared new pool is created ONCE, in the global phase, with the
     chosen settings; unlimited devices omits the limit.
  8. configure_dpsk links the assigned pool and creates nothing.
  9. Plans without DPSK choices keep the per-WLAN behaviour.
 10. Nothing returned for the UI carries a passphrase.

Usage:
    docker compose exec backend python scripts/test_migration_dpsk_choices.py

Exits non-zero on failure.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from schemas.sz_migration import SZMigrationSnapshot, SZZoneSnapshot, SZWLANFull, SZReferencedObject
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.dpsk_choices import load_r1_dpsk, validate_dpsk_plan, default_plan
from workflow.phases.phase_executor import PhaseContext
import workflow.phases.sz_migration.validate as validate_mod
import workflow.phases.sz_migration.create_dpsk_pools as pools_mod
import workflow.phases.sz_migration.configure_dpsk as configure_mod

ROW_PASSPHRASE = "never-show-this-passphrase"


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    return 0 if ok else 1


# ==================== Fake R1 ====================

R1_POOLS = [{"id": "pool-old", "name": "Old-Pool", "passphraseLength": 12, "deviceCountLimit": 3,
             "networkCount": 1, "passphrase": ROW_PASSPHRASE}]
R1_GROUPS = [
    {"id": "ig-bound", "name": "Bound-Group", "dpskPoolId": "pool-old", "identityCount": 40},
    {"id": "ig-free", "name": "Free-Group", "dpskPoolId": None, "identityCount": 12},
]


class FakeIdentity:
    def __init__(self):
        self.created = []

    async def query_identity_groups(self, tenant_id=None, page=0, size=1000, **kw):
        return {"content": R1_GROUPS if page == 0 else [], "last": True}

    async def create_identity_group(self, name, description=None, tenant_id=None, **kw):
        self.created.append(name)
        return {"id": f"ig-new-{len(self.created)}", "name": name}


class FakeDpsk:
    def __init__(self):
        self.created = []

    async def query_dpsk_pools(self, tenant_id=None, search_string=None, page=1, limit=100):
        return {"data": R1_POOLS if page == 1 else [], "totalCount": len(R1_POOLS)}

    async def create_dpsk_pool(self, **kw):
        self.created.append(kw)
        return {"id": f"pool-new-{len(self.created)}"}


class FakeNetworks:
    def __init__(self):
        self.linked = []

    async def activate_dpsk_service_on_network(self, tenant_id, network_id, dpsk_service_id):
        self.linked.append((network_id, dpsk_service_id))


class FakeR1:
    def __init__(self):
        self.identity, self.dpsk, self.networks = FakeIdentity(), FakeDpsk(), FakeNetworks()
        self.radius_profiles = SimpleNamespace(get_radius_profiles=lambda t=None: [])

    async def await_task_completion(self, **kw):
        return None


class FakeState:
    def __init__(self, job=None, unit=None):
        self.job, self.unit, self.tracked = job, unit, []

    async def get_job(self, job_id):
        return self.job

    async def get_unit(self, job_id, unit_id):
        return self.unit

    async def track_created_resource(self, job_id, rtype, data):
        self.tracked.append((rtype, data))


def ctx(r1, options, state=None):
    return PhaseContext(job_id="job-1", r1_client=r1, venue_id="v", tenant_id="t",
                        state_manager=state, options=options)


def snapshot():
    return SZMigrationSnapshot(
        zone=SZZoneSnapshot(id="z", name="Oak Park"),
        wlans=[
            SZWLANFull(id="w-res", name="Resident", ssid="Resident", auth_type="DPSK"),
            SZWLANFull(id="w-corp", name="Corp", ssid="Corp", auth_type="WPA2-Enterprise",
                       auth_service_id="s-cp"),
            SZWLANFull(id="w-guest", name="Guest", ssid="Guest", auth_type="WPA2-PSK"),
        ],
        referenced_objects={"auth_service:s-cp": SZReferencedObject(
            ref_type="auth_service", id="s-cp", name="cloudpath",
            raw={"name": "cloudpath", "primary": {"ip": "10.0.0.5", "port": 1812}})},
    )


async def run_validate(options):
    snap = snapshot()

    class R:
        async def get(self, k):
            return snap.model_dump_json()

    async def fake_redis():
        return R()

    async def fake_inventory(*a, **k):
        return R1VenueInventory(venue_id="v", venue_name="V", tenant_id="t")

    validate_mod.get_redis_client = fake_redis
    validate_mod.capture_venue_inventory = fake_inventory
    phase = validate_mod.ValidateMigrationPhase(ctx(FakeR1(), options))
    return await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))


def errors(out):
    return [c.description for c in out.validation_result.conflicts if c.severity == "error"]


NEW = {"mode": "new", "name": "Oak-Park-dpsk", "passphrase_length": 12,
       "passphrase_format": "KEYBOARD_FRIENDLY", "devices_per_passphrase": None,
       "expiration_days": None, "identity_group": {"mode": "new", "name": "Oak-Park-identities"}}
RADIUS_SKIP_TO_DPSK = {"radius_decisions": {"s-cp": {"action": "skip"}},
                       "wlan_fallbacks": {"w-corp": "dpsk"}}


async def main():
    failures = 0
    print("Migration DPSK choices")

    # 1 + 2
    out = await run_validate({**RADIUS_SKIP_TO_DPSK, "dpsk_pools": {"shared": NEW},
                              "dpsk_assignments": {"w-res": "shared"}})
    failures += check("converted WLAN without a DPSK service blocks the plan",
                      not out.validation_result.valid and any("Corp" in e for e in errors(out)),
                      str(errors(out)))

    out = await run_validate({**RADIUS_SKIP_TO_DPSK, "dpsk_pools": {"shared": NEW},
                              "dpsk_assignments": {"w-res": "shared", "w-corp": "shared"}})
    failures += check("both DPSK WLANs on one new shared pool -> valid",
                      out.validation_result.valid, str(errors(out)))
    failures += check("each DPSK unit carries its pool key",
                      out.unit_mappings["w-res"].plan.extra.get("dpsk_pool_key") == "shared"
                      and out.unit_mappings["w-corp"].plan.extra.get("dpsk_pool_key") == "shared")
    failures += check("PSK WLAN gets no pool key",
                      "dpsk_pool_key" not in out.unit_mappings["w-guest"].plan.extra)
    creates = [a.name for a in out.validation_result.actions if a.action == "create"
               and a.resource_type in ("dpsk_pool", "identity_group")]
    failures += check("plan lists the pool and group it will create",
                      creates.count("Oak-Park-dpsk") == 1 and "Oak-Park-identities" in creates, str(creates))

    # 3-6: the pure validator
    wl = {"w-res": "Resident"}
    pools, groups = await load_r1_dpsk(FakeR1(), "t")

    def v(spec):
        c, _ = validate_dpsk_plan({"k": spec}, {"w-res": "k"}, wl, pools, groups)
        return [x.description for x in c if x.severity == "error"]

    failures += check("new pool named like an existing one blocks",
                      any("already has a DPSK pool" in e for e in v({**NEW, "name": "old-pool"})))
    failures += check("new group named like an existing one blocks",
                      any("identity group named" in e for e in
                          v({**NEW, "identity_group": {"mode": "new", "name": "Free-Group"}})))
    failures += check("group already serving a pool blocks (DPSK-10032)",
                      any("DPSK-10032" in e for e in
                          v({**NEW, "identity_group": {"mode": "existing", "id": "ig-bound"}})))
    failures += check("a free existing group is accepted",
                      not v({**NEW, "identity_group": {"mode": "existing", "id": "ig-free"}}))
    c, _ = validate_dpsk_plan(
        {"a": {**NEW, "name": "A", "identity_group": {"mode": "existing", "id": "ig-free"}},
         "b": {**NEW, "name": "B", "identity_group": {"mode": "existing", "id": "ig-free"}}},
        {"w1": "a", "w2": "b"}, {"w1": "One", "w2": "Two"}, pools, groups)
    failures += check("one group for two new pools blocks",
                      any("two new DPSK services" in x.description for x in c))
    for bad, label in (({"passphrase_length": 7}, "length below 8"),
                       ({"passphrase_length": 64}, "length above 63"),
                       ({"devices_per_passphrase": 0}, "0 devices"),
                       ({"devices_per_passphrase": 513}, "513 devices"),
                       ({"passphrase_format": "EMOJI"}, "unknown format"),
                       ({"expiration_days": 0}, "0-day expiry")):
        failures += check(f"{label} blocks", bool(v({**NEW, **bad})))
    failures += check("valid settings pass",
                      not v({**NEW, "passphrase_length": 16, "devices_per_passphrase": 5,
                             "passphrase_format": "DICTIONARY_WORDS", "expiration_days": 365}))
    failures += check("existing pool that no longer exists blocks",
                      any("no longer exists" in e for e in v({"mode": "existing", "existing_pool_id": "gone"})))
    failures += check("existing pool is accepted",
                      not v({"mode": "existing", "existing_pool_id": "pool-old"}))

    # 7. the global phase
    r1 = FakeR1()
    state = FakeState()
    phase = pools_mod.CreateDpskPoolsPhase(ctx(r1, {
        "dpsk_pools": {"shared": {**NEW, "devices_per_passphrase": None, "expiration_days": 30},
                       "old": {"mode": "existing", "existing_pool_id": "pool-old"}},
        "dpsk_assignments": {"w-res": "shared", "w-corp": "shared", "w-x": "old"},
    }, state))
    out = await phase.execute(phase.Inputs(sz_snapshot_job_id="snap"))
    failures += check("shared new pool created exactly once", len(r1.dpsk.created) == 1)
    kw = r1.dpsk.created[0]
    failures += check("pool created with the chosen settings",
                      kw["passphrase_length"] == 12 and kw["passphrase_format"] == "KEYBOARD_FRIENDLY"
                      and kw["max_devices_per_passphrase"] == 0 and kw["expiration_days"] == 30)
    failures += check("new identity group created and used for the pool",
                      r1.identity.created == ["Oak-Park-identities"] and kw["identity_group_id"] == "ig-new-1")
    failures += check("existing pool mapped, not created",
                      out.dpsk_pool_ids == {"shared": "pool-new-1", "old": "pool-old"}, str(out.dpsk_pool_ids))
    failures += check("only created resources tracked",
                      sorted(t for t, _ in state.tracked) == ["dpsk_pools", "identity_groups"])

    # 8. configure_dpsk links
    r1 = FakeR1()
    job = SimpleNamespace(global_phase_results={"create_dpsk_pools": {"dpsk_pool_ids": {"shared": "pool-new-1"}}})
    unit = SimpleNamespace(plan=SimpleNamespace(extra={"r1_network_type": "dpsk", "sz_wlan_name": "Resident",
                                                       "dpsk_pool_key": "shared"}))
    phase = configure_mod.ConfigureDPSKPhase(ctx(r1, {}, FakeState(job, unit)))
    out = await phase.execute(phase.Inputs(unit_id="w-res", unit_number="Resident", network_id="net-1"))
    failures += check("configure_dpsk links the assigned pool and creates nothing",
                      r1.networks.linked == [("net-1", "pool-new-1")]
                      and not r1.dpsk.created and not r1.identity.created)

    # 9. legacy
    out = await run_validate({})
    failures += check("no DPSK choices -> no pool key, plan valid (legacy path)",
                      out.validation_result.valid
                      and "dpsk_pool_key" not in out.unit_mappings["w-res"].plan.extra)

    # 10. nothing for the UI carries a passphrase
    failures += check("pool summaries carry no passphrase", ROW_PASSPHRASE not in json.dumps(pools))
    d = default_plan("Oak Park #2", ["w-res"])
    failures += check("default plan: one shared pool, safe names",
                      d["pools"]["shared"]["name"] == "Oak-Park--2-dpsk" and d["assignments"] == {"w-res": "shared"},
                      str(d))

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
