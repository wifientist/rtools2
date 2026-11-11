"""
Which R1 calls our tools make that R1 has deprecated, or says it will remove.

Sends no requests to R1. Three sources, joined per call:

1. The code: every R1 call written out with its path in api/ (scripts and
   tests are left out: no tool runs them).
2. What the tools actually called: route labels in the R1 call-timing totals
   (30 days), which also covers paths built at runtime.
3. The newest R1 spec in api/specs/: the `deprecated` flag, "will be removed
   no sooner than MM/DD/YYYY", the replacement R1 names, and media types
   (vnd.ruckus.v1+json) R1 says it will drop.

Plus what R1 said on live responses (r1api.deprecation_watch): the
`ruckus-deprecated` header, and 405/410 for a route that is gone.

The spec is only as fresh as the last one uploaded, so its age is part of
the report. The live header is the early warning that does not depend on it.
"""

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from services.r1_deprecation_reviewed import RETIRED, REVIEWED

logger = logging.getLogger(__name__)

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_DIR = os.path.join(API_ROOT, "specs")
SPEC_GLOB = re.compile(r"^RUCKUS_One_Consolidated_API_(\d{2})(\d{2})(\d{4})\.json$")
SKIP_DIRS = {"specs", "alembic", "__pycache__", "scripts", "tests", "szapi", "node_modules"}

REPORT_KEY = "r1:deprecations:report"
# Findings Slack has already been told about, as "METHOD /path|severity".
NOTIFIED_KEY = "r1:deprecations:notified"
SOON_DAYS = 90
SPEC_STALE_DAYS = 45

