import {
  DENSITY_RAMP,
  INK,
  TIER_COLORS,
  TIER_LABELS,
  TIER_ORDER,
  TIER_RANGES,
  tierForRssi,
} from "./mapColors";
import type { MapAp, OverlayMode, RssiStats } from "./types";

// ============================================================================
// Stat tiles
// ============================================================================

interface StatTileProps {
  label: string;
  value: string;
  hint?: string;
}

function StatTile({ label, value, hint }: StatTileProps) {
  return (
    <div className="rounded-lg border border-gray-200 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">
        {label}
      </div>
      <div className="text-xl font-semibold text-gray-900 leading-tight">
        {value}
      </div>
      {hint && <div className="text-[11px] text-gray-500">{hint}</div>}
    </div>
  );
}

export function MapStats({
  apCount,
  clientCount,
  rssi,
}: {
  apCount: number;
  clientCount: number;
  rssi: RssiStats;
}) {
  return (
    <div className="grid grid-cols-3 gap-2">
      <StatTile label="APs" value={String(apCount)} hint="on this plan" />
      <StatTile label="Clients" value={String(clientCount)} hint="associated now" />
      <StatTile
        label="Median RSSI"
        value={rssi.median !== null ? `${Math.round(rssi.median)}` : "—"}
        hint={rssi.median !== null ? "dBm at the AP" : "no readings"}
      />
    </div>
  );
}

// ============================================================================
// Tier distribution bar
// ============================================================================

/**
 * Stacked tier counts. Segments carry a 2px surface gap so adjacent fills stay
 * separate marks, and each is directly labelled — the tier colors alone are
 * never the only channel.
 */
export function TierBar({ tiers, total }: { tiers: Record<string, number>; total: number }) {
  if (!total) {
    return <div className="text-xs text-gray-500">No clients with RSSI readings.</div>;
  }

  return (
    <div>
      <div className="flex h-2.5 w-full gap-[2px] overflow-hidden">
        {TIER_ORDER.map((tier) => {
          const count = tiers[tier] || 0;
          if (!count) return null;
          return (
            <div
              key={tier}
              title={`${TIER_LABELS[tier]}: ${count}`}
              style={{
                width: `${(count / total) * 100}%`,
                backgroundColor: TIER_COLORS[tier],
                borderRadius: 4,
              }}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {TIER_ORDER.map((tier) => {
          const count = tiers[tier] || 0;
          if (!count) return null;
          return (
            <span key={tier} className="flex items-center gap-1.5 text-[11px] text-gray-600">
              <span
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: TIER_COLORS[tier] }}
              />
              {TIER_LABELS[tier]} {count}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================================
// Legend — swaps with the overlay mode so one hue means one thing
// ============================================================================

export function MapLegend({ mode }: { mode: OverlayMode }) {
  if (mode === "density") {
    return (
      <div>
        <div className="text-xs font-semibold text-gray-700 mb-2">Client density</div>
        <div
          className="h-2.5 w-full rounded"
          style={{
            background: `linear-gradient(to right, ${DENSITY_RAMP[0]}, ${
              DENSITY_RAMP[DENSITY_RAMP.length - 1]
            })`,
          }}
        />
        <div className="mt-1 flex justify-between text-[11px] text-gray-500">
          <span>Sparse</span>
          <span>Dense pocket</span>
        </div>
        <p className="mt-2 text-[11px] text-gray-500">
          Warmth is the number of clients estimated to be near a point, not
          signal quality.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="text-xs font-semibold text-gray-700 mb-2">
        Client signal (RSSI at the AP)
      </div>
      <div className="space-y-1">
        {TIER_ORDER.map((tier) => (
          <div key={tier} className="flex items-center gap-2 text-[11px]">
            <span
              className="inline-block h-3 w-3 rounded-full border border-white shadow-sm"
              style={{ backgroundColor: TIER_COLORS[tier] }}
            />
            <span className="text-gray-700 font-medium w-16">{TIER_LABELS[tier]}</span>
            <span className="text-gray-500">{TIER_RANGES[tier]}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px]">
        <span
          className="inline-block h-3 w-3 rounded-sm"
          style={{ backgroundColor: INK.primary }}
        />
        <span className="text-gray-700 font-medium w-16">AP</span>
        <span className="text-gray-500">surveyed position</span>
      </div>
    </div>
  );
}

// ============================================================================
// Selected AP detail
// ============================================================================

export function ApDetail({ ap }: { ap: MapAp }) {
  const stats = ap.rssi_stats;
  const medianTier = tierForRssi(stats.median);
  const bands = Object.entries(ap.bands).sort((a, b) => b[1] - a[1]);
  const ssids = Object.entries(ap.ssids).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-3">
      <div>
        <div className="font-semibold text-gray-900">{ap.name}</div>
        <div className="text-[11px] text-gray-500">
          {ap.model || "Unknown model"} · {ap.serial_number} ·{" "}
          {ap.status || "unknown status"}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <StatTile label="Clients" value={String(ap.client_count)} />
        <StatTile
          label="Median RSSI"
          value={stats.median !== null ? `${Math.round(stats.median)} dBm` : "—"}
          hint={stats.median !== null ? TIER_LABELS[medianTier] : undefined}
        />
      </div>

      {stats.count > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-700 mb-1.5">
            Signal spread
          </div>
          <TierBar tiers={stats.tiers} total={stats.count} />
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] text-gray-600">
            <div>
              <span className="text-gray-500">Best</span>{" "}
              <span className="font-medium tabular-nums">{stats.max} dBm</span>
            </div>
            <div>
              <span className="text-gray-500">p10</span>{" "}
              <span className="font-medium tabular-nums">
                {stats.p10 !== null ? Math.round(stats.p10) : "—"} dBm
              </span>
            </div>
            <div>
              <span className="text-gray-500">Worst</span>{" "}
              <span className="font-medium tabular-nums">{stats.min} dBm</span>
            </div>
          </div>
        </div>
      )}

      {(ap.cell_radius_m || ap.median_distance_m) && (
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">
            Estimated cell
          </div>
          <div className="text-xs text-gray-700">
            Most clients within{" "}
            <span className="font-semibold tabular-nums">
              {ap.cell_radius_m?.toFixed(1)} m
            </span>
            {ap.median_distance_m !== null && (
              <>
                {" "}
                · median{" "}
                <span className="font-semibold tabular-nums">
                  {ap.median_distance_m.toFixed(1)} m
                </span>
              </>
            )}
          </div>
        </div>
      )}

      {ap.radios.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-700 mb-1.5">Radios</div>
          <div className="space-y-1">
            {ap.radios.map((radio, index) => (
              <div
                key={`${radio.band}-${index}`}
                className="flex justify-between text-[11px] text-gray-600"
              >
                <span className="font-medium">{radio.band || "—"}</span>
                <span className="tabular-nums">
                  ch {radio.channel ?? "—"}
                  {radio.channel_bandwidth ? ` / ${radio.channel_bandwidth}` : ""}
                  {radio.tx_power_dbm !== null && radio.tx_power_dbm !== undefined
                    ? ` · ${radio.tx_power_dbm} dBm`
                    : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {bands.length > 0 && (
        <div className="text-[11px] text-gray-600">
          <span className="text-gray-500">Bands in use: </span>
          {bands.map(([band, count]) => `${band} (${count})`).join(", ")}
        </div>
      )}

      {ssids.length > 0 && (
        <div className="text-[11px] text-gray-600">
          <span className="text-gray-500">SSIDs: </span>
          {ssids.map(([ssid, count]) => `${ssid} (${count})`).join(", ")}
        </div>
      )}
    </div>
  );
}
