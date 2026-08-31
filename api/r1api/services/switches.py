"""
R1 switch crawl service (WiredWiz).

READ-ONLY BY CONSTRUCTION. Every method here issues GET or `*/query` POST
requests. Nothing in this module creates, updates, deletes, reboots, syncs, or
pushes a CLI template. `adminPassword` is deliberately absent from every field
list -- it exists on R1's switch DTO and must never appear in a response body.

Pagination notes, verified live against a 24,470-port tenant:
  * the query layer is Elasticsearch: `from + size` may not exceed 10000, so
    paging can never reach past row 10000 regardless of totalCount;
  * `page: 1` is an ALIAS for `page: 0` -- both return the first chunk.
    Distinct pages resume at 2. Iteration must go 0, 2, 3, 4, ...
  * a totalCount of exactly 10000 is the ceiling reporting itself, not a count;
  * WITHOUT an explicit `sortField` the row order is not stable between pages,
    so successive pages return overlapping random subsets and a dedupe-on-read
    crawler silently loses coverage. Observed: a 4,746-port venue collected
    2,962 rows, and a 1,176-port venue collected exactly 1,000. Every paged
    query here pins a sort key.

So the crawl fans out per venue, and per switch inside any venue that would
still overflow. See plans/wiredwiz.md.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ES_WINDOW = 10000
PAGE_SIZE = 1000

SWITCH_FIELDS = [
    "id", "name", "serialNumber", "switchMac", "model", "family", "firmwareVersion",
    "ipAddress", "deviceStatus", "venueId", "venueName", "uptime", "numOfPorts",
    "numOfUnits", "isStack", "clientCount", "cpu", "memory", "syncedSwitchConfig",
    # PoE budget at the chassis level
    "poeTotal", "poeFree", "poeUtilization",
    # Hardware health -- JSON-encoded strings describing PSU and fan groups
    "powerSupplyGroups", "fanGroups", "modules", "rearModule",
    # Stack composition and health
    "stackMembers", "stackMembersStatus", "unitSerialNumbers", "activeSerial",
    # Operational signals
    "operationalWarning", "alerts", "cliApplied", "configReady",
    # Management addressing, for consistency checks
    "defaultGateway", "dns", "subnetMask", "staticOrDynamic",
    "switchType", "veCount", "tags",
]

PORT_FIELDS = [
    "id", "switchMac", "switchName", "switchSerial", "portIdentifier",
    "portIdentifierFormatted", "name",
    "status", "adminStatus", "portSpeed", "portSpeedCapacity", "mediaType",
    "broadcastIn", "broadcastOut", "multicastIn", "multicastOut", "rx", "tx",
    "crcErr", "inErr", "outErr", "inDiscard", "signalIn", "signalOut",
    "spanningTreeStatus", "errorDisableStatus",
    "neighborName", "neighborMacAddress", "neighborPortMacAddress",
    "lagId", "lagName", "lagStatus", "usedInFormingStack", "stackingNeighborPort",
    "unTaggedVlan", "vlanIds", "cloudPort", "venueId",
    # PoE per port -- needed for budget and overdraw analysis
    "isPoeSupported", "poeEnabled", "poeType", "poeUsed", "poeTotal", "poeUsage",
    # Physical media -- optic/connector mismatches show up as CRC errors
    "opticsType", "portConnectorType",
    # Applied policy, for consistency checks
    "switchPortProfileName", "switchPortProfileType",
    "ingressAclName", "egressAclName", "stickyMacAclAllowCount",
    # Stack unit health
    "unitState", "unitStatus", "switchUnitId",
    "tags",
]

MAC_FIELDS = [
    "clientMac", "clientIpv4Addr", "clientVlan", "vlanName", "clientName",
    "clientType", "switchMac", "switchName", "switchSerialNumber", "switchPort",
    "switchPortId", "dhcpClientHostName", "venueId",
]


def _pages(total: int):
    """
    Page numbers that actually return fresh rows, given the page-1 alias and the
    10000-row window. Yields 0, 2, 3, ... up to whatever the window allows.
    """
    reachable = min(total, ES_WINDOW)
    needed = (reachable + PAGE_SIZE - 1) // PAGE_SIZE
    yield 0
    for p in range(2, needed + 1):
        yield p


class SwitchService:
    def __init__(self, client):
        self.client = client  # back-reference to main R1Client
        # One entry per paged query issued, so a caller can prove the crawl was
        # complete rather than assume it. Reset with reset_completeness().
        self.last_completeness: List[Dict[str, Any]] = []

    def reset_completeness(self):
        self.last_completeness = []

    def completeness_report(self) -> Dict[str, Any]:
        """Summary of the queries issued since the last reset."""
        bad = [c for c in self.last_completeness if not c["complete"]]
        return {
            "queries": len(self.last_completeness),
            "incomplete": len(bad),
            "expected": sum(c["expected"] for c in self.last_completeness),
            "collected": sum(c["collected"] for c in self.last_completeness),
            "shortfalls": bad,
        }

    def _supersede(self, path: str, filters: Dict[str, Any]) -> None:
        """
        Drop a window-capped completeness entry that a narrower fan-out replaced.

        When a venue query hits the ES window we refetch that venue per switch.
        The capped venue query is then no longer the coverage claim -- the
        per-switch queries are -- so leaving it in the report would show a
        permanent shortfall on every venue big enough to overflow.
        """
        for i in range(len(self.last_completeness) - 1, -1, -1):
            c = self.last_completeness[i]
            if c["path"] == path and c["filters"] == filters and c.get("windowCapped"):
                del self.last_completeness[i]
                return

    def _fanout_by_switch(self, venue_id: str, batch: List[Dict[str, Any]],
                          tenant_id: Optional[str], fetch, identity, label: str) -> int:
        """
        Refetch an overflowed venue one switch at a time, appending only rows the
        capped venue result did not already have.

        The capped rows are KEPT as a seed rather than discarded: they can cover
        switches that `online_only` inventory leaves out, so throwing them away
        would trade one coverage gap for another.

        Returns the number of rows recovered. Zero means the fan-out bought us
        nothing -- the caller must leave the venue marked incomplete rather than
        pretend the ceiling was cleared.
        """
        logger.info("venue %s overflowed the %d-row ES window; refetching per switch",
                    venue_id, ES_WINDOW)
        seen = {identity(r) for r in batch}
        added = 0
        for sw in self.list_switches(tenant_id, venue_id=venue_id, online_only=True):
            mac = sw.get("switchMac") or sw.get("id")
            if not mac:
                continue
            for row in fetch(mac, tenant_id):
                ident = identity(row)
                if ident in seen:
                    continue
                seen.add(ident)
                batch.append(row)
                added += 1
        if added:
            logger.info("venue %s: per-switch refetch recovered %d extra %s (%d total)",
                        venue_id, added, label, len(batch))
        else:
            logger.warning("venue %s: per-switch refetch recovered no extra %s -- the "
                           "venue result is still capped at %d rows",
                           venue_id, label, ES_WINDOW)
        return added

    # ---------- primitives ----------

    def _query(self, path: str, payload: Dict[str, Any], tenant_id: Optional[str]) -> Dict[str, Any]:
        resp = self.client.post(path, payload=payload, override_tenant_id=tenant_id)
        if not resp.ok:
            logger.warning("%s -> HTTP %s: %s", path, resp.status_code, resp.text[:300])
            return {}
        try:
            return resp.json()
        except ValueError:
            logger.warning("%s returned non-JSON: %s", path, resp.text[:200])
            return {}

    def _query_all(self, path: str, fields: List[str], filters: Dict[str, Any],
                   tenant_id: Optional[str], sort_field: str,
                   unique_fields: List[str]) -> List[Dict[str, Any]]:
        """
        Page a single filtered query to exhaustion.

        `sort_field` is pinned on every request. ES gives no stable order without
        one, and unstable order plus dedupe-on-read loses rows silently -- far
        worse than an error, because the shortfall reads downstream as ports
        disappearing from the network.

        `unique_fields` is the row's real identity, which is NOT always the sort
        field. The MAC table is the case that matters: it sorts by clientMac, but
        one MAC legitimately appears on several ports at once -- and that is the
        loop fingerprint we are hunting. Deduping on clientMac alone would delete
        the signal, so identity there is (clientMac, switchPortId).

        Shortfalls are recorded on self.last_completeness so the caller can
        report them instead of quietly accepting a partial crawl.
        """
        base = {"fields": fields, "pageSize": PAGE_SIZE,
                "sortField": sort_field, "sortOrder": "ASC"}
        if filters:
            base["filters"] = filters

        def identity(row):
            return tuple(row.get(f) for f in unique_fields)

        first = self._query(path, {**base, "page": 0}, tenant_id)
        rows = first.get("data") or []
        total = first.get("totalCount") or len(rows)

        if total > len(rows):
            seen = {identity(r) for r in rows}
            for page in list(_pages(total))[1:]:
                body = self._query(path, {**base, "page": page}, tenant_id)
                batch = body.get("data") or []
                if not batch:
                    break
                fresh = [r for r in batch if identity(r) not in seen]
                seen.update(identity(r) for r in fresh)
                rows.extend(fresh)

        collected, expected = len(rows), total
        # totalCount SATURATES at the window, so once we are here `expected` is a
        # floor, not a count -- the real total is unknowable from this response.
        window_capped = expected >= ES_WINDOW
        if window_capped:
            logger.warning(
                "%s filters=%s hit the %d-row ES window (totalCount=%s, collected=%d) -- "
                "narrow the filter further", path, filters, ES_WINDOW, expected, collected)
        elif collected < expected:
            logger.warning("%s filters=%s INCOMPLETE: expected %d, collected %d",
                           path, filters, expected, collected)
        self.last_completeness.append({
            "path": path, "filters": filters,
            "expected": expected, "collected": collected,
            "windowCapped": window_capped,
            # A window-capped query is NOT complete. The previous
            # `collected >= min(expected, ES_WINDOW)` reported exactly 10000 of
            # 10000 as a full result, which is how a truncated MAC crawl passed
            # itself off as a complete one.
            "complete": (not window_capped) and collected >= expected,
        })
        return rows

    # ---------- inventory ----------

    def list_switches(self, tenant_id: Optional[str] = None,
                      venue_id: Optional[str] = None,
                      online_only: bool = False) -> List[Dict[str, Any]]:
        """
        Every switch known to the tenant. `id` on these rows IS the switch MAC.

        PREPROVISIONED rows carry almost no other fields (no MAC, no firmware),
        so `online_only` filters to devices that can actually report counters.
        """
        filters: Dict[str, Any] = {}
        if venue_id:
            filters["venueId"] = [venue_id]
        if online_only:
            filters["deviceStatus"] = ["ONLINE"]
        return self._query_all("/venues/switches/query", SWITCH_FIELDS, filters,
                               tenant_id, sort_field="id", unique_fields=["id"])

    def venues_with_switches(self, tenant_id: Optional[str] = None) -> Dict[str, str]:
        """venueId -> venueName, for every venue that holds at least one switch."""
        return {s["venueId"]: s.get("venueName", "")
                for s in self.list_switches(tenant_id) if s.get("venueId")}

    # ---------- ports ----------

    def ports_for_switch(self, switch_mac: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Exact per-switch port fetch. `switchMac` and `switchId` both work as
        filter keys; `switchSerial` and `id` return nothing."""
        return self._query_all("/venues/switches/switchPorts/query", PORT_FIELDS,
                               {"switchMac": [switch_mac]}, tenant_id,
                               sort_field="id", unique_fields=["id"])

    def crawl_ports(self, tenant_id: Optional[str] = None,
                    venue_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        All ports in the tenant, fanned out per venue so the ES window never bites.
        Falls back to per-switch fetches for any venue that still overflows.
        """
        if venue_ids is None:
            venue_ids = list(self.venues_with_switches(tenant_id))

        path = "/venues/switches/switchPorts/query"
        rows: List[Dict[str, Any]] = []
        for vid in venue_ids:
            batch = self._query_all(path, PORT_FIELDS, {"venueId": [vid]}, tenant_id,
                                    sort_field="id", unique_fields=["id"])
            if len(batch) >= ES_WINDOW:
                if self._fanout_by_switch(vid, batch, tenant_id, self.ports_for_switch,
                                          lambda r: r.get("id"), "ports"):
                    self._supersede(path, {"venueId": [vid]})
            rows.extend(batch)
            logger.debug("venue %s: %d ports", vid, len(batch))
        return rows

    # ---------- MAC table ----------

    def macs_for_switch(self, switch_mac: str,
                        tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Exact per-switch MAC table fetch. `switchMac` is a working filter key on
        this endpoint (verified live: a venue pull totalling 3,024 rows narrowed
        to 41 for one switch, every row matching that switch).
        """
        return self._query_all("/venues/switches/clients/query", MAC_FIELDS,
                               {"switchMac": [switch_mac]}, tenant_id,
                               sort_field="clientMac",
                               unique_fields=["clientMac", "switchPortId"])

    def crawl_mac_table(self, tenant_id: Optional[str] = None,
                        venue_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        The switch MAC address table, one row per learned address. `clientIpv4Addr`
        is the only MAC->IP binding R1 offers -- there is no ARP endpoint -- and its
        coverage varies a lot by tenant (38% to 88% observed).

        Fans out per switch for any venue that overflows the ES window, exactly as
        crawl_ports does. Without this a venue holding more than ES_WINDOW learned
        addresses returned exactly 10000 rows and called itself complete -- and a
        MAC table silently cut off at the ceiling is the worst possible input to a
        loop hunter, because the duplicate-MAC-across-ports signal it looks for is
        precisely what gets truncated away.
        """
        if venue_ids is None:
            venue_ids = list(self.venues_with_switches(tenant_id))

        path = "/venues/switches/clients/query"
        identity = lambda r: (r.get("clientMac"), r.get("switchPortId"))
        rows: List[Dict[str, Any]] = []
        for vid in venue_ids:
            batch = self._query_all(path, MAC_FIELDS, {"venueId": [vid]}, tenant_id,
                                    sort_field="clientMac",
                                    unique_fields=["clientMac", "switchPortId"])
            if len(batch) >= ES_WINDOW:
                if self._fanout_by_switch(vid, batch, tenant_id, self.macs_for_switch,
                                          identity, "MACs"):
                    self._supersede(path, {"venueId": [vid]})
            rows.extend(batch)
            logger.debug("venue %s: %d MACs", vid, len(batch))
        return rows

    # ---------- config ----------

    def list_config_backups(self, venue_id: str, switch_id: str,
                            tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Existing backups for one switch. The list response embeds the complete
        running config inline in `config` -- no second call needed.

        Read-only: this never POSTs a new backup. Switches with no scheduled
        backup simply return an empty list, and that gap gets reported rather
        than filled.
        """
        resp = self.client.get(f"/venues/{venue_id}/switches/{switch_id}/configBackups",
                               override_tenant_id=tenant_id)
        if not resp.ok:
            logger.debug("configBackups %s/%s -> HTTP %s", venue_id, switch_id, resp.status_code)
            return []
        try:
            return resp.json() or []
        except ValueError:
            return []

    def latest_config(self, venue_id: str, switch_id: str,
                      tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Most recent successful backup for a switch, as {backupId, createdDate, config}.
        The caller MUST run utils.icx_redact.redact_icx_config over `config` before
        storing, logging, or displaying it.
        """
        backups = [b for b in self.list_config_backups(venue_id, switch_id, tenant_id)
                   if b.get("config")]
        if not backups:
            return None
        newest = max(backups, key=lambda b: b.get("createdDate") or "")
        return {
            "backupId": newest.get("backupId") or newest.get("id"),
            "createdDate": newest.get("createdDate"),
            "backupType": newest.get("backupType"),
            "config": newest["config"],
        }
