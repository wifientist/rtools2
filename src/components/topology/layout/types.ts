/**
 * The layout adapter interface.
 *
 * Every engine is async and cancellable — even the trivial ones — so the caller
 * has exactly one code path and switching engines mid-run can abort the old
 * one. This is also what keeps elkjs droppable later: it would be a licensing
 * decision, not a rewrite.
 *
 * Engines declare their OWN options. The toolbar renders controls from that
 * declaration, so adding an engine adds its controls with no UI change — and
 * an option that only means something to one engine cannot leak into another.
 */

export type NodeKind =
  | "switch"
  | "stack"
  | "ap"
  | "client"
  | "external"
  | "wan"
  | "group"
  | "unknown";

export type LayoutNode = {
  id: string;
  width: number;
  height: number;
  parentId?: string;
  kind: NodeKind;
  /** 0 = WAN, 1 = core, 2 = distribution, 3 = access, 4 = AP. */
  rank?: number;
  pinned?: { x: number; y: number };
  /** Real placement on a floorplan, when the controller has one. */
  geo?: { floorplanId: string; xPercent: number; yPercent: number };
  /** For engines that order rather than simulate. */
  label?: string;
  degree?: number;
};

export type LayoutEdge = {
  id: string;
  source: string;
  target: string;
  kind: string;
  /** Member count for bundles; force uses it as link strength. */
  weight?: number;
};

export type LayoutInput = {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  /** Confirmed WAN nodes. Hierarchical engines root here. */
  roots: string[];
  direction: "TB" | "LR" | "BT" | "RL";
  spacing: { node: number; rank: number; group: number };
  /** This engine's own settings, keyed by its option definitions. */
  options: Record<string, number | string | boolean>;
  signal?: AbortSignal;
};

export type LayoutResult = {
  positions: Record<string, { x: number; y: number }>;
  sizes?: Record<string, { width: number; height: number }>;
  edgeRoutes?: Record<string, { points: { x: number; y: number }[] }>;
  meta: {
    engine: string;
    elapsedMs: number;
    iterations?: number;
    converged?: boolean;
    /** Anything the engine wants to tell the user about what it did. */
    note?: string;
  };
};

export type LayoutOption =
  | {
      key: string;
      label: string;
      kind: "number";
      min: number;
      max: number;
      step: number;
      def: number;
      help?: string;
    }
  | {
      key: string;
      label: string;
      kind: "select";
      choices: { value: string; label: string }[];
      def: string;
      help?: string;
    }
  | {
      key: string;
      label: string;
      kind: "boolean";
      def: boolean;
      help?: string;
    };

export type LayoutEngine = {
  id: string;
  label: string;
  /** Shown in the toolbar tooltip. */
  description: string;
  supportsGroups: boolean;
  /**
   * TRUE only where the engine itself honours a fixed position — force (fx/fy)
   * and manual. dagre has no such concept, so useLayout restores pinned nodes
   * afterwards and the UI greys out the pin affordance rather than pretending.
   */
  supportsPinning: boolean;
  supportsEdgeRoutes: boolean;
  /** Can warm-start from a previous result. */
  incremental: boolean;
  /**
   * Whether changing a setting should RE-RUN FROM SCRATCH rather than refine.
   *
   * A simulation must restart, or dragging its slider only nudges an
   * already-settled graph and the control reads as broken. "Manual" must NOT:
   * its previous result IS the arrangement, and discarding it would scatter
   * everything the user placed.
   */
  restartOnSettingsChange: boolean;
  options: LayoutOption[];
  /**
   * Whether this engine can do anything useful with the graph in hand.
   *
   * `geo` needs real floorplan coordinates; without them it would stack every
   * device on the origin while looking like it worked. An engine that cannot
   * run says so, with a reason, instead of producing a confidently wrong map.
   */
  available?: (nodes: LayoutNode[]) => { ok: boolean; reason?: string };
  run(input: LayoutInput, previous?: LayoutResult): Promise<LayoutResult>;
};

/** Defaults for one engine, as a plain object. */
export function defaultOptions(
  engine: LayoutEngine,
): Record<string, number | string | boolean> {
  return Object.fromEntries(engine.options.map((o) => [o.key, o.def]));
}

export function optionValue<T extends number | string | boolean>(
  input: LayoutInput,
  key: string,
  fallback: T,
): T {
  const value = input.options?.[key];
  return (value === undefined ? fallback : value) as T;
}
