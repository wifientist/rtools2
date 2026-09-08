import { createStore, useStore } from "zustand";
import { createContext, useContext } from "react";
import type { Device, Link, OverlayId, Port, Tier, UplinkState } from "./types";
import type { PaintMode } from "../colors";
import { TIER_RANK } from "./types";

/**
 * Graph state for one Topology page.
 *
 * PAGE-LOCAL, not a module singleton: the store is created in TopologyShell and
 * handed down through context, so it cannot leak into the rest of the app and
 * is confined to this directory if the pattern is ever unwound.
 *
 * zustand rather than useState + context, for one reason: selector granularity.
 * Hovering an edge must not re-render 2000 memoised nodes, and a context value
 * re-renders every consumer on every change. useReducer fixes the update
 * ergonomics but not the subscriptions.
 */

export type LayoutEngineId = "dagre" | "force" | "manual" | "grid" | "geo";

/** The arrangement, as it is stored per venue set. */
export type StoredLayout = {
  pinned?: Record<string, { x: number; y: number }>;
  collapsed?: Record<string, boolean>;
  portsOpen?: Record<string, boolean>;
  engine?: string;
  engineOptions?: Record<string, Record<string, number | string | boolean>>;
  direction?: "TB" | "LR";
  groupVenues?: boolean;
};

/** What the current state would be saved as. */
export function layoutPayload(state: TopologyState): StoredLayout {
  return {
    pinned: state.pinned,
    collapsed: state.collapsed,
    portsOpen: state.portsOpen,
    engine: state.engine,
    engineOptions: state.engineOptions,
    direction: state.direction,
    groupVenues: state.groupVenues,
  };
}

