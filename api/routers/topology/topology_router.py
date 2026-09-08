"""
Topology API.

HUMAN-TRIGGERED ONLY. Every endpoint here runs because someone asked for it.
There is no scheduler hook, no background refresh and no polling loop: a
discovery run reads a live production network, and nothing here should do that
on a timer.

Discovery is a synchronous POST. The fan-out is concurrent and fits inside
nginx's 300s /api budget (nginx-proxy.conf:47); the one read that would not --
per-AP LLDP, ~99ms x N against an ~8% hit rate -- is behind `deep` and capped.
"""

import logging
from typing import Any, Dict, List, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from decorators import require_alpha
from dependencies import get_current_user, get_db
from models.controller import Controller
from models.user import User
from services.topology import collect as topo_collect
from services.topology import fetch, overrides, store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/topology", tags=["Topology"])


def _controller(controller_id: int, user: User, db: Session) -> Controller:
    """
    Ownership-checked, unlike WiredWiz's local helper -- which omits the
    user_id filter. Use the shared validator, which 404s an unknown controller
    and 403s one belonging to someone else.
    """
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(400, "Topology is a RUCKUS ONE tool; this controller is "
                                 f"{controller.controller_type}.")
    return controller


def _resolve_tenant(controller: Controller, tenant_id: Optional[str]):
    """(override_tenant_id, storage_key). MSP controllers must name an EC."""
    if controller.controller_subtype == "MSP":
        if not tenant_id:
            raise HTTPException(400, "This is an MSP controller -- select an MSP-EC first.")
        return tenant_id, tenant_id
    return None, controller.r1_tenant_id or f"controller-{controller.id}"


def _venue_ids(raw: Optional[str]) -> List[str]:
    ids = sorted({part.strip() for part in (raw or "").split(",") if part.strip()})
    if not ids:
        raise HTTPException(400, "venue_ids is required -- a topology is scoped to a "
                                 "venue set, and the set is its identity.")
    return ids


