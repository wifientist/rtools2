import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Plus, ClipboardPaste, AlertTriangle } from "lucide-react";
import { apiFetch } from "@/utils/api";
import TrackerGantt from "@/components/migration-tracker/TrackerGantt";
import TrackerTable, { type EventPatch, type FailBody, type MissBody, type MissMigrationBody } from "@/components/migration-tracker/TrackerTable";
import {
  STAGES, TONE_CLASSES, parsePaste, stageOf, siteStatus, todayISO,
  type PasteResult, type TrackerSite,
} from "@/components/migration-tracker/trackerModel";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const BASE = `${API_BASE_URL}/tracker/sites`;
const FILTER_KEY = "tracker.table.filter";

type TableFilter = "active" | "overdue" | "all" | (typeof STAGES)[number]["key"];

function matchesFilter(site: TrackerSite, filter: TableFilter, today: string): boolean {
  if (filter === "all") return true;
  if (filter === "active") return stageOf(site) !== "migrated";
  if (filter === "overdue") return siteStatus(site, today).label === "Overdue";
  return stageOf(site) === filter;
}

/**
 * Tracker — one shared board for the last few site migrations, in place of
 * the spreadsheet that gets pasted back and forth. A two- or three-week timeline up top,
 * the full list below. Admins edit in place; everyone else reads.
 *
 * Human-driven only: the page loads once and on Refresh. No polling.
 */
