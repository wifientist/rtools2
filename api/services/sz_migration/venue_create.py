"""
Destination venue: create it in R1 from the SZ zone, then carry the zone's
settings over.

Two halves:

  * venue_draft_from_zone / build_settings_plan -- pure. What the new venue
    would be called and where, and for each R1 venue-settings section the
    values the zone implies, with a note for every zone setting R1 can't
    take (or takes somewhere other than the venue).
  * create_venue / apply_settings -- I/O. One POST /venues; then per section
    GET the venue's current body, overlay the zone's values and PUT it back.
    R1 replaces a settings object whole, so overlaying onto what's there
    keeps every field the zone says nothing about at R1's own value.

Only "high level" settings are carried: channel plans and radio behaviour
per band (incl. dual 5 GHz and the 6 GHz rates), management VLAN, mesh, load
and band balancing, client admission control, AP behaviours. Per-WLAN radio
settings (OFDM-only, BSS minimum rate) are not venue settings in either
controller; the network create already carries them (field_mappings).

Encodings come from venue_settings (the audit's comparison of the same
fields), so the two can't drift apart.

The mesh passphrase is put into the request body at apply time only; the
plan shows "set" / "not set" and never the value.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.sz_migration.venue_settings import (
    V1, V1_1, _METHOD, _MESH_RADIO, bss_min_rate_6g, channel_width, mbps, tx_power,
)

logger = logging.getLogger(__name__)

VENUE_NAME_MAX = 63
APPLY_CONCURRENCY = 4

_WIDTHS_24 = {"AUTO", "20MHz", "40MHz"}
_WIDTHS_5_6 = {"AUTO", "20MHz", "40MHz", "80MHz", "160MHz"}
_MGMT_RATES_6G = {"6", "9", "12", "18", "24", "36", "48", "54"}
_LB_METHODS = {"BASED_ON_CLIENT_COUNT", "BASED_ON_CAPACITY"}
_STEERING = {"BASIC", "PROACTIVE", "STRICT"}


# ═══════════════════════════════════════════════════════════════════════
# Venue draft (pure)
# ═══════════════════════════════════════════════════════════════════════

def _float(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def venue_draft_from_zone(zone_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-fill for the create form. SZ zones rarely carry an address (location
    and coordinates are usually blank), so the operator fills in the rest.
    """
    z = zone_raw or {}
    notes: List[str] = []
    name = (z.get("name") or "").strip()
    if len(name) > VENUE_NAME_MAX:
        notes.append(f"Zone name is {len(name)} characters; R1 allows {VENUE_NAME_MAX}, so it was shortened")
        name = name[:VENUE_NAME_MAX].rstrip()

    tz = (z.get("timezone") or {}).get("systemTimezone")
    timezone = tz if tz and "/" in tz else None
    if tz and not timezone:
        notes.append(f"SZ time zone '{tz}' is not a region name (e.g. America/Chicago); pick the venue's time zone")
    if (z.get("timezone") or {}).get("customizedTimezone"):
        notes.append("SZ zone uses a custom GMT offset / DST rule; R1 has no custom time zone")

    return {
        "name": name,
        "description": z.get("description") or "",
        "address": {
            "addressLine": z.get("location") or "",
            "countryCode": (z.get("countryCode") or "").upper(),
            "timezone": timezone,
            "latitude": _float(z.get("latitude")),
            "longitude": _float(z.get("longitude")),
        },
        "notes": notes,
    }


# ═══════════════════════════════════════════════════════════════════════
# Settings plan (pure)
# ═══════════════════════════════════════════════════════════════════════

class _Section:
    def __init__(self, key: str, label: str, path: str, media: str, by_model: bool = False):
        self.key, self.label, self.path, self.media = key, label, path, media
        # by_model: the R1 resource is a list of per-model entries; body is a
        # list of entries to merge into it by "model" (venue_models).
        self.by_model = by_model
        self.body: Any = {}
        self.rows: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.secret: Optional[Dict[str, Any]] = None   # merged in at apply time only

    def set(self, target: Dict[str, Any], field: str, value: Any, label: str, shown: Any = None):
        target[field] = value
        self.rows.append({"label": label, "value": value if shown is None else shown})

    def out(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "path": self.path, "media": self.media,
                "body": self.body, "rows": self.rows, "notes": self.notes}


