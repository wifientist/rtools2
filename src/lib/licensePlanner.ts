/**
 * Max-extension planning for MSP license assignments.
 *
 * The question this answers: if I renew a set of end customers today, how far
 * out can I take them? Their current expiration is not the ceiling — it is just
 * where the licenses they happen to hold run out. Any pool capacity that is not
 * committed to *other* customers is available to extend them into.
 *
 * Everything is computed against the pool's capacity step function, because
 * R1's own calculator (/entitlements/availabilityReports/query) returns an
 * empty data array for every valid request shape tried against it.
 */

const DAY = 86_400_000;

export interface PlanBlock {
    quantity: number;
    effective_date: string | null;
    expiration_date: string | null;
}

export interface PlanEc {
    tenant_id: string;
    name: string;
    quantity: number;
    assignments: PlanBlock[];
}

export interface PlanSegment {
    start: string;
    end: string;
    /** Pool capacity in force. */
    capacity: number;
    /** Held by customers NOT in the selection. */
    committedOthers: number;
    /** capacity - committedOthers: what the selection could draw on. */
    available: number;
    /** Whether `available` covers the requested quantity here. */
    covered: boolean;
}

export interface PlanResult {
    /** False when the requested quantity cannot even be met today. */
    feasible: boolean;
    requiredQty: number;
    /** Last date the requirement is continuously met. */
    maxDate: string | null;
    maxDays: number | null;
    /** Earliest expiration across the selection's current assignments. */
    currentDate: string | null;
    currentDays: number | null;
    /** Extra days the selection could gain over its current expiration. */
    gainDays: number | null;
    availableToday: number;
    /** Plain-language reason the answer stops where it does. */
    limitedBy: string | null;
    segments: PlanSegment[];
}

const toT = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
const toIso = (t: number) => new Date(t).toISOString().slice(0, 10);
const AVG_MONTH = 30.437;

/**
 * How demand evolves, as three independent levers.
 *
 *   base      licenses in play today
 *   runRate   new licenses won per month (absolute — an MSP can estimate
 *             "about N a month" far more reliably than a percentage)
 *   churn     percent of licenses lost per month (a rate, because losses
 *             scale with how much you hold)
 *
 * Modelled as a leaky bucket applied monthly:
 *
 *   n(m) = n(m-1) * (1 - c) + runRate
 *
 * so churn bites on new business too, not just the starting base — otherwise
 * a high run rate quietly outruns the churn assumption. Solved in closed form
 * below. With churn on, demand converges on runRate / c rather than growing
 * without bound, which is the steady state the business actually tends to.
 */
export interface DemandModel {
    base: number;
    runRatePerMonth: number;
    churnPctPerMonth: number;
}

export const FLAT_DEMAND = (base: number): DemandModel => ({
    base,
    runRatePerMonth: 0,
    churnPctPerMonth: 0,
});

export function monthsBetween(asOf: string, iso: string): number {
    return Math.max((toT(iso) - toT(asOf)) / (AVG_MONTH * DAY), 0);
}

export function demandAt(model: DemandModel, asOf: string, iso: string): number {
    const { base, runRatePerMonth: r, churnPctPerMonth } = model;
    const c = Math.min(Math.max(churnPctPerMonth, 0), 100) / 100;
    const m = monthsBetween(asOf, iso);
    if (!r && !c) return base;
    if (c === 0) return Math.round(base + r * m);
    const decay = Math.pow(1 - c, m);
    return Math.max(0, Math.round(base * decay + r * ((1 - decay) / c)));
}

/** Where demand settles once churn and new business balance out. */
export function steadyState(model: DemandModel): number | null {
    const c = model.churnPctPerMonth / 100;
    if (c <= 0) return null;
    return Math.round(model.runRatePerMonth / c);
}

/**
 * The same model rebased onto a subset of the estate.
 *
 * Churn is a rate so it carries over untouched, but the run rate is an
 * estate-wide absolute — attributing all of it to a handful of customers
 * would overstate their growth, so it is split by their share of the base.
 */
export function scaleModel(model: DemandModel, base: number): DemandModel {
    const share = model.base > 0 ? base / model.base : 1;
    return {
        base,
        runRatePerMonth: model.runRatePerMonth * share,
        churnPctPerMonth: model.churnPctPerMonth,
    };
}

function sumInForce(blocks: PlanBlock[], t: number): number {
    let total = 0;
    for (const b of blocks) {
        if (!b.effective_date || !b.expiration_date) continue;
        if (toT(b.effective_date) <= t && t <= toT(b.expiration_date)) {
            total += b.quantity;
        }
    }
    return total;
}

/**
 * Build the availability step function for a selection, from today forward.
 *
 * Selected customers' own assignments are excluded from `committedOthers` —
 * renewing them releases what they currently hold, which is exactly why a
 * selection can often extend past its present expiration date.
 */
