import type { LayoutEngine, LayoutInput, LayoutResult } from "./types";
import { optionValue } from "./types";

/**
 * Your arrangement, left alone.
 *
 * Not really an algorithm — it is the "I have already placed these, stop moving
 * them" mode. Anything pinned stays exactly where it was put; anything new
 * (a switch discovered since you arranged the map) is parked in a tidy row
 * below rather than dropped at the origin on top of everything else.
 */

export const manualEngine: LayoutEngine = {
  id: "manual",
  label: "Manual",
  description:
    "Keeps what you arranged. Newly discovered devices are parked in a row " +
    "below instead of being placed automatically.",
  supportsGroups: false,
  supportsPinning: true,
  supportsEdgeRoutes: false,
  incremental: true,
  restartOnSettingsChange: false,
  options: [
    {
      key: "snap",
      label: "Snap to grid",
      kind: "number",
      min: 0,
      max: 80,
      step: 5,
      def: 0,
      help: "0 places freely. Anything higher rounds every position to a grid.",
    },
    {
      key: "gap",
      label: "Parking gap",
      kind: "number",
      min: 8,
      max: 120,
      step: 4,
      def: 24,
      help: "Spacing for devices that have never been placed.",
    },
  ],

  async run(input: LayoutInput, previous?: LayoutResult): Promise<LayoutResult> {
    const started = performance.now();
    const snap = Number(optionValue(input, "snap", 0));
    const gap = Number(optionValue(input, "gap", 24));
    const round = (v: number) => (snap > 0 ? Math.round(v / snap) * snap : v);

    const placed: Record<string, { x: number; y: number }> = {};
    const unplaced: typeof input.nodes = [];
    let lowest = 0;

    input.nodes.forEach((node) => {
      const at = node.pinned ?? previous?.positions[node.id];
      if (at) {
        placed[node.id] = { x: round(at.x), y: round(at.y) };
        lowest = Math.max(lowest, at.y + node.height);
      } else {
        unplaced.push(node);
      }
    });

    // A parking row under everything already arranged, so new devices are
    // obvious and are not hiding beneath something you positioned.
    let x = 0;
    const y = lowest + gap * 2;
    unplaced.forEach((node) => {
      placed[node.id] = { x: round(x), y: round(y) };
      x += node.width + gap;
    });

    if (input.signal?.aborted) {
      throw new DOMException("layout aborted", "AbortError");
    }
    return {
      positions: placed,
      meta: {
        engine: "manual",
        elapsedMs: Math.round(performance.now() - started),
        note: unplaced.length
          ? `${unplaced.length} device${unplaced.length === 1 ? "" : "s"} not placed yet — parked below.`
          : undefined,
      },
    };
  },
};
