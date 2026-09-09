"""
User verdicts on links and WAN uplinks.

These outrank every machine source, and they are the one artefact in this tool
that must survive everything else. A snapshot is regenerable in six seconds; a
human's forty confirmed links are not. They live beside the snapshots but are
never swept with them -- see store.SNAPSHOT_GLOB.

Override keys are snapshot-independent and built from serial-or-MAC plus port
identifier, sorted. Serial first so a device that changes IP, name or firmware
keeps its verdicts. An RMA -- new serial, new MAC -- correctly loses them: it is
a different device, and inheriting a judgement about the old one would be worse
than asking again.
"""

import time
from typing import Any, Dict, List, Optional

from services.storekit import read_json, write_json_atomic

from .model import Override
from .store import _scope_dir

VERDICTS = ("confirm", "reject", "assert")


def endpoint_key(serial_or_mac: str, ident: Optional[str]) -> str:
    base = str(serial_or_mac or "").strip().lower()
    return f"{base}#{ident}" if ident else base


def link_key(a: str, b: str) -> str:
    """Order-independent: a link is the same link seen from either end."""
    return "|".join(sorted([a, b]))


def _path(tenant_key: str, venue_ids):
    return _scope_dir(tenant_key, venue_ids) / "overrides.json"


def load(tenant_key: str, venue_ids) -> Dict[str, Any]:
    data = read_json(_path(tenant_key, venue_ids))
    if not isinstance(data, dict):
        return {"links": {}, "wan": {}, "venueIds": sorted(venue_ids or [])}
    data.setdefault("links", {})
    data.setdefault("wan", {})
    return data


def _save(tenant_key: str, venue_ids, data: Dict[str, Any]) -> None:
    data["venueIds"] = sorted({str(v) for v in venue_ids if v})
    data["updatedAt"] = time.time()
    write_json_atomic(_path(tenant_key, venue_ids), data)


def set_link(tenant_key: str, venue_ids, key: str, verdict: str,
             by: str = "", note: str = "",
             endpoints: Optional[List[Dict]] = None) -> Dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    data = load(tenant_key, venue_ids)
    data["links"][key] = {"key": key, "verdict": verdict, "by": by, "note": note,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "endpoints": endpoints or []}
    _save(tenant_key, venue_ids, data)
    return data["links"][key]


def clear_link(tenant_key: str, venue_ids, key: str) -> bool:
    data = load(tenant_key, venue_ids)
    if key not in data["links"]:
        return False
    del data["links"][key]
    _save(tenant_key, venue_ids, data)
    return True


