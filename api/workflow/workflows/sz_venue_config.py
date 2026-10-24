"""
SZ → R1 migration, stage: Venue Configuration

Carries everything the SZ zone sets onto the R1 venue -- channel plans and
radio per band, management VLAN, mesh, balancing, admission control, AP
behaviours, and the zone's per-AP-model settings (PoE, LAN ports, LEDs,
external antennas, band mode) -- then reads it all back and checks R1 holds
what the zone says.

Its own job, before AP Groups and WLANs: the venue is set up and verified
before anything lands on it.

    venue_config_plan (global)    read zone + R1, list what changes, pause
      → apply_venue_settings      write each chosen section (non-critical:
                                  a refused section doesn't stop the rest)
      → verify_venue_settings     read back; anything R1 doesn't hold fails
                                  the job, field by field
"""

from workflow.workflows.definition import Workflow, Phase


SZVenueConfigWorkflow = Workflow(
    name="sz_venue_config",
    description="Apply the SmartZone zone's settings to the R1 venue and verify them",
    requires_confirmation=True,
    default_options={},
    phases=[
        Phase(
            id="venue_config_plan",
            name="Plan Venue Configuration",
            description="Read the zone's settings and the venue's current ones; list what will change.",
            executor="workflow.phases.sz_migration.venue_config.VenueConfigPlanPhase",
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["validation_result"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="apply_venue_settings",
            name="Apply Venue Settings",
            description="Write each chosen section of zone settings onto the venue.",
            executor="workflow.phases.sz_migration.apply_venue_settings.ApplyVenueSettingsPhase",
            depends_on=["venue_config_plan"],
            per_unit=False,
            critical=False,
            inputs=["sz_snapshot_job_id"],
            outputs=["results"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="verify_venue_settings",
            name="Verify Venue Settings",
            description="Read the venue back and check R1 holds every value the zone sets.",
            executor="workflow.phases.sz_migration.venue_config.VerifyVenueSettingsPhase",
            depends_on=["apply_venue_settings"],
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["results"],
            api_calls_per_unit=0,
        ),
    ],
)
