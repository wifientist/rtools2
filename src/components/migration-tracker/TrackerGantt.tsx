import { Fragment, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, ChevronDown, X, CalendarDays } from "lucide-react";
import {
  STEPS, STEP_STYLES, addDays, fmtShort, isOverdue, isReconfigOverdue, markerDates,
  STAGES, compareSites, parseISO, stageOf, type StageKey, startOfWeek, toISO, type TrackerEvent, type TrackerSite,
} from "./trackerModel";

const COLLAPSED_KEY = "tracker.gantt.collapsed";

/**
 * Two-week timeline, one line per site. The window opens on Monday of the
 * current week and steps a week at a time. A site shows when any part of its
 * config → HW span overlaps the window (or always, with "All sites").
 */
const HOME_OFFSET = -1;
const COMPACT_KEY = "tracker.gantt.compact";
const WEEKS_KEY = "tracker.gantt.weeks";

export default function TrackerGantt({
  sites, today, onSelect,
}: {
  sites: TrackerSite[];
  today: string;
  onSelect: (id: number) => void;
}) {
  // Home view is last week + this week: what just happened matters more than what's two weeks out.
  const [weekOffset, setWeekOffset] = useState(HOME_OFFSET);
  const [showAll, setShowAll] = useState(false);
  const [compact, setCompactState] = useState(() => {
    try { return localStorage.getItem(COMPACT_KEY) === "1"; } catch { return false; }
  });
  const setCompact = (on: boolean) => {
    setCompactState(on);
    try { localStorage.setItem(COMPACT_KEY, on ? "1" : "0"); } catch { /* storage unavailable */ }
  };
  const [weeks, setWeeksState] = useState<2 | 3>(() => {
    try { return localStorage.getItem(WEEKS_KEY) === "3" ? 3 : 2; } catch { return 2; }
  });
  const setWeeks = (n: 2 | 3) => {
    setWeeksState(n);
    try { localStorage.setItem(WEEKS_KEY, String(n)); } catch { /* storage unavailable */ }
  };
  const DAYS = weeks * 7;
  // Marker sizes for each density.
  const sz = compact
    ? { dot: "w-4 h-4 text-[9px] border", badge: "-top-1 -right-1 w-2.5 h-2.5", badgeX: 7,
        fail: "w-3.5 h-3.5", failX: 9, ring: "ring-1 ring-red-500", sup: "text-[6px]" }
    : { dot: "w-6 h-6 text-[11px] border-2", badge: "-top-1.5 -right-1.5 w-3.5 h-3.5", badgeX: 9,
        fail: "w-5 h-5", failX: 12, ring: "ring-2 ring-red-500 ring-offset-1", sup: "text-[8px]" };
  const [collapsed, setCollapsed] = useState<Set<StageKey>>(() => {
    try {
      const saved = localStorage.getItem(COLLAPSED_KEY);
      if (saved) return new Set(JSON.parse(saved) as StageKey[]);
    } catch { /* storage unavailable */ }
    return new Set<StageKey>(["migrated"]);
  });
  const toggleGroup = (stage: StageKey) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(stage)) next.delete(stage);
      else next.add(stage);
      try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next])); } catch { /* storage unavailable */ }
      return next;
    });

  const start = addDays(startOfWeek(parseISO(today)), weekOffset * 7);
  const days = Array.from({ length: DAYS }, (_, i) => toISO(addDays(start, i)));
  const winStart = days[0];
  const winEnd = days[DAYS - 1];

  const rows = useMemo(() => {
    return sites
      .map((site) => {
        const dates = markerDates(site);
        return { site, spanStart: dates[0] ?? null, spanEnd: dates[dates.length - 1] ?? null };
      })
      .filter(({ spanStart, spanEnd }) =>
        showAll || (spanStart !== null && spanStart <= winEnd && spanEnd! >= winStart))
      .sort((a, b) => compareSites(a.site, b.site));
  }, [sites, showAll, winStart, winEnd]);

  const hidden = sites.length - rows.length;
  // Sat and Sun get half a column each, so a weekend takes one weekday's width.
  const gridCols = {
    gridTemplateColumns: `minmax(170px, 220px) ${days
      .map((d) => ([0, 6].includes(parseISO(d).getDay()) ? "minmax(26px, 0.5fr)" : "minmax(44px, 1fr)"))
      .join(" ")}`,
  };
  const rangeLabel = `${fmtShort(winStart)} – ${fmtShort(winEnd)}`;

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-gray-200">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setWeekOffset((o) => o - 1)}
            className="p-1.5 rounded border border-gray-300 hover:bg-gray-50"
            title="Back one week"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            onClick={() => setWeekOffset(HOME_OFFSET)}
            disabled={weekOffset === HOME_OFFSET}
            className="px-2.5 py-1 rounded border border-gray-300 text-sm hover:bg-gray-50 disabled:opacity-50 disabled:hover:bg-white"
          >
            This week
          </button>
          <button
            onClick={() => setWeekOffset((o) => o + 1)}
            className="p-1.5 rounded border border-gray-300 hover:bg-gray-50"
            title="Forward one week"
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <span className="font-semibold text-gray-800 flex items-center gap-1.5">
          <CalendarDays size={16} className="text-gray-500" /> {rangeLabel}
        </span>
        <Legend />
        <div className="ml-auto flex items-center gap-3">
        <Segmented value={weeks} onChange={setWeeks} options={[[2, "2 wk"], [3, "3 wk"]]} />
        <Segmented value={compact} onChange={setCompact} options={[[false, "Comfortable"], [true, "Compact"]]} />
        <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          All sites{!showAll && hidden > 0 && <span className="text-gray-400">({hidden} outside window)</span>}
        </label>
        </div>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[720px]">
          {/* Day header */}
          <div className="grid border-b border-gray-200 text-xs" style={gridCols}>
            <div className="px-3 py-2 font-medium text-gray-500">Site</div>
            {days.map((d) => {
              const date = parseISO(d);
              const weekend = date.getDay() === 0 || date.getDay() === 6;
              const isToday = d === today;
              return (
                <div
                  key={d}
                  className={`py-1.5 text-center border-l border-gray-100 ${
                    isToday ? "bg-blue-100 text-blue-800 font-bold" : weekend ? "bg-gray-50 text-gray-400" : "text-gray-600"
                  } ${date.getDay() === 1 ? "border-l-gray-300" : ""}`}
                >
                  <div className={weekend ? "text-[10px]" : ""}>{date.toLocaleDateString(undefined, { weekday: "short" })}</div>
                  <div className={weekend ? "text-xs" : "text-sm"}>{date.getDate()}</div>
                </div>
              );
            })}
          </div>

          {rows.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-gray-500">
              {sites.length === 0
                ? "No sites yet."
                : `Nothing scheduled ${rangeLabel}. Step a week either way, or tick "All sites".`}
            </div>
          )}

          {rows.map(({ site, spanStart, spanEnd }, rowIdx) => {
            const stage = stageOf(site);
            const groupStart = rowIdx === 0 || stageOf(rows[rowIdx - 1].site) !== stage;
            const groupSize = groupStart ? rows.filter((r) => stageOf(r.site) === stage).length : 0;
            const isCollapsed = collapsed.has(stage);
            const offWindow = [...new Set(markerDates(site))];
            const earlier = offWindow.filter((d) => d < winStart);
            const later = offWindow.filter((d) => d > winEnd);
            return (
              <Fragment key={site.id}>
              {groupStart && (
                <button
                  onClick={() => toggleGroup(stage)}
                  className={`w-full flex items-center gap-1 px-2 ${compact ? "py-0.5" : "py-1"} bg-gray-50 hover:bg-gray-100 border-b border-gray-200 text-[11px] font-semibold uppercase tracking-wide text-gray-500`}
                  title={isCollapsed ? "Show these sites" : "Hide these sites"}
                >
                  {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                  {STAGES.find((s) => s.key === stage)!.label}
                  <span className="ml-0.5 font-normal normal-case tracking-normal text-gray-400">{groupSize}</span>
                </button>
              )}
              {!isCollapsed && (
              <div className="grid border-b border-gray-100 hover:bg-gray-50/60" style={gridCols}>
                <button
                  onClick={() => onSelect(site.id)}
                  className={`px-3 text-left min-w-0 ${compact ? "py-0.5" : "py-1.5"}`}
                  title="Jump to this site in the table"
                >
                  <div className={`font-medium text-gray-900 truncate ${compact ? "text-xs" : "text-sm"}`}>{site.site_name}</div>
                  {!compact && <div className="text-[11px] text-gray-500 truncate">
                    {[site.category, site.vsz || site.tenant ? `${site.vsz ?? "?"} → ${site.tenant ?? "?"}` : null]
                      .filter(Boolean).join(" · ") || " "}
                  </div>}
                </button>
                {days.map((d, i) => {
                  const date = parseISO(d);
                  const weekend = date.getDay() === 0 || date.getDay() === 6;
                  const inSpan = spanStart !== null && d >= spanStart && d <= spanEnd!;
                  const steps = STEPS.filter((s) => site[s.key] === d);
                  // Where validation sat before each failure, faded, with the ✕ on it when it failed that day.
                  const ghosts = site.events.filter((e) =>
                    e.original_validation === d && e.original_validation !== site.validation);
                  const fails = site.events.filter((e) =>
                    e.kind === "validation_failed" && e.date === d && !ghosts.some((g) => g === e));
                  const reconfigs = site.events
                    .filter((e) => e.kind === "validation_failed")
                    .map((e, idx) => ({ e, attempt: idx + 2 }))
                    .filter(({ e }) => e.reconfig_on === d);
                  // Where config or the HW swap was meant to happen before it was missed.
                  const missed = [
                    ...site.events
                      .filter((e) => e.kind === "config_missed" && e.date === d && e.date !== site.config_pushed)
                      .map((e) => ({
                        letter: "C", tone: "border-sky-400 text-sky-400",
                        title: `Config missed ${fmtShort(d)} — moved to ${fmtShort(e.new_config ?? null)}${
                          e.pushed_later ? ", validation + HW moved with it" : ""}${e.note ? `\n${e.note}` : ""}`,
                      })),
                    ...site.events
                      .filter((e) => e.kind === "migration_missed" && e.date === d && e.date !== site.hw_migrated)
                      .map((e) => ({
                        letter: "M", tone: "border-emerald-400 text-emerald-400",
                        title: `Migration missed ${fmtShort(d)} — moved to ${fmtShort(e.new_hw ?? null)}${e.note ? `\n${e.note}` : ""}`,
                      })),
                  ];
                  return (
                    <div
                      key={d}
                      className={`relative flex items-center justify-center gap-0.5 border-l border-gray-100 ${
                        d === today ? "bg-blue-50" : weekend ? "bg-gray-50" : ""
                      } ${date.getDay() === 1 ? "border-l-gray-300" : ""}`}
                    >
                      {inSpan && spanStart !== spanEnd && (
                        <div
                          className={`absolute top-1/2 -translate-y-1/2 h-1.5 bg-gray-200 ${
                            d === spanStart ? "left-1/2 right-0" : d === spanEnd ? "left-0 right-1/2" : "left-0 right-0"
                          }`}
                        />
                      )}
                      {i === 0 && earlier.length > 0 && (
                        <OffWindow side="left" text={`Earlier: ${earlier.map(fmtShort).join(", ")}`} />
                      )}
                      {i === DAYS - 1 && later.length > 0 && (
                        <OffWindow side="right" text={`Later: ${later.map(fmtShort).join(", ")}`} />
                      )}
                      {missed.map((m, k) => (
                        <span
                          key={`m${k}`}
                          className={`relative z-10 inline-flex items-center justify-center ${sz.dot} rounded-full border-dashed bg-white font-bold ${m.tone}`}
                          title={m.title}
                        >
                          {m.letter}
                          <span className={`absolute ${sz.badge} inline-flex items-center justify-center rounded-full bg-red-600 text-white`}>
                            <X size={sz.badgeX} strokeWidth={3} />
                          </span>
                        </span>
                      ))}
                      {ghosts.map((e, k) => (
                        <span
                          key={`g${k}`}
                          className={`relative z-10 inline-flex items-center justify-center ${sz.dot} rounded-full border-dashed border-amber-400 bg-white font-bold text-amber-400`}
                          title={`Validation was planned ${fmtShort(d)}${e.date === d ? " — failed" : ""}${e.note ? `\n${e.note}` : ""}`}
                        >
                          V
                          {e.date === d && (
                            <span className={`absolute ${sz.badge} inline-flex items-center justify-center rounded-full bg-red-600 text-white`}>
                              <X size={sz.badgeX} strokeWidth={3} />
                            </span>
                          )}
                        </span>
                      ))}
                      {fails.map((e, k) => (
                        <span
                          key={`f${k}`}
                          className={`relative z-10 inline-flex items-center justify-center ${sz.fail} rounded-full bg-red-600 text-white`}
                          title={failTitle(e)}
                        >
                          <X size={sz.failX} strokeWidth={3} />
                        </span>
                      ))}
                      {reconfigs.map(({ e, attempt }) => {
                        const overdue = isReconfigOverdue(e, today);
                        return (
                          <span
                            key={`r${attempt}`}
                            className={`relative z-10 inline-flex items-center justify-center ${sz.dot} rounded-full font-bold ${
                              e.reconfig_done ? STEP_STYLES.sky.done : STEP_STYLES.sky.planned
                            } ${overdue ? sz.ring : ""}`}
                            title={`Re-config #${attempt - 1} + re-validation — ${fmtShort(d)} — ${
                              e.reconfig_done ? "done" : overdue ? "overdue" : "planned"}${e.note ? `\nAfter: ${e.note}` : ""}`}
                          >
                            C<sup className={`${sz.sup} -mr-0.5`}>{attempt}</sup>
                          </span>
                        );
                      })}
                      {steps.map((s) => {
                        const done = site[s.doneKey];
                        const overdue = isOverdue(site, s, today);
                        const style = STEP_STYLES[s.color];
                        return (
                          <span
                            key={s.key}
                            className={`relative z-10 inline-flex items-center justify-center ${sz.dot} rounded-full font-bold ${
                              done ? style.done : style.planned
                            } ${overdue ? sz.ring : ""}`}
                            title={`${s.label} — ${fmtShort(d)} — ${done ? "done" : overdue ? "overdue" : "planned"}`}
                          >
                            {s.short}
                          </span>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
              )}
              </Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Segmented<T extends string | number | boolean>({
  value, onChange, options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: readonly (readonly [T, string])[];
}) {
  return (
    <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
      {options.map(([v, label]) => (
        <button
          key={label}
          onClick={() => onChange(v)}
          className={`px-2 py-1 ${value === v ? "bg-gray-700 text-white" : "text-gray-600 hover:bg-gray-50"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function failTitle(e: TrackerEvent): string {
  const after = e.reconfig_on ? ` — re-config ${fmtShort(e.reconfig_on)}`
    : e.slip_days ? ` — pushed +${e.slip_days}d` : "";
  return `Validation failed ${fmtShort(e.date)}${after}${e.note ? `\n${e.note}` : ""}`;
}

function OffWindow({ side, text }: { side: "left" | "right"; text: string }) {
  return (
    <span
      className={`absolute ${side === "left" ? "left-0" : "right-0"} top-1/2 -translate-y-1/2 z-10 text-gray-400`}
      title={text}
    >
      {side === "left" ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
    </span>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
      {STEPS.map((s) => (
        <span key={s.key} className="flex items-center gap-1">
          <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] font-bold ${STEP_STYLES[s.color].done}`}>
            {s.short}
          </span>
          {s.label}
        </span>
      ))}
      <span className="text-gray-300">|</span>
      <span className="flex items-center gap-1">
        <span className="w-3.5 h-3.5 rounded-full border-2 border-gray-400 bg-white" /> planned
      </span>
      <span className="flex items-center gap-1">
        <span className="w-3.5 h-3.5 rounded-full bg-gray-500" /> done
      </span>
      <span className="flex items-center gap-1">
        <span className="w-3.5 h-3.5 rounded-full border-2 border-gray-400 ring-2 ring-red-500 ring-offset-1" /> overdue
      </span>
      <span className="flex items-center gap-1">
        <span className="relative inline-flex items-center justify-center w-4 h-4 rounded-full border-2 border-dashed border-amber-400 text-[8px] font-bold text-amber-400">
          V
          <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-600" />
        </span>
        failed validation
      </span>
      <span className="flex items-center gap-1">
        <span className="relative inline-flex items-center justify-center w-4 h-4 rounded-full border-2 border-dashed border-sky-400 text-[8px] font-bold text-sky-400">
          C
          <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-600" />
        </span>
        missed config
      </span>
      <span className="flex items-center gap-1">
        <span className="relative inline-flex items-center justify-center w-4 h-4 rounded-full border-2 border-dashed border-emerald-400 text-[8px] font-bold text-emerald-400">
          M
          <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-600" />
        </span>
        missed migration
      </span>
      <span className="flex items-center gap-1">
        <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[8px] font-bold ${STEP_STYLES.sky.done}`}>
          C<sup className="text-[6px]">2</sup>
        </span>
        re-config + re-validate
      </span>
    </div>
  );
}
