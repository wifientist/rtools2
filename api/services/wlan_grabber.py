"""
WLAN Grabber: create in R1 the SZ WLANs that R1 does not have yet.

One SZ zone in, one R1 tenant out. A network is created tenant-wide and is
NOT activated anywhere: activation is the WLAN Activator's job. The venue is
optional context only -- the DPSK networks already active there suggest the
DPSK pool new DPSK networks are tied to (usually one pool serves every DPSK
network in a venue).

Each SZ WLAN can be created in one or more MODES (the R1 network type):

    SZ PSK         -> psk (default) or dpsk
    SZ Enterprise  -> dpsk (default) or aaa
    SZ DPSK        -> dpsk; external DPSK (Cloudpath/RADIUS) is converted
                      to an internal R1 DPSK network
    SZ Open        -> open; a captive portal is not copied

    psk   -- SSID, passphrase, security (WPA2 / WPA3 / mixed), VLAN
    aaa   -- SSID, VLAN, linked to the R1 RADIUS profile chosen for the SZ
             auth (and accounting) service
    dpsk  -- SSID, VLAN, linked to an R1 DPSK pool (existing or new). No
             SZ passphrase, DPSK key or identity is copied.

Every mode also carries the advanced settings field_mappings knows how to
copy; values R1 would reject are dropped and noted.

"Exists" is decided tenant-wide by network name: R1 network names are unique
in the tenant, so a taken name cannot be created anyway. A network that only
shares the SSID is a review note, not a match -- many properties broadcast
the same "Guest".

Passphrases are read and used server-side only: build_spec() keeps the one
it finds under "passphrase", and preview rows never carry it.

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

# SZ-mapped type -> the R1 types it may be created as, default first.
MODES = {
    "psk": ("psk", "dpsk"),
    "aaa": ("dpsk", "aaa"),
    "dpsk": ("dpsk",),
    "open": ("open",),
    "unsupported": (),
}

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


def _base(raw: Dict[str, Any]) -> Dict[str, Any]:
    """What one SZ WLAN is, independent of the R1 type it becomes."""
    auth_type = WlanService.extract_auth_type(raw)
    full = SZWLANFull.from_sz_response(raw, auth_type)
    mapping = map_wlan_to_r1_type(full)
    vlan = raw.get("vlan") or {}

    blockers: List[str] = []
    cautions: List[str] = []
    if mapping.r1_network_type == "unsupported":
        blockers.append(mapping.notes or f"SZ auth type '{auth_type}' has no R1 equivalent")
    lo, hi = R1_NAME_LEN
    if not lo <= len(full.name) <= hi:
        blockers.append(f"Name is {len(full.name)} characters; R1 network names are {lo}–{hi}")
    lo, hi = R1_SSID_LEN
    if not lo <= len(full.ssid) <= hi:
        blockers.append(f"SSID is {len(full.ssid)} characters; R1 allows {lo}–{hi}")

    vlan_id = _int(vlan.get("accessVlan")) or 1
    pool = _ref_name(vlan.get("vlanPooling"))
    if pool:
        cautions.append(f"SZ uses VLAN pool '{pool}' — only its access VLAN {vlan_id} is copied")
    dropped: List[Dict[str, Any]] = []
    advanced = build_r1_advanced_settings(raw, dropped)
    if dropped:
        cautions.append("Left at R1 default: " + ", ".join(d["label"] for d in dropped))

    return {
        "sz_wlan_id": full.id,
        "sz_name": full.name,
        "sz_ssid": full.ssid,
        "auth_type": auth_type,
        "sz_type": mapping.r1_network_type,
        "external_dpsk": mapping.dpsk_type == "external",
        "vlan_id": vlan_id,
        "aaa_vlan_override": bool(vlan.get("aaaVlanOverride")),
        "auth_service_id": full.auth_service_id,
        "accounting_service_id": full.accounting_service_id,
        "advanced": advanced,
        "blockers": blockers,
        "cautions": cautions,
    }


def _mode(base: Dict[str, Any], raw: Dict[str, Any], r1_type: str) -> Dict[str, Any]:
    """
    One way to create it: {r1_type, security, blockers, cautions, notes, passphrase}.
    blockers/cautions include the WLAN-wide ones; notes are informational.
    """
    sz_type = base["sz_type"]
    blockers, cautions, notes = list(base["blockers"]), list(base["cautions"]), []
    out = {"r1_type": r1_type, "security": None, "passphrase": None}

    if r1_type == "psk":
        sec = psk_security(raw)
        out["security"] = sec["security"]
        if sec["caution"]:
            cautions.append(sec["caution"])
        passphrase = psk_passphrase(raw, sec["security"])
        problem = passphrase_problem(passphrase)
        if problem:
            blockers.append(problem)
        else:
            out["passphrase"] = passphrase
    elif r1_type == "aaa":
        out["security"] = "WPA3" if "WPA3" in base["auth_type"] else "WPA2Enterprise"
        if not base["auth_service_id"]:
            blockers.append("SZ WLAN has no authentication service to link")
    elif r1_type == "open":
        if base["auth_type"] == "Open + Portal":
            cautions.append("SZ captive portal is not copied — created as a plain Open network")
    elif r1_type == "dpsk":
        if sz_type == "psk":
            notes.append("Converted from PSK — the SZ passphrase is not copied; keys come from the DPSK pool")
        elif sz_type == "aaa":
            notes.append("Converted from Enterprise — SZ RADIUS is not used")
        elif base["external_dpsk"]:
            notes.append("SZ external DPSK (Cloudpath/RADIUS) converted to an internal R1 DPSK network")

    if r1_type != "aaa":
        if base["accounting_service_id"]:
            cautions.append("SZ accounting service is not copied (only linked on Enterprise networks)")
        if base["aaa_vlan_override"]:
            cautions.append("SZ allows AAA VLAN override — not copied")
    return {**out, "blockers": blockers, "cautions": cautions, "notes": notes}


def build_spec(raw: Dict[str, Any], r1_type: Optional[str] = None) -> Dict[str, Any]:
    """
    The R1 network one SZ WLAN (detail object) becomes as r1_type (default:
    its first mode), for apply. Carries the passphrase -- server-side only.
    An r1_type the WLAN cannot take is a blocker.
    """
    base = _base(raw)
    modes = MODES.get(base["sz_type"], ())
    chosen = r1_type or (modes[0] if modes else base["sz_type"])
    spec = {**base, **_mode(base, raw, chosen)}
    if modes and chosen not in modes:
        spec["blockers"].append(f"An SZ {base['auth_type']} WLAN cannot be created as {chosen}")
    return spec


def mode_status(mode: Dict[str, Any]) -> str:
    if mode["blockers"]:
        return BLOCKED
    return REVIEW if mode["cautions"] else CREATE


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
) -> Dict[str, Any]:
    """
    One row per SZ WLAN (detail objects): every mode it can be created in,
    each with its own blockers/cautions, and the status of the default mode.
    The page re-derives the status when the user picks another mode.
    """
    rows = []
    for raw in sz_wlans:
        base = _base(raw)
        match = tenant_match(base, tenant_networks, venue_id)
        modes = MODES.get(base["sz_type"], ())
        options = {}
        for m in modes or (base["sz_type"],):
            opt = _mode(base, raw, m)
            opt.pop("passphrase")
            options[m] = opt
        default = next(iter(options))

        notes: List[str] = []
        row = {
            **{k: base[k] for k in ("sz_wlan_id", "sz_name", "sz_ssid", "auth_type", "sz_type",
                                    "vlan_id", "auth_service_id", "accounting_service_id")},
            "modes": options,
            "default_mode": default,
            "r1": match["existing"],
            "notes": notes,
        }
        rows.append(row)

        if match["existing"]:
            ex = match["existing"]
            row["status"] = EXISTS
            if ex["name"] != base["sz_name"]:
                notes.append(f"R1 has it as '{ex['name']}' (case differs)")
            if ex["ssid"] != base["sz_ssid"]:
                notes.append(f"SSID differs: SZ '{base['sz_ssid']}', R1 '{ex['ssid']}'")
            if ex["active_in_venue"] is False:
                notes.append("Not activated in this venue — use WLAN Activator")
            elif not ex["venue_count"]:
                notes.append("Not activated in any venue — use WLAN Activator")
            continue

        if match["same_ssid"]:
            shown = ", ".join(f"'{n}'" for n in match["same_ssid"][:3])
            more = len(match["same_ssid"]) - 3
            caution = f"SSID already broadcast by R1 network {shown}" + (f" and {more} more" if more > 0 else "")
            for opt in options.values():
                opt["cautions"].insert(0, caution)
        row["status"] = mode_status(options[default])

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    return {"rows": rows, "counts": counts}


def radius_services(rows: List[Dict[str, Any]], refs: Dict[str, Dict[str, Any]],
                    r1_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The SZ RADIUS services of the WLANs that could still be created as
    Enterprise, with a suggested R1 profile for each (radius_choices.suggest).

    refs: "auth_service:{id}" / "accounting_service:{id}" -> SZ service raw.
    r1_profiles: already reduced by r1_profile_summary (no secrets).
    """
    wlans = [
        SimpleNamespace(id=r["sz_wlan_id"], name=r["sz_name"], auth_type=r["auth_type"],
                        auth_service_id=r["auth_service_id"],
                        accounting_service_id=r["accounting_service_id"])
        for r in rows if "aaa" in r["modes"] and r["status"] != EXISTS
        and not r["modes"]["aaa"]["blockers"]
    ]
    snapshot = SimpleNamespace(wlans=wlans, referenced_objects={
        k: SimpleNamespace(name=(v or {}).get("name"), raw=v or {}) for k, v in refs.items()
    })
    services = referenced_services(snapshot)
    for s in services:
        s["suggestion"] = suggest(s, r1_profiles)
    return services
