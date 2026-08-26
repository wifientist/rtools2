"""
Probe: what can WiredWiz actually pull about a switching network from R1,
short of CLI access?

The OpenAPI spec documents the *surface*; it does not say which fields are
populated on a real tenant, whether the port counters move, or what event
types exist for a link flap. This walks the read-only surface and reports
population rates so we can design the crawler against reality.

Everything here is a GET or a *query* POST. Nothing is created, modified,
deleted, rebooted, or synced. No CLI template is pushed. `adminPassword` is
never requested.

Configuration is NOT touched unless you pass --configs. Without it the probe
reports only whether backups exist, and never retrieves one -- config retrieval
stays an explicit act, never a side effect.

Usage:
    docker compose exec backend python scripts/probe_wiredwiz_surface.py <controller_id> [options]

Options:
    --tenant <id>     EC tenant to probe (MSP controllers; default: first EC with switches)
    --venue <id>      restrict to one venue
    --configs         also retrieve one backup per sampled switch and report
                      redaction stats (off by default -- no config is fetched)
    --show-config     print the redacted config text (implies --configs)
    --events-hours N  event lookback window in hours (default 24)
"""
import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller
from utils.icx_redact import redact_icx_config, assert_clean

# Fields we care about, per DTO. adminPassword is deliberately absent from the
# switch list -- it exists on the DTO and we never want it in a response body.
SWITCH_FIELDS = [
    "id", "name", "serialNumber", "switchMac", "model", "family", "firmwareVersion",
    "ipAddress", "deviceStatus", "venueId", "venueName", "uptime", "numOfPorts",
    "numOfUnits", "isStack", "switchType", "clientCount", "alerts", "cpu", "memory",
    "lastUpdTime", "syncedSwitchConfig",
]

# The loop-hunting payload: broadcast/error counters, STP state, LLDP neighbour.
PORT_FIELDS = [
    "id", "switchSerial", "switchName", "switchMac", "port", "portIdentifier", "name",
    "status", "adminStatus", "portSpeed", "mediaType",
    "broadcastIn", "broadcastOut", "multicastIn", "multicastOut", "rx", "tx",
    "crcErr", "inErr", "outErr", "inDiscard",
    "signalIn", "signalOut",
    "spanningTreeStatus", "errorDisableStatus",
    "neighborName", "neighborMacAddress", "neighborPortMacAddress",
    "lagId", "lagName", "lagStatus", "usedInFormingStack", "stackingNeighborPort",
    "unTaggedVlan", "vlanIds", "cloudPort", "venueId",
]

CLIENT_FIELDS = [
    "clientMac", "clientIpv4Addr", "clientVlan", "vlanName", "clientName",
    "switchId", "switchName", "switchSerialNumber", "switchMac", "switchPort",
    "switchPortId", "isRuckusAP", "clientType", "dhcpClientHostName", "venueId",
]


def hr(title):
    print(f"\n{'=' * 78}\n== {title}\n{'=' * 78}")


def population(rows, fields):
    """Non-null / non-empty rate per field, so we learn what R1 really fills in."""
    filled = Counter()
    for r in rows:
        for f in fields:
            v = r.get(f)
            if v not in (None, "", [], {}):
                filled[f] += 1
    n = len(rows) or 1
    return {f: (filled[f], round(100 * filled[f] / n)) for f in fields}


def print_population(rows, fields, label):
    pop = population(rows, fields)
    empty = [f for f, (c, _) in pop.items() if c == 0]
    print(f"  {label}: {len(rows)} rows sampled")
    for f, (c, pct) in pop.items():
        if c:
            print(f"    {f:26s} {pct:3d}%  e.g. {str(rows[0].get(f))[:44]!r}")
    if empty:
        print(f"    -- always empty: {', '.join(empty)}")


def query(r1, path, payload, tenant):
    resp = r1.post(path, payload=payload, override_tenant_id=tenant)
    if not resp.ok:
        print(f"  !! {path} -> HTTP {resp.status_code}: {resp.text[:220]}")
        return {}
    try:
        return resp.json()
    except ValueError:
        print(f"  !! {path} -> non-JSON body: {resp.text[:200]}")
        return {}


