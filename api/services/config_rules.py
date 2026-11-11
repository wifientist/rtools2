"""
Config Rules: check R1 venues, AP Groups and WLANs against a company's rules.

A run has two halves. A job captures (workflow/phases/config_rules.py); the
report evaluates here, with no R1 calls, so a rule can change and be re-checked
without reading R1 again.

ONLY CATALOGUE FIELDS ARE KEPT. A WLAN GET carries its passphrase and a venue
read can carry mesh or AAA secrets; extract() copies the listed paths and
nothing else, so a secret cannot reach Redis or a report by way of this tool.

A RESULT IS NOT JUST PASS OR FAIL. A value R1 did not give us is "unavailable",
never a failure: a 403, a 429 or a missing key says nothing about the config.

    pass, fail
    na           the rule does not apply (join RSSI threshold with the feature off)
    inherited    an AP Group band using the venue's settings; the venue rule covers it
    unavailable  could not read it, or dual-5G makes the 5 GHz field meaningless

Field notes, checked live on SuperSandbox 2026-11-01:
  * dtimInterval is returned but is not in the 08122026 spec.
  * Wi-Fi calling and the ACL flags are not on /wifiNetworks/{id}. They read
    from the "legacy" section, which since 2026-11-11 is built from which
    profiles (Wi-Fi calling, access control, L2/L3 ACL) list the network in
    their wifiNetworkIds, not from the deprecated /networks/{id}.
  * Venue 5 GHz is radioParams50G, AP Group 5 GHz is radioParams5G.
"""

from typing import Any, Dict, Iterable, List, Optional, Set

V1_1 = "application/vnd.ruckus.v1.1+json"

# Venue sections: key -> (path suffix, accept)
VENUE_SECTIONS = {
    "radio": ("apRadioSettings", V1_1),
    "smart_monitor": ("apSmartMonitorSettings", None),
    "dos": ("apDosProtectionSettings", None),
    "bss_coloring": ("apBssColoringSettings", None),
    "reboot": ("apRebootTimeoutSettings", None),
}
# Rogue on/off lives on the venue query row; rogueApSettings is deprecated.
VENUE_ROW = "venue_row"

OPS_BY_TYPE = {
    "bool": ["eq"],
    "enum": ["eq", "in", "not_in"],
    "int": ["eq", "range"],
    "channels": ["only", "includes", "excludes"],
}
OP_LABELS = {
    "eq": "is", "in": "is one of", "not_in": "is not one of", "range": "is within",
    "only": "uses only", "includes": "includes all of", "excludes": "excludes all of",
}