def _channels(values: Any) -> Optional[List[str]]:
    if not values:
        return None
    out = set()
    for v in values:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            pass
    return [str(c) for c in sorted(out)] or None


def _band(sec: _Section, label: str, s: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """
    One SZ radio block -> one R1 radioParams body.
    kind: "24" (one channel list), "5" (indoor + outdoor lists), "6".
    """
    t: Dict[str, Any] = {}
    p = f"{label}: "

    method = _METHOD.get((s.get("autoChannelSelection") or {}).get("channelSelectMode"))
    if method:
        sec.set(t, "method", method, p + "Channel selection")
    freq = (s.get("autoChannelSelection") or {}).get("channelFlyChangeFrequency")
    if method == "CHANNELFLY" and isinstance(freq, int) and 1 <= freq <= 100:
        sec.set(t, "changeInterval", freq, p + "ChannelFly change frequency")
    scan = (s.get("backgroundScanning") or {}).get("frequencyInSec")
    if isinstance(scan, int) and 1 <= scan <= 65535:
        sec.set(t, "scanInterval", scan, p + "Background scan interval (s)")

    width = channel_width(s.get("channelWidth"))
    allowed = _WIDTHS_24 if kind == "24" else _WIDTHS_5_6
    if width in allowed:
        sec.set(t, "channelBandwidth", width, p + "Channel width")
    elif width:
        sec.notes.append(f"{p}channel width {s.get('channelWidth')} has no R1 value; R1's stays")

    power = tx_power(s.get("txPower"), s.get("autoCellSizing"))
    if power and not power.startswith("UNMAPPABLE:") and not (kind == "6" and power == "Auto"):
        sec.set(t, "txPower", power, p + "Transmit power")
    elif power:
        sec.notes.append(f"{p}transmit power '{s.get('txPower')}' has no R1 value; R1's stays")

    outdoor, indoor = _channels(s.get("channelRange")), _channels(s.get("channelRangeIndoor"))
    if kind == "24":
        if outdoor:
            sec.set(t, "allowedChannels", outdoor, p + "Allowed channels", ", ".join(outdoor))
    else:
        if indoor:
            sec.set(t, "allowedIndoorChannels", indoor, p + "Allowed channels (indoor)", ", ".join(indoor))
        if outdoor:
            sec.set(t, "allowedOutdoorChannels", outdoor, p + "Allowed channels (outdoor)", ", ".join(outdoor))

    if s.get("channel") or s.get("channelIndoor"):
        sec.notes.append(f"{p}manual channel {s.get('channel') or s.get('channelIndoor')} -- "
                         "R1 sets manual channels per AP Group or AP, not per venue")
    if s.get("protectionMode"):
        sec.notes.append(f"{p}protection mode {s['protectionMode']} -- R1 has no protection mode setting")

    if kind == "6":
        rate = bss_min_rate_6g(s.get("bssMinRate"))
        if rate and not rate.startswith("UNMAPPABLE:"):
            sec.set(t, "bssMinRate6G", rate, p + "BSS minimum rate")
        mgmt = mbps(s.get("mgmtTxRate"))
        if mgmt in _MGMT_RATES_6G:
            sec.set(t, "mgmtTxRate6G", mgmt, p + "Management Tx rate (Mbps)")
        elif mgmt:
            sec.notes.append(f"{p}management rate '{s.get('mgmtTxRate')}' has no R1 value")
        if s.get("afcEnabled") is not None:
            sec.set(t, "enableAfc", bool(s["afcEnabled"]), p + "AFC")
    return t


def _radio(z: Dict[str, Any]) -> _Section:
    sec = _Section("radio", "Radio & channel plan", "apRadioSettings", V1_1)
    rc = z.get("radioConfig") or {}
    for label, szk, r1k, kind in (("2.4 GHz", "radio24g", "radioParams24G", "24"),
                                  ("5 GHz", "radio5g", "radioParams50G", "5"),
                                  ("6 GHz", "radio6g", "radioParams6G", "6")):
        if rc.get(szk):
            band = _band(sec, label, rc[szk], kind)
            if band:
                sec.body[r1k] = band

    dual = bool(rc.get("dual5gEnabled"))
    d5: Dict[str, Any] = {}
    sec.set(d5, "enabled", dual, "Dual 5 GHz")
    halves = (("Lower 5 GHz", "radio5gLower", "radioParamsLower5G", "inheritParamsLower5G"),
              ("Upper 5 GHz", "radio5gUpper", "radioParamsUpper5G", "inheritParamsUpper5G"))
    if dual:
        for label, szk, r1k, inherit in halves:
            if rc.get(szk):
                d5[inherit] = False
                d5[r1k] = _band(sec, label, rc[szk], "5")
    else:
        # R1 doesn't keep Lower/Upper channel lists while Dual 5 GHz is off
        # (verified on a production venue: the write is accepted, the lists
        # stay R1's defaults). Send R1's defaults for the inherit flags --
        # undoing an earlier version that set them False -- and say what the
        # zone's split plan is, for whoever turns Dual 5 GHz on in R1.
        for label, szk, r1k, inherit in halves:
            d5[inherit] = True
        plans = [f"{label} {', '.join(_channels((rc.get(szk) or {}).get('channelRange') or (rc.get(szk) or {}).get('channelRangeIndoor')) or [])}"
                 for label, szk, _, _ in halves if rc.get(szk)]
        if plans:
            sec.notes.append("Dual 5 GHz is off in the zone; R1 only keeps Lower/Upper channel lists while it's on. "
                             f"If you turn it on in R1, set them to the zone's plan: {'; '.join(plans)}")
    sec.body["radioParamsDual5G"] = d5
    sec.notes.append("Channel lists are checked against the venue's country by R1 when applied")
    return sec


def _mgmt_vlan(z: Dict[str, Any]) -> Optional[_Section]:
    mv = z.get("apMgmtVlan") or {}
    sec = _Section("mgmt_vlan", "AP management VLAN", "apManagementTrafficVlanSettings", V1)
    if mv.get("mode") == "USER_DEFINED" and mv.get("id"):
        sec.set(sec.body, "vlanId", int(mv["id"]), "Management VLAN")
        return sec
    return None


def _mesh(z: Dict[str, Any]) -> Optional[_Section]:
    m = z.get("mesh")
    if m is None:
        return None
    sec = _Section("mesh", "Mesh", "apMeshSettings", V1_1)
    sec.set(sec.body, "enabled", True, "Mesh enabled")
    if m.get("ssid"):
        sec.set(sec.body, "ssid", m["ssid"], "Mesh SSID")
    radio = _MESH_RADIO.get(m.get("meshRadioOption"))
    if radio:
        sec.set(sec.body, "radioType", radio, "Mesh radio")
    elif m.get("meshRadioOption"):
        sec.notes.append(f"Mesh radio '{m['meshRadioOption']}' has no R1 value; R1's stays")
    pw = m.get("passphrase")
    if isinstance(pw, str) and pw and not pw.startswith("__"):
        sec.secret = {"passphrase": pw}
        sec.rows.append({"label": "Mesh passphrase", "value": "from SZ (hidden)"})
    else:
        sec.notes.append("No usable mesh passphrase in the extraction; R1 will keep / generate its own")
    sec.notes.append("Mesh APs only re-join if SSID and passphrase match what they were provisioned with")
    return sec


def _load_balancing(z: Dict[str, Any]) -> _Section:
    sec = _Section("load_balancing", "Load & band balancing", "apLoadBalancingSettings", V1)
    lb = z.get("loadBalancing") or {}
    method = lb.get("loadBalancingMethod")
    on = method not in (None, "OFF")
    sec.set(sec.body, "enabled", on, "Client load balancing")
    if on and method in _LB_METHODS:
        sec.set(sec.body, "loadBalancingMethod", method, "Method")
    elif on:
        sec.notes.append(f"Load balancing method '{method}' has no R1 value; R1's stays")
    bb = lb.get("bandBalancing")
    sec.set(sec.body, "bandBalancingEnabled", bb is not None, "Band balancing")
    if bb is not None and bb.get("wifi24Percentage") is not None:
        sec.set(sec.body, "bandBalancingClientPercent24G", int(bb["wifi24Percentage"]), "Band balancing 2.4 GHz client %")
    if lb.get("steeringMode") in _STEERING:
        sec.set(sec.body, "steeringMode", lb["steeringMode"], "Steering mode")
    return sec


def _cac(z: Dict[str, Any]) -> _Section:
    sec = _Section("cac", "Client admission control", "apClientAdmissionControlSettings", V1)
    for label, szk, sfx in (("2.4 GHz", "clientAdmissionControl24", "24G"),
                            ("5 GHz", "clientAdmissionControl50", "50G")):
        s = z.get(szk)
        sec.set(sec.body, f"enable{sfx}", s is not None, f"{label} enabled")
        if s is None:
            continue
        for szf, r1f, lbl in (("maxRadioLoadPercent", "maxRadioLoad", "max radio load %"),
                              ("minClientCount", "minClientCount", "min client count"),
                              ("minClientThroughputMbps", "minClientThroughput", "min client throughput (Mbps)")):
            if s.get(szf) is not None:
                sec.set(sec.body, f"{r1f}{sfx}", int(float(s[szf])), f"{label} {lbl}")
    return sec


def _simple_sections(z: Dict[str, Any]) -> List[_Section]:
    out: List[_Section] = []

    sm = z.get("smartMonitor")
    sec = _Section("smart_monitor", "Smart Monitor", "apSmartMonitorSettings", V1)
    sec.set(sec.body, "enabled", sm is not None, "Smart Monitor")
    if sm is not None:
        if sm.get("intervalInSec") is not None:
            sec.set(sec.body, "interval", sm["intervalInSec"], "Interval (s)")
        if sm.get("retryThreshold") is not None:
            sec.set(sec.body, "threshold", sm["retryThreshold"], "Retry threshold")
    out.append(sec)

    rb = z.get("apRebootTimeout") or {}
    if rb:
        sec = _Section("reboot_timeout", "AP reboot timeouts", "apRebootTimeoutSettings", V1)
        if rb.get("gatewayLossTimeoutInSec") is not None:
            sec.set(sec.body, "gatewayLossTimeout", rb["gatewayLossTimeoutInSec"], "Reboot on gateway loss (s)")
        if rb.get("serverLossTimeoutInSec") is not None:
            sec.set(sec.body, "serverLossTimeout", rb["serverLossTimeoutInSec"], "Reboot on server loss (s)")
        out.append(sec)

    if z.get("bssColoringEnable") is not None:
        sec = _Section("bss_coloring", "BSS coloring", "apBssColoringSettings", V1)
        sec.set(sec.body, "bssColoringEnabled", bool(z["bssColoringEnable"]), "BSS coloring")
        out.append(sec)

    dm = [(lbl, szk, r1k) for lbl, szk, r1k in (
        ("from wired clients", "directedMulticastFromWiredClientEnabled", "wiredEnabled"),
        ("from wireless clients", "directedMulticastFromWirelessClientEnabled", "wirelessEnabled"),
        ("from network", "directedMulticastFromNetworkEnabled", "networkEnabled")) if z.get(szk) is not None]
    if dm:
        sec = _Section("directed_multicast", "Directed multicast", "apDirectedMulticastSettings", V1)
        for lbl, szk, r1k in dm:
            sec.set(sec.body, r1k, bool(z[szk]), f"Directed multicast {lbl}")
        out.append(sec)

    if z.get("dosBarringEnable") is not None:
        sec = _Section("dos_protection", "DoS protection (auth failure blocking)", "apDosProtectionSettings", V1)
        sec.set(sec.body, "enabled", bool(z["dosBarringEnable"]), "Block clients after repeated auth failures")
        if z.get("dosBarringEnable"):
            for szk, r1k, lbl in (("dosBarringThreshold", "failThreshold", "Failure threshold"),
                                  ("dosBarringCheckPeriod", "checkPeriod", "Check period (s)"),
                                  ("dosBarringPeriod", "blockingPeriod", "Blocking period (s)")):
                if z.get(szk) is not None:
                    sec.set(sec.body, r1k, z[szk], lbl)
        out.append(sec)

    if z.get("sshTlsKeyEnhanceModeEnabled") is not None:
        sec = _Section("tls_key", "TLS key enhanced mode", "apTlsKeyEnhancedSettings", V1)
        sec.set(sec.body, "tlsKeyEnhancedModeEnabled", bool(z["sshTlsKeyEnhanceModeEnabled"]), "TLS key enhanced mode")
        out.append(sec)

    if z.get("rogueApReportThreshold") is not None:
        sec = _Section("rogue_policy", "Rogue AP report threshold", "roguePolicySettings", V1)
        sec.set(sec.body, "reportThreshold", z["rogueApReportThreshold"], "Report threshold")
        sec.notes.append("SZ doesn't state the unit; confirm both mean the same scale")
        out.append(sec)
    return out


def _not_carried(z: Dict[str, Any]) -> List[str]:
    """Zone settings that are set but this tool doesn't carry, and why."""
    out = []
    if z.get("rogue") is not None:
        out.append("Rogue detection on/off -- R1 sets it on the venue's rogue AP policy; set it in R1")
    if z.get("syslog"):
        out.append("Syslog -- R1 uses a tenant syslog server profile")
    if z.get("snmpAgent"):
        out.append("SNMP agent -- R1 uses a tenant SNMP agent profile")
    if (z.get("login") or {}).get("apLoginName"):
        out.append("AP admin login -- R1 manages AP credentials per tenant")
    if z.get("locationBasedService"):
        out.append("Location based service -- not carried")
    if z.get("tunnelProfile") or z.get("ruckusGreTunnelProfile") or z.get("softGreTunnelProflies"):
        out.append("Tunnel profiles -- not carried")
    if z.get("channelEvaluationInterval") is not None:
        out.append(f"Channel evaluation interval ({z['channelEvaluationInterval']} s) -- R1 has no such setting")
    return out


def build_settings_plan(zone_raw: Dict[str, Any],
                        zone_models: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    {"sections": [...], "not_carried": [...]}. Section bodies are the
    overlay applied on top of the venue's current settings. zone_models
    (snapshot.zone_ap_models) adds the per-AP-model sections.
    """
    from services.sz_migration.venue_models import build_model_sections

    z = zone_raw or {}
    sections = [_radio(z), _mgmt_vlan(z), _mesh(z), _load_balancing(z), _cac(z), *_simple_sections(z)]
    sections = [s for s in sections if s is not None]
    mv = z.get("apMgmtVlan") or {}
    not_carried = _not_carried(z)
    if mv and mv.get("mode") != "USER_DEFINED":
        not_carried.insert(0, "Management VLAN -- SZ keeps each AP's own VLAN (KEEP); R1's venue default stays")
    if zone_models:
        models = build_model_sections(zone_models, _Section)
        sections += models["sections"]
        not_carried += models["not_carried"]
    return {"sections": sections, "not_carried": not_carried}


def public_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """The plan as the browser sees it: no request bodies, no secrets."""
    return {
        "sections": [{k: v for k, v in s.out().items() if k != "body"} for s in plan["sections"]],
        "not_carried": plan["not_carried"],
    }


# ═══════════════════════════════════════════════════════════════════════
# R1 I/O
# ═══════════════════════════════════════════════════════════════════════

def _kw(r1_client, tenant_id: Optional[str]) -> Dict[str, Any]:
    return {"override_tenant_id": tenant_id} if getattr(r1_client, "ec_type", None) == "MSP" and tenant_id else {}


def r1_error(resp) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:300]}"
    errors = data.get("errors") if isinstance(data, dict) else None
    if errors:
        return "; ".join(e.get("message") or e.get("reason") or str(e) for e in errors if isinstance(e, dict)) \
            or f"HTTP {resp.status_code}"
    return f"HTTP {resp.status_code}: {str(data)[:300]}"


def fit_6g_outdoor(current: Dict[str, Any], body: Dict[str, Any]) -> None:
    """
    R1's 6 GHz outdoor list is a country-limited subset (US: 1-93, 129-157);
    SZ has one list. Send the SZ channels R1 allows outdoors, or leave R1's
    own list when none of them are.
    """
    want = (body.get("radioParams6G") or {}).get("allowedOutdoorChannels")
    allowed = (current.get("radioParams6G") or {}).get("allowedOutdoorChannels")
    if want is None or not allowed:
        return
    fitted = [c for c in want if c in set(allowed)]
    if fitted:
        body["radioParams6G"]["allowedOutdoorChannels"] = fitted
    else:
        body["radioParams6G"]["allowedOutdoorChannels"] = allowed


def drop_combined_channels(body: Dict[str, Any]) -> None:
    """
    R1 returns 6 GHz allowedChannels beside the indoor/outdoor lists, then
    refuses a write carrying allowedChannels with allowedIndoorChannels
    ("mutually exclusive"). Keep the indoor/outdoor lists; drop the combined
    one wherever both made it into the body (R1's own read-back included).
    """
    for k, band in list(body.items()):
        if k.startswith("radioParams") and isinstance(band, dict):
            if band.get("allowedIndoorChannels") is not None:
                band.pop("allowedChannels", None)
            drop_combined_channels(band)


async def poe_capabilities(r1_client, venue_id: str, kw: Dict[str, Any]) -> Dict[str, List[str]]:
    """model -> PoE modes R1 accepts for it (empty when unknown)."""
    resp = await asyncio.to_thread(lambda: r1_client.get(f"/venues/{venue_id}/apModelCapabilities", **kw))
    if resp.status_code != 200:
        return {}
    models = (resp.json() or {}).get("apModels") or []
    return {m.get("model"): list(m.get("poeModeCapabilities") or []) for m in models if m.get("model")}


def fit_poe_modes(rows: List[Dict[str, Any]], caps: Dict[str, List[str]]) -> List[str]:
    """
    Make every per-model poeMode one R1 accepts for that model. R1 reports
    'Auto' for models that only take 802.3af (H375) and then rejects it on
    write -- from the zone or from R1's own read-back alike. A model with one
    supported mode gets that mode; otherwise the value is cleared (R1's
    default). Returns a note per change.
    """
    notes = []
    for r in rows:
        mode, allowed = r.get("poeMode"), caps.get(r.get("model")) or []
        if mode is None or not allowed or mode in allowed:
            continue
        r["poeMode"] = allowed[0] if len(allowed) == 1 else None
        notes.append(f"{r['model']}: PoE mode {mode} isn't offered for this model "
                     f"(R1 supports {', '.join(allowed)}) — set to {r['poeMode'] or 'R1 default'}")
    return notes


def merge_by_model(current: Any, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    R1's per-model list with ours merged in: other models untouched, ours
    overlaid on R1's entry for that model, LAN ports merged by portId.
    """
    rows = [dict(r) for r in (current if isinstance(current, list) else []) if isinstance(r, dict)]
    by_model = {r.get("model"): r for r in rows}
    for e in entries:
        cur = by_model.get(e["model"])
        if cur is None:
            rows.append(dict(e))
            continue
        for k, v in e.items():
            if k == "lanPorts":
                ports = {p.get("portId"): dict(p) for p in cur.get("lanPorts") or []}
                for p in v:
                    ports[p["portId"]] = {**ports.get(p["portId"], {}), **p}
                cur["lanPorts"] = sorted(ports.values(), key=lambda p: int(p["portId"]) if str(p.get("portId")).isdigit() else 0)
            else:
                cur[k] = v
    return rows


def merge(base: Any, overlay: Any) -> Any:
    """Overlay onto base; dicts merge, everything else (lists included) replaces."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for k, v in overlay.items():
            out[k] = merge(base.get(k), v)
        return out
    return overlay


async def list_venue_tags(r1_client, tenant_id: Optional[str]) -> List[str]:
    resp = await asyncio.to_thread(r1_client.get, "/venues/tags", **_kw(r1_client, tenant_id))
    if resp.status_code != 200:
        raise RuntimeError(r1_error(resp))
    body = resp.json()
    return sorted({t for t in body if isinstance(t, str)}) if isinstance(body, list) else []


async def _find_venue_by_name(r1_client, tenant_id: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    resp = await asyncio.to_thread(r1_client.get, "/venues", **_kw(r1_client, tenant_id))
    if resp.status_code != 200:
        return None
    body = resp.json()
    rows = body if isinstance(body, list) else (body or {}).get("data", [])
    return next((v for v in rows if (v.get("name") or "").strip().lower() == name.strip().lower()), None)


async def create_venue(r1_client, tenant_id: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /venues and wait for it. Returns {"id", "name"}. Raises ValueError
    for a name already taken (checked first: the R1 error for it is vague)
    and RuntimeError with R1's message for anything R1 refuses.
    """
    if await _find_venue_by_name(r1_client, tenant_id, body["name"]):
        raise ValueError(f"A venue named '{body['name']}' already exists in this tenant")

    resp = await asyncio.to_thread(lambda: r1_client.post("/venues", payload=body, **_kw(r1_client, tenant_id)))
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(r1_error(resp))
    data = resp.json() if resp.content else {}
    if resp.status_code == 202 and data.get("requestId"):
        await r1_client.await_task_completion(data["requestId"], override_tenant_id=tenant_id)

    venue_id = (data.get("response") or {}).get("id") or data.get("id")
    if not venue_id:
        found = await _find_venue_by_name(r1_client, tenant_id, body["name"])
        venue_id = found and found.get("id")
    if not venue_id:
        raise RuntimeError("R1 accepted the venue but it could not be found afterwards; check R1 before retrying")
    logger.info(f"[SZ Migration] Created R1 venue '{body['name']}' ({venue_id})")
    return {"id": venue_id, "name": body["name"]}


async def apply_settings(r1_client, tenant_id: Optional[str], venue_id: str,
                         plan: Dict[str, Any], keys: List[str]) -> List[Dict[str, Any]]:
    """
    Apply the chosen sections. Each is independent: one failing doesn't stop
    the rest, and each result says what R1 said.
    """
    kw = _kw(r1_client, tenant_id)
    sem = asyncio.Semaphore(APPLY_CONCURRENCY)

    async def one(sec: _Section) -> Dict[str, Any]:
        path = f"/venues/{venue_id}/{sec.path}"
        out = {"key": sec.key, "label": sec.label}
        async with sem:
            try:
                cur = await asyncio.to_thread(lambda: r1_client.get(path, accept=sec.media, **kw))
                if cur.status_code != 200:
                    return {**out, "status": "failed", "error": f"Reading current settings: {r1_error(cur)}"}
                current = cur.json() or ([] if sec.by_model else {})
                body = merge_by_model(current, sec.body) if sec.by_model else merge(current, sec.body)
                notes: List[str] = []
                if sec.key == "radio":
                    fit_6g_outdoor(current, body)
                    drop_combined_channels(body)
                if sec.key == "model_lan_ports":
                    notes = fit_poe_modes(body, await poe_capabilities(r1_client, venue_id, kw))
                if sec.secret:
                    body = merge(body, sec.secret)
                resp = await asyncio.to_thread(
                    lambda: r1_client.put(path, payload=body, content_type=sec.media, **kw))
                if resp.status_code not in (200, 201, 202, 204):
                    return {**out, "status": "failed", "error": r1_error(resp)}
                data = resp.json() if resp.content else {}
                if resp.status_code == 202 and isinstance(data, dict) and data.get("requestId"):
                    await r1_client.await_task_completion(data["requestId"], override_tenant_id=tenant_id)
                return {**out, "status": "applied", "notes": notes}
            except Exception as e:
                logger.error(f"[SZ Migration] Venue {venue_id} {sec.path} failed: {e}")
                return {**out, "status": "failed", "error": str(e)[:500]}

    chosen = [s for s in plan["sections"] if s.key in set(keys)]
    return list(await asyncio.gather(*(one(s) for s in chosen)))


# ═══════════════════════════════════════════════════════════════════════
# Read-back verification
# ═══════════════════════════════════════════════════════════════════════

def _norm(v: Any) -> Any:
    if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
        return sorted(str(x) for x in v)
    return v


def _diff(wanted: Any, actual: Any, path: str, out: List[Dict[str, Any]]) -> None:
    """Every field we set that R1 doesn't now hold. Fields we didn't set are ignored."""
    if isinstance(wanted, dict):
        actual = actual if isinstance(actual, dict) else {}
        for k, v in wanted.items():
            _diff(v, actual.get(k), f"{path}.{k}" if path else k, out)
        return
    if path.endswith("radioParams6G.allowedOutdoorChannels"):
        # Fitted to R1's country list at apply time: R1's must be within ours.
        if actual is not None and set(map(str, actual)) <= set(map(str, wanted)):
            return
    if _norm(wanted) != _norm(actual):
        out.append({"field": path, "wanted": wanted, "actual": actual})


def _poe_default(field_path: str, wanted: Any, actual: Any, poe_caps: Optional[Dict[str, List[str]]]) -> bool:
    """
    R1 stores no poeMode for a model whose setting equals R1's default, and
    that default is Auto: on a production venue R550 (its entry customized
    by a port change) read back Auto, while R650 / T350D, sent Auto, read
    back with no poeMode at all. Wanted Auto + nothing stored = a match,
    for models that support Auto.
    """
    if wanted != "Auto" or actual is not None or not field_path.endswith(".poeMode"):
        return False
    model = field_path.rsplit(".", 1)[0]
    return not poe_caps or "Auto" in (poe_caps.get(model) or ["Auto"])


def diff_section(sec: "_Section", current: Any,
                 poe_caps: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if sec.by_model:
        rows = {r.get("model"): r for r in (current or []) if isinstance(r, dict)}
        wanted = [dict(e) for e in sec.body]
        if poe_caps:
            fit_poe_modes(wanted, poe_caps)   # what apply sent, not what the zone said
        for e in wanted:
            r = rows.get(e["model"]) or {}
            for k, v in e.items():
                if k == "model":
                    continue
                if k == "lanPorts":
                    ports = {p.get("portId"): p for p in r.get("lanPorts") or []}
                    for p in v:
                        _diff({x: y for x, y in p.items() if x != "portId"}, ports.get(p["portId"]),
                              f"{e['model']} port {p['portId']}", out)
                elif not _poe_default(f"{e['model']}.{k}", v, r.get(k), poe_caps):
                    _diff(v, r.get(k), f"{e['model']}.{k}", out)
    else:
        _diff(sec.body, current, "", out)
    return out


async def verify_settings(r1_client, tenant_id: Optional[str], venue_id: str,
                          plan: Dict[str, Any], keys: List[str]) -> List[Dict[str, Any]]:
    """
    Read every chosen section back from R1 and compare it with what the zone
    says: {key, label, status: verified|differs|unreadable, differences}.
    The mesh passphrase is never read back or compared.
    """
    kw = _kw(r1_client, tenant_id)

    async def one(sec: _Section) -> Dict[str, Any]:
        out = {"key": sec.key, "label": sec.label}
        try:
            resp = await asyncio.to_thread(
                lambda: r1_client.get(f"/venues/{venue_id}/{sec.path}", accept=sec.media, **kw))
        except Exception as e:
            return {**out, "status": "unreadable", "error": str(e)[:300], "differences": []}
        if resp.status_code != 200:
            return {**out, "status": "unreadable", "error": r1_error(resp), "differences": []}
        caps = await poe_capabilities(r1_client, venue_id, kw) if sec.key == "model_lan_ports" else None
        diffs = diff_section(sec, resp.json(), caps)
        return {**out, "status": "differs" if diffs else "verified", "differences": diffs}

    chosen = [s for s in plan["sections"] if s.key in set(keys)]
    return list(await asyncio.gather(*(one(s) for s in chosen)))
