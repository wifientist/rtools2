import { useEffect, useState } from "react";
import { Check, RotateCcw, X } from "lucide-react";
import { TIER_STYLE } from "../colors";
import type { Link } from "../state/types";

/**
 * The human verdict on one link.
 *
 * This is the top of the confidence lattice: a person who has walked the cable
 * outranks every machine source, and that verdict lives outside the snapshot so
 * it survives re-discovery. It is deliberately NOT presented as "the truth" —
 * the engine's own conclusion stays on screen underneath, including evidence
 * that disagrees, because an override that hides the disagreement is just a
 * confident-looking guess.
 *
 * Only confirm and reject are offered here. The third verdict the engine
 * supports, `assert`, means "there is a link the engine never found", which
 * needs a way to draw one — a different feature, not a third button.
 */

type Props = {
  link: Link;
  onSet?: (link: Link, verdict: "confirm" | "reject", note: string) => Promise<void>;
  onClear?: (link: Link) => Promise<void>;
};

export default function LinkVerdict({ link, onSet, onClear }: Props) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  // A note belongs to the link it was typed against, not to the panel.
  useEffect(() => {
    setNote("");
    setError("");
  }, [link.id]);

  if (!onSet) return null;

  const run = async (fn: () => Promise<void>, tag: string) => {
    setBusy(tag);
    setError("");
    try {
      await fn();
      setNote("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const verdict = link.override?.verdict;
  const machine = link.machine;

  if (verdict) {
    const rejected = verdict === "reject";
    return (
      <div
        className={`mt-3 rounded border p-2 ${
          rejected
            ? "border-red-200 bg-red-50"
            : "border-blue-200 bg-blue-50"
        }`}
      >
        <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-900">
          {rejected ? <X size={13} /> : <Check size={13} />}
          {rejected ? "Ruled out by a person" : "Confirmed by a person"}
        </div>
        <p className="mt-1 text-[11px] text-gray-700">
          {link.override?.by || "Someone"}
          {link.override?.at ? ` · ${link.override.at.slice(0, 10)}` : ""}
        </p>
        {link.override?.note && (
          <p className="mt-1 text-xs italic text-gray-700">
            “{link.override.note}”
          </p>
        )}
        {rejected && (
          <p className="mt-1 text-[11px] text-gray-600">
            It is no longer drawn on the canvas. Tick <em>Rejected</em> in the
            toolbar to see ruled-out links again.
          </p>
        )}
        {/*
          The machine's own conclusion, kept visible. If the engine disagrees
          with the person, that is worth seeing every time — it is either a bug
          in the engine or a mistake in the verdict, and hiding it finds neither.
        */}
        {machine && (
          <p className="mt-1.5 border-t border-black/5 pt-1.5 text-[11px] text-gray-600">
            The engine scored this{" "}
            <span className="font-medium">
              {TIER_STYLE[machine.tier]?.label ?? machine.tier}
            </span>{" "}
            ({machine.score >= 0 ? "+" : ""}
            {machine.score.toFixed(2)}) on its own evidence.
          </p>
        )}
        {onClear && (
          <button
            onClick={() => run(() => onClear(link), "clear")}
            disabled={busy !== null}
            className="mt-2 flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <RotateCcw size={11} />
            {busy === "clear" ? "Clearing…" : "Clear verdict"}
          </button>
        )}
        {error && <p className="mt-1 text-[11px] text-red-700">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mt-3 rounded border border-gray-200 p-2">
      <p className="text-xs font-semibold text-gray-700">Do you know better?</p>
      <p className="mt-0.5 text-[11px] text-gray-500">
        A verdict outranks every source above, and survives re-discovery.
      </p>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Why? (optional — e.g. traced by hand)"
        maxLength={200}
        className="mt-2 w-full rounded border border-gray-300 px-2 py-1 text-xs"
      />
      <div className="mt-2 flex gap-1.5">
        <button
          onClick={() => run(() => onSet(link, "confirm", note), "confirm")}
          disabled={busy !== null}
          className="flex flex-1 items-center justify-center gap-1 rounded bg-blue-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <Check size={11} />
          {busy === "confirm" ? "Saving…" : "This link is real"}
        </button>
        <button
          onClick={() => run(() => onSet(link, "reject", note), "reject")}
          disabled={busy !== null}
          className="flex flex-1 items-center justify-center gap-1 rounded border border-red-300 px-2 py-1 text-[11px] font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
        >
          <X size={11} />
          {busy === "reject" ? "Saving…" : "No such link"}
        </button>
      </div>
      {error && <p className="mt-1 text-[11px] text-red-700">{error}</p>}
    </div>
  );
}
