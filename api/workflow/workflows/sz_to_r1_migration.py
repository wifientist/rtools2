"""
SZ → R1 Full Configuration Migration Workflow

Migrates WiFi networks from SmartZone to RuckusONE, including:
- PSK, Open, Enterprise (AAA), and DPSK networks
- RADIUS server profiles for Enterprise WLANs
- DPSK pool + identity group creation
- SSID activation on per-AP-Group basis

Prerequisites (completed BEFORE workflow starts):
- M0: SZ deep extraction → snapshot stored in Redis
- M2: WLAN Group resolution + security type mapping

Flow:
1. sz_validate_and_plan (global) → Read SZ snapshot, resolve, build unit mappings, pause
2. create_radius_profiles (global) → RADIUS profiles as chosen on Review
2b. create_dpsk_pools (global) → DPSK pools + identity groups as chosen on Review
2c. create_ap_groups (global) → R1 AP Groups for SZ groups with APs and no R1 match
3. create_networks (per-WLAN) → Create/reuse WiFi networks in R1
4. configure_dpsk (per-WLAN, skip if not DPSK) → Create identity groups + DPSK pools
5. activate_ssids (per-WLAN) → Activate SSIDs on appropriate AP Groups

Dependency graph:
    sz_validate_and_plan → create_radius_profiles → create_dpsk_pools → create_ap_groups
        → create_networks → configure_dpsk → activate_ssids

activate_ssids waits for configure_dpsk: R1 refuses to activate a DPSK
network that has no DPSK service yet (WIFI-20300).
"""

from workflow.workflows.definition import Workflow, Phase


SZtoR1MigrationWorkflow = Workflow(
    name="sz_to_r1_migration",
    description="Migrate WiFi networks from SmartZone to RuckusONE",
    requires_confirmation=True,
    default_options={
        "skip_dpsk": False,
        "skip_activation": False,
    },
    phases=[
        # Phase 0: Validate & Plan (global)
        Phase(
            id="sz_validate_and_plan",
            name="Validate & Plan",
            description=(
                "Read SZ snapshot from Redis, run WLAN Group resolver + security mapper, "
                "check existing R1 resources, build per-WLAN unit mappings, "
                "and pause for user confirmation."
            ),
            executor="workflow.phases.sz_migration.validate.ValidateMigrationPhase",
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["unit_mappings", "validation_result", "resolver_result",
                     "type_mappings", "r1_inventory"],
            api_calls_per_unit=0,
        ),

        # Phase 1: Create RADIUS Profiles (global)
        Phase(
            id="create_radius_profiles",
            name="Create RADIUS Profiles",
            description=(
                "Find or create RADIUS server profiles in R1 for Enterprise WLANs. "
                "Skipped if no AAA WLANs in the migration."
            ),
            executor="workflow.phases.sz_migration.create_radius.CreateRadiusProfilesPhase",
            depends_on=["sz_validate_and_plan"],
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["radius_profile_mappings"],
            api_calls_per_unit=0,
        ),

        # Phase 1b: Create DPSK Pools (global)
        Phase(
            id="create_dpsk_pools",
            name="Create DPSK Pools",
            description=(
                "Create the DPSK services (and identity groups) chosen on Review, "
                "once each. Global because several WLANs can share one pool and "
                "per-WLAN units would race to create it. No-op for plans without "
                "DPSK choices."
            ),
            executor="workflow.phases.sz_migration.create_dpsk_pools.CreateDpskPoolsPhase",
            depends_on=["create_radius_profiles"],
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["dpsk_pool_ids"],
            api_calls_per_unit=0,
        ),

        # Phase 1c: Create AP Groups (global)
        Phase(
            id="create_ap_groups",
            name="Create AP Groups",
            description=(
                "Create R1 AP Groups for SZ AP Groups that have APs but no R1 "
                "counterpart, empty, so activations have a target before the "
                "APs arrive. No-op when every group already exists."
            ),
            executor="workflow.phases.sz_migration.create_ap_groups.CreateApGroupsPhase",
            depends_on=["create_dpsk_pools"],
            per_unit=False,
            critical=True,
            inputs=["sz_snapshot_job_id"],
            outputs=["ap_group_ids"],
            api_calls_per_unit=0,
        ),

        # Phase 2: Create Networks (per-WLAN)
        Phase(
            id="create_networks",
            name="Create WiFi Networks",
            description=(
                "Create or reuse WiFi networks in R1 for each WLAN. "
                "Network type (PSK/Open/AAA/DPSK) determined by security mapper."
            ),
            executor="workflow.phases.sz_migration.create_networks.CreateNetworksPhase",
            depends_on=["create_ap_groups"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number"],
            outputs=["network_id"],
            api_calls_per_unit=1,
        ),

        # Phase 3: Configure DPSK (per-WLAN, skip if not DPSK)
        Phase(
            id="configure_dpsk",
            name="Configure DPSK",
            description=(
                "Create identity group and DPSK pool for DPSK WLANs. "
                "Skipped for non-DPSK network types."
            ),
            executor="workflow.phases.sz_migration.configure_dpsk.ConfigureDPSKPhase",
            depends_on=["create_networks"],
            per_unit=True,
            critical=False,
            inputs=["unit_id", "unit_number", "network_id"],
            outputs=["dpsk_pool_id", "identity_group_id"],
            api_calls_per_unit=2,
        ),

        # Phase 4: Activate SSIDs (per-WLAN)
        Phase(
            id="activate_ssids",
            name="Activate SSIDs",
            description=(
                "Activate each WLAN's SSID on the appropriate R1 AP Groups "
                "based on the resolver's activation map."
            ),
            executor="workflow.phases.sz_migration.activate_ssids.ActivateSSIDsPhase",
            depends_on=["configure_dpsk"],
            per_unit=True,
            critical=True,
            inputs=["unit_id", "unit_number", "network_id", "dpsk_pool_id"],
            outputs=["activated"],
            api_calls_per_unit="dynamic",
        ),
    ],
)
