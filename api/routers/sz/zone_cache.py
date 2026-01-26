"""
Zone Cache Manager

Handles zone-level caching for SmartZone audits.
Allows incremental refreshes and fast retrieval of previously audited zones.

Cache Structure:
- sz_audit:cache:{controller_id}:zone:{zone_id} → ZoneAudit JSON (TTL: configurable)
- sz_audit:cache:{controller_id}:meta → {last_audit_time, zone_ids[], version}
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Cache TTL settings
ZONE_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
CACHE_META_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days for metadata


class RefreshMode(str, Enum):
    """Audit refresh modes"""
    FULL = "full"  # Force refresh everything
    INCREMENTAL = "incremental"  # Use cached zones if fresh, only audit stale/new
    CACHED_ONLY = "cached_only"  # Return cached data immediately, no API calls
    SWITCHES_ONLY = "switches_only"  # Use cached zones, but refresh switches


class ZoneCacheManager:
    """Manages zone-level caching for SmartZone audits"""

    def __init__(self, redis_client: redis.Redis, controller_id: int):
        """
        Initialize zone cache manager

        Args:
            redis_client: Async Redis client
            controller_id: Controller ID for cache key namespacing
        """
        self.redis = redis_client
        self.controller_id = controller_id
        self.cache_prefix = f"sz_audit:cache:{controller_id}"

    def _zone_key(self, zone_id: str) -> str:
        """Get Redis key for a zone"""
        return f"{self.cache_prefix}:zone:{zone_id}"

    def _meta_key(self) -> str:
        """Get Redis key for cache metadata"""
        return f"{self.cache_prefix}:meta"

    async def get_cached_zone(self, zone_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a cached zone audit if it exists and is fresh

        Args:
            zone_id: Zone ID to retrieve

        Returns:
            ZoneAudit dict or None if not cached/expired
        """
        key = self._zone_key(zone_id)
        data = await self.redis.get(key)

        if not data:
            return None

        try:
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to parse cached zone {zone_id}: {e}")
            return None

    async def get_cached_zones(self, zone_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Get multiple cached zones in a single operation (MGET)

        Args:
            zone_ids: List of zone IDs to retrieve

        Returns:
            Dict of zone_id -> ZoneAudit dict (only includes found zones)
        """
        if not zone_ids:
            return {}

        keys = [self._zone_key(zid) for zid in zone_ids]
        results = await self.redis.mget(keys)

        cached = {}
        for zone_id, data in zip(zone_ids, results):
            if data:
                try:
                    cached[zone_id] = json.loads(data)
                except Exception as e:
                    logger.debug(f"Failed to parse cached zone {zone_id}: {e}")

        logger.info(f"Cache hit: {len(cached)}/{len(zone_ids)} zones")
        return cached

    async def cache_zone(
        self,
        zone_id: str,
        zone_audit: Dict[str, Any],
        ttl: int = ZONE_CACHE_TTL_SECONDS
    ) -> bool:
        """
        Cache a zone audit result

        Args:
            zone_id: Zone ID
            zone_audit: ZoneAudit dict to cache
            ttl: Cache TTL in seconds

        Returns:
            True if cached successfully
        """
        key = self._zone_key(zone_id)

        # Add cache metadata
        zone_audit['_cached_at'] = datetime.utcnow().isoformat()

        try:
            await self.redis.setex(key, ttl, json.dumps(zone_audit))
            return True
        except Exception as e:
            logger.warning(f"Failed to cache zone {zone_id}: {e}")
            return False

    async def cache_zones_bulk(
        self,
        zones: Dict[str, Dict[str, Any]],
        ttl: int = ZONE_CACHE_TTL_SECONDS
    ) -> int:
        """
        Cache multiple zones in a pipeline

        Args:
            zones: Dict of zone_id -> ZoneAudit dict
            ttl: Cache TTL in seconds

        Returns:
            Number of zones cached
        """
        if not zones:
            return 0

        cached_at = datetime.utcnow().isoformat()
        pipe = self.redis.pipeline()

        for zone_id, zone_audit in zones.items():
            key = self._zone_key(zone_id)
            zone_audit['_cached_at'] = cached_at
            pipe.setex(key, ttl, json.dumps(zone_audit))

        try:
            await pipe.execute()
            logger.info(f"Bulk cached {len(zones)} zones")
            return len(zones)
        except Exception as e:
            logger.warning(f"Failed to bulk cache zones: {e}")
            return 0

    async def get_cache_meta(self) -> Optional[Dict[str, Any]]:
        """
        Get cache metadata

        Returns:
            Cache metadata dict or None
        """
        key = self._meta_key()
        data = await self.redis.get(key)

        if not data:
            return None

        try:
            return json.loads(data)
        except Exception:
            return None

    async def update_cache_meta(
        self,
        zone_ids: List[str],
        partial: bool = False
    ) -> bool:
        """
        Update cache metadata after an audit

        Args:
            zone_ids: List of zone IDs that were cached
            partial: True if this was an incremental/partial update
                     When partial, merges with existing zone IDs to preserve
                     zones from previous audits that weren't processed this time

        Returns:
            True if updated successfully
        """
        key = self._meta_key()

        # For partial updates, merge with existing zone IDs to preserve
        # zones that were cached previously but not processed in this run
        # (e.g., when audit is cancelled partway through)
        final_zone_ids = zone_ids
        if partial:
            existing_meta = await self.get_cache_meta()
            if existing_meta and existing_meta.get('zone_ids'):
                # Merge: keep existing zones, add/update new ones
                existing_set = set(existing_meta['zone_ids'])
                new_set = set(zone_ids)
                final_zone_ids = list(existing_set | new_set)
                logger.info(f"Cache meta merge: {len(existing_set)} existing + {len(new_set)} current = {len(final_zone_ids)} total")

        meta = {
            'controller_id': self.controller_id,
            'last_audit_time': datetime.utcnow().isoformat(),
            'zone_count': len(final_zone_ids),
            'zone_ids': final_zone_ids,
            'partial_update': partial,
            'version': 1  # For future schema changes
        }

        try:
            await self.redis.setex(key, CACHE_META_TTL_SECONDS, json.dumps(meta))
            return True
        except Exception as e:
            logger.warning(f"Failed to update cache meta: {e}")
            return False

    async def invalidate_zone(self, zone_id: str) -> bool:
        """
        Invalidate a specific zone's cache

        Args:
            zone_id: Zone ID to invalidate

        Returns:
            True if deleted
        """
        key = self._zone_key(zone_id)
        result = await self.redis.delete(key)
        return result > 0

    async def invalidate_all(self) -> int:
        """
        Invalidate all cached zones for this controller

        Returns:
            Number of keys deleted
        """
        pattern = f"{self.cache_prefix}:*"
        count = 0

        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
            count += 1

        logger.info(f"Invalidated {count} cached keys for controller {self.controller_id}")
        return count

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for this controller

        Returns:
            Dict with cache stats
        """
        meta = await self.get_cache_meta()

        # Count cached zones
        pattern = f"{self.cache_prefix}:zone:*"
        cached_count = 0
        async for _ in self.redis.scan_iter(match=pattern):
            cached_count += 1

        return {
            'controller_id': self.controller_id,
            'cached_zones': cached_count,
            'last_audit_time': meta.get('last_audit_time') if meta else None,
            'last_zone_count': meta.get('zone_count') if meta else None,
            'has_cache': cached_count > 0
        }

    async def get_zone_cache_age(self, zone_id: str) -> Optional[int]:
        """
        Get the age of a cached zone in seconds

        Args:
            zone_id: Zone ID to check

        Returns:
            Age in seconds or None if not cached
        """
        cached = await self.get_cached_zone(zone_id)
        if not cached:
            return None

        cached_at = cached.get('_cached_at')
        if not cached_at:
            return None

        try:
            cached_time = datetime.fromisoformat(cached_at)
            age = (datetime.utcnow() - cached_time).total_seconds()
            return int(age)
        except Exception:
            return None

    async def list_cached_zones(self) -> List[Dict[str, Any]]:
        """
        List all cached zones with summary info for selection UI

        Returns:
            List of dicts with zone_id, zone_name, cached_at, ap_count, wlan_count
        """
        meta = await self.get_cache_meta()
        if not meta or not meta.get('zone_ids'):
            return []

        zone_ids = meta['zone_ids']
        cached_zones = await self.get_cached_zones(zone_ids)

        zones_list = []
        for zone_id, zone_data in cached_zones.items():
            zones_list.append({
                'zone_id': zone_id,
                'zone_name': zone_data.get('zone_name', 'Unknown'),
                'domain_name': zone_data.get('domain_name', 'Unknown'),
                'cached_at': zone_data.get('_cached_at'),
                'ap_count': zone_data.get('ap_status', {}).get('total', 0) if isinstance(zone_data.get('ap_status'), dict) else 0,
                'wlan_count': zone_data.get('wlan_count', 0)
            })

        # Sort by zone name
        zones_list.sort(key=lambda z: z['zone_name'].lower())
        return zones_list

    # =========================================================================
    # Switch Group Caching
    # =========================================================================

    def _switch_groups_key(self) -> str:
        """Get Redis key for switch groups cache"""
        return f"{self.cache_prefix}:switch_groups"

    async def cache_switch_groups(
        self,
        switch_groups: Dict[str, List[Dict[str, Any]]],
        switches: List[Dict[str, Any]] = None
    ) -> None:
        """
        Cache all switch groups with their switch data.

        Args:
            switch_groups: Dict of domain_id -> list of switch group dicts
            switches: Optional list of all switches (to compute counts per group)
        """
        # Build switch count per group from switches if provided
        switch_counts = {}  # sg_id -> {total, online, offline, firmware_versions}
        if switches:
            for s in switches:
                sg_id = s.get("switchGroupId") or s.get("groupId") or "ungrouped"
                if sg_id not in switch_counts:
                    switch_counts[sg_id] = {
                        "total": 0,
                        "online": 0,
                        "offline": 0,
                        "firmware_versions": {}
                    }
                switch_counts[sg_id]["total"] += 1

                # Count online/offline
                status = s.get("status", "").upper()
                if status in ("ONLINE", "UP", "CONNECTED"):
                    switch_counts[sg_id]["online"] += 1
                else:
                    switch_counts[sg_id]["offline"] += 1

                # Track firmware versions
                fw = s.get("firmwareVersion") or s.get("firmware") or "Unknown"
                switch_counts[sg_id]["firmware_versions"][fw] = \
                    switch_counts[sg_id]["firmware_versions"].get(fw, 0) + 1

        # Enrich switch groups with counts
        enriched_groups = {}
        for domain_id, groups in switch_groups.items():
            enriched_groups[domain_id] = []
            for sg in groups:
                sg_id = sg.get("id")
                counts = switch_counts.get(sg_id, {})

                # If we computed counts from switches, use those
                # Otherwise preserve existing counts from the switch group
                if counts:
                    enriched_sg = {
                        "id": sg_id,
                        "name": sg.get("name", sg_id),
                        "switch_count": counts.get("total", 0),
                        "switches_online": counts.get("online", 0),
                        "switches_offline": counts.get("offline", 0),
                        "firmware_versions": [
                            {"version": v, "count": c}
                            for v, c in counts.get("firmware_versions", {}).items()
                        ]
                    }
                else:
                    # Preserve existing data from the switch group
                    enriched_sg = {
                        "id": sg_id,
                        "name": sg.get("name", sg_id),
                        "switch_count": sg.get("switch_count", 0),
                        "switches_online": sg.get("switches_online", 0),
                        "switches_offline": sg.get("switches_offline", 0),
                        "firmware_versions": sg.get("firmware_versions", [])
                    }
                enriched_groups[domain_id].append(enriched_sg)

        # Calculate total switches
        if switches:
            total_switches = len(switches)
        else:
            # Sum from enriched groups if switches not provided
            total_switches = sum(
                sg.get("switch_count", 0)
                for groups in enriched_groups.values()
                for sg in groups
            )

        # Cache to Redis
        key = self._switch_groups_key()
        cache_data = {
            "switch_groups": enriched_groups,
            "total_switches": total_switches,
            "_cached_at": datetime.utcnow().isoformat()
        }

        await self.redis.set(
            key,
            json.dumps(cache_data),
            ex=ZONE_CACHE_TTL_SECONDS
        )

        total_groups = sum(len(groups) for groups in enriched_groups.values())
        logger.info(
            f"Cached {total_groups} switch groups across {len(enriched_groups)} domains "
            f"({len(switches) if switches else 0} switches)"
        )

    async def get_cached_switch_groups(self) -> Optional[Dict[str, Any]]:
        """
        Get cached switch groups.

        Returns:
            Dict with 'switch_groups' (domain_id -> list of switch group dicts),
            'total_switches', and '_cached_at', or None if not cached
        """
        key = self._switch_groups_key()
        data = await self.redis.get(key)

        if not data:
            return None

        try:
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to parse cached switch groups: {e}")
            return None

    async def clear_switch_groups_cache(self) -> None:
        """Clear the switch groups cache"""
        key = self._switch_groups_key()
        await self.redis.delete(key)
