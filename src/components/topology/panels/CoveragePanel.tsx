import { AlertTriangle, CircleAlert, Info, ShieldCheck } from "lucide-react";
import { assessCoverage } from "../state/coverage";
import type { CoverageLevel } from "../state/coverage";
import type { SnapshotMeta } from "../state/types";

/**
 * What this run could not see.
 *
 * Two parts on purpose. The BANNER appears by itself whenever the input was
 * degraded, because the person who needs this information is exactly the one
 * who would never think to go looking for it. The PANEL holds the detail, for
 * when they do.
 *
 * A clean run shows a quiet line and nothing else. If this shouted on every
 * run it would be ignored on the run that mattered.
 */

const TONE: Record<CoverageLevel, { box: string; chip: string; Icon: typeof Info }> = {
  ok: {
    box: "border-gray-200 bg-white text-gray-600",
    chip: "border-gray-300 text-gray-600 hover:bg-gray-50",
    Icon: ShieldCheck,
  },
  info: {
    box: "border-gray-200 bg-white text-gray-600",
    chip: "border-gray-300 text-gray-600 hover:bg-gray-50",
    Icon: Info,
  },
  warn: {
    box: "border-amber-200 bg-amber-50 text-amber-900",
    chip: "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100",
    Icon: AlertTriangle,
  },
  error: {
    box: "border-red-200 bg-red-50 text-red-900",
    chip: "border-red-300 bg-red-50 text-red-900 hover:bg-red-100",
    Icon: CircleAlert,
  },
};

const GROUP_LABEL: Record<string, string> = {
  reads: "Reading RUCKUS ONE",
  collection: "Collecting the data",
  evidence: "Working out the links",
  identity: "Telling devices apart",
};

const LEVEL_ORDER: CoverageLevel[] = ["error", "warn", "info", "ok"];

export default function CoveragePanel({
  meta,
  open,
  setOpen,
}: {
  meta: SnapshotMeta | null;
  // Controlled, so the banner's own button can open it. The banner sits above
  // the canvas and this chip sits in the snapshot bar; they are one control.
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  const coverage = assessCoverage(meta);
  if (!meta) return null;

  const tone = TONE[coverage.level];
  const flagged = coverage.counts.error + coverage.counts.warn;
  const groups = [...new Set(coverage.items.map((item) => item.group))];

  return (
    <>
      <div className="relative">
        <button
          onClick={() => setOpen(!open)}
          title="What this run could and could not see"
          className={`flex items-center gap-1 rounded border px-2 py-1 text-xs ${tone.chip}`}
        >
          <tone.Icon size={12} />
          {flagged
            ? `${flagged} coverage issue${flagged === 1 ? "" : "s"}`
            : "Coverage"}
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
            <div className="absolute left-0 top-full z-30 mt-1 w-[30rem] rounded-lg border border-gray-200 bg-white shadow-lg">
              <div className="border-b border-gray-100 p-2.5">
                <p className="text-xs font-semibold text-gray-900">
                  What this run could see
                </p>
                <p className="mt-0.5 text-[11px] text-gray-500">
                  A map drawn from partial data looks exactly like a map of a
                  network with a hole in it. Anything below changes how much of
                  this map you should trust.
                </p>
              </div>

              {coverage.items.length === 0 ? (
                <p className="m-2 rounded bg-green-50 px-2 py-1.5 text-[11px] text-green-800">
                  Every read returned in full, every evidence source ran, and
                  nothing was truncated. This map is drawn from everything
                  RUCKUS ONE offered.
                </p>
              ) : (
                <div className="max-h-96 overflow-y-auto p-2">
                  {groups.map((group) => (
                    <div key={group} className="mb-2 last:mb-0">
                      <p className="px-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                        {GROUP_LABEL[group] ?? group}
                      </p>
                      {coverage.items
                        .filter((item) => item.group === group)
                        .sort(
                          (a, b) =>
                            LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level),
                        )
                        .map((item, index) => {
                          const itemTone = TONE[item.level];
                          return (
                            <div
                              key={`${item.title}-${index}`}
                              className={`mt-1 rounded border p-1.5 ${itemTone.box}`}
                            >
                              <p className="flex items-start gap-1.5 text-xs font-medium">
                                <itemTone.Icon size={12} className="mt-0.5 shrink-0" />
                                {item.title}
                              </p>
                              {item.detail && (
                                <p className="mt-0.5 pl-[18px] text-[11px] opacity-90">
                                  {item.detail}
                                </p>
                              )}
                            </div>
                          );
                        })}
                    </div>
                  ))}
                </div>
              )}

              {/* The reads themselves, always, so "did it even look?" is
                  answerable on a clean run too. */}
              <div className="border-t border-gray-100 p-2">
                <p className="px-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  Reads
                </p>
                <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
                  {(meta.sources ?? []).map((source) => (
                    <li
                      key={source.id}
                      className="flex items-baseline justify-between gap-2 text-[11px]"
                    >
                      <span className="truncate text-gray-600">
                        {source.id.split(":")[0]}
                      </span>
                      <span
                        className={`shrink-0 font-mono ${
                          source.status === "ok" ? "text-gray-400" : "text-red-600"
                        }`}
                      >
                        {source.status === "ok"
                          ? source.rows.toLocaleString()
                          : source.status}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}

/**
 * The unmissable half. Rendered above the canvas whenever the run was degraded,
 * so nobody has to know this panel exists to find out the map is incomplete.
 */
export function CoverageBanner({
  meta,
  onOpen,
}: {
  meta: SnapshotMeta | null;
  onOpen?: () => void;
}) {
  const coverage = assessCoverage(meta);
  if (coverage.level !== "warn" && coverage.level !== "error") return null;
  const tone = TONE[coverage.level];
  return (
    <div
      className={`flex flex-wrap items-center gap-2 border-b px-3 py-1.5 text-xs ${tone.box}`}
    >
      <tone.Icon size={13} className="shrink-0" />
      <span>
        <span className="font-medium">
          {coverage.level === "error"
            ? "This map is missing data."
            : "This map was drawn from incomplete data."}
        </span>{" "}
        {coverage.headline}
      </span>
      {onOpen && (
        <button
          onClick={onOpen}
          className="rounded border border-current/30 px-1.5 py-0.5 font-medium hover:bg-black/5"
        >
          What is missing
        </button>
      )}
    </div>
  );
}
