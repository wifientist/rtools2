"""
AP Port Grabber: each SZ AP's effective LAN ports, compared with the same AP in R1.

SZ resolves an AP's LAN ports and PoE per AP model, most specific first:

    AP            GET /aps/{mac}                             .specific.{lanPorts, poeModeSetting, poeOutPortEnabled}
    AP Group      GET /rkszones/{z}/apgroups/{g}/apmodel/{m}  null = not overridden
    Zone          GET /rkszones/{z}/apmodel/{m}

Each port points at a zone Ethernet Port Profile (type, untagged VLAN,
members); an AP-level port can also override the VLANs
(overwriteVlanEnabled + vlanUntagId / members).

R1 has no AP Group level. The venue holds per-model ports (migrated with the
venue, services/sz_migration/lan_ports.py); an AP either uses them
(lanPortSpecificSettings.useVenueSettings) or has its own: per port, an
Ethernet Port Profile plus overwriteUntagId / overwriteVlanMembers. So an SZ
AP-level or AP-Group-level difference lands on R1 as a per-AP override.

THE UPLINK IS NEVER WRITTEN. A change to the port that connects and powers
the AP can cut it off, so an AP's uplink ports (uplink_ports: R1's PoE-in /
trunk-only flags, the static model map, and the AP's live poePort -- all of
them, unioned) are left out of every plan. When R1 doesn't say which port is
the uplink, the AP is blocked. The phase checks again, live, before writing.

What is carried: on/off, type, untagged VLAN, members, PoE mode, PoE out.
The profile only has to match the TYPE -- the exact VLANs are written as the
port's override whenever the chosen profile's differ, so the end result is
SZ's whatever profile is picked. 802.1X, client isolation, tunnelling and
user-side limits on an SZ profile are noted, not carried.

Row statuses:
    match       R1 already gives this AP what SZ does (uplink aside)
    update      write the AP's own non-uplink ports in R1 (useVenueSettings off)
    review      an update with something to look at (PoE mode, carry limits,
                live uplink not the model's usual one)

There is no "back to venue settings": that would swap the uplink's config
source too. An R1 AP with its own settings keeps them; only its non-uplink
ports are brought in line.
    not_in_r1   no AP with this serial / MAC in the R1 venue
    blocked     can't be planned (R1 can't read the AP's ports, unknown model, ...)

Pure: no I/O.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from r1api.models.ap_models import get_uplink_port
from services.sz_migration.lan_ports import NAME_MAX, _clash_name, _plain
from services.sz_migration.venue_models import _POE, _PORT_TYPE, _port_id, _profile_extras

MATCH, UPDATE, REVIEW, NOT_IN_R1, BLOCKED = "match", "update", "review", "not_in_r1", "blocked"
ACTIONABLE = (UPDATE, REVIEW)

_R1_TYPE = {"GENERAL": "SELECTIVE_TRUNK"}
TYPE_LABEL = {"ACCESS": "Access", "TRUNK": "Trunk", "SELECTIVE_TRUNK": "General (selective trunk)"}

# SZ 802.1X port mode -> R1 authType. "Client visibility" is RoleOpen / OPEN.
SZ_AUTH = {"Disable": "DISABLED", "Supplicant": "SUPPLICANT", "PortBasedAuthenticator": "PORT_BASED_AUTHENTICATOR",
           "MACBasedAuthenticator": "MAC_BASED_AUTHENTICATOR", "RoleOpen": "OPEN"}
AUTH_LABEL = {"DISABLED": "no auth", "SUPPLICANT": "802.1X supplicant", "OPEN": "client visibility (open)",
              "PORT_BASED_AUTHENTICATOR": "802.1X port-based", "MAC_BASED_AUTHENTICATOR": "802.1X MAC-based"}


def sz_auth(prof: Dict[str, Any]) -> str:
    return SZ_AUTH.get(((prof or {}).get("_8021X") or {}).get("type") or "Disable", "DISABLED")




# ── small helpers ─────────────────────────────────────────────────────

def norm_mac(mac: Optional[str]) -> Optional[str]:
    hexes = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    return hexes.upper() if len(hexes) == 12 else None


def norm_serial(serial: Optional[str]) -> Optional[str]:
    s = (serial or "").strip().upper()
    return s or None


def sig(type_: Optional[str], untag: Any, members: Any) -> Tuple[Optional[str], Optional[int], str]:
    """(R1 type, untagged VLAN, members) -- the comparable form of a port's VLAN setup."""
    t = _R1_TYPE.get(type_, type_)
    try:
        u = int(untag) if untag not in (None, "") else None
    except (TypeError, ValueError):
        u = None
    m = str(members or "").replace(" ", "")
    if t == "ACCESS":
        # An access port carries its untagged VLAN only; members are noise.
        m = str(u) if u is not None else m
    return t, u, m


