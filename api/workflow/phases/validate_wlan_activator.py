"""
V2 Phase: Validate & Plan a WLAN Activation.

Takes rows of "ap_identifier,ap_group_name,wlan[,vlan]" and works out what has to
happen: which APs exist in the venue, which AP Groups exist or must be
created, which network each WLAN value means, and where each network is
already active.

ONE UNIT PER AP GROUP, like AP Regroup, so create_ap_group and assign_aps run
unchanged. Each unit carries the WLAN(s) to activate on its group.

A row may leave ap_identifier blank: the group is still created and the WLAN
activated on it, with no AP moved.

vlan is the AP-Group VLAN override for that WLAN on that group; blank
inherits the network's VLAN. A WLAN already bound to its group is only
re-touched when a vlan is given and the group's current one differs.

Radios copy an existing binding of the same network in the venue (so a
property-wide WLAN keeps its 6 GHz); a network bound nowhere yet gets 2.4 +
5 GHz, plus 6 GHz for WPA3/OWE.

Checks that block the plan (errors), because R1 would refuse the activation:
  * a group would carry more than 15 SSIDs (R1's per-AP-Group limit);
  * a DPSK WLAN has no DPSK service linked (WIFI-20300).
Rows whose AP or WLAN can't be resolved are skipped with a warning.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, List, Set

from pydantic import BaseModel, Field

from services.wlan_activator import (
    index_networks, is_dpsk, radio_types_for, resolve_network, venue_state,
)
from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.ap_fields import (
    ap_serial,
    index_aps_by_serial,
    index_aps_by_name,
)
from workflow.v2.models import (
    ConflictDetail, UnitMapping, UnitPlan, UnitResolved, UnitStatus,
    ValidationResult, ValidationSummary, ResourceAction,
)

logger = logging.getLogger(__name__)

SSID_LIMIT_PER_AP_GROUP = 15


def _sample(items: List[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" …+{len(items) - n}" if len(items) > n else "")


@register_phase("validate_wlan_activator", "Validate & Plan WLAN Activation")
class ValidateWlanActivatorPhase(PhaseExecutor):
    """Resolve the rows against the venue and build one unit per AP Group."""

    class Inputs(BaseModel):
        activations: List[Dict[str, Any]] = Field(default_factory=list)
        options: Dict[str, Any] = Field(default_factory=dict)

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: ValidationResult
        all_venue_aps: List[Dict[str, Any]] = Field(default_factory=list)
        unmatched_identifiers: List[str] = Field(default_factory=list)
        unresolved_wlans: List[Dict[str, str]] = Field(default_factory=list)
        ap_groups_to_create: List[str] = Field(default_factory=list)
        ap_groups_to_reuse: List[str] = Field(default_factory=list)

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        rows = []
        for r in inputs.activations:
            row = {k: str(r.get(k) or "").strip() for k in ("ap_identifier", "ap_group_name", "wlan", "vlan")}
            # A group plus an AP or a WLAN: no WLAN = just move the AP.
            if row["ap_group_name"] and (row["wlan"] or row["ap_identifier"]):
                rows.append(row)
        if not rows:
            raise ValueError(
                "No usable rows. Each row needs an AP Group plus an AP and/or "
                "a WLAN (ap_identifier,ap_group_name,wlan[,vlan])."
            )
        await self.emit(f"Planning {len(rows)} rows...")

        # ---- venue APs, AP Groups, tenant networks ---------------------
        try:
            resp = await self.r1_client.venues.get_aps_by_tenant_venue(self.tenant_id, self.venue_id)
            all_venue_aps = resp.get('data', []) or []
        except Exception as e:
            raise RuntimeError(f"Could not list APs in this venue: {e}")
        by_serial = index_aps_by_serial(all_venue_aps)
        by_name = index_aps_by_name(all_venue_aps)

        try:
            groups = await self.r1_client.venues.list_ap_groups_in_venue(self.tenant_id, self.venue_id)
        except Exception as e:
            # A partial view here would create duplicate groups -- refuse instead.
            raise RuntimeError(f"Could not list AP Groups in this venue: {e}")
        existing_groups = self.r1_client.venues.index_ap_groups_by_name(groups)

        try:
            # Tenant-wide: the WLANs this tool exists for are usually not
            # activated anywhere yet, so a venue filter would hide them.
            net_resp = await self.r1_client.networks.get_wifi_networks(self.tenant_id)
            networks = net_resp.get('data', []) if isinstance(net_resp, dict) else net_resp
        except Exception as e:
            raise RuntimeError(f"Could not list WiFi networks: {e}")
        nets_by_name, nets_by_ssid = index_networks(networks)

        await self.emit(
            f"Venue has {len(all_venue_aps)} APs and {len(existing_groups)} AP Groups; "
            f"tenant has {len(networks)} networks"
        )

        # ---- resolve rows ----------------------------------------------
        conflicts: List[ConflictDetail] = []
        unmatched: List[str] = []
        unresolved: Dict[str, str] = {}
        serials_by_group: Dict[str, List[str]] = defaultdict(list)
        wlans_by_group: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        group_of_serial: Dict[str, str] = {}
        moved_twice: List[str] = []
        vlan_for: Dict[tuple, int] = {}          # (group, network id) -> VLAN override
        bad_vlans: List[str] = []
        vlan_clashes: List[str] = []

        for row in rows:
            group = row["ap_group_name"]
            network = None
            if row["wlan"]:
                network, reason = resolve_network(row["wlan"], nets_by_name, nets_by_ssid)
                if not network:
                    unresolved.setdefault(row["wlan"], reason)
                    continue
                wlans_by_group[group][network["id"]] = network
            else:
                wlans_by_group[group]  # the group is still planned for the AP
            if network and row["vlan"]:
                try:
                    vlan = int(row["vlan"])
                    if not 1 <= vlan <= 4094:
                        raise ValueError
                except ValueError:
                    bad_vlans.append(f"{group}/{row['wlan']}: '{row['vlan']}'")
                else:
                    key = (group, network["id"])
                    if key in vlan_for and vlan_for[key] != vlan:
                        vlan_clashes.append(f"{group}/{row['wlan']}: {vlan_for[key]} vs {vlan}")
                    else:
                        vlan_for[key] = vlan

            ident = row["ap_identifier"]
            if not ident:
                continue
            ap = by_serial.get(ident) or by_name.get(ident)
            serial = ap_serial(ap) if ap else ""
            if not serial:
                unmatched.append(ident)
                continue
            if serial in group_of_serial:
                if group_of_serial[serial] != group:
                    moved_twice.append(f"{ident} -> {group_of_serial[serial]} / {group}")
                continue
            group_of_serial[serial] = group
            serials_by_group[group].append(serial)

        if unresolved:
            for wlan, reason in sorted(unresolved.items()):
                conflicts.append(ConflictDetail(
                    resource_type="wlan", resource_name=wlan,
                    description=f"{reason}. Its rows are skipped.", severity="warning",
                ))
            await self.emit(f"{len(unresolved)} WLAN value(s) did not resolve: {_sample(sorted(unresolved))}", "warning")
        if unmatched:
            conflicts.append(ConflictDetail(
                resource_type="access_point", resource_name=f"{len(unmatched)} AP(s)",
                description=(
                    f"Not in this venue: {_sample(unmatched, 10)}. Those APs are not moved; "
                    "their AP Group and WLAN are still set up."
                ),
                severity="warning",
            ))
        if bad_vlans:
            conflicts.append(ConflictDetail(
                resource_type="vlan", resource_name=f"{len(bad_vlans)} row(s)",
                description=f"Not a VLAN (1-4094), so those WLANs inherit the network VLAN: {_sample(bad_vlans, 5)}",
                severity="warning",
            ))
        if vlan_clashes:
            conflicts.append(ConflictDetail(
                resource_type="vlan", resource_name=f"{len(vlan_clashes)} AP Group/WLAN pair(s)",
                description=f"Given two different VLANs; the FIRST is used: {_sample(vlan_clashes, 5)}",
                severity="warning",
            ))
        if moved_twice:
            # First row wins; an AP is in one group, and moving it twice in
            # parallel units would leave it wherever the last PUT landed.
            conflicts.append(ConflictDetail(
                resource_type="access_point", resource_name=f"{len(moved_twice)} AP(s)",
                description=f"Listed for two AP Groups; the FIRST row is used: {_sample(moved_twice, 3)}",
                severity="warning",
            ))

        # ---- what is already active in this venue ----------------------
        planned_ids: Set[str] = {nid for ws in wlans_by_group.values() for nid in ws}
        venue_wide_other = 0                     # venue-wide SSIDs we are not moving
        bound_count: Dict[str, int] = defaultdict(int)   # group id -> specific SSIDs on it
        for n in networks:
            state, bound = venue_state(n, self.venue_id)
            if state == "venue_wide" and n.get("id") not in planned_ids:
                venue_wide_other += 1
            elif state == "specific":
                for gid in bound:
                    bound_count[gid] += 1

        # ---- DPSK services for networks that still need step 1 ---------
        dpsk_pool: Dict[str, str] = {}
        need_pool = [
            n for ws in wlans_by_group.values() for n in ws.values()
            if is_dpsk(n) and venue_state(n, self.venue_id)[0] != "specific"
        ]
        need_pool = list({n["id"]: n for n in need_pool}.values())
        if need_pool:
            await self.emit(f"Checking DPSK services on {len(need_pool)} DPSK network(s)...")
            sem = asyncio.Semaphore(5)

            async def fetch_pool(n):
                async with sem:
                    try:
                        resp = await self.r1_client.networks.get_dpsk_services_on_network(n["id"], self.tenant_id)
                        data = (resp or {}).get("data") or []
                        if data and data[0].get("id"):
                            dpsk_pool[n["id"]] = data[0]["id"]
                    except Exception as e:
                        logger.warning(f"DPSK service lookup failed for {n.get('name')}: {e}")

            await asyncio.gather(*(fetch_pool(n) for n in need_pool))
            no_pool = sorted(n.get("name") or n["id"] for n in need_pool if n["id"] not in dpsk_pool)
            for name in no_pool:
                conflicts.append(ConflictDetail(
                    resource_type="wlan", resource_name=name,
                    description=(
                        "DPSK network with no DPSK service linked; R1 refuses to activate it "
                        "(WIFI-20300). Link a DPSK service to it first."
                    ),
                    severity="error",
                ))

        # ---- radios from an existing binding, current VLANs ------------
        # One GET per network bound anywhere in the venue, plus one per
        # already-bound pair that was given a VLAN.
        sem = asyncio.Semaphore(5)

        async def group_settings(nid: str, gid: str) -> Dict[str, Any]:
            async with sem:
                try:
                    return await self.r1_client.venues.get_ssid_ap_group_settings(
                        self.tenant_id, self.venue_id, nid, gid)
                except Exception as e:
                    logger.warning(f"AP Group settings read failed ({nid} on {gid}): {e}")
                    return {}

        radios: Dict[str, List[str]] = {}
        sample_group: Dict[str, str] = {}
        for nid in planned_ids:
            n = next(ws[nid] for ws in wlans_by_group.values() if nid in ws)
            state, bound = venue_state(n, self.venue_id)
            if state == "specific" and bound:
                sample_group[nid] = sorted(bound)[0]
        pair_checks = []
        for group_name, ws in wlans_by_group.items():
            gid = existing_groups.get(group_name)
            for nid, n in ws.items():
                if gid and (group_name, nid) in vlan_for and gid in venue_state(n, self.venue_id)[1]:
                    pair_checks.append((group_name, nid, gid))
        if sample_group or pair_checks:
            await self.emit(
                f"Reading AP Group settings ({len(sample_group)} radio, {len(pair_checks)} VLAN checks)..."
            )
        sample_results = await asyncio.gather(*(group_settings(nid, gid) for nid, gid in sample_group.items()))
        for nid, res in zip(sample_group, sample_results):
            if res.get("radioTypes"):
                radios[nid] = list(res["radioTypes"])
        pair_results = await asyncio.gather(*(group_settings(nid, gid) for _, nid, gid in pair_checks))
        current_vlan: Dict[tuple, Any] = {
            (g, nid): res.get("vlanId") for (g, nid, _), res in zip(pair_checks, pair_results)
        }

        # ---- one unit per AP Group -------------------------------------
        unit_mappings: Dict[str, UnitMapping] = {}
        to_create: List[str] = []
        to_reuse: List[str] = []
        activations = already = vlan_updates = 0
        venue_wide_moves: Set[str] = set()

        for group_name in sorted(wlans_by_group):
            group_id = existing_groups.get(group_name)
            (to_reuse if group_id else to_create).append(group_name)

            wlans = []
            new_here = 0
            for n in sorted(wlans_by_group[group_name].values(), key=lambda x: x.get("name") or ""):
                state, bound = venue_state(n, self.venue_id)
                vlan = vlan_for.get((group_name, n["id"]))
                if group_id and group_id in bound:
                    if vlan is None or current_vlan.get((group_name, n["id"])) == vlan:
                        already += 1
                        continue
                    vlan_updates += 1
                else:
                    new_here += 1
                if state == "venue_wide":
                    venue_wide_moves.add(n.get("name") or n["id"])
                wlans.append({
                    "network_id": n["id"],
                    "name": n.get("name") or "",
                    "ssid": n.get("ssid") or "",
                    "dpsk_pool_id": dpsk_pool.get(n["id"]),
                    "radio_types": radios.get(n["id"]) or radio_types_for(n),
                    "vlan": vlan,
                })
            activations += len(wlans)

            # SSIDs this group would carry: venue-wide ones it inherits, the
            # specific ones already on it, and ours.
            total = venue_wide_other + (bound_count.get(group_id, 0) if group_id else 0) + new_here
            if total > SSID_LIMIT_PER_AP_GROUP:
                conflicts.append(ConflictDetail(
                    unit_id=f"apgroup_{group_name}", resource_type="ap_group", resource_name=group_name,
                    description=(
                        f"Would carry {total} SSIDs ({venue_wide_other} venue-wide, "
                        f"{total - venue_wide_other - new_here} already on it, {new_here} new); "
                        f"R1 allows {SSID_LIMIT_PER_AP_GROUP} per AP Group."
                    ),
                    severity="error",
                ))

            serials = serials_by_group.get(group_name, [])
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
                    ssid_name=", ".join(w["ssid"] or w["name"] for w in wlans) or None,
                ),
                resolved=UnitResolved(ap_group_id=group_id),
                status=UnitStatus.PENDING,
                input_config={'ap_group_name': group_name, 'wlans': wlans},
            )

        if venue_wide_moves:
            conflicts.append(ConflictDetail(
                resource_type="wlan", resource_name=f"{len(venue_wide_moves)} WLAN(s)",
                description=(
                    f"Currently on All AP Groups in this venue: {_sample(sorted(venue_wide_moves))}. "
                    "They are moved to ONLY the AP Groups listed here and stop broadcasting elsewhere."
                ),
                severity="warning",
            ))

        # ---- plan summary ----------------------------------------------
        actions: List[ResourceAction] = []
        if to_create:
            actions.append(ResourceAction(
                action="create", resource_type="ap_groups", name=f"{len(to_create)} AP Groups",
                notes=[_sample(to_create, 8)],
            ))
        if to_reuse:
            actions.append(ResourceAction(
                action="reuse", resource_type="ap_groups", name=f"{len(to_reuse)} AP Groups",
            ))
        actions.append(ResourceAction(
            action="update", resource_type="access_points", name=f"{len(group_of_serial)} APs moved",
            notes=[f"{len(unmatched)} rows matched no AP in this venue"] if unmatched else [],
        ))
        actions.append(ResourceAction(
            action="create", resource_type="wlan_activations",
            name=f"{activations - vlan_updates} WLAN activations on AP Groups",
            notes=(
                ([f"{already} already active on their AP Group (skipped)"] if already else [])
                + ["Direct AP Group activation - never All AP Groups"]
            ),
        ))
        if vlan_updates:
            actions.append(ResourceAction(
                action="update", resource_type="ap_group_vlans",
                name=f"{vlan_updates} AP Group VLAN(s) changed on WLANs already active",
            ))
        if vlan_for:
            actions.append(ResourceAction(
                action="update", resource_type="ap_group_vlans",
                name=f"{len(vlan_for)} AP Group/WLAN pair(s) with a VLAN set",
                notes=["Blank VLAN = inherit the network's VLAN"],
            ))

        errors = [c for c in conflicts if c.severity == "error"]
        await self.emit(
            f"Plan: {len(unit_mappings)} AP Groups ({len(to_create)} new), "
            f"{len(group_of_serial)} APs, {activations} activations"
            + (f", {already} already active" if already else "")
            + (f" - {len(errors)} blocking issue(s)" if errors else ""),
            "warning" if errors else "success",
        )

        return self.Outputs(
            unit_mappings=unit_mappings,
            all_venue_aps=all_venue_aps,
            unmatched_identifiers=unmatched,
            unresolved_wlans=[{"wlan": w, "reason": r} for w, r in sorted(unresolved.items())],
            ap_groups_to_create=to_create,
            ap_groups_to_reuse=to_reuse,
            validation_result=ValidationResult(
                valid=bool(unit_mappings) and not errors,
                conflicts=conflicts,
                summary=ValidationSummary(
                    total_units=len(unit_mappings),
                    ap_groups_to_create=len(to_create),
                    ap_groups_to_reuse=len(to_reuse),
                    networks_to_reuse=len(planned_ids),
                    total_api_calls=len(to_create) + len(group_of_serial) + 3 * activations,
                ),
                actions=actions,
            ),
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(
            valid=True,
            will_create=bool(inputs.activations),
            estimated_api_calls=3,
            notes=["Resolve APs, AP Groups and WLANs against the venue"],
        )
