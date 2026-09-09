import { useEffect, useState } from "react";
import { BookOpen, X } from "lucide-react";
import { TIER_STYLE } from "../colors";
import type { SourceInfo, Tier } from "../state/types";

/**
 * What the tool reads, what each source proves, and what it is worth.
 *
 * Served from the same weight table the engine scores with, so this cannot
 * drift from the model it describes — the alternative is a document that
 * slowly starts lying about the code.
 *
 * It exists because "why does LLDP outrank the MAC table?" is a fair question
 * and the honest answer is a table, not a shrug.
 */

type Props = { fetchSources?: () => Promise<SourceInfo[] | null> };

const TIER_ORDER: Tier[] = ["confirmed", "strong", "probable", "weak", "rejected"];

export default function SourceCatalogue({ fetchSources }: Props) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceInfo[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || sources || !fetchSources) return;
    setLoading(true);
    fetchSources()
      .then(setSources)
      .finally(() => setLoading(false));
  }, [open, sources, fetchSources]);

  if (!fetchSources) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="What the map reads, and how much each source counts for"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50"
      >
        <BookOpen size={12} /> Sources
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
              <BookOpen size={16} className="text-blue-600" />
              <h2 className="font-semibold text-gray-900">Evidence sources</h2>
              <button
                onClick={() => setOpen(false)}
                className="ml-auto rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={16} />
              </button>
            </div>

            <div className="overflow-y-auto px-4 py-3">
              <p className="mb-3 text-xs leading-relaxed text-gray-600">
                Every link is argued for by one or more of these. A source can
                never push a link above its own <strong>tier cap</strong> — ten
                MAC-table observations still top out at Probable. Only two
                independent devices naming each other, a structural fact like a
                stack, or your own verdict reaches Confirmed. That rule is what
                stops a pile of weak evidence from manufacturing certainty.
              </p>

              <div className="mb-4 flex flex-wrap gap-3 rounded border border-gray-200 p-2">
                {TIER_ORDER.map((tier) => (
                  <div key={tier} className="flex items-center gap-1.5">
                    <svg width="30" height="10">
                      <line
                        x1="1"
                        y1="5"
                        x2="29"
                        y2="5"
                        stroke="#2563eb"
                        strokeWidth={TIER_STYLE[tier].strokeWidth}
                        strokeDasharray={TIER_STYLE[tier].dash}
                        opacity={TIER_STYLE[tier].opacity}
                      />
                    </svg>
                    <span className="text-[11px] font-medium text-gray-700">
                      {TIER_STYLE[tier].label}
                    </span>
                  </div>
                ))}
              </div>

              {loading && <p className="text-sm text-gray-400">loading…</p>}
              {!loading && !sources && (
                <p className="text-sm text-gray-500">
                  Could not load the source catalogue.
                </p>
              )}

              {sources && (
                <div className="space-y-3">
                  {sources.map((source) => (
                    <div
                      key={source.id}
                      className="rounded border border-gray-200 p-2.5"
                    >
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="text-sm font-medium text-gray-900">
                          {source.label}
                        </span>
                        <span className="font-mono text-[10px] text-gray-400">
                          {source.id}
                        </span>
                        {source.tierCap && (
                          <span className="ml-auto rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700">
                            up to{" "}
                            {TIER_STYLE[source.tierCap as Tier]?.label ??
                              source.tierCap}
                          </span>
                        )}
                      </div>

                      {source.proves && (
                        <p className="mt-1 text-xs text-gray-700">
                          {source.proves}
                        </p>
                      )}
                      {source.reads && (
                        <p className="mt-1 font-mono text-[10px] text-gray-400">
                          reads {source.reads}
                        </p>
                      )}
                      {source.caveats && (
                        <p className="mt-1 rounded bg-amber-50 px-1.5 py-1 text-[11px] leading-snug text-amber-800">
                          {source.caveats}
                        </p>
                      )}

                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {source.emits.map((emitted) => (
                          <span
                            key={emitted.source}
                            className="rounded bg-gray-50 px-1.5 py-0.5 font-mono text-[10px] text-gray-600"
                            title={
                              emitted.tierCap
                                ? `Can justify up to ${emitted.tierCap}`
                                : "Adjusts the score without changing the tier"
                            }
                          >
                            {emitted.source}{" "}
                            <span
                              className={
                                emitted.weight < 0
                                  ? "text-red-600"
                                  : emitted.weight > 0
                                    ? "text-green-700"
                                    : "text-gray-400"
                              }
                            >
                              {emitted.weight >= 0 ? "+" : ""}
                              {emitted.weight.toFixed(1)}
                            </span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <p className="mt-4 text-[11px] leading-snug text-gray-500">
                Weights are log-odds and are priors calibrated against measured
                agreement on a real tenant, not physical constants. The tier is
                a lattice and the number is a running sum — open any link to
                watch it add up.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
