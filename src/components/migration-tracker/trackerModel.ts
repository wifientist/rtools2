/**
 * Migration Tracker — shared types and date helpers.
 *
 * Dates travel as "YYYY-MM-DD" strings and are compared as strings; they are
 * only turned into Date objects (local midnight) for labels and arithmetic, so
 * a site never shifts a day because of the viewer's timezone.
 */

/**
 * Something that moved a site's dates, on `date`.
 *
 * validation_failed — a retry loop: re-config on reconfig_on, and validation
 *   re-runs that same day. Entries from before retry loops existed have
 *   slip_days and no reconfig_on.
 * config_missed — config didn't happen on `date` and moved to
 *   new_config; pushed_later says whether validation + HW moved with it.
 * migration_missed — the HW swap didn't happen on `date` and moved to
 *   new_hw. Nothing comes after it, so nothing else moves.
 */
export interface TrackerEvent {
  date: string;
  kind: "validation_failed" | "config_missed" | "migration_missed";
  note: string | null;
  original_validation?: string | null;
  reconfig_on?: string | null;
  reconfig_done?: boolean;
  slip_days?: number;
  original_config?: string;
  new_config?: string;
  pushed_later?: boolean;
  original_hw?: string;
  new_hw?: string;
  by?: string;
  at?: string;
}

export interface TrackerSite {
  id: number;
  site_name: string;
  vsz: string | null;
  tenant: string | null;
  sz_ap_count: number | null;
  sz_wlan_count: number | null;
  category: string | null;
  config_pushed: string | null;
  config_pushed_done: boolean;
  validation: string | null;
  validation_done: boolean;
  hw_migrated: string | null;
  hw_migrated_done: boolean;
  notes: string | null;
  events: TrackerEvent[];
  updated_at: string | null;
  updated_by: string | null;
}

export type EditableField = Exclude<keyof TrackerSite, "id" | "events" | "updated_at" | "updated_by">;

export type StepKey = "config_pushed" | "validation" | "hw_migrated";

export const STEPS: { key: StepKey; doneKey: `${StepKey}_done`; label: string; short: string; color: string }[] = [
  { key: "config_pushed", doneKey: "config_pushed_done", label: "Config pushed", short: "C", color: "sky" },
  { key: "validation", doneKey: "validation_done", label: "Validation", short: "V", color: "amber" },
  { key: "hw_migrated", doneKey: "hw_migrated_done", label: "HW migrated", short: "M", color: "emerald" },
];

// Full class strings so Tailwind's scanner keeps them.
export const STEP_STYLES: Record<string, { done: string; planned: string; text: string; dot: string }> = {
  sky: {
    done: "bg-sky-600 border-sky-600 text-white",
    planned: "bg-white border-sky-500 text-sky-700",
    text: "text-sky-700",
    dot: "bg-sky-600",
  },
  amber: {
    done: "bg-amber-500 border-amber-500 text-white",
    planned: "bg-white border-amber-500 text-amber-700",
    text: "text-amber-700",
    dot: "bg-amber-500",
  },
  emerald: {
    done: "bg-emerald-600 border-emerald-600 text-white",
    planned: "bg-white border-emerald-500 text-emerald-700",
    text: "text-emerald-700",
    dot: "bg-emerald-600",
  },
};