export function availabilitySegments(
    pool: PlanBlock[],
    ecs: PlanEc[],
    selectedIds: Set<string>,
    asOf: string,
    requiredQty: number,
    demand?: DemandModel,
): PlanSegment[] {
    // The selection needs its own quantity, grown/churned at the estate's
    // rates rebased onto that quantity.
    const model = demand ? scaleModel(demand, requiredQty) : FLAT_DEMAND(requiredQty);
    const today = toT(asOf);

    const livePool = pool.filter(
        (b) => b.effective_date && b.expiration_date && toT(b.expiration_date) >= today,
    );
    const others: PlanBlock[] = [];
    for (const ec of ecs) {
        if (selectedIds.has(ec.tenant_id)) continue;
        for (const a of ec.assignments) {
            if (a.effective_date && a.expiration_date && toT(a.expiration_date) >= today) {
                others.push(a);
            }
        }
    }

    const points = new Set<number>([today]);
    for (const b of [...livePool, ...others]) {
        const eff = toT(b.effective_date!);
        if (eff > today) points.add(eff);
        points.add(toT(b.expiration_date!) + DAY);
    }
    const sorted = [...points].sort((a, b) => a - b);

    const segments: PlanSegment[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
        const start = sorted[i];
        const capacity = sumInForce(livePool, start);
        const committedOthers = sumInForce(others, start);
        const available = capacity - committedOthers;
        segments.push({
            start: toIso(start),
            end: toIso(sorted[i + 1] - DAY),
            capacity,
            committedOthers,
            available,
            covered:
                available >= demandAt(model, asOf, toIso(start)) && requiredQty > 0,
        });
    }
    return segments;
}

/**
 * How far a selection could be extended.
 *
 * Coverage must be continuous — the walk stops at the first segment that
 * cannot carry the full quantity, since a gap mid-term is not a renewal.
 */
export function planMaxPeriod(
    pool: PlanBlock[],
    ecs: PlanEc[],
    selectedIds: Set<string>,
    asOf: string,
    requiredQty: number,
    demand?: DemandModel,
): PlanResult {
    const today = toT(asOf);
    const model = demand ? scaleModel(demand, requiredQty) : FLAT_DEMAND(requiredQty);
    const segments = availabilitySegments(
        pool, ecs, selectedIds, asOf, requiredQty, demand,
    );
    const availableToday = segments[0]?.available ?? 0;

    const selected = ecs.filter((e) => selectedIds.has(e.tenant_id));
    const currentExpirations = selected
        .flatMap((e) => e.assignments)
        .map((a) => a.expiration_date)
        .filter((d): d is string => !!d)
        .sort();
    const currentDate = currentExpirations[0] ?? null;
    const currentDays = currentDate
        ? Math.round((toT(currentDate) - today) / DAY)
        : null;

    const base: PlanResult = {
        feasible: false,
        requiredQty,
        maxDate: null,
        maxDays: null,
        currentDate,
        currentDays,
        gainDays: null,
        availableToday,
        limitedBy: null,
        segments,
    };

    if (requiredQty <= 0) {
        return { ...base, limitedBy: "No quantity requested." };
    }
    if (!segments.length || availableToday < requiredQty) {
        return {
            ...base,
            limitedBy: `Only ${availableToday.toLocaleString()} licenses are free today — ${(
                requiredQty - availableToday
            ).toLocaleString()} short.`,
        };
    }

    let lastCovered: PlanSegment | null = null;
    let blocker: PlanSegment | null = null;
    for (const seg of segments) {
        if (seg.covered) {
            lastCovered = seg;
        } else {
            blocker = seg;
            break;
        }
    }

    const maxDate = lastCovered?.end ?? null;
    const maxDays = maxDate ? Math.round((toT(maxDate) - today) / DAY) : null;

    let limitedBy: string;
    if (!blocker) {
        limitedBy = "The pool runs out — nothing is licensed beyond this date.";
    } else if (blocker.available <= 0) {
        limitedBy = "No pool capacity remains after this date.";
    } else {
        // With growth on, the requirement at the blocking date is higher than
        // the base — quote the number that actually failed.
        const needThere = demandAt(model, asOf, blocker.start);
        const moved = needThere !== requiredQty;
        limitedBy =
            `Capacity drops to ${blocker.available.toLocaleString()} available ` +
            `on ${blocker.start}, short of the ${needThere.toLocaleString()} needed` +
            (moved ? " by then under your demand model." : ".");
    }

    return {
        ...base,
        feasible: true,
        maxDate,
        maxDays,
        gainDays: maxDays !== null && currentDays !== null ? maxDays - currentDays : null,
        limitedBy,
    };
}

/** Convenience: what one customer could reach on its own, at its current size. */
export function planForSingleEc(
    pool: PlanBlock[],
    ecs: PlanEc[],
    ec: PlanEc,
    asOf: string,
): PlanResult {
    return planMaxPeriod(pool, ecs, new Set([ec.tenant_id]), asOf, ec.quantity);
}

