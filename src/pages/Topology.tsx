import { useCallback, useEffect, useState } from "react";
import { Network } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../utils/api";
import SingleEcSelector from "../components/SingleEcSelector";
import VenuePicker from "../components/VenuePicker";
import type { VenueRow } from "../components/VenuePicker";
import TopologyShell from "../components/topology/TopologyShell";
import SnapshotManager from "../components/topology/panels/SnapshotManager";
import type { StoredLayout } from "../components/topology/state/store";
import type {
  Device,
  EvidenceResponse,
  Link,
  Port,
  SourceInfo,
  SnapshotMeta,
  TopologyFindings,
  SnapshotRow,
  UplinkCandidate,
  UplinkState,
  VenueOption,
} from "../components/topology/state/types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

function fmtTime(iso?: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}

/**
 * Topology — a visual map of a venue set, with an explainable confidence on
 * every link.
 *
 * Scope gating follows WiredWiz exactly (isR1 / needsEcSelection /
 * effectiveTenantId / ecChosen / scopeReady) because every R1 tool in this app
 * has to answer the same three questions before it can do anything.
 *
 * One deliberate difference: WiredWiz omits `venue_ids` when every venue is
 * selected, letting the backend treat it as a whole-tenant request. Topology
 * never does — a venue SET is the snapshot's identity, and "all of them" is a
 * different set from "these three that happen to be all of them today".
 */
