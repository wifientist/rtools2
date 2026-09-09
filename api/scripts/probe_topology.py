"""
Probe: which R1 endpoints can actually carry a topology, and what do they fill in?

The OpenAPI spec documents the *surface*. It does not say whether
`/venues/{id}/topologies` returns anything on a real tenant, whether an AP's
LLDP neighbour cache is warm, or what format `lldpPortID` arrives in. Six of
the endpoints the Topology tool wants are called by nothing in this repo, so
every field list below is a hypothesis until this script runs.

Everything here is a GET or a `*/query` POST -- the same read-only surface the
tool itself uses. Nothing is created, modified, deleted, rebooted or synced.
In particular the PATCH that refreshes an AP's neighbour cache is deliberately
NOT called: if `neighbors/query` comes back cold, that is a finding, not a
problem to work around.

Usage:
    docker compose exec backend python scripts/probe_topology.py <controller_id> [options]

Options:
    --tenant <id>       MSP-EC to probe (MSP controllers; default: first EC with switches)
    --venue <id>        venue to probe (default: the venue with the most switches)
    --section <name>    run one section only (repeatable). Default: all.
                        topologies mesh apneighbors members lags vlans veports
                        staticroutes portfields identity
    --ap-sample <n>     APs to query for LLDP neighbours (default 20, 0 = every AP)
    --switch-sample <n> switches for per-switch reads (default 10, 0 = every switch)
    --json              dump raw section payloads instead of the summary
"""
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.r1_client import create_r1_client_from_controller
from database import SessionLocal
from models.controller import Controller

SECTIONS = ["topologies", "mesh", "apneighbors", "members", "lags", "vlans",
            "veports", "staticroutes", "portfields", "identity"]

# Port fields WiredWiz already requests, plus the three Topology wants added.
# Probing them together is the point: a field list R1 rejects fails the whole
# query, so "does adding portMac break the existing crawl?" must be answered
# before PORT_FIELDS is edited.
EXTRA_PORT_FIELDS = ["portMac", "switchModel", "authDefaultVlan"]

_HEX = re.compile(r"[0-9a-f]{12}$")


def norm_mac(v) -> str:
    """12 lowercase hex chars, or "" if it isn't a MAC.

    Strict on purpose: a value that will not normalise must never fall through
    as a truthy join key. This is the normalizer the tool will use, exercised
    here against real data before anything depends on it.
    """
    s = re.sub(r"[^0-9a-fA-F]", "", str(v or "")).lower()
    return s if _HEX.match(s) else ""


def population(rows, fields):
    """How many rows carry a non-empty value for each field."""
    total = len(rows) or 1
    counts = Counter()
    for row in rows:
        for field in fields:
            if row.get(field) not in (None, "", [], {}):
                counts[field] += 1
    return {f: f"{counts[f]}/{len(rows)} ({counts[f] * 100 // total}%)" for f in fields}


def all_keys(rows, limit=400):
    """Every key seen across a sample of rows -- catches fields the spec omits."""
    keys = set()
    for row in rows[:limit]:
        if isinstance(row, dict):
            keys.update(row.keys())
    return sorted(keys)


