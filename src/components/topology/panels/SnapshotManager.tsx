import { useState } from "react";
import { Archive, Trash2 } from "lucide-react";
import type { SnapshotRow } from "../state/types";

/**
 * The stored runs, and getting rid of one.
 *
 * Retention is stated rather than implied: a run that will be swept in four
 * days should say so, because the map is the only change history this tool has
 * — R1's own /events and /alarms queries return nothing usable — and "how far
 * back can I look?" is therefore a question with real consequences.
 *
 * Deleting is deliberately per-run and confirmed. There is no "delete all":
 * the only thing it would ever be used for is the thing you cannot undo.
 */

type Props = {
  snapshots: SnapshotRow[];
  ttlDays: number | null;
  maxSnapshots: number | null;
  activeSnapshot: string | null;
  onDelete: (name: string) => Promise<void>;
  fmtTime: (iso?: string) => string;
};

function size(bytes: number): string {
  if (!bytes) return "";
  const mb = bytes / 1_000_000;
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1000)} kB`;
}

function expiry(epoch: number): string {
  if (!epoch) return "";
  const days = Math.round((epoch * 1000 - Date.now()) / 86_400_000);
  if (days <= 0) return "due to be swept";
  return `${days} day${days === 1 ? "" : "s"} left`;
}

export default function SnapshotManager({
  snapshots,
  ttlDays,
  maxSnapshots,
  activeSnapshot,
  onDelete,
  fmtTime,
}: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  if (!snapshots.length) return null;

  const remove = async (row: SnapshotRow) => {
    if (
      !window.confirm(
        `Delete the run from ${fmtTime(row.takenAt)}? Its devices, links and ` +
          `evidence go with it. Verdicts you have recorded are stored ` +
          `separately and are not affected.`,
      )
    ) {
      return;
    }
    setBusy(row.name);
    setError("");
    try {
      await onDelete(row.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title="Every stored run, and how long it will be kept"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
      >
        <Archive size={12} /> {snapshots.length} kept
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-30 mt-1 w-80 rounded-lg border border-gray-200 bg-white p-2 shadow-lg">
            <p className="px-1 text-[11px] text-gray-500">
              {ttlDays && maxSnapshots
                ? `Runs are kept for ${ttlDays} days, up to ${maxSnapshots} of them — whichever limit is reached first. `
                : ""}
              Your recorded verdicts live outside these and are never swept.
            </p>
            <ul className="mt-1.5 max-h-72 space-y-0.5 overflow-y-auto">
              {snapshots.map((row) => (
                <li
                  key={row.name}
                  className={`flex items-start gap-2 rounded px-1.5 py-1 ${
                    (activeSnapshot ?? snapshots[0]?.name) === row.name
                      ? "bg-blue-50"
                      : ""
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-gray-900">
                      {fmtTime(row.takenAt)}
                      {row.deep && (
                        <span className="ml-1 text-[10px] text-gray-500">deep</span>
                      )}
                    </p>
                    <p className="text-[11px] text-gray-500">
                      {row.counts?.devices ?? 0} devices · {row.counts?.links ?? 0}{" "}
                      links
                      {size(row.sizeBytes) ? ` · ${size(row.sizeBytes)}` : ""}
                      {expiry(row.expiresAtEpoch)
                        ? ` · ${expiry(row.expiresAtEpoch)}`
                        : ""}
                    </p>
                  </div>
                  <button
                    onClick={() => remove(row)}
                    disabled={busy !== null || snapshots.length === 1}
                    title={
                      snapshots.length === 1
                        ? "This is the only run. Delete it and the map is empty until you discover again."
                        : "Delete this run"
                    }
                    className="mt-0.5 shrink-0 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400"
                  >
                    <Trash2 size={12} />
                  </button>
                </li>
              ))}
            </ul>
            {error && (
              <p className="px-1 pt-1 text-[11px] text-red-700">{error}</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
