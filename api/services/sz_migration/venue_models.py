"""
Zone per-AP-model settings -> R1 venue per-model settings.

SZ keeps PoE, LAN ports, LED, external antennas and band combination per AP
model at the zone (GET /rkszones/{z}/apmodel/{model}); R1 keeps the same per
model at the venue, one list per kind:

    apModelLanPortSettings          [{model, poeMode, poeOut, lanPorts: [{portId, enabled, type, untagId, vlanMembers}]}]
    apModelLedSettings              [{model, ledEnabled}]
    apModelExternalAntennaSettings  [{model, enable24G, gain24G, enable50G, gain50G}]
    apModelBandModeSettings         [{model, bandMode}]

A SZ LAN port points at a zone Ethernet Port Profile (type, untagged VLAN,
VLAN members, 802.1X, ...). R1 takes type and VLANs inline on the port, so
no R1 Ethernet Port Profile is created; what the profile carries beyond that
(802.1X, client isolation, tunnelling, user-side limits) is noted, not sent.

Sections hold what the zone sets for each of its models. What is actually
written is narrower (customise, below): only the zone's models that R1
offers at the venue, and only where the zone differs from R1's default for
that model -- every other model, and every default, is left alone.

Pure.
"""

from typing import Any, Dict, List, Optional, Tuple

_PORT_TYPE = {"AccessPort": "ACCESS", "TrunkPort": "TRUNK", "GeneralPort": "GENERAL"}
_POE = {
    "Auto": "Auto", "_802_3af": "802.3af", "_802_3at": "802.3at",
    "_802_3bt_Class_5": "802.3bt-Class_5", "_802_3bt_Class_6": "802.3bt-Class_6",
    "_802_3bt_Class_7": "802.3bt-Class_7", "_802_3bt_Class_8": "802.3bt-Class_8",
}
_BAND_MODE = {"_24G_5G": "DUAL", "_24G_5G_6G": "TRIPLE"}


def _port_id(port_name: Optional[str]) -> Optional[str]:
    """SZ 'LAN3' -> R1 '3'."""
    if not port_name or not port_name.upper().startswith("LAN"):
        return None
    n = port_name[3:]
    return n if n.isdigit() else None


def _profile_extras(profile: Dict[str, Any]) -> List[str]:
    """What a SZ Ethernet Port Profile does that R1's inline port can't."""
    out = []
    dot1x = (profile.get("_8021X") or {}).get("type")
    if dot1x and dot1x != "Disable":
        out.append(f"802.1X ({dot1x})")
    if profile.get("clientIsolationEnabled"):
        out.append("client isolation")
    if profile.get("tunnelEnabled"):
        out.append("tunnelling")
    if profile.get("userSidePortEnabled"):
        out.append("user-side port limits")
    if profile.get("dynamicVlanEnabled"):
        out.append("dynamic VLAN")
    return out


