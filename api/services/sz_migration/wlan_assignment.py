"""
Where each WLAN is assigned, on each controller -- for the pairing views.

SmartZone assigns WLANs through WLAN Groups, at three levels, per radio:
    zone          zone radioConfig.<band>.wlanGroupId, or the WLAN Group
                  named "default" when the zone reports none
    AP Group      AP Group radioConfig.<band>.wlanGroupId (overrides the zone)
    AP            per-AP radioConfig.<band>.wlanGroupId (overrides both)
So a WLAN can reach APs by several paths at once; each is listed with its
WLAN Group and radios. (Which APs actually end up broadcasting it -- after
the closest override wins -- is broadcast_model's job; the AP counts here
come from there.)

RuckusONE activates a network on a venue either on All AP Groups or on
one or more specific AP Groups.

Pure: snapshots only.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from schemas.migration_audit import WlanGroupUsage
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot

SZ_DEFAULT_WLAN_GROUP = "default"

# (SZRadioConfig attribute, per-AP detail key, label)
BANDS = [
    ("radio_24g", "radio24g", "2.4"),
    ("radio_5g", "radio5g", "5"),
    ("radio_5g_lower", "radio5gLower", "5 lower"),
    ("radio_5g_upper", "radio5gUpper", "5 upper"),
    ("radio_6g", "radio6g", "6"),
]


def _bands(labels: List[str]) -> str:
    order = [b[2] for b in BANDS]
    return "/".join(sorted(set(labels), key=order.index)) + " GHz"


class SzAssignments:
    """Every path by which each WLAN Group (and so each WLAN) is assigned."""

    def __init__(self, snapshot: SZMigrationSnapshot):
        self.s = snapshot
        self.groups = {g.id: g for g in snapshot.wlan_groups}
        default = next((g for g in snapshot.wlan_groups if g.name == SZ_DEFAULT_WLAN_GROUP), None)
        zrc = snapshot.zone.radio_config

        # zone: group -> bands
        self.zone: Dict[str, List[str]] = defaultdict(list)
        for attr, _, label in BANDS:
            gid = (getattr(zrc, attr, None) if zrc else None) or (default.id if default else None)
            if gid and (attr not in ("radio_5g_lower", "radio_5g_upper", "radio_6g") or getattr(zrc, attr, None)):
                # Lower/upper/6 GHz only when the zone sets them explicitly --
                # otherwise every zone would claim bands most APs don't have.
                self.zone[gid].append(label)

        # AP Group overrides: group -> {ap group name: bands}
        self.ap_group: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for apg in snapshot.ap_groups:
            rc = apg.radio_config
            for attr, _, label in BANDS:
                gid = getattr(rc, attr, None) if rc else None
                if gid:
                    self.ap_group[gid][apg.name].append(label)

        # AP overrides: group -> {band: [ap names]}
        names = {(a.get("apMac") or a.get("mac")): (a.get("deviceName") or a.get("name") or a.get("apMac"))
                 for a in snapshot.aps}
        self.ap: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for mac, cfg in snapshot.ap_radio_config.items():
            radios = (cfg or {}).get("radios") or {}
            for _, key, label in BANDS:
                gid = (radios.get(key) or {}).get("wlan_group_id")
                if gid:
                    self.ap[gid][label].append(names.get(mac, mac))

    def group_name(self, gid: str) -> str:
        g = self.groups.get(gid)
        return g.name if g else gid

    def paths_for_wlan(self, wlan_id: str) -> List[str]:
        """Human lines: every way this WLAN is assigned, with its WLAN Group and radios."""
        lines: List[str] = []
        for gid, g in self.groups.items():
            if not any(m.id == wlan_id for m in g.members):
                continue
            if self.zone.get(gid):
                lines.append(f"Zone-wide: {_bands(self.zone[gid])} (WLAN Group '{g.name}')")
            for apg, bands in sorted(self.ap_group.get(gid, {}).items()):
                lines.append(f"AP Group '{apg}': {_bands(bands)} (WLAN Group '{g.name}')")
            per_band = self.ap.get(gid, {})
            if per_band:
                aps = {a for names in per_band.values() for a in names}
                lines.append(f"AP override on {len(aps)} AP{'s' if len(aps) != 1 else ''}: "
                             f"{_bands(list(per_band))} (WLAN Group '{g.name}')")
        return lines or ["Not in any WLAN Group that is applied"]

    def usage(self, r1_groups_carrying: Dict[str, List[str]]) -> List[WlanGroupUsage]:
        ssid = {w.id: w.ssid for w in self.s.wlans}
        out = []
        for gid, g in self.groups.items():
            per_band = self.ap.get(gid, {})
            aps = sorted({a for names in per_band.values() for a in names})
            out.append(WlanGroupUsage(
                id=gid, name=g.name,
                ssids=sorted(ssid.get(m.id, m.ssid or m.name or m.id) for m in g.members),
                zone_bands=list(self.zone.get(gid, [])),
                ap_groups={k: list(v) for k, v in self.ap_group.get(gid, {}).items()},
                ap_override_counts={b: len(v) for b, v in per_band.items()},
                ap_override_sample=aps[:8],
                ap_override_total=len(aps),
                r1_groups_carrying_all=r1_groups_carrying.get(gid, []),
            ))
        # Most-used first: zone, then AP Groups, then AP overrides
        out.sort(key=lambda u: (-(len(u.zone_bands) > 0), -len(u.ap_groups), -u.ap_override_total, u.name))
        return out


def r1_activation(inv: R1VenueInventory, net: Dict[str, Any]) -> Dict[str, Any]:
    """{all: bool, groups: [(name, ap_count)]} for this network in this venue."""
    names = {g.get("id"): (g.get("name") or "(default)") for g in inv.ap_groups}
    counts: Dict[str, int] = defaultdict(int)
    for a in inv.aps:
        counts[a.get("apGroupId")] += 1
    for vag in net.get("venueApGroups") or []:
        if vag.get("venueId") not in (None, inv.venue_id):
            continue
        if vag.get("isAllApGroups"):
            return {"all": True, "groups": [], "ap_count": len(inv.aps)}
        ids: Set[str] = set(vag.get("apGroupIds") or [])
        ids |= {x.get("apGroupId") for x in vag.get("apGroups") or [] if x.get("apGroupId")}
        groups = sorted(((names.get(i, "(AP Group not in capture)"), counts.get(i, 0)) for i in ids), key=lambda t: t[0])
        return {"all": False, "groups": groups, "ap_count": sum(c for _, c in groups)}
    return {"all": False, "groups": [], "ap_count": 0}


def r1_activation_lines(act: Dict[str, Any]) -> List[str]:
    if act["all"]:
        return [f"All AP Groups ({act['ap_count']} APs)"]
    if not act["groups"]:
        return ["Not activated in this venue"]
    return [f"AP Group '{n}' ({c} AP{'s' if c != 1 else ''})" for n, c in act["groups"]]
