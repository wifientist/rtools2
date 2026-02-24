import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
} from "@tanstack/react-table";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ============================================================================
// Types
// ============================================================================

interface Network {
  id: string;
  name: string;
  ssid: string;
  securityProtocol: string;
  vlan: number;
  type: string;
  venues: number;
  aps: number;
  clients: number;
}

interface MvpSettings {
  clientIsolation: boolean | null;
  clientIsolationPacketsType: string | null;
  applicationVisibilityEnabled: boolean | null;
  bssMinimumPhyRate: string | null;
  phyTypeConstraint: string | null;
  enableJoinRSSIThreshold: boolean | null;
  joinRSSIThreshold: number | null;
  dtimInterval: number | null;
  qosMirroringEnabled: boolean | null;
  qosMirroringScope: string | null;
  enableApHostNameAdvertisement: boolean | null;
}

interface NetworkSettings {
  name: string;
  ssid: string;
  settings: MvpSettings;
}

interface FieldDiff {
  field: string;
  old_value: any;
  new_value: any;
}

interface NetworkDiff {
  network_id: string;
  name: string;
  ssid: string;
  changes: FieldDiff[];
}

interface Changes {
  clientIsolation?: boolean;
  clientIsolationPacketsType?: string;
  applicationVisibilityEnabled?: boolean;
  bssMinimumPhyRate?: string;
  phyTypeConstraint?: string;
  enableJoinRSSIThreshold?: boolean;
  joinRSSIThreshold?: number;
  dtimInterval?: number;
  qosMirroringEnabled?: boolean;
  qosMirroringScope?: string;
  enableApHostNameAdvertisement?: boolean;
}

// ============================================================================
// Default settings (pre-populated in editor)
// ============================================================================

const DEFAULT_CHANGES: Changes = {
  clientIsolation: true,
  clientIsolationPacketsType: "UNICAST_MULTICAST",
  applicationVisibilityEnabled: true,
  bssMinimumPhyRate: "12",
  phyTypeConstraint: "OFDM",
  enableJoinRSSIThreshold: true,
  joinRSSIThreshold: -75,
  dtimInterval: 2,
  qosMirroringEnabled: true,
  qosMirroringScope: "ALL_CLIENTS",
  enableApHostNameAdvertisement: true,
};

// ============================================================================
// Setting display config
// ============================================================================

const FIELD_LABELS: Record<string, string> = {
  clientIsolation: "Client Isolation",
  clientIsolationPacketsType: "Isolation Packets Type",
  applicationVisibilityEnabled: "Application Visibility",
  bssMinimumPhyRate: "BSS Min Rate",
  phyTypeConstraint: "OFDM Only",
  enableJoinRSSIThreshold: "Join RSSI Threshold",
  joinRSSIThreshold: "RSSI Value (dBm)",
  dtimInterval: "DTIM Interval",
  qosMirroringEnabled: "QoS Mirroring",
  qosMirroringScope: "QoS Mirroring Scope",
  enableApHostNameAdvertisement: "AP Hostname in Beacon",
};

const BSS_MIN_RATE_OPTIONS = [
  { value: "default", label: "Default" },
  { value: "1", label: "1 Mbps" },
  { value: "2", label: "2 Mbps" },
  { value: "5.5", label: "5.5 Mbps" },
  { value: "12", label: "12 Mbps" },
  { value: "24", label: "24 Mbps" },
];

function formatValue(field: string, value: any): string {
  if (value === null || value === undefined) return "N/A";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  if (field === "phyTypeConstraint") return value === "OFDM" ? "Enabled" : "Disabled";
  if (field === "bssMinimumPhyRate") {
    const opt = BSS_MIN_RATE_OPTIONS.find((o) => o.value === value);
    return opt ? opt.label : String(value);
  }
  if (field === "clientIsolationPacketsType") {
    if (value === "UNICAST") return "Unicast";
    if (value === "MULTICAST") return "Multicast";
    if (value === "UNICAST_MULTICAST") return "Unicast + Multicast";
    return String(value);
  }
  if (field === "qosMirroringScope")
    return value === "ALL_CLIENTS" ? "All Clients" : "MSCS Requests Only";
  if (field === "joinRSSIThreshold") return `${value} dBm`;
  return String(value);
}

