import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleEcSelector from "@/components/SingleEcSelector";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

/**
 * WiredWiz — read-only switch crawler and loop hunter.
 *
 * Everything this page triggers is read-only against RUCKUS ONE. It never
 * pushes config, creates a backup, reboots, or syncs a switch.
 *
 * Human-driven only: every request here fires because someone clicked. There is
 * no auto-refresh, no polling timer, and no scheduled crawl. Configuration is
 * read one switch at a time, only when that switch is opened.
 */

type Tab = "health" | "findings" | "mac" | "inventory" | "config" | "checks" | "snapshots";

interface SnapshotMeta {
  file: string; takenAt: string; takenAtEpoch: number;
  switches: number; ports: number; macs: number;
  complete: boolean; expected: number | null; collected: number | null;
  sizeBytes: number; expiresAtEpoch?: number;
}

interface VenueRow {
  venueId: string; venueName: string; switches: number; online: number; offline: number;
}

interface SwitchRow {
  id: string; name: string; serialNumber: string; model: string;
  firmwareVersion: string; ipAddress: string; deviceStatus: string;
  venueId: string; venueName: string; uptime: string; numOfPorts: number;
  isStack: boolean; clientCount: number; cpu: string; memory: string;
  crawledPorts: number; upPorts: number; learnedMacs: number;
}

const fmtTime = (iso?: string) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" }) : "—";

const fmtNum = (n: number | null | undefined, d = 1) =>
  n === null || n === undefined ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: d });

