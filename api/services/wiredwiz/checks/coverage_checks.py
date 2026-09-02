"""
Data-completeness checks — is the collected view actually whole?

Every other check in this package reasons over the crawled snapshot as if it
were the network. These three test that premise, because the failure mode of a
partial crawl is silence: a switch whose MAC table came back empty produces no
MAC findings, which on the report is indistinguishable from a clean switch.

What was verified against a 195-switch tenant while writing these:

  * **There is no ARP table in the RUCKUS ONE API.** The only path in the
    consolidated spec matching /arp/i is
    `/venues/{venueId}/edgeClusters/{clusterId}/arpTerminationSettings`, an SD-LAN
    edge setting unrelated to switch ARP. `clientIpv4Addr` on the switch-clients
    query is the ONLY MAC->IP binding R1 offers, and it is populated on a
    fraction of rows -- 87.9% on that tenant, 38% on another. So MAC->IP is a
    partial index, never an ARP table, and nothing here may assume otherwise.

  * **The MAC table does not always populate per switch.** One online
    ICX7150-24P reported 17 up ports and `clientCount` 16, while the clients
    query returned zero rows for it on a crawl that was otherwise verified
    complete (13,063/13,063 rows, 7/7 queries). That switch is invisible to
    every MAC-derived check, and nothing previously said so.

  * **Paging can silently truncate.** switches.py records an entry per paged
    query on the snapshot's `completeness` block, including whether a query hit
    the 10,000-row Elasticsearch window. Nothing read it until now.
"""

from .framework import Finding, _norm_mac, check

CAT_MAC = "mac-table"
CAT_META = "coverage"

ES_WINDOW = 10000

# Checks that reason over rows from the switch-clients query. Named in findings
# so "this switch reported no MAC data" lands as "these checks are blind here"
# rather than as a statistic.
MAC_DERIVED_CHECKS = [
    "dense-blind-port", "mac-flapping", "macs-on-down-port",
    "mac-table-outlier", "mac-table-growth", "mac-table-headroom",
]


@check("mac-table-not-reported", "Switch returned no MAC table", CAT_MAC,
       needs="snapshot",
       trigger="an ONLINE switch with at least one up port and clientCount >= 1 for which the clients query returned zero rows")
def mac_table_not_reported(ctx):
    """
    An online switch that returns no MAC rows at all, while reporting up ports
    and a non-zero client count of its own, has not had its forwarding table
    collected — the switch is not empty, the API did not report it.

    This is deliberately separate from `mac-count-mismatch`, which compares two
    numbers that both exist. Here one side is missing entirely, so the switch
    contributes nothing to any MAC-derived check and silently reads as clean.
    """
    blind = []
    for s in ctx.switches:
        if s.get("deviceStatus") != "ONLINE":
            continue
        claimed = s.get("clientCount") or 0
        if claimed < 1:
            continue
        if ctx.mac_count_for_switch(s) > 0:
            continue
        ports = ctx.ports_by_switch.get(_norm_mac(s.get("switchMac") or s.get("id")), [])
        up = sum(1 for p in ports if str(p.get("status", "")).lower() == "up")
        if up < 1:
            continue
        blind.append({"switch": s.get("name"), "model": s.get("model"),
                      "venue": s.get("venueName"), "upPorts": up,
                      "clientCount": claimed, "queryRows": 0})

    if not blind:
        return
    blind.sort(key=lambda b: -b["clientCount"])
    names = ", ".join(b["switch"] for b in blind[:4])
    yield Finding(
        "mac-table-not-reported",
        f"No MAC table returned for {len(blind)} online switch(es) — {names}"
        + (f" and {len(blind) - 4} more" if len(blind) > 4 else ""),
        "warning", CAT_MAC, blind[0]["switch"] if len(blind) == 1 else "fabric",
        f"{len(blind)} switch(es) are ONLINE with up ports and report a non-zero "
        "`clientCount`, yet the RUCKUS ONE clients query returned no MAC rows for them "
        f"at all — the largest is {blind[0]['switch']}, which claims "
        f"{blind[0]['clientCount']} clients across {blind[0]['upPorts']} up ports and "
        "returned zero. The forwarding table was not reported, so these switches "
        "contribute nothing to " + ", ".join(MAC_DERIVED_CHECKS) + ". Those checks "
        "passing on these switches means nothing was examined, not that nothing is wrong.",
        {"switches": blind, "count": len(blind),
         "blindChecks": MAC_DERIVED_CHECKS},
        "Confirm on the switch with `show mac-address count` — if the CLI shows entries "
        "the gap is in R1's reporting, and a re-crawl often fills it. Until then, do not "
        "read a clean MAC result on these switches as evidence of anything.",
        confidence="high",
    )