METHODS = ["BACKGROUND_SCANNING", "CHANNELFLY", "MANUAL"]
TX_POWER = ["Auto", "MAX", "-1", "-2", "-3", "-4", "-5", "-6", "-7", "-8", "-9", "-10", "MIN"]
WIDTHS = {
    "24g": ["AUTO", "20MHz", "40MHz"],
    "5g": ["AUTO", "20MHz", "40MHz", "80MHz", "160MHz"],
    "6g": ["AUTO", "20MHz", "40MHz", "80MHz", "160MHz"],
}
BAND_LABELS = {"24g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}

CH_24 = [str(c) for c in range(1, 14)]
UNII_1 = ["36", "40", "44", "48"]
DFS = ["52", "56", "60", "64", "100", "104", "108", "112", "116", "120", "124", "128", "132", "136", "140", "144"]
UNII_3 = ["149", "153", "157", "161", "165"]
CH_5 = UNII_1 + DFS + UNII_3
CH_6 = [str(c) for c in range(1, 234, 4)]
# US plan, for the rule builder's shortcuts. Rules store plain channel lists.
PRESETS_24 = {"1, 6, 11": ["1", "6", "11"]}
PRESETS_5 = {"DFS": DFS, "Non-DFS": UNII_1 + UNII_3, "UNII-1": UNII_1, "UNII-3": UNII_3, "Channel 144": ["144"]}


def _field(id: str, label: str, group: str, level: str, section: str, path: str, type: str, **kw) -> Dict[str, Any]:
    return {"id": id, "label": label, "group": group, "level": level, "section": section,
            "path": path, "type": type, "ops": OPS_BY_TYPE[type], **kw}


def _radio_fields(level: str, keys: Dict[str, str]) -> List[Dict[str, Any]]:
    prefix = "venue" if level == "venue" else "group"
    out: List[Dict[str, Any]] = []
    for band, key in keys.items():
        name = BAND_LABELS[band]
        group = f"Wi-Fi Radio: {name}"
        kw: Dict[str, Any] = {"band": band}
        if level == "ap_group":
            kw["inherit_path"] = f"{key}.useVenueSettings"
        section = "group_radio" if level == "ap_group" else "radio"

        def add(suffix, label, path, type, **extra):
            out.append(_field(f"{prefix}.radio.{band}.{suffix}", f"{name} {label}", group, level, section,
                              f"{key}.{path}", type, **kw, **extra))

        add("method", "channel selection", "method", "enum", options=METHODS)
        add("width", "channel width", "channelBandwidth", "enum", options=WIDTHS[band])
        add("tx", "transmit power", "txPower", "enum", options=TX_POWER if band != "6g" else TX_POWER[1:])
        if band == "24g":
            add("channels", "channels", "allowedChannels", "channels", options=CH_24, presets=PRESETS_24)
        else:
            options, presets = (CH_5, PRESETS_5) if band == "5g" else (CH_6, {})
            add("channels_indoor", "indoor channels", "allowedIndoorChannels", "channels",
                options=options, presets=presets)
            add("channels_outdoor", "outdoor channels", "allowedOutdoorChannels", "channels",
                options=options, presets=presets)
    return out


def _wlan_fields() -> List[Dict[str, Any]]:
    adv = "wlan.advancedCustomization."
    radio = adv + "radioCustomization."

    def wlan(id, label, group, path, type, section="wifi", **kw):
        return _field(f"wlan.{id}", label, group, "wlan", section, path, type, **kw)

    control, radio_g, net, advanced = ("WLAN: Network Control", "WLAN: Radio", "WLAN: Network", "WLAN: Advanced")
    return [
        wlan("wifi_calling", "Wi-Fi calling", control, adv + "wifiCallingEnabled", "bool", section="legacy"),
        wlan("client_isolation", "Client isolation", control, adv + "clientIsolation", "bool"),
        wlan("isolation_packets", "Isolated packets", control, adv + "clientIsolationOptions.packetsType", "enum",
             options=["UNICAST", "MULTICAST", "UNICAST_MULTICAST"],
             only_when={"field": "wlan.client_isolation", "value": True}),
        wlan("access_control", "Access control", control, adv + "accessControlEnable", "bool", section="legacy"),
        wlan("l2_acl", "Layer 2 ACL", control, adv + "l2AclEnable", "bool", section="legacy"),
        wlan("l3_acl", "Layer 3 ACL", control, adv + "l3AclEnable", "bool", section="legacy"),

        wlan("ofdm", "PHY type (OFDM only)", radio_g, radio + "phyTypeConstraint", "enum", options=["OFDM", "NONE"]),
        wlan("bss_min", "BSS minimum rate", radio_g, radio + "bssMinimumPhyRate", "enum",
             options=["1", "2", "5.5", "12", "24", "default"]),
        wlan("mgmt_tx", "Management Tx rate", radio_g, radio + "managementFrameMinimumPhyRate", "enum",
             options=["1", "2", "5.5", "6", "9", "11", "12", "18", "24", "36", "48", "54"]),

        wlan("neighbor_report", "11k neighbor reports", net, adv + "enableNeighborReport", "bool"),
        wlan("inactivity", "Client inactivity timeout (s)", net, adv + "clientInactivityTimeout", "int",
             min=60, max=86400),
        wlan("join_rssi_on", "Join RSSI threshold enabled", net, adv + "enableJoinRSSIThreshold", "bool"),
        wlan("join_rssi", "Join RSSI threshold (dBm)", net, adv + "joinRSSIThreshold", "int", min=-90, max=-60,
             only_when={"field": "wlan.join_rssi_on", "value": True}),
        wlan("transient", "Transient client management", net, adv + "enableTransientClientManagement", "bool"),
        wlan("multicast_filter", "Multicast filter", net, adv + "multicastFilterEnabled", "bool"),
        wlan("mlo", "MLO", net, adv + "multiLinkOperationEnabled", "bool"),

        wlan("dtim", "DTIM interval", advanced, adv + "dtimInterval", "int", min=1, max=255),
        wlan("qos_mirroring", "QoS mirroring", advanced, adv + "qosMirroringEnabled", "bool"),
        wlan("qos_mirroring_scope", "QoS mirroring scope", advanced, adv + "qosMirroringScope", "enum",
             options=["MSCS_REQUESTS_ONLY", "ALL_CLIENTS"],
             only_when={"field": "wlan.qos_mirroring", "value": True}),
    ]


def _build_catalog() -> List[Dict[str, Any]]:
    fields = _radio_fields("venue", {"24g": "radioParams24G", "5g": "radioParams50G", "6g": "radioParams6G"})
    fields.append(_field("venue.radio.6g.afc", "6 GHz AFC", "Wi-Fi Radio: 6 GHz", "venue", "radio",
                         "radioParams6G.enableAfc", "bool"))
    fields += [
        _field("venue.smart_monitor", "Smart Monitor", "Networking", "venue", "smart_monitor", "enabled", "bool"),
        _field("venue.dos", "DoS protection", "Security", "venue", "dos", "enabled", "bool"),
        _field("venue.rogue", "Rogue detection", "Security", "venue", VENUE_ROW, "rogueDetection.enabled", "bool"),
        _field("venue.bss_coloring", "BSS coloring", "Advanced", "venue", "bss_coloring", "bssColoringEnabled", "bool"),
        _field("venue.reboot_gateway", "Reboot on gateway loss timeout (s)", "Advanced", "venue", "reboot",
               "gatewayLossTimeout", "int", min=0, max=86400),
        _field("venue.reboot_server", "Reboot on controller loss timeout (s)", "Advanced", "venue", "reboot",
               "serverLossTimeout", "int", min=0, max=86400),
    ]
    fields += _radio_fields("ap_group", {"24g": "radioParams24G", "5g": "radioParams5G", "6g": "radioParams6G"})
    return fields + _wlan_fields()


CATALOG = _build_catalog()
FIELDS = {f["id"]: f for f in CATALOG}


def _starter(id: str, name: str, field: str, op: str, value: Any) -> Dict[str, Any]:
    return {"id": id, "name": name, "field": field, "op": op, "value": value, "enabled": True}


STARTER_RULES = [
    _starter("s1", "2.4 GHz uses 1, 6, 11 only", "venue.radio.24g.channels", "only", ["1", "6", "11"]),
    _starter("s2", "2.4 GHz width is 20 MHz", "venue.radio.24g.width", "eq", "20MHz"),
    _starter("s3", "5 GHz width is 20, 40 or 80", "venue.radio.5g.width", "in", ["20MHz", "40MHz", "80MHz"]),
    _starter("s4", "6 GHz width is 20 to 160", "venue.radio.6g.width", "in", ["20MHz", "40MHz", "80MHz", "160MHz"]),
    _starter("s5", "2.4 GHz power is not full", "venue.radio.24g.tx", "not_in", ["MAX"]),
    _starter("s6", "6 GHz AFC is off", "venue.radio.6g.afc", "eq", False),
    _starter("s7", "Smart Monitor is on", "venue.smart_monitor", "eq", True),
    _starter("s8", "DoS protection is on", "venue.dos", "eq", True),
    _starter("s9", "Rogue detection is on", "venue.rogue", "eq", True),
    _starter("s10", "BSS coloring is on", "venue.bss_coloring", "eq", True),
    _starter("s11", "OFDM only on every WLAN", "wlan.ofdm", "eq", "OFDM"),
]


# ── Rules ────────────────────────────────────────────────────────────

def clean_rule(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A rule fit to store, or ValueError saying what is wrong with it."""
    name = str(raw.get("name") or "").strip()
    rule_id = str(raw.get("id") or "").strip()
    if not rule_id or not name:
        raise ValueError("Every rule needs an id and a name")
    field = FIELDS.get(raw.get("field"))
    if not field:
        raise ValueError(f"Rule '{name}': unknown setting {raw.get('field')!r}")
    op, value = raw.get("op"), raw.get("value")
    if op not in field["ops"]:
        raise ValueError(f"Rule '{name}': {op!r} cannot be used on {field['label']}")

    if op == "eq" and field["type"] == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"Rule '{name}': value must be on or off")
    elif op == "eq" and field["type"] == "int":
        if not _is_number(value):
            raise ValueError(f"Rule '{name}': value must be a number")
    elif op == "eq":
        if not isinstance(value, str) or not value:
            raise ValueError(f"Rule '{name}': choose a value")
    elif op == "range":
        low, high = (value.get("min"), value.get("max")) if isinstance(value, dict) else (None, None)
        if (low is None and high is None) or any(v is not None and not _is_number(v) for v in (low, high)):
            raise ValueError(f"Rule '{name}': give a minimum, a maximum or both")
        if low is not None and high is not None and low > high:
            raise ValueError(f"Rule '{name}': the minimum is above the maximum")
        value = {"min": low, "max": high}
    else:
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise ValueError(f"Rule '{name}': choose at least one value")
    return {"id": rule_id, "name": name, "field": field["id"], "op": op, "value": value,
            "enabled": raw.get("enabled", True) is not False}


def clean_rules(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rules = [clean_rule(r) for r in raw]
    if len({r["id"] for r in rules}) != len(rules):
        raise ValueError("Two rules share an id")
    return rules


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def sections_needed(rules: Iterable[Dict[str, Any]]) -> Set[str]:
    """The sections a run has to read: a venue's cost is only what its rules use."""
    return {FIELDS[r["field"]]["section"] for r in rules if r.get("enabled", True) and r["field"] in FIELDS}


# ── Capture helpers ──────────────────────────────────────────────────

def dig(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def extract(body: Any, section: str) -> Dict[str, Any]:
    """The catalogue fields of one section. Everything else in the body is dropped."""
    values: Dict[str, Any] = {}
    inherited: List[str] = []
    blocked: List[str] = []
    for field in CATALOG:
        if field["section"] != section:
            continue
        values[field["id"]] = dig(body, field["path"])
        if field.get("inherit_path") and dig(body, field["inherit_path"]) is True:
            inherited.append(field["id"])
        if field.get("band") == "5g" and dig(body, "radioParamsDual5G.enabled") is True:
            blocked.append(field["id"])
    return {"ok": True, "values": values, "inherited": inherited, "blocked": blocked}


# ── Evaluation (no R1 calls) ─────────────────────────────────────────

def compare(field: Dict[str, Any], op: str, expected: Any, actual: Any) -> bool:
    if op == "eq":
        if field["type"] == "bool":
            return actual is expected
        if field["type"] == "int":
            return _is_number(actual) and actual == expected
        return str(actual) == expected
    if op == "in":
        return str(actual) in expected
    if op == "not_in":
        return str(actual) not in expected
    if op == "range":
        if not _is_number(actual):
            return False
        return ((expected.get("min") is None or actual >= expected["min"])
                and (expected.get("max") is None or actual <= expected["max"]))
    have = {str(c) for c in (actual if isinstance(actual, list) else [])}
    want = set(expected)
    if op == "only":
        return have <= want
    if op == "includes":
        return want <= have
    if op == "excludes":
        return not (want & have)
    raise ValueError(f"Unknown operator {op}")


def _item(object_name: str, state: str, actual: Any = None, reason: str = "") -> Dict[str, Any]:
    return {"object": object_name, "state": state, "actual": actual, "reason": reason}


def _check(rule: Dict[str, Any], field: Dict[str, Any], object_name: str,
           sections: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    section = sections.get(field["section"])
    if section is None:
        return _item(object_name, "unavailable", reason="Not read in this run. Run it again to include this setting.")
    if not section.get("ok"):
        return _item(object_name, "unavailable", reason=f"Could not read from R1: {section.get('error') or 'unknown'}")
    if field["id"] in (section.get("inherited") or []):
        return _item(object_name, "inherited", reason="Uses the venue's settings")
    if field["id"] in (section.get("blocked") or []):
        return _item(object_name, "unavailable", reason="Dual-5G is on; this rule does not cover its radios yet")
    values = section.get("values") or {}
    gate = field.get("only_when")
    if gate:
        gate_section = sections.get(FIELDS[gate["field"]]["section"]) or {}
        gate_value = (gate_section.get("values") or {}).get(gate["field"])
        if gate_value is not None and gate_value != gate["value"]:
            return _item(object_name, "na", reason=f"{FIELDS[gate['field']]['label']} is off")
    actual = values.get(field["id"])
    if actual is None:
        return _item(object_name, "unavailable", reason="R1 did not return this setting")
    passed = compare(field, rule["op"], rule["value"], actual)
    return _item(object_name, "pass" if passed else "fail", actual)


STATE_ORDER = ["fail", "unavailable", "pass", "inherited", "na"]


def _rollup(items: List[Dict[str, Any]]) -> str:
    states = {i["state"] for i in items}
    return next((s for s in STATE_ORDER if s in states), "na")


def evaluate_venue(rule: Dict[str, Any], capture: Dict[str, Any], wlans: Dict[str, Any]) -> Dict[str, Any]:
    field = FIELDS[rule["field"]]
    if capture.get("error"):
        items = [_item(capture.get("name") or "Venue", "unavailable", reason=capture["error"])]
    elif field["level"] == "venue":
        items = [_check(rule, field, capture.get("name") or "Venue", capture.get("sections") or {})]
    elif field["level"] == "ap_group":
        if capture.get("groups_error"):
            items = [_item("AP Groups", "unavailable", reason=f"Could not read from R1: {capture['groups_error']}")]
        elif "group_radio" not in (capture.get("read") or []):
            items = [_item("AP Groups", "unavailable", reason="Not read in this run. Run it again to include this setting.")]
        else:
            items = [_check(rule, field, g.get("name") or "Default", {"group_radio": g.get("radio") or {}})
                     for g in capture.get("groups") or []]
    else:
        if wlans.get("error"):
            items = [_item("WLANs", "unavailable", reason=f"Could not read from R1: {wlans['error']}")]
        else:
            items = [_check(rule, field, w.get("name") or w["id"], w.get("sections") or {})
                     for w in (wlans.get("networks") or {}).values()
                     if capture.get("id") in (w.get("venue_ids") or [])]
    return {"state": _rollup(items), "items": items}


def build_report(rules: List[Dict[str, Any]], captures: List[Dict[str, Any]],
                 wlans: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    wlans = wlans or {}
    active = [r for r in rules if r.get("enabled", True) and r.get("field") in FIELDS]
    venues = sorted(captures, key=lambda c: (c.get("name") or "").lower())
    out_rules = []
    out_venues = [{"id": v.get("id"), "name": v.get("name"), "results": {},
                   "counts": dict.fromkeys(STATE_ORDER, 0)} for v in venues]
    for rule in active:
        field = FIELDS[rule["field"]]
        counts = dict.fromkeys(STATE_ORDER, 0)
        for venue, row in zip(venues, out_venues):
            result = evaluate_venue(rule, venue, wlans)
            row["results"][rule["id"]] = result
            row["counts"][result["state"]] += 1
            counts[result["state"]] += 1
        out_rules.append({**rule, "field_label": field["label"], "group": field["group"], "level": field["level"],
                          "op_label": OP_LABELS[rule["op"]], "counts": counts})
    totals = dict.fromkeys(STATE_ORDER, 0)
    for rule in out_rules:
        for state, n in rule["counts"].items():
            totals[state] += n
    return {"rules": out_rules, "venues": out_venues, "totals": totals}
