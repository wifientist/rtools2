"""
SmartZone Event / Alarm Service

Handles querying events and alarms from SmartZone controllers.
Supports the common_queryCriteriaSuperSet filter structure used by
both v11_1 and v13_1 (endpoints are identical across versions).
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class EventService:
    def __init__(self, client):
        self.client = client  # back-reference to main SZClient

    async def query_events(
        self,
        filters: Optional[list[dict]] = None,
        extra_filters: Optional[list[dict]] = None,
        extra_not_filters: Optional[list[dict]] = None,
        extra_time_range: Optional[dict] = None,
        page: int = 1,
        limit: int = 100,
        sort_info: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Query events using POST /alert/event/list.

        Args:
            filters: Scope filters (ZONE, APGROUP, AP, etc.)
            extra_filters: Attribute filters (SEVERITY, CATEGORY, etc.)
            extra_not_filters: Exclusion filters.
            extra_time_range: Time range dict with start/end epoch ms and field.
            page: Page number (1-based).
            limit: Results per page.
            sort_info: Sort configuration dict.

        Returns:
            Response dict with keys: list, totalCount, hasMore, firstIndex.
        """
        endpoint = f"/{self.client.api_version}/alert/event/list"

        body: Dict[str, Any] = {
            "page": page,
            "limit": limit,
        }
        if filters:
            body["filters"] = filters
        if extra_filters:
            body["extraFilters"] = extra_filters
        if extra_not_filters:
            body["extraNotFilters"] = extra_not_filters
        if extra_time_range:
            body["extraTimeRange"] = extra_time_range
        if sort_info:
            body["sortInfo"] = sort_info

        result = await self.client._request("POST", endpoint, json=body)
        return result

    async def query_alarms(
        self,
        filters: Optional[list[dict]] = None,
        extra_filters: Optional[list[dict]] = None,
        extra_time_range: Optional[dict] = None,
        page: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Query alarms using POST /alert/alarm/list.

        Same filter structure as query_events.
        """
        endpoint = f"/{self.client.api_version}/alert/alarm/list"

        body: Dict[str, Any] = {
            "page": page,
            "limit": limit,
        }
        if filters:
            body["filters"] = filters
        if extra_filters:
            body["extraFilters"] = extra_filters
        if extra_time_range:
            body["extraTimeRange"] = extra_time_range

        result = await self.client._request("POST", endpoint, json=body)
        return result

    async def get_event_summary(
        self,
        filters: Optional[list[dict]] = None,
        extra_time_range: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Get event count summary by severity."""
        endpoint = f"/{self.client.api_version}/alert/eventSummary"

        body: Dict[str, Any] = {}
        if filters:
            body["filters"] = filters
        if extra_time_range:
            body["extraTimeRange"] = extra_time_range

        result = await self.client._request("POST", endpoint, json=body)
        return result

    async def query_events_all_pages(
        self,
        filters: Optional[list[dict]] = None,
        extra_filters: Optional[list[dict]] = None,
        extra_not_filters: Optional[list[dict]] = None,
        extra_time_range: Optional[dict] = None,
        limit: int = 100,
        max_pages: int = 50,
    ) -> list[dict]:
        """
        Fetch all events across pages.

        Returns a flat list of event dicts. Stops when hasMore is False
        or max_pages is reached.
        """
        all_events: list[dict] = []
        page = 1

        while page <= max_pages:
            result = await self.query_events(
                filters=filters,
                extra_filters=extra_filters,
                extra_not_filters=extra_not_filters,
                extra_time_range=extra_time_range,
                page=page,
                limit=limit,
                sort_info={"sortColumn": "insertionTime", "dir": "DESC"},
            )

            events = result.get("list", [])
            all_events.extend(events)

            total = result.get("totalCount", 0)
            has_more = result.get("hasMore", False)

            logger.debug(
                "Events page %d: got %d, total %d, hasMore=%s",
                page, len(events), total, has_more,
            )

            if not has_more or not events:
                break
            page += 1

        logger.info("Fetched %d events across %d pages", len(all_events), page)
        return all_events

    async def query_dfs_events(
        self,
        zone_ids: list[str],
        ap_group_ids: Optional[list[str]] = None,
        start_epoch_ms: Optional[int] = None,
        end_epoch_ms: Optional[int] = None,
        additional_filters: Optional[list[dict]] = None,
    ) -> list[dict]:
        """
        Convenience method to query DFS-related events.

        Builds the appropriate filter structure for DFS event codes
        and delegates to query_events_all_pages.

        Args:
            zone_ids: List of zone IDs to scope the query.
            ap_group_ids: Optional list of AP group IDs for finer scoping.
            start_epoch_ms: Start of time range (epoch milliseconds).
            end_epoch_ms: End of time range (epoch milliseconds).
            additional_filters: Extra filters from config (pass-through).

        Returns:
            List of DFS event dicts from the SZ API.
        """
        # Build scope filters
        filters = []
        for zid in zone_ids:
            filters.append({"type": "ZONE", "value": zid})
        if ap_group_ids:
            for gid in ap_group_ids:
                filters.append({"type": "APGROUP", "value": gid})

        # Build extra filters — DFS event category
        extra_filters = [
            {"type": "CATEGORY", "value": "AP"},
        ]
        if additional_filters:
            extra_filters.extend(additional_filters)

        # Time range
        extra_time_range = None
        if start_epoch_ms is not None and end_epoch_ms is not None:
            extra_time_range = {
                "start": start_epoch_ms,
                "end": end_epoch_ms,
                "field": "insertionTime",
            }

        events = await self.query_events_all_pages(
            filters=filters,
            extra_filters=extra_filters,
            extra_time_range=extra_time_range,
        )

        # Filter for DFS-related event codes client-side
        # 306 = "AP detects interference on radio and switches to another channel"
        # Additional codes can be added as we learn more about DFS event types
        dfs_codes = {306}
        dfs_events = [
            e for e in events
            if e.get("eventCode") in dfs_codes
            or "dfs" in (e.get("activity") or "").lower()
            or "radar" in (e.get("activity") or "").lower()
        ]

        logger.info(
            "Found %d DFS events out of %d total AP events",
            len(dfs_events), len(events),
        )
        return dfs_events
