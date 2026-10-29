"""
Read side of the Template Tool: RUCKUS ONE MSP configuration templates.

READ-ONLY. Nothing here creates, applies, syncs or enforces a template. All
functions are sync (the R1 client is `requests`); call them through
asyncio.to_thread from async routes.

What a "template" is, as verified against a live MSP (2026-10-08):

* `POST /templates/query` lists every template the MSP owns, of every type,
  with `appliedOnTenants` (EC ids), `driftStatus` and `isEnforced`. The page is
  1-indexed. `templateContext` comes back null here even though the same rows
  say "MSP" when read through the dependency query.
* `POST /templates/{id}/dependencies/query` returns DIRECT children only. A
  venue template's children are its networks and switch CLI profile; a
  network's are its portal or DPSK pool. This tree is the closest thing R1 has
  to a template "bundle"; there is no bundle object anywhere in the API.
* `/rec/templates/...` is a parallel namespace for REC templates. The generic
  endpoints document the prefix; the per-type readers accept it too (the
  wifi-network one answers WIFI-10002 not RCG-10016 -- the route exists).
* Each template type has its own body reader, and the spec is incomplete:
  `GET /templates/identityGroups/{id}` works but is undocumented, and a
  SWITCH_CLI template is NOT a /cliTemplates row -- it is a switch profile
  (`/templates/switchProfiles/{id}`, profileType CLI, CLI text inline).
* A venue template's own body is just name + address. Its real settings live
  in ~20 `/templates/venues/{id}/<setting>` sub-resources, read in a fan-out.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTEXTS = ("MSP", "REC")
PAGE_SIZE = 500

# Template type (as /templates/query reports it) -> body reader path.
# Only types seen on a live tenant are mapped; an unknown type is shown in the
# list but its body is reported as unreadable rather than guessed at.
BODY_PATHS = {
    "VENUE": "/templates/venues/{id}",
    "NETWORK": "/templates/wifiNetworks/{id}",
    "DPSK": "/templates/dpskServices/{id}",
    "PORTAL": "/templates/portalServiceProfiles/{id}",
    "IDENTITY_GROUP": "/templates/identityGroups/{id}",  # undocumented, works
    "SWITCH_CLI": "/templates/switchProfiles/{id}",     # not /cliTemplates
}

# Venue-template settings that are a plain GET with no extra parameters.
VENUE_SETTINGS = (
    "aaaSettings",
    "apBssColoringSettings",
    "apCellularSettings",
    "apClientAdmissionControlSettings",
    "apDirectedMulticastSettings",
    "apDosProtectionSettings",
    "apLoadBalancingSettings",
    "apMeshSettings",
    "apModelBandModeSettings",
    "apModelExternalAntennaSettings",
    "apModelLanPortSettings",
    "apModelLedSettings",
    "apMulticastDnsFencingSettings",
    "apRadioSettings",
    "apRadiusOptions",
    "apRebootTimeoutSettings",
    "apSmartMonitorSettings",
    "roguePolicySettings",
    "switchProfiles",
    "switchSettings",
)
VENUE_FANOUT = 6


def _prefix(context: str) -> str:
    return "/rec" if context == "REC" else ""


def _body(response) -> Any:
    try:
        return response.json()
    except ValueError:
        return (response.text or "").strip()[:500]


def _error(response, what: str) -> Dict[str, Any]:
    return {"status": response.status_code, "what": what, "body": _body(response)}


def list_templates(r1, context: str = "MSP") -> Dict[str, Any]:
    """Every template in one context, paged to the end."""
    rows: List[Dict[str, Any]] = []
    page = 1
    while True:
        response = r1.post(f"{_prefix(context)}/templates/query",
                           payload={"page": page, "pageSize": PAGE_SIZE})
        if not response.ok:
            return {"templates": rows, "error": _error(response, f"{context} template list")}
        body = response.json() or {}
        batch = body.get("data") or []
        rows.extend(batch)
        total = body.get("totalCount") or 0
        if not batch or len(rows) >= total:
            break
        page += 1
    for row in rows:
        row["context"] = context
    return {"templates": rows, "error": None}


def children(r1, template_id: str, context: str = "MSP") -> Dict[str, Any]:
    response = r1.post(f"{_prefix(context)}/templates/{template_id}/dependencies/query",
                       payload={"page": 1, "pageSize": PAGE_SIZE})
    if not response.ok:
        return {"children": [], "error": _error(response, "template dependencies")}
    return {"children": (response.json() or {}).get("data") or [], "error": None}


def body(r1, template_id: str, template_type: str, context: str = "MSP") -> Dict[str, Any]:
    """The template's full configuration, as its type's reader returns it."""
    path_fmt = BODY_PATHS.get(template_type)
    if not path_fmt:
        return {"body": None, "settings": None,
                "error": {"status": None, "what": "template body",
                          "body": f"No reader mapped for template type {template_type!r} yet."}}
    base = _prefix(context) + path_fmt.format(id=template_id)
    response = r1.get(base)
    if not response.ok:
        return {"body": None, "settings": None, "error": _error(response, "template body")}
    result = {"body": response.json(), "settings": None, "error": None}

    if template_type == "VENUE":
        def read(setting: str):
            r = r1.get(f"{base}/{setting}")
            return setting, (_body(r) if r.ok else {"_error": _error(r, setting)})

        with ThreadPoolExecutor(max_workers=VENUE_FANOUT) as pool:
            result["settings"] = dict(pool.map(read, VENUE_SETTINGS))
    return result


def drift(r1, template_id: str, tenant_id: str, context: str = "MSP") -> Dict[str, Any]:
    """Template-vs-instance differences on one EC the template was applied to."""
    response = r1.get(f"{_prefix(context)}/templates/{template_id}/tenants/{tenant_id}/diffReports")
    if not response.ok:
        return {"diffs": [], "error": _error(response, "drift report")}
    return {"diffs": response.json() or [], "error": None}


def ec_names(r1) -> Dict[str, str]:
    """EC id -> name, so appliedOnTenants can be read by a human."""
    response = r1.post("/mspecs/query", payload={
        "fields": ["id", "name"], "sortField": "name", "sortOrder": "ASC",
    })
    if not response.ok:
        logger.warning(f"EC name lookup failed: {response.status_code}")
        return {}
    return {row["id"]: row.get("name") or row["id"]
            for row in (response.json() or {}).get("data") or [] if row.get("id")}
