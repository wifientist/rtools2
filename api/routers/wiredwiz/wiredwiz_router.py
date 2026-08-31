"""
WiredWiz Router — read-only switch crawler and loop hunter.

Every endpoint here is READ-ONLY against RUCKUS ONE: GETs and `*/query` POSTs.
Nothing creates a config backup, pushes a CLI template, reboots, or syncs a
switch. The only writes are to local snapshot storage.

HUMAN-TRIGGERED ONLY. Every endpoint runs once per request. WiredWiz registers
no scheduled job, starts no background task, and exposes no recurring-crawl
entry point -- there is intentionally nothing here for a scheduler to call.

  GET    /wiredwiz/{cid}/scope                  what tenant this controller acts on
  GET    /wiredwiz/{cid}/venues                 venues holding switches, for the picker
  POST   /wiredwiz/{cid}/crawl                  take one snapshot (~6s for 200 switches)
  GET    /wiredwiz/{cid}/snapshots              stored snapshots for a tenant
  DELETE /wiredwiz/{cid}/snapshots/{file}       drop one snapshot
  GET    /wiredwiz/{cid}/analysis               the four loop signals
  GET    /wiredwiz/{cid}/switches               inventory from the latest snapshot
  GET    /wiredwiz/{cid}/switches/{sid}/config  redacted running config, one switch
  GET    /wiredwiz/{cid}/macTables              MAC table size per switch
  GET    /wiredwiz/{cid}/checks                 the rule library (what gets checked)
  POST   /wiredwiz/{cid}/baseline               explicit bulk config read, stored
  GET    /wiredwiz/{cid}/baselines              stored baselines
  DELETE /wiredwiz/{cid}/baselines/{file}
  POST   /wiredwiz/{cid}/health                 run the checks and return findings
  GET    /wiredwiz/{cid}/report.pdf             findings + check catalogue as a PDF
  GET    /wiredwiz/{cid}/report.csv             findings as CSV
  GET    /wiredwiz/{cid}/report.json            the full stored result, verbatim
  GET    /wiredwiz/{cid}/health                 the last stored result (no re-run)

A crawl never touches configuration. Config is read in exactly two ways, both
explicit: one switch at a time when someone opens it, or a whole-estate BASELINE
that a person deliberately triggers. Neither happens on a timer, and a crawl
never does either.

Baselines are kept precisely so config *drift* is detectable -- comparing a fresh
read against the baseline answers "what changed?", which no live metric can.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML as WeasyHTML
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from dependencies import get_current_user, get_db
from models.controller import Controller
from models.user import User
from services.wiredwiz import analyze as analysis
from services.wiredwiz import store
from reports.wiredwiz import build_context as build_report_context
from reports.wiredwiz import findings_csv
from services.wiredwiz.checks import REGISTRY as CHECK_REGISTRY
from services.wiredwiz.checks import run_health_check
from services.wiredwiz.crawl import fetch_redacted_config, take_snapshot
from services.wiredwiz.mactable import summarize_mac_tables

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wiredwiz", tags=["WiredWiz"])


@lru_cache(maxsize=1)
def _jinja() -> Environment:
    """Report templates live alongside the other PDF reports in api/templates."""
    return Environment(loader=FileSystemLoader(
        str(Path(__file__).resolve().parent.parent.parent / "templates")))


def _controller(controller_id: int, db: Session) -> Controller:
    c = db.query(Controller).filter(Controller.id == controller_id).first()
    if not c:
        raise HTTPException(404, f"Controller {controller_id} not found")
    if c.controller_type != "RuckusONE":
        raise HTTPException(400, f"WiredWiz needs a RuckusONE controller; "
                                 f"'{c.name}' is {c.controller_type}")
    return c


def _resolve_tenant(c: Controller, tenant_id: Optional[str]):
    """
    Returns (override_tenant_id, storage_key).

    An MSP controller must be told which EC to act on -- its own tenant id
    addresses the MSP account, which owns no switches. An EC controller
    addresses itself and takes no override header.
    """
    if c.controller_subtype == "MSP":
        if not tenant_id:
            raise HTTPException(400, "This is an MSP controller — select an MSP-EC first.")
        return tenant_id, tenant_id
    return None, c.r1_tenant_id or f"controller-{c.id}"


def _venue_ids(raw: Optional[str]) -> Optional[List[str]]:
    """Comma-separated venue ids from the query string, or None for the whole tenant."""
    if not raw:
        return None
    ids = [v.strip() for v in raw.split(",") if v.strip()]
    return ids or None


@router.get("/{controller_id}/scope")
async def get_scope(controller_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Tells the UI whether it has to ask for an MSP-EC before anything else."""
    c = _controller(controller_id, db)
    return {
        "controllerId": c.id,
        "controllerName": c.name,
        "subtype": c.controller_subtype,
        "needsEcSelection": c.controller_subtype == "MSP",
        "tenantId": c.r1_tenant_id,
        "region": c.r1_region or "NA",
    }


