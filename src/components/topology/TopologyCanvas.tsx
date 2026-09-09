import { useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { engineOr } from "./layout/registry";
import { defaultOptions } from "./layout/types";
import type { LayoutEdge, LayoutNode, LayoutResult } from "./layout/types";
import {
  GROUP_HEIGHT,
  GROUP_WIDTH,
  NODE_HEIGHT,
  NODE_WIDTH,
  expandedSize,
  nodeTypes,
} from "./nodes";
import { edgeTypes } from "./edges";
import { DEVICE_ACCENT } from "./colors";
import { buildCanvasGraph } from "./state/collapse";
import { findCycles } from "./state/overlays";
import { stripOffsets, withStripOffsets } from "./state/strips";
import type { Box } from "./state/strips";
import { OVERLAY_COLOR } from "./colors";
import { assignAnchors } from "./state/anchors";
import { countCrossings } from "./state/crossings";
import { filterLinks, useTopology } from "./state/store";

/**
 * Footprint of a node as it will actually be drawn.
 *
 * An open port strip makes a switch several times wider and taller, and the
 * layout must know that BEFORE it runs or the expanded node lands on top of its
 * neighbours. Port COUNT is already in the snapshot, so the size is knowable
 * without waiting for the port fetch.
 */
const sizeOf = (
  node: { kind: string; device?: { counts?: Record<string, number> } },
  portsOpen: boolean,
) => {
  if (node.kind !== "device") return { w: GROUP_WIDTH, h: GROUP_HEIGHT };
  if (portsOpen) return expandedSize(node.device?.counts?.ports ?? 0);
  return { w: NODE_WIDTH, h: NODE_HEIGHT };
};

/**
 * Which side of each node an edge should leave from and arrive at.
 *
 * Nodes carry anchors on all four sides. Picking by the actual geometry — the
 * dominant axis between the two boxes — means a sideways link leaves sideways
 * instead of looping out of the top, which is what top/bottom-only anchors do
 * to every horizontal run, and to the entire graph in left-right mode.
 *
 * This is the coarse version: per-NODE. Once port strips land, the same idea
 * applies per-PORT, so an edge can land on the specific port it belongs to.
 */
function handlesFor(
  a: { x: number; y: number } | undefined,
  b: { x: number; y: number } | undefined,
  aSize: { w: number; h: number },
  bSize: { w: number; h: number },
): { sourceHandle: string; targetHandle: string } {
  if (!a || !b) return { sourceHandle: "b", targetHandle: "t" };
  const dx = b.x + bSize.w / 2 - (a.x + aSize.w / 2);
  const dy = b.y + bSize.h / 2 - (a.y + aSize.h / 2);
  if (Math.abs(dx) > Math.abs(dy)) {
    return dx >= 0
      ? { sourceHandle: "r", targetHandle: "l" }
      : { sourceHandle: "l", targetHandle: "r" };
  }
  return dy >= 0
    ? { sourceHandle: "b", targetHandle: "t" }
    : { sourceHandle: "t", targetHandle: "b" };
}

/**
 * The graph canvas.
 *
 * Layout runs off to the side and feeds positions back in — React Flow never
 * computes geometry itself, which is what keeps the engines interchangeable.
 *
 * TWO RULES this file exists to obey, both learned the hard way:
 *
 *  1. Never select a DERIVED ARRAY from the store. zustand v5 compares selector
 *     output with Object.is, so `useTopology(s => s.links.filter(...))` reports
 *     a change on every read and loops until React aborts with "Maximum update
 *     depth exceeded". Select stable slices; derive with useMemo.
 *
 *  2. React Flow is CONTROLLED here, so it needs onNodesChange/onEdgesChange.
 *     Passing `nodes` without them makes dragging silently inert and lets React
 *     Flow's own StoreUpdater fight whatever we pass in.
 */
/**
 * What determines a node's rendered height, as a comparable string.
 *
 * Used to decide whether a measurement can be carried across a node rebuild.
 * Deliberately conservative: anything not listed here counts as "changed", so
 * the failure mode is a needless re-measure rather than a wrong one.
 */
function sizeSig(node: Node): string {
  const data = node.data as {
    portsOpen?: boolean;
    ports?: unknown[];
    node?: { label?: string };
  };
  return [node.type, data?.portsOpen ? "1" : "0", data?.ports?.length ?? "-",
          data?.node?.label ?? ""].join("|");
}

function Canvas() {
  // ── stable slices only ──────────────────────────────────────────────────
  const devices = useTopology((s) => s.devices);
  const allLinks = useTopology((s) => s.links);
  const devicesById = useTopology((s) => s.devicesById);
  const minTier = useTopology((s) => s.minTier);
  const showRejected = useTopology((s) => s.showRejected);
  const showAps = useTopology((s) => s.showAps);
  const collapsed = useTopology((s) => s.collapsed);
  const groupVenues = useTopology((s) => s.groupVenues);
  const portsOpen = useTopology((s) => s.portsOpen);
  const ports = useTopology((s) => s.ports);
  const portsLoading = useTopology((s) => s.portsLoading);
  const engine = useTopology((s) => s.engine);
  const engineOptions = useTopology((s) => s.engineOptions);
  const direction = useTopology((s) => s.direction);
  const laidOut = useTopology((s) => s.laidOut);
  const layoutSeq = useTopology((s) => s.layoutSeq);
  const positions = useTopology((s) => s.positions);
  const edgeRoutes = useTopology((s) => s.edgeRoutes);
  const pinned = useTopology((s) => s.pinned);
  const setPositions = useTopology((s) => s.setPositions);
  const setLayingOut = useTopology((s) => s.setLayingOut);
  const selectLink = useTopology((s) => s.selectLink);
  const selectDevice = useTopology((s) => s.selectDevice);
  const hover = useTopology((s) => s.hover);
  const pin = useTopology((s) => s.pin);

  // ── derived, memoised ───────────────────────────────────────────────────
  // Tier filtering first; the collapse layer then decides what each surviving
  // link is drawn BETWEEN. AP visibility belongs to collapse, not to the tier
  // filter, so `showAps` is deliberately not passed here.
  const tierLinks = useMemo(
    () => filterLinks(allLinks, devicesById, minTier, showRejected),
    [allLinks, devicesById, minTier, showRejected],
  );

  const graph = useMemo(
    () =>
      buildCanvasGraph(devices, tierLinks, {
        collapsed,
        includeAps: showAps,
        groupVenues,
      }),
    [devices, tierLinks, collapsed, showAps, groupVenues],
  );

  /**
   * The running overlay's answer, as a map from canvas edge to how to paint it.
   *
   * Computed here, once, because an overlay is a property of the WHOLE graph —
   * a ring is only a ring when you can see all of it — and because the
   * alternative is every edge component walking the graph for itself.
   */
  const overlay = useTopology((s) => s.overlay);
  const setOverlayResult = useTopology((s) => s.setOverlayResult);
  const overlayResult = useMemo(() => {
    if (overlay !== "loops") {
      return { edges: {}, summary: null } as {
        edges: Record<string, { color: string; note: string }>;
        summary: { total: number; attention: number } | null;
      };
    }
    const cycles = findCycles(allLinks, devicesById);
    const edges: Record<string, { color: string; note: string }> = {};
    cycles.forEach((cycle, index) => {
      const parallel = cycle.verdict === "parallel";
      const color = parallel
        ? OVERLAY_COLOR.attention
        : OVERLAY_COLOR.ring[index % OVERLAY_COLOR.ring.length];
      const note = parallel
        ? "These two switches are joined by more than one link that is not a " +
          "LAG. Usually that is meant to be a bundle."
        : `A ring of ${new Set(cycle.nodes).size} switches. Ordinary redundancy — ` +
          "RUCKUS ONE does not report usable spanning-tree state, so this " +
          "shows the shape, not whether anything is blocking it.";
      const inCycle = new Set(cycle.links);
      graph.edges.forEach((edge) => {
        if (edges[edge.id]) return;
        if (edge.links.some((l) => inCycle.has(l.id))) edges[edge.id] = { color, note };
      });
    });
    return {
      edges,
      summary: {
        total: cycles.length,
        attention: cycles.filter((c) => c.verdict === "parallel").length,
      },
    };
  }, [overlay, allLinks, devicesById, graph.edges]);

  useEffect(() => {
    setOverlayResult(overlayResult.edges, overlayResult.summary);
  }, [overlayResult, setOverlayResult]);

  /**
   * Which of each device's ports carry a link that is actually drawn.
   *
   * The strip highlights those, and only they get handles — a 48-port switch
   * would otherwise put 96 measured handles into React Flow's store to serve a
   * handful of edges.
   */
  const linkedIdents = useMemo(() => {
    const out: Record<string, string[]> = {};
    graph.edges.forEach((edge) => {
      edge.links.forEach((link) => {
        [link.a, link.b].forEach((end) => {
          if (!end.ident) return;
          (out[end.deviceId] ??= []).push(end.ident);
        });
      });
    });
    return out;
  }, [graph.edges]);

  // Read at layout time WITHOUT making the effect depend on it: positions are
  // an OUTPUT of layout, so depending on them would re-run the layout forever.
  const positionsRef = useRef(positions);
  positionsRef.current = positions;

  // What the engine was last run WITH. Changing a setting should visibly
  // re-settle the graph, not nudge an already-settled one — a slider you drag
  // and cannot see the effect of reads as broken.
  const lastRunRef = useRef<string>("");

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (laidOut || !graph.nodes.length) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const layoutNodes: LayoutNode[] = graph.nodes.map((node) => {
      const size = sizeOf(node, Boolean(portsOpen[node.id]));
      return {
        id: node.id,
        width: size.w,
        height: size.h,
        kind: (node.device?.kind ?? "group") as LayoutNode["kind"],
        pinned: pinned[node.id],
        label: node.label,
        degree: node.device?.counts?.links ?? 0,
        geo: node.device?.floorplan
          ? {
              floorplanId: node.device.floorplan.floorplanId,
              xPercent: node.device.floorplan.xPercent ?? 0,
              yPercent: node.device.floorplan.yPercent ?? 0,
            }
          : undefined,
      };
    });
    const layoutEdges: LayoutEdge[] = graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: edge.links[0]?.kind ?? "ethernet",
      weight: edge.links.length,
    }));

    const chosen = engineOr(engine);
    setLayingOut(true);
    // Hand the engine wherever things currently sit.
    //
    // "Manual" means FREEZE THIS, not "scatter everything and start again" —
    // without a seed it parked all 123 nodes in a single 23,594px row. Force
    // wants it too: re-running a simulation should refine the picture rather
    // than throw it away and re-settle from noise.
    const resolvedOptions = {
      ...defaultOptions(chosen),
      ...(engineOptions[chosen.id] ?? {}),
    };
    const signature = `${chosen.id}|${direction}|${JSON.stringify(resolvedOptions)}`;
    const settingsChanged = lastRunRef.current !== signature;
    lastRunRef.current = signature;

    const current = positionsRef.current;
    // Warm-start only when the SETTINGS are unchanged — a graph change should
    // refine what is there, a settings change should show what the setting does.
    const seed: LayoutResult | undefined =
      chosen.incremental &&
      !(settingsChanged && chosen.restartOnSettingsChange) &&
      Object.keys(current).length
        ? { positions: current, meta: { engine: "previous", elapsedMs: 0 } }
        : undefined;
    chosen
      .run({
        nodes: layoutNodes,
        edges: layoutEdges,
        // A confirmed WAN uplink is the top of the network, so the hierarchy
        // should hang off it rather than off whatever dagre picks.
        roots: graph.nodes
          .filter((n) => n.device?.kind === "wan")
          .map((n) => n.id),
        direction,
        spacing: { node: 18, rank: 104, group: 40 },
        options: resolvedOptions,
        signal: controller.signal,
      }, seed)
      .then((result) => {
        if (!controller.signal.aborted)
          setPositions(result.positions, result.edgeRoutes);
      })
      .catch(() => setLayingOut(false));

    return () => controller.abort();
  }, [
    laidOut,
    engine,
    direction,
    graph,
    pinned,
    portsOpen,
    engineOptions,
    setPositions,
    setLayingOut,
  ]);

  /**
   * Where nodes are actually DRAWN: the layout's answer, plus room made for any
   * open port strip.
   *
   * Kept separate from `positions` on purpose. `positions` is what the layout
   * produced and what gets pinned and persisted; the offsets are a presentation
   * detail recomputed from scratch every render, so closing a strip restores
   * the arrangement exactly rather than un-doing an accumulated nudge.
   *
   * Pinned nodes are excluded: a node placed by hand stays exactly there.
   */
  const displayPositions = useMemo(() => {
    const open = graph.nodes.filter((n) => portsOpen[n.id]);
    if (!open.length) return positions;
    const boxes: Record<string, Box> = {};
    graph.nodes.forEach((node) => {
      const at = positions[node.id];
      if (!at) return;
      const size = sizeOf(node, false);
      boxes[node.id] = { x: at.x, y: at.y, w: size.w, h: size.h };
    });
    const growth: Record<string, number> = {};
    open.forEach((node) => {
      growth[node.id] = sizeOf(node, true).h - sizeOf(node, false).h;
    });
    return withStripOffsets(positions, stripOffsets(boxes, growth, pinned));
  }, [graph.nodes, positions, portsOpen, pinned]);

  const computedNodes: Node[] = useMemo(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: node.kind === "device" ? "device" : node.kind,
        position: displayPositions[node.id] ?? { x: 0, y: 0 },
        data: {
          node,
          portsOpen: Boolean(portsOpen[node.id]),
          ports: ports[node.id],
          portsLoading: Boolean(portsLoading[node.id]),
          linkedIdents: linkedIdents[node.id],
        },
      })),
    [graph.nodes, displayPositions, portsOpen, ports, portsLoading, linkedIdents],
  );


  /** Canvas edges ending on each node, so a drag drops only their routes. */
  const edgesByNode = useMemo(() => {
    const out: Record<string, string[]> = {};
    graph.edges.forEach((edge) => {
      (out[edge.source] ??= []).push(edge.id);
      (out[edge.target] ??= []).push(edge.id);
    });
    return out;
  }, [graph.edges]);

  // Attachment slots, recomputed whenever the geometry changes. Cheap — one
  // pass over the edges plus a sort per node side — and DETERMINISTIC, which
  // is why there is no "redraw connections" button: it would reproduce exactly
  // the same answer, and dragging a node already re-runs this anyway.
  const anchors = useMemo(
    () =>
      assignAnchors(graph.nodes, graph.edges, displayPositions, (node) =>
        sizeOf(node, Boolean(portsOpen[node.id])),
      ),
    [graph, displayPositions, portsOpen],
  );

  // Reported so the layout picker can be judged rather than guessed at.
  const setCrossings = useTopology((s) => s.setCrossings);
  const exporting = useTopology((s) => s.exporting);
  useEffect(() => {
    if (!laidOut) return;
    setCrossings(
      countCrossings(
        graph.nodes,
        graph.edges,
        positions,
        (node) => sizeOf(node, Boolean(portsOpen[node.id])),
        anchors,
      ),
    );
  }, [laidOut, graph, positions, portsOpen, anchors, setCrossings]);

  const computedEdges: Edge[] = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    return graph.edges.map((edge) => {
      const sourceNode = byId.get(edge.source);
      const targetNode = byId.get(edge.target);
      const sourceOpen = Boolean(portsOpen[edge.source]);
      const targetOpen = Boolean(portsOpen[edge.target]);
      const route = edgeRoutes[edge.id];
      // With a routed path, the anchors must match the RANK DIRECTION rather
      // than raw geometry: dagre leaves the bottom of a node (top-down) and
      // does the sideways travel itself. Picking a side anchor here would make
      // the line exit sideways and then jump across to the routed corridor.
      // The assignment pass has already picked a side AND a slot on it. The
      // geometric fallback only covers an edge it could not place.
      let { sourceHandle, targetHandle } = anchors[edge.id] ?? {
        sourceHandle: "b1",
        targetHandle: "t1",
      };

      // With a port strip open, land the edge on the PORT rather than the
      // middle of the box — the point of modelling ports at all. Only for a
      // single link: a bundle has no one port to land on, and the handle must
      // exist or React Flow drops the edge, so this also requires the strip's
      // ports to have loaded.
      if (edge.links.length === 1) {
        const link = edge.links[0];
        const endFor = (deviceId: string) =>
          link.a.deviceId === deviceId ? link.a : link.b;
        const loaded = (id: string) =>
          (ports[id]?.length ?? 0) > 0 &&
          (linkedIdents[id]?.length ?? 0) > 0;
        const sEnd = endFor(edge.source);
        const tEnd = endFor(edge.target);
        if (sourceOpen && sEnd.ident && loaded(edge.source)) {
          sourceHandle = `p:${sEnd.ident}`;
        }
        if (targetOpen && tEnd.ident && loaded(edge.target)) {
          targetHandle = `p:${tEnd.ident}`;
        }
      }

      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle,
        targetHandle,
        type: "confidence",
        data: { edge, route },
      };
    });
  }, [graph, positions, portsOpen, ports, linkedIdents, edgeRoutes, anchors]);

  // React Flow owns the live node list so dragging is smooth; we push a new one
  // in whenever the derived graph actually changes.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Carry each node's measured size across the rebuild.
  //
  // `computedNodes` is a fresh array of fresh objects whenever anything it
  // derives from changes -- including `positions`, which a DRAG writes to. A
  // node handed to React Flow without its `measured` box is a node React Flow
  // has to measure again, and while it is doing that the graph counts as
  // uninitialised. That is both wasted work on every drop and the thing that
  // used to yank the viewport (see the fit effect below).
  useEffect(() => {
    setNodes((current) => {
      if (!current.length) return computedNodes;
      const kept = new Map(current.map((n) => [n.id, { box: n.measured, sig: sizeSig(n) }]));
      return computedNodes.map((node) => {
        const previous = kept.get(node.id);
        // Only when the node still renders at the same size. Nodes declare no
        // width or height -- React Flow measures them from the DOM -- so
        // carrying a stale box across an OPENED PORT STRIP would leave every
        // edge on that node attached to where its bottom used to be.
        return previous?.box && previous.sig === sizeSig(node)
          ? { ...node, measured: previous.box }
          : node;
      });
    });
  }, [computedNodes, setNodes]);
  useEffect(() => setEdges(computedEdges), [computedEdges, setEdges]);

  // Fit the viewport AFTER a layout, not on mount.
  //
  // The `fitView` prop only fires on first render, when `nodes` is still empty
  // because layout is async — it fit nothing, zoomed to maxZoom at the origin,
  // and then the real nodes arrived far outside the viewport. With
  // onlyRenderVisibleElements culling them, the canvas rendered completely
  // blank while the toolbar cheerfully reported 123 devices.
  //
  // Fired once per LAYOUT, tracked by sequence number rather than by effect
  // deps. `nodesInitialized` is in the dependency list because a fit before the
  // nodes are measured fits nothing -- but it is not a REASON to fit, and it
  // goes false and true again whenever React Flow re-measures. Dragging one
  // node did exactly that, so every drop re-zoomed and re-centred the map,
  // which is intolerable when arranging by hand. The ref is what makes the
  // distinction: a fit needs a new layout, not merely a new measurement.
  const { fitView } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  const fittedSeq = useRef(-1);
  useEffect(() => {
    if (!nodesInitialized || !nodes.length) return;
    if (fittedSeq.current === layoutSeq) return;
    fittedSeq.current = layoutSeq;
    // A switch hierarchy is FLAT AND WIDE — measured 15,400 x 890 px for one
    // 112-switch venue, because ~53 access switches share the leaf rank.
    // Fitting that whole width puts every node at 11px across: technically
    // "the whole network", practically unreadable. Floor the zoom and let the
    // minimap orient; collapsing is the real answer to density.
    fitView({ padding: 0.15, duration: 200, minZoom: 0.32, maxZoom: 1.2 });
  }, [nodesInitialized, layoutSeq, fitView, nodes.length]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={(_, node) => selectDevice(node.id)}
      onEdgeClick={(_, edge) => {
        const data = edge.data as { edge?: { links?: { id: string }[] } };
        selectLink(
          edge.id,
          (data?.edge?.links ?? []).map((l) => l.id),
        );
      }}
      onNodeMouseEnter={(_, node) => hover(node.id)}
      onNodeMouseLeave={() => hover(null)}
      onEdgeMouseEnter={(_, edge) => hover(edge.id)}
      onEdgeMouseLeave={() => hover(null)}
      onNodeDragStop={(_, node) =>
        pin(node.id, node.position, edgesByNode[node.id] ?? [])
      }
      onPaneClick={() => {
        selectDevice(null);
        selectLink(null);
      }}
      // Only draw what is on screen — but only once the graph is big enough to
      // need it. On a small graph it buys nothing and turns any viewport bug
      // into a blank canvas rather than a visibly wrong one.
      //
      // Suspended while exporting: the exporter serialises the DOM, so culling
      // would silently produce a picture of whatever happened to be on screen.
      onlyRenderVisibleElements={nodes.length > 300 && !exporting}
      elevateEdgesOnSelect={false}
      nodesConnectable={false}
      minZoom={0.05}
      maxZoom={2.5}
    >
      <Background gap={24} size={1} color="#e5e7eb" />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        nodeColor={(node) => {
          const data = node.data as {
            node?: { kind?: string; device?: { kind?: string } };
          };
          if (data?.node?.kind === "venue") return "#4f46e5";
          if (data?.node?.kind === "apFan") return "#0891b2";
          return (
            DEVICE_ACCENT[data?.node?.device?.kind ?? "unknown"] ?? "#9ca3af"
          );
        }}
        style={{ width: 150, height: 100 }}
      />
    </ReactFlow>
  );
}

/**
 * The provider now lives in TopologyShell, wrapping the toolbar too — the
 * export menu needs `useReactFlow()` to measure the graph, and it sits in the
 * toolbar.
 */
export default Canvas;
