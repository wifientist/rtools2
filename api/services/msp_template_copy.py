"""
Template Tool copy planning: read a venue "bundle" and preview a copy of it.

A bundle is a VENUE template plus the templates hanging off it (networks, the
switch CLI profile, and their portal / DPSK / identity-group children). The
reason to copy one rather than Apply it is the country: country code is fixed
when a venue (template) is created and cannot be edited afterwards, so a venue
for another country needs a NEW venue template.

The country also decides which radio channels are legal, and the venue's
apRadioSettings stores explicit channel lists. R1 only answers "which channels
are legal" for an existing venue template (`wifiAvailableChannels`); no endpoint
takes a country, and countryCode/country query params are silently ignored. So
the preview borrows a venue template that already has the target country; if
there is none, the trim can only happen after the new venue template exists.

PREVIEW ONLY. Nothing here writes to R1. `preview()` returns the ordered steps
a copy would take, so the write path can be built against a reviewed plan.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from services import msp_templates as mt

logger = logging.getLogger(__name__)

MAX_DEPTH = 4

# Venue settings a copy writes back with a plain PUT to the same path.
# apModelLanPortSettings is read-only at this path (LAN ports are written per
# AP model and port) and switchProfiles is the SWITCH_CLI child, attached as a
# template, so neither is copied as a setting.
NOT_COPIED_SETTINGS = {
    "apModelLanPortSettings": "Read-only here; LAN ports are written per AP model and port. Not copied yet.",
    "switchProfiles": "Copied as the SWITCH_CLI child template, not as a setting.",
}

# How each type is created. IDENTITY_GROUP has no documented create endpoint;
# DPSK is created THROUGH its identity group.
CREATE_PATHS = {
    "VENUE": "/templates/venues",
    "NETWORK": "/templates/wifiNetworks",
    "PORTAL": "/templates/portalServiceProfiles",
    "SWITCH_CLI": "/templates/switchProfiles",
    "DPSK": "/templates/identityGroups/{identityGroup}/dpskServices",
    "IDENTITY_GROUP": "/templates/identityGroups",  # undocumented -- unverified
}
UNVERIFIED_CREATES = {"IDENTITY_GROUP"}

# (parent type, child type) -> how the child is attached to the parent.
ATTACH_PATHS = {
    ("VENUE", "NETWORK"): "/templates/venues/{parent}/wifiNetworks/{child}",
    ("VENUE", "SWITCH_CLI"): "/templates/venues/{parent}/switchProfiles/{child}",
    ("NETWORK", "PORTAL"): "/templates/wifiNetworks/{parent}/portalServiceProfiles/{child}",
    ("NETWORK", "DPSK"): "/templates/wifiNetworks/{parent}/dpskServices/{child}",
}

# Radio band key in apRadioSettings -> band key in wifiAvailableChannels.
RADIO_BANDS = {
    "radioParams24G": "2.4GChannels",
    "radioParams50G": "5GChannels",
    "radioParams6G": "6GChannels",
}
DUAL5G_BANDS = {"lower": "5GLowerChannels", "upper": "5GUpperChannels"}
CHANNEL_FIELDS = {
    "allowedChannels": None,          # band-level list (2.4G, 6G)
    "allowedIndoorChannels": "indoor",
    "allowedOutdoorChannels": "outdoor",
}


# ── Reading the bundle ─────────────────────────────────────────────────────


def _node(r1, row: Dict[str, Any], context: str, parent: Optional[Dict[str, Any]],
          depth: int, seen: Set[str]) -> Dict[str, Any]:
    node = {
        "id": row["id"],
        "name": row.get("name"),
        "type": row.get("type"),
        "context": context,
        "body": None,
        "settings": None,
        "venueNetworkSettings": None,
        "error": None,
        "children": [],
    }
    read = mt.body(r1, row["id"], row.get("type"), context)
    node["body"], node["settings"], node["error"] = read["body"], read["settings"], read["error"]

    # A network's per-venue activation (radio types, VLAN, schedule) belongs
    # to the venue->network link, not to either template.
    if parent and parent["type"] == "VENUE" and node["type"] == "NETWORK":
        r = r1.get(f"{mt._prefix(context)}/templates/venues/{parent['id']}"
                   f"/wifiNetworks/{node['id']}/settings")
        node["venueNetworkSettings"] = r.json() if r.ok else {"_error": mt._error(r, "venue network settings")}

    seen.add(node["id"])
    if depth >= MAX_DEPTH or row.get("supportsChildren") is False:
        return node
    kids = mt.children(r1, node["id"], context)
    if kids["error"]:
        node["error"] = node["error"] or kids["error"]
    for kid in kids["children"]:
        if kid.get("id") in seen:
            continue  # shared child already read on another branch
        node["children"].append(_node(r1, kid, context, node, depth + 1, seen))
    return node


def bundle(r1, template_id: str, context: str = "MSP") -> Dict[str, Any]:
    """The venue template and everything under it, with full bodies."""
    rows = mt.list_templates(r1, context)["templates"]
    row = next((t for t in rows if t["id"] == template_id), None)
    if not row:
        return {"root": None, "error": f"Template {template_id} not found in {context} templates."}
    if row.get("type") != "VENUE":
        return {"root": None, "error": f"Only VENUE templates can be copied as a bundle; this is {row.get('type')}."}
    return {"root": _node(r1, row, context, None, 0, set()), "error": None,
            "notCopiedSettings": NOT_COPIED_SETTINGS}


def venue_templates(r1, context: str = "MSP") -> List[Dict[str, Any]]:
    """Venue templates with their country -- /templates/query omits address."""
    r = r1.post(f"{mt._prefix(context)}/templates/venues/query",
                payload={"page": 1, "pageSize": mt.PAGE_SIZE})
    if not r.ok:
        return []
    return [{k: v.get(k) for k in ("id", "name", "country", "countryCode", "timezone")}
            for v in (r.json() or {}).get("data") or []]


def reference_channels(r1, country_code: str, context: str = "MSP") -> Dict[str, Any]:
    """Legal channels for a country, borrowed from a venue template that has it."""
    code = (country_code or "").upper()
    ref = next((v for v in venue_templates(r1, context)
                if (v.get("countryCode") or "").upper() == code), None)
    if not ref:
        return {"reference": None, "channels": None}
    r = r1.get(f"{mt._prefix(context)}/templates/venues/{ref['id']}/wifiAvailableChannels")
    if not r.ok:
        return {"reference": ref, "channels": None, "error": mt._error(r, "available channels")}
    return {"reference": ref, "channels": r.json()}


# ── Channel trimming ───────────────────────────────────────────────────────


def _legal(table: Optional[Dict[str, Any]], scope: Optional[str], bandwidth: str) -> Optional[List[str]]:
    """Legal channels for one band table, scope (indoor/outdoor/None) and width."""
    if not isinstance(table, dict):
        return None
    if scope:
        table = table.get(scope)
        if not isinstance(table, dict):
            return None
    for key in (bandwidth, "auto"):
        if isinstance(table.get(key), list):
            return [str(c) for c in table[key]]
    return None


def _trim_params(params: Dict[str, Any], table: Any, band_label: str,
                 report: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(params)
    width = str(params.get("channelBandwidth") or "auto")
    if width.upper() == "AUTO":
        width = "auto"
    for field, scope in CHANNEL_FIELDS.items():
        current = params.get(field)
        if not isinstance(current, list):
            continue
        legal = _legal(table, scope, width)
        if legal is None and scope is None:
            legal = _legal(table, "indoor", width)  # 5G has no band-level list
        entry = {"band": band_label, "field": field, "bandwidth": width,
                 "source": [str(c) for c in current]}
        if legal is None:
            entry.update(kept=entry["source"], dropped=[], checked=False)
        else:
            legal_set = set(legal)
            kept = [c for c in entry["source"] if c in legal_set]
            entry.update(kept=kept, dropped=[c for c in entry["source"] if c not in legal_set],
                         checked=True)
            out[field] = kept
        entry["emptied"] = entry["checked"] and not entry["kept"] and bool(entry["source"])
        report.append(entry)
    return out


def trim_radio(radio: Dict[str, Any], available: Dict[str, Any]):
    """
    (trimmed apRadioSettings, report). Every channel list is intersected with
    what the target country allows at that band, scope and width. A list the
    table cannot speak for is left alone and reported `checked: False`.
    """
    report: List[Dict[str, Any]] = []
    trimmed = dict(radio or {})
    for band, avail_key in RADIO_BANDS.items():
        if isinstance(trimmed.get(band), dict):
            trimmed[band] = _trim_params(trimmed[band], available.get(avail_key), band, report)
    dual = trimmed.get("radioParamsDual5G")
    if isinstance(dual, dict):
        dual = dict(dual)
        for key, val in dual.items():
            half = next((h for h in DUAL5G_BANDS if h in key.lower()), None)
            if half and isinstance(val, dict):
                dual[key] = _trim_params(val, available.get(DUAL5G_BANDS[half]),
                                         f"radioParamsDual5G.{key}", report)
        trimmed["radioParamsDual5G"] = dual
    return trimmed, report


# ── Preview ────────────────────────────────────────────────────────────────


def _walk(node: Dict[str, Any], parent: Optional[Dict[str, Any]] = None):
    yield node, parent
    for kid in node["children"]:
        yield from _walk(kid, node)


MODES = ("reuse", "copy", "skip")


def resolve_modes(root: Dict[str, Any], requested: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """
    id -> {mode, chosen, reason} for every template under the venue.

    The venue itself is always new (its country is the point). Under it, each
    template is REUSED by default: the existing template is attached to the new
    venue, and everything under it comes along untouched. Only the children of
    a COPIED template get a choice of their own. One forced rule: a copied DPSK
    pool needs a copied identity group, because an identity group belongs to
    exactly one pool (DPSK-10032).

    The frontend mirrors these rules (CopyPlanner.effectiveModes) to show them
    live; this is the authority.
    """
    out: Dict[str, Dict[str, Any]] = {}

    def visit(node, parent_mode):
        for kid in node["children"]:
            if parent_mode in ("reuse", "skip"):
                out[kid["id"]] = {"mode": parent_mode, "chosen": False,
                                  "reason": "comes with its parent" if parent_mode == "reuse"
                                  else "skipped with its parent"}
            elif node["type"] == "DPSK" and kid["type"] == "IDENTITY_GROUP":
                out[kid["id"]] = {"mode": "copy", "chosen": False,
                                  "reason": "an identity group belongs to exactly one DPSK pool"}
            else:
                mode = requested.get(kid["id"])
                out[kid["id"]] = {"mode": mode if mode in MODES else "reuse", "chosen": True, "reason": None}
            visit(kid, out[kid["id"]]["mode"])

    visit(root, "copy")
    return out


def preview(r1, template_id: str, plan: Dict[str, Any], context: str = "MSP") -> Dict[str, Any]:
    """
    The ordered steps a copy would take, with the radio trim applied. `plan`:
      name, address{...}, excludeSettings[], modes{id: reuse|copy|skip},
      renames{id: name}
    The destination is this same MSP for now.
    """
    read = bundle(r1, template_id, context)
    if read["error"]:
        return {"steps": [], "warnings": [read["error"]], "radio": None, "modes": {}}
    root = read["root"]
    warnings: List[str] = []
    steps: List[Dict[str, Any]] = []
    renames = plan.get("renames") or {}
    modes = resolve_modes(root, plan.get("modes") or {})
    excluded_settings = set(plan.get("excludeSettings") or [])
    address = plan.get("address") or {}
    country = (address.get("countryCode") or "").upper()
    src_country = ((root["body"] or {}).get("address") or {}).get("countryCode")

    def mode(node):
        return "copy" if node is root else modes[node["id"]]["mode"]

    nodes = [(n, p) for n, p in _walk(root) if n is not root]
    copied = [(n, p) for n, p in nodes if mode(n) == "copy"]
    # What the new venue (or a copied parent) gets linked to: only templates
    # whose parent is being created. A reused template's own links already exist.
    linked = [(n, p) for n, p in nodes if mode(n) != "skip" and mode(p) == "copy"]

    existing = {(t["type"], t["name"]) for t in mt.list_templates(r1, context)["templates"]}

    def new_name(node):
        return (renames.get(node["id"]) or "").strip() or node["name"]

    def ref(node):
        """A path segment: {source id} for a new copy, the real id for a reused one."""
        if node is root:
            return "{venue}"
        return "{" + node["id"] + "}" if mode(node) == "copy" else node["id"]

    def label(node):
        if node is root:
            return f"VENUE {venue_name!r}"
        return f"{node['type']} {new_name(node) if mode(node) == 'copy' else node['name']!r}" \
               + (" (existing)" if mode(node) == "reuse" else "")

    venue_name = (plan.get("name") or "").strip() or root["name"]
    if ("VENUE", venue_name) in existing:
        warnings.append(f"A venue template named {venue_name!r} already exists in this MSP.")
    if not country:
        warnings.append("No country code chosen.")
    for node, _ in copied:
        if (node["type"], new_name(node)) in existing:
            warnings.append(f"{node['type']} {new_name(node)!r} already exists in this MSP -- rename the copy"
                            + (", or reuse the existing one." if modes[node["id"]]["chosen"] else "."))
        if node["error"]:
            warnings.append(f"{node['type']} {node['name']!r} could not be read fully, so it cannot be "
                            f"copied faithfully: {(node['error'] or {}).get('body')}")
        if node["type"] == "PORTAL":
            warnings.append(f"Copying portal {node['name']!r}: logo/background images are tenant-scoped "
                            "URLs and must be re-uploaded.")

    # 1. The venue template, with the new country. Nothing else can come first:
    #    country is fixed here and the channel table depends on it.
    steps.append({"op": "create", "type": "VENUE", "method": "POST",
                  "path": CREATE_PATHS["VENUE"], "name": venue_name,
                  "summary": f"Create venue template {venue_name!r} in {country or '??'}"
                             f" (source is {src_country})"})

    # 2. Radio trim against the target country.
    settings = root["settings"] or {}
    channels = None
    reference = None
    if country:
        found = reference_channels(r1, country, context)
        channels, reference = found.get("channels"), found.get("reference")
    radio_out = None
    if "apRadioSettings" in settings and "apRadioSettings" not in excluded_settings:
        if channels:
            _, report = trim_radio(settings["apRadioSettings"], channels)
            radio_out = {"reference": reference, "report": report, "deferred": False}
            for e in report:
                if e["emptied"]:
                    warnings.append(f"{e['band']}.{e['field']} has NO legal channels in {country} "
                                    f"at {e['bandwidth']} -- review before copying.")
        else:
            radio_out = {"reference": None, "report": [], "deferred": True}
            steps.append({"op": "read", "type": "VENUE", "method": "GET",
                          "path": "/templates/venues/{venue}/wifiAvailableChannels",
                          "summary": f"No venue template in {country or '??'} to borrow channels from; "
                                     "read them from the new venue template and trim then"})

    # 3. Venue settings.
    for name in mt.VENUE_SETTINGS:
        if name not in settings or name in excluded_settings:
            continue
        if name in NOT_COPIED_SETTINGS:
            warnings.append(f"{name}: {NOT_COPIED_SETTINGS[name]}")
            continue
        if isinstance(settings[name], dict) and "_error" in settings[name]:
            warnings.append(f"{name} could not be read and will not be copied.")
            continue
        steps.append({"op": "put", "type": "VENUE_SETTING", "method": "PUT",
                      "path": f"/templates/venues/{{venue}}/{name}",
                      "summary": f"Write {name}" + (" (channels trimmed)" if name == "apRadioSettings"
                                                     and radio_out and not radio_out["deferred"] else "")})

    # 4. New templates, leaves first: a DPSK pool is created through its
    #    identity group, and a network needs its portal or pool to attach.
    for node, _ in sorted(copied, key=lambda np: -_depth(root, np[0]["id"])):
        path = CREATE_PATHS.get(node["type"])
        if not path:
            warnings.append(f"No create path for {node['type']} {node['name']!r}; reuse it instead.")
            continue
        if node["type"] == "DPSK":
            group = next((k for k in node["children"] if k["type"] == "IDENTITY_GROUP"), None)
            path = path.replace("{identityGroup}", ref(group) if group else "{identityGroup?}")
            if not group:
                warnings.append(f"DPSK {node['name']!r} is created through an identity group, and has none.")
        renamed = new_name(node) != node["name"]
        steps.append({"op": "create", "type": node["type"], "method": "POST", "path": path,
                      "name": new_name(node), "unverified": node["type"] in UNVERIFIED_CREATES,
                      "summary": f"Create {node['type']} {new_name(node)!r}"
                                 + (f" (copy of {node['name']!r})" if renamed else " (copy, same name)")})

    # 5. Link templates to their new parents, deepest first. A DPSK pool's
    #    identity group is linked by creating the pool through it (step 4).
    for node, parent in sorted(linked, key=lambda np: -_depth(root, np[0]["id"])):
        attach = ATTACH_PATHS.get((parent["type"], node["type"]))
        if not attach:
            continue
        steps.append({"op": "attach", "type": node["type"], "method": "PUT", "reused": mode(node) == "reuse",
                      "path": attach.format(parent=ref(parent), child=ref(node)),
                      "summary": f"Attach {label(node)} to {label(parent)}"})
        if parent is root and node["type"] == "NETWORK" and node["venueNetworkSettings"]:
            steps.append({"op": "put", "type": "VENUE_NETWORK_SETTING", "method": "PUT",
                          "path": f"/templates/venues/{{venue}}/wifiNetworks/{ref(node)}/settings",
                          "summary": f"Write {label(node)} activation on the venue (radio types, VLAN, schedule)"})

    # 6. Country checks on what the venue activates, reused or not.
    for node, parent in linked:
        if parent is not root or node["type"] != "NETWORK":
            continue
        radio_types = (node["venueNetworkSettings"] or {}).get("allApGroupsRadioTypes") or []
        if any("6" in str(t) for t in radio_types):
            if channels is None:
                warnings.append(f"{node['name']!r} is activated on 6 GHz; whether {country or 'the target country'} "
                                "allows 6 GHz is only known once the new venue template exists.")
            elif not (channels.get("6GChannels") or {}):
                warnings.append(f"{node['name']!r} is activated on 6 GHz, which {country} does not offer.")

    return {"steps": steps, "warnings": warnings, "radio": radio_out, "modes": modes}


def _depth(root: Dict[str, Any], node_id: str, depth: int = 0) -> int:
    if root["id"] == node_id:
        return depth
    for kid in root["children"]:
        d = _depth(kid, node_id, depth + 1)
        if d >= 0:
            return d
    return -1
