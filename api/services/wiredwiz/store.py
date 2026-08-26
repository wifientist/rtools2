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
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(os.environ.get("WIREDWIZ_DATA_DIR", "/app/wiredwiz_data"))

# Keep the most recent N snapshots per tenant. Rates need only two; a longer tail
# is what lets a human compare across a working day. Snapshots are only ever
# created by an explicit request, so this is a storage ceiling, not a schedule.
MAX_PER_TENANT = 48

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _tenant_dir(tenant_id: str) -> Path:
    return SNAPSHOT_DIR / _SAFE.sub("", tenant_id or "unknown")[:64]


def save(tenant_id: str, snapshot: Dict[str, Any]) -> str:
    d = _tenant_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["takenAt"].replace(":", "").replace("-", "").split(".")[0]
    path = d / f"snap_{stamp}.json"
    path.write_text(json.dumps(snapshot))
    _prune(d)
    return path.name


def _prune(d: Path):
    files = sorted(d.glob("snap_*.json"))
    for old in files[:-MAX_PER_TENANT]:
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
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("snap_*.json")):
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
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
        })
    return out


def load(tenant_id: str, file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    d = _tenant_dir(tenant_id)
    if file:
        p = d / Path(file).name          # never let a caller escape the directory
        return json.loads(p.read_text()) if p.exists() else None
    files = sorted(d.glob("snap_*.json"))
    return json.loads(files[-1].read_text()) if files else None


def load_all(tenant_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Most recent `limit` snapshots, oldest first."""
    d = _tenant_dir(tenant_id)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("snap_*.json"))[-limit:]:
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
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


def save_baseline(tenant_id: str, baseline: Dict[str, Any]) -> str:
    d = _tenant_dir(tenant_id) / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    stamp = baseline["takenAt"].replace(":", "").replace("-", "").split(".")[0]
    path = d / f"baseline_{stamp}.json"
    path.write_text(json.dumps(baseline))
    for old in sorted(d.glob("baseline_*.json"))[:-MAX_BASELINES]:
        try:
            old.unlink()
        except OSError:
            logger.warning("could not prune %s", old)
    return path.name


def list_baselines(tenant_id: str) -> List[Dict[str, Any]]:
    d = _tenant_dir(tenant_id) / "baselines"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("baseline_*.json")):
        try:
            b = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out.append({
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
    """Newest baseline unless a specific file is named."""
    d = _tenant_dir(tenant_id) / "baselines"
    if not d.exists():
        return None
    if file:
        p = d / Path(file).name
        return json.loads(p.read_text()) if p.exists() else None
    files = sorted(d.glob("baseline_*.json"))
    return json.loads(files[-1].read_text()) if files else None


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
    p = _tenant_dir(tenant_id) / "health.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


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

    def coverage(s):
        explicit = s.get("scopeVenueIds")
        return set(explicit) if explicit else set((s.get("venues") or {}).keys())

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