export function parseISO(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function toISO(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

export function todayISO(): string {
  return toISO(new Date());
}

/** The next Monday–Friday after d (holidays aren't modelled). Mirrors the API. */
export function nextWorkday(d: Date): Date {
  let out = addDays(d, 1);
  while (out.getDay() === 0 || out.getDay() === 6) out = addDays(out, 1);
  return out;
}

export function onOrAfterWorkday(d: Date): Date {
  return d.getDay() === 0 || d.getDay() === 6 ? nextWorkday(d) : d;
}

function addWorkdaysISO(iso: string, n: number): string {
  let d = parseISO(iso);
  for (let i = 0; i < n; i++) d = nextWorkday(d);
  return toISO(d);
}

/** Workdays in (a, b]. */
function workdaysBetween(a: string, b: string): number {
  let n = 0;
  for (let d = parseISO(a); toISO(d) < b;) {
    d = addDays(d, 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) n++;
  }
  return n;
}

/** The default new date after a missed config or migration: the next workday. Mirrors the API.
 *  Not clamped to today, so back-filling an old miss lands where it really went. */
export function defaultNewDate(missedOn: string): string {
  return toISO(nextWorkday(parseISO(missedOn)));
}

/** Where validation and HW land after a missed config. Mirrors the API's miss-config. */
export function previewMissConfig(
  site: TrackerSite, missedOn: string, newConfig: string, pushLater: boolean,
): { validation: string | null; hw_migrated: string | null } {
  let validation = site.validation;
  let hw = site.hw_migrated;
  if (pushLater) {
    const shift = workdaysBetween(site.config_pushed ?? missedOn, newConfig);
    if (validation) validation = addWorkdaysISO(validation, shift);
    if (hw) hw = addWorkdaysISO(hw, shift);
  }
  if (validation && validation < newConfig) validation = newConfig;
  const hwFloor = toISO(nextWorkday(parseISO(validation ?? newConfig)));
  if (hw && hw < hwFloor) hw = hwFloor;
  return { validation, hw_migrated: hw };
}

/** Monday of the week containing d. */
export function startOfWeek(d: Date): Date {
  const out = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  out.setDate(out.getDate() - ((out.getDay() + 6) % 7));
  return out;
}

export function fmtShort(iso: string | null): string {
  if (!iso) return "—";
  return parseISO(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function stepDates(site: TrackerSite): string[] {
  return STEPS.map((s) => site[s.key]).filter((d): d is string => !!d);
}

/** Every date the chart draws for a site: steps, failures, re-configs and where validation used to be. */
export function markerDates(site: TrackerSite): string[] {
  const dates = stepDates(site);
  for (const e of site.events) {
    dates.push(e.date);
    if (e.reconfig_on) dates.push(e.reconfig_on);
    if (e.original_validation) dates.push(e.original_validation);
  }
  return dates.sort();
}

export function isOverdue(site: TrackerSite, step: (typeof STEPS)[number], today: string): boolean {
  const d = site[step.key];
  return !!d && d < today && !site[step.doneKey];
}

export function isReconfigOverdue(e: TrackerEvent, today: string): boolean {
  return !!e.reconfig_on && e.reconfig_on < today && !e.reconfig_done;
}

export function pendingReconfig(site: TrackerSite): TrackerEvent | undefined {
  return site.events.find((e) => e.reconfig_on && !e.reconfig_done);
}

export type Status = { label: string; tone: "green" | "blue" | "amber" | "red" | "gray" | "sky"; rank: number };

export function siteStatus(site: TrackerSite, today: string): Status {
  if (site.hw_migrated_done) return { label: "Migrated", tone: "green", rank: 5 };
  if (STEPS.some((s) => isOverdue(site, s, today)) || site.events.some((e) => isReconfigOverdue(e, today))) {
    return { label: "Overdue", tone: "red", rank: 0 };
  }
  if (site.validation_done) return { label: "Validated", tone: "blue", rank: 3 };
  if (pendingReconfig(site)) return { label: "Re-config", tone: "amber", rank: 1 };
  if (site.events.some((e) => e.kind === "validation_failed")) return { label: "Re-validating", tone: "amber", rank: 1.5 };
  if (site.config_pushed_done) return { label: "Config pushed", tone: "sky", rank: 2 };
  if (stepDates(site).length) return { label: "Scheduled", tone: "gray", rank: 4 };
  return { label: "Not scheduled", tone: "gray", rank: 6 };
}

export const TONE_CLASSES: Record<Status["tone"], string> = {
  green: "bg-green-100 text-green-800",
  blue: "bg-blue-100 text-blue-800",
  sky: "bg-sky-100 text-sky-800",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800",
  gray: "bg-gray-100 text-gray-700",
};

// ---------- Ordering ----------

/**
 * Where a site is in the pipeline, in the order the chart lists them: done
 * (collapsed by default) on top, then furthest along to least.
 */
export const STAGES = [
  { key: "migrated", label: "Migrated" },
  { key: "migrating", label: "Migrating next" },
  { key: "validation", label: "Pending validation" },
  { key: "config", label: "Pending config" },
  { key: "unscheduled", label: "Not scheduled" },
] as const;

export type StageKey = (typeof STAGES)[number]["key"];

export function stageOf(site: TrackerSite): StageKey {
  if (site.hw_migrated_done) return "migrated";
  if (site.validation_done) return "migrating";
  if (site.config_pushed_done || site.events.some((e) => e.kind === "validation_failed")) return "validation";
  if (stepDates(site).length) return "config";
  return "unscheduled";
}

const NO_DATE = "9999-12-31";

/** The date that decides a site's place within its stage. */
function stageDate(site: TrackerSite, stage: StageKey): string {
  switch (stage) {
    case "migrating":
    case "migrated": return site.hw_migrated ?? NO_DATE;
    case "validation": return pendingReconfig(site)?.reconfig_on ?? site.validation ?? NO_DATE;
    case "config": return site.config_pushed ?? site.validation ?? site.hw_migrated ?? NO_DATE;
    default: return NO_DATE;
  }
}

/** Stage first, then earliest date, then HW date, then name — the same direction in every stage. */
export function compareSites(a: TrackerSite, b: TrackerSite): number {
  const sa = stageOf(a);
  const sb = stageOf(b);
  const rank = (k: StageKey) => STAGES.findIndex((s) => s.key === k);
  if (sa !== sb) return rank(sa) - rank(sb);
  return stageDate(a, sa).localeCompare(stageDate(b, sb))
    || (a.hw_migrated ?? NO_DATE).localeCompare(b.hw_migrated ?? NO_DATE)
    || a.site_name.localeCompare(b.site_name);
}

// ---------- Spreadsheet paste ----------

const PASTE_COLUMNS: { field: EditableField; aliases: string[] }[] = [
  { field: "site_name", aliases: ["site", "site name", "property", "name", "venue"] },
  { field: "vsz", aliases: ["vsz", "current vsz", "sz", "controller", "cluster"] },
  { field: "tenant", aliases: ["tenant", "ec", "r1 tenant"] },
  { field: "sz_ap_count", aliases: ["sz ap count", "ap count", "aps", "ap"] },
  { field: "sz_wlan_count", aliases: ["sz wlan count", "wlan count", "wlans", "wlan", "ssids"] },
  { field: "category", aliases: ["category", "type", "cat"] },
  { field: "config_pushed", aliases: ["config pushed", "config", "config push"] },
  { field: "validation", aliases: ["validation", "validated", "validate"] },
  { field: "hw_migrated", aliases: ["hw migrated", "hw migration", "hardware", "hw", "migrated", "migration"] },
  { field: "notes", aliases: ["notes", "note", "comments", "comment"] },
];

const DATE_FIELDS: EditableField[] = ["config_pushed", "validation", "hw_migrated"];
const INT_FIELDS: EditableField[] = ["sz_ap_count", "sz_wlan_count"];

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

/**
 * "Monday | 26 OCT" (the tracker sheet's format), also "Mon 26 Oct 2026",
 * "26 October". Without a year, take the year that puts the date nearest
 * today — and when a weekday is given, only years where it actually falls
 * on that weekday, so "Mon | 4 JAN" pasted in late December lands right.
 */
function parseDayMonthName(s: string): string | null {
  const m = s.match(/^(?:([a-z]{3,9})\.?\s*[|,\-–]?\s*)?(\d{1,2})\s+([a-z]{3,9})\.?(?:\s+(\d{2,4}))?$/i);
  if (!m) return null;
  const month = MONTHS.indexOf(m[3].slice(0, 3).toLowerCase());
  if (month < 0) return null;
  const day = +m[2];
  const weekday = m[1] ? WEEKDAYS.indexOf(m[1].slice(0, 3).toLowerCase()) : -1;
  if (m[1] && weekday < 0) return null;

  let years: number[];
  if (m[4]) {
    years = [+m[4] < 100 ? +m[4] + 2000 : +m[4]];
  } else {
    const y = new Date().getFullYear();
    years = [y - 1, y, y + 1];
  }
  const now = Date.now();
  const candidates = years
    .map((y) => new Date(y, month, day))
    .filter((d) => d.getDate() === day); // drop rollovers like 31 NOV
  const matching = candidates.filter((d) => weekday < 0 || d.getDay() === weekday);
  const pool = matching.length ? matching : candidates;
  if (!pool.length) return null;
  pool.sort((a, b) => Math.abs(a.getTime() - now) - Math.abs(b.getTime() - now));
  return toISO(pool[0]);
}

/** Accepts 2026-10-27, 10/27/2026, 10/27/26, 10/27 (this year) and "Monday | 26 OCT". */
export function parseLooseDate(raw: string): string | null {
  const s = raw.trim();
  if (!s) return null;
  const named = parseDayMonthName(s);
  if (named) return named;
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return toISO(new Date(+m[1], +m[2] - 1, +m[3]));
  m = s.match(/^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$/);
  if (m) {
    let y = m[3] ? +m[3] : new Date().getFullYear();
    if (y < 100) y += 2000;
    return toISO(new Date(y, +m[1] - 1, +m[2]));
  }
  // Last resort for things like "Oct 27, 2026". Browsers fill a missing year
  // with 2001, so anything far from now is a misread, not a date.
  const d = new Date(s);
  if (isNaN(d.getTime()) || Math.abs(d.getFullYear() - new Date().getFullYear()) > 2) return null;
  return toISO(d);
}

export interface PasteResult {
  rows: Partial<Record<EditableField, string | number | null>>[];
  columns: EditableField[];
  hadHeader: boolean;
  warnings: string[];
}

/**
 * Tab-separated rows straight out of Excel/Sheets. If the first row looks like
 * a header, columns are matched by name; otherwise they're taken in the same
 * order as the table on this page.
 */
export function parsePaste(text: string): PasteResult {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const cells = lines.map((l) => l.split("\t"));
  const norm = (s: string) => s.trim().toLowerCase().replace(/[_#]/g, " ").replace(/\s+/g, " ");
  const lookup = (h: string) => PASTE_COLUMNS.find((c) => c.aliases.includes(norm(h)))?.field;

  const headerHits = cells.length ? cells[0].filter((h) => lookup(h)).length : 0;
  const hadHeader = headerHits >= 2;
  const columns: (EditableField | undefined)[] = hadHeader
    ? cells[0].map(lookup)
    : PASTE_COLUMNS.map((c) => c.field);
  const body = hadHeader ? cells.slice(1) : cells;
  const warnings: string[] = [];

  const rows = body.map((row, i) => {
    const out: Partial<Record<EditableField, string | number | null>> = {};
    row.forEach((raw, ci) => {
      const field = columns[ci];
      if (!field) return;
      const v = raw.trim();
      if (!v) return;
      if (DATE_FIELDS.includes(field)) {
        const d = parseLooseDate(v);
        if (d) out[field] = d;
        else warnings.push(`Row ${i + 1}: couldn't read "${v}" as a date`);
      } else if (INT_FIELDS.includes(field)) {
        const n = parseInt(v.replace(/,/g, ""), 10);
        if (!isNaN(n)) out[field] = n;
      } else {
        out[field] = v;
      }
    });
    return out;
  }).filter((r) => r.site_name);

  return {
    rows,
    columns: columns.filter((c): c is EditableField => !!c),
    hadHeader,
    warnings,
  };
}
