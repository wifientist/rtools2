"""
Take one read-only snapshot of a tenant's switching estate.

Shared by the CLI script and the API router so the two can never disagree about
what a snapshot contains.

READ-ONLY: GETs and `*/query` POSTs only. Never creates a config backup, pushes
CLI, reboots, or syncs.

HUMAN-TRIGGERED ONLY. Nothing here schedules itself, and nothing loops. A
snapshot happens because a person asked for one -- there is deliberately no
recurring-crawl entry point for a scheduler to call.

Configs are NOT part of a snapshot. A crawl never bulk-pulls configuration, and
snapshots never store it. `fetch_redacted_config` handles exactly one switch and
is only ever called when someone opens that switch's config. That keeps config
retrieval an explicit, per-device act rather than a side effect of crawling.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from utils.icx_redact import assert_clean, redact_icx_config

logger = logging.getLogger(__name__)


def take_snapshot(r1, tenant_id: Optional[str],
                  venue_ids: Optional[List[str]] = None,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """
    Crawl switches, ports and the MAC table. One call, one snapshot, no loop.

    `venue_ids` limits the crawl. The selection is recorded on the snapshot as
    `scopeVenueIds`, because two snapshots taken at different scopes must never
    be differenced as-is -- the venues missing from the narrower one would read
    as ports and MACs disappearing from the network. The analyzer intersects
    scopes before comparing.

    Configuration is deliberately not collected here -- see the module docstring.
    """
    def say(msg):
        logger.info("wiredwiz: %s", msg)
        if progress:
            progress(msg)

    started = time.time()
    taken_at = datetime.now(timezone.utc).isoformat()

    r1.switches.reset_completeness()
    switches = r1.switches.list_switches(tenant_id)
    if venue_ids:
        wanted = set(venue_ids)
        switches = [s for s in switches if s.get("venueId") in wanted]
    venues = {s["venueId"]: s.get("venueName", "") for s in switches if s.get("venueId")}
    say(f"{len(switches)} switches across {len(venues)} venue(s)"
        + (" (scoped)" if venue_ids else ""))

    ports = r1.switches.crawl_ports(tenant_id, list(venues))
    say(f"{len(ports)} ports")

    macs = r1.switches.crawl_mac_table(tenant_id, list(venues))
    say(f"{len(macs)} MAC table entries")

    # A short crawl must never be mistaken for the network getting smaller.
    completeness = r1.switches.completeness_report()
    if completeness["incomplete"]:
        say(f"INCOMPLETE: {completeness['incomplete']}/{completeness['queries']} queries "
            f"short ({completeness['collected']}/{completeness['expected']} rows)")

    return {
        "takenAt": taken_at,
        "takenAtEpoch": time.time(),
        "tenantId": tenant_id,
        "scopeVenueIds": sorted(venue_ids) if venue_ids else None,
        "venues": venues,
        "switches": switches,
        "ports": ports,
        "macs": macs,
        "completeness": completeness,
        "elapsedSeconds": round(time.time() - started, 1),
    }


def fetch_redacted_config(r1, tenant_id, venue_id, switch_id,
                          switch_name=None, model=None) -> Optional[Dict[str, Any]]:
    """
    Newest existing backup for ONE switch, redacted and verified.

    Called only when a person opens that switch's config. Never called in bulk
    and never on a timer.

    Returns None when the switch has no backup, or {"dropped": True, ...} when
    the redactor cannot vouch for the result. Callers must treat a dropped entry
    as "no config" -- never fall back to the raw text.
    """
    latest = r1.switches.latest_config(venue_id, switch_id, tenant_id)
    if not latest:
        return None

    redacted, stats = redact_icx_config(latest["config"])
    leftovers = assert_clean(redacted)
    if leftovers:
        logger.warning("wiredwiz: dropping config for %s -- %d secret-like lines survived "
                       "redaction", switch_name or switch_id, len(leftovers))
        return {"dropped": True, "switchName": switch_name,
                "leftoverCount": len(leftovers)}

    if stats["catchall"]:
        logger.info("wiredwiz: %s matched the redaction catch-all %dx -- an unrecognised "
                    "secret-bearing command exists, add a rule to utils/icx_redact.py",
                    switch_name or switch_id, stats["catchall"])

    return {
        "switchName": switch_name,
        "model": model,
        "backupId": latest["backupId"],
        "createdDate": latest["createdDate"],
        "backupType": latest["backupType"],
        "redactionStats": stats,
        "config": redacted,
    }