// ---------------------------------------------------------------------------
// Earliest start — the inverse question
// ---------------------------------------------------------------------------

export interface StartWindow {
    /** First date the full quantity becomes available. */
    startDate: string;
    startDays: number;
    /** How far it could then run, from that start. */
    throughDate: string;
    /** Null when the requirement is already met today. */
    waitDays: number;
}

/**
 * When a quantity is not available today, the useful answer is not "no" but
 * "not yet" — capacity frees up as other customers' assignments lapse.
 * Returns the first window that can carry the full quantity, or null if the
 * pool never has room for it.
 */
export function planEarliestStart(
    pool: PlanBlock[],
    ecs: PlanEc[],
    selectedIds: Set<string>,
    asOf: string,
    requiredQty: number,
    demand?: DemandModel,
): StartWindow | null {
    if (requiredQty <= 0) return null;
    const segments = availabilitySegments(
        pool, ecs, selectedIds, asOf, requiredQty, demand,
    );
    const today = toT(asOf);
    const model = demand ? scaleModel(demand, requiredQty) : FLAT_DEMAND(requiredQty);
    const need = (s: PlanSegment) => demandAt(model, asOf, s.start);

    const firstIdx = segments.findIndex((s) => s.available >= need(s));
    if (firstIdx === -1) return null;

    let lastIdx = firstIdx;
    while (
        lastIdx + 1 < segments.length &&
        segments[lastIdx + 1].available >= need(segments[lastIdx + 1])
    ) {
        lastIdx++;
    }

    const startDate = segments[firstIdx].start;
    return {
        startDate,
        startDays: Math.round((toT(startDate) - today) / DAY),
        throughDate: segments[lastIdx].end,
        waitDays: Math.round((toT(startDate) - today) / DAY),
    };
}

// ---------------------------------------------------------------------------
// License-days — how much of what you own is actually committed
// ---------------------------------------------------------------------------

export interface Utilization {
    /** Licenses x days of remaining term across the pool. */
    owned: number;
    /** The same, across what customers actually hold. */
    committed: number;
    idle: number;
    pct: number;
}

export function licenseDays(
    pool: PlanBlock[],
    ecs: PlanEc[],
    asOf: string,
): Utilization {
    const today = toT(asOf);
    const span = (b: PlanBlock) =>
        b.expiration_date ? Math.max((toT(b.expiration_date) - today) / DAY, 0) : 0;

    const owned = pool.reduce((s, b) => s + b.quantity * span(b), 0);
    const committed = ecs.reduce(
        (s, e) => s + e.assignments.reduce((t, a) => t + a.quantity * span(a), 0),
        0,
    );
    return {
        owned: Math.round(owned),
        committed: Math.round(committed),
        idle: Math.round(owned - committed),
        pct: owned > 0 ? (committed / owned) * 100 : 0,
    };
}

// ---------------------------------------------------------------------------
// Optimal allocation — which customer belongs on which block
// ---------------------------------------------------------------------------

export interface AllocationPortion {
    quantity: number;
    expiration: string;
    sku: string | null;
}

export interface AllocationRow {
    tenant_id: string;
    name: string;
    quantity: number;
    currentExpiration: string | null;
    plannedExpiration: string | null;
    gainDays: number | null;
    portions: AllocationPortion[];
    /** True when the customer had to draw from blocks with different end dates. */
    straddles: boolean;
}

export interface AllocationResult {
    rows: AllocationRow[];
    /** Demand the pool cannot cover at all. */
    unplaced: { name: string; quantity: number }[];
    improved: number;
    plannedLicenseDays: number;
    currentLicenseDays: number;
    ownedLicenseDays: number;
    plannedPct: number;
}

export interface AllocBlock extends PlanBlock {
    sku?: string | null;
    /**
     * When the licences were bought, which can precede the date their term
     * starts by up to the activation window. They provide no capacity in
     * between — the chart shades that stretch so the wait is visible.
     */
    purchase_date?: string | null;
}

/**
 * Greedy smallest-customer-first packing into the longest-dated blocks.
 *
 * Total license-days is maximised by filling long blocks to capacity no matter
 * who fills them, so that alone gives a degenerate answer. Packing the smallest
 * customers first instead maximises the NUMBER of customers who reach the far
 * date — which is the decision actually being made, since a customer too large
 * for the long block was never going to reach it anyway.
 */
