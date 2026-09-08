import type { Tier } from "./state/types";

/**
 * Encodings, kept on separate channels and never conflated.
 *
 *   DASH + WIDTH + OPACITY carry CONFIDENCE, always. Confidence is the one
 *   thing on this map that must never be misread, so it never depends on hue
 *   and the map stays correct in greyscale and to a colour-blind viewer.
 *
 *   HUE carries whatever the PAINT mode says:
 *     "kind"    — link kind (ethernet / lag / stack / mesh / wan), the default
 *     "neutral" — nothing. Every link is grey, which frees hue entirely for an
 *                 overlay to answer one question at a time.
 *
 * In neutral paint, kind does not simply vanish: stack and mesh — the two kinds
 * worth telling apart at a glance, and the two that are rare — keep a midpoint
 * glyph, and a bundle keeps its count. That is the "kind moves to line texture"
 * option from plans/topology.md §3c.
 *
 * Palette follows the reasoning already documented in
 * components/maps/mapColors.ts.
 */

export const INK = {
  text: "#111827",
  muted: "#6b7280",
  faint: "#9ca3af",
  line: "#d1d5db",
  panel: "#ffffff",
};

export const KIND_COLOR: Record<string, string> = {
  ethernet: "#2563eb", // blue — the ordinary case
  lag: "#7c3aed", // violet — a bundle of several
  stack: "#0891b2", // cyan — inside one chassis
  mesh: "#ea580c", // orange — wireless
  wan: "#0d9488", // teal — the way out
  "wireless-client": "#9ca3af",
};

export type TierStyle = {
  strokeWidth: number;
  opacity: number;
  dash?: string;
  badge: string;
  label: string;
  blurb: string;
};

export const TIER_STYLE: Record<Tier, TierStyle> = {
  confirmed: {
    strokeWidth: 2.5,
    opacity: 1,
    badge: "✓",
    label: "Confirmed",
    blurb:
      "Two independent devices name each other, or the chassis reports its " +
      "own assembly, or you confirmed it.",
  },
  strong: {
    strokeWidth: 2,
    opacity: 0.9,
    badge: "",
    label: "Strong",
    blurb: "One end reports it directly, with corroboration.",
  },
  probable: {
    strokeWidth: 1.75,
    opacity: 0.75,
    dash: "6 3",
    badge: "?",
    label: "Probable",
    blurb: "Inferred from indirect evidence such as the MAC table.",
  },
  weak: {
    strokeWidth: 1.5,
    opacity: 0.5,
    dash: "2 4",
    badge: "?",
    label: "Weak",
    blurb: "Supporting evidence is outweighed by what contradicts it.",
  },
  rejected: {
    strokeWidth: 1,
    opacity: 0.25,
    dash: "2 4",
    badge: "✕",
    label: "Rejected",
    blurb:
      "Kept so 'why is there no edge here?' stays answerable — something " +
      "better claimed the same port, or a human ruled it out.",
  },
};

export const STATUS_COLOR: Record<string, string> = {
  online: "#16a34a",
  offline: "#dc2626",
  degraded: "#d97706",
  pending: "#0891b2",
  unknown: "#9ca3af",
};

export const DEVICE_ACCENT: Record<string, string> = {
  switch: "#2563eb",
  stack: "#4f46e5",
  ap: "#0891b2",
  external: "#6b7280",
  wan: "#0d9488",
  client: "#9ca3af",
  unknown: "#9ca3af",
};

export function linkStroke(kind: string): string {
  return KIND_COLOR[kind] ?? KIND_COLOR.ethernet;
}

/** Grey default, so colour is free to mean whatever an overlay is asking. */
export const NEUTRAL_LINK = "#94a3b8";

/**
 * Overlay hues, for the links an overlay is speaking about. Distinguishable
 * from each other AND from NEUTRAL_LINK at a glance, and each is paired with a
 * non-colour cue at the point of use so the overlay is never colour alone.
 */
export const OVERLAY_COLOR = {
  /** Worth investigating. Amber, not red: a ring is a question, not a fault. */
  attention: "#d97706",
  /** Explained — redundancy that is already bonded. */
  benign: "#059669",
  /** One ring told apart from another when several overlap. */
  ring: ["#d97706", "#c026d3", "#0284c7", "#65a30d", "#e11d48", "#7c3aed"],
};

/** A short, non-colour marker for the kinds worth telling apart at a glance. */
export const KIND_GLYPH: Record<string, string> = {
  stack: "▣",
  mesh: "≈",
  wan: "↑",
};

export type PaintMode = "kind" | "neutral";

/** The hue for a link, before any overlay has its say. */
export function basePaint(kind: string, paint: PaintMode): string {
  return paint === "neutral" ? NEUTRAL_LINK : linkStroke(kind);
}