def warn_truncation(body, rows, path):
    """
    These endpoints are Elasticsearch-backed. Two limits bite on a real tenant:
      * `from + size` may not exceed 10000, so paging can never reach past row
        10000 no matter what totalCount says;
      * page=1 is an alias for page=0 (both return the first chunk); distinct
        pages start again at page=2.
    Anything above 10000 has to be crawled with a narrower filter (per venue,
    then per switch) rather than by paging.
    """
    total = body.get("totalCount")
    if not isinstance(total, int) or total <= len(rows):
        return
    print(f"  ** TRUNCATED: {path} reports {total} rows, this sample holds {len(rows)}.")
    if total >= 10000:
        print("     at or above the 10000-row ES window -- paging cannot reach the tail, and a"
              " totalCount of exactly 10000 is the ceiling reporting itself, not the real count."
              " Crawl per venue (and per switch inside big venues) instead.")
    else:
        print("     reachable by paging, but note page=1 repeats page=0; use page=0 then 2,3,4...")


async def resolve_tenant(r1, requested):
    """MSP controllers need an EC tenant id; ECs address themselves."""
    if requested:
        return requested
    if r1.ec_type != "MSP":
        return None
    ecs = (await r1.msp.get_msp_ecs()).get("data", [])
    print(f"MSP controller: scanning {len(ecs)} ECs for one with switches...")
    for ec in ecs:
        tid = ec.get("id") or ec.get("tenantId")
        if not tid:
            continue
        body = query(r1, "/venues/switches/query",
                     {"fields": ["serialNumber"], "page": 0, "pageSize": 1}, tid)
        if body.get("totalCount"):
            print(f"  -> using EC {ec.get('name', tid)} ({tid}), {body['totalCount']} switches")
            return tid
    print("  !! no EC had switches")
    return None


def probe_switches(r1, tenant, venue):
    hr("1. SWITCH INVENTORY  --  POST /venues/switches/query")
    payload = {"fields": SWITCH_FIELDS, "page": 0, "pageSize": 1000}
    if venue:
        payload["filters"] = {"venueId": [venue]}
    body = query(r1, "/venues/switches/query", payload, tenant)
    rows = body.get("data") or []
    print(f"  totalCount={body.get('totalCount')} returned={len(rows)}")
    warn_truncation(body, rows, "/venues/switches/query")
    if not rows:
        return []
    print_population(rows, SWITCH_FIELDS, "switch")
    print("\n  deviceStatus distribution:", dict(Counter(r.get("deviceStatus") for r in rows)))
    print("  model distribution:", dict(Counter(r.get("model") for r in rows).most_common(10)))
    print(f"  stacks: {sum(1 for r in rows if r.get('isStack'))} / {len(rows)}")
    return rows


def probe_ports(r1, tenant, venue):
    hr("2. PORT STATE + COUNTERS  --  POST /venues/switches/switchPorts/query")
    payload = {"fields": PORT_FIELDS, "page": 0, "pageSize": 1000}
    if venue:
        payload["filters"] = {"venueId": [venue]}
    body = query(r1, "/venues/switches/switchPorts/query", payload, tenant)
    rows = body.get("data") or []
    print(f"  totalCount={body.get('totalCount')} returned={len(rows)}")
    warn_truncation(body, rows, "/venues/switches/switchPorts/query")
    if not rows:
        return []
    print_population(rows, PORT_FIELDS, "port")

    print("\n  -- values that drive loop detection --")
    for f in ("status", "adminStatus", "spanningTreeStatus", "errorDisableStatus", "lagStatus"):
        vals = Counter(r.get(f) for r in rows)
        print(f"    {f:22s} {dict(vals.most_common(8))}")

    up = [r for r in rows if str(r.get("status", "")).lower() in ("up", "connected", "1")]
    print(f"\n  up ports: {len(up)} / {len(rows)}")
    if up:
        def as_int(v):
            try:
                return int(str(v))
            except (TypeError, ValueError):
                return 0
        for f in ("broadcastIn", "broadcastOut", "multicastIn", "crcErr", "inErr", "inDiscard"):
            nz = [r for r in up if as_int(r.get(f))]
            top = sorted(up, key=lambda r: as_int(r.get(f)), reverse=True)[:3]
            print(f"    {f:14s} nonzero on {len(nz):4d}/{len(up)} up ports;"
                  f" top: {[(t.get('switchName'), t.get('portIdentifier'), t.get(f)) for t in top]}")

    lldp = [r for r in rows if r.get("neighborName") or r.get("neighborMacAddress")]
    print(f"\n  LLDP neighbours present on {len(lldp)} / {len(rows)} ports"
          f" ({len(set(r.get('neighborName') for r in lldp if r.get('neighborName')))} distinct names)")
    for r in lldp[:5]:
        print(f"    {r.get('switchName')} {r.get('portIdentifier')} -> "
              f"{r.get('neighborName')} / {r.get('neighborMacAddress')} / {r.get('neighborPortMacAddress')}")
    return rows


