import { Handle, Position } from "@xyflow/react";
import type { Port } from "../state/types";

/**
 * A switch's ports, as a strip of chips — and as real connection points.
 *
 * Each chip carries a React Flow handle whose id is the port identifier, so a
 * link between two open switches lands on the actual ports it runs between
 * rather than on the middle of a box. That is the whole reason ports are
 * first-class in the model.
 *
 * Colour says what the port is DOING, which is what you look at a port strip
 * for: forwarding, down, blocked by spanning tree, or an uplink to more
 * network.
 */

export const PORTS_PER_ROW = 24;
const CHIP = 13;
const GAP = 2;
const STRIP_PAD = 8;

/** Size of the strip alone, so the layout can reserve room for it. */
export function stripSize(portCount: number) {
  const cols = Math.min(portCount, PORTS_PER_ROW);
  const rows = Math.ceil(portCount / PORTS_PER_ROW) || 1;
  return {
    width: cols * (CHIP + GAP) + STRIP_PAD * 2,
    height: rows * (CHIP + GAP) + STRIP_PAD + 12,
  };
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

type Props = {
  ports: Port[];
  /** Port idents that carry a link currently drawn on the canvas. */
  linkedIdents: Set<string>;
  loading?: boolean;
};

export default function PortStrip({ ports, linkedIdents, loading }: Props) {
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
    <div
      className="flex flex-wrap px-2 pb-2 pt-1"
      style={{ gap: GAP, width: stripSize(ports.length).width }}
    >
      {ports.map((port) => {
        const linked = linkedIdents.has(port.ident);
        return (
          <div
            key={port.id}
            title={title(port, linked)}
            className="relative rounded-[2px]"
            style={{
              width: CHIP,
              height: CHIP,
              backgroundColor: portColor(port, linked),
              outline: linked ? "1px solid #1d4ed8" : "none",
            }}
          >
            {/*
              Handles are only rendered for ports that actually carry a link.
              A 48-port switch would otherwise add 96 handles to the React Flow
              store for no benefit, and every one of them is measured.
            */}
            {linked && (
              <>
                <Handle
                  type="target"
                  id={`p:${port.ident}`}
                  position={Position.Top}
                  style={{
                    opacity: 0,
                    width: 1,
                    height: 1,
                    border: "none",
                    minWidth: 1,
                    minHeight: 1,
                    left: "50%",
                    top: "50%",
                  }}
                  isConnectable={false}
                />
                <Handle
                  type="source"
                  id={`p:${port.ident}`}
                  position={Position.Bottom}
                  style={{
                    opacity: 0,
                    width: 1,
                    height: 1,
                    border: "none",
                    minWidth: 1,
                    minHeight: 1,
                    left: "50%",
                    top: "50%",
                  }}
                  isConnectable={false}
                />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
