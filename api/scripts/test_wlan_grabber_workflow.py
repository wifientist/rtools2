#!/usr/bin/env python3
"""
WLAN Grabber workflow: the phases against a fake R1, no network calls.

WHAT THIS GUARDS

  1. The workflow is registered; RADIUS and the shared pool run before any
     network is created.
  2. Validate: a name already in the tenant is skipped (reuse), a blocked
     spec or an Enterprise WLAN with no mapped RADIUS is skipped with a
     warning; nothing to create is a blocking error.
  3. The shared pool is created ONCE (identity group + pool) and every
     shared-pool DPSK network links it; a row's own pool wins; "none" links
     nothing.
  4. A pool that fails to create fails only its DPSK units, before any
     network is created for them.
  5. PSK takes its passphrase from the secret store and deletes it; without
     it the unit fails and nothing is created.
  6. No description unless add_description.

Usage:
    docker compose exec backend python scripts/test_wlan_grabber_workflow.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import workflow.phases.wlan_grabber as mod
from workflow.phases.phase_executor import PhaseContext
from workflow.workflows import get_workflow
from workflow.workflows.wlan_grabber import WlanGrabberWorkflow

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


# ── fakes ─────────────────────────────────────────────────────────────

SECRETS = {}


async def fake_take_secret(job_id, field):
    return SECRETS.pop(field, None)

mod._take_secret = fake_take_secret


class FakeNetworks:
    def __init__(self, existing=()):
        self.existing = [{"id": f"x-{n}", "name": n, "ssid": n} for n in existing]
        self.created, self.dpsk_links = [], []

    async def get_wifi_networks(self, tenant_id, venue_id=None):
        return {"data": self.existing}

    async def find_wifi_network_by_name(self, tenant_id, venue_id, name):
        return next((n for n in self.existing if n["name"] == name), None)

    async def _create(self, kind, **kw):
        self.created.append((kind, kw))
        return {"id": f"net-{kw['name']}"}

    async def create_wifi_network(self, **kw): return await self._create("psk", **kw)
    async def create_open_wifi_network(self, **kw): return await self._create("open", **kw)
    async def create_aaa_wifi_network(self, **kw): return await self._create("aaa", **kw)
    async def create_dpsk_wifi_network(self, **kw): return await self._create("dpsk", **kw)

    async def activate_dpsk_service_on_network(self, tenant_id, network_id, dpsk_service_id):
        self.dpsk_links.append((network_id, dpsk_service_id))


class FakeDpsk:
    def __init__(self, fail=False):
        self.fail, self.created = fail, []

    async def query_dpsk_pools(self, **kw):
        return {"data": [{"id": p, "name": n} for p, n in self.created]}

    async def create_dpsk_pool(self, identity_group_id, name, **kw):
        if self.fail:
            raise RuntimeError("Name must be unique")
        self.created.append((f"pool-{name}", name))
        return {"id": f"pool-{name}"}


class FakeIdentity:
    def __init__(self):
        self.created = []

    async def create_identity_group(self, tenant_id, name, **kw):
        self.created.append(name)
        return {"id": f"ig-{name}"}


class FakeState:
    def __init__(self, units=None):
        self.units, self.tracked = units or {}, []

    async def get_job(self, job_id):
        return SimpleNamespace(units=self.units)

    async def track_created_resource(self, job_id, kind, data):
        self.tracked.append((kind, data))


def r1(networks=None, dpsk=None):
    return SimpleNamespace(networks=networks or FakeNetworks(), dpsk=dpsk or FakeDpsk(),
                           identity=FakeIdentity(), radius_profiles=None)


def ctx(client, state=None, job="j"):
    return PhaseContext(job_id=job, r1_client=client, venue_id="", tenant_id="t",
                        state_manager=state or FakeState(), options={})


def spec(wid, name, r1_type="psk", **kw):
    return {"sz_wlan_id": wid, "sz_name": name, "sz_ssid": name, "r1_type": r1_type,
            "security": "WPA2" if r1_type == "psk" else None, "vlan_id": 10,
            "auth_service_id": kw.pop("auth", None), "accounting_service_id": None,
            "advanced": {}, "blockers": kw.pop("blockers", []), **kw}


def run(coro):
    return asyncio.run(coro)


# ── 1. workflow ───────────────────────────────────────────────────────

print("\n1. Workflow")
check("registered", get_workflow("wlan_grabber") is WlanGrabberWorkflow)
check("definition valid", WlanGrabberWorkflow.validate_definition() == [], WlanGrabberWorkflow.validate_definition())
deps = {p.id: p.depends_on for p in WlanGrabberWorkflow.phases}
check("network create waits on pool, pool on RADIUS",
      deps["grabber_create_network"] == ["grabber_dpsk_pool"]
      and deps["grabber_dpsk_pool"] == ["grabber_radius_profiles"], deps)

# ── 2. validate ───────────────────────────────────────────────────────

print("\n2. Validate")
phase = mod.ValidateWlanGrabberPhase(ctx(r1(FakeNetworks(existing=["Taken"]))))
out = run(phase.execute(phase.Inputs(
    wlans=[
        {"spec": spec("a", "Resident")},
        {"spec": spec("b", "taken")},
        {"spec": spec("c", "Bad", blockers=["SZ returned no passphrase"])},
        {"spec": spec("d", "Corp", "aaa", auth="rs1")},
        {"spec": spec("e", "Units", "dpsk")},
    ],
    radius={"rs1": {"action": "skip"}},
    dpsk_pool={"mode": "new", "name": "Zone DPSK"},
)))
check("one unit per WLAN to create", sorted(u.unit_number for u in out.unit_mappings.values()) == ["Resident", "Units"],
      list(out.unit_mappings))
check("name taken (case-insensitive) -> existing", out.networks_existing == ["taken"], out.networks_existing)
check("blocked + unmapped RADIUS -> skipped", sorted(s["wlan"] for s in out.wlans_skipped) == ["Bad", "Corp"],
      out.wlans_skipped)
check("plan valid", out.validation_result.valid)
check("new shared pool counted", out.validation_result.summary.dpsk_pools_to_create == 1)
check("spec rides in input_config",
      out.unit_mappings["wlan_e"].input_config["spec"]["r1_type"] == "dpsk")

out = run(phase.execute(phase.Inputs(wlans=[{"spec": spec("b", "Taken")}])))
check("nothing to create -> invalid", not out.validation_result.valid)

# ── 3. shared pool once, links ────────────────────────────────────────

print("\n3. Shared DPSK pool")
units = {
    "u1": SimpleNamespace(input_config={"spec": spec("1", "D1", "dpsk"), "dpsk_pool_choice": None}),
    "u2": SimpleNamespace(input_config={"spec": spec("2", "D2", "dpsk"), "dpsk_pool_choice": None}),
}
client = r1()
pool_phase = mod.GrabberDpskPoolPhase(ctx(client, FakeState(units)))
pool_out = run(pool_phase.execute(pool_phase.Inputs(dpsk_pool={"mode": "new", "name": "Zone DPSK"})))
check("pool + identity group created once",
      client.dpsk.created == [("pool-Zone DPSK", "Zone DPSK")] and client.identity.created == ["Zone DPSK"],
      (client.dpsk.created, client.identity.created))
again = run(pool_phase.execute(pool_phase.Inputs(dpsk_pool={"mode": "new", "name": "Zone DPSK"})))
check("existing pool by name is linked, not recreated",
      again.shared_dpsk_pool_id == "pool-Zone DPSK" and len(client.dpsk.created) == 1)

net_phase = mod.GrabberCreateNetworkPhase(ctx(client))
for wid, choice in (("1", None), ("2", "pool-own"), ("3", "none")):
    run(net_phase.execute(net_phase.Inputs(
        unit_id=f"u{wid}", unit_number=f"D{wid}", spec=spec(wid, f"D{wid}", "dpsk"),
        dpsk_pool_choice=choice, shared_dpsk_pool_id=pool_out.shared_dpsk_pool_id)))
check("shared, own and no pool linked as chosen",
      client.networks.dpsk_links == [("net-D1", "pool-Zone DPSK"), ("net-D2", "pool-own")],
      client.networks.dpsk_links)

# ── 4. pool failure ───────────────────────────────────────────────────

print("\n4. Pool failure")
client = r1(dpsk=FakeDpsk(fail=True))
pool_phase = mod.GrabberDpskPoolPhase(ctx(client, FakeState(units)))
fail_out = run(pool_phase.execute(pool_phase.Inputs(dpsk_pool={"mode": "new", "name": "Zone DPSK"})))
check("pool error reported, phase does not raise", fail_out.shared_dpsk_pool_error and not fail_out.shared_dpsk_pool_id)
net_phase = mod.GrabberCreateNetworkPhase(ctx(client))
try:
    run(net_phase.execute(net_phase.Inputs(unit_id="u1", unit_number="D1", spec=spec("1", "D1", "dpsk"),
                                           shared_dpsk_pool_error=fail_out.shared_dpsk_pool_error)))
    check("shared-pool unit fails", False, "no exception")
except RuntimeError as e:
    check("shared-pool unit fails before creating", "not created" in str(e) and not client.networks.created, str(e))
run(net_phase.execute(net_phase.Inputs(unit_id="u9", unit_number="O1", spec=spec("9", "O1", "open"),
                                       shared_dpsk_pool_error=fail_out.shared_dpsk_pool_error)))
check("non-DPSK unit unaffected", [k for k, _ in client.networks.created] == ["open"])

# ── 5. PSK secret ─────────────────────────────────────────────────────

print("\n5. PSK passphrase")
client = r1()
net_phase = mod.GrabberCreateNetworkPhase(ctx(client))
SECRETS["psk:p1"] = "s3cret-pass-phrase"
run(net_phase.execute(net_phase.Inputs(unit_id="up", unit_number="P1", spec=spec("p1", "P1"))))
kind, kw = client.networks.created[-1]
check("passphrase from the secret store", kind == "psk" and kw["passphrase"] == "s3cret-pass-phrase")
check("secret consumed", "psk:p1" not in SECRETS)
check("no description by default", kw["description"] is None, kw["description"])
try:
    run(net_phase.execute(net_phase.Inputs(unit_id="uq", unit_number="P2", spec=spec("p2", "P2"))))
    check("missing passphrase fails", False, "no exception")
except RuntimeError as e:
    check("missing passphrase fails, nothing created", len(client.networks.created) == 1, str(e))

# ── 6. description opt-in ─────────────────────────────────────────────

print("\n6. Description")
run(net_phase.execute(net_phase.Inputs(unit_id="uo", unit_number="O2", spec=spec("o2", "O2", "open"),
                                       add_description=True)))
check("add_description writes it", client.networks.created[-1][1]["description"] == "Migrated from SZ: O2")

# ── 7. WISPr ──────────────────────────────────────────────────────────

print("\n7. WISPr guest")
KEY = "AbCdEf0123456789"
SOURCE = {"id": "k1", "name": "Key Source", "type": "guest",
          "guestPortal": {"guestNetworkType": "WISPr", "wisprPage": {"integrationKey": KEY}}}


class WisprNetworks(FakeNetworks):
    def __init__(self):
        super().__init__()
        self.settings = []

    async def get_wifi_network_by_id(self, nid, tenant_id=None):
        return SOURCE if nid == "k1" else None

    async def create_wifi_network_from_payload(self, tenant_id, payload):
        self.created.append(("guest", payload))
        return {"id": f"net-{payload['name']}"}

    async def update_radius_settings_on_network(self, nid, settings, tenant_id=None):
        self.settings.append((nid, settings))


class FakeRadius:
    def __init__(self):
        self.links = []

    async def link_radius_to_network(self, network_id, radius_profile_id, tenant_id=None):
        self.links.append((network_id, radius_profile_id))


client = SimpleNamespace(networks=WisprNetworks(), radius_profiles=FakeRadius(), dpsk=FakeDpsk(),
                         identity=FakeIdentity())
facts = {"walled_gardens": ["*.xfinity.com"], "portal_url": "http://sz/y.html", "provider": "guest",
         "auth_type": "RADIUS", "auth_proxy": True, "session_timeout": 1440, "grace_period": 60}
wspec = spec("g1", "Guest Net", "wispr", portal=facts, auth="rs1")
wispr = {"key_source": "network", "key_network_id": "k1", "encrypt_mac_ip": True, "enable_dhcp": False}

phase = mod.ValidateWlanGrabberPhase(ctx(client))
out = run(phase.execute(phase.Inputs(wlans=[{"spec": wspec}], wispr=wispr,
                                     radius={"rs1": {"action": "existing", "r1_profile_id": "rp"}})))
check("validate accepts: key source + mapped RADIUS", out.validation_result.valid,
      [c.description for c in out.validation_result.conflicts])
check("plan does not hold the key", KEY not in out.model_dump_json())
out = run(phase.execute(phase.Inputs(wlans=[{"spec": wspec}], wispr=wispr, radius={})))
check("RADIUS-auth WISPr with unmapped RADIUS -> skipped", out.wlans_skipped and not out.unit_mappings)
out = run(phase.execute(phase.Inputs(wlans=[{"spec": wspec}], wispr={"key_source": "network"},
                                     radius={"rs1": {"action": "existing", "r1_profile_id": "rp"}})))
check("no key source -> blocking", not out.validation_result.valid)

net_phase = mod.GrabberCreateNetworkPhase(ctx(client, job="jw"))
for wid in ("g1", "g2"):
    o = run(net_phase.execute(net_phase.Inputs(
        unit_id=wid, unit_number=wid, wispr=wispr, radius_profile_mappings={"rs1": "rp"},
        spec={**wspec, "sz_wlan_id": wid, "sz_name": f"Guest {wid}", "sz_ssid": f"Guest {wid}"})))
kind, body = client.networks.created[0]
check("guest network: key copied, RADIUS auth",
      kind == "guest" and body["guestPortal"]["wisprPage"]["integrationKey"] == KEY
      and body["guestPortal"]["wisprPage"]["authType"] == "RADIUS")
check("mapped RADIUS profile linked to each",
      client.radius_profiles.links == [("net-Guest g1", "rp"), ("net-Guest g2", "rp")], client.radius_profiles.links)
check("auth proxy set as SZ does", client.networks.settings[0] == ("net-Guest g1", {"enableAuthProxy": True}))
check("unit outputs carry no key", KEY not in o.model_dump_json())
try:
    run(net_phase.execute(net_phase.Inputs(unit_id="g3", unit_number="g3", wispr=wispr,
                                           spec={**wspec, "sz_wlan_id": "g3", "sz_name": "Guest g3"})))
    check("RADIUS auth without a profile fails", False, "no exception")
except RuntimeError as e:
    check("RADIUS auth without a profile fails before creating", len(client.networks.created) == 2, str(e))
print("   SZ key source (the default)")
out = run(phase.execute(phase.Inputs(wlans=[{"spec": wspec}], wispr={},
                                     radius={"rs1": {"action": "existing", "r1_profile_id": "rp"}})))
check("no key choice = from SZ, plan valid", out.validation_result.valid,
      [c.description for c in out.validation_result.conflicts])
SECRETS["wispr_key:g5"] = "SzSign0123456789"
run(net_phase.execute(net_phase.Inputs(unit_id="g5", unit_number="g5", wispr={}, radius_profile_mappings={"rs1": "rp"},
                                       spec={**wspec, "sz_wlan_id": "g5", "sz_name": "Guest g5"})))
wp5 = client.networks.created[-1][1]["guestPortal"]["wisprPage"]
check("SZ signing key used, encrypted, consumed", wp5["integrationKey"] == "SzSign0123456789"
      and wp5["encryptMacIpEnabled"] is True and "wispr_key:g5" not in SECRETS)
run(net_phase.execute(net_phase.Inputs(unit_id="g6", unit_number="g6", wispr={}, radius_profile_mappings={"rs1": "rp"},
                                       spec={**wspec, "sz_wlan_id": "g6", "sz_name": "Guest g6"})))
wp6 = client.networks.created[-1][1]["guestPortal"]["wisprPage"]
check("no SZ key: no integration key, not encrypted", "integrationKey" not in wp6 and wp6["encryptMacIpEnabled"] is False)

links_before = len(client.radius_profiles.links)
aa = {**wspec, "sz_wlan_id": "g4", "sz_name": "Guest g4", "portal": {**facts, "auth_type": "ALWAYS_ACCEPT"}}
run(net_phase.execute(net_phase.Inputs(unit_id="g4", unit_number="g4", wispr=wispr, spec=aa)))
check("Always Accept: created, nothing linked",
      client.networks.created[-1][1]["guestPortal"]["wisprPage"]["authType"] == "ALWAYS_ACCEPT"
      and len(client.radius_profiles.links) == links_before)

print(f"\n{'FAILED: ' + str(FAILS) if FAILS else 'All passed'}\n")
sys.exit(1 if FAILS else 0)
