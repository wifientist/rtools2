import type { LayoutEngine, LayoutInput, LayoutNode, LayoutResult } from "./types";
import { optionValue } from "./types";

/**
 * Real placement: where the devices actually are on a floorplan.
 *
 * R1 carries `floorplanId` plus `xPercent`/`yPercent` per device, so a site that
 * has drawn its floorplans can see its topology laid over its geography — which
 * beats any algorithm for "why is that AP on the far switch?".
 *
 * SELF-DISABLING, and that matters. On the tenant this was built against every
 * device reports `xPercent: 0, yPercent: 0, floorplanId: ''` — defaults, not
 * placements — and 0 floorplans are defined. Laying that out would stack the
 * whole estate on one point while looking like it had worked, which is worse
 * than refusing. `available()` therefore demands real, non-identical
 * coordinates before this engine will run at all.
 *
 * NOT VERIFIED against a tenant that genuinely uses floorplans. The maths is
 * exercised with synthetic coordinates; the shape of R1's real data is an
 * assumption until a site with floorplans is available.
 */

function placeable(node: LayoutNode): boolean {
  const geo = node.geo;
  if (!geo || !geo.floorplanId) return false;
  return (
    Number.isFinite(geo.xPercent) &&
    Number.isFinite(geo.yPercent) &&
    !(geo.xPercent === 0 && geo.yPercent === 0)
  );
}

export const geoEngine: LayoutEngine = {
  id: "geo",
  label: "Floorplan",
  description:
    "Positions devices where they physically sit, using the floorplan " +
    "coordinates from RUCKUS ONE. Devices with no placement are parked below.",
  supportsGroups: false,
  supportsPinning: true,
  supportsEdgeRoutes: false,
  incremental: false,
  restartOnSettingsChange: true,
  options: [
    {
      key: "scale",
      label: "Floor size",
      kind: "number",
      min: 400,
      max: 6000,
      step: 100,
      def: 1600,
      help: "How many pixels wide one floorplan is drawn.",
    },
    {
      key: "floorGap",
      label: "Gap between floors",
      kind: "number",
      min: 0,
      max: 1200,
      step: 50,
      def: 300,
      help: "Floors are laid out side by side; this is the space between them.",
    },
  ],

  available(nodes) {
    const good = nodes.filter(placeable);
    if (good.length < 2) {
      return {
        ok: false,
        reason:
          "No floorplan positions in this venue — RUCKUS ONE reports no " +
          "floorplans and every device sits at the default 0,0.",
      };
    }
    return { ok: true };
  },

  async run(input: LayoutInput): Promise<LayoutResult> {
    const started = performance.now();
    const scale = Number(optionValue(input, "scale", 1600));
    const floorGap = Number(optionValue(input, "floorGap", 300));

    const placedNodes = input.nodes.filter(placeable);
    const floors = [...new Set(placedNodes.map((n) => n.geo!.floorplanId))].sort();
    const originOf = new Map(
      floors.map((id, index) => [id, index * (scale + floorGap)]),
    );

    const positions: Record<string, { x: number; y: number }> = {};
    placedNodes.forEach((node) => {
      const geo = node.geo!;
      positions[node.id] = node.pinned ?? {
        x: (originOf.get(geo.floorplanId) ?? 0) + (geo.xPercent / 100) * scale
          - node.width / 2,
        y: (geo.yPercent / 100) * scale - node.height / 2,
      };
    });

    // Everything with no placement goes in a tray below the floors, rather
    // than silently onto the origin where it would look deliberately placed.
    const trayY = scale + floorGap;
    let x = 0;
    input.nodes
      .filter((n) => !placeable(n))
      .forEach((node) => {
        positions[node.id] = node.pinned ?? { x, y: trayY };
        x += node.width + 16;
      });

    if (input.signal?.aborted) {
      throw new DOMException("layout aborted", "AbortError");
    }
    const unplaced = input.nodes.length - placedNodes.length;
    return {
      positions,
      meta: {
        engine: "geo",
        elapsedMs: Math.round(performance.now() - started),
        note: unplaced
          ? `${unplaced} device${unplaced === 1 ? "" : "s"} have no floorplan position — parked below.`
          : undefined,
      },
    };
  },
};
