"""
Snapshot storage for Topology.

Flat JSON on disk, namespaced by tenant then by SCOPE. A scope is a sorted set
of venue ids, and its identity is EXACT: a topology of {A,B} is not a subset of
one of {A,B,C}, because the links between venues differ. This is a deliberate
divergence from WiredWiz's `load_covering()`, which answers "the newest snapshot
that COVERS the request, narrowed" -- correct for per-port analysis, wrong for a
graph. A near-miss is offered to the caller, never silently substituted.

Each snapshot is a DIRECTORY, not a file. A 200-switch venue is roughly 6 MB of
ports and 5 MB of links-with-evidence; reading 11 MB to draw a graph would
defeat the point of fetching ports and evidence on demand. Splitting lets
/graph read ~3 MB while the port strip and the evidence panel each read one
slice.

Snapshots expire. `overrides.json` and `layout.json` DO NOT -- they are user
intent, not derived data. Someone who confirmed forty links six months ago has
not consented to losing that work because the crawl that produced them aged out.
That guarantee is enforced structurally: every sweep in this module passes the
literal pattern "snap_*", and nothing here ever calls sweep_by_age with a wider
one. See test_topology_ttl.py.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.storekit import read_json, safe_component, sweep_by_age, write_json_atomic

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("TOPOLOGY_DATA_DIR", "/app/topology_data"))

# Snapshots are only ever written by an explicit request, so these are storage
# ceilings, not a schedule. Age is enforced on READ as well as write: retention
# that fires only on write keeps data forever the moment someone stops crawling.
SNAPSHOT_TTL_DAYS = float(os.environ.get("TOPOLOGY_SNAPSHOT_TTL_DAYS", "14"))
MAX_SNAPSHOTS = int(os.environ.get("TOPOLOGY_MAX_SNAPSHOTS", "24"))

# The one glob this module may sweep with. Named so its role is unmissable.
SNAPSHOT_GLOB = "snap_*"

# Files that must outlive every snapshot beside them.
PROTECTED = ("overrides.json", "layout.json", "scopes.json")

_PARTS = ("meta", "devices", "ports", "links", "evidence", "wan")


def scope_key(venue_ids) -> str:
    """
    Stable id for a venue SET. Hashed because a 50-venue path would blow past
    filename limits -- but the hash is never the only record: scopes.json maps
    it back, and every meta.json and overrides.json embeds venueIds verbatim,
    so a lost index is recoverable by scanning.
    """
    ids = sorted({str(v) for v in (venue_ids or []) if v})
    if not ids:
        return "vempty"
    return "v" + hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def _tenant_dir(tenant_key: str) -> Path:
    return DATA_DIR / safe_component(tenant_key)


def _scope_dir(tenant_key: str, venue_ids) -> Path:
    return _tenant_dir(tenant_key) / safe_component(scope_key(venue_ids), 32)


def _snapshot_dirs(scope_dir: Path) -> List[Path]:
    """
    Surviving snapshot DIRECTORIES, oldest first, enforcing BOTH ceilings.

    TTL bounds how long a snapshot is kept; MAX_SNAPSHOTS bounds how many. Both
    are applied here rather than on write, for the same reason WiredWiz sweeps
    on read: retention that only fires when something is written stops running
    the moment someone stops using the tool, which is exactly the case worth
    protecting against.

    The glob is hardcoded to SNAPSHOT_GLOB. overrides.json and layout.json sit
    in this same directory and are user intent -- a wider pattern here would
    delete them, and no care taken elsewhere would compensate.
    """
    alive = sweep_by_age(scope_dir, SNAPSHOT_GLOB, SNAPSHOT_TTL_DAYS,
                         kind="dir", label="topology")
    if MAX_SNAPSHOTS and len(alive) > MAX_SNAPSHOTS:
        for stale in alive[:-MAX_SNAPSHOTS]:
            _remove(stale)
        alive = alive[-MAX_SNAPSHOTS:]
    return alive


def _remove(snap_dir: Path) -> bool:
    """Delete one snapshot directory. One stubborn entry never fails a listing."""
    try:
        for child in snap_dir.iterdir():
            child.unlink()
        snap_dir.rmdir()
        logger.info("topology: removed %s", snap_dir.name)
        return True
    except OSError:
        logger.warning("topology: could not remove %s", snap_dir)
        return False


# ── scope index ─────────────────────────────────────────────────────────────

def _scopes_path(tenant_key: str) -> Path:
    return _tenant_dir(tenant_key) / "scopes.json"


def register_scope(tenant_key: str, venue_ids, venue_names: Dict[str, str]) -> None:
    """Remember what a scope hash means, so the UI can name it."""
    path = _scopes_path(tenant_key)
    index = read_json(path) or {}
    if not isinstance(index, dict):
        index = {}
    key = scope_key(venue_ids)
    index[key] = {"venueIds": sorted({str(v) for v in venue_ids if v}),
                  "venueNames": venue_names or {}, "lastSeen": time.time()}
    write_json_atomic(path, index)


def list_scopes(tenant_key: str) -> List[Dict[str, Any]]:
    index = read_json(_scopes_path(tenant_key)) or {}
    if not isinstance(index, dict):
        return []
    out = []
    for key, entry in index.items():
        if isinstance(entry, dict):
            out.append({"scopeKey": key, **entry})
    return sorted(out, key=lambda e: -(e.get("lastSeen") or 0))


def near_misses(tenant_key: str, venue_ids) -> List[Dict[str, Any]]:
    """
    Other stored scopes that overlap this request.

    Offered to the caller as a suggestion ("you have a snapshot of A,B,C --
    open that instead?"), never substituted. Substituting would hand back a
    graph whose inter-venue links answer a different question.
    """
    wanted = {str(v) for v in venue_ids if v}
    if not wanted:
        return []
    out = []
    for entry in list_scopes(tenant_key):
        stored = set(entry.get("venueIds") or [])
        if not stored or stored == wanted:
            continue
        overlap = stored & wanted
        if overlap:
            out.append({**entry, "overlap": sorted(overlap),
                        "covers": wanted.issubset(stored),
                        "missing": sorted(wanted - stored)})
    return sorted(out, key=lambda e: (-len(e["overlap"]), len(e.get("venueIds") or [])))


# ── snapshots ───────────────────────────────────────────────────────────────

def save(tenant_key: str, snapshot) -> str:
    """
    Persist one snapshot as a directory of parts. `meta.json` is written LAST so
    the directory's mtime -- which the TTL sweep ages off -- reflects a complete
    write, and a crashed run cannot leave a snapshot that looks fresh.
    """
    scope_dir = _scope_dir(tenant_key, snapshot.scope_venue_ids)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(snapshot.taken_at_epoch))
    snap_dir = scope_dir / f"snap_{stamp}"
    snap_dir.mkdir(parents=True, exist_ok=True)

    write_json_atomic(snap_dir / "devices.json",
                      [d.to_dict() for d in snapshot.devices])
    write_json_atomic(snap_dir / "ports.json",
                      [p.to_dict() for p in snapshot.ports])
    write_json_atomic(snap_dir / "links.json",
                      [l.to_dict(with_evidence=False) for l in snapshot.links])
    write_json_atomic(snap_dir / "evidence.json",
                      {l.id: [e.to_dict() for e in l.evidence]
                       for l in snapshot.links if l.evidence})
    write_json_atomic(snap_dir / "wan.json", getattr(snapshot, "wan", None) or {})
    write_json_atomic(snap_dir / "meta.json", snapshot.meta())

    register_scope(tenant_key, snapshot.scope_venue_ids, snapshot.venues)
    _snapshot_dirs(scope_dir)               # applies both ceilings
    return snap_dir.name


def list_snapshots(tenant_key: str, venue_ids) -> List[Dict[str, Any]]:
    """Newest first, with the metadata the picker needs. Sweeps on read."""
    scope_dir = _scope_dir(tenant_key, venue_ids)
    out = []
    for snap_dir in reversed(_snapshot_dirs(scope_dir)):
        meta = read_json(snap_dir / "meta.json")
        if not isinstance(meta, dict):
            continue                        # a half-written or corrupt snapshot
        try:
            size = sum(f.stat().st_size for f in snap_dir.iterdir() if f.is_file())
            mtime = snap_dir.stat().st_mtime
        except OSError:
            continue
        out.append({
            "name": snap_dir.name,
            "takenAt": meta.get("takenAt"),
            "takenAtEpoch": meta.get("takenAtEpoch"),
            "counts": meta.get("counts") or {},
            "deep": meta.get("deep", False),
            "warnings": meta.get("warnings") or [],
            "sizeBytes": size,
            "expiresAtEpoch": mtime + SNAPSHOT_TTL_DAYS * 86400,
        })
    return out


def _resolve(tenant_key: str, venue_ids, name: Optional[str]) -> Optional[Path]:
    scope_dir = _scope_dir(tenant_key, venue_ids)
    dirs = _snapshot_dirs(scope_dir)
    if not dirs:
        return None
    if not name:
        return dirs[-1]                     # newest
    safe = safe_component(name, 64)
    for snap_dir in dirs:
        if snap_dir.name == safe:
            return snap_dir
    return None


def load_part(tenant_key: str, venue_ids, part: str,
              name: Optional[str] = None) -> Optional[Any]:
    """
    One slice of one snapshot. The whole reason snapshots are directories: the
    canvas reads devices+links, the port strip reads ports, and the evidence
    panel reads evidence -- each without paying for the others.
    """
    if part not in _PARTS:
        raise ValueError(f"unknown part {part!r}")
    snap_dir = _resolve(tenant_key, venue_ids, name)
    if snap_dir is None:
        return None
    return read_json(snap_dir / f"{part}.json")


def latest_name(tenant_key: str, venue_ids) -> Optional[str]:
    snap_dir = _resolve(tenant_key, venue_ids, None)
    return snap_dir.name if snap_dir else None


def delete(tenant_key: str, venue_ids, name: str) -> bool:
    snap_dir = _resolve(tenant_key, venue_ids, name)
    return _remove(snap_dir) if snap_dir is not None else False


# ── layout (user intent -- never swept) ─────────────────────────────────────

def load_layout(tenant_key: str, venue_ids) -> Dict[str, Any]:
    data = read_json(_scope_dir(tenant_key, venue_ids) / "layout.json")
    return data if isinstance(data, dict) else {}


def save_layout(tenant_key: str, venue_ids, layout: Dict[str, Any],
                expected_version: Optional[int] = None) -> Dict[str, Any]:
    """
    Persist a hand-arranged layout, with an optimistic version check.

    Last-writer-wins is wrong here: two tabs open on the same venue would
    silently discard whichever arrangement was saved first, and a layout is
    work someone did by hand. A stale write is refused and the caller is handed
    the current version to merge against.
    """
    path = _scope_dir(tenant_key, venue_ids) / "layout.json"
    current = read_json(path) or {}
    if not isinstance(current, dict):
        current = {}
    version = int(current.get("version") or 0)
    if expected_version is not None and expected_version != version:
        return {"ok": False, "conflict": True, "version": version, "current": current}
    merged = {**layout, "version": version + 1, "updatedAt": time.time(),
              "venueIds": sorted({str(v) for v in venue_ids if v})}
    write_json_atomic(path, merged)
    return {"ok": True, "version": merged["version"]}
