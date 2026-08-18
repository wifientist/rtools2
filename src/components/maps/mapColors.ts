/**
 * Color tokens for the Maps overlay.
 *
 * Two encodings, deliberately never on screen at the same time (the overlay
 * mode switches between them) so a given hue means exactly one thing:
 *
 *  - Signal health — a diverging blue↔red ramp across the four RSSI tiers,
 *    split at the -70 dBm usability line. Blue = strong, red = weak. Chosen
 *    over the conventional green→red because green↔red is the classic
 *    color-vision failure: this set clears CVD separation (worst all-pairs
 *    deutan ΔE 13.9), the normal-vision floor (15.7), and the lightness band.
 *    Two steps sit below 3:1 on a pale surface, so tiers are always shown with
 *    a name and a dBm value, never by color alone.
 *
 *  - Client density — a single-hue sequential blue ramp, light→dark, because
 *    density is a magnitude with no meaningful midpoint.
 */

export type RssiTier = "excellent" | "good" | "fair" | "poor" | "unknown";

export const TIER_ORDER: RssiTier[] = ["excellent", "good", "fair", "poor"];

export const TIER_COLORS: Record<RssiTier, string> = {
  excellent: "#1c5cab",
  good: "#5598e7",
  fair: "#ec835a",
  poor: "#d03b3b",
  unknown: "#898781",
};

export const TIER_LABELS: Record<RssiTier, string> = {
  excellent: "Excellent",
  good: "Good",
  fair: "Fair",
  poor: "Poor",
  unknown: "No RSSI",
};

/** Inclusive upper bound / exclusive lower bound of each tier, in dBm. */
export const TIER_RANGES: Record<RssiTier, string> = {
  excellent: "≥ -60 dBm",
  good: "-70 to -60 dBm",
  fair: "-80 to -70 dBm",
  poor: "< -80 dBm",
  unknown: "not reported",
};

export function tierForRssi(rssi: number | null | undefined): RssiTier {
  if (rssi === null || rssi === undefined) return "unknown";
  if (rssi >= -60) return "excellent";
  if (rssi >= -70) return "good";
  if (rssi >= -80) return "fair";
  return "poor";
}

/** Sequential blue ramp, light→dark, for the density heat layer. */
export const DENSITY_RAMP = [
  "#cde2fb",
  "#b7d3f6",
  "#9ec5f4",
  "#86b6ef",
  "#6da7ec",
  "#5598e7",
  "#3987e5",
  "#2a78d6",
  "#256abf",
  "#1c5cab",
  "#184f95",
  "#104281",
  "#0d366b",
];

const RAMP_RGB = DENSITY_RAMP.map((hex) => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
]);

/** Sample the density ramp at t in [0,1]. Returns [r,g,b]. */
export function densityColorAt(t: number): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, t));
  const index = Math.min(RAMP_RGB.length - 1, Math.floor(clamped * RAMP_RGB.length));
  const rgb = RAMP_RGB[index];
  return [rgb[0], rgb[1], rgb[2]];
}

/** Chart chrome — matches the tokens the rest of the overlay is drawn against. */
export const INK = {
  surface: "#fcfcfb",
  primary: "#0b0b0b",
  secondary: "#52514e",
  muted: "#898781",
  gridline: "#e1e0d9",
  border: "rgba(11,11,11,0.10)",
};
