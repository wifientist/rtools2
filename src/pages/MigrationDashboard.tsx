import { useState, useEffect, useMemo, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";
import {
  BarChart3, RefreshCw, Pencil, Check, ChevronUp, ChevronDown,
  AlertCircle, Wifi, WifiOff, MapPin, Building2, Target, ShieldX, Settings, X, EyeOff, Users,
  TrendingUp, TrendingDown, Minus, Plus, Trash2, Calendar,
} from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface StatusSummary {
  operational: number;
  offline: number;
}

interface ECTenantStats {
  id: string;
  name: string;
  ap_count: number;
  venue_count: number;
  client_count: number;
  status_summary: StatusSummary;
  status_counts: Record<string, number>;
  error: string | null;
  ignored: boolean;
}

interface DashboardData {
  total_aps: number;
  total_venues: number;
  total_clients: number;
  total_ecs: number;
  errors: number;
  status_summary: StatusSummary;
  status_counts: Record<string, number>;
  tenants: ECTenantStats[];
}

interface DashboardSettings {
  target_aps: number;
  ignored_tenant_ids: string[];
}

type SortField = "name" | "ap_count" | "venue_count";

const STATUS_LABELS: Record<string, string> = {
  "1_01_NeverContactedCloud": "Never Contacted Cloud",
  "1_07_Initializing": "Initializing",
  "1_09_Offline": "Offline (Setup)",
  "2_00_Operational": "Operational",
  "2_01_ApplyingFirmware": "Applying Firmware",
  "2_02_ApplyingConfiguration": "Applying Configuration",
  "3_02_FirmwareUpdateFailed": "Firmware Update Failed",
  "3_03_ConfigurationUpdateFailed": "Config Update Failed",
  "3_04_DisconnectedFromCloud": "Disconnected from Cloud",
  "4_01_Rebooting": "Rebooting",
  "4_04_HeartbeatLost": "Heartbeat Lost",
};

const STATUS_COLORS: Record<string, string> = {
  "1_": "text-gray-500",
  "2_": "text-green-600",
  "3_": "text-red-500",
  "4_": "text-amber-500",
};

function getStatusColor(code: string): string {
  const prefix = code.substring(0, 2);
  return STATUS_COLORS[prefix] ?? "text-gray-500";
}

interface SnapshotPoint {
  id: number;
  captured_at: string;
  total_aps: number;
  operational_aps: number;
  total_venues: number;
  total_clients: number;
  total_ecs: number;
}

interface BackfillRow {
  date: string;
  total_aps: string;
  operational_aps: string;
  total_venues: string;
  total_clients: string;
  total_ecs: string;
}

function Sparkline({
  data,
  color = "#6366f1",
  width = 64,
  height = 20,
}: {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map(
      (v, i) =>
        `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * (height - 2) - 1}`
    )
    .join(" ");
  return (
    <svg width={width} height={height} className="inline-block ml-2 opacity-70">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrendIndicator({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const first = data[0];
  const last = data[data.length - 1];
  if (first === 0 && last === 0) return null;
  const change = first > 0 ? ((last - first) / first) * 100 : last > 0 ? 100 : 0;
  if (Math.abs(change) < 0.5) {
    return (
      <span className="inline-flex items-center text-xs text-gray-400 ml-1">
        <Minus size={12} />
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center text-xs ml-1 ${
        change > 0 ? "text-green-500" : "text-red-500"
      }`}
    >
      {change > 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
      <span className="ml-0.5">{Math.abs(change).toFixed(0)}%</span>
    </span>
  );
}

interface PeriodDelta {
  aps: number;
  operational: number;
  venues: number;
  clients: number;
}

function getPeriodDelta(snapshots: SnapshotPoint[], days: number): PeriodDelta | null {
  if (snapshots.length < 2) return null;
  const latest = snapshots[snapshots.length - 1];
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  // Find the snapshot closest to the cutoff date
  const baseline = snapshots.reduce((best, s) => {
    const d = Math.abs(new Date(s.captured_at).getTime() - cutoff.getTime());
    const bestD = Math.abs(new Date(best.captured_at).getTime() - cutoff.getTime());
    return d < bestD ? s : best;
  });
  // Only use if the baseline is old enough to represent this period
  const baselineAge = (Date.now() - new Date(baseline.captured_at).getTime()) / 86400000;
  if (baselineAge < days * 0.5) return null;
  return {
    aps: latest.total_aps - baseline.total_aps,
    operational: latest.operational_aps - baseline.operational_aps,
    venues: latest.total_venues - baseline.total_venues,
    clients: latest.total_clients - baseline.total_clients,
  };
}

function DeltaRow({ label, value }: { label: string; value: number }) {
  const color = value > 0 ? "text-green-600" : value < 0 ? "text-red-500" : "text-gray-400";
  const prefix = value > 0 ? "+" : "";
  const display = value === 0 ? "\u2014" : `${prefix}${value.toLocaleString()}`;
  return (
    <div className="flex justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono font-medium ${color}`}>{display}</span>
    </div>
  );
}

function PeriodCard({ label, delta }: { label: string; delta: PeriodDelta | null }) {
  return (
    <div className="bg-white rounded-xl shadow p-5">
      <div className="text-sm font-semibold text-gray-700 mb-3">{label}</div>
      {delta ? (
        <div className="space-y-1.5">
          <DeltaRow label="APs" value={delta.aps} />
          <DeltaRow label="Operational" value={delta.operational} />
          <DeltaRow label="Venues" value={delta.venues} />
          <DeltaRow label="Clients" value={delta.clients} />
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">Not enough data</p>
      )}
    </div>
  );
}

function getMessage(pct: number): string {
  if (pct >= 100) return "Migration complete!";
  if (pct >= 90) return "Almost there!";
  if (pct >= 75) return "The finish line is in sight!";
  if (pct >= 50) return "Past the halfway mark!";
  if (pct >= 25) return "Making great progress!";
  return "The journey begins!";
}

const MigrationDashboard = () => {
  const { controllers, featureAccess, userRole } = useAuth();

  const mspControllers = controllers.filter(
    (c) => c.controller_subtype === "MSP"
  );

  const [controllerID, setControllerID] = useState<number | null>(null);
  const [data, setData] = useState<DashboardData | null>(null);
  const [settings, setSettings] = useState<DashboardSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingTarget, setEditingTarget] = useState(false);
  const [targetInput, setTargetInput] = useState("");
  const [sortField, setSortField] = useState<SortField>("ap_count");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [animatedPct, setAnimatedPct] = useState(0);

  // Status breakdown expand
  const [showStatusBreakdown, setShowStatusBreakdown] = useState(false);

  // Snapshots
  const [snapshots, setSnapshots] = useState<SnapshotPoint[]>([]);

  // Settings panel
  const [showSettings, setShowSettings] = useState(false);
  const [settingsTarget, setSettingsTarget] = useState("");
  const [settingsIgnored, setSettingsIgnored] = useState<Set<string>>(new Set());
  const [savingSettings, setSavingSettings] = useState(false);

  // Backfill
  const emptyBackfillRow = (): BackfillRow => ({
    date: "", total_aps: "", operational_aps: "", total_venues: "", total_clients: "", total_ecs: "",
  });
  const [backfillRows, setBackfillRows] = useState<BackfillRow[]>([emptyBackfillRow()]);
  const [backfillStatus, setBackfillStatus] = useState<string | null>(null);
  const [savingBackfill, setSavingBackfill] = useState(false);
  const [deletingSnapshot, setDeletingSnapshot] = useState<number | null>(null);

  const target = settings?.target_aps ?? 180000;

  // Auto-select first MSP controller
  useEffect(() => {
    if (mspControllers.length > 0 && !controllerID) {
      setControllerID(mspControllers[0].id);
    }
  }, [controllers]);

  // Fetch progress data + snapshots in parallel
  const fetchData = useCallback((ctrlId: number) => {
    const abortController = new AbortController();

    setLoading(true);
    setError(null);
    setAnimatedPct(0);

    const progressFetch = fetch(`${API_BASE_URL}/migration-dashboard/progress/${ctrlId}`, {
      credentials: "include",
      signal: abortController.signal,
    }).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    });

    const snapshotFetch = fetch(`${API_BASE_URL}/migration-dashboard/snapshots/${ctrlId}?days=365`, {
      credentials: "include",
      signal: abortController.signal,
    })
      .then((res) => (res.ok ? res.json() : { data: [] }))
      .catch(() => ({ data: [] }));

    Promise.all([progressFetch, snapshotFetch])
      .then(([progressJson, snapshotJson]) => {
        setData(progressJson.data);
        if (progressJson.settings) {
          setSettings(progressJson.settings);
        }
        setSnapshots(snapshotJson.data ?? []);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => abortController.abort();
  }, []);

  // Fetch data when controller changes
  useEffect(() => {
    if (!controllerID) return;
    return fetchData(controllerID);
  }, [controllerID, fetchData]);

  // Animate progress bar after data loads
  useEffect(() => {
    if (!data) return;
    const pct = Math.min((data.total_aps / target) * 100, 100);
    const timer = setTimeout(() => setAnimatedPct(pct), 100);
    return () => clearTimeout(timer);
  }, [data, target]);

  const percentage = data ? (data.total_aps / target) * 100 : 0;
  const remaining = data ? Math.max(target - data.total_aps, 0) : target;

  // Sorted tenants
  const sortedTenants = useMemo(() => {
    if (!data?.tenants) return [];
    return [...data.tenants].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortDir === "asc"
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }
      return sortDir === "asc"
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });
  }, [data?.tenants, sortField, sortDir]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir(field === "name" ? "asc" : "desc");
    }
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDir === "asc" ? (
      <ChevronUp size={14} className="inline ml-1" />
    ) : (
      <ChevronDown size={14} className="inline ml-1" />
    );
  };

  // Inline target editing (saves to DB)
  const saveTarget = async () => {
    const val = parseInt(targetInput, 10);
    if (!isNaN(val) && val > 0 && controllerID) {
      try {
        const res = await fetch(
          `${API_BASE_URL}/migration-dashboard/settings/${controllerID}`,
          {
            method: "PUT",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_aps: val }),
          }
        );
        if (res.ok) {
          const updated = await res.json();
          setSettings(updated);
        }
      } catch {
        // Silently fail — target stays unchanged
      }
    }
    setEditingTarget(false);
  };

  // Open settings panel
  const openSettings = () => {
    setSettingsTarget(String(target));
    setSettingsIgnored(new Set(settings?.ignored_tenant_ids ?? []));
    setShowSettings(true);
  };

  // Save full settings
  const saveSettings = async () => {
    if (!controllerID) return;
    setSavingSettings(true);
    try {
      const val = parseInt(settingsTarget, 10);
      const res = await fetch(
        `${API_BASE_URL}/migration-dashboard/settings/${controllerID}`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_aps: !isNaN(val) && val > 0 ? val : undefined,
            ignored_tenant_ids: Array.from(settingsIgnored),
          }),
        }
      );
      if (res.ok) {
        setShowSettings(false);
        // Refetch progress so totals reflect new ignored tenants
        fetchData(controllerID);
      }
    } catch {
      // Keep panel open on error
    } finally {
      setSavingSettings(false);
    }
  };

  const toggleIgnored = (tenantId: string) => {
    setSettingsIgnored((prev) => {
      const next = new Set(prev);
      if (next.has(tenantId)) next.delete(tenantId);
      else next.add(tenantId);
      return next;
    });
  };

  const updateBackfillRow = (idx: number, field: keyof BackfillRow, value: string) => {
    setBackfillRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));
  };

  const addBackfillRow = () => {
    setBackfillRows((prev) => [...prev, emptyBackfillRow()]);
  };

  const removeBackfillRow = (idx: number) => {
    setBackfillRows((prev) => prev.filter((_, i) => i !== idx));
  };

  const submitBackfill = async () => {
    if (!controllerID) return;
    const entries = backfillRows
      .filter((r) => r.date && r.total_aps)
      .map((r) => ({
        date: r.date,
        total_aps: parseInt(r.total_aps, 10) || 0,
        operational_aps: parseInt(r.operational_aps, 10) || 0,
        total_venues: parseInt(r.total_venues, 10) || 0,
        total_clients: parseInt(r.total_clients, 10) || 0,
        total_ecs: parseInt(r.total_ecs, 10) || 0,
      }));
    if (entries.length === 0) return;

    setSavingBackfill(true);
    setBackfillStatus(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/migration-dashboard/snapshots/${controllerID}/backfill`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries }),
        }
      );
      if (res.ok) {
        const result = await res.json();
        setBackfillStatus(`Inserted ${result.inserted}, skipped ${result.skipped} (duplicates)`);
        setBackfillRows([emptyBackfillRow()]);
        fetchData(controllerID);
      } else {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        setBackfillStatus(`Error: ${err.detail}`);
      }
    } catch {
      setBackfillStatus("Error: Network request failed");
    } finally {
      setSavingBackfill(false);
    }
  };

  const deleteSnapshot = async (snapshotId: number) => {
    if (!controllerID) return;
    setDeletingSnapshot(snapshotId);
    try {
      const res = await fetch(
        `${API_BASE_URL}/migration-dashboard/snapshots/${controllerID}/${snapshotId}`,
        { method: "DELETE", credentials: "include" }
      );
      if (res.ok) {
        setSnapshots((prev) => prev.filter((s) => s.id !== snapshotId));
      }
    } catch {
      // Silently fail
    } finally {
      setDeletingSnapshot(null);
    }
  };

  if (!featureAccess.migration_dashboard) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center">
        <ShieldX size={48} className="mx-auto text-gray-300 mb-4" />
        <h2 className="text-xl font-semibold text-gray-700 mb-2">
          Access Restricted
        </h2>
        <p className="text-gray-500">
          The Migration Dashboard is restricted to authorized users.
        </p>
      </div>
    );
  }

  if (mspControllers.length === 0) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center">
        <BarChart3 size={48} className="mx-auto text-gray-300 mb-4" />
        <h2 className="text-xl font-semibold text-gray-700 mb-2">
          No MSP Controller Found
        </h2>
        <p className="text-gray-500">
          The Migration Dashboard requires an MSP-type RuckusONE controller.
          Add one on the Controllers page.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">
            Migration Dashboard
          </h1>
          <p className="text-gray-500 mt-1">
            SZ to R1 Migration Progress
            {mspControllers.length === 1 && controllerID && (
              <span className="ml-1">
                &mdash; {mspControllers[0].name}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {mspControllers.length > 1 && (
            <select
              value={controllerID ?? ""}
              onChange={(e) => setControllerID(parseInt(e.target.value))}
              className="px-3 py-2 border rounded-lg text-sm"
            >
              {mspControllers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={openSettings}
            className="flex items-center gap-2 px-3 py-2 bg-white border rounded-lg hover:bg-gray-50 text-sm"
            title="Dashboard Settings"
          >
            <Settings size={16} />
          </button>
          <button
            onClick={() => controllerID && fetchData(controllerID)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-white border rounded-lg hover:bg-gray-50 text-sm disabled:opacity-50"
          >
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* Settings Panel */}
      {showSettings && (
        <div className="bg-white rounded-xl shadow-lg border mb-6 overflow-hidden">
          <div className="flex items-center justify-between px-6 py-4 border-b bg-gray-50">
            <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
              <Settings size={18} /> Dashboard Settings
            </h2>
            <button onClick={() => setShowSettings(false)} className="text-gray-400 hover:text-gray-600">
              <X size={20} />
            </button>
          </div>
          <div className="p-6 space-y-6">
            {/* Target APs */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Target AP Count
              </label>
              <input
                type="number"
                value={settingsTarget}
                onChange={(e) => setSettingsTarget(e.target.value)}
                className="w-48 px-3 py-2 border rounded-lg text-sm"
                min={1}
              />
              <p className="text-xs text-gray-400 mt-1">
                Shared across all users viewing this controller's dashboard.
              </p>
            </div>

            {/* Ignored Tenants */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Ignored Tenants
                <span className="font-normal text-gray-400 ml-1">
                  (excluded from totals)
                </span>
              </label>
              {data?.tenants && data.tenants.length > 0 ? (
                <div className="max-h-64 overflow-y-auto border rounded-lg divide-y">
                  {[...data.tenants]
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((t) => (
                      <label
                        key={t.id}
                        className="flex items-center gap-3 px-4 py-2 hover:bg-gray-50 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={settingsIgnored.has(t.id)}
                          onChange={() => toggleIgnored(t.id)}
                          className="rounded border-gray-300"
                        />
                        <span className={`text-sm flex-1 ${settingsIgnored.has(t.id) ? "text-gray-400 line-through" : "text-gray-700"}`}>
                          {t.name}
                        </span>
                        <span className="text-xs text-gray-400 font-mono">
                          {t.ap_count.toLocaleString()} APs
                        </span>
                      </label>
                    ))}
                </div>
              ) : (
                <p className="text-sm text-gray-400">
                  Load the dashboard first to see tenants.
                </p>
              )}
            </div>

            {/* Save */}
            <div className="flex items-center gap-3">
              <button
                onClick={saveSettings}
                disabled={savingSettings}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm disabled:opacity-50"
              >
                {savingSettings ? "Saving..." : "Save Settings"}
              </button>
              <button
                onClick={() => setShowSettings(false)}
                className="px-4 py-2 bg-white border rounded-lg hover:bg-gray-50 text-sm"
              >
                Cancel
              </button>
              {settingsIgnored.size > 0 && (
                <span className="text-xs text-gray-400">
                  {settingsIgnored.size} tenant{settingsIgnored.size > 1 ? "s" : ""} will be excluded from totals
                </span>
              )}
            </div>

            {/* Historical Data (super only) */}
            {userRole === "super" && (
              <>
                <hr className="border-gray-200" />
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
                    <Calendar size={14} />
                    Add Historical Snapshots
                  </label>
                  <p className="text-xs text-gray-400 mb-3">
                    Backfill past migration data. Date and Total APs are required. One entry per day; duplicates are skipped.
                  </p>
                  <div className="space-y-2">
                    {backfillRows.map((row, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <input
                          type="date"
                          value={row.date}
                          onChange={(e) => updateBackfillRow(idx, "date", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-40"
                        />
                        <input
                          type="number"
                          placeholder="Total APs *"
                          value={row.total_aps}
                          onChange={(e) => updateBackfillRow(idx, "total_aps", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-28"
                        />
                        <input
                          type="number"
                          placeholder="Operational"
                          value={row.operational_aps}
                          onChange={(e) => updateBackfillRow(idx, "operational_aps", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-28"
                        />
                        <input
                          type="number"
                          placeholder="Venues"
                          value={row.total_venues}
                          onChange={(e) => updateBackfillRow(idx, "total_venues", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-24"
                        />
                        <input
                          type="number"
                          placeholder="Clients"
                          value={row.total_clients}
                          onChange={(e) => updateBackfillRow(idx, "total_clients", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-24"
                        />
                        <input
                          type="number"
                          placeholder="ECs"
                          value={row.total_ecs}
                          onChange={(e) => updateBackfillRow(idx, "total_ecs", e.target.value)}
                          className="px-2 py-1.5 border rounded text-sm w-20"
                        />
                        {backfillRows.length > 1 && (
                          <button
                            onClick={() => removeBackfillRow(idx)}
                            className="text-gray-400 hover:text-red-500 p-1"
                          >
                            <X size={14} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center gap-3 mt-3">
                    <button
                      onClick={addBackfillRow}
                      className="flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-700"
                    >
                      <Plus size={14} /> Add row
                    </button>
                    <button
                      onClick={submitBackfill}
                      disabled={savingBackfill || backfillRows.every((r) => !r.date || !r.total_aps)}
                      className="px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm disabled:opacity-50"
                    >
                      {savingBackfill ? "Inserting..." : "Insert Snapshots"}
                    </button>
                    {backfillStatus && (
                      <span className={`text-xs ${backfillStatus.startsWith("Error") ? "text-red-500" : "text-green-600"}`}>
                        {backfillStatus}
                      </span>
                    )}
                  </div>
                </div>

                {/* Existing snapshots list */}
                {snapshots.length > 0 && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Snapshot History ({snapshots.length})
                    </label>
                    <div className="max-h-64 overflow-y-auto border rounded-lg">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-gray-50 text-gray-500">
                          <tr>
                            <th className="text-left font-medium px-3 py-1.5">Date</th>
                            <th className="text-right font-medium px-3 py-1.5">APs</th>
                            <th className="text-right font-medium px-3 py-1.5">Online</th>
                            <th className="text-right font-medium px-3 py-1.5">Offline</th>
                            <th className="text-right font-medium px-3 py-1.5">Venues</th>
                            <th className="text-right font-medium px-3 py-1.5">Clients</th>
                            <th className="text-right font-medium px-3 py-1.5">ECs</th>
                            <th className="w-12"></th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {[...snapshots].reverse().map((s) => (
                            <tr key={s.id} className="hover:bg-gray-50">
                              <td className="px-3 py-1.5 text-gray-600 font-mono whitespace-nowrap">
                                {new Date(s.captured_at).toLocaleDateString()} {new Date(s.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                              </td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{s.total_aps.toLocaleString()}</td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{s.operational_aps.toLocaleString()}</td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{(s.total_aps - s.operational_aps).toLocaleString()}</td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{s.total_venues.toLocaleString()}</td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{s.total_clients.toLocaleString()}</td>
                              <td className="px-3 py-1.5 text-right font-mono font-medium text-gray-700">{s.total_ecs.toLocaleString()}</td>
                              <td className="px-3 py-1.5">
                                <button
                                  onClick={() => deleteSnapshot(s.id)}
                                  disabled={deletingSnapshot === s.id}
                                  className="text-gray-300 hover:text-red-500 p-2 disabled:opacity-50"
                                  title="Delete snapshot"
                                >
                                  <Trash2 size={14} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-center gap-3">
          <AlertCircle size={20} className="text-red-500 flex-shrink-0" />
          <p className="text-red-700">{error}</p>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="text-center py-16">
          <RefreshCw
            size={32}
            className="mx-auto text-indigo-500 animate-spin mb-4"
          />
          <p className="text-gray-600 font-medium">
            Querying {mspControllers.find((c) => c.id === controllerID)?.name || "MSP"} tenants...
          </p>
          <p className="text-gray-400 text-sm mt-1">
            This may take a few seconds
          </p>
        </div>
      )}

      {/* Dashboard content */}
      {data && !loading && (
        <>
          {/* Hero Progress Section */}
          <div className="bg-white rounded-xl shadow-lg p-8 mb-6">
            {/* Target editor */}
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500 flex items-center gap-2">
                <Target size={14} />
                {editingTarget ? (
                  <span className="flex items-center gap-1">
                    Target:{" "}
                    <input
                      type="number"
                      value={targetInput}
                      onChange={(e) => setTargetInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && saveTarget()}
                      onBlur={saveTarget}
                      autoFocus
                      className="w-28 px-2 py-0.5 border rounded text-sm"
                    />
                    <button
                      onClick={saveTarget}
                      className="text-green-600 hover:text-green-700"
                    >
                      <Check size={14} />
                    </button>
                  </span>
                ) : (
                  <span
                    className="cursor-pointer hover:text-gray-700 group"
                    onClick={() => {
                      setTargetInput(String(target));
                      setEditingTarget(true);
                    }}
                  >
                    Target: {target.toLocaleString()} APs
                    <Pencil
                      size={12}
                      className="inline ml-1 opacity-0 group-hover:opacity-100 transition"
                    />
                  </span>
                )}
              </div>
              <div className="text-sm font-medium text-gray-600">
                {getMessage(percentage)}
              </div>
            </div>

            {/* Big progress bar */}
            <div className="relative">
              <div className="w-full bg-gray-100 rounded-full h-12 overflow-hidden">
                <div
                  className={`h-12 rounded-full transition-all duration-1000 ease-out ${
                    percentage >= 100
                      ? "bg-gradient-to-r from-green-500 to-emerald-400"
                      : "bg-gradient-to-r from-blue-600 to-indigo-500"
                  }`}
                  style={{ width: `${Math.min(animatedPct, 100)}%` }}
                />
              </div>
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-lg font-bold drop-shadow-sm mix-blend-difference text-white">
                  {data.total_aps.toLocaleString()} / {target.toLocaleString()}{" "}
                  APs ({percentage.toFixed(1)}%)
                </span>
              </div>
              {/* Milestone markers */}
              {[25, 50, 75].map((m) => (
                <div
                  key={m}
                  className="absolute top-0 h-12 border-l border-gray-300 border-dashed opacity-40"
                  style={{ left: `${m}%` }}
                />
              ))}
            </div>
          </div>

          {/* Metrics Strip */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
            <MetricCard
              icon={<Wifi size={24} />}
              label="Total APs"
              value={data.total_aps.toLocaleString()}
              color="blue"
              sparkData={snapshots.map((s) => s.total_aps)}
            />
            <MetricCard
              icon={<Wifi size={24} />}
              label="Operational"
              value={(data.status_summary?.operational ?? 0).toLocaleString()}
              color="teal"
              sparkData={snapshots.map((s) => s.operational_aps)}
            />
            <div
              className="cursor-pointer"
              onClick={() => setShowStatusBreakdown((v) => !v)}
              title="Click for detailed status breakdown"
            >
              <MetricCard
                icon={<WifiOff size={24} />}
                label={`Offline ${showStatusBreakdown ? "\u25B2" : "\u25BC"}`}
                value={(data.status_summary?.offline ?? 0).toLocaleString()}
                color="amber"
                sparkData={snapshots.map((s) => s.total_aps - s.operational_aps)}
              />
            </div>
            <MetricCard
              icon={<MapPin size={24} />}
              label="Total Venues"
              value={data.total_venues.toLocaleString()}
              color="purple"
              sparkData={snapshots.map((s) => s.total_venues)}
            />
            <MetricCard
              icon={<Users size={24} />}
              label="Clients"
              value={(data.total_clients ?? 0).toLocaleString()}
              color="blue"
              sparkData={snapshots.map((s) => s.total_clients)}
            />
            <MetricCard
              icon={<Building2 size={24} />}
              label="EC Tenants"
              value={data.total_ecs.toLocaleString()}
              color="teal"
              sparkData={snapshots.map((s) => s.total_ecs)}
            />
          </div>

          {/* Status Breakdown */}
          {showStatusBreakdown && data.status_counts && (
            <div className="bg-white rounded-xl shadow p-5 mb-6">
              <h3 className="text-sm font-semibold text-gray-700 mb-3">AP Status Breakdown</h3>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                {Object.entries(data.status_counts)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([code, count]) => (
                    <div key={code} className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg">
                      <span className={`text-sm ${getStatusColor(code)}`}>
                        {STATUS_LABELS[code] ?? code}
                      </span>
                      <span className="text-sm font-mono font-medium text-gray-800 ml-2">
                        {count.toLocaleString()}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 30/60/90 Day Period Tracker */}
          {snapshots.length >= 2 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              <PeriodCard label="Last 30 Days" delta={getPeriodDelta(snapshots, 30)} />
              <PeriodCard label="Last 60 Days" delta={getPeriodDelta(snapshots, 60)} />
              <PeriodCard label="Last 90 Days" delta={getPeriodDelta(snapshots, 90)} />
            </div>
          )}

          {/* Errors banner */}
          {data.errors > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-6 flex items-center gap-2 text-sm text-amber-700">
              <AlertCircle size={16} />
              {data.errors} tenant{data.errors > 1 ? "s" : ""} returned errors
              during query
            </div>
          )}

          {/* EC Breakdown Table */}
          <div className="bg-white rounded-xl shadow overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-800">
                Tenant Breakdown
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      #
                    </th>
                    <th
                      className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("name")}
                    >
                      EC Tenant <SortIcon field="name" />
                    </th>
                    <th
                      className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("ap_count")}
                    >
                      APs <SortIcon field="ap_count" />
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Operational
                    </th>
                    <th
                      className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700"
                      onClick={() => handleSort("venue_count")}
                    >
                      Venues <SortIcon field="venue_count" />
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Clients
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Share
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {sortedTenants.map((tenant, idx) => {
                    const allAps = data.tenants.reduce((s, t) => s + t.ap_count, 0);
                    const share =
                      allAps > 0
                        ? (tenant.ap_count / allAps) * 100
                        : 0;
                    return (
                      <tr
                        key={tenant.id}
                        className={`hover:bg-gray-50 transition-colors ${tenant.ignored ? "opacity-40" : ""}`}
                      >
                        <td className="px-6 py-3 text-sm text-gray-400">
                          {idx + 1}
                        </td>
                        <td className="px-6 py-3 text-sm font-medium text-gray-800">
                          <span className={tenant.ignored ? "line-through" : ""}>
                            {tenant.name}
                          </span>
                          {tenant.ignored && (
                            <span className="ml-2 text-gray-400" title="Ignored — excluded from totals">
                              <EyeOff size={14} className="inline -mt-0.5" />
                            </span>
                          )}
                          {tenant.error && (
                            <span
                              className="ml-2 text-red-400"
                              title={tenant.error}
                            >
                              <AlertCircle
                                size={14}
                                className="inline -mt-0.5"
                              />
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono text-gray-700">
                          {tenant.ap_count.toLocaleString()}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono">
                          {(() => {
                            const op = tenant.status_summary?.operational ?? 0;
                            const pct = tenant.ap_count > 0 ? (op / tenant.ap_count) * 100 : 0;
                            return (
                              <span className={pct >= 90 ? "text-green-600" : pct >= 50 ? "text-amber-600" : "text-red-500"}>
                                {op.toLocaleString()}
                                <span className="text-xs text-gray-400 ml-1">
                                  ({pct.toFixed(0)}%)
                                </span>
                              </span>
                            );
                          })()}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono text-gray-700">
                          {tenant.venue_count.toLocaleString()}
                        </td>
                        <td className="px-6 py-3 text-sm text-right font-mono text-gray-700">
                          {(tenant.client_count ?? 0).toLocaleString()}
                        </td>
                        <td className="px-6 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-24 bg-gray-100 rounded-full h-2">
                              <div
                                className="h-2 rounded-full bg-indigo-400"
                                style={{
                                  width: `${Math.min(share, 100)}%`,
                                }}
                              />
                            </div>
                            <span className="text-xs text-gray-500 w-12">
                              {share.toFixed(1)}%
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// Sparkline color mapping per metric color
const SPARK_COLORS: Record<string, string> = {
  blue: "#3b82f6",
  purple: "#8b5cf6",
  teal: "#14b8a6",
  amber: "#f59e0b",
};

// Metric card component
function MetricCard({
  icon,
  label,
  value,
  color,
  sparkData,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color: "blue" | "purple" | "teal" | "amber";
  sparkData?: number[];
}) {
  const colors = {
    blue: "bg-blue-50 text-blue-600",
    purple: "bg-purple-50 text-purple-600",
    teal: "bg-teal-50 text-teal-600",
    amber: "bg-amber-50 text-amber-600",
  };

  return (
    <div className="bg-white rounded-xl shadow p-5">
      <div
        className={`w-10 h-10 rounded-lg flex items-center justify-center mb-3 ${colors[color]}`}
      >
        {icon}
      </div>
      <div className="text-2xl font-bold text-gray-900 flex items-center">
        {value}
        {sparkData && sparkData.length >= 2 && (
          <Sparkline data={sparkData} color={SPARK_COLORS[color] ?? "#6366f1"} />
        )}
      </div>
      <div className="text-sm text-gray-500 mt-1 flex items-center">
        {label}
        {sparkData && <TrendIndicator data={sparkData} />}
      </div>
    </div>
  );
}

export default MigrationDashboard;
