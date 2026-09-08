import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  TopologyStoreContext,
  createTopologyStore,
  layoutPayload,
  useTopology,
  useTopologyStore,
} from "./state/store";
import type { StoredLayout } from "./state/store";
import type {
  Device,
  EvidenceResponse,
  Link,
  Port,
  SourceInfo,
  TopologyFindings,
  UplinkCandidate,
  UplinkState,
} from "./state/types";
import { ReactFlowProvider } from "@xyflow/react";
import TopologyCanvas from "./TopologyCanvas";
import Toolbar from "./panels/Toolbar";
import Inspector from "./panels/Inspector";

/**
 * Provider + three-pane layout: toolbar on top, canvas left, inspector right.
 *
 * The store is created HERE, per mount, rather than at module scope — so it is
 * page-local, cannot leak into the rest of the app, and is thrown away when the
 * page unmounts.
 */

type Props = {
  devices: Device[];
  links: Link[];
  loading: boolean;
  banner?: ReactNode;
  fetchEvidence?: (linkId: string) => Promise<EvidenceResponse | null>;
  fetchSources?: () => Promise<SourceInfo[] | null>;
  fetchFindings?: () => Promise<TopologyFindings | null>;
  /** WAN candidates and verdicts for the current snapshot. */
  wan?: UplinkState | null;
  onConfirmUplink?: (
    venueId: string,
    candidate: UplinkCandidate | null,
    action: "confirm" | "reject" | "clear",
  ) => Promise<void>;
  /** Builds a scoped backend URL for a download path. */
  exportUrl?: (path: string) => string;
  fetchPorts?: (deviceId: string) => Promise<Port[] | null>;
  /**
   * Record or clear a human verdict on one link. Resolves with the links the
   * verdict changed, already re-lensed by the server — the shell patches those
   * into the graph rather than reloading it, so an arrangement survives a
   * verdict.
   */
  onSetVerdict?: (
    link: Link,
    verdict: "confirm" | "reject",
    note: string,
  ) => Promise<Link[]>;
  onClearVerdict?: (link: Link) => Promise<Link[]>;
  /** Stored arrangement for this venue set, or null if there is none yet. */
  loadLayout?: () => Promise<{ layout: StoredLayout; version: number } | null>;
  saveLayout?: (
    layout: StoredLayout,
    version: number,
  ) => Promise<{ ok: true; version: number } | { ok: false; version: number }>;
};

/** Debounce for the arrangement write. Dragging emits one event per drop. */
const SAVE_DEBOUNCE_MS = 1500;

