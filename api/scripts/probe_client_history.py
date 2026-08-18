"""
Probe: how much per-client history can we actually get out of R1?

Background: the Maps tool draws live client RSSI. The obvious next step is a
time dimension — a client's trail across APs. Two things have to be true for
that, and the OpenAPI spec answers neither:

  1. /historicalClients/query has to return usable records with timestamps,
     and we need to know how far back they go. The spec types its response as
     a bare `data: array` with no item schema and documents no date filter, no
     retention window, and no field list.
  2. Something has to tell us which AP served the client at a given time.
     /venues/aps/clients/query only ever reports the *serving* AP, so a trail
     has to be reconstructed from history or from events.

This script answers both empirically. It prints the real field names, the
oldest reachable record, which date-filter shapes the endpoint accepts, and —
for the busiest client it can find — whether the history actually spans more
than one AP.

What it does NOT probe: per-AP RSSI for an associated client from APs it isn't
associated to. That doesn't exist in the API at all (see the notes at the
bottom of the output).

Usage:
    docker compose exec backend python scripts/probe_client_history.py <controller_id> [tenant_id] [client_mac]

Example:
    docker compose exec backend python scripts/probe_client_history.py 14
    docker compose exec backend python scripts/probe_client_history.py 14 abc123 aa:bb:cc:dd:ee:ff
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller

# Field names R1 might use for the record timestamp — we don't know which,
# so the probe tries each and reports what sticks.
CANDIDATE_TIME_FIELDS = [
    "timestamp", "lastSeenTime", "lastUpdatedTime", "disconnectTime",
    "connectedTime", "sessionStartTime", "sessionEndTime", "eventTime", "date",
]

SEPARATOR = "=" * 78


def post(r1, path, body, tenant_id):
    """POST with the MSP tenant override only when the client is MSP-scoped."""
    if r1.ec_type == "MSP" and tenant_id:
        return r1.post(path, payload=body, override_tenant_id=tenant_id)
    return r1.post(path, payload=body)


def show(resp, label, limit=2):
    """Print status, shape, and a couple of sample rows."""
    print(f"  {label}: HTTP {resp.status_code}")
    if not resp.ok:
        print(f"    body: {resp.text[:400]}")
        return None
    try:
        data = resp.json() or {}
    except ValueError:
        print(f"    non-JSON body: {resp.text[:200]}")
        return None

    rows = data.get("data") if isinstance(data, dict) else data
    rows = rows or []
    total = data.get("totalCount") if isinstance(data, dict) else None
    print(f"    totalCount={total} returned={len(rows)}")
    if isinstance(data, dict) and data.get("fields"):
        print(f"    server-declared fields: {sorted(data['fields'])}")
    for row in rows[:limit]:
        print(f"    sample: {json.dumps(row, default=str)[:600]}")
    return rows


def probe_shape(r1, tenant_id):
    """Step 1 — what does a bare historical query even return?"""
    print(SEPARATOR)
    print("STEP 1  /historicalClients/query — bare query, server-default fields")
    print(SEPARATOR)

    rows = show(post(r1, "/historicalClients/query", {"page": 0, "pageSize": 5}, tenant_id),
                "no filters")
    if not rows:
        print("\n  No rows. Either the tenant has no history or the endpoint is")
        print("  not enabled for this account. Everything below will be empty.")
        return [], []

    keys = sorted({k for row in rows for k in row.keys()})
    print(f"\n  ACTUAL KEYS ({len(keys)}): {keys}")

    time_keys = [k for k in keys if k in CANDIDATE_TIME_FIELDS]
    other_time = [k for k in keys if k not in time_keys
                  and any(t in k.lower() for t in ("time", "date", "seen"))]
    print(f"  timestamp-ish keys: {time_keys + other_time}")

    ap_keys = [k for k in keys if "ap" in k.lower() or "serial" in k.lower()]
    print(f"  AP-ish keys: {ap_keys}")
    signal_keys = [k for k in keys if any(s in k.lower() for s in ("rssi", "snr", "signal"))]
    print(f"  signal-ish keys: {signal_keys}  <-- empty here means no RSSI in history")

    return keys, time_keys + other_time


def probe_retention(r1, tenant_id, time_keys):
    """Step 2 — sort ascending on each timestamp field to find the oldest record."""
    print()
    print(SEPARATOR)
    print("STEP 2  How far back does it go?")
    print(SEPARATOR)

    if not time_keys:
        print("  No timestamp field found in step 1 — cannot bound the window.")
        return

    now = datetime.now(timezone.utc)
    for field in time_keys:
        body = {"page": 0, "pageSize": 1, "sortField": field, "sortOrder": "ASC"}
        resp = post(r1, "/historicalClients/query", body, tenant_id)
        if not resp.ok:
            print(f"  sort ASC on '{field}': HTTP {resp.status_code} — {resp.text[:150]}")
            continue
        rows = (resp.json() or {}).get("data") or []
        if not rows:
            print(f"  sort ASC on '{field}': no rows")
            continue

        value = rows[0].get(field)
        print(f"  oldest by '{field}': {value}")
        parsed = parse_time(value)
        if parsed:
            age = now - parsed
            print(f"    -> {age.days} days {age.seconds // 3600}h old  "
                  f"(retention is AT LEAST this; may be capped by pageSize/window)")


def parse_time(value):
    """Best-effort parse of whatever R1 puts in a timestamp field."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds or milliseconds.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def probe_date_filters(r1, tenant_id, time_keys):
    """Step 3 — which date-range shape, if any, does this endpoint accept?"""
    print()
    print(SEPARATOR)
    print("STEP 3  Does it accept a date range? (spec documents none)")
    print(SEPARATOR)

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=7)).isoformat()
    end = now.isoformat()
    field = time_keys[0] if time_keys else "timestamp"

    shapes = {
        "rangeDateFilter (DynamicQuery style)": {
            "rangeDateFilter": {"start": start, "end": end, "fieldName": field}
        },
        "rangeFilter": {
            "rangeFilter": {"start": start, "end": end, "fieldName": field}
        },
        "filters.startTime/endTime": {
            "filters": {"startTime": [start], "endTime": [end]}
        },
        "top-level startTime/endTime": {"startTime": start, "endTime": end},
        "search_after cursor": {"search_after": []},
        "detailLevel=DEEP": {"detailLevel": "DEEP"},
    }

    for label, extra in shapes.items():
        body = {"page": 0, "pageSize": 2, **extra}
        resp = post(r1, "/historicalClients/query", body, tenant_id)
        status = "ACCEPTED" if resp.ok else "REJECTED"
        count = ""
        if resp.ok:
            try:
                payload = resp.json() or {}
                count = f" totalCount={payload.get('totalCount')} rows={len(payload.get('data') or [])}"
            except ValueError:
                pass
        print(f"  {status:9} {label}{count}")
        if not resp.ok:
            print(f"            {resp.text[:180]}")

    print("\n  NOTE: 'ACCEPTED' only means no 400. Compare totalCount against the")
    print("  unfiltered count in step 1 — an unchanged count means the filter was")
    print("  silently ignored, which is how /identityGroups/query treats")
    print("  searchString.")


