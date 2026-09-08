import { useMemo } from "react";
import { ChevronsDownUp, ChevronsUpDown } from "lucide-react";
import { useTopology } from "../state/store";
import { collapsibleGroups } from "../state/collapse";
import { ENGINES } from "../layout/registry";
import LayoutOptions from "./LayoutOptions";
import SourceCatalogue from "./SourceCatalogue";
import UplinkPanel from "./UplinkPanel";
import ExportMenu from "./ExportMenu";
import FindingsPanel from "./FindingsPanel";
import type { SourceInfo, TopologyFindings, UplinkCandidate } from "../state/types";
import { TIER_STYLE } from "../colors";
import { TIERS } from "../state/types";
import type { OverlayId, Tier } from "../state/types";

export default function Toolbar({
  fetchSources,
  fetchFindings,
  onConfirmUplink,
  exportUrl,
}: {
  fetchSources?: () => Promise<SourceInfo[] | null>;
  fetchFindings?: () => Promise<TopologyFindings | null>;
  onConfirmUplink?: (
    venueId: string,
    candidate: UplinkCandidate | null,
    action: "confirm" | "reject" | "clear",
  ) => Promise<void>;
  exportUrl?: (path: string) => string;
}) {
  const engine = useTopology((s) => s.engine);
  const direction = useTopology((s) => s.direction);
  const minTier = useTopology((s) => s.minTier);
  const showRejected = useTopology((s) => s.showRejected);
  const showAps = useTopology((s) => s.showAps);
  const groupVenues = useTopology((s) => s.groupVenues);
  const layingOut = useTopology((s) => s.layingOut);
  const layoutSaving = useTopology((s) => s.layoutSaving);
  const layoutDirty = useTopology((s) => s.layoutDirty);
  const layoutVersion = useTopology((s) => s.layoutVersion);
  const crossings = useTopology((s) => s.crossings);
  const setDirection = useTopology((s) => s.setDirection);
  const setMinTier = useTopology((s) => s.setMinTier);
  const setShowRejected = useTopology((s) => s.setShowRejected);
  const setShowAps = useTopology((s) => s.setShowAps);
  const setGroupVenues = useTopology((s) => s.setGroupVenues);
  const setCollapsed = useTopology((s) => s.setCollapsed);
  const setEngine = useTopology((s) => s.setEngine);
  const paint = useTopology((s) => s.paint);
  const overlay = useTopology((s) => s.overlay);
  const overlaySummary = useTopology((s) => s.overlaySummary);
  const setPaint = useTopology((s) => s.setPaint);
  const setOverlay = useTopology((s) => s.setOverlay);
  const devicesById = useTopology((s) => s.devicesById);

  const devices = useTopology((s) => s.devices);
  const links = useTopology((s) => s.links);
  const venueCount = useTopology(
    (s) => new Set(s.devices.map((d) => d.venueId).filter(Boolean)).size,
  );

  const groups = useMemo(
    () => collapsibleGroups(devices, links, { includeAps: showAps, groupVenues }),
    [devices, links, showAps, groupVenues],
  );
  const allGroups = [...groups.venues, ...groups.fans];

  // Opening every group on a big estate means laying out thousands of nodes —
  // measured 4.8s for 2788 access points. That is a fine thing to ask for and a
  // bad thing to trigger by accident, so it asks once past a threshold.
  const EXPAND_WARN_AT = 800;
  const expandAll = () => {
    const total = devices.length;
    if (
      total > EXPAND_WARN_AT &&
      !window.confirm(
        `Opening every group puts about ${total.toLocaleString()} devices on the ` +
          `canvas. Laying that out takes a few seconds and it will be dense. ` +
          `Open them all anyway?`,
      )
    ) {
      return;
    }
    setCollapsed(allGroups, false);
  };

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-gray-200 bg-white px-3 py-2 text-xs">
      <span className="font-semibold uppercase tracking-wide text-gray-600">
        Layout
      </span>
      <select
        value={engine}
        onChange={(e) =>
          setEngine(e.target.value as Parameters<typeof setEngine>[0])
        }
        title={ENGINES.find((x) => x.id === engine)?.description}
        className="rounded border px-2 py-1"
      >
        {ENGINES.map((candidate) => {
          // An engine that has nothing to work with says so in the picker,
          // rather than being selectable and then quietly producing nonsense.
          const verdict = candidate.available?.(
            Object.values(devicesById).map((d) => ({
              id: d.id,
              width: 0,
              height: 0,
              kind: "unknown" as const,
              geo: d.floorplan
                ? {
                    floorplanId: d.floorplan.floorplanId,
                    xPercent: d.floorplan.xPercent ?? 0,
                    yPercent: d.floorplan.yPercent ?? 0,
                  }
                : undefined,
            })),
          );
          const blocked = verdict && !verdict.ok;
          return (
            <option
              key={candidate.id}
              value={candidate.id}
              disabled={blocked}
              title={blocked ? verdict?.reason : candidate.description}
            >
              {candidate.label}
              {blocked ? " — no data" : ""}
            </option>
          );
        })}
      </select>
      <LayoutOptions />
      <SourceCatalogue fetchSources={fetchSources} />
      <UplinkPanel onConfirm={onConfirmUplink} />
      <FindingsPanel fetchFindings={fetchFindings} />
      <ExportMenu exportUrl={exportUrl} />

      <div className="flex overflow-hidden rounded border">
        {(["TB", "LR"] as const).map((value) => (
          <button
            key={value}
            onClick={() => setDirection(value)}
            className={`px-2 py-1 ${
              direction === value
                ? "bg-blue-600 text-white"
                : "bg-white text-gray-700 hover:bg-gray-50"
            }`}
          >
            {value === "TB" ? "Top-down" : "Left-right"}
          </button>
        ))}
      </div>

      <span className="ml-1 font-semibold uppercase tracking-wide text-gray-600">
        Group
      </span>
      <button
        onClick={() => setCollapsed(allGroups, true)}
        disabled={!allGroups.length}
        title="Collapse every access-point fan and venue"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
      >
        <ChevronsDownUp size={12} /> Collapse all
      </button>
      <button
        onClick={expandAll}
        disabled={!allGroups.length}
        title="Open every group"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
      >
        <ChevronsUpDown size={12} /> Expand all
      </button>
      {venueCount > 1 && (
        <label
          className="flex items-center gap-1.5"
          title="Draw each venue as one box you can open"
        >
          <input
            type="checkbox"
            checked={groupVenues}
            onChange={(e) => setGroupVenues(e.target.checked)}
          />
          <span>By venue ({venueCount})</span>
        </label>
      )}

      <span className="ml-1 font-semibold uppercase tracking-wide text-gray-600">
        Colour
      </span>
      <select
        value={overlay}
        onChange={(e) => setOverlay(e.target.value as OverlayId)}
        title={
          "What hue means. Confidence is always carried by line style and " +
          "never by colour, so an overlay can take the colour channel without " +
          "making the map harder to trust."
        }
        className="rounded border px-2 py-1"
      >
        <option value="none">by link kind</option>
        <option value="loops">highlight rings</option>
      </select>
      {overlay === "none" && (
        <label
          className="flex items-center gap-1.5"
          title="Draw every link grey. Line style still carries confidence; stack and mesh keep a midpoint glyph."
        >
          <input
            type="checkbox"
            checked={paint === "neutral"}
            onChange={(e) => setPaint(e.target.checked ? "neutral" : "kind")}
          />
          <span>Grey</span>
        </label>
      )}
      {overlay === "loops" && overlaySummary && (
        <span
          className={`rounded px-1.5 py-0.5 text-[11px] ${
            overlaySummary.attention
              ? "bg-amber-50 text-amber-800"
              : "bg-green-50 text-green-700"
          }`}
          title={
            "A ring is redundancy, not automatically a fault, and RUCKUS ONE " +
            "does not report usable spanning-tree state — so rings are shown, " +
            "not judged. Counted separately: two switches joined by more than " +
            "one link that is not a LAG, which usually should be a bundle."
          }
        >
          {overlaySummary.total} ring{overlaySummary.total === 1 ? "" : "s"}
          {overlaySummary.attention
            ? ` · ${overlaySummary.attention} unbonded pair${
                overlaySummary.attention === 1 ? "" : "s"
              }`
            : ""}
        </span>
      )}

      <span className="ml-1 font-semibold uppercase tracking-wide text-gray-600">
        Show
      </span>
      <label className="flex items-center gap-1.5">
        <span className="text-gray-600">at least</span>
        <select
          value={minTier}
          onChange={(e) => setMinTier(e.target.value as Tier)}
          className="rounded border px-2 py-1"
        >
          {TIERS.filter((t) => t !== "rejected").map((tier) => (
            <option key={tier} value={tier}>
              {TIER_STYLE[tier].label}
            </option>
          ))}
        </select>
      </label>
      <label
        className="flex items-center gap-1.5"
        title="Access points appear grouped per switch; open a group to see them individually."
      >
        <input
          type="checkbox"
          checked={showAps}
          onChange={(e) => setShowAps(e.target.checked)}
        />
        <span>Access points</span>
      </label>
      <label
        className="flex items-center gap-1.5"
        title="Links something better outranked, or a human ruled out. Kept so 'why is there no edge here?' stays answerable."
      >
        <input
          type="checkbox"
          checked={showRejected}
          onChange={(e) => setShowRejected(e.target.checked)}
        />
        <span>Rejected</span>
      </label>

      <span className="ml-auto flex items-center gap-2 text-gray-500">
        {crossings !== null && (
          <span
            className={`rounded px-1.5 py-0.5 text-[11px] ${
              crossings === 0
                ? "bg-green-50 text-green-700"
                : crossings < 100
                  ? "bg-gray-100 text-gray-600"
                  : "bg-amber-50 text-amber-800"
            }`}
            title={
              "Pairs of links that cross. Lower is easier to read — and the " +
              "LAYOUT moves this far more than anything else: on a real venue " +
              "Hierarchy scored 480 against Organic's 58 for the same graph."
            }
          >
            {crossings} crossing{crossings === 1 ? "" : "s"}
          </span>
        )}
        <span>
          {layingOut
            ? "laying out…"
            : `${allGroups.length} group${allGroups.length === 1 ? "" : "s"}`}
        </span>
        {/*
          Arrangement state, stated plainly. A map you rearranged and cannot
          tell whether it saved is worse than one that never saved at all.
        */}
        <span
          className="text-[11px] text-gray-400"
          title="Your arrangement — pinned positions, open groups and port strips — is stored per venue set and survives re-discovery."
        >
          {layoutSaving
            ? "saving…"
            : layoutDirty
              ? "unsaved"
              : layoutVersion > 0
                ? `arrangement v${layoutVersion}`
                : ""}
        </span>
      </span>
    </div>
  );
}
