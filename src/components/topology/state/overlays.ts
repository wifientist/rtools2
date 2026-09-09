import type { Device, Link } from "./types";

/**
 * Overlays: what colour is allowed to mean once the map stops using it for
 * link kind.
 *
 * The first one is ring highlighting, and it is deliberately NOT "loop / no
 * loop". A physical cycle is not a fault — it is redundancy, and a well-built
 * site has many. R1 does not expose usable spanning-tree state (plans/
 * topology.md §1.9, §3b), so the overlay cannot say whether a ring is blocked,
 * and pretending otherwise would train people to ignore it.
 *
 * What it CAN separate is two genuinely different questions:
 *
 *   "parallel" — two switches joined by more than one link that is not a LAG.
 *                This is the §3b case: it is usually meant to be a bundle, and
 *                it is worth looking at.
 *   "ring"     — three or more switches in a loop. Ordinary redundant design.
 *                Shown, counted, and NOT flagged, because flagging every
 *                triangle on a properly built estate is just noise.
 *
 * Note what cannot appear here: a redundant pair correctly bonded into a LAG
 * is one logical link, so it forms no ring at all and never shows up.
 */

export type Cycle = {
  id: string;
  /** Device ids around the ring, in order. */
  nodes: string[];
  /** Link ids forming the ring. */
  links: string[];
  /** See the module note: one is a question, the other is just the shape. */
  verdict: "parallel" | "ring";
};

const INFRASTRUCTURE = new Set(["switch", "stack"]);

/**
 * One ring per independent cycle — a fundamental cycle basis.
 *
 * A spanning forest is grown first; every edge left over closes exactly one
 * ring, found by walking both endpoints up to their common ancestor. That
 * yields |E| - |V| + components rings, which is the number of independent
 * loops that actually exist, rather than the exponential number of paths that
 * can be traced around them.
 */
export function findCycles(links: Link[], devicesById: Record<string, Device>): Cycle[] {
  // LAG members are not separate paths: the bundle is the link. Counting them
  // individually would report a loop for every correctly built redundant pair
  // on the estate.
  const usable = links.filter((l) => {
    if (l.logicalOf) return false;
    if (l.tier === "rejected") return false;
    if (l.kind === "stack") return false; // inside one chassis, not a path
    const a = devicesById[l.a.deviceId];
    const b = devicesById[l.b.deviceId];
    return (
      !!a && !!b &&
      INFRASTRUCTURE.has(a.kind) && INFRASTRUCTURE.has(b.kind) &&
      a.id !== b.id
    );
  });

  const adjacency = new Map<string, { to: string; link: Link }[]>();
  const add = (from: string, to: string, link: Link) => {
    const list = adjacency.get(from);
    if (list) list.push({ to, link });
    else adjacency.set(from, [{ to, link }]);
  };
  usable.forEach((l) => {
    add(l.a.deviceId, l.b.deviceId, l);
    add(l.b.deviceId, l.a.deviceId, l);
  });

  // Deterministic traversal: the same graph must produce the same rings in the
  // same order every render, or the overlay flickers between colours.
  const order = [...adjacency.keys()].sort();
  adjacency.forEach((list) => list.sort((x, y) => x.to.localeCompare(y.to)));

  const parent = new Map<string, string | null>();
  const parentLink = new Map<string, Link>();
  const depth = new Map<string, number>();
  const seenEdge = new Set<string>();
  const cycles: Cycle[] = [];

  for (const root of order) {
    if (parent.has(root)) continue;
    parent.set(root, null);
    depth.set(root, 0);
    const queue = [root];
    while (queue.length) {
      const node = queue.shift() as string;
      for (const { to, link } of adjacency.get(node) ?? []) {
        if (seenEdge.has(link.id)) continue;
        if (!parent.has(to)) {
          seenEdge.add(link.id);
          parent.set(to, node);
          parentLink.set(to, link);
          depth.set(to, (depth.get(node) ?? 0) + 1);
          queue.push(to);
          continue;
        }
        // A back edge: it closes exactly one ring.
        seenEdge.add(link.id);
        const ring = climb(node, to, parent, parentLink, depth);
        if (!ring) continue;
        ring.links.push(link.id);
        cycles.push({
          id: `cyc:${[...ring.links].sort().join("|")}`,
          nodes: ring.nodes,
          links: ring.links,
          verdict: new Set(ring.nodes).size <= 2 ? "parallel" : "ring",
        });
      }
    }
  }
  return cycles;
}

function climb(
  a: string,
  b: string,
  parent: Map<string, string | null>,
  parentLink: Map<string, Link>,
  depth: Map<string, number>,
): { nodes: string[]; links: string[] } | null {
  const upA: string[] = [a];
  const upB: string[] = [b];
  let x = a;
  let y = b;
  let guard = 0;
  while ((depth.get(x) ?? 0) > (depth.get(y) ?? 0) && guard++ < 10000) {
    x = parent.get(x) as string;
    if (!x) return null;
    upA.push(x);
  }
  while ((depth.get(y) ?? 0) > (depth.get(x) ?? 0) && guard++ < 10000) {
    y = parent.get(y) as string;
    if (!y) return null;
    upB.push(y);
  }
  while (x !== y && guard++ < 10000) {
    x = parent.get(x) as string;
    y = parent.get(y) as string;
    if (!x || !y) return null;
    upA.push(x);
    upB.push(y);
  }

  const linkIds: string[] = [];
  for (let i = 0; i < upA.length - 1; i += 1) {
    const l = parentLink.get(upA[i]);
    if (l) linkIds.push(l.id);
  }
  for (let i = 0; i < upB.length - 1; i += 1) {
    const l = parentLink.get(upB[i]);
    if (l) linkIds.push(l.id);
  }
  upB.pop(); // the meeting point is already the last entry of upA
  return { nodes: [...upA, ...upB.reverse()], links: linkIds };
}

/** linkId -> the index of the ring it belongs to, for colouring. */
export function cycleIndex(cycles: Cycle[]): Map<string, number> {
  const out = new Map<string, number>();
  cycles.forEach((cycle, index) => {
    cycle.links.forEach((id) => {
      if (!out.has(id)) out.set(id, index);
    });
  });
  return out;
}
