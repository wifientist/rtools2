import dagre from "@dagrejs/dagre";
import type { LayoutEngine, LayoutInput, LayoutResult } from "./types";
import { optionValue } from "./types";

/**
 * Hierarchical layout: core at the top, access below, APs at the leaves.
 *
 * This is THE layout for a network topology, and dagre carries it alone because
 * elkjs was rejected on licensing (EPL-2.0 against an otherwise all-MIT tree).
 * dagre handles trees well; it is weaker with redundant links and rings and has
 * no port-level anchoring. If that shows on meshy sites, elk drops in behind
 * `LayoutEngine` without touching callers.
 */

export const dagreEngine: LayoutEngine = {
  id: "dagre",
  label: "Hierarchy",
  description:
    "Layered: the core at one end, access switches below it, APs at the " +
    "leaves. Best for seeing how the network is tiered.",
  supportsGroups: false,
  supportsPinning: false,
  // dagre DOES compute routed points that bend around nodes; we just have to
  // ask for them instead of drawing straight lines and hoping.
  supportsEdgeRoutes: true,
  incremental: false,
  restartOnSettingsChange: true,
  options: [
    {
      key: "rankSpacing",
      label: "Tier spacing",
      kind: "number",
      min: 40,
      max: 400,
      step: 10,
      def: 104,
      help: "Gap between one tier and the next.",
    },
    {
      key: "nodeSpacing",
      label: "Sibling spacing",
      kind: "number",
      min: 4,
      max: 120,
      step: 2,
      def: 18,
      help:
        "Gap between neighbours in the same tier. The single biggest lever on " +
        "how wide a flat hierarchy gets.",
    },
    {
      key: "edgeSpacing",
      label: "Edge spacing",
      kind: "number",
      min: 2,
      max: 60,
      step: 2,
      def: 12,
      help:
        "How far apart parallel edges are kept where they run together. " +
        "Raising it separates a bundle of links that would otherwise overlap.",
    },
    {
      key: "routeEdges",
      label: "Route edges around nodes",
      kind: "boolean",
      def: false,
      help:
        "Follow the path dagre computes instead of drawing straight. MEASURED " +
        "WORSE on a real 130-edge venue: 491 crossings straight vs 538 routed, " +
        "because adjacent-rank routes are near-straight and smoothing through " +
        "them only adds wiggles. Left here for deep hierarchies where it may " +
        "earn its keep.",
    },
    {
      key: "anchorMode",
      label: "Edge anchors",
      kind: "select",
      def: "rank",
      choices: [
        { value: "rank", label: "Follow the tiers" },
        { value: "geometry", label: "Nearest sides" },
      ],
      help:
        "Whether a line leaves in the direction of the layout, or out of " +
        "whichever side faces the other device.",
    },
    {
      key: "ranker",
      label: "Tier assignment",
      kind: "select",
      def: "network-simplex",
      choices: [
        { value: "network-simplex", label: "Balanced (default)" },
        { value: "tight-tree", label: "Tight tree" },
        { value: "longest-path", label: "Longest path" },
      ],
      help:
        "How dagre decides which tier a node belongs to. Tight tree keeps " +
        "related nodes closer; longest path pushes leaves as deep as possible.",
    },
    {
      key: "align",
      label: "Alignment",
      kind: "select",
      def: "none",
      choices: [
        { value: "none", label: "Centred" },
        { value: "UL", label: "Up-left" },
        { value: "UR", label: "Up-right" },
        { value: "DL", label: "Down-left" },
        { value: "DR", label: "Down-right" },
      ],
    },
  ],

  async run(input: LayoutInput): Promise<LayoutResult> {
    const started = performance.now();
    const graph = new dagre.graphlib.Graph();
    graph.setDefaultEdgeLabel(() => ({}));

    const align = optionValue(input, "align", "none");
    graph.setGraph({
      rankdir: input.direction,
      nodesep: optionValue(input, "nodeSpacing", input.spacing.node),
      ranksep: optionValue(input, "rankSpacing", input.spacing.rank),
      ranker: optionValue(input, "ranker", "network-simplex"),
      edgesep: optionValue(input, "edgeSpacing", 12),
      ...(align === "none" ? {} : { align }),
      marginx: 40,
      marginy: 40,
    });

    input.nodes.forEach((node) => {
      graph.setNode(node.id, { width: node.width, height: node.height });
    });
    // dagre needs both endpoints; a filtered view can leave an edge dangling.
    const present = new Set(input.nodes.map((n) => n.id));
    const usable = input.edges.filter(
      (e) => present.has(e.source) && present.has(e.target),
    );

    // ROOTING. dagre puts an edge's target BELOW its source, and our edge
    // direction is whatever the merge happened to settle on — so a confirmed
    // WAN uplink landed near the BOTTOM of the hierarchy, measured at position
    // 208 of 224. Given roots, orient every edge along a breadth-first walk
    // outward from them, which makes the tiers actually flow from the way out
    // of the network downward.
    const roots = input.roots.filter((id) => present.has(id));
    let oriented = usable.map((e) => [e.source, e.target] as [string, string]);
    if (roots.length) {
      const neighbours = new Map<string, string[]>();
      usable.forEach((e) => {
        (neighbours.get(e.source) ?? neighbours.set(e.source, []).get(e.source)!).push(e.target);
        (neighbours.get(e.target) ?? neighbours.set(e.target, []).get(e.target)!).push(e.source);
      });
      const depth = new Map<string, number>();
      const queue = [...roots];
      roots.forEach((id) => depth.set(id, 0));
      while (queue.length) {
        const id = queue.shift()!;
        const here = depth.get(id)!;
        (neighbours.get(id) ?? []).forEach((next) => {
          if (depth.has(next)) return;
          depth.set(next, here + 1);
          queue.push(next);
        });
      }
      // Point each edge from the shallower end to the deeper one. An edge
      // between two equally deep nodes keeps whatever order it had.
      oriented = usable.map((e) => {
        const a = depth.get(e.source);
        const b = depth.get(e.target);
        if (a !== undefined && b !== undefined && b < a) {
          return [e.target, e.source] as [string, string];
        }
        return [e.source, e.target] as [string, string];
      });
    }
    oriented.forEach(([from, to]) => graph.setEdge(from, to));

    dagre.layout(graph);
    if (input.signal?.aborted) {
      throw new DOMException("layout aborted", "AbortError");
    }

    const positions: Record<string, { x: number; y: number }> = {};
    input.nodes.forEach((node) => {
      const laid = graph.node(node.id);
      if (!laid) return;
      // dagre centres nodes; React Flow positions by top-left corner.
      positions[node.id] = {
        x: laid.x - node.width / 2,
        y: laid.y - node.height / 2,
      };
    });

    // dagre cannot honour a fixed position, so pinned nodes are restored after
    // the fact. Declared as supportsPinning: false rather than hidden.
    input.nodes.forEach((node) => {
      if (node.pinned) positions[node.id] = node.pinned;
    });

    // Dagre's routed points are the whole reason a layered layout looks tidy:
    // the ordering phase minimises crossings, and the routing phase bends each
    // edge around whatever sits between its endpoints. Drawing a straight line
    // between the two boxes throws both away and puts the crossings back.
    let edgeRoutes: LayoutResult["edgeRoutes"];
    if (optionValue(input, "routeEdges", true)) {
      edgeRoutes = {};
      usable.forEach((edge) => {
        const laid = (graph.edge(edge.source, edge.target) ??
          graph.edge(edge.target, edge.source)) as
          | { points?: { x: number; y: number }[] }
          | undefined;
        // A pinned endpoint invalidates the route dagre computed for it.
        const moved =
          input.nodes.some(
            (n) => n.pinned && (n.id === edge.source || n.id === edge.target),
          );
        if (laid?.points?.length && !moved) {
          edgeRoutes![edge.id] = { points: laid.points };
        }
      });
    }

    return {
      positions,
      edgeRoutes,
      meta: {
        engine: "dagre",
        elapsedMs: Math.round(performance.now() - started),
      },
    };
  },
};
