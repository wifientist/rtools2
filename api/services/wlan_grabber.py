"""
WLAN Grabber: create in R1 the SZ WLANs that R1 does not have yet.

One SZ zone in, one R1 tenant out. A network is created tenant-wide and is
NOT activated anywhere: activation is the WLAN Activator's job. The venue is
optional context only -- the DPSK networks already active there suggest which
DPSK pool a new DPSK network should be tied to.

What gets created, per SZ auth type (services/sz_migration/mapper.py):

    psk   -- SSID, passphrase, security (WPA2 / WPA3 / mixed), VLAN
    open  -- SSID, VLAN; a captive portal is not copied
    aaa   -- SSID, VLAN, linked to the R1 RADIUS profile chosen for the SZ
             auth (and accounting) service: an existing one, or one created
             from the SZ servers with a secret typed by the user
    dpsk  -- SSID, VLAN, linked to the R1 DPSK pool the user picks (no
             passphrases or identities are copied)

All four also carry the advanced settings field_mappings knows how to copy;
values R1 would reject are dropped and noted.

"Exists" is decided tenant-wide by network name: R1 network names are unique
in the tenant, so a taken name cannot be created anyway. A network that only
shares the SSID is a review note, not a match -- many properties broadcast
the same "Guest".

Passphrases are read and used server-side only: build_spec() keeps the one
it finds under "passphrase", and public_spec() is what may leave the server.

Pure: no I/O.
"""

import re
from collections import Counter
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from schemas.sz_migration import SZWLANFull
from services.sz_migration.field_mappings import build_r1_advanced_settings
from services.sz_migration.mapper import map_wlan_to_r1_type
from services.sz_migration.radius_choices import referenced_services, suggest
from szapi.services.wlans import WlanService

EXISTS = "exists"    # R1 already has a network by this name -- nothing to create
CREATE = "create"    # create it; preselected
REVIEW = "review"    # can be created, but read the notes first
BLOCKED = "blocked"  # cannot be created as it stands

STATUSES = (CREATE, REVIEW, EXISTS, BLOCKED)

R1_NAME_LEN = (2, 32)
R1_SSID_LEN = (1, 32)
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _ref_name(ref: Any) -> Optional[str]:
    if isinstance(ref, dict):
        return ref.get("name") or ref.get("id")
    return ref or None


def psk_security(raw: Dict[str, Any]) -> Dict[str, Any]:
    """SZ encryption.method -> R1 PSK security (create_wifi_network's names), plus a caution."""
    method = str((raw.get("encryption") or {}).get("method") or "").upper()
    if "WPA23" in method or ("WPA2" in method and "WPA3" in method):
        return {"security": "WPA2/WPA3", "caution": None}
    if "WPA3" in method:
        return {"security": "WPA3", "caution": None}
    if "WPA2" in method:
        return {"security": "WPA2", "caution": None}
    if method == "WPA_MIXED":
        return {"security": "WPA2", "caution": "SZ is WPA/WPA2 mixed — R1 network is created as WPA2"}
    return {"security": "WPA2", "caution": f"SZ uses legacy {method or 'WPA'} — R1 network is created as WPA2"}


def psk_passphrase(raw: Dict[str, Any], security: str) -> Optional[str]:
    enc = raw.get("encryption") or {}
    if security == "WPA3":
        return enc.get("saePassphrase") or enc.get("passphrase")
    return enc.get("passphrase") or enc.get("saePassphrase")


def passphrase_problem(passphrase: Optional[str]) -> Optional[str]:
    """Why R1 would refuse this PSK passphrase, or None. Never echoes it."""
    if not passphrase:
        return "SZ returned no passphrase for this WLAN"
    if len(passphrase) == 64:
        return None if HEX64.match(passphrase) else "SZ passphrase is 64 characters but not hex"
    if not 8 <= len(passphrase) <= 63:
        return f"SZ passphrase is {len(passphrase)} characters; R1 needs 8–63"
    return None


