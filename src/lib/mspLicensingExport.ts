/**
 * Flattens everything the MSP Licensing page is showing into plain tabular
 * data, one sheet per table.
 *
 * Built from the page's *current* state — exclusions, demand levers, what-if
 * purchases and the customer selection all apply — so the export always matches
 * what is on screen rather than the raw server payload. Dates stay as ISO text
 * so they survive any spreadsheet's locale handling.
 */
import type { Sheet, Cell } from "./xlsx";
import {
    demandAt, steadyState, purchaseEnds, totalLicenses, CADENCE_LABEL,
    type DemandModel, type WhatIfPurchase, type PlanResult, type AllocationResult,
} from "./licensePlanner";

interface ExportInput {
    data: any;
    view: any;
    actualPool: any[];
    selectedPool: any[];
    excluded: Set<string>;
    blockKey: (b: any) => string;
    demandModel: DemandModel;
    whatIf: WhatIfPurchase[];
    plan: PlanResult | null;
    selectedEcIds: Set<string>;
    requiredQty: number;
    alloc: AllocationResult | null;
    maxAlone: Map<string, PlanResult>;
    util: { owned: number; committed: number; idle: number; pct: number } | null;
}

const yn = (b: boolean) => (b ? "yes" : "no");

export function buildExportSheets(i: ExportInput): Sheet[] {
    const { data, view, demandModel: dm, asOf = data.as_of } = { ...i, asOf: i.data.as_of } as any;
    const sheets: Sheet[] = [];
    const horizon = view.summary.last_expiration ?? asOf;

    // --- Summary ---------------------------------------------------------
    const summary: Cell[][] = [
        ["Metric", "Value"],
        ["Generated (UTC)", new Date().toISOString().slice(0, 19).replace("T", " ")],
        ["Data as of", asOf],
        ["Controller", data.compliance?.tenant_name ?? ""],
        [],
        ["Pool capacity today", view.summary.capacity_today],
        ["Blocks included", `${i.selectedPool.length} of ${i.actualPool.length}`],
        ["Effective expiration", view.summary.effective_expiration ?? ""],
        ["Days to effective expiration", view.summary.days_to_effective_expiration ?? ""],
        ["Capacity after first cliff", view.summary.capacity_after_first_cliff ?? ""],
        ["Last expiration", view.summary.last_expiration ?? ""],
        ["Tail beyond first cliff (days)", view.summary.tail_days],
        ["Number of cliffs", view.summary.cliff_count],
        [],
        ["Licenses assigned to ECs", data.assigned_total ?? 0],
        ["License-days owned", i.util?.owned ?? ""],
        ["License-days committed", i.util?.committed ?? ""],
        ["License-days idle", i.util?.idle ?? ""],
        ["Utilization %", i.util ? Number(i.util.pct.toFixed(1)) : ""],
        [],
        ["MSP-wide licenses used", data.compliance?.used ?? ""],
        ["MSP-wide headroom", data.compliance?.available ?? ""],
    ];
    if (view.idleTail) {
        summary.push([], ["Idle tail from", view.idleTail.from],
            ["Idle tail until", view.idleTail.until],
            ["Idle tail licenses", view.idleTail.quantity]);
    }
    sheets.push({ name: "Summary", rows: summary });

    // --- Scenario --------------------------------------------------------
    const scenario: Cell[][] = [
        ["Demand model", ""],
        ["Base licenses", dm.base],
        ["Run rate (new/month)", dm.runRatePerMonth],
        ["Churn (%/month)", dm.churnPctPerMonth],
        ["Projected demand at " + horizon, demandAt(dm, asOf, horizon)],
        ["Steady state", steadyState(dm) ?? "n/a (no churn)"],
        [],
        ["Excluded license blocks", i.excluded.size],
    ];
    for (const b of i.actualPool.filter((b) => i.excluded.has(i.blockKey(b)))) {
        scenario.push(["  excluded", `${b.sku} · ${b.quantity} · expires ${b.expiration_date}`]);
    }
    scenario.push([], ["Hypothetical purchases", i.whatIf.length]);
    scenario.push(["Kind", "Quantity", "Term (yr)", "Start", "Detail", "Total licenses", "Last expiry"]);
    for (const w of i.whatIf) {
        scenario.push([
            w.kind, w.quantity, w.termYears, w.startDate,
            w.kind === "recurring"
                ? `every ${CADENCE_LABEL[w.cadence]} x ${w.periods}`
                : w.kind === "staggered"
                ? `${w.tranches} tranches, ${w.intervalDays}d apart`
                : "one-off",
            totalLicenses(w), purchaseEnds(w),
        ]);
    }
    sheets.push({ name: "Scenario", rows: scenario });

    // --- License pool ----------------------------------------------------
    sheets.push({
        name: "License pool",
        rows: [
            ["Included", "SKU", "Quantity", "Term (years)", "Effective", "Expires",
             "Days remaining", "Trial", "Status", "Device type"],
            ...i.actualPool.map((b) => [
                yn(!i.excluded.has(i.blockKey(b))), b.sku, b.quantity, b.term_years,
                b.effective_date, b.expiration_date, b.days_remaining,
                yn(!!b.is_trial), b.status, b.device_type,
            ] as Cell[]),
        ],
    });

    // --- Capacity timeline ----------------------------------------------
    sheets.push({
        name: "Capacity timeline",
        rows: [
            ["From", "To", "Pool capacity", "Committed to ECs", "Unassigned",
             "Estate demand", "Deficit"],
            ...view.combined.map((s: any) => {
                const need = demandAt(dm, asOf, s.start);
                return [s.start, s.end, s.capacity, s.committed, s.headroom,
                        need, Math.max(0, need - s.capacity)] as Cell[];
            }),
        ],
    });

    // --- Cliffs ----------------------------------------------------------
    sheets.push({
        name: "Cliffs",
        rows: [
            ["Date", "Days out", "Pool lapsing", "Capacity after", "Committed after",
             "Free after", "Coverage ending", "Shortfall to renew", "Customers going dark", "SKUs"],
            ...view.cliffs.map((c: any) => {
                const after = new Date(new Date(c.date + "T00:00:00Z").getTime() + 86400000)
                    .toISOString().slice(0, 10);
                const seg = view.combined.find((s: any) => after >= s.start && after <= s.end);
                const committedAfter = seg?.committed ?? 0;
                const affected = (data.ecs ?? [])
                    .map((e: any) => ({
                        name: e.name,
                        q: (e.assignments ?? [])
                            .filter((a: any) => a.expiration_date === c.date)
                            .reduce((s: number, a: any) => s + a.quantity, 0),
                    }))
                    .filter((e: any) => e.q > 0);
                const lost = affected.reduce((s: number, e: any) => s + e.q, 0);
                const free = c.capacity_after - committedAfter;
                return [
                    c.date, c.days_out, c.quantity_lost, c.capacity_after, committedAfter,
                    free, lost, Math.max(0, lost - free),
                    affected.map((e: any) => `${e.name} (${e.q})`).join("; "),
                    (c.skus ?? []).join("; "),
                ] as Cell[];
            }),
        ],
    });

    // --- End customers ---------------------------------------------------
    sheets.push({
        name: "End customers",
        rows: [
            ["Customer", "Tenant ID", "In extension plan", "Licenses", "Effective expiration",
             "Days remaining", "Tail (days)", "Could reach alone", "Gain (days)",
             "License types", "Active assignments", "Historical assignments"],
            ...(data.ecs ?? []).map((e: any) => {
                const m = i.maxAlone.get(e.tenant_id);
                return [
                    e.name, e.tenant_id, yn(i.selectedEcIds.has(e.tenant_id)), e.quantity,
                    e.effective_expiration ?? "", e.days_to_effective_expiration ?? "",
                    e.tail_days ?? 0, m?.maxDate ?? "", m?.gainDays ?? "",
                    (e.license_types ?? []).join("; "),
                    e.assignment_count, e.historical_count,
                ] as Cell[];
            }),
        ],
    });

    // --- EC assignments (flattened) --------------------------------------
    const assignRows: Cell[][] = [[
        "Customer", "State", "Quantity", "License type", "SKU", "Effective",
        "Expires", "Term (years)", "Status", "Created by", "Revoked",
    ]];
    for (const e of data.ecs ?? []) {
        for (const a of e.assignments ?? []) {
            assignRows.push([e.name, "active", a.quantity, a.license_type, a.sku,
                a.effective_date, a.expiration_date, a.term_years, a.status,
                a.created_by ?? "", a.revoked_date ?? ""]);
        }
        for (const a of e.history ?? []) {
            assignRows.push([e.name, "historical", a.quantity, a.license_type, a.sku,
                a.effective_date, a.expiration_date, a.term_years, a.status,
                a.created_by ?? "", a.revoked_date ?? ""]);
        }
    }
    sheets.push({ name: "EC assignments", rows: assignRows });

    // --- Extension plan --------------------------------------------------
    if (i.plan) {
        const p = i.plan;
        sheets.push({
            name: "Extension plan",
            rows: [
                ["Field", "Value"],
                ["Customers selected", i.selectedEcIds.size],
                ["Licenses needed", i.requiredQty],
                ["Expires today", p.currentDate ?? ""],
                ["Days to current expiry", p.currentDays ?? ""],
                ["Feasible", yn(p.feasible)],
                ["Max extension", p.maxDate ?? ""],
                ["Days to max extension", p.maxDays ?? ""],
                ["Gain (days)", p.gainDays ?? ""],
                ["Available today", p.availableToday],
                ["Limited by", p.limitedBy ?? ""],
                [],
                ["From", "To", "Pool capacity", "Committed (others)", "Available", "Covers requirement"],
                ...p.segments.map((s) => [
                    s.start, s.end, s.capacity, s.committedOthers, s.available, yn(s.covered),
                ] as Cell[]),
            ],
        });
    }

    // --- Optimal allocation ----------------------------------------------
    if (i.alloc) {
        sheets.push({
            name: "Optimal allocation",
            rows: [
                ["License-days owned", i.alloc.ownedLicenseDays],
                ["License-days committed today", i.alloc.currentLicenseDays],
                ["License-days under plan", i.alloc.plannedLicenseDays],
                ["Utilization under plan %", Number(i.alloc.plannedPct.toFixed(1))],
                ["Customers gaining time", i.alloc.improved],
                [],
                ["Customer", "Licenses", "Expires now", "Would expire", "Gain (days)",
                 "Drawn from", "Straddles blocks"],
                ...i.alloc.rows.map((r) => [
                    r.name, r.quantity, r.currentExpiration ?? "", r.plannedExpiration ?? "",
                    r.gainDays ?? "",
                    r.portions.map((x) => `${x.quantity} from ${x.sku ?? "—"} (to ${x.expiration})`).join("; "),
                    yn(r.straddles),
                ] as Cell[]),
                ...(i.alloc.unplaced.length
                    ? [[], ["Unplaced demand", ""],
                       ...i.alloc.unplaced.map((u) => [u.name, u.quantity] as Cell[])]
                    : []),
            ],
        });
    }

    // --- Expirations by quarter ------------------------------------------
    sheets.push({
        name: "Expirations by quarter",
        rows: [
            ["Quarter", "Licenses expiring", "Breakdown by customer"],
            ...(data.quarters ?? []).map((q: any) => [
                q.quarter, q.total,
                (q.by_ec ?? []).map((e: any) => `${e.name} (${e.quantity})`).join("; "),
            ] as Cell[]),
        ],
    });

    // --- Assignment history ----------------------------------------------
    const churnRows: Cell[][] = [["Customer", "Date", "Licenses held"]];
    for (const e of data.ecs ?? []) {
        for (const c of e.churn ?? []) churnRows.push([e.name, c.date, c.quantity]);
    }
    sheets.push({ name: "Assignment history", rows: churnRows });

    return sheets;
}