function ShellBody({
  banner,
  fetchEvidence,
  fetchPorts,
  fetchSources,
  fetchFindings,
  onConfirmUplink,
  exportUrl,
  saveLayout,
  loadLayout,
  onSetVerdict,
  onClearVerdict,
}: {
  banner?: ReactNode;
  fetchEvidence?: Props["fetchEvidence"];
  fetchPorts?: Props["fetchPorts"];
  fetchSources?: Props["fetchSources"];
  fetchFindings?: Props["fetchFindings"];
  onConfirmUplink?: Props["onConfirmUplink"];
  exportUrl?: Props["exportUrl"];
  saveLayout?: Props["saveLayout"];
  loadLayout?: Props["loadLayout"];
  onSetVerdict?: Props["onSetVerdict"];
  onClearVerdict?: Props["onClearVerdict"];
}) {
  const store = useTopologyStore();
  const layoutDirty = useTopology((s) => s.layoutDirty);
  const layoutSaving = useTopology((s) => s.layoutSaving);
  const layoutConflict = useTopology((s) => s.layoutConflict);
  const applyLayout = useTopology((s) => s.applyLayout);
  const markLayoutSaved = useTopology((s) => s.markLayoutSaved);
  const setLayoutSaving = useTopology((s) => s.setLayoutSaving);
  const setLayoutConflict = useTopology((s) => s.setLayoutConflict);
  const portsOpen = useTopology((s) => s.portsOpen);
  const loadedPorts = useTopology((s) => s.ports);
  const setPorts = useTopology((s) => s.setPorts);
  const setPortsLoading = useTopology((s) => s.setPortsLoading);
  const selectedLinkId = useTopology((s) => s.selectedLinkId);
  const loading = useTopology((s) => s.loading);
  const deviceCount = useTopology((s) => s.devices.length);

  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const requestRef = useRef(0);
  // Bumped by a verdict, so the arithmetic below it reloads. A verdict adds a
  // row worth +99 to that list; leaving the old rows on screen would show a
  // sum that no longer matches the tier drawn above it.
  const [verdictSeq, setVerdictSeq] = useState(0);

  const patchLinks = useTopology((s) => s.patchLinks);
  const applyVerdict = useCallback(
    async (run: () => Promise<Link[]>) => {
      patchLinks(await run());
      setVerdictSeq((n) => n + 1);
    },
    [patchLinks],
  );
  const setVerdict = useMemo(
    () =>
      onSetVerdict
        ? (link: Link, verdict: "confirm" | "reject", note: string) =>
            applyVerdict(() => onSetVerdict(link, verdict, note))
        : undefined,
    [onSetVerdict, applyVerdict],
  );
  const clearVerdict = useMemo(
    () =>
      onClearVerdict
        ? (link: Link) => applyVerdict(() => onClearVerdict(link))
        : undefined,
    [onClearVerdict, applyVerdict],
  );

  useEffect(() => {
    if (!selectedLinkId || !fetchEvidence) {
      setEvidence(null);
      return;
    }
    const token = ++requestRef.current;
    setEvidenceLoading(true);
    setEvidence(null);
    fetchEvidence(selectedLinkId)
      .then((rows) => {
        // Ignore a response for a link the user has already clicked away from.
        if (requestRef.current === token) setEvidence(rows);
      })
      .finally(() => {
        if (requestRef.current === token) setEvidenceLoading(false);
      });
  }, [selectedLinkId, fetchEvidence, verdictSeq]);

  // Ports are fetched the first time a strip is opened, and cached for the
  // life of the snapshot — a 200-switch venue has ~9600 ports, which is a lot
  // to ship for a view most people never open on most devices.
  const inFlight = useRef(new Set<string>());
  useEffect(() => {
    if (!fetchPorts) return;
    Object.entries(portsOpen).forEach(([deviceId, open]) => {
      if (!open || loadedPorts[deviceId] || inFlight.current.has(deviceId)) {
        return;
      }
      inFlight.current.add(deviceId);
      setPortsLoading(deviceId, true);
      fetchPorts(deviceId)
        .then((rows) => setPorts(deviceId, rows ?? []))
        .finally(() => {
          inFlight.current.delete(deviceId);
          setPortsLoading(deviceId, false);
        });
    });
  }, [portsOpen, loadedPorts, fetchPorts, setPorts, setPortsLoading]);

  // Save the arrangement, debounced. Reads state at FIRE time rather than
  // closing over it, so a burst of drags collapses into one write of the final
  // arrangement instead of one stale write per drag.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!saveLayout || !layoutDirty || layoutSaving || layoutConflict) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const state = store.getState();
      setLayoutSaving(true);
      saveLayout(layoutPayload(state), state.layoutVersion)
        .then((result) => {
          if (result.ok) markLayoutSaved(result.version);
          // A refused write means someone else rearranged this map. Local work
          // is KEPT and the choice is handed to the user -- silently loading
          // theirs would throw away whatever they just did.
          else setLayoutConflict(result.version);
        })
        .finally(() => setLayoutSaving(false));
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [
    layoutDirty,
    layoutSaving,
    layoutConflict,
    saveLayout,
    store,
    markLayoutSaved,
    setLayoutSaving,
    setLayoutConflict,
  ]);

  const resolveConflict = (keepMine: boolean) => {
    if (!loadLayout || !saveLayout) return;
    if (keepMine) {
      const state = store.getState();
      setLayoutConflict(null);
      setLayoutSaving(true);
      saveLayout(layoutPayload(state), layoutConflict ?? state.layoutVersion)
        .then((r) => (r.ok ? markLayoutSaved(r.version) : setLayoutConflict(r.version)))
        .finally(() => setLayoutSaving(false));
    } else {
      loadLayout().then((stored) => {
        if (stored) applyLayout(stored.layout, stored.version);
        else setLayoutConflict(null);
      });
    }
  };

  return (
    <div className="flex h-[calc(100vh-13rem)] min-h-[28rem] flex-col overflow-hidden rounded-lg border border-gray-200 bg-white">
      <Toolbar
        fetchSources={fetchSources}
        fetchFindings={fetchFindings}
        onConfirmUplink={onConfirmUplink}
        exportUrl={exportUrl}
      />
      {layoutConflict !== null && (
        <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
          <span>
            This map&rsquo;s arrangement was changed somewhere else since you
            opened it. Your changes have not been saved.
          </span>
          <button
            onClick={() => resolveConflict(true)}
            className="rounded border border-amber-400 px-2 py-0.5 font-medium hover:bg-amber-100"
          >
            Keep mine
          </button>
          <button
            onClick={() => resolveConflict(false)}
            className="rounded border border-amber-400 px-2 py-0.5 font-medium hover:bg-amber-100"
          >
            Load theirs
          </button>
        </div>
      )}
      {banner}
      <div className="flex flex-1 overflow-hidden">
        <div className="relative flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70">
              <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
            </div>
          )}
          {!loading && deviceCount === 0 && (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-gray-500">
              No topology yet for this venue set. Run a discovery to build one.
            </div>
          )}
          {deviceCount > 0 && <TopologyCanvas />}
        </div>
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-gray-200 bg-gray-50">
          <Inspector
            evidence={evidence}
            evidenceLoading={evidenceLoading}
            onSetVerdict={setVerdict}
            onClearVerdict={clearVerdict}
          />
        </aside>
      </div>
    </div>
  );
}