export function optimalAllocation(
    pool: AllocBlock[],
    ecs: PlanEc[],
    asOf: string,
): AllocationResult {
    const today = toT(asOf);

    const blocks = pool
        .filter((b) => b.expiration_date && toT(b.expiration_date) >= today)
        .map((b) => ({
            expiration: b.expiration_date!,
            sku: b.sku ?? null,
            remaining: b.quantity,
        }))
        .sort((a, b) => toT(b.expiration) - toT(a.expiration));

    const demand = ecs
        .filter((e) => e.quantity > 0)
        .sort((a, b) => a.quantity - b.quantity);

    const rows: AllocationRow[] = [];
    const unplaced: { name: string; quantity: number }[] = [];

    for (const ec of demand) {
        let need = ec.quantity;
        const portions: AllocationPortion[] = [];
        for (const blk of blocks) {
            if (need <= 0) break;
            if (blk.remaining <= 0) continue;
            const take = Math.min(blk.remaining, need);
            blk.remaining -= take;
            need -= take;
            const existing = portions.find((p) => p.expiration === blk.expiration);
            if (existing) existing.quantity += take;
            else portions.push({ quantity: take, expiration: blk.expiration, sku: blk.sku });
        }
        if (need > 0) unplaced.push({ name: ec.name, quantity: need });

        const currentExpiration =
            ec.assignments
                .map((a) => a.expiration_date)
                .filter((d): d is string => !!d)
                .sort()[0] ?? null;
        // A customer is only covered through the earliest of its portions —
        // the rest lapse under it.
        const plannedExpiration =
            portions.length && !need
                ? portions.map((p) => p.expiration).sort()[0]
                : null;

        rows.push({
            tenant_id: ec.tenant_id,
            name: ec.name,
            quantity: ec.quantity,
            currentExpiration,
            plannedExpiration,
            gainDays:
                plannedExpiration && currentExpiration
                    ? Math.round((toT(plannedExpiration) - toT(currentExpiration)) / DAY)
                    : null,
            portions,
            straddles: new Set(portions.map((p) => p.expiration)).size > 1,
        });
    }

    const daysOut = (iso: string) => Math.max((toT(iso) - today) / DAY, 0);
    const plannedLicenseDays = Math.round(
        rows.reduce(
            (s, r) => s + r.portions.reduce((t, p) => t + p.quantity * daysOut(p.expiration), 0),
            0,
        ),
    );
    const util = licenseDays(pool, ecs, asOf);

    rows.sort((a, b) => (b.gainDays ?? -1) - (a.gainDays ?? -1));

    return {
        rows,
        unplaced,
        improved: rows.filter((r) => (r.gainDays ?? 0) > 0).length,
        plannedLicenseDays,
        currentLicenseDays: util.committed,
        ownedLicenseDays: util.owned,
        plannedPct: util.owned > 0 ? (plannedLicenseDays / util.owned) * 100 : 0,
    };
}

// ---------------------------------------------------------------------------
// Scenario timelines — supply vs demand under a given set of commitments
// ---------------------------------------------------------------------------

export interface ScenarioSegment {
    start: string;
    end: string;
    capacity: number;
    committed: number;
    headroom: number;
}

function scenarioTimeline(
    pool: PlanBlock[],
    commitments: PlanBlock[],
    asOf: string,
): ScenarioSegment[] {
    const today = toT(asOf);
    const livePool = pool.filter(
        (b) => b.expiration_date && toT(b.expiration_date) >= today,
    );

    const points = new Set<number>([today]);
    for (const b of [...livePool, ...commitments]) {
        if (b.effective_date && toT(b.effective_date) > today) {
            points.add(toT(b.effective_date));
        }
        if (b.expiration_date) points.add(toT(b.expiration_date) + DAY);
    }
    const sorted = [...points].sort((a, b) => a - b);

    const out: ScenarioSegment[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
        const start = sorted[i];
        const capacity = sumInForce(livePool, start);
        const committed = sumInForce(commitments, start);
        out.push({
            start: toIso(start),
            end: toIso(sorted[i + 1] - DAY),
            capacity,
            committed,
            headroom: capacity - committed,
        });
    }
    return out;
}

/**
 * The capacity-over-time picture as it would look if the allocation plan were
 * applied — same bands, but every customer now runs to the end of the block
 * they were packed into, so the committed line tracks the pool instead of
 * collapsing under it.
 */
export function allocationTimeline(
    pool: PlanBlock[],
    alloc: AllocationResult,
    asOf: string,
): ScenarioSegment[] {
    const commitments: PlanBlock[] = alloc.rows.flatMap((r) =>
        r.portions.map((p) => ({
            quantity: p.quantity,
            effective_date: asOf,
            expiration_date: p.expiration,
        })),
    );
    return scenarioTimeline(pool, commitments, asOf);
}

// ---------------------------------------------------------------------------
// Hypothetical purchases
// ---------------------------------------------------------------------------

/** Calendar-month arithmetic that clamps rather than rolling over month ends. */
function addMonths(iso: string, months: number): string {
    const d = new Date(toT(iso));
    const day = d.getUTCDate();
    d.setUTCDate(1);
    d.setUTCMonth(d.getUTCMonth() + months);
    const lastDay = new Date(
        Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0),
    ).getUTCDate();
    d.setUTCDate(Math.min(day, lastDay));
    return d.toISOString().slice(0, 10);
}

