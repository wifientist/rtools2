import { memo } from "react";
import { BaseEdge, getBezierPath } from "@xyflow/react";
import type { EdgeProps } from "@xyflow/react";
import { KIND_GLYPH, TIER_STYLE, basePaint } from "../colors";
import { useTopology } from "../state/store";
import type { CanvasEdge } from "../state/collapse";

/**
 * Confidence-encoded edge, which also stands in for a whole bundle of links
 * when either end is collapsed.
 *
 * Dash pattern, width and opacity carry CONFIDENCE — always, on channels that
 * survive greyscale and colour blindness. Hue carries link kind by default, or
 * an overlay's answer when one is running, at which point kind falls back to a
 * midpoint glyph. See colors.ts.
 *
 * `edgeTypes` MUST be a module-level constant, for the same reason as
 * `nodeTypes`.
 */

type Data = {
  edge: CanvasEdge;
  route?: { points: { x: number; y: number }[] };
};

/**
 * A smooth path through the points the layout engine routed.
 *
 * Catmull-Rom converted to cubic beziers: it passes THROUGH every point rather
 * than near them, so the curve actually follows the corridor the engine cleared
 * between nodes instead of cutting the corner back across whatever it was
 * routed around.
 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (points.length < 2) return "";
  if (points.length === 2) {
    return `M ${points[0].x},${points[0].y} L ${points[1].x},${points[1].y}`;
  }
  const parts = [`M ${points[0].x},${points[0].y}`];
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1 = { x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6 };
    const c2 = { x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6 };
    parts.push(`C ${c1.x},${c1.y} ${c2.x},${c2.y} ${p2.x},${p2.y}`);
  }
  return parts.join(" ");
}

function ConfidenceEdgeInner({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps) {
  const { edge, route } = data as unknown as Data;
  const selected = useTopology((s) => s.selectedLinkId === id);
  const hovered = useTopology((s) => s.hoveredId === id);

  const [bezier, bezierLabelX, bezierLabelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  // Prefer the engine's routed path. Dagre's ordering phase already minimises
  // crossings and its routing phase bends each edge around whatever lies
  // between the endpoints — drawing a straight bezier between the two boxes
  // discards both and puts the crossings back on screen.
  //
  // The engine routes between node CENTRES, so the ends are stitched to the
  // actual handle positions React Flow gives us; otherwise every line would
  // start in the middle of a box.
  const routed = route?.points?.length ? route.points : null;
  const path = routed
    ? smoothPath([
        { x: sourceX, y: sourceY },
        ...routed.slice(1, -1),
        { x: targetX, y: targetY },
      ])
    : bezier;
  const mid = routed ? routed[Math.floor(routed.length / 2)] : null;
  const labelX = mid ? mid.x : bezierLabelX;
  const labelY = mid ? mid.y : bezierLabelY;

  const tier = TIER_STYLE[edge.tier];
  const kind = edge.links[0]?.kind ?? "ethernet";
  const paint = useTopology((s) => s.paint);
  const overlayHit = useTopology((s) => s.overlayEdges[id]);
  const overlayOn = useTopology((s) => s.overlay !== "none");
  const stroke = overlayHit?.color ?? basePaint(kind, paint);
  const emphasised = selected || hovered;
  // An overlay is a spotlight: what it is not talking about recedes rather than
  // disappearing, so the highlighted part is still read in context.
  const dimmed = overlayOn && !overlayHit;
  const glyph = paint === "neutral" ? KIND_GLYPH[kind] : undefined;

  // A bundle is drawn thicker, but only mildly — thickness already carries
  // confidence, and letting count dominate would make an uncertain 40-link
  // bundle look more solid than a confirmed single cable.
  const bundleBoost = edge.aggregate
    ? Math.min(2, Math.log2(edge.links.length))
    : 0;
  const width =
    tier.strokeWidth + bundleBoost + (emphasised ? 1.5 : 0) + (overlayHit ? 1 : 0);

  const distribution = Object.entries(edge.tierCounts)
    .map(([t, n]) => `${n} ${t}`)
    .join(", ");

  return (
    <>
      {/* A wide invisible stroke so thin edges are still easy to click. */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={14}>
        <title>
          {[
            edge.aggregate
              ? `${edge.links.length} links: ${distribution}`
              : `${tier.label}: ${edge.links[0]?.tierReason ?? ""}`,
            overlayHit?.note,
          ]
            .filter(Boolean)
            .join("\n")}
        </title>
      </path>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: emphasised ? "#111827" : stroke,
          strokeWidth: width,
          strokeDasharray: tier.dash,
          opacity: emphasised ? 1 : dimmed ? tier.opacity * 0.3 : tier.opacity,
        }}
      />
      {glyph && !edge.aggregate && (
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          dominantBaseline="middle"
          className="pointer-events-none select-none"
          style={{
            fontSize: 11,
            fill: stroke,
            opacity: dimmed ? 0.3 : 1,
            paintOrder: "stroke",
            stroke: "#fff",
            strokeWidth: 3,
          }}
        >
          {glyph}
        </text>
      )}
      {edge.aggregate && (
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          dominantBaseline="middle"
          className="pointer-events-none select-none"
          style={{
            fontSize: 10,
            fontWeight: 600,
            fill: stroke,
            opacity: dimmed ? 0.3 : 1,
            paintOrder: "stroke",
            stroke: "#fff",
            strokeWidth: 3,
          }}
        >
          {edge.links.length}
        </text>
      )}
    </>
  );
}

export const ConfidenceEdge = memo(ConfidenceEdgeInner);

export const edgeTypes = { confidence: ConfidenceEdge };
