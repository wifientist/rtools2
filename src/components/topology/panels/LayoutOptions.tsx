import { useState } from "react";
import { RotateCcw, Sliders } from "lucide-react";
import { useTopology } from "../state/store";
import { engineOr } from "../layout/registry";
import { defaultOptions } from "../layout/types";

/**
 * Settings for whichever engine is selected.
 *
 * Rendered entirely from the engine's own `options` declaration, so adding an
 * engine brings its controls with it and no option can leak into an engine it
 * means nothing to.
 */
export default function LayoutOptions() {
  const engineId = useTopology((s) => s.engine);
  const engineOptions = useTopology((s) => s.engineOptions);
  const setEngineOption = useTopology((s) => s.setEngineOption);
  const resetEngineOptions = useTopology((s) => s.resetEngineOptions);
  const [open, setOpen] = useState(false);

  const engine = engineOr(engineId);
  const defaults = defaultOptions(engine);
  const current = { ...defaults, ...(engineOptions[engine.id] ?? {}) };
  const changed = Object.keys(defaults).some(
    (key) => current[key] !== defaults[key],
  );

  if (!engine.options.length) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title={`Settings for the ${engine.label} layout`}
        className={`flex items-center gap-1 rounded border px-2 py-1 ${
          changed
            ? "border-blue-400 bg-blue-50 text-blue-800"
            : "border-gray-300 text-gray-700 hover:bg-gray-50"
        }`}
      >
        <Sliders size={12} />
        Options
        {changed && <span className="text-[10px]">•</span>}
      </button>

      {open && (
        <>
          {/* Click-away, behind the panel. */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-lg border border-gray-200 bg-white p-3 shadow-lg">
            <div className="mb-2 flex items-baseline gap-2">
              <span className="text-xs font-semibold text-gray-900">
                {engine.label}
              </span>
              <button
                onClick={() => resetEngineOptions(engine.id, defaults)}
                disabled={!changed}
                className="ml-auto flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-800 disabled:opacity-40"
              >
                <RotateCcw size={10} /> Reset
              </button>
            </div>
            <p className="mb-3 text-[11px] leading-snug text-gray-500">
              {engine.description}
            </p>

            <div className="space-y-3">
              {engine.options.map((option) => (
                <div key={option.key}>
                  <div className="flex items-baseline justify-between">
                    <label className="text-[11px] font-medium text-gray-700">
                      {option.label}
                    </label>
                    {option.kind === "number" && (
                      <span className="font-mono text-[10px] text-gray-500">
                        {String(current[option.key])}
                      </span>
                    )}
                  </div>

                  {option.kind === "number" && (
                    <input
                      type="range"
                      min={option.min}
                      max={option.max}
                      step={option.step}
                      value={Number(current[option.key])}
                      onChange={(e) =>
                        setEngineOption(
                          engine.id,
                          option.key,
                          Number(e.target.value),
                        )
                      }
                      className="mt-0.5 w-full"
                    />
                  )}

                  {option.kind === "select" && (
                    <select
                      value={String(current[option.key])}
                      onChange={(e) =>
                        setEngineOption(engine.id, option.key, e.target.value)
                      }
                      className="mt-0.5 w-full rounded border px-2 py-1 text-xs"
                    >
                      {option.choices.map((choice) => (
                        <option key={choice.value} value={choice.value}>
                          {choice.label}
                        </option>
                      ))}
                    </select>
                  )}

                  {option.kind === "boolean" && (
                    <label className="mt-0.5 flex items-center gap-1.5 text-xs text-gray-700">
                      <input
                        type="checkbox"
                        checked={Boolean(current[option.key])}
                        onChange={(e) =>
                          setEngineOption(
                            engine.id,
                            option.key,
                            e.target.checked,
                          )
                        }
                      />
                      enabled
                    </label>
                  )}

                  {option.help && (
                    <p className="mt-0.5 text-[10px] leading-snug text-gray-400">
                      {option.help}
                    </p>
                  )}
                </div>
              ))}
            </div>

            {!engine.supportsPinning && (
              <p className="mt-3 border-t border-gray-100 pt-2 text-[10px] leading-snug text-amber-700">
                This layout cannot hold a node in place itself, so anything you
                have dragged is restored after it runs rather than respected
                during it.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