export type Cadence = "monthly" | "quarterly" | "semiannual" | "annual";

export const CADENCE_MONTHS: Record<Cadence, number> = {
    monthly: 1,
    quarterly: 3,
    semiannual: 6,
    annual: 12,
};

export const CADENCE_LABEL: Record<Cadence, string> = {
    monthly: "month",
    quarterly: "quarter",
    semiannual: "half-year",
    annual: "year",
};

/**
 * Days between activating a purchase and the latest date its term may start.
 *
 * Not derivable from R1: /mspEntitlements exposes no activation date, and its
 * effectiveDate is already the post-deferral start (term length checks out to
 * the day). The window lives upstream in ordering/ALM, so it is a constant
 * here. Note graceEndDate in the API is the opposite concept — grace *after*
 * expiry — and must not be confused with this.
 */
export const GRACE_WINDOW_DAYS = 180;

/** Most tranches that fit in the window at a given spacing. */
export function maxTranches(intervalDays: number): number {
    if (intervalDays <= 0) return 1;
    return Math.floor(GRACE_WINDOW_DAYS / intervalDays) + 1;
}

export interface WhatIfPurchase {
    id: number;
    kind: "once" | "recurring" | "staggered";
    /** Licenses per purchase, not per month — see monthlyRate() for the rate. */
    quantity: number;
    termYears: number;
    startDate: string;
    cadence: Cadence;
    /** Number of purchases in the series. Ignored for a one-off. */
    periods: number;
    /** staggered only: how many activations the total is split across. */
    tranches?: number;
    /** staggered only: days between activations. */
    intervalDays?: number;
    /**
     * Days between ordering and the term starting, 0..GRACE_WINDOW_DAYS.
     * For a staggered order the spacing supplies this per tranche instead.
     */
    deferDays?: number;
}

/**
 * Split a total into whole-license tranches that sum back to it exactly.
 * Remainders go to the earliest tranches so the shortfall lands last.
 */
function splitEvenly(total: number, parts: number): number[] {
    const n = Math.max(1, Math.floor(parts));
    const base = Math.floor(total / n);
    const extra = total - base * n;
    return Array.from({ length: n }, (_, i) => base + (i < extra ? 1 : 0));
}

/** A single hypothetical purchase, shaped to concatenate onto the pool. */
export function hypotheticalBlock(
    quantity: number,
    termYears: number,
    startDate: string,
    purchaseDate?: string,
): AllocBlock {
    return {
        quantity,
        effective_date: startDate,
        expiration_date: addMonths(startDate, Math.round(termYears * 12)),
        sku: `WHAT-IF-${termYears}YR`,
        purchase_date: purchaseDate ?? startDate,
    };
}

/** Deferral clamped to the activation window. */
export function deferOf(p: WhatIfPurchase): number {
    return Math.min(Math.max(p.deferDays ?? 0, 0), GRACE_WINDOW_DAYS);
}

const shiftDays = (iso: string, days: number) => toIso(toT(iso) + days * DAY);

/**
 * Expand a purchase into the pool blocks it would create.
 *
 * A rolling purchase is modelled as independent blocks rather than one growing
 * block, because each tranche carries its own term and therefore its own
 * expiration — which is the whole reason staggered buying behaves differently
 * from one large one.
 */
export function expandPurchase(p: WhatIfPurchase): AllocBlock[] {
    const defer = deferOf(p);
    if (p.kind === "once") {
        return [
            hypotheticalBlock(
                p.quantity, p.termYears, shiftDays(p.startDate, defer), p.startDate,
            ),
        ];
    }
    if (p.kind === "staggered") {
        const interval = Math.max(1, p.intervalDays ?? 30);
        // Hard-capped: a tranche cannot start after the activation window
        // closes, so clamp defensively even if the UI let something through.
        const count = Math.min(
            Math.max(1, Math.floor(p.tranches ?? 1)),
            maxTranches(interval),
        );
        return splitEvenly(p.quantity, count)
            .map((qty, i) =>
                // One order, several activations — the spacing IS the
                // purchase-to-start deferral here.
                hypotheticalBlock(
                    qty,
                    p.termYears,
                    toIso(toT(p.startDate) + i * interval * DAY),
                    p.startDate,
                ),
            )
            .filter((b) => b.quantity > 0);
    }
    const step = CADENCE_MONTHS[p.cadence];
    const out: AllocBlock[] = [];
    for (let i = 0; i < Math.max(p.periods, 0); i++) {
        // Each order in the series carries the same deferral.
        const ordered = addMonths(p.startDate, i * step);
        out.push(
            hypotheticalBlock(
                p.quantity, p.termYears, shiftDays(ordered, defer), ordered,
            ),
        );
    }
    return out;
}

