#!/usr/bin/env python3
"""
Re-run skip regression test.

A second import of an unchanged property was doing nearly all the work again.
Some phases skipped correctly -- "No passphrases to create", "No identities to
update" -- but two did not, for two different reasons:

  create_dpsk_network  recognised the SSID ("already exists") and then issued
                       the DPSK link PUT anyway. Deliberate, and it had no
                       choice: the SSID lookup asks for
                       ['id','name','ssid','vlan','nwSubType','venueApGroups']
                       and R1 exposes no DPSK-service field there, so the
                       phase could not see the link. Each re-link costs an
                       activity slot and an AP config apply.

  activate_ap_group    has the skip -- "if inputs.already_activated ... return
                       already_active=True" -- but nothing ever set the flag
                       on the Cloudpath path. already_activated was assigned
                       only in validate_psk.py, so for Cloudpath it was always
                       False and every unit was re-activated: three R1 calls
                       and an AP config apply each.

And the Brain's pre-completion matched PSK-era phase ids only
(create_psk_network, activate_network), so it could never fire for the
Cloudpath phases, which are named create_dpsk_network and activate_ap_group.

WHAT THIS GUARDS

  1. An already-linked DPSK service is detected by GET
     /wifiNetworks/{id}/dpskServices, and the link PUT is NOT re-issued.
  2. A network that is NOT linked yet still gets linked.
  3. If that read fails, the phase links anyway -- a missed link is far worse
     than a redundant one.
  4. The Brain pre-completes activate_ap_group when a unit is already bound to
     its AP Group, and leaves venue-wide units alone (they still need the
     3-step config).

Usage:
    docker compose exec backend python scripts/test_rerun_skips.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.phases.create_dpsk_network import CreateDPSKNetworkPhase
from workflow.v2.brain import WorkflowBrain
from workflow.v2.models import UnitStatus

POOL = "pool-abc"
NETWORK = "net-1"


class FakeNetworks:
    def __init__(self, calls, linked_ids, read_fails=False):
        self.calls, self.linked_ids, self.read_fails = calls, linked_ids, read_fails

    async def get_dpsk_services_on_network(self, network_id, tenant_id=None):
        self.calls.append("get_dpsk_services_on_network")
        if self.read_fails:
            raise RuntimeError("R1 said no")
        return {"data": [{"id": i, "name": i} for i in self.linked_ids]}

    async def activate_dpsk_service_on_network(self, **kw):
        self.calls.append("activate_dpsk_service_on_network")   # the PUT
        return {"requestId": None}


class FakeR1:
    def __init__(self, calls, linked_ids, read_fails=False):
        self.networks = FakeNetworks(calls, linked_ids, read_fails)


class FakeCtx:
    activity_tracker = None


def make_net_phase(calls, linked_ids, read_fails=False):
    phase = CreateDPSKNetworkPhase.__new__(CreateDPSKNetworkPhase)
    phase.r1_client = FakeR1(calls, linked_ids, read_fails)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"
    phase.context = FakeCtx()

    async def noop(*a, **kw):
        return None

    phase.emit = noop
    return phase


class FakeResolved:
    def __init__(self):
        self.ap_group_id = "apg-1"
        self.network_id = None
        self.activated = False
        self.already_active = False


class FakePlan:
    ap_serial_numbers = ["SER1"]


class FakeUnit:
    def __init__(self, **cfg):
        self.unit_number = cfg.get("unit_number", "101")
        self.resolved = FakeResolved()
        self.plan = FakePlan()
        self.input_config = cfg
        self.completed_phases = []
        self.status = UnitStatus.PENDING


class FakeState:
    """Pre-completion persists each unit it marks; record the calls."""

    def __init__(self):
        self.saved = []

    async def save_unit(self, job_id, unit):
        self.saved.append(unit.unit_number)
        return True

    async def save_job(self, job):
        return True


class FakeJob:
    def __init__(self, units):
        self.id = "job-1"
        self.units = {f"u{i}": u for i, u in enumerate(units)}
        self.options = {}

    def get_progress(self):
        return {}


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("Re-run skips\n")

    class Inputs:
        unit_number = "101"
        dpsk_pool_id = POOL

    # 1. already linked -> read, no write
    calls = []
    await make_net_phase(calls, [POOL])._link_dpsk_service(NETWORK, Inputs())
    failures += check(
        "an already-linked DPSK service is not re-linked",
        calls == ["get_dpsk_services_on_network"], f"calls={calls}",
    )

    # 2. not linked -> link it
    calls = []
    await make_net_phase(calls, ["some-other-pool"])._link_dpsk_service(NETWORK, Inputs())
    failures += check(
        "a network that is not linked yet still gets linked",
        "activate_dpsk_service_on_network" in calls, f"calls={calls}",
    )

    # 3. read fails -> link anyway
    calls = []
    await make_net_phase(calls, [POOL], read_fails=True)._link_dpsk_service(NETWORK, Inputs())
    failures += check(
        "if the check fails, it links anyway rather than skipping",
        "activate_dpsk_service_on_network" in calls, f"calls={calls}",
    )

    # 4. the Brain pre-completes activate_ap_group
    brain = WorkflowBrain.__new__(WorkflowBrain)

    async def noop(*a, **kw):
        return None

    brain._publish_event = noop
    brain.state = FakeState()
    units = [
        FakeUnit(unit_number="101", already_activated=True),                    # skip
        FakeUnit(unit_number="102", already_activated=False),                   # run
        FakeUnit(unit_number="prop", already_activated=True, is_venue_wide=True),  # run
    ]
    job = FakeJob(units)
    await brain._pre_complete_resolved_phases(job)
    done = [("activate_ap_group" in u.completed_phases) for u in units]
    failures += check(
        "already-bound units skip activation; unbound and venue-wide still run",
        done == [True, False, False], f"pre-completed={done}",
    )
    failures += check(
        "the skipped unit carries the outputs activation would have produced",
        units[0].resolved.activated and units[0].resolved.already_active,
    )
    failures += check(
        "the pre-completion is persisted, so a restart does not redo it",
        "101" in brain.state.saved, f"saved={brain.state.saved}",
    )

    # 5. The checks above feed input_config directly, so they cannot see
    # validate dropping the wiring that produces the flag. Read the source.
    validate_src = (
        Path(__file__).parent.parent
        / "workflow" / "phases" / "cloudpath" / "validate.py"
    ).read_text()
    failures += check(
        "cloudpath validate still records which AP Groups are already activated",
        "activated_ap_groups_by_ssid" in validate_src
        and "'already_activated'" in validate_src,
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
