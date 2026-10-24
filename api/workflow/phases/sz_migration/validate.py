"""
SZ → R1 Migration: Phase 0 — Validate & Plan

Reads the SZ snapshot from Redis, runs the WLAN Group resolver and security
type mapper, inventories the R1 venue, and builds per-WLAN unit mappings.

This is the "dry-run" phase — no writes to R1. The Brain will pause the
workflow after this phase for user confirmation.
"""

import asyncio
import json
import re
import logging
from typing import Dict, Any, List, Optional, Tuple
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
from services.sz_migration.mapper import map_all_wlans, R1NetworkTypeMapping
from services.sz_migration.field_mappings import build_r1_advanced_settings
from services.sz_migration.radius_choices import referenced_services, r1_profile_summary
from services.sz_migration.dpsk_choices import load_r1_dpsk, validate_dpsk_plan
from services.sz_migration.r1_layout import build_layout, wlan_activations
from services.wlan_grabber import build_spec as build_wispr_spec, sz_portal_facts
from services.r1_inventory import capture_venue_inventory

logger = logging.getLogger(__name__)

# SZ-mapped R1 types that authenticate against RADIUS and so can become R1 DPSK
CONVERTIBLE = ("dpsk_external", "aaa")


R1_NETWORK_NAME_MAX = 32

# R1's own rules (WIFI-10200 on create):
#   wlan.ssid  [^`\s]([^`\t\r\n]){0,30}[^`\s]   2-32 chars, no backtick, no
#              leading/trailing whitespace, no tab/CR/LF
#   name       2-32 chars, no backtick or "$(", no leading/trailing whitespace
# SZ allows an SSID like "430T " (trailing space): the run failed on it twice
# with a bare "400 Bad Request".
_R1_SSID = re.compile(r"^[^`\s]([^`\t\r\n]){0,30}[^`\s]$")


def _r1_ssid(ssid: str) -> Tuple[Optional[str], str, str]:
    """(R1 SSID or None, error, note). Edge whitespace is trimmed -- R1 won't take it -- and said so."""
    raw = ssid or ""
    fixed = raw.strip()
    if not _R1_SSID.match(fixed):
        why = ("contains a backtick" if "`" in fixed else "contains a tab or line break" if re.search(r"[\t\r\n]", fixed)
               else f"is {len(fixed)} character(s); R1 needs 2-32")
        return None, f"SSID '{raw}' {why} — R1 can't broadcast it; rename it in SZ or skip this WLAN", ""
    if fixed != raw:
        return fixed, "", (f"SZ SSID '{raw}' has a leading/trailing space R1 doesn't allow — it will be "
                           f"'{fixed}'. Clients see that as a different network")
    return fixed, "", ""


def _r1_name_base(name: str) -> str:
    """The SZ name as an R1 network name: edges trimmed, ` and $( removed, 2-32 chars."""
    n = re.sub(r"`|\$\(", "", name or "").strip()[:R1_NETWORK_NAME_MAX].strip()
    return n if len(n) >= 2 else f"{n or 'WLAN'}-net"


def _network_venues(net: Dict[str, Any]) -> set:
    ids = set(net.get("venueIds") or [])
    ids |= {v.get("venueId") for v in net.get("venueApGroups") or [] if v.get("venueId")}
    return ids


def _network_name(inv: R1VenueInventory, name: str, ssid: str, taken: set) -> Tuple[str, Optional[Dict[str, Any]], str]:
    """
    (R1 network name to use, existing network to reuse or None, note).

    Reuse only what is surely ours: same exact name and SSID, activated
    nowhere or only on this venue (an earlier run). Any other clash -- the
    name differing only in case (R1 treats those as the same name), or held
    by another property's network, or with another SSID -- creates this one
    under "<name>_<venue>" (number added if needed); the SSID stays exactly
    as in SZ. taken: names already planned this run (casefolded).
    """
    exact = inv.network_in_tenant(name)
    if exact and (exact.get("ssid") in (None, ssid)) and _network_venues(exact) <= {inv.venue_id}:
        return name, exact, ""
    folded = {(n.get("name") or "").casefold(): n for n in [*inv.wifi_networks, *inv.tenant_networks]}
    clash = exact or folded.get(name.casefold())
    if not clash and name.casefold() not in taken:
        return name, None, ""
    venue = re.sub(r"\s+", "_", (inv.venue_name or "venue").strip())
    for i in range(1, 100):
        suffix = f"_{venue}" if i == 1 else f"_{venue}{i}"
        candidate = name[:max(2, R1_NETWORK_NAME_MAX - len(suffix))] + suffix
        candidate = candidate[:R1_NETWORK_NAME_MAX]
        if candidate.casefold() not in folded and candidate.casefold() not in taken:
            break
    where = sorted(_network_venues(clash)) if clash else []
    held = (f"R1 already has '{clash.get('name')}'" + (" (another property's network)" if where and where != [inv.venue_id] else "")
            if clash else "Another WLAN in this plan has that name")
    return candidate, None, f"{held} — created as '{candidate}'; SSID stays '{ssid}'"


