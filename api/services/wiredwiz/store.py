"""
Snapshot storage for WiredWiz.

Flat JSON files on disk, one per crawl, namespaced by tenant. Snapshots are
~10 MB for a 200-switch tenant and are pure analysis input, so a directory is
plenty -- no schema to migrate, and the CLI scripts and the API read the exact
same files.

Snapshots hold learned MAC addresses, client IPs and LLDP topology. They are
network-sensitive, so the directory is gitignored. Snapshots never contain device
configuration.

Configuration lives separately, as a BASELINE: an explicit, user-triggered bulk
read of every switch's running config, redacted and kept so later runs can be
compared against it. Baselines are created only when someone asks for one --
a crawl never writes one, and nothing refreshes one on a timer. Keeping them is
what makes config *drift* detectable at all.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(os.environ.get("WIREDWIZ_DATA_DIR", "/app/wiredwiz_data"))

# Keep the most recent N snapshots per tenant. Rates need only two; a longer tail
# is what lets a human compare across a working day. Snapshots are only ever
# created by an explicit request, so this is a storage ceiling, not a schedule.
MAX_PER_TENANT = 48

_SAFE = re.compile(r"[^A-Za-z0-9_-]")

# ── Age-based retention ──────────────────────────────────────────────────────
# MAX_PER_TENANT and MAX_BASELINES bound how MUCH is kept; these bound how LONG.
# Count caps alone meant a baseline survived until ten newer ones displaced it,
# which for occasional use is indefinitely -- and a baseline is redacted device
# configuration, the artefact here least worth keeping past its usefulness.
#
# Enforced on READ as well as on write, deliberately: retention that only runs
# when something is written keeps stale configs forever the moment someone stops
# crawling, which is precisely the case worth protecting against. A sweep is one
# stat() per file and never parses JSON.
SNAPSHOT_TTL_DAYS = float(os.environ.get("WIREDWIZ_SNAPSHOT_TTL_DAYS", "7"))
BASELINE_TTL_DAYS = float(os.environ.get("WIREDWIZ_BASELINE_TTL_DAYS", "7"))
HEALTH_TTL_DAYS = float(os.environ.get("WIREDWIZ_HEALTH_TTL_DAYS", "7"))


def _sweep(d: Path, pattern: str, ttl_days: float) -> List[Path]:
    """
    Delete everything in `d` matching `pattern` older than `ttl_days`, and return
    the survivors OLDEST FIRST.

    Ages off mtime, matching _snap_files: these files are written once and never
    rewritten, so mtime is creation time, and reading it costs no parse.
    """
    if not d.exists():
        return []
    cutoff = time.time() - ttl_days * 86400
    alive = []
    for f in d.glob(pattern):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue                       # vanished under us; not our problem
        if mtime >= cutoff:
            alive.append((mtime, f))
            continue
        try:
            f.unlink()
            logger.info("wiredwiz: expired %s (older than %g days)", f.name, ttl_days)
        except OSError:
            # A concurrent request may have swept it already. One unremovable
            # file must never take down the whole listing.
            logger.warning("could not expire %s", f)
    return [f for _, f in sorted(alive, key=lambda x: (x[0], x[1].name))]


def _tenant_dir(tenant_id: str) -> Path:
    return SNAPSHOT_DIR / _SAFE.sub("", tenant_id or "unknown")[:64]


def _snap_files(d: Path) -> List[Path]:
    """
    Every snapshot file for a tenant, OLDEST FIRST.

    Ordered by mtime, deliberately not by name. This directory holds two naming
    schemes -- `snap_<stamp>.json` written by the API and
    `snap_<tenantprefix>_<stamp>.json` written by the CLI script -- and
    lexicographically every `snap_0...` sorts ahead of every `snap_2...`, so a
    name sort can hand back a months-old CLI snapshot as "the newest one".
    Snapshots are written once and never rewritten, so mtime is creation time.

    Also expires anything past SNAPSHOT_TTL_DAYS -- see _sweep.
    """
    return _sweep(d, "snap_*.json", SNAPSHOT_TTL_DAYS)


def _read(path: Path) -> Optional[Dict[str, Any]]:
    """A snapshot that will not parse is skipped, never allowed to abort a listing."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        logger.warning("unreadable snapshot %s", path)
        return None


