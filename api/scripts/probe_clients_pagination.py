"""
Probe: does /venues/aps/clients/query actually paginate?

Background: /venues/aps/query on the tenant-wide scope silently ignores the
`page` parameter (page=1 returns the same rows as page=0). We never tested the
clients sibling endpoint. This script picks a controller, finds the venue with
the most clients, and walks pages 0..N printing totalCount, len(data), first
clientMac, and overlap between consecutive pages.

Usage:
    docker compose exec backend python scripts/probe_clients_pagination.py <controller_id> [max_pages]

Example:
    docker compose exec backend python scripts/probe_clients_pagination.py 14 5
"""
import sys
from pathlib import Path

# Add project root to path (same pattern as other scripts/ entries)
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal
from clients.r1_client import create_r1_client_from_controller


def probe(controller_id: int, max_pages: int = 5, page_size: int = 1000):
    db = SessionLocal()
    try:
        r1 = create_r1_client_from_controller(controller_id, db)
    finally:
        db.close()

    print(f"[probe] controller={controller_id} ec_type={r1.ec_type}")

    # Find a candidate venue: walk tenants, pick the venue whose client count
    # hits the 10k cap (where pagination would matter). Fall back to the first
    # venue with any clients.
    if r1.ec_type == "MSP":
        ecs = r1.msp.get_msp_ecs_sync() if hasattr(r1.msp, "get_msp_ecs_sync") else None
        if not ecs:
            import asyncio
            ecs = asyncio.run(r1.msp.get_msp_ecs())
        tenants = ecs.get("data", [])
    else:
        tenants = [{"id": None, "name": "self"}]

    candidate = None
    for t in tenants:
        tid = t["id"]
        tname = t.get("name", "?")
        if tid:
            vresp = r1.get("/venues", override_tenant_id=tid)
        else:
            vresp = r1.get("/venues")
        if not vresp.ok:
            continue
        vdata = vresp.json()
        venues = vdata if isinstance(vdata, list) else vdata.get("data", [])

        for v in venues:
            vid = v.get("id")
            vname = v.get("name", "?")
            if not vid:
                continue
            body = {
                "fields": ["macAddress"],
                "filters": {"venueId": [vid]},
                "page": 0,
                "pageSize": 1,
            }
            if tid:
                probe_resp = r1.post("/venues/aps/clients/query", payload=body, override_tenant_id=tid)
            else:
                probe_resp = r1.post("/venues/aps/clients/query", payload=body)
            if not probe_resp.ok:
                continue
            count = (probe_resp.json() or {}).get("totalCount", 0)

            if count >= 10000:
                candidate = (tid, tname, vid, vname, count)
                break
            if candidate is None and count > 0:
                candidate = (tid, tname, vid, vname, count)

        if candidate and candidate[4] >= 10000:
            break

    if not candidate:
        print("[probe] no candidate venue with clients found — aborting")
        return

    tid, tname, vid, vname, first_count = candidate
    print(f"[probe] target venue: tenant={tname} ({tid}) venue={vname} ({vid}) initial totalCount={first_count}")
    print(f"[probe] strategy: walk page=0..{max_pages - 1} with pageSize={page_size}, dedupe by clientMac")

    def get_mac(row: dict):
        """Clients endpoint returns the client MAC under different keys
        depending on which schema flavor the server picks. Try them all."""
        return (
            row.get("macAddress")
            or row.get("clientMac")
            or row.get("mac")
            or row.get("clientMacAddress")
        )

    # ------------------------------------------------------------------
    # Test A: plain `page` iteration
    # ------------------------------------------------------------------
    print("\n[probe] === TEST A: page/pageSize iteration ===")
    previous_macs: set = set()
    all_macs_a: set = set()
    page0_response: dict | None = None

    for page in range(max_pages):
        body = {
            "fields": ["macAddress", "clientMac", "apMac", "lastSeenTime"],
            "filters": {"venueId": [vid]},
            "sortField": "macAddress",
            "sortOrder": "ASC",
            "page": page,
            "pageSize": page_size,
        }
        if tid:
            resp = r1.post("/venues/aps/clients/query", payload=body, override_tenant_id=tid)
        else:
            resp = r1.post("/venues/aps/clients/query", payload=body)

        if not resp.ok:
            print(f"[probe]   page={page} HTTP {resp.status_code}: {resp.text[:300]}")
            break

        data = resp.json() or {}
        rows = data.get("data") or []
        reported = data.get("totalCount", 0)

        if page == 0:
            page0_response = data
            if rows:
                print(f"[probe]   first-row keys: {sorted(rows[0].keys())}")
            subs = data.get("subsequentQueries")
            print(
                f"[probe]   response keys: {sorted(data.keys())}  "
                f"subsequentQueries={'<present>' if subs else '<empty/missing>'}"
            )
            if subs:
                print(f"[probe]   subsequentQueries[0]: {subs[0]}")

        current_macs = {get_mac(r) for r in rows if get_mac(r)}
        overlap = current_macs & previous_macs
        new_in_page = current_macs - all_macs_a
        all_macs_a |= current_macs

        first_mac = get_mac(rows[0]) if rows else None
        last_mac = get_mac(rows[-1]) if rows else None

        print(
            f"[probe]   page={page} reported_total={reported} rows={len(rows)} "
            f"new={len(new_in_page)} overlap_with_prev={len(overlap)} "
            f"first={first_mac} last={last_mac}"
        )

        if not rows:
            print("[probe]   empty page — end")
            break
        if len(rows) < page_size:
            print(f"[probe]   short page — end at page={page}")
            break
        # Do NOT early-exit on zero-new — we want to see if later pages advance
        # even when an earlier one returned duplicates (observed in some runs).
        previous_macs = current_macs

    print(f"[probe]   Test A collected: {len(all_macs_a)} unique clients")

    # ------------------------------------------------------------------
    # Test B: subsequentQueries link from server
    # ------------------------------------------------------------------
    print("\n[probe] === TEST B: subsequentQueries cursor ===")
    if not page0_response:
        print("[probe]   no page0 response captured — skipping")
    else:
        subs = page0_response.get("subsequentQueries") or []
        if not subs:
            print("[probe]   page0 response has no subsequentQueries — server does not offer a server-side cursor for this query")
        else:
            sub = subs[0] or {}
            sub_url = sub.get("url") or ""
            sub_method = (sub.get("httpMethod") or "POST").upper()
            sub_payload = sub.get("payload")
            print(f"[probe]   server cursor: {sub_method} {sub_url}")
            if sub_url.startswith("http"):
                from urllib.parse import urlparse
                sub_url = urlparse(sub_url).path
            try:
                if sub_method == "POST":
                    if tid:
                        sub_resp = r1.post(sub_url, payload=sub_payload, override_tenant_id=tid)
                    else:
                        sub_resp = r1.post(sub_url, payload=sub_payload)
                elif sub_method == "GET":
                    if tid:
                        sub_resp = r1.get(sub_url, override_tenant_id=tid)
                    else:
                        sub_resp = r1.get(sub_url)
                else:
                    print(f"[probe]   unsupported method {sub_method} — skipping")
                    sub_resp = None
                if sub_resp is not None:
                    if not sub_resp.ok:
                        print(f"[probe]   cursor HTTP {sub_resp.status_code}: {sub_resp.text[:300]}")
                    else:
                        sub_data = sub_resp.json() or {}
                        sub_rows = sub_data.get("data") or []
                        sub_macs = {get_mac(r) for r in sub_rows if get_mac(r)}
                        page0_macs = {get_mac(r) for r in (page0_response.get("data") or []) if get_mac(r)}
                        overlap_b = sub_macs & page0_macs
                        new_b = sub_macs - page0_macs
                        print(
                            f"[probe]   cursor page rows={len(sub_rows)} "
                            f"new={len(new_b)} overlap_with_page0={len(overlap_b)} "
                            f"first={get_mac(sub_rows[0]) if sub_rows else None} "
                            f"last={get_mac(sub_rows[-1]) if sub_rows else None}"
                        )
                        if new_b:
                            print("[probe]   subsequentQueries ADVANCES — viable pagination path")
                        else:
                            print("[probe]   subsequentQueries returned duplicates — cursor also broken")
            except Exception as e:
                print(f"[probe]   cursor error: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Test C: search_after cursor from last row
    # ------------------------------------------------------------------
    print("\n[probe] === TEST C: search_after cursor ===")
    if not page0_response:
        print("[probe]   no page0 response captured — skipping")
    else:
        page0_rows = page0_response.get("data") or []
        last_mac_c = get_mac(page0_rows[-1]) if page0_rows else None
        if not last_mac_c:
            print("[probe]   no last MAC available — skipping")
        else:
            print(f"[probe]   passing search_after=[{last_mac_c}]")
            body_c = {
                "fields": ["macAddress", "clientMac", "apMac", "lastSeenTime"],
                "filters": {"venueId": [vid]},
                "sortField": "macAddress",
                "sortOrder": "ASC",
                "pageSize": page_size,
                "search_after": [last_mac_c],
            }
            try:
                if tid:
                    resp_c = r1.post("/venues/aps/clients/query", payload=body_c, override_tenant_id=tid)
                else:
                    resp_c = r1.post("/venues/aps/clients/query", payload=body_c)
                if not resp_c.ok:
                    print(f"[probe]   HTTP {resp_c.status_code}: {resp_c.text[:300]}")
                else:
                    data_c = resp_c.json() or {}
                    rows_c = data_c.get("data") or []
                    macs_c = {get_mac(r) for r in rows_c if get_mac(r)}
                    page0_macs = {get_mac(r) for r in page0_rows if get_mac(r)}
                    overlap_c = macs_c & page0_macs
                    new_c = macs_c - page0_macs
                    first_c = get_mac(rows_c[0]) if rows_c else None
                    last_c = get_mac(rows_c[-1]) if rows_c else None
                    print(
                        f"[probe]   rows={len(rows_c)} new={len(new_c)} "
                        f"overlap_with_page0={len(overlap_c)} first={first_c} last={last_c}"
                    )
                    if new_c:
                        print("[probe]   search_after ADVANCES — viable pagination path")
                    elif not rows_c:
                        print("[probe]   search_after returned empty — cursor accepted but exhausted?")
                    else:
                        print("[probe]   search_after returned duplicates — cursor silently ignored")
            except Exception as e:
                print(f"[probe]   search_after error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    controller_id = int(sys.argv[1])
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    probe(controller_id, max_pages=max_pages)