def build_model_sections(zone_models: Dict[str, Dict[str, Any]], section_cls) -> Dict[str, Any]:
    """
    {"sections": [...], "not_carried": [...]} for the zone's AP models.
    section_cls is venue_create._Section (passed in to avoid a cycle).
    """
    lan = section_cls("model_lan_ports", "AP model LAN ports & PoE", "apModelLanPortSettings", "application/json",
                      by_model=True)
    led = section_cls("model_leds", "AP model LEDs", "apModelLedSettings", "application/json", by_model=True)
    ant = section_cls("model_antennas", "AP model external antennas", "apModelExternalAntennaSettings",
                      "application/json", by_model=True)
    band = section_cls("model_band_mode", "AP model band mode", "apModelBandModeSettings", "application/json",
                       by_model=True)
    lan.body, led.body, ant.body, band.body = [], [], [], []
    not_carried: List[str] = []

    for model in sorted(zone_models):
        entry = zone_models[model]
        if entry.get("error"):
            lan.notes.append(f"{model}: zone settings could not be read ({entry['error']})")
            continue
        s = entry.get("settings") or {}
        profiles = entry.get("eth_profiles") or {}

        # ── LAN ports + PoE ──
        item: Dict[str, Any] = {"model": model}
        poe = s.get("poeModeSetting")
        if poe in _POE:
            item["poeMode"] = _POE[poe]
            lan.rows.append({"label": f"{model}: PoE mode", "value": _POE[poe]})
        elif poe:
            lan.notes.append(f"{model}: PoE mode {poe} has no R1 value; R1's stays")
        if s.get("poeOutPortEnabled") is not None:
            item["poeOut"] = bool(s["poeOutPortEnabled"])
            lan.rows.append({"label": f"{model}: PoE out", "value": item["poeOut"]})
        ports = []
        for p in s.get("lanPorts") or []:
            pid = _port_id(p.get("portName"))
            if pid is None:
                lan.notes.append(f"{model}: port '{p.get('portName')}' has no R1 port id")
                continue
            port: Dict[str, Any] = {"portId": pid, "enabled": bool(p.get("enabled"))}
            prof_ref = p.get("ethPortProfile") or {}
            prof = profiles.get(prof_ref.get("id")) or {}
            if prof and not prof.get("error"):
                if prof.get("type") in _PORT_TYPE:
                    port["type"] = _PORT_TYPE[prof["type"]]
                if prof.get("untagId") is not None:
                    port["untagId"] = int(prof["untagId"])
                if prof.get("vlanMembers"):
                    port["vlanMembers"] = str(prof["vlanMembers"])
                extras = _profile_extras(prof)
                if extras:
                    lan.notes.append(f"{model} {p.get('portName')}: profile '{prof.get('name')}' also has "
                                     f"{', '.join(extras)} — not carried (R1 sets these by Ethernet Port Profile)")
            elif prof_ref:
                lan.notes.append(f"{model} {p.get('portName')}: profile '{prof_ref.get('name')}' could not be read "
                                 "— only enabled/disabled is carried")
            ports.append(port)
            desc = "disabled" if not port["enabled"] else " ".join(
                str(x) for x in (port.get("type", "?"), f"untag {port.get('untagId', '?')}",
                                 f"members {port.get('vlanMembers', '?')}"))
            lan.rows.append({"label": f"{model}: {p.get('portName')}", "value": desc})
        if ports:
            item["lanPorts"] = ports
        if len(item) > 1:
            lan.body.append(item)

        # ── LED ──
        if s.get("ledStatusEnabled") is not None:
            led.body.append({"model": model, "ledEnabled": bool(s["ledStatusEnabled"])})
            led.rows.append({"label": f"{model}: status LEDs", "value": bool(s["ledStatusEnabled"])})
        if s.get("ledMode"):
            led.notes.append(f"{model}: LED mode {s['ledMode']} — R1 has only on/off")

        # ── External antennas ──
        a24, a50 = s.get("externalAntenna24"), s.get("externalAntenna50")
        if a24 or a50:
            a: Dict[str, Any] = {"model": model}
            for band_label, sz, en, gain in (("2.4 GHz", a24, "enable24G", "gain24G"), ("5 GHz", a50, "enable50G", "gain50G")):
                if sz:
                    a[en] = bool(sz.get("enabled"))
                    if sz.get("dbi") is not None:
                        a[gain] = int(sz["dbi"])
                    ant.rows.append({"label": f"{model}: {band_label} external antenna",
                                     "value": f"{'on' if a[en] else 'off'}" + (f", {a[gain]} dBi" if gain in a else "")})
            ant.body.append(a)

        # ── Band mode (tri-band models) ──
        bc = s.get("bandCombSetting")
        if bc in _BAND_MODE:
            band.body.append({"model": model, "bandMode": _BAND_MODE[bc]})
            band.rows.append({"label": f"{model}: band mode", "value": _BAND_MODE[bc]})
        elif bc:
            band.notes.append(f"{model}: band combination {bc} has no R1 band mode")

        # ── Not carried ──
        if (s.get("lldp") or {}).get("enabled"):
            not_carried.append(f"{model}: LLDP — R1 has no per-model LLDP setting")
        if s.get("usbPowerEnable") is not None:
            not_carried.append(f"{model}: USB power ({'on' if s['usbPowerEnable'] else 'off'}) — R1's field for it isn't documented")
        if (s.get("lacp") or {}).get("enabled"):
            not_carried.append(f"{model}: LACP — not carried")

    sections = [x for x in (lan, led, ant, band) if x.body]
    return {"sections": sections, "not_carried": not_carried}


# ═══════════════════════════════════════════════════════════════════════
# What actually gets pushed: only what the zone sets differently from R1's
# own defaults, for the zone's models that R1 offers at this venue.
# ═══════════════════════════════════════════════════════════════════════
#
# R1's per-model lists hold every model in its catalogue, beta ones included
# (an H375 got pushed that way, and R1 then rejected its own read-back).
# The venue is fine on R1's defaults; a model is written only when the zone
# configured it differently, and then as one complete entry. R1's defaults
# per model come from GET /venues/{v}/apModelCapabilities.