def probe_client_trail(r1, tenant_id, keys, time_keys, client_mac):
    """Step 4 — for one client, does history span more than one AP?"""
    print()
    print(SEPARATOR)
    print("STEP 4  Does one client's history span multiple APs? (the trail question)")
    print(SEPARATOR)

    mac_key = next((k for k in keys if "mac" in k.lower()), None)
    if not mac_key:
        print("  No MAC-ish key in the records — cannot group by client.")
        return

    if not client_mac:
        # Pull a page and pick whichever client appears most often.
        resp = post(r1, "/historicalClients/query", {"page": 0, "pageSize": 500}, tenant_id)
        if not resp.ok:
            print(f"  sampling failed: HTTP {resp.status_code}")
            return
        rows = (resp.json() or {}).get("data") or []
        counts = Counter(row.get(mac_key) for row in rows if row.get(mac_key))
        if not counts:
            print("  no MACs in the sample")
            return
        client_mac, occurrences = counts.most_common(1)[0]
        print(f"  busiest client in a 500-row sample: {client_mac} ({occurrences} rows)")
        print(f"  (note: the endpoint description says results are GROUPED BY MAC —")
        print(f"   if every MAC appears exactly once, there is no per-client timeline)")
        print(f"  distinct MACs in sample: {len(counts)} / {len(rows)} rows")

    body = {
        "page": 0,
        "pageSize": 200,
        "filters": {mac_key: [client_mac]},
    }
    if time_keys:
        body["sortField"] = time_keys[0]
        body["sortOrder"] = "ASC"

    rows = show(post(r1, "/historicalClients/query", body, tenant_id),
                f"filter {mac_key}={client_mac}", limit=3)
    if not rows:
        return

    ap_keys = [k for k in keys if "serial" in k.lower() or k.lower() in ("apmac", "apname")]
    if not ap_keys:
        print("  no AP identifier in the records — a trail cannot be built from this")
        return

    ap_key = ap_keys[0]
    seen_aps = Counter(str(row.get(ap_key)) for row in rows)
    print(f"\n  distinct '{ap_key}' values for this client: {len(seen_aps)}")
    for ap, count in seen_aps.most_common(10):
        print(f"    {ap}: {count} record(s)")
    if len(seen_aps) > 1:
        print("  -> MULTIPLE APs. An AP-to-AP trail over time IS reconstructable.")
    else:
        print("  -> ONE AP only. Either this client never roamed, or history keeps")
        print("     just the latest state per MAC (check the row count above).")


