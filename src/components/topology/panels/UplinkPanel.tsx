import { useState } from "react";
import { Check, Cloud, X } from "lucide-react";
import { useTopology } from "../state/store";
import type { UplinkCandidate } from "../state/types";

/**
 * Where each venue reaches the internet.
 *
 * Proposed, never assumed. A guessed WAN edge drawn on the map would be
 * indistinguishable from a fact, and it anchors the whole hierarchy — so
 * nothing is materialised until someone says yes. A rejection is remembered
 * too, so a wrong guess stops being offered on every subsequent discovery.
 *
 * Not every venue HAS its own uplink: a campus of several venues usually leaves
 * through one core. Leaving the others unconfirmed is the correct answer, and
 * this panel does not nag for them.
 */

type Props = {
  onConfirm?: (
    venueId: string,
    candidate: UplinkCandidate | null,
    action: "confirm" | "reject" | "clear",
  ) => Promise<void>;
};

export default function UplinkPanel({ onConfirm }: Props) {
  const wan = useTopology((s) => s.wan);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  if (!wan) return null;
  const venues = Object.values(wan.venues);
  const confirmed = venues.filter((v) => v.confirmed).length;

  const act = async (
    venueId: string,
    candidate: UplinkCandidate | null,
    action: "confirm" | "reject" | "clear",
  ) => {
    if (!onConfirm) return;
    setBusy(`${venueId}:${candidate?.key ?? ""}:${action}`);
    try {
      await onConfirm(venueId, candidate, action);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Where this network reaches the internet"
        className={`flex items-center gap-1 rounded border px-2 py-1 ${
          confirmed
            ? "border-teal-400 bg-teal-50 text-teal-800"
            : "border-gray-300 text-gray-700 hover:bg-gray-50"
        }`}
      >
        <Cloud size={12} />
        {confirmed ? `WAN ×${confirmed}` : "Set WAN"}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
              <Cloud size={16} className="text-teal-600" />
              <h2 className="font-semibold text-gray-900">Internet uplinks</h2>
              <button
                onClick={() => setOpen(false)}
                className="ml-auto rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={16} />
              </button>
            </div>

            <div className="overflow-y-auto px-4 py-3">
              <p className="mb-3 text-xs leading-relaxed text-gray-600">
                Confirming an uplink anchors the top of the hierarchy and gives
                client-path tracing somewhere to trace <em>to</em>. Not every
                venue has its own — a campus usually leaves through one core, so
                leaving the rest unconfirmed is the right answer.
              </p>

              {venues.map((venue) => (
                <div key={venue.venueId} className="mb-4">
                  <div className="mb-1 flex items-baseline gap-2">
                    <span className="text-sm font-medium text-gray-900">
                      {venue.venueName}
                    </span>
                    {venue.confirmed && (
                      <span className="rounded bg-teal-100 px-1.5 py-0.5 text-[11px] text-teal-800">
                        confirmed
                      </span>
                    )}
                  </div>

                  {venue.confirmed && (
                    <div className="mb-2 flex flex-wrap items-center gap-2 rounded border border-teal-200 bg-teal-50 p-2 text-xs">
                      <Check size={12} className="text-teal-700" />
                      <span className="font-medium text-gray-900">
                        {venue.confirmed.deviceId.replace(/^sw:/, "")}
                        {venue.confirmed.portIdent
                          ? ` port ${venue.confirmed.portIdent}`
                          : ""}
                      </span>
                      <span className="text-gray-500">
                        by {venue.confirmed.by || "someone"}
                        {venue.confirmed.at ? ` · ${venue.confirmed.at.slice(0, 10)}` : ""}
                      </span>
                      <button
                        onClick={() => act(venue.venueId, null, "clear")}
                        disabled={busy !== null}
                        className="ml-auto rounded border border-gray-300 bg-white px-2 py-0.5 hover:bg-gray-50 disabled:opacity-40"
                      >
                        Clear
                      </button>
                    </div>
                  )}

                  {!venue.candidates.length && !venue.confirmed && (
                    <p className="text-xs text-gray-500">
                      Nothing here looks like a way out. That is normal for a
                      venue that reaches the internet through another one.
                    </p>
                  )}

                  <div className="space-y-1.5">
                    {venue.candidates.map((candidate) => (
                      <div
                        key={candidate.key}
                        className="rounded border border-gray-200 p-2"
                      >
                        <div className="flex flex-wrap items-baseline gap-2">
                          <span className="text-xs font-medium text-gray-900">
                            {candidate.deviceName}
                          </span>
                          {candidate.portIdent && (
                            <span className="font-mono text-[11px] text-gray-600">
                              port {candidate.portIdent}
                            </span>
                          )}
                          <span className="font-mono text-[10px] text-gray-400">
                            score {candidate.score.toFixed(1)}
                          </span>
                          <div className="ml-auto flex gap-1">
                            <button
                              onClick={() => act(venue.venueId, candidate, "confirm")}
                              disabled={busy !== null}
                              className="rounded bg-teal-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-teal-700 disabled:opacity-40"
                            >
                              This one
                            </button>
                            <button
                              onClick={() => act(venue.venueId, candidate, "reject")}
                              disabled={busy !== null}
                              title="Stop offering this — it is not the uplink"
                              className="rounded border border-gray-300 px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                            >
                              Not this
                            </button>
                          </div>
                        </div>
                        <ul className="mt-1 space-y-0.5">
                          {candidate.reasons.map((reason, index) => (
                            <li
                              key={`${reason.source}-${index}`}
                              className="text-[11px] leading-snug text-gray-600"
                            >
                              <span className="font-mono text-gray-400">
                                {reason.weight >= 0 ? "+" : ""}
                                {reason.weight.toFixed(1)}
                              </span>{" "}
                              {reason.claim}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>

                  {venue.rejected.length > 0 && (
                    <p className="mt-1 text-[11px] text-gray-400">
                      {venue.rejected.length} candidate
                      {venue.rejected.length === 1 ? "" : "s"} ruled out and no
                      longer offered.
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