@router.get("/{controller_id}/scope")
@require_alpha()
async def scope(controller_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """What the UI needs before it can ask for anything else."""
    controller = _controller(controller_id, current_user, db)
    return {
        "controllerId": controller.id,
        "controllerName": controller.name,
        "subtype": controller.controller_subtype,
        "needsEcSelection": controller.controller_subtype == "MSP",
        "tenantId": controller.r1_tenant_id,
        "region": controller.r1_region,
        "snapshotTtlDays": store.SNAPSHOT_TTL_DAYS,
        "maxSnapshots": store.MAX_SNAPSHOTS,
    }


@router.get("/{controller_id}/venues")
@require_alpha()
async def venues(controller_id: int, tenant_id: Optional[str] = Query(None),
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Venue picker rows, annotated with which already have a snapshot."""
    controller = _controller(controller_id, current_user, db)
    override_tenant, storage_key = _resolve_tenant(controller, tenant_id)
    r1 = create_r1_client_from_controller(controller.id, db)

    rows = fetch.venues(r1, override_tenant) or []
    stored = {vid for scope_entry in store.list_scopes(storage_key)
              for vid in (scope_entry.get("venueIds") or [])}
    out = []
    for row in rows:
        vid = str(row.get("id") or "")
        if not vid:
            continue
        aps = row.get("aps") or {}
        out.append({
            "venueId": vid,
            "venueName": row.get("name"),
            "city": row.get("city"),
            "switches": row.get("operationalSwitches"),
            "aps": aps.get("total") if isinstance(aps, dict) else None,
            "clients": row.get("clients"),
            "hasSnapshot": vid in stored,
        })
    return {"tenantId": storage_key, "venues": sorted(out, key=lambda v: v["venueName"] or "")}


@router.post("/{controller_id}/discover")
@require_alpha()
async def discover(controller_id: int,
                   tenant_id: Optional[str] = Query(None),
                   venue_ids: Optional[str] = Query(
                       None, description="Comma-separated venue ids. The SET is the "
                                         "snapshot's identity."),
                   deep: bool = Query(
                       False, description="Also fan out per-AP LLDP and per-switch L3 "
                                          "reads. Slower; per-AP LLDP has a low hit rate "
                                          "because R1's neighbour cache is usually cold."),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """Read the network, shape it, persist a snapshot, hand it back."""
    controller = _controller(controller_id, current_user, db)
    override_tenant, storage_key = _resolve_tenant(controller, tenant_id)
    venues_wanted = _venue_ids(venue_ids)
    r1 = create_r1_client_from_controller(controller.id, db)

    logger.info("topology discover: user=%s controller=%s tenant=%s venues=%s deep=%s",
                current_user.id, controller.id, storage_key, len(venues_wanted), deep)
    # Pass the stored verdicts in: a confirmed WAN uplink and any confirmed or
    # rejected links must survive re-discovery, which is the whole reason they
    # live outside the snapshot.
    stored = overrides.load(storage_key, venues_wanted)
    snapshot = await topo_collect.discover(r1, override_tenant, venues_wanted,
                                           deep=deep, overrides_data=stored)
    name = store.save(storage_key, snapshot)
    logger.info("topology discover done: %s in %ss", snapshot.counts(),
                snapshot.elapsed_seconds)

    return {"snapshot": name, "meta": snapshot.meta(),
            "nearMisses": store.near_misses(storage_key, venues_wanted)}


@router.get("/{controller_id}/snapshots")
@require_alpha()
async def snapshots(controller_id: int, tenant_id: Optional[str] = Query(None),
                    venue_ids: Optional[str] = Query(None),
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)
    # Both ceilings, so the UI can state the retention rule rather than leave
    # "older runs are kept" to mean whatever the reader assumes.
    return {"ttlDays": store.SNAPSHOT_TTL_DAYS,
            "maxSnapshots": store.MAX_SNAPSHOTS,
            "snapshots": store.list_snapshots(storage_key, wanted),
            "nearMisses": store.near_misses(storage_key, wanted)}


@router.delete("/{controller_id}/snapshots/{name}")
@require_alpha()
async def delete_snapshot(controller_id: int, name: str,
                          tenant_id: Optional[str] = Query(None),
                          venue_ids: Optional[str] = Query(None),
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    return {"deleted": store.delete(storage_key, _venue_ids(venue_ids), name)}


@router.get("/{controller_id}/graph")
@require_alpha()
async def graph(controller_id: int, tenant_id: Optional[str] = Query(None),
                venue_ids: Optional[str] = Query(None),
                snapshot: Optional[str] = Query(None, description="Default: newest."),
                include_clients: bool = Query(False),
                min_tier: Optional[str] = Query(None),
                include_rejected: bool = Query(False),
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """
    Devices and links for the canvas. Deliberately WITHOUT per-link evidence --
    that is one request per opened panel, not 5 MB on every page load.
    """
    from services.topology.model import TIER_RANK

    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    meta = store.load_part(storage_key, wanted, "meta", snapshot)
    if meta is None:
        raise HTTPException(404, "No snapshot for this venue set. Run a discovery first.")
    devices = store.load_part(storage_key, wanted, "devices", snapshot) or []
    links = store.load_part(storage_key, wanted, "links", snapshot) or []

    # A snapshot bakes in the verdicts that existed when it was taken, but the
    # normal way to record one is to look at the map and press confirm -- which
    # happens AFTER. Re-lens on read so a verdict shows immediately rather than
    # at the next discovery. Filtering runs after, so rejecting a link hides it.
    stored = overrides.load(storage_key, wanted)
    overrides.relens(links, stored, overrides.identity_map(devices))

    if not include_clients:
        devices = [d for d in devices if d.get("kind") != "client"]
    if not include_rejected:
        links = [l for l in links if l.get("tier") != "rejected"]
    if min_tier:
        floor = TIER_RANK.get(min_tier)
        if floor is None:
            raise HTTPException(400, f"min_tier must be one of {list(TIER_RANK)}")
        links = [l for l in links if TIER_RANK.get(l.get("tier"), 0) >= floor]

    return {"snapshot": snapshot or store.latest_name(storage_key, wanted),
            "meta": meta, "nodes": devices, "links": links,
            "wan": store.load_part(storage_key, wanted, "wan", snapshot) or {},
            "overrides": stored}


@router.get("/{controller_id}/devices/{device_id:path}/ports")
@require_alpha()
async def device_ports(controller_id: int, device_id: str,
                       tenant_id: Optional[str] = Query(None),
                       venue_ids: Optional[str] = Query(None),
                       snapshot: Optional[str] = Query(None),
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """One device's port strip, fetched when the node is expanded."""
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    ports = store.load_part(storage_key, wanted, "ports", snapshot)
    if ports is None:
        raise HTTPException(404, "No snapshot for this venue set.")
    owned = [p for p in ports if p.get("deviceId") == device_id]
    links = store.load_part(storage_key, wanted, "links", snapshot) or []
    port_ids = {p["id"] for p in owned}
    touching = [l for l in links
                if (l.get("a") or {}).get("portId") in port_ids
                or (l.get("b") or {}).get("portId") in port_ids]
    return {"deviceId": device_id, "ports": owned, "links": touching}


@router.get("/{controller_id}/links/{link_id}/evidence")
@require_alpha()
async def link_evidence(controller_id: int, link_id: str,
                        tenant_id: Optional[str] = Query(None),
                        venue_ids: Optional[str] = Query(None),
                        snapshot: Optional[str] = Query(None),
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """
    Why this link is believed -- every supporting and contradicting item, plus
    the running arithmetic, so the confidence number summarises something the
    reader can see rather than acting as an oracle.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    links = store.load_part(storage_key, wanted, "links", snapshot)
    if links is None:
        raise HTTPException(404, "No snapshot for this venue set.")
    link = next((l for l in links if l.get("id") == link_id), None)
    if link is None:
        raise HTTPException(404, f"No link {link_id} in this snapshot.")

    evidence = store.load_part(storage_key, wanted, "evidence", snapshot) or {}
    # Same lens as /graph, so the arithmetic in the panel always sums to the
    # confidence drawn on the canvas.
    devices = store.load_part(storage_key, wanted, "devices", snapshot) or []
    overrides.relens([link], overrides.load(storage_key, wanted),
                     overrides.identity_map(devices), evidence)
    rows = evidence.get(link_id, [])
    running, lines = 0.0, []
    for item in rows:
        running += float(item.get("weight") or 0)
        lines.append({"source": item.get("source"), "kind": item.get("kind"),
                      "claim": item.get("claim"), "weight": item.get("weight"),
                      "running": round(running, 4)})
    return {"link": link, "evidence": rows,
            "arithmetic": {"lines": lines, "total": round(running, 4),
                           "confidence": link.get("confidence")},
            "tierReason": link.get("tierReason")}


@router.get("/{controller_id}/findings")
@require_alpha()
async def findings(controller_id: int, tenant_id: Optional[str] = Query(None),
                   venue_ids: Optional[str] = Query(None),
                   snapshot: Optional[str] = Query(None),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """
    What the map implies about the estate, as lists rather than as a picture.

    All four fall out of the correlation run for free -- they are the devices
    the graph could not fully place -- but they are invisible on a canvas of
    4,500 nodes, and each answers a question somebody actually asks:

      unmanaged   something is plugged in that RUCKUS ONE does not manage.
      outOfScope  a real device in a venue this map does not cover. The fix is
                  to add the venue, not to investigate -- so it is listed
                  separately from `unmanaged` rather than lumped in with it.
      unattached  online, but nothing reports what feeds it. Almost always
                  cabled to a switch R1 does not manage.
      ghosts      in inventory, not online, and mentioned by no evidence at all.
                  Either genuinely dead or decommissioned and never removed.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    devices = store.load_part(storage_key, wanted, "devices", snapshot)
    if devices is None:
        raise HTTPException(404, "No snapshot for this venue set. Run a discovery first.")
    links = store.load_part(storage_key, wanted, "links", snapshot) or []
    overrides.relens(links, overrides.load(storage_key, wanted),
                     overrides.identity_map(devices))
    by_id = {d["id"]: d for d in devices}
    # Snapshots taken before the shaper backfilled it have no venueName on APs.
    # The name map is in meta, so read it from there rather than showing blanks
    # on every stored run until someone re-discovers.
    venue_names = ((store.load_part(storage_key, wanted, "meta", snapshot) or {})
                   .get("venues") or {})

    # Who reported each external peer, and through which port. Without this the
    # list says "there is something out there" and leaves you to find it.
    seen_from: Dict[str, List[Dict[str, Any]]] = {}
    for link in links:
        if link.get("tier") == "rejected":
            continue
        for near, far in ((link["a"], link["b"]), (link["b"], link["a"])):
            if not str(far.get("deviceId", "")).startswith("ext:"):
                continue
            owner = by_id.get(near.get("deviceId")) or {}
            seen_from.setdefault(far["deviceId"], []).append({
                "device": owner.get("displayName") or near.get("deviceId"),
                "deviceId": near.get("deviceId"),
                "port": near.get("ident") or "",
                "tier": link.get("tier"),
            })

    unmanaged, out_of_scope, unattached, ghosts = [], [], [], []
    for device in devices:
        kind = device.get("kind")
        attrs = device.get("attrs") or {}
        if kind == "external":
            row = {"deviceId": device["id"],
                   "name": device.get("displayName") or device.get("name") or "",
                   "mac": device.get("mac", ""), "model": device.get("model", ""),
                   "venueName": device.get("venueName", ""),
                   "seenFrom": seen_from.get(device["id"], [])[:8]}
            if attrs.get("outOfScope"):
                out_of_scope.append({**row, "venueId": device.get("venueId", "")})
            else:
                unmanaged.append({**row, "foreignOui": bool(attrs.get("foreignOui"))})
            continue

        if kind not in ("switch", "stack", "ap"):
            continue
        if (device.get("counts") or {}).get("links"):
            continue
        row = {"deviceId": device["id"], "kind": kind,
               "name": device.get("displayName") or device.get("name") or "",
               "status": device.get("status", "unknown"),
               "rawStatus": device.get("rawStatus", ""),
               "model": device.get("model", ""), "serial": device.get("serial", ""),
               "venueName": device.get("venueName")
                            or venue_names.get(device.get("venueId", ""), ""),
               "hint": str(attrs.get("hint") or "")}
        (unattached if device.get("status") == "online" else ghosts).append(row)

    order = lambda rows: sorted(rows, key=lambda r: (r.get("venueName", ""), r.get("name", "")))
    return {"unmanaged": order(unmanaged), "outOfScope": order(out_of_scope),
            "unattached": order(unattached), "ghosts": order(ghosts),
            "counts": {"unmanaged": len(unmanaged), "outOfScope": len(out_of_scope),
                       "unattached": len(unattached), "ghosts": len(ghosts)}}


@router.get("/{controller_id}/export/links.csv")
@require_alpha()
async def export_links_csv(controller_id: int, tenant_id: Optional[str] = Query(None),
                           venue_ids: Optional[str] = Query(None),
                           snapshot: Optional[str] = Query(None),
                           include_rejected: bool = Query(False),
                           db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)):
    """
    Every inferred link as a row: both ends, both ports, how sure we are, and
    which evidence said so.

    This is an as-built patch schedule — the artefact people otherwise pay
    somebody to produce by hand — so it is deliberately verbose enough to audit
    rather than just to look at.

    Built from the STORED snapshot, never recomputed, so an export can never
    disagree with the map it was taken from.
    """
    import csv
    import io

    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    meta = store.load_part(storage_key, wanted, "meta", snapshot)
    links = store.load_part(storage_key, wanted, "links", snapshot)
    if meta is None or links is None:
        raise HTTPException(404, "No snapshot for this venue set. Run a discovery first.")
    device_rows = store.load_part(storage_key, wanted, "devices", snapshot) or []
    devices = {d["id"]: d for d in device_rows}
    overrides.relens(links, overrides.load(storage_key, wanted),
                     overrides.identity_map(device_rows))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "aDevice", "aKind", "aVenue", "aPort", "bDevice", "bKind", "bVenue", "bPort",
        "linkKind", "confidence", "score", "certainty", "observedBy", "evidenceSources",
        "why", "confirmedBy", "confirmedAt",
    ])
    for link in sorted(links, key=lambda l: (
            devices.get(l["a"]["deviceId"], {}).get("displayName", ""),
            l["a"].get("ident") or "")):
        if link.get("logicalOf"):
            continue                        # a bundle member; the bundle is the row
        if link.get("tier") == "rejected" and not include_rejected:
            continue
        a = devices.get(link["a"]["deviceId"], {})
        b = devices.get(link["b"]["deviceId"], {})
        writer.writerow([
            a.get("displayName") or link["a"]["deviceId"], a.get("kind", ""),
            a.get("venueName", ""), link["a"].get("ident") or "",
            b.get("displayName") or link["b"]["deviceId"], b.get("kind", ""),
            b.get("venueName", ""), link["b"].get("ident") or "",
            link.get("kind", ""), link.get("tier", ""),
            link.get("score", ""), link.get("confidence", ""),
            link.get("directionality", ""),
            " ".join((link.get("evidenceSummary") or {}).get("sources") or []),
            link.get("tierReason", ""),
            (link.get("override") or {}).get("by", ""),
            (link.get("override") or {}).get("at", ""),
        ])

    stamp = (meta.get("takenAt") or "")[:19].replace(":", "").replace("-", "")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="topology-links-{stamp}.csv"'},
    )


