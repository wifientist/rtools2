import type { ReactNode } from "react";

/**
 * Multi-venue selector.
 *
 * Extracted from WiredWiz, which had the only good multi-select in the app, so
 * Topology could use it without a second copy drifting from the first. The one
 * generalisation: WiredWiz hardcoded a switch-count column, which is now
 * `renderMeta` — Topology shows AP counts alongside switches, and a future
 * caller will want something else again.
 */

export type VenueRow = {
  venueId: string;
  venueName: string;
  switches?: number;
  offline?: number;
  [key: string]: unknown;
};

type Props = {
  venues: VenueRow[];
  loading?: boolean;
  selected: string[];
  setSelected: (ids: string[]) => void;
  filter: string;
  setFilter: (value: string) => void;
  /** Right-hand detail per row. Defaults to the switch count. */
  renderMeta?: (venue: VenueRow) => ReactNode;
  /** Right-hand summary in the header. Defaults to total switches selected. */
  renderSummary?: (selectedVenues: VenueRow[]) => ReactNode;
  emptyMessage?: string;
  footer?: ReactNode;
};

function defaultMeta(v: VenueRow) {
  const switches = v.switches ?? 0;
  return (
    <>
      {switches} switch{switches === 1 ? "" : "es"}
      {(v.offline ?? 0) > 0 && (
        <span className="text-red-600"> · {v.offline} offline</span>
      )}
    </>
  );
}

export default function VenuePicker({
  venues,
  loading = false,
  selected,
  setSelected,
  filter,
  setFilter,
  renderMeta = defaultMeta,
  renderSummary,
  emptyMessage = "No venues with switches in this tenant.",
  footer,
}: Props) {
  const q = filter.trim().toLowerCase();
  const shown = q
    ? venues.filter((v) => v.venueName.toLowerCase().includes(q))
    : venues;
  const shownIds = shown.map((v) => v.venueId);
  const allShownSelected =
    shownIds.length > 0 && shownIds.every((id) => selected.includes(id));
  const selectedVenues = venues.filter((v) => selected.includes(v.venueId));

  const toggle = (id: string) =>
    setSelected(
      selected.includes(id)
        ? selected.filter((x) => x !== id)
        : [...selected, id],
    );

  // Acts on the FILTERED set, which is the point of the filter: type "RIDGE",
  // hit Select shown, get every Ridgecrest venue.
  const toggleShown = () =>
    setSelected(
      allShownSelected
        ? selected.filter((id) => !shownIds.includes(id))
        : Array.from(new Set([...selected, ...shownIds])),
    );

  const summary = renderSummary
    ? renderSummary(selectedVenues)
    : `${selected.length} of ${venues.length} selected · ${selectedVenues.reduce(
        (n, v) => n + (v.switches ?? 0),
        0,
      )} switches`;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
          Venues
        </span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter venues by name…"
          className="border rounded px-3 py-1.5 text-sm w-full sm:w-72"
        />
        <button
          onClick={toggleShown}
          disabled={!shown.length}
          className="px-3 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
        >
          {allShownSelected ? "Clear shown" : `Select shown (${shown.length})`}
        </button>
        {selected.length > 0 && (
          <button
            onClick={() => setSelected([])}
            className="px-3 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            Clear all
          </button>
        )}
        <span className="ml-auto text-xs text-gray-500">
          {loading ? "loading venues…" : summary}
        </span>
      </div>

      {!loading && !venues.length && (
        <p className="text-sm text-gray-500">{emptyMessage}</p>
      )}

      {venues.length > 0 && (
        <div className="max-h-56 overflow-y-auto border rounded divide-y">
          {shown.map((v) => {
            const on = selected.includes(v.venueId);
            return (
              <label
                key={v.venueId}
                className={`flex items-center gap-3 px-3 py-1.5 text-sm cursor-pointer ${
                  on ? "bg-blue-50" : "hover:bg-gray-50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(v.venueId)}
                />
                <span className="flex-1">{v.venueName}</span>
                <span className="text-xs text-gray-500">{renderMeta(v)}</span>
              </label>
            );
          })}
          {!shown.length && (
            <p className="px-3 py-2 text-sm text-gray-500">
              No venue matches “{filter}”.
            </p>
          )}
        </div>
      )}

      {footer}
    </div>
  );
}
