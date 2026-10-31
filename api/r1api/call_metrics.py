"""
How long R1 calls take, kept as hourly totals in Redis.

Every call through R1Client lands here, grouped by method and route with IDs
stripped, so 2,000 policy IDs are one row. The figure that matters most is
async: seconds from R1's 202 until the activity's endDatetime. Those come from
reading the activity responses the pollers already fetch, so measuring adds no
R1 calls, and a 202 nobody polls is never measured. The same read also gives
the wait a job really sits through: the 202 until a poll first shows it done,
which R1's endDatetime can be well ahead of.

Nothing here may affect a real call: every entry point swallows its errors.
Totals sit in memory and are written every FLUSH_SECONDS, so a restart loses at
most that much.
"""

import atexit
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "r1:call_metrics:"
RETENTION_SECONDS = 30 * 24 * 3600
FLUSH_SECONDS = 30
# Upper bound of each time bucket, in seconds. One more bucket catches the rest.
BUCKET_BOUNDS = (0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300)
# A route pattern that escapes the ID stripping must not grow Redis without end.
MAX_ROWS_PER_HOUR = 1000
MAX_TRACKED_REQUESTS = 50000
TRACKED_REQUEST_SECONDS = 3600
MAX_LABEL_LENGTH = 120

KIND_CALL = "call"              # one HTTP call, send to response
KIND_ASYNC_SEEN = "async_seen"  # 202 until a poll of ours first read it as finished
KIND_ASYNC = "async"            # 202 until the activity's endDatetime
KIND_ASYNC_WORK = "async_work"  # the activity's startDatetime to endDatetime
KIND_READY = "ready"            # no activity to read: 202 until a read shows it done
KIND_PACED = "paced"            # a call held back by our own rate limit, not R1

_DONE_STATUSES = {"SUCCESS", "COMPLETED", "COMPLETE", "DONE",
                  "FAIL", "FAILED", "ERROR", "FAILURE"}

_ID_PATTERNS = (
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}$"),   # uuid
    re.compile(r"^[0-9a-fA-F]{16,}$"),                  # R1's 32-hex ids
    re.compile(r"^\d{6,}$"),                            # serials
    re.compile(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$"),  # MACs
)

_lock = threading.Lock()
# (hour, kind, label, status) -> {count, total, max, buckets}
_totals: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
# requestId -> (time the 202 came back, route)
_sent: Dict[str, Tuple[float, str]] = {}
_flusher: Optional[threading.Thread] = None
_client: Optional[redis.Redis] = None


def route_label(path: str) -> str:
    """The path with IDs, and anything that could be a name, replaced."""
    parts = []
    for part in path.split("?", 1)[0].split("/"):
        if part and (
            any(p.match(part) for p in _ID_PATTERNS)
            or "@" in part or "%" in part
        ):
            part = "{id}"
        parts.append(part)
    return "/".join(parts)[:MAX_LABEL_LENGTH]


def status_group(status_code: int) -> str:
    # These two mean something different from the rest of their hundred.
    if status_code in (401, 429):
        return str(status_code)
    return f"{status_code // 100}xx"


def _hour_now() -> str:
    return time.strftime("%Y%m%d%H", time.gmtime())


def record(kind: str, label: str, status: str, seconds: float) -> None:
    """Add one timing. Safe to call from any thread."""
    try:
        seconds = max(0.0, float(seconds))
        label = str(label).replace("|", "/")[:MAX_LABEL_LENGTH]
        hour = _hour_now()
        bucket = len(BUCKET_BOUNDS)
        for i, bound in enumerate(BUCKET_BOUNDS):
            if seconds <= bound:
                bucket = i
                break
        with _lock:
            key = (hour, kind, label, status)
            row = _totals.get(key)
            if row is None:
                if sum(1 for k in _totals if k[0] == hour) >= MAX_ROWS_PER_HOUR:
                    key = (hour, kind, "other", status)
                    row = _totals.get(key)
            if row is None:
                row = _totals[key] = {
                    "count": 0, "total": 0.0, "max": 0.0,
                    "buckets": [0] * (len(BUCKET_BOUNDS) + 1),
                }
            row["count"] += 1
            row["total"] += seconds
            row["max"] = max(row["max"], seconds)
            row["buckets"][bucket] += 1
        _ensure_flusher()
    except Exception as e:
        logger.debug(f"R1 call metric dropped: {e}")