export default function TopologyShell({
  devices,
  links,
  loading,
  banner,
  fetchEvidence,
  fetchPorts,
  fetchSources,
  fetchFindings,
  wan,
  onConfirmUplink,
  exportUrl,
  loadLayout,
  saveLayout,
  onSetVerdict,
  onClearVerdict,
}: Props) {
  const store = useMemo(() => createTopologyStore(), []);

  useEffect(() => {
    store.getState().setGraph(devices, links);
    // setGraph deliberately clears the view state, because a new snapshot may
    // not contain the same node ids. The stored arrangement belongs to the
    // VENUE SET rather than to a snapshot, so it is restored straight after —
    // otherwise every re-discovery would throw away work done by hand.
    if (loadLayout && devices.length) {
      loadLayout().then((stored) => {
        if (stored) store.getState().applyLayout(stored.layout, stored.version);
      });
    }
  }, [store, devices, links, loadLayout]);

  useEffect(() => {
    store.getState().setLoading(loading);
  }, [store, loading]);

  useEffect(() => {
    store.getState().setWan(wan ?? null);
  }, [store, wan]);

  return (
    <TopologyStoreContext.Provider value={store}>
      {/* Wraps the toolbar as well as the canvas: the export menu measures the
          graph through useReactFlow(), and it lives in the toolbar. */}
      <ReactFlowProvider>
        <ShellBody
        banner={banner}
        fetchEvidence={fetchEvidence}
        fetchPorts={fetchPorts}
        fetchSources={fetchSources}
        fetchFindings={fetchFindings}
        onConfirmUplink={onConfirmUplink}
          loadLayout={loadLayout}
          saveLayout={saveLayout}
          onSetVerdict={onSetVerdict}
          onClearVerdict={onClearVerdict}
        />
      </ReactFlowProvider>
    </TopologyStoreContext.Provider>
  );
}
