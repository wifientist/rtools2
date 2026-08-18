/**
 * Band filtering for the Maps overlay.
 *
 * Filtering happens here rather than on the server so toggling a band is
 * instant — the live query walks the whole venue and is far too expensive to
 * re-run per click.
 *
 * The per-AP statistics are *recomputed* from the surviving clients, not just
 * carried over. A 2.4 GHz client and a 5 GHz client at the same spot report
 * very different RSSI, so a cell radius or median that still reflected the
 * hidden band would describe something other than what's on screen.
 *
 * The maths mirrors api/routers/maps/rf.py — keep the two in step.
 */

import { TIER_ORDER, tierForRssi } from "./mapColors";
import type { MapAp, MapClient, RssiStats } from "./types";

/** Label used for clients whose band R1 didn't report. */
export const UNKNOWN_BAND = "Unknown";

export function bandKey(client: MapClient): string {
  return client.band || UNKNOWN_BAND;
}

/** Bands present in the data, ordered 2.4 → 5 → 6, unknown last. */
export function availableBands(aps: MapAp[]): string[] {
  const bands = new Set<string>();
  for (const ap of aps) {
    for (const client of ap.clients) bands.add(bandKey(client));
  }

  return Array.from(bands).sort((a, b) => {
    if (a === UNKNOWN_BAND) return 1;
    if (b === UNKNOWN_BAND) return -1;
    const numeric = (value: string) => parseFloat(value.replace(/[^\d.]/g, "")) || 0;
    return numeric(a) - numeric(b);
  });
}

/** Linear-interpolated percentile. pct is 0-100. */
export function percentile(values: number[], pct: number): number | null {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  if (ordered.length === 1) return ordered[0];

  const rank = (pct / 100) * (ordered.length - 1);
  const low = Math.floor(rank);
  const high = Math.ceil(rank);
  if (low === high) return ordered[low];
  return ordered[low] + (ordered[high] - ordered[low]) * (rank - low);
}

export function summarizeRssi(values: number[]): RssiStats {
  const tiers: Record<string, number> = Object.fromEntries(
    TIER_ORDER.map((tier) => [tier, 0])
  );

  if (!values.length) {
    return {
      count: 0,
      min: null,
      max: null,
      mean: null,
      median: null,
      p10: null,
      p90: null,
      tiers,
    };
  }

  for (const value of values) {
    const tier = tierForRssi(value);
    tiers[tier] = (tiers[tier] || 0) + 1;
  }

  const sum = values.reduce((total, value) => total + value, 0);
  return {
    count: values.length,
    min: Math.min(...values),
    max: Math.max(...values),
    mean: Math.round((sum / values.length) * 10) / 10,
    median: percentile(values, 50),
    p10: percentile(values, 10),
    p90: percentile(values, 90),
    tiers,
  };
}

export interface FilteredOverlay {
  aps: MapAp[];
  clientCount: number;
  rssi: RssiStats;
  /** Clients hidden by the current band selection. */
  hiddenCount: number;
}

/**
 * Apply a band selection and rebuild every derived number from what survives.
 *
 * `cellPercentile` must be the value the overlay was fetched with, so the
 * recomputed radius means the same thing as the server's.
 */
export function applyBandFilter(
  aps: MapAp[],
  selectedBands: Set<string>,
  cellPercentile: number
): FilteredOverlay {
  const filteredAps: MapAp[] = [];
  const allRssi: number[] = [];
  let clientCount = 0;
  let hiddenCount = 0;

  for (const ap of aps) {
    const clients = ap.clients.filter((client) => {
      const keep = selectedBands.has(bandKey(client));
      if (!keep) hiddenCount += 1;
      return keep;
    });

    const rssiValues: number[] = [];
    const distances: number[] = [];
    const bands: Record<string, number> = {};
    const ssids: Record<string, number> = {};

    for (const client of clients) {
      if (client.rssi !== null) {
        rssiValues.push(client.rssi);
        allRssi.push(client.rssi);
      }
      if (client.estimated_distance_m !== null) {
        distances.push(client.estimated_distance_m);
      }
      const band = bandKey(client);
      bands[band] = (bands[band] || 0) + 1;
      if (client.ssid) ssids[client.ssid] = (ssids[client.ssid] || 0) + 1;
    }

    const cellRadius = percentile(distances, cellPercentile);
    const medianDistance = percentile(distances, 50);

    clientCount += clients.length;
    filteredAps.push({
      ...ap,
      clients,
      client_count: clients.length,
      rssi_stats: summarizeRssi(rssiValues),
      bands,
      ssids,
      cell_radius_m: cellRadius !== null ? Math.round(cellRadius * 100) / 100 : null,
      median_distance_m:
        medianDistance !== null ? Math.round(medianDistance * 100) / 100 : null,
    });
  }

  return {
    aps: filteredAps,
    clientCount,
    rssi: summarizeRssi(allRssi),
    hiddenCount,
  };
}
