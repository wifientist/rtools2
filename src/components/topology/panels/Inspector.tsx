import { OVERLAY_COLOR, TIER_STYLE, basePaint, linkStroke } from "../colors";
import { useTopology } from "../state/store";
import LinkVerdict from "./LinkVerdict";
import type { EvidenceResponse, Link } from "../state/types";

/**
 * The right rail: what is selected, and why the tool believes it.
 *
 * The confidence number is never presented on its own. Every row carries its own
 * weight AND the running total after it, so the reader watches the score
 * accumulate and can see exactly which piece of evidence decided it. That is
 * the difference between a number you can audit and a number you have to
 * trust — and this tool exists because no single source here deserves trust.
 */

type Props = {
  evidence: EvidenceResponse | null;
  evidenceLoading: boolean;
  onSetVerdict?: (
    link: Link,
    verdict: "confirm" | "reject",
    note: string,
  ) => Promise<void>;
  onClearVerdict?: (link: Link) => Promise<void>;
};

export default function Inspector({
  evidence,
  evidenceLoading,
  onSetVerdict,
  onClearVerdict,
}: Props) {
  const selectedLinkId = useTopology((s) => s.selectedLinkId);
  const selectedLinkIds = useTopology((s) => s.selectedLinkIds);
  const selectedDeviceId = useTopology((s) => s.selectedDeviceId);
  const links = useTopology((s) => s.links);
  const devicesById = useTopology((s) => s.devicesById);
  const toggleCollapse = useTopology((s) => s.toggleCollapse);
  const collapsed = useTopology((s) => s.collapsed);
  const paint = useTopology((s) => s.paint);
  const overlay = useTopology((s) => s.overlay);

  // A selected edge may stand for a whole bundle when either end is collapsed.
  const chosen = links.filter((l) => selectedLinkIds.includes(l.id));
  const link = chosen.length === 1 ? chosen[0] : null;
  const device = selectedDeviceId ? devicesById[selectedDeviceId] : null;
  const isGroup =
    !!selectedDeviceId &&
    (selectedDeviceId.startsWith("venue:") ||
      selectedDeviceId.startsWith("fan:"));

  // ── a collapsed group ───────────────────────────────────────────────────
  if (isGroup && selectedDeviceId) {
    const isFan = selectedDeviceId.startsWith("fan:");
    const owner = isFan
      ? devicesById[selectedDeviceId.slice("fan:".length)]
      : null;
    const shut = collapsed[selectedDeviceId] ?? isFan;
    return (
      <div className="p-4 text-sm">
        <p className="text-xs uppercase tracking-wide text-gray-500">
          {isFan ? "Access points" : "Venue"}
        </p>
        <p className="font-semibold text-gray-900">
          {isFan
            ? `On ${owner?.displayName ?? "a switch"}`
            : (devicesById[selectedDeviceId] as unknown as { venueName?: string })
                ?.venueName ?? "Venue"}
        </p>
        <p className="mt-2 text-xs text-gray-600">
          {shut
            ? "This group is closed, so everything inside it is drawn as one box and its links are bundled."
            : "This group is open."}
        </p>
        <button
          onClick={() => toggleCollapse(selectedDeviceId)}
          className="mt-3 rounded border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {shut ? "Expand" : "Collapse"}
        </button>
      </div>
    );
  }

  // ── a bundle of links ───────────────────────────────────────────────────
  if (chosen.length > 1) {
    const counts: Record<string, number> = {};
    chosen.forEach((l) => {
      counts[l.tier] = (counts[l.tier] ?? 0) + 1;
    });
    return (
      <div className="p-4 text-sm">
        <p className="text-xs uppercase tracking-wide text-gray-500">
          Bundled links
        </p>
        <p className="font-semibold text-gray-900">
          {chosen.length} links between these two
        </p>
        <p className="mt-1 text-xs text-gray-500">
          One or both ends is a closed group, so its links are drawn together.
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {Object.entries(counts).map(([tier, n]) => (
            <span
              key={tier}
              className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700"
            >
              {n} {tier}
            </span>
          ))}
        </div>
        <ul className="mt-3 max-h-80 space-y-1 overflow-y-auto">
          {chosen.slice(0, 60).map((l) => {
            const a = devicesById[l.a.deviceId];
            const b = devicesById[l.b.deviceId];
            return (
              <li
                key={l.id}
                className="rounded border border-gray-100 px-1.5 py-1 text-[11px]"
              >
                <span className="text-gray-800">
                  {a?.displayName ?? l.a.deviceId}
                  {l.a.ident ? ` ${l.a.ident}` : ""}
                </span>
                <span className="text-gray-400"> ↔ </span>
                <span className="text-gray-800">
                  {b?.displayName ?? l.b.deviceId}
                  {l.b.ident ? ` ${l.b.ident}` : ""}
                </span>
                <span className="ml-1 text-gray-400">{l.tier}</span>
              </li>
            );
          })}
        </ul>
        {chosen.length > 60 && (
          <p className="mt-1 text-[11px] text-gray-400">
            …and {chosen.length - 60} more.
          </p>
        )}
      </div>
    );
  }

  if (!link && !device) {
    return (
      <div className="p-4 text-sm text-gray-500">
        <p className="font-medium text-gray-700">Nothing selected</p>
        <p className="mt-1">
          Click a device or a link. Every link can explain why it is believed.
        </p>

        {/*
          What colour currently means. Only shown when an overlay has taken the
          hue channel, because the rest of the time hue means link kind and the
          node headers already say so.
        */}
        {overlay === "loops" && (
          <div className="mt-4 rounded border border-gray-200 p-2">
            <p className="text-xs font-semibold text-gray-700">Rings</p>
            <p className="mt-0.5 text-[11px] text-gray-600">
              Each closed loop in the switch graph is drawn in its own colour;
              everything else fades back. A ring is <em>redundancy</em>, not a
              fault — RUCKUS ONE does not report usable spanning-tree state, so
              this shows the shape, not whether anything is blocking it.
            </p>
            <div className="mt-2 flex items-start gap-2 text-[11px]">
              <span
                className="mt-1 inline-block h-0.5 w-6 shrink-0"
                style={{ backgroundColor: OVERLAY_COLOR.attention }}
              />
              <span className="text-gray-600">
                Two switches joined by more than one link that is{" "}
                <strong>not</strong> a LAG. Usually meant to be a bundle — the
                one case here worth acting on.
              </span>
            </div>
          </div>
        )}

        {/*
          The swatches follow the paint mode. A blue legend beside a grey map
          would be a legend for something that is not on screen.
        */}
        <div className="mt-4 space-y-1.5">
          {(["confirmed", "strong", "probable", "weak"] as const).map((tier) => (
            <div key={tier} className="flex items-start gap-2 text-xs">
              <svg width="34" height="12" className="mt-0.5 shrink-0">
                <line
                  x1="2"
                  y1="6"
                  x2="32"
                  y2="6"
                  stroke={basePaint("ethernet", paint)}
                  strokeWidth={TIER_STYLE[tier].strokeWidth}
                  strokeDasharray={TIER_STYLE[tier].dash}
                  opacity={TIER_STYLE[tier].opacity}
                />
              </svg>
              <div>
                <span className="font-medium text-gray-700">
                  {TIER_STYLE[tier].label}
                </span>
                <p className="text-gray-500">{TIER_STYLE[tier].blurb}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (device) {
    const outOfScope = Boolean(device.attrs?.outOfScope);
    return (
      <div className="p-4 text-sm">
        <p className="font-semibold text-gray-900">{device.displayName}</p>
        <p className="text-xs uppercase tracking-wide text-gray-500">
          {device.kind}
          {device.model ? ` · ${device.model}` : ""}
        </p>
        {outOfScope && (
          <p className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
            {String(device.attrs?.hint ?? "Outside the selected venues.")}
          </p>
        )}
        <dl className="mt-3 space-y-1 text-xs">
          {[
            ["Status", device.rawStatus || device.status],
            ["Venue", device.venueName],
            ["IP", device.ip],
            ["MAC", device.mac],
            ["Serial", device.serial],
          ]
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label} className="flex gap-2">
                <dt className="w-16 shrink-0 text-gray-500">{label}</dt>
                <dd className="font-mono text-gray-800 break-all">{value}</dd>
              </div>
            ))}
        </dl>
        {Object.keys(device.counts ?? {}).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(device.counts).map(([key, value]) => (
              <span
                key={key}
                className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700"
              >
                {key}: {value}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (!link) return null;
  const style = TIER_STYLE[link.tier];
  const a = devicesById[link.a.deviceId];
  const b = devicesById[link.b.deviceId];

  return (
    <div className="p-4 text-sm">
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-3 w-3 rounded-sm"
          style={{ backgroundColor: basePaint(link.kind, paint) }}
        />
        <span className="text-xs uppercase tracking-wide text-gray-500">
          {link.kind} link
        </span>
      </div>

      <p className="mt-2 font-medium text-gray-900">
        {a?.displayName ?? link.a.deviceId}
        {link.a.ident && (
          <span className="font-mono text-xs text-gray-500"> {link.a.ident}</span>
        )}
      </p>
      <p className="text-xs text-gray-400">↕</p>
      <p className="font-medium text-gray-900">
        {b?.displayName ?? link.b.deviceId}
        {link.b.ident && (
          <span className="font-mono text-xs text-gray-500"> {link.b.ident}</span>
        )}
      </p>

      <div className="mt-3 rounded border border-gray-200 p-2">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold text-gray-900">{style.label}</span>
          <span className="font-mono text-xs text-gray-500">
            {link.score >= 0 ? "+" : ""}
            {link.score.toFixed(2)} · {(link.confidence * 100).toFixed(1)}%
          </span>
          {link.userAsserted && (
            <span className="rounded bg-blue-100 px-1.5 text-[11px] text-blue-800">
              yours
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-gray-600">{link.tierReason}</p>
        <p className="mt-1 text-[11px] text-gray-400">
          {link.directionality === "bidirectional"
            ? "Both ends independently reported this."
            : link.directionality === "derived"
              ? "Reported only through R1, not by either end directly."
              : "Only one end reported this."}
        </p>
      </div>

      <LinkVerdict
        link={link}
        onSet={onSetVerdict}
        onClear={onClearVerdict}
      />

      <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-gray-600">
        Evidence
        <span className="ml-1 font-normal normal-case text-gray-400">
          {link.evidenceSummary.support} for · {link.evidenceSummary.contradict}{" "}
          against · {link.evidenceSummary.context} context
        </span>
      </p>

      {evidenceLoading && (
        <p className="mt-2 text-xs text-gray-400">loading…</p>
      )}

      {!evidenceLoading && evidence && (
        <>
          <ul className="mt-2 space-y-1">
            {evidence.arithmetic.lines.map((row, index) => (
              <li
                key={`${row.source}-${index}`}
                className="rounded border border-gray-100 p-1.5"
              >
                <div className="flex items-baseline gap-1.5 font-mono text-[11px]">
                  <span
                    className={
                      row.kind === "contradict"
                        ? "text-red-600"
                        : row.kind === "context"
                          ? "text-gray-400"
                          : "text-green-700"
                    }
                  >
                    {row.weight >= 0 ? "+" : ""}
                    {row.weight.toFixed(1)}
                  </span>
                  <span className="text-gray-300">→</span>
                  {/* The running total. Watching this climb is the point. */}
                  <span className="font-semibold text-gray-700">
                    {row.running >= 0 ? "+" : ""}
                    {row.running.toFixed(2)}
                  </span>
                  <span className="ml-auto text-[10px] text-gray-400">
                    {row.source}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-gray-700">{row.claim}</p>
              </li>
            ))}
          </ul>

          <div className="mt-2 flex items-baseline gap-2 rounded bg-gray-50 px-2 py-1.5 font-mono text-xs">
            <span className="text-gray-500">total</span>
            <span className="font-semibold text-gray-900">
              {evidence.arithmetic.total >= 0 ? "+" : ""}
              {evidence.arithmetic.total.toFixed(2)}
            </span>
            <span className="text-gray-400">→</span>
            <span className="font-semibold text-gray-900">
              {(evidence.arithmetic.confidence * 100).toFixed(1)}%
            </span>
          </div>
          {/*
            If these disagree the panel is lying about how the number was
            reached, which is exactly the failure this display exists to make
            impossible. Say so rather than quietly showing two figures.
          */}
          {Math.abs(evidence.arithmetic.total - link.score) > 0.01 && (
            <p className="mt-1 rounded border border-amber-200 bg-amber-50 p-1.5 text-[11px] text-amber-800">
              These rows sum to {evidence.arithmetic.total.toFixed(2)} but the
              link is scored {link.score.toFixed(2)}. The evidence shown does not
              fully account for the score — treat both with suspicion.
            </p>
          )}
        </>
      )}

      {!evidenceLoading && !evidence && (
        <ul className="mt-2 space-y-0.5">
          {link.evidenceSummary.sources.map((source) => (
            <li key={source} className="font-mono text-[11px] text-gray-500">
              {source}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
