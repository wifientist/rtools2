"""
V2 Phases: Config Rules -- read what a company's rules need. Writes nothing to R1.

    validate_config_rules  (global)    the chosen venues, one unit each
    config_rules_wlans     (global)    each WLAN active on a chosen venue, once
    config_rules_venue     (per venue) venue and AP Group settings

Captures go to their own Redis hash, not the job: extract() keeps catalogue
fields only, and a tenant's worth does not belong in the job blob. A read that
fails is recorded and the rest carries on; the report shows it as unavailable.
"""

import asyncio
import base64
import json
import logging
import zlib
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from redis_client import get_redis_client
from services import ap_group_ops
from services.config_rules import VENUE_ROW, VENUE_SECTIONS, extract
from workflow.phases.phase_executor import PhaseExecutor, PhaseValidation
from workflow.phases.registry import register_phase
from workflow.v2.models import ConflictDetail, UnitMapping, ValidationResult, ValidationSummary

logger = logging.getLogger(__name__)

# Redis hash per job: "wlans" -> JSON, "venue:{id}" -> JSON.
CAPTURE_KEY = "config_rules:capture:{job_id}"
CAPTURE_TTL = 7 * 86400

WLAN_CONCURRENCY = 8
WLAN_PROGRESS_EVERY = 100
VENUE_CONCURRENCY = 4
QUERY_PAGE_SIZE = 500


def encode_capture(value: Dict[str, Any]) -> str:
    """Channel lists repeat per AP Group: ~100 KB a venue raw, a fraction of that packed."""
    return base64.b64encode(zlib.compress(json.dumps(value).encode())).decode()


def decode_capture(raw: str) -> Dict[str, Any]:
    return json.loads(zlib.decompress(base64.b64decode(raw)))


def _error(resp: Any) -> str:
    try:
        errors = resp.json().get("errors") or []
        if errors:
            return "; ".join(e.get("message") or e.get("reason") or str(e) for e in errors)
    except Exception:
        pass
    return f"HTTP {resp.status_code}"


async def _read(r1_client, tenant_id: str, method: str, path: str, **kw) -> Dict[str, Any]:
    """{ok, body} or {ok: False, error}. An R1 error is never mistaken for settings."""
    try:
        resp = await asyncio.to_thread(ap_group_ops.r1_call, r1_client, method, path, tenant_id, **kw)
        if not resp.ok:
            return {"ok": False, "error": _error(resp)}
        return {"ok": True, "body": resp.json() if resp.content else None}
    except Exception as e:
        return {"ok": False, "error": ap_group_ops.api_error(e)}