export default function Topology() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers,
  } = useAuth();
  const activeController = controllers.find((c) => c.id === activeControllerId);
  const isR1 = activeControllerType === "RuckusONE";
  const needsEcSelection = activeControllerSubtype === "MSP";

  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const [ecPickerOpen, setEcPickerOpen] = useState(true);
  const effectiveTenantId = needsEcSelection
    ? ecId
    : activeController?.r1_tenant_id || null;
  const ecChosen = isR1 && (!needsEcSelection || !!ecId);

  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [venuesLoading, setVenuesLoading] = useState(false);
  const [selectedVenues, setSelectedVenues] = useState<string[]>([]);
  const [venueFilter, setVenueFilter] = useState("");

  const scopeReady = ecChosen && selectedVenues.length > 0;

  const [devices, setDevices] = useState<Device[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [meta, setMeta] = useState<SnapshotMeta | null>(null);
  const [wan, setWan] = useState<UplinkState | null>(null);
  // Which stored snapshot is on screen. null = the newest.
  const [activeSnapshot, setActiveSnapshot] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotRow[]>([]);
  // How long runs are kept. Stated in the UI rather than left to assumption:
  // these snapshots are the only change history this tool has.
  const [retention, setRetention] = useState<{
    ttlDays: number | null;
    maxSnapshots: number | null;
  }>({ ttlDays: null, maxSnapshots: null });
  const [graphLoading, setGraphLoading] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [deep, setDeep] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const base = `${API_BASE_URL}/topology/${activeControllerId}`;

  const qs = useCallback(
    (extra: Record<string, string | number | boolean> = {}) => {
      const p = new URLSearchParams();
      if (needsEcSelection && ecId) p.set("tenant_id", ecId);
      if (selectedVenues.length) p.set("venue_ids", selectedVenues.join(","));
      // Every read is pinned to the snapshot on screen, so the map, its
      // evidence and its exports can never come from different runs.
      if (activeSnapshot) p.set("snapshot", activeSnapshot);
      Object.entries(extra).forEach(([k, v]) => p.set(k, String(v)));
      return p.toString() ? `?${p}` : "";
    },
    [needsEcSelection, ecId, selectedVenues, activeSnapshot],
  );

  const loadVenues = useCallback(async () => {
    if (!ecChosen) return;
    setVenuesLoading(true);
    setError("");
    try {
      const p = new URLSearchParams();
      if (needsEcSelection && ecId) p.set("tenant_id", ecId);
      const res = await apiFetch(`${base}/venues?${p}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 300));
      const body = await res.json();
      setVenues(body.venues || []);
    } catch (e) {
      setError(`Could not load venues: ${e}`);
    } finally {
      setVenuesLoading(false);
    }
  }, [base, ecChosen, ecId, needsEcSelection]);

  const loadGraph = useCallback(async () => {
    if (!scopeReady) return;
    setGraphLoading(true);
    setError("");
    try {
      const res = await apiFetch(`${base}/graph${qs()}`, {
        credentials: "include",
      });
      if (res.status === 404) {
        // No snapshot for this venue SET. Not an error — the empty state says so.
        setDevices([]);
        setLinks([]);
        setMeta(null);
        return;
      }
      if (!res.ok) throw new Error((await res.text()).slice(0, 300));
      const body = await res.json();
      setDevices(body.nodes || []);
      setLinks(body.links || []);
      setMeta(body.meta || null);
      setWan(body.wan && body.wan.venues ? body.wan : null);
    } catch (e) {
      setError(`Could not load the map: ${e}`);
    } finally {
      setGraphLoading(false);
    }
  }, [base, qs, scopeReady]);

  const loadSnapshots = useCallback(async () => {
    if (!scopeReady) return;
    try {
      const res = await apiFetch(`${base}/snapshots${qs()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const body = await res.json();
        setSnapshots(body.snapshots || []);
        setRetention({
          ttlDays: body.ttlDays ?? null,
          maxSnapshots: body.maxSnapshots ?? null,
        });
      }
    } catch {
      /* the snapshot list is a convenience; its absence is not an error */
    }
  }, [base, qs, scopeReady]);

  const discover = useCallback(async () => {
    if (!scopeReady) return;
    setDiscovering(true);
    setError("");
    setNotice("");
    try {
      const res = await apiFetch(`${base}/discover${qs({ deep })}`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 300));
      const body = await res.json();
      const counts = body.meta?.counts || {};
      setNotice(
        `Discovered ${counts.devices ?? 0} devices and ${counts.links ?? 0} ` +
          `links in ${body.meta?.elapsedSeconds ?? "?"}s.`,
      );
      // A fresh run becomes the one on screen.
      setActiveSnapshot(null);
      await Promise.all([loadGraph(), loadSnapshots()]);
    } catch (e) {
      setError(`Discovery failed: ${e}`);
    } finally {
      setDiscovering(false);
    }
  }, [base, qs, deep, scopeReady, loadGraph, loadSnapshots]);

  const fetchEvidence = useCallback(
    async (linkId: string): Promise<EvidenceResponse | null> => {
      try {
        const res = await apiFetch(
          `${base}/links/${encodeURIComponent(linkId)}/evidence${qs()}`,
          { credentials: "include" },
        );
        if (!res.ok) return null;
        return (await res.json()) as EvidenceResponse;
      } catch {
        return null;
      }
    },
    [base, qs],
  );

  // ── human verdicts on links ─────────────────────────────────────────────
  // The endpoints hand back the links the verdict changed, already re-lensed
  // server-side. The shell patches those into the graph, so a verdict never
  // costs the user their arrangement — and the arithmetic that produced the new
  // tier is the same code the discovery run uses, not a second copy in here.
  const endpointsOf = (link: Link) => [
    { deviceId: link.a.deviceId, portIdent: link.a.ident },
    { deviceId: link.b.deviceId, portIdent: link.b.ident },
  ];

  const setVerdict = useCallback(
    async (link: Link, verdict: "confirm" | "reject", note: string) => {
      const res = await apiFetch(`${base}/overrides${qs()}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verdict, note, endpoints: endpointsOf(link) }),
      });
      if (!res.ok) {
        throw new Error((await res.text()).slice(0, 200) || "the server refused it");
      }
      const body = (await res.json()) as { links?: Link[] };
      return body.links ?? [];
    },
    [base, qs],
  );

  const clearVerdict = useCallback(
    async (link: Link) => {
      const key = link.override?.key;
      if (!key) return [];
      const res = await apiFetch(
        `${base}/overrides/${encodeURIComponent(key)}${qs()}`,
        { method: "DELETE", credentials: "include" },
      );
      if (!res.ok) {
        throw new Error((await res.text()).slice(0, 200) || "the server refused it");
      }
      const body = (await res.json()) as { links?: Link[] };
      return body.links ?? [];
    },
    [base, qs],
  );

  const fetchFindings = useCallback(async (): Promise<TopologyFindings | null> => {
    try {
      const res = await apiFetch(`${base}/findings${qs()}`, {
        credentials: "include",
      });
      return res.ok ? ((await res.json()) as TopologyFindings) : null;
    } catch {
      return null;
    }
  }, [base, qs]);

  const deleteSnapshot = useCallback(
    async (name: string) => {
      const res = await apiFetch(
        `${base}/snapshots/${encodeURIComponent(name)}${qs()}`,
        { method: "DELETE", credentials: "include" },
      );
      if (!res.ok) {
        throw new Error((await res.text()).slice(0, 200) || "the server refused it");
      }
      // If the run on screen was the one deleted, fall back to the newest.
      setActiveSnapshot((current) => (current === name ? null : current));
      await loadSnapshots();
      await loadGraph();
    },
    [base, qs, loadSnapshots, loadGraph],
  );

  const confirmUplink = useCallback(
    async (
      venueId: string,
      candidate: UplinkCandidate | null,
      action: "confirm" | "reject" | "clear",
    ) => {
      try {
        const res = await apiFetch(`${base}/uplinks/confirm${qs()}`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            venueId,
            deviceId: candidate?.deviceId ?? "",
            portIdent: candidate?.portIdent ?? "",
            action,
          }),
        });
        if (!res.ok) {
          setError(`Could not save the uplink: ${(await res.text()).slice(0, 200)}`);
          return;
        }
        // The verdict changes the GRAPH — a confirmed uplink materialises a
        // wan: node and its link — so the map is rebuilt, not just the panel.
        setNotice(
          action === "confirm"
            ? "Uplink confirmed. Re-run Discover to anchor the hierarchy on it."
            : "Saved.",
        );
        await loadGraph();
      } catch (e) {
        setError(`Could not save the uplink: ${e}`);
      }
    },
    [base, qs, loadGraph],
  );

  const exportUrl = useCallback(
    (path: string) => `${base}/${path}${qs()}`,
    [base, qs],
  );

  const fetchSources = useCallback(async (): Promise<SourceInfo[] | null> => {
    try {
      const res = await apiFetch(`${base}/sources`, { credentials: "include" });
      if (!res.ok) return null;
      return (await res.json()).sources || null;
    } catch {
      return null;
    }
  }, [base]);

  const fetchPorts = useCallback(
    async (deviceId: string): Promise<Port[] | null> => {
      try {
        const res = await apiFetch(
          `${base}/devices/${encodeURIComponent(deviceId)}/ports${qs()}`,
          { credentials: "include" },
        );
        if (!res.ok) return null;
        return (await res.json()).ports || [];
      } catch {
        return null;
      }
    },
    [base, qs],
  );

  const loadLayout = useCallback(async () => {
    try {
      const res = await apiFetch(`${base}/layout${qs()}`, {
        credentials: "include",
      });
      if (!res.ok) return null;
      const body = await res.json();
      // An empty object is "nothing saved yet", not a layout of nothing.
      if (!body || typeof body.version !== "number") return null;
      return { layout: body as StoredLayout, version: body.version };
    } catch {
      return null;
    }
  }, [base, qs]);

  const saveLayout = useCallback(
    async (layout: StoredLayout, version: number) => {
      try {
        const res = await apiFetch(`${base}/layout${qs()}`, {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...layout, version }),
        });
        if (res.status === 409) {
          // The server refuses a stale write rather than clobbering: it hands
          // back the version it actually holds so the caller can decide.
          const body = await res.json().catch(() => null);
          const detail = body?.detail ?? body;
          return { ok: false as const, version: Number(detail?.version ?? version) };
        }
        if (!res.ok) return { ok: false as const, version };
        const body = await res.json();
        return { ok: true as const, version: Number(body.version ?? version + 1) };
      } catch {
        return { ok: false as const, version };
      }
    },
    [base, qs],
  );

  // Reset on scope change, then reload. Mirrors WiredWiz's cascade.
  useEffect(() => {
    setVenues([]);
    setSelectedVenues([]);
    setDevices([]);
    setLinks([]);
    setMeta(null);
    setSnapshots([]);
    setActiveSnapshot(null);
    setError("");
    setNotice("");
    if (ecChosen) loadVenues();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ecChosen, effectiveTenantId]);

  useEffect(() => {
    if (!scopeReady) {
      setDevices([]);
      setLinks([]);
      setMeta(null);
      setWan(null);
      return;
    }
    loadGraph();
    loadSnapshots();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeReady, selectedVenues.join(","), activeSnapshot]);

  if (!isR1) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-gray-900">Topology</h1>
        <div className="mt-4 rounded-lg border border-yellow-200 bg-yellow-50 p-4 text-sm text-yellow-800">
          Topology reads the RUCKUS ONE API. Switch to a RUCKUS ONE controller
          to use it.
        </div>
      </div>
    );
  }

  const correlation = meta?.correlation;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-start gap-3">
        <Network className="mt-1 text-blue-600" size={26} />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Topology</h1>
          <p className="text-sm text-gray-500">
            What is plugged into what — inferred from LLDP, MAC tables, AP
            self-reports and R1&rsquo;s own topology, with an explainable
            confidence on every link. Read-only, and only when you ask.
          </p>
        </div>
      </div>

      {needsEcSelection && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          {ecId && !ecPickerOpen ? (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-gray-500">MSP-EC</span>
              <span className="font-medium text-gray-900">
                {ecName || ecId}
              </span>
              <button
                onClick={() => setEcPickerOpen(true)}
                className="ml-auto rounded border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
              >
                Change
              </button>
            </div>
          ) : (
            <>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-600">
                Select an MSP-EC
              </p>
              <SingleEcSelector
                controllerId={activeControllerId!}
                selectedEcId={ecId}
                onEcSelect={(id: string | null, ec: { name?: string }) => {
                  setEcId(id);
                  setEcName(ec?.name || null);
                  if (id) setEcPickerOpen(false);
                }}
              />
            </>
          )}
        </div>
      )}

      {ecChosen && (
        <VenuePicker
          venues={venues as unknown as VenueRow[]}
          loading={venuesLoading}
          selected={selectedVenues}
          setSelected={setSelectedVenues}
          filter={venueFilter}
          setFilter={setVenueFilter}
          emptyMessage="No venues in this tenant."
          renderMeta={(v) => {
            const venue = v as unknown as VenueOption;
            return (
              <>
                {venue.switches ?? 0} sw · {venue.aps ?? 0} AP
                {venue.hasSnapshot && (
                  <span className="text-green-600"> · mapped</span>
                )}
              </>
            );
          }}
          renderSummary={(sel) =>
            `${sel.length} of ${venues.length} selected` +
            (sel.length > 1
              ? " · one map across all of them"
              : "")
          }
          footer={
            selectedVenues.length > 1 ? (
              <p className="mt-2 text-xs text-gray-400">
                A topology is identified by its exact venue set, so this is a
                different map from any single venue on its own.
              </p>
            ) : null
          }
        />
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-900">
          {notice}
        </div>
      )}

      {scopeReady && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-white p-4">
          <button
            onClick={discover}
            disabled={discovering}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {discovering ? "Discovering…" : "Discover"}
          </button>
          <label
            className="flex items-center gap-1.5 text-xs text-gray-600"
            title="Also fans out per-AP LLDP and per-switch routing reads. Slower, and R1's AP neighbour cache is usually cold, so expect little from it."
          >
            <input
              type="checkbox"
              checked={deep}
              onChange={(e) => setDeep(e.target.checked)}
            />
            Deep scan
          </label>
          {snapshots.length > 0 && (
            <label
              className="flex items-center gap-1.5 text-xs text-gray-600"
              title="Older runs are kept so you can look back. Every read on the page follows this choice."
            >
              <span>Showing</span>
              <select
                value={activeSnapshot ?? ""}
                onChange={(e) => setActiveSnapshot(e.target.value || null)}
                className="rounded border px-2 py-1 text-xs"
              >
                <option value="">
                  Latest — {fmtTime(snapshots[0]?.takenAt)}
                </option>
                {snapshots.slice(1).map((snap) => (
                  <option key={snap.name} value={snap.name}>
                    {fmtTime(snap.takenAt)} · {snap.counts?.devices ?? 0} devices
                    {snap.deep ? " · deep" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {meta && (
            <span className="text-xs text-gray-500">{meta.elapsedSeconds}s</span>
          )}
          <SnapshotManager
            snapshots={snapshots}
            ttlDays={retention.ttlDays}
            maxSnapshots={retention.maxSnapshots}
            activeSnapshot={activeSnapshot}
            onDelete={deleteSnapshot}
            fmtTime={fmtTime}
          />
          {correlation && (
            <span className="ml-auto flex flex-wrap gap-1.5 text-[11px]">
              {Object.entries(correlation.byTier || {}).map(([tier, n]) => (
                <span
                  key={tier}
                  className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700"
                >
                  {n} {tier}
                </span>
              ))}
            </span>
          )}
        </div>
      )}

      {scopeReady ? (
        <TopologyShell
          devices={devices}
          links={links}
          loading={graphLoading || discovering}
          fetchEvidence={fetchEvidence}
          fetchSources={fetchSources}
          fetchFindings={fetchFindings}
          wan={wan}
          exportUrl={exportUrl}
          onConfirmUplink={confirmUplink}
          fetchPorts={fetchPorts}
          loadLayout={loadLayout}
          saveLayout={saveLayout}
          onSetVerdict={setVerdict}
          onClearVerdict={clearVerdict}
          banner={
            correlation?.unattachedAps ? (
              <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
                {correlation.unattachedAps} online access point
                {correlation.unattachedAps === 1 ? " is" : "s are"} up but
                nothing reports what feeds them — they are almost certainly
                cabled to switches RUCKUS ONE does not manage.
              </div>
            ) : null
          }
        />
      ) : (
        ecChosen && (
          <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-500">
            Select one or more venues to map.
          </div>
        )
      )}
    </div>
  );
}
