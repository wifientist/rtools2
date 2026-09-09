import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { ChevronDown, ChevronRight, Layers, Network, Wifi } from "lucide-react";
import { DEVICE_ACCENT, STATUS_COLOR } from "../colors";
import { useTopology } from "../state/store";
import type { CanvasNode } from "../state/collapse";
import PortStrip, { stripSize } from "./PortStrip";
import { ANCHORS_PER_SIDE } from "../state/anchors";
import type { Port } from "../state/types";

/**
 * Custom node types.
 *
 * `nodeTypes` MUST be a module-level constant. Recreating it inline is the
 * single most common React Flow performance bug: it remounts every node on
 * every render, which at 200 switches is the difference between instant and
 * unusable.
 */

export const NODE_WIDTH = 168;
export const NODE_HEIGHT = 54;
export const GROUP_WIDTH = 176;
export const GROUP_HEIGHT = 60;

/**
 * How much room an open port strip needs.
 *
 * The layout has to know this BEFORE the ports arrive, or an expanded switch
 * lays out at collapsed size and overlaps its neighbours. `device.counts.ports`
 * is already in the snapshot, so the size is known without the fetch.
 */
export function expandedSize(portCount: number) {
  const strip = stripSize(portCount);
  return {
    w: Math.max(NODE_WIDTH, strip.width),
    h: NODE_HEIGHT + strip.height,
  };
}

/**
 * Connection points: THREE per side, on all four sides.
 *
 * One anchor per side makes every edge leaving a node converge on a single
 * point and fan out through each other — most of the visible mess near a busy
 * switch is that, not the layout. With several slots per side the edges can be
 * ordered along the side to match where their far ends sit, which is what
 * state/anchors.ts does.
 *
 * Three is corner / middle / corner. Raising ANCHORS_PER_SIDE is the only
 * change needed to give the assignment pass more room.
 */
const SIDES = [
  { id: "t", position: Position.Top, horizontal: true },
  { id: "r", position: Position.Right, horizontal: false },
  { id: "b", position: Position.Bottom, horizontal: true },
  { id: "l", position: Position.Left, horizontal: false },
] as const;

const HANDLE_STYLE = {
  opacity: 0,
  width: 1,
  height: 1,
  border: "none",
  minWidth: 1,
  minHeight: 1,
};

/** Even fractions across a side: 3 slots -> 25% / 50% / 75%. */
const OFFSETS = Array.from(
  { length: ANCHORS_PER_SIDE },
  (_, i) => `${((i + 1) / (ANCHORS_PER_SIDE + 1)) * 100}%`,
);

function Anchors() {
  return (
    <>
      {SIDES.flatMap((side) =>
        OFFSETS.flatMap((offset, index) =>
          (["target", "source"] as const).map((type) => (
            <Handle
              key={`${type}-${side.id}${index}`}
              type={type}
              id={`${side.id}${index}`}
              position={side.position}
              style={{
                ...HANDLE_STYLE,
                ...(side.horizontal ? { left: offset } : { top: offset }),
              }}
              isConnectable={false}
            />
          )),
        ),
      )}
    </>
  );
}

type Data = {
  node: CanvasNode;
  ports?: Port[];
  portsOpen?: boolean;
  portsLoading?: boolean;
  linkedIdents?: string[];
};

