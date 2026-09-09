import { useEffect, useState } from "react";
import { AlertTriangle, MapPin, PlugZap, Search, Ghost } from "lucide-react";
import { useTopology } from "../state/store";
import type { TopologyFindings } from "../state/types";

/**
 * What the map implies, as lists.
 *
 * A canvas of four thousand nodes is very good at showing you a shape and very
 * bad at showing you an absence. Every row here is a device the graph could not
 * fully place, and each list answers a different question — which is why they
 * are four lists and not one "problems" list. Lumping "there is unmanaged gear
 * on your network" together with "you did not select that venue" would make
 * both easier to ignore.
 *
 * Clicking a row selects the device on the canvas, so the list is a way into
 * the map rather than a replacement for it.
 */

type Props = {
  fetchFindings?: () => Promise<TopologyFindings | null>;
};

const SECTIONS = [
  {
    key: "unmanaged" as const,
    tab: "Unmanaged",
    icon: PlugZap,
    title: "Not managed by RUCKUS ONE",
    blurb:
      "Something is plugged in that R1 does not manage — a router, a firewall, " +
      "or third-party switch. Each row names the port it hangs off.",
    tone: "text-amber-700",
  },
  {
    key: "outOfScope" as const,
    tab: "Other venues",
    icon: MapPin,
    title: "In a venue this map does not cover",
    blurb:
      "Real, managed devices — the map just does not include their venue. Add " +
      "it to the selection and they stop being unknown boxes.",
    tone: "text-blue-700",
  },
  {
    key: "unattached" as const,
    tab: "No uplink",
    icon: AlertTriangle,
    title: "Online, but nothing says what feeds them",
    blurb:
      "Up and reporting, with no uplink anything can see. Almost always cabled " +
      "to a switch R1 does not manage.",
    tone: "text-amber-700",
  },
  {
    key: "ghosts" as const,
    tab: "Ghosts",
    icon: Ghost,
    title: "In inventory, but nowhere on the network",
    blurb:
      "Not online, and no LLDP, MAC table or topology row mentions them. " +
      "Either genuinely dead, or decommissioned and never removed from R1.",
    tone: "text-gray-600",
  },
];

export default function FindingsPanel({ fetchFindings }: Props) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<TopologyFindings | null>(null);
  const [loading, setLoading] = useState(false);
  const [section, setSection] = useState<(typeof SECTIONS)[number]["key"]>("unmanaged");
  const select = useTopology((s) => s.selectDevice);

  useEffect(() => {
    if (!open || data || !fetchFindings) return;
    setLoading(true);
    fetchFindings()
      .then(setData)
      .finally(() => setLoading(false));
  }, [open, data, fetchFindings]);

  if (!fetchFindings) return null;
  const counts = data?.counts;
  const flagged = (counts?.unmanaged ?? 0) + (counts?.unattached ?? 0);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        title="Devices the map could not fully place"
        className="flex items-center gap-1 rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50"
      >
        <Search size={12} /> Loose ends
        {flagged > 0 && (
          <span className="rounded bg-amber-100 px-1 text-[10px] font-medium text-amber-800">
            {flagged}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-30 mt-1 w-[27rem] rounded-lg border border-gray-200 bg-white shadow-lg">
            <div className="flex flex-wrap gap-1 border-b border-gray-100 p-1.5">
              {SECTIONS.map(({ key, tab }) => (
                <button
                  key={key}
                  onClick={() => setSection(key)}
                  className={`rounded px-1.5 py-1 text-[11px] ${
                    section === key
                      ? "bg-gray-900 text-white"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {tab}
                  <span className="ml-1 opacity-70">{counts?.[key] ?? "–"}</span>
                </button>
              ))}
            </div>

            {loading && <p className="p-3 text-xs text-gray-400">loading…</p>}

            {!loading &&
              SECTIONS.filter((s) => s.key === section).map(
                ({ key, icon: Icon, title, blurb, tone }) => {
                  const rows = data?.[key] ?? [];
                  return (
                    <div key={key} className="p-2">
                      <p className={`flex items-center gap-1.5 text-xs font-semibold ${tone}`}>
                        <Icon size={13} /> {title}
                      </p>
                      <p className="mt-0.5 text-[11px] text-gray-500">{blurb}</p>
                      {rows.length === 0 ? (
                        <p className="mt-2 rounded bg-green-50 px-2 py-1.5 text-[11px] text-green-800">
                          None found in this map.
                        </p>
                      ) : (
                        <ul className="mt-1.5 max-h-72 space-y-0.5 overflow-y-auto">
                          {rows.map((row) => (
                            <li key={row.deviceId}>
                              <button
                                onClick={() => {
                                  select(row.deviceId);
                                  setOpen(false);
                                }}
                                className="w-full rounded px-1.5 py-1 text-left hover:bg-gray-50"
                              >
                                <p className="truncate text-xs text-gray-900">
                                  {row.name || row.mac || row.deviceId}
                                  {row.status && (
                                    <span className="ml-1.5 text-[10px] uppercase text-gray-400">
                                      {row.rawStatus || row.status}
                                    </span>
                                  )}
                                </p>
                                <p className="truncate text-[11px] text-gray-500">
                                  {[
                                    row.venueName,
                                    row.model,
                                    row.seenFrom?.length
                                      ? `via ${row.seenFrom
                                          .slice(0, 2)
                                          .map((s) => `${s.device} ${s.port}`)
                                          .join(", ")}${
                                          row.seenFrom.length > 2
                                            ? ` +${row.seenFrom.length - 2}`
                                            : ""
                                        }`
                                      : "",
                                    row.hint,
                                  ]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </p>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                },
              )}
          </div>
        </>
      )}
    </div>
  );
}