export type TopologyState = {
  // ── data ────────────────────────────────────────────────────────────────
  devices: Device[];
  links: Link[];
  devicesById: Record<string, Device>;
  loading: boolean;
  error: string;

  // ── view ────────────────────────────────────────────────────────────────
  engine: LayoutEngineId;
  /** Per-engine settings, so switching engines does not lose your tuning. */
  engineOptions: Record<string, Record<string, number | string | boolean>>;
  direction: "TB" | "LR";
  minTier: Tier;
  showRejected: boolean;
  showAps: boolean;
  /** What hue means right now. See colors.ts. */
  paint: PaintMode;
  /** Which question colour is currently answering. */
  overlay: OverlayId;
  /**
   * Canvas edge id -> how the running overlay paints it. Computed once per
   * overlay change in TopologyCanvas and read per-edge, so switching overlay
   * does not re-run a graph walk inside two thousand edge components.
   */
  overlayEdges: Record<string, { color: string; note: string }>;
  overlaySummary: { total: number; attention: number } | null;
  /**
   * True only while an image export is capturing.
   *
   * Culling is what makes a big graph usable, and it is also what would make an
   * exported picture a lie -- the exporter serialises the DOM, and the DOM only
   * holds what is on screen. Measured: a 420-node graph keeps 27 nodes mounted.
   */
  exporting: boolean;
  positions: Record<string, { x: number; y: number }>;
  /** Routed paths the engine computed, when it computes them. */
  edgeRoutes: Record<string, { points: { x: number; y: number }[] }>;
  pinned: Record<string, { x: number; y: number }>;
  /** Group id -> collapsed. Absent means expanded. */
  collapsed: Record<string, boolean>;
  groupVenues: boolean;
  /** Device id -> its port strip is open. Ports are fetched on demand. */
  portsOpen: Record<string, boolean>;
  ports: Record<string, Port[]>;
  portsLoading: Record<string, boolean>;

  // ── persistence ─────────────────────────────────────────────────────────
  /** Server version of the stored arrangement; sent back for the write check. */
  layoutVersion: number;
  /** Something persistable changed since the last successful save. */
  layoutDirty: boolean;
  layoutSaving: boolean;
  /** Set when the server refused a stale write, so the UI can offer a choice. */
  layoutConflict: number | null;
  laidOut: boolean;
  layingOut: boolean;
  /**
   * Bumped each time a LAYOUT produces new positions — never by a drag.
   * The canvas refits the viewport on this, so it refits after a relayout but
   * not every time the user nudges a node.
   */
  layoutSeq: number;
  /** Crossing pairs in the current arrangement; null when too big to count. */
  crossings: number | null;
  /** WAN candidates and verdicts, per venue. */
  wan: UplinkState | null;

  // ── interaction ─────────────────────────────────────────────────────────
  selectedLinkId: string | null;
  /** Real link ids behind the selection — several when a bundle is selected. */
  selectedLinkIds: string[];
  selectedDeviceId: string | null;
  hoveredId: string | null;

  // ── actions ─────────────────────────────────────────────────────────────
  setGraph: (devices: Device[], links: Link[]) => void;
  /**
   * Replace individual links in place, leaving the arrangement alone.
   *
   * Recording a verdict changes a handful of links and nothing else, so it must
   * not go through setGraph — that resets positions, open groups and port
   * strips, and losing a hand-made layout because you pressed Confirm would be
   * a worse bug than the one this fixes.
   */
  patchLinks: (links: Link[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string) => void;
  setEngine: (engine: LayoutEngineId) => void;
  setEngineOption: (
    engine: string,
    key: string,
    value: number | string | boolean,
  ) => void;
  resetEngineOptions: (
    engine: string,
    defaults: Record<string, number | string | boolean>,
  ) => void;
  setDirection: (direction: "TB" | "LR") => void;
  setMinTier: (tier: Tier) => void;
  setShowRejected: (on: boolean) => void;
  setPaint: (paint: PaintMode) => void;
  /**
   * Choosing an overlay switches paint to neutral, because an overlay competing
   * with kind-hue for the same channel is unreadable. Clearing it puts kind
   * back, so the control is a single decision rather than two that must agree.
   */
  setOverlay: (overlay: OverlayId) => void;
  setOverlayResult: (
    overlayEdges: Record<string, { color: string; note: string }>,
    overlaySummary: { total: number; attention: number } | null,
  ) => void;
  setExporting: (on: boolean) => void;
  setShowAps: (on: boolean) => void;
  toggleCollapse: (id: string) => void;
  setCollapsed: (ids: string[], collapsed: boolean) => void;
  setGroupVenues: (on: boolean) => void;
  togglePorts: (deviceId: string) => void;
  applyLayout: (layout: StoredLayout, version: number) => void;
  markLayoutSaved: (version: number) => void;
  setLayoutSaving: (on: boolean) => void;
  setLayoutConflict: (version: number | null) => void;
  setPorts: (deviceId: string, ports: Port[]) => void;
  setPortsLoading: (deviceId: string, loading: boolean) => void;
  setPositions: (
    positions: Record<string, { x: number; y: number }>,
    edgeRoutes?: Record<string, { points: { x: number; y: number }[] }>,
  ) => void;
  setLayingOut: (on: boolean) => void;
  setCrossings: (n: number | null) => void;
  setWan: (wan: UplinkState | null) => void;
  /**
   * Place a node by hand. `touchedEdgeIds` are the canvas edges that end on it;
   * only their routes are dropped.
   */
  pin: (
    id: string,
    at: { x: number; y: number },
    touchedEdgeIds?: string[],
  ) => void;
  selectLink: (id: string | null, linkIds?: string[]) => void;
  selectDevice: (id: string | null) => void;
  hover: (id: string | null) => void;
  reset: () => void;
};

const initial = {
  devices: [] as Device[],
  links: [] as Link[],
  devicesById: {} as Record<string, Device>,
  loading: false,
  error: "",
  engine: "dagre" as LayoutEngineId,
  engineOptions: {} as Record<string, Record<string, number | string | boolean>>,
  direction: "TB" as const,
  minTier: "weak" as Tier,
  showRejected: false,
  paint: "kind" as PaintMode,
  overlay: "none" as OverlayId,
  overlayEdges: {} as Record<string, { color: string; note: string }>,
  overlaySummary: null as { total: number; attention: number } | null,
  exporting: false,
  showAps: false,
  positions: {},
  edgeRoutes: {} as Record<string, { points: { x: number; y: number }[] }>,
  pinned: {},
  collapsed: {} as Record<string, boolean>,
  groupVenues: false,
  portsOpen: {} as Record<string, boolean>,
  ports: {} as Record<string, Port[]>,
  portsLoading: {} as Record<string, boolean>,
  layoutVersion: 0,
  layoutDirty: false,
  layoutSaving: false,
  layoutConflict: null as number | null,
  laidOut: false,
  layingOut: false,
  layoutSeq: 0,
  crossings: null as number | null,
  wan: null as UplinkState | null,
  selectedLinkId: null,
  selectedLinkIds: [] as string[],
  selectedDeviceId: null,
  hoveredId: null,
};

export function createTopologyStore() {
  return createStore<TopologyState>((set) => ({
    ...initial,

    patchLinks: (incoming) =>
      set((state) => {
        if (!incoming.length) return {};
        const byId = new Map(incoming.map((l) => [l.id, l]));
        return { links: state.links.map((l) => byId.get(l.id) ?? l) };
      }),

    setGraph: (devices, links) =>
      set({
        devices,
        links,
        devicesById: Object.fromEntries(devices.map((d) => [d.id, d])),
        // A new snapshot invalidates the arrangement: node ids may have come
        // and gone, and stale positions would strand them at the origin.
        positions: {},
        edgeRoutes: {},
        laidOut: false,
        // Collapse state is keyed by device/venue id, which a new snapshot may
        // not contain any more. Keeping it would strand groups that no longer
        // exist and silently hide devices that do.
        collapsed: {},
        // Ports belong to a snapshot: keeping them would show one snapshot's
        // port list on another's device.
        portsOpen: {},
        ports: {},
        portsLoading: {},
        selectedLinkId: null,
        selectedLinkIds: [],
        selectedDeviceId: null,
        error: "",
      }),

    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    setEngine: (engine) => set({ engine, laidOut: false, layoutDirty: true }),
    setEngineOption: (engine, key, value) =>
      set((s) => ({
        engineOptions: {
          ...s.engineOptions,
          [engine]: { ...(s.engineOptions[engine] ?? {}), [key]: value },
        },
        laidOut: false,
        layoutDirty: true,
      })),
    resetEngineOptions: (engine, defaults) =>
      set((s) => ({
        engineOptions: { ...s.engineOptions, [engine]: { ...defaults } },
        laidOut: false,
        layoutDirty: true,
      })),
    setDirection: (direction) =>
      set({ direction, laidOut: false, layoutDirty: true }),
    // Filters change which nodes exist, so the layout has to run again.
    setMinTier: (minTier) => set({ minTier, laidOut: false }),
    setShowRejected: (showRejected) => set({ showRejected, laidOut: false }),
    setPaint: (paint) => set({ paint }),
    setOverlay: (overlay) =>
      set({
        overlay,
        paint: overlay === "none" ? "kind" : "neutral",
        // Cleared here rather than left to the effect that recomputes it, so
        // there is never a frame painted with the previous overlay's answer.
        overlayEdges: {},
        overlaySummary: null,
      }),
    setOverlayResult: (overlayEdges, overlaySummary) =>
      set({ overlayEdges, overlaySummary }),
    setExporting: (exporting) => set({ exporting }),
    setShowAps: (showAps) => set({ showAps, laidOut: false }),
    // Collapsing changes which nodes exist, so the layout has to run again.
    toggleCollapse: (id) =>
      set((s) => ({
        collapsed: { ...s.collapsed, [id]: !s.collapsed[id] },
        laidOut: false,
        layoutDirty: true,
      })),
    setCollapsed: (ids, collapsed) =>
      set((s) => {
        const next = { ...s.collapsed };
        ids.forEach((id) => {
          next[id] = collapsed;
        });
        return { collapsed: next, laidOut: false, layoutDirty: true };
      }),
    setGroupVenues: (groupVenues) =>
      set({ groupVenues, laidOut: false, layoutDirty: true }),
    // Opening a port strip changes that node's SIZE, so the layout has to run
    // again or the expanded node overlaps its neighbours.
    togglePorts: (deviceId) =>
      set((s) => ({
        portsOpen: { ...s.portsOpen, [deviceId]: !s.portsOpen[deviceId] },
        layoutDirty: true,
      })),
    // Deliberately does NOT invalidate the layout. The ports arriving changes
    // nothing about where anything sits -- the node's height comes from its
    // port COUNT, which was known before the fetch -- and clearing `laidOut`
    // here meant one click ran the layout twice and refit the viewport twice.
    setPorts: (deviceId, ports) =>
      set((s) => ({ ports: { ...s.ports, [deviceId]: ports } })),
    setPortsLoading: (deviceId, loading) =>
      set((s) => ({ portsLoading: { ...s.portsLoading, [deviceId]: loading } })),

    /**
     * Restore a saved arrangement.
     *
     * Applied AFTER setGraph, which clears the view state because a new
     * snapshot may not contain the same node ids. A layout belongs to the
     * VENUE SET, not to a snapshot, so it has to survive re-discovery — that
     * is the whole point of it being stored separately and never swept.
     *
     * Pinned positions are restored but the layout still runs: dagre places
     * whatever is new and then the pins are reapplied, so a freshly discovered
     * switch gets a sensible spot without disturbing anything arranged by hand.
     */
    applyLayout: (layout, version) =>
      set({
        pinned: layout.pinned ?? {},
        collapsed: layout.collapsed ?? {},
        portsOpen: layout.portsOpen ?? {},
        engine: (layout.engine as LayoutEngineId) ?? "dagre",
        engineOptions: layout.engineOptions ?? {},
        direction: layout.direction ?? "TB",
        groupVenues: layout.groupVenues ?? false,
        layoutVersion: version,
        layoutDirty: false,
        layoutConflict: null,
        laidOut: false,
      }),
    markLayoutSaved: (layoutVersion) =>
      set({ layoutVersion, layoutDirty: false, layoutConflict: null }),
    setLayoutSaving: (layoutSaving) => set({ layoutSaving }),
    setLayoutConflict: (layoutConflict) => set({ layoutConflict }),
    setPositions: (positions, edgeRoutes) =>
      set((s) => ({
        positions,
        edgeRoutes: edgeRoutes ?? {},
        laidOut: true,
        layingOut: false,
        layoutSeq: s.layoutSeq + 1,
      })),
    setLayingOut: (layingOut) => set({ layingOut }),
    setCrossings: (crossings) => set({ crossings }),
    // A confirmed uplink anchors the hierarchy, so a change re-lays out.
    setWan: (wan) => set({ wan, laidOut: false }),
    pin: (id, at, touchedEdgeIds) =>
      set((s) => {
        // A dragged node invalidates the routes that touched IT — a stale route
        // would leave the line behind — but only those. Dropping the whole map's
        // routing on the first drag flattened every carefully routed edge in the
        // graph into a straight bezier, which is a big visible loss for a small
        // local move. Callers that cannot say what they touched still clear
        // everything, because a wrong line is worse than a plain one.
        let edgeRoutes = s.edgeRoutes;
        if (touchedEdgeIds) {
          if (touchedEdgeIds.some((edgeId) => edgeId in s.edgeRoutes)) {
            edgeRoutes = { ...s.edgeRoutes };
            touchedEdgeIds.forEach((edgeId) => delete edgeRoutes[edgeId]);
          }
        } else {
          edgeRoutes = {};
        }
        return {
          pinned: { ...s.pinned, [id]: at },
          positions: { ...s.positions, [id]: at },
          edgeRoutes,
          layoutDirty: true,
        };
      }),
    selectLink: (selectedLinkId, linkIds) =>
      set({
        selectedLinkId,
        selectedLinkIds: linkIds ?? (selectedLinkId ? [selectedLinkId] : []),
        selectedDeviceId: null,
      }),
    selectDevice: (selectedDeviceId) =>
      set({ selectedDeviceId, selectedLinkId: null }),
    hover: (hoveredId) => set({ hoveredId }),
    reset: () => set(initial),
  }));
}

export type TopologyStore = ReturnType<typeof createTopologyStore>;

export const TopologyStoreContext = createContext<TopologyStore | null>(null);

export function useTopology<T>(selector: (state: TopologyState) => T): T {
  const store = useContext(TopologyStoreContext);
  if (!store) {
    throw new Error("useTopology must be used inside <TopologyShell>");
  }
  return useStore(store, selector);
}

export function useTopologyStore(): TopologyStore {
  const store = useContext(TopologyStoreContext);
  if (!store) {
    throw new Error("useTopologyStore must be used inside <TopologyShell>");
  }
  return store;
}

/**
 * Tier filtering, as a PURE FUNCTION OF EXPLICIT ARGUMENTS — deliberately not a
 * selector.
 *
 * Which devices exist and what each link is drawn BETWEEN is decided by
 * state/collapse.ts, so that grouping lives in exactly one place.
 *
 * These build new arrays. zustand v5 compares selector results with Object.is,
 * so a selector that derives an array reports a change on every single read:
 * render -> new array -> "changed" -> render, until React gives up with
 * "Maximum update depth exceeded". Callers select the stable slices they need
 * (state.links, state.showAps, …) and memoise these with useMemo instead.
 */

/**
 * The links the canvas should draw.
 *
 * LAG members are dropped in favour of their bundle: drawing both would show a
 * four-cable trunk as five parallel edges, which reads as a loop.
 */
export function filterLinks(
  links: Link[],
  _devicesById: Record<string, Device>,
  minTier: Tier,
  showRejected: boolean,
): Link[] {
  const floor = TIER_RANK[minTier];
  return links.filter((link) => {
    if (link.tier === "rejected") return showRejected;
    return TIER_RANK[link.tier] >= floor;
  });
}