def probe_clients(r1, tenant, venue):
    hr("3. MAC TABLE (switch clients)  --  POST /venues/switches/clients/query")
    payload = {"fields": CLIENT_FIELDS, "page": 0, "pageSize": 1000}
    if venue:
        payload["filters"] = {"venueId": [venue]}
    body = query(r1, "/venues/switches/clients/query", payload, tenant)
    rows = body.get("data") or []
    print(f"  totalCount={body.get('totalCount')} returned={len(rows)}")
    warn_truncation(body, rows, "/venues/switches/clients/query")
    if not rows:
        return []
    print_population(rows, CLIENT_FIELDS, "client")

    with_ip = sum(1 for r in rows if r.get("clientIpv4Addr"))
    print(f"\n  ARP coverage: {with_ip}/{len(rows)} MACs carry an IPv4 address"
          f"  ({round(100 * with_ip / len(rows))}%)")

    # A MAC learned on more than one switch port is the classic loop fingerprint.
    where = defaultdict(set)
    for r in rows:
        mac = (r.get("clientMac") or "").lower()
        if mac:
            where[mac].add((r.get("switchName"), r.get("switchPort")))
    multi = {m: p for m, p in where.items() if len(p) > 1}
    print(f"  MACs seen on >1 switch/port in this snapshot: {len(multi)}")
    for mac, places in list(multi.items())[:10]:
        print(f"    {mac}  {sorted(places)}")

    # Port density: a loop makes one port learn a large slice of the MAC table.
    per_port = Counter((r.get("switchName"), r.get("switchPort")) for r in rows)
    print("\n  densest ports (MACs learned):")
    for (sw, port), n in per_port.most_common(10):
        print(f"    {n:5d}  {sw} {port}")
    return rows


