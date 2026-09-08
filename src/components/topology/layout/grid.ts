import type { LayoutEngine, LayoutInput, LayoutResult } from "./types";
import { optionValue } from "./types";

/**
 * A plain ordered grid.
 *
 * No pretence of showing topology — it shows INVENTORY, in an order you choose,
 * with nothing overlapping. Useful when you want to find a device by name, or
 * to see everything at once without a hierarchy stretching one rank to 13,000
 * pixels.
 */

const ORDERS: Record<string, (a: GridNode, b: GridNode) => number> = {
  name: (a, b) => (a.label || a.id).localeCompare(b.label || b.id),
  kind: (a, b) =>
    (a.kind || "").localeCompare(b.kind || "") ||
    (a.label || a.id).localeCompare(b.label || b.id),
  degree: (a, b) => (b.degree ?? 0) - (a.degree ?? 0),
};

type GridNode = {
  id: string;
  label?: string;
  kind?: string;
  degree?: number;
  width: number;
  height: number;
};

export const gridEngine: LayoutEngine = {
  id: "grid",
  label: "Grid",
  description:
    "Everything in rows, in the order you pick. Shows inventory rather than " +
    "topology — good for finding a device by name.",
  supportsGroups: false,
  supportsPinning: true,
  supportsEdgeRoutes: false,
  incremental: false,
  restartOnSettingsChange: true,
  options: [
    {
      key: "columns",
      label: "Columns",
      kind: "number",
      min: 2,
      max: 40,
      step: 1,
      def: 12,
    },
    {
      key: "gap",
      label: "Gap",
      kind: "number",
      min: 4,
      max: 120,
      step: 4,
      def: 24,
    },
    {
      key: "order",
      label: "Order by",
      kind: "select",
      def: "kind",
      choices: [
        { value: "kind", label: "Type, then name" },
        { value: "name", label: "Name" },
        { value: "degree", label: "Most connected first" },
      ],
    },
  ],

  async run(input: LayoutInput): Promise<LayoutResult> {
    const started = performance.now();
    const columns = Math.max(1, Number(optionValue(input, "columns", 12)));
    const gap = Number(optionValue(input, "gap", 24));
    const order = String(optionValue(input, "order", "kind"));

    const sorted = [...input.nodes].sort(ORDERS[order] ?? ORDERS.kind);

    // Uniform cells, sized to the widest and tallest node, so nothing overlaps
    // however wide an expanded port strip makes one of them.
    const cellW = Math.max(...sorted.map((n) => n.width), 1) + gap;
    const cellH = Math.max(...sorted.map((n) => n.height), 1) + gap;

    const positions: Record<string, { x: number; y: number }> = {};
    sorted.forEach((node, index) => {
      positions[node.id] = node.pinned ?? {
        x: (index % columns) * cellW,
        y: Math.floor(index / columns) * cellH,
      };
    });

    if (input.signal?.aborted) {
      throw new DOMException("layout aborted", "AbortError");
    }
    return {
      positions,
      meta: {
        engine: "grid",
        elapsedMs: Math.round(performance.now() - started),
      },
    };
  },
};
