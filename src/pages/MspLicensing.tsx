import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, RefreshCw, ChevronRight, Info, CalendarClock, Shuffle, Download, Printer } from "lucide-react";
import { buildWorkbook, downloadBlob } from "@/lib/xlsx";
import { buildExportSheets } from "@/lib/mspLicensingExport";
import { useAuth } from "@/context/AuthContext";
import {
    planMaxPeriod,
    planForSingleEc,
    planEarliestStart,
    optimalAllocation,
    licenseDays,
    hypotheticalBlock,
    type AllocationResult,
    type AllocBlock,
    type PlanEc,
    type PlanResult,
    type PlanSegment,
    type ScenarioSegment,
    type StartWindow,
    allocationTimeline,
    demandAt,
    expandPurchase,
    monthlyRate,
    totalLicenses,
    purchaseEnds,
    CADENCE_LABEL,
    maxTranches,
    GRACE_WINDOW_DAYS,
    scaleModel,
    steadyState,
    type DemandModel,
    FLAT_DEMAND,
    requiredPurchases,
    poolTimeline,
    poolCliffs,
    poolSummary,
    commitmentTimeline,
    type Cadence,
    type WhatIfPurchase,
} from "@/lib/licensePlanner";
import {
    useMspLicensing,
    type ChurnPoint,
    type Cliff,
    type CombinedSegment,
    type EcPosition,
    type LicenseBlock,
    type QuarterBucket,
    type TimelineSegment,
} from "@/hooks/useMspLicensing";

/*
 * Ordinal single-hue ramp (blue), dark -> light. Encodes how long a license
 * block survives: the darkest band outlives everything above it. Validated for
 * light surfaces — the lightest step here clears the 2:1 floor against white.
 */
const RAMP = ["#184f95", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef"];

/** A second measure against the blue supply bands — categorical slot 2. */
const COMMITTED = "#eb6834";
/** Single-hue default for one-measure charts (histogram, sparklines). */
const ACCENT = "#2a78d6";
/** Reserved status colour: coverage you would have to buy. */
const DEFICIT = "#c0392b";

/** Evenly spaced ramp steps for n bands, darkest (longest-lived) first. */
function rampFor(n: number): string[] {
    if (n <= 1) return [RAMP[0]];
    return Array.from({ length: n }, (_, i) =>
        RAMP[Math.round((i * (RAMP.length - 1)) / (n - 1))]
    );
}

/** White text on the darker half of the ramp, near-black on the lighter half. */
function inkOn(hex: string): string {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return lum > 0.55 ? "#0b0b0b" : "#ffffff";
}

const DAY = 86400000;
const toDate = (s: string) => new Date(`${s}T00:00:00Z`).getTime();
/** Stable identity for a pool block, for the exclusion set. */
const blockKey = (b: LicenseBlock) =>
    String(b.id ?? `${b.sku}|${b.effective_date}|${b.expiration_date}|${b.quantity}`);

function fmtDate(s: string | null | undefined): string {
    if (!s) return "—";
    return new Date(`${s}T00:00:00Z`).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
    });
}

function fmtDuration(days: number | null | undefined): string {
    if (days === null || days === undefined) return "—";
    if (days < 0) return "expired";
    if (days < 60) return `${days} days`;
    const years = days / 365.25;
    if (years < 1) return `${Math.round(days / 30.44)} months`;
    return `${years.toFixed(1)} years`;
}

/** Renewal urgency. Always paired with the day count in text, never color alone. */
function urgency(days: number | null | undefined) {
    if (days === null || days === undefined) return { cls: "bg-gray-100 text-gray-500", label: "none" } as const;
    if (days < 30) return { cls: "bg-red-50 text-red-700", label: "critical" } as const;
    if (days < 90) return { cls: "bg-amber-50 text-amber-800", label: "serious" } as const;
    if (days < 365) return { cls: "bg-blue-50 text-blue-700", label: "warning" } as const;
    return { cls: "bg-gray-100 text-gray-600", label: "healthy" } as const;
}

// ---------------------------------------------------------------------------
// Pool capacity chart
// ---------------------------------------------------------------------------

const VB_W = 940;
/** Height with a single row of cliff labels; grows as more rows are needed. */
const BASE_H = 372;
const M = { top: 42, right: 28, bottom: 68, left: 56 };
const QTY_ROW_H = 13;
const DATE_ROW_H = 14;
/** Below this the viewBox shrinks text past legibility, so scroll instead. */
const MIN_CHART_PX = 640;

interface Band {
    key: string;
    expiration: string;
    start: number;
    /** When bought, if the term had not started yet. Null when immediate. */
    purchased: number | null;
    end: number;
    quantity: number;
    color: string;
    blocks: LicenseBlock[];
}

/**
 * Stacked step chart of licensed capacity over time.
 *
 * One band per expiration cohort, longest-lived at the bottom, so the pool
 * visibly steps down as each cohort lapses. This is the whole point of the
 * tool: R1's own summary reports a mixed-term pool as a single quantity
 * expiring on the *earliest* date, which erases the surviving tail.
 */
