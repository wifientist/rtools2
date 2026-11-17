#!/usr/bin/env python3
"""
WLAN Activator: create the WLANs the list names but R1 does not have.

WHAT THIS GUARDS

  1. groups_needing_wlans turns AP Groups into the unit WLAN each should
     carry, skips the default group and groups a unit already claimed, and
     splits missing / existing / invalid (too long for R1).
  2. Without create_missing, a missing WLAN is still a skipped row.
  3. With it, the WLAN is planned once (name = SSID), carries network_id ""
     + create_name into each AP Group, the first row's vlan becomes the
     network VLAN and only a different one is an AP-Group override.
  4. Not planned: a name differing only in case from an R1 network, a PSK
     with no passphrase. A chosen pool that does not exist blocks the plan.
  5. create_missing_wlans creates each once, links DPSK ones to the pool,
     makes a new pool first when asked, reuses one that appeared since the
     plan, and a failure fails only that WLAN.
  6. activate_wlans swaps in the created id (and pool), and fails a group
     whose WLAN was not created, with the reason, before sending anything.
  7. The endpoint strips passphrases from the rows and checks them.

Usage:
    docker compose exec backend python scripts/test_wlan_activator_create_missing.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import workflow.phases.create_missing_wlans as create_mod
import workflow.phases.validate_wlan_activator as validate_mod
from routers.wlan_activator.v2_endpoints import _passphrase_problem, _take_passphrases
from services.wlan_activator import groups_needing_wlans, render_pattern
from workflow.phases.activate_wlans import ActivateWlansPhase
from workflow.phases.phase_executor import PhaseContext

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


GROUPS = [
    {"id": "g0", "name": "default", "isDefault": True},
    {"id": "g1", "name": "101"},
    {"id": "g2", "name": "102"},
    {"id": "g3", "name": "103"},
    {"id": "g4", "name": "201"},
]
NETWORKS = [
    {"id": "n103", "name": "103@Fieldhouse", "ssid": "103@Fieldhouse", "venueApGroups": []},
    {"id": "nres", "name": "Resident", "ssid": "Resident", "venueApGroups": []},
]

print("\n1. AP Groups -> the WLANs they should carry")
r = groups_needing_wlans(GROUPS, NETWORKS, "{unit}@{property}", "{unit}", "Fieldhouse", claimed_groups={"201"})
check("missing", [m["wlan"] for m in r["missing"]] == ["101@Fieldhouse", "102@Fieldhouse"], r["missing"])
check("existing", [m["wlan"] for m in r["existing"]] == ["103@Fieldhouse"], r["existing"])
check("default and claimed groups skipped",
      not any(m["ap_group"] in ("default", "201") for m in r["missing"] + r["existing"]), r)
r = groups_needing_wlans([{"name": "101"}], [], "{unit}@" + "x" * 40, "{unit}", "")
check("too long for R1 is invalid", r["invalid"] and "32" in r["invalid"][0]["reason"], r)
r = groups_needing_wlans([{"name": "Unit 101"}, {"name": "Lobby"}], [], "{unit}@P", "Unit {unit}")
check("group pattern captures the unit", [m["wlan"] for m in r["missing"]] == ["101@P"]
      and r["unmatched_groups"] == ["Lobby"], r)
check("render with a field", render_pattern("{building}-{unit}@{property}",
                                            {"unit": "1", "building": "A"}, "P") == "A-1@P")
check("render missing field -> None", render_pattern("{building}-{unit}", {"unit": "1"}) is None)


class FakeVenues:
    async def get_aps_by_tenant_venue(self, t, v): return {"data": []}
    async def list_ap_groups_in_venue(self, t, v): return [g for g in GROUPS if not g.get("isDefault")]
    @staticmethod
    def index_ap_groups_by_name(groups): return {g["name"]: g["id"] for g in groups}
    async def get_ssid_ap_group_settings(self, *a): return {}


class FakeNetworks:
    def __init__(self, existing=None):
        self.existing = list(existing if existing is not None else NETWORKS)
        self.created, self.links, self.fail = [], [], set()

    async def get_wifi_networks(self, t, venue_id=None): return {"data": self.existing}

    async def get_dpsk_services_on_network(self, nid, t=None): return {"data": []}

    async def _make(self, kind, name, **kw):
        if name in self.fail:
            raise RuntimeError("R1 said no")
        self.created.append((kind, name, kw.get("vlan_id"), kw.get("security_type")))
        return {"id": f"id-{name}"}

    async def create_dpsk_wifi_network(self, name, **kw): return await self._make("dpsk", name, **kw)
    async def create_open_wifi_network(self, name, **kw): return await self._make("open", name, **kw)
    async def create_wifi_network(self, name, passphrase, **kw):
        assert passphrase == "s3cretpass"
        return await self._make("psk", name, **kw)

    async def activate_dpsk_service_on_network(self, tenant_id, network_id, dpsk_service_id):
        self.links.append((network_id, dpsk_service_id))


class FakeDpsk:
    def __init__(self): self.pools = {"pool-u": "Units"}; self.created = []
    async def get_dpsk_pool(self, pool_id, tenant_id=None):
        return {"id": pool_id, "name": self.pools[pool_id]} if pool_id in self.pools else {}
    async def query_dpsk_pools(self, tenant_id, search_string, page, limit):
        return {"data": [{"id": i, "name": n} for i, n in self.pools.items() if n == search_string]}
    async def create_dpsk_pool(self, identity_group_id, name, **kw):
        self.created.append(name); self.pools["pool-new"] = name
        return {"id": "pool-new"}


class FakeIdentity:
    async def create_identity_group(self, tenant_id, name): return {"id": "idg-1"}


def ctx(networks, dpsk=None):
    r1 = SimpleNamespace(venues=FakeVenues(), networks=networks, dpsk=dpsk or FakeDpsk(),
                         identity=FakeIdentity())
    return PhaseContext(job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={})


ROWS = [
    {"ap_identifier": "", "ap_group_name": "101", "wlan": "101@Fieldhouse", "vlan": "20"},
    {"ap_identifier": "", "ap_group_name": "102", "wlan": "102@Fieldhouse", "vlan": ""},
    {"ap_identifier": "", "ap_group_name": "201", "wlan": "101@Fieldhouse", "vlan": "30"},
    {"ap_identifier": "", "ap_group_name": "103", "wlan": "103@Fieldhouse", "vlan": ""},
    {"ap_identifier": "", "ap_group_name": "103", "wlan": "resident", "vlan": ""},
]


def plan(create=None, rows=ROWS, nets=None):
    phase = validate_mod.ValidateWlanActivatorPhase(ctx(nets or FakeNetworks()))
    return asyncio.run(phase.execute(phase.Inputs(activations=rows, create_missing=create or {})))


def wlans(out, group):
    return out.unit_mappings[f"apgroup_{group}"].input_config["wlans"]


print("\n2. Without create_missing")
out = plan()
check("missing WLANs are skipped rows", {u["wlan"] for u in out.unresolved_wlans} >= {"101@Fieldhouse", "102@Fieldhouse"},
      out.unresolved_wlans)
check("nothing to create", out.networks_to_create == [])

print("\n3. With create_missing (DPSK, existing pool)")
DPSK = {"type": "dpsk", "dpsk_pool": {"mode": "existing", "pool_id": "pool-u"}}
out = plan(DPSK)
errs = [c.description for c in out.validation_result.conflicts if c.severity == "error"]
check("plan valid", out.validation_result.valid, errs)
made = {n["name"]: n for n in out.networks_to_create}
check("each missing WLAN planned once", sorted(made) == ["101@Fieldhouse", "102@Fieldhouse"], made)
check("name = SSID, type dpsk", all(n["ssid"] == n["name"] and n["type"] == "dpsk" for n in made.values()))
check("first row's vlan is the network VLAN", made["101@Fieldhouse"]["vlan"] == 20 and made["102@Fieldhouse"]["vlan"] == 1, made)
w101, w201 = wlans(out, "101")[0], wlans(out, "201")[0]
check("unit carries create_name, no id yet", w101["network_id"] == "" and w101["create_name"] == "101@Fieldhouse", w101)
check("same VLAN is no override", w101["vlan"] is None, w101)
check("a different VLAN is an override", w201["vlan"] == 30, w201)
check("existing WLAN still resolves", wlans(out, "103")[0]["network_id"] == "n103", wlans(out, "103"))
check("summary counts creates", out.validation_result.summary.networks_to_create == 2)
check("plan action lists them", any(a.resource_type == "wifi_networks" for a in out.validation_result.actions))

print("\n4. Not planned / blocking")
out = plan(DPSK, rows=[{"ap_identifier": "", "ap_group_name": "101", "wlan": "RESIDENT", "vlan": ""}],
           nets=FakeNetworks([{"id": "nres", "name": "Resident", "ssid": "Resident2", "venueApGroups": []}]))
check("case-only clash is not created", out.networks_to_create == []
      and any("differs only in case" in c.description for c in out.validation_result.conflicts),
      [c.description for c in out.validation_result.conflicts])
PSK = {"type": "psk", "security": "WPA3", "psk_names": ["101@Fieldhouse"]}
out = plan(PSK, rows=ROWS[:2])
check("PSK with passphrase planned, without not", [n["name"] for n in out.networks_to_create] == ["101@Fieldhouse"]
      and any("passphrase" in c.description for c in out.validation_result.conflicts), out.networks_to_create)
check("WPA3 PSK gets 6 GHz", "6-GHz" in wlans(out, "101")[0]["radio_types"], wlans(out, "101"))
out = plan({"type": "dpsk", "dpsk_pool": {"mode": "existing", "pool_id": "nope"}})
check("unknown pool blocks", not out.validation_result.valid
      and any(c.resource_type == "dpsk_pool" and c.severity == "error" for c in out.validation_result.conflicts))
out = plan({"type": "dpsk", "dpsk_pool": {"mode": "new", "name": "Units"}})
check("new pool named like an existing one: warning, still valid", out.validation_result.valid
      and any(c.resource_type == "dpsk_pool" and c.severity == "warning" for c in out.validation_result.conflicts))


print("\n5. create_missing_wlans")
secrets = {"psk:p1": "s3cretpass"}


async def fake_take(job_id, field): return secrets.pop(field, None)
async def fake_drop(job_id): secrets.clear()
create_mod._take_secret, create_mod.drop_secrets = fake_take, fake_drop


def create(todo, opts, nets=None, dpsk=None):
    nets = nets or FakeNetworks()
    phase = create_mod.CreateMissingWlansPhase(ctx(nets, dpsk))
    return nets, asyncio.run(phase.execute(phase.Inputs(networks_to_create=todo, create_missing=opts)))


todo = [{"name": "a", "ssid": "a", "type": "dpsk", "vlan": 20},
        {"name": "b", "ssid": "b", "type": "dpsk", "vlan": 1},
        {"name": "Resident", "ssid": "Resident", "type": "dpsk", "vlan": 1}]
nets = FakeNetworks(); nets.fail.add("b")
nets, out = create(todo, DPSK, nets)
check("created with its VLAN", ("dpsk", "a", 20, None) in nets.created, nets.created)
check("linked to the chosen pool", nets.links == [("id-a", "pool-u")], nets.links)
check("failure is per WLAN", out.create_failures.keys() == {"b"} and "R1 said no" in out.create_failures["b"], out.create_failures)
check("appeared since the plan -> reused, not created", out.created_networks.get("Resident") == "nres"
      and not any(c[1] == "Resident" for c in nets.created), out.created_networks)
check("count excludes the reused one", out.networks_created == 1, out.networks_created)

dpsk = FakeDpsk()
nets, out = create(todo[:1], {"type": "dpsk", "dpsk_pool": {"mode": "new", "name": "Fieldhouse Units"}}, dpsk=dpsk)
check("new pool made first and linked", dpsk.created == ["Fieldhouse Units"] and nets.links == [("id-a", "pool-new")]
      and out.created_dpsk_pool_id == "pool-new", (dpsk.created, nets.links))

secrets["psk:p1"] = "s3cretpass"   # earlier runs dropped the job's secrets, as the phase does
nets, out = create([{"name": "p1", "ssid": "p1", "type": "psk", "vlan": 1},
                    {"name": "p2", "ssid": "p2", "type": "psk", "vlan": 1}], {"type": "psk", "security": "WPA3"})
check("PSK uses its passphrase and security", ("psk", "p1", 1, "WPA3") in nets.created, nets.created)
check("PSK with no secret left fails, alone", "p2" in out.create_failures and "p1" in out.created_networks, out)

nets, out = create(todo[:1], {"type": "dpsk", "dpsk_pool": {"mode": "existing"}})
check("no pool -> DPSK WLANs fail, nothing created", not nets.created and "a" in out.create_failures, out)

print("\n6. activate_wlans uses the created ids")
I = ActivateWlansPhase.Inputs
inp = I(unit_id="u", unit_number="101", ap_group_id="g1",
        wlans=[{"network_id": "", "create_name": "a", "create_type": "dpsk", "dpsk_pool_id": None},
               {"network_id": "n103", "name": "103"}],
        created_networks={"a": "id-a"}, created_dpsk_pool_id="pool-u")
ws = ActivateWlansPhase._with_created_ids(inp, "101")
check("id and pool swapped in", ws[0]["network_id"] == "id-a" and ws[0]["dpsk_pool_id"] == "pool-u", ws)
check("existing WLAN untouched", ws[1] == {"network_id": "n103", "name": "103"}, ws)
inp.created_networks, inp.create_failures = {}, {"a": "R1 said no"}
try:
    ActivateWlansPhase._with_created_ids(inp, "101")
    check("not-created WLAN fails the group", False)
except RuntimeError as e:
    check("not-created WLAN fails the group, with the reason", "R1 said no" in str(e), str(e))

print("\n7. Endpoint passphrase handling")
rows = [{"ap_group_name": "1", "wlan": "w1", "passphrase": "abcdefgh"},
        {"ap_group_name": "2", "wlan": "w1", "passphrase": "other123"},
        {"ap_group_name": "3", "wlan": "w2"}]
pp = _take_passphrases(rows)
check("first passphrase per WLAN", pp == {"w1": "abcdefgh"}, pp)
check("stripped from every row", not any("passphrase" in r for r in rows), rows)
check("passphrase rules", _passphrase_problem("short") and not _passphrase_problem("a" * 63)
      and _passphrase_problem("g" * 64) and not _passphrase_problem("a" * 64))

print(f"\n{'ALL PASSED' if not FAILS else f'FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
