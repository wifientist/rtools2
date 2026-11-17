"""
Migration Audit: each SZ AP's effective LAN ports vs the same AP in R1.

Same comparison as AP Port Grabber (services/ap_port_grabber.build_rows), fed
from the audit's captures instead of fresh reads:

    SZ   snapshot.ap_radio_config[mac]["lan"]   the AP's own ports / PoE
         snapshot.ap_lan["group_models"]        AP Group per-model overrides
         snapshot.zone_ap_models                zone per-model settings
         snapshot.ap_lan["profiles"]            every Ethernet Port Profile referenced
    R1   r1_lan (services/ap_port_reads.read_r1_lan_state, cached with the R1 capture)

R1 only serves an AP's own port settings once the AP has connected, so the
migration can't carry per-AP port VLANs up front; this section shows where SZ
and R1 still disagree, and AP Port Grabber fixes them.

Audit statuses (from the grabber's):
    match       R1 gives the AP what SZ does (the uplink port compared too, noted)
    differs     grabber update / review
    pending     the AP is in the R1 venue but R1 can't read its ports yet
    not_in_r1   no AP with this serial / MAC in the R1 venue
    unreadable  something else stops the comparison (unreadable SZ settings, unknown model)

Pure: no I/O.
"""

from collections import Counter
from typing import Any, Dict, List, Optional

from schemas.migration_audit import LanPortAuditRow, LanPortsAudit
from schemas.sz_migration import SZMigrationSnapshot
from services.ap_port_grabber import (
    BLOCKED, MATCH, NOT_IN_R1, REVIEW, UPDATE, build_rows, norm_serial, sz_ap_fields, sz_effective,
)

# What the R1 capture keeps for this section (read_r1_lan_state returns more).
R1_LAN_KEYS = ("r1_aps", "caps", "venue_entries", "ap_states", "activations", "venue_profiles")

_STATUS = {MATCH: "match", UPDATE: "differs", REVIEW: "differs", NOT_IN_R1: "not_in_r1", BLOCKED: "unreadable"}


def lan_sz_aps(snapshot: SZMigrationSnapshot) -> List[Dict[str, Any]]:
    """The zone's APs in the grabber's shape, AP Group names filled in."""
    names = {g.id: g.name for g in snapshot.ap_groups}
    aps = [sz_ap_fields(a) for a in snapshot.aps]
    for a in aps:
        a["group_name"] = a["group_name"] or names.get(a["group_id"])
    return [a for a in aps if a["mac"]]


def _sz_ctx(snapshot: SZMigrationSnapshot) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    for mac, rc in snapshot.ap_radio_config.items():
        if rc.get("error"):
            details[mac] = {"_error": rc["error"]}
        elif "lan" in rc:
            details[mac] = {"specific": rc["lan"]}
    zone_models = {
        m: ({"_error": e["error"]} if e.get("error") else e.get("settings") or {})
        for m, e in snapshot.zone_ap_models.items()
    }
    profiles: Dict[str, Any] = {}
    for e in snapshot.zone_ap_models.values():
        profiles.update(e.get("eth_profiles") or {})
    profiles.update((snapshot.ap_lan or {}).get("profiles") or {})
    return {
        "sz_aps": lan_sz_aps(snapshot),
        "sz_details": details,
        "zone_models": zone_models,
        "group_models": (snapshot.ap_lan or {}).get("group_models") or {},
        "sz_profiles": profiles,
    }


def sz_sources(snapshot: SZMigrationSnapshot) -> Dict[str, int]:
    """From SZ alone: how many APs take their ports from their own settings, an AP Group, or the zone."""
    ctx = _sz_ctx(snapshot)
    out: Counter = Counter()
    for ap in ctx["sz_aps"]:
        detail = ctx["sz_details"].get(ap["mac"]) or {}
        zone = ctx["zone_models"].get(ap.get("model") or "")
        group = ctx["group_models"].get(f"{ap.get('group_id')}|{ap.get('model')}")
        if detail.get("_error") or not zone or zone.get("_error") or (isinstance(group, dict) and group.get("_error")):
            out["unknown"] += 1
            continue
        sz = sz_effective(detail.get("specific"), group if isinstance(group, dict) else None, zone,
                          ctx["sz_profiles"])
        out[sz.get("ports_source") or "zone"] += 1
    return dict(out)


def build_lan_ports_audit(snapshot: SZMigrationSnapshot, r1_lan: Optional[Dict[str, Any]],
                          r1_read: Optional[Dict[str, Any]] = None) -> LanPortsAudit:
    if snapshot.ap_lan is None:
        return LanPortsAudit(available=False, reason=(
            "The SmartZone capture predates LAN port capture: refresh the SZ side to audit AP LAN ports"))
    if r1_lan is None:
        # The per-AP R1 read (one+ GET per AP) runs on demand, after the audit.
        src = sz_sources(snapshot)
        return LanPortsAudit(available=False, deferred=True, sz_sources=src, reason=(
            "R1 ports not checked yet: SZ gives "
            f"{src.get('ap', 0)} AP(s) their own port settings and {src.get('group', 0)} AP(s) AP Group "
            f"port settings; {src.get('zone', 0)} follow the zone"))
    if r1_lan.get("_error"):
        return LanPortsAudit(available=False, reason=f"R1 LAN port read failed: {r1_lan['_error']}")

    ctx = {**_sz_ctx(snapshot), **{k: r1_lan.get(k) or ({} if k != "r1_aps" else []) for k in R1_LAN_KEYS}}
    built = build_rows(ctx)
    unreadable_r1 = {s for s, st in (r1_lan.get("ap_states") or {}).items() if (st or {}).get("_error")}

    rows: List[LanPortAuditRow] = []
    for r in built["rows"]:
        status = _STATUS.get(r["status"], "unreadable")
        if r["status"] == BLOCKED and r.get("r1") and norm_serial(r["r1"].get("serial")) in unreadable_r1:
            status = "pending"
        rows.append(LanPortAuditRow(
            status=status,
            sz_name=r["sz_name"], sz_mac=r["sz_mac"], serial=r.get("serial"), model=r.get("model"),
            sz_group=r.get("sz_group"), source=r.get("source") or "—",
            r1_name=(r.get("r1") or {}).get("name"), r1_status=(r.get("r1") or {}).get("status"),
            r1_use_venue=r.get("r1_use_venue"),
            sz_ports=r["sz_ports"], r1_ports=r["r1_ports"], changes=r["changes"],
            poe=r["poe"], r1_poe=r["r1_poe"], uplink_ports=r["uplink_ports"],
            notes=r["notes"] + r["cautions"], blockers=r["blockers"], extras=r["extras"],
        ))
    order = {"differs": 0, "pending": 1, "unreadable": 2, "not_in_r1": 3, "match": 4}
    rows.sort(key=lambda x: (order.get(x.status, 9), x.sz_name or ""))
    return LanPortsAudit(available=True, rows=rows, counts=dict(Counter(r.status for r in rows)),
                         model_notes=built["model_notes"], sz_sources=sz_sources(snapshot),
                         r1_read=r1_read or {})
