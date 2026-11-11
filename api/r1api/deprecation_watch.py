"""
What R1 itself says about the routes we call, read off responses we already get.

R1 marks a deprecated route on every response with a `ruckus-deprecated`
header naming the replacement (GET /entitlements -> "/entitlements/query"),
and a retired one answers 405 (GET /venues, 2026-11-11). Nothing here sends a
request: call_metrics.record_call hands each response over, and the signal is
kept per route in one Redis hash for the deprecation audit to read.

Like call_metrics, nothing here may affect a real call: every entry point
swallows its errors.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

SEEN_KEY = "r1:deprecations:seen"
RETENTION_SECONDS = 90 * 24 * 3600
# A busy route sends its header on every call; Redis hears about it this often.
WRITE_EVERY_SECONDS = 300
GONE_STATUSES = (405, 410)

_lock = threading.Lock()
# label -> {"count": calls since the last write, "written": monotonic time}
_pending: Dict[str, Dict[str, Any]] = {}


def _signal(response) -> Dict[str, Any]:
    """The deprecation signal in one response, or {} when it carries none."""
    headers = response.headers
    deprecated = headers.get("ruckus-deprecated")
    if deprecated and "://" in deprecated:
        # Some come as a full URL (https://api.ruckus.cloud/brandings); keep the path.
        deprecated = "/" + deprecated.split("://", 1)[1].split("/", 1)[-1]
    sunset = headers.get("Sunset")
    deprecation = headers.get("Deprecation")
    gone = response.status_code in GONE_STATUSES
    current = headers.get("ruckus-current-version")
    sent = set()
    request = getattr(response, "request", None)
    if request is not None:
        for name in ("Accept", "Content-Type"):
            value = request.headers.get(name)
            if value:
                sent.add(value.split(";")[0].strip().lower())
    # R1 names its current media type on most routes. It matters only when
    # that is newer than what plain JSON gets today (v1): then plain JSON can
    # switch shape under us, as /entitlements/compliances/query did. A
    # "current is v1" notice, or one naming what we asked for, is not kept.
    plain = {"*/*", "application/json"}
    newer_version = None
    if current and current.lower() not in sent - plain:
        if current.lower() not in ("application/json", "application/vnd.ruckus.v1+json"):
            newer_version = current
    if not (deprecated or sunset or deprecation or gone or newer_version):
        return {}
    return {
        "replacement": deprecated,
        "sunset": sunset,
        "deprecation": deprecation,
        "gone_status": response.status_code if gone else None,
        "allow": headers.get("Allow") if gone else None,
        "current_version": newer_version,
        "sent_media_types": sorted(sent),
    }


def observe(label: str, response) -> None:
    """Note what R1 said about this route. `label` is "METHOD /route/{id}"."""
    try:
        signal = _signal(response)
        if not signal:
            return
        now = time.monotonic()
        with _lock:
            entry = _pending.setdefault(label, {"count": 0, "written": 0.0})
            entry["count"] += 1
            if now - entry["written"] < WRITE_EVERY_SECONDS:
                return
            count, entry["count"], entry["written"] = entry["count"], 0, now
        _write(label, signal, count)
    except Exception as e:
        logger.debug(f"R1 deprecation signal dropped: {e}")


def _write(label: str, signal: Dict[str, Any], count: int) -> None:
    from r1api.call_metrics import _redis

    client = _redis()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous = client.hget(SEEN_KEY, label)
    row = json.loads(previous) if previous else {"first_seen": stamp, "count": 0}
    row.update({k: v for k, v in signal.items() if v is not None})
    row["count"] = int(row.get("count", 0)) + count
    row["last_seen"] = stamp
    client.hset(SEEN_KEY, label, json.dumps(row))
    client.expire(SEEN_KEY, RETENTION_SECONDS)
    if not previous:
        logger.warning(f"[r1-deprecation] R1 flags {label}: {signal}")


def read_seen() -> Dict[str, Dict[str, Any]]:
    """Every route R1 has flagged in the last RETENTION_SECONDS."""
    from r1api.call_metrics import _redis

    rows = {}
    for label, value in (_redis().hgetall(SEEN_KEY) or {}).items():
        try:
            rows[label] = json.loads(value)
        except ValueError:
            continue
    return rows
