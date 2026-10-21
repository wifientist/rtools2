"""
Migration-audit capture cache.

Pulling a large site is slow (Jersey Village: ~2 min of SZ reads incl. one
GET per AP, and 600+ R1 network-detail GETs), and often nothing changed
since yesterday. So the raw captures each side produces are kept for 7
days and re-used until the operator asks for a refresh:

    audit_cache:sz:{sz_controller_id}:{zone_id}
    audit_cache:r1:{r1_controller_id}:{tenant_id or -}:{venue_id}

each a hash: "meta" (small JSON: captured_at, version, counts) and "data"
(zlib+base64 JSON). Only the latest capture per site is kept; a refresh
overwrites it. The TTL runs from capture -- reading doesn't extend it.

Captures, not reports: the report is recomputed on every run, so audit
improvements apply to cached data. Each capture carries a format version;
an older one reads as "needs refresh" rather than producing a misleading
audit (e.g. SZ captures made before per-AP radio reads existed).

Credentials are stripped before anything is written: every string under a
credential-named key becomes CREDENTIAL_PRESENT. The audit never compares
credential values -- only "is one set" -- so results are unchanged, and a
week-long cache holds no passphrases or shared secrets.

Lives in its own logical Redis DB (redis_client "audit_cache" pool) so it
can be inspected or flushed without touching workflow state.
"""

import base64
import json
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from redis_client import get_audit_cache_redis
from schemas.sz_migration import SZMigrationSnapshot
from services.sz_migration.audit_redaction import CREDENTIAL_PRESENT, is_secret_key
from services.sz_migration.passphrase_check import fingerprint

TTL_SECONDS = 7 * 24 * 3600


# Bump when a capture's shape changes so old ones read as stale.
SZ_CAPTURE_VERSION = 3   # 2: per-AP radio config (ap_radio_config); 3: passphrase fingerprints
R1_CAPTURE_VERSION = 2   # 2: passphrase fingerprints


def sz_key(controller_id: int, zone_id: str) -> str:
    return f"audit_cache:sz:{controller_id}:{zone_id}"


def r1_key(controller_id: int, tenant_id: Optional[str], venue_id: str) -> str:
    return f"audit_cache:r1:{controller_id}:{tenant_id or '-'}:{venue_id}"


def _strip_value(key: str, value: str) -> str:
    # Passphrases become keyed fingerprints so a cached capture can still be
    # checked against the other side (passphrase_check); every other
    # credential (shared secrets, passwords) is just marked present.
    if key.lower().endswith("passphrase"):
        return fingerprint(value)
    return CREDENTIAL_PRESENT


def strip_credentials(node: Any) -> Any:
    """Copy with every credential-named string replaced (empty stays empty)."""
    if isinstance(node, dict):
        return {
            k: (_strip_value(k, v) if is_secret_key(k) and isinstance(v, str) and v else strip_credentials(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [strip_credentials(v) for v in node]
    return node


def _pack(obj: Any) -> str:
    return base64.b64encode(zlib.compress(json.dumps(obj, default=str).encode(), 6)).decode()


def _unpack(blob: str) -> Any:
    return json.loads(zlib.decompress(base64.b64decode(blob)).decode())


def _age(meta: Dict[str, Any]) -> Optional[int]:
    try:
        then = datetime.fromisoformat(meta["captured_at"])
        return int((datetime.now(timezone.utc) - then).total_seconds())
    except (KeyError, ValueError, TypeError):
        return None


async def _write(key: str, meta: Dict[str, Any], data: Any) -> Dict[str, Any]:
    r = await get_audit_cache_redis()
    meta = {**meta, "captured_at": datetime.now(timezone.utc).isoformat()}
    async with r.pipeline(transaction=True) as p:
        p.delete(key)
        p.hset(key, mapping={"meta": json.dumps(meta), "data": _pack(data)})
        p.expire(key, TTL_SECONDS)
        await p.execute()
    return {**meta, "age_seconds": 0, "expires_in_seconds": TTL_SECONDS}


async def read_meta(key: str, version: int) -> Optional[Dict[str, Any]]:
    """Metadata only (cheap), with age, time left, and whether it's usable."""
    r = await get_audit_cache_redis()
    raw = await r.hget(key, "meta")
    if not raw:
        return None
    meta = json.loads(raw)
    meta["age_seconds"] = _age(meta)
    meta["expires_in_seconds"] = await r.ttl(key)
    meta["stale_reason"] = (
        None if meta.get("version") == version
        else "Captured by an older version of the audit — refresh to re-capture"
    )
    return meta


async def _read(key: str, version: int) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    meta = await read_meta(key, version)
    if meta is None or meta.get("stale_reason"):
        return None, meta
    r = await get_audit_cache_redis()
    blob = await r.hget(key, "data")
    return (_unpack(blob) if blob else None), meta


# ── SZ side ───────────────────────────────────────────────────────────

async def save_sz(controller_id: int, zone_id: str, snapshot: SZMigrationSnapshot) -> Dict[str, Any]:
    data = strip_credentials(snapshot.model_dump(mode="json"))
    return await _write(sz_key(controller_id, zone_id), {
        "side": "sz", "version": SZ_CAPTURE_VERSION, "zone_id": zone_id,
        "zone_name": snapshot.zone.name, "wlans": len(snapshot.wlans), "aps": len(snapshot.aps),
    }, data)


async def load_sz(controller_id: int, zone_id: str) -> Tuple[Optional[SZMigrationSnapshot], Optional[Dict[str, Any]]]:
    data, meta = await _read(sz_key(controller_id, zone_id), SZ_CAPTURE_VERSION)
    return (SZMigrationSnapshot.model_validate(data) if data else None), meta


# ── R1 side ───────────────────────────────────────────────────────────

async def save_r1(controller_id: int, tenant_id: Optional[str], venue_id: str,
                  bundle: Dict[str, Any]) -> Dict[str, Any]:
    """bundle: inventory (dict), network_details, auth_facts, venue_settings."""
    inv = bundle.get("inventory") or {}
    return await _write(r1_key(controller_id, tenant_id, venue_id), {
        "side": "r1", "version": R1_CAPTURE_VERSION, "venue_id": venue_id,
        "venue_name": inv.get("venue_name"), "networks": len(inv.get("wifi_networks") or []),
        "aps": len(inv.get("aps") or []),
        "live_reads": bundle.get("auth_facts") is not None,
    }, strip_credentials(bundle))


async def load_r1(controller_id: int, tenant_id: Optional[str], venue_id: str
                  ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    return await _read(r1_key(controller_id, tenant_id, venue_id), R1_CAPTURE_VERSION)


# ── Status / clear ────────────────────────────────────────────────────

async def status(sz_controller_id: Optional[int], zone_id: Optional[str],
                 r1_controller_id: Optional[int], tenant_id: Optional[str],
                 venue_id: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"sz": None, "r1": None, "ttl_days": TTL_SECONDS // 86400}
    if sz_controller_id and zone_id:
        out["sz"] = await read_meta(sz_key(sz_controller_id, zone_id), SZ_CAPTURE_VERSION)
    if r1_controller_id and venue_id:
        out["r1"] = await read_meta(r1_key(r1_controller_id, tenant_id, venue_id), R1_CAPTURE_VERSION)
    return out


async def clear(*keys: str) -> int:
    keys = tuple(k for k in keys if k)
    if not keys:
        return 0
    r = await get_audit_cache_redis()
    return await r.delete(*keys)
