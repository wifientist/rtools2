"""
The Phase 2 gate: does the correlator agree with WiredWiz about the wired graph?

WiredWiz's analyze.topology() and this tool's correlator read the same R1 data
through completely separate code. If they disagree about which switches are
cabled together, one of them is wrong -- and finding out which is far cheaper
now than after four more phases are built on top.

They deliberately COUNT differently, and the test accounts for it: WiredWiz
counts one-sided port observations (one per up port whose LLDP neighbour is a
managed switch), while Topology merges the two halves of a mutual observation
into one link. A mutual pair is 2 there and 1 here. The comparison that has to
hold exactly is the SET OF DEVICE PAIRS.

Usage:
    docker compose exec backend python scripts/test_topology_correlate.py \\
        <controller_id> --tenant <ec-id> [--venue <venue-id>]
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.r1_client import create_r1_client_from_controller
from database import SessionLocal
from models.controller import Controller
from services.topology import collect as topo_collect
from services.topology.correlate import switch_graph
from services.topology.normalize import norm_mac
from services.wiredwiz import analyze as ww_analyze
from services.wiredwiz import crawl as ww_crawl

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("controller_id", type=int)
    parser.add_argument("--tenant")
    parser.add_argument("--venue")
    args = parser.parse_args()

    db = SessionLocal()
    controller = db.query(Controller).filter(Controller.id == args.controller_id).first()
    if not controller:
        sys.exit(f"No controller {args.controller_id}")
    override_tenant = args.tenant if controller.controller_subtype == "MSP" else None
    if controller.controller_subtype == "MSP" and not override_tenant:
        sys.exit("MSP controller: pass --tenant")

    r1 = create_r1_client_from_controller(controller.id, db)
    venue = args.venue
    if not venue:
        rows = r1.switches.list_switches(override_tenant) or []
        counts = Counter(row.get("venueId") for row in rows)
        venue = counts.most_common(1)[0][0]
    venues = [venue]
    print(f"venue {venue}\n")

    # Two independent reads of the same network.
    print("reading (WiredWiz)...")
    ww_snap = ww_crawl.take_snapshot(r1, override_tenant, venues)
    ww_topo = ww_analyze.topology(ww_snap)

    print("reading (Topology)...")
    snapshot = await topo_collect.discover(r1, override_tenant, venues)
    report = snapshot.correlation
    graph = switch_graph(snapshot)

    print(f"\ncorrelation: {report['claims']} claims -> {report['links']} links "
          f"in {report['elapsedSeconds']}s")
    print("by tier:", json.dumps(report["byTier"]))
    print("by kind:", json.dumps(report["byKind"]))
    print("by directionality:", json.dumps(report["byDirectionality"]))
    if report["sourcesFailed"]:
        print("SOURCES FAILED:", json.dumps(report["sourcesFailed"], indent=1))
    print("\nsources:")
    for entry in report["sourcesRun"]:
        print(f"    {entry['id']:<28} {entry['claims']:>6} claims")

    # ── the gate ────────────────────────────────────────────────────────────
    print("\n=== PHASE 2 GATE: switch graph vs WiredWiz ===")
    check("no evidence source crashed", not report["sourcesFailed"],
          json.dumps(report["sourcesFailed"])[:200])

    # Rebuild WiredWiz's pair set in device-id terms so the two are comparable.
    ww_switch_macs = {norm_mac(s.get("switchMac") or s.get("id"))
                      for s in ww_snap["switches"]} - {""}
    ww_pairs = set()
    ww_observations = 0
    for port in ww_snap["ports"]:
        if not ww_analyze.is_up(port):
            continue
        me, neighbour = norm_mac(port.get("switchMac")), norm_mac(port.get("neighborMacAddress"))
        if me and neighbour and neighbour in ww_switch_macs and neighbour != me:
            ww_observations += 1
            ww_pairs.add(tuple(sorted([f"sw:{me}", f"sw:{neighbour}"])))

    topo_pairs = graph["pairs"]
    print(f"  WiredWiz: {ww_observations} port observations over {len(ww_pairs)} pairs")
    print(f"  Topology: {graph['observations']} observations over "
          f"{len(topo_pairs)} pairs")

    check("device-pair sets are identical", ww_pairs == topo_pairs,
          f"ww-only={len(ww_pairs - topo_pairs)} topo-only={len(topo_pairs - ww_pairs)}")
    if ww_pairs != topo_pairs:
        names = graph["names"]
        for pair in list(ww_pairs - topo_pairs)[:5]:
            print(f"      WiredWiz saw, Topology missed: "
                  f"{names.get(pair[0], pair[0])} <-> {names.get(pair[1], pair[1])}")
        for pair in list(topo_pairs - ww_pairs)[:5]:
            print(f"      Topology saw, WiredWiz missed: "
                  f"{names.get(pair[0], pair[0])} <-> {names.get(pair[1], pair[1])}")

    check("merging halved the observation count, not lost links",
          graph["observations"] >= len(topo_pairs),
          f"{graph['observations']} observations for {len(topo_pairs)} pairs")

    # ── invariants that would betray a broken merge ─────────────────────────
    print("\n=== invariants ===")
    links = snapshot.links
    ids = [l.id for l in links]
    check("link ids are unique", len(ids) == len(set(ids)),
          f"{len(ids) - len(set(ids))} duplicates")
    check("no link joins a device to itself",
          not [l for l in links
               if l.a.device_id == l.b.device_id and l.kind != "stack"])

    # Evidence must never be double-counted: the same fact read from both port
    # rows has to collapse to one row.
    dupes = [l for l in links
             if len({e.id for e in l.evidence}) != len(l.evidence)]
    check("no link counts the same evidence twice", not dupes,
          f"{len(dupes)} links with duplicate evidence")

    # A source may never exceed its own tier cap.
    from services.topology.score import WEIGHTS
    from services.topology.model import TIER_RANK
    violations = []
    for link in links:
        if link.override is not None:
            continue
        caps = [WEIGHTS.get(e.source, (None, 0))[0] for e in link.evidence
                if e.kind == "support"]
        caps = [c for c in caps if c]
        best = max((TIER_RANK[c] for c in caps), default=TIER_RANK["weak"])
        if TIER_RANK[link.tier] > best:
            violations.append((link.id, link.tier, caps))
    check("no link exceeds its best source's tier cap", not violations,
          str(violations[:3]))

    # Only mutual agreement, a structural fact or a human reaches 'confirmed'.
    confirmed = [l for l in links if l.tier == "confirmed"]
    bad = [l for l in confirmed
           if not any(e.source in ("link.mutual", "stack.interconnect")
                      or e.source.startswith("override.") for e in l.evidence)]
    check("'confirmed' is only reached by mutual LLDP, stack, or a human",
          not bad, f"{len(bad)} links confirmed without one")
    print(f"       ({len(confirmed)} confirmed links)")

    # Every link must be able to explain itself.
    silent = [l for l in links if not l.tier_reason]
    check("every link states a reason", not silent, f"{len(silent)} without one")

    scored = [l for l in links if abs(l.score - sum(e.weight for e in l.evidence)) > 0.01]
    check("score equals the sum of its evidence weights", not scored,
          f"{len(scored)} links where the arithmetic does not add up")

    # ── PHASE 3 GATE: AP uplinks ────────────────────────────────────────────
    print("\n=== PHASE 3 GATE: AP uplinks ===")
    by_id = {d.id: d for d in snapshot.devices}
    aps = [d for d in snapshot.devices if d.kind == "ap"]
    online = [a for a in aps if a.status == "online"]

    uplinks = {}
    for link in links:
        if link.tier == "rejected" or link.logical_of:
            continue
        for near, far in ((link.a, link.b), (link.b, link.a)):
            device = by_id.get(near.device_id)
            other = by_id.get(far.device_id)
            if device is None or device.kind != "ap":
                continue
            # An uplink is a link to infrastructure, not to another AP's mesh
            # child or a client.
            if other is not None and other.kind in ("switch", "stack", "external"):
                uplinks.setdefault(device.id, []).append(link)

    have_one = [a for a in online if len(uplinks.get(a.id, [])) == 1]
    have_none = [a for a in online if not uplinks.get(a.id)]
    have_many = [a for a in online if len(uplinks.get(a.id, [])) > 1]

    # An AP fed by a switch R1 does not manage is UNKNOWABLE from a read-only
    # surface: no switchSerialNumber, no LLDP anywhere, nothing to correlate.
    # The gate must not demand a link the data cannot support -- inventing one
    # would be the failure. So the real question is: of the APs where an uplink
    # is knowable at all, does exactly one resolve?
    def has_any_evidence(ap):
        if ap.attrs.get("switchSerialNumber"):
            return True
        return any(p.neighbor_mac == ap.mac for p in snapshot.ports)

    knowable = [a for a in online if has_any_evidence(a)]
    unknowable = [a for a in online if not has_any_evidence(a)]
    resolved = [a for a in knowable if len(uplinks.get(a.id, [])) == 1]

    print(f"  online APs: {len(online)} of {len(aps)}")
    print(f"    exactly one uplink: {len(have_one)}")
    print(f"    no uplink:          {len(have_none)}")
    print(f"    more than one:      {len(have_many)}")
    print(f"  uplink knowable for:  {len(knowable)}  "
          f"(unknowable: {len(unknowable)})")

    check("every AP with uplink evidence resolves to exactly one",
          len(knowable) and len(resolved) == len(knowable),
          f"{len(resolved)}/{len(knowable)}")
    check("no online AP has more than one uplink", not have_many,
          f"{len(have_many)} do")
    # The unattached ones must be unattached because the DATA is silent, not
    # because the correlator dropped something it was given.
    check("every unlinked AP genuinely has no uplink evidence",
          all(not has_any_evidence(a) for a in have_none),
          f"{sum(1 for a in have_none if has_any_evidence(a))} had evidence and were dropped")
    check("the report counts the unattached APs",
          report.get("unattachedAps") == len(have_none),
          f"report says {report.get('unattachedAps')}, found {len(have_none)}")
    for ap in unknowable[:3]:
        print(f"      unknowable: {ap.display_name} "
              f"(poePortStatus={ap.attrs.get('poePortStatus')!r}, "
              f"no switchSerialNumber, no LLDP) -- fed by a switch R1 "
              f"does not manage")
    for ap in have_many[:3]:
        peers = [by_id.get(l.b.device_id if l.a.device_id == ap.id else l.a.device_id)
                 for l in uplinks[ap.id]]
        print(f"      multi: {ap.display_name} -> "
              f"{[p.display_name if p else '?' for p in peers]}")

    # Mutual confirmation must come from INDEPENDENT channels only.
    ap_confirmed = [l for l in links if l.tier == "confirmed"
                    and (by_id.get(l.a.device_id) or by_id.get(l.b.device_id))
                    and any((by_id.get(e.device_id) or by_id.get(e.device_id)) is not None
                            and (by_id.get(e.device_id).kind == "ap")
                            for e in (l.a, l.b) if by_id.get(e.device_id))]
    derived_only = []
    for link in ap_confirmed:
        mutual = [e for e in link.evidence if e.source == "link.mutual"]
        for item in mutual:
            channels = set(item.fields.get("channels") or [])
            if not (channels & {"switch-lldp", "ap-lldp"}):
                derived_only.append(link.id)
    check("no AP link is confirmed on derived evidence alone", not derived_only,
          f"{len(derived_only)} are")
    print(f"       ({len(ap_confirmed)} AP links confirmed)")

    # ── a worked example, so the output is inspectable by eye ───────────────
    sw_links = [l for l in links if l.kind == "ethernet"
                and l.directionality == "bidirectional"]
    if sw_links:
        example = max(sw_links, key=lambda l: len(l.evidence))
        names = graph["names"]
        print(f"\n=== worked example ===")
        print(f"  {names.get(example.a.device_id, example.a.device_id)} "
              f"{example.a.ident} <-> "
              f"{names.get(example.b.device_id, example.b.device_id)} "
              f"{example.b.ident}")
        print(f"  tier={example.tier} score={example.score:+.2f} "
              f"confidence={example.confidence:.3f}")
        print(f"  because: {example.tier_reason}")
        running = 0.0
        for item in example.evidence:
            running += item.weight
            mark = {"support": "+", "contradict": "!", "context": "."}[item.kind]
            print(f"    {mark} {item.weight:+5.1f} -> {running:+6.2f}  "
                  f"{item.source}")
            print(f"           {item.claim}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