export default function MigrationTracker() {
  const [sites, setSites] = useState<TrackerSite[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const [filter, setFilterState] = useState<TableFilter>(() => {
    try { return (localStorage.getItem(FILTER_KEY) as TableFilter) || "active"; } catch { return "active"; }
  });
  const setFilter = (f: TableFilter) => {
    setFilterState(f);
    try { localStorage.setItem(FILTER_KEY, f); } catch { /* storage unavailable */ }
  };
  const today = todayISO();

  const call = useCallback(async (url: string, init?: RequestInit) => {
    const res = await apiFetch(url, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await call(BASE);
      setSites(data.sites);
      setCanEdit(data.can_edit);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [call]);

  useEffect(() => { void load(); }, [load]);

  const replace = (site: TrackerSite) => setSites((prev) => prev.map((s) => (s.id === site.id ? site : s)));

  // Every mutation returns the saved site; on failure, surface it and leave the row as the server has it.
  const guard = async (fn: () => Promise<void>) => {
    try {
      await fn();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
      void load();
    }
  };

  const onPatch = (id: number, patch: Partial<TrackerSite>) => guard(async () => {
    setSites((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    replace(await call(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify(patch) }));
  });
  const onFailValidation = (id: number, body: FailBody) => guard(async () => {
    replace(await call(`${BASE}/${id}/fail-validation`, { method: "POST", body: JSON.stringify(body) }));
  });
  const onMissConfig = (id: number, body: MissBody) => guard(async () => {
    replace(await call(`${BASE}/${id}/miss-config`, { method: "POST", body: JSON.stringify(body) }));
  });
  const onMissMigration = (id: number, body: MissMigrationBody) => guard(async () => {
    replace(await call(`${BASE}/${id}/miss-migration`, { method: "POST", body: JSON.stringify(body) }));
  });
  const onUpdateEvent = (id: number, index: number, patch: EventPatch) => guard(async () => {
    replace(await call(`${BASE}/${id}/events/${index}`, { method: "PATCH", body: JSON.stringify(patch) }));
  });
  const onDeleteEvent = (id: number, index: number) => guard(async () => {
    replace(await call(`${BASE}/${id}/events/${index}`, { method: "DELETE" }));
  });
  const onDelete = (id: number) => guard(async () => {
    await call(`${BASE}/${id}`, { method: "DELETE" });
    setSites((prev) => prev.filter((s) => s.id !== id));
  });
  const onAdd = (rows: Partial<TrackerSite>[]) => guard(async () => {
    const data = await call(`${BASE}/bulk`, { method: "POST", body: JSON.stringify({ sites: rows }) });
    setSites((prev) => [...prev, ...data.sites]);
    if (data.sites.length === 1) jumpTo(data.sites[0].id);
  });

  const jumpTo = (id: number) => {
    const site = sites.find((s) => s.id === id);
    if (site && !matchesFilter(site, filter, today)) setFilter("all");
    setHighlightId(id);
    // Let a just-added row render before scrolling to it.
    requestAnimationFrame(() =>
      document.getElementById(`tracker-site-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
    window.setTimeout(() => setHighlightId((h) => (h === id ? null : h)), 2000);
  };

  // Alphabetical, so a row stays put when you tick it (the chart does the stage ordering).
  const ordered = useMemo(
    () => [...sites].sort((a, b) => a.site_name.localeCompare(b.site_name, undefined, { numeric: true, sensitivity: "base" })),
    [sites]);
  const visible = ordered.filter((s) => matchesFilter(s, filter, today));
  const filterOptions: { key: TableFilter; label: string }[] = [
    { key: "active", label: "Active" },
    { key: "overdue", label: "Overdue" },
    ...STAGES.map((s) => ({ key: s.key as TableFilter, label: s.label })),
    { key: "all", label: "All" },
  ];

  const summary = useMemo(() => {
    const counts = new Map<string, { tone: keyof typeof TONE_CLASSES; rank: number; n: number }>();
    for (const s of sites) {
      const st = siteStatus(s, today);
      const cur = counts.get(st.label) ?? { tone: st.tone, rank: st.rank, n: 0 };
      cur.n += 1;
      counts.set(st.label, cur);
    }
    return [...counts.entries()].sort((a, b) => a[1].rank - b[1].rank);
  }, [sites, today]);

  const totalAps = sites.reduce((n, s) => n + (s.sz_ap_count ?? 0), 0);
  const lastEdit = sites.reduce<TrackerSite | null>(
    (best, s) => (!best || (s.updated_at ?? "") > (best.updated_at ?? "") ? s : best), null);

  return (
    <div className="p-4 md:p-6 max-w-[1600px] mx-auto space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold text-gray-900">Tracker</h1>
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-100 text-purple-800">Alpha</span>
            {!loading && !canEdit && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">View only</span>
            )}
          </div>
          <p className="text-sm text-gray-500 mt-1">
            Where each remaining site migration stands: config push, validation, and hardware swap.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {lastEdit?.updated_at && (
            <span className="text-xs text-gray-400">
              Last change {new Date(lastEdit.updated_at + "Z").toLocaleString()}
              {lastEdit.updated_by ? ` by ${lastEdit.updated_by}` : ""}
            </span>
          )}
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded border border-red-300 bg-red-50 text-sm text-red-800">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      {sites.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-gray-700 font-medium">
            {sites.length} site{sites.length === 1 ? "" : "s"}
            {totalAps > 0 && <span className="text-gray-500 font-normal"> · {totalAps.toLocaleString()} SZ APs</span>}
          </span>
          {summary.map(([label, { tone, n }]) => (
            <span key={label} className={`px-2 py-0.5 rounded text-xs font-medium ${TONE_CLASSES[tone]}`}>
              {n} {label}
            </span>
          ))}
        </div>
      )}

      <TrackerGantt sites={sites} today={today} onSelect={jumpTo} />

      <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-gray-200">
          <h2 className="font-semibold text-gray-800">Sites</h2>
          {canEdit && <AddBar onAdd={onAdd} />}
          {sites.length > 0 && (
            <div className="w-full flex flex-wrap items-center gap-1.5">
              {filterOptions.map(({ key, label }) => {
                const n = sites.filter((s) => matchesFilter(s, key, today)).length;
                if (n === 0 && key !== filter && key !== "active" && key !== "all") return null;
                const on = key === filter;
                return (
                  <button
                    key={key}
                    onClick={() => setFilter(key)}
                    className={`px-2.5 py-0.5 rounded-full border text-xs ${
                      on ? "bg-gray-800 border-gray-800 text-white" : "border-gray-300 text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    {label} <span className={on ? "text-gray-300" : "text-gray-400"}>{n}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        {loading && sites.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500">Loading…</div>
        ) : sites.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500">
            {canEdit ? "Add a site above, or paste rows from the spreadsheet." : "No sites have been added yet."}
          </div>
        ) : (
          visible.length === 0 ? (
            <div className="p-8 text-center text-sm text-gray-500">
              No sites match this filter.{" "}
              <button onClick={() => setFilter("all")} className="text-blue-600 hover:underline">Show all</button>
            </div>
          ) : (
          <TrackerTable
            sites={visible}
            canEdit={canEdit}
            today={today}
            highlightId={highlightId}
            onPatch={onPatch}
            onFailValidation={onFailValidation}
            onMissConfig={onMissConfig}
            onMissMigration={onMissMigration}
            onUpdateEvent={onUpdateEvent}
            onDeleteEvent={onDeleteEvent}
            onDelete={onDelete}
          />
          )
        )}
      </div>
    </div>
  );
}

function AddBar({ onAdd }: { onAdd: (rows: Partial<TrackerSite>[]) => Promise<void> }) {
  const [name, setName] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const parsed: PasteResult | null = useMemo(() => (pasteText.trim() ? parsePaste(pasteText) : null), [pasteText]);

  const addOne = async () => {
    if (!name.trim()) return;
    await onAdd([{ site_name: name.trim() }]);
    setName("");
  };

  const importRows = async () => {
    if (!parsed?.rows.length) return;
    await onAdd(parsed.rows as Partial<TrackerSite>[]);
    setPasteText("");
    setPasteOpen(false);
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void addOne(); }}
          placeholder="New site name"
          className="border border-gray-300 rounded px-2 py-1 text-sm w-56"
        />
        <button
          onClick={() => void addOne()}
          disabled={!name.trim()}
          className="flex items-center gap-1 px-3 py-1 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          <Plus size={14} /> Add site
        </button>
      </div>
      <button
        onClick={() => setPasteOpen((o) => !o)}
        className="flex items-center gap-1 px-3 py-1 rounded border border-gray-300 text-sm hover:bg-gray-50"
      >
        <ClipboardPaste size={14} /> Paste from spreadsheet
      </button>

      {pasteOpen && (
        <div className="w-full space-y-2">
          <p className="text-xs text-gray-500">
            Copy rows straight out of Excel or Sheets. With a header row, columns are matched by name
            (Site, vSZ, Tenant, APs, WLANs, Category, Config, Validation, HW, Notes); without one they're
            read in that order. Dates like 10/27, 10/27/26, 2026-10-27 or Monday | 26 OCT all work.
          </p>
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            rows={6}
            placeholder={"Site\tvSZ\tTenant\tAPs\tWLANs\tCategory\tConfig\tValidation\tHW\tNotes"}
            className="w-full border border-gray-300 rounded p-2 font-mono text-xs"
          />
          {parsed && (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-gray-700">
                {parsed.rows.length} site{parsed.rows.length === 1 ? "" : "s"} ready
                <span className="text-gray-500">
                  {parsed.hadHeader ? " · matched header: " : " · no header, columns in table order: "}
                  {parsed.columns.join(", ")}
                </span>
              </span>
              <button
                onClick={() => void importRows()}
                disabled={!parsed.rows.length}
                className="px-3 py-1 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                Import {parsed.rows.length}
              </button>
              {parsed.warnings.length > 0 && (
                <ul className="w-full text-xs text-amber-700 list-disc pl-5">
                  {parsed.warnings.slice(0, 5).map((w) => <li key={w}>{w}</li>)}
                  {parsed.warnings.length > 5 && <li>…and {parsed.warnings.length - 5} more</li>}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}
