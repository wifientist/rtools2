"""
Audit checkpoint 1b: SZ zone settings vs R1 venue Wi-Fi settings.

The "parent" settings every AP inherits -- country, per-band radio (method,
channels, width, power, 6 GHz rates), AP management VLAN, mesh, load and
band balancing, client admission control, rogue detection, and a few AP
behaviours. Field map from the v13.1 SZ spec and the 08122026 R1 spec; the
R1 endpoints used are the current ap* ones (the plain ones are deprecated),
with v1.1 requested where it carries more (radio, mesh).

Encodings differ, so every rule normalises both sides before comparing:
  * SZ "off" is usually an ABSENT object (mesh, CAC, smartMonitor, rogue,
    loadBalancing); R1 has an explicit enabled flag.
  * txPower "Full"/"-3dB(1/2)"/"Min" vs "MAX"/"-3"/"MIN"; SZ -11..-23 dB
    has no R1 value.
  * channelWidth 0/20/40/80/8080/160 vs "AUTO"/"20MHz"...; 8080 has no R1 value.
  * channel lists int vs string; compared as sets.

Credentials (mesh passphrase) are reduced to present/absent at fetch time
and never leave this module as values.

The R1 fetch is I/O; compare_venue_settings is pure.
"""

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from schemas.migration_audit import SettingComparison, VenueSettingsAudit
from services.sz_migration.passphrase_check import fingerprint

logger = logging.getLogger(__name__)

V1 = "application/vnd.ruckus.v1+json"
V1_1 = "application/vnd.ruckus.v1.1+json"

# section key -> (path suffix under /venues/{v}, media type)
R1_ENDPOINTS = {
    "radio": ("apRadioSettings", V1_1),
    "mgmt_vlan": ("apManagementTrafficVlanSettings", V1),
    "mesh": ("apMeshSettings", V1_1),
    "load_balancing": ("apLoadBalancingSettings", V1),
    "cac": ("apClientAdmissionControlSettings", V1),
    "rogue_policy": ("roguePolicySettings", V1),
    "smart_monitor": ("apSmartMonitorSettings", V1),
    "reboot_timeout": ("apRebootTimeoutSettings", V1),
    "bss_coloring": ("apBssColoringSettings", V1),
    "directed_multicast": ("apDirectedMulticastSettings", V1),
}




# ═══════════════════════════════════════════════════════════════════════
# R1 fetch (I/O)
# ═══════════════════════════════════════════════════════════════════════

async def fetch_r1_venue_settings(r1_client, tenant_id: Optional[str], venue_id: str) -> Dict[str, Any]:
    """
    {section: body | None, "_errors": [..]} for the venue, as the given
    tenant (MSP: the delegated EC via x-rks-tenantid).
    """
    msp = getattr(r1_client, "ec_type", None) == "MSP" and tenant_id
    out: Dict[str, Any] = {"_errors": []}

    def get(path: str, accept: str):
        kw = {"override_tenant_id": tenant_id} if msp else {}
        return r1_client.get(path, accept=accept, **kw)

    async def one(key: str, suffix: str, accept: str):
        try:
            resp = await asyncio.to_thread(get, f"/venues/{venue_id}/{suffix}", accept)
        except Exception as e:
            out["_errors"].append(f"{suffix}: {type(e).__name__}")
            out[key] = None
            return
        if resp.status_code != 200:
            out["_errors"].append(f"{suffix}: HTTP {resp.status_code}")
            out[key] = None
            return
        try:
            body = resp.json()
        except ValueError:
            out["_errors"].append(f"{suffix}: unparseable response")
            out[key] = None
            return
        if key == "mesh" and isinstance(body, dict):
            # Never hold the mesh passphrase: keep only whether one is set.
            body = {k: v for k, v in body.items() if k != "passphrase"} | {
                "passphrase_set": bool(body.get("passphrase")),
                # Keyed fingerprint, for a match check without keeping the value
                "passphrase_fp": fingerprint(body.get("passphrase")),
            }
        out[key] = body

    await asyncio.gather(*(one(k, s, a) for k, (s, a) in R1_ENDPOINTS.items()))

    # Rogue detection on/off and mesh on/off live on the venue query row.
    try:
        body = {"filters": {"id": [venue_id]}, "fields": ["id", "rogueDetection", "mesh"], "page": 1, "pageSize": 1}
        kw = {"override_tenant_id": tenant_id} if msp else {}
        resp = await asyncio.to_thread(lambda: r1_client.post("/venues/query", payload=body, **kw))
        rows = (resp.json() or {}).get("data", []) if resp.status_code == 200 else []
        out["venue_query"] = rows[0] if rows else None
    except Exception as e:
        out["_errors"].append(f"venues/query: {type(e).__name__}")
        out["venue_query"] = None
    return out


