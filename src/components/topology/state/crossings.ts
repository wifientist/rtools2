import type { CanvasEdge, CanvasNode } from "./collapse";
import { ANCHORS_PER_SIDE } from "./anchors";
import type { AnchorAssignment } from "./anchors";

/**
 * How many pairs of links cross, for the arrangement currently on screen.
 *
 * Measured between the ACTUAL ATTACHMENT POINTS, not node centres. A
 * centre-to-centre count is blind to anchor assignment by construction, which
 * would make "Redraw connections" report no effect however much it helped.
 *
 * Two things move this number, and they are not close in magnitude. Measured on
 * a real 130-edge venue: the LAYOUT moved it from 480 (Hierarchy) to 58
 * (Organic), while anchor assignment moved it from 491 to 480. Both are worth
 * having; only one is worth choosing a layout for.
 *
 * O(E^2), so it is bounded and simply declines to answer on a big graph rather
 * than freezing the page to produce a number nobody asked for.
 */

export const MAX_EDGES_TO_COUNT = 500;

type Pt = { x: number; y: number };

function ccw(a: Pt, b: Pt, c: Pt) {
  return (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x);
}

function intersects(a: Pt, b: Pt, c: Pt, d: Pt) {
  return ccw(a, c, d) !== ccw(b, c, d) && ccw(a, b, c) !== ccw(a, b, d);
}

type Box = { x: number; y: number; w: number; h: number };

/** Where a slot id such as "r1" sits on a node's border. */
function anchorPoint(box: Box, handle: string | undefined): Pt {
  const centre = { x: box.x + box.w / 2, y: box.y + box.h / 2 };
  if (!handle) return centre;
  const side = handle[0];
  const index = Number(handle.slice(1));
  if (!Number.isFinite(index)) return centre;
  const fraction = (index + 1) / (ANCHORS_PER_SIDE + 1);
  switch (side) {
    case "t":
      return { x: box.x + box.w * fraction, y: box.y };
    case "b":
      return { x: box.x + box.w * fraction, y: box.y + box.h };
    case "l":
      return { x: box.x, y: box.y + box.h * fraction };
    case "r":
      return { x: box.x + box.w, y: box.y + box.h * fraction };
    default:
      return centre;
  }
}

export function countCrossings(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  positions: Record<string, { x: number; y: number }>,
  sizeOf: (node: CanvasNode) => { w: number; h: number },
  anchors: AnchorAssignment = {},
): number | null {
  if (edges.length > MAX_EDGES_TO_COUNT) return null;

  const boxes = new Map<string, Box>();
  nodes.forEach((node) => {
    const at = positions[node.id];
    if (!at) return;
    const size = sizeOf(node);
    boxes.set(node.id, { x: at.x, y: at.y, w: size.w, h: size.h });
  });

  const segments: { a: Pt; b: Pt; s: string; t: string }[] = [];
  edges.forEach((edge) => {
    const boxA = boxes.get(edge.source);
    const boxB = boxes.get(edge.target);
    if (!boxA || !boxB) return;
    const assigned = anchors[edge.id];
    segments.push({
      a: anchorPoint(boxA, assigned?.sourceHandle),
      b: anchorPoint(boxB, assigned?.targetHandle),
      s: edge.source,
      t: edge.target,
    });
  });

  let crossings = 0;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      const p = segments[i];
      const q = segments[j];
      // Edges meeting at a shared device touch, they do not cross.
      if (p.s === q.s || p.s === q.t || p.t === q.s || p.t === q.t) continue;
      if (intersects(p.a, p.b, q.a, q.b)) crossings += 1;
    }
  }
  return crossings;
}