METHODS = ("get", "post", "put", "patch", "delete")
_M = "|".join(METHODS)
_PATH = r"""f?["'](/[^"'?\s]*)"""
# The shapes R1 calls take in this codebase.
CALL_PATTERNS = [
    # r1_client.post("/x" | self.client.get(f"/x/{id}" | client.get("/x"
    re.compile(rf"""\b(?:self\.)?(?:r1_client|r1|client|_client|r1c)\.({_M})\(\s*{_PATH}"""),
    # asyncio.to_thread(r1_client.get, "/x", ...)
    re.compile(rf"""\b(?:self\.)?(?:r1_client|r1|client|_client|r1c)\.({_M})\s*,\s*{_PATH}"""),
    # _get(r1, "/x", ...)  (topology, pisr helpers)
    re.compile(rf"""\b_({_M})\(\s*\w+\s*,\s*{_PATH}"""),
    # limited("post", "/x"  |  _request("get", "/x"
    re.compile(rf"""\b(?:limited|_request|request|_call)\(\s*["']({_M})["']\s*,\s*{_PATH}"""),
    # _get(r1_client, tenant_id, "/x")  (three-argument helpers: venue checkup, ...)
    re.compile(rf"""\b_({_M})\(\s*\w+\s*,\s*[\w.]+\s*,\s*{_PATH}"""),
    # _call(r1_client, "get", "/x" | r1_call(...) | _read(r1_client, tenant_id, "get", "/x"
    re.compile(rf"""\b(?:_call|r1_call|_read)\(\s*[\w.]+\s*,\s*(?:[\w.]+\s*,\s*)?["']({_M})["']\s*,\s*{_PATH}"""),
]
# Helpers that take only a path and always GET: venue checkup's limited(path).
GET_ONLY_PATTERNS = [
    re.compile(rf"""\blimited\(\s*{_PATH}"""),
]
MEDIA_TYPE = re.compile(r"application/vnd\.ruckus\.v[\d.]+\+json", re.I)
_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
_MT = r"(application/vnd\.ruckus\.v[\d.]+\+json)"
# The route itself goes: "This method will be removed no sooner than 08/31/2026."
REMOVAL = re.compile(rf"This method will be removed no sooner than {_DATE}", re.I)
# One media type goes: "Version application/vnd.ruckus.v1+JSON will be removed..."
# or "The application/vnd.ruckus.v1+json will be removed no sooner than ..."
MEDIA_REMOVAL = re.compile(rf"{_MT} will be removed no sooner than {_DATE}", re.I)
# Plain application/json changes meaning: "application/json is currently tied to
# application/vnd.ruckus.v1+json which will be moved to application/vnd.ruckus.v1.1+json on 08/31/2026"
DEFAULT_MOVE = re.compile(
    rf"application/json is currently tied to {_MT} which will be moved to {_MT} on {_DATE}", re.I
)
REPLACEMENT = re.compile(
    r"following (?:URL|endpoint)?\s*(?:(?:GET|POST|PUT|PATCH|DELETE)\s+)?['\"]?(/[A-Za-z0-9_{}/.-]+)['\"]?", re.I
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


# ---------- spec ----------

def latest_spec() -> Tuple[Optional[str], Optional[date]]:
    """The newest dated R1 spec in api/specs, by the date in its name."""
    best: Tuple[Optional[str], Optional[date]] = (None, None)
    for name in os.listdir(SPEC_DIR):
        m = SPEC_GLOB.match(name)
        if not m:
            continue
        month, day, year = map(int, m.groups())
        stamp = date(year, month, day)
        if best[1] is None or stamp > best[1]:
            best = (os.path.join(SPEC_DIR, name), stamp)
    return best


def _date(month: str, day: str, year: str) -> Optional[date]:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


@lru_cache(maxsize=2)
def _spec_index(path: str, mtime: float) -> Dict[str, Any]:
    """Every operation in the spec with what it says about its own future."""
    with open(path) as f:
        spec = json.load(f)
    ops: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for spec_path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in METHODS or not isinstance(op, dict):
                continue
            text = " ".join(str(op.get(k) or "") for k in ("summary", "description"))
            removal = REMOVAL.search(text)
            replacement = REPLACEMENT.search(text)
            media = [
                {"media_type": m.group(1).lower(), "removal_date": _date(*m.group(2, 3, 4))}
                for m in MEDIA_REMOVAL.finditer(text)
            ]
            move = DEFAULT_MOVE.search(text)
            ops[(method, spec_path)] = {
                "deprecated": bool(op.get("deprecated")),
                "removal_date": _date(*removal.groups()) if removal else None,
                "default_move": {
                    "from": move.group(1).lower(), "to": move.group(2).lower(),
                    "date": _date(*move.group(3, 4, 5)),
                } if move else None,
                "replacement": replacement.group(1) if replacement else None,
                "media_removals": media,
                "summary": (op.get("summary") or "").strip(),
            }
    return {"ops": ops, "paths": list(spec.get("paths", {}))}


def _segments(path: str) -> List[str]:
    return [s for s in path.strip("/").split("/") if s]


def match_spec(method: str, path: str, ops: Dict[Tuple[str, str], Any]) -> Optional[str]:
    """
    The spec path this call hits, or None. `{...}` on either side matches any
    one segment; among several matches the one with the most literal
    segments wins (/venues/aps over /venues/{venueId}).
    """
    want = _segments(path)
    best, best_score = None, -1
    for (m, spec_path) in ops:
        if m != method:
            continue
        have = _segments(spec_path)
        if len(have) != len(want):
            continue
        score = 0
        for a, b in zip(want, have):
            a_var, b_var = a.startswith("{"), b.startswith("{")
            if a_var and b_var:
                score += 1      # an id where the spec has one
            elif a_var or b_var:
                continue        # possible, but a worse fit
            elif a != b:
                break
            else:
                score += 2      # the same word
        else:
            if score > best_score:
                best, best_score = spec_path, score
    return best


# ---------- what we call ----------

def _normalize(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{x}", path.split("?")[0]).rstrip("/")
    return path or "/"


def _tool_of(rel: str) -> str:
    parts = rel.split(os.sep)
    if parts[0] in ("routers", "services", "workflow") and len(parts) > 1:
        sub = parts[2] if parts[1] == "phases" and len(parts) > 2 else parts[1]
        return f"{parts[0]}/{os.path.splitext(sub)[0]}"
    return os.path.splitext(rel)[0]


def code_calls(root: str = API_ROOT) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(method, path) -> where the code makes that R1 call, and media types sent."""
    calls: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                src = open(full, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            matches = [(m, m.group(1).lower(), m.group(2)) for pattern in CALL_PATTERNS for m in pattern.finditer(src)]
            matches += [(m, "get", m.group(1)) for pattern in GET_ONLY_PATTERNS for m in pattern.finditer(src)]
            for m, method, path in matches:
                line_start = src.rfind("\n", 0, m.start()) + 1
                if src[line_start:m.start()].lstrip().startswith(("@", "#")):
                    continue  # a FastAPI route decorator or a comment
                if re.match(r"^/v\d+_\d+/", path) or path.startswith("/api/"):
                    continue  # SmartZone or our own API
                key = (method, _normalize(path))
                entry = calls.setdefault(key, {"locations": set(), "tools": set(), "media_types": set()})
                line = src.count("\n", 0, m.start()) + 1
                entry["locations"].add(f"{rel}:{line}")
                entry["tools"].add(_tool_of(rel))
                window = src[m.start(): m.start() + 400].split("\n\n")[0]
                entry["media_types"].update(t.lower() for t in MEDIA_TYPE.findall(window))
    return calls


def observed_calls(days: int = 30) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(method, path) -> how often the tools called it, from the call-timing totals."""
    from r1api import call_metrics

    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    try:
        rows = call_metrics.read_summary(days * 24)
    except Exception as e:
        logger.warning(f"[r1-deprecation] call totals unavailable: {e}")
        return seen
    for row in rows:
        if row.get("kind") != call_metrics.KIND_CALL or " " not in row.get("label", ""):
            continue
        method, path = row["label"].split(" ", 1)
        key = (method.lower(), _normalize(path.replace("{id}", "{x}")))
        entry = seen.setdefault(key, {"count": 0, "statuses": {}})
        entry["count"] += int(row.get("count") or 0)
        entry["statuses"][row["status"]] = entry["statuses"].get(row["status"], 0) + int(row.get("count") or 0)
    return seen


def settled_note(method: str, path: str, live: Dict[str, Any]) -> Optional[str]:
    """Why a live R1 flag no longer needs attention, or None when it still does.

    A flag on a call we have since replaced (RETIRED) is history once nothing
    has called it after the replacement; a media-type notice on a call we
    pinned (REVIEWED) is settled unless R1 adds a real alarm.
    """
    key = (method.lower(), _normalize(path.replace("{id}", "{x}")))
    retired = RETIRED.get(key)
    if retired and (live.get("last_seen") or "") <= retired["since"]:
        return f"replaced {retired['since'][:10]} by {retired['replaced_by']}; not called since"
    alarm = live.get("gone_status") or live.get("replacement") or live.get("sunset") or live.get("deprecation")
    review = REVIEWED.get(key)
    if review and not alarm:
        return f"reviewed {review['date']}: {review['note']}"
    return None


# ---------- the audit ----------

def _bump(current: str, new: str) -> str:
    return new if SEVERITY_ORDER[new] < SEVERITY_ORDER[current] else current


def run_audit(today: Optional[date] = None) -> Dict[str, Any]:
    from r1api import deprecation_watch

    today = today or datetime.now(timezone.utc).date()
    spec_path, spec_date = latest_spec()
    if not spec_path:
        raise RuntimeError(f"No RUCKUS_One_Consolidated_API_MMDDYYYY.json in {SPEC_DIR}")
    index = _spec_index(spec_path, os.path.getmtime(spec_path))
    ops = index["ops"]

    in_code = code_calls()
    observed = observed_calls()
    try:
        flagged_live = deprecation_watch.read_seen()
    except Exception as e:
        logger.warning(f"[r1-deprecation] live signals unavailable: {e}")
        flagged_live = {}
    live_by_key = {}
    for label, row in flagged_live.items():
        method, path = label.split(" ", 1)
        live_by_key[(method.lower(), _normalize(path.replace("{id}", "{x}")))] = row

    keys = set(in_code) | set(observed) | set(live_by_key)
    findings, ok = [], 0
    for method, path in sorted(keys, key=lambda k: (k[1], k[0])):
        code = in_code.get((method, path), {})
        seen = observed.get((method, path), {})
        live = live_by_key.get((method, path))
        spec_path_hit = match_spec(method, path, ops)
        op = ops.get((method, spec_path_hit)) if spec_path_hit else None

        severity, reasons = "info", []
        removal_date, replacement = None, None

        if live and live.get("gone_status"):
            severity = _bump(severity, "critical")
            reasons.append(f"R1 answered {live['gone_status']} (allows {live.get('allow') or '?'}): the route is gone")
        if live and live.get("replacement"):
            severity = _bump(severity, "high")
            replacement = live["replacement"]
            reasons.append(f"R1 flags it deprecated on live responses; use {live['replacement']}")
        if live and (live.get("sunset") or live.get("deprecation")):
            severity = _bump(severity, "high")
            reasons.append(f"R1 sent Sunset={live.get('sunset')} Deprecation={live.get('deprecation')}")
        if live and live.get("current_version"):
            severity = _bump(severity, "medium")
            reasons.append(f"R1's current media type is {live['current_version']}; we sent {', '.join(live.get('sent_media_types') or [])}")

        if op is None:
            if code or seen:
                severity = _bump(severity, "medium")
                reasons.append("not in the R1 spec (undocumented, mistyped, or removed)")
        else:
            replacement = replacement or op["replacement"]
            if op["deprecated"] or op["removal_date"]:
                removal_date = op["removal_date"]
                if removal_date and removal_date <= today:
                    severity = _bump(severity, "high")
                    reasons.append(f"spec: removal allowed since {removal_date.isoformat()}; it can disappear any day")
                elif removal_date and (removal_date - today).days <= SOON_DAYS:
                    severity = _bump(severity, "high")
                    reasons.append(f"spec: will be removed no sooner than {removal_date.isoformat()}")
                elif removal_date:
                    severity = _bump(severity, "medium")
                    reasons.append(f"spec: will be removed no sooner than {removal_date.isoformat()}")
                else:
                    severity = _bump(severity, "medium")
                    reasons.append("spec: deprecated, no removal date given")
            sent = code.get("media_types") or set()
            for notice in op["media_removals"]:
                if notice["media_type"] in sent:
                    severity = _bump(severity, "high")
                    reasons.append(f"spec: we send {notice['media_type']}, removed no sooner than {notice['removal_date']}")
            move = op.get("default_move")
            if move and not sent:
                severity = _bump(severity, "medium")
                when = move["date"]
                tense = "moved" if when and when <= today else "moves"
                reasons.append(
                    f"spec: plain application/json {tense} from {move['from'].split('/')[-1]} to "
                    f"{move['to'].split('/')[-1]} on {when}; check the fields we read still come back"
                )

        review = REVIEWED.get((method, path))
        live_alarm = bool(live and (live.get("gone_status") or live.get("replacement")
                                    or live.get("sunset") or live.get("deprecation")))
        if review and reasons and not live_alarm:
            if review["spec"] == os.path.basename(spec_path):
                severity = "info"
                reasons.append(f"reviewed {review['date']}: {review['note']}")
            else:
                severity = "medium"
                reasons.append(f"reviewed {review['date']} against {review['spec']}; re-check with the newer spec")

        retired = RETIRED.get((method, path))
        if retired and reasons and not code:
            flagged_after = bool(live and (live.get("last_seen") or "") > retired["since"])
            if flagged_after:
                reasons.append(f"retired {retired['since'][:10]} for {retired['replaced_by']}, but R1 flagged a call to it since")
            else:
                severity = "info"
                reasons.append(f"retired {retired['since'][:10]}: replaced by {retired['replaced_by']}; nothing has called it since")

        if not reasons:
            ok += 1
            continue
        findings.append({
            "severity": severity,
            "method": method.upper(),
            "path": path,
            "spec_path": spec_path_hit,
            "reasons": reasons,
            "removal_date": removal_date.isoformat() if removal_date else None,
            "days_left": (removal_date - today).days if removal_date else None,
            "replacement": replacement,
            "summary": op["summary"] if op else None,
            "tools": sorted(code.get("tools", [])),
            "locations": sorted(code.get("locations", []))[:8],
            "calls_30d": seen.get("count", 0),
            "statuses_30d": seen.get("statuses", {}),
            "live": live,
        })

    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["days_left"] if f["days_left"] is not None else 99999, f["path"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec_file": os.path.basename(spec_path),
        "spec_date": spec_date.isoformat() if spec_date else None,
        "spec_age_days": (today - spec_date).days if spec_date else None,
        "spec_stale": bool(spec_date and (today - spec_date).days > SPEC_STALE_DAYS),
        "calls_checked": len(keys),
        "calls_in_code": len(in_code),
        "calls_observed_30d": len(observed),
        "ok": ok,
        "counts": counts,
        "findings": findings,
    }


def fingerprint(finding: Dict[str, Any]) -> str:
    return f"{finding['method']} {finding['path']}|{finding['severity']}"


def save_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Store the report; return the findings that are new or more severe than last time."""
    from r1api.call_metrics import _redis

    client = _redis()
    previous_raw = client.get(REPORT_KEY)
    previous = json.loads(previous_raw) if previous_raw else None
    before = {}
    for f in (previous or {}).get("findings", []):
        before[f"{f['method']} {f['path']}"] = f["severity"]
    changed = [
        f for f in report["findings"]
        if f["severity"] != "info"
        and (f"{f['method']} {f['path']}" not in before
             or SEVERITY_ORDER[f["severity"]] < SEVERITY_ORDER[before[f"{f['method']} {f['path']}"]])
    ]
    report["new_since_last_run"] = [fingerprint(f) for f in changed]
    report["first_run"] = previous is None
    client.set(REPORT_KEY, json.dumps(report, default=str))
    return {"changed": changed, "first_run": previous is None}


def unnotified(report: Dict[str, Any], severities=("critical", "high")) -> List[Dict[str, Any]]:
    """Findings at these severities Slack has not heard about at this severity yet."""
    from r1api.call_metrics import _redis

    told = _redis().smembers(NOTIFIED_KEY) or set()
    return [f for f in report["findings"] if f["severity"] in severities and fingerprint(f) not in told]


def mark_notified(findings: List[Dict[str, Any]]) -> None:
    from r1api.call_metrics import _redis

    if findings:
        _redis().sadd(NOTIFIED_KEY, *[fingerprint(f) for f in findings])


def load_report() -> Optional[Dict[str, Any]]:
    from r1api.call_metrics import _redis

    raw = _redis().get(REPORT_KEY)
    return json.loads(raw) if raw else None
