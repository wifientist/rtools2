import { useMemo, useState } from "react";
import {
  bumpCandidate, fmtShort, prevWorkday, parseISO, toISO, previewMissConfig, previewMissValidation,
  type BumpCandidate, type TrackerSite,
} from "./trackerModel";

/**
 * Bulk bump: every site whose next step is still unticked on or before the cutoff
 * gets that step recorded as missed (no reason) on each workday from its due date
 * through today, ending on the next workday.
 * Validation + HW are pushed along, as with a single miss. Each row goes through
 * the same endpoint a single "Record miss" does.
 */
export default function BumpPanel({
  sites, today, onBump, onClose,
}: {
  sites: TrackerSite[];
  today: string;
  onBump: (c: BumpCandidate) => Promise<void>;
  onClose: () => void;
}) {
  const [includeToday, setIncludeToday] = useState(true);
  const cutoff = includeToday ? today : toISO(prevWorkday(parseISO(today)));
  const candidates = useMemo(
    () => sites
      .map((s) => bumpCandidate(s, cutoff, today))
      .filter((c): c is BumpCandidate => !!c)
      .sort((a, b) => a.due.localeCompare(b.due) || a.site.site_name.localeCompare(b.site.site_name)),
    [sites, cutoff, today]);
  const [skipped, setSkipped] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const chosen = candidates.filter((c) => !skipped.has(c.site.id));
  const toggle = (id: number) => setSkipped((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const run = async () => {
    setBusy(true);
    setDone(0);
    setError(null);
    try {
      // One at a time, so a failure stops the run with the rest untouched.
      for (const c of chosen) {
        await onBump(c);
        setDone((n) => n + 1);
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const knockOn = (c: BumpCandidate): string => {
    const s = c.site;
    const moved = (label: string, from: string | null, to: string | null) =>
      from && from !== to ? `${label} → ${fmtShort(to)}` : null;
    if (c.step.key === "config_pushed") {
      const after = previewMissConfig(s, c.due, c.newDate, true);
      return [moved("V", s.validation, after.validation), moved("M", s.hw_migrated, after.hw_migrated)]
        .filter(Boolean).join(" · ");
    }
    if (c.step.key === "validation") return moved("M", s.hw_migrated, previewMissValidation(s, c.due, c.newDate, true)) ?? "";
    return "";
  };

  return (
    <div className="p-3 rounded border border-amber-300 bg-amber-50 text-sm space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium text-amber-900">Bump pending steps</span>
        <label className="flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer">
          <input type="checkbox" checked={includeToday} onChange={(e) => setIncludeToday(e.target.checked)} />
          Include steps due today
        </label>
        <span className="text-xs text-gray-600">
          Each workday from the due date on is recorded as a miss with no reason, ending on the next workday; later steps move with it.
        </span>
        <button onClick={onClose} disabled={busy} className="ml-auto text-xs text-amber-800 hover:underline">Close</button>
      </div>

      {candidates.length === 0 ? (
        <div className="text-xs text-gray-600">Nothing pending on or before {fmtShort(cutoff)}.</div>
      ) : (
        <>
          <table className="w-full text-xs bg-white rounded border border-amber-200">
            <thead className="text-gray-500">
              <tr className="text-left">
                <th className="px-2 py-1 w-6" />
                <th className="px-2 py-1">Site</th>
                <th className="px-2 py-1">Step</th>
                <th className="px-2 py-1">Due</th>
                <th className="px-2 py-1">Moves to</th>
                <th className="px-2 py-1">Also moves</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.site.id} className={`border-t border-amber-100 ${skipped.has(c.site.id) ? "opacity-50" : ""}`}>
                  <td className="px-2 py-1">
                    <input type="checkbox" checked={!skipped.has(c.site.id)} disabled={busy}
                      onChange={() => toggle(c.site.id)} />
                  </td>
                  <td className="px-2 py-1 font-medium text-gray-800">{c.site.site_name}</td>
                  <td className="px-2 py-1">{c.step.label}</td>
                  <td className={`px-2 py-1 ${c.due < today ? "text-red-700" : ""}`}>{fmtShort(c.due)}</td>
                  <td className="px-2 py-1">
                    {fmtShort(c.newDate)}
                    {c.missDays.length > 1 && <span className="text-gray-500"> ({c.missDays.length} misses)</span>}
                  </td>
                  <td className="px-2 py-1 text-gray-500">{knockOn(c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center gap-3">
            <button
              onClick={() => void run()}
              disabled={busy || chosen.length === 0}
              className="px-3 py-1 rounded bg-amber-600 text-white text-sm hover:bg-amber-700 disabled:opacity-50"
            >
              {busy ? `Bumping ${done}/${chosen.length}…` : `Bump ${chosen.length} site${chosen.length === 1 ? "" : "s"}`}
            </button>
            {error && <span className="text-xs text-red-700">Stopped after {done}: {error}</span>}
          </div>
        </>
      )}
    </div>
  );
}