function DeviceNodeInner({ id, data }: NodeProps) {
  const { node, ports, portsOpen, portsLoading, linkedIdents } =
    data as unknown as Data;
  const device = node.device!;
  const togglePorts = useTopology((s) => s.togglePorts);
  const hasPorts = (device.counts?.ports ?? 0) > 0;
  // Each node subscribes to ITS OWN selection state, not the selection set, so
  // selecting one node re-renders one node.
  const selected = useTopology((s) => s.selectedDeviceId === id);
  const hovered = useTopology((s) => s.hoveredId === id);
  const toggleCollapse = useTopology((s) => s.toggleCollapse);

  const accent = DEVICE_ACCENT[device.kind] ?? DEVICE_ACCENT.unknown;
  const status = STATUS_COLOR[device.status] ?? STATUS_COLOR.unknown;
  const outOfScope = Boolean(device.attrs?.outOfScope);
  const links = device.counts?.links ?? 0;

  return (
    <div
      className={`rounded-md border bg-white shadow-sm ${
        selected
          ? "ring-2 ring-blue-500 border-blue-400"
          : hovered
            ? "border-gray-400 shadow"
            : "border-gray-200"
      }`}
      style={{
        width: portsOpen
          ? expandedSize(device.counts?.ports ?? 0).w
          : NODE_WIDTH,
        minHeight: NODE_HEIGHT,
      }}
    >
      <Anchors />
      <div
        className="h-1 rounded-t-md"
        style={{ backgroundColor: outOfScope ? "#9ca3af" : accent }}
      />
      <div className="px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: status }}
            title={device.rawStatus || device.status}
          />
          <span
            className="truncate text-[11px] font-medium text-gray-900"
            title={device.displayName}
          >
            {device.displayName}
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-gray-500">
          <span className="uppercase tracking-wide">{device.kind}</span>
          <span className="ml-auto shrink-0">
            {links > 0 && `${links} link${links === 1 ? "" : "s"}`}
          </span>
        </div>
        {outOfScope && (
          <div className="mt-0.5 truncate text-[10px] text-amber-700">
            outside selected venues
          </div>
        )}
        <div className="mt-1 flex gap-1">
          {node.collapsible && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                toggleCollapse(`fan:${device.id}`);
              }}
              className="flex flex-1 items-center justify-center gap-1 rounded bg-cyan-50 px-1 py-0.5 text-[10px] text-cyan-800 hover:bg-cyan-100"
              title="Hide this switch's access points"
            >
              <ChevronDown size={10} />
              <Wifi size={10} />
              APs
            </button>
          )}
          {hasPorts && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                togglePorts(device.id);
              }}
              className={`flex flex-1 items-center justify-center gap-1 rounded px-1 py-0.5 text-[10px] ${
                portsOpen
                  ? "bg-blue-100 text-blue-800 hover:bg-blue-200"
                  : "bg-gray-50 text-gray-600 hover:bg-gray-100"
              }`}
              title={
                portsOpen
                  ? "Hide ports"
                  : `Show all ${device.counts?.ports} ports`
              }
            >
              <Network size={10} />
              {device.counts?.ports}p
            </button>
          )}
        </div>
      </div>
      {portsOpen && (
        <div className="border-t border-gray-100">
          <PortStrip
            ports={ports ?? []}
            linkedIdents={new Set(linkedIdents ?? [])}
            loading={portsLoading}
          />
        </div>
      )}
    </div>
  );
}

function GroupNodeInner({ id, data }: NodeProps) {
  const { node } = data as unknown as Data;
  const hovered = useTopology((s) => s.hoveredId === id);
  const toggleCollapse = useTopology((s) => s.toggleCollapse);

  const isFan = node.kind === "apFan";
  const accent = isFan ? "#0891b2" : "#4f46e5";
  const { online, offline, other } = node.status;

  return (
    <div
      className={`rounded-md border-2 border-dashed bg-white shadow-sm ${
        hovered ? "border-gray-500" : "border-gray-300"
      }`}
      style={{ width: GROUP_WIDTH, minHeight: GROUP_HEIGHT }}
    >
      <Anchors />
      <button
        onClick={(e) => {
          e.stopPropagation();
          toggleCollapse(id);
        }}
        className="w-full px-2 py-1.5 text-left hover:bg-gray-50"
        title="Expand"
      >
        <div className="flex items-center gap-1.5">
          <ChevronRight size={11} className="shrink-0 text-gray-500" />
          {isFan ? (
            <Wifi size={11} style={{ color: accent }} className="shrink-0" />
          ) : (
            <Layers size={11} style={{ color: accent }} className="shrink-0" />
          )}
          <span className="truncate text-[11px] font-semibold text-gray-900">
            {node.label}
          </span>
        </div>
        {node.sublabel && (
          <div className="truncate text-[10px] text-gray-500">
            {node.sublabel}
          </div>
        )}
        <div className="mt-0.5 flex items-center gap-1.5 text-[10px]">
          {online > 0 && (
            <span className="text-green-700">{online} up</span>
          )}
          {offline > 0 && <span className="text-red-600">{offline} down</span>}
          {other > 0 && <span className="text-gray-400">{other} other</span>}
        </div>
      </button>
    </div>
  );
}

export const DeviceNode = memo(DeviceNodeInner);
export const GroupNode = memo(GroupNodeInner);

export const nodeTypes = {
  device: DeviceNode,
  venue: GroupNode,
  apFan: GroupNode,
};
