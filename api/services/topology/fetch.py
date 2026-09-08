"""
Every R1 read the Topology tool makes.

READ-ONLY BY CONSTRUCTION. GET and `*/query` POST only. Nothing here creates,
updates, deletes, reboots, syncs or triggers. In particular the PATCH that warms
an AP's LLDP neighbour cache is deliberately absent: a cold cache is a finding
this tool reports, not a state it changes.

Field lists and quirks below are MEASURED, not read off the spec -- see
`plans/topology.md` §1 for the probe run they come from. The spec documents the
surface; it does not say what a tenant fills in.

The three heavy ES crawls (switches, ports, MAC table) delegate to
`r1.switches`, which owns the only correct implementation of R1's paging in this
repo: 10000-row window, `page:1` aliasing `page:0`, and an explicit `sortField`
without which pages return overlapping random subsets. Do not reimplement it.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The AP field names that actually work on a live tenant. The spec's own names
# (apMac, deviceStatus, fwVersion) come back empty -- see services/pisr/fetch.py,
# which established this list. `IP`/`extIp` are empty too; addressing lives in
# nested `networkStatus`.
AP_FIELDS = [
    "name", "status", "model", "serialNumber", "macAddress",
    "firmwareVersion", "venueId", "venueName", "apGroupId", "apGroupName",
    "clientCount", "meshRole", "switchSerialNumber", "poePort", "poePortStatus",
    "lastSeenTime", "uptime", "networkStatus", "lanPortStatuses",
    "floorplanId", "xPercent", "yPercent", "tags",
]


def _tenant(r1, tenant_id: Optional[str]) -> Optional[str]:
    """
    Only MSP controllers take the tenant-override header.

    Sending `x-rks-tenantid` to a direct-tenant controller is at best redundant
    and at worst wrong, so mirror PISR's rule rather than always passing it.
    """
    return tenant_id if getattr(r1, "ec_type", None) == "MSP" and tenant_id else None


def _get(r1, path: str, tenant_id: Optional[str], params: Optional[Dict] = None):
    return r1.get(path, params=params, override_tenant_id=_tenant(r1, tenant_id))


def _post(r1, path: str, tenant_id: Optional[str], payload: Any = None):
    return r1.post(path, payload=payload, override_tenant_id=_tenant(r1, tenant_id))


def _rows(response) -> List[Dict[str, Any]]:
    """
    Unwrap the three envelopes R1 uses: ES (`data`), Spring Page (`content`),
    and a bare list. Never raises -- a failed read is an empty section with a
    logged reason, not a dead discovery run.
    """
    if response is None or not getattr(response, "ok", False):
        return []
    try:
        body = response.json()
    except Exception:                                               # noqa: BLE001
        return []
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("data", "content", "list"):
        value = body.get(key)
        if isinstance(value, list):
            return value
    return []


# ── inventory (delegated to the hardened crawler) ────────────────────────────

def switches(r1, tenant_id: Optional[str], venue_ids: Optional[List[str]] = None):
    """Switch inventory. `id` IS the switch MAC."""
    return _scoped(r1.switches.list_switches(tenant_id) or [], venue_ids)


def _scoped(rows: List[Dict], venue_ids: Optional[List[str]]) -> List[Dict]:
    if not venue_ids:
        return rows
    wanted = set(venue_ids)
    return [row for row in rows if row.get("venueId") in wanted]


def switches_tenant_wide(r1, tenant_id: Optional[str]):
    """
    Every switch in the tenant, unscoped.

    Costs nothing extra -- `list_switches` is tenant-wide and the scoped read
    just filters it -- and it answers a question the scoped list cannot: when a
    port's LLDP neighbour is not in this venue, is it an unmanaged device, or a
    managed switch in a venue the user did not select?

    Those two look identical in a scoped map and mean opposite things. One is a
    firewall worth investigating; the other is "add that venue to see the rest
    of your network", which on the probe tenant was 8 of the 8 external nodes.
    """
    return r1.switches.list_switches(tenant_id) or []


def switch_ports(r1, tenant_id: Optional[str], venue_ids: List[str]):
    """
    Port rows -- LLDP neighbours, VLANs, counters, PoE, STP and LAG all ride here.

    `portMac` and `switchModel` were added to the shared PORT_FIELDS after
    probing showed both 100% populated; this tool needs `portMac` to resolve an
    LLDP portId to a specific far port.
    """
    return r1.switches.crawl_ports(tenant_id, list(venue_ids)) or []


def mac_table(r1, tenant_id: Optional[str], venue_ids: List[str]):
    """
    The switch forwarding table, despite the endpoint being named `clients`.

    NOTE `isRuckusAP` is NOT requested: probing showed R1 silently drops the
    field -- asking does not error, the key simply never appears. APs are
    identified by joining clientMac against AP inventory instead, which
    resolved 2757 of 2788 APs on the probe venue.
    """
    return r1.switches.crawl_mac_table(tenant_id, venue_ids) or []


def access_points(r1, tenant_id: Optional[str], venue_ids: List[str]):
    """
    AP inventory, fanned out per venue.

    The tenant-wide form of /venues/aps/query silently caps at ~1000 rows and
    ignores both `page` and `search_after`, so the per-venue fan-out in
    r1.venues is the only complete read.
    """
    try:
        return r1.venues.query_all_aps_by_tenant(tenant_id, list(venue_ids), AP_FIELDS) or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("topology: AP query failed: %s", exc)
        return []


# ── the topology-specific reads ──────────────────────────────────────────────

def r1_topology(r1, tenant_id: Optional[str], venue_id: str) -> Dict[str, Any]:
    """
    R1's own wired topology graph. The single best source we have.

    Measured on a 112-switch venue: 200 in ~2.9s, 1106 nodes / 1093 edges, with
    from/to MAC+name+serial, connectedPort, connectedPortTaggedVlan, linkSpeed,
    connectionType and connectionStatus all 100% populated.

    Two things worth knowing before using the result:
      * `data` holds exactly ONE entry carrying nodes[]+edges[], not one entry
        per connected component;
      * `correspondingPort` is populated on only ~9% of edges, and that is not a
        gap -- it is exactly the switch-to-switch subset. An AP has a single
        uplink, so only the switch end of an AP edge has a meaningful port.

    `meshOnly` is documented as required but is optional in practice; it is sent
    explicitly anyway so behaviour does not depend on that staying true.
    """
    response = _get(r1, f"/venues/{venue_id}/topologies", tenant_id,
                    params={"meshOnly": "false"})
    entries = _rows(response)
    nodes, edges = [], []
    for entry in entries:
        if isinstance(entry, dict):
            nodes.extend(entry.get("nodes") or [])
            edges.extend(entry.get("edges") or [])
    if not entries and response is not None and not getattr(response, "ok", True):
        logger.warning("topology: /topologies %s -> %s", venue_id,
                       getattr(response, "status_code", "?"))
    return {"nodes": nodes, "edges": edges, "entries": len(entries)}


def mesh_topology(r1, tenant_id: Optional[str], venue_id: str) -> Dict[str, Any]:
    """Mesh links only. Empty on wired venues -- that is a property of the site."""
    response = _get(r1, f"/venues/{venue_id}/meshTopologies", tenant_id)
    nodes, edges = [], []
    for entry in _rows(response):
        if isinstance(entry, dict):
            nodes.extend(entry.get("nodes") or [])
            edges.extend(entry.get("edges") or [])
    return {"nodes": nodes, "edges": edges}


def stack_members(r1, tenant_id: Optional[str], venue_ids: List[str]):
    """
    Stack composition: {activeSerial, members[]} with per-unit MAC, id and model.

    R1 models a stack as ONE switch row, so these units become sub-entities
    rather than devices.
    """
    out: List[Dict[str, Any]] = []
    for venue_id in venue_ids:
        response = _post(r1, "/venues/switches/members/query", tenant_id,
                         {"filters": {"venueId": [venue_id]},
                          "page": 0, "pageSize": 1000,
                          "sortField": "activeSerial", "sortOrder": "ASC"})
        out.extend(_rows(response))
    return out


def ve_ports(r1, tenant_id: Optional[str], venue_id: str, switch_id: str):
    """
    SVIs: VLAN -> subnet. 100% populated on veId/vlanId/ipAddress/ipSubnetMask.

    `switchId`/`switchName` come back empty, which is harmless -- the caller
    knows which switch it asked about.
    """
    return _rows(_get(r1, f"/venues/{venue_id}/switches/{switch_id}/vePorts", tenant_id))


def static_routes(r1, tenant_id: Optional[str], venue_id: str, switch_id: str):
    """
    Static routes. A `0.0.0.0/0` entry is the strongest WAN-uplink signal there
    is -- configuration rather than inference. 100% populated where present.
    """
    return _rows(_get(r1, f"/venues/{venue_id}/switches/{switch_id}/staticRoutes", tenant_id))


def vlan_names(r1, tenant_id: Optional[str], venue_id: str, switch_ids: List[str]):
    """
    VLAN ids and names per switch.

    The request body is an ARRAY OF SWITCH IDS, not a query DTO -- a DTO returns
    400 SWITCH-10000, and an empty array returns zero rows.

    Only `vlanId` and `vlanName` are populated. `taggedPorts`, `untaggedPorts`,
    `isAuthVlan` and `usedByVePort` measured 0%, so this is NOT the VLAN-to-port
    map it looks like: that has to come from port rows (`unTaggedVlan`,
    `vlanIds`) and from /topologies edge VLAN fields.
    """
    if not switch_ids:
        return []
    return _rows(_post(r1, f"/venues/{venue_id}/vlans/query", tenant_id, list(switch_ids)))


def ap_lldp_neighbors(r1, tenant_id: Optional[str], venue_id: str, serial: str):
    """
    One AP's LLDP neighbours. Best-effort by design.

    Measured: 11 of 12 APs return HTTP 400 `WIFI-10498 "No detected neighbor
    data."` -- a cold cache, not a bad request -- and the one that answered was
    18 days stale. Warming it requires a PATCH, which is a write and therefore
    out of scope. At ~99ms per call, a 2788-AP venue would spend ~276s of a 300s
    budget for an ~8% hit rate, which is why this is only ever called behind the
    opt-in `deep` flag.

    Returns [] for the 400 case; a cold cache is not an error worth raising.

    When it does answer, the data is the best available: `lldpChassisID` and
    `lldpPortID` carry a literal "mac " prefix (strip it), and `lldpPortDesc`
    ("GigabitEthernet1/1/12") is the only AP-side field naming the far port.
    """
    response = _post(r1, f"/venues/{venue_id}/aps/{serial}/neighbors/query", tenant_id,
                     {"filters": [{"type": "LLDP_NEIGHBOR"}], "page": 0, "pageSize": 100})
    if response is None or not getattr(response, "ok", False):
        return []
    try:
        body = response.json()
    except Exception:                                               # noqa: BLE001
        return []
    rows = body.get("neighbors") if isinstance(body, dict) else None
    return rows if isinstance(rows, list) else _rows(response)


def venues(r1, tenant_id: Optional[str]):
    """Venue list for the picker, with the counts that make it useful."""
    from services.pisr.fetch import VENUE_FIELDS

    response = _post(r1, "/venues/query", tenant_id,
                     {"fields": list(VENUE_FIELDS), "page": 0, "pageSize": 1000,
                      "sortField": "name", "sortOrder": "ASC"})
    rows = _rows(response)
    if rows:
        return rows
    return _rows(_get(r1, "/venues", tenant_id))