/** Licenses per month a purchase represents, for comparison against growth. */
export function monthlyRate(p: WhatIfPurchase): number {
    if (p.kind === "recurring") return p.quantity / CADENCE_MONTHS[p.cadence];
    return 0;
}

/** Total licenses a purchase adds across its whole series. */
export function totalLicenses(p: WhatIfPurchase): number {
    if (p.kind === "recurring") return p.quantity * Math.max(p.periods, 0);
    // one-off and staggered both spend exactly their stated total.
    return p.quantity;
}

/** Last date any tranche of a purchase is still in force. */
export function purchaseEnds(p: WhatIfPurchase): string {
    const blocks = expandPurchase(p);
    return blocks.length
        ? blocks[blocks.length - 1].expiration_date!
        : p.startDate;
}

// ---------------------------------------------------------------------------
// Client-side pool derivation
// ---------------------------------------------------------------------------

/*
 * The backend ships a timeline, cliffs and a summary for the whole pool. Once
 * blocks can be deselected — to model a chunk not being renewed — those become
 * stale, so the same maths is mirrored here and every view is driven from the
 * selected subset instead. With everything selected these reproduce the
 * server's numbers exactly.
 */

export interface PoolTimelineSegment {
    start: string;
    end: string;
    capacity: number;
    days: number;
}

export interface PoolCliff {
    date: string;
    days_out: number;
    quantity_lost: number;
    capacity_after: number;
    skus: string[];
}

export interface PoolSummary {
    capacity_today: number;
    effective_expiration: string | null;
    days_to_effective_expiration: number | null;
    capacity_after_first_cliff: number | null;
    last_expiration: string | null;
    days_to_last_expiration: number | null;
    tail_days: number;
    cliff_count: number;
    purchased: number;
    block_count: number;
}

function livePoolOf<T extends PlanBlock>(pool: T[], asOf: string): T[] {
    const today = toT(asOf);
    return pool.filter(
        (b) => b.effective_date && b.expiration_date && toT(b.expiration_date) >= today,
    );
}

function breakpoints(pool: PlanBlock[], asOf: string): number[] {
    const today = toT(asOf);
    const pts = new Set<number>([today]);
    for (const b of pool) {
        if (toT(b.effective_date!) > today) pts.add(toT(b.effective_date!));
        pts.add(toT(b.expiration_date!) + DAY);
    }
    return [...pts].sort((a, b) => a - b);
}

export function poolTimeline(pool: PlanBlock[], asOf: string): PoolTimelineSegment[] {
    const live = livePoolOf(pool, asOf);
    if (!live.length) return [];
    const pts = breakpoints(live, asOf);
    const out: PoolTimelineSegment[] = [];
    for (let i = 0; i < pts.length - 1; i++) {
        out.push({
            start: toIso(pts[i]),
            end: toIso(pts[i + 1] - DAY),
            capacity: sumInForce(live, pts[i]),
            days: Math.round((pts[i + 1] - pts[i]) / DAY),
        });
    }
    return out;
}

export function poolCliffs(pool: AllocBlock[], asOf: string): PoolCliff[] {
    const today = toT(asOf);
    const live = livePoolOf(pool, asOf);
    if (!live.length) return [];
    const dates = [...new Set(live.map((b) => b.expiration_date!))].sort();
    return dates.map((date) => {
        const at = toT(date);
        return {
            date,
            days_out: Math.round((at - today) / DAY),
            quantity_lost: live
                .filter((b) => b.expiration_date === date && toT(b.effective_date!) <= at)
                .reduce((s, b) => s + b.quantity, 0),
            capacity_after: sumInForce(live, at + DAY),
            skus: [
                ...new Set(
                    live
                        .filter((b) => b.expiration_date === date && b.sku)
                        .map((b) => b.sku as string),
                ),
            ].sort(),
        };
    });
}

export function poolSummary(pool: PlanBlock[], asOf: string): PoolSummary {
    const today = toT(asOf);
    const live = livePoolOf(pool, asOf);
    const base: PoolSummary = {
        capacity_today: 0,
        effective_expiration: null,
        days_to_effective_expiration: null,
        capacity_after_first_cliff: null,
        last_expiration: null,
        days_to_last_expiration: null,
        tail_days: 0,
        cliff_count: 0,
        purchased: pool.reduce((s, b) => s + b.quantity, 0),
        block_count: pool.length,
    };
    if (!live.length) return base;

    const cliffs = poolCliffs(live as AllocBlock[], asOf);
    const first = cliffs[0] ?? null;
    const lastExp = live
        .map((b) => b.expiration_date!)
        .sort()
        .slice(-1)[0];

    return {
        ...base,
        capacity_today: sumInForce(live, today),
        effective_expiration: first?.date ?? null,
        days_to_effective_expiration: first?.days_out ?? null,
        capacity_after_first_cliff: first?.capacity_after ?? null,
        last_expiration: lastExp,
        days_to_last_expiration: Math.round((toT(lastExp) - today) / DAY),
        tail_days: first ? Math.round((toT(lastExp) - toT(first.date)) / DAY) : 0,
        cliff_count: cliffs.length,
    };
}