def build_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    What R1 network one SZ WLAN (detail object) becomes, and what stands in
    the way. Carries the passphrase -- server-side only; see public_spec().
    """
    auth_type = WlanService.extract_auth_type(raw)
    full = SZWLANFull.from_sz_response(raw, auth_type)
    mapping = map_wlan_to_r1_type(full)
    r1_type = mapping.r1_network_type

    vlan = raw.get("vlan") or {}
    dropped: List[Dict[str, Any]] = []
    spec: Dict[str, Any] = {
        "sz_wlan_id": full.id,
        "sz_name": full.name,
        "sz_ssid": full.ssid,
        "auth_type": auth_type,
        "r1_type": r1_type,
        "security": None,
        "vlan_id": _int(vlan.get("accessVlan")) or 1,
        "auth_service_id": full.auth_service_id,
        "accounting_service_id": full.accounting_service_id,
        "external_dpsk": mapping.dpsk_type == "external",
        "advanced": {},
        "blockers": [],
        "cautions": [],
        "passphrase": None,
    }
    blockers, cautions = spec["blockers"], spec["cautions"]

    if r1_type == "unsupported":
        blockers.append(mapping.notes or f"SZ auth type '{auth_type}' has no R1 equivalent")
        return spec

    lo, hi = R1_NAME_LEN
    if not lo <= len(full.name) <= hi:
        blockers.append(f"Name is {len(full.name)} characters; R1 network names are {lo}–{hi}")
    lo, hi = R1_SSID_LEN
    if not lo <= len(full.ssid) <= hi:
        blockers.append(f"SSID is {len(full.ssid)} characters; R1 allows {lo}–{hi}")

    if r1_type == "psk":
        sec = psk_security(raw)
        spec["security"] = sec["security"]
        if sec["caution"]:
            cautions.append(sec["caution"])
        passphrase = psk_passphrase(raw, sec["security"])
        problem = passphrase_problem(passphrase)
        if problem:
            blockers.append(problem)
        else:
            spec["passphrase"] = passphrase
    elif r1_type == "aaa":
        spec["security"] = "WPA3" if "WPA3" in auth_type else "WPA2Enterprise"
        if not full.auth_service_id:
            blockers.append("SZ WLAN has no authentication service to link")
    elif r1_type == "open":
        if auth_type == "Open + Portal":
            cautions.append("SZ captive portal is not copied — created as a plain Open network")
    elif r1_type == "dpsk":
        if spec["external_dpsk"]:
            cautions.append("SZ uses external DPSK (Cloudpath/RADIUS) — the R1 network uses an R1 DPSK pool instead")

    if r1_type != "aaa" and full.accounting_service_id:
        cautions.append("SZ accounting service is not copied (only linked on Enterprise networks)")

    pool = _ref_name(vlan.get("vlanPooling"))
    if pool:
        cautions.append(f"SZ uses VLAN pool '{pool}' — only its access VLAN {spec['vlan_id']} is copied")
    if vlan.get("aaaVlanOverride") and r1_type != "aaa":
        cautions.append("SZ allows AAA VLAN override — not copied")

    spec["advanced"] = build_r1_advanced_settings(raw, dropped)
    if dropped:
        cautions.append("Left at R1 default: " + ", ".join(d["label"] for d in dropped))
    return spec


def public_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """A spec that may leave the server: no passphrase, no payload internals."""
    return {k: v for k, v in spec.items() if k not in ("passphrase", "advanced")}


def tenant_match(spec: Dict[str, Any], tenant_networks: List[Dict[str, Any]],
                 venue_id: Optional[str]) -> Dict[str, Any]:
    """
    The R1 network already holding this WLAN's name (tenant-wide), and any
    differently named networks broadcasting its SSID.
    """
    name = spec["sz_name"]
    exact = [n for n in tenant_networks if n.get("name") == name]
    folded = [n for n in tenant_networks if (n.get("name") or "").lower() == name.lower()]
    hit = (exact or folded or [None])[0]
    same_ssid = [n.get("name") for n in tenant_networks
                 if n.get("ssid") == spec["sz_ssid"] and n is not hit]

    existing = None
    if hit is not None:
        venues = {v.get("venueId") for v in hit.get("venueApGroups") or []} - {None}
        existing = {
            "network_id": hit.get("id"),
            "name": hit.get("name"),
            "ssid": hit.get("ssid"),
            "type": str(hit.get("nwSubType") or "").lower() or None,
            "venue_count": len(venues),
            "active_in_venue": (venue_id in venues) if venue_id else None,
        }
    return {"existing": existing, "same_ssid": same_ssid}


def dpsk_pool_suggestion(venue_links: Dict[str, List[Dict[str, Any]]],
                         venue_name: str) -> Optional[Dict[str, Any]]:
    """
    The DPSK pool the venue's DPSK networks are tied to, as a starting choice.

    venue_links: network_id -> the pools linked to it ({id, name}).
    """
    counts = Counter(p["id"] for pools in venue_links.values() for p in pools if p.get("id"))
    if not counts:
        return None
    names = {p["id"]: p.get("name") for pools in venue_links.values() for p in pools if p.get("id")}
    pool_id, n = counts.most_common(1)[0]
    total = len(venue_links)
    reason = f"Used by {n} of {total} DPSK network{'s' if total != 1 else ''} in {venue_name}"
    if len(counts) > 1:
        reason += f" (the venue uses {len(counts)} pools)"
    return {"pool_id": pool_id, "pool_name": names.get(pool_id), "reason": reason}


def build_rows(
    sz_wlans: List[Dict[str, Any]],
    tenant_networks: List[Dict[str, Any]],
    venue_id: Optional[str] = None,
    dpsk_suggestion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One row per SZ WLAN (detail objects): what it becomes in R1, and a status."""
    rows = []
    for raw in sz_wlans:
        spec = build_spec(raw)
        match = tenant_match(spec, tenant_networks, venue_id)
        row = {**public_spec(spec), "r1": match["existing"], "notes": []}
        notes = row["notes"]
        rows.append(row)

        if match["existing"]:
            ex = match["existing"]
            row["status"] = EXISTS
            if ex["name"] != spec["sz_name"]:
                notes.append(f"R1 has it as '{ex['name']}' (case differs)")
            if ex["ssid"] != spec["sz_ssid"]:
                notes.append(f"SSID differs: SZ '{spec['sz_ssid']}', R1 '{ex['ssid']}'")
            if ex["type"] and spec["r1_type"] not in ("unsupported", ex["type"]):
                notes.append(f"Type differs: SZ maps to {spec['r1_type']}, R1 is {ex['type']}")
            if ex["active_in_venue"] is False:
                notes.append("Not activated in this venue — use WLAN Activator")
            elif not ex["venue_count"]:
                notes.append("Not activated in any venue — use WLAN Activator")
            continue

        if spec["blockers"]:
            row["status"] = BLOCKED
            notes.extend(spec["blockers"])
            notes.extend(spec["cautions"])
            continue

        cautions = list(spec["cautions"])
        if match["same_ssid"]:
            shown = ", ".join(f"'{n}'" for n in match["same_ssid"][:3])
            more = len(match["same_ssid"]) - 3
            cautions.insert(0, f"SSID already broadcast by R1 network {shown}"
                               + (f" and {more} more" if more > 0 else ""))
        if spec["r1_type"] == "dpsk":
            if dpsk_suggestion:
                row["suggested_dpsk_pool_id"] = dpsk_suggestion["pool_id"]
                notes.append(f"DPSK pool '{dpsk_suggestion['pool_name']}': {dpsk_suggestion['reason']}")
            else:
                cautions.append("Pick a DPSK pool — without one R1 will not activate it (WIFI-20300)")
        notes[:0] = cautions
        row["status"] = REVIEW if cautions else CREATE

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    return {"rows": rows, "counts": counts}


def radius_services(rows: List[Dict[str, Any]], refs: Dict[str, Dict[str, Any]],
                    r1_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The SZ RADIUS services the Enterprise WLANs still to be created use,
    with a suggested R1 profile for each (radius_choices.suggest).

    refs: "auth_service:{id}" / "accounting_service:{id}" -> SZ service raw.
    r1_profiles: already reduced by r1_profile_summary (no secrets).
    """
    wlans = [
        SimpleNamespace(id=r["sz_wlan_id"], name=r["sz_name"], auth_type=r["auth_type"],
                        auth_service_id=r["auth_service_id"],
                        accounting_service_id=r["accounting_service_id"])
        for r in rows if r["r1_type"] == "aaa" and r["status"] in (CREATE, REVIEW)
    ]
    snapshot = SimpleNamespace(wlans=wlans, referenced_objects={
        k: SimpleNamespace(name=(v or {}).get("name"), raw=v or {}) for k, v in refs.items()
    })
    services = referenced_services(snapshot)
    for s in services:
        s["suggestion"] = suggest(s, r1_profiles)
    return services
