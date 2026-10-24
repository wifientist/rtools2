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

Only models the zone's APs are, and only what SZ actually sets, become
sections. Each is one entry per model, merged into R1's current list at
apply time (venue_create.apply_settings), so other models keep theirs.

Pure.
"""

from typing import Any, Dict, List, Optional

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