@router.get("/{controller_id}/venues")
async def list_venues(controller_id: int,
                      tenant_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    Venues that actually hold switches, with counts, for the venue picker.

    Deliberately live rather than snapshot-derived: the picker has to work before
    the first crawl. It is one cheap query — switch ids and venue names only.
    """
    c = _controller(controller_id, db)
    override, key = _resolve_tenant(c, tenant_id)

    r1 = create_r1_client_from_controller(controller_id, db)
    rows = r1.switches.list_switches(override)

    venues: Dict[str, Dict[str, Any]] = {}
    for s in rows:
        vid = s.get("venueId")
        if not vid:
            continue
        v = venues.setdefault(vid, {"venueId": vid, "venueName": s.get("venueName") or vid,
                                    "switches": 0, "online": 0, "offline": 0})
        v["switches"] += 1
        if s.get("deviceStatus") == "ONLINE":
            v["online"] += 1
        elif s.get("deviceStatus") == "OFFLINE":
            v["offline"] += 1

    crawled = {}
    snap = store.load(key)
    if snap:
        crawled = {"takenAt": snap.get("takenAt"),
                   "venueIds": sorted((snap.get("venues") or {}).keys()),
                   "scopeVenueIds": snap.get("scopeVenueIds")}

    return {"venues": sorted(venues.values(), key=lambda v: v["venueName"].lower()),
            "totalSwitches": len(rows), "lastSnapshot": crawled}


@router.post("/{controller_id}/crawl")
async def crawl(controller_id: int,
                tenant_id: Optional[str] = Query(None),
                venue_ids: Optional[str] = Query(
                    None, description="Comma-separated venue ids. Omit for the whole "
                                      "tenant. The scope is recorded on the snapshot so "
                                      "snapshots taken at different scopes are never "
                                      "differenced as-is."),
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """
    One snapshot, one request. Ports, MAC table and topology only -- configuration
    is never collected here.
    """
    c = _controller(controller_id, db)
    override, key = _resolve_tenant(c, tenant_id)

    r1 = create_r1_client_from_controller(controller_id, db)
    snap = take_snapshot(r1, override, venue_ids=_venue_ids(venue_ids))
    filename = store.save(key, snap)

    comp = snap["completeness"]
    return {
        "file": filename,
        "takenAt": snap["takenAt"],
        "elapsedSeconds": snap["elapsedSeconds"],
        "switches": len(snap["switches"]),
        "ports": len(snap["ports"]),
        "macs": len(snap["macs"]),
        "venues": snap["venues"],
        "scopeVenueIds": snap.get("scopeVenueIds"),
        "complete": not comp["incomplete"],
        "completeness": comp,
    }


@router.get("/{controller_id}/snapshots")
async def list_snapshots(controller_id: int,
                         tenant_id: Optional[str] = Query(None),
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    return {"snapshots": store.list_snapshots(key),
            "ttlDays": store.SNAPSHOT_TTL_DAYS}


@router.delete("/{controller_id}/snapshots/{file}")
async def delete_snapshot(controller_id: int, file: str,
                          tenant_id: Optional[str] = Query(None),
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    if not store.delete(key, file):
        raise HTTPException(404, f"No snapshot named {file}")
    return {"deleted": file}


@router.get("/{controller_id}/analysis")
async def get_analysis(controller_id: int,
                       tenant_id: Optional[str] = Query(None),
                       venue_ids: Optional[str] = Query(None),
                       min_window: int = Query(analysis.MIN_WINDOW),
                       limit: int = Query(12, le=48),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    snaps = store.load_all(key, limit=limit)
    if not snaps:
        raise HTTPException(404, "No snapshots yet — run a crawl first.")
    snaps, scope = store.comparable(snaps, _venue_ids(venue_ids))
    if not any(s.get("ports") for s in snaps):
        raise HTTPException(404, "No crawled data for the selected venues.")
    result = analysis.analyze(snaps, min_window=min_window)
    result["scope"] = scope
    return result


@router.get("/{controller_id}/switches")
async def list_switches(controller_id: int,
                        tenant_id: Optional[str] = Query(None),
                        venue_ids: Optional[str] = Query(None),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """
    Inventory as of the latest snapshot, with per-switch port and MAC counts so
    the UI can show what has actually been crawled.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    # load_covering, not load: the newest snapshot is not necessarily one that
    # covers the venues being viewed, and filtering a non-covering snapshot down
    # yields a near-empty inventory that looks like data loss rather than scope.
    snap, scope = store.load_covering(key, _venue_ids(venue_ids))
    if not snap:
        raise HTTPException(404, "No snapshots yet — run a crawl first.")

    ports_by_sw: Dict[str, int] = {}
    up_by_sw: Dict[str, int] = {}
    for p in snap["ports"]:
        mac = (p.get("switchMac") or "").lower()
        ports_by_sw[mac] = ports_by_sw.get(mac, 0) + 1
        if str(p.get("status", "")).lower() == "up":
            up_by_sw[mac] = up_by_sw.get(mac, 0) + 1
    macs_by_sw: Dict[str, int] = {}
    for m in snap["macs"]:
        mac = (m.get("switchMac") or "").lower()
        macs_by_sw[mac] = macs_by_sw.get(mac, 0) + 1

    rows = []
    for s in snap["switches"]:
        mac = (s.get("switchMac") or s.get("id") or "").lower()
        rows.append({
            **{k: s.get(k) for k in ("id", "name", "serialNumber", "model", "family",
                                     "firmwareVersion", "ipAddress", "deviceStatus",
                                     "venueId", "venueName", "uptime", "numOfPorts",
                                     "isStack", "clientCount", "cpu", "memory")},
            "crawledPorts": ports_by_sw.get(mac, 0),
            "upPorts": up_by_sw.get(mac, 0),
            "learnedMacs": macs_by_sw.get(mac, 0),
        })
    rows.sort(key=lambda r: (r.get("venueName") or "", r.get("name") or ""))
    return {"takenAt": snap["takenAt"], "venues": snap.get("venues", {}),
            "switches": rows, "scope": scope}


@router.get("/{controller_id}/switches/{switch_id}/ports")
async def switch_ports(controller_id: int, switch_id: str,
                       tenant_id: Optional[str] = Query(None),
                       venue_ids: Optional[str] = Query(None),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """
    Port detail for one switch, with learned MACs.

    Takes the same venue scope as the inventory so both read the same snapshot --
    otherwise a scoped inventory and an unscoped port view can disagree about a
    switch that only one of their snapshots saw.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    snap, _scope = store.load_covering(key, _venue_ids(venue_ids))
    if not snap:
        raise HTTPException(404, "No snapshots yet — run a crawl first.")

    sid = switch_id.lower()
    ports = [p for p in snap["ports"] if (p.get("switchMac") or "").lower() == sid]
    if not ports:
        raise HTTPException(404, f"No ports for switch {switch_id} in the latest snapshot")

    macs_by_port: Dict[str, list] = {}
    for m in snap["macs"]:
        macs_by_port.setdefault(m.get("switchPortId"), []).append({
            "mac": m.get("clientMac"), "ip": m.get("clientIpv4Addr"),
            "vlan": m.get("clientVlan"), "name": m.get("clientName"),
            "type": m.get("clientType"),
        })
    for p in ports:
        p["learnedMacs"] = macs_by_port.get(p["id"], [])
    ports.sort(key=lambda p: p.get("portIdentifierFormatted") or p.get("portIdentifier") or "")
    return {"takenAt": snap["takenAt"], "ports": ports}


@router.get("/{controller_id}/switches/{switch_id}/config")
async def switch_config(controller_id: int, switch_id: str,
                        venue_id: str = Query(...),
                        tenant_id: Optional[str] = Query(None),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """
    Redacted running config for ONE switch, fetched live for this request.

    Not cached and not stored: config is only ever read when a person asks for
    this specific switch. The text has passed icx_redact.assert_clean — a config
    the redactor cannot vouch for is reported as unavailable, never returned raw.
    """
    c = _controller(controller_id, db)
    override, _ = _resolve_tenant(c, tenant_id)

    r1 = create_r1_client_from_controller(controller_id, db)
    entry = fetch_redacted_config(r1, override, venue_id, switch_id)
    if entry is None:
        raise HTTPException(404, "This switch has no configuration backup in RUCKUS ONE. "
                                 "WiredWiz is read-only and will not create one.")
    if entry.get("dropped"):
        raise HTTPException(422, f"Redaction gate rejected this config: "
                                 f"{entry['leftoverCount']} lines still look like live "
                                 f"secrets. Refusing to return it.")
    return entry


@router.get("/{controller_id}/macTables")
async def mac_tables(controller_id: int,
                     tenant_id: Optional[str] = Query(None),
                     venue_ids: Optional[str] = Query(None),
                     limit: int = Query(12, le=48),
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """
    MAC address table size for every switch, from the stored snapshots.

    No API calls — this reads what the crawl already collected. Note there is no
    utilisation percentage: neither R1 nor the running config exposes the
    hardware table size, so sizing is peer-relative and growth-based. See
    services/wiredwiz/mactable.py for why.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    snaps = store.load_all(key, limit=limit)
    if not snaps:
        raise HTTPException(404, "No snapshots yet — run a crawl first.")
    snaps, scope = store.comparable(snaps, _venue_ids(venue_ids))
    result = summarize_mac_tables(snaps)
    result["scope"] = scope
    return result


@router.get("/{controller_id}/checks")
async def list_checks(controller_id: int,
                      tenant_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    The rule library itself, so the UI can show what is being checked — including
    the checks that will be skipped without config data. A check nobody knows
    exists is a check nobody trusts.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)

    # Fold in the last run so the catalogue shows what actually happened to each
    # check, not just that it exists.
    last = store.load_health(key) or {}
    ran = {x["checkId"]: x for x in last.get("checksRun", [])}
    skipped = {x["checkId"]: x for x in last.get("checksSkipped", [])}
    failed = {x["checkId"]: x for x in last.get("checksFailed", [])}

    out = []
    for chk in CHECK_REGISTRY:
        status, findings, note = "not run", None, None
        if chk.id in ran:
            status = "ran"
            findings = ran[chk.id]["findings"]
        elif chk.id in skipped:
            status = "skipped"
            note = skipped[chk.id].get("reason")
        elif chk.id in failed:
            status = "errored"
            note = failed[chk.id].get("error")
        out.append({
            "id": chk.id, "title": chk.title, "category": chk.category,
            "needs": chk.needs, "summary": chk.summary, "trigger": chk.trigger,
            "description": chk.description,
            "status": status, "findings": findings, "note": note,
        })

    by_cat: Dict[str, int] = {}
    for x in out:
        by_cat[x["category"]] = by_cat.get(x["category"], 0) + 1
    return {"checks": out, "total": len(out), "byCategory": by_cat,
            "lastRunAt": last.get("ranAt")}


@router.post("/{controller_id}/baseline")
async def create_baseline(controller_id: int,
                          tenant_id: Optional[str] = Query(None),
                          venue_ids: Optional[str] = Query(
                              None, description="Comma-separated venue ids to limit the "
                                                "config read. Omit for every online switch."),
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """
    Read every online switch's running config once, redact it, and store it as a
    baseline.

    This IS a bulk read — roughly one request per switch, ~30s for 200 — and it
    happens only because someone asked. Nothing schedules it and nothing
    refreshes it; to re-baseline, call it again.

    Configs are redacted and re-verified before being written. A config the
    redactor cannot vouch for is counted and skipped, never stored raw.
    """
    c = _controller(controller_id, db)
    override, key = _resolve_tenant(c, tenant_id)
    wanted = _venue_ids(venue_ids)

    snap, scope = store.load_covering(key, wanted)
    if not snap:
        raise HTTPException(404, "Crawl first — the baseline needs the switch inventory.")
    # Refuse rather than fall back here, unlike the read-only views. The fallback
    # snapshot covers whatever it happens to cover, so baselining against it would
    # spend a per-switch bulk read on the WRONG venues and store the result under
    # the scope the user asked for. 409, not 404: snapshots exist, they just do
    # not cover this selection.
    if scope.get("insufficientCoverage"):
        missing = (scope.get("excluded") or [{}])[0].get("missingVenueIds") or []
        raise HTTPException(409, "No crawl covers the selected venue(s), so there is no "
                                 "switch inventory to baseline against. Crawl at this "
                                 "scope first."
                                 + (f" Missing venue ids: {', '.join(missing[:5])}."
                                    if missing else ""))

    r1 = create_r1_client_from_controller(controller_id, db)
    # snap is already narrowed to `wanted` by load_covering.
    targets = [s for s in snap["switches"]
               if s.get("deviceStatus") == "ONLINE" and s.get("venueId")]
    if not targets:
        raise HTTPException(409, "The crawl covering this selection holds no ONLINE "
                                 "switches, so there is nothing to read a config from. "
                                 "Re-crawl and check the switches are online.")

    configs, no_backup, rejected = {}, 0, []
    for sw in targets:
        entry = fetch_redacted_config(r1, override, sw["venueId"], sw["id"],
                                      sw.get("name"), sw.get("model"))
        if entry is None:
            no_backup += 1
            continue
        if entry.get("dropped"):
            rejected.append(sw.get("name") or sw["id"])
            continue
        configs[sw["id"]] = entry

    baseline = {
        "takenAt": datetime.now(timezone.utc).isoformat(),
        "takenAtEpoch": time.time(),
        "tenantId": key,
        "scopeVenueIds": wanted,
        "configs": configs,
        "noBackup": no_backup,
        "rejectedByRedaction": len(rejected),
        "rejectedSwitches": rejected,
    }
    filename = store.save_baseline(key, baseline)
    logger.info("wiredwiz: baseline %s stored — %d configs, %d without backup, %d rejected",
                filename, len(configs), no_backup, len(rejected))
    return {
        "file": filename, "takenAt": baseline["takenAt"],
        "switchesTargeted": len(targets), "configsStored": len(configs),
        "noBackup": no_backup, "rejectedByRedaction": len(rejected),
        "rejectedSwitches": rejected, "scope": scope,
    }


@router.get("/{controller_id}/baselines")
async def list_baselines(controller_id: int,
                         tenant_id: Optional[str] = Query(None),
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    return {"baselines": store.list_baselines(key),
            "ttlDays": store.BASELINE_TTL_DAYS}


@router.delete("/{controller_id}/baselines/{file}")
async def delete_baseline(controller_id: int, file: str,
                          tenant_id: Optional[str] = Query(None),
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    if not store.delete_baseline(key, file):
        raise HTTPException(404, f"No baseline named {file}")
    return {"deleted": file}


@router.post("/{controller_id}/health")
async def health(controller_id: int,
                 tenant_id: Optional[str] = Query(None),
                 audit_configs: bool = Query(
                     False,
                     description="Re-read every online switch's config live for this run. "
                                 "An explicit bulk read (~30s for 200 switches). If a "
                                 "baseline exists, the fresh read is diffed against it to "
                                 "show config drift. Nothing is scheduled."),
                 use_baseline: bool = Query(
                     True,
                     description="Use the stored config baseline as the config source when "
                                 "not re-reading live. Costs no API calls."),
                 venue_ids: Optional[str] = Query(
                     None, description="Comma-separated venue ids to scope the analysis "
                                       "and any config read."),
                 min_window: int = Query(analysis.MIN_WINDOW),
                 limit: int = Query(12, le=48),
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """
    Run the check library over the stored snapshots.

    Metric, topology and stability checks run off snapshots alone. The
    loop-containment, forensics and hygiene checks need running configs, which
    is why `audit_configs` exists and defaults to off: reading config for the
    whole estate is a deliberate act, not something a health view does behind
    your back. When it is off, those checks are reported as SKIPPED rather than
    silently omitted.

    Configs read for an audit are held in memory for the duration of the request
    and never written to the snapshot store.
    """
    c = _controller(controller_id, db)
    override, key = _resolve_tenant(c, tenant_id)

    snaps = store.load_all(key, limit=limit)
    if not snaps:
        raise HTTPException(404, "No snapshots yet — run a crawl first.")
    wanted = _venue_ids(venue_ids)
    snaps, scope = store.comparable(snaps, wanted)
    if not any(s.get("ports") for s in snaps):
        raise HTTPException(404, "No crawled data for the selected venues.")

    baseline = store.load_baseline(key) if use_baseline else None

    configs: Dict[str, Any] = {}
    audit_meta = {"liveRead": audit_configs, "switchesRead": 0, "noBackup": 0,
                  "rejectedByRedaction": 0, "venueIds": wanted,
                  "baselineTakenAt": (baseline or {}).get("takenAt"),
                  "baselineSwitches": len((baseline or {}).get("configs") or {}),
                  "source": None}
    if audit_configs:
        r1 = create_r1_client_from_controller(controller_id, db)
        latest = snaps[-1]
        targets = [s for s in latest["switches"]
                   if s.get("deviceStatus") == "ONLINE" and s.get("venueId")
                   and (not wanted or s.get("venueId") in wanted)]
        for sw in targets:
            entry = fetch_redacted_config(r1, override, sw["venueId"], sw["id"],
                                          sw.get("name"), sw.get("model"))
            if entry is None:
                audit_meta["noBackup"] += 1
                continue
            if entry.get("dropped"):
                # Fail closed: a config the redactor cannot vouch for is not
                # analysed either, because the checks quote config lines back.
                audit_meta["rejectedByRedaction"] += 1
                continue
            configs[sw["id"]] = entry
            audit_meta["switchesRead"] += 1
        logger.info("wiredwiz health: live config read %d/%d switches",
                    audit_meta["switchesRead"], len(targets))
        audit_meta["source"] = "live"
    elif baseline and baseline.get("configs"):
        # A baseline may be wider than the selected venues; restrict it so config
        # findings match the scope the rest of the report is using.
        if wanted:
            in_scope = {s["id"] for s in snaps[-1]["switches"]}
            baseline = dict(baseline)
            baseline["configs"] = {k: v for k, v in baseline["configs"].items()
                                   if k in in_scope}
        # No API calls: analyse the stored baseline. Its age is reported so a
        # stale baseline cannot be mistaken for current state.
        configs = baseline["configs"]
        audit_meta["source"] = "baseline"
        audit_meta["switchesRead"] = len(configs)

    result = run_health_check(snaps, configs=configs, min_window=min_window,
                              # Drift only means anything when the configs being
                              # checked were read fresh -- diffing a baseline
                              # against itself is noise.
                              baseline=baseline if audit_configs else None)
    result["configAudit"] = audit_meta
    result["scope"] = scope
    result["ranAt"] = datetime.now(timezone.utc).isoformat()

    # Persist so the findings survive a reload. This stores a RESULT; it does
    # not schedule anything and does not re-run on its own.
    store.save_health(key, result)
    return result


@router.get("/{controller_id}/health")
async def last_health(controller_id: int,
                      tenant_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    The most recent check result, as stored — nothing is recomputed here.

    This exists so the dashboard can show findings on load instead of a blank
    page. `ranAt` is returned so a stale result cannot pass as current.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    result = store.load_health(key)
    if not result:
        raise HTTPException(404, "No check run stored yet — run the checks first.")
    return result


# ── Export ───────────────────────────────────────────────────────────────────
# Exports read the STORED result and re-run nothing. The PDF therefore matches
# exactly what was on screen, costs no API calls, and cannot quietly differ from
# the findings the user was looking at when they hit export.

def _export_name(c: Controller, tenant_key: str, ext: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", (c.name or "controller")).strip("-")
    return f"wiredwiz-{label}-{tenant_key[:8]}-{stamp}.{ext}"


def _tenant_label(c: Controller, tenant_id: Optional[str], label: Optional[str]) -> str:
    """
    Human name for the tenant being reported on.

    On an MSP controller the tenant id is a 32-character hex string, which makes a
    poor report title and a worse page footer. The UI knows the EC's name, so it
    passes it in; fall back to the id only when it does not.
    """
    if label:
        return label.strip()[:80]
    if c.controller_subtype != "MSP":
        return c.name
    return tenant_id or c.name


@router.get("/{controller_id}/report.pdf")
async def report_pdf(controller_id: int,
                     tenant_id: Optional[str] = Query(None),
                     label: Optional[str] = Query(
                         None, description="Human name for the tenant, used in the report "
                                           "title and footer. The MSP tenant id is a hex "
                                           "string and reads badly on a shared document."),
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """
    Findings and the full check catalogue as a PDF.

    Built from the last stored check run — nothing is recomputed and no API call
    is made, so the export always matches what was on screen. Includes the
    catalogue (what was tested and the exact condition each check fires on), the
    skipped checks, and the standing caveats about the underlying data.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)

    try:
        context = build_report_context(key, _tenant_label(c, tenant_id, label), c.name)
    except ValueError as e:
        raise HTTPException(404, str(e))

    template = _jinja().get_template("reports/wiredwiz.html")
    pdf = WeasyHTML(string=template.render(**context)).write_pdf()
    logger.info("wiredwiz: exported PDF for tenant=%s (%d findings, %d bytes)",
                key, context["total_findings"], len(pdf))
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{_export_name(c, key, "pdf")}"'},
    )


@router.get("/{controller_id}/report.csv")
async def report_csv(controller_id: int,
                     tenant_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Findings as CSV — for pasting into a tracker or sorting by switch."""
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    try:
        csv_text = findings_csv(key)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(
        content=csv_text, media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{_export_name(c, key, "csv")}"'},
    )


@router.get("/{controller_id}/report.json")
async def report_json(controller_id: int,
                      tenant_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    The stored check result verbatim — every finding with full evidence, the
    checks that ran, the ones that were skipped and why, and the scope.
    """
    c = _controller(controller_id, db)
    _, key = _resolve_tenant(c, tenant_id)
    result = store.load_health(key)
    if not result:
        raise HTTPException(404, "No check run stored yet — run the checks before exporting.")
    return Response(
        content=json.dumps(result, indent=2), media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="{_export_name(c, key, "json")}"'},
    )
