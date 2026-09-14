import { useMemo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { Port } from "../state/types";
import { CHIP, panelLayout } from "./portLayout";

/**
 * A switch's ports, as a faceplate of chips — and as real connection points.
 *
 * Each chip carries a React Flow handle whose id is the port identifier, so a
 * link between two open switches lands on the actual ports it runs between
 * rather than on the middle of a box. That is the whole reason ports are
 * first-class in the model.
 *
 * Arranged the way the switch itself is — odd ports on top, even below, one
 * block per module, one band per stack unit — so the strip can be read against
 * the hardware. See portLayout.ts.
 *
 * Colour says what the port is DOING, which is what you look at a port strip
 * for: forwarding, down, blocked by spanning tree, or an uplink to more
 * network.
 */

/** px-2 on each side. */
const PAD_X = 16;
/** pt-1 above, pb-2 below, and a little slack so a rounding error never clips. */
const PAD_Y = 16;

/**
 * Size of the strip alone, so the layout can reserve room for it.
 *
 * Takes the port IDENTS, not a count: the faceplate's shape depends on how the
 * ports split into modules and units, and a 48+4 switch and a 24+24+4 stack
 * with the same total are very different sizes.
 */
export function stripSize(idents: string[]) {
  const layout = panelLayout(idents);
  return { width: layout.width + PAD_X, height: layout.height + PAD_Y };
}

function portColor(port: Port, linked: boolean): string {
  if (port.adminUp === false) return "#9ca3af"; // administratively off
  const stp = (port.stpState || "").toLowerCase();
  if (stp && /block|discard|listen|learn|broken/.test(stp)) return "#d97706";
  if (port.operUp === false) return "#e5e7eb"; // down, but enabled
  if (linked) return "#2563eb"; // carries a link we drew
  if (port.macCount > 0) return "#16a34a"; // forwarding for something
  return "#a7f3d0"; // up, nothing learned
}

function title(port: Port, linked: boolean): string {
  const bits = [`Port ${port.ident}`];
  bits.push(
    port.adminUp === false
      ? "administratively down"
      : port.operUp
        ? "up"
        : "down",
  );
  if (port.speedMbps) bits.push(`${port.speedMbps} Mb/s`);
  if (port.untaggedVlan != null) bits.push(`untagged ${port.untaggedVlan}`);
  if (port.taggedVlans?.length)
    bits.push(`tagged ${port.taggedVlans.join(", ")}`);
  if (port.stpState) bits.push(`STP ${port.stpState}`);
  if (port.macCount)
    bits.push(`${port.macCount} MAC${port.macCount === 1 ? "" : "es"}`);
  if (port.neighborName) bits.push(`sees ${port.neighborName}`);
  if (linked) bits.push("carries a link on this map");
  return bits.join(" · ");
}

const HANDLE_STYLE = {
  opacity: 0,
  width: 1,
  height: 1,
  border: "none",
  minWidth: 1,
  minHeight: 1,
  left: "50%",
  top: "50%",
} as const;

type Props = {
  ports: Port[];
  /** Port idents that carry a link currently drawn on the canvas. */
  linkedIdents: Set<string>;
  loading?: boolean;
};

export default function PortStrip({ ports, linkedIdents, loading }: Props) {
  const layout = useMemo(() => panelLayout(ports.map((p) => p.ident)), [ports]);

  if (loading) {
    return (
      <div className="px-2 py-1.5 text-[10px] text-gray-400">loading ports…</div>
    );
  }
  if (!ports.length) {
    return (
      <div className="px-2 py-1.5 text-[10px] text-gray-400">
        no ports reported
      </div>
    );
  }

  return (
    <div className="px-2 pb-2 pt-1">
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        {layout.units.map(({ unit, y }) => (
          <span
            key={unit}
            className="absolute left-0 select-none text-[8px] font-semibold leading-none text-gray-400"
            // Centred on the unit's two-row band.
            style={{ top: y + CHIP - 4 }}
            title={`Stack unit ${unit}`}
          >
            {unit}
          </span>
        ))}
        {ports.map((port) => {
          const cell = layout.cells[port.ident];
          if (!cell) return null;
          const linked = linkedIdents.has(port.ident);
          return (
            <div
              key={port.id}
              title={title(port, linked)}
              className="absolute rounded-[2px]"
              style={{
                left: cell.x,
                top: cell.y,
                width: CHIP,
                height: CHIP,
                backgroundColor: portColor(port, linked),
                outline: linked ? "1px solid #1d4ed8" : "none",
              }}
            >
              {/*
                Handles are only rendered for ports that actually carry a link.
                A 48-port switch would otherwise add 96 handles to the React
                Flow store for no benefit, and every one of them is measured.
              */}
              {linked && (
                <>
                  <Handle
                    type="target"
                    id={`p:${port.ident}`}
                    position={Position.Top}
                    style={HANDLE_STYLE}
                    isConnectable={false}
                  />
                  <Handle
                    type="source"
                    id={`p:${port.ident}`}
                    position={Position.Bottom}
                    style={HANDLE_STYLE}
                    isConnectable={false}
                  />
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
