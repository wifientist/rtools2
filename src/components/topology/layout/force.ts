import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import type { SimulationLinkDatum, SimulationNodeDatum } from "d3-force";
import type { LayoutEngine, LayoutInput, LayoutResult } from "./types";
import { optionValue } from "./types";

interface Sim extends SimulationNodeDatum {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

type SimLink = SimulationLinkDatum<Sim> & { weight: number };

/**
 * Force-directed: let the graph settle into whatever shape its connections
 * imply.
 *
 * Good for the question "what clusters with what" on a site whose hierarchy you
 * do not already know, and for spotting a switch that is oddly far from
 * everything it should be near. Poor at showing deliberate tiering — that is
 * what Hierarchy is for.
 *
 * Ticked in bounded batches with a yield between them rather than on a worker.
 * A worker would be better for thousands of nodes, but it also means shipping
 * d3-force twice and marshalling the graph across a boundary; batching keeps
 * the UI responsive at the sizes this actually runs on, and the abort signal
 * gets honoured between batches so switching engines mid-run stops the work.
 */

const BATCH = 40;

export const forceEngine: LayoutEngine = {
  id: "force",
  label: "Organic",
  description:
    "Physics: linked devices pull together, everything pushes apart. Shows " +
    "clustering rather than hierarchy.",
  supportsGroups: false,
  // d3-force honours a fixed position natively through fx/fy.
  supportsPinning: true,
  supportsEdgeRoutes: false,
  incremental: true,
  restartOnSettingsChange: true,
  options: [
    {
      key: "linkDistance",
      label: "Link length",
      kind: "number",
      min: 20,
      max: 600,
      step: 10,
      def: 180,
      help: "How far apart the simulation wants two connected devices to sit.",
    },
    {
      key: "charge",
      label: "Repulsion",
      kind: "number",
      min: 50,
      max: 4000,
      step: 50,
      def: 900,
      help: "How hard every device pushes every other away. Raise it to spread a dense cluster.",
    },
    {
      key: "iterations",
      label: "Settling passes",
      kind: "number",
      min: 60,
      max: 800,
      step: 20,
      def: 300,
      help: "More passes settle further but take longer.",
    },
    {
      key: "gravity",
      label: "Pull to centre",
      kind: "number",
      min: 0,
      max: 0.2,
      step: 0.005,
      def: 0.03,
      help: "Stops disconnected pieces drifting away for ever.",
    },
  ],

  async run(input: LayoutInput, previous?: LayoutResult): Promise<LayoutResult> {
    const started = performance.now();
    const linkDistance = optionValue(input, "linkDistance", 180);
    const charge = optionValue(input, "charge", 900);
    const iterations = optionValue(input, "iterations", 300);
    const gravity = optionValue(input, "gravity", 0.03);

    // Warm-start from wherever things already are: re-running the simulation
    // should refine the picture, not throw it away and start from noise.
    const radius = Math.max(300, Math.sqrt(input.nodes.length) * 90);
    const nodes: Sim[] = input.nodes.map((node, index) => {
      const seed = previous?.positions[node.id];
      const angle = (index / Math.max(1, input.nodes.length)) * Math.PI * 2;
      return {
        id: node.id,
        x: seed?.x ?? Math.cos(angle) * radius,
        y: seed?.y ?? Math.sin(angle) * radius,
        w: node.width,
        h: node.height,
        ...(node.pinned ? { fx: node.pinned.x, fy: node.pinned.y } : {}),
      };
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links: SimLink[] = input.edges
      .filter((e) => byId.has(e.source) && byId.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, weight: e.weight ?? 1 }));

    const sim = forceSimulation<Sim>(nodes)
      .force(
        "link",
        forceLink<Sim, SimLink>(links)
          .id((d) => d.id)
          .distance(linkDistance)
          .strength((l) => Math.min(1, 0.25 * (l.weight ?? 1))),
      )
      .force("charge", forceManyBody<Sim>().strength(-charge))
      .force(
        "collide",
        forceCollide<Sim>().radius((d) => Math.hypot(d.w, d.h) / 2 + 6),
      )
      .force("center", forceCenter<Sim>(0, 0))
      .force("x", forceX<Sim>(0).strength(gravity))
      .force("y", forceY<Sim>(0).strength(gravity))
      .stop();

    for (let done = 0; done < iterations; done += BATCH) {
      const run = Math.min(BATCH, iterations - done);
      for (let i = 0; i < run; i += 1) sim.tick();
      if (input.signal?.aborted) {
        sim.stop();
        throw new DOMException("layout aborted", "AbortError");
      }
      // Hand the main thread back so the page stays responsive mid-settle.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    sim.stop();

    const positions: Record<string, { x: number; y: number }> = {};
    nodes.forEach((node) => {
      // Simulation coordinates are centres; React Flow wants top-left.
      positions[node.id] = { x: node.x - node.w / 2, y: node.y - node.h / 2 };
    });

    return {
      positions,
      meta: {
        engine: "force",
        elapsedMs: Math.round(performance.now() - started),
        iterations,
      },
    };
  },
};