def probe_events(r1, tenant_id, client_mac):
    """Step 5 — do events carry per-client roam/connect records?"""
    print()
    print(SEPARATOR)
    print("STEP 5  /events/query — are there client connect/roam events?")
    print(SEPARATOR)

    rows = show(post(r1, "/events/query", {"page": 0, "pageSize": 5}, tenant_id), "bare query")
    if rows:
        keys = sorted({k for row in rows for k in row.keys()})
        print(f"\n  ACTUAL KEYS: {keys}")
        type_key = next((k for k in keys if "type" in k.lower() or "code" in k.lower()), None)
        if type_key:
            resp = post(r1, "/events/query", {"page": 0, "pageSize": 500}, tenant_id)
            if resp.ok:
                sample = (resp.json() or {}).get("data") or []
                types = Counter(str(row.get(type_key)) for row in sample)
                print(f"\n  event '{type_key}' values in a 500-row sample:")
                for name, count in types.most_common(25):
                    print(f"    {count:5}  {name}")
                print("\n  Look for connect / disconnect / roam / association types —")
                print("  those are what a movement trail would be built from.")

    print()
    resp = post(r1, "/events/metas/query", {"page": 0, "pageSize": 50}, tenant_id)
    show(resp, "/events/metas/query (event catalogue)", limit=3)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    controller_id = int(sys.argv[1])
    tenant_id = sys.argv[2] if len(sys.argv) > 2 else None
    client_mac = sys.argv[3] if len(sys.argv) > 3 else None

    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    print(f"[probe] controller={controller_id} ec_type={r1.ec_type} tenant={tenant_id}")
    if r1.ec_type == "MSP" and not tenant_id:
        print("[probe] WARNING: MSP controller without a tenant_id — R1 will scope")
        print("[probe] this to the MSP itself, which usually has no clients.")

    keys, time_keys = probe_shape(r1, tenant_id)
    if keys:
        probe_retention(r1, tenant_id, time_keys)
        probe_date_filters(r1, tenant_id, time_keys)
        probe_client_trail(r1, tenant_id, keys, time_keys, client_mac)
    probe_events(r1, tenant_id, client_mac)

    print()
    print(SEPARATOR)
    print("NOT PROBED — known absent from the API")
    print(SEPARATOR)
    print("""  Per-AP RSSI for an associated client (the 'bouncing off every AP'
  matrix) is not in the R1 API in any form:

    - ApClientQueryData carries exactly one apInformation and one
      signalStatus — the serving AP's view, nothing else.
    - /clients/{mac} (deprecated, removal >= 2026-06-30) is the same single
      AP: apMac / apSerialNumber / receiveSignalStrength_dBm.
    - AP neighbors (/venues/{id}/aps/{sn}/neighbors/query) are AP-to-AP RF
      and LLDP neighbours, not clients.
    - Rogue AP detection IS the multi-receiver shape we'd want —
      RogueApDto.detectingAps[] is a list of {apSerialNumber, snr} — but it
      only applies to rogue APs, not to associated clients.
    - LBS (/lbsServerProfiles) is a forwarding config: serverAddress, port,
      password. The AP location stream goes to an external SPoT/vSPoT server;
      R1 stores the profile, not the data. If such a server exists for this
      tenant, that server is where real trilateration data lives.""")


if __name__ == "__main__":
    main()