/** Pool capacity against what every customer holds, on shared breakpoints. */
export function commitmentTimeline(
    pool: PlanBlock[],
    ecs: PlanEc[],
    asOf: string,
): ScenarioSegment[] {
    const today = toT(asOf);
    const commitments = ecs
        .flatMap((e) => e.assignments)
        .filter(
            (a) => a.effective_date && a.expiration_date && toT(a.expiration_date) >= today,
        );
    return scenarioTimeline(pool, commitments, asOf);
}

// ---------------------------------------------------------------------------
// Required purchases — what it takes to actually meet the demand curve
// ---------------------------------------------------------------------------

/*
 * The demand line says how many licenses must be *held* at a given moment. On
 * its own that is only half a plan: existing licenses expire, so meeting it
 * means buying — and anything bought expires in its turn, becoming a renewal
 * later in the same window. Modelling demand without that feedback understates
 * what the estate costs to keep running.
 *
 * Walked forward at a purchase cadence. At each ordering point we look ahead to
 * the next one and buy enough to cover the WORST moment in between — peak
 * demand against trough capacity — so coverage never dips between orders. Each
 * purchase is added to the running pool, which is what makes its own future
 * expiry show up as another order further down the list.
 */

export interface RequiredPurchase {
    /** When the order is placed. */
    purchaseDate: string;
    /** When this chunk's term begins — up to the window later. */
    startDate: string;
    deferDays: number;
    quantity: number;
    termYears: number;
    expires: string;
    /** Demand at the moment this chunk switches on. */
    demandThen: number;
    /** Capacity at that moment, before this chunk. */
    capacityThen: number;
    /** Part of it that merely replaces licences lapsing then. */
    replacing: number;
    forGrowth: number;
}

export interface PurchasePlan {
    purchases: RequiredPurchase[];
    totalLicenses: number;
    totalReplacing: number;
    totalForGrowth: number;
    horizon: string;
    alreadyCovered: boolean;
    /**
     * Licence-days of capacity carried above the demand line. This is the cost
     * of buying early, and the number the activation schedule minimises.
     */
    idleLicenseDays: number;
}

/**
 * Orders needed to hold the demand curve, activated as late as the window
 * allows.
 *
 * Buying and switching on at the same moment is the expensive way to do it:
 * an order sized for a whole ordering period sits above the demand line for
 * most of that period, and every day it does is capacity paid for and unused.
 *
 * Since a licence may start up to GRACE_WINDOW_DAYS after purchase, one order
 * can be broken into chunks that switch on as the demand line reaches them, so
 * capacity climbs in steps just under demand rather than in one jump above it.
 *
 * The float does not stretch forever, so a chunk activating at the end of the
 * window has to carry through to the next order's first possible activation —
 * that stretch is unreachable by either order and is sized accordingly.
 */