class Probe:
    def __init__(self, r1, tenant, args):
        self.r1, self.tenant, self.args = r1, tenant, args
        self.results = {}

    def _tid(self):
        """Only MSP controllers take the tenant override header (PISR's rule)."""
        return self.tenant if getattr(self.r1, "ec_type", None) == "MSP" else None

    def call(self, method, path, payload=None, params=None, label=None):
        """One read. Never raises -- a dead endpoint is a result, not a crash."""
        started = time.time()
        try:
            if method == "get":
                resp = self.r1.get(path, params=params, override_tenant_id=self._tid())
            else:
                resp = self.r1.post(path, payload=payload, params=params,
                                    override_tenant_id=self._tid())
        except Exception as exc:                                    # noqa: BLE001
            return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {exc}",
                    "elapsedMs": int((time.time() - started) * 1000), "body": None}

        out = {"ok": bool(resp is not None and resp.ok),
               "status": getattr(resp, "status_code", None),
               "elapsedMs": int((time.time() - started) * 1000),
               "error": None, "body": None}
        if resp is None:
            out["error"] = "no response"
            return out
        try:
            out["body"] = resp.json()
        except Exception:                                           # noqa: BLE001
            out["error"] = (resp.text or "")[:300]
        if not out["ok"] and not out["error"]:
            out["error"] = (resp.text or "")[:300]
        print(f"    {method.upper()} {label or path} -> {out['status']} "
              f"({out['elapsedMs']}ms)" + (f" {out['error'][:120]}" if out["error"] else ""))
        return out

    def rows(self, body):
        """Unwrap the three envelopes R1 uses: ES, Spring Page, and bare list."""
        if body is None:
            return []
        if isinstance(body, list):
            return body
        for key in ("data", "content", "list", "neighbors"):
            value = body.get(key)
            if isinstance(value, list):
                return value
        return []

    # ── sections ────────────────────────────────────────────────────────────

    def topologies(self, venue_id, ctx):
        """The headline unknown: does R1 hand us a ready-made graph?"""
        print("\n=== /venues/{v}/topologies ===")
        out = {}
        # meshOnly is a REQUIRED query param per the spec. Probe both, and probe
        # it missing too -- "required" in a spec is not always required in fact.
        for label, params in (("meshOnly=false", {"meshOnly": "false"}),
                              ("meshOnly=true", {"meshOnly": "true"}),
                              ("(omitted)", None)):
            res = self.call("get", f"/venues/{venue_id}/topologies", params=params,
                            label=f"topologies {label}")
            out[label] = {k: v for k, v in res.items() if k != "body"}
            if not res["ok"]:
                continue
            entries = self.rows(res["body"])
            out[label]["entries"] = len(entries)
            nodes = [n for e in entries if isinstance(e, dict) for n in (e.get("nodes") or [])]
            edges = [g for e in entries if isinstance(e, dict) for g in (e.get("edges") or [])]
            out[label].update({"nodes": len(nodes), "edges": len(edges)})
            print(f"      entries={len(entries)} nodes={len(nodes)} edges={len(edges)}")
            if isinstance(res["body"], dict):
                print(f"      envelope keys: {sorted(res['body'].keys())}")
            if nodes:
                print(f"      node keys: {all_keys(nodes)}")
                print("      node population:", json.dumps(population(nodes, [
                    "id", "name", "type", "serial", "mac", "ipAddress", "status",
                    "meshRole", "parentId", "parentMac", "cloudPort", "isConnectedCloud",
                    "uplink", "downlink", "taggedVlan", "untaggedVlan"]), indent=1))
                print("      node types:", dict(Counter(n.get("type") for n in nodes)))
                print("      sample node:", json.dumps(nodes[0], indent=1)[:700])
                out[label]["nodeTypes"] = dict(Counter(str(n.get("type")) for n in nodes))
            if edges:
                print(f"      edge keys: {all_keys(edges)}")
                # THE question for the confidence engine: do we get BOTH ends'
                # ports? If yes this is the strongest single source in the tool.
                print("      edge population:", json.dumps(population(edges, [
                    "from", "fromMac", "fromName", "fromSerial", "fromRole",
                    "to", "toMac", "toName", "toSerial", "toRole",
                    "connectionType", "connectionStatus",
                    "connectedPort", "connectedPortTaggedVlan", "connectedPortUntaggedVlan",
                    "correspondingPort", "correspondingPortTaggedVlan",
                    "linkSpeed", "band", "channel", "poeEnabled", "extraEdges"]), indent=1))
                both = sum(1 for e in edges if e.get("connectedPort") and e.get("correspondingPort"))
                print(f"      *** edges with BOTH ports: {both}/{len(edges)} ***")
                print("      connectionType:", dict(Counter(str(e.get("connectionType")) for e in edges)))
                print("      connectionStatus:", dict(Counter(str(e.get("connectionStatus")) for e in edges)))
                print("      sample edge:", json.dumps(edges[0], indent=1)[:900])
                extra = [e for e in edges if e.get("extraEdges")]
                if extra:
                    print(f"      extraEdges on {len(extra)} edges, sample:",
                          json.dumps(extra[0]["extraEdges"], indent=1)[:400])
                out[label].update({
                    "edgesWithBothPorts": both,
                    "connectionTypes": dict(Counter(str(e.get("connectionType")) for e in edges)),
                })
                ctx["r1_edges"] = edges
                ctx["r1_nodes"] = nodes
        self.results["topologies"] = out

    def mesh(self, venue_id, ctx):
        print("\n=== /venues/{v}/meshTopologies ===")
        res = self.call("get", f"/venues/{venue_id}/meshTopologies")
        out = {k: v for k, v in res.items() if k != "body"}
        if res["ok"]:
            entries = self.rows(res["body"])
            edges = [g for e in entries if isinstance(e, dict) for g in (e.get("edges") or [])]
            out.update({"entries": len(entries), "edges": len(edges)})
            print(f"      entries={len(entries)} edges={len(edges)}")
            if edges:
                print("      sample:", json.dumps(edges[0], indent=1)[:500])
            # Does /topologies already subsume mesh? Decides whether we need both.
            if ctx.get("r1_edges") is not None:
                base = {(str(e.get("fromMac")), str(e.get("toMac"))) for e in ctx["r1_edges"]}
                extra = [e for e in edges
                         if (str(e.get("fromMac")), str(e.get("toMac"))) not in base]
                print(f"      mesh edges NOT in /topologies: {len(extra)}/{len(edges)}")
                out["notInTopologies"] = len(extra)
        self.results["mesh"] = out

    def apneighbors(self, venue_id, ctx):
        """The only real LLDP TLV endpoint -- and one call per AP."""
        print("\n=== /venues/{v}/aps/{serial}/neighbors/query ===")
        aps = ctx.get("aps") or []
        if not aps:
            print("      no APs in this venue")
            self.results["apneighbors"] = {"aps": 0}
            return
        sample = aps if self.args.ap_sample == 0 else aps[:self.args.ap_sample]
        print(f"      querying {len(sample)} of {len(aps)} APs")

        with_rows, latencies, all_rows, statuses = 0, [], [], Counter()
        for ap in sample:
            serial = ap.get("serialNumber")
            if not serial:
                continue
            res = self.call("post", f"/venues/{venue_id}/aps/{serial}/neighbors/query",
                            payload={"filters": [{"type": "LLDP_NEIGHBOR"}],
                                     "page": 0, "pageSize": 100},
                            label=f"neighbors {serial}")
            statuses[res["status"]] += 1
            latencies.append(res["elapsedMs"])
            if res["ok"]:
                rows = self.rows(res["body"])
                if rows:
                    with_rows += 1
                    all_rows.extend(rows)

        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0
        # This number decides whether the source is opt-in, capped, or dropped.
        projected = median * len(aps) / 1000.0
        print(f"\n      statuses: {dict(statuses)}")
        print(f"      *** APs returning >=1 LLDP row: {with_rows}/{len(sample)} ***")
        print(f"      median latency {median}ms -> {len(aps)} APs serialised "
              f"= {projected:.0f}s (budget 300s)")
        out = {"aps": len(aps), "sampled": len(sample), "withRows": with_rows,
               "medianMs": median, "projectedSerialSeconds": round(projected),
               "statuses": {str(k): v for k, v in statuses.items()}}
        if all_rows:
            print(f"      rows: {len(all_rows)}, keys: {all_keys(all_rows)}")
            print("      population:", json.dumps(population(all_rows, [
                "lldpChassisID", "lldpPortID", "lldpPortDesc", "lldpSysName",
                "lldpSysDesc", "lldpCapability", "lldpMgmtIP", "lldpInterface",
                "neighborManaged", "neighborSerialNumber", "detectedTime"]), indent=1))
            # Format of lldpPortID decides which resolution path the tool needs.
            fmt = Counter()
            for row in all_rows:
                pid = str(row.get("lldpPortID") or "")
                fmt["mac" if norm_mac(pid) else
                    "u/s/p" if re.match(r"^\d+/\d+/\d+$", pid) else
                    "numeric" if pid.isdigit() else
                    "empty" if not pid else "other"] += 1
            print(f"      *** lldpPortID format: {dict(fmt)} ***")
            print("      sample:", json.dumps(all_rows[0], indent=1)[:800])
            out["portIdFormats"] = dict(fmt)
            ctx["ap_lldp"] = all_rows
        self.results["apneighbors"] = out

    def members(self, venue_id, ctx):
        print("\n=== /venues/switches/members/query ===")
        out = {}
        for label, payload in (
            ("venueId filter", {"fields": ["activeSerial", "members"],
                                "filters": {"venueId": [venue_id]},
                                "page": 0, "pageSize": 100}),
            ("no filter", {"page": 0, "pageSize": 100}),
        ):
            res = self.call("post", "/venues/switches/members/query", payload=payload,
                            label=f"members ({label})")
            entry = {k: v for k, v in res.items() if k != "body"}
            if res["ok"]:
                rows = self.rows(res["body"])
                entry["rows"] = len(rows)
                if isinstance(res["body"], dict):
                    entry["envelope"] = sorted(res["body"].keys())
                    print(f"      rows={len(rows)} envelope={sorted(res['body'].keys())}")
                if rows:
                    print(f"      keys: {all_keys(rows)}")
                    print("      sample:", json.dumps(rows[0], indent=1)[:600])
            out[label] = entry
        self.results["members"] = out

    def _per_switch(self, name, venue_id, ctx, path_fn, fields, method="get", payload=None):
        """lags / vePorts / staticRoutes all share this shape."""
        print(f"\n=== {name} ===")
        switches = ctx.get("switches") or []
        sample = switches if self.args.switch_sample == 0 else switches[:self.args.switch_sample]
        print(f"      querying {len(sample)} of {len(switches)} switches")
        with_rows, all_rows, statuses, latencies = 0, [], Counter(), []
        for sw in sample:
            sid = sw.get("id") or sw.get("switchMac")
            if not sid:
                continue
            res = self.call(method, path_fn(venue_id, sid), payload=payload,
                            label=f"{name} {sw.get('name') or sid}")
            statuses[res["status"]] += 1
            latencies.append(res["elapsedMs"])
            if res["ok"]:
                rows = self.rows(res["body"])
                if rows:
                    with_rows += 1
                    all_rows.extend(rows)
        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0
        print(f"      statuses: {dict(statuses)}")
        print(f"      switches with >=1 row: {with_rows}/{len(sample)}  median {median}ms")
        out = {"sampled": len(sample), "withRows": with_rows, "rows": len(all_rows),
               "medianMs": median, "statuses": {str(k): v for k, v in statuses.items()}}
        if all_rows:
            print(f"      keys: {all_keys(all_rows)}")
            print("      population:", json.dumps(population(all_rows, fields), indent=1))
            print("      sample:", json.dumps(all_rows[0], indent=1)[:700])
            ctx[name] = all_rows
        self.results[name] = out
        return all_rows

    def lags(self, venue_id, ctx):
        rows = self._per_switch(
            "lags", venue_id, ctx,
            lambda v, s: f"/venues/{v}/switches/{s}/lags",
            ["id", "lagId", "name", "type", "ports", "taggedVlans", "untaggedVlan"])
        if rows:
            # Port format decides whether canon_port_ident needs another case.
            fmt = Counter()
            for row in rows:
                for port in (row.get("ports") or []):
                    p = str(port)
                    fmt["u/s/p" if re.match(r"^\d+/\d+/\d+$", p)
                        else "named" if re.search(r"[A-Za-z]", p) else "other"] += 1
            print(f"      lag ports[] format: {dict(fmt)}")
            self.results["lags"]["portFormats"] = dict(fmt)

    def veports(self, venue_id, ctx):
        """VLAN -> subnet. Feeds both WAN inference and the v2 VLAN overlay."""
        self._per_switch(
            "veports", venue_id, ctx,
            lambda v, s: f"/venues/{v}/switches/{s}/vePorts",
            ["veId", "vlanId", "name", "ipAddress", "ipSubnetMask", "ipAddressType",
             "dhcpRelayAgent", "switchId", "switchName"])

    def staticroutes(self, venue_id, ctx):
        """Default route == the strongest WAN-uplink signal there is."""
        rows = self._per_switch(
            "staticroutes", venue_id, ctx,
            lambda v, s: f"/venues/{v}/switches/{s}/staticRoutes",
            ["destinationIp", "nextHop", "adminDistance"])
        default = [r for r in rows if str(r.get("destinationIp") or "").startswith("0.0.0.0")]
        print(f"      *** switches carrying a DEFAULT route: {len(default)} ***")
        for row in default[:5]:
            print(f"        {row.get('destinationIp')} -> {row.get('nextHop')}")
        self.results["staticroutes"]["defaultRoutes"] = len(default)

    def vlans(self, venue_id, ctx):
        """Spec says the body is array<string>, not a query DTO. Probe both."""
        print("\n=== /venues/{v}/vlans/query ===")
        switch_ids = [s.get("id") for s in (ctx.get("switches") or [])[:5] if s.get("id")]
        out = {}
        for label, payload in (("empty list", []),
                               ("switch id list", switch_ids),
                               ("query dto", {"page": 0, "pageSize": 100})):
            res = self.call("post", f"/venues/{venue_id}/vlans/query", payload=payload,
                            label=f"vlans ({label})")
            entry = {k: v for k, v in res.items() if k != "body"}
            if res["ok"]:
                rows = self.rows(res["body"])
                entry["rows"] = len(rows)
                print(f"      rows={len(rows)}")
                if rows:
                    print(f"      keys: {all_keys(rows)}")
                    vlan_lists = [v for r in rows for v in (r.get("vlanList") or [])]
                    entry["vlanEntries"] = len(vlan_lists)
                    print(f"      vlanList entries: {len(vlan_lists)}")
                    if vlan_lists:
                        print("      vlan population:", json.dumps(population(vlan_lists, [
                            "vlanId", "vlanName", "taggedPorts", "untaggedPorts",
                            "isAuthVlan", "usedByVePort", "spanningTreeProtocol"]), indent=1))
                        print("      sample:", json.dumps(vlan_lists[0], indent=1)[:600])
            out[label] = entry
        self.results["vlans"] = out

    def portfields(self, venue_id, ctx):
        """Does R1 accept the three fields we want to add -- and fill them in?"""
        print("\n=== switchPorts/query with EXTRA fields ===")
        from r1api.services.switches import PORT_FIELDS

        for label, fields in (("current PORT_FIELDS", list(PORT_FIELDS)),
                              ("+ extra", list(PORT_FIELDS) + EXTRA_PORT_FIELDS)):
            res = self.call("post", "/venues/switches/switchPorts/query",
                            payload={"fields": fields, "filters": {"venueId": [venue_id]},
                                     "page": 0, "pageSize": 1000,
                                     "sortField": "id", "sortOrder": "ASC"},
                            label=f"switchPorts ({label})")
            if not res["ok"]:
                self.results.setdefault("portfields", {})[label] = {
                    "ok": False, "status": res["status"], "error": res["error"]}
                continue
            rows = self.rows(res["body"])
            print(f"      rows={len(rows)}")
            entry = {"ok": True, "rows": len(rows)}
            if rows and "extra" in label:
                print("      extra field population:",
                      json.dumps(population(rows, EXTRA_PORT_FIELDS), indent=1))
                entry["extraPopulation"] = population(rows, EXTRA_PORT_FIELDS)
                ctx["sample_ports"] = rows
            self.results.setdefault("portfields", {})[label] = entry

        # One page is enough to prove the fields are accepted, but NOT to measure
        # bidirectionality -- a partial port set makes links look one-sided that
        # are not. Crawl the whole venue for anything that counts links.
        ctx["ports"] = r1_ports = self.r1.switches.crawl_ports(self.tenant, [venue_id]) or []
        print(f"      full venue crawl: {len(r1_ports)} ports")

        ports = ctx.get("sample_ports") or []
        if not ports:
            return
        # The measurement the whole merge design hinges on. If these two fields
        # are always equal, the far-end PORT is never knowable from the near
        # side and port-to-port links must be resolved by pairing both ends.
        both = [p for p in ports if p.get("neighborMacAddress") and p.get("neighborPortMacAddress")]
        same = sum(1 for p in both
                   if norm_mac(p["neighborMacAddress"]) == norm_mac(p["neighborPortMacAddress"]))
        print(f"\n      *** neighborMac == neighborPortMac: {same}/{len(both)} "
              f"({same * 100 // (len(both) or 1)}%) ***")
        distinct = [p for p in both
                    if norm_mac(p["neighborMacAddress"]) != norm_mac(p["neighborPortMacAddress"])]
        if distinct:
            print("      sample with DISTINCT port mac:", json.dumps(
                {k: distinct[0].get(k) for k in
                 ("switchName", "portIdentifier", "neighborName",
                  "neighborMacAddress", "neighborPortMacAddress", "portMac")}, indent=1))
        self.results["portfields"]["neighborMacEqualsPortMac"] = f"{same}/{len(both)}"

    def identity(self, venue_id, ctx):
        """A correlation dry run: would the AliasIndex actually resolve anything?"""
        print("\n=== identity / correlation dry run ===")
        switches = ctx.get("switches") or []
        ports = ctx.get("ports") or []
        aps = ctx.get("aps") or []
        macs = ctx.get("macs") or []
        if not (switches and ports):
            print("      need switches + ports; run with portfields")
            return

        sw_macs = {norm_mac(s.get("switchMac") or s.get("id")) for s in switches}
        sw_macs.discard("")
        ap_macs = {norm_mac(a.get("macAddress")) for a in aps}
        ap_macs.discard("")
        ap_serials = {str(a.get("serialNumber")) for a in aps if a.get("serialNumber")}
        # OUI set derived from managed devices -- NOT a hardcoded vendor list.
        tenant_ouis = {m[:6] for m in (sw_macs | ap_macs)}

        with_nbr = [p for p in ports if norm_mac(p.get("neighborMacAddress"))]
        to_switch = [p for p in with_nbr if norm_mac(p["neighborMacAddress"]) in sw_macs]
        to_ap = [p for p in with_nbr if norm_mac(p["neighborMacAddress"]) in ap_macs]
        unresolved = [p for p in with_nbr
                      if norm_mac(p["neighborMacAddress"]) not in (sw_macs | ap_macs)]
        foreign_oui = [p for p in unresolved
                       if norm_mac(p["neighborMacAddress"])[:6] not in tenant_ouis]

        print(f"      switches={len(switches)} ports={len(ports)} aps={len(aps)} macs={len(macs)}")
        print(f"      ports with LLDP neighbour MAC: {len(with_nbr)}/{len(ports)}")
        print(f"        -> managed switch: {len(to_switch)}")
        print(f"        -> managed AP:     {len(to_ap)}")
        print(f"        -> unresolved:     {len(unresolved)}")
        print(f"        -> foreign OUI (WAN candidates): {len(foreign_oui)}")

        # Bidirectionality: the evidence that earns tier "confirmed".
        by_switch = defaultdict(list)
        for p in ports:
            by_switch[norm_mac(p.get("switchMac"))].append(p)
        bidir = sum(1 for p in to_switch
                    if any(norm_mac(q.get("neighborMacAddress")) == norm_mac(p.get("switchMac"))
                           for q in by_switch.get(norm_mac(p["neighborMacAddress"]), [])))
        print(f"      *** switch<->switch bidirectional: {bidir}/{len(to_switch)} ***")

        # Name truncation and prefix ambiguity: decides the weight of name evidence.
        names = [str(p.get("neighborName")) for p in ports if p.get("neighborName")]
        trunc = [n for n in names if len(n) == 32]
        managed_names = [str(s.get("name") or "") for s in switches] + \
                        [str(a.get("name") or "") for a in aps]
        ambiguous = 0
        for n in set(trunc):
            hits = sum(1 for m in managed_names if m.casefold().startswith(n.casefold()))
            if hits > 1:
                ambiguous += 1
        print(f"      neighbour names: {len(names)}, exactly 32 chars: {len(trunc)} "
              f"({len(trunc) * 100 // (len(names) or 1)}%)")
        print(f"      *** 32-char prefixes matching >1 managed device: {ambiguous}"
              f"/{len(set(trunc))} ***")

        # AP self-report: does switchSerialNumber resolve, and to what?
        sw_serials = {str(s.get("serialNumber")) for s in switches if s.get("serialNumber")}
        with_sw = [a for a in aps if a.get("switchSerialNumber")]
        resolves = sum(1 for a in with_sw if str(a["switchSerialNumber"]) in sw_serials)
        print(f"      APs reporting switchSerialNumber: {len(with_sw)}/{len(aps)}, "
              f"resolving to a managed switch: {resolves}")

        # MAC table: is a managed device's MAC on exactly one port?
        ports_per_mac = defaultdict(set)
        for m in macs:
            mac = norm_mac(m.get("clientMac"))
            if mac:
                ports_per_mac[mac].add(m.get("switchPortId"))
        managed_seen = {m: p for m, p in ports_per_mac.items() if m in (sw_macs | ap_macs)}
        single = sum(1 for p in managed_seen.values() if len(p) == 1)
        print(f"      managed device MACs in the MAC table: {len(managed_seen)}, "
              f"on exactly one port: {single}")
        ruckus_flag = sum(1 for m in macs if m.get("isRuckusAP"))
        print(f"      rows flagged isRuckusAP: {ruckus_flag}/{len(macs)}")

        self.results["identity"] = {
            "switches": len(switches), "ports": len(ports), "aps": len(aps), "macs": len(macs),
            "portsWithNeighbourMac": len(with_nbr), "toManagedSwitch": len(to_switch),
            "toManagedAp": len(to_ap), "unresolved": len(unresolved),
            "foreignOui": len(foreign_oui), "bidirectional": f"{bidir}/{len(to_switch)}",
            "namesTruncated32": f"{len(trunc)}/{len(names)}",
            "ambiguousPrefixes": f"{ambiguous}/{len(set(trunc))}",
            "apSelfReportResolving": f"{resolves}/{len(with_sw)}",
            "managedMacsSinglePort": f"{single}/{len(managed_seen)}",
            "isRuckusApFlagged": ruckus_flag,
            "apSerialsKnown": len(ap_serials),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("controller_id", type=int)
    parser.add_argument("--tenant")
    parser.add_argument("--venue")
    parser.add_argument("--section", action="append", choices=SECTIONS)
    parser.add_argument("--ap-sample", type=int, default=20)
    parser.add_argument("--switch-sample", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    controller = db.query(Controller).filter(Controller.id == args.controller_id).first()
    if not controller:
        sys.exit(f"No controller {args.controller_id}")
    if controller.controller_type != "RuckusONE":
        sys.exit(f"Controller {controller.id} is {controller.controller_type}; R1 only.")
    print(f"controller: {controller.name} ({controller.controller_subtype}) "
          f"region={controller.r1_region}")

    r1 = create_r1_client_from_controller(controller.id, db)
    if getattr(r1, "auth_failed", False):
        sys.exit(f"auth failed: {getattr(r1, 'auth_error', '?')}")

    tenant = args.tenant
    if controller.controller_subtype == "MSP":
        if not tenant:
            sys.exit("MSP controller: pass --tenant <ec-tenant-id>")
    else:
        tenant = tenant or controller.r1_tenant_id
    print(f"tenant: {tenant}")

    # Inventory first: every section needs switches, ports or APs to work from.
    print("\n=== inventory ===")
    r1.switches.reset_completeness()
    switches = r1.switches.list_switches(tenant) or []
    venues = defaultdict(list)
    for sw in switches:
        venues[sw.get("venueId")].append(sw)
    print(f"switches: {len(switches)} across {len(venues)} venues")
    for vid, rows in sorted(venues.items(), key=lambda kv: -len(kv[1]))[:8]:
        print(f"  {rows[0].get('venueName')} ({vid}): {len(rows)} switches")

    venue_id = args.venue
    if not venue_id:
        if not venues:
            sys.exit("No switches under this tenant.")
        venue_id = max(venues.items(), key=lambda kv: len(kv[1]))[0]
    venue_switches = venues.get(venue_id, [])
    print(f"\nvenue: {venue_switches[0].get('venueName') if venue_switches else '?'} "
          f"({venue_id}) -- {len(venue_switches)} switches")

    # AP_FIELDS is the list PISR proved against a live tenant -- the spec's own
    # names (apMac, deviceStatus, fwVersion) come back empty. Reuse, don't guess.
    from services.pisr.fetch import AP_FIELDS

    aps = []
    try:
        aps = r1.venues.query_all_aps_by_tenant(tenant, [venue_id], AP_FIELDS) or []
    except Exception as exc:                                        # noqa: BLE001
        print(f"  AP query failed: {type(exc).__name__}: {exc}")
    print(f"APs in venue: {len(aps)}")

    macs = r1.switches.crawl_mac_table(tenant, [venue_id]) or []
    print(f"MAC table rows: {len(macs)}")

    ctx = {"switches": venue_switches, "aps": aps, "macs": macs}
    probe = Probe(r1, tenant, args)

    wanted = args.section or SECTIONS
    # portfields populates ctx["ports"], which identity needs. Order matters.
    order = [s for s in SECTIONS if s in wanted]
    for name in order:
        try:
            getattr(probe, name)(venue_id, ctx)
        except Exception as exc:                                    # noqa: BLE001
            print(f"\n!! section {name} raised: {type(exc).__name__}: {exc}")
            probe.results[name] = {"error": f"{type(exc).__name__}: {exc}"}

    print("\n\n=== SUMMARY ===")
    print(json.dumps(probe.results, indent=1, default=str))
    print("\ncompleteness:", json.dumps(r1.switches.completeness_report(), default=str)[:400])


if __name__ == "__main__":
    main()