# R1 nwSubType -> the plan's network types, for "reused as a different type".
_R1_SUBTYPE = {"PSK": "psk", "DPSK": "dpsk", "AAA": "aaa", "OPEN": "open", "GUEST": "wispr"}


# R1 caps how many networks one DPSK service can bind (64 on the tenants we've
# hit it on: "Maximum number of DPSK service profile binding networks
# reached"). Past it, configure_dpsk fails per WLAN and those networks are
# never activated -- so the plan says so before anything runs.
DPSK_NETWORKS_PER_POOL = 64


def _dpsk_network_cap(pools: Dict[str, Any], assignments: Dict[str, str], dpsk_wlans: Dict[str, str],
                      r1_pools: Dict[str, Any]) -> List[ConflictDetail]:
    counts: Dict[str, int] = {}
    for uid in dpsk_wlans:
        key = assignments.get(uid)
        if key:
            counts[key] = counts.get(key, 0) + 1
    out: List[ConflictDetail] = []
    for key, n in counts.items():
        spec = pools.get(key) or {}
        name, bound = spec.get("name") or key, 0
        if spec.get("mode") == "existing":
            p = r1_pools.get(spec.get("existing_pool_id") or "") or {}
            name = p.get("name") or name
            bound = p.get("networkCount", p.get("network_count")) or 0
        if bound + n > DPSK_NETWORKS_PER_POOL:
            out.append(ConflictDetail(
                resource_type="dpsk_pool", resource_name=name,
                description=(f"DPSK pool '{name}': {n} WLAN(s) in this plan"
                             + (f" + {bound} network(s) already bound" if bound else "")
                             + f" — over R1's {DPSK_NETWORKS_PER_POOL} networks per DPSK service on many tenants. "
                             "Past the cap, DPSK setup fails and those WLANs aren't activated; split them across "
                             "pools on the DPSK step"),
                severity="warning",
            ))
    return out


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
        # R1 AP Group names create_ap_groups must create (SZ groups with APs
        # and no R1 counterpart).
        ap_groups_to_create: List[str] = []

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

        # ── Placement: All AP Groups vs derived AP Groups ─────────────
        # Same computation the Review step showed, against the inventory
        # just captured (an AP Group may have appeared since).
        await self.emit("Working out R1 placement from SZ broadcast...")
        layout = build_layout(snapshot, r1_inventory, self.context.options.get("layout_choices"))
        placements = wlan_activations(layout)
        ls = layout["summary"]
        await self.emit(
            f"Placement: {ls['all_ap_wlans']} WLAN(s) on All AP Groups, {ls['groups']} AP Group(s) "
            f"({ls['groups_existing']} exist, {ls['groups_to_create']} to create, "
            f"{ls['groups_pending']} not created)"
            + (f"; {ls.get('staged_groups', 0)} staged AP Group(s) for {ls.get('staged_wlans', 0)} WLAN(s) "
               "no AP broadcasts (built, not activated)" if ls.get("staged_groups") else "")
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
        planned_names: set = set()   # R1 network names this plan uses (casefolded)
        # SZ settings R1 cannot take: shown on Review as warnings, never sent
        setting_warnings: List[ConflictDetail] = []

        # ── External auth converted to R1 DPSK on Review ──────────────
        # Only WLANs that authenticate against a RADIUS server can convert:
        # SZ external DPSK (kept external = dpsk_external) and Enterprise (aaa).
        conversions: Dict[str, str] = self.context.options.get("auth_conversions") or {}
        converted = {
            wid for wid, to in conversions.items()
            if to == "dpsk" and getattr(type_mappings.get(wid), "r1_network_type", None) in CONVERTIBLE
        }

        # ── RADIUS choices made on Review ─────────────────────────────
        # Only WLANs that still authenticate against RADIUS in R1 need one:
        # not converted ones, and not e.g. a portal WLAN created as plain PSK.
        radius_users = {
            wid for wid, m in type_mappings.items()
            if m.r1_network_type in CONVERTIBLE and wid not in converted
        }
        # A WISPr guest network authenticates against RADIUS unless SZ's hotspot
        # uses Always Accept (no auth service, or one named so).
        wlan_raw = {w.id: (w.raw or {}) for w in snapshot.wlans}
        radius_users |= {
            wid for wid, m in type_mappings.items()
            if m.r1_network_type == "wispr" and sz_portal_facts(None, wlan_raw.get(wid))["auth_type"] == "RADIUS"
        }
        radius_conflicts, radius_actions, skipped_services = await self._apply_radius_decisions(
            snapshot, radius_users
        )
        actions.extend(radius_actions)
        fallbacks: Dict[str, str] = self.context.options.get("wlan_fallbacks") or {}
        wlan_overrides: Dict[str, Dict[str, Any]] = {}
        activation_count = 0

        # ── R1 AP Groups ──────────────────────────────────────────────
        apg_conflicts: List[ConflictDetail] = [
            ConflictDetail(resource_type="ap_group", resource_name="", description=w["message"],
                           severity=w["severity"])
            for w in layout["warnings"]
        ]
        for g in layout["groups"]:
            if g["exists"]:
                actions.append(ResourceAction(
                    resource_type="ap_group", name=g["name"], action="reuse", existing_id=g["r1_id"],
                    notes=[f"{g['ap_count']} SZ AP(s) belong here"],
                ))
            elif g["create"]:
                actions.append(ResourceAction(
                    resource_type="ap_group", name=g["name"], action="create",
                    notes=["Created empty; APs join it when they are placed"],
                ))
            else:
                actions.append(ResourceAction(
                    resource_type="ap_group", name=g["name"], action="skip",
                    notes=["Not created by choice: its WLANs are not activated for these APs"],
                ))
        # Staged: WLANs no AP broadcasts in SZ, created with their paired AP
        # Group built -- here, with the rest of the configuration -- and never
        # activated. AP Mapping only moves APs into it.
        for g in (x for x in layout["staged"]["groups"] if x["chosen"]):
            actions.append(ResourceAction(
                resource_type="ap_group", name=g["name"], action="reuse" if g["exists"] else "create",
                existing_id=g["r1_id"] if g["exists"] else None,
                notes=[f"Staged for {', '.join(g['ssids'][:3])}: built, not activated",
                       f"{g['ap_count']} AP(s) pair with it ({', '.join(a['name'] for a in g['aps'][:3])})"],
            ))
        for msg in layout["staged"]["conflicts"]:
            apg_conflicts.append(ConflictDetail(resource_type="ap_group", resource_name="staging",
                                                description=msg, severity="warning"))

        for wlan in snapshot.wlans:
            wlan_id = wlan.id
            mapping = type_mappings.get(wlan_id)
            r1_type = mapping.r1_network_type if mapping else "unsupported"
            notes = mapping.notes if mapping else ""

            if wlan_id in converted:
                r1_type = "dpsk"
                notes = (f"Converted to R1 DPSK — SZ {wlan.auth_type} used RADIUS; R1's own DPSK "
                         "service and identities replace it")
                wlan_overrides[wlan_id] = {"r1_network_type": "dpsk", "notes": notes, "converted": True}

            # Authentication service not migrated: the WLAN is skipped or
            # becomes an R1 DPSK network, per the choice made for it. Only for
            # WLANs that would use it in R1.
            elif wlan.auth_service_id and wlan.auth_service_id in skipped_services and wlan_id in radius_users:
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

            # Where it goes in R1. No placement = no AP broadcasts it and it
            # wasn't ticked for migration.
            placement = placements.get(wlan_id)
            if placement is None:
                actions.append(ResourceAction(
                    resource_type="wifi_network", name=wlan.name, action="skip",
                    notes=["No SZ AP broadcasts it; not ticked for migration"],
                ))
                wlan_overrides[wlan_id] = {"r1_network_type": "skipped",
                                           "notes": "Skipped: no AP broadcasts it"}
                continue
            row = next((w for w in layout["wlans"] if w["id"] == wlan_id), {})
            for n in row.get("radio_notes", []):
                setting_warnings.append(ConflictDetail(
                    unit_id=wlan_id, resource_type="wifi_network_activation",
                    resource_name=wlan.name, description=f"{wlan.name} — {n}", severity="warning",
                ))
            if placement.get("skipped_groups"):
                sk = placement["skipped_groups"]
                setting_warnings.append(ConflictDetail(
                    unit_id=wlan_id, resource_type="wifi_network_activation", resource_name=wlan.name,
                    description=(f"{wlan.name} — not activated on {len(sk)} AP Group(s) not being created: "
                                 f"{', '.join(sk[:5])}{'…' if len(sk) > 5 else ''}"),
                    severity="warning",
                ))
            activation_count += 1 if placement["mode"] == "all_ap_groups" else len(placement.get("targets", []))

            # A hotspot WLAN becomes an R1 WISPr guest network built exactly as
            # WLAN Grabber builds it: SZ portal URL / redirect / provider /
            # walled garden / session times / signing key, RADIUS auth.
            wispr_facts = None
            if r1_type == "wispr":
                pid = (wlan_raw.get(wlan_id, {}).get("portalServiceProfile") or {}).get("id")
                ref = snapshot.referenced_objects.get(f"portal_service_profile:{pid}")
                portal = None if ref is None or (ref.raw or {}).get("_error") else ref.raw
                spec = build_wispr_spec(wlan.raw or {}, "wispr", {pid: portal} if portal else {})
                if spec["blockers"]:
                    hint = (" — the extraction couldn't read the SZ hotspot portal; run the extraction again"
                            if portal is None else "")
                    radius_conflicts.append(ConflictDetail(
                        unit_id=wlan_id, resource_type="wifi_network", resource_name=wlan.name,
                        description=f"{wlan.name} (WISPr guest): {'; '.join(spec['blockers'])}{hint}",
                        severity="error"))
                    continue
                for c in spec["cautions"]:
                    setting_warnings.append(ConflictDetail(
                        unit_id=wlan_id, resource_type="wifi_network_setting", resource_name=wlan.name,
                        description=f"{wlan.name} — {c}", severity="warning"))
                wispr_facts = spec["portal"]   # no key: create_networks reads it from the snapshot
                notes = "; ".join(spec["notes"]) or notes

            # The R1 network: reuse ours (an earlier run's -- anywhere in the
            # tenant, activated here or nowhere), else create, under another
            # name when this one is taken by someone else's network.
            r1_ssid, ssid_error, ssid_note = _r1_ssid(wlan.ssid)
            if ssid_error:
                radius_conflicts.append(ConflictDetail(
                    unit_id=wlan_id, resource_type="wifi_network", resource_name=wlan.name,
                    description=f"{wlan.name}: {ssid_error}", severity="error"))
                continue
            if ssid_note:
                setting_warnings.append(ConflictDetail(
                    unit_id=wlan_id, resource_type="wifi_network", resource_name=wlan.name,
                    description=f"{wlan.name}: {ssid_note}", severity="warning"))
            r1_name, existing_network, name_note = _network_name(
                r1_inventory, _r1_name_base(wlan.name), r1_ssid, planned_names)
            planned_names.add(r1_name.casefold())
            if name_note:
                setting_warnings.append(ConflictDetail(
                    unit_id=wlan_id, resource_type="wifi_network", resource_name=wlan.name,
                    description=f"{wlan.name}: {name_note}", severity="warning"))

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
                    "r1_network_name": r1_name,
                    "wispr": wispr_facts,
                    # SZ's own description, copied as is; none when SZ has none
                    "sz_description": (wlan.raw or {}).get("description") or None,
                    "ssid": r1_ssid,
                    "sz_auth_type": wlan.auth_type,
                    "r1_network_type": r1_type,
                    "activation_mode": placement["mode"],
                    "activations": placement.get("targets", []),
                    "all_ap_radio_types": placement.get("radio_types", []),
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
                reuse_notes = []
                have = _R1_SUBTYPE.get(str(existing_network.get("nwSubType") or "").upper())
                want = "dpsk" if r1_type == "dpsk_external" else r1_type
                if have and want in _R1_SUBTYPE.values() and have != want:
                    msg = (f"{wlan.name}: R1 already has it as {have.upper()}, the plan makes it {want.upper()} — "
                           "it's reused as it is (the type isn't changed); delete or rename it in R1 to recreate")
                    reuse_notes.append(msg)
                    setting_warnings.append(ConflictDetail(
                        unit_id=wlan_id, resource_type="wifi_network", resource_name=wlan.name,
                        description=msg, severity="warning"))
                actions.append(ResourceAction(
                    resource_type="wifi_network",
                    name=wlan.name,
                    action="reuse",
                    existing_id=existing_network.get("id"),
                    notes=reuse_notes or ["Already in R1 (an earlier run?) — reused, not created again"],
                ))
            else:
                will_create += 1
                actions.append(ResourceAction(
                    resource_type="wifi_network",
                    name=r1_name,
                    action="create",
                    notes=[name_note] if name_note else [],
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
                    "ssid": r1_ssid,
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
                dpsk_conflicts.extend(_dpsk_network_cap(dpsk_pools, assignments, dpsk_wlans, r1_pools))

        # ── Build validation result ───────────────────────────────────
        # Estimate API calls (from the units actually planned)
        planned_types = [u.plan.extra.get("r1_network_type") for u in unit_mappings.values()]
        aaa_count = planned_types.count("aaa")
        dpsk_count = planned_types.count("dpsk")
        total_api_calls = (
            will_create  # network creation
            + aaa_count  # RADIUS profile creation (max, may reuse)
            + dpsk_count * 2  # identity group + DPSK pool
            + layout["summary"]["groups_to_create"] + sum(1 for g in layout["staged"]["groups"] if g["create"])
            + activation_count  # SSID activations
        )

        conflicts = apg_conflicts + radius_conflicts + dpsk_conflicts + setting_warnings
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
            resolver_result={"layout_summary": layout["summary"]},
            type_mappings=type_mappings_serializable,
            r1_inventory_summary=r1_inventory.summary(),
            ap_groups_to_create=sorted({g["name"] for g in layout["groups"] if g["create"]}
                                       | {g["name"] for g in layout["staged"]["groups"] if g["create"]}),
        )

    async def _apply_radius_decisions(self, snapshot: SZMigrationSnapshot, radius_users: Optional[set] = None):
        """
        Turn the Review step's RADIUS choices into plan actions and conflicts.

        radius_users: WLANs that authenticate against RADIUS in R1. A service
        none of them uses (all converted to R1 DPSK, or a portal WLAN made
        plain PSK) is not needed: no choice is asked and nothing is created.
        None = every referenced service counts (older callers).

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
            users = ", ".join(sorted({u["wlan_name"] for u in s["used_by"]}))
            if radius_users is not None and not any(u["wlan_id"] in radius_users for u in s["used_by"]):
                skipped.add(s["id"])
                actions.append(ResourceAction(
                    resource_type="radius_profile", name=s["name"], action="skip",
                    notes=["Not needed: no WLAN using it authenticates against RADIUS in R1 "
                           "(converted to R1 DPSK, or created without RADIUS)"],
                ))
                continue
            d = (decisions or {}).get(s["id"]) if decisions is not None else {"action": "create"}
            if not d:
                # Name only the WLANs that still need it -- listing every WLAN
                # that references the server (converted ones included) made
                # conversions look ignored.
                needing = sorted({u["wlan_name"] for u in s["used_by"]
                                  if radius_users is None or u["wlan_id"] in radius_users})
                shown = ", ".join(needing[:12]) + (f" and {len(needing) - 12} more" if len(needing) > 12 else "")
                conflicts.append(ConflictDetail(
                    resource_type="radius_profile", resource_name=s["name"],
                    description=f"RADIUS '{s['name']}' is still needed by {len(needing)} WLAN(s) kept on external "
                                f"auth: {shown} — choose what to do with the server, or convert those WLANs to "
                                "R1 DPSK",
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