def set_wan(tenant_key: str, venue_ids, venue_id: str, device_id: str,
            port_ident: str, action: str, by: str = "") -> Dict[str, Any]:
    """
    One verdict per venue -- a three-venue scope plausibly has three WAN edges,
    and forcing a single answer would misdraw two of them.
    """
    data = load(tenant_key, venue_ids)
    entry = data["wan"].setdefault(str(venue_id), {"confirmed": None, "rejected": []})
    target = {"deviceId": device_id, "portIdent": port_ident}
    marker = f"{device_id}#{port_ident}"
    if action == "confirm":
        entry["confirmed"] = {**target, "by": by,
                              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        entry["rejected"] = [r for r in entry["rejected"] if r != marker]
    elif action == "reject":
        # Remember rejections too, so a wrong guess stops being offered.
        if marker not in entry["rejected"]:
            entry["rejected"].append(marker)
        if entry.get("confirmed") and \
                entry["confirmed"].get("deviceId") == device_id and \
                entry["confirmed"].get("portIdent") == port_ident:
            entry["confirmed"] = None
    elif action == "clear":
        entry["confirmed"] = None
        entry["rejected"] = []
    else:
        raise ValueError("action must be confirm, reject or clear")
    _save(tenant_key, venue_ids, data)
    return data["wan"]


def as_overrides(data: Dict[str, Any]) -> Dict[str, Override]:
    """The stored dict as model objects, for the correlator to apply last."""
    out = {}
    for key, entry in (data.get("links") or {}).items():
        if isinstance(entry, dict) and entry.get("verdict") in VERDICTS:
            out[key] = Override(key=key, verdict=entry["verdict"],
                                by=entry.get("by", ""), at=entry.get("at", ""),
                                note=entry.get("note", ""))
    return out


# ---------------------------------------------------------------------------
# Read-time re-lensing
#
# A snapshot bakes in whatever verdicts existed when it was taken. Verdicts
# recorded afterwards -- which is the normal case, because you confirm a link
# by looking at the map -- would otherwise not show until the next discovery.
# So every read path lifts the baked verdict off and applies the CURRENT one.
#
# This works on stored dicts rather than model objects because that is what the
# read path has. The arithmetic itself is NOT duplicated: it comes from
# score.override_verdict, the same call the discovery path makes.
# ---------------------------------------------------------------------------

def identity_map(devices: List[Dict[str, Any]]) -> Dict[str, str]:
    """deviceId -> serial-or-MAC, the identity override keys are built from."""
    return {d.get("id"): (d.get("serial") or d.get("mac") or d.get("id") or "")
            for d in devices if d.get("id")}


def key_for_link(link: Dict[str, Any], identity: Dict[str, str]) -> str:
    """The override key for a stored link dict. Mirrors correlate._override_key_for."""
    parts = []
    for end in (link.get("a") or {}, link.get("b") or {}):
        device_id = end.get("deviceId") or ""
        parts.append(endpoint_key(identity.get(device_id, device_id), end.get("ident")))
    return link_key(*parts)


def _summarise(evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for item in evidence:
        kind = item.get("kind") or ""
        counts[kind] = counts.get(kind, 0) + 1
    return {"total": len(evidence),
            "support": counts.get("support", 0),
            "contradict": counts.get("contradict", 0),
            "context": counts.get("context", 0),
            "sources": sorted({e.get("source") for e in evidence if e.get("source")})}


def _strip(link: Dict[str, Any], evidence: Optional[List[Dict[str, Any]]]) -> None:
    """Undo a baked verdict, restoring exactly what the engine had concluded."""
    previous = link.pop("override", None)
    machine = link.pop("machine", None)
    link["userAsserted"] = False
    if machine:
        link["tier"] = machine.get("tier", link.get("tier"))
        link["score"] = machine.get("score", link.get("score"))
        link["confidence"] = machine.get("confidence", link.get("confidence"))
        link["tierReason"] = machine.get("tierReason", link.get("tierReason"))

    summary = link.get("evidenceSummary")
    if evidence is not None:
        evidence[:] = [e for e in evidence
                       if not str(e.get("source", "")).startswith("override.user.")]
        # Recomputed from what is left rather than decremented, so the counts
        # cannot drift however many times this runs.
        if isinstance(summary, dict):
            summary.update(_summarise(evidence))
    elif previous and isinstance(summary, dict):
        # /graph ships summaries without evidence, so undo the one row the
        # verdict added.
        source = f"override.user.{previous.get('verdict')}"
        bucket = "contradict" if previous.get("verdict") == "reject" else "support"
        summary["total"] = max(0, int(summary.get("total") or 0) - 1)
        summary[bucket] = max(0, int(summary.get(bucket) or 0) - 1)
        summary["sources"] = [x for x in (summary.get("sources") or []) if x != source]


def _apply(link: Dict[str, Any], entry: Dict[str, Any],
           evidence: Optional[List[Dict[str, Any]]]) -> None:
    from .score import WEIGHTS, override_claim, override_verdict

    verdict, by = entry["verdict"], entry.get("by", "")
    machine = {"tier": link.get("tier", "weak"), "score": float(link.get("score") or 0.0),
               "confidence": link.get("confidence"), "tierReason": link.get("tierReason", "")}
    _, weight = WEIGHTS[f"override.user.{verdict}"]

    row = {"source": f"override.user.{verdict}",
           "kind": "support" if verdict != "reject" else "contradict",
           "claim": override_claim(verdict, by, entry.get("at", ""), entry.get("note", "")),
           "weight": weight, "baseWeight": weight,
           "a": (link.get("a") or {}).get("portId") or (link.get("a") or {}).get("deviceId"),
           "b": (link.get("b") or {}).get("portId") or (link.get("b") or {}).get("deviceId"),
           "fields": {"verdict": verdict, "by": by, "at": entry.get("at", ""),
                      "machine": dict(machine)},
           "observedAt": entry.get("at", "")}
    if evidence is not None:
        evidence.insert(0, row)
        disagreeing = sum(1 for e in evidence if e.get("kind") == "contradict"
                          and not str(e.get("source", "")).startswith("override"))
    else:
        # No evidence loaded (the /graph path ships summaries only). The count
        # is already on the summary, minus this row if it is a rejection.
        summary = link.get("evidenceSummary") or {}
        disagreeing = int(summary.get("contradict") or 0)

    applied = override_verdict(verdict, machine["tier"], machine["score"], by, disagreeing)
    link["tier"] = applied["tier"]
    link["score"] = applied["score"]
    link["confidence"] = applied["confidence"]
    link["tierReason"] = applied["tierReason"]
    link["machine"] = machine
    link["userAsserted"] = True
    link["override"] = {k: entry.get(k, "") for k in ("key", "verdict", "by", "at", "note")}

    summary = link.get("evidenceSummary")
    if isinstance(summary, dict):
        summary["total"] = int(summary.get("total") or 0) + 1
        bucket = "support" if verdict != "reject" else "contradict"
        summary[bucket] = int(summary.get(bucket) or 0) + 1
        summary["sources"] = sorted(set(summary.get("sources") or []) | {row["source"]})


def relens(links: List[Dict[str, Any]], stored: Dict[str, Any],
           identity: Dict[str, str],
           evidence_by_link: Optional[Dict[str, List[Dict[str, Any]]]] = None
           ) -> List[Dict[str, Any]]:
    """
    Re-apply the CURRENT verdicts to stored links, in place.

    Idempotent by construction: every link is stripped back to the engine's own
    conclusion first, so this is safe to run on a snapshot that already had
    verdicts baked in, on one that did not, and on the same list twice.
    """
    entries = (stored or {}).get("links") or {}
    for link in links:
        evidence = None if evidence_by_link is None \
            else evidence_by_link.setdefault(link.get("id"), [])
        had = bool(link.get("override"))
        entry = entries.get(key_for_link(link, identity))
        if not had and not entry:
            continue
        _strip(link, evidence)
        if entry and entry.get("verdict") in VERDICTS:
            _apply(link, {**entry, "key": entry.get("key", "")}, evidence)
    return links
