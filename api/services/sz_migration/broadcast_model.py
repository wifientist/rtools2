"""
Audit checkpoint 2: which SSIDs each AP broadcasts, worked out through each
controller's own hierarchy, then compared AP by AP.

SmartZone (per AP, per radio -- the closest override wins):
    AP-level WLAN Group override      (GET /aps/{mac} radioConfig.<band>.wlanGroupId)
    > AP Group override               (AP Group radioConfig.<band>.wlanGroupId)
    > zone                            (zone radioConfig.<band>.wlanGroupId)
    > the WLAN Group named "default"  (what the zone uses when it reports none)
  A radio with wlanServiceEnabled=false broadcasts nothing. Dual-5G APs use
  their lower/upper 5 GHz radios. Radios are taken from the AP's own detail
  (it lists only the radios that AP has). WLANs scheduled AlwaysOff are left
  out. The broadcast set is the union over the AP's radios.

RuckusONE (much flatter):
    AP -> its AP Group -> every network activated on that group in this
    venue, or on All AP Groups.
  Per-group radio types aren't in the network list R1 returns, so this
  checkpoint compares SSIDs per AP, not per radio.

SSIDs are compared as strings: an SSID broadcast on an AP is what a client
sees, whichever object carries it.

Then two roll-ups:
  * per SSID: how many APs broadcast it on each side, and which differ;
  * "broadcast groups": APs with the same SZ SSID set (the groups SZ's
    overrides effectively create), and which R1 AP Group(s) they landed in.

Pure: works on the snapshots plus the pairings.
"""

from collections import Counter, defaultdict
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from schemas.migration_audit import (
    ApBroadcast, AuditMatching, BroadcastAudit, BroadcastGroup, SsidCoverage,
)
from schemas.r1_inventory import R1VenueInventory
from schemas.sz_migration import SZMigrationSnapshot

SZ_DEFAULT_WLAN_GROUP = "default"
SAMPLE = 8

# AP detail band key -> (SZRadioConfig attribute on AP Group/zone, display label)
_BANDS = {
    "radio24g": ("radio_24g", "2.4 GHz"),
    "radio5g": ("radio_5g", "5 GHz"),
    "radio5gLower": ("radio_5g_lower", "5 GHz lower"),
    "radio5gUpper": ("radio_5g_upper", "5 GHz upper"),
    "radio6g": ("radio_6g", "6 GHz"),
}


# ═══════════════════════════════════════════════════════════════════════
# SmartZone side
# ═══════════════════════════════════════════════════════════════════════