@router.get("/{controller_id}/export.json")
@require_alpha()
async def export_json(controller_id: int, tenant_id: Optional[str] = Query(None),
                      venue_ids: Optional[str] = Query(None),
                      snapshot: Optional[str] = Query(None),
                      include_evidence: bool = Query(True),
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """
    The whole snapshot as one JSON file.

    Reassembled from the parts it is stored in, so what comes out is what the
    map was drawn from — including the evidence, which is the part worth having
    if you want to argue with a conclusion later.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    meta = store.load_part(storage_key, wanted, "meta", snapshot)
    if meta is None:
        raise HTTPException(404, "No snapshot for this venue set. Run a discovery first.")
    devices = store.load_part(storage_key, wanted, "devices", snapshot) or []
    links = store.load_part(storage_key, wanted, "links", snapshot) or []
    stored = overrides.load(storage_key, wanted)
    evidence = store.load_part(storage_key, wanted, "evidence", snapshot) or {}
    # Lensed like every other read, so a download and the map on screen can
    # never disagree about a link a human has ruled on.
    overrides.relens(links, stored, overrides.identity_map(devices),
                     evidence if include_evidence else None)
    payload = {
        "meta": meta,
        "devices": devices,
        "ports": store.load_part(storage_key, wanted, "ports", snapshot) or [],
        "links": links,
        "wan": store.load_part(storage_key, wanted, "wan", snapshot) or {},
        "overrides": stored,
    }
    if include_evidence:
        payload["evidence"] = evidence

    stamp = (meta.get("takenAt") or "")[:19].replace(":", "").replace("-", "")
    return Response(
        content=json.dumps(payload, indent=1, default=str),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="topology-{stamp}.json"'},
    )


@router.get("/{controller_id}/uplinks")
@require_alpha()
async def uplinks(controller_id: int, tenant_id: Optional[str] = Query(None),
                  venue_ids: Optional[str] = Query(None),
                  snapshot: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    Where each venue reaches the internet: what has been confirmed, and what the
    tool would propose otherwise.

    Read from the stored snapshot rather than recomputed, so the candidates
    shown are the ones the map was actually built from.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    stored = store.load_part(storage_key, wanted, "wan", snapshot)
    if stored is None:
        raise HTTPException(404, "No snapshot for this venue set. Run a discovery first.")
    return stored


@router.post("/{controller_id}/uplinks/confirm")
@require_alpha()
async def confirm_uplink(controller_id: int, body: Dict[str, Any],
                         tenant_id: Optional[str] = Query(None),
                         venue_ids: Optional[str] = Query(None),
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """
    Settle a venue's WAN uplink.

    `confirm` pins it, `reject` remembers that this candidate is wrong so it
    stops being offered, `clear` forgets both. One verdict per venue -- a
    three-venue scope plausibly has three ways out.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    venue_id = body.get("venueId")
    action = body.get("action")
    if not venue_id:
        raise HTTPException(400, "venueId is required.")
    if action not in ("confirm", "reject", "clear"):
        raise HTTPException(400, "action must be confirm, reject or clear.")
    if action != "clear" and not body.get("deviceId"):
        raise HTTPException(400, "deviceId is required to confirm or reject.")

    return overrides.set_wan(
        storage_key, wanted, str(venue_id), str(body.get("deviceId") or ""),
        str(body.get("portIdent") or ""), action,
        by=current_user.email or str(current_user.id))


@router.get("/{controller_id}/sources")
@require_alpha()
async def sources(controller_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """
    The evidence catalogue: every source, what it reads, what it proves, its
    caveats, and the weight and tier cap of each evidence kind it emits.

    Served from the same table the engine scores with, so the UI's explanation
    of the confidence model cannot drift from the model.
    """
    from services.topology.evidence import catalogue

    _controller(controller_id, current_user, db)
    return {"tiers": ["rejected", "weak", "probable", "strong", "confirmed"],
            "sources": catalogue()}


def _links_for_key(storage_key: str, wanted, key: str) -> List[Dict[str, Any]]:
    """
    The stored links a verdict key addresses, with the CURRENT verdict applied.

    Returned by the verdict endpoints so the canvas can patch just these rows.
    Re-fetching the whole graph would be correct but would also reset the
    arrangement -- positions, open groups, selection -- and losing a hand-made
    layout because you pressed Confirm is not a trade worth making.
    """
    devices = store.load_part(storage_key, wanted, "devices", None) or []
    links = store.load_part(storage_key, wanted, "links", None) or []
    identity = overrides.identity_map(devices)
    stored = overrides.load(storage_key, wanted)
    hits = [l for l in links if overrides.key_for_link(l, identity) == key]
    return overrides.relens(hits, stored, identity)


@router.get("/{controller_id}/overrides")
@require_alpha()
async def get_overrides(controller_id: int, tenant_id: Optional[str] = Query(None),
                        venue_ids: Optional[str] = Query(None),
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    return overrides.load(storage_key, _venue_ids(venue_ids))


@router.post("/{controller_id}/overrides")
@require_alpha()
async def set_override(controller_id: int, body: Dict[str, Any],
                       tenant_id: Optional[str] = Query(None),
                       venue_ids: Optional[str] = Query(None),
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Record a human verdict. Outranks every machine source, and never expires."""
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)

    endpoints = body.get("endpoints") or []
    if len(endpoints) != 2:
        raise HTTPException(400, "endpoints must be a list of exactly two "
                                 "{deviceId, portIdent} objects.")
    verdict = body.get("verdict")
    if verdict not in overrides.VERDICTS:
        raise HTTPException(400, f"verdict must be one of {list(overrides.VERDICTS)}")

    # The key is built from serial-or-MAC, never from the device id, so a
    # verdict survives a rename. The SERVER resolves that identity from the
    # snapshot rather than trusting the client to: the correlator derives the
    # same key its own way, and a client that guessed differently would store a
    # verdict that silently never applies to anything.
    identity = overrides.identity_map(
        store.load_part(storage_key, wanted, "devices", None) or [])
    key = overrides.link_key(*[
        overrides.endpoint_key(
            e.get("serialOrMac") or identity.get(e.get("deviceId", ""))
            or e.get("deviceId", ""),
            e.get("portIdent"))
        for e in endpoints])
    entry = overrides.set_link(storage_key, wanted, key, verdict,
                               by=current_user.email or str(current_user.id),
                               note=body.get("note", ""), endpoints=endpoints)
    logger.info("topology override: user=%s verdict=%s key=%s",
                current_user.id, verdict, key)
    return {"override": entry, "links": _links_for_key(storage_key, wanted, key)}


@router.delete("/{controller_id}/overrides/{key:path}")
@require_alpha()
async def clear_override(controller_id: int, key: str,
                         tenant_id: Optional[str] = Query(None),
                         venue_ids: Optional[str] = Query(None),
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    wanted = _venue_ids(venue_ids)
    deleted = overrides.clear_link(storage_key, wanted, key)
    return {"deleted": deleted, "links": _links_for_key(storage_key, wanted, key)}


@router.get("/{controller_id}/layout")
@require_alpha()
async def get_layout(controller_id: int, tenant_id: Optional[str] = Query(None),
                     venue_ids: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    return store.load_layout(storage_key, _venue_ids(venue_ids))


@router.put("/{controller_id}/layout")
@require_alpha()
async def put_layout(controller_id: int, body: Dict[str, Any],
                     tenant_id: Optional[str] = Query(None),
                     venue_ids: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """
    Save a hand-arranged layout. A stale `version` is refused rather than
    overwritten -- two tabs on one venue must not silently discard the
    arrangement that was saved first.
    """
    controller = _controller(controller_id, current_user, db)
    _, storage_key = _resolve_tenant(controller, tenant_id)
    result = store.save_layout(storage_key, _venue_ids(venue_ids), body,
                               expected_version=body.get("version"))
    if not result.get("ok"):
        raise HTTPException(409, {"message": "Layout changed since you loaded it.",
                                  **result})
    return result
