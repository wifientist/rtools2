"""
SZ → R1 migration, stage: AP Mapping -- the APs, last (and skippable).

Create WLANs has built all configuration (networks, every AP Group, staged
ones included, and activations). This puts each AP in its group -- moving
the ones in the venue, adding the rest by serial when opted in -- then the
audit's per-AP broadcast comparison as the finish line. Re-runnable; each
phase re-plans against the venue as it is now.

    ap_mapping_plan (global)   plan + pause
      → map_aps                move / add APs (non-critical: verify still runs)
      → verify_ap_mapping      read back; anything off fails the job
"""

from workflow.workflows.definition import Workflow, Phase


def _phase(pid, name, description, executor, outputs, depends_on=None, critical=True):
    return Phase(
        id=pid, name=name, description=description,
        executor=f"workflow.phases.sz_migration.ap_mapping.{executor}",
        depends_on=depends_on or [], per_unit=False, critical=critical,
        inputs=["sz_snapshot_job_id"], outputs=outputs, api_calls_per_unit=0,
    )


SZApMappingWorkflow = Workflow(
    name="sz_ap_mapping",
    description="Map SmartZone APs into their R1 AP Groups, then verify every AP's broadcast",
    requires_confirmation=True,
    default_options={},
    phases=[
        _phase("ap_mapping_plan", "Plan AP Mapping",
               "Read the SZ snapshot and the venue; list AP Groups to create, APs to add or move, activations.",
               "ApMappingPlanPhase", ["validation_result", "counts"]),
        _phase("map_aps", "Map APs",
               "Move APs into their AP Group; add APs not in the venue by serial when opted in.",
               "MapApsPhase", ["counts"], ["ap_mapping_plan"], critical=False),
        _phase("verify_ap_mapping", "Verify AP Mapping",
               "Read the venue back: membership, activations, and what every AP broadcasts versus SZ.",
               "VerifyApMappingPhase", ["counts"], ["map_aps"]),
    ],
)
