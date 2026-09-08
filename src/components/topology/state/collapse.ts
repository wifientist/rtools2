import type { Device, Link, Tier } from "./types";
import { TIER_RANK } from "./types";

/**
 * Collapse and expand, in ONE place.
 *
 * The whole mechanism is a single idea: every device resolves to the node that
 * currently REPRESENTS it on canvas. A device in an expanded group represents
 * itself; a device in a collapsed group is represented by the group. Then every
 * link is redrawn between representatives — links whose ends resolve to the
 * same representative are internal and vanish, and several links between the
 * same pair of representatives become one aggregate edge.
 *
 * Keeping that resolution in one function is what stops collapse logic
 * metastasising into the node components, the edge components and the layout
 * adapter, which is how this kind of feature usually rots.
 *
 * DELIBERATE SIMPLIFICATION: a collapsed group is a SEPARATE SYNTHETIC NODE,
 * not a React Flow parent/subflow with `extent: "parent"`. Subflows with hidden
 * children and parent auto-resize are historically fiddly, and this achieves
 * the same result — one box you can open — with none of that machinery. It also
 * means the layout engine needs to know nothing about grouping at all.
 */

export type GroupKind = "venue" | "apFan";

export type CanvasNode = {
  id: string;
  /** "device" renders the real thing; the others render a group box. */
  kind: "device" | GroupKind;
  device?: Device;
  label: string;
  sublabel: string;
  /** Device ids this node stands for (itself, for a plain device). */
  members: string[];
  collapsible: boolean;
  collapsed: boolean;
  /** Status roll-up across members, for the group badge. */
  status: { online: number; offline: number; other: number };
  counts: Record<string, number>;
};

export type CanvasEdge = {
  id: string;
  source: string;
  target: string;
  /** The real links this edge stands for. One, unless it is an aggregate. */
  links: Link[];
  tier: Tier;
  tierCounts: Record<string, number>;
  aggregate: boolean;
};

export type CollapseOptions = {
  collapsed: Record<string, boolean>;
  includeAps: boolean;
  /** Group by venue. Pointless for a single-venue scope. */
  groupVenues: boolean;
};

export const VENUE_PREFIX = "venue:";
export const FAN_PREFIX = "fan:";

const INFRA = new Set(["switch", "stack", "external"]);

/**
 * Which switch feeds each AP, from the links themselves.
 *
 * Derived rather than read off the AP, because the uplink the MAP believes is
 * the one the correlator settled on — including the case where it rejected the
 * AP's own self-reported switch in favour of better evidence.
 */
function apParents(
  devicesById: Record<string, Device>,
  links: Link[],
): Record<string, string> {
  const best: Record<string, { parent: string; rank: number; score: number }> =
    {};
  links.forEach((link) => {
    if (link.tier === "rejected") return;
    [
      [link.a.deviceId, link.b.deviceId],
      [link.b.deviceId, link.a.deviceId],
    ].forEach(([near, far]) => {
      if (devicesById[near]?.kind !== "ap") return;
      if (!INFRA.has(devicesById[far]?.kind ?? "")) return;
      const rank = TIER_RANK[link.tier];
      const current = best[near];
      if (
        !current ||
        rank > current.rank ||
        (rank === current.rank && link.score > current.score)
      ) {
        best[near] = { parent: far, rank, score: link.score };
      }
    });
  });
  return Object.fromEntries(
    Object.entries(best).map(([ap, v]) => [ap, v.parent]),
  );
}

function rollUp(members: Device[]) {
  const status = { online: 0, offline: 0, other: 0 };
  members.forEach((d) => {
    if (d.status === "online") status.online += 1;
    else if (d.status === "offline") status.offline += 1;
    else status.other += 1;
  });
  return status;
}

function modalTier(links: Link[]): {
  tier: Tier;
  counts: Record<string, number>;
} {
  const counts: Record<string, number> = {};
  links.forEach((l) => {
    counts[l.tier] = (counts[l.tier] ?? 0) + 1;
  });
  // MODAL, not minimum. A twelve-link trunk where nine are confirmed and three
  // probable is a well-understood trunk; styling it by its worst member would
  // say the opposite.
  let tier: Tier = "weak";
  let most = -1;
  (Object.entries(counts) as [Tier, number][]).forEach(([t, n]) => {
    if (n > most || (n === most && TIER_RANK[t] > TIER_RANK[tier])) {
      tier = t;
      most = n;
    }
  });
  return { tier, counts };
}