# ═══════════════════════════════════════════════════════════════════════
# Normalisers
# ═══════════════════════════════════════════════════════════════════════

def tx_power(sz: Optional[str], auto_cell: Optional[bool] = None) -> Optional[str]:
    """SZ txPower -> R1 enum value, or 'UNMAPPABLE:<v>' when R1 can't express it."""
    if auto_cell:
        return "Auto"
    if sz is None:
        return None
    if sz == "Full":
        return "MAX"
    if sz == "Min":
        return "MIN"
    m = re.match(r"^-(\d+)dB", sz)
    if m:
        n = int(m.group(1))
        return f"-{n}" if n <= 10 else f"UNMAPPABLE:{sz}"
    return f"UNMAPPABLE:{sz}"


def channel_width(sz: Optional[int]) -> Optional[str]:
    if sz is None:
        return None
    if sz == 0:
        return "AUTO"
    if sz in (8080, 320):
        return f"UNMAPPABLE:{sz}"
    return f"{sz}MHz"


_METHOD = {"None": "MANUAL", "BackgroundScanning": "BACKGROUND_SCANNING", "ChannelFly": "CHANNELFLY"}
_MESH_RADIO = {"Radio24G": "2.4-GHz", "Radio56G": "5-6-GHz", "Radio5GOnly": "5-GHz", "Radio6GOnly": "6-GHz"}


def channel_set(values: Optional[List[Any]]) -> Optional[List[int]]:
    if values is None:
        return None
    out = set()
    for v in values:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            pass
    return sorted(out)


def bss_min_rate_6g(sz: Optional[str]) -> Optional[str]:
    """HE-MCS0 -> HE_MCS_0."""
    if not sz:
        return None
    m = re.match(r"^HE-MCS(\d)$", sz)
    return f"HE_MCS_{m.group(1)}" if m else f"UNMAPPABLE:{sz}"


def mbps(sz: Optional[str]) -> Optional[str]:
    return sz.replace("mbps", "").strip() if isinstance(sz, str) else sz


# ═══════════════════════════════════════════════════════════════════════
# Comparison (pure)
# ═══════════════════════════════════════════════════════════════════════

class _Rows:
    def __init__(self, r1: Dict[str, Any]):
        self.rows: List[SettingComparison] = []
        self.r1 = r1

    def cmp(self, section: str, label: str, sz: Any, r1: Any, note: str = "",
            r1_section: Optional[str] = None, equal: Optional[Callable[[Any, Any], bool]] = None):
        if r1_section and self.r1.get(r1_section) is None:
            self.rows.append(SettingComparison(section=section, label=label, sz_value=sz,
                                               status="unavailable", note="R1 setting could not be read"))
            return
        if isinstance(sz, str) and sz.startswith("UNMAPPABLE:"):
            self.rows.append(SettingComparison(
                section=section, label=label, sz_value=sz.split(":", 1)[1], r1_value=r1,
                status="unmappable", note=note or "R1 has no equivalent for this SZ value"))
            return
        # On/off settings: a side that doesn't report the setting has it off
        # (SZ omits disabled features; R1 omits defaults that are false), so
        # "—" vs "no" is the same thing, not a difference.
        if (isinstance(sz, bool) or isinstance(r1, bool)) and \
                all(v is None or isinstance(v, bool) for v in (sz, r1)):
            if sz is None and not note:
                note = "Not set on SZ — off"
            elif r1 is None and not note:
                note = "Not set in R1 — off"
            sz, r1 = bool(sz), bool(r1)
        same = equal(sz, r1) if equal else sz == r1
        self.rows.append(SettingComparison(section=section, label=label, sz_value=sz, r1_value=r1,
                                           status="match" if same else "differs", note=note))

    def info(self, section: str, label: str, sz: Any, status: str, note: str, r1: Any = None):
        self.rows.append(SettingComparison(section=section, label=label, sz_value=sz, r1_value=r1,
                                           status=status, note=note))