class _SzModel:
    def __init__(self, snapshot: SZMigrationSnapshot):
        self.s = snapshot
        self.group_by_id = {g.id: g for g in snapshot.wlan_groups}
        self.default_group = next((g for g in snapshot.wlan_groups if g.name == SZ_DEFAULT_WLAN_GROUP), None)
        self.ap_group_by_id = {g.id: g for g in snapshot.ap_groups}
        off = {w.id for w in snapshot.wlans if ((w.schedule or {}).get("type") == "AlwaysOff")}
        self.ssid_by_wlan = {w.id: w.ssid for w in snapshot.wlans if w.id not in off}
        self.always_off = len(off)
        zr = (snapshot.zone.raw or {}).get("radioConfig") or {}
        self.zone_dual5g = bool(zr.get("dual5gEnabled"))

    def _zone_group(self, band_attr: str) -> Optional[str]:
        rc = self.s.zone.radio_config
        return getattr(rc, band_attr, None) if rc else None

    def _apg_group(self, apg_id: Optional[str], band_attr: str) -> Optional[str]:
        apg = self.ap_group_by_id.get(apg_id or "")
        rc = apg.radio_config if apg else None
        return getattr(rc, band_attr, None) if rc else None

    def radios_for(self, ap_raw: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> List[str]:
        """Which radio keys this AP has. Its detail lists only its own radios."""
        radios = list(((detail or {}).get("radios") or {}).keys())
        if not radios:
            return ["radio24g", "radio5g"]          # detail unreadable: assume dual-band
        dual = (detail or {}).get("dual5g")
        dual = self.zone_dual5g if dual is None else bool(dual)
        split = [r for r in ("radio5gLower", "radio5gUpper") if r in radios]
        if dual and split:
            radios = [r for r in radios if r != "radio5g"]
        else:
            radios = [r for r in radios if r not in ("radio5gLower", "radio5gUpper")]
        return radios

    def broadcast(self, ap_raw: Dict[str, Any]) -> Tuple[Set[str], List[str], bool]:
        """(SSIDs this AP broadcasts, source per radio, whether AP-level overrides were readable)."""
        mac = ap_raw.get("apMac") or ap_raw.get("mac")
        detail = self.s.ap_radio_config.get(mac) if mac else None
        known = bool(detail) and not detail.get("error")
        apg_id = ap_raw.get("apGroupId")
        ssids: Set[str] = set()
        sources: List[str] = []
        for band in self.radios_for(ap_raw, detail):
            attr, label = _BANDS[band]
            r = ((detail or {}).get("radios") or {}).get(band) or {}
            if r.get("service_enabled") is False:
                sources.append(f"{label}: WLAN service off")
                continue
            for tier, gid in (("AP override", r.get("wlan_group_id")),
                              ("AP Group override", self._apg_group(apg_id, attr)),
                              ("zone", self._zone_group(attr)),
                              ("zone default", self.default_group.id if self.default_group else None)):
                if gid:
                    g = self.group_by_id.get(gid)
                    sources.append(f"{label}: {tier} → {g.name if g else gid}")
                    if g:
                        ssids |= {self.ssid_by_wlan[m.id] for m in g.members if m.id in self.ssid_by_wlan}
                    break
            else:
                sources.append(f"{label}: no WLAN Group")
        return ssids, sources, known


# ═══════════════════════════════════════════════════════════════════════
# RuckusONE side
# ═══════════════════════════════════════════════════════════════════════

def r1_ssids_by_group(inv: R1VenueInventory) -> Tuple[Dict[str, Set[str]], Set[str]]:
    """(AP Group id -> SSIDs activated on it, SSIDs activated on All AP Groups)."""
    by_group: Dict[str, Set[str]] = defaultdict(set)
    everywhere: Set[str] = set()
    for net in inv.wifi_networks:
        ssid = net.get("ssid")
        if not ssid:
            continue
        for vag in net.get("venueApGroups") or []:
            if vag.get("venueId") not in (None, inv.venue_id):
                continue
            if vag.get("isAllApGroups"):
                everywhere.add(ssid)
                continue
            ids = set(vag.get("apGroupIds") or [])
            ids |= {g.get("apGroupId") for g in vag.get("apGroups") or [] if g.get("apGroupId")}
            for gid in ids:
                by_group[gid].add(ssid)
    return by_group, everywhere


# ═══════════════════════════════════════════════════════════════════════
# Compare
# ═══════════════════════════════════════════════════════════════════════

def build_broadcast_audit(snapshot: SZMigrationSnapshot, inv: R1VenueInventory,
                          matching: AuditMatching) -> BroadcastAudit:
    sz = _SzModel(snapshot)
    r1_by_group, r1_everywhere = r1_ssids_by_group(inv)
    r1_group_name = {g.get("id"): (g.get("name") or "(default)") for g in inv.ap_groups}
    r1_ap_by_serial = {a.get("serialNumber"): a for a in inv.aps if a.get("serialNumber")}
    sz_ap_by_mac = {(a.get("apMac") or a.get("mac")): a for a in snapshot.aps}
    sz_group_name = {g.id: g.name for g in snapshot.ap_groups}

    def r1_ssids_for(ap: Dict[str, Any]) -> Set[str]:
        return set(r1_by_group.get(ap.get("apGroupId"), set())) | r1_everywhere

    rows: List[ApBroadcast] = []
    unknown_overrides = 0
    for m in matching.aps:
        row = ApBroadcast(name=m.name, serial=m.serial, mac=m.mac, presence=m.presence,
                          sz_ap_group=m.sz_ap_group, r1_ap_group=m.r1_ap_group)
        sz_set: Set[str] = set()
        if m.presence in ("in_both", "sz_only"):
            raw = sz_ap_by_mac.get(m.sz_ap_mac) or {}
            sz_set, row.sz_sources, known = sz.broadcast(raw)
            if not known:
                unknown_overrides += 1
            row.sz_ssids = sorted(sz_set)
        r1_set: Set[str] = set()
        if m.presence in ("in_both", "r1_only"):
            r1_ap = r1_ap_by_serial.get(m.r1_serial) or {}
            r1_set = r1_ssids_for(r1_ap)
            row.r1_ssids = sorted(r1_set)
            row.r1_via_all_ap_groups = sorted(r1_set & r1_everywhere)
        if m.presence == "in_both":
            row.missing_in_r1 = sorted(sz_set - r1_set)
            row.extra_in_r1 = sorted(r1_set - sz_set)
            row.status = "match" if not (row.missing_in_r1 or row.extra_in_r1) else "differs"
            if row.status == "match" and not known:
                row.status = "unknown"
        else:
            row.status = "pending" if m.presence == "sz_only" else "r1_only"
        rows.append(row)

    # ── Per SSID ───────────────────────────────────────────────────
    r1_reported = {n.get("ssid"): n.get("apCount") for n in inv.wifi_networks if n.get("ssid")}
    sz_count: Counter = Counter()
    r1_count: Counter = Counter()
    missing: Dict[str, List[str]] = defaultdict(list)
    extra: Dict[str, List[str]] = defaultdict(list)
    for r in rows:
        if r.presence in ("in_both", "sz_only"):
            sz_count.update(r.sz_ssids)
        if r.presence in ("in_both", "r1_only"):
            r1_count.update(r.r1_ssids)
        for s in r.missing_in_r1:
            missing[s].append(r.name or r.serial or r.mac or "?")
        for s in r.extra_in_r1:
            extra[s].append(r.name or r.serial or r.mac or "?")
    ssids = []
    for s in sorted(set(sz_count) | set(r1_count)):
        cov = SsidCoverage(
            ssid=s, sz_ap_count=sz_count[s], r1_ap_count=r1_count[s],
            r1_reported_ap_count=r1_reported.get(s),
            missing_in_r1=len(missing[s]), extra_in_r1=len(extra[s]),
            sample_missing=missing[s][:SAMPLE], sample_extra=extra[s][:SAMPLE],
        )
        if s not in r1_count:
            cov.status = "missing"
        elif s not in sz_count:
            cov.status = "extra"
        elif cov.missing_in_r1 or cov.extra_in_r1:
            cov.status = "differs"
        ssids.append(cov)

    # ── Broadcast groups: APs sharing one SZ SSID set ──────────────
    clusters: Dict[FrozenSet[str], List[ApBroadcast]] = defaultdict(list)
    for r in rows:
        if r.presence in ("in_both", "sz_only"):
            clusters[frozenset(r.sz_ssids)].append(r)
    r1_group_sets = {gid: frozenset(r1_by_group.get(gid, set()) | r1_everywhere) for gid in r1_group_name}
    groups: List[BroadcastGroup] = []
    for key, members in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0]))):
        g = BroadcastGroup(
            ssids=sorted(key), sz_ap_count=len(members),
            sz_ap_groups=dict(Counter(m.sz_ap_group or "?" for m in members)),
            r1_ap_groups=dict(Counter(m.r1_ap_group or "?" for m in members if m.presence == "in_both")),
            r1_not_in_venue=sum(1 for m in members if m.presence == "sz_only"),
            r1_groups_matching_ssids=sorted(r1_group_name[gid] for gid, s in r1_group_sets.items() if s == key),
            sample_aps=[m.name or m.serial or m.mac or "?" for m in members[:SAMPLE]],
        )
        in_both = [m for m in members if m.presence == "in_both"]
        if not in_both:
            g.status = "pending"
        elif any(m.status == "differs" for m in in_both):
            g.status = "differs"
        elif len(g.r1_ap_groups) > 1:
            g.status = "split"      # same SZ broadcast, spread over several R1 groups (fine if each carries it)
        groups.append(g)

    st = Counter(r.status for r in rows)
    notes = [
        "Compared per AP as SSID sets; R1 per-AP-Group radio types are not compared yet.",
    ]
    if sz.always_off:
        notes.append(f"{sz.always_off} SZ WLAN(s) scheduled Always Off are left out of the SZ side.")
    if unknown_overrides:
        notes.append(f"{unknown_overrides} SZ AP(s) had no readable per-AP radio config; "
                     "their AP-level overrides are unknown (shown as 'unknown' when otherwise matching).")
    if not snapshot.ap_radio_config and snapshot.aps:
        notes.append("This SZ snapshot predates per-AP radio reads — re-extract to see AP-level overrides.")
    overridden = sum(1 for v in snapshot.ap_radio_config.values()
                     if any(r.get("wlan_group_id") for r in (v.get("radios") or {}).values()))

    return BroadcastAudit(
        aps=rows, ssids=ssids, groups=groups, notes=notes,
        counts={
            "aps_compared": st["match"] + st["differs"] + st["unknown"],
            "aps_match": st["match"],
            "aps_differs": st["differs"],
            "aps_unknown": st["unknown"],
            "aps_pending": st["pending"],
            "aps_r1_only": st["r1_only"],
            "sz_aps_with_ap_override": overridden,
            "ssids_total": len(ssids),
            "ssids_match": sum(1 for s in ssids if s.status == "match"),
            "ssids_missing_in_r1": sum(1 for s in ssids if s.status == "missing"),
            "ssids_extra_in_r1": sum(1 for s in ssids if s.status == "extra"),
            "broadcast_groups": len(groups),
        },
    )