def port_sig(p: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], str]:
    return sig(p.get("type"), p.get("untagId"), p.get("vlanMembers"))


def describe(p: Dict[str, Any]) -> str:
    """'LAN2 Access 30' / 'LAN1 Trunk untag 1, 1-4094' / 'LAN3 off'."""
    head = f"LAN{p.get('portId')}"
    if p.get("enabled") is False:
        return f"{head} off"
    t, u, m = port_sig(p)
    auth = p.get("auth")
    tail = f" ({AUTH_LABEL.get(auth, auth)})" if auth and auth != "DISABLED" else ""
    if t is None:
        return f"{head} on"
    if t == "ACCESS":
        return f"{head} Access {u}{tail}"
    return f"{head} {TYPE_LABEL.get(t, t)} untag {u}, {m or '-'}{tail}"


# ── SZ: the AP's effective ports ─────────────────────────────────────

def _first(layers: List[Tuple[str, Optional[Dict[str, Any]]]], field: str, empty_ok: bool = False):
    """(value, source) from the most specific layer that sets `field`."""
    for source, layer in layers:
        if not isinstance(layer, dict):
            continue
        v = layer.get(field)
        if v is None or (v == [] and not empty_ok):
            continue
        return v, source
    return None, None


def sz_effective(specific: Optional[Dict[str, Any]], group: Optional[Dict[str, Any]],
                 zone: Optional[Dict[str, Any]], sz_profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    What SZ actually gives this AP: ports {portId: {...}}, PoE, and where each
    came from ("ap", "group", "zone"). Each port: enabled, type / untagId /
    vlanMembers (R1 words; None when the profile could not be read),
    sz_profile_id / sz_profile_name, vlan_override.
    """
    layers = [("ap", specific), ("group", group), ("zone", zone)]
    notes: List[str] = []
    extras: List[str] = []

    lan, ports_source = _first(layers, "lanPorts")
    ports: Dict[str, Dict[str, Any]] = {}
    for p in lan or []:
        pid = _port_id(p.get("portName"))
        if pid is None:
            notes.append(f"SZ port '{p.get('portName')}' has no R1 port number")
            continue
        ref = p.get("ethPortProfile") or {}
        prof = sz_profiles.get(ref.get("id")) if ref.get("id") else None
        port: Dict[str, Any] = {
            "portId": pid, "enabled": bool(p.get("enabled")),
            "sz_profile_id": ref.get("id"),
            "sz_profile_name": (prof or {}).get("name") or ref.get("name"),
            "type": None, "untagId": None, "vlanMembers": None, "vlan_override": False,
        }
        if prof and not prof.get("error") and prof.get("type") in _PORT_TYPE:
            port["type"] = _R1_TYPE.get(_PORT_TYPE[prof["type"]], _PORT_TYPE[prof["type"]])
            port["untagId"] = prof.get("untagId")
            port["vlanMembers"] = prof.get("vlanMembers")
            port["auth"] = sz_auth(prof)
            for x in _profile_extras(prof):
                if not x.startswith("802.1X"):   # auth goes with the profile mapping
                    extras.append(f"LAN{pid} profile '{prof.get('name')}': {x}")
        elif ref.get("id"):
            notes.append(f"LAN{pid}: SZ profile '{ref.get('name') or ref.get('id')}' could not be read")
        if p.get("overwriteVlanEnabled"):
            port["vlan_override"] = True
            if p.get("vlanUntagId") is not None:
                port["untagId"] = p["vlanUntagId"]
            if p.get("members"):
                port["vlanMembers"] = p["members"]
        ports[pid] = port

    poe_raw, poe_source = _first(layers, "poeModeSetting")
    poe_mode = _POE.get(poe_raw) if poe_raw else None
    if poe_raw and not poe_mode:
        notes.append(f"PoE mode {poe_raw} has no R1 value")
    poe_out, poe_out_source = _first(layers, "poeOutPortEnabled", empty_ok=True)

    return {
        "ports": ports,
        "ports_source": ports_source,
        "poeMode": poe_mode, "poe_source": poe_source if poe_mode else None,
        "poeOut": bool(poe_out) if poe_out is not None else None,
        "poe_out_source": poe_out_source,
        "notes": notes, "extras": extras,
    }


# ── R1: the AP's effective ports ─────────────────────────────────────

def r1_effective(cap: Dict[str, Any], venue_entry: Dict[str, Any], ap_state: Dict[str, Any],
                 activations: Dict[str, Dict[str, Any]],
                 venue_profiles: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    What R1 gives this AP now. ap_state: {"specific": lanPortSpecificSettings,
    "ports": {portId: lanPorts/{p}/settings}}. activations: {portId: R1
    profile} attached to this AP's ports. On venue settings, the venue's
    per-model entry applies.
    """
    specific = ap_state.get("specific") or {}
    use_venue = specific.get("useVenueSettings", True) is not False
    venue_ports = {str(p.get("portId")): p for p in venue_entry.get("lanPorts") or []}
    out_ports: Dict[str, Dict[str, Any]] = {}
    for spec in cap.get("lanPorts") or []:
        pid = str(spec.get("id"))
        v = venue_ports.get(pid) or {"type": spec.get("defaultType"), "untagId": spec.get("untagId"),
                                     "vlanMembers": spec.get("vlanMembers"), "enabled": True}
        venue_prof = (venue_profiles or {}).get(pid) or {}
        if use_venue:
            t, u, m = port_sig(v)
            out_ports[pid] = {"portId": pid, "enabled": v.get("enabled", True) is not False,
                              "type": t, "untagId": u, "vlanMembers": m, "profile_id": None,
                              "auth": venue_prof.get("authType") or "DISABLED"}
            continue
        prof = activations.get(pid)
        base = prof or v
        s = (ap_state.get("ports") or {}).get(pid) or {}
        untag = s.get("overwriteUntagId") if s.get("overwriteUntagId") is not None else base.get("untagId")
        members = s.get("overwriteVlanMembers") or base.get("vlanMembers")
        t, u, m = sig(base.get("type"), untag, members)
        out_ports[pid] = {"portId": pid, "enabled": s.get("enabled", True) is not False,
                          "type": t, "untagId": u, "vlanMembers": m,
                          "profile_id": (prof or {}).get("id"),
                          "auth": (prof or venue_prof).get("authType") or "DISABLED"}
    src = venue_entry if use_venue else specific
    return {"use_venue": use_venue, "ports": out_ports,
            "poeMode": src.get("poeMode"), "poeOut": src.get("poeOut")}


# ── the uplink: never touched ─────────────────────────────────────────

def live_poe_port(r1ap: Dict[str, Any]) -> Optional[str]:
    """
    The R1 port id (1-based) the AP is powered / connected on right now.
    R1's AP query reports poePort 0-BASED: verified 2026-11-17 on a lab
    tenant -- H510 "4" = LAN5, H320 "2" = LAN3, R720/R550 "1" = LAN2,
    R510 "0" = LAN1, each matching the model's PoE-in port.
    """
    raw = str((r1ap or {}).get("poePort") or "").strip()
    return str(int(raw) + 1) if raw.isdigit() else None


def uplink_ports(model: Optional[str], cap: Dict[str, Any], r1ap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Every port that could be the AP's uplink, from every source, unioned:
      r1_poe_in    R1 capability lanPorts[].isPoePort
      trunk_only   R1 capability lanPorts[].trunkPortOnly
      static       r1api.models.MODEL_UPLINK_PORTS (AP Port Config's map)
      live         the AP's poePort right now
    -> {"ports": [ids], "sources": {source: [ids]}, "blocker": str|None, "caution": str|None}

    Blocked when R1's capability names no PoE-in port (a multi-port model
    with no flagged uplink is a model we can't vouch for). Cautioned when the
    live port isn't the model's flagged one: both stay untouched.
    """
    ports = cap.get("lanPorts") or []
    sources = {
        "r1_poe_in": sorted(str(p["id"]) for p in ports if p.get("isPoePort")),
        "trunk_only": sorted(str(p["id"]) for p in ports if p.get("trunkPortOnly")),
        "static": [],
        "live": [],
    }
    static = get_uplink_port(model or "")
    if static and static.upper().startswith("LAN") and static[3:].isdigit():
        sources["static"] = [static[3:]]
    live = live_poe_port(r1ap)
    if live:
        sources["live"] = [live]
    union = sorted({p for v in sources.values() for p in v}, key=int)
    blocker = caution = None
    if not sources["r1_poe_in"]:
        blocker = (f"R1 doesn't say which {model} port is the uplink (no PoE-in port in its model data) — "
                   "not touched")
    elif sources["static"] and sources["static"] != sources["r1_poe_in"]:
        caution = (f"Uplink sources disagree (R1 LAN{', LAN'.join(sources['r1_poe_in'])}, "
                   f"model map LAN{sources['static'][0]}) — both left untouched")
    if live and live not in sources["r1_poe_in"]:
        caution = (f"The AP is connected on LAN{live}, not its usual uplink "
                   f"LAN{', LAN'.join(sources['r1_poe_in'])} — both left untouched")
    return {"ports": union, "sources": sources, "blocker": blocker, "caution": caution}


# ── desired: SZ's result fitted to the R1 model ──────────────────────

def desired_for(sz: Dict[str, Any], cap: Dict[str, Any], r1: Dict[str, Any],
                protected: List[str]) -> Dict[str, Any]:
    """
    SZ's effective ports on R1's ports for the model. A port SZ does not
    describe keeps R1's current value; a protected (uplink) port ALWAYS keeps
    R1's current value. Returns ports, poe, and the notes / cautions /
    blockers met on the way.
    """
    notes: List[str] = list(sz["notes"])
    cautions: List[str] = []
    blockers: List[str] = []
    specs = {str(p.get("id")): p for p in cap.get("lanPorts") or [] if p.get("id") is not None}
    ports: Dict[str, Dict[str, Any]] = {}

    for pid, spec in specs.items():
        now = r1["ports"].get(pid) or {}
        szp = sz["ports"].get(pid)
        if pid in protected:
            ports[pid] = {**{k: now.get(k) for k in ("enabled", "type", "untagId", "vlanMembers", "auth")},
                          "portId": pid, "sz_profile_id": None, "sz_profile_name": None,
                          "vlan_override": False, "protected": True}
            if szp and (bool(szp["enabled"]) != bool(now.get("enabled", True))
                        or (szp.get("type") and port_sig(szp) != port_sig(now))):
                notes.append(f"LAN{pid} is the uplink — never changed here (SZ: {describe(szp)}; "
                             f"R1: {describe(now)})")
            continue
        if not szp:
            ports[pid] = {**{k: now.get(k) for k in ("portId", "enabled", "type", "untagId", "vlanMembers", "auth")},
                          "portId": pid, "sz_profile_id": None, "sz_profile_name": None, "vlan_override": False}
            notes.append(f"LAN{pid}: not set in SZ — R1's kept")
            continue
        want = dict(szp)
        if want["type"] is None:
            # Unknown profile: carry on/off only, on R1's current type and VLANs.
            want.update(type=now.get("type"), untagId=now.get("untagId"), vlanMembers=now.get("vlanMembers"),
                        auth=now.get("auth"), sz_profile_id=None)
            cautions.append(f"LAN{pid}: only on/off carried (SZ profile unreadable)")
        t, u, m = port_sig(want)
        want.update(type=t, untagId=u, vlanMembers=m)
        if not want["enabled"] and spec.get("supportDisable") is False:
            cautions.append(f"LAN{pid} can't be disabled in R1 — left on")
            want["enabled"] = True
        if spec.get("trunkPortOnly") and t != "TRUNK":
            cautions.append(f"LAN{pid} is trunk-only in R1 — SZ's {TYPE_LABEL.get(t, t)} not carried")
            want.update(type="TRUNK", untagId=now.get("untagId"), vlanMembers=now.get("vlanMembers"),
                        auth=now.get("auth"), sz_profile_id=None)
        ports[pid] = want
    for pid in sz["ports"]:
        if pid not in specs:
            notes.append(f"SZ LAN{pid}: this model has no port {pid} in R1 — not carried")

    if cap.get("requireOneEnabledTrunkPort") and not any(
            p["enabled"] and p.get("type") == "TRUNK" for p in ports.values()):
        blockers.append("R1 needs one enabled trunk port on this model and SZ leaves none")

    poe: Dict[str, Any] = {}
    if sz.get("poeMode"):
        allowed = cap.get("poeModeCapabilities") or []
        if cap.get("canSupportPoeMode") and (not allowed or sz["poeMode"] in allowed):
            poe["poeMode"] = sz["poeMode"]
        elif cap.get("canSupportPoeMode") or sz["poeMode"] != "Auto":
            notes.append(f"PoE mode {sz['poeMode']} isn't offered for this model in R1 — R1's kept")
    if sz.get("poeOut") is not None and cap.get("canSupportPoeOut"):
        poe["poeOut"] = bool(sz["poeOut"])
    elif sz.get("poeOut"):
        notes.append("PoE out is on in SZ but R1 doesn't offer it for this model")
    return {"ports": ports, "poe": poe, "notes": notes, "cautions": cautions, "blockers": blockers}


def changed_ports(want: Dict[str, Dict[str, Any]], now: Dict[str, Dict[str, Any]]) -> List[str]:
    """Ports whose on/off or VLANs differ. Never a protected (uplink) port."""
    return [pid for pid, p in want.items()
            if not p.get("protected")
            and (bool(p.get("enabled")) != bool((now.get(pid) or {}).get("enabled", True))
                 or port_sig(p) != port_sig(now.get(pid) or {})
                 # Client visibility / 802.1X lives on the profile: on the wrong one, the port differs.
                 or (p.get("auth") or "DISABLED") != ((now.get(pid) or {}).get("auth") or "DISABLED"))]


# ── rows ──────────────────────────────────────────────────────────────

def _source_label(sz: Dict[str, Any], group_name: Optional[str]) -> str:
    src = sz.get("ports_source")
    return {"ap": "AP override", "group": f"AP Group '{group_name or '?'}'", "zone": "Zone"}.get(src, "—")


def match_r1_ap(sz_ap: Dict[str, Any], by_serial: Dict[str, Dict[str, Any]],
                by_mac: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    s = norm_serial(sz_ap.get("serial"))
    if s and s in by_serial:
        return by_serial[s]
    m = norm_mac(sz_ap.get("mac"))
    return by_mac.get(m) if m else None


def sz_ap_fields(ap: Dict[str, Any]) -> Dict[str, Any]:
    """The fields used from an SZ query/ap row."""
    return {
        "mac": ap.get("apMac") or ap.get("mac"),
        "serial": ap.get("serial") or ap.get("serialNumber"),
        "name": ap.get("deviceName") or ap.get("name") or ap.get("apMac") or ap.get("mac"),
        "model": ap.get("model"),
        "group_id": ap.get("apGroupId"),
        "group_name": ap.get("apGroupName"),
    }


def build_rows(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    ctx (all read by the router):
        sz_aps          [sz_ap_fields rows]
        sz_details      {mac: GET /aps/{mac}} ({"_error": ...} when unreadable)
        zone_models     {model: settings | {"_error": ...}}
        group_models    {"group_id|model": settings | None | {"_error": ...}}
        sz_profiles     {profile_id: SZ Ethernet Port Profile}
        r1_aps          [R1 venue AP rows]
        caps            {model: R1 capability row}
        venue_entries   {model: lan_ports.read_entries row}
        ap_states       {serial: {"specific", "ports"} | {"_error": ...}}
        activations     {serial: {portId: R1 profile}}
    -> {"rows": [...], "model_notes": [...]}
    """
    by_serial = {norm_serial(a.get("serialNumber")): a for a in ctx["r1_aps"] if a.get("serialNumber")}
    by_mac = {norm_mac(a.get("macAddress")): a for a in ctx["r1_aps"] if norm_mac(a.get("macAddress"))}
    rows: List[Dict[str, Any]] = []
    # How many APs of each model need the same zone-level change: a venue-level fix.
    zone_diff: Dict[str, int] = {}

    for ap in ctx["sz_aps"]:
        row: Dict[str, Any] = {
            "sz_mac": ap["mac"], "sz_name": ap["name"], "serial": ap.get("serial"), "model": ap.get("model"),
            "sz_group": ap.get("group_name"), "source": "—",
            "r1": None, "status": BLOCKED, "sz_ports": [], "r1_ports": [], "changes": [],
            "poe": {}, "r1_poe": {}, "notes": [], "cautions": [], "blockers": [], "extras": [],
            "port_plan": [], "r1_use_venue": None, "uplink_ports": [],
        }
        rows.append(row)
        r1ap = match_r1_ap(ap, by_serial, by_mac)
        if r1ap:
            row["r1"] = {"serial": r1ap.get("serialNumber"), "name": r1ap.get("name"), "model": r1ap.get("model"),
                         "status": r1ap.get("status"), "ap_group": r1ap.get("apGroupName")}
        model = (r1ap or {}).get("model") or ap.get("model")

        detail = ctx["sz_details"].get(ap["mac"]) or {}
        zone = ctx["zone_models"].get(ap.get("model") or "")
        group = ctx["group_models"].get(f"{ap.get('group_id')}|{ap.get('model')}")
        if detail.get("_error"):
            row["blockers"].append(f"SZ AP detail unreadable: {detail['_error']}")
        if not zone or zone.get("_error"):
            row["blockers"].append(f"SZ zone settings for {ap.get('model')} unreadable")
        if isinstance(group, dict) and group.get("_error"):
            row["blockers"].append(f"SZ AP Group '{ap.get('group_name')}' {ap.get('model')} settings unreadable")
        if row["blockers"]:
            continue

        sz = sz_effective(detail.get("specific"), group if isinstance(group, dict) else None, zone,
                          ctx["sz_profiles"])
        row["source"] = _source_label(sz, ap.get("group_name"))
        row["sz_ports"] = [describe(p) for p in sorted(sz["ports"].values(), key=lambda p: int(p["portId"]))]
        row["poe"] = {k: sz.get(k) for k in ("poeMode", "poeOut") if sz.get(k) is not None}
        row["extras"] = sz["extras"]

        if not r1ap:
            row["status"] = NOT_IN_R1
            row["notes"].append("No AP with this serial or MAC in the R1 venue")
            continue
        if ap.get("model") and r1ap.get("model") and ap["model"] != r1ap["model"]:
            row["notes"].append(f"Model differs: SZ {ap['model']}, R1 {r1ap['model']}")
        cap = ctx["caps"].get(model)
        if not cap:
            row["blockers"].append(f"R1 offers no settings for model {model} at this venue")
            row["status"] = BLOCKED
            continue
        if not cap.get("lanPorts"):
            row["notes"].append("This model has no configurable LAN ports in R1")
            row["status"] = MATCH
            continue
        state = ctx["ap_states"].get(norm_serial(r1ap.get("serialNumber"))) or {"_error": "not read"}
        if state.get("_error"):
            row["blockers"].append(f"R1 can't read this AP's LAN ports ({state['_error']}) — "
                                   "R1 serves them once the AP has connected")
            row["status"] = BLOCKED
            continue

        venue_entry = ctx["venue_entries"].get(model) or {}
        r1 = r1_effective(cap, venue_entry, state,
                          ctx["activations"].get(norm_serial(r1ap.get("serialNumber"))) or {},
                          (ctx.get("venue_profiles") or {}).get(model) or {})
        row["r1_use_venue"] = r1["use_venue"]
        row["r1_ports"] = [describe(p) for p in sorted(r1["ports"].values(), key=lambda p: int(p["portId"]))]
        row["r1_poe"] = {k: r1.get(k) for k in ("poeMode", "poeOut") if r1.get(k) is not None}

        # The uplink first: everything below works around it.
        up = uplink_ports(model, cap, r1ap)
        row["uplink_ports"] = up["ports"]
        if up["blocker"]:
            row["blockers"].append(up["blocker"])
            row["status"] = BLOCKED
            continue
        if up["caution"]:
            row["cautions"].append(up["caution"])

        want = desired_for(sz, cap, r1, up["ports"])
        row["notes"].extend(want["notes"])
        row["cautions"].extend(want["cautions"])
        row["blockers"].extend(want["blockers"])
        if row["blockers"]:
            row["status"] = BLOCKED
            continue

        changed = changed_ports(want["ports"], r1["ports"])
        poe_changed = [k for k, v in want["poe"].items() if r1.get(k) != v]
        row["changes"] = ([f"{describe(r1['ports'][p])} → {describe(want['ports'][p])}" for p in changed]
                          + [f"{k} {r1.get(k)} → {want['poe'][k]}" for k in poe_changed])
        if not changed and not poe_changed:
            row["status"] = MATCH
            if not r1["use_venue"]:
                row["notes"].append("R1 AP has its own port settings, with SZ's values")
            continue

        if sz.get("ports_source") == "zone":
            zone_diff[model] = zone_diff.get(model, 0) + 1
        if "poeMode" in poe_changed:
            row["cautions"].append("Changes the PoE mode — the AP may renegotiate power or reboot")
        row["status"] = REVIEW if row["cautions"] else UPDATE
        # The uplink never enters the plan, so no phase can be handed it.
        row["port_plan"] = [
            {k: p.get(k) for k in ("portId", "enabled", "type", "untagId", "vlanMembers",
                                    "sz_profile_id", "sz_profile_name", "vlan_override")}
            | {"current_profile_id": (r1["ports"].get(pid) or {}).get("profile_id"),
               "changed": pid in changed}
            for pid, p in sorted(want["ports"].items(), key=lambda kv: int(kv[0]))
            if not p.get("protected") and pid not in up["ports"]
        ]
        row["poe_plan"] = want["poe"]

    model_notes = [
        f"{n} {m} AP(s) follow the SZ zone yet differ from the R1 venue's {m} ports — "
        "the venue's per-model setting differs; fixing it once at the venue may be simpler"
        for m, n in sorted(zone_diff.items()) if n >= 2
    ]
    return {"rows": rows, "model_notes": model_notes}


# ── Ethernet Port Profile mapping ─────────────────────────────────────

def r1_profile_summary(p: Dict[str, Any]) -> Dict[str, Any]:
    t, u, m = port_sig(p)
    return {"id": p.get("id"), "name": p.get("name"), "type": t, "untagId": u, "vlanMembers": m,
            "auth": p.get("authType") or "DISABLED",
            "isDefault": bool(p.get("isDefault")), "plain": _plain(p),
            "in_use": len(p.get("apActivations") or []) + len(p.get("venueActivations") or [])}


def profiles_to_map(rows: List[Dict[str, Any]], sz_profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """SZ profiles the actionable rows' written ports use: {id: {name, type, untagId, vlanMembers, auth, aps, ports}}."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r["status"] not in (UPDATE, REVIEW):
            continue
        flip = r.get("r1_use_venue") is not False
        for p in r.get("port_plan") or []:
            pid = p.get("sz_profile_id")
            if not pid or not (flip or p.get("changed")):
                continue
            prof = sz_profiles.get(pid) or {}
            t, u, m = sig(_PORT_TYPE.get(prof.get("type"), prof.get("type")), prof.get("untagId"),
                          prof.get("vlanMembers"))
            e = out.setdefault(pid, {"sz_profile_id": pid, "name": prof.get("name") or p.get("sz_profile_name"),
                                     "type": t, "untagId": u, "vlanMembers": m, "auth": sz_auth(prof),
                                     "extras": [x for x in _profile_extras(prof) if not x.startswith("802.1X")],
                                     "aps": set(), "ports": 0})
            e["aps"].add(r["sz_mac"])
            e["ports"] += 1
    for e in out.values():
        e["aps"] = len(e["aps"])
    return out


def _name_key(name: Optional[str]) -> str:
    """Names compare ignoring case and spaces: SZ 'Default Trunk Port(WAN)' == R1 'Default Trunk Port (WAN)'."""
    return re.sub(r"\s+", "", (name or "")).lower()


def creatable(auth: str) -> bool:
    """802.1X needs RADIUS profiles / supplicant credentials this tool doesn't copy; no auth and open can be made."""
    return auth in ("DISABLED", "OPEN")


def suggest_profile(sz: Dict[str, Any], r1_profiles: List[Dict[str, Any]], venue_name: str) -> Dict[str, Any]:
    """
    {action: existing|create, r1_profile_id, name, reason}. BY NAME, as the
    user decided (2026-11-17): the R1 profile named like the SZ one is reused;
    none -> created under the SZ name. Each port then gets SZ's exact VLANs
    as its own override, so the profile's VLANs don't have to match.

    Same name but another port type can't stand in: then "<name>_<venue>" is
    reused or created. An SZ default profile with no namesake maps to R1's
    default of the same type.
    """
    auth = sz.get("auth") or "DISABLED"
    key = _name_key(sz.get("name"))
    named = [p for p in r1_profiles if p.get("id") and _name_key(p.get("name")) == key]
    usable = [p for p in named if p.get("type") == sz["type"]]
    if usable:
        p = usable[0]
        reason = "R1 profile of the same name"
        if (p.get("auth") or "DISABLED") != auth:
            reason += f" — but its auth is {AUTH_LABEL.get(p.get('auth'), p.get('auth'))}, SZ's is {AUTH_LABEL.get(auth, auth)}"
        return {"action": "existing", "r1_profile_id": p["id"], "name": None, "reason": reason}
    if not named and key.startswith("default"):
        d = [p for p in r1_profiles if p.get("isDefault") and p.get("type") == sz["type"]
             and (p.get("auth") or "DISABLED") == auth]
        if d:
            d.sort(key=lambda p: -p.get("in_use", 0))
            return {"action": "existing", "r1_profile_id": d[0]["id"], "name": None,
                    "reason": "SZ default profile -> R1's default of the same type"}
    name = (sz.get("name") or f"{TYPE_LABEL.get(sz['type'], sz['type'])} {sz['untagId']}").strip()[:NAME_MAX]
    if named:
        name = _clash_name(name, venue_name)
        clash = [p for p in r1_profiles if _name_key(p.get("name")) == _name_key(name) and p.get("type") == sz["type"]]
        if clash:
            return {"action": "existing", "r1_profile_id": clash[0]["id"], "name": None,
                    "reason": f"'{sz.get('name')}' is a {TYPE_LABEL.get(named[0].get('type'), named[0].get('type'))} "
                              f"profile in R1 — using '{clash[0].get('name')}'"}
    if not creatable(auth):
        return {"action": "existing", "r1_profile_id": None, "name": None,
                "reason": f"Not in R1, and SZ's {AUTH_LABEL.get(auth, auth)} needs RADIUS settings this tool "
                          "doesn't copy — create it in R1 first, or pick a profile"}
    if any(_name_key(p.get("name")) == _name_key(name) for p in r1_profiles):
        return {"action": "create", "r1_profile_id": None, "name": "",
                "reason": "Name taken in R1 by a profile of another type — name it, or pick a profile"}
    return {"action": "create", "r1_profile_id": None, "name": name,
            "reason": "Not in R1 — created under the SZ name" if not named
            else f"'{sz.get('name')}' is another port type in R1 — created as '{name}'"}


def unit_spec(row: Dict[str, Any]) -> Dict[str, Any]:
    """What a unit of the job needs about one AP (no SZ reads in phases)."""
    return {
        "sz_mac": row["sz_mac"], "sz_name": row["sz_name"],
        "serial": (row.get("r1") or {}).get("serial"), "r1_name": (row.get("r1") or {}).get("name"),
        "model": (row.get("r1") or {}).get("model") or row.get("model"),
        "r1_use_venue": row.get("r1_use_venue"),
        "uplink_ports": row.get("uplink_ports") or [],
        "ports": row.get("port_plan") or [],
        "poe": row.get("poe_plan") or {},
        "changes": row.get("changes") or [],
    }