async def _query_all(r1_client, tenant_id: str, path: str, fields: List[str],
                     filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every row of a query endpoint. These page from 1, and raise rather than return a part."""
    rows: List[Dict[str, Any]] = []
    page = 1
    while True:
        body: Dict[str, Any] = {"fields": fields, "page": page, "pageSize": QUERY_PAGE_SIZE}
        if filters:
            body["filters"] = filters
        result = await _read(r1_client, tenant_id, "post", path, payload=body)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        data = (result["body"] or {}).get("data") or []
        rows.extend(data)
        total = (result["body"] or {}).get("totalCount")
        if not data or (total is not None and len(rows) >= total) or (total is None and len(data) < QUERY_PAGE_SIZE):
            return rows
        page += 1


async def _store(job_id: str, field: str, value: Dict[str, Any]) -> None:
    redis = await get_redis_client()
    key = CAPTURE_KEY.format(job_id=job_id)
    await redis.hset(key, field, encode_capture(value))
    await redis.expire(key, CAPTURE_TTL)


@register_phase("validate_config_rules", "Find Venues")
class ValidateConfigRulesPhase(PhaseExecutor):
    """One unit per chosen venue that this tenant really has."""

    class Inputs(BaseModel):
        venue_ids: List[str]
        sections: List[str] = Field(default_factory=list)

    class Outputs(BaseModel):
        unit_mappings: Dict[str, UnitMapping] = Field(default_factory=dict)
        validation_result: Optional[ValidationResult] = None

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        rows = await _query_all(self.r1_client, self.tenant_id, "/venues/query", ["id", "name"])
        names = {r["id"]: r.get("name") or r["id"] for r in rows if r.get("id")}
        units: Dict[str, UnitMapping] = {}
        for venue_id in inputs.venue_ids:
            if venue_id in names:
                unit_id = f"venue_{venue_id}"
                units[unit_id] = UnitMapping(
                    unit_id=unit_id, unit_number=names[venue_id],
                    input_config={"venue_id": venue_id, "venue_name": names[venue_id], "sections": inputs.sections},
                )
        missing = len(inputs.venue_ids) - len(units)
        conflicts = []
        if missing:
            conflicts.append(ConflictDetail(
                resource_type="venues", resource_name=f"{missing} venue(s)",
                description="Not in this tenant, so not checked.", severity="warning",
            ))
        if not units:
            conflicts.append(ConflictDetail(
                resource_type="venues", resource_name="no venues",
                description="None of the chosen venues are in this tenant.", severity="error",
            ))
        await self.emit(f"Checking {len(units)} venue(s)", "warning" if missing else "success")
        return self.Outputs(
            unit_mappings=units,
            validation_result=ValidationResult(
                valid=bool(units), conflicts=conflicts,
                summary=ValidationSummary(total_units=len(units), total_api_calls=8 * len(units)),
            ),
        )

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=False, estimated_api_calls=1)


@register_phase("config_rules_wlans", "Read WLANs")
class ConfigRulesWlansPhase(PhaseExecutor):
    """Each WLAN active on a chosen venue, read once however many venues carry it."""

    class Inputs(BaseModel):
        venue_ids: List[str]
        sections: List[str] = Field(default_factory=list)

    class Outputs(BaseModel):
        wlans_read: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        wifi = "wifi" in inputs.sections
        legacy = "legacy" in inputs.sections
        if not wifi and not legacy:
            await self.emit("No WLAN rules in this ruleset")
            await _store(self.job_id, "wlans", {"networks": {}})
            return self.Outputs()

        try:
            rows = await _query_all(self.r1_client, self.tenant_id, "/wifiNetworks/query",
                                    ["id", "name", "venueApGroups"])
        except Exception as e:
            await self.emit(f"Could not list WLANs: {e}", "error")
            await _store(self.job_id, "wlans", {"error": str(e)})
            return self.Outputs()

        chosen = set(inputs.venue_ids)
        targets = []
        for row in rows:
            venues = sorted({g.get("venueId") for g in row.get("venueApGroups") or []} & chosen)
            if venues:
                targets.append({"id": row["id"], "name": row.get("name"), "venue_ids": venues})
        total, done, failed = len(targets), 0, 0
        await self.emit(f"Reading {total} WLAN(s) on the chosen venues, out of {len(rows)} in the tenant")
        semaphore = asyncio.Semaphore(WLAN_CONCURRENCY)
        networks: Dict[str, Any] = {}

        async def section(path: str, name: str) -> Dict[str, Any]:
            result = await _read(self.r1_client, self.tenant_id, "get", path)
            if not result["ok"]:
                return {"ok": False, "error": result["error"]}
            return extract(result["body"], name)

        async def read(target: Dict[str, Any]) -> None:
            nonlocal done, failed
            async with semaphore:
                if await self.stop_requested():
                    return
                sections: Dict[str, Any] = {}
                if wifi:
                    sections["wifi"] = await section(f"/wifiNetworks/{target['id']}", "wifi")
                if legacy:
                    sections["legacy"] = await section(f"/networks/{target['id']}", "legacy")
                networks[target["id"]] = {**target, "sections": sections}
                done += 1
                failed += 0 if all(s.get("ok") for s in sections.values()) else 1
                await self.emit_progress(done, total, failed, "WLAN", target.get("name") or "")
                if done % WLAN_PROGRESS_EVERY == 0:
                    await self.emit(f"Read {done} of {total} WLANs")

        await asyncio.gather(*(read(t) for t in targets))
        await _store(self.job_id, "wlans", {"networks": networks})
        await self.emit(f"Read {len(networks)} WLAN(s)" + (f", {failed} with errors" if failed else ""),
                        "warning" if failed else "success")
        return self.Outputs(wlans_read=len(networks))

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=False, estimated_api_calls=1)


@register_phase("config_rules_venue", "Read Venue")
class ConfigRulesVenuePhase(PhaseExecutor):
    """The venue and AP Group settings the rules use. Only those."""

    class Inputs(BaseModel):
        unit_id: str
        unit_number: str
        venue_id: str
        venue_name: str
        sections: List[str] = Field(default_factory=list)

    class Outputs(BaseModel):
        venues_read: int = 0

    async def execute(self, inputs: 'Inputs') -> 'Outputs':
        venue_id = inputs.venue_id
        semaphore = asyncio.Semaphore(VENUE_CONCURRENCY)
        capture: Dict[str, Any] = {"id": venue_id, "name": inputs.venue_name, "sections": {},
                                   "read": list(inputs.sections)}

        async def limited(method: str, path: str, **kw) -> Dict[str, Any]:
            async with semaphore:
                return await _read(self.r1_client, self.tenant_id, method, path, **kw)

        async def section(name: str) -> None:
            suffix, accept = VENUE_SECTIONS[name]
            result = await limited("get", f"/venues/{venue_id}/{suffix}", accept=accept)
            capture["sections"][name] = (extract(result["body"], name) if result["ok"]
                                         else {"ok": False, "error": result["error"]})

        async def venue_row() -> None:
            result = await limited("post", "/venues/query", payload={
                "fields": ["id", "rogueDetection"], "filters": {"id": [venue_id]}, "page": 1, "pageSize": 1})
            rows = ((result.get("body") or {}).get("data") or []) if result["ok"] else []
            if rows:
                capture["sections"][VENUE_ROW] = extract(rows[0], VENUE_ROW)
            else:
                capture["sections"][VENUE_ROW] = {"ok": False, "error": result.get("error") or "Venue not returned"}

        async def groups() -> None:
            listed = await limited("get", f"/venues/{venue_id}/apGroups")
            if not listed["ok"]:
                capture["groups_error"] = listed["error"]
                return
            body = listed["body"]
            rows = body if isinstance(body, list) else (body or {}).get("data") or []

            async def one(group: Dict[str, Any]) -> Dict[str, Any]:
                result = await limited("get", f"/venues/{venue_id}/apGroups/{group['id']}/radioSettings")
                radio = (extract(result["body"], "group_radio") if result["ok"]
                         else {"ok": False, "error": result["error"]})
                return {"id": group["id"], "name": group.get("name") or "Default", "radio": radio}

            capture["groups"] = list(await asyncio.gather(*(one(g) for g in rows if g.get("id"))))

        tasks = [section(s) for s in inputs.sections if s in VENUE_SECTIONS]
        if VENUE_ROW in inputs.sections:
            tasks.append(venue_row())
        if "group_radio" in inputs.sections:
            tasks.append(groups())
        await asyncio.gather(*tasks)
        await _store(self.job_id, f"venue:{venue_id}", capture)
        return self.Outputs(venues_read=1)

    async def validate(self, inputs: 'Inputs') -> PhaseValidation:
        return PhaseValidation(valid=True, will_create=False, estimated_api_calls=8)
