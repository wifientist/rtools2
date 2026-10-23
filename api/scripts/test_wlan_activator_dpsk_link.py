#!/usr/bin/env python3
"""
WLAN Activator: linking a DPSK pool to DPSK networks that have none.

WHAT THIS GUARDS

  1. Without a chosen pool, an unlinked DPSK network still blocks the plan
     (WIFI-20300), as before.
  2. With one, the link is planned instead (network -> pool), the WLAN
     carries that pool into activation, and the plan is valid.
  3. A chosen pool that does not exist in the tenant blocks the plan.
  4. The workflow links before any AP Group or activation work.
  5. link_dpsk_pools links each unlinked network once, leaves one that is
     linked by now alone, and reports a failure without raising.

Usage:
    docker compose exec backend python scripts/test_wlan_activator_dpsk_link.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import workflow.phases.validate_wlan_activator as validate_mod
from workflow.phases.link_dpsk_pools import LinkDpskPoolsPhase
from workflow.phases.phase_executor import PhaseContext
from workflow.workflows import get_workflow

FAILS = 0


def check(label, ok, detail=""):
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok and detail else ""))
    FAILS += 0 if ok else 1


NETWORKS = [
    {"id": "d1", "name": "101@F", "ssid": "101@F", "nwSubType": "dpsk", "venueApGroups": []},   # no pool
    {"id": "d2", "name": "102@F", "ssid": "102@F", "nwSubType": "dpsk", "venueApGroups": []},   # linked
]
ROWS = [{"ap_identifier": "", "ap_group_name": "101", "wlan": "101@F"},
        {"ap_identifier": "", "ap_group_name": "102", "wlan": "102@F"}]


class FakeVenues:
    async def get_aps_by_tenant_venue(self, t, v): return {"data": []}
    async def list_ap_groups_in_venue(self, t, v): return []
    @staticmethod
    def index_ap_groups_by_name(groups): return {}
    async def get_ssid_ap_group_settings(self, *a): return {}


class FakeNetworks:
    def __init__(self, linked):
        self.linked, self.links, self.fail = dict(linked), [], set()

    async def get_wifi_networks(self, t, venue_id=None): return {"data": NETWORKS}

    async def get_dpsk_services_on_network(self, nid, t=None):
        return {"data": [{"id": self.linked[nid]}] if nid in self.linked else []}

    async def activate_dpsk_service_on_network(self, tenant_id, network_id, dpsk_service_id):
        if network_id in self.fail:
            raise RuntimeError("R1 said no")
        self.links.append((network_id, dpsk_service_id))
        self.linked[network_id] = dpsk_service_id


class FakeDpsk:
    async def get_dpsk_pool(self, pool_id, tenant_id=None):
        return {"id": pool_id, "name": "Units"} if pool_id == "pool-u" else {}


def ctx(networks):
    r1 = SimpleNamespace(venues=FakeVenues(), networks=networks, dpsk=FakeDpsk())
    return PhaseContext(job_id="j", r1_client=r1, venue_id="v", tenant_id="t", options={})


def plan(options):
    phase = validate_mod.ValidateWlanActivatorPhase(ctx(FakeNetworks({"d2": "pool-x"})))
    return asyncio.run(phase.execute(phase.Inputs(activations=ROWS, options=options)))


print("\n1. No pool chosen")
out = plan({})
errs = [c.resource_name for c in out.validation_result.conflicts if c.severity == "error"]
check("unlinked DPSK still blocks", errs == ["101@F"] and not out.validation_result.valid, errs)
check("nothing planned to link", out.dpsk_links_to_make == {})

print("\n2. Pool chosen")
out = plan({"dpsk_link_pool_id": "pool-u"})
check("plan valid", out.validation_result.valid, [c.description for c in out.validation_result.conflicts])
check("only the unlinked network is linked", out.dpsk_links_to_make == {"d1": "pool-u"}, out.dpsk_links_to_make)
w = out.unit_mappings["apgroup_101"].input_config["wlans"][0]
check("activation carries the chosen pool", w["dpsk_pool_id"] == "pool-u", w)
w2 = out.unit_mappings["apgroup_102"].input_config["wlans"][0]
check("a linked network keeps its own pool", w2["dpsk_pool_id"] == "pool-x", w2)
check("plan says so", any(a.resource_type == "dpsk_links" for a in out.validation_result.actions))

print("\n2b. Pool as the engine passes it (job options are top-level inputs)")
job = SimpleNamespace(input_data={"activations": ROWS}, options={"dpsk_link_pool_id": "pool-u"})
phase = validate_mod.ValidateWlanActivatorPhase(ctx(FakeNetworks({"d2": "pool-x"})))
out = asyncio.run(phase.execute(phase.Inputs(**{**job.input_data, **job.options})))
check("plan valid via job options", out.validation_result.valid
      and out.dpsk_links_to_make == {"d1": "pool-u"}, [c.description for c in out.validation_result.conflicts])

print("\n3. Unknown pool")
out = plan({"dpsk_link_pool_id": "nope"})
check("unknown pool blocks", not out.validation_result.valid
      and any(c.resource_type == "dpsk_pool" for c in out.validation_result.conflicts))

print("\n4. Workflow order")
deps = {p.id: p.depends_on for p in get_workflow("wlan_activator").phases}
check("link before AP Groups and activation",
      deps["link_dpsk_pools"] == ["validate_wlan_activator"] and deps["create_ap_group"] == ["link_dpsk_pools"], deps)
# The scheduler checks direct dependencies only, and pre-completes create_ap_group for groups
# that exist -- so activation must name the link phase itself.
check("activation depends on the link phase directly", "link_dpsk_pools" in deps["activate_wlans"], deps)

print("\n5. Linking")
nets = FakeNetworks({"d2": "pool-x"})
nets.fail = {"d3"}
phase = LinkDpskPoolsPhase(ctx(nets))
res = asyncio.run(phase.execute(phase.Inputs(dpsk_links_to_make={"d1": "pool-u", "d2": "pool-u", "d3": "pool-u"})))
check("unlinked linked once", nets.links == [("d1", "pool-u")], nets.links)
check("linked-by-now left alone", "d2" not in res.dpsk_links_made and nets.linked["d2"] == "pool-x")
check("failure reported, not raised", list(res.dpsk_links_failed) == ["d3"], res.dpsk_links_failed)
skip = asyncio.run(phase.execute(phase.Inputs()))
check("nothing to link -> skipped", skip.skipped)

print(f"\n{'FAILED: ' + str(FAILS) if FAILS else 'All passed'}\n")
sys.exit(1 if FAILS else 0)
