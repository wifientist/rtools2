"""
The correlation pipeline.

    sources -> claims -> merge -> score -> contention -> overrides

The order is the design. Merging before scoring keeps the output independent of
the order claims arrived in. Overrides last means a human verdict sits on top of
everything the machine concluded, with that conclusion still visible underneath.
"""

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from . import evidence as evidence_pkg
from . import merge as merge_mod
from . import score as score_mod
from .index import TopologyIndex
from .model import Device, Link, Snapshot
from .normalize import mac_display, norm_mac
from .overrides import endpoint_key, link_key

logger = logging.getLogger(__name__)


def _override_key_for(index: TopologyIndex):
    """
    A link's override key, built from serial-or-MAC rather than device id.

    Serial first so a device that changes IP, name or firmware keeps its
    verdicts. An RMA -- new serial, new MAC -- correctly loses them: it is a
    different device, and inheriting a judgement about the old one would be
    worse than asking again.
    """
    def key_for(link: Link) -> str:
        parts = []
        for end in (link.a, link.b):
            device = index.device(end.device_id)
            identity = ((device.serial or device.mac) if device
                        else end.device_id)
            parts.append(endpoint_key(identity, end.ident))
        return link_key(*parts)
    return key_for


def _external_devices(links: List[Link], index: TopologyIndex,
                      bundle) -> List[Device]:
    """
    Materialise the peers that sources referred to but inventory did not cover.

    Two very different things end up here, and conflating them would be a
    genuine misrepresentation:

      * a device R1 does not manage -- a firewall, a router, a third-party
        switch. Worth investigating.
      * a device R1 manages perfectly well, in a venue the user did not select.
        On the probe tenant this was all 8 "external" nodes: a scoped map of one
        quadrant showed its uplinks to the other quadrants' cores as unknown
        boxes. The fix is not investigation, it is "add that venue".

    They are told apart with the tenant-wide switch roster, which the scoped
    read already fetched, so this costs no extra call.
    """
    in_scope = set(index.devices)
    out_of_scope: Dict[str, Dict] = {}
    for row in getattr(bundle, "all_switches", None) or []:
        mac = norm_mac(row.get("switchMac") or row.get("id"))
        if mac and f"sw:{mac}" not in in_scope:
            out_of_scope[mac] = row

    out: Dict[str, Device] = {}
    for link in links:
        for end in (link.a, link.b):
            if not end.device_id.startswith("ext:") or end.device_id in out:
                continue
            if end.device_id.startswith("ext:dense:"):
                continue                    # a context marker, not a device
            discovered = end.discovered_as or {}
            mac12 = norm_mac(discovered.get("mac"))
            known = out_of_scope.get(mac12)

            if known:
                out[end.device_id] = Device(
                    id=end.device_id, kind="external",
                    name=str(known.get("name") or discovered.get("name") or ""),
                    mac=mac12, serial=str(known.get("serialNumber") or ""),
                    model=str(known.get("model") or ""),
                    ip=str(known.get("ipAddress") or ""),
                    venue_id=str(known.get("venueId") or ""),
                    venue_name=str(known.get("venueName") or ""),
                    managed=True, status=_status_of(known), role="unknown",
                    attrs={"outOfScope": True,
                           "hint": f"Managed switch in '{known.get('venueName')}'. "
                                   f"Add that venue to the scope to see it and "
                                   f"everything behind it.",
                           "discoveredAs": discovered},
                )
            else:
                out[end.device_id] = Device(
                    id=end.device_id, kind="external",
                    name=str(discovered.get("name")
                             or mac_display(mac12) or "unknown neighbour"),
                    mac=mac12, managed=False, status="unknown", role="unknown",
                    attrs={"outOfScope": False,
                           "foreignOui": discovered.get("foreignOui", False),
                           "discoveredAs": discovered},
                )
    return list(out.values())


def _kind_of(index: TopologyIndex):
    """
    A device id -> its kind, including peers not yet materialised.

    External devices are created AFTER scoring, because they exist only because
    a link named them. So a plain index lookup returns None for an `ext:` id at
    contention time, and the single-uplink constraint silently skipped every AP
    whose competing uplink pointed at an out-of-scope switch.
    """
    def kind_of(device_id: str) -> str:
        device = index.device(device_id)
        if device is not None:
            return device.kind
        if device_id.startswith("ext:dense:"):
            return "context"
        if device_id.startswith("ext:"):
            return "external"
        return "unknown"
    return kind_of


def _status_of(row: Dict) -> str:
    from .collect import _online
    return _online(row.get("deviceStatus"))