const fmtDuration = (s: number) =>
  s < 90 ? `${Math.round(s)}s` : `${(s / 60).toFixed(1)} min`;

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red-50 border-red-300 text-red-900",
  warning: "bg-amber-50 border-amber-300 text-amber-900",
  info: "bg-blue-50 border-blue-200 text-blue-900",
};
const SEVERITY_BADGE: Record<string, string> = {
  critical: "bg-red-600 text-white",
  warning: "bg-amber-500 text-white",
  info: "bg-blue-500 text-white",
};

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "ONLINE" ? "bg-green-100 text-green-800"
    : status === "OFFLINE" ? "bg-red-100 text-red-800"
    : status === "PREPROVISIONED" ? "bg-gray-100 text-gray-600"
    : "bg-yellow-100 text-yellow-800";
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${tone}`}>{status}</span>;
}

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="font-semibold text-gray-800">{title}</h3>
      </div>
      {hint && <p className="text-xs text-gray-500 mb-3">{hint}</p>}
      {children}
    </div>
  );
}

export default function WiredWiz() {
  const { activeControllerId, activeControllerType, activeControllerSubtype, controllers } = useAuth();
  const activeController = controllers.find((c) => c.id === activeControllerId);
  const isR1 = activeControllerType === "RuckusONE";
  const needsEcSelection = activeControllerSubtype === "MSP";

  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const [ecPickerOpen, setEcPickerOpen] = useState(true);
  const effectiveTenantId = needsEcSelection ? ecId : activeController?.r1_tenant_id || null;
  const ecChosen = isR1 && (!needsEcSelection || !!ecId);

  // Venue scope — checks are normally run per venue, occasionally across several.
  const [venues, setVenues] = useState<VenueRow[]>([]);
  const [venuesLoading, setVenuesLoading] = useState(false);
  const [selectedVenues, setSelectedVenues] = useState<string[]>([]);
  const [venueFilter, setVenueFilter] = useState("");
  const [lastSnapshotScope, setLastSnapshotScope] = useState<any>(null);

  // Nothing runs until a venue scope exists — every action is venue-scoped.
  const scopeReady = ecChosen && selectedVenues.length > 0;

  const [tab, setTab] = useState<Tab>("health");

  const [health, setHealth] = useState<any>(null);
  const [baselines, setBaselines] = useState<any[]>([]);
  // Retention is enforced server-side; the UI reads it back rather than
  // hardcoding it, so stored data never disappears without the page saying why.
  const [snapshotTtlDays, setSnapshotTtlDays] = useState<number | null>(null);
  const [baselineTtlDays, setBaselineTtlDays] = useState<number | null>(null);
  const [macTables, setMacTables] = useState<any>(null);
  const [catalogue, setCatalogue] = useState<any>(null);
  const [catFilter, setCatFilter] = useState("");
  const [macFilter, setMacFilter] = useState("");
  const [baselining, setBaselining] = useState(false);
  const [healthRunning, setHealthRunning] = useState(false);
  const [auditConfigs, setAuditConfigs] = useState(false);
  const [openFinding, setOpenFinding] = useState<string | null>(null);
  const [crawling, setCrawling] = useState(false);
  const [minWindow, setMinWindow] = useState(900);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [snapshots, setSnapshots] = useState<SnapshotMeta[]>([]);
  const [analysis, setAnalysis] = useState<any>(null);
  const [switches, setSwitches] = useState<SwitchRow[]>([]);
  // Which snapshot the inventory actually came from. Without this a scope
  // mismatch shows up only as counts that disagree with the crawl you just ran.
  const [switchesScope, setSwitchesScope] = useState<any>(null);
  const [switchFilter, setSwitchFilter] = useState("");
  const [selectedSwitch, setSelectedSwitch] = useState<SwitchRow | null>(null);
  const [config, setConfig] = useState<any>(null);
  const [configLoading, setConfigLoading] = useState(false);

  const qs = useCallback(
    (extra: Record<string, string | number | boolean> = {}) => {
      const p = new URLSearchParams();
      if (needsEcSelection && ecId) p.set("tenant_id", ecId);
      // Scope every call. Omitted only when every venue is selected, so the
      // backend can treat it as a whole-tenant request.
      if (selectedVenues.length && selectedVenues.length !== venues.length) {
        p.set("venue_ids", selectedVenues.join(","));
      }
      Object.entries(extra).forEach(([k, v]) => p.set(k, String(v)));
      return p.toString() ? `?${p}` : "";
    },
    [needsEcSelection, ecId, selectedVenues, venues.length],
  );

  const base = `${API_BASE_URL}/wiredwiz/${activeControllerId}`;

  const loadSnapshots = useCallback(async () => {
    if (!scopeReady) return;
    const res = await apiFetch(`${base}/snapshots${qs()}`, { credentials: "include" });
    if (res.ok) {
      const body = await res.json();
      setSnapshots(body.snapshots || []);
      setSnapshotTtlDays(body.ttlDays ?? null);
    }
  }, [base, qs, scopeReady]);

  const loadAnalysis = useCallback(async () => {
    if (!scopeReady) return;
    setError("");
    const res = await apiFetch(`${base}/analysis${qs({ min_window: minWindow })}`, {
      credentials: "include",
    });
    if (res.ok) {
      setAnalysis(await res.json());
    } else if (res.status === 404) {
      setAnalysis(null);
    } else {
      setError(`Analysis failed: ${res.status}`);
    }
  }, [base, qs, minWindow, scopeReady]);

  const loadVenues = useCallback(async () => {
    if (!ecChosen) return;
    setVenuesLoading(true);
    try {
      const p = new URLSearchParams();
      if (needsEcSelection && ecId) p.set("tenant_id", ecId);
      const res = await apiFetch(`${base}/venues?${p}`, { credentials: "include" });
      if (!res.ok) { setError(`Could not load venues: HTTP ${res.status}`); return; }
      const body = await res.json();
      setVenues(body.venues || []);
      setLastSnapshotScope(body.lastSnapshot || null);
      // Pre-select what was last crawled, so returning to the page resumes where
      // you left off rather than making you re-pick.
      const prior: string[] = body.lastSnapshot?.scopeVenueIds
        || body.lastSnapshot?.venueIds || [];
      const valid = prior.filter((v: string) =>
        (body.venues || []).some((x: VenueRow) => x.venueId === v));
      if (valid.length) setSelectedVenues(valid);
    } finally {
      setVenuesLoading(false);
    }
  }, [base, ecChosen, needsEcSelection, ecId]);

  const loadMacTables = useCallback(async () => {
    if (!scopeReady) return;
    const res = await apiFetch(`${base}/macTables${qs()}`, { credentials: "include" });
    if (res.ok) setMacTables(await res.json());
    else if (res.status === 404) setMacTables(null);
  }, [base, qs, scopeReady]);

  const loadCatalogue = useCallback(async () => {
    if (!ecChosen) return;
    const p = new URLSearchParams();
    if (needsEcSelection && ecId) p.set("tenant_id", ecId);
    const res = await apiFetch(`${base}/checks?${p}`, { credentials: "include" });
    if (res.ok) setCatalogue(await res.json());
  }, [base, ecChosen, needsEcSelection, ecId]);

  const loadHealth = useCallback(async () => {
    if (!scopeReady) return;
    const res = await apiFetch(`${base}/health${qs()}`, { credentials: "include" });
    if (res.ok) setHealth(await res.json());
    else if (res.status === 404) setHealth(null);
  }, [base, qs, scopeReady]);

  const loadBaselines = useCallback(async () => {
    if (!scopeReady) return;
    const res = await apiFetch(`${base}/baselines${qs()}`, { credentials: "include" });
    if (res.ok) {
      const body = await res.json();
      setBaselines(body.baselines || []);
      setBaselineTtlDays(body.ttlDays ?? null);
    }
  }, [base, qs, scopeReady]);

  const runBaseline = async () => {
    setBaselining(true); setError(""); setNotice("");
    try {
      const res = await apiFetch(`${base}/baseline${qs()}`, {
        method: "POST", credentials: "include",
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
        setError(`Baseline failed: ${detail}`);
        return;
      }
      const r = await res.json();
      setNotice(
        `Baseline captured: ${r.configsStored} of ${r.switchesTargeted} switch configs stored` +
        (r.noBackup ? `, ${r.noBackup} had no backup in R1` : "") +
        (r.rejectedByRedaction ? `, ${r.rejectedByRedaction} rejected by the redaction gate` : "") +
        ".",
      );
      await loadBaselines();
    } finally {
      setBaselining(false);
    }
  };

  const loadSwitches = useCallback(async () => {
    if (!scopeReady) return;
    const res = await apiFetch(`${base}/switches${qs()}`, { credentials: "include" });
    if (res.ok) {
      const body = await res.json();
      setSwitches(body.switches || []);
      setSwitchesScope(body.scope || null);
    } else if (res.status === 404) {
      setSwitches([]); setSwitchesScope(null);
    }
  }, [base, qs, scopeReady]);

  // EC changed → reset everything and fetch its venues.
  useEffect(() => {
    setVenues([]); setSelectedVenues([]); setVenueFilter("");
    setAnalysis(null); setSwitches([]); setSwitchesScope(null); setSnapshots([]); setHealth(null);
    setBaselines([]); setMacTables(null); setSelectedSwitch(null); setConfig(null);
    setError(""); setNotice("");
    if (ecChosen) loadVenues();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ecChosen, effectiveTenantId]);

  // Venue scope changed → reload everything at the new scope.
  useEffect(() => {
    if (!scopeReady) return;
    loadSnapshots(); loadAnalysis(); loadSwitches(); loadBaselines(); loadHealth();
    loadMacTables(); loadCatalogue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeReady, selectedVenues.join(",")]);

  const runCrawl = async () => {
    setCrawling(true); setError(""); setNotice("");
    try {
      const res = await apiFetch(`${base}/crawl${qs()}`, {
        method: "POST", credentials: "include",
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
        setError(`Crawl failed: ${detail}`);
        return;
      }
      const r = await res.json();
      // A window-capped query reports collected === expected === the ceiling, so
      // "only 10,000 of 10,000 rows came back" would be nonsense. Name the real
      // cause instead: the query ceiling truncated a venue we could not split.
      const capped = (r.completeness?.shortfalls || []).filter((s: any) => s.windowCapped);
      setNotice(
        r.complete
          ? `Crawled ${r.switches} switches, ${fmtNum(r.ports, 0)} ports, ${fmtNum(r.macs, 0)} MACs across `
            + `${Object.keys(r.venues || {}).length} venue(s) in ${fmtDuration(r.elapsedSeconds)}.`
          : capped.length
            ? `TRUNCATED crawl — ${capped.length} quer${capped.length === 1 ? "y" : "ies"} hit the `
              + `10,000-row query ceiling and splitting per switch did not clear it, so some rows `
              + `were never returned. Crawl a narrower venue selection. This snapshot will not be used for rates.`
            : `INCOMPLETE crawl — only ${fmtNum(r.completeness?.collected, 0)} of ${fmtNum(r.completeness?.expected, 0)} rows came back. This snapshot will not be used for rates.`,
      );
      await Promise.all([loadSnapshots(), loadAnalysis(), loadSwitches(), loadMacTables()]);
    } finally {
      setCrawling(false);
    }
  };

  const runHealth = async () => {
    setHealthRunning(true); setError(""); setNotice("");
    try {
      const res = await apiFetch(
        `${base}/health${qs({ min_window: minWindow, audit_configs: auditConfigs })}`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
        setError(`Checks failed: ${detail}`);
        return;
      }
      setHealth(await res.json());
      await loadCatalogue();   // refresh run status on the catalogue
    } finally {
      setHealthRunning(false);
    }
  };

  const openConfig = async (sw: SwitchRow) => {
    setSelectedSwitch(sw); setConfig(null); setConfigLoading(true); setTab("config"); setError("");
    try {
      const res = await apiFetch(
        `${base}/switches/${encodeURIComponent(sw.id)}/config${qs({ venue_id: sw.venueId })}`,
        { credentials: "include" },
      );
      if (res.ok) setConfig(await res.json());
      else {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
        setError(detail);
      }
    } finally {
      setConfigLoading(false);
    }
  };

  const venueScopeLabel = selectedVenues.length === 0 ? ""
    : selectedVenues.length === venues.length ? "all venues"
    : selectedVenues.length === 1
      ? (venues.find((v) => v.venueId === selectedVenues[0])?.venueName || "1 venue")
      : `${selectedVenues.length} venues`;

  const filteredSwitches = useMemo(() => {
    const q = switchFilter.trim().toLowerCase();
    if (!q) return switches;
    return switches.filter((s) =>
      [s.name, s.serialNumber, s.model, s.ipAddress, s.venueName]
        .some((v) => (v || "").toLowerCase().includes(q)));
  }, [switches, switchFilter]);

  // ─────────────────────────────────────────────────────────
  if (!isR1) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">WiredWiz</h1>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 text-sm text-yellow-800">
          WiredWiz reads switches through the RUCKUS ONE API. Select a RuckusONE
          controller to use it.
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold">
          WiredWiz{ecName ? ` — ${ecName}` : activeController ? ` — ${activeController.name}` : ""}
        </h1>
        <p className="text-sm text-gray-500">
          Read-only switch crawler and loop hunter. Pulls port counters, the MAC
          table and LLDP topology. Never writes to a switch, never runs on a
          schedule — every crawl happens because you clicked.
        </p>
      </div>

      {health && !health.error && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-gray-600">Last check run {fmtTime(health.ranAt)}:</span>
          {(["critical", "warning", "info"] as const).map((sev) => (
            <button key={sev} onClick={() => setTab("health")}
              className={`px-2.5 py-1 rounded font-medium ${
                health.counts[sev] ? SEVERITY_BADGE[sev] : "bg-gray-100 text-gray-500"}`}>
              {health.counts[sev]} {sev}
            </button>
          ))}
          {health.checksSkipped?.length > 0 && (
            <span className="text-xs text-gray-500">
              {health.checksSkipped.length} check(s) skipped
            </span>
          )}
        </div>
      )}

      {needsEcSelection && (
        ecId && !ecPickerOpen ? (
          /* Collapsed to a single line once chosen — the picker is a large table
             and there is no reason to keep it on screen for the rest of the session. */
          <div className="bg-white border border-gray-200 rounded-lg px-4 py-2.5 flex items-center gap-3">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              End Customer
            </span>
            <span className="font-medium text-gray-900">{ecName || ecId}</span>
            <span className="text-xs text-gray-400 font-mono">{ecId}</span>
            <button
              onClick={() => setEcPickerOpen(true)}
              className="ml-auto px-3 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50">
              Change
            </button>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs font-semibold text-gray-600">
                MSP controller — choose an End Customer to crawl
              </div>
              {ecId && (
                <button onClick={() => setEcPickerOpen(false)}
                        className="text-xs text-gray-500 hover:text-gray-700">
                  Cancel
                </button>
              )}
            </div>
            <SingleEcSelector
              controllerId={activeControllerId}
              onEcSelect={(id, ec) => {
                setEcId(id);
                setEcName(ec?.name || null);
                if (id) setEcPickerOpen(false);
              }}
              selectedEcId={ecId}
            />
          </div>
        )
      )}

      {!ecChosen ? (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
          Select an MSP-EC above to begin. An MSP account owns no switches itself —
          the crawl has to be pointed at one of its end customers.
        </div>
      ) : (
        <>
          <VenuePicker
            venues={venues}
            loading={venuesLoading}
            selected={selectedVenues}
            setSelected={setSelectedVenues}
            filter={venueFilter}
            setFilter={setVenueFilter}
            lastSnapshot={lastSnapshotScope}
          />

          {!scopeReady ? (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
              Pick at least one venue. Everything below — crawls, config baselines and
              checks — runs against the venues selected above, so nothing is read from
              sites you did not ask for.
            </div>
          ) : (
            <>
          {/* ── control bar ───────────────────────────────── */}
          {/* Capture on the left, read-back settings on the right. The rate window
              used to sit immediately beside the Crawl button, where it read as a
              crawl interval — it is not one: it never triggers anything, it only
              chooses which two snapshots you ALREADY took get differenced. */}
          <div className="bg-white border border-gray-200 rounded-lg p-4 flex flex-wrap items-center gap-x-6 gap-y-3">
            <div className="flex items-center gap-3">
              <button
                onClick={runCrawl}
                disabled={crawling}
                className="px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {crawling ? "Crawling…" : `Crawl ${venueScopeLabel}`}
              </button>
              <span className="text-xs text-gray-500 max-w-xs">
                One snapshot per click. Nothing here runs on a timer — take the next
                one whenever you want it.
              </span>
            </div>

            <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 justify-end">
              <div className="text-xs text-gray-500">
                {snapshots.length} snapshot{snapshots.length === 1 ? "" : "s"} stored
              </div>
              <div className="h-8 w-px bg-gray-200 hidden sm:block" />
              <label className="flex items-center gap-2 text-sm text-gray-700"
                     title="Applies to the Findings and Health tabs. It selects which stored snapshots are compared; it never starts a crawl.">
                <span className="text-gray-500">Compare snapshots taken at least</span>
                <select value={minWindow} onChange={(e) => setMinWindow(Number(e.target.value))}
                        className="border rounded px-2 py-1 text-sm">
                  <option value={300}>5 min</option>
                  <option value={900}>15 min</option>
                  <option value={1800}>30 min</option>
                  <option value={3600}>1 hour</option>
                </select>
                <span className="text-gray-500">apart</span>
              </label>
            </div>
            <p className="w-full text-xs text-gray-500 -mt-1">
              R1 refreshes port counters about every 5 minutes, so a pair closer together
              than that reports fake spikes — 15 min is the safe default.
            </p>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-800">{error}</div>
          )}
          {notice && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">{notice}</div>
          )}

          {/* ── tabs ──────────────────────────────────────── */}
          <div className="flex gap-1 border-b border-gray-200">
            {([
              ["health", `Health checks${health && !health.error
                ? ` (${health.counts.critical + health.counts.warning})` : ""}`],
              ["findings", "Signals"],
              ["mac", `MAC tables${macTables ? ` (${fmtNum(macTables.totals.learnedTotal, 0)})` : ""}`],
              ["inventory", `Inventory${switches.length ? ` (${switches.length})` : ""}`],
              ["config", "Config"],
              ["checks", `Checks${catalogue ? ` (${catalogue.total})` : ""}`],
              ["snapshots", `Snapshots${snapshots.length ? ` (${snapshots.length})` : ""}`],
            ] as [Tab, string][]).map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
                  tab === key ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
                {label}
              </button>
            ))}
          </div>

          {tab === "health" && (
            <HealthChecks
              health={health}
              running={healthRunning}
              auditConfigs={auditConfigs}
              setAuditConfigs={setAuditConfigs}
              onRun={runHealth}
              openFinding={openFinding}
              setOpenFinding={setOpenFinding}
              baselines={baselines}
              baselineTtlDays={baselineTtlDays}
              baselining={baselining}
              onBaseline={runBaseline}
              scopeLabel={venueScopeLabel}
              exportBase={base}
              exportQs={qs()}
              exportLabel={ecName || activeController?.name || ""}
            />
          )}

          {tab === "findings" && <Findings analysis={analysis} minWindow={minWindow} />}

          {tab === "mac" && (
            <MacTables data={macTables} filter={macFilter} setFilter={setMacFilter} />
          )}

          {tab === "inventory" && (
            <Inventory rows={filteredSwitches} filter={switchFilter} setFilter={setSwitchFilter}
                       onOpenConfig={openConfig} total={switches.length}
                       scope={switchesScope} />
          )}

          {tab === "config" && (
            <ConfigView sw={selectedSwitch} config={config} loading={configLoading} />
          )}

          {tab === "checks" && (
            <Catalogue data={catalogue} filter={catFilter} setFilter={setCatFilter} />
          )}

          {tab === "snapshots" && <Snapshots rows={snapshots} ttlDays={snapshotTtlDays} />}
            </>
          )}
        </>
      )}
    </div>
  );
}

/* ── Scope banner ─────────────────────────────────────────── */

/**
 * Surfaces what the backend actually analysed. A snapshot silently dropped for
 * insufficient venue coverage would otherwise look like missing data.
 */
function expiresIn(epoch?: number | null): string | null {
  if (!epoch) return null;
  const hours = (epoch - Date.now() / 1000) / 3600;
  if (hours <= 0) return "expired";
  if (hours < 24) return `expires in ${Math.max(1, Math.round(hours))}h`;
  return `expires in ${Math.round(hours / 24)}d`;
}

function ScopeNote({ scope }: { scope: any }) {
  if (!scope) return null;
  const excluded = scope.excluded || [];
  if (!scope.scoped && !excluded.length) return null;
  const bad = scope.insufficientCoverage;
  return (
    <div className={`border rounded-lg p-3 text-xs ${
      bad ? "bg-orange-50 border-orange-200 text-orange-900"
          : "bg-gray-50 border-gray-200 text-gray-600"}`}>
      <span className="font-medium">
        Scoped to {scope.venueCount} venue{scope.venueCount === 1 ? "" : "s"}.
      </span>{" "}
      {scope.reason}
      {excluded.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {excluded.slice(0, 4).map((e: any, i: number) => (
            <li key={i}>
              · snapshot {fmtTime(e.takenAt)} covered {e.covered} of {e.requested}{" "}
              selected venue(s) — not used
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ── Venue picker ─────────────────────────────────────────── */

function VenuePicker({
  venues, loading, selected, setSelected, filter, setFilter, lastSnapshot,
}: any) {
  const q = filter.trim().toLowerCase();
  const shown: VenueRow[] = q
    ? venues.filter((v: VenueRow) => v.venueName.toLowerCase().includes(q))
    : venues;
  const shownIds = shown.map((v: VenueRow) => v.venueId);
  const allShownSelected = shownIds.length > 0 &&
    shownIds.every((id: string) => selected.includes(id));
  const selectedSwitches = venues
    .filter((v: VenueRow) => selected.includes(v.venueId))
    .reduce((n: number, v: VenueRow) => n + v.switches, 0);

  const toggle = (id: string) =>
    setSelected(selected.includes(id)
      ? selected.filter((x: string) => x !== id)
      : [...selected, id]);

  // Acts on the FILTERED set, which is the point of the filter: type "RIDGE",
  // hit Select shown, get every Ridgecrest venue.
  const toggleShown = () =>
    setSelected(allShownSelected
      ? selected.filter((id: string) => !shownIds.includes(id))
      : Array.from(new Set([...selected, ...shownIds])));

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
          Venues
        </span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter venues by name…"
          className="border rounded px-3 py-1.5 text-sm w-full sm:w-72"
        />
        <button onClick={toggleShown} disabled={!shown.length}
          className="px-3 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40">
          {allShownSelected ? "Clear shown" : `Select shown (${shown.length})`}
        </button>
        {selected.length > 0 && (
          <button onClick={() => setSelected([])}
            className="px-3 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50">
            Clear all
          </button>
        )}
        <span className="ml-auto text-xs text-gray-500">
          {loading ? "loading venues…"
            : `${selected.length} of ${venues.length} selected · ${selectedSwitches} switches`}
        </span>
      </div>

      {!loading && !venues.length && (
        <p className="text-sm text-gray-500">No venues with switches in this tenant.</p>
      )}

      {venues.length > 0 && (
        <div className="max-h-56 overflow-y-auto border rounded divide-y">
          {shown.map((v: VenueRow) => {
            const on = selected.includes(v.venueId);
            return (
              <label key={v.venueId}
                className={`flex items-center gap-3 px-3 py-1.5 text-sm cursor-pointer ${
                  on ? "bg-blue-50" : "hover:bg-gray-50"}`}>
                <input type="checkbox" checked={on} onChange={() => toggle(v.venueId)} />
                <span className="flex-1">{v.venueName}</span>
                <span className="text-xs text-gray-500">
                  {v.switches} switch{v.switches === 1 ? "" : "es"}
                  {v.offline > 0 && (
                    <span className="text-red-600"> · {v.offline} offline</span>
                  )}
                </span>
              </label>
            );
          })}
          {!shown.length && (
            <p className="px-3 py-2 text-sm text-gray-500">No venue matches “{filter}”.</p>
          )}
        </div>
      )}

      {lastSnapshot?.takenAt && (
        <p className="text-xs text-gray-400 mt-2">
          Last crawl {fmtTime(lastSnapshot.takenAt)} covered{" "}
          {lastSnapshot.scopeVenueIds
            ? `${lastSnapshot.scopeVenueIds.length} selected venue(s)`
            : `${(lastSnapshot.venueIds || []).length} venue(s)`}
          . Selection restored from it.
        </p>
      )}
    </div>
  );
}

/* ── Health checks ────────────────────────────────────────── */


function HealthChecks({
  health, running, auditConfigs, setAuditConfigs, onRun, openFinding, setOpenFinding,
  baselines, baselining, onBaseline, scopeLabel, exportBase, exportQs, exportLabel,
  baselineTtlDays,
}: any) {
  const newest = baselines?.[baselines.length - 1];
  const ageHours = newest
    ? (Date.now() / 1000 - newest.takenAtEpoch) / 3600
    : null;

  return (
    <div className="space-y-4">
      {/* Config baseline — the one deliberate bulk read */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex flex-wrap items-center gap-4">
          <button onClick={onBaseline} disabled={baselining}
            className="px-4 py-2 rounded bg-slate-700 text-white text-sm font-medium hover:bg-slate-800 disabled:opacity-50">
            {baselining ? "Reading configs…"
              : newest ? `Re-baseline ${scopeLabel}` : `Capture config baseline (${scopeLabel})`}
          </button>
          <div className="text-sm text-gray-700 max-w-2xl">
            {newest ? (
              <>
                <span className="font-medium">
                  Baseline: {newest.switches} switch configs
                </span>{" "}
                <span className={ageHours !== null && ageHours > 24 ? "text-amber-700" : "text-gray-500"}>
                  captured {fmtTime(newest.takenAt)}
                  {ageHours !== null && ` (${ageHours < 1 ? "under an hour" : `${Math.round(ageHours)}h`} old)`}
                  {expiresIn(newest.expiresAtEpoch) && ` · ${expiresIn(newest.expiresAtEpoch)}`}
                </span>
                <span className="block text-xs text-gray-500 mt-0.5">
                  Config checks run against this at no API cost. Keeping it is what makes
                  drift detectable — re-read live below to see what changed since.
                  {baselineTtlDays
                    ? ` Stored configs are deleted after ${baselineTtlDays} day${baselineTtlDays === 1 ? "" : "s"}.`
                    : ""}
                </span>
              </>
            ) : (
              <>
                <span className="font-medium">No config baseline yet.</span>
                <span className="block text-xs text-gray-500 mt-0.5">
                  A one-off bulk read of every online switch&apos;s running config
                  (~one request per switch, ~30s for 200), redacted and stored. It powers
                  the loop-containment, forensics and hygiene checks, and gives later runs
                  something to diff against. Triggered only by this button — never on a
                  timer, and{baselineTtlDays ? ` deleted after ${baselineTtlDays} day${baselineTtlDays === 1 ? "" : "s"}.` : " not kept indefinitely."}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex flex-wrap items-center gap-4">
          <button onClick={onRun} disabled={running}
            className="px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {running ? "Running checks…" : "Run checks"}
          </button>
          {health && !health.error && (
            <span className="flex items-center gap-1.5 text-sm">
              <span className="text-xs text-gray-500">Export:</span>
              {(["pdf", "csv", "json"] as const).map((fmt) => (
                <a key={fmt}
                   href={`${exportBase}/report.${fmt}${exportQs}`
                     + (fmt === "pdf" && exportLabel
                        ? `${exportQs ? "&" : "?"}label=${encodeURIComponent(exportLabel)}` : "")}
                   className="px-2.5 py-1 rounded border border-gray-300 text-xs font-medium text-gray-700 hover:bg-gray-50 uppercase">
                  {fmt}
                </a>
              ))}
            </span>
          )}
          <label className="flex items-start gap-2 text-sm text-gray-700 max-w-2xl">
            <input type="checkbox" className="mt-1" checked={auditConfigs}
                   onChange={(e) => setAuditConfigs(e.target.checked)} />
            <span>
              <span className="font-medium">Re-read configs live (shows drift)</span>
              <span className="block text-xs text-gray-500">
                Pulls every online switch&apos;s config again for this run and diffs it
                against the baseline, so you see exactly what changed. ~30s for 200
                switches. Leave it off and the checks use the stored baseline at no API
                cost. Either way, config checks that have no data are reported as
                <em> skipped</em>, never silently omitted.
              </span>
            </span>
          </label>
        </div>
      </div>

      {health && !health.error && (
        <p className="text-xs text-gray-500 -mt-2">
          The PDF carries the findings, the full check catalogue (what was tested and the
          exact condition each check fires on), the skipped checks, and the caveats about
          the underlying data. CSV is findings only, for a tracker or a spreadsheet. JSON
          is the complete stored result with full evidence. All three read the stored run —
          nothing is recomputed and no API call is made.
        </p>
      )}

      {!health ? (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
          No check run stored yet. Hit <strong>Run checks</strong> for a ranked list of
          findings — the things a senior ICX engineer would look for. Metric, topology and
          stability checks work off the snapshots you have already taken; the
          loop-containment, forensics and hygiene checks use the config baseline above.
          <span className="block mt-2 text-xs">
            Results are stored once run, so they stay here across reloads until you run
            them again.
          </span>
        </div>
      ) : health.error ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">
          {health.error}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[["critical", health.counts.critical], ["warning", health.counts.warning],
              ["info", health.counts.info]].map(([sev, n]: any) => (
              <div key={sev} className={`border rounded-lg p-3 ${SEVERITY_STYLE[sev]}`}>
                <div className="text-xs uppercase tracking-wide">{sev}</div>
                <div className="text-2xl font-semibold">{n}</div>
              </div>
            ))}
            <div className="bg-white border border-gray-200 rounded-lg p-3">
              <div className="text-xs text-gray-500">Checks run</div>
              <div className="text-2xl font-semibold">{health.checksRun.length}</div>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-3">
              <div className="text-xs text-gray-500">Configs audited</div>
              <div className="text-2xl font-semibold">{health.context.configsAudited}</div>
            </div>
          </div>

          <ScopeNote scope={health.scope} />

          <div className="text-xs text-gray-500">
            Run {fmtTime(health.ranAt)} · {health.context.switches} switches ·{" "}
            {fmtNum(health.context.ports, 0)} ports ·{" "}
            {health.context.snapshots} snapshot(s)
            {health.configAudit?.source === "baseline" &&
              ` · configs from baseline of ${fmtTime(health.configAudit.baselineTakenAt)}`}
            {health.configAudit?.source === "live" &&
              ` · configs re-read live (${health.configAudit.switchesRead} switches)`}
            {!health.configAudit?.source && " · no config data"}
            {health.context.rateWindowSeconds
              ? ` · rates over ${health.context.rateWindowSeconds}s`
              : ` · no rate window (${health.context.rateReason || "need two snapshots"})`}
          </div>

          {health.checksSkipped.length > 0 && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <div className="text-sm font-medium text-gray-700">
                {health.checksSkipped.length} check(s) skipped — not run, not passed
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {health.checksSkipped.map((s: any) => s.title).join(" · ")}
              </div>
              <div className="text-xs text-gray-500 mt-1">
                Tick <strong>Include config audit</strong> (or take a second snapshot for
                rate-based checks) to run these.
              </div>
            </div>
          )}

          {health.checksFailed?.length > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-800">
              {health.checksFailed.length} check(s) errored:{" "}
              {health.checksFailed.map((f: any) => `${f.checkId} (${f.error})`).join("; ")}
            </div>
          )}

          {health.findings.length === 0 ? (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-900">
              No findings from the checks that ran.
            </div>
          ) : (
            <div className="space-y-2">
              {health.findings.map((f: any, i: number) => {
                const key = `${f.checkId}-${i}`;
                const open = openFinding === key;
                return (
                  <div key={key} className={`border rounded-lg ${SEVERITY_STYLE[f.severity]}`}>
                    <button
                      onClick={() => setOpenFinding(open ? null : key)}
                      className="w-full text-left p-3 flex items-start gap-3">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase shrink-0 ${SEVERITY_BADGE[f.severity]}`}>
                        {f.severity}
                      </span>
                      <span className="flex-1">
                        <span className="font-medium">{f.title}</span>
                        <span className="block text-xs opacity-70 mt-0.5">
                          {f.category} · {f.entity}
                          {f.confidence === "medium" && " · unconfirmed — verify"}
                        </span>
                      </span>
                      <span className="text-xs opacity-60 shrink-0">{open ? "−" : "+"}</span>
                    </button>
                    {open && (
                      <div className="px-3 pb-3 space-y-2 text-sm">
                        <p>{f.detail}</p>
                        {f.remediation && (
                          <p className="text-xs">
                            <span className="font-semibold">What to do: </span>{f.remediation}
                          </p>
                        )}
                        <details className="text-xs">
                          <summary className="cursor-pointer opacity-70">Evidence</summary>
                          <pre className="mt-1 bg-white/60 rounded p-2 overflow-auto max-h-60 whitespace-pre-wrap">
                            {JSON.stringify(f.evidence, null, 2)}
                          </pre>
                        </details>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Findings ─────────────────────────────────────────────── */

function Findings({ analysis, minWindow }: { analysis: any; minWindow: number }) {
  if (!analysis) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
        No snapshots yet. Hit <strong>Crawl now</strong> to take the first one, then
        take a second at least {Math.round(minWindow / 60)} minutes later — rates are
        differences between two snapshots, so the first crawl alone can only show
        topology and cumulative counters.
      </div>
    );
  }
  const { latest, rates, macs, density, topology, errors, rejected } = analysis;

  return (
    <div className="space-y-4">
      <ScopeNote scope={analysis.scope} />
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          ["Switches", latest.switches],
          ["Ports", latest.ports],
          ["Up ports", latest.upPorts],
          ["Learned MACs", latest.macs],
          ["Venues", Object.keys(latest.venues || {}).length],
        ].map(([label, val]) => (
          <div key={label as string} className="bg-white border border-gray-200 rounded-lg p-3">
            <div className="text-xs text-gray-500">{label}</div>
            <div className="text-2xl font-semibold">{fmtNum(val as number, 0)}</div>
          </div>
        ))}
      </div>
      <div className="text-xs text-gray-500">Latest snapshot {fmtTime(latest.takenAt)}</div>

      {rejected?.length > 0 && (
        <div className="bg-orange-50 border border-orange-200 rounded-lg p-3 text-sm text-orange-900">
          {rejected.length} snapshot(s) excluded because the crawl came up short — a
          partial crawl would read as ports vanishing from the network.
        </div>
      )}

      {/* Signal 1 */}
      <Card title="Signal 1 — Broadcast rate"
            hint="Counters are cumulative since reboot, so only the change between two snapshots means anything. Ranked on the weaker of in/out: a loop pushes broadcast both ways on the same port, a chatty host only one way.">
        {!rates?.available ? (
          <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded p-3">
            No rates yet. {rates?.reason}{" "}
            {rates?.gapSeconds ? `Closest pair spans ${rates.gapSeconds}s.` : ""}
          </div>
        ) : (
          <>
            <div className="text-xs text-gray-500 mb-2">
              {rates.windowSeconds}s window ({fmtTime(rates.from)} → {fmtTime(rates.to)}),
              {" "}{fmtNum(rates.portsCompared, 0)} up ports compared
              {rates.counterResets > 0 && `, ${rates.counterResets} dropped for counter reset`}
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="text-xs text-gray-500 border-b">
                  <tr>
                    <th className="text-left py-1 pr-3">Port</th>
                    <th className="text-left py-1 pr-3">VLAN</th>
                    <th className="text-right py-1 pr-3">bcast in/s</th>
                    <th className="text-right py-1 pr-3">bcast out/s</th>
                    <th className="text-right py-1 pr-3">×in</th>
                    <th className="text-right py-1 pr-3">×out</th>
                    <th className="text-right py-1 pr-3">discard/s</th>
                    <th className="text-left py-1">LLDP neighbour</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.top.map((r: any) => (
                    <tr key={r.label} className="border-b last:border-0">
                      <td className="py-1 pr-3 font-mono text-xs">{r.label}</td>
                      <td className="py-1 pr-3">{r.vlan || <span className="text-gray-400">trunk</span>}</td>
                      <td className="py-1 pr-3 text-right">{fmtNum(r.broadcastIn)}</td>
                      <td className="py-1 pr-3 text-right">{fmtNum(r.broadcastOut)}</td>
                      <td className="py-1 pr-3 text-right">{r.xIn ?? <span className="text-gray-300">—</span>}</td>
                      <td className="py-1 pr-3 text-right">{r.xOut ?? <span className="text-gray-300">—</span>}</td>
                      <td className="py-1 pr-3 text-right">{fmtNum(r.inDiscard)}</td>
                      <td className="py-1 text-xs text-gray-600">{r.lldp || <span className="text-gray-400">none</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-gray-400 mt-2">
              “—” means every other up port in that VLAN is at zero, so there is no
              baseline to multiply against. Judge on the raw rate.
            </p>
            <div className="mt-3">
              <div className="text-xs font-semibold text-gray-600 mb-1">
                Per VLAN — a loop lifts the whole broadcast domain, not one port
              </div>
              <div className="flex flex-wrap gap-2">
                {rates.vlanSummary.slice(0, 10).map((v: any) => (
                  <div key={v.vlan} className="border rounded px-2 py-1 text-xs bg-gray-50">
                    <span className="font-medium">VLAN {v.vlan || "trunk"}</span>{" "}
                    <span className="text-gray-500">{v.ports}p · med {fmtNum(v.median)}/s · max {fmtNum(v.max)}/s</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </Card>

      {/* Signal 2 */}
      <Card title="Signal 2 — MAC moves and duplicate learning"
            hint="A MAC on two ports at once, or ping-ponging between them, is the classic loop fingerprint. Uplinks are excluded via LLDP — a MAC on an access port and on the trunk carrying it is ordinary forwarding.">
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <div className="text-sm font-medium mb-1">
              On more than one non-uplink port: {macs.duplicates.length}
            </div>
            {macs.duplicates.length === 0 ? (
              <p className="text-sm text-gray-500">
                None. {macs.suppressedUplinkDuplicates > 0 &&
                  `${macs.suppressedUplinkDuplicates} duplicate(s) suppressed as LLDP-confirmed uplinks.`}
              </p>
            ) : (
              <ul className="text-xs font-mono space-y-1">
                {macs.duplicates.slice(0, 10).map((d: any) => (
                  <li key={d.mac}>
                    <span className="font-semibold">{d.mac}</span> ×{d.snapshots}{" "}
                    <span className="text-gray-500">{d.places.map((p: any[]) => `${p[0]} ${p[1]}`).join(" ↔ ")}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <div className="text-sm font-medium mb-1">Changed port between snapshots: {macs.moves.length}</div>
            {macs.moves.length === 0 ? (
              <p className="text-sm text-gray-500">None.</p>
            ) : (
              <ul className="text-xs font-mono space-y-1">
                {macs.moves.slice(0, 10).map((m: any) => (
                  <li key={m.mac}>
                    <span className="font-semibold">{m.mac}</span> — {m.count} move(s)
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>

      {/* Signal 3 */}
      <Card title="Signal 3 — MAC density on ports LLDP cannot see"
            hint="Density behind a visible managed switch is an uplink and expected. Density behind nothing is an unmanaged device — a dumb switch or a patch loop. That is the port to physically inspect.">
        <div className="text-xs text-gray-500 mb-2">
          {density.blindCount} of {density.totalPorts} ports with learned MACs have no LLDP neighbour
        </div>
        <table className="min-w-full text-sm">
          <thead className="text-xs text-gray-500 border-b">
            <tr><th className="text-left py-1 pr-3">Port</th><th className="text-right py-1 pr-3">MACs</th>
                <th className="text-left py-1 pr-3">VLAN</th><th className="text-left py-1">Far end</th></tr>
          </thead>
          <tbody>
            {density.top.slice(0, 12).map((d: any) => (
              <tr key={d.label} className={`border-b last:border-0 ${!d.lldp ? "bg-amber-50" : ""}`}>
                <td className="py-1 pr-3 font-mono text-xs">{d.label}</td>
                <td className="py-1 pr-3 text-right font-medium">{d.macs}</td>
                <td className="py-1 pr-3">{d.vlan || "—"}</td>
                <td className="py-1 text-xs">
                  {d.lldp || <span className="text-amber-700 font-semibold">nothing visible</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Signal 4 */}
      <Card title="Signal 4 — LLDP topology"
            hint="Cycles between managed switches that are not a LAG and not stacking. Core and distribution switches showing up here are usually intended redundancy that RSTP is blocking — listed so they get dismissed deliberately.">
        <div className="text-sm mb-2">
          {topology.linkCount} switch-to-switch links among {topology.switchCount} switches ·{" "}
          <span className={topology.cycles.length ? "text-amber-700 font-medium" : ""}>
            {topology.cycles.length} cycle-closing back edge(s)
          </span>{" "}
          · {topology.redundantPairs.length} pair(s) with multiple non-LAG links
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <ul className="text-xs font-mono space-y-1 max-h-56 overflow-y-auto">
            {topology.cycles.map((c: any, i: number) => (
              <li key={i}>{c.a} ↔ {c.b}</li>
            ))}
          </ul>
          <ul className="text-xs font-mono space-y-1 max-h-56 overflow-y-auto">
            {topology.redundantPairs.slice(0, 20).map((p: any, i: number) => (
              <li key={i}>{p.a} ↔ {p.b} <span className="text-gray-500">[{p.ports.join(", ")}]</span></li>
            ))}
          </ul>
        </div>
      </Card>

      <Card title="Corroborating errors"
            hint="Cumulative since reboot. CRC and input errors point at a failing cable or optic rather than a loop — but a port with both an error count and a broadcast spike belongs at the top of the list.">
        <div className="grid md:grid-cols-3 gap-4">
          {Object.entries(errors).map(([field, e]: [string, any]) => (
            <div key={field}>
              <div className="text-sm font-medium">{field}</div>
              <div className="text-xs text-gray-500 mb-1">
                nonzero on {e.nonzeroPorts} / {e.upPorts} up ports
              </div>
              <ul className="text-xs font-mono space-y-0.5">
                {e.top.slice(0, 5).map((t: any) => (
                  <li key={t.label}>
                    <span className="text-gray-800">{fmtNum(t.value, 0)}</span>{" "}
                    <span className="text-gray-500">{t.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ── MAC tables ───────────────────────────────────────────── */

function MacTables({ data, filter, setFilter }: any) {
  if (!data) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
        No snapshots yet. Hit <strong>Crawl now</strong> — MAC tables come from the crawl,
        with no extra API calls.
      </div>
    );
  }
  const q = filter.trim().toLowerCase();
  const rows = q
    ? data.switches.filter((s: any) =>
        [s.name, s.model, s.venueName].some((v: string) => (v || "").toLowerCase().includes(q)))
    : data.switches;

  return (
    <div className="space-y-4">
      <ScopeNote scope={data.scope} />
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          ["Total learned", fmtNum(data.totals.learnedTotal, 0)],
          ["Median / switch", fmtNum(data.totals.median, 0)],
          ["Largest table", fmtNum(data.totals.max, 0)],
          ["Switches", data.totals.online],
          ["Counts disagree", data.totals.countsDisagreeOn],
        ].map(([label, val]: any) => (
          <div key={label} className="bg-white border border-gray-200 rounded-lg p-3">
            <div className="text-xs text-gray-500">{label}</div>
            <div className="text-2xl font-semibold">{val}</div>
          </div>
        ))}
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-900">
        <strong>No utilisation percentage, deliberately.</strong> {data.capacityNote}
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex flex-wrap items-baseline gap-3 mb-3">
          <input value={filter} onChange={(e) => setFilter(e.target.value)}
                 placeholder="Filter by switch, model, venue…"
                 className="w-full md:w-80 border rounded px-3 py-1.5 text-sm" />
          <span className="text-xs text-gray-500">
            {rows.length} of {data.switches.length} · snapshot {fmtTime(data.takenAt)}
            {data.previousTakenAt && ` · growth vs ${fmtTime(data.previousTakenAt)}`}
          </span>
        </div>
        <div className="overflow-x-auto max-h-[34rem]">
          <table className="min-w-full text-sm">
            <thead className="text-xs text-gray-500 border-b sticky top-0 bg-white">
              <tr>
                <th className="text-left py-1 pr-3">Switch</th>
                <th className="text-left py-1 pr-3">Model</th>
                <th className="text-right py-1 pr-3">MACs</th>
                <th className="text-right py-1 pr-3">R1 count</th>
                <th className="text-right py-1 pr-3">Growth</th>
                <th className="text-right py-1 pr-3">× model median</th>
                <th className="text-right py-1 pr-3">per up-port</th>
                <th className="text-left py-1">Densest port</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s: any) => {
                const hot = s.vsModelMedian && s.vsModelMedian >= 5;
                const grew = s.growth !== null && s.growth >= 40;
                return (
                  <tr key={s.id} className={`border-b last:border-0 ${hot ? "bg-amber-50" : ""}`}>
                    <td className="py-1 pr-3">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-xs text-gray-400">{s.venueName}</div>
                    </td>
                    <td className="py-1 pr-3 text-xs">{s.model}</td>
                    <td className="py-1 pr-3 text-right font-medium">{fmtNum(s.learned, 0)}</td>
                    <td className={`py-1 pr-3 text-right ${s.countsAgree ? "text-gray-500" : "text-amber-700 font-medium"}`}>
                      {s.clientCount ?? "—"}
                    </td>
                    <td className={`py-1 pr-3 text-right ${grew ? "text-red-700 font-semibold" : "text-gray-500"}`}>
                      {s.growth === null ? "—" : s.growth > 0 ? `+${s.growth}` : s.growth}
                    </td>
                    <td className={`py-1 pr-3 text-right ${hot ? "text-amber-800 font-semibold" : "text-gray-500"}`}>
                      {s.vsModelMedian ? `${s.vsModelMedian}×` : "—"}
                    </td>
                    <td className="py-1 pr-3 text-right text-gray-600">{s.macsPerUpPort ?? "—"}</td>
                    <td className="py-1 text-xs">
                      {s.densestPort ? (
                        <>
                          <span className="font-mono">{s.densestPort.port}</span>{" "}
                          <span className="text-gray-500">({s.densestPort.macs})</span>{" "}
                          <span className="text-gray-400">
                            {s.densestPort.lldp || "no LLDP"}
                          </span>
                        </>
                      ) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 mt-2">
          “R1 count” is the <code>clientCount</code> on the switch record; “MACs” is the
          number of rows the client query returned. Where they differ (highlighted), the
          MAC view is partial — treat the count as a floor.
        </p>
      </div>
    </div>
  );
}

/* ── Inventory ────────────────────────────────────────────── */

function Inventory({ rows, filter, setFilter, onOpenConfig, total, scope }: any) {
  if (!total) {
    return (
      <div className="space-y-4">
        <ScopeNote scope={scope} />
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
          {scope?.insufficientCoverage
            ? <>No crawl covers the selected venue(s). Hit <strong>Crawl now</strong> at this scope.</>
            : <>Nothing crawled yet. Hit <strong>Crawl now</strong>.</>}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
    <ScopeNote scope={scope} />
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by name, serial, model, IP, venue…"
        className="w-full md:w-96 border rounded px-3 py-1.5 text-sm mb-3"
      />
      <div className="text-xs text-gray-500 mb-2">{rows.length} of {total} switches</div>
      <div className="overflow-x-auto max-h-[32rem]">
        <table className="min-w-full text-sm">
          <thead className="text-xs text-gray-500 border-b sticky top-0 bg-white">
            <tr>
              <th className="text-left py-1 pr-3">Switch</th>
              <th className="text-left py-1 pr-3">Venue</th>
              <th className="text-left py-1 pr-3">Model</th>
              <th className="text-left py-1 pr-3">Status</th>
              <th className="text-left py-1 pr-3">IP</th>
              <th className="text-right py-1 pr-3">Ports up</th>
              <th className="text-right py-1 pr-3">MACs</th>
              <th className="text-left py-1 pr-3">Uptime</th>
              <th className="text-left py-1">Config</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s: SwitchRow) => (
              <tr key={s.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-1 pr-3">
                  <div className="font-medium">{s.name}</div>
                  <div className="text-xs text-gray-400 font-mono">{s.serialNumber}</div>
                </td>
                <td className="py-1 pr-3 text-xs">{s.venueName}</td>
                <td className="py-1 pr-3 text-xs">{s.model}</td>
                <td className="py-1 pr-3"><StatusPill status={s.deviceStatus} /></td>
                <td className="py-1 pr-3 text-xs font-mono">{s.ipAddress || "—"}</td>
                <td className="py-1 pr-3 text-right">{s.upPorts}/{s.crawledPorts}</td>
                <td className="py-1 pr-3 text-right">{s.learnedMacs}</td>
                <td className="py-1 pr-3 text-xs">{s.uptime || "—"}</td>
                <td className="py-1">
                  <button onClick={() => onOpenConfig(s)}
                          className="text-xs text-blue-600 hover:underline">
                    view config
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
    </div>
  );
}

/* ── Config ───────────────────────────────────────────────── */

function ConfigView({ sw, config, loading }: any) {
  if (!sw) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
        Pick a switch on the Inventory tab to see its redacted running config.
      </div>
    );
  }
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <h3 className="font-semibold">{sw.name}</h3>
          <div className="text-xs text-gray-500">
            {sw.model} · {sw.serialNumber} · {sw.ipAddress}
          </div>
        </div>
        {config && (
          <div className="text-xs text-gray-500 text-right">
            backup {fmtTime(config.createdDate)} · {config.backupType} · fetched for this switch only
          </div>
        )}
      </div>

      <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-900 mb-3">
        Read on demand for this switch alone — configs are never bulk-pulled or
        stored. Credentials are removed before this ever leaves the backend:
        passwords, SNMP communities, RADIUS/TACACS keys, routing-protocol auth
        and certificate blocks are masked, and the result is re-scanned before
        being returned — anything that still looks live is refused, not shown.
        {config?.redactionStats && (
          <> Masked {config.redactionStats.rule} line(s) by rule
            {config.redactionStats.catchall > 0 && `, ${config.redactionStats.catchall} by the catch-all`}
            {config.redactionStats.block_lines > 0 && `, ${config.redactionStats.block_lines} encoded-block line(s) dropped`}.
          </>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : config ? (
        <pre className="text-xs font-mono bg-gray-900 text-gray-100 rounded p-3 overflow-auto max-h-[32rem] whitespace-pre">
          {config.config}
        </pre>
      ) : (
        <p className="text-sm text-gray-500">No config available for this switch.</p>
      )}
    </div>
  );
}

/* ── Check catalogue ──────────────────────────────────────── */

const STATUS_STYLE: Record<string, string> = {
  ran: "bg-green-100 text-green-800",
  skipped: "bg-gray-200 text-gray-600",
  errored: "bg-red-100 text-red-800",
  "not run": "bg-gray-100 text-gray-500",
};

function Catalogue({ data, filter, setFilter }: any) {
  if (!data) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
        Loading the check catalogue…
      </div>
    );
  }
  const q = filter.trim().toLowerCase();
  const rows = q
    ? data.checks.filter((c: any) =>
        [c.id, c.title, c.category, c.summary, c.trigger]
          .some((v: string) => (v || "").toLowerCase().includes(q)))
    : data.checks;

  const byCat: Record<string, any[]> = {};
  rows.forEach((c: any) => { (byCat[c.category] ||= []).push(c); });

  return (
    <div className="space-y-4">
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex flex-wrap items-center gap-3">
          <input value={filter} onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter checks by name, category or condition…"
            className="border rounded px-3 py-1.5 text-sm w-full sm:w-96" />
          <span className="text-xs text-gray-500">
            {rows.length} of {data.total} checks
            {data.lastRunAt && ` · status from the run at ${fmtTime(data.lastRunAt)}`}
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-2">
          <strong>Fires when</strong> is the actual condition and threshold, so you can tell
          whether your situation would have been reported. A <em>skipped</em> check has not
          passed — it had no data to look at.
        </p>
      </div>

      {Object.entries(byCat).map(([cat, checks]) => (
        <div key={cat} className="bg-white border border-gray-200 rounded-lg p-4">
          <h3 className="font-semibold text-gray-800 mb-2">
            {cat} <span className="text-xs font-normal text-gray-400">({checks.length})</span>
          </h3>
          <div className="divide-y">
            {checks.map((c: any) => (
              <div key={c.id} className="py-2">
                <div className="flex items-start gap-2 flex-wrap">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${STATUS_STYLE[c.status]}`}>
                    {c.status}
                  </span>
                  <span className="font-medium text-sm">{c.title}</span>
                  <code className="text-[11px] text-gray-400">{c.id}</code>
                  <span className="text-[11px] text-gray-400">needs {c.needs}</span>
                  {c.findings > 0 && (
                    <span className="text-[11px] font-semibold text-amber-700">
                      {c.findings} finding{c.findings === 1 ? "" : "s"}
                    </span>
                  )}
                  {c.status === "ran" && c.findings === 0 && (
                    <span className="text-[11px] text-green-700">clear</span>
                  )}
                </div>
                {c.summary && <p className="text-xs text-gray-600 mt-1">{c.summary}</p>}
                {c.trigger && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    <span className="font-semibold">Fires when:</span> {c.trigger}
                  </p>
                )}
                {c.note && (
                  <p className="text-xs text-amber-700 mt-0.5">
                    <span className="font-semibold">Not run:</span> {c.note}
                  </p>
                )}
                {c.description && c.description !== c.summary && (
                  <details className="mt-1">
                    <summary className="text-xs text-blue-600 cursor-pointer">
                      Why it matters
                    </summary>
                    <p className="text-xs text-gray-600 mt-1 whitespace-pre-wrap">
                      {c.description}
                    </p>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Snapshots ────────────────────────────────────────────── */

function Snapshots({ rows, ttlDays }: { rows: SnapshotMeta[]; ttlDays: number | null }) {
  const retention = ttlDays ? (
    <div className="text-xs text-gray-500">
      Snapshots hold learned MACs, client IPs and LLDP topology — never device
      configuration. They are deleted {ttlDays} day{ttlDays === 1 ? "" : "s"} after
      capture, whether or not you crawl again.
    </div>
  ) : null;

  if (!rows.length) {
    return (
      <div className="space-y-2">
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
          No snapshots stored yet.
        </div>
        {retention}
      </div>
    );
  }
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
      <table className="min-w-full text-sm">
        <thead className="text-xs text-gray-500 border-b">
          <tr>
            <th className="text-left py-1 pr-3">Taken</th>
            <th className="text-right py-1 pr-3">Switches</th>
            <th className="text-right py-1 pr-3">Ports</th>
            <th className="text-right py-1 pr-3">MACs</th>
            <th className="text-right py-1 pr-3">Size</th>
            <th className="text-left py-1 pr-3">Crawl</th>
            <th className="text-left py-1">Retention</th>
          </tr>
        </thead>
        <tbody>
          {[...rows].reverse().map((s) => (
            <tr key={s.file} className={`border-b last:border-0 ${!s.complete ? "bg-orange-50" : ""}`}>
              <td className="py-1 pr-3">{fmtTime(s.takenAt)}</td>
              <td className="py-1 pr-3 text-right">{s.switches}</td>
              <td className="py-1 pr-3 text-right">{fmtNum(s.ports, 0)}</td>
              <td className="py-1 pr-3 text-right">{fmtNum(s.macs, 0)}</td>
              <td className="py-1 pr-3 text-right text-xs">{(s.sizeBytes / 1048576).toFixed(1)} MB</td>
              <td className="py-1 pr-3 text-xs">
                {s.complete
                  ? <span className="text-green-700">complete</span>
                  : <span className="text-orange-700 font-medium">
                      incomplete ({fmtNum(s.collected, 0)}/{fmtNum(s.expected, 0)}) — excluded from rates
                    </span>}
              </td>
              <td className="py-1 text-xs text-gray-500">{expiresIn(s.expiresAtEpoch) || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {retention}
    </div>
  );
}
