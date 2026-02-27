"""
SZ → R1 Migration: Phase 0 — Validate & Plan

Reads the SZ snapshot from Redis, runs the WLAN Group resolver and security
type mapper, inventories the R1 venue, and builds per-WLAN unit mappings.

This is the "dry-run" phase — no writes to R1. The Brain will pause the
workflow after this phase for user confirmation.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from workflow.v2.models import (
    UnitMapping, UnitPlan, UnitResolved, UnitStatus,
    ValidationResult, ValidationSummary, ResourceAction,
)

from redis_client import get_redis_client
from schemas.sz_migration import SZMigrationSnapshot
from schemas.r1_inventory import R1VenueInventory
from services.sz_migration.resolver import resolve_wlan_activations
from services.sz_migration.mapper import map_all_wlans, R1NetworkTypeMapping
from services.sz_migration.field_mappings import build_r1_advanced_settings
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

        for wlan in snapshot.wlans:
            wlan_id = wlan.id
            mapping = type_mappings.get(wlan_id)
            r1_type = mapping.r1_network_type if mapping else "unsupported"

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
            r1_advanced = build_r1_advanced_settings(wlan.raw) if wlan.raw else {}

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
                    "notes": mapping.notes if mapping else "",
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

        # ── Build validation result ───────────────────────────────────
        # Estimate API calls
        aaa_count = sum(
            1 for m in type_mappings.values() if m.r1_network_type == "aaa"
        )
        dpsk_count = sum(
            1 for m in type_mappings.values() if m.r1_network_type == "dpsk"
        )
        total_api_calls = (
            will_create  # network creation
            + aaa_count  # RADIUS profile creation (max, may reuse)
            + dpsk_count * 2  # identity group + DPSK pool
            + len(resolver_result.activations)  # SSID activations
        )

        validation_result = ValidationResult(
            valid=True,
            conflicts=[],
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