def record_call(method: str, path: str, response, seconds: float) -> None:
    """Record one finished HTTP call, and follow its async work if it has any."""
    try:
        label = f"{method.upper()} {route_label(path)}"
        record(KIND_CALL, label, status_group(response.status_code), seconds)
        if response.status_code == 202:
            _track_accepted(response, label)
        elif response.ok and path.startswith("/activities") and _sent:
            _note_activities(response.json())
    except Exception as e:
        logger.debug(f"R1 call metric dropped: {e}")


def record_call_error(method: str, path: str, seconds: float) -> None:
    """A call that never got a response (connection error, timeout)."""
    record(KIND_CALL, f"{method.upper()} {route_label(path)}", "no response", seconds)


def _track_accepted(response, label: str) -> None:
    try:
        body = response.json()
    except ValueError:
        return
    request_id = body.get("requestId") if isinstance(body, dict) else None
    if not request_id:
        return
    now = time.time()
    with _lock:
        if len(_sent) >= MAX_TRACKED_REQUESTS:
            for rid, (sent_at, _) in list(_sent.items()):
                if now - sent_at > TRACKED_REQUEST_SECONDS:
                    del _sent[rid]
            if len(_sent) >= MAX_TRACKED_REQUESTS:
                return
        _sent[request_id] = (now, label)


def _timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _note_activities(body: Any) -> None:
    """Close out every tracked request this activity read shows as finished."""
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        rows = body["data"]
    else:
        rows = [body]
    for row in rows:
        if not isinstance(row, dict):
            continue
        request_id = row.get("requestId") or row.get("id")
        status = str(row.get("status") or "").upper()
        if status not in _DONE_STATUSES:
            continue
        with _lock:
            sent = _sent.pop(request_id, None)
        if sent is None:
            continue
        sent_at, route = sent
        label = row.get("useCase") or route
        result = "success" if status in ("SUCCESS", "COMPLETED", "COMPLETE", "DONE") else "fail"
        now = time.time()
        record(KIND_ASYNC_SEEN, label, result, now - sent_at)
        started, ended = _timestamp(row.get("startDatetime")), _timestamp(row.get("endDatetime"))
        # R1 cuts its times to whole seconds, so its end is on average half a
        # second before the real one. Short tasks read as 0 without this.
        record(KIND_ASYNC, label, result, (ended + 0.5 if ended else now) - sent_at)
        if started and ended:
            record(KIND_ASYNC_WORK, label, result, ended - started)


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "1")),
            password=os.getenv("REDIS_PASSWORD", None),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _client


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None:
        return
    with _lock:
        if _flusher is not None:
            return
        _flusher = threading.Thread(target=_flush_forever, name="r1-call-metrics", daemon=True)
        _flusher.start()
    atexit.register(flush)


def _flush_forever() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