def probe_config_backups(r1, tenant, switches, venue, fetch, show):
    hr("4. CONFIG BACKUPS  --  GET /venues/{v}/switches/{s}/configBackups")
    if not fetch:
        print("  (skipped -- pass --configs to retrieve backups. This probe does not")
        print("   read configuration as a side effect of surveying the API.)")
        return
    if not switches:
        print("  (no switches to check)")
        return
    checked = 0
    for sw in switches:
        vid, sid = sw.get("venueId") or venue, sw.get("id")
        if not (vid and sid):
            continue
        resp = r1.get(f"/venues/{vid}/switches/{sid}/configBackups", override_tenant_id=tenant)
        if not resp.ok:
            print(f"  {sw.get('name')}: HTTP {resp.status_code} {resp.text[:160]}")
            checked += 1
            if checked >= 3:
                break
            continue
        backups = resp.json() or []
        print(f"  {sw.get('name')} ({sw.get('model')}): {len(backups)} backup(s)")
        for b in backups[:2]:
            print(f"    id={b.get('backupId') or b.get('id')} type={b.get('backupType')} "
                  f"status={b.get('status')} created={b.get('createdDate')} "
                  f"configInline={'yes' if b.get('config') else 'no'}")
        if backups:
            bid = backups[0].get("backupId") or backups[0].get("id")
            raw = backups[0].get("config")
            if not raw:
                det = r1.get(f"/venues/{vid}/switches/{sid}/configBackups/{bid}",
                             override_tenant_id=tenant)
                raw = det.json().get("config") if det.ok else None
                print(f"    detail fetch -> HTTP {det.status_code}, config present: {bool(raw)}")
            fmt = r1.get(f"/venues/{vid}/switches/{sid}/configBackups/{bid}/formattedConfigs",
                         override_tenant_id=tenant)
            print(f"    formattedConfigs -> HTTP {fmt.status_code}"
                  + (f", cli chars={len(fmt.json().get('cli') or '')}" if fmt.ok else ""))
            if raw:
                red, stats = redact_icx_config(raw)
                leftovers = assert_clean(red)
                print(f"    config: {stats['total_lines']} lines | "
                      f"rule hits={stats['rule']} catchall={stats['catchall']} "
                      f"encoded-block lines={stats['block_lines']}")
                if stats["catchall"]:
                    print("    NOTE: catchall fired -- an unrecognised secret-bearing command "
                          "exists; add an explicit rule in utils/icx_redact.py")
                if leftovers:
                    print(f"    !! REDACTION GAP -- {len(leftovers)} suspicious lines remain")
                    for l in leftovers[:5]:
                        print(f"       {l}")
                else:
                    print("    redaction verified clean")
                if show:
                    print("    ---- redacted config (first 60 lines) ----")
                    for line in red.splitlines()[:60]:
                        print(f"    | {line}")
        checked += 1
        if checked >= 3:
            print("  (stopping after 3 switches)")
            break


def probe_events(r1, tenant, hours):
    hr(f"5. EVENTS  --  POST /events/query  (last {hours}h)")
    body = query(r1, "/events/query", {"page": 0, "pageSize": 200, "sortField": "date",
                                       "sortOrder": "desc"}, tenant)
    rows = body.get("data") or []
    print(f"  totalCount={body.get('totalCount')} returned={len(rows)}")
    print(f"  response 'fields': {body.get('fields')}")
    if not rows:
        return
    keys = Counter()
    for r in rows:
        keys.update(r.keys())
    print(f"  keys present on event rows: {sorted(keys)}")
    print(f"\n  sample row:\n{json.dumps(rows[0], indent=4, default=str)[:1200]}")
    for key in ("eventType", "type", "code", "eventCode", "name", "category", "severity"):
        if keys.get(key):
            print(f"\n  distinct {key} ({keys[key]} rows have it):")
            for v, n in Counter(r.get(key) for r in rows).most_common(30):
                print(f"    {n:5d}  {v}")

    hr("6. ALARMS  --  POST /alarms/query")
    body = query(r1, "/alarms/query", {"page": 0, "pageSize": 100}, tenant)
    rows = body.get("data") or []
    print(f"  totalCount={body.get('totalCount')} returned={len(rows)}")
    if rows:
        print(f"  keys: {sorted({k for r in rows for k in r})}")
        print(f"  sample:\n{json.dumps(rows[0], indent=4, default=str)[:900]}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("controller_id", type=int)
    ap.add_argument("--tenant")
    ap.add_argument("--venue")
    ap.add_argument("--configs", action="store_true",
                    help="retrieve a config backup for the sampled switches")
    ap.add_argument("--show-config", action="store_true",
                    help="print the redacted config text (implies --configs)")
    ap.add_argument("--events-hours", type=int, default=24)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(args.controller_id, db)
    finally:
        db.close()
    print(f"controller={args.controller_id} r1_tenant={r1.tenant_id} "
          f"ec_type={r1.ec_type} host={r1.host}")

    tenant = await resolve_tenant(r1, args.tenant)
    switches = probe_switches(r1, tenant, args.venue)
    probe_ports(r1, tenant, args.venue)
    probe_clients(r1, tenant, args.venue)
    probe_config_backups(r1, tenant, switches, args.venue,
                         fetch=args.configs or args.show_config,
                         show=args.show_config)
    probe_events(r1, tenant, args.events_hours)


if __name__ == "__main__":
    asyncio.run(main())
