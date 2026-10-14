"""
SZ → R1 Migration: Phase 0 — Validate & Plan

Reads the SZ snapshot from Redis, runs the WLAN Group resolver and security
type mapper, inventories the R1 venue, and builds per-WLAN unit mappings.

This is the "dry-run" phase — no writes to R1. The Brain will pause the
workflow after this phase for user confirmation.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.v2.models import (
    UnitMapping, UnitPlan, UnitResolved, UnitStatus,
    ValidationResult, ValidationSummary, ResourceAction, ConflictDetail,
)

from redis_client import get_redis_client
from schemas.sz_migration import SZMigrationSnapshot
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.mapper import map_all_wlans, R1NetworkTypeMapping
from services.sz_migration.field_mappings import build_r1_advanced_settings
from services.sz_migration.radius_choices import referenced_services, r1_profile_summary
from services.sz_migration.dpsk_choices import load_r1_dpsk, validate_dpsk_plan
from services.r1_inventory import capture_venue_inventory

logger = logging.getLogger(__name__)


@register_phase("sz_validate_and_plan", "SZ Migration: Validate & Plan")
class ValidateMigrationPhase(PhaseExecutor):
    """
    Phase 0: Read snapshots, resolve, map, build plan.

    Outputs unit_mappings (one per WLAN) and validation_result for the Brain.
    """

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = {}
        validation_result: Optional[ValidationResult] = None
        resolver_result: Dict[str, Any] = {}
        type_mappings: Dict[str, Any] = {}
        r1_inventory_summary: Dict[str, Any] = {}

    async def execute(self, inputs: Inputs) -> Outputs:
        # ── Load SZ snapshot from Redis ───────────────────────────────
        await self.emit("Loading SZ snapshot...")
        redis = await get_redis_client()
        snapshot_key = f"sz_migration:extraction:{inputs.sz_snapshot_job_id}:snapshot"
        raw = await redis.get(snapshot_key)
        if not raw:
            raise RuntimeError(
                f"SZ snapshot not found for job {inputs.sz_snapshot_job_id}. "
                f"Run extraction first."
            )
        snapshot = SZMigrationSnapshot.model_validate_json(raw)
        await self.emit(
            f"SZ snapshot loaded: {snapshot.zone.name} — "
            f"{len(snapshot.wlans)} WLANs, {len(snapshot.ap_groups)} AP Groups"
        )

        # ── Capture R1 venue inventory ────────────────────────────────
        await self.emit("Capturing R1 venue inventory...")
        r1_inventory = await capture_venue_inventory(
            self.r1_client,
            self.tenant_id,
            self.venue_id,
        )
        await self.emit(
            f"R1 inventory: {len(r1_inventory.wifi_networks)} networks, "
            f"{len(r1_inventory.ap_groups)} AP groups, {len(r1_inventory.aps)} APs"
        )

        # ── Run resolver ──────────────────────────────────────────────
        await self.emit("Resolving WLAN Groups...")
        resolver_result = resolve_wlan_activations(snapshot)
        await self.emit(
            f"Resolver: {len(resolver_result.activations)} activations, "
            f"blocked={resolver_result.blocked}"
        )

        if resolver_result.blocked:
            return self.Outputs(
                validation_result=ValidationResult(
                    valid=False,
                    conflicts=[],
                    summary=ValidationSummary(
                        total_units=0,
                        will_create=0,
                        will_reuse=0,
                        total_api_calls=0,
                    ),
                    unit_plans={},
                    actions=[],
                ),
                resolver_result=resolver_result.model_dump(),
            )

        # ── Run security mapper ───────────────────────────────────────
        await self.emit("Mapping security types...")
        type_mappings = map_all_wlans(snapshot.wlans)

        # ── Build per-WLAN unit mappings ──────────────────────────────
        await self.emit("Building migration plan...")
        unit_mappings: Dict[str, UnitMapping] = {}
        actions: List[ResourceAction] = []
        will_create = 0
        will_reuse = 0
        # SZ settings R1 cannot take: shown on Review as warnings, never sent
        setting_warnings: List[ConflictDetail] = []

        # ── RADIUS choices made on Review ─────────────────────────────
        radius_conflicts, radius_actions, skipped_services = await self._apply_radius_decisions(
            snapshot
        )
        actions.extend(radius_actions)
        fallbacks: Dict[str, str] = self.context.options.get("wlan_fallbacks") or {}
        wlan_overrides: Dict[str, Dict[str, Any]] = {}

        for wlan in snapshot.wlans:
            wlan_id = wlan.id
            mapping = type_mappings.get(wlan_id)
            r1_type = mapping.r1_network_type if mapping else "unsupported"
            notes = mapping.notes if mapping else ""

            # Authentication service not migrated: the WLAN is skipped or
            # becomes an R1 DPSK network, per the choice made for it.
            if wlan.auth_service_id and wlan.auth_service_id in skipped_services:
                fallback = fallbacks.get(wlan_id)
                if fallback == "skip":
                    actions.append(ResourceAction(
                        resource_type="wifi_network", name=wlan.name, action="skip",
                        notes=["RADIUS service not migrated; WLAN skipped by choice"],
                    ))
                    wlan_overrides[wlan_id] = {"r1_network_type": "skipped",
                                               "notes": "Skipped: its RADIUS service is not migrated"}
                    continue
                if fallback == "dpsk":
                    r1_type = "dpsk"
                    notes = "Converted to R1 DPSK: its RADIUS service is not migrated"
                    wlan_overrides[wlan_id] = {"r1_network_type": "dpsk", "notes": notes}
                else:
                    radius_conflicts.append(ConflictDetail(
                        unit_id=wlan_id,
                        resource_type="wifi_network",
                        resource_name=wlan.name,
                        description=(
                            f"{wlan.name} — its RADIUS service is set to 'Don't migrate'; "
                            "choose whether to skip this WLAN or convert it to R1 DPSK"
                        ),
                        severity="error",
                    ))
                    continue

            # Check if R1 network already exists (by name)
            existing_network = r1_inventory.network_by_name(wlan.name)

            # Collect activations for this WLAN and map SZ AP Group names → R1 IDs
            wlan_activations = []
            for a in resolver_result.activations:
                if a.wlan_id != wlan_id:
                    continue
                act = a.model_dump()
                # Resolve SZ AP Group name to R1 AP Group ID
                r1_apg = r1_inventory.ap_group_by_name(a.ap_group_name)
                if r1_apg:
                    act["r1_ap_group_id"] = r1_apg.get("id")
                    act["r1_ap_group_name"] = r1_apg.get("name", a.ap_group_name)
                wlan_activations.append(act)

            # Pre-compute R1 advanced settings from SZ raw data
            dropped: List[Dict[str, Any]] = []
            r1_advanced = build_r1_advanced_settings(wlan.raw, dropped) if wlan.raw else {}
            for d in dropped:
                setting_warnings.append(ConflictDetail(
                    unit_id=wlan_id,
                    resource_type="wifi_network_setting",
                    resource_name=wlan.name,
                    description=f"{wlan.name} — {d['label']}: {d['reason']}",
                    severity="warning",
                ))

            # Build plan
            plan = UnitPlan(
                extra={
                    "sz_wlan_id": wlan.id,
                    "sz_wlan_name": wlan.name,
                    "ssid": wlan.ssid,
                    "sz_auth_type": wlan.auth_type,
                    "r1_network_type": r1_type,
                    "activations": wlan_activations,
                    "needs_user_decision": mapping.needs_user_decision if mapping else False,
                    "dpsk_type": mapping.dpsk_type if mapping else None,
                    "notes": notes,
                    "vlan_id": wlan.vlan_id or 1,
                    # SZ encryption details for PSK passphrase
                    "encryption": wlan.encryption,
                    "dpsk_config": wlan.dpsk,
                    # Reference IDs for RADIUS
                    "auth_service_id": wlan.auth_service_id,
                    "accounting_service_id": wlan.accounting_service_id,
                    # Pre-computed R1 advanced settings (MFP, rate limits, radio, etc.)
                    "r1_advanced_settings": r1_advanced,
                },
            )

            # Build resolved (pre-populate if network already exists)
            resolved = UnitResolved(
                extra={},
            )
            if existing_network:
                resolved.network_id = existing_network.get("id")
                resolved.extra["network_reused"] = True
                will_reuse += 1
                actions.append(ResourceAction(
                    resource_type="wifi_network",
                    name=wlan.name,
                    action="reuse",
                    existing_id=existing_network.get("id"),
                ))
            else:
                will_create += 1
                actions.append(ResourceAction(
                    resource_type="wifi_network",
                    name=wlan.name,
                    action="create",
                ))

            unit_mappings[wlan_id] = UnitMapping(
                unit_id=wlan_id,
                unit_number=wlan.name,
                plan=plan,
                resolved=resolved,
                status=UnitStatus.PENDING,
                completed_phases=[],
                failed_phases=[],
                phase_errors={},
                input_config={
                    "sz_wlan_id": wlan.id,
                    "wlan_name": wlan.name,
                    "ssid": wlan.ssid,
                    "r1_network_type": r1_type,
                },
            )

        # ── DPSK services chosen on Review ────────────────────────────
        # Runs after the loop: which WLANs end up DPSK depends on the RADIUS
        # fallbacks applied above. None = an older client; configure_dpsk then
        # keeps its per-WLAN {wlan}-pool behaviour.
        dpsk_conflicts: List[ConflictDetail] = []
        dpsk_pools = self.context.options.get("dpsk_pools")
        if dpsk_pools is not None:
            assignments = self.context.options.get("dpsk_assignments") or {}
            dpsk_wlans = {
                uid: u.plan.extra.get("sz_wlan_name", uid)
                for uid, u in unit_mappings.items()
                if u.plan.extra.get("r1_network_type") == "dpsk"
            }
            if dpsk_wlans:
                r1_pools, r1_groups = await load_r1_dpsk(self.r1_client, self.tenant_id)
                dpsk_conflicts, dpsk_actions = validate_dpsk_plan(
                    dpsk_pools, assignments, dpsk_wlans, r1_pools, r1_groups,
                )
                actions.extend(dpsk_actions)
                for uid in dpsk_wlans:
                    unit_mappings[uid].plan.extra["dpsk_pool_key"] = assignments.get(uid)

        # ── Build validation result ───────────────────────────────────
        # Estimate API calls (from the units actually planned)
        planned_types = [u.plan.extra.get("r1_network_type") for u in unit_mappings.values()]
        aaa_count = planned_types.count("aaa")
        dpsk_count = planned_types.count("dpsk")
        total_api_calls = (
            will_create  # network creation
            + aaa_count  # RADIUS profile creation (max, may reuse)
            + dpsk_count * 2  # identity group + DPSK pool
            + len(resolver_result.activations)  # SSID activations
        )

        conflicts = radius_conflicts + dpsk_conflicts + setting_warnings
        validation_result = ValidationResult(
            valid=not any(c.severity == "error" for c in conflicts),
            conflicts=conflicts,
            summary=ValidationSummary(
                total_units=len(unit_mappings),
                networks_to_create=will_create,
                networks_to_reuse=will_reuse,
                total_api_calls=total_api_calls,
            ),
            unit_plans={uid: um.plan for uid, um in unit_mappings.items()},
            actions=actions,
        )

        type_mappings_serializable = {
            wlan_id: {
                "sz_auth_type": m.sz_auth_type,
                "r1_network_type": m.r1_network_type,
                "notes": m.notes,
                "needs_user_decision": m.needs_user_decision,
                "dpsk_type": m.dpsk_type,
                **wlan_overrides.get(wlan_id, {}),
            }
            for wlan_id, m in type_mappings.items()
        }

        await self.emit(
            f"Plan: {len(unit_mappings)} WLANs — "
            f"{will_create} to create, {will_reuse} to reuse, "
            f"~{total_api_calls} API calls",
            "success",
        )

        return self.Outputs(
            unit_mappings=unit_mappings,
            validation_result=validation_result,
            resolver_result=resolver_result.model_dump(),
            type_mappings=type_mappings_serializable,
            r1_inventory_summary=r1_inventory.summary(),
        )

    async def _apply_radius_decisions(self, snapshot: SZMigrationSnapshot):
        """
        Turn the Review step's RADIUS choices into plan actions and conflicts.

        Returns (conflicts, actions, skipped_service_ids). With no decisions in
        the job options (an older client), every referenced service is
        planned as 'create' -- the previous behaviour.
        """
        decisions: Optional[Dict[str, Dict[str, Any]]] = self.context.options.get("radius_decisions")
        services = referenced_services(snapshot)
        conflicts: List[ConflictDetail] = []
        actions: List[ResourceAction] = []
        skipped: set = set()
        if not services:
            return conflicts, actions, skipped

        r1_profiles: Dict[str, Dict[str, Any]] = {}
        # Needed for "existing" (does it still exist?) and "create" (name collision).
        if decisions is None or any(d.get("action") in ("existing", "create") for d in decisions.values()):
            rows = await asyncio.to_thread(
                self.r1_client.radius_profiles.get_radius_profiles, self.tenant_id
            )
            r1_profiles = {p["id"]: r1_profile_summary(p) for p in rows if p.get("id")}

        for s in services:
            d = (decisions or {}).get(s["id"]) if decisions is not None else {"action": "create"}
            users = ", ".join(sorted({u["wlan_name"] for u in s["used_by"]}))
            if not d:
                conflicts.append(ConflictDetail(
                    resource_type="radius_profile", resource_name=s["name"],
                    description=f"RADIUS '{s['name']}' (used by {users}) — no choice made",
                    severity="error",
                ))
                continue

            action = d.get("action")
            if action == "skip":
                skipped.add(s["id"])
                actions.append(ResourceAction(
                    resource_type="radius_profile", name=s["name"], action="skip",
                    notes=[f"Not migrated (used by {users})"],
                ))
            elif action == "existing":
                p = r1_profiles.get(d.get("r1_profile_id"))
                if p is None:
                    conflicts.append(ConflictDetail(
                        resource_type="radius_profile", resource_name=s["name"],
                        description=f"RADIUS '{s['name']}' — the chosen R1 profile no longer exists",
                        severity="error",
                    ))
                    continue
                if p.get("placeholder_secret"):
                    conflicts.append(ConflictDetail(
                        resource_type="radius_profile", resource_name=p["name"],
                        description=(
                            f"R1 profile '{p['name']}' still has the placeholder shared secret — "
                            f"{users} will not authenticate until you set it in R1"
                        ),
                        severity="warning",
                    ))
                actions.append(ResourceAction(
                    resource_type="radius_profile", name=p["name"], action="reuse",
                    existing_id=p["id"],
                    notes=[f"For SZ '{s['name']}' (used by {users})"],
                ))
            elif action == "create":
                same_name = next(
                    (p for p in r1_profiles.values() if p.get("name") == s["name"]), None
                )
                if same_name is not None:
                    # The phase links a same-named profile rather than create a
                    # duplicate, and leaves its secret alone. Say so now.
                    conflicts.append(ConflictDetail(
                        resource_type="radius_profile", resource_name=s["name"],
                        description=(
                            f"R1 already has a profile named '{s['name']}' — Create will link it "
                            "instead, and any secret entered here is NOT applied to it"
                            + (" (it still has the placeholder secret)"
                               if same_name.get("placeholder_secret") else "")
                            + ". Choose Use existing, or delete or rename it in R1 first."
                        ),
                        severity="warning",
                    ))
                if not s.get("primary"):
                    conflicts.append(ConflictDetail(
                        resource_type="radius_profile", resource_name=s["name"],
                        description=(
                            f"RADIUS '{s['name']}' — the SZ snapshot has no server address "
                            "to create it from; use an existing R1 profile or don't migrate it"
                        ),
                        severity="error",
                    ))
                    continue
                notes = [f"{s['primary']} (used by {users})"]
                if not d.get("secret_provided"):
                    conflicts.append(ConflictDetail(
                        resource_type="radius_profile", resource_name=s["name"],
                        description=(
                            f"RADIUS '{s['name']}' will be created with a placeholder shared "
                            "secret — authentication fails until you set it in R1"
                        ),
                        severity="warning",
                    ))
                    notes.append("placeholder shared secret")
                actions.append(ResourceAction(
                    resource_type="radius_profile", name=s["name"], action="create", notes=notes,
                ))
            else:
                conflicts.append(ConflictDetail(
                    resource_type="radius_profile", resource_name=s["name"],
                    description=f"RADIUS '{s['name']}' — unknown choice {action!r}",
                    severity="error",
                ))

        return conflicts, actions, skipped