def _coverage(snap: Dict[str, Any]) -> set:
    """
    The venues a snapshot can actually answer for: its explicit crawl scope when
    it has one, otherwise whatever venues it happened to see.
    """
    explicit = snap.get("scopeVenueIds")
    return set(explicit) if explicit else set((snap.get("venues") or {}).keys())


def save(tenant_id: str, snapshot: Dict[str, Any]) -> str:
    d = _tenant_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["takenAt"].replace(":", "").replace("-", "").split(".")[0]
    path = d / f"snap_{stamp}.json"
    path.write_text(json.dumps(snapshot))
    _prune(d)
    return path.name


def _prune(d: Path):
    for old in _snap_files(d)[:-MAX_PER_TENANT]:
        try:
            old.unlink()
        except OSError:
            logger.warning("could not prune %s", old)


def list_snapshots(tenant_id: str) -> List[Dict[str, Any]]:
    """
    Metadata for every stored snapshot, newest last. Reads each file, so keep
    MAX_PER_TENANT modest; a summary index would be the next optimisation.
    """
    d = _tenant_dir(tenant_id)
    out = []
    for f in _snap_files(d):
        s = _read(f)
        if s is None:
            continue
        c = s.get("completeness") or {}
        out.append({
            "file": f.name,
            "takenAt": s.get("takenAt"),
            "takenAtEpoch": s.get("takenAtEpoch"),
            "switches": len(s.get("switches") or []),
            "ports": len(s.get("ports") or []),
            "macs": len(s.get("macs") or []),
            "complete": not c.get("incomplete"),
            "expected": c.get("expected"),
            "collected": c.get("collected"),
            "sizeBytes": f.stat().st_size,
            "expiresAtEpoch": f.stat().st_mtime + SNAPSHOT_TTL_DAYS * 86400,
        })
    return out


