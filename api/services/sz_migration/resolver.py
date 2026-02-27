"""
WLAN Group Resolution Engine — M2

Resolves SZ WLAN Groups into per-AP-Group WLAN activations with radio bands.

Algorithm:
1. Build lookups: wlan_group_by_id, wlan_by_id from snapshot
2. Find zone's "default" WLAN Group (the one named "default")
3. For each AP Group:
   a. Check ap_group.radioConfig.radio{band}.wlanGroupId for overrides
   b. Fall back to zone's default WLAN Group if no override
   c. Resolve WLAN Group → member WLANs per radio band
   d. Invert: for each WLAN, which radio bands is it active on?
4. Emit one WLANActivationEntry per (wlan_id, ap_group_id)
5. Enforce 15-SSID-per-AP-Group limit

SZ Architecture note:
  Zone radioConfig does NOT carry wlanGroupId — that's only at the AP Group level.
  When an AP Group's radioConfig has None for a band, it uses the zone's "default"
  WLAN Group (the one named "default") for ALL bands.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from schemas.sz_migration import (
    SZMigrationSnapshot,
    SZWLANGroup,
    WLANActivationEntry,
    APGroupSSIDSummary,
    ResolverResult,
)

logger = logging.getLogger(__name__)

R1_SSID_LIMIT = 15

# Radio band labels used in activation entries
RADIO_BANDS = [
    ("radio_24g", "2.4"),
    ("radio_5g", "5"),
    ("radio_5g_lower", "5L"),
    ("radio_5g_upper", "5U"),
    ("radio_6g", "6"),
]


def _find_default_wlan_group(snapshot: SZMigrationSnapshot) -> Optional[SZWLANGroup]:
    """Find the zone's default WLAN Group (named 'default')."""
    for wg in snapshot.wlan_groups:
        if wg.name.lower() == "default":
            return wg
    return None


def resolve_wlan_activations(snapshot: SZMigrationSnapshot) -> ResolverResult:
    """
    Resolve the SZ snapshot into per-AP-Group WLAN activations.

    Args:
        snapshot: Complete SZ zone extraction from M0

    Returns:
        ResolverResult with activations, per-group summaries, and limit warnings
    """
    warnings: List[str] = []

    # ── Build lookups ─────────────────────────────────────────────────
    wlan_by_id = {w.id: w for w in snapshot.wlans}
    wlan_group_by_id: Dict[str, SZWLANGroup] = {wg.id: wg for wg in snapshot.wlan_groups}

    # ── Zone default WLAN Group ───────────────────────────────────────
    # SZ zones always have a "default" WLAN Group — it's used for any
    # radio band where the AP Group doesn't specify an override.
    default_wlan_group = _find_default_wlan_group(snapshot)

    if not default_wlan_group and not snapshot.wlan_groups:
        warnings.append("No WLAN Groups found — cannot resolve activations")
        return ResolverResult(warnings=warnings)

    if not default_wlan_group and snapshot.wlan_groups:
        # No group named "default" — fall back to first group
        default_wlan_group = snapshot.wlan_groups[0]
        warnings.append(
            f"No WLAN Group named 'default' found — using '{default_wlan_group.name}' as fallback"
        )

    # ── Resolve each AP Group ─────────────────────────────────────────
    all_activations: List[WLANActivationEntry] = []
    ap_group_summaries: List[APGroupSSIDSummary] = []

    for apg in snapshot.ap_groups:
        apg_rc = apg.radio_config

        # For each radio band, determine which WLAN Group is active
        band_wlan_groups: Dict[str, Tuple[str, str]] = {}  # band_label → (wlan_group_id, source)

        for rc_field, band_label in RADIO_BANDS:
            # Check AP Group override first
            apg_wg_id = getattr(apg_rc, rc_field, None) if apg_rc else None
            if apg_wg_id:
                band_wlan_groups[band_label] = (apg_wg_id, "ap_group_override")
                continue

            # Fall back to zone's default WLAN Group
            if default_wlan_group:
                band_wlan_groups[band_label] = (default_wlan_group.id, "zone_default")

        # ── Resolve WLAN Group → WLANs per band ──────────────────────
        # Invert: for each WLAN, collect which bands it's active on
        wlan_bands: Dict[str, Tuple[Set[str], str]] = {}  # wlan_id → (set of bands, source)

        for band_label, (wg_id, source) in band_wlan_groups.items():
            wg = wlan_group_by_id.get(wg_id)
            if not wg:
                warnings.append(
                    f"AP Group '{apg.name}': WLAN Group ID '{wg_id}' "
                    f"({source}, band {band_label}) not found in snapshot"
                )
                continue

            for member in wg.members:
                if member.id not in wlan_bands:
                    wlan_bands[member.id] = (set(), source)
                wlan_bands[member.id][0].add(band_label)
                # If any band comes from override, mark as override
                if source == "ap_group_override":
                    wlan_bands[member.id] = (wlan_bands[member.id][0], "ap_group_override")

        # ── Emit activation entries ───────────────────────────────────
        group_ssids: Set[str] = set()

        for wlan_id, (bands, source) in wlan_bands.items():
            wlan = wlan_by_id.get(wlan_id)
            if not wlan:
                warnings.append(
                    f"AP Group '{apg.name}': WLAN ID '{wlan_id}' referenced "
                    f"in WLAN Group but not found in snapshot WLANs"
                )
                continue

            sorted_bands = sorted(bands, key=_band_sort_key)
            all_activations.append(WLANActivationEntry(
                wlan_id=wlan.id,
                wlan_name=wlan.name,
                ssid=wlan.ssid,
                auth_type=wlan.auth_type,
                ap_group_id=apg.id,
                ap_group_name=apg.name,
                radios=sorted_bands,
                source=source,
                ap_count=apg.ap_count,
            ))
            group_ssids.add(wlan.ssid)

        # ── Per-group SSID summary ────────────────────────────────────
        over_limit = len(group_ssids) > R1_SSID_LIMIT
        if over_limit:
            warnings.append(
                f"AP Group '{apg.name}' has {len(group_ssids)} SSIDs (limit: {R1_SSID_LIMIT})"
            )

        ap_group_summaries.append(APGroupSSIDSummary(
            ap_group_id=apg.id,
            ap_group_name=apg.name,
            ap_count=apg.ap_count,
            ssid_count=len(group_ssids),
            over_limit=over_limit,
            ssids=sorted(group_ssids),
        ))

    blocked = any(s.over_limit for s in ap_group_summaries)

    result = ResolverResult(
        activations=all_activations,
        ap_group_summaries=ap_group_summaries,
        warnings=warnings,
        blocked=blocked,
    )

    logger.info(
        f"Resolver: {len(all_activations)} activations across "
        f"{len(ap_group_summaries)} AP Groups, blocked={blocked}"
    )

    return result


def _band_sort_key(band: str) -> int:
    """Sort radio bands in natural order."""
    order = {"2.4": 0, "5": 1, "5L": 2, "5U": 3, "6": 4}
    return order.get(band, 99)
