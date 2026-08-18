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
 * Requirement at a future date, grown at a flat licences-per-month rate.
 *
 * Deliberately linear — an MSP can estimate "we add about N a month" far more
 * reliably than a compound rate, and a wrong curve shape is worse than a
 * roughly-right straight line.
 */
export function demandAt(
    base: number,
    growthPerMonth: number,
    asOf: string,
    iso: string,
): number {
    if (!growthPerMonth) return base;
    const months = Math.max((toT(iso) - toT(asOf)) / (AVG_MONTH * DAY), 0);
    return Math.round(base + growthPerMonth * months);
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
    growthPerMonth = 0,
): PlanSegment[] {
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
                available >= demandAt(requiredQty, growthPerMonth, asOf, toIso(start)) &&
                requiredQty > 0,
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
    growthPerMonth = 0,
): PlanResult {
    const today = toT(asOf);
    const segments = availabilitySegments(
        pool, ecs, selectedIds, asOf, requiredQty, growthPerMonth,
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
        const needThere = demandAt(requiredQty, growthPerMonth, asOf, blocker.start);
        limitedBy =
            `Capacity drops to ${blocker.available.toLocaleString()} available ` +
            `on ${blocker.start}, short of the ${needThere.toLocaleString()} needed` +
            (growthPerMonth ? ` by then at +${growthPerMonth}/mo.` : ".");
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
    growthPerMonth = 0,
): StartWindow | null {
    if (requiredQty <= 0) return null;
    const segments = availabilitySegments(
        pool, ecs, selectedIds, asOf, requiredQty, growthPerMonth,
    );
    const today = toT(asOf);
    const need = (s: PlanSegment) =>
        demandAt(requiredQty, growthPerMonth, asOf, s.start);

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

export interface WhatIfPurchase {
    id: number;
    kind: "once" | "recurring";
    /** Licenses per purchase, not per month — see monthlyRate() for the rate. */
    quantity: number;
    termYears: number;
    startDate: string;
    cadence: Cadence;
    /** Number of purchases in the series. Ignored for a one-off. */
    periods: number;
}

/** A single hypothetical purchase, shaped to concatenate onto the pool. */
export function hypotheticalBlock(
    quantity: number,
    termYears: number,
    startDate: string,
): AllocBlock {
    return {
        quantity,
        effective_date: startDate,
        expiration_date: addMonths(startDate, Math.round(termYears * 12)),
        sku: `WHAT-IF-${termYears}YR`,
    };
}

/**
 * Expand a purchase into the pool blocks it would create.
 *
 * A rolling purchase is modelled as independent blocks rather than one growing
 * block, because each tranche carries its own term and therefore its own
 * expiration — which is the whole reason staggered buying behaves differently
 * from one large one.
 */
export function expandPurchase(p: WhatIfPurchase): AllocBlock[] {
    if (p.kind === "once") {
        return [hypotheticalBlock(p.quantity, p.termYears, p.startDate)];
    }
    const step = CADENCE_MONTHS[p.cadence];
    const out: AllocBlock[] = [];
    for (let i = 0; i < Math.max(p.periods, 0); i++) {
        out.push(
            hypotheticalBlock(p.quantity, p.termYears, addMonths(p.startDate, i * step)),
        );
    }
    return out;
}

/** Licenses per month a purchase represents, for comparison against growth. */
export function monthlyRate(p: WhatIfPurchase): number {
    if (p.kind === "once") return 0;
    return p.quantity / CADENCE_MONTHS[p.cadence];
}

/** Total licenses a purchase adds across its whole series. */
export function totalLicenses(p: WhatIfPurchase): number {
    return p.kind === "once" ? p.quantity : p.quantity * Math.max(p.periods, 0);
}

/** Last date any tranche of a purchase is still in force. */
export function purchaseEnds(p: WhatIfPurchase): string {
    const blocks = expandPurchase(p);
    return blocks.length
        ? blocks[blocks.length - 1].expiration_date!
        : p.startDate;
}
