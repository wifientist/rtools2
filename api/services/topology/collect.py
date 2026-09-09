"""
One discovery run: fan out the reads, then shape them into Devices and Ports.

Synchronous by design. The R1 client is `requests`-based, so every read runs in
a thread and they all run at once -- the PISR pattern. There is no job, no Redis
state and no SSE: discovery is a single human-triggered POST that returns the
snapshot it built. nginx allows 300s on /api, which the measured fan-out fits
inside comfortably as long as the per-AP LLDP scan stays behind `deep`.

A failed source is recorded and survived, never raised. A topology missing its
MAC table is still a useful topology; one that 500s because a single venue's
vePorts read timed out is not.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import fetch
from .model import Device, FloorplanRef, Port, Snapshot, StackInfo
from .normalize import (canon_port_ident, norm_mac, parse_speed_mbps,
                        parse_vlan_list, split_r1_port_id)

logger = logging.getLogger(__name__)

# Per-AP LLDP is one HTTP call per AP against a shared 300s budget. Bounded so a
# deep scan on a 2788-AP venue cannot monopolise the pool, and capped so it
# cannot silently blow the request budget either.
AP_LLDP_CONCURRENCY = 10
AP_LLDP_MAX_APS = 400

# Per-switch reads (vePorts, staticRoutes) are cheap but N-shaped.
SWITCH_FANOUT_CONCURRENCY = 8


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


# AP statuses are coded `<family>_<sub>_<Word>`. The leading digit is the family
# and is the reliable part: 1 = not yet up, 2 = up, 3 = lost. Measured on a live
# venue: 2_00_Operational (2529), 2_02_ApplyingConfiguration (143),
# 3_04_DisconnectedFromCloud (99), 1_01_NeverContactedCloud (10),
# 1_07_Initializing (7). Matching only on words would have filed the 143 APs
# mid-config as "unknown" when they are plainly up.
_AP_STATUS_FAMILY = {"1": "pending", "2": "online", "3": "offline"}


def _online(raw_status: Any) -> str:
    """
    Normalise R1's two status vocabularies: coded strings on APs
    (`2_00_Operational`), plain words on switches (`ONLINE`).
    """
    text = str(raw_status or "").strip()
    if not text:
        return "unknown"
    if len(text) > 1 and text[0].isdigit() and text[1] == "_":
        return _AP_STATUS_FAMILY.get(text[0], "unknown")
    lowered = text.lower()
    if "operational" in lowered or lowered in ("online", "up", "good", "active"):
        return "online"
    if "disconnect" in lowered or "offline" in lowered or "down" in lowered:
        return "offline"
    if "nevercontacted" in lowered or "provision" in lowered or "initializ" in lowered:
        return "pending"
    if "degrad" in lowered or "warn" in lowered:
        return "degraded"
    return "unknown"


class RawBundle:
    """Everything one run read, plus how each read went."""

    def __init__(self) -> None:
        self.switches: List[Dict] = []
        self.ports: List[Dict] = []
        self.macs: List[Dict] = []
        self.aps: List[Dict] = []
        # Tenant-wide switch roster, used only to tell an unmanaged neighbour
        # apart from a managed switch in a venue the user did not select.
        self.all_switches: List[Dict] = []
        self.r1_nodes: List[Dict] = []
        self.r1_edges: List[Dict] = []
        self.mesh_edges: List[Dict] = []
        self.stacks: List[Dict] = []
        self.ve_ports: Dict[str, List[Dict]] = {}
        self.static_routes: Dict[str, List[Dict]] = {}
        self.ap_lldp: Dict[str, List[Dict]] = {}
        self.sources: List[Dict[str, Any]] = []
        self.warnings: List[str] = []

    def record(self, source_id: str, started: float, rows: int,
               error: Optional[str] = None, note: str = "") -> None:
        entry = {"id": source_id, "status": "error" if error else "ok",
                 "rows": rows, "elapsedMs": int((time.time() - started) * 1000)}
        if error:
            entry["error"] = error[:300]
        if note:
            entry["note"] = note
        self.sources.append(entry)


async def _gather(named_calls: Dict[str, tuple], bundle: RawBundle) -> Dict[str, Any]:
    """Run every read concurrently; a raiser becomes a recorded error."""
    keys = list(named_calls)
    started = {key: time.time() for key in keys}
    results = await asyncio.gather(
        *(asyncio.to_thread(named_calls[key][0], *named_calls[key][1]) for key in keys),
        return_exceptions=True,
    )
    out: Dict[str, Any] = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.warning("topology: %s read failed: %s", key, result)
            bundle.record(key, started[key], 0, f"{type(result).__name__}: {result}")
            out[key] = None
        else:
            bundle.record(key, started[key], _row_count(result))
            out[key] = result
    return out


def _row_count(result: Any) -> int:
    """
    How many rows a read actually returned.

    A graph read hands back {"nodes": [...], "edges": [...]}; len() of that is 2,
    which would report a 1100-node topology as "2 rows" in the source table and
    make a healthy read look broken.
    """
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return sum(len(value) for value in result.values() if isinstance(value, list))
    return 0


async def _bounded(items, worker, limit: int):
    """Map `worker` over `items` with at most `limit` in flight."""
    semaphore = asyncio.Semaphore(limit)

    async def run(item):
        async with semaphore:
            return item, await asyncio.to_thread(worker, item)

    return await asyncio.gather(*(run(item) for item in items), return_exceptions=True)


async def collect(r1, tenant_id: Optional[str], venue_ids: List[str],
                  deep: bool = False) -> RawBundle:
    """Every read for one venue set, concurrently."""
    bundle = RawBundle()
    venue_ids = sorted({v for v in venue_ids if v})
    r1.switches.reset_completeness()

    core = await _gather({
        "switches": (fetch.switches, (r1, tenant_id, venue_ids)),
        "ports": (fetch.switch_ports, (r1, tenant_id, venue_ids)),
        "macs": (fetch.mac_table, (r1, tenant_id, venue_ids)),
        "aps": (fetch.access_points, (r1, tenant_id, venue_ids)),
        "stacks": (fetch.stack_members, (r1, tenant_id, venue_ids)),
        "allSwitches": (fetch.switches_tenant_wide, (r1, tenant_id)),
    }, bundle)
    bundle.switches = core.get("switches") or []
    bundle.ports = core.get("ports") or []
    bundle.macs = core.get("macs") or []
    bundle.aps = core.get("aps") or []
    bundle.stacks = core.get("stacks") or []
    bundle.all_switches = core.get("allSwitches") or []

    # /topologies and /meshTopologies are per-venue, so fan out across the scope.
    topo_calls: Dict[str, tuple] = {}
    for venue_id in venue_ids:
        topo_calls[f"r1topology:{venue_id}"] = (fetch.r1_topology, (r1, tenant_id, venue_id))
        topo_calls[f"mesh:{venue_id}"] = (fetch.mesh_topology, (r1, tenant_id, venue_id))
    topo = await _gather(topo_calls, bundle)
    for key, value in topo.items():
        if not value:
            continue
        if key.startswith("r1topology:"):
            bundle.r1_nodes.extend(value.get("nodes") or [])
            bundle.r1_edges.extend(value.get("edges") or [])
        else:
            bundle.mesh_edges.extend(value.get("edges") or [])

    # Per-switch L3 reads. Cheap individually, N-shaped in aggregate, and only
    # needed for WAN inference -- so they ride the deep flag on large scopes.
    switch_refs = [(s.get("venueId"), s.get("id")) for s in bundle.switches
                   if s.get("venueId") and s.get("id")]
    if switch_refs and (deep or len(switch_refs) <= 60):
        started = time.time()
        routes = await _bounded(
            switch_refs,
            lambda ref: fetch.static_routes(r1, tenant_id, ref[0], ref[1]),
            SWITCH_FANOUT_CONCURRENCY)
        ves = await _bounded(
            switch_refs,
            lambda ref: fetch.ve_ports(r1, tenant_id, ref[0], ref[1]),
            SWITCH_FANOUT_CONCURRENCY)
        for outcome in routes:
            if isinstance(outcome, Exception):
                continue
            ref, rows = outcome
            if rows:
                bundle.static_routes[ref[1]] = rows
        for outcome in ves:
            if isinstance(outcome, Exception):
                continue
            ref, rows = outcome
            if rows:
                bundle.ve_ports[ref[1]] = rows
        bundle.record("l3", started,
                      sum(len(v) for v in bundle.static_routes.values()) +
                      sum(len(v) for v in bundle.ve_ports.values()))
    elif switch_refs:
        bundle.warnings.append(
            f"Skipped per-switch routes/SVIs for {len(switch_refs)} switches "
            "(re-run with deep=true). WAN inference will fall back to LLDP and "
            "cloud-port signals.")

    # Per-AP LLDP: measured ~8% hit rate on 18-day-stale data at ~99ms a call.
    # Opt-in only, bounded, and capped -- see fetch.ap_lldp_neighbors.
    if deep and bundle.aps:
        started = time.time()
        candidates = [ap for ap in bundle.aps if ap.get("serialNumber")][:AP_LLDP_MAX_APS]
        outcomes = await _bounded(
            candidates,
            lambda ap: fetch.ap_lldp_neighbors(r1, tenant_id, ap.get("venueId"),
                                               ap.get("serialNumber")),
            AP_LLDP_CONCURRENCY)
        hits = 0
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                continue
            ap, rows = outcome
            if rows:
                bundle.ap_lldp[ap["serialNumber"]] = rows
                hits += 1
        bundle.record("apLldp", started, hits,
                      note=f"{hits}/{len(candidates)} APs had a warm neighbour cache")
        if len(bundle.aps) > AP_LLDP_MAX_APS:
            bundle.warnings.append(
                f"Deep AP scan capped at {AP_LLDP_MAX_APS} of {len(bundle.aps)} APs "
                "to stay inside the request budget.")

    return bundle


# ── shaping ─────────────────────────────────────────────────────────────────

def _switch_device(row: Dict, stacks_by_serial: Dict[str, Dict]) -> Device:
    mac = norm_mac(row.get("switchMac") or row.get("id"))
    serial = str(row.get("serialNumber") or "")
    is_stack = bool(row.get("isStack"))
    device = Device(
        id=f"sw:{mac}" if mac else f"sw:serial:{serial}",
        kind="stack" if is_stack else "switch",
        name=str(row.get("name") or ""),
        venue_id=str(row.get("venueId") or ""),
        venue_name=str(row.get("venueName") or ""),
        mac=mac, serial=serial,
        model=str(row.get("model") or ""),
        ip=str(row.get("ipAddress") or ""),
        raw_status=str(row.get("deviceStatus") or ""),
        status=_online(row.get("deviceStatus")),
        role="access",
        attrs={
            "firmwareVersion": row.get("firmwareVersion"),
            "uptime": row.get("uptime"),
            "numOfPorts": row.get("numOfPorts"),
            "clientCount": row.get("clientCount"),
            "defaultGateway": row.get("defaultGateway"),
            "subnetMask": row.get("subnetMask"),
            "poeTotal": row.get("poeTotal"),
            "poeFree": row.get("poeFree"),
            # poeUtilization is ALLOCATED MILLIWATTS, not a percentage.
            "poeAllocatedMilliwatts": row.get("poeUtilization"),
            "cloudPort": row.get("cloudPort"),
            "veCount": row.get("veCount"),
            "tags": row.get("tags"),
        },
    )
    if row.get("floorplanId"):
        device.floorplan = FloorplanRef(str(row["floorplanId"]),
                                        row.get("xPercent"), row.get("yPercent"))
    stack = stacks_by_serial.get(serial) or stacks_by_serial.get(str(row.get("activeSerial") or ""))
    if stack:
        device.stack = StackInfo(active_serial=str(stack.get("activeSerial") or ""),
                                 units=stack.get("members") or [])
        device.kind = "stack"
    return device


def _ap_device(row: Dict) -> Device:
    serial = str(row.get("serialNumber") or "")
    network = row.get("networkStatus") or {}
    if not isinstance(network, dict):
        network = {}
    device = Device(
        id=f"ap:{serial}",
        kind="ap",
        name=str(row.get("name") or ""),
        venue_id=str(row.get("venueId") or ""),
        venue_name=str(row.get("venueName") or ""),
        mac=norm_mac(row.get("macAddress")),
        serial=serial,
        model=str(row.get("model") or ""),
        # `IP`/`extIp` are on the schema but come back empty; networkStatus has
        # the real addressing.
        ip=str(network.get("ipAddress") or ""),
        raw_status=str(row.get("status") or ""),
        status=_online(row.get("status")),
        role="ap",
        attrs={
            "firmwareVersion": row.get("firmwareVersion"),
            "apGroupName": row.get("apGroupName"),
            "clientCount": row.get("clientCount"),
            "meshRole": row.get("meshRole"),
            "uptime": row.get("uptime"),
            "lastSeenTime": row.get("lastSeenTime"),
            "gateway": network.get("gateway"),
            "managementVlan": network.get("managementTrafficVlan"),
            # The AP's own view of its uplink: 99.9% of these resolve to a
            # managed switch, which makes it a first-class evidence source.
            "switchSerialNumber": row.get("switchSerialNumber"),
            "poePort": row.get("poePort"),
            "poePortStatus": row.get("poePortStatus"),
            "tags": row.get("tags"),
        },
    )
    if row.get("floorplanId"):
        device.floorplan = FloorplanRef(str(row["floorplanId"]),
                                        row.get("xPercent"), row.get("yPercent"))
    return device


def _port(row: Dict, device_id: str) -> Optional[Port]:
    ident = canon_port_ident(row.get("portIdentifier") or row.get("name"))
    if not ident:
        _, ident = split_r1_port_id(row.get("id"))
    if not ident:
        return None
    lag_id = row.get("lagId")
    status = str(row.get("status") or "").lower()
    admin = str(row.get("adminStatus") or "").lower()
    return Port(
        id=f"{device_id}#{ident}",
        device_id=device_id,
        ident=ident,
        label=str(row.get("portIdentifierFormatted") or row.get("name") or ident),
        r1_port_id=str(row.get("id") or ""),
        port_mac=norm_mac(row.get("portMac")),
        unit=int(row.get("switchUnitId")) if str(row.get("switchUnitId") or "").isdigit() else None,
        kind="ethernet",
        admin_up=None if not admin else admin not in ("down", "disabled", "admindown"),
        oper_up=None if not status else status in ("up", "connected", "active"),
        speed_mbps=parse_speed_mbps(row.get("portSpeed")),
        untagged_vlan=(parse_vlan_list(row.get("unTaggedVlan")) or [None])[0],
        tagged_vlans=parse_vlan_list(row.get("vlanIds")),
        lag_id=str(lag_id) if lag_id not in (None, "", 0, "0") else None,
        lag_key=f"{device_id}#lag:{lag_id}" if lag_id not in (None, "", 0, "0") else None,
        stack_capable=bool(row.get("usedInFormingStack")),
        stack_peer_ident=canon_port_ident(row.get("stackingNeighborPort")),
        is_cloud_port=bool(row.get("cloudPort")),
        stp_state=str(row.get("spanningTreeStatus") or ""),
        neighbor_name=str(row.get("neighborName") or ""),
        neighbor_mac=norm_mac(row.get("neighborMacAddress")),
        neighbor_port_mac=norm_mac(row.get("neighborPortMacAddress")),
        poe={"enabled": row.get("poeEnabled"), "used": row.get("poeUsed"),
             "total": row.get("poeTotal"), "usage": row.get("poeUsage"),
             "type": row.get("poeType")},
        attrs={"mediaType": row.get("mediaType"), "opticsType": row.get("opticsType"),
               "errorDisable": row.get("errorDisableStatus"),
               "profile": row.get("switchPortProfileName"),
               "stackingNeighborPort": row.get("stackingNeighborPort"),
               "lagName": row.get("lagName"), "lagStatus": row.get("lagStatus")},
    )


def shape(bundle: RawBundle, tenant_id: str, venue_ids: List[str],
          elapsed: float, deep: bool) -> Snapshot:
    """RawBundle -> Snapshot of Devices and Ports. Links come later (Phase 2)."""
    now = time.time()
    stacks_by_serial = {str(s.get("activeSerial") or ""): s for s in bundle.stacks
                        if s.get("activeSerial")}

    devices: List[Device] = []
    by_switch_mac: Dict[str, Device] = {}
    for row in bundle.switches:
        device = _switch_device(row, stacks_by_serial)
        devices.append(device)
        if device.mac:
            by_switch_mac[device.mac] = device
    # The AP query returns venueId but never venueName, while the switch query
    # returns both. Backfill from the switches so an AP's venue is not blank
    # everywhere it is shown -- the inspector, the findings lists and the venue
    # grouping all read it.
    venue_names = {str(r["venueId"]): str(r["venueName"])
                   for r in bundle.switches
                   if r.get("venueId") and r.get("venueName")}
    for row in bundle.aps:
        device = _ap_device(row)
        if not device.venue_name and device.venue_id:
            device.venue_name = venue_names.get(device.venue_id, "")
        devices.append(device)

    ports: List[Port] = []
    for row in bundle.ports:
        mac = norm_mac(row.get("switchMac"))
        device = by_switch_mac.get(mac)
        if device is None:
            continue                        # a port whose switch is out of scope
        port = _port(row, device.id)
        if port is None:
            continue
        ports.append(port)
        device.port_ids.append(port.id)

    # Learned-MAC counts per port. A port with exactly one MAC is an edge port;
    # a port with many leads toward more network. Both are evidence later.
    ports_by_r1_id = {p.r1_port_id: p for p in ports if p.r1_port_id}
    for row in bundle.macs:
        port = ports_by_r1_id.get(str(row.get("switchPortId") or ""))
        if port is not None:
            port.mac_count += 1

    for device in devices:
        if device.kind in ("switch", "stack"):
            owned = [p for p in ports if p.device_id == device.id]
            device.counts = {
                "ports": len(owned),
                "upPorts": sum(1 for p in owned if p.oper_up),
                "macs": sum(p.mac_count for p in owned),
            }

    venues = dict(venue_names)
    for row in bundle.aps:
        if row.get("venueId") and row.get("venueName"):
            venues[str(row["venueId"])] = str(row["venueName"])

    return Snapshot(
        taken_at=_iso(now), taken_at_epoch=now,
        tenant_id=tenant_id, scope_venue_ids=sorted(venue_ids), venues=venues,
        devices=devices, ports=ports, links=[],
        sources=bundle.sources, warnings=bundle.warnings,
        elapsed_seconds=round(elapsed, 2), deep=deep,
    )


async def discover(r1, tenant_id: Optional[str], venue_ids: List[str],
                   deep: bool = False, overrides_data: Optional[Dict] = None,
                   correlate: bool = True) -> Snapshot:
    """Read, shape, correlate, return. The whole of a discovery run."""
    from .correlate import correlate as run_correlation

    started = time.time()
    bundle = await collect(r1, tenant_id, venue_ids, deep=deep)
    snapshot = shape(bundle, tenant_id or "", venue_ids, time.time() - started, deep)
    try:
        snapshot.completeness = r1.switches.completeness_report()
    except Exception:                                               # noqa: BLE001
        snapshot.completeness = {}

    # The correlator reads the raw bundle rather than re-fetching. Attached to
    # the snapshot for callers that want it (the CLI reports on it); not
    # persisted -- the store writes only the shaped model.
    snapshot.raw = bundle                                           # type: ignore[attr-defined]
    if correlate:
        snapshot.correlation = run_correlation(                     # type: ignore[attr-defined]
            snapshot, bundle, overrides_data)
    snapshot.elapsed_seconds = round(time.time() - started, 2)
    return snapshot
