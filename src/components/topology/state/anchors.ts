import type { CanvasEdge, CanvasNode } from "./collapse";

/**
 * Where each edge attaches to each node.
 *
 * A SEPARATE PASS from layout, deliberately. Node placement and edge attachment
 * are different problems: placement needs the whole graph and is expensive,
 * attachment needs only the final positions and is cheap. Splitting them means
 * the attachment pass can be re-run on its own — including over an arrangement
 * the user positioned by hand, where moving nodes is not an option.
 *
 * The idea is the standard one for this subproblem. Every edge leaving a node
 * wants the side that faces its far end; within a side, the edges are sorted by
 * where their far end actually sits and assigned slots IN THAT ORDER. Two edges
 * whose targets do not cross therefore do not cross near the node either — with
 * a single anchor per side they would all converge on one point and fan out
 * through each other.
 */

/** Slots per side. Three gives corner / middle / corner. */
export const ANCHORS_PER_SIDE = 3;

export type Side = "t" | "r" | "b" | "l";
export type AnchorAssignment = Record<
  string,
  { sourceHandle: string; targetHandle: string }
>;

type Box = { x: number; y: number; w: number; h: number };

function centre(box: Box) {
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
}

/** The side of `from` that faces `to`. */
function facingSide(from: Box, to: Box): Side {
  const a = centre(from);
  const b = centre(to);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (Math.abs(dx) > Math.abs(dy)) return dx >= 0 ? "r" : "l";
  return dy >= 0 ? "b" : "t";
}

/**
 * Slot ids for one side, ordered the way the side runs.
 *
 * Top and bottom run left-to-right; left and right run top-to-bottom. Getting
 * this order right is the whole trick — it is what makes "sort by where the far
 * end is, then assign in order" actually avoid crossings.
 */
function slotIds(side: Side): string[] {
  return Array.from({ length: ANCHORS_PER_SIDE }, (_, i) => `${side}${i}`);
}

export function assignAnchors(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  positions: Record<string, { x: number; y: number }>,
  sizeOf: (node: CanvasNode) => { w: number; h: number },
): AnchorAssignment {
  const boxes = new Map<string, Box>();
  nodes.forEach((node) => {
    const at = positions[node.id];
    if (!at) return;
    const size = sizeOf(node);
    boxes.set(node.id, { x: at.x, y: at.y, w: size.w, h: size.h });
  });

  // Per node, per side: the edges wanting that side, with the far centre they
  // are aiming at.
  type Want = { edgeId: string; end: "source" | "target"; far: { x: number; y: number } };
  const wants = new Map<string, Record<Side, Want[]>>();
  const sideOf = new Map<string, Side>(); // `${edgeId}:${end}` -> side

  const ensure = (id: string) => {
    let entry = wants.get(id);
    if (!entry) {
      entry = { t: [], r: [], b: [], l: [] };
      wants.set(id, entry);
    }
    return entry;
  };

  edges.forEach((edge) => {
    const a = boxes.get(edge.source);
    const b = boxes.get(edge.target);
    if (!a || !b) return;
    const sideA = facingSide(a, b);
    const sideB = facingSide(b, a);
    sideOf.set(`${edge.id}:source`, sideA);
    sideOf.set(`${edge.id}:target`, sideB);
    ensure(edge.source)[sideA].push({
      edgeId: edge.id,
      end: "source",
      far: centre(b),
    });
    ensure(edge.target)[sideB].push({
      edgeId: edge.id,
      end: "target",
      far: centre(a),
    });
  });

  const handles: Record<string, string> = {};
  wants.forEach((bySide, nodeId) => {
    (Object.keys(bySide) as Side[]).forEach((side) => {
      const list = bySide[side];
      if (!list.length) return;
      // Sort along the axis the side runs, so slot order matches target order.
      const horizontal = side === "t" || side === "b";
      list.sort((p, q) =>
        horizontal ? p.far.x - q.far.x : p.far.y - q.far.y,
      );
      const slots = slotIds(side);
      list.forEach((want, index) => {
        // Spread evenly across the available slots; with more edges than slots
        // several share one, which is still far better than all sharing one.
        const slot =
          list.length === 1
            ? slots[Math.floor(slots.length / 2)]
            : slots[
                Math.min(
                  slots.length - 1,
                  Math.round((index / (list.length - 1)) * (slots.length - 1)),
                )
              ];
        handles[`${want.edgeId}:${want.end}`] = slot;
      });
    });
  });

  const out: AnchorAssignment = {};
  edges.forEach((edge) => {
    const source = handles[`${edge.id}:source`];
    const target = handles[`${edge.id}:target`];
    if (source && target) out[edge.id] = { sourceHandle: source, targetHandle: target };
  });
  return out;
}