def default_poe_mode(cap: Dict[str, Any]) -> Optional[str]:
    modes = cap.get("poeModeCapabilities") or []
    return "Auto" if "Auto" in modes else (modes[0] if modes else None)


def _default_ports(cap: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(p.get("id")): {"portId": str(p.get("id")), "enabled": True, "type": p.get("defaultType"),
                               "untagId": p.get("untagId"), "vlanMembers": p.get("vlanMembers")}
            for p in cap.get("lanPorts") or [] if p.get("id") is not None}


def _lan_entry(e: Dict[str, Any], cap: Dict[str, Any], notes: List[str]) -> Optional[Dict[str, Any]]:
    model = e["model"]
    out: Dict[str, Any] = {"model": model}
    changed = False

    poe = e.get("poeMode")
    if poe is not None:
        allowed = cap.get("poeModeCapabilities") or []
        if not cap.get("canSupportPoeMode") or (allowed and poe not in allowed):
            notes.append(f"{model}: PoE mode {poe} isn't offered for this model in R1 "
                         f"({', '.join(allowed) or 'none'}) — R1 default kept")
        elif poe != default_poe_mode(cap):
            out["poeMode"], changed = poe, True
    if e.get("poeOut"):
        if cap.get("canSupportPoeOut"):
            out["poeOut"], changed = True, True
        else:
            notes.append(f"{model}: PoE out is on in SZ but R1 doesn't offer it for this model")

    defaults = _default_ports(cap)
    spec = {p["id"]: p for p in cap.get("lanPorts") or [] if p.get("id") is not None}
    desired = {pid: dict(p) for pid, p in defaults.items()}
    for p in e.get("lanPorts") or []:
        pid = p["portId"]
        if pid not in desired:
            notes.append(f"{model}: SZ LAN{pid} has no port {pid} on this model in R1 — not pushed")
            continue
        want = {**desired[pid], **p}
        if not want["enabled"] and spec[pid].get("supportDisable") is False:
            notes.append(f"{model}: LAN{pid} can't be disabled in R1 — left enabled")
            want["enabled"] = True
        if spec[pid].get("trunkPortOnly") and want.get("type") != "TRUNK":
            notes.append(f"{model}: LAN{pid} is trunk-only in R1 — SZ {want.get('type')} not pushed")
            want = {**want, "type": "TRUNK", "untagId": desired[pid]["untagId"],
                    "vlanMembers": desired[pid]["vlanMembers"]}
        desired[pid] = want
    if desired != defaults:
        if cap.get("requireOneEnabledTrunkPort") and not any(
                p["enabled"] and p.get("type") == "TRUNK" for p in desired.values()):
            notes.append(f"{model}: R1 needs one enabled trunk port and the zone leaves none — ports not pushed")
        else:
            out["lanPorts"] = sorted(desired.values(), key=lambda p: int(p["portId"]) if p["portId"].isdigit() else 0)
            changed = True
    return out if changed else None


def customise(section_key: str, entries: List[Dict[str, Any]],
              caps: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """
    (entries to PUT, notes, models left on R1's defaults) for one per-model
    section. caps: model -> capability row from apModelCapabilities.
    """
    push: List[Dict[str, Any]] = []
    notes: List[str] = []
    defaults: List[str] = []
    for e in entries:
        model = e["model"]
        cap = caps.get(model)
        if cap is None:
            notes.append(f"{model}: R1 doesn't offer this model at this venue — not pushed")
            continue
        if section_key == "model_lan_ports":
            entry = _lan_entry(e, cap, notes)
        elif section_key == "model_leds":
            entry = e if e.get("ledEnabled") != cap.get("ledOn", True) else None
        elif section_key == "model_antennas":
            entry = e if (e.get("enable24G") or e.get("enable50G")) else None
        elif section_key == "model_band_mode":
            if cap.get("supportBandCombination") or cap.get("supportTriRadio"):
                entry = e
            else:
                notes.append(f"{model}: R1 has no band mode for this model — not pushed")
                entry = None
        else:
            entry = e
        if entry is None:
            defaults.append(model)
        else:
            push.append(entry)
    return push, notes, defaults
