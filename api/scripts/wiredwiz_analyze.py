"""
WiredWiz: find the loop.

Terminal renderer over services.wiredwiz.analyze -- the dashboard calls the same
analysis, so the two always agree. Pure analysis: no API calls, no network.

Usage:
    docker compose exec backend python scripts/wiredwiz_analyze.py <tenant_id> [--min-window S] [--top N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.wiredwiz import store
from services.wiredwiz.analyze import MIN_WINDOW, analyze


def hr(title):
    print(f"\n{'=' * 78}\n== {title}\n{'=' * 78}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tenant_id", help="tenant the snapshots were stored under")
    ap.add_argument("--min-window", type=int, default=MIN_WINDOW,
                    help="minimum seconds between the snapshots used for rates "
                         "(default %(default)s). MEASURED: R1 refreshes port counters "
                         "about every 300s — polling one switch every 20s for 8 minutes "
                         "showed exactly one change, at t=283s. A 300s window straddles "
                         "1 or 2 refreshes (±100%% error); 900s catches 3 ±1 (~33%%). "
                         "Shorter windows report fake spikes, not rates.")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--limit", type=int, default=12, help="snapshots to consider")
    args = ap.parse_args()

    snaps = store.load_all(args.tenant_id, limit=args.limit)
    if not snaps:
        sys.exit(f"no snapshots for tenant {args.tenant_id} in {store.SNAPSHOT_DIR} — "
                 f"run wiredwiz_snapshot.py first")

    r = analyze(snaps, min_window=args.min_window, top=args.top)
    if r.get("error"):
        sys.exit(r["error"])

    print("snapshots:")
    for s in r["snapshots"]:
        print(f"  {s['takenAt']}  {s['ports']} ports  {s['macs']} MACs")
    for bad in r["rejected"]:
        print(f"  {bad['takenAt']}  SKIPPED — {bad['reason']}")

    L = r["latest"]
    print(f"\nlatest: {L['switches']} switches, {L['ports']} ports "
          f"({L['upPorts']} up), {L['macs']} MACs, {len(L['venues'])} venues")

    hr("SIGNAL 1: broadcast rate")
    rt = r["rates"]
    if not rt["available"]:
        print(f"  {rt['reason']}")
        if rt.get("gapSeconds"):
            print(f"  Closest pair spans {rt['gapSeconds']}s.")
    else:
        print(f"  window {rt['windowSeconds']}s across {rt['portsCompared']} up ports"
              + (f", {rt['counterResets']} dropped for counter reset" if rt["counterResets"] else ""))
        print(f"\n  {'port':50s} {'vlan':>5s} {'bcIn/s':>10s} {'bcOut/s':>10s} {'xIn':>7s} {'xOut':>7s}")
        for t in rt["top"]:
            # xIn/xOut are None when the VLAN's median is zero -- see analyze.py
            mult_in = f"{t['xIn']:7.1f}" if t["xIn"] else "      -"
            mult_out = f"{t['xOut']:7.1f}" if t["xOut"] else "      -"
            label = t["label"][:50]
            vlan = str(t["vlan"] or "")[:5]
            print(f"  {label:50s} {vlan:>5s} "
                  f"{t['broadcastIn']:10.1f} {t['broadcastOut']:10.1f} "
                  f"{mult_in} {mult_out}")
        print("  ('-' = every other up port in that VLAN is at zero, so there is no "
              "baseline to multiply against; judge on the raw rate)")
        print("\n  broadcast-in per VLAN (a loop lifts the whole domain, not one port):")
        for v in rt["vlanSummary"][:8]:
            print(f"    vlan {str(v['vlan'] or 'trunk'):>6s}  ports={v['ports']:4d}  "
                  f"median={v['median']:10.1f}/s  max={v['max']:12.1f}/s")

    hr("SIGNAL 2: MAC moves and duplicate learning")
    m = r["macs"]
    if m["suppressedUplinkDuplicates"]:
        print(f"  ({m['suppressedUplinkDuplicates']} duplicates suppressed: explained by an "
              f"LLDP-confirmed uplink carrying the downstream port's traffic)")
    print(f"  MACs on >1 NON-UPLINK port within a snapshot: {len(m['duplicates'])}")
    for d in m["duplicates"][:args.top]:
        print(f"    {d['mac']}  in {d['snapshots']} snapshot(s)  {d['places']}")
    print(f"\n  MACs that changed port between snapshots: {len(m['moves'])}")
    for mv in m["moves"][:args.top]:
        print(f"    {mv['mac']}  {mv['count']} move(s)  last: {mv['last'][0]} -> {mv['last'][1]}")

    hr("SIGNAL 3: MAC density on ports LLDP cannot see")
    d = r["density"]
    print(f"  {d['blindCount']} of {d['totalPorts']} ports with learned MACs have no "
          f"LLDP neighbour\n")
    print(f"  {'port':50s} {'MACs':>6s}  far end")
    for row in d["top"][:args.top]:
        print(f"  {row['label'][:50]:50s} {row['macs']:6d}  "
              f"{row['lldp'] or '*** NOTHING VISIBLE ***'}")

    hr("SIGNAL 4: LLDP topology")
    t = r["topology"]
    print(f"  {t['linkCount']} switch-to-switch links among {t['switchCount']} switches")
    if t["cycles"]:
        print(f"\n  !! {len(t['cycles'])} redundant path(s) closing a cycle "
              f"(not LAG, not stacking):")
        for c in t["cycles"]:
            print(f"     {c['a']}  <->  {c['b']}")
    else:
        print("  no cycles in the LLDP graph — if a loop exists it is behind a device")
        print("  LLDP cannot see, so signals 1-3 are where it will show up.")
    if t["redundantPairs"]:
        print(f"\n  {len(t['redundantPairs'])} switch pair(s) with multiple non-LAG links:")
        for p in t["redundantPairs"][:args.top]:
            print(f"     {p['a']} <-> {p['b']}: {p['ports']}")

    hr("corroborating errors")
    for field, e in r["errors"].items():
        print(f"  {field}: nonzero on {e['nonzeroPorts']}/{e['upPorts']} up ports")
        for x in e["top"][:5]:
            print(f"    {x['value']:>15,}  {x['label']}")


if __name__ == "__main__":
    main()