export function buildCanvasGraph(
  devices: Device[],
  links: Link[],
  opts: CollapseOptions,
): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const devicesById = Object.fromEntries(devices.map((d) => [d.id, d]));
  const parents = apParents(devicesById, links);

  // ── group membership ────────────────────────────────────────────────────
  const fanMembers: Record<string, string[]> = {};
  const venueMembers: Record<string, string[]> = {};

  devices.forEach((device) => {
    if (device.kind === "ap") {
      if (!opts.includeAps) return;
      const parent = parents[device.id];
      // An AP with no resolved uplink has no fan to join — it stands alone, and
      // it should: "online but nothing reports what feeds it" is exactly the
      // thing a map ought to make visible rather than tidy away.
      if (parent) {
        (fanMembers[`${FAN_PREFIX}${parent}`] ??= []).push(device.id);
      }
    }
    if (opts.groupVenues && device.venueId) {
      (venueMembers[`${VENUE_PREFIX}${device.venueId}`] ??= []).push(device.id);
    }
  });

  // ── representative resolution ───────────────────────────────────────────
  // A venue wraps everything inside it, so a collapsed venue wins over a
  // collapsed fan within it.
  const fanOf: Record<string, string> = {};
  Object.entries(fanMembers).forEach(([fanId, members]) =>
    members.forEach((id) => {
      fanOf[id] = fanId;
    }),
  );
  const venueOf: Record<string, string> = {};
  Object.entries(venueMembers).forEach(([venueId, members]) =>
    members.forEach((id) => {
      venueOf[id] = venueId;
    }),
  );

  /**
   * Groups have DIFFERENT DEFAULTS, and both defaults are the useful one.
   *
   * An AP fan starts CLOSED: opening every fan at once on this estate would put
   * 2788 access points on the canvas, which is not a map of anything. A venue
   * starts OPEN, because collapsing the thing you just asked to look at would
   * be perverse — it is offered as a toggle instead.
   *
   * An explicit entry always wins, so Collapse all / Expand all work on both.
   */
  const isCollapsed = (id: string) => {
    const explicit = opts.collapsed[id];
    if (explicit !== undefined) return explicit;
    return id.startsWith(FAN_PREFIX);
  };

  function represent(deviceId: string): string | null {
    const device = devicesById[deviceId];
    if (!device) return null;
    if (device.kind === "ap" && !opts.includeAps) return null;

    const venue = venueOf[deviceId];
    if (venue && isCollapsed(venue)) return venue;

    const fan = fanOf[deviceId];
    if (fan && isCollapsed(fan)) return fan;

    return deviceId;
  }

  // ── nodes ───────────────────────────────────────────────────────────────
  const nodes: CanvasNode[] = [];
  const emitted = new Set<string>();

  const addGroup = (
    id: string,
    kind: GroupKind,
    memberIds: string[],
    label: string,
    sublabel: string,
  ) => {
    if (emitted.has(id)) return;
    const members = memberIds.map((m) => devicesById[m]).filter(Boolean);
    const counts: Record<string, number> = {};
    members.forEach((m) => {
      counts[m.kind] = (counts[m.kind] ?? 0) + 1;
    });
    nodes.push({
      id,
      kind,
      label,
      sublabel,
      members: memberIds,
      collapsible: true,
      collapsed: true,
      status: rollUp(members),
      counts,
    });
    emitted.add(id);
  };

  devices.forEach((device) => {
    const rep = represent(device.id);
    if (!rep) return;

    if (rep.startsWith(VENUE_PREFIX)) {
      const members = venueMembers[rep] ?? [];
      addGroup(
        rep,
        "venue",
        members,
        device.venueName || "Venue",
        `${members.length} devices`,
      );
      return;
    }
    if (rep.startsWith(FAN_PREFIX)) {
      const members = fanMembers[rep] ?? [];
      const parent = devicesById[rep.slice(FAN_PREFIX.length)];
      addGroup(
        rep,
        "apFan",
        members,
        `${members.length} access point${members.length === 1 ? "" : "s"}`,
        parent ? `on ${parent.displayName}` : "",
      );
      return;
    }
    if (emitted.has(device.id)) return;

    // A device offers a "collapse" affordance only when it OWNS a group that
    // is currently OPEN. When the fan is closed the fan node carries its own
    // expand affordance, so offering both would be two controls for one thing.
    const ownFan = `${FAN_PREFIX}${device.id}`;
    const hasFan =
      Boolean(fanMembers[ownFan]?.length) && !isCollapsed(ownFan);
    nodes.push({
      id: device.id,
      kind: "device",
      device,
      label: device.displayName,
      sublabel: device.model || device.kind,
      members: [device.id],
      collapsible: hasFan,
      collapsed: false,
      status: rollUp([device]),
      counts: device.counts ?? {},
    });
    emitted.add(device.id);
  });

  // An expanded venue still needs a way back: mark its devices so the toolbar
  // can offer "collapse venues" globally rather than per node.

  // ── edges ───────────────────────────────────────────────────────────────
  const buckets: Record<string, { source: string; target: string; links: Link[] }> =
    {};
  const present = new Set(nodes.map((n) => n.id));

  links.forEach((link) => {
    // LAG members are represented by their bundle; drawing both would show one
    // trunk as N+1 parallel edges, which reads as a loop.
    if (link.logicalOf) return;
    const a = represent(link.a.deviceId);
    const b = represent(link.b.deviceId);
    if (!a || !b || a === b) return; // internal to a collapsed group
    if (!present.has(a) || !present.has(b)) return;
    const key = a < b ? `${a}|${b}` : `${b}|${a}`;
    (buckets[key] ??= { source: a, target: b, links: [] }).links.push(link);
  });

  const edges: CanvasEdge[] = Object.entries(buckets).map(([key, bucket]) => {
    const { tier, counts } = modalTier(bucket.links);
    return {
      id: bucket.links.length === 1 ? bucket.links[0].id : `agg:${key}`,
      source: bucket.source,
      target: bucket.target,
      links: bucket.links,
      tier,
      tierCounts: counts,
      aggregate: bucket.links.length > 1,
    };
  });

  return { nodes, edges };
}

/** Every group id the current graph could collapse, for expand/collapse all. */
export function collapsibleGroups(
  devices: Device[],
  links: Link[],
  opts: Omit<CollapseOptions, "collapsed">,
): { venues: string[]; fans: string[] } {
  const devicesById = Object.fromEntries(devices.map((d) => [d.id, d]));
  const parents = apParents(devicesById, links);
  const venues = new Set<string>();
  const fans = new Set<string>();
  devices.forEach((device) => {
    if (opts.groupVenues && device.venueId) {
      venues.add(`${VENUE_PREFIX}${device.venueId}`);
    }
    if (device.kind === "ap" && opts.includeAps && parents[device.id]) {
      fans.add(`${FAN_PREFIX}${parents[device.id]}`);
    }
  });
  return { venues: [...venues], fans: [...fans] };
}
