/**
 * Making room for an opened port strip, without re-laying out the world.
 *
 * Opening a strip makes one node taller. The obvious response — mark the layout
 * stale and run it again — is what the code used to do, and it was wrong twice
 * over: it re-arranged four thousand unrelated nodes and refit the viewport,
 * both while the user was in the middle of arranging something by hand. The
 * plan said so from the start: *"Expanding one device does NOT trigger a global
 * relayout. The box grows; everything else stays put."*
 *
 * So instead: everything stays where the layout put it, and whatever the grown
 * box would now sit on top of is pushed straight down by exactly the amount it
 * grew.
 *
 * The result is a pure function of (layout positions, which strips are open),
 * which matters more than it sounds. It means the offsets are recomputed from
 * scratch on every render rather than accumulated, so closing a strip restores
 * the previous arrangement exactly, opening and closing the same strip fifty
 * times drifts nothing, and nothing has to be remembered or undone.
 */

export type Box = { x: number; y: number; w: number; h: number };

/** Horizontal slack, so a node sitting a hair to the side still counts. */
const X_MARGIN = 8;
/** Vertical slack, so a node exactly level with the bottom edge is left alone. */
const Y_EPSILON = 1;
/**
 * How many times the affected column is allowed to widen.
 *
 * A pushed node can overlap a neighbour that the GROWN node did not, so the
 * band grows to take it in. Bounded because each pass is a full scan and the
 * third one has never changed the answer on a real graph.
 */
const MAX_PASSES = 3;

function overlapsX(a: { x: number; w: number }, b: { x: number; w: number }): boolean {
  return a.x < b.x + b.w + X_MARGIN && b.x < a.x + a.w + X_MARGIN;
}

/**
 * Vertical offsets to apply to each node, keyed by id. Absent means zero.
 *
 * @param boxes   every node at its LAYOUT position, at its CLOSED size
 * @param growth  id -> extra height that node gains when its strip is open
 * @param frozen  ids that must not move: a node placed by hand stays placed
 */
export function stripOffsets(
  boxes: Record<string, Box>,
  growth: Record<string, number>,
  frozen: Record<string, unknown> = {},
): Record<string, number> {
  const grown = Object.keys(growth).filter((id) => growth[id] > 0 && boxes[id]);
  if (!grown.length) return {};

  const offsets: Record<string, number> = {};
  const at = (id: string) => boxes[id].y + (offsets[id] ?? 0);

  // Top-down, so a strip above has already displaced things before a strip
  // below is considered. Sorted by id on ties, so the answer never depends on
  // object key order.
  grown.sort((a, b) => at(a) - at(b) || a.localeCompare(b));

  for (const id of grown) {
    const source = boxes[id];
    const bottom = at(id) + source.h;
    // The column this strip pushes into. Starts as the grown node's own span
    // and widens to cover whatever it displaces.
    let band: { x: number; w: number }[] = [source];
    const pushed = new Set<string>();

    for (let pass = 0; pass < MAX_PASSES; pass += 1) {
      let widened = false;
      for (const other of Object.keys(boxes)) {
        if (other === id || pushed.has(other) || other in frozen) continue;
        const box = boxes[other];
        if (at(other) < bottom - Y_EPSILON) continue;      // above it; unaffected
        if (!band.some((span) => overlapsX(span, box))) continue;
        pushed.add(other);
        band = [...band, box];
        widened = true;
      }
      if (!widened) break;
    }

    pushed.forEach((other) => {
      offsets[other] = (offsets[other] ?? 0) + growth[id];
    });
  }
  return offsets;
}

/** The offsets applied, as a positions map the canvas can render from. */
export function withStripOffsets(
  positions: Record<string, { x: number; y: number }>,
  offsets: Record<string, number>,
): Record<string, { x: number; y: number }> {
  const ids = Object.keys(offsets);
  if (!ids.length) return positions;
  const out = { ...positions };
  ids.forEach((id) => {
    const at = positions[id];
    if (at) out[id] = { x: at.x, y: at.y + offsets[id] };
  });
  return out;
}