def load(tenant_id: str, file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    d = _tenant_dir(tenant_id)
    if file:
        p = d / Path(file).name          # never let a caller escape the directory
        return _read(p) if p.exists() else None
    files = _snap_files(d)
    return _read(files[-1]) if files else None


# Every load_covering() branch returns these keys, so a caller never has to tell
# "no coverage problem" apart from "the key was not set on this path".
_INFO_NONE = {"scoped": False, "venueCount": 0, "venueIds": [], "excluded": [],
              "reason": None, "insufficientCoverage": False}


def load_covering(tenant_id: str, venue_ids=None, scan: int = 12):
    """
    The newest snapshot that COVERS the requested venues, narrowed to them.

    `load()` returns the newest snapshot whatever it covers, and the detail
    endpoints then filtered that down to the venues being viewed. Crawl venue A,
    then open venue B, and you got a near-empty inventory with nothing saying
    why -- the numbers simply disagreed with the crawl you had just run, which
    reads as the tool being broken rather than as a scope mismatch.

    This is the single-snapshot sibling of comparable(), and it follows the same
    two rules: a snapshot that does not cover the request is passed over rather
    than intersected down, and when nothing covers the request we still return
    the best available and say so loudly instead of returning a thin slice
    silently.

    Only the most recent `scan` snapshots are considered -- if none of those
    covers the venue, the answer is "re-crawl at this scope", and reading the
    whole 48-deep tail (~10 MB each) to prove it is not worth the I/O.

    Returns (snapshot, info); `info` matches comparable()'s shape so the UI's
    ScopeNote renders it with no changes.
    """
    files = _snap_files(_tenant_dir(tenant_id))[-scan:]
    if not files:
        return None, _INFO_NONE.copy()

    target = set(venue_ids) if venue_ids else None
    considered = []                       # (snapshot, coverage), newest first

    for f in reversed(files):
        snap = _read(f)
        if snap is None:
            continue
        if target is None:
            # No scope asked for: newest readable snapshot, as-is.
            return snap, {"scoped": False, "venueCount": len(_coverage(snap)),
                          "venueIds": sorted(_coverage(snap)), "excluded": [],
                          "reason": None, "insufficientCoverage": False}
        cov = _coverage(snap)
        if target <= cov:
            narrowed = scope_snapshot(snap, target) if cov != target else snap
            return narrowed, {
                "scoped": cov != target,
                "venueCount": len(target),
                "venueIds": sorted(target),
                "excluded": [_excluded_entry(s, c, target) for s, c in considered],
                "reason": ("narrowed to the requested venues" if cov != target else None),
                "insufficientCoverage": False,
            }
        considered.append((snap, cov))

    if not considered:
        return None, _INFO_NONE.copy()

    # Nothing covers the request. Fall back to the widest overlap, flagged.
    best, cov = max(considered, key=lambda x: len(x[1] & target))
    shown = (cov & target) or cov
    return scope_snapshot(best, shown), {
        "scoped": True,
        "venueCount": len(shown),
        "venueIds": sorted(shown),
        "excluded": [_excluded_entry(s, c, target) for s, c in considered if s is not best],
        "reason": "no snapshot covers the requested venues; showing the closest "
                  "available crawl instead — re-crawl at this scope for accurate "
                  "results",
        "insufficientCoverage": True,
    }


def _excluded_entry(snap: Dict[str, Any], cov: set, target: set) -> Dict[str, Any]:
    return {"takenAt": snap.get("takenAt"),
            "covered": len(cov), "requested": len(target),
            "missingVenueIds": sorted(target - cov)[:10]}


def load_all(tenant_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Most recent `limit` snapshots, oldest first."""
    d = _tenant_dir(tenant_id)
    out = []
    for f in _snap_files(d)[-limit:]:
        s = _read(f)
        if s is not None:
            out.append(s)
    out.sort(key=lambda s: s.get("takenAtEpoch") or 0)
    return out


def delete(tenant_id: str, file: str) -> bool:
    p = _tenant_dir(tenant_id) / Path(file).name
    if p.exists():
        p.unlink()
        return True
    return False


# ── Config baselines ─────────────────────────────────────────────────────────
# A baseline is a point-in-time set of redacted running configs. It exists so
# that (a) config checks can run without re-reading the estate every time, and
# (b) a later read can be diffed against it to show what changed.

MAX_BASELINES = 10


def _baseline_dir(tenant_id: str) -> Path:
    return _tenant_dir(tenant_id) / "baselines"


def _baseline_files(tenant_id: str) -> List[Path]:
    """Surviving baselines, oldest first, expiring anything past BASELINE_TTL_DAYS."""
    return _sweep(_baseline_dir(tenant_id), "baseline_*.json", BASELINE_TTL_DAYS)


def save_baseline(tenant_id: str, baseline: Dict[str, Any]) -> str:
    d = _baseline_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    stamp = baseline["takenAt"].replace(":", "").replace("-", "").split(".")[0]
    path = d / f"baseline_{stamp}.json"
    path.write_text(json.dumps(baseline))
    # Sweep by age first, then apply the count cap to whatever is left.
    for old in _baseline_files(tenant_id)[:-MAX_BASELINES]:
        try:
            old.unlink()
        except OSError:
            logger.warning("could not prune %s", old)
    return path.name


def list_baselines(tenant_id: str) -> List[Dict[str, Any]]:
    out = []
    for f in _baseline_files(tenant_id):
        b = _read(f)
        if b is None:
            continue
        out.append({
            "expiresAtEpoch": f.stat().st_mtime + BASELINE_TTL_DAYS * 86400,
            "file": f.name,
            "takenAt": b.get("takenAt"),
            "takenAtEpoch": b.get("takenAtEpoch"),
            "switches": len(b.get("configs") or {}),
            "noBackup": b.get("noBackup", 0),
            "rejectedByRedaction": b.get("rejectedByRedaction", 0),
            "sizeBytes": f.stat().st_size,
        })
    return out


def load_baseline(tenant_id: str, file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Newest baseline unless a specific file is named.

    Expired baselines are swept before the lookup, so a named file that has aged
    out reads as absent rather than being resurrected by name.
    """
    files = _baseline_files(tenant_id)
    if file:
        wanted = Path(file).name          # never let a caller escape the directory
        match = next((f for f in files if f.name == wanted), None)
        return _read(match) if match else None
    return _read(files[-1]) if files else None


def delete_baseline(tenant_id: str, file: str) -> bool:
    p = _tenant_dir(tenant_id) / "baselines" / Path(file).name
    if p.exists():
        p.unlink()
        return True
    return False


# ── Last health-check result ─────────────────────────────────────────────────
# Running the checks is a deliberate act, but the RESULT should survive a page
# reload -- otherwise findings live only in browser state and vanish the moment
# you switch tabs, which makes the tool feel broken even when it is working.
# Storing the result is not the same as re-running it: nothing here re-computes
# anything on its own.

def save_health(tenant_id: str, result: Dict[str, Any]) -> None:
    d = _tenant_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps(result))


def load_health(tenant_id: str) -> Optional[Dict[str, Any]]:
    """
    The last stored check result, if it has not aged out.

    Subject to the same TTL as the rest: findings quote VLAN ids, spanning-tree
    state and remediation commands derived from device configuration, so this is
    not the innocuous leftover it looks like.
    """
    files = _sweep(_tenant_dir(tenant_id), "health.json", HEALTH_TTL_DAYS)
    return _read(files[0]) if files else None


def scope_snapshot(snapshot: Dict[str, Any], venue_ids) -> Dict[str, Any]:
    """
    Restrict a snapshot to a set of venues.

    Used so a wide snapshot can be analysed at a narrower scope without
    re-crawling. Returns a shallow copy -- the original is left intact.
    """
    if not venue_ids:
        return snapshot
    wanted = set(venue_ids)
    out = dict(snapshot)
    out["switches"] = [x for x in snapshot.get("switches", []) if x.get("venueId") in wanted]
    out["ports"] = [x for x in snapshot.get("ports", []) if x.get("venueId") in wanted]
    out["macs"] = [x for x in snapshot.get("macs", []) if x.get("venueId") in wanted]
    out["venues"] = {k: v for k, v in (snapshot.get("venues") or {}).items() if k in wanted}
    out["scopedTo"] = sorted(wanted)
    return out


def comparable(snapshots: List[Dict[str, Any]], venue_ids=None):
    """
    Prepare a list of snapshots for differencing at a requested venue scope.

    Snapshots may have been taken at different scopes. Two rules, and the second
    one matters more than it first appears:

    1. A snapshot that does NOT cover the requested scope is EXCLUDED, not
       intersected down. Intersecting instead would silently shrink the whole
       view to whatever the narrowest crawl happened to cover -- crawl one venue,
       and the tenant-wide report quietly becomes that one venue.
    2. Snapshots that do cover it are narrowed to exactly the requested scope, so
       venues outside the selection never influence a rate or a finding.

    When no scope is requested the target is the widest coverage seen, i.e. the
    tenant as last fully crawled.

    Returns (snapshots, info). `info.excluded` lists snapshots dropped for
    insufficient coverage, so a stale-looking result is always explained.
    """
    if not snapshots:
        return [], {"scoped": False, "venueCount": 0, "excluded": []}

    coverage = _coverage
    covers = [(s, coverage(s)) for s in snapshots]
    target = set(venue_ids) if venue_ids else set().union(*(c for _, c in covers))

    kept, excluded = [], []
    for s, cov in covers:
        if target <= cov:
            kept.append(s)
        else:
            excluded.append({"takenAt": s.get("takenAt"),
                             "covered": len(cov), "requested": len(target),
                             "missingVenueIds": sorted(target - cov)[:10]})

    if not kept:
        # Nothing covers the request. Fall back to what IS available rather than
        # returning nothing, and say so loudly.
        widest, cov = max(covers, key=lambda x: len(x[1]))
        return [scope_snapshot(widest, cov & target or cov)], {
            "scoped": True, "venueCount": len(cov & target or cov),
            "venueIds": sorted(cov & target or cov), "excluded": excluded,
            "reason": "no snapshot covers the requested venues; showing the widest "
                      "available crawl instead — re-crawl at this scope for accurate "
                      "results",
            "insufficientCoverage": True,
        }

    needs_narrowing = any(coverage(s) != target for s in kept)
    prepared = [scope_snapshot(s, target) for s in kept] if needs_narrowing else kept

    reason = None
    if excluded:
        reason = (f"{len(excluded)} snapshot(s) excluded: they cover fewer venues than "
                  "requested, and differencing them would read the missing venues as "
                  "ports disappearing from the network")
    elif needs_narrowing:
        reason = "narrowed to the requested venues"

    return prepared, {
        "scoped": needs_narrowing or bool(excluded),
        "venueCount": len(target),
        "venueIds": sorted(target),
        "excluded": excluded,
        "reason": reason,
        "insufficientCoverage": False,
    }
