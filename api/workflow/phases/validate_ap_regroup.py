"""
V2 Phase: Validate & Plan an AP Regroup.

Takes a flat CSV of "ap_identifier,ap_group_name" and works out what has to
happen: which APs exist in the venue, which AP Groups already exist, which
must be created, and which rows match nothing.

The engine's per-unit parallelism is reused with ONE UNIT PER AP GROUP, so
create_ap_group and assign_aps run unchanged from the per-unit SSID tools.
Nothing here is DPSK- or SSID-specific.
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.ap_fields import (
    ap_serial,
    index_aps_by_serial,
    index_aps_by_name,
)
from workflow.v2.models import (
    UnitMapping, UnitPlan, UnitResolved, UnitStatus,
    ValidationResult, ValidationSummary, ResourceAction,
)

logger = logging.getLogger(__name__)


class ApRegroupRow(BaseModel):
    """One CSV row: move this AP into this AP Group."""
    ap_identifier: str
    ap_group_name: str


@register_phase("validate_ap_regroup", "Validate & Plan AP Regroup")
class ValidateApRegroupPhase(PhaseExecutor):
    """Resolve the CSV against the venue and build one unit per AP Group."""

    class Inputs(BaseModel):
        ap_assignments: List[Dict[str, Any]] = Field(default_factory=list)
        options: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: ValidationResult
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)
        # Rows whose AP is not in this venue. Reported, never guessed at.
        unmatched_identifiers: List[str] = Field(default_factory=list)
        ap_groups_to_create: List[str] = Field(default_factory=list)
        ap_groups_to_reuse: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        rows = [
            ApRegroupRow(
                ap_identifier=str(r.get('ap_identifier', '')).strip(),
                ap_group_name=str(r.get('ap_group_name', '')).strip(),
            )
            for r in inputs.ap_assignments
            if str(r.get('ap_identifier', '')).strip()
            and str(r.get('ap_group_name', '')).strip()
        ]
        if not rows:
            raise ValueError(
                "No usable rows. Expected CSV lines of "
                "'ap_identifier,ap_group_name'."
            )

        await self.emit(f"Planning {len(rows)} AP assignments...")

        # ---- venue APs -------------------------------------------------
        all_venue_aps: List[Dict[str, Any]] = []
        try:
            resp = await self.r1_client.venues.get_aps_by_tenant_venue(
                self.tenant_id, self.venue_id
            )
            all_venue_aps = resp.get('data', []) or []
        except Exception as e:
            raise RuntimeError(f"Could not list APs in this venue: {e}")
        await self.emit(f"Found {len(all_venue_aps)} APs in the venue")

        # Match on serial OR name, exactly as assign_aps does. The shared
        # readers skip records missing the key, so a serial-less AP is never
        # indexed under "" where a blank identifier would match it.
        by_serial = index_aps_by_serial(all_venue_aps)
        by_name = index_aps_by_name(all_venue_aps)

        # ---- existing AP groups ----------------------------------------
        existing_groups: Dict[str, str] = {}
        try:
            resp = await self.r1_client.venues.query_ap_groups(
                tenant_id=self.tenant_id,
                venue_id=self.venue_id,
                fields=['id', 'name', 'venueId'],
            )
            for g in resp.get('data', []) or []:
                if g.get('name'):
                    existing_groups[g['name']] = g.get('id', '')
        except Exception as e:
            logger.warning(f"Could not list AP groups: {e}")
        await self.emit(f"Found {len(existing_groups)} existing AP Groups")

        # ---- resolve every row -----------------------------------------
        by_group: Dict[str, List[str]] = defaultdict(list)
        unmatched: List[str] = []
        seen_ap: Dict[str, str] = {}   # identifier -> group, to catch duplicates
        duplicates: List[str] = []

        for row in rows:
            ap = by_serial.get(row.ap_identifier) or by_name.get(row.ap_identifier)
            if not ap:
                unmatched.append(row.ap_identifier)
                continue
            serial = ap_serial(ap)
            if not serial:
                unmatched.append(row.ap_identifier)
                continue
            if serial in seen_ap and seen_ap[serial] != row.ap_group_name:
                # An AP belongs to exactly one group; last row would silently win.
                duplicates.append(
                    f"{row.ap_identifier} -> {seen_ap[serial]} / {row.ap_group_name}"
                )
            seen_ap[serial] = row.ap_group_name
            by_group[row.ap_group_name].append(serial)

        if unmatched:
            sample = ", ".join(unmatched[:5])
            await self.emit(
                f"{len(unmatched)} row(s) match no AP in this venue "
                f"({sample}{'...' if len(unmatched) > 5 else ''}). They are "
                f"skipped; everything else still runs.",
                "warning",
            )
        if duplicates:
            await self.emit(
                f"{len(duplicates)} AP(s) appear more than once with different "
                f"AP Groups ({'; '.join(duplicates[:3])}). An AP belongs to one "
                f"group — the LAST row wins.",
                "warning",
            )

        # ---- one unit per AP Group -------------------------------------
        unit_mappings: Dict[str, UnitMapping] = {}
        to_create: List[str] = []
        to_reuse: List[str] = []
        actions: List[ResourceAction] = []

        for group_name in sorted(by_group):
            serials = by_group[group_name]
            group_id = existing_groups.get(group_name)
            (to_reuse if group_id else to_create).append(group_name)

            unit_id = f"apgroup_{group_name}"
            unit_mappings[unit_id] = UnitMapping(
                unit_id=unit_id,
                unit_number=group_name,
                plan=UnitPlan(
                    ap_group_name=group_name,
                    ap_group_exists=bool(group_id),
                    will_create_ap_group=not group_id,
                    ap_serial_numbers=serials,
                    will_create_network=False,
                ),
                resolved=UnitResolved(ap_group_id=group_id),
                status=UnitStatus.PENDING,
                input_config={'ap_group_name': group_name},
            )

        if to_create:
            actions.append(ResourceAction(
                action="create", resource_type="ap_groups",
                name=f"{len(to_create)} AP Groups",
                notes=[", ".join(sorted(to_create)[:8])],
            ))
        if to_reuse:
            actions.append(ResourceAction(
                action="reuse", resource_type="ap_groups",
                name=f"{len(to_reuse)} AP Groups",
            ))
        actions.append(ResourceAction(
            action="update", resource_type="access_points",
            name=f"{len(seen_ap)} APs moved",
            notes=(
                [f"{len(unmatched)} rows matched no AP and are skipped"]
                if unmatched else []
            ),
        ))

        await self.emit(
            f"Plan: {len(seen_ap)} APs into {len(unit_mappings)} AP Groups "
            f"({len(to_create)} new, {len(to_reuse)} existing)"
            + (f", {len(unmatched)} unmatched" if unmatched else ""),
            "success",
        )

        return self.Outputs(
            unit_mappings=unit_mappings,
            all_venue_aps=all_venue_aps,
            unmatched_identifiers=unmatched,
            ap_groups_to_create=to_create,
            ap_groups_to_reuse=to_reuse,
            validation_result=ValidationResult(
                valid=bool(unit_mappings),
                summary=ValidationSummary(
                    total_units=len(unit_mappings),
                    ap_groups_to_create=len(to_create),
                    ap_groups_to_reuse=len(to_reuse),
                    total_api_calls=len(to_create) + len(seen_ap),
                ),
                actions=actions,
            ),
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(
            valid=True,
            will_create=bool(inputs.ap_assignments),
            estimated_api_calls=2,
            notes=["Resolve APs and AP Groups against the venue"],
        )