function PoolTimelineChart({
    blocks,
    cliffs,
    timeline,
    combined,
    optimized,
    mode,
    estateDemand,
    showCommitted = true,
    demand,
    asOf,
}: {
    blocks: LicenseBlock[];
    cliffs: Cliff[];
    timeline: TimelineSegment[];
    combined: CombinedSegment[];
    optimized: ScenarioSegment[] | null;
    mode: "actual" | "optimized";
    /**
     * Optional forward projection. Omitted on the "what we have today" chart,
     * supplied on the planner's "what we should have" copy — the difference
     * between describing the pool and projecting against it.
     */
    estateDemand?: number;
    demand?: DemandModel;
    /**
     * Off on the projection, where the story is capacity against demand.
     * Commitments there have all lapsed by the first cliff, so the line just
     * falls to zero and adds noise.
     */
    showCommitted?: boolean;
    asOf: string;
}) {
    const svgRef = useRef<SVGSVGElement>(null);
    const [hoverX, setHoverX] = useState<number | null>(null);
    const [hoverY, setHoverY] = useState<number | null>(null);

    const model = useMemo(() => {
        const live = blocks.filter((b) => !b.expired && b.expiration_date);
        if (!live.length) return null;

        const today = toDate(asOf);
        const lastExp = Math.max(...live.map((b) => toDate(b.expiration_date!)));
        // Pad the right edge so the final cliff label has room to sit.
        const domainEnd = lastExp + Math.max((lastExp - today) * 0.04, 5 * DAY);

        /*
         * Cohorts are keyed by their whole life, not just expiry: with staggered
         * purchases two tranches can share an end date but start months apart,
         * and merging those would misplace capacity in time.
         *
         * Ordered by start date so the pool you already own sits at the bottom
         * and later purchases stack visibly on top of it as they come into
         * force.
         */
        const byLife = new Map<string, LicenseBlock[]>();
        for (const b of live) {
            const eff = b.effective_date ?? asOf;
            const k = `${eff}|${b.expiration_date}`;
            byLife.set(k, [...(byLife.get(k) ?? []), b]);
        }
        const cohorts = [...byLife.entries()].sort((a, b) => {
            const [aEff, aExp] = a[0].split("|");
            const [bEff, bExp] = b[0].split("|");
            return toDate(aEff) - toDate(bEff) || toDate(aExp) - toDate(bExp);
        });
        const colors = rampFor(cohorts.length);
        // Shade by how long a cohort survives, not by where it sits in the
        // stack: darkest outlives everything. Stacking order is chronological,
        // so the two must be derived separately or the ramp inverts.
        const byLongevity = [...cohorts]
            .sort((a, b) => toDate(b[0].split("|")[1]) - toDate(a[0].split("|")[1]))
            .map(([k]) => k);

        const bands: Band[] = cohorts.map(([key, group]) => {
            const [eff, expiration] = key.split("|");
            const purchased = group
                .map((b) => (b as any).purchase_date as string | undefined)
                .filter(Boolean)
                .sort()[0];
            return {
                key,
                expiration,
                start: Math.max(today, toDate(eff)),
                end: toDate(expiration) + DAY,
                // Only meaningful when it precedes the start: bought, waiting.
                purchased:
                    purchased && toDate(purchased) < toDate(eff)
                        ? Math.max(today, toDate(purchased))
                        : null,
                quantity: group.reduce((s, b) => s + b.quantity, 0),
                color: colors[byLongevity.indexOf(key)],
                blocks: group,
            };
        });

        // The demand line has to fit on the same axis, and with growth on it
        // can rise well above anything the pool ever held.
        const demandEnd = estateDemand && demand
            ? demandAt(demand, asOf, new Date(domainEnd).toISOString().slice(0, 10))
            : 0;
        const maxCapacity = Math.max(
            ...timeline.map((s) => s.capacity), estateDemand ?? 0, demandEnd, 1,
        );

        const x = (t: number) =>
            M.left + ((t - today) / (domainEnd - today)) * (VB_W - M.left - M.right);

        /*
         * Lay cliff labels out onto as many rows as they need. Two rows was not
         * enough — a run of small cliffs close together still collided. Each
         * label takes the first row where it clears the previous entry, and the
         * chart grows to fit however many rows that takes.
         */
        const packRows = (xs: number[], minGap: number) => {
            const lastPerRow: number[] = [];
            const rowOf = xs.map((cx) => {
                for (let r = 0; r < lastPerRow.length; r++) {
                    if (cx - lastPerRow[r] >= minGap) {
                        lastPerRow[r] = cx;
                        return r;
                    }
                }
                lastPerRow.push(cx);
                return lastPerRow.length - 1;
            });
            return { rowOf, rowCount: Math.max(lastPerRow.length, 1) };
        };

        const cliffXs = cliffs.map((c) => x(toDate(c.date) + DAY));
        const dateLayout = packRows(cliffXs, 92);
        const qtyLayout = packRows(cliffXs, 34);

        const padTop = M.top + (qtyLayout.rowCount - 1) * QTY_ROW_H;
        const padBottom = M.bottom + (dateLayout.rowCount - 1) * DATE_ROW_H;
        const height = BASE_H + (padTop - M.top) + (padBottom - M.bottom);

        const y = (v: number) =>
            height - padBottom - (v / (maxCapacity * 1.08)) * (height - padTop - padBottom);

        // Year gridlines across the domain.
        const ticks: { t: number; label: string }[] = [];
        const firstYear = new Date(today).getUTCFullYear();
        const lastYear = new Date(domainEnd).getUTCFullYear();
        for (let yr = firstYear; yr <= lastYear; yr++) {
            const t = Date.UTC(yr, 0, 1);
            if (t >= today && t <= domainEnd) ticks.push({ t, label: String(yr) });
        }

        /*
         * Stack per time segment rather than once globally. Blocks that never
         * coexist must not pile on top of each other — doing so made the stack
         * total every licence ever bought, which both misplaced the bands and
         * pushed them off the top of the axis.
         */
        const edges = new Set<number>([today]);
        for (const b of bands) {
            if (b.start > today) edges.add(b.start);
            edges.add(b.end);
        }
        const cuts = [...edges].sort((a, z) => a - z);
        const pieces: {
            cohort: string; key: string; color: string; quantity: number;
            x0: number; x1: number; base: number;
        }[] = [];
        for (let i = 0; i < cuts.length - 1; i++) {
            const from = cuts[i], to = cuts[i + 1];
            let base = 0;
            for (const b of bands) {
                if (b.start > from || b.end <= from) continue;
                // Extend the run rather than emitting a fresh rectangle: a
                // cohort only needs a new one when something below it changes
                // and shifts its baseline. Without this a single licence block
                // is drawn as one rounded box per segment boundary, which reads
                // as far more buckets than were ever bought.
                const prev = pieces[pieces.length - 1];
                const run = pieces.find(
                    (pc) => pc.cohort === b.key && pc.x1 === from && pc.base === base,
                );
                void prev;
                if (run) {
                    run.x1 = to;
                } else {
                    pieces.push({
                        cohort: b.key, key: `${b.key}@${from}`, color: b.color,
                        quantity: b.quantity, x0: from, x1: to, base,
                    });
                }
                base += b.quantity;
            }
        }

        /*
         * Licences bought but not yet started provide no capacity, so they must
         * not join the stack. They are drawn instead as a hollow lead-in at the
         * slot the cohort will occupy once its term begins, which is what makes
         * a staggered activation window visible rather than merely implied.
         */
        const pending = bands
            .filter((b) => b.purchased !== null)
            .map((b) => {
                const firstLive = pieces.find((pc) => pc.cohort === b.key);
                if (!firstLive) return null;
                return {
                    key: `${b.key}#pending`,
                    color: b.color,
                    x0: b.purchased!,
                    x1: b.start,
                    base: firstLive.base,
                    quantity: b.quantity,
                };
            })
            .filter(Boolean) as {
                key: string; color: string; x0: number; x1: number;
                base: number; quantity: number;
            }[];

        // Label each cohort once, on its widest run.
        const widest = new Map<string, typeof pieces[number]>();
        for (const p of pieces) {
            const cur = widest.get(p.cohort);
            if (!cur || p.x1 - p.x0 > cur.x1 - cur.x0) widest.set(p.cohort, p);
        }
        const labelled = new Set([...widest.values()].map((p) => p.key));

        return {
            live, today, domainEnd, bands, pieces, pending, labelled, x, y, maxCapacity, ticks,
            height, padTop, padBottom,
            cliffXs, dateRowOf: dateLayout.rowOf, qtyRowOf: qtyLayout.rowOf,
        };
    }, [blocks, timeline, cliffs, asOf, estateDemand, demand]);

    /**
     * Where the pool cannot carry the estate, if nothing is bought.
     *
     * Deliberately drawn against *demand*, not against what is committed. The
     * committed line falls to zero as assignments lapse, which reads as demand
     * disappearing — but those customers still have hardware deployed. This is
     * the gap you would have to purchase your way out of.
     */
    const deficit = useMemo(() => {
        if (!model || !estateDemand || !demand) return null;
        const { x, y, today, domainEnd } = model;
        const iso = (t: number) => new Date(t).toISOString().slice(0, 10);

        // Stop at the pool's real end, not the axis padding. Past the last
        // expiration capacity is zero, so every chart would carry a permanent
        // sliver of hatch in the right margin — an artifact of the padding
        // rather than anything to plan around. The terminal cliff row says it.
        const poolEnd = timeline.length
            ? Math.min(toDate(timeline[timeline.length - 1].end) + DAY, domainEnd)
            : domainEnd;
        const bounds = new Set<number>([today, poolEnd]);
        for (const s of timeline) {
            bounds.add(Math.max(toDate(s.start), today));
            bounds.add(Math.min(toDate(s.end) + DAY, poolEnd));
        }
        // With growth the demand line is a ramp, so slice finely enough that
        // the shaded region follows it rather than stepping.
        if (demand!.runRatePerMonth || demand!.churnPctPerMonth) {
            const steps = 60;
            for (let i = 0; i <= steps; i++) {
                bounds.add(today + ((poolEnd - today) * i) / steps);
            }
        }
        const pts = [...bounds].sort((a, b) => a - b);

        const capAt = (t: number) =>
            timeline.find((s) => iso(t) >= s.start && iso(t) <= s.end)?.capacity ?? 0;

        const rects: { x: number; y: number; w: number; h: number }[] = [];
        let peak = 0;
        let atFirst: number | null = null;
        for (let i = 0; i < pts.length - 1; i++) {
            const t0 = pts[i];
            const need = demandAt(demand!, asOf, iso(t0));
            const cap = capAt(t0);
            if (need <= cap) continue;
            peak = Math.max(peak, need - cap);
            // The gap where it first opens — the number to act on, which is not
            // the peak (that only arrives once the pool has fully expired).
            if (atFirst === null) atFirst = need - cap;
            rects.push({
                x: x(t0),
                y: y(need),
                w: Math.max(x(pts[i + 1]) - x(t0), 0.5),
                h: Math.max(y(cap) - y(need), 0),
            });
        }
        // The line is drawn whether or not there is a shortfall — covering the
        // demand fully is itself worth seeing — so it lives outside `rects`.
        const line: string[] = [];
        pts.forEach((t, i) => {
            const yv = y(demandAt(demand!, asOf, iso(t)));
            line.push(`${i === 0 ? "M" : "L"}${x(t)},${yv}`);
        });

        const firstGap = pts.find(
            (t) => demandAt(demand!, asOf, iso(t)) > capAt(t),
        );
        return {
            rects, line: line.join(" "), peak, atFirst: atFirst ?? 0, firstGap,
            hasGap: rects.length > 0,
        };
    }, [model, timeline, estateDemand, demand, asOf]);

    /** The commitment set the chart is currently describing. */
    const activeSegments: CombinedSegment[] =
        mode === "optimized" && optimized ? optimized : combined;

    /** Committed-to-ECs as a step path over the same x/y scales. */
    const stepPath = useMemo(() => {
        if (!model) return () => null;
        const { x, y, domainEnd } = model;
        return (segs: CombinedSegment[] | null) => {
            if (!segs?.length) return null;
            const parts: string[] = [];
            segs.forEach((s, i) => {
                const x0 = x(toDate(s.start));
                const x1 = Math.min(x(toDate(s.end) + DAY), x(domainEnd));
                const yv = y(s.committed);
                parts.push(`${i === 0 ? "M" : "L"}${x0},${yv}`, `L${x1},${yv}`);
            });
            return parts.join(" ");
        };
    }, [model]);

    const committedPath = useMemo(
        () => (showCommitted ? stepPath(activeSegments) : null),
        [stepPath, activeSegments, showCommitted],
    );
    /** In optimized mode, keep today's line visible so the gain is legible. */
    const ghostPath = useMemo(
        () => (showCommitted && mode === "optimized" && optimized ? stepPath(combined) : null),
        [stepPath, mode, optimized, combined, showCommitted],
    );

    /**
     * The tranche under the cursor. Highlighting the whole cohort — not just
     * the segment being pointed at — is the point: it traces one purchase
     * across its life so the moment it drops out is visible.
     */
    const hoveredCohort = useMemo(() => {
        if (!model || hoverX === null || hoverY === null) return null;
        const { x, y, pieces: ps } = model;
        for (const p of ps) {
            if (x(p.x0) > hoverX || x(p.x1) < hoverX) continue;
            if (y(p.base + p.quantity) <= hoverY && hoverY <= y(p.base)) return p.cohort;
        }
        return null;
    }, [model, hoverX, hoverY]);

    const hover = useMemo(() => {
        if (!model || hoverX === null) return null;
        const { x, today, domainEnd } = model;
        const span = domainEnd - today;
        const frac = (hoverX - M.left) / (VB_W - M.left - M.right);
        if (frac < 0 || frac > 1) return null;
        const t = today + frac * span;
        const iso = new Date(t).toISOString().slice(0, 10);
        const seg = timeline.find((s) => iso >= s.start && iso <= s.end);
        const com = activeSegments.find((s) => iso >= s.start && iso <= s.end);
        const need = estateDemand && demand ? demandAt(demand, asOf, iso) : 0;
        return {
            t,
            iso,
            capacity: seg?.capacity ?? 0,
            committed: com?.committed ?? null,
            headroom: com?.headroom ?? null,
            demand: need,
            deficit: Math.max(0, need - (seg?.capacity ?? 0)),
            px: x(t),
        };
    }, [model, hoverX, timeline, combined]);

    if (!model) {
        return (
            <div className="text-sm text-gray-400 py-12 text-center">
                No active license blocks to plot.
            </div>
        );
    }

    const hoveredBand = hoveredCohort
        ? model.bands.find((b) => b.key === hoveredCohort) ?? null
        : null;
    const { bands, pieces, pending, labelled, x, y, maxCapacity, ticks, height, padTop, padBottom,
            cliffXs, dateRowOf, qtyRowOf } = model;
    const yTicks = [0, Math.round(maxCapacity / 2), maxCapacity];

    return (
        <div className="relative overflow-x-auto">
            <svg
                ref={svgRef}
                viewBox={`0 0 ${VB_W} ${height}`}
                className="w-full"
                style={{ minWidth: MIN_CHART_PX }}
                role="img"
                aria-label="Licensed capacity over time, stepping down at each expiration"
                onMouseMove={(e) => {
                    const r = svgRef.current!.getBoundingClientRect();
                    setHoverX(((e.clientX - r.left) * VB_W) / r.width);
                    setHoverY(((e.clientY - r.top) * height) / r.height);
                }}
                onMouseLeave={() => { setHoverX(null); setHoverY(null); }}
            >
                {/* recessive grid */}
                {yTicks.map((v) => (
                    <g key={v}>
                        <line
                            x1={M.left} x2={VB_W - M.right} y1={y(v)} y2={y(v)}
                            stroke="#e8e8e5" strokeWidth={1}
                        />
                        <text
                            x={M.left - 10} y={y(v) + 4} textAnchor="end"
                            fontSize={12} fill="#8a8a85" fontFamily="ui-monospace, monospace"
                        >
                            {v}
                        </text>
                    </g>
                ))}

                {/* year ticks */}
                {ticks.map((t) => (
                    <g key={t.label}>
                        <line
                            x1={x(t.t)} x2={x(t.t)} y1={padTop} y2={height - padBottom}
                            stroke="#f0efec" strokeWidth={1}
                        />
                        <text
                            x={x(t.t)} y={height - padBottom + 18} textAnchor="middle"
                            fontSize={12} fill="#8a8a85"
                        >
                            {t.label}
                        </text>
                    </g>
                ))}

                {/* capacity bands, stacked per time segment so the height at any
                    moment is the capacity actually in force then */}
                {pieces.map((p) => {
                    const yTop = y(p.base + p.quantity);
                    const yBottom = y(p.base);
                    const rawH = yBottom - yTop;
                    const w = Math.max(x(p.x1) - x(p.x0), 1);
                    /*
                     * Separator and corner radius scale with the band. A fixed
                     * 2px gap and 3px radius swallow a thin band whole and turn
                     * a narrow one into a pill, which is what made a plan with
                     * many small chunks look like scattered bubbles.
                     */
                    const gap = rawH > 7 ? 1 : 0;
                    const h = Math.max(rawH - gap, 0.75);
                    const r = Math.min(2, rawH / 5, w / 5);
                    const dim = hoveredCohort !== null && hoveredCohort !== p.cohort;
                    const showLabel = labelled.has(p.key) && h >= 16 && w >= 46;
                    return (
                        <g key={p.key}>
                            <rect
                                x={x(p.x0)} y={yTop + gap} width={w} height={h}
                                rx={r} fill={p.color}
                                opacity={dim ? 0.42 : 1}
                            />
                            {showLabel && (
                                <text
                                    x={x(p.x0) + 10} y={yTop + gap + h / 2 + 4}
                                    fontSize={13} fontWeight={600} fill={inkOn(p.color)}
                                    fontFamily="ui-monospace, monospace"
                                    opacity={dim ? 0.5 : 1}
                                >
                                    {p.quantity}
                                </text>
                            )}
                        </g>
                    );
                })}

                {/* outline the hovered tranche across its whole life */}
                {hoveredCohort !== null && pieces
                    .filter((p) => p.cohort === hoveredCohort)
                    .map((p) => (
                        <rect
                            key={`${p.key}#hi`}
                            x={x(p.x0)} y={y(p.base + p.quantity)}
                            width={Math.max(x(p.x1) - x(p.x0), 1)}
                            height={Math.max(y(p.base) - y(p.base + p.quantity), 0.75)}
                            fill="none" stroke="#0b0b0b" strokeWidth={1} opacity={0.45}
                        />
                    ))}

                {/* bought but not yet started — outlined, never filled, because
                    these licences carry no capacity during the window */}
                {pending.map((p) => {
                    const yTop = y(p.base + p.quantity);
                    const yBottom = y(p.base);
                    const rawH = yBottom - yTop;
                    const gap = rawH > 7 ? 1 : 0;
                    const h = Math.max(rawH - gap, 0.75);
                    const w = Math.max(x(p.x1) - x(p.x0), 1);
                    // The wait belongs to the tranche, so it follows the same
                    // highlight — hovering a chunk shows when it was bought too.
                    const cohortKey = p.key.replace("#pending", "");
                    const dim = hoveredCohort !== null && hoveredCohort !== cohortKey;
                    return (
                        <g key={p.key} opacity={dim ? 0.4 : 1}>
                            <rect
                                x={x(p.x0)} y={yTop + gap} width={w} height={h}
                                rx={Math.min(2, rawH / 5, w / 5)} fill={p.color} opacity={0.16}
                            />
                            {/* Only the activation edge is drawn. A full outline
                                competed with the cliff lines and band gaps for
                                attention; the one edge that carries meaning is
                                where the term actually begins. */}
                            <line
                                x1={x(p.x1)} x2={x(p.x1)} y1={yTop + gap} y2={yTop + gap + h}
                                stroke={p.color} strokeWidth={1} opacity={0.55}
                            />
                        </g>
                    );
                })}

                {/* expiration cliffs — labels stagger onto extra rows rather than
                    overlap when several fall close together */}
                {cliffs.map((c, i) => {
                    const cx = cliffXs[i];
                    const anchorSide =
                        cx > VB_W - M.right - 40 ? "end"
                        : cx < M.left + 40 ? "start"
                        : "middle";
                    const qtyRow = qtyRowOf[i];
                    const dateRow = dateRowOf[i];
                    return (
                        <g key={c.date}>
                            <line
                                x1={cx} x2={cx} y1={padTop - 8} y2={height - padBottom}
                                stroke="#52514e" strokeWidth={1} strokeDasharray="3 3"
                            />
                            {/* A leader down to its own row, so a label on a lower
                                row is still traceable to its line. */}
                            {dateRow > 0 && (
                                <line
                                    x1={cx} x2={cx}
                                    y1={height - padBottom + 24}
                                    y2={height - padBottom + 30 + dateRow * DATE_ROW_H}
                                    stroke="#c9c8c3" strokeWidth={1}
                                />
                            )}
                            <text
                                x={cx} y={padTop - 14 - qtyRow * QTY_ROW_H}
                                textAnchor={anchorSide}
                                fontSize={12} fill="#52514e" fontWeight={600}
                            >
                                −{c.quantity_lost}
                            </text>
                            <text
                                x={cx} y={height - padBottom + 36 + dateRow * DATE_ROW_H}
                                textAnchor={anchorSide} fontSize={11} fill="#52514e"
                            >
                                {fmtDate(c.date)}
                            </text>
                        </g>
                    );
                })}

                {/* today */}
                <line
                    x1={M.left} x2={M.left} y1={padTop - 8} y2={height - padBottom}
                    stroke="#0b0b0b" strokeWidth={1.5}
                />
                <text x={M.left + 6} y={padTop - 14} fontSize={11} fill="#0b0b0b" fontWeight={600}>
                    today
                </text>

                {/* deficit: demand the pool cannot cover if nothing is bought */}
                {deficit && (
                    <>
                        <defs>
                            <pattern
                                id="deficitHatch" width={7} height={7}
                                patternUnits="userSpaceOnUse" patternTransform="rotate(45)"
                            >
                                <rect width={7} height={7} fill={DEFICIT} opacity={0.1} />
                                <line
                                    x1={0} y1={0} x2={0} y2={7}
                                    stroke={DEFICIT} strokeWidth={2} opacity={0.32}
                                />
                            </pattern>
                        </defs>
                        {deficit.hasGap && deficit.rects.map((r, i) => (
                            <rect
                                key={i} x={r.x} y={r.y} width={r.w} height={r.h}
                                fill="url(#deficitHatch)"
                            />
                        ))}
                        <path
                            d={deficit.line} fill="none" stroke={DEFICIT}
                            strokeWidth={2} strokeDasharray="6 3"
                        />
                        {deficit.hasGap && deficit.firstGap !== undefined && (
                            <text
                                x={Math.min(x(deficit.firstGap) + 8, VB_W - M.right - 90)}
                                y={y(estateDemand ?? 0) - 8}
                                fontSize={11} fill={DEFICIT} fontWeight={700}
                                stroke="#ffffff" strokeWidth={3}
                                paintOrder="stroke" strokeLinejoin="round"
                            >
                                short {deficit.atFirst.toLocaleString()}
                                {deficit.peak > deficit.atFirst
                                    ? ` → ${deficit.peak.toLocaleString()}`
                                    : ""}
                            </text>
                        )}
                    </>
                )}

                {/* today's commitments, kept as a faint reference under the
                    optimized line so the recovered area is visible */}
                {ghostPath && (
                    <path
                        d={ghostPath} fill="none" stroke={COMMITTED}
                        strokeWidth={1.5} strokeLinejoin="round"
                        strokeDasharray="4 3" opacity={0.45}
                    />
                )}

                {/* committed-to-ECs step line — the demand side. Where it sits
                    below the bands, that gap is pool nobody is holding. */}
                {committedPath && (
                    <path
                        d={committedPath} fill="none" stroke={COMMITTED}
                        strokeWidth={2} strokeLinejoin="round"
                    />
                )}

                {/* hover crosshair */}
                {hover && (
                    <line
                        x1={hover.px} x2={hover.px} y1={padTop} y2={height - padBottom}
                        stroke="#0b0b0b" strokeWidth={1} opacity={0.35}
                    />
                )}
            </svg>

            {hover && (
                <div
                    className="absolute pointer-events-none bg-white border border-gray-200 rounded-lg shadow-lg px-3 py-2 text-xs"
                    style={{
                        left: `calc(${(hover.px / VB_W) * 100}% + 8px)`,
                        top: 8,
                        transform: hover.px > VB_W * 0.7 ? "translateX(-100%)" : undefined,
                    }}
                >
                    <div className="text-gray-500 mb-1">{fmtDate(hover.iso)}</div>
                    {hoveredBand && (
                        <div className="mb-1.5 pb-1.5 border-b border-gray-100 flex items-center gap-1.5">
                            <span
                                className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                                style={{ backgroundColor: hoveredBand.color }}
                            />
                            <span className="font-mono font-semibold text-gray-900">
                                {hoveredBand.quantity.toLocaleString()}
                            </span>
                            <span className="text-gray-500">
                                {fmtDate(new Date(hoveredBand.start).toISOString().slice(0, 10))}
                                {" → "}
                                {fmtDate(hoveredBand.expiration)}
                            </span>
                        </div>
                    )}
                    <table className="border-separate border-spacing-x-2 -mx-2">
                        <tbody>
                            <tr>
                                <td>
                                    <span
                                        className="inline-block w-2.5 h-2.5 rounded-sm align-middle"
                                        style={{ backgroundColor: RAMP[0] }}
                                    />
                                </td>
                                <td className="font-mono font-semibold text-gray-900 text-sm text-right">
                                    {hover.capacity.toLocaleString()}
                                </td>
                                <td className="text-gray-500 whitespace-nowrap">licensed</td>
                            </tr>
                            {hover.committed !== null && (
                                <>
                                    <tr>
                                        <td>
                                            <span
                                                className="inline-block w-2.5 h-0.5 align-middle"
                                                style={{ backgroundColor: COMMITTED }}
                                            />
                                        </td>
                                        <td className="font-mono font-semibold text-gray-900 text-sm text-right">
                                            {hover.committed.toLocaleString()}
                                        </td>
                                        <td className="text-gray-500 whitespace-nowrap">
                                            {mode === "optimized"
                                                ? "committed (planned)"
                                                : "committed to ECs"}
                                        </td>
                                    </tr>
                                    <tr>
                                        <td />
                                        <td className="font-mono text-gray-500 text-right">
                                            {hover.headroom!.toLocaleString()}
                                        </td>
                                        <td className="text-gray-400 whitespace-nowrap">
                                            unassigned
                                        </td>
                                    </tr>
                                </>
                            )}
                            {hover.deficit > 0 && (
                                <tr>
                                    <td>
                                        <span
                                            className="inline-block w-2.5 h-2.5 rounded-sm align-middle"
                                            style={{
                                                backgroundImage:
                                                    `repeating-linear-gradient(45deg, ${DEFICIT} 0 2px, transparent 2px 4px)`,
                                            }}
                                        />
                                    </td>
                                    <td
                                        className="font-mono font-semibold text-sm text-right"
                                        style={{ color: DEFICIT }}
                                    >
                                        {hover.deficit.toLocaleString()}
                                    </td>
                                    <td className="whitespace-nowrap" style={{ color: DEFICIT }}>
                                        short of {hover.demand.toLocaleString()} needed
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}

            {/* legend — identity is never color alone */}
            <div className="flex flex-wrap gap-x-6 gap-y-2 mt-3 px-2">
                {bands.map((b) => {
                    const term = b.blocks[0]?.term_years;
                    return (
                        <div key={b.key} className="flex items-center gap-2 text-xs">
                            <span
                                className="w-3 h-3 rounded-sm flex-shrink-0"
                                style={{ backgroundColor: b.color }}
                            />
                            <span className="font-mono font-semibold text-gray-900">
                                {b.quantity}
                            </span>
                            <span className="text-gray-500">
                                through {fmtDate(b.expiration)}
                                {term ? ` · ${term}yr term` : ""}
                            </span>
                        </div>
                    );
                })}
                {committedPath && (
                    <div className="flex items-center gap-2 text-xs">
                        <span
                            className="w-3 h-0.5 flex-shrink-0"
                            style={{ backgroundColor: COMMITTED }}
                        />
                        <span className="text-gray-500">
                            {mode === "optimized"
                                ? "committed after reallocation"
                                : "committed to end customers"}
                        </span>
                    </div>
                )}
                {pending.length > 0 && (
                    <div className="flex items-center gap-2 text-xs">
                        <span
                            className="w-3 h-3 rounded-sm flex-shrink-0"
                            style={{ backgroundColor: `${RAMP[3]}2e` }}
                        />
                        <span className="text-gray-500">
                            bought, awaiting activation
                        </span>
                    </div>
                )}
                {deficit && (
                    <div className="flex items-center gap-2 text-xs">
                        <span
                            className="w-3 h-0.5 flex-shrink-0"
                            style={{
                                backgroundImage:
                                    `repeating-linear-gradient(to right, ${DEFICIT} 0 4px, transparent 4px 7px)`,
                            }}
                        />
                        <span className="text-gray-500">projected demand</span>
                    </div>
                )}
                {deficit?.hasGap && (
                    <div className="flex items-center gap-2 text-xs">
                        <span
                            className="w-3 h-3 rounded-sm flex-shrink-0"
                            style={{
                                backgroundImage:
                                    `repeating-linear-gradient(45deg, ${DEFICIT} 0 2px, transparent 2px 5px)`,
                            }}
                        />
                        <span className="text-gray-500">
                            deficit — projected demand the pool cannot cover
                        </span>
                    </div>
                )}
                {ghostPath && (
                    <div className="flex items-center gap-2 text-xs">
                        <span
                            className="w-3 h-0.5 flex-shrink-0 opacity-45"
                            style={{
                                backgroundImage: `repeating-linear-gradient(to right, ${COMMITTED} 0 4px, transparent 4px 7px)`,
                            }}
                        />
                        <span className="text-gray-400">committed today</span>
                    </div>
                )}
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Cliffs ahead — the prep list
// ---------------------------------------------------------------------------

/**
 * Each upcoming drop in pool capacity, measured by what it costs in customer
 * coverage.
 *
 * The tempting check — "is what is still committed the next day <= what is
 * left?" — is nearly always true and nearly always useless, because customer
 * assignments usually expire on the same date as the block backing them. It
 * reports "covered" precisely when customers have just gone dark. What matters
 * is how much coverage ends here versus how much pool is free to renew it.
 */
function CliffsAhead({
    cliffs, combined, ecs, estateDemand, demand, asOf,
}: {
    cliffs: Cliff[];
    combined: CombinedSegment[];
    ecs: EcPosition[];
    /** What customers consume today — the bar any future pool has to clear. */
    estateDemand: number;
    demand: DemandModel;
    asOf: string;
}) {
    const upcoming = cliffs.filter((c) => c.days_out >= 0);
    if (!upcoming.length) {
        return (
            <div className="text-sm text-gray-400 py-4 text-center">
                No further expirations in the pool.
            </div>
        );
    }

    return (
        <div className="space-y-2">
            {upcoming.map((c) => {
                const after = new Date(toDate(c.date) + DAY).toISOString().slice(0, 10);
                const seg = combined.find((s) => after >= s.start && after <= s.end);
                const committedAfter = seg?.committed ?? 0;

                // Customers whose own licenses end on this exact date — the
                // coverage that actually stops.
                const affected = ecs
                    .map((e) => ({
                        name: e.name,
                        quantity: e.assignments
                            .filter((a) => a.expiration_date === c.date)
                            .reduce((s, a) => s + a.quantity, 0),
                    }))
                    .filter((e) => e.quantity > 0);
                const coverageLost = affected.reduce((s, e) => s + e.quantity, 0);

                // What is left over once surviving assignments take their share.
                const freeAfter = c.capacity_after - committedAfter;
                const renewShort = Math.max(0, coverageLost - freeAfter);
                // Rare but real: an assignment outliving the block behind it.
                const overCommitted = Math.max(0, committedAfter - c.capacity_after);
                // Nothing licensed past this date at all. Assignments will have
                // lapsed long before, so counting expiries here reports zero and
                // reads as "nothing happens" — the opposite of the truth.
                const terminal = c.capacity_after === 0;
                // Estate size on the cliff date, not today's, so the numbers
                // agree with the demand line on the chart above.
                const demandThere = demandAt(demand, asOf, c.date);
                const severe = terminal || renewShort > 0 || overCommitted > 0;
                const u = urgency(c.days_out);

                return (
                    <div
                        key={c.date}
                        className={`rounded-lg border px-3 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-1 ${
                            severe
                                ? "border-red-200 bg-red-50"
                                : "border-gray-200 bg-gray-50"
                        }`}
                    >
                        <div className="w-40">
                            <div className="font-semibold text-gray-900 text-sm">
                                {fmtDate(c.date)}
                            </div>
                            <div className="text-xs">
                                <span className={`px-1.5 py-0.5 rounded ${u.cls}`}>
                                    {fmtDuration(c.days_out)}
                                </span>
                            </div>
                        </div>

                        <div className="text-sm w-48">
                            <span className="font-mono font-semibold text-gray-900">
                                −{c.quantity_lost.toLocaleString()}
                            </span>{" "}
                            <span className="text-gray-500">pool lapses</span>
                            <span className="text-gray-400">
                                {" "}· {c.capacity_after.toLocaleString()} left
                            </span>
                        </div>

                        <div className="text-sm flex-1 min-w-[18rem]">
                            {terminal ? (
                                <span className="text-red-800">
                                    <strong>Everything ends here.</strong> The pool expires
                                    completely — no licenses exist beyond this date, so all{" "}
                                    {demandThere.toLocaleString()} licenses the estate needs
                                    by then require a new purchase to stay covered.
                                </span>
                            ) : overCommitted > 0 ? (
                                <span className="text-red-800">
                                    <strong>Over-committed:</strong>{" "}
                                    {committedAfter.toLocaleString()} licenses stay
                                    assigned past this date but only{" "}
                                    {c.capacity_after.toLocaleString()} remain — short by{" "}
                                    <strong>{overCommitted.toLocaleString()}</strong>.
                                </span>
                            ) : coverageLost === 0 ? (
                                <span className="text-gray-600">
                                    No assignment ends on this date
                                    {committedAfter > 0
                                        ? `, and ${committedAfter.toLocaleString()} stay assigned. `
                                        : " — they have all lapsed already. "}
                                    {demandThere > 0 && (
                                        <span
                                            className={
                                                c.capacity_after < demandThere
                                                    ? "text-red-800"
                                                    : "text-gray-500"
                                            }
                                        >
                                            {c.capacity_after.toLocaleString()} left covers{" "}
                                            {Math.round(
                                                (c.capacity_after / demandThere) * 100,
                                            )}
                                            % of the {demandThere.toLocaleString()} licenses
                                            the estate needs by then.
                                        </span>
                                    )}
                                </span>
                            ) : renewShort > 0 ? (
                                <span className="text-red-800">
                                    <strong>
                                        {coverageLost.toLocaleString()} licenses of customer
                                        coverage end here.
                                    </strong>{" "}
                                    Only {freeAfter.toLocaleString()} pool licenses are free
                                    afterwards — renewing them all needs{" "}
                                    <strong>{renewShort.toLocaleString()} more</strong>.
                                </span>
                            ) : (
                                <span className="text-gray-600">
                                    {coverageLost.toLocaleString()} licenses of customer
                                    coverage end here, and all of it can be renewed from the{" "}
                                    {freeAfter.toLocaleString()} left free.
                                </span>
                            )}
                        </div>

                        <div className="text-xs text-gray-500 w-full lg:w-64">
                            {affected.length > 0 ? (
                                <>
                                    <span className="text-gray-400">goes dark: </span>
                                    {affected
                                        .map((e) => `${e.name} (${e.quantity})`)
                                        .join(", ")}
                                </>
                            ) : terminal ? (
                                <span className="text-red-700 font-semibold">
                                    every customer — nothing is licensable past this date
                                </span>
                            ) : (
                                <span className="text-gray-400">
                                    no assignment ends on this date
                                </span>
                            )}
                            {c.skus.length > 0 && (
                                <span className="ml-2 font-mono text-[11px] text-gray-400">
                                    {c.skus.join(", ")}
                                </span>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Renewal queue — the call list as a picture
// ---------------------------------------------------------------------------

/** Bar fill by urgency. Always accompanied by the day count in text. */
const URGENCY_FILL = {
    critical: "#c0392b",
    serious: "#c77700",
    warning: "#2a78d6",
    healthy: "#8a8a85",
} as const;

function RenewalQueue({ ecs }: { ecs: EcPosition[] }) {
    const rows = ecs.filter((e) => e.days_to_effective_expiration != null);
    if (!rows.length) {
        return <div className="text-sm text-gray-400 py-6 text-center">No active assignments.</div>;
    }

    const maxDays = Math.max(...rows.map((e) => e.days_to_effective_expiration!), 1);
    // Year gridlines, so bar length reads as real time rather than rank.
    const marks = [365, 730, 1095, 1460].filter((d) => d <= maxDays * 1.05);

    return (
        <div className="space-y-1">
            {rows.map((ec) => {
                const days = ec.days_to_effective_expiration!;
                const u = urgency(days);
                const fill = URGENCY_FILL[u.label as keyof typeof URGENCY_FILL] ?? URGENCY_FILL.healthy;
                return (
                    <div key={ec.tenant_id} className="flex items-center gap-3 text-xs">
                        <div className="w-36 truncate text-gray-700" title={ec.name}>
                            {ec.name}
                        </div>
                        <div className="w-10 text-right font-mono text-gray-500">
                            {ec.quantity}
                        </div>
                        <div className="flex-1 relative h-5">
                            {marks.map((m) => (
                                <div
                                    key={m}
                                    className="absolute top-0 bottom-0 border-l border-gray-100"
                                    style={{ left: `${(m / maxDays) * 100}%` }}
                                />
                            ))}
                            <div
                                className="absolute top-1 bottom-1 rounded"
                                style={{
                                    width: `${Math.max((days / maxDays) * 100, 0.5)}%`,
                                    backgroundColor: fill,
                                }}
                            />
                        </div>
                        <div className="w-24 text-right text-gray-600">
                            {fmtDuration(days)}
                        </div>
                        <div className="w-24 text-right text-gray-400">
                            {fmtDate(ec.effective_expiration)}
                        </div>
                    </div>
                );
            })}
            <div className="flex items-center gap-3 text-[11px] text-gray-400 pt-1">
                <div className="w-36" />
                <div className="w-10" />
                <div className="flex-1 relative h-4">
                    {marks.map((m) => (
                        <div
                            key={m}
                            className="absolute top-0"
                            style={{ left: `${(m / maxDays) * 100}%`, transform: "translateX(-50%)" }}
                        >
                            {m / 365}yr
                        </div>
                    ))}
                </div>
                <div className="w-24" />
                <div className="w-24" />
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Expiration histogram — where renewal work piles up
// ---------------------------------------------------------------------------

function ExpirationHistogram({ quarters }: { quarters: QuarterBucket[] }) {
    const [hover, setHover] = useState<number | null>(null);
    if (!quarters.length) {
        return <div className="text-sm text-gray-400 py-6 text-center">Nothing expiring.</div>;
    }
    // Headroom above the tallest bar so its value label has somewhere to sit.
    const max = Math.max(...quarters.map((q) => q.total), 1) * 1.14;

    return (
        <div className="relative">
            <div className="flex items-end gap-1 h-40">
                {quarters.map((q, i) => (
                    <div
                        key={q.quarter}
                        className="flex-1 flex flex-col justify-end items-center h-full min-w-0"
                        onMouseEnter={() => setHover(i)}
                        onMouseLeave={() => setHover(null)}
                    >
                        {q.total > 0 && (
                            <div className="text-[11px] font-mono text-gray-600 mb-0.5">
                                {q.total}
                            </div>
                        )}
                        <div
                            className="w-full rounded-t transition-opacity"
                            style={{
                                height: `${(q.total / max) * 100}%`,
                                minHeight: q.total > 0 ? 2 : 0,
                                backgroundColor: ACCENT,
                                opacity: hover === null || hover === i ? 1 : 0.45,
                            }}
                        />
                    </div>
                ))}
            </div>
            <div className="flex gap-1 mt-1 border-t border-gray-200 pt-1">
                {quarters.map((q) => (
                    <div
                        key={q.quarter}
                        className="flex-1 text-[10px] text-gray-400 text-center min-w-0 truncate"
                    >
                        {q.quarter.replace("-", " ")}
                    </div>
                ))}
            </div>
            {hover !== null && quarters[hover].total > 0 && (
                <div className="absolute -top-2 left-1/2 -translate-x-1/2 bg-white border border-gray-200 rounded-lg shadow-lg px-3 py-2 text-xs pointer-events-none z-10">
                    <div className="font-semibold text-gray-900 mb-1">
                        {quarters[hover].quarter} · {quarters[hover].total} licenses
                    </div>
                    {quarters[hover].by_ec.map((e) => (
                        <div key={e.name} className="text-gray-600 whitespace-nowrap">
                            <span className="font-mono">{e.quantity}</span> {e.name}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Assignment churn — one small multiple per EC
// ---------------------------------------------------------------------------

/**
 * Per-EC assigned quantity over past time. Small multiples rather than one
 * stacked chart: each EC gets its own single-hue panel, so this stays readable
 * at any customer count and no categorical palette is needed.
 */
function ChurnSpark({ points, peak }: { points: ChurnPoint[]; peak: number }) {
    const W = 200, H = 44, PAD = 2;
    if (points.length < 2) return <div className="h-11" />;

    const t0 = toDate(points[0].date);
    const t1 = toDate(points[points.length - 1].date);
    const span = Math.max(t1 - t0, 1);
    const x = (t: number) => PAD + ((t - t0) / span) * (W - PAD * 2);
    const y = (v: number) => H - PAD - (v / peak) * (H - PAD * 2);

    const d: string[] = [];
    points.forEach((p, i) => {
        const px = x(toDate(p.date));
        const py = y(p.quantity);
        if (i === 0) d.push(`M${px},${py}`);
        else d.push(`L${px},${py}`);
        const next = points[i + 1];
        d.push(`L${next ? x(toDate(next.date)) : W - PAD},${py}`);
    });

    return (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-11" preserveAspectRatio="none">
            <path
                d={`${d.join(" ")} L${W - PAD},${H - PAD} L${PAD},${H - PAD} Z`}
                fill={ACCENT} opacity={0.13}
            />
            <path d={d.join(" ")} fill="none" stroke={ACCENT} strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke" />
        </svg>
    );
}

function ChurnGrid({ ecs }: { ecs: EcPosition[] }) {
    const rows = ecs.filter((e) => (e.churn?.length ?? 0) > 1);
    if (!rows.length) {
        return (
            <div className="text-sm text-gray-400 py-6 text-center">
                No assignment changes on record.
            </div>
        );
    }
    // One shared y-scale would flatten every small customer against the
    // largest, so each panel scales to its own peak and prints it.
    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {rows.map((ec) => {
                const quantities = ec.churn.map((c) => c.quantity);
                const peak = Math.max(...quantities, 1);
                const trough = Math.min(...quantities);
                const now = ec.churn[ec.churn.length - 1].quantity;
                const delta = now - ec.churn[0].quantity;
                // "flat" only if it never moved — several customers return to
                // their starting count after a lapse-and-reassign cycle, and
                // calling that flat hides the gap.
                const movement =
                    peak === trough
                        ? "flat"
                        : delta === 0
                        ? `dipped to ${trough}`
                        : delta > 0
                        ? `+${delta}`
                        : `${delta}`;
                return (
                    <div key={ec.tenant_id} className="min-w-0">
                        <div className="flex items-baseline justify-between gap-2">
                            <div className="text-xs text-gray-700 truncate" title={ec.name}>
                                {ec.name}
                            </div>
                            <div className="text-xs font-mono text-gray-900">{now}</div>
                        </div>
                        <ChurnSpark points={ec.churn} peak={peak} />
                        <div className="text-[11px] text-gray-400">
                            peak {peak} · {movement} since {fmtDate(ec.churn[0].date)}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Extension planner
// ---------------------------------------------------------------------------

/**
 * Available capacity over time for the current selection, against the quantity
 * it needs. Where the area sits above the dashed line, the selection is
 * covered; the first place it dips below is the ceiling on the extension.
 */
function AvailabilityChart({
    segments, required, maxDate, currentDate, demand, asOf,
}: {
    segments: PlanSegment[];
    required: number;
    maxDate: string | null;
    currentDate: string | null;
    demand: DemandModel;
    asOf: string;
}) {
    // Matched roughly to the pool chart above so the two read as a pair.
    const H = 320, W = 940;
    const m = { top: 30, right: 24, bottom: 42, left: 48 };
    const svgRef = useRef<SVGSVGElement>(null);
    const [hoverX, setHoverX] = useState<number | null>(null);
    if (!segments.length) return null;

    const today = toDate(asOf);
    const endT = toDate(segments[segments.length - 1].end) + DAY;
    const span = Math.max(endT - today, DAY);
    const scaled = scaleModel(demand, required);
    const endNeed = demandAt(scaled, asOf, segments[segments.length - 1].end);
    const peak =
        Math.max(...segments.map((s) => s.available), required, endNeed, 1) * 1.12;

    const x = (t: number) => m.left + ((t - today) / span) * (W - m.left - m.right);
    const y = (v: number) => H - m.bottom - (v / peak) * (H - m.top - m.bottom);

    const reqY = y(required);

    /* Same crosshair-and-tooltip contract as the pool charts, so hovering
       behaves identically wherever you are on the page. */
    let hover: {
        iso: string; px: number; available: number; need: number; covered: boolean;
    } | null = null;
    if (hoverX !== null) {
        const frac = (hoverX - m.left) / (W - m.left - m.right);
        if (frac >= 0 && frac <= 1) {
            const t = today + frac * span;
            const iso = new Date(t).toISOString().slice(0, 10);
            const seg = segments.find((s) => iso >= s.start && iso <= s.end)
                ?? segments[segments.length - 1];
            const need = demandAt(scaled, asOf, iso);
            hover = {
                iso, px: x(t), available: seg.available, need,
                covered: seg.available >= need,
            };
        }
    }
    const years: { t: number; label: string }[] = [];
    for (let yr = new Date(today).getUTCFullYear(); yr <= new Date(endT).getUTCFullYear(); yr++) {
        const t = Date.UTC(yr, 0, 1);
        if (t >= today && t <= endT) years.push({ t, label: String(yr) });
    }

    return (
        <div className="overflow-x-auto relative">
            <svg
                ref={svgRef}
                viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 560 }}
                role="img"
                aria-label="Licenses available to this selection over time, against the quantity needed"
                onMouseMove={(e) => {
                    const r = svgRef.current!.getBoundingClientRect();
                    setHoverX(((e.clientX - r.left) * W) / r.width);
                }}
                onMouseLeave={() => setHoverX(null)}
            >
                {years.map((yr) => (
                    <g key={yr.label}>
                        <line x1={x(yr.t)} x2={x(yr.t)} y1={m.top} y2={H - m.bottom}
                              stroke="#f0efec" strokeWidth={1} />
                        <text x={x(yr.t)} y={H - m.bottom + 15} textAnchor="middle"
                              fontSize={11} fill="#8a8a85">{yr.label}</text>
                    </g>
                ))}

                {/* available capacity, covered vs not */}
                {segments.map((s) => {
                    const x0 = x(toDate(s.start));
                    const x1 = x(toDate(s.end) + DAY);
                    const yv = y(s.available);
                    return (
                        <rect
                            key={s.start}
                            x={x0} y={yv} width={Math.max(x1 - x0 - 1, 1)}
                            height={Math.max(H - m.bottom - yv, 0)}
                            fill={s.covered ? ACCENT : "#c9c8c3"}
                            opacity={s.covered ? 0.85 : 0.5}
                        />
                    );
                })}

                {/* the quantity the selection needs — a ramp when growth is on,
                    since a flat rule would understate what must be covered later */}
                {demand.runRatePerMonth || demand.churnPctPerMonth ? (
                    <path
                        d={segments
                            .map((s, i) => {
                                const need = (iso: string) =>
                                    y(demandAt(scaled, asOf, iso));
                                return `${i === 0 ? "M" : "L"}${x(toDate(s.start))},${need(
                                    s.start,
                                )} L${x(toDate(s.end) + DAY)},${need(s.end)}`;
                            })
                            .join(" ")}
                        fill="none" stroke={COMMITTED} strokeWidth={2}
                        strokeDasharray="5 3"
                    />
                ) : (
                    <line x1={m.left} x2={W - m.right} y1={reqY} y2={reqY}
                          stroke={COMMITTED} strokeWidth={2} strokeDasharray="5 3" />
                )}
                {/* Halo, because this label often sits over a filled band */}
                <text
                    x={m.left + 4} y={reqY - 5} fontSize={11} fill={COMMITTED}
                    fontWeight={600} stroke="#ffffff" strokeWidth={3}
                    paintOrder="stroke" strokeLinejoin="round"
                >
                    {required.toLocaleString()} needed
                    {endNeed !== required ? ` → ${endNeed.toLocaleString()}` : ""}
                </text>

                {/* where they expire today vs where they could reach */}
                {currentDate && (
                    <g>
                        <line x1={x(toDate(currentDate))} x2={x(toDate(currentDate))}
                              y1={m.top} y2={H - m.bottom}
                              stroke="#8a8a85" strokeWidth={1} strokeDasharray="2 3" />
                        <text x={x(toDate(currentDate))} y={m.top - 8} textAnchor="middle"
                              fontSize={10} fill="#8a8a85">now expires</text>
                    </g>
                )}
                {maxDate && (
                    <g>
                        <line x1={x(toDate(maxDate) + DAY)} x2={x(toDate(maxDate) + DAY)}
                              y1={m.top} y2={H - m.bottom} stroke="#0b0b0b" strokeWidth={1.5} />
                        <text
                            x={x(toDate(maxDate) + DAY)} y={m.top - 8} fontSize={10}
                            fill="#0b0b0b" fontWeight={700}
                            textAnchor={x(toDate(maxDate) + DAY) > W * 0.75 ? "end" : "middle"}
                        >
                            could reach {fmtDate(maxDate)}
                        </text>
                    </g>
                )}

                <line x1={m.left} x2={W - m.right} y1={H - m.bottom} y2={H - m.bottom}
                      stroke="#e8e8e5" strokeWidth={1} />
                <text x={m.left - 8} y={y(0) + 4} textAnchor="end" fontSize={11} fill="#8a8a85">0</text>

                {hover && (
                    <line
                        x1={hover.px} x2={hover.px} y1={m.top} y2={H - m.bottom}
                        stroke="#0b0b0b" strokeWidth={1} opacity={0.35}
                    />
                )}
            </svg>

            {hover && (
                <div
                    className="absolute pointer-events-none bg-white border border-gray-200 rounded-lg shadow-lg px-3 py-2 text-xs"
                    style={{
                        left: `calc(${(hover.px / W) * 100}% + 8px)`,
                        top: 4,
                        transform: hover.px > W * 0.7 ? "translateX(-100%)" : undefined,
                    }}
                >
                    <div className="text-gray-500 mb-1">{fmtDate(hover.iso)}</div>
                    <table className="border-separate border-spacing-x-2 -mx-2">
                        <tbody>
                            <tr>
                                <td>
                                    <span
                                        className="inline-block w-2.5 h-2.5 rounded-sm align-middle"
                                        style={{
                                            backgroundColor: hover.covered ? ACCENT : "#c9c8c3",
                                        }}
                                    />
                                </td>
                                <td className="font-mono font-semibold text-gray-900 text-sm text-right">
                                    {hover.available.toLocaleString()}
                                </td>
                                <td className="text-gray-500 whitespace-nowrap">available</td>
                            </tr>
                            <tr>
                                <td>
                                    <span
                                        className="inline-block w-2.5 h-0.5 align-middle"
                                        style={{ backgroundColor: COMMITTED }}
                                    />
                                </td>
                                <td className="font-mono font-semibold text-gray-900 text-sm text-right">
                                    {hover.need.toLocaleString()}
                                </td>
                                <td className="text-gray-500 whitespace-nowrap">needed</td>
                            </tr>
                            <tr>
                                <td />
                                <td
                                    className="font-mono text-right"
                                    style={{ color: hover.covered ? "#8a8a85" : DEFICIT }}
                                >
                                    {hover.covered
                                        ? `+${(hover.available - hover.need).toLocaleString()}`
                                        : (hover.available - hover.need).toLocaleString()}
                                </td>
                                <td
                                    className="whitespace-nowrap"
                                    style={{ color: hover.covered ? "#8a8a85" : DEFICIT }}
                                >
                                    {hover.covered ? "spare" : "short"}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

const FIELD =
    "border border-gray-200 rounded px-2 py-1 focus:outline-none focus:border-blue-400";

/**
 * One row per order, expandable to the tranches it creates. A staggered order
 * is a single purchase with several activation dates, so collapsing it to its
 * tranches would misrepresent it as several purchases.
 */
function PurchaseRow({ p, onRemove }: { p: WhatIfPurchase; onRemove: () => void }) {
    const [open, setOpen] = useState(false);
    const blocks = expandPurchase(p);
    if (!blocks.length) return null;

    const gapDays = (b: (typeof blocks)[number]) =>
        b.purchase_date && b.effective_date
            ? Math.round((toDate(b.effective_date) - toDate(b.purchase_date)) / DAY)
            : 0;
    const gaps = blocks.map(gapDays);
    const multi = blocks.length > 1;
    const firstStart = blocks[0].effective_date!;
    const lastEnd = blocks.map((b) => b.expiration_date!).sort().slice(-1)[0];

    return (
        <>
            <tr
                className={`border-t border-gray-100 ${multi ? "cursor-pointer hover:bg-gray-50" : ""}`}
                onClick={() => multi && setOpen((o) => !o)}
            >
                <td className="py-2 pl-1 pr-2">
                    {multi && (
                        <ChevronRight
                            size={14}
                            className={`text-gray-400 transition-transform ${open ? "rotate-90" : ""}`}
                        />
                    )}
                </td>
                <td className="py-2 pr-4 text-right font-mono font-semibold text-gray-900">
                    {totalLicenses(p).toLocaleString()}
                </td>
                <td className="py-2 pr-4 text-gray-700">{fmtDate(p.startDate)}</td>
                <td className="py-2 pr-4 text-gray-700">
                    {fmtDate(firstStart)}
                    {multi && <span className="text-gray-400"> …</span>}
                </td>
                <td className="py-2 pr-4 text-gray-700">{fmtDate(lastEnd)}</td>
                <td className="py-2 pr-4 text-gray-600">{p.termYears} yr</td>
                <td className="py-2 pr-4 text-xs text-gray-500">
                    {multi
                        ? `${blocks.length} tranches · +${Math.min(...gaps)}–${Math.max(...gaps)}d`
                        : gaps[0]
                        ? `+${gaps[0]}d after purchase`
                        : "starts immediately"}
                </td>
                <td className="py-2">
                    <button
                        onClick={(e) => { e.stopPropagation(); onRemove(); }}
                        className="text-gray-400 hover:text-red-600 font-bold px-1"
                        aria-label="Remove this hypothetical purchase"
                    >
                        ×
                    </button>
                </td>
            </tr>
            {open && multi && blocks.map((b, i) => (
                <tr key={i} className="bg-gray-50/60 text-xs text-gray-600">
                    <td />
                    <td className="py-1 pr-4 text-right font-mono">{b.quantity}</td>
                    <td className="py-1 pr-4">{fmtDate(b.purchase_date)}</td>
                    <td className="py-1 pr-4">{fmtDate(b.effective_date)}</td>
                    <td className="py-1 pr-4">{fmtDate(b.expiration_date)}</td>
                    <td className="py-1 pr-4">{p.termYears} yr</td>
                    <td className="py-1 pr-4 text-gray-400">
                        {gaps[i] ? `+${gaps[i]}d` : "immediate"}
                    </td>
                    <td />
                </tr>
            ))}
        </>
    );
}

/** Add hypothetical purchases to the pool and re-plan against them. */
function WhatIfControls({
    items, onAdd, onRemove, demand, asOf,
}: {
    items: WhatIfPurchase[];
    onAdd: (p: Omit<WhatIfPurchase, "id">) => void;
    onRemove: (id: number) => void;
    demand: DemandModel;
    asOf: string;
}) {
    const [kind, setKind] = useState<WhatIfPurchase["kind"]>("once");
    const [qty, setQty] = useState(20);
    const [term, setTerm] = useState(3);
    const [startDate, setStartDate] = useState(asOf);
    const [cadence, setCadence] = useState<Cadence>("quarterly");
    const [periods, setPeriods] = useState(8);
    /** Days from purchase to the term starting; the window allows up to 180. */
    const [defer, setDefer] = useState(0);
    const [tranches, setTranches] = useState(6);
    const [intervalDays, setIntervalDays] = useState(30);

    const trancheCap = maxTranches(intervalDays);
    // Hard cap: the activation window cannot be exceeded, so the control is
    // clamped rather than allowed to describe a purchase you cannot place.
    const trancheCount = Math.min(Math.max(1, tranches), trancheCap);
    const draft: Omit<WhatIfPurchase, "id"> = {
        kind, quantity: qty, termYears: term, startDate, cadence, periods,
        tranches: trancheCount, intervalDays, deferDays: defer,
    };
    // Expressed as a date because that is how an order is actually placed,
    // but stored as an offset so the window is enforced by construction.
    const startIso = new Date(toDate(startDate) + defer * DAY)
        .toISOString().slice(0, 10);
    const windowEnd = new Date(toDate(startDate) + GRACE_WINDOW_DAYS * DAY)
        .toISOString().slice(0, 10);
    const rate = monthlyRate({ ...draft, id: 0 });
    const perTranche = Math.floor(qty / trancheCount);
    const lastOffset = (trancheCount - 1) * intervalDays;

    return (
        <div className="space-y-2 text-sm">
            <div className="flex rounded-lg border border-gray-200 overflow-hidden w-fit text-xs">
                {(
                    [
                        ["once", "One-off"],
                        ["recurring", "Rolling purchase"],
                        ["staggered", "Staggered activation"],
                    ] as const
                ).map(([k, label]) => (
                    <button
                        key={k}
                        onClick={() => setKind(k)}
                        className={`px-3 py-1 ${
                            kind === k
                                ? "bg-blue-600 text-white font-semibold"
                                : "bg-white text-gray-600 hover:bg-gray-50"
                        }`}
                    >
                        {label}
                    </button>
                ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <span className="text-gray-500">
                    {kind === "once" ? "Buy" : "Buy"}
                </span>
                <input
                    type="number" min={1} value={qty}
                    aria-label="Licenses per purchase"
                    onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))}
                    className={`w-20 font-mono ${FIELD}`}
                />
                <span className="text-gray-500">
                    {kind === "staggered" ? "licenses in total, activated in" : "licenses"}
                </span>

                {kind === "staggered" && (
                    <>
                        <input
                            type="number" min={1} max={trancheCap} value={trancheCount}
                            aria-label="Number of tranches"
                            onChange={(e) => setTranches(Math.max(1, Number(e.target.value) || 1))}
                            className={`w-16 font-mono ${FIELD}`}
                        />
                        <span className="text-gray-500">tranches,</span>
                        <input
                            type="number" min={1} max={GRACE_WINDOW_DAYS} value={intervalDays}
                            aria-label="Days between activations"
                            onChange={(e) => setIntervalDays(Math.max(1, Number(e.target.value) || 1))}
                            className={`w-16 font-mono ${FIELD}`}
                        />
                        <span className="text-gray-500">days apart,</span>
                    </>
                )}

                {kind === "recurring" && (
                    <>
                        <span className="text-gray-500">every</span>
                        <select
                            value={cadence}
                            aria-label="Purchase cadence"
                            onChange={(e) => setCadence(e.target.value as Cadence)}
                            className={FIELD}
                        >
                            {(Object.keys(CADENCE_LABEL) as Cadence[]).map((c) => (
                                <option key={c} value={c}>{CADENCE_LABEL[c]}</option>
                            ))}
                        </select>
                        <span className="text-gray-500">×</span>
                        <input
                            type="number" min={1} value={periods}
                            aria-label="Number of purchases"
                            onChange={(e) => setPeriods(Math.max(1, Number(e.target.value) || 1))}
                            className={`w-16 font-mono ${FIELD}`}
                        />
                    </>
                )}

                <span className="text-gray-500">on a</span>
                <select
                    value={term}
                    aria-label="Term in years"
                    onChange={(e) => setTerm(Number(e.target.value))}
                    className={FIELD}
                >
                    {[1, 2, 3, 4, 5].map((y) => (
                        <option key={y} value={y}>{y} year</option>
                    ))}
                </select>
                <span className="text-gray-500">term, purchased</span>
                <input
                    type="date" value={startDate} min={asOf}
                    aria-label="Purchase date"
                    onChange={(e) => setStartDate(e.target.value || asOf)}
                    className={FIELD}
                />
                {kind !== "staggered" && (
                    <>
                        <span className="text-gray-500">starting</span>
                        <input
                            type="date" value={startIso}
                            min={startDate} max={windowEnd}
                            aria-label="Term start date"
                            onChange={(e) => {
                                const v = e.target.value;
                                if (!v) return setDefer(0);
                                const days = Math.round(
                                    (toDate(v) - toDate(startDate)) / DAY,
                                );
                                setDefer(Math.min(Math.max(days, 0), GRACE_WINDOW_DAYS));
                            }}
                            className={FIELD}
                        />
                    </>
                )}
                <button
                    onClick={() => onAdd(draft)}
                    className="px-3 py-1 rounded bg-blue-600 text-white text-xs font-semibold hover:bg-blue-700 print:hidden"
                >
                    Add
                </button>

                {kind === "staggered" && (
                    <span className="text-xs text-gray-400">
                        ≈ {perTranche.toLocaleString()} per tranche · last starts day{" "}
                        {lastOffset} of {GRACE_WINDOW_DAYS}
                        {tranches > trancheCap && (
                            <span className="text-amber-700">
                                {" "}· capped at {trancheCap} — {intervalDays}d spacing does
                                not fit more in the activation window
                            </span>
                        )}
                    </span>
                )}

                {kind === "recurring" && (
                    <span className="text-xs text-gray-400">
                        = {rate.toFixed(rate % 1 ? 1 : 0)}/mo ·{" "}
                        {(qty * periods).toLocaleString()} total
                        {demand.runRatePerMonth > 0 && (
                            <span
                                className={
                                    rate >= demand.runRatePerMonth
                                        ? " text-green-700"
                                        : " text-amber-700"
                                }
                            >
                                {" "}·{" "}
                                {rate >= demand.runRatePerMonth
                                    ? `keeps pace with +${demand.runRatePerMonth}/mo`
                                    : `behind +${demand.runRatePerMonth}/mo`}
                            </span>
                        )}
                    </span>
                )}
            </div>

            {items.length > 0 && (
                <div className="pt-2">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-xs uppercase tracking-wide text-gray-400 text-left">
                                <th className="pb-2 pl-1 pr-2 font-medium w-6" />
                                <th className="pb-2 pr-4 font-medium text-right">Qty</th>
                                <th className="pb-2 pr-4 font-medium">Purchased</th>
                                <th className="pb-2 pr-4 font-medium">Starts</th>
                                <th className="pb-2 pr-4 font-medium">Ends</th>
                                <th className="pb-2 pr-4 font-medium">Term</th>
                                <th className="pb-2 pr-4 font-medium">Activation</th>
                                <th className="pb-2 font-medium w-6" />
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((w) => (
                                <PurchaseRow key={w.id} p={w} onRemove={() => onRemove(w.id)} />
                            ))}
                            <tr className="border-t border-gray-200">
                                <td />
                                <td className="pt-2 pr-4 text-right font-mono font-semibold text-gray-900">
                                    {items.reduce((s, w) => s + totalLicenses(w), 0).toLocaleString()}
                                </td>
                                <td className="pt-2 text-xs text-gray-500" colSpan={6}>
                                    licenses added in total
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

function ExtensionPlanner({
    ecs, plan, required, onRequiredChange, selectedCount, asOf, earliest, demand,
}: {
    ecs: EcPosition[];
    plan: PlanResult;
    required: number;
    onRequiredChange: (n: number | null) => void;
    selectedCount: number;
    asOf: string;
    earliest: StartWindow | null;
    demand: DemandModel;
}) {
    if (!selectedCount) {
        return (
            <div className="text-sm text-gray-400 py-8 text-center">
                Select customers in the table below to see how far their licenses
                could be extended.
            </div>
        );
    }

    const gain = plan.gainDays;
    return (
        <>
            <div className="flex flex-wrap items-end gap-6 mb-4">
                <div>
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                        Licenses needed
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                        <input
                            type="number"
                            min={0}
                            value={required}
                            onChange={(e) => {
                                const v = e.target.value;
                                onRequiredChange(v === "" ? null : Math.max(0, Number(v)));
                            }}
                            className="w-28 text-2xl font-semibold font-mono text-gray-900 border-b-2 border-gray-200 focus:border-blue-400 focus:outline-none"
                        />
                        <button
                            onClick={() => onRequiredChange(null)}
                            className="text-xs text-gray-400 hover:text-gray-700 underline"
                            title="Reset to the selection's current license count"
                        >
                            reset
                        </button>
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                        across {selectedCount} customer{selectedCount === 1 ? "" : "s"}
                    </div>
                </div>

                <div>
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                        Expires today
                    </div>
                    <div className="text-2xl font-semibold text-gray-900 mt-1">
                        {fmtDate(plan.currentDate)}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                        {fmtDuration(plan.currentDays)} out
                    </div>
                </div>

                <div className="text-gray-300 text-2xl pb-6">→</div>

                <div
                    className={`rounded-xl border px-4 py-3 ${
                        plan.feasible
                            ? gain && gain > 0
                                ? "bg-blue-50 border-blue-200"
                                : "bg-gray-50 border-gray-200"
                            : "bg-red-50 border-red-200"
                    }`}
                >
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                        Max extension
                    </div>
                    <div className="text-2xl font-semibold text-gray-900 mt-1">
                        {plan.feasible ? fmtDate(plan.maxDate) : "Not possible"}
                    </div>
                    <div className="text-xs mt-1">
                        {plan.feasible ? (
                            gain === null ? (
                                <span className="text-gray-500">{fmtDuration(plan.maxDays)} out</span>
                            ) : gain > 0 ? (
                                <span className="text-blue-700 font-semibold">
                                    +{fmtDuration(gain)} beyond today's expiry
                                </span>
                            ) : (
                                <span className="text-gray-500">
                                    already at the maximum
                                </span>
                            )
                        ) : (
                            <span className="text-red-700">
                                {plan.availableToday.toLocaleString()} free today
                            </span>
                        )}
                    </div>
                </div>

                <div className="text-xs text-gray-500 max-w-xs pb-1">
                    <span className="font-semibold text-gray-600">Limited by:</span>{" "}
                    {plan.limitedBy}
                </div>
            </div>

            {/* When it will not fit today, "not yet" is more useful than "no" */}
            {!plan.feasible && (
                <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
                    {earliest ? (
                        <>
                            <span className="text-gray-500">Earliest you could start:</span>{" "}
                            <strong className="text-gray-900">
                                {fmtDate(earliest.startDate)}
                            </strong>{" "}
                            <span className="text-gray-500">
                                — {fmtDuration(earliest.waitDays)} from now, once other
                                customers' assignments lapse. From there it would run
                                through <strong>{fmtDate(earliest.throughDate)}</strong>.
                            </span>
                        </>
                    ) : (
                        <span className="text-gray-500">
                            The pool never has room for{" "}
                            <strong className="text-gray-900">
                                {required.toLocaleString()}
                            </strong>{" "}
                            licenses at once, at any point in its remaining term — even
                            with everything else unassigned.
                        </span>
                    )}
                </div>
            )}

            <AvailabilityChart
                segments={plan.segments}
                required={required}
                maxDate={plan.feasible ? plan.maxDate : null}
                currentDate={plan.currentDate}
                demand={demand}
                asOf={asOf}
            />

            <div className="flex flex-wrap gap-x-6 gap-y-2 mt-3 px-2 text-xs">
                <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: ACCENT }} />
                    <span className="text-gray-500">available &amp; sufficient</span>
                </div>
                <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#c9c8c3" }} />
                    <span className="text-gray-500">available but short</span>
                </div>
                <div className="flex items-center gap-2">
                    <span className="w-3 h-0.5" style={{ backgroundColor: COMMITTED }} />
                    <span className="text-gray-500">quantity needed</span>
                </div>
            </div>
        </>
    );
}

// ---------------------------------------------------------------------------
// Optimal allocation
// ---------------------------------------------------------------------------

/** Horizontal utilization meter — committed license-days against owned. */
function UtilizationBar({
    label, pct, detail, tone,
}: {
    label: string; pct: number; detail: string; tone: "current" | "planned";
}) {
    return (
        <div>
            <div className="flex items-baseline justify-between text-xs mb-1">
                <span className="text-gray-500">{label}</span>
                <span className="font-mono font-semibold text-gray-900">
                    {pct.toFixed(1)}%
                </span>
            </div>
            <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
                <div
                    className="h-full rounded-full"
                    style={{
                        width: `${Math.min(pct, 100)}%`,
                        backgroundColor: tone === "planned" ? ACCENT : "#c9c8c3",
                    }}
                />
            </div>
            <div className="text-[11px] text-gray-400 mt-1">{detail}</div>
        </div>
    );
}

/**
 * A concrete reassignment plan: which customer should sit on which pool block.
 * The pool's long-dated blocks are a scarce resource, and packing the smallest
 * customers into them first gets the most customers onto the far date.
 */
function AllocationPanel({ alloc }: { alloc: AllocationResult }) {
    const currentPct = alloc.ownedLicenseDays
        ? (alloc.currentLicenseDays / alloc.ownedLicenseDays) * 100
        : 0;

    return (
        <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-4">
                <UtilizationBar
                    label="Committed today"
                    pct={currentPct}
                    tone="current"
                    detail={`${alloc.currentLicenseDays.toLocaleString()} of ${alloc.ownedLicenseDays.toLocaleString()} license-days`}
                />
                <UtilizationBar
                    label="Under this plan"
                    pct={alloc.plannedPct}
                    tone="planned"
                    detail={`${alloc.plannedLicenseDays.toLocaleString()} license-days · ${(
                        alloc.plannedLicenseDays - alloc.currentLicenseDays
                    ).toLocaleString()} recovered`}
                />
                <div className="flex items-center">
                    <div>
                        <div className="text-2xl font-semibold text-gray-900 font-mono">
                            {alloc.improved}
                        </div>
                        <div className="text-xs text-gray-500">
                            customer{alloc.improved === 1 ? "" : "s"} would gain time
                        </div>
                    </div>
                </div>
            </div>

            {alloc.unplaced.length > 0 && (
                <div className="mb-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-800">
                    Pool cannot cover:{" "}
                    {alloc.unplaced
                        .map((u) => `${u.name} (${u.quantity.toLocaleString()} short)`)
                        .join(", ")}
                </div>
            )}

            <table className="w-full text-sm">
                <thead>
                    <tr className="text-xs uppercase tracking-wide text-gray-400 text-left">
                        <th className="pb-2 pr-4 font-medium">Customer</th>
                        <th className="pb-2 pr-4 font-medium text-right">Qty</th>
                        <th className="pb-2 pr-4 font-medium">Expires now</th>
                        <th className="pb-2 pr-4 font-medium">Would expire</th>
                        <th className="pb-2 pr-4 font-medium">Gain</th>
                        <th className="pb-2 font-medium">Drawn from</th>
                    </tr>
                </thead>
                <tbody>
                    {alloc.rows.map((r) => (
                        <tr key={r.tenant_id} className="border-t border-gray-100">
                            <td className="py-2 pr-4 text-gray-900">{r.name}</td>
                            <td className="py-2 pr-4 font-mono text-right text-gray-600">
                                {r.quantity.toLocaleString()}
                            </td>
                            <td className="py-2 pr-4 text-gray-500">
                                {fmtDate(r.currentExpiration)}
                            </td>
                            <td className="py-2 pr-4 text-gray-900">
                                {fmtDate(r.plannedExpiration)}
                            </td>
                            <td className="py-2 pr-4">
                                {r.gainDays === null ? (
                                    <span className="text-gray-300">—</span>
                                ) : r.gainDays > 0 ? (
                                    <span className="text-blue-700 font-semibold text-xs">
                                        +{fmtDuration(r.gainDays)}
                                    </span>
                                ) : (
                                    <span className="text-gray-400 text-xs">no change</span>
                                )}
                            </td>
                            <td className="py-2 text-xs text-gray-500">
                                {r.portions.map((p, i) => (
                                    <span key={i} className="mr-2 whitespace-nowrap">
                                        <span className="font-mono">{p.quantity}</span>
                                        {" from "}
                                        <span className="font-mono">{p.sku ?? "—"}</span>
                                    </span>
                                ))}
                                {r.straddles && (
                                    <span
                                        className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-800"
                                        title="Drawn from blocks with different end dates — covered only through the earliest"
                                    >
                                        straddles
                                    </span>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </>
    );
}

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------

function Stat({
    label, value, sub, tone = "neutral", mono = true,
}: {
    label: string; value: string; sub?: string;
    tone?: "neutral" | "accent" | "warn";
    /** Off for dates — monospace spaces them out badly. */
    mono?: boolean;
}) {
    const tones = {
        neutral: "bg-white border-gray-200",
        accent: "bg-blue-50 border-blue-200",
        warn: "bg-amber-50 border-amber-200",
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`}>
            <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
            <div className={`text-2xl font-semibold text-gray-900 mt-1 ${mono ? "font-mono" : ""}`}>
                {value}
            </div>
            {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
        </div>
    );
}

function AssignmentRows({ rows, muted = false }: { rows: LicenseBlock[]; muted?: boolean }) {
    if (!rows.length) return null;
    return (
        <table className="w-full text-xs">
            <tbody>
                {rows.map((a) => (
                    <tr key={String(a.id)} className={muted ? "text-gray-400" : "text-gray-600"}>
                        <td className="py-1 pr-4 font-mono font-semibold">{a.quantity}</td>
                        <td className="py-1 pr-4">{a.license_type}</td>
                        <td className="py-1 pr-4 font-mono text-[11px]">{a.sku}</td>
                        <td className="py-1 pr-4">
                            {fmtDate(a.effective_date)} → {fmtDate(a.expiration_date)}
                        </td>
                        <td className="py-1 pr-4">{a.term_years ? `${a.term_years}yr` : "—"}</td>
                        <td className="py-1">
                            <span className="px-1.5 py-0.5 rounded bg-gray-100 text-[11px]">
                                {a.status}
                            </span>
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}

function EcRow({
    ec, selected, onToggle, maxAlone,
}: {
    ec: EcPosition;
    selected: boolean;
    onToggle: () => void;
    maxAlone: PlanResult | null;
}) {
    const [open, setOpen] = useState(false);
    const u = urgency(ec.days_to_effective_expiration);
    const hasTail = (ec.tail_days ?? 0) > 0;
    const gain = maxAlone?.gainDays ?? null;

    return (
        <>
            <tr
                className={`border-t border-gray-100 cursor-pointer ${
                    selected ? "bg-blue-50/60" : "hover:bg-gray-50"
                }`}
                onClick={() => setOpen((o) => !o)}
            >
                <td className="py-2 pl-2 w-8" onClick={(e) => e.stopPropagation()}>
                    <input
                        type="checkbox"
                        checked={selected}
                        onChange={onToggle}
                        className="cursor-pointer"
                        aria-label={`Include ${ec.name} in the extension plan`}
                    />
                </td>
                <td className="py-2 pr-3">
                    <div className="flex items-center gap-1.5">
                        <ChevronRight
                            size={14}
                            className={`text-gray-400 transition-transform ${open ? "rotate-90" : ""}`}
                        />
                        <span className="font-medium text-gray-900">{ec.name}</span>
                    </div>
                </td>
                <td className="py-2 pr-3 font-mono text-right">{ec.quantity.toLocaleString()}</td>
                <td className="py-2 pr-3">{fmtDate(ec.effective_expiration)}</td>
                <td className="py-2 pr-3">
                    <span className={`px-2 py-0.5 rounded text-xs ${u.cls}`}>
                        {fmtDuration(ec.days_to_effective_expiration)}
                    </span>
                </td>
                <td className="py-2 pr-3">
                    {maxAlone?.feasible && maxAlone.maxDate ? (
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-gray-700">{fmtDate(maxAlone.maxDate)}</span>
                            {gain !== null && gain > 0 && (
                                <span className="text-blue-700 text-xs font-semibold">
                                    +{fmtDuration(gain)}
                                </span>
                            )}
                        </div>
                    ) : (
                        <span className="text-gray-300">—</span>
                    )}
                </td>
                <td className="py-2 pr-3 text-gray-500 text-xs">
                    {hasTail && (
                        <span title="Some licenses outlive the first expiration" className="mr-2">
                            +{fmtDuration(ec.tail_days)} tail
                        </span>
                    )}
                    {ec.license_types.join(", ") || "—"}
                </td>
                <td className="py-2 pr-2 text-gray-400 text-xs text-right">
                    {ec.historical_count > 0 ? `${ec.historical_count} past` : ""}
                </td>
            </tr>
            {open && (
                <tr className="bg-gray-50/60">
                    <td colSpan={8} className="px-8 py-3">
                        {ec.error ? (
                            <div className="text-xs text-red-700">{ec.error}</div>
                        ) : (
                            <>
                                <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">
                                    Active assignments
                                </div>
                                {ec.assignments.length ? (
                                    <AssignmentRows rows={ec.assignments} />
                                ) : (
                                    <div className="text-xs text-gray-400">None</div>
                                )}
                                {ec.history.length > 0 && (
                                    <>
                                        <div className="text-[11px] uppercase tracking-wide text-gray-400 mt-3 mb-1">
                                            History ({ec.history.length} expired / revoked)
                                        </div>
                                        <AssignmentRows rows={ec.history} muted />
                                    </>
                                )}
                            </>
                        )}
                    </td>
                </tr>
            )}
        </>
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function MspLicensing() {
    const { activeControllerName, activeControllerType } = useAuth();
    const { data, loading, error, refresh } = useMspLicensing();
    const [filter, setFilter] = useState("");
    const [selected, setSelected] = useState<Set<string>>(new Set());
    /** null = follow the selection's own license count. */
    const [qtyOverride, setQtyOverride] = useState<number | null>(null);
    /** Loose planning assumption: licences the estate adds each month. */
    const [runRate, setRunRate] = useState(0);
    const [churnPct, setChurnPct] = useState(0);
    /** null = follow the estate's real assigned total. */
    const [baseOverride, setBaseOverride] = useState<number | null>(null);

    const demandModel: DemandModel = useMemo(
        () => ({
            base: baseOverride ?? data?.assigned_total ?? 0,
            runRatePerMonth: runRate,
            churnPctPerMonth: churnPct,
        }),
        [baseOverride, data, runRate, churnPct],
    );


    // Start with every customer selected — the whole-estate view is the most
    // useful default, and narrowing down reads better than building up. Seeded
    // once, so a Refresh does not discard a selection the user has curated.
    const seeded = useRef(false);
    useEffect(() => {
        if (data && !seeded.current) {
            seeded.current = true;
            setSelected(new Set(data.ecs.map((e) => e.tenant_id)));
        }
    }, [data]);

    const ecs = useMemo(() => {
        if (!data) return [];
        const q = filter.trim().toLowerCase();
        return q ? data.ecs.filter((e) => e.name?.toLowerCase().includes(q)) : data.ecs;
    }, [data, filter]);

    // --- extension planning -------------------------------------------
    const planEcs: PlanEc[] = useMemo(
        () =>
            (data?.ecs ?? []).map((e) => ({
                tenant_id: e.tenant_id,
                name: e.name,
                quantity: e.quantity,
                assignments: e.assignments,
            })),
        [data],
    );

    const actualPool = useMemo(
        () => (data?.pool.blocks ?? []).filter((b) => !b.expired),
        [data],
    );

    /*
     * Blocks the user has excluded. R1 can carry entitlements that should not
     * count toward the real pool, and leaving them in quietly inflates every
     * number on the page. Tracked as exclusions rather than selections so a
     * refresh that surfaces a genuinely new block includes it by default.
     */
    const [excluded, setExcluded] = useState<Set<string>>(new Set());
    const selectedPool = useMemo(
        () => actualPool.filter((b) => !excluded.has(blockKey(b))),
        [actualPool, excluded],
    );
    const excludedBlocks = useMemo(
        () => actualPool.filter((b) => excluded.has(blockKey(b))),
        [actualPool, excluded],
    );
    const excludedQty = excludedBlocks.reduce((s, b) => s + b.quantity, 0);

    /*
     * Everything the pool drives, recomputed from the selected blocks. The
     * server ships the same figures for the full pool; these reproduce them
     * exactly when nothing is excluded, and stay correct when something is.
     */
    const view = useMemo(() => {
        if (!data) return null;
        const asOf = data.as_of;
        const summary = poolSummary(selectedPool, asOf);
        const combined = commitmentTimeline(selectedPool, planEcs, asOf);
        const tail = [...combined].reverse().find((x) => x.capacity > 0) ?? null;
        return {
            timeline: poolTimeline(selectedPool, asOf),
            cliffs: poolCliffs(selectedPool, asOf),
            summary,
            combined,
            idleTail:
                tail && tail.committed === 0 && tail.headroom > 0
                    ? { from: tail.start, until: tail.end, quantity: tail.headroom }
                    : null,
        };
    }, [data, selectedPool, planEcs]);

    // Hypothetical purchases feed every planning view — the planner, the
    // allocation plan and the utilization score — but never the pool chart,
    // which stays a picture of what is actually owned.
    const [whatIf, setWhatIf] = useState<WhatIfPurchase[]>([]);
    const livePool: AllocBlock[] = useMemo(() => {
        if (!data) return actualPool;
        return [...selectedPool, ...whatIf.flatMap(expandPurchase)];
    }, [selectedPool, whatIf, data]);

    /*
     * What the extension planner may quote against.
     *
     * A tenant can be extended into licences already bought — including ones
     * still inside their activation window, since those are paid for and will
     * switch on — but NOT into an order that has not been placed. A future
     * purchase in the what-if list is a plan, not capacity, and quoting a
     * customer against it would promise a term nothing yet backs.
     */
    const quotablePool = useMemo(
        () =>
            livePool.filter(
                (b: any) => !b.purchase_date || b.purchase_date <= (data?.as_of ?? ""),
            ),
        [livePool, data],
    );
    const unpurchased = livePool.length - quotablePool.length;

    /** What each customer could reach on its own, at its current size. */
    const maxAlone = useMemo(() => {
        const out = new Map<string, PlanResult>();
        if (!data) return out;
        for (const ec of planEcs) {
            if (!ec.quantity) continue;
            out.set(ec.tenant_id, planForSingleEc(quotablePool, planEcs, ec, data.as_of));
        }
        return out;
    }, [data, planEcs, quotablePool]);

    const selectedQty = useMemo(
        () =>
            planEcs
                .filter((e) => selected.has(e.tenant_id))
                .reduce((s, e) => s + e.quantity, 0),
        [planEcs, selected],
    );
    const requiredQty = qtyOverride ?? selectedQty;

    const plan = useMemo(
        () =>
            data
                ? planMaxPeriod(
                      quotablePool, planEcs, selected, data.as_of, requiredQty, demandModel,
                  )
                : null,
        [data, quotablePool, planEcs, selected, requiredQty, demandModel],
    );

    const earliest = useMemo(
        () =>
            data && plan && !plan.feasible
                ? planEarliestStart(
                      quotablePool, planEcs, selected, data.as_of, requiredQty, demandModel,
                  )
                : null,
        [data, plan, quotablePool, planEcs, selected, requiredQty, demandModel],
    );

    const alloc = useMemo(
        () => (data ? optimalAllocation(livePool, planEcs, data.as_of) : null),
        [data, livePool, planEcs],
    );

    const util = useMemo(
        () => (data ? licenseDays(livePool, planEcs, data.as_of) : null),
        [data, livePool, planEcs],
    );

    /**
     * The first point the pool can no longer carry the estate at the size it
     * runs today. This is the top-up question — cliffs tell you when coverage
     * ends, this tells you whether you can renew it from what you own.
     */
    const shortfallPoint = useMemo(() => {
        if (!data) return null;
        const demand = data.assigned_total ?? 0;
        if (!demand) return null;
        const seg = (view?.combined ?? []).find(
            (s) => s.capacity < demand,
        );
        return seg
            ? {
                  from: seg.start,
                  capacity: seg.capacity,
                  demand,
              }
            : null;
    }, [data, view]);

    /*
     * The forward-looking twin of `view`: the same pool maths, but over the
     * pool as it *would* be — selected blocks plus any hypothetical purchases.
     * Kept separate so the top chart stays a description of what exists today.
     */
    const projected = useMemo(() => {
        if (!data) return null;
        return {
            timeline: poolTimeline(livePool, data.as_of),
            cliffs: poolCliffs(livePool, data.as_of),
            combined: commitmentTimeline(livePool, planEcs, data.as_of),
        };
    }, [data, livePool, planEcs]);

    /*
     * What it would actually take to meet the demand curve: orders placed at a
     * cadence, each sized to the worst moment before the next one, and each
     * expiring in its turn so its own replacement shows up further down.
     * Planned against livePool, so anything already added above is credited.
     */
    const [planTerm, setPlanTerm] = useState(3);
    const [planCadence, setPlanCadence] = useState<Cadence>("annual");
    /*
     * How finely each order is broken up inside its activation window.
     * 60 days by default: tighter staggers track demand marginally better
     * (30d saves ~7% more idle capacity) but nearly double the line items and
     * the bands on the chart, which is a poor trade for the extra precision.
     */
    const [planStagger, setPlanStagger] = useState(60);
    /** null = plan to the end of what is currently owned. */
    const [planYears, setPlanYears] = useState<number | null>(null);
    const planHorizon = useMemo(() => {
        if (!data) return null;
        if (planYears === null) return view?.summary.last_expiration ?? null;
        const d = new Date(toDate(data.as_of));
        d.setUTCFullYear(d.getUTCFullYear() + planYears);
        return d.toISOString().slice(0, 10);
    }, [data, view, planYears]);

    const purchasePlan = useMemo(() => {
        if (!data || !planHorizon) return null;
        return requiredPurchases(
            livePool, demandModel, data.as_of, planHorizon, planTerm, planCadence,
            planStagger,
        );
    }, [data, livePool, demandModel, planHorizon, planTerm, planCadence, planStagger]);

    const [chartMode, setChartMode] = useState<"actual" | "optimized">("actual");
    // Deliberately planned against the ACTUAL pool, not the what-if one: this
    // chart draws bands for the blocks you own, so a committed line drawn from
    // hypothetical capacity would float above them and imply licences that do
    // not exist.
    const optimizedTimeline = useMemo(() => {
        if (!data) return null;
        const actualAlloc = optimalAllocation(selectedPool, planEcs, data.as_of);
        return actualAlloc.rows.length
            ? allocationTimeline(selectedPool, actualAlloc, data.as_of)
            : null;
    }, [data, selectedPool, planEcs]);

    const exportWorkbook = () => {
        if (!data || !view) return;
        const sheets = buildExportSheets({
            data, view, actualPool, selectedPool, excluded, blockKey,
            demandModel, whatIf, plan, selectedEcIds: selected,
            requiredQty, alloc, maxAlone, util,
        });
        const stamp = new Date().toISOString().slice(0, 10);
        const who = (activeControllerName ?? "msp").replace(/[^A-Za-z0-9]+/g, "-");
        downloadBlob(buildWorkbook(sheets), `msp-licensing-${who}-${stamp}.xlsx`);
    };

    const toggle = (id: string) =>
        setSelected((prev) => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });

    const allVisibleSelected =
        ecs.length > 0 && ecs.every((e) => selected.has(e.tenant_id));

    if (loading) return <p className="p-4 text-gray-500">Loading MSP licensing…</p>;

    if (error || !data) {
        return (
            <div className="p-4">
                <div className="bg-red-50 border-2 border-red-200 rounded-lg p-6 flex items-start gap-3">
                    <AlertCircle className="w-6 h-6 text-red-600 mt-0.5 flex-shrink-0" />
                    <div>
                        <h3 className="text-lg font-semibold text-red-900 mb-2">
                            Could not load MSP licensing
                        </h3>
                        <p className="text-red-800">{error}</p>
                        {activeControllerType && activeControllerType !== "RuckusONE" && (
                            <p className="text-sm text-gray-700 mt-3">
                                This tool needs an MSP-level RuckusONE controller. "{activeControllerName}"
                                is a {activeControllerType} controller — pick another on the{" "}
                                <a href="/controllers" className="underline font-semibold text-blue-600">
                                    Controllers
                                </a>{" "}
                                page.
                            </p>
                        )}
                    </div>
                </div>
            </div>
        );
    }

    const { compliance } = data;
    const pool = view?.summary ?? null;
    if (!pool || !view) return null;
    const firstCliff = view.cliffs[0] ?? null;
    const hasTail = (pool.tail_days ?? 0) > 0;
    const unassigned = (pool.capacity_today ?? 0) - (data.assigned_total ?? 0);

    return (
        <div className="p-4 msp-licensing-print">
            <style>{`
                @media print {
                    /* Everything outside this tool — nav, sidebar, other tools */
                    body * { visibility: hidden; }
                    .msp-licensing-print, .msp-licensing-print * { visibility: visible; }
                    .msp-licensing-print {
                        position: absolute; left: 0; top: 0; width: 100%; padding: 0;
                    }
                    .print\\:hidden { display: none !important; }
                    /* Keep a panel and its chart on one sheet wherever it fits */
                    .msp-licensing-print .bg-white { break-inside: avoid; page-break-inside: avoid; }
                    /* Charts scroll horizontally on screen; on paper let them size down */
                    .msp-licensing-print .overflow-x-auto { overflow: visible !important; }
                    .msp-licensing-print svg { min-width: 0 !important; max-width: 100%; }
                    /* Inputs render as flat text so the configuration is legible */
                    .msp-licensing-print input, .msp-licensing-print select {
                        border: none !important; -webkit-appearance: none; appearance: none;
                    }
                    .msp-licensing-print input[type="checkbox"] { -webkit-appearance: checkbox; appearance: checkbox; }
                }
                @page { margin: 12mm; size: A4 landscape; }
            `}</style>
            <div className="flex items-center justify-between mb-1">
                <h2 className="text-xl font-semibold text-gray-700">
                    MSP Licensing {activeControllerName ? `— ${activeControllerName}` : ""}
                </h2>
                <div className="flex items-center gap-1 print:hidden">
                    <button
                        onClick={exportWorkbook}
                        title="Every table on this page, as configured, one sheet each"
                        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100"
                    >
                        <Download size={14} /> Export data
                    </button>
                    <button
                        onClick={() => window.print()}
                        title="Charts print as vectors — choose Save as PDF"
                        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100"
                    >
                        <Printer size={14} /> Print / PDF
                    </button>
                    <button
                        onClick={refresh}
                        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100"
                    >
                        <RefreshCw size={14} /> Refresh
                    </button>
                </div>
            </div>
            <p className="text-xs text-gray-400 mb-5">As of {fmtDate(data.as_of)}</p>

            {data.warnings?.length > 0 && (
                <div className="mb-4 rounded-lg bg-amber-50 border border-amber-200 px-4 py-2 text-sm text-amber-900">
                    {data.warnings.map((w, i) => (
                        <div key={i}>{w}</div>
                    ))}
                </div>
            )}

            {/* Headline numbers */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-5">
                <Stat
                    label="Capacity today"
                    value={(pool.capacity_today ?? 0).toLocaleString()}
                    sub={
                        excluded.size
                            ? `${selectedPool.length} of ${actualPool.length} blocks · ${excludedQty.toLocaleString()} excluded`
                            : data.pool.courtesy
                            ? `${data.pool.purchased.toLocaleString()} purchased + ${data.pool.courtesy} courtesy`
                            : `${pool.block_count} license block${pool.block_count === 1 ? "" : "s"}`
                    }
                />
                <Stat
                    label="Effective expiration"
                    value={fmtDate(pool.effective_expiration)}
                    mono={false}
                    sub={`${fmtDuration(pool.days_to_effective_expiration)} out · earliest of all blocks`}
                    tone={(pool.days_to_effective_expiration ?? 999) < 90 ? "warn" : "neutral"}
                />
                <Stat
                    label="Longest runway"
                    value={fmtDate(pool.last_expiration)}
                    mono={false}
                    sub={
                        hasTail
                            ? `${fmtDuration(pool.tail_days)} beyond the first cliff`
                            : "all blocks expire together"
                    }
                    tone={hasTail ? "accent" : "neutral"}
                />
                <Stat
                    label="Assigned to ECs"
                    value={(data.assigned_total ?? 0).toLocaleString()}
                    sub={
                        util
                            ? `${util.pct.toFixed(0)}% of owned license-days committed`
                            : unassigned > 0
                            ? `${unassigned.toLocaleString()} unassigned`
                            : "fully allocated"
                    }
                    tone={unassigned < 0 ? "warn" : "neutral"}
                />
            </div>

            {/* The thing R1's own summary hides */}
            {hasTail && firstCliff && (
                <div className="mb-5 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 flex gap-3">
                    <Info size={18} className="text-blue-600 mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-blue-900">
                        RuckusONE reports this pool as expiring{" "}
                        <strong>{fmtDate(pool.effective_expiration)}</strong>. That is the earliest
                        of {pool.cliff_count} expiration dates — on that day{" "}
                        <strong>{firstCliff.quantity_lost.toLocaleString()}</strong> licenses lapse
                        and <strong>{firstCliff.capacity_after.toLocaleString()}</strong> remain, running
                        another <strong>{fmtDuration(pool.tail_days)}</strong> to{" "}
                        {fmtDate(pool.last_expiration)}.
                    </div>
                </div>
            )}

            {/* Never let an exclusion be invisible — every figure below moves. */}
            {excluded.size > 0 && (
                <div className="mb-5 rounded-xl border border-gray-300 bg-gray-50 px-4 py-3 flex gap-3">
                    <Info size={18} className="text-gray-500 mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-gray-700">
                        Excluding <strong>{excluded.size}</strong> license block
                        {excluded.size === 1 ? "" : "s"} (
                        <strong>{excludedQty.toLocaleString()}</strong> licenses). Every
                        figure, chart and plan on this page reflects the remaining{" "}
                        <strong>{pool.capacity_today.toLocaleString()}</strong>.{" "}
                        <button
                            onClick={() => setExcluded(new Set())}
                            className="underline hover:text-gray-900"
                        >
                            Include all
                        </button>
                    </div>
                </div>
            )}

            {/* The top-up question: when does the pool stop being able to carry
                the estate at its current size? */}
            {shortfallPoint && (
                <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 flex gap-3">
                    <AlertCircle size={18} className="text-red-600 mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-red-900">
                        From <strong>{fmtDate(shortfallPoint.from)}</strong> the pool holds{" "}
                        <strong>{shortfallPoint.capacity.toLocaleString()}</strong> licenses
                        against <strong>{shortfallPoint.demand.toLocaleString()}</strong>{" "}
                        your customers hold today — enough for{" "}
                        <strong>
                            {Math.round(
                                (shortfallPoint.capacity / shortfallPoint.demand) * 100,
                            )}
                            %
                        </strong>{" "}
                        of it. Covering everyone then needs{" "}
                        <strong>
                            {(shortfallPoint.demand - shortfallPoint.capacity).toLocaleString()}
                        </strong>{" "}
                        more licenses.
                    </div>
                </div>
            )}

            {/* Pool outliving every assignment — the mirror of the cliff problem */}
            {view.idleTail && view.idleTail.quantity > 0 && (
                <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex gap-3">
                    <Info size={18} className="text-amber-700 mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-amber-900">
                        From <strong>{fmtDate(view.idleTail.from)}</strong> to{" "}
                        <strong>{fmtDate(view.idleTail.until)}</strong>,{" "}
                        <strong>{view.idleTail.quantity.toLocaleString()}</strong> licenses
                        have no customer assigned against them — pool you own but
                        nobody is holding.
                    </div>
                </div>
            )}

            {/* Pool timeline */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 mb-5">
                <div className="flex items-start justify-between gap-4 mb-1">
                    <div className="text-sm font-semibold text-gray-700">
                        Licensed capacity over time
                    </div>
                    {optimizedTimeline && (
                        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs flex-shrink-0">
                            {(
                                [
                                    ["actual", "As assigned today"],
                                    ["optimized", "After reallocation"],
                                ] as const
                            ).map(([key, label]) => (
                                <button
                                    key={key}
                                    onClick={() => setChartMode(key)}
                                    className={`px-3 py-1 ${
                                        chartMode === key
                                            ? "bg-blue-600 text-white font-semibold"
                                            : "bg-white text-gray-600 hover:bg-gray-50"
                                    }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
                <div className="text-xs text-gray-400 mb-3">
                    Bands stack in the order they come into force, shaded darker the
                    longer they last.
                    {chartMode === "optimized" && (
                        <span className="text-blue-700">
                            {" "}Showing the plan from Optimal allocation below — every
                            customer runs to the end of the block they were packed into.

                        </span>
                    )}
                </div>
                <PoolTimelineChart
                    blocks={selectedPool}
                    cliffs={view.cliffs}
                    timeline={view.timeline}
                    combined={view.combined}
                    optimized={optimizedTimeline}
                    mode={chartMode}
                    asOf={data.as_of}
                />

                <div className="mt-5 pt-4 border-t border-gray-100">
                    <div className="text-sm font-semibold text-gray-700 mb-1">
                        Cliffs ahead
                    </div>
                    <div className="text-xs text-gray-400 mb-3">
                        Each drop in pool capacity, which customers lose their licenses
                        on that date, and whether enough pool is left to renew them.
                    </div>
                    <CliffsAhead
                        cliffs={view.cliffs}
                        combined={view.combined}
                        ecs={data.ecs}
                        estateDemand={data.assigned_total ?? 0}
                        demand={demandModel}
                        asOf={data.as_of}
                    />
                </div>
            </div>

            {/* Renewal queue + expiration concentration */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
                <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="text-sm font-semibold text-gray-700 mb-1">
                        Renewal queue
                    </div>
                    <div className="text-xs text-gray-400 mb-3">
                        Time until each customer's first licenses lapse.
                    </div>
                    <RenewalQueue ecs={data.ecs} />
                </div>

                <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="text-sm font-semibold text-gray-700 mb-1">
                        Licenses expiring by quarter
                    </div>
                    <div className="text-xs text-gray-400 mb-3">
                        Hover a bar for the customers behind it.
                    </div>
                    <ExpirationHistogram quarters={data.quarters ?? []} />
                </div>
            </div>

            {/* Historical churn */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 mb-5">
                <div className="text-sm font-semibold text-gray-700 mb-1">
                    Assignment history
                </div>
                <div className="text-xs text-gray-400 mb-4">
                    Licenses held by each customer over time, from assignment and
                    revocation dates. Each panel scales to its own peak.
                </div>
                <ChurnGrid ecs={data.ecs} />
            </div>

            {/* Pool blocks + MSP device counts */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-5">
                <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 p-4">
                    <div className="flex items-center justify-between gap-3 mb-1">
                        <div className="text-sm font-semibold text-gray-700">
                            License pool ({selectedPool.length} of {actualPool.length} blocks)
                        </div>
                        {excluded.size > 0 && (
                            <button
                                onClick={() => setExcluded(new Set())}
                                className="text-xs text-gray-500 hover:text-gray-800 underline"
                            >
                                include all
                            </button>
                        )}
                    </div>
                    <div className="text-xs text-gray-400 mb-3">
                        Untick a block to leave it out of every figure on this page — for
                        entitlements R1 is carrying that should not count toward your pool.
                    </div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-xs uppercase tracking-wide text-gray-400 text-left">
                                <th className="pb-2 pl-1 pr-2 font-medium w-8">
                                    <input
                                        type="checkbox"
                                        checked={excluded.size === 0}
                                        aria-label="Include all license blocks"
                                        onChange={() =>
                                            setExcluded(
                                                excluded.size === 0
                                                    ? new Set(actualPool.map(blockKey))
                                                    : new Set(),
                                            )
                                        }
                                        className="cursor-pointer"
                                    />
                                </th>
                                <th className="pb-2 pr-4 font-medium">SKU</th>
                                <th className="pb-2 pr-4 font-medium text-right">Qty</th>
                                <th className="pb-2 pr-4 font-medium">Term</th>
                                <th className="pb-2 pr-4 font-medium">Effective</th>
                                <th className="pb-2 pr-4 font-medium">Expires</th>
                                <th className="pb-2 font-medium text-right">Remaining</th>
                            </tr>
                        </thead>
                        <tbody>
                            {actualPool.map((b) => {
                              const key = blockKey(b);
                              const off = excluded.has(key);
                              return (
                                <tr
                                    key={key}
                                    className={`border-t border-gray-100 ${
                                        off
                                            ? "text-gray-300 line-through"
                                            : b.expired
                                            ? "text-gray-400"
                                            : "text-gray-700"
                                    }`}
                                >
                                    <td className="py-2 pl-1 pr-2">
                                        <input
                                            type="checkbox"
                                            checked={!off}
                                            aria-label={`Include ${b.sku} expiring ${b.expiration_date}`}
                                            onChange={() =>
                                                setExcluded((prev) => {
                                                    const next = new Set(prev);
                                                    next.has(key) ? next.delete(key) : next.add(key);
                                                    return next;
                                                })
                                            }
                                            className="cursor-pointer"
                                        />
                                    </td>
                                    <td className="py-2 pr-4 font-mono text-xs">
                                        {b.sku}
                                        {b.is_trial && (
                                            <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 text-[10px]">
                                                trial
                                            </span>
                                        )}
                                    </td>
                                    <td className="py-2 pr-4 text-right font-mono">{b.quantity}</td>
                                    <td className="py-2 pr-4">{b.term_years ? `${b.term_years} yr` : "—"}</td>
                                    <td className="py-2 pr-4">{fmtDate(b.effective_date)}</td>
                                    <td className="py-2 pr-4">{fmtDate(b.expiration_date)}</td>
                                    <td className="py-2 text-right">{fmtDuration(b.days_remaining)}</td>
                                </tr>
                              );
                            })}
                        </tbody>
                    </table>
                </div>

                <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="text-sm font-semibold text-gray-700 mb-3">
                        Consumption (MSP-wide)
                    </div>
                    {compliance?.error ? (
                        <div className="text-xs text-gray-400">{compliance.error}</div>
                    ) : (
                        <>
                            <div className="flex gap-4 mb-3">
                                <div>
                                    <div className="text-xl font-mono font-semibold text-gray-900">
                                        {(compliance.used ?? 0).toLocaleString()}
                                    </div>
                                    <div className="text-xs text-gray-500">licenses used</div>
                                </div>
                                <div>
                                    <div className="text-xl font-mono font-semibold text-gray-900">
                                        {(compliance.available ?? 0).toLocaleString()}
                                    </div>
                                    <div className="text-xs text-gray-500">headroom</div>
                                </div>
                            </div>
                            <table className="w-full text-xs">
                                <tbody>
                                    {(compliance.device_breakdown ?? [])
                                        .filter((d) => d.installed > 0 || d.used > 0)
                                        .map((d) => (
                                            <tr key={d.device_type} className="text-gray-600">
                                                <td className="py-1">{d.device_type}</td>
                                                <td className="py-1 text-right font-mono">
                                                    {d.installed}
                                                </td>
                                                <td className="py-1 text-right font-mono text-gray-400">
                                                    {d.used} lic
                                                </td>
                                            </tr>
                                        ))}
                                </tbody>
                            </table>
                        </>
                    )}
                </div>
            </div>

            {/* Extension planner, driven by the roster selection below */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 mb-5">
                <div className="flex items-center gap-2 mb-1">
                    <CalendarClock size={16} className="text-gray-400" />
                    <div className="text-sm font-semibold text-gray-700">
                        Extension planner
                    </div>
                </div>
                <div className="text-xs text-gray-400 mb-4">
                    How far the selected customers could be renewed, using pool capacity
                    not committed to anyone else. Selecting a customer releases what they
                    hold today, so a group can often reach further than any of its members
                    expires now.
                </div>
                <div className="mb-4 pb-4 border-b border-gray-100">
                    <WhatIfControls
                        items={whatIf}
                        asOf={data.as_of}
                        demand={demandModel}
                        onAdd={(draft) =>
                            setWhatIf((prev) => [
                                ...prev,
                                { ...draft, id: prev.reduce((m, w) => Math.max(m, w.id), 0) + 1 },
                            ])
                        }
                        onRemove={(id) => setWhatIf((prev) => prev.filter((w) => w.id !== id))}
                    />
                    {whatIf.length > 0 && (
                        <div className="text-xs text-blue-700 mt-2">
                            Included in the projection below. "Licensed capacity over
                            time" at the top of the page still shows only what you own.
                        </div>
                    )}
                </div>

                {/* Demand model — three independent levers driving the projection
                    below and the extension maths. */}
                <div className="mb-4 pb-4 border-b border-gray-100">
                    <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">
                        Demand model
                    </div>
                    <div className="flex flex-wrap items-end gap-x-6 gap-y-3 text-sm">
                        <div>
                            <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">
                                Base
                            </div>
                            <div className="flex items-center gap-1.5">
                                <input
                                    type="number" min={0}
                                    value={baseOverride ?? (data.assigned_total ?? 0)}
                                    aria-label="Base licenses in play today"
                                    onChange={(e) =>
                                        setBaseOverride(Math.max(0, Number(e.target.value) || 0))
                                    }
                                    className={`w-24 font-mono ${FIELD}`}
                                />
                                <span className="text-gray-500 text-xs">licenses today</span>
                                {baseOverride !== null &&
                                    baseOverride !== (data.assigned_total ?? 0) && (
                                        <button
                                            onClick={() => setBaseOverride(null)}
                                            className="text-xs text-gray-400 hover:text-gray-700 underline"
                                        >
                                            reset
                                        </button>
                                    )}
                            </div>
                        </div>

                        <div>
                            <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
                                Run rate
                            </div>
                            <div className="flex items-center gap-1.5">
                                <span className="text-gray-400 font-mono">+</span>
                                <input
                                    type="number" min={0} step={1} value={runRate}
                                    aria-label="New licenses won per month"
                                    onChange={(e) => setRunRate(Math.max(0, Number(e.target.value) || 0))}
                                    className={`w-20 font-mono ${FIELD}`}
                                />
                                <span className="text-gray-500 text-xs">new / month</span>
                            </div>
                        </div>

                        <div>
                            <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
                                Churn
                            </div>
                            <div className="flex items-center gap-1.5">
                                <span className="text-gray-400 font-mono">−</span>
                                <input
                                    type="number" min={0} max={100} step={0.1} value={churnPct}
                                    aria-label="Percent of licenses lost per month"
                                    onChange={(e) =>
                                        setChurnPct(Math.min(100, Math.max(0, Number(e.target.value) || 0)))
                                    }
                                    className={`w-20 font-mono ${FIELD}`}
                                />
                                <span className="text-gray-500 text-xs">% / month</span>
                            </div>
                        </div>

                        {(runRate > 0 || churnPct > 0) && (
                            <div className="text-xs text-gray-500 pb-1">
                                {demandModel.base.toLocaleString()} today →{" "}
                                <strong className="text-gray-900">
                                    {demandAt(
                                        demandModel, data.as_of,
                                        pool.last_expiration ?? data.as_of,
                                    ).toLocaleString()}
                                </strong>{" "}
                                by {fmtDate(pool.last_expiration)}
                                {steadyState(demandModel) !== null && (
                                    <span className="text-gray-400">
                                        {" "}· settles at{" "}
                                        {steadyState(demandModel)!.toLocaleString()} once
                                        churn and new business balance
                                    </span>
                                )}
                                <button
                                    onClick={() => { setRunRate(0); setChurnPct(0); }}
                                    className="ml-2 underline hover:text-gray-800"
                                >
                                    clear
                                </button>
                            </div>
                        )}
                    </div>
                    <div className="text-[11px] text-gray-400 mt-2">
                        Churn compounds monthly on everything held, new business included,
                        so a high run rate cannot outrun it.
                    </div>
                </div>


                {/* Same chart as "what we have today", projected forward: the pool
                    including hypothetical purchases, against modelled demand. */}
                {projected && (
                    <div className="mb-4 pb-4 border-b border-gray-100">
                        <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
                            Projected capacity over time
                        </div>
                        <div className="text-xs text-gray-400 mb-3">
                            The pool you would hold — selected blocks plus anything bought
                            above — against demand under the model.
                        </div>
                        <PoolTimelineChart
                            blocks={livePool as any}
                            cliffs={projected.cliffs}
                            timeline={projected.timeline}
                            combined={projected.combined}
                            optimized={null}
                            mode="actual"
                            showCommitted={false}
                            estateDemand={data.assigned_total ?? 0}
                            demand={demandModel}
                            asOf={data.as_of}
                        />
                    </div>
                )}

                {/* What meeting that demand curve actually costs in orders */}
                {purchasePlan && (
                    <div className="mb-4 pb-4 border-b border-gray-100">
                        <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
                            <div className="text-xs uppercase tracking-wide text-gray-500">
                                Required purchases
                            </div>
                            <div className="flex items-center gap-2 text-xs print:hidden">
                                <span className="text-gray-500">through</span>
                                <select
                                    value={planYears ?? ""}
                                    aria-label="Planning horizon"
                                    onChange={(e) =>
                                        setPlanYears(e.target.value === "" ? null : Number(e.target.value))
                                    }
                                    className={FIELD}
                                >
                                    <option value="">end of current pool</option>
                                    {[1, 2, 3, 5, 7, 10].map((y) => (
                                        <option key={y} value={y}>
                                            {y} year{y === 1 ? "" : "s"} out
                                        </option>
                                    ))}
                                </select>
                                <span className="text-gray-500">ordering</span>
                                <select
                                    value={planCadence}
                                    aria-label="Ordering cadence"
                                    onChange={(e) => setPlanCadence(e.target.value as Cadence)}
                                    className={FIELD}
                                >
                                    {(Object.keys(CADENCE_LABEL) as Cadence[]).map((c) => (
                                        <option key={c} value={c}>
                                            every {CADENCE_LABEL[c]}
                                        </option>
                                    ))}
                                </select>
                                <span className="text-gray-500">on a</span>
                                <select
                                    value={planTerm}
                                    aria-label="Term for required purchases"
                                    onChange={(e) => setPlanTerm(Number(e.target.value))}
                                    className={FIELD}
                                >
                                    {[1, 2, 3, 4, 5].map((y) => (
                                        <option key={y} value={y}>{y} year</option>
                                    ))}
                                </select>
                                <span className="text-gray-500">term, activating every</span>
                                <select
                                    value={planStagger}
                                    aria-label="Activation stagger"
                                    onChange={(e) => setPlanStagger(Number(e.target.value))}
                                    className={FIELD}
                                >
                                    {[30, 45, 60, 90, 180].map((d) => (
                                        <option key={d} value={d}>{d} days</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                        <div className="text-xs text-gray-400 mb-3">
                            Orders needed to hold the demand line through{" "}
                            {fmtDate(purchasePlan.horizon)}, counting what you own, anything
                            bought above, and each order's own expiry. Each order is broken
                            into chunks that switch on as demand reaches them, using the{" "}
                            {GRACE_WINDOW_DAYS}-day window so capacity tracks demand instead
                            of sitting above it. The chart may still show a deficit past the
                            horizon — nothing is provisioned beyond it.
                        </div>

                        {purchasePlan.alreadyCovered ? (
                            <div className="text-sm text-gray-600 py-3">
                                Nothing to buy — what you hold covers the demand model all
                                the way to {fmtDate(purchasePlan.horizon)}.
                            </div>
                        ) : (
                            <>
                                <div className="flex flex-wrap items-end gap-6 mb-3">
                                    <div>
                                        <div className="text-2xl font-semibold font-mono text-gray-900">
                                            {purchasePlan.totalLicenses.toLocaleString()}
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            licenses across {new Set(purchasePlan.purchases.map((p) => p.purchaseDate)).size} order
                                            {new Set(purchasePlan.purchases.map((p) => p.purchaseDate)).size === 1 ? "" : "s"}
                                            {", "}
                                            {purchasePlan.purchases.length} activation
                                            {purchasePlan.purchases.length === 1 ? "" : "s"}
                                        </div>
                                    </div>
                                    <div className="text-sm">
                                        <span className="font-mono font-semibold text-gray-900">
                                            {purchasePlan.totalReplacing.toLocaleString()}
                                        </span>{" "}
                                        <span className="text-gray-500">
                                            replacing licenses that lapse
                                        </span>
                                        <span className="text-gray-300"> · </span>
                                        <span className="font-mono font-semibold text-gray-900">
                                            {purchasePlan.totalForGrowth.toLocaleString()}
                                        </span>{" "}
                                        <span className="text-gray-500">for growth</span>
                                        <div className="text-xs text-gray-400 mt-0.5">
                                            {purchasePlan.idleLicenseDays.toLocaleString()}{" "}
                                            license-days carried above demand — the cost of
                                            switching on before it is needed
                                        </div>
                                    </div>
                                    <button
                                        onClick={() =>
                                            setWhatIf((prev) => {
                                                let id = prev.reduce((m, w) => Math.max(m, w.id), 0);
                                                return [
                                                    ...prev,
                                                    ...purchasePlan.purchases.map((p) => ({
                                                        id: ++id,
                                                        kind: "once" as const,
                                                        quantity: p.quantity,
                                                        termYears: p.termYears,
                                                        startDate: p.purchaseDate,
                                                        deferDays: p.deferDays,
                                                        cadence: "annual" as Cadence,
                                                        periods: 1,
                                                        tranches: 1,
                                                        intervalDays: 30,
                                                    })),
                                                ];
                                            })
                                        }
                                        className="px-3 py-1.5 rounded bg-blue-600 text-white text-xs font-semibold hover:bg-blue-700 print:hidden"
                                        title="Adds each order above as a purchase, so the chart shows the gap closing"
                                    >
                                        Add these to the plan
                                    </button>
                                </div>

<table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-xs uppercase tracking-wide text-gray-400 text-left">
                                            <th className="pb-2 pr-4 font-medium">Order placed</th>
                                            <th className="pb-2 pr-4 font-medium">Activates</th>
                                            <th className="pb-2 pr-4 font-medium text-right">Defer</th>
                                            <th className="pb-2 pr-4 font-medium text-right">Licenses</th>
                                            <th className="pb-2 pr-4 font-medium text-right">Replacing</th>
                                            <th className="pb-2 pr-4 font-medium text-right">Growth</th>
                                            <th className="pb-2 pr-4 font-medium">Expires</th>
                                            <th className="pb-2 font-medium text-right">Demand then</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {purchasePlan.purchases.map((p, idx, arr) => {
                                            // Chunks belong to an order; only the first
                                            // of each repeats the order date so the
                                            // grouping is readable at a glance.
                                            const newOrder =
                                                idx === 0 || arr[idx - 1].purchaseDate !== p.purchaseDate;
                                            return (
                                                <tr
                                                    key={idx}
                                                    className={`text-gray-700 ${
                                                        newOrder ? "border-t border-gray-200" : ""
                                                    }`}
                                                >
                                                    <td className="py-1.5 pr-4">
                                                        {newOrder ? (
                                                            <span className="font-medium text-gray-900">
                                                                {fmtDate(p.purchaseDate)}
                                                            </span>
                                                        ) : (
                                                            <span className="text-gray-300">↳</span>
                                                        )}
                                                    </td>
                                                    <td className="py-1.5 pr-4">{fmtDate(p.startDate)}</td>
                                                    <td className="py-1.5 pr-4 text-right font-mono text-gray-500">
                                                        {p.deferDays ? `+${p.deferDays}d` : "—"}
                                                    </td>
                                                    <td className="py-1.5 pr-4 text-right font-mono font-semibold">
                                                        {p.quantity.toLocaleString()}
                                                    </td>
                                                    <td className="py-1.5 pr-4 text-right font-mono text-gray-500">
                                                        {p.replacing ? p.replacing.toLocaleString() : "—"}
                                                    </td>
                                                    <td className="py-1.5 pr-4 text-right font-mono text-gray-500">
                                                        {p.forGrowth ? p.forGrowth.toLocaleString() : "—"}
                                                    </td>
                                                    <td className="py-1.5 pr-4">{fmtDate(p.expires)}</td>
                                                    <td className="py-1.5 text-right font-mono text-gray-500">
                                                        {p.demandThen.toLocaleString()}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </>
                        )}
                    </div>
                )}

                {unpurchased > 0 && (
                    <div className="mb-3 text-xs text-gray-500">
                        {unpurchased} planned purchase{unpurchased === 1 ? "" : "s"} above
                        {unpurchased === 1 ? " is" : " are"} not yet placed, so
                        {unpurchased === 1 ? " it is" : " they are"} excluded from the
                        extension figures below — a customer cannot be committed to a term
                        nothing has been ordered for.
                    </div>
                )}

                {plan && (
                    <ExtensionPlanner
                        ecs={data.ecs}
                        plan={plan}
                        required={requiredQty}
                        onRequiredChange={setQtyOverride}
                        selectedCount={selected.size}
                        asOf={data.as_of}
                        earliest={earliest}
                        demand={demandModel}
                    />
                )}
            </div>

            {/* EC roster */}
            <div className="bg-white rounded-xl border border-gray-200 p-4 mb-5">
                <div className="flex items-center justify-between mb-3 gap-3">
                    <div className="text-sm font-semibold text-gray-700">
                        End customers ({data.ecs.length}) — soonest to lapse first
                    </div>
                    <div className="flex items-center gap-2">
                        {selected.size > 0 && (
                            <button
                                onClick={() => {
                                    setSelected(new Set());
                                    setQtyOverride(null);
                                }}
                                className="text-xs text-gray-500 hover:text-gray-800 underline print:hidden"
                            >
                                clear {selected.size} selected
                            </button>
                        )}
                        <button
                            onClick={() => {
                                setSelected(new Set(data.ecs.map((e) => e.tenant_id)));
                                setQtyOverride(null);
                            }}
                            className="text-xs px-2 py-1 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 print:hidden"
                        >
                            Select all
                        </button>
                        <input
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder="Filter by name…"
                            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 w-48 focus:outline-none focus:border-blue-400 print:hidden"
                        />
                    </div>
                </div>
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-xs uppercase tracking-wide text-gray-400 text-left">
                            <th className="pb-2 pl-2 font-medium w-8">
                                <input
                                    type="checkbox"
                                    checked={allVisibleSelected}
                                    onChange={() => {
                                        setQtyOverride(null);
                                        setSelected((prev) => {
                                            const next = new Set(prev);
                                            if (allVisibleSelected) {
                                                ecs.forEach((e) => next.delete(e.tenant_id));
                                            } else {
                                                ecs.forEach((e) => next.add(e.tenant_id));
                                            }
                                            return next;
                                        });
                                    }}
                                    className="cursor-pointer"
                                    aria-label="Select all visible customers"
                                />
                            </th>
                            <th className="pb-2 pr-3 font-medium">Customer</th>
                            <th className="pb-2 pr-3 font-medium text-right">Qty</th>
                            <th className="pb-2 pr-3 font-medium">Effective expiration</th>
                            <th className="pb-2 pr-3 font-medium">Remaining</th>
                            <th className="pb-2 pr-3 font-medium">Could reach alone</th>
                            <th className="pb-2 pr-3 font-medium">Types</th>
                            <th className="pb-2 pr-2 font-medium text-right">History</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ecs.map((ec) => (
                            <EcRow
                                key={ec.tenant_id}
                                ec={ec}
                                selected={selected.has(ec.tenant_id)}
                                onToggle={() => {
                                    setQtyOverride(null);
                                    toggle(ec.tenant_id);
                                }}
                                maxAlone={maxAlone.get(ec.tenant_id) ?? null}
                            />
                        ))}
                    </tbody>
                </table>
                {!ecs.length && (
                    <div className="text-sm text-gray-400 py-6 text-center">
                        No end customers match "{filter}".
                    </div>
                )}
            </div>

            {/* Optimal allocation */}
            {alloc && alloc.rows.length > 0 && (
                <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="flex items-center gap-2 mb-1">
                        <Shuffle size={16} className="text-gray-400" />
                        <div className="text-sm font-semibold text-gray-700">
                            Optimal allocation
                        </div>
                        {whatIf.length > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-900 text-[11px]">
                                includes hypothetical purchases
                            </span>
                        )}
                    </div>
                    <div className="text-xs text-gray-400 mb-4">
                        If every customer were reassigned today, packing the smallest into
                        the longest-dated blocks first. Utilization is measured in
                        license-days — licenses multiplied by the term left on them.
                    </div>
                    <AllocationPanel alloc={alloc} />
                </div>
            )}
        </div>
    );
}

export default MspLicensing;
