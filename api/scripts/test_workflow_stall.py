#!/usr/bin/env python3
"""
Workflow stall detection regression test.

The scheduler used to have no escape from a deadlock:

    if not in_flight:
        # No work in flight and nothing ready - shouldn't happen
        # unless all work is done or blocked
        await asyncio.sleep(SCHEDULE_INTERVAL)
        continue

Only task completions advance state, and there are no tasks, so nothing
could ever change. Worse, the heartbeat sits AFTER this branch, so the
`continue` skipped it: the job stopped reporting alive, the stranded-job
reaper failed it up to 300s later with a generic message, and the loop kept
spinning at 4Hz for the life of the process.

That is the production report -- "stopped after one or two phases, didn't
pick back up, then slept for 300 seconds and failed" -- with no crash
involved at all.

Now the scheduler heartbeats while idle, tries one self-heal, and then fails
with a message naming what it is waiting for.

WHAT THIS GUARDS

  1. _recover_orphaned_units frees units flagged as running a phase that
     nothing is actually running. That flag makes _find_ready_work skip the
     unit forever, so one stuck flag can halt the whole workflow.
  2. _diagnose_stall names the blocked phases and their unmet dependencies,
     aggregated -- 400 units blocked the same way must read as one line.
  3. The idle branch heartbeats. If the heartbeat ever moves back below the
     `continue`, a stall goes silent again and the reaper handles it 300s
     later. Checked against the source, since that is a layout property.

Usage:
    docker compose exec backend python scripts/test_workflow_stall.py

Exits non-zero on failure.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow.v2.brain import WorkflowBrain, WorkflowStalled, STALL_GRACE_SECONDS
from workflow.v2.graph import DependencyGraph
from workflow.v2.models import UnitStatus


class FakeUnit:
    def __init__(self, number, status=UnitStatus.PENDING, current=None,
                 completed=(), failed=()):
        self.unit_id = f"u{number}"
        self.unit_number = str(number)
        self.status = status
        self.current_phase = current
        self.completed_phases = list(completed)
        self.failed_phases = list(failed)


class FakePhaseDef:
    def __init__(self, pid, per_unit, depends_on=()):
        self.id = pid
        self.per_unit = per_unit
        self.depends_on = list(depends_on)


class FakeJob:
    def __init__(self, phase_definitions, units, settled=()):
        self.id = "job-1"
        self.phase_definitions = phase_definitions
        self.units = {u.unit_id: u for u in units}
        self._settled = set(settled)


class FakeState:
    def __init__(self):
        self.saved = []

    async def save_unit(self, job_id, unit):
        self.saved.append(unit.unit_number)
        return True


def make_brain(job):
    brain = WorkflowBrain.__new__(WorkflowBrain)
    brain.state = FakeState()
    # The real one reads job.global_phase_status; the fixture carries the
    # answer directly so the test stays about stall handling.
    brain._settled_global_phases = lambda j: set(j._settled)
    return brain


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


PHASES = [
    FakePhaseDef("validate", per_unit=False),
    FakePhaseDef("create_shared", per_unit=False, depends_on=["validate"]),
    FakePhaseDef("create_network", per_unit=True, depends_on=["create_shared"]),
    FakePhaseDef("assign_aps", per_unit=True, depends_on=["create_network"]),
]


async def main() -> int:
    failures = 0
    print("Workflow stall handling\n")
    graph = DependencyGraph(PHASES)

    # 1. orphaned units are freed
    units = [
        FakeUnit(101, UnitStatus.RUNNING, current="create_network"),
        FakeUnit(102, UnitStatus.RUNNING, current="assign_aps"),
        FakeUnit(103, UnitStatus.COMPLETED, completed=["create_network", "assign_aps"]),
    ]
    job = FakeJob(PHASES, units, settled={"validate", "create_shared"})
    brain = make_brain(job)
    freed = await brain._recover_orphaned_units(job)
    failures += check(
        "orphaned units are freed and persisted",
        freed == 2
        and units[0].current_phase is None
        and units[0].status == UnitStatus.PENDING
        and units[2].current_phase is None
        and brain.state.saved == ["101", "102"],
        f"freed={freed}, saved={brain.state.saved}",
    )

    # 2. a completed unit is not disturbed
    failures += check(
        "a finished unit is left alone",
        units[2].status == UnitStatus.COMPLETED,
    )

    # 3. the diagnostic names the unmet dependency
    units = [FakeUnit(n) for n in range(101, 105)]
    job = FakeJob(PHASES, units, settled={"validate"})
    msg = make_brain(job)._diagnose_stall(job, graph)
    failures += check(
        "diagnostic names the blocked global phase and its unmet dep",
        "create_shared" in msg and "validate" not in msg.split("waiting on")[0].split(";")[-1],
        msg,
    )
    failures += check(
        "per-unit blockage is aggregated, not one line per unit",
        "4 unit(s)" in msg and msg.count("unit phase") == 1,
        msg,
    )

    # 4. units stuck mid-phase are called out by name
    units = [FakeUnit(101, UnitStatus.RUNNING, current="assign_aps")]
    job = FakeJob(PHASES, units, settled={"validate", "create_shared"})
    msg = make_brain(job)._diagnose_stall(job, graph)
    failures += check(
        "units flagged running with no task are reported",
        "flagged as running 'assign_aps' with no task" in msg,
        msg,
    )

    # 5. the exception carries the diagnosis, not just "stalled"
    failures += check(
        "WorkflowStalled is an Exception, so the brain marks the job FAILED",
        issubclass(WorkflowStalled, Exception)
        and not issubclass(WorkflowStalled, asyncio.CancelledError),
    )

    # 6. layout: the idle branch must heartbeat before it loops
    src = (Path(__file__).parent.parent / "workflow" / "v2" / "brain.py").read_text()
    branch = re.search(
        r"\n                if not in_flight:\n(.*?)\n                    continue\n",
        src, re.S,
    )
    body = branch.group(1) if branch else ""
    failures += check(
        "the idle branch heartbeats (or the reaper handles stalls again)",
        bool(branch) and "touch_job_heartbeat" in body,
    )
    failures += check(
        "the idle branch can fail the job instead of spinning forever",
        "WorkflowStalled" in body,
    )
    failures += check(
        f"stall grace ({STALL_GRACE_SECONDS}s) stays well under the 300s reaper",
        0 < STALL_GRACE_SECONDS < 120,
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
