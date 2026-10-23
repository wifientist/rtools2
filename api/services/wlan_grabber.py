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
    SZ Hotspot     -> wispr (default) or open. An open WLAN on an external
                      SZ hotspot (WISPr) portal.

    psk   -- SSID, passphrase, security (WPA2 / WPA3 / mixed), VLAN
    aaa   -- SSID, VLAN, linked to the R1 RADIUS profile chosen for the SZ
             auth (and accounting) service
    dpsk  -- SSID, VLAN, linked to an R1 DPSK pool (existing or new). No
             SZ passphrase, DPSK key or identity is copied.
    wispr -- an R1 guest network (WISPr), built from SZ: portal and
             redirect URL, provider (the SZ portal's name), walled garden,
             session times, bypass CNA, MAC bypass, VLAN and settings;
             RADIUS auth with the R1 profile mapped from the SZ auth service,
             proxied as SZ proxies it (no SZ auth service = Always Accept).
             The SZ portal's signature signing key is R1's integration key,
             and MAC/IP are encrypted exactly when SZ signs (no key in SZ =
             no key, no encryption). Using R1's key instead is an opt-in
             override; the tenant's R1 WISPr networks are only compared.

Every mode also carries the advanced settings field_mappings knows how to
copy; values R1 would reject are dropped and noted.

"Exists" is decided tenant-wide by network name: R1 network names are unique
in the tenant, so a taken name cannot be created anyway. A network that only
shares the SSID is a review note, not a match -- many properties broadcast
the same "Guest".

Passphrases are read and used server-side only: build_spec() keeps the one
it finds under "passphrase", and preview rows never carry it. An R1
integration key never leaves the server either: wispr_key_sources() reports
which networks share a key, never the key.

Pure: no I/O.
"""

import copy
import hashlib
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
    "dpsk_external": ("dpsk",),
    "open": ("open",),
    "unsupported": (),
}
HOTSPOT_MODES = ("wispr", "open")

# SZ WLAN types served by a hotspot (WISPr) portal.
SZ_HOTSPOT_TYPES = ("Hotspot", "Hotspot_MacByPass")

R1_NAME_LEN = (2, 32)
R1_SSID_LEN = (1, 32)
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
HEX_OR_ALNUM16 = re.compile(r"^[a-zA-Z0-9]{16}$")   # R1 wisprPage.integrationKey


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

    portal_ref = raw.get("portalServiceProfile") or {}
    hotspot = raw.get("type") in SZ_HOTSPOT_TYPES and bool(portal_ref.get("id"))

    return {
        "sz_wlan_id": full.id,
        "sz_name": full.name,
        "sz_ssid": full.ssid,
        "auth_type": auth_type,
        "sz_type": mapping.r1_network_type,
        "external_dpsk": mapping.dpsk_type == "external",
        "vlan_id": vlan_id,
        "sz_portal_id": portal_ref.get("id") if hotspot else None,
        "sz_portal_name": portal_ref.get("name") if hotspot else None,
        "auth_service_id": full.auth_service_id,
        "accounting_service_id": full.accounting_service_id,
        "advanced": advanced,
        "hotspot": hotspot,
        "blockers": blockers,
        "cautions": cautions,
    }


def modes_for(base: Dict[str, Any]) -> tuple:
    if base["sz_type"] == "open" and base["hotspot"]:
        return HOTSPOT_MODES
    return MODES.get(base["sz_type"], ())


def sz_portal_facts(portal: Optional[Dict[str, Any]], raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    What an SZ hotspot portal (GET /rkszones/{z}/portals/hotspot/{id}) and
    its WLAN give a WISPr network.
    """
    portal, raw = portal or {}, raw or {}
    session = portal.get("userSession") or {}
    auth = raw.get("authServiceOrProfile") or {}
    acct = raw.get("accountingServiceOrProfile") or {}
    always_accept = not auth.get("id") or "always accept" in str(auth.get("name") or "").lower()
    return {
        "provider": portal.get("name") or None,
        "bypass_cna": bool(raw.get("bypassCNA")),
        # SZ says MAC bypass in the WLAN type; R1 checks it against the RADIUS server.
        "mac_bypass": raw.get("type") == "Hotspot_MacByPass",
        # The key itself stays server-side (see _mode); rows only say whether SZ signs.
        "has_signing_key": bool(portal.get("signatureSigningKey")),
        "auth_type": "ALWAYS_ACCEPT" if always_accept else "RADIUS",
        "auth_proxy": bool(auth.get("throughController")) if not always_accept else None,
        "accounting_proxy": bool(acct.get("throughController")) if acct.get("id") else None,
        "portal_type": portal.get("portalType"),
        "portal_url": portal.get("portalUrl") or None,
        "redirect_url": (portal.get("redirect") or {}).get("url") or None,
        "walled_gardens": list(portal.get("walledGardens") or []),
        "session_timeout": _int(session.get("timeoutInMin")),
        "grace_period": _int(session.get("gracePeriodInMin")),
    }


def _mode(base: Dict[str, Any], raw: Dict[str, Any], r1_type: str,
          portal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    One way to create it: {r1_type, security, blockers, cautions, notes, passphrase}.
    blockers/cautions include the WLAN-wide ones; notes are informational.
    """
    sz_type = base["sz_type"]
    blockers, cautions, notes = list(base["blockers"]), list(base["cautions"]), []
    out = {"r1_type": r1_type, "security": None, "passphrase": None, "wispr_key": None, "portal": None}

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
    elif r1_type == "wispr":
        if portal is None or portal.get("_error"):
            blockers.append(f"Could not read SZ hotspot portal '{base['sz_portal_name'] or base['sz_portal_id']}'")
        else:
            facts = sz_portal_facts(portal, raw)
            out["portal"] = facts
            if facts["portal_type"] != "External":
                blockers.append(f"SZ hotspot portal is {facts['portal_type'] or 'not external'} — "
                                "R1 WISPr needs an external portal")
            elif not facts["portal_url"]:
                blockers.append("SZ hotspot portal has no portal URL")
            key = (portal or {}).get("signatureSigningKey") or None
            if key and not HEX_OR_ALNUM16.match(key):
                facts["has_signing_key"] = False
                cautions.append("SZ signature signing key does not fit R1's integration key (16 letters or "
                                "digits) — not copied; MAC/IP will not be encrypted")
            else:
                out["wispr_key"] = key
            if facts["mac_bypass"] and facts["auth_type"] != "RADIUS":
                blockers.append("SZ uses MAC bypass with no auth service — R1 needs a RADIUS server for "
                                "MAC bypass and refuses it with Always Accept")
            notes.append(f"SZ portal '{base['sz_portal_name']}': {facts['portal_url']}; "
                         f"{len(facts['walled_gardens'])} walled-garden entries; "
                         + ("RADIUS auth" if facts["auth_type"] == "RADIUS" else "Always Accept")
                         + ("; MAC bypass via RADIUS" if facts["mac_bypass"] else ""))
    elif r1_type == "dpsk":
        if sz_type == "psk":
            notes.append("Converted from PSK — the SZ passphrase is not copied; keys come from the DPSK pool")
        elif sz_type == "aaa":
            notes.append("Converted from Enterprise — SZ RADIUS is not used")
        elif base["external_dpsk"]:
            notes.append("SZ external DPSK (Cloudpath/RADIUS) converted to an internal R1 DPSK network")

    if r1_type in ("psk", "dpsk") and base["auth_type"].endswith("+ Portal"):
        notes.append(f"SZ also puts portal '{(raw.get('portalServiceProfile') or {}).get('name') or '?'}' "
                     "on this WLAN — not copied")
    if r1_type not in ("aaa", "wispr") and base["accounting_service_id"]:
        cautions.append("SZ accounting service is not copied (only linked on Enterprise networks)")
    return {**out, "blockers": blockers, "cautions": cautions, "notes": notes}


def build_spec(raw: Dict[str, Any], r1_type: Optional[str] = None,
               portals: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    The R1 network one SZ WLAN (detail object) becomes as r1_type (default:
    its first mode), for apply. Carries the passphrase -- server-side only.
    An r1_type the WLAN cannot take is a blocker.
    """
    base = _base(raw)
    modes = modes_for(base)
    chosen = r1_type or (modes[0] if modes else base["sz_type"])
    portal = (portals or {}).get(base["sz_portal_id"]) if base["sz_portal_id"] else None
    spec = {**base, **_mode(base, raw, chosen, portal)}
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
    portals: Optional[Dict[str, Dict[str, Any]]] = None,
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
        modes = modes_for(base)
        portal = (portals or {}).get(base["sz_portal_id"]) if base["sz_portal_id"] else None
        options = {}
        for m in modes or (base["sz_type"],):
            opt = _mode(base, raw, m, portal)
            opt.pop("passphrase")
            opt.pop("wispr_key")
            options[m] = opt
        default = next(iter(options))

        notes: List[str] = []
        row = {
            **{k: base[k] for k in ("sz_wlan_id", "sz_name", "sz_ssid", "auth_type", "sz_type",
                                    "vlan_id", "auth_service_id", "accounting_service_id",
                                    "sz_portal_id", "sz_portal_name")},
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
    Enterprise or WISPr guest, with a suggested R1 profile for each
    (radius_choices.suggest).

    refs: "auth_service:{id}" / "accounting_service:{id}" -> SZ service raw.
    r1_profiles: already reduced by r1_profile_summary (no secrets).
    """
    wlans = [
        SimpleNamespace(id=r["sz_wlan_id"], name=r["sz_name"], auth_type=r["auth_type"],
                        auth_service_id=r["auth_service_id"],
                        accounting_service_id=r["accounting_service_id"])
        for r in rows if r["status"] != EXISTS and any(
            m in r["modes"] and not r["modes"][m]["blockers"] for m in ("aaa", "wispr"))
    ]
    snapshot = SimpleNamespace(wlans=wlans, referenced_objects={
        k: SimpleNamespace(name=(v or {}).get("name"), raw=v or {}) for k, v in refs.items()
    })
    services = referenced_services(snapshot)
    for s in services:
        s["suggestion"] = suggest(s, r1_profiles)
    return services


# ── WISPr ──────────────────────────────────────────────────────────────

def _key_id(key: Optional[str]) -> Optional[str]:
    """Which networks share a key, without the key (not reversible from 8 hex chars of a hash)."""
    return hashlib.sha256(key.encode()).hexdigest()[:8] if key else None


def is_wispr_network(network: Dict[str, Any]) -> bool:
    """A query row or full GET of an R1 WISPr guest network."""
    if str(network.get("nwSubType") or network.get("type") or "").lower() != "guest":
        return False
    portal = network.get("guestPortal") or {}
    return (network.get("captiveType") or portal.get("guestNetworkType")) == "WISPr"


def wispr_key_sources(fulls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The tenant's WISPr networks that hold an integration key, grouped by key,
    most used first -- one network per group to copy it from. The key itself
    is never returned: key_group is a label ("key A") shared by its networks.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for f in fulls:
        if not is_wispr_network(f):
            continue
        kid = _key_id(((f.get("guestPortal") or {}).get("wisprPage") or {}).get("integrationKey"))
        if not kid:
            continue
        g = groups.setdefault(kid, {"network_id": f.get("id"), "name": f.get("name"), "count": 0, "examples": []})
        g["count"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(f.get("name"))
    out = sorted(groups.values(), key=lambda g: (-g["count"], g["name"] or ""))
    for i, g in enumerate(out):
        g["key_group"] = f"key {chr(ord('A') + i)}"
    return out


def wispr_integration_key(network: Dict[str, Any]) -> Optional[str]:
    return ((network.get("guestPortal") or {}).get("wisprPage") or {}).get("integrationKey") or None


def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def build_wispr_payload(spec: Dict[str, Any], integration_key: Optional[str] = None,
                        encrypt_mac_ip: Optional[bool] = None, enable_dhcp: bool = False,
                        description: Optional[str] = None) -> Dict[str, Any]:
    """
    The POST /wifiNetworks body for one WISPr guest network, from the SZ spec
    (its portal facts). integration_key is the SZ signing key unless the user
    overrode it; MAC/IP are encrypted when there is a key, unless
    encrypt_mac_ip says otherwise. R1 defaults everything else.
    """
    if encrypt_mac_ip is None:
        encrypt_mac_ip = bool(integration_key)
    facts = spec.get("portal") or {}
    url = facts.get("portal_url")
    wispr_page: Dict[str, Any] = {
        "captivePortalUrl": url,
        "externalProviderName": facts.get("provider") or "Custom Provider",
        "customExternalProvider": True,
        "encryptMacIpEnabled": bool(encrypt_mac_ip),
        "authType": facts.get("auth_type") or "ALWAYS_ACCEPT",
    }
    portal: Dict[str, Any] = {
        "guestNetworkType": "WISPr",
        "wisprPage": wispr_page,
        "externalPortalUrl": url,
        "redirectUrl": facts.get("redirect_url") or url,
        "walledGardens": list(facts.get("walled_gardens") or []),
    }
    if integration_key:
        wispr_page["integrationKey"] = integration_key
        portal["externalPortalKey"] = integration_key   # R1 keeps the two equal
    if facts.get("session_timeout") is not None:
        portal["userSessionTimeout"] = facts["session_timeout"]
    if facts.get("grace_period") is not None:
        portal["userSessionGracePeriod"] = facts["grace_period"]

    wlan: Dict[str, Any] = {
        "ssid": spec["sz_ssid"],
        "vlanId": int(spec["vlan_id"]),
        "enabled": True,
        "wlanSecurity": "None",
        "bypassCNA": bool(facts.get("bypass_cna")),
        "bypassCPUsingMacAddressAuthentication": bool(facts.get("mac_bypass")),
    }
    _merge(wlan, spec.get("advanced") or {})

    payload: Dict[str, Any] = {
        "type": "guest",
        "name": spec["sz_name"],
        "wlan": wlan,
        "guestPortal": portal,
        "enableDhcp": bool(enable_dhcp),
    }
    if description:
        payload["description"] = description
    return payload


def wispr_radius_settings(spec: Dict[str, Any]) -> Dict[str, Any]:
    """R1 radiusServerProfileSettings matching how SZ proxies the WLAN's RADIUS."""
    facts = spec.get("portal") or {}
    out = {}
    if facts.get("auth_proxy") is not None:
        out["enableAuthProxy"] = facts["auth_proxy"]
    if facts.get("accounting_proxy") is not None:
        out["enableAccountingProxy"] = facts["accounting_proxy"]
    return out


def wispr_r1_norms(fulls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    How the tenant's R1 WISPr networks are mostly set up, to compare SZ
    against (never to copy from). Each field: {value, count}; total = networks.
    """
    rows = [f for f in fulls if is_wispr_network(f)]

    def most(get):
        c = Counter(get(f) for f in rows)
        if not c:
            return None
        value, count = c.most_common(1)[0]
        return {"value": list(value) if isinstance(value, tuple) else value, "count": count}

    wp = lambda f: (f.get("guestPortal") or {}).get("wisprPage") or {}
    return {
        "total": len(rows),
        "portal_url": most(lambda f: wp(f).get("captivePortalUrl")),
        "provider": most(lambda f: wp(f).get("externalProviderName")),
        "auth_type": most(lambda f: wp(f).get("authType")),
        "encrypt_mac_ip": most(lambda f: bool(wp(f).get("encryptMacIpEnabled") and wp(f).get("integrationKey"))),
        "walled_gardens": most(lambda f: tuple(sorted((f.get("guestPortal") or {}).get("walledGardens") or []))),
    }


def wispr_r1_check(facts: Dict[str, Any], norms: Dict[str, Any]) -> List[str]:
    """Where an SZ portal differs from how most of the tenant's R1 WISPr networks are set up. Info only."""
    total = (norms or {}).get("total") or 0
    if not total or not facts:
        return []
    out = []

    def differs(label, sz_value, norm, show=lambda v: v):
        if norm and norm["value"] != sz_value:
            out.append(f"R1 check: {label} {show(sz_value)!s} from SZ; {norm['count']} of {total} "
                       f"R1 WISPr networks use {show(norm['value'])!s}")

    differs("portal URL", facts.get("portal_url"), norms.get("portal_url"))
    differs("provider", facts.get("provider"), norms.get("provider"))
    differs("auth", facts.get("auth_type"), norms.get("auth_type"))
    differs("MAC/IP encryption", bool(facts.get("has_signing_key")), norms.get("encrypt_mac_ip"),
            lambda v: "on" if v else "off")
    differs("walled garden", sorted(facts.get("walled_gardens") or []), norms.get("walled_gardens"),
            lambda v: f"{len(v)} entries")
    return out