// ============================================================================
// Component
// ============================================================================

const columnHelper = createColumnHelper<Network>();

export default function BulkWlanEdit() {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();
  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  const effectiveTenantId =
    activeControllerSubtype === "MSP" ? null : activeController?.r1_tenant_id || null;

  // Network list
  const [networks, setNetworks] = useState<Network[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Selection
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [globalFilter, setGlobalFilter] = useState("");

  // Settings
  const [fetchedSettings, setFetchedSettings] = useState<Record<string, NetworkSettings>>({});
  const [settingsLoading, setSettingsLoading] = useState(false);

  // Changes — defaults pre-populated with recommended settings
  const [changes, setChanges] = useState<Changes>({ ...DEFAULT_CHANGES });

  // Preview
  const [previewData, setPreviewData] = useState<{
    diffs: NetworkDiff[];
    change_count: number;
    unchanged_count: number;
    errors: string[];
  } | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // Job
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastResult, setLastResult] = useState<JobResult | null>(null);

  // ---- Derived ----
  const selectedIds = useMemo(
    () => Object.entries(rowSelection).filter(([, v]) => v).map(([k]) => networks[parseInt(k)]?.id).filter(Boolean),
    [rowSelection, networks]
  );

  const hasChanges = useMemo(
    () => Object.values(changes).some((v) => v !== undefined),
    [changes]
  );

  // ---- Fetch networks ----
  useEffect(() => {
    if (!activeControllerId) return;
    setLoading(true);
    setError("");
    setNetworks([]);
    setRowSelection({});
    setFetchedSettings({});
    setChanges({ ...DEFAULT_CHANGES });

    const url = effectiveTenantId
      ? `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks?tenant_id=${effectiveTenantId}`
      : `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks`;

    fetch(url, { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setNetworks(data.networks || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [activeControllerId, effectiveTenantId]);

  // ---- Fetch settings for selected networks (batched) ----
  const FETCH_BATCH_SIZE = 50;
  const [fetchProgress, setFetchProgress] = useState({ fetched: 0, total: 0 });

  const fetchSettings = async () => {
    if (!selectedIds.length || !activeControllerId) return;
    setSettingsLoading(true);
    setFetchProgress({ fetched: 0, total: selectedIds.length });

    const allNetworks: Record<string, NetworkSettings> = {};
    let fetched = 0;

    try {
      // Chunk selectedIds into batches of FETCH_BATCH_SIZE
      for (let i = 0; i < selectedIds.length; i += FETCH_BATCH_SIZE) {
        const batch = selectedIds.slice(i, i + FETCH_BATCH_SIZE);

        const res = await fetch(`${API_BASE_URL}/bulk-wlan/fetch-settings`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            controller_id: activeControllerId,
            tenant_id: effectiveTenantId,
            network_ids: batch,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const detail = Array.isArray(err.detail)
            ? err.detail.map((d: any) => `${d.loc?.join(".")}: ${d.msg}`).join("; ")
            : err.detail || `HTTP ${res.status}`;
          throw new Error(detail);
        }
        const data = await res.json();
        Object.assign(allNetworks, data.networks || {});
        fetched += batch.length;
        setFetchProgress({ fetched, total: selectedIds.length });
      }

      setFetchedSettings(allNetworks);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSettingsLoading(false);
    }
  };

  // ---- Current value summary ----
  const settingsSummary = useMemo(() => {
    const entries = Object.values(fetchedSettings);
    if (!entries.length) return {};

    const summary: Record<string, Record<string, number>> = {};
    for (const entry of entries) {
      for (const [field, value] of Object.entries(entry.settings)) {
        if (!summary[field]) summary[field] = {};
        const key = formatValue(field, value);
        summary[field][key] = (summary[field][key] || 0) + 1;
      }
    }
    return summary;
  }, [fetchedSettings]);

  // ---- Preview (computed client-side from already-fetched settings) ----
  const handlePreview = () => {
    if (!selectedIds.length || !hasChanges) return;

    const diffs: NetworkDiff[] = [];
    let unchangedCount = 0;

    for (const nid of selectedIds) {
      const entry = fetchedSettings[nid];
      if (!entry) continue;

      const fieldDiffs: FieldDiff[] = [];
      for (const [field, newValue] of Object.entries(changes)) {
        if (newValue === undefined) continue;
        const oldValue = entry.settings[field as keyof MvpSettings];
        if (oldValue !== newValue) {
          fieldDiffs.push({ field, old_value: oldValue, new_value: newValue });
        }
      }

      if (fieldDiffs.length > 0) {
        diffs.push({ network_id: nid, name: entry.name, ssid: entry.ssid, changes: fieldDiffs });
      } else {
        unchangedCount++;
      }
    }

    setPreviewData({ diffs, change_count: diffs.length, unchanged_count: unchangedCount, errors: [] });
    setShowPreview(true);
  };

  // ---- Apply ----
  const handleApply = async () => {
    if (!selectedIds.length || !hasChanges || !activeControllerId) return;
    try {
      const res = await fetch(`${API_BASE_URL}/bulk-wlan/apply`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          tenant_id: effectiveTenantId,
          network_ids: selectedIds,
          changes,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail = Array.isArray(err.detail)
          ? err.detail.map((d: any) => `${d.loc?.join(".")}: ${d.msg}`).join("; ")
          : err.detail || `HTTP ${res.status}`;
        throw new Error(detail);
      }
      const data = await res.json();
      setCurrentJobId(data.job_id);
      setShowJobModal(true);
      setShowPreview(false);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleJobComplete = (result: JobResult) => {
    // Don't close the modal — let the user see the completed state in JobMonitorView.
    // State reset happens when the user manually closes the modal.
    setLastResult(result);
  };

  const handleJobModalClose = () => {
    setShowJobModal(false);
    setChanges({ ...DEFAULT_CHANGES });
    setFetchedSettings({});
    setRowSelection({});
  };

  // ---- Table ----
  const columns = useMemo(
    () => [
      columnHelper.display({
        id: "select",
        header: ({ table }) => (
          <input
            type="checkbox"
            checked={table.getIsAllPageRowsSelected()}
            onChange={table.getToggleAllPageRowsSelectedHandler()}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
          />
        ),
        size: 40,
      }),
      columnHelper.accessor("ssid", { header: "SSID", size: 200 }),
      columnHelper.accessor("name", { header: "Network Name", size: 220 }),
      columnHelper.accessor("securityProtocol", { header: "Security", size: 120 }),
      columnHelper.accessor("type", { header: "Type", size: 80 }),
      columnHelper.accessor("vlan", { header: "VLAN", size: 60 }),
      columnHelper.accessor("venues", { header: "Venues", size: 60 }),
      columnHelper.accessor("aps", { header: "APs", size: 60 }),
      columnHelper.accessor("clients", { header: "Clients", size: 60 }),
    ],
    []
  );

  const table = useReactTable({
    data: networks,
    columns,
    state: { rowSelection, globalFilter },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // ---- Render helpers ----
  const TriStateToggle = ({
    label,
    value,
    onChange,
    summaryKey,
  }: {
    label: string;
    value: boolean | undefined;
    onChange: (v: boolean | undefined) => void;
    summaryKey: string;
  }) => (
    <div className="flex items-center justify-between py-2 border-b border-gray-100">
      <div className="flex-1">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <div className="flex gap-1">
        {(["no_change", "enable", "disable"] as const).map((opt) => {
          const isActive =
            opt === "no_change"
              ? value === undefined
              : opt === "enable"
              ? value === true
              : value === false;
          return (
            <button
              key={opt}
              onClick={() =>
                onChange(
                  opt === "no_change" ? undefined : opt === "enable" ? true : false
                )
              }
              className={`px-3 py-1 text-xs rounded ${
                isActive
                  ? opt === "no_change"
                    ? "bg-gray-200 text-gray-700"
                    : opt === "enable"
                    ? "bg-green-600 text-white"
                    : "bg-red-600 text-white"
                  : "bg-gray-100 text-gray-400 hover:bg-gray-200"
              }`}
            >
              {opt === "no_change" ? "No Change" : opt === "enable" ? "Enable" : "Disable"}
            </button>
          );
        })}
      </div>
    </div>
  );

  if (!activeControllerId) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <h1 className="text-2xl font-bold mb-2">Bulk WLAN Edit</h1>
        <p className="text-gray-500">Select a RuckusONE controller to get started.</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Bulk WLAN Edit</h1>
      <p className="text-gray-500 mb-6 text-sm">
        Select WiFi networks and modify advanced settings in bulk.
      </p>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-red-800 text-sm">
          {error}
          <button className="ml-2 text-red-600 underline text-xs" onClick={() => setError("")}>
            dismiss
          </button>
        </div>
      )}

      {/* ============= Last Job Result Banner ============= */}
      {lastResult && !showJobModal && (
        <div
          className={`rounded-lg p-4 mb-4 text-sm flex items-center justify-between ${
            lastResult.status === "COMPLETED"
              ? "bg-green-50 border border-green-200 text-green-800"
              : lastResult.status === "FAILED"
              ? "bg-red-50 border border-red-200 text-red-800"
              : "bg-yellow-50 border border-yellow-200 text-yellow-800"
          }`}
        >
          <div>
            <span className="font-medium">
              {lastResult.status === "COMPLETED"
                ? "Bulk WLAN update completed successfully."
                : lastResult.status === "FAILED"
                ? "Bulk WLAN update failed."
                : "Bulk WLAN update cancelled."}
            </span>
            {lastResult.progress?.total_tasks > 0 && (
              <span className="ml-2">
                {lastResult.progress.completed} of {lastResult.progress.total_tasks} networks updated
                {lastResult.progress.failed > 0 && `, ${lastResult.progress.failed} failed`}
              </span>
            )}
          </div>
          <button
            onClick={() => setLastResult(null)}
            className="text-xs underline opacity-70 hover:opacity-100"
          >
            dismiss
          </button>
        </div>
      )}

      {/* ============= STEP 1: Network Selection ============= */}
      <div className="bg-white rounded-lg shadow p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">1. Select Networks</h2>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-500">
              {selectedIds.length} of {networks.length} selected
            </span>
            {selectedIds.length > 0 && (
              <button
                onClick={() => setRowSelection({})}
                className="text-xs text-gray-500 hover:text-gray-700 underline"
              >
                Deselect All
              </button>
            )}
          </div>
        </div>

        <input
          type="text"
          placeholder="Search networks by name, SSID, type..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg mb-3 text-sm"
        />

        {loading ? (
          <div className="text-center py-8 text-gray-400">Loading networks...</div>
        ) : networks.length === 0 ? (
          <div className="text-center py-8 text-gray-400">No WiFi networks found.</div>
        ) : (
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto border rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                        style={{ width: header.getSize() }}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-gray-100">
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={`hover:bg-gray-50 cursor-pointer ${
                      row.getIsSelected() ? "bg-blue-50" : ""
                    }`}
                    onClick={row.getToggleSelectedHandler()}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-2 text-gray-700">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {selectedIds.length > 0 && (
          <div className="mt-3 flex justify-end">
            <button
              onClick={fetchSettings}
              disabled={settingsLoading}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:bg-gray-400"
            >
              {settingsLoading
                ? `Fetching Settings... ${fetchProgress.fetched}/${fetchProgress.total}`
                : `Fetch Settings for ${selectedIds.length} Network${selectedIds.length > 1 ? "s" : ""}`}
            </button>
          </div>
        )}
      </div>

      {/* ============= STEP 2: Settings Editor ============= */}
      {Object.keys(fetchedSettings).length > 0 && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <h2 className="text-lg font-semibold mb-3">2. Edit Settings</h2>
          <p className="text-xs text-gray-400 mb-4">
            Only settings you change will be applied. "No Change" leaves the current value untouched.
          </p>

          <div className="space-y-1">
            {/* Client Isolation */}
            <TriStateToggle
              label="Client Isolation"
              value={changes.clientIsolation}
              onChange={(v) => setChanges((c) => ({
                ...c,
                clientIsolation: v,
                // Clear packets type when disabling or resetting
                clientIsolationPacketsType: v === true ? c.clientIsolationPacketsType : undefined,
              }))}
              summaryKey="clientIsolation"
            />

            {/* Client Isolation Packets Type - only when enabled */}
            {changes.clientIsolation === true && (
              <div className="flex items-center justify-between py-2 border-b border-gray-100 pl-4">
                <div className="flex-1">
                  <div className="text-sm font-medium text-gray-700">Isolation Packets Type</div>
                  {settingsSummary.clientIsolationPacketsType && (
                    <div className="text-xs text-gray-400 mt-0.5">
                      Current:{" "}
                      {Object.entries(settingsSummary.clientIsolationPacketsType)
                        .map(([k, c]) => `${c}x ${k}`)
                        .join(", ")}
                    </div>
                  )}
                </div>
                <select
                  value={changes.clientIsolationPacketsType ?? "__no_change__"}
                  onChange={(e) =>
                    setChanges((c) => ({
                      ...c,
                      clientIsolationPacketsType:
                        e.target.value === "__no_change__" ? undefined : e.target.value,
                    }))
                  }
                  className="px-3 py-1 text-sm border rounded"
                >
                  <option value="__no_change__">No Change</option>
                  <option value="UNICAST">Unicast</option>
                  <option value="MULTICAST">Multicast</option>
                  <option value="UNICAST_MULTICAST">Unicast + Multicast</option>
                </select>
              </div>
            )}

            {/* Application Visibility */}
            <TriStateToggle
              label="Application Visibility"
              value={changes.applicationVisibilityEnabled}
              onChange={(v) => setChanges((c) => ({ ...c, applicationVisibilityEnabled: v }))}
              summaryKey="applicationVisibilityEnabled"
            />

            {/* BSS Min Rate */}
            <div className="flex items-center justify-between py-2 border-b border-gray-100">
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-700">BSS Min Rate</div>
                {settingsSummary.bssMinimumPhyRate && (
                  <div className="text-xs text-gray-400 mt-0.5">
                    Current:{" "}
                    {Object.entries(settingsSummary.bssMinimumPhyRate)
                      .map(([k, c]) => `${c}x ${k}`)
                      .join(", ")}
                  </div>
                )}
              </div>
              <select
                value={changes.bssMinimumPhyRate ?? "__no_change__"}
                onChange={(e) =>
                  setChanges((c) => ({
                    ...c,
                    bssMinimumPhyRate:
                      e.target.value === "__no_change__" ? undefined : e.target.value,
                  }))
                }
                className="px-3 py-1 text-sm border rounded"
              >
                <option value="__no_change__">No Change</option>
                {BSS_MIN_RATE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            {/* OFDM Only */}
            <TriStateToggle
              label="OFDM Only"
              value={
                changes.phyTypeConstraint === undefined
                  ? undefined
                  : changes.phyTypeConstraint === "OFDM"
              }
              onChange={(v) =>
                setChanges((c) => ({
                  ...c,
                  phyTypeConstraint: v === undefined ? undefined : v ? "OFDM" : "NONE",
                }))
              }
              summaryKey="phyTypeConstraint"
            />

            {/* Join RSSI Threshold */}
            <div className="flex items-center justify-between py-2 border-b border-gray-100">
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-700">Join RSSI Threshold</div>
                {settingsSummary.enableJoinRSSIThreshold && (
                  <div className="text-xs text-gray-400 mt-0.5">
                    Current:{" "}
                    {Object.entries(settingsSummary.enableJoinRSSIThreshold)
                      .map(([k, c]) => `${c}x ${k}`)
                      .join(", ")}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  {(["no_change", "enable", "disable"] as const).map((opt) => {
                    const isActive =
                      opt === "no_change"
                        ? changes.enableJoinRSSIThreshold === undefined
                        : opt === "enable"
                        ? changes.enableJoinRSSIThreshold === true
                        : changes.enableJoinRSSIThreshold === false;
                    return (
                      <button
                        key={opt}
                        onClick={() =>
                          setChanges((c) => ({
                            ...c,
                            enableJoinRSSIThreshold:
                              opt === "no_change"
                                ? undefined
                                : opt === "enable"
                                ? true
                                : false,
                            // Clear threshold value when not enabling
                            ...(opt !== "enable" && { joinRSSIThreshold: undefined }),
                          }))
                        }
                        className={`px-3 py-1 text-xs rounded ${
                          isActive
                            ? opt === "no_change"
                              ? "bg-gray-200 text-gray-700"
                              : opt === "enable"
                              ? "bg-green-600 text-white"
                              : "bg-red-600 text-white"
                            : "bg-gray-100 text-gray-400 hover:bg-gray-200"
                        }`}
                      >
                        {opt === "no_change" ? "No Change" : opt === "enable" ? "Enable" : "Disable"}
                      </button>
                    );
                  })}
                </div>
                {changes.enableJoinRSSIThreshold === true && (
                  <div className="flex items-center gap-1">
                    <input
                      type="range"
                      min={-90}
                      max={-60}
                      value={changes.joinRSSIThreshold ?? -85}
                      onChange={(e) =>
                        setChanges((c) => ({
                          ...c,
                          joinRSSIThreshold: parseInt(e.target.value),
                        }))
                      }
                      className="w-24"
                    />
                    <span className="text-xs text-gray-600 w-14 text-right">
                      {changes.joinRSSIThreshold ?? -85} dBm
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* AP Hostname in Beacon */}
            <TriStateToggle
              label="AP Hostname in Beacon"
              value={changes.enableApHostNameAdvertisement}
              onChange={(v) => setChanges((c) => ({ ...c, enableApHostNameAdvertisement: v }))}
              summaryKey="enableApHostNameAdvertisement"
            />

            {/* DTIM Interval */}
            <div className="flex items-center justify-between py-2 border-b border-gray-100">
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-700">DTIM Interval</div>
                {settingsSummary.dtimInterval && (
                  <div className="text-xs text-gray-400 mt-0.5">
                    Current:{" "}
                    {Object.entries(settingsSummary.dtimInterval)
                      .map(([k, c]) => `${c}x ${k}`)
                      .join(", ")}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={1}
                  max={255}
                  placeholder="No Change"
                  value={changes.dtimInterval ?? ""}
                  onChange={(e) =>
                    setChanges((c) => ({
                      ...c,
                      dtimInterval: e.target.value ? parseInt(e.target.value) : undefined,
                    }))
                  }
                  className="w-24 px-2 py-1 text-sm border rounded"
                />
              </div>
            </div>

            {/* QoS Mirroring */}
            <TriStateToggle
              label="QoS Mirroring"
              value={changes.qosMirroringEnabled}
              onChange={(v) => setChanges((c) => ({
                ...c,
                qosMirroringEnabled: v,
                // Clear scope when not enabling
                ...(v !== true && { qosMirroringScope: undefined }),
              }))}
              summaryKey="qosMirroringEnabled"
            />

            {/* QoS Mirroring Scope - only when enabled */}
            {changes.qosMirroringEnabled === true && (
              <div className="flex items-center justify-between py-2 border-b border-gray-100 pl-4">
                <div className="text-sm font-medium text-gray-700">QoS Mirroring Scope</div>
                <select
                  value={changes.qosMirroringScope ?? "__no_change__"}
                  onChange={(e) =>
                    setChanges((c) => ({
                      ...c,
                      qosMirroringScope:
                        e.target.value === "__no_change__" ? undefined : e.target.value,
                    }))
                  }
                  className="px-3 py-1 text-sm border rounded"
                >
                  <option value="__no_change__">No Change</option>
                  <option value="MSCS_REQUESTS_ONLY">MSCS Requests Only</option>
                  <option value="ALL_CLIENTS">All Clients</option>
                </select>
              </div>
            )}

          </div>

          {/* Preview button */}
          <div className="mt-4 flex justify-end gap-3">
            <button
              onClick={() => setChanges({ ...DEFAULT_CHANGES })}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
            >
              Reset All
            </button>
            <button
              onClick={handlePreview}
              disabled={!hasChanges}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:bg-gray-400"
            >
              Preview Changes
            </button>
          </div>
        </div>
      )}

      {/* ============= STEP 3: Preview Modal ============= */}
      {showPreview && previewData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col">
            <div className="px-6 py-4 border-b flex items-center justify-between">
              <h3 className="text-lg font-semibold">Preview Changes</h3>
              <button
                onClick={() => setShowPreview(false)}
                className="text-gray-400 hover:text-gray-600 text-xl"
              >
                &times;
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <div className="flex gap-4 mb-4 text-sm">
                <span className="bg-green-100 text-green-800 px-3 py-1 rounded">
                  {previewData.change_count} network{previewData.change_count !== 1 ? "s" : ""} will change
                </span>
                <span className="bg-gray-100 text-gray-600 px-3 py-1 rounded">
                  {previewData.unchanged_count} unchanged
                </span>
              </div>

              {previewData.errors.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-sm text-red-700">
                  {previewData.errors.map((e, i) => (
                    <div key={i}>{e}</div>
                  ))}
                </div>
              )}

              {previewData.diffs.length === 0 ? (
                <p className="text-gray-500 text-center py-8">No changes to apply.</p>
              ) : (
                <div className="space-y-2">
                  {previewData.diffs.map((diff) => (
                    <div key={diff.network_id} className="border rounded-lg p-3">
                      <div className="font-medium text-sm">
                        {diff.ssid}{" "}
                        <span className="text-gray-400 font-normal">({diff.name})</span>
                      </div>
                      <div className="mt-2 space-y-1">
                        {diff.changes.map((c, i) => (
                          <div key={i} className="flex items-center text-xs gap-2">
                            <span className="text-gray-500 w-40">
                              {FIELD_LABELS[c.field] || c.field}
                            </span>
                            <span className="text-red-600 line-through">
                              {formatValue(c.field, c.old_value)}
                            </span>
                            <span className="text-gray-400">&rarr;</span>
                            <span className="text-green-700 font-medium">
                              {formatValue(c.field, c.new_value)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t flex justify-end gap-3">
              <button
                onClick={() => setShowPreview(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleApply}
                disabled={previewData.change_count === 0}
                className="px-5 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 disabled:bg-gray-400"
              >
                Apply Changes to {previewData.change_count} Network
                {previewData.change_count !== 1 ? "s" : ""}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Job Monitor */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleJobModalClose}
          onJobComplete={handleJobComplete}
        />
      )}
    </div>
  );
}