def flush() -> None:
    """Write the totals to Redis. They are dropped if Redis refuses them."""
    with _lock:
        rows = dict(_totals)
        _totals.clear()
    if not rows:
        return
    try:
        client = _redis()
        max_fields = [
            (f"{KEY_PREFIX}{hour}", f"{kind}|{label}|{status}|max")
            for hour, kind, label, status in rows
        ]
        reader = client.pipeline(transaction=False)
        for key, field in max_fields:
            reader.hget(key, field)
        stored_max = reader.execute()

        writer = client.pipeline(transaction=False)
        for ((hour, kind, label, status), row), stored in zip(rows.items(), stored_max):
            key = f"{KEY_PREFIX}{hour}"
            prefix = f"{kind}|{label}|{status}|"
            writer.hincrby(key, f"{prefix}count", row["count"])
            writer.hincrbyfloat(key, f"{prefix}total", round(row["total"], 3))
            if stored is None or row["max"] > float(stored):
                writer.hset(key, f"{prefix}max", round(row["max"], 3))
            for i, n in enumerate(row["buckets"]):
                if n:
                    writer.hincrby(key, f"{prefix}b{i}", n)
            writer.expire(key, RETENTION_SECONDS)
        writer.execute()
    except Exception as e:
        logger.warning(f"R1 call metrics not saved: {e}")


def _percentile(buckets: List[int], count: int, share: float, largest: float) -> float:
    """The bucket's upper bound, so "under this many seconds", capped at the max."""
    target = count * share
    seen = 0
    for i, n in enumerate(buckets):
        seen += n
        if seen >= target and n:
            bound = BUCKET_BOUNDS[i] if i < len(BUCKET_BOUNDS) else largest
            return min(bound, largest)
    return largest


def read_hourly(kind: str, label: str, status: str, hours: int = 168) -> List[Dict[str, Any]]:
    """One row's figures per UTC hour, newest first, to compare times of day."""
    client = _redis()
    now = time.time()
    prefix = f"{kind}|{label}|{status}|"
    result = []
    for offset in range(hours):
        moment = time.gmtime(now - offset * 3600)
        stats = client.hgetall(f"{KEY_PREFIX}{time.strftime('%Y%m%d%H', moment)}")
        count = int(stats.get(f"{prefix}count", 0))
        if not count:
            continue
        total = float(stats.get(f"{prefix}total", 0))
        largest = float(stats.get(f"{prefix}max", 0))
        buckets = [
            int(stats.get(f"{prefix}b{i}", 0)) for i in range(len(BUCKET_BOUNDS) + 1)
        ]
        result.append({
            "hour_utc": time.strftime("%Y-%m-%dT%H:00Z", moment),
            "count": count,
            "average_seconds": round(total / count, 2),
            "median_under_seconds": _percentile(buckets, count, 0.5, largest),
            "p90_under_seconds": _percentile(buckets, count, 0.9, largest),
            "max_seconds": round(largest, 2),
        })
    return result


def read_summary(hours: int = 24) -> List[Dict[str, Any]]:
    """Totals per row over the last N hours, this hour included."""
    client = _redis()
    now = time.time()
    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for offset in range(hours):
        hour = time.strftime("%Y%m%d%H", time.gmtime(now - offset * 3600))
        for field, value in client.hgetall(f"{KEY_PREFIX}{hour}").items():
            kind, label, status, stat = field.rsplit("|", 3)
            row = merged.setdefault((kind, label, status), {
                "count": 0, "total": 0.0, "max": 0.0,
                "buckets": [0] * (len(BUCKET_BOUNDS) + 1),
            })
            if stat == "count":
                row["count"] += int(value)
            elif stat == "total":
                row["total"] += float(value)
            elif stat == "max":
                row["max"] = max(row["max"], float(value))
            elif stat.startswith("b"):
                row["buckets"][int(stat[1:])] += int(value)

    summary = []
    for (kind, label, status), row in merged.items():
        if not row["count"]:
            continue
        summary.append({
            "kind": kind,
            "label": label,
            "status": status,
            "count": row["count"],
            "total_seconds": round(row["total"], 1),
            "average_seconds": round(row["total"] / row["count"], 2),
            "median_under_seconds": _percentile(row["buckets"], row["count"], 0.5, row["max"]),
            "p90_under_seconds": _percentile(row["buckets"], row["count"], 0.9, row["max"]),
            "max_seconds": round(row["max"], 2),
        })
    summary.sort(key=lambda r: r["total_seconds"], reverse=True)
    return summary