@check("ip-binding-coverage", "MAC-to-IP binding is partial", CAT_MAC,
       needs="snapshot",
       trigger="fewer than 95% of learned MAC entries carry clientIpv4Addr (warning below 60%)")
def ip_binding_coverage(ctx):
    """
    RUCKUS ONE exposes no switch ARP table. The only MAC->IP binding available is
    `clientIpv4Addr` on the clients query, and it is populated for some entries
    and not others — so "which host is this MAC" is answerable for part of the
    table and unanswerable for the rest.

    Reported as a measured percentage rather than assumed, because the gap is
    invisible when you are looking at rows that happen to have an IP.
    """
    macs = ctx.latest.get("macs") or []
    if not macs:
        return
    with_ip = sum(1 for m in macs if m.get("clientIpv4Addr"))
    pct = 100 * with_ip / len(macs)
    if pct >= 95:
        return

    missing = len(macs) - with_ip
    named = sum(1 for m in macs if m.get("clientName"))
    yield Finding(
        "ip-binding-coverage",
        f"{missing} of {len(macs)} MAC entries have no IP address ({pct:.0f}% bound)",
        "warning" if pct < 60 else "info", CAT_MAC, "fabric",
        f"{with_ip} of {len(macs)} learned MAC entries carry an IPv4 address "
        f"({pct:.1f}%); {missing} have none. RUCKUS ONE has no switch ARP endpoint — "
        "`clientIpv4Addr` on the clients query is the only MAC-to-IP binding it offers, "
        "so this percentage is the ceiling on how much of the forwarding table can be "
        "traced to a host by address. Entries without one can still be traced by port "
        "and VLAN, which is what the loop checks use, so this limits investigation "
        "rather than detection.",
        {"entriesWithIp": with_ip, "entriesWithoutIp": missing,
         "totalEntries": len(macs), "percentBound": round(pct, 1),
         "entriesWithClientName": named,
         "arpEndpointAvailable": False},
        "For a MAC with no IP, pivot on switch port and VLAN instead — both are "
        "populated on every row. If you need the address, read it from the DHCP server "
        "or `show arp` on the L3 gateway; it is not obtainable from the R1 switch API.",
        confidence="high",
    )


@check("crawl-incomplete", "Crawl did not collect every row", CAT_META,
       needs="snapshot",
       trigger="the snapshot's completeness record shows a query that collected fewer rows than its totalCount, or one that hit the 10,000-row query window")
def crawl_incomplete(ctx):
    """
    switches.py records every paged query it issues and whether it reached the
    row count R1 declared. A shortfall means part of the estate is absent from
    this snapshot — and absence reads downstream as ports and MACs that do not
    exist, not as data that was never fetched.
    """
    report = ctx.latest.get("completeness") or {}
    if not report:
        return

    shortfalls = report.get("shortfalls") or []
    capped = [c for c in shortfalls if (c.get("expected") or 0) >= ES_WINDOW]
    short = [c for c in shortfalls if c not in capped]
    if not shortfalls:
        return

    expected = report.get("expected") or 0
    collected = report.get("collected") or 0
    missing = max(expected - collected, 0)

    detail = (
        f"{report.get('incomplete')} of {report.get('queries')} paged queries in this "
        f"crawl returned fewer rows than RUCKUS ONE said were available "
        f"({collected} of {expected} rows, {missing} missing). "
    )
    if capped:
        detail += (
            f"{len(capped)} of them hit the 10,000-row query window, which is the API "
            "refusing to page further rather than a count — the crawl needs to be "
            "narrowed by venue so each query stays under the ceiling. "
        )
    detail += (
        "Every check here reasons over these rows, so whatever was not collected reads "
        "as absent from the network rather than as unexamined."
    )

    yield Finding(
        "crawl-incomplete",
        f"Snapshot is missing {missing} row(s) across {len(shortfalls)} query(ies)",
        "warning", CAT_META, "crawl",
        detail,
        {"queries": report.get("queries"), "incompleteQueries": report.get("incomplete"),
         "rowsExpected": expected, "rowsCollected": collected, "rowsMissing": missing,
         "hitQueryWindow": len(capped),
         "shortfalls": [{"path": c.get("path"), "filters": c.get("filters"),
                         "expected": c.get("expected"), "collected": c.get("collected")}
                        for c in shortfalls]},
        "Re-run the crawl scoped to fewer venues at a time. If a single venue alone "
        "exceeds the window, the per-switch fallback in crawl_ports covers ports but "
        "the MAC table has no equivalent — treat MAC results for that venue as partial.",
        confidence="high",
    )
