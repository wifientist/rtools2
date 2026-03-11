"""
Redis CRUD operations for Pop and Swap records.

Each swap pair is stored as a Redis hash with a 7-day TTL.
Index sets track active swaps for efficient polling and company-scoped queries.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

SWAP_KEY_PREFIX = "pop_swap:"
ACTIVE_SET_KEY = "pop_swap:active"
COMPANY_SET_PREFIX = "pop_swap:by_company:"
CONTROLLER_SET_PREFIX = "pop_swap:by_controller:"

DEFAULT_TTL_DAYS = 7
EXTEND_DAYS = 3
MAX_TTL_DAYS = 7


class SwapStore:
    """Redis-backed storage for Pop and Swap records."""

    def __init__(self, redis_client):
        self.redis = redis_client

    # --- Create ---

    async def create_swap(
        self,
        company_id: int,
        controller_id: int,
        tenant_id: str,
        venue_id: str,
        old_serial: str,
        new_serial: str,
        ap_name: str,
        ap_group_id: str,
        ap_group_name: str,
        config_data: dict,
        created_by: int,
        cleanup_action: str = "none",
    ) -> str:
        """Create a new swap record. Returns the swap_id."""
        swap_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=DEFAULT_TTL_DAYS)
        ttl_seconds = int(DEFAULT_TTL_DAYS * 86400)

        record = {
            "swap_id": swap_id,
            "company_id": str(company_id),
            "controller_id": str(controller_id),
            "tenant_id": tenant_id,
            "venue_id": venue_id,
            "old_serial": old_serial,
            "new_serial": new_serial,
            "ap_name": ap_name or "",
            "ap_group_id": ap_group_id or "",
            "ap_group_name": ap_group_name or "",
            "config_data": json.dumps(config_data),
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "created_by": str(created_by),
            "sync_attempts": "0",
            "last_attempt_at": "",
            "applied_at": "",
            "apply_results": "",
            "cleanup_action": cleanup_action,
        }

        key = f"{SWAP_KEY_PREFIX}{swap_id}"

        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=record)
        pipe.expire(key, ttl_seconds)
        pipe.sadd(ACTIVE_SET_KEY, swap_id)
        pipe.sadd(f"{COMPANY_SET_PREFIX}{company_id}", swap_id)
        pipe.sadd(f"{CONTROLLER_SET_PREFIX}{controller_id}", swap_id)
        await pipe.execute()

        logger.info(f"Created swap record {swap_id}: {old_serial} -> {new_serial}")
        return swap_id

    # --- Read ---

    async def get_swap(self, swap_id: str) -> Optional[Dict[str, Any]]:
        """Get a single swap record by ID."""
        key = f"{SWAP_KEY_PREFIX}{swap_id}"
        data = await self.redis.hgetall(key)
        if not data:
            return None
        return self._deserialize(data)

    async def list_swaps_for_company(self, company_id: int) -> List[Dict[str, Any]]:
        """List all swap records for a company (all statuses)."""
        swap_ids = await self.redis.smembers(f"{COMPANY_SET_PREFIX}{company_id}")
        return await self._get_many(swap_ids)

    async def list_all_swaps(self) -> List[Dict[str, Any]]:
        """List all swap records across all companies (super admin)."""
        # Gather from all company sets — scan for company keys
        all_ids = set()
        async for key in self.redis.scan_iter(match=f"{COMPANY_SET_PREFIX}*"):
            ids = await self.redis.smembers(key)
            all_ids.update(ids)
        return await self._get_many(all_ids)

    async def list_active_swaps(self) -> List[Dict[str, Any]]:
        """List only active (pending/failed) swaps for background polling."""
        swap_ids = await self.redis.smembers(ACTIVE_SET_KEY)
        return await self._get_many(swap_ids)

    # --- Update ---

    async def update_status(self, swap_id: str, status: str, **extra_fields):
        """Update swap status and optional extra fields."""
        key = f"{SWAP_KEY_PREFIX}{swap_id}"
        updates = {"status": status}
        for field, value in extra_fields.items():
            if isinstance(value, dict):
                updates[field] = json.dumps(value)
            else:
                updates[field] = str(value) if value is not None else ""
        await self.redis.hset(key, mapping=updates)

    async def increment_sync_attempts(self, swap_id: str):
        """Increment sync_attempts and update last_attempt_at."""
        key = f"{SWAP_KEY_PREFIX}{swap_id}"
        now = datetime.now(timezone.utc).isoformat()
        pipe = self.redis.pipeline()
        pipe.hincrby(key, "sync_attempts", 1)
        pipe.hset(key, "last_attempt_at", now)
        await pipe.execute()

    async def mark_completed(self, swap_id: str, apply_results: dict):
        """Mark a swap as completed and remove from active set."""
        now = datetime.now(timezone.utc).isoformat()
        await self.update_status(
            swap_id, "completed",
            applied_at=now,
            apply_results=apply_results,
        )
        await self.redis.srem(ACTIVE_SET_KEY, swap_id)

    async def mark_failed(self, swap_id: str, apply_results: dict):
        """Mark a swap as failed (stays in active set for retry)."""
        now = datetime.now(timezone.utc).isoformat()
        await self.update_status(
            swap_id, "failed",
            last_attempt_at=now,
            apply_results=apply_results,
        )

    async def mark_expired(self, swap_id: str):
        """Mark a swap as expired and remove from active set."""
        await self.update_status(swap_id, "expired")
        await self.redis.srem(ACTIVE_SET_KEY, swap_id)

    async def extend_window(self, swap_id: str) -> Optional[str]:
        """
        Extend the migration window by EXTEND_DAYS, capped at MAX_TTL_DAYS from now.
        Returns the new expires_at ISO string, or None if swap not found.
        """
        swap = await self.get_swap(swap_id)
        if not swap:
            return None

        now = datetime.now(timezone.utc)
        current_expires = datetime.fromisoformat(swap["expires_at"])

        # Add EXTEND_DAYS to current expiry
        new_expires = current_expires + timedelta(days=EXTEND_DAYS)

        # Cap at MAX_TTL_DAYS from now
        max_expires = now + timedelta(days=MAX_TTL_DAYS)
        if new_expires > max_expires:
            new_expires = max_expires

        new_ttl = int((new_expires - now).total_seconds())
        if new_ttl <= 0:
            return None

        key = f"{SWAP_KEY_PREFIX}{swap_id}"
        pipe = self.redis.pipeline()
        pipe.hset(key, "expires_at", new_expires.isoformat())
        pipe.expire(key, new_ttl)
        await pipe.execute()

        logger.info(f"Extended swap {swap_id} to {new_expires.isoformat()} (TTL: {new_ttl}s)")
        return new_expires.isoformat()

    # --- Delete ---

    async def delete_swap(self, swap_id: str) -> bool:
        """Delete a swap record and remove from all indexes."""
        swap = await self.get_swap(swap_id)
        if not swap:
            return False

        key = f"{SWAP_KEY_PREFIX}{swap_id}"
        company_id = swap.get("company_id")
        controller_id = swap.get("controller_id")

        pipe = self.redis.pipeline()
        pipe.delete(key)
        pipe.srem(ACTIVE_SET_KEY, swap_id)
        if company_id:
            pipe.srem(f"{COMPANY_SET_PREFIX}{company_id}", swap_id)
        if controller_id:
            pipe.srem(f"{CONTROLLER_SET_PREFIX}{controller_id}", swap_id)
        await pipe.execute()

        logger.info(f"Deleted swap record {swap_id}")
        return True

    # --- Helpers ---

    async def _get_many(self, swap_ids) -> List[Dict[str, Any]]:
        """Fetch multiple swap records by IDs, filtering out expired/missing."""
        results = []
        for swap_id in swap_ids:
            swap = await self.get_swap(swap_id)
            if swap:
                results.append(swap)
            else:
                # Record expired from Redis TTL — clean up index references
                await self.redis.srem(ACTIVE_SET_KEY, swap_id)
        # Sort by created_at descending (newest first)
        results.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return results

    def _deserialize(self, data: dict) -> dict:
        """Deserialize Redis hash data into a proper dict."""
        result = dict(data)
        # Parse JSON fields
        for json_field in ("config_data", "apply_results"):
            if result.get(json_field):
                try:
                    result[json_field] = json.loads(result[json_field])
                except (json.JSONDecodeError, TypeError):
                    pass
        # Parse int fields
        for int_field in ("company_id", "controller_id", "created_by", "sync_attempts"):
            if result.get(int_field):
                try:
                    result[int_field] = int(result[int_field])
                except (ValueError, TypeError):
                    pass
        return result