def correlate(snapshot: Snapshot, bundle, overrides_data: Optional[Dict] = None,
              only_sources: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Run the pipeline over one snapshot, mutating it in place.

    Returns a report describing what ran, for the UI and for the CLI's own
    accounting. A failing source is recorded, never fatal.
    """
    started = time.time()
    index = TopologyIndex(snapshot.devices, snapshot.ports,
                          getattr(bundle, "macs", None))

    outcome = evidence_pkg.run_sources(bundle, index, only=only_sources)
    claims = outcome["claims"]

    links = merge_mod.merge(claims)
    for link in links:
        score_mod.score_link(link)
    links = score_mod.resolve_port_contention(links)
    # An AP has one wired uplink. Complements port contention, which is
    # per-port: this is the one-device-many-links case.
    links = score_mod.resolve_single_uplink(links, _kind_of(index))

    overrides = {}
    if overrides_data:
        from .overrides import as_overrides
        overrides = as_overrides(overrides_data)
        links = score_mod.apply_overrides(links, overrides, _override_key_for(index))

    # Drop context-only pseudo-links: they carry no peer, and their evidence has
    # already done its work by suppressing endpoint claims on dense ports.
    links = [l for l in links if not l.b.device_id.startswith("ext:dense:")]

    externals = _external_devices(links, index, bundle)
    snapshot.devices.extend(externals)
    snapshot.links = links

    # WAN uplinks. Candidates are proposed from whatever signals exist; a
    # `wan:` node and its link are materialised ONLY where a human confirmed
    # one, because an unconfirmed guess drawn on the map is indistinguishable
    # from a fact and it anchors the entire hierarchy.
    from . import uplink as uplink_mod

    wan_state = (overrides_data or {}).get("wan") or {}
    snapshot.wan = uplink_mod.build(snapshot, bundle, index, wan_state)  # type: ignore[attr-defined]
    wan_devices, wan_links = uplink_mod.wan_devices_and_links(snapshot, wan_state)
    snapshot.devices.extend(wan_devices)
    snapshot.links = links = links + wan_links

    # Per-device link counts, so a collapsed node can show a degree without the
    # canvas walking every edge.
    degree: Dict[str, int] = {}
    for link in links:
        if link.logical_of:
            continue                        # a bundle member; counted as the bundle
        for end in (link.a, link.b):
            degree[end.device_id] = degree.get(end.device_id, 0) + 1
    for device in snapshot.devices:
        if degree.get(device.id):
            device.counts["links"] = degree[device.id]

    by_tier: Dict[str, int] = {}
    for link in links:
        by_tier[link.tier] = by_tier.get(link.tier, 0) + 1

    report = {
        "elapsedSeconds": round(time.time() - started, 2),
        "claims": len(claims),
        "links": len(links),
        "byTier": by_tier,
        "byKind": _count(links, lambda l: l.kind),
        "byDirectionality": _count(links, lambda l: l.directionality),
        "externalDevices": len(externals),
        "outOfScopeDevices": sum(1 for d in externals if d.attrs.get("outOfScope")),
        "overridesApplied": sum(1 for l in links if l.override is not None),
        "wanConfirmed": sum(
            1 for v in (snapshot.wan or {}).get("venues", {}).values()  # type: ignore[attr-defined]
            if v.get("confirmed")),
        "wanCandidates": (snapshot.wan or {}).get("totalCandidates", 0),  # type: ignore[attr-defined]
        # APs that are online but which nothing reports an uplink for. Almost
        # always fed by a switch R1 does not manage -- unknowable from a
        # read-only surface, and worth stating rather than quietly omitting.
        "unattachedAps": _unattached_aps(snapshot, links),
        "sourcesRun": outcome["sourcesRun"],
        "sourcesSkipped": outcome["sourcesSkipped"],
        "sourcesFailed": outcome["sourcesFailed"],
        "index": index.stats(),
        "aliasCollisions": index.aliases.collisions[:20],
    }
    logger.info("topology correlate: %s claims -> %s links %s", len(claims),
                len(links), by_tier)
    return report


def _unattached_aps(snapshot: Snapshot, links: List[Link]) -> int:
    kinds = {d.id: d.kind for d in snapshot.devices}
    attached = set()
    for link in links:
        if link.tier == "rejected" or link.logical_of:
            continue
        for near, far in ((link.a, link.b), (link.b, link.a)):
            if kinds.get(near.device_id) == "ap":
                attached.add(near.device_id)
    return sum(1 for d in snapshot.devices
               if d.kind == "ap" and d.status == "online" and d.id not in attached)


def _count(items, key):
    out: Dict[str, int] = {}
    for item in items:
        value = key(item)
        out[value] = out.get(value, 0) + 1
    return out


def switch_graph(snapshot: Snapshot) -> Dict[str, Any]:
    """
    The switch-to-switch subgraph, in the shape WiredWiz's analyze.topology()
    reports it -- so the two implementations can be compared directly.

    They count different things on purpose: WiredWiz counts one-sided PORT
    OBSERVATIONS (one per up port whose LLDP neighbour is a managed switch),
    while this counts merged LINKS. A mutual pair is 2 there and 1 here. The
    comparison that matters is the set of device PAIRS, which must be identical.
    """
    by_id = {d.id: d for d in snapshot.devices}
    pairs = set()
    observations = 0
    for link in snapshot.links:
        # Skip LAG MEMBERS (their pair is represented by the bundle) but keep
        # the bundle itself. Skipping both would erase the pair entirely, which
        # is how a real Core-to-Distribution LAG went missing from this
        # comparison while the engine had bundled it correctly.
        if link.logical_of:
            continue
        a, b = by_id.get(link.a.device_id), by_id.get(link.b.device_id)
        if not (a and b):
            continue
        if a.kind not in ("switch", "stack") or b.kind not in ("switch", "stack"):
            continue
        if a.id == b.id:
            continue                        # stack-internal
        pairs.add(tuple(sorted([a.id, b.id])))
        observations += 2 if link.directionality == "bidirectional" else 1
    return {"pairs": pairs, "pairCount": len(pairs), "observations": observations,
            "names": {d.id: d.display_name for d in snapshot.devices}}