export function requiredPurchases(
    pool: AllocBlock[],
    demand: DemandModel,
    asOf: string,
    horizon: string,
    termYears: number,
    cadence: Cadence,
    staggerDays = 60,
): PurchasePlan {
    const step = CADENCE_MONTHS[cadence];
    const endT = toT(horizon);
    const empty: PurchasePlan = {
        purchases: [], totalLicenses: 0, totalReplacing: 0, totalForGrowth: 0,
        horizon, alreadyCovered: true, idleLicenseDays: 0,
    };
    if (endT <= toT(asOf)) return empty;

    const orderPoints: string[] = [];
    for (let i = 0; ; i++) {
        const d = addMonths(asOf, i * step);
        if (toT(d) > endT) break;
        orderPoints.push(d);
        if (i > 600) break;
    }

    const bought: AllocBlock[] = [];
    const purchases: RequiredPurchase[] = [];
    const capAt = (t: number) => sumInForce(pool, t) + sumInForce(bought, t);

    const sampleBetween = (fromT: number, toTime: number): number[] => {
        const out = [fromT];
        for (let d = fromT + 15 * DAY; d < toTime; d += 15 * DAY) out.push(d);
        if (toTime - DAY > fromT) out.push(toTime - DAY);
        return out;
    };

    /*
     * Activation dates, which are NOT simply the cadence grid.
     *
     * Capacity has to arrive when it is needed, and there are two reasons for
     * that: demand climbing (handled by staggering inside each order's window)
     * and existing licences lapsing. A cliff falling between one order's window
     * closing and the next order being placed would otherwise force a chunk to
     * switch on months early and sit above demand until the cliff arrived —
     * on a large pool that is tens of thousands of idle licences.
     */
    const cliffDates = new Set<number>();
    for (const b of pool) {
        if (!b.expiration_date) continue;
        const drop = toT(b.expiration_date) + DAY;
        if (drop > toT(asOf) && drop <= endT) cliffDates.add(drop);
    }

    const activations = new Set<number>();
    for (let i = 0; i < orderPoints.length; i++) {
        const P = toT(orderPoints[i]);
        const nextT = i + 1 < orderPoints.length ? toT(orderPoints[i + 1]) : endT + DAY;
        for (let d = 0; d <= GRACE_WINDOW_DAYS; d += Math.max(1, staggerDays)) {
            const a = P + d * DAY;
            if (a >= nextT) break;
            activations.add(a);
        }
        activations.add(P);
    }
    for (const c of cliffDates) activations.add(c);
    // Seeded points can collide too — a pool cliff falling a day off a cadence
    // point would otherwise produce the same fragmentation.
    const seeded = [...activations].filter((a) => a <= endT).sort((a, b) => a - b);
    const thinned: number[] = [];
    for (const a of seeded) {
        if (!thinned.length || a - thinned[thinned.length - 1] > 7 * DAY) thinned.push(a);
    }
    activations.clear();
    for (const a of thinned) activations.add(a);
    // Mutable: each purchase creates a cliff of its own when its term ends, and
    // that needs an activation too. With short terms most cliffs in the plan are
    // self-inflicted, so seeding this list from the existing pool alone leaves
    // them uncovered until some later chunk switches on early to compensate.
    const acts = [...activations].filter((a) => a <= endT).sort((a, b) => a - b);
    const known = new Set(acts);
    /*
     * Anything landing within a few days of an existing activation is folded
     * onto it rather than becoming its own line item. Short terms make chunks
     * expire a day either side of a scheduled order, and without this the plan
     * fragments into pairs of activations a day apart — one large, one for
     * twenty-odd licences. Folding backwards is always safe for coverage: it
     * only ever switches capacity on slightly early.
     */
    const COALESCE_DAYS = 7;
    const addActivation = (t: number) => {
        if (t <= toT(asOf) || t > endT || known.has(t)) return;
        if (acts.some((x) => Math.abs(x - t) <= COALESCE_DAYS * DAY)) return;
        known.add(t);
        const at = acts.findIndex((x) => x > t);
        if (at === -1) acts.push(t);
        else acts.splice(at, 0, t);
    };

    /** Latest order that could still supply an activation, honouring the window. */
    const orderFor = (a: number): string => {
        const eligible = orderPoints.filter(
            (op) => toT(op) <= a && a - toT(op) <= GRACE_WINDOW_DAYS * DAY,
        );
        // No scheduled order reaches this date — it needs one off-cycle, placed
        // as late as the window allows.
        return eligible.length
            ? eligible[eligible.length - 1]
            : toIso(Math.max(toT(asOf), a - GRACE_WINDOW_DAYS * DAY));
    };

    for (let j = 0; j < acts.length && j < 2000; j++) {
        const a = acts[j];
        const until = j + 1 < acts.length ? acts[j + 1] : endT + DAY;
        const samples = sampleBetween(a, until);

        let peakNeed = 0;
        let troughCap = Infinity;
        for (const t of samples) {
            peakNeed = Math.max(peakNeed, demandAt(demand, asOf, toIso(t)));
            troughCap = Math.min(troughCap, capAt(t));
        }
        if (!Number.isFinite(troughCap)) troughCap = 0;
        if (peakNeed <= troughCap) continue;

        const qty = Math.ceil(peakNeed - troughCap);
        const startIso = toIso(a);
        const P = orderFor(a);
        const block = hypotheticalBlock(qty, termYears, startIso, P);
        bought.push(block);
        addActivation(toT(block.expiration_date!) + DAY);

        const capBefore = capAt(a) - qty;
        const lapsingInWindow = Math.max(0, capBefore - troughCap);
        const replacing = Math.min(qty, lapsingInWindow);
        purchases.push({
            purchaseDate: P,
            startDate: startIso,
            deferDays: Math.round((a - toT(P)) / DAY),
            quantity: qty,
            termYears,
            expires: block.expiration_date!,
            demandThen: demandAt(demand, asOf, startIso),
            capacityThen: capBefore,
            replacing,
            forGrowth: qty - replacing,
        });
    }

    // Score the result: capacity carried above demand, in licence-days.
    let idle = 0;
    for (let t = toT(asOf); t <= endT; t += 15 * DAY) {
        const over = capAt(t) - demandAt(demand, asOf, toIso(t));
        if (over > 0) idle += over * 15;
    }

    return {
        purchases,
        totalLicenses: purchases.reduce((s, p) => s + p.quantity, 0),
        totalReplacing: purchases.reduce((s, p) => s + p.replacing, 0),
        totalForGrowth: purchases.reduce((s, p) => s + p.forGrowth, 0),
        horizon,
        alreadyCovered: purchases.length === 0,
        idleLicenseDays: Math.round(idle),
    };
}