def _g(d: Any, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


def compare_venue_settings(zone_raw: Dict[str, Any], venue: Dict[str, Any], r1: Dict[str, Any]) -> VenueSettingsAudit:
    """SZ zone (GET /rkszones/{id}) vs R1 venue detail + fetch_r1_venue_settings output."""
    R = _Rows(r1)
    z = zone_raw or {}

    # ── Venue identity ─────────────────────────────────────────────
    R.cmp("Venue", "Country code", z.get("countryCode"), _g(venue, "address", "countryCode"),
          equal=lambda a, b: (a or "").upper() == (b or "").upper())
    R.cmp("Venue", "Time zone", _g(z, "timezone", "systemTimezone"), _g(venue, "address", "timezone"),
          note="SZ and R1 may name the same zone differently")
    if _g(z, "timezone", "customizedTimezone"):
        R.info("Venue", "Custom time zone (GMT offset / DST)", "set", "sz_only", "R1 has no custom time zone")

    # ── Radio per band ─────────────────────────────────────────────
    rc = z.get("radioConfig") or {}
    radio = r1.get("radio") or {}
    bands = (("2.4 GHz", "radio24g", "radioParams24G"), ("5 GHz", "radio5g", "radioParams50G"),
             ("6 GHz", "radio6g", "radioParams6G"))
    for label, szk, r1k in bands:
        s, t = rc.get(szk), radio.get(r1k)
        if s is None and t is None:
            continue
        sec = f"Radio {label}"
        s = s or {}
        t = t or {}
        R.cmp(sec, "Channel selection", _METHOD.get(_g(s, "autoChannelSelection", "channelSelectMode")),
              t.get("method"), r1_section="radio")
        R.cmp(sec, "Channel width", channel_width(s.get("channelWidth")), t.get("channelBandwidth"),
              r1_section="radio")
        R.cmp(sec, "Transmit power", tx_power(s.get("txPower"), s.get("autoCellSizing")), t.get("txPower"),
              r1_section="radio")
        if szk == "radio24g":
            R.cmp(sec, "Allowed channels", channel_set(s.get("channelRange")), channel_set(t.get("allowedChannels")),
                  r1_section="radio")
        else:
            outdoor_key = "allowedOutdoorChannels"
            R.cmp(sec, "Allowed channels (outdoor)", channel_set(s.get("channelRange")),
                  channel_set(t.get(outdoor_key) or t.get("allowedChannels")), r1_section="radio")
            R.cmp(sec, "Allowed channels (indoor)", channel_set(s.get("channelRangeIndoor")),
                  channel_set(t.get("allowedIndoorChannels")), r1_section="radio")
        if s.get("channel"):
            R.info(sec, "Manual channel", s.get("channel"), "sz_only",
                   "R1 sets manual channels per AP Group or AP, not per venue")
        if szk == "radio6g":
            R.cmp(sec, "BSS minimum rate", bss_min_rate_6g(s.get("bssMinRate")), t.get("bssMinRate6G"),
                  r1_section="radio")
            R.cmp(sec, "Management Tx rate", mbps(s.get("mgmtTxRate")), t.get("mgmtTxRate6G"),
                  r1_section="radio")
            R.cmp(sec, "AFC", s.get("afcEnabled"), t.get("enableAfc"), r1_section="radio")
        if s.get("protectionMode"):
            R.info(sec, "Protection mode", s.get("protectionMode"), "sz_only", "R1 has no protection mode setting")

    R.cmp("Radio 5 GHz", "Dual 5 GHz", bool(rc.get("dual5gEnabled")),
          bool(_g(radio, "radioParamsDual5G", "enabled")), r1_section="radio")

    # ── AP management VLAN ─────────────────────────────────────────
    mv = z.get("apMgmtVlan") or {}
    r1_vlan = _g(r1, "mgmt_vlan", "vlanId")
    if mv.get("mode") == "USER_DEFINED":
        R.cmp("AP management", "Management VLAN", mv.get("id"), r1_vlan, r1_section="mgmt_vlan")
    elif r1.get("mgmt_vlan") is not None:
        R.info("AP management", "Management VLAN", "keep AP's own (KEEP)", "review",
               "SZ keeps each AP's VLAN; R1 shows the venue value — confirm it is the untagged/keep setting",
               r1=r1_vlan)

    # ── Mesh ───────────────────────────────────────────────────────
    mesh_sz = z.get("mesh")
    mesh_r1 = r1.get("mesh") or {}
    R.cmp("Mesh", "Mesh enabled", mesh_sz is not None,
          bool(mesh_r1.get("enabled") or _g(r1, "venue_query", "mesh", "enabled")), r1_section="mesh")
    if mesh_sz is not None:
        R.cmp("Mesh", "Mesh SSID", mesh_sz.get("ssid"), mesh_r1.get("ssid"), r1_section="mesh")
        R.cmp("Mesh", "Mesh radio", _MESH_RADIO.get(mesh_sz.get("meshRadioOption")), mesh_r1.get("radioType"),
              r1_section="mesh")
        R.cmp("Mesh", "Mesh passphrase set", bool(mesh_sz.get("passphrase")), bool(mesh_r1.get("passphrase_set")),
              note="Compared as set / not set only", r1_section="mesh")
        sz_fp, r1_fp = fingerprint(mesh_sz.get("passphrase")), mesh_r1.get("passphrase_fp")
        if sz_fp and r1_fp and sz_fp.split(":")[1] == r1_fp.split(":")[1]:
            R.cmp("Mesh", "Mesh passphrase matches", "SZ passphrase", "same" if sz_fp == r1_fp else "different",
                  note="Compared server-side; values never shown", r1_section="mesh",
                  equal=lambda a, b: b == "same")

    # ── Load / band balancing ──────────────────────────────────────
    lb = z.get("loadBalancing")
    lbr = r1.get("load_balancing") or {}
    lb_on = bool(lb) and lb.get("loadBalancingMethod") not in (None, "OFF")
    R.cmp("Load balancing", "Client load balancing", lb_on, bool(lbr.get("enabled")), r1_section="load_balancing")
    if lb_on:
        R.cmp("Load balancing", "Method", lb.get("loadBalancingMethod"), lbr.get("loadBalancingMethod"),
              r1_section="load_balancing")
    bb = (lb or {}).get("bandBalancing")
    R.cmp("Load balancing", "Band balancing", bb is not None, bool(lbr.get("bandBalancingEnabled")),
          r1_section="load_balancing")
    if bb is not None:
        R.cmp("Load balancing", "Band balancing 2.4 GHz client %", bb.get("wifi24Percentage"),
              lbr.get("bandBalancingClientPercent24G"), r1_section="load_balancing")
    if lb and lb.get("steeringMode"):
        R.cmp("Load balancing", "Steering mode", lb.get("steeringMode"), lbr.get("steeringMode"),
              r1_section="load_balancing")

    # ── Client admission control ───────────────────────────────────
    cac = r1.get("cac") or {}
    for label, szk, sfx in (("2.4 GHz", "clientAdmissionControl24", "24G"), ("5 GHz", "clientAdmissionControl50", "50G")):
        s = z.get(szk)
        R.cmp("Client admission control", f"{label} enabled", s is not None, bool(cac.get(f"enable{sfx}")),
              r1_section="cac")
        if s is not None:
            R.cmp("Client admission control", f"{label} max radio load %", s.get("maxRadioLoadPercent"),
                  cac.get(f"maxRadioLoad{sfx}"), r1_section="cac")
            R.cmp("Client admission control", f"{label} min client count", s.get("minClientCount"),
                  cac.get(f"minClientCount{sfx}"), r1_section="cac")
            R.cmp("Client admission control", f"{label} min client throughput (Mbps)",
                  s.get("minClientThroughputMbps"), cac.get(f"minClientThroughput{sfx}"), r1_section="cac",
                  equal=lambda a, b: a is not None and b is not None and float(a) == float(b))

    # ── Rogue detection ────────────────────────────────────────────
    rogue_r1 = _g(r1, "venue_query", "rogueDetection", "enabled")
    if r1.get("venue_query") is None:
        R.info("Rogue detection", "Rogue detection enabled", z.get("rogue") is not None, "unavailable",
               "R1 venue row could not be read")
    else:
        R.cmp("Rogue detection", "Rogue detection enabled", z.get("rogue") is not None, bool(rogue_r1))
    if z.get("rogueApReportThreshold") is not None:
        R.cmp("Rogue detection", "Report threshold", z.get("rogueApReportThreshold"),
              _g(r1, "rogue_policy", "reportThreshold"), r1_section="rogue_policy",
              note="SZ doesn't state the unit; confirm both mean the same scale")

    # ── AP behaviours ──────────────────────────────────────────────
    sm = z.get("smartMonitor")
    smr = r1.get("smart_monitor") or {}
    R.cmp("AP behaviour", "Smart Monitor", sm is not None, bool(smr.get("enabled")), r1_section="smart_monitor")
    if sm is not None:
        R.cmp("AP behaviour", "Smart Monitor interval (s)", sm.get("intervalInSec"), smr.get("interval"),
              r1_section="smart_monitor")
        R.cmp("AP behaviour", "Smart Monitor retry threshold", sm.get("retryThreshold"), smr.get("threshold"),
              r1_section="smart_monitor")
    rb = z.get("apRebootTimeout") or {}
    rbr = r1.get("reboot_timeout") or {}
    if rb:
        R.cmp("AP behaviour", "Reboot on gateway loss (s)", rb.get("gatewayLossTimeoutInSec"),
              rbr.get("gatewayLossTimeout"), r1_section="reboot_timeout")
        R.cmp("AP behaviour", "Reboot on server loss (s)", rb.get("serverLossTimeoutInSec"),
              rbr.get("serverLossTimeout"), r1_section="reboot_timeout")
    if z.get("bssColoringEnable") is not None:
        R.cmp("AP behaviour", "BSS coloring", bool(z.get("bssColoringEnable")),
              bool(_g(r1, "bss_coloring", "bssColoringEnabled")), r1_section="bss_coloring")
    dm = r1.get("directed_multicast") or {}
    for label, szk, r1k in (("wired clients", "directedMulticastFromWiredClientEnabled", "wiredEnabled"),
                            ("wireless clients", "directedMulticastFromWirelessClientEnabled", "wirelessEnabled"),
                            ("network", "directedMulticastFromNetworkEnabled", "networkEnabled")):
        if z.get(szk) is not None:
            R.cmp("AP behaviour", f"Directed multicast from {label}", bool(z.get(szk)), bool(dm.get(r1k)),
                  r1_section="directed_multicast")

    return VenueSettingsAudit(rows=R.rows, errors=list(r1.get("_errors") or []))
