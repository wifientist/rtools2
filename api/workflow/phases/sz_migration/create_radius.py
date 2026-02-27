"""
SZ → R1 Migration: Phase 1 — Create RADIUS Profiles (Global)

For each unique RADIUS auth/accounting service referenced by AAA WLANs,
find-or-create a corresponding RADIUS server profile in R1.

Stores the mapping (sz_service_id → r1_profile_id) in global_phase_results
so the per-WLAN create_networks phase can link profiles.
"""

import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from workflow.phases.registry import register_phase
from workflow.phases.phase_executor import PhaseExecutor
from redis_client import get_redis_client
from schemas.sz_migration import SZMigrationSnapshot

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
        # Load SZ snapshot
        redis = await get_redis_client()
        snapshot_key = f"sz_migration:extraction:{inputs.sz_snapshot_job_id}:snapshot"
        raw = await redis.get(snapshot_key)
        if not raw:
            raise RuntimeError("SZ snapshot not found")
        snapshot = SZMigrationSnapshot.model_validate_json(raw)

        # Find AAA WLANs that need RADIUS profiles
        aaa_wlans = [w for w in snapshot.wlans if w.auth_service_id]
        if not aaa_wlans:
            await self.emit("No AAA WLANs — skipping RADIUS profile creation")
            return self.Outputs(skipped=True)

        # Collect unique auth service IDs
        unique_auth_ids = set()
        for wlan in aaa_wlans:
            if wlan.auth_service_id:
                unique_auth_ids.add(wlan.auth_service_id)
            if wlan.accounting_service_id:
                unique_auth_ids.add(wlan.accounting_service_id)

        await self.emit(
            f"Found {len(aaa_wlans)} AAA WLANs with "
            f"{len(unique_auth_ids)} unique RADIUS references"
        )

        # For each unique RADIUS reference, get details from snapshot and create in R1
        mappings: Dict[str, str] = {}
        created = 0
        reused = 0

        for service_id in unique_auth_ids:
            # Look up the chased reference in the snapshot
            auth_ref = snapshot.referenced_objects.get(f"auth_service:{service_id}")
            acct_ref = snapshot.referenced_objects.get(f"accounting_service:{service_id}")
            ref = auth_ref or acct_ref

            if not ref or not ref.raw:
                await self.emit(
                    f"RADIUS service {service_id[:12]} not found in snapshot — "
                    f"will need manual configuration",
                    "warning",
                )
                continue

            raw_service = ref.raw
            if raw_service.get("_error"):
                await self.emit(
                    f"RADIUS service {service_id[:12]} had extraction error — skipping",
                    "warning",
                )
                continue

            # Extract server details
            name = raw_service.get("name", f"migrated-radius-{service_id[:8]}")
            primary_ip = None
            primary_port = 1812
            primary_secret = ""

            # SZ stores RADIUS server details in various formats
            primary = raw_service.get("primary") or {}
            if isinstance(primary, dict):
                primary_ip = primary.get("ip") or primary.get("server")
                primary_port = primary.get("port", 1812)
                primary_secret = primary.get("sharedSecret", "")
            elif raw_service.get("ip"):
                primary_ip = raw_service.get("ip")
                primary_port = raw_service.get("port", 1812)
                primary_secret = raw_service.get("sharedSecret", "")

            if not primary_ip:
                await self.emit(
                    f"RADIUS '{name}' has no server IP — skipping",
                    "warning",
                )
                continue

            # SZ API never exposes shared secrets — use placeholder
            if not primary_secret:
                primary_secret = "CHANGE_ME_after_migration"
                await self.emit(
                    f"RADIUS '{name}' shared secret not available from SZ — "
                    f"using placeholder (manual update required after migration)",
                    "warning",
                )

            # Determine profile type
            profile_type = "ACCOUNTING" if acct_ref and not auth_ref else "AUTHENTICATION"

            # Find-or-create in R1
            await self.emit(f"Finding/creating RADIUS profile '{name}'...")
            result = await self.r1_client.radius_profiles.find_or_create_radius_profile(
                tenant_id=self.tenant_id,
                name=name,
                primary_ip=primary_ip,
                primary_port=primary_port,
                primary_secret=primary_secret,
                profile_type=profile_type,
            )

            if result and result.get("id"):
                r1_profile_id = result["id"]
                mappings[service_id] = r1_profile_id

                # Check if reused or created (find_or_create logs this)
                was_existing = bool(
                    self.r1_client.radius_profiles.find_radius_profile_by_name(
                        self.tenant_id, name
                    )
                )
                if was_existing:
                    reused += 1
                    await self.emit(f"Reused RADIUS profile '{name}' (id={r1_profile_id[:12]})")
                else:
                    created += 1
                    await self.emit(f"Created RADIUS profile '{name}' (id={r1_profile_id[:12]})")

                await self.track_resource("radius_profiles", {
                    "id": r1_profile_id,
                    "name": name,
                    "sz_service_id": service_id,
                })

        await self.emit(
            f"RADIUS profiles: {created} created, {reused} reused, "
            f"{len(unique_auth_ids) - created - reused} skipped",
            "success",
        )

        return self.Outputs(
            radius_profile_mappings=mappings,
            radius_profiles_created=created,
            radius_profiles_reused=reused,
        )
