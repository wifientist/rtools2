"""
AP Port Grabber Workflow (standalone)

Give R1 APs the LAN ports and PoE their SZ counterparts have: per-AP
overrides in R1 for what SZ sets per AP or per AP Group, or back onto venue
settings when SZ's result is the venue's.

ONE UNIT PER AP. The plan endpoint reads SmartZone and R1 (phases get no SZ
client) and hands each AP's spec over (services/ap_port_grabber.unit_spec).

Flow:
    validate_ap_port_grabber (global)
        └── grabber_port_profiles (global)     creates the profiles chosen as new
                └── grabber_apply_ap_ports (per AP)

Every profile exists before any AP port is pointed at it.
"""

from workflow.workflows.definition import Workflow, Phase


ApPortGrabberWorkflow = Workflow(
    name="ap_port_grabber",
    description="Give R1 APs the LAN ports and PoE they have on SmartZone.",
    requires_confirmation=True,
    default_options={},
    phases=[
        Phase(
            id="validate_ap_port_grabber",
            name="Validate & Plan AP Port Grabber",
            description="Re-check the profile choices against R1; one unit per AP.",
            executor="validate_ap_port_grabber",
            per_unit=False,
            critical=True,
            inputs=["aps", "profiles"],
            outputs=["unit_mappings", "validation_result", "profiles_resolved"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="grabber_port_profiles",
            name="Create Ethernet Port Profiles",
            description="Create the R1 Ethernet Port Profiles chosen as new; map the rest.",
            executor="grabber_port_profiles",
            depends_on=["validate_ap_port_grabber"],
            per_unit=False,
            critical=True,
            inputs=["profiles_resolved"],
            outputs=["profile_ids", "profile_errors"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="grabber_apply_ap_ports",
            name="Apply AP LAN Ports",
            description="Write each AP's own LAN ports and PoE, or put it back on venue settings.",
            executor="grabber_apply_ap_ports",
            depends_on=["grabber_port_profiles"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "ap", "profile_ids", "profile_errors"],
            outputs=["ports_written", "aps_updated"],
            api_calls_per_unit=6,
            effect_field="aps_updated",
            effect_label="APs updated",
            effect_agg="sum",
        ),
    ],
)
