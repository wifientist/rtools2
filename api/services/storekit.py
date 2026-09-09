"""
Shared primitives for the flat-file tool stores (WiredWiz, Topology).

Deliberately small. Only the genuinely subtle, security-relevant logic lives
here -- the age sweep, whose semantics are easy to get wrong in ways nobody
notices: it must tolerate files vanishing under it, must never let one
unremovable file take down a listing, and must run on READ as well as write,
because retention that only fires when something is written keeps sensitive
data forever the moment someone stops using the tool.

Everything schema-shaped stays in the calling tool's own store module. The two
tools disagree about what a snapshot IS and about scope identity, and a shared
abstraction parameterised on both would be more code and more risk than either
caller has.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_component(value: str, maxlen: int = 64) -> str:
    """One path segment from untrusted input. Never empty, never traversing."""
    cleaned = _SAFE.sub("", value or "")[:maxlen]
    return cleaned or "unknown"


def sweep_by_age(directory: Path, pattern: str, ttl_days: float,
                 kind: str = "file", label: str = "store") -> List[Path]:
    """
    Delete entries in `directory` matching `pattern` older than `ttl_days`;
    return the survivors OLDEST FIRST.

    Ages off mtime: these entries are written once and never rewritten, so mtime
    is creation time and reading it costs no parse.

    `pattern` is the caller's whole safety contract. A store that keeps user
    intent (confirmed links, pinned uplinks, hand-arranged layouts) beside its
    derived snapshots MUST pass a pattern that cannot match those files. Passing
    "*" here would delete them, and no amount of care elsewhere would save it.
    """
    if not directory.exists():
        return []
    cutoff = time.time() - ttl_days * 86400
    alive = []
    for entry in directory.glob(pattern):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue                        # vanished under us; not our problem
        if mtime >= cutoff:
            alive.append((mtime, entry))
            continue
        try:
            if entry.is_dir():
                _rmtree(entry)
            else:
                entry.unlink()
            logger.info("%s: expired %s (older than %g days)", label, entry.name, ttl_days)
        except OSError:
            # A concurrent request may have swept it already. One unremovable
            # entry must never take down the whole listing.
            logger.warning("%s: could not expire %s", label, entry)
    if kind == "dir":
        alive = [(m, e) for m, e in alive if e.is_dir()]
    return [entry for _, entry in sorted(alive, key=lambda pair: (pair[0], pair[1].name))]


def _rmtree(directory: Path) -> None:
    """Depth-first delete. Only ever called on a path a glob already matched."""
    for child in directory.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    directory.rmdir()


def read_json(path: Path) -> Optional[Any]:
    """
    Parse `path`, or None.

    A missing file is the ordinary case -- an index that has not been written
    yet, a scope with no overrides -- and is silent. A file that exists but does
    not parse is a real problem worth a log line, and is still skipped rather
    than raised: one corrupt snapshot must not fail a whole listing.
    """
    try:
        with path.open() as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("unreadable %s: %s", path, exc)
        return None


def write_json_atomic(path: Path, obj: Any) -> None:
    """
    Write via a temp file in the same directory, then rename.

    os.replace is atomic within a filesystem, so a reader either sees the whole
    previous version or the whole new one -- never a half-written file. Matters
    most for the small files that are read constantly and rewritten in place
    (overrides, layouts), where a truncated write loses user work rather than
    just one regenerable snapshot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w") as handle:
            json.dump(obj, handle, default=str)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass                            # already renamed, or never created
