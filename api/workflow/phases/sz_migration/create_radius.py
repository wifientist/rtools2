"""
SZ → R1 Migration: Phase 1 — Create RADIUS Profiles (Global)

For each unique RADIUS auth/accounting service referenced by AAA WLANs,
find-or-create a corresponding RADIUS server profile in R1.

Stores the mapping (sz_service_id → r1_profile_id) in global_phase_results
so the per-WLAN create_networks phase can link profiles.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from redis_client import get_redis_client
from schemas.sz_migration import SZMigrationSnapshot
from services.sz_migration.radius_choices import (
    PLACEHOLDER_RADIUS_SECRET, RADIUS_SECRET_KEY, referenced_services, sz_server_details,
)


logger = logging.getLogger(__name__)


@register_phase("create_radius_profiles", "Create RADIUS Profiles")
class CreateRadiusProfilesPhase(PhaseExecutor):
    """
    Global phase: find-or-create RADIUS profiles for AAA WLANs.

    Reads the SZ snapshot to get RADIUS server details from referenced objects,
    then creates corresponding profiles in R1.
    """

    class Inputs(BaseModel):
        sz_snapshot_job_id: str

    class Outputs(BaseModel):
        radius_profile_mappings: Dict[str, str] = {}  # sz_ref_key → r1_profile_id
        radius_profiles_created: int = 0
        radius_profiles_reused: int = 0
        skipped: bool = False


    async def execute(self, inputs: Inputs) -> Outputs:
        redis = await get_redis_client()
        secret_key = RADIUS_SECRET_KEY.format(job_id=self.job_id)
        try:
            return await self._execute(inputs, redis, secret_key)
        finally:
            # Secrets typed on Review are single-use: gone once this phase
            # has run, whether it succeeded or not.
            await redis.delete(secret_key)

    async def _execute(self, inputs: Inputs, redis, secret_key: str) -> Outputs:
        snapshot_key = f"sz_migration:extraction:{inputs.sz_snapshot_job_id}:snapshot"
        raw = await redis.get(snapshot_key)
        if not raw:
            raise RuntimeError("SZ snapshot not found")
        snapshot = SZMigrationSnapshot.model_validate_json(raw)

        services = referenced_services(snapshot)
        if not services:
            await self.emit("No RADIUS-backed WLANs — skipping RADIUS profiles")
            return self.Outputs(skipped=True)

        # None = a plan from before Review offered choices: create them all.
        decisions: Optional[Dict[str, Dict[str, Any]]] = self.context.options.get("radius_decisions")
        await self.emit(f"{len(services)} RADIUS service(s) referenced by this zone")

        mappings: Dict[str, str] = {}
        created = reused = skipped = 0

        for i, s in enumerate(services):
            await self.emit_progress(i, len(services), item_name="RADIUS service")
            sid, sz_name = s["id"], s["name"]
            d = {"action": "create"} if decisions is None else decisions.get(sid)
            action = (d or {}).get("action")

            if action == "skip" or d is None:
                skipped += 1
                await self.emit(f"RADIUS '{sz_name}' — not migrated (by choice)")
                await self.record_change("skipped", "radius_profiles", sz_name, detail="Not migrated, by choice")
                continue

            if action == "existing":
                profile_id = d.get("r1_profile_id")
                mappings[sid] = profile_id
                reused += 1
                await self.emit(f"RADIUS '{sz_name}' → existing R1 profile (id={profile_id[:12]})")
                await self.record_change("reused", "radius_profiles", sz_name, resource_id=profile_id,
                                         detail="Existing R1 profile, chosen on Review")
                continue

            # ── create ────────────────────────────────────────────────
            details = sz_server_details(snapshot, sid)
            if details["error"] or not details["primary"]:
                skipped += 1
                await self.emit(
                    f"RADIUS '{sz_name}' has no server address in the snapshot — not created",
                    "warning",
                )
                await self.record_change("skipped", "radius_profiles", sz_name,
                                         detail="No server address in the snapshot")
                continue

            # Same name already in R1: link it rather than create a duplicate.
            # The secret typed on Review is NOT applied to it.
            existing = await asyncio.to_thread(
                self.r1_client.radius_profiles.find_radius_profile_by_name,
                self.tenant_id, details["name"],
            )
            if existing and existing.get("id"):
                mappings[sid] = existing["id"]
                reused += 1
                await self.emit(
                    f"RADIUS '{details['name']}' already exists in R1 — linked it; its "
                    "shared secret was left unchanged",
                    "warning",
                )
                await self.record_change("reused", "radius_profiles", details["name"], resource_id=existing["id"],
                                         detail="Same name already in R1; its shared secret was left unchanged")
                continue

            raw_secret = await redis.hget(secret_key, sid)
            secret = raw_secret.decode() if isinstance(raw_secret, bytes) else raw_secret
            if not secret:
                secret = PLACEHOLDER_RADIUS_SECRET
                await self.emit(
                    f"RADIUS '{details['name']}' — no shared secret given; using a placeholder. "
                    "Set it in R1 before this network can authenticate.",
                    "warning",
                )

            primary, secondary = details["primary"], details["secondary"]
            result = await self.r1_client.radius_profiles.create_radius_profile(
                tenant_id=self.tenant_id,
                name=details["name"],
                primary_ip=primary["ip"],
                primary_port=primary["port"],
                primary_secret=secret,
                secondary_ip=secondary["ip"] if secondary else None,
                secondary_port=secondary["port"] if secondary else 1812,
                secondary_secret=secret if secondary else "",
                profile_type=details["role"],
            )
            secret = None  # don't keep it around longer than the call

            profile_id = (result or {}).get("id")
            if not profile_id:
                raise RuntimeError(f"RADIUS profile creation returned no ID for '{details['name']}'")
            mappings[sid] = profile_id
            created += 1
            await self.emit(f"Created RADIUS profile '{details['name']}' (id={profile_id[:12]})")
            # Only profiles this run created are tracked: a linked, pre-existing
            # profile belongs to the tenant, not to this migration.
            await self.track_resource("radius_profiles", {
                "id": profile_id, "name": details["name"], "sz_service_id": sid,
            })

        await self.emit_progress(len(services), len(services), item_name="RADIUS service")
        await self.emit(
            f"RADIUS profiles: {created} created, {reused} linked existing, {skipped} not migrated",
            "success",
        )
        return self.Outputs(
            radius_profile_mappings=mappings,
            radius_profiles_created=created,
            radius_profiles_reused=reused,
        )
