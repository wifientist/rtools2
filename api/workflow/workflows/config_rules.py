"""
Config Rules Workflow (standalone, read-only)

Capture what a company's rules need from the chosen venues of one tenant. The
report evaluates the capture afterwards (services/config_rules.py), so nothing
here decides pass or fail, and nothing writes to R1.

ONE UNIT PER VENUE.

Flow:
    validate_config_rules (global)      the chosen venues, one unit each
        └── config_rules_wlans (global) WLANs are tenant-wide: each read once
                └── config_rules_venue (per venue)

Nothing to confirm, so the router runs the job straight on from validation.
"""

from workflow.workflows.definition import Workflow, Phase


ConfigRulesWorkflow = Workflow(
    name="config_rules",
    description="Read venue, AP Group and WLAN settings to check against a company's rules.",
    requires_confirmation=False,
    phases=[
        Phase(
            id="validate_config_rules",
            name="Find Venues",
            description="Confirm the chosen venues are in this tenant; one unit per venue.",
            executor="validate_config_rules",
            per_unit=False,
            critical=True,
            inputs=["venue_ids", "sections"],
            outputs=["unit_mappings", "validation_result"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="config_rules_wlans",
            name="Read WLANs",
            description="Read each WLAN active on a chosen venue, once.",
            executor="config_rules_wlans",
            depends_on=["validate_config_rules"],
            per_unit=False,
            critical=False,
            inputs=["venue_ids", "sections"],
            outputs=["wlans_read"],
            api_calls_per_unit=0,
        ),
        Phase(
            id="config_rules_venue",
            name="Read Venues",
            description="Read the venue and AP Group settings the rules use.",
            executor="config_rules_venue",
            depends_on=["config_rules_wlans"],
            per_unit=True,
            critical=False,
            inputs=["unit_id", "unit_number", "venue_id", "venue_name", "sections"],
            outputs=["venues_read"],
            api_calls_per_unit=8,
            effect_field="venues_read",
            effect_label="Venues read",
            effect_agg="sum",
        ),
    ],
)
