"""
WLAN Grabber Workflow (standalone)

Create in R1 the SZ WLANs of one zone that the tenant does not have yet.
Networks are tenant-wide and left unactivated -- WLAN Activator does that.

ONE UNIT PER WLAN. The plan endpoint reads SmartZone (phases get no SZ
client) and hands each WLAN's spec over; secrets go to their own Redis hash
(workflow/phases/wlan_grabber.py).

Flow:
    validate_wlan_grabber (global)
        └── grabber_radius_profiles (global)    skipped with no Enterprise WLAN
                └── grabber_dpsk_pool (global)  skipped with no shared-pool WLAN
                        └── grabber_create_network (per WLAN)

The shared pool and the RADIUS profiles exist before any network does, so
every network can link them as it is created.
"""

from workflow.workflows.definition import Workflow, Phase


WlanGrabberWorkflow = Workflow(
    name="wlan_grabber",
    description="Create in R1 the SmartZone WLANs it does not have yet (unactivated).",
    requires_confirmation=True,
    default_options={"add_description": False},
    phases=[
        Phase(
            id="validate_wlan_grabber",
            name="Validate & Plan WLAN Grabber",
            description="Re-check each WLAN against the tenant's networks; one unit per WLAN to create.",
            executor="validate_wlan_grabber",
            per_unit=False,
            critical=True,
            inputs=["wlans", "radius", "dpsk_pool", "wispr"],
            outputs=["unit_mappings", "validation_result", "networks_existing", "wlans_skipped"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="grabber_radius_profiles",
            name="Create RADIUS Profiles",
            description="Create or link the R1 RADIUS profiles the Enterprise WLANs use.",
            executor="grabber_radius_profiles",
            depends_on=["validate_wlan_grabber"],
            per_unit=False,
            critical=True,
            inputs=["radius"],
            outputs=["radius_profile_mappings", "radius_errors"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="grabber_dpsk_pool",
            name="Create Shared DPSK Pool",
            description="Create (or find) the DPSK pool and identity group shared by the DPSK networks.",
            executor="grabber_dpsk_pool",
            depends_on=["grabber_radius_profiles"],
            per_unit=False,
            critical=True,
            inputs=["dpsk_pool"],
            outputs=["shared_dpsk_pool_id", "shared_dpsk_pool_error"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="grabber_create_network",
            name="Create WiFi Networks",
            description="Create each network, then link its RADIUS profile(s) or DPSK pool.",
            executor="grabber_create_network",
            depends_on=["grabber_dpsk_pool"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "spec", "dpsk_pool_choice"],
            outputs=["network_id", "created", "networks_created"],
            api_calls_per_unit=3,
            effect_field="networks_created",
            effect_label="Networks created",
            effect_agg="sum",
        ),
    ],
)
