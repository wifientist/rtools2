import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import JobMonitorModal from "@/components/JobMonitorModal";
import MspEcPicker from "@/components/MspEcPicker";
import type { JobResult } from "@/components/JobMonitorModal";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
} from "@tanstack/react-table";
import { apiFetch } from "@/utils/api";

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

// Fresh counts from POST /bulk-wlan/delete/check
interface CheckedNetwork {
  id: string;
  name: string;
  ssid: string;
  venues: number;
  aps: number;
  clients: number;
  in_use: boolean;
  in_use_for_deactivate: boolean;
}

type Action = "delete" | "deactivate";

const ACTION_LABELS: Record<Action, { verb: string; noun: string; past: string }> = {
  delete: { verb: "Delete", noun: "deletion", past: "deleted" },
  deactivate: { verb: "Deactivate", noun: "deactivation", past: "deactivated" },
};

// Deactivating targets networks that are on venues, so only live APs or
// clients need a confirmation; a delete wants venues, APs and clients at 0.
// Mirrors is_in_use() in bulk_wlan_delete_router.py.
function inUse(n: { venues: number; aps: number; clients: number }, action: Action): boolean {
  if (action === "deactivate") return n.aps > 0 || n.clients > 0;
  return n.venues > 0 || n.aps > 0 || n.clients > 0;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count !== 1 ? "s" : ""}`;
}

// ============================================================================
// Component
// ============================================================================

const columnHelper = createColumnHelper<Network>();

const CountCell = ({ value }: { value: number }) => (
  <span className={value > 0 ? "text-red-600 font-semibold" : "text-gray-400"}>{value ?? 0}</span>
);

export default function BulkWlanDelete() {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();
  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  // An MSP's key delegates down to one MSP-EC, so the EC is a step before
  // anything below it can be listed (same as Bulk WLAN Edit).
  const needsEcSelection = activeControllerSubtype === "MSP";

  const [ecId, setEcId] = useState<string | null>(null);
  const [ecName, setEcName] = useState<string | null>(null);
  const effectiveTenantId = needsEcSelection
    ? ecId
    : (activeController?.r1_tenant_id || null);

  // Switching controllers leaves the previous controller's EC behind.
  useEffect(() => {
    setEcId(null);
    setEcName(null);
  }, [activeControllerId]);

  const [action, setAction] = useState<Action>("delete");
  const labels = ACTION_LABELS[action];

  // Network list
  const [networks, setNetworks] = useState<Network[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  // Selection — keyed by network id (getRowId), not row index, so a reload
  // or a filter can never shift a selection onto a different network.
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [globalFilter, setGlobalFilter] = useState("");
  const [hideInUse, setHideInUse] = useState(false);

  // Review
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<{ networks: CheckedNetwork[]; missing: string[] } | null>(null);

  // Per-network confirmation walk over the in-use networks
  const [confirmQueue, setConfirmQueue] = useState<CheckedNetwork[]>([]);
  const [confirmIndex, setConfirmIndex] = useState(0);
  const [overrideIds, setOverrideIds] = useState<string[]>([]);
  const [skippedIds, setSkippedIds] = useState<string[]>([]);

  // Job
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastResult, setLastResult] = useState<JobResult | null>(null);
  const [lastAction, setLastAction] = useState<Action>("delete");
  const [submitting, setSubmitting] = useState(false);

  // ---- Derived ----
  const networkIds = useMemo(() => new Set(networks.map((n) => n.id)), [networks]);

  const selectedIds = useMemo(
    () => Object.entries(rowSelection).filter(([id, v]) => v && networkIds.has(id)).map(([id]) => id),
    [rowSelection, networkIds]
  );

  const visibleNetworks = useMemo(
    () => (hideInUse ? networks.filter((n) => !inUse(n, action)) : networks),
    [networks, hideInUse, action]
  );

  // ---- Fetch networks ----
  useEffect(() => {
    if (!activeControllerId) return;
    if (needsEcSelection && !ecId) {
      setNetworks([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    setNetworks([]);
    setRowSelection({});

    const url = effectiveTenantId
      ? `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks?tenant_id=${effectiveTenantId}`
      : `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks`;

    apiFetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setNetworks(data.networks || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [activeControllerId, effectiveTenantId, needsEcSelection, ecId, reloadKey]);

  // ---- Review: re-read counts for the selection ----
  const handleReview = async () => {
    if (!selectedIds.length || !activeControllerId) return;
    setChecking(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/bulk-wlan/delete/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          tenant_id: effectiveTenantId,
          network_ids: selectedIds,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setChecked(await res.json());
    } catch (err: any) {
      setError(err.message);
    } finally {
      setChecking(false);
    }
  };

  const reviewClear = useMemo(
    () => (checked ? checked.networks.filter((n) => !inUse(n, action)) : []),
    [checked, action]
  );
  const reviewInUse = useMemo(
    () => (checked ? checked.networks.filter((n) => inUse(n, action)) : []),
    [checked, action]
  );

  // ---- Submit: walk every in-use network through its own confirmation ----
  const handleSubmitClick = () => {
    setOverrideIds([]);
    setSkippedIds([]);
    if (reviewInUse.length > 0) {
      setConfirmIndex(0);
      setConfirmQueue(reviewInUse);
    } else {
      submitJob([]);
    }
  };

  const advanceConfirm = (decision: "override" | "skip") => {
    const current = confirmQueue[confirmIndex];
    const nextOverrides = decision === "override" ? [...overrideIds, current.id] : overrideIds;
    if (decision === "override") setOverrideIds(nextOverrides);
    else setSkippedIds((s) => [...s, current.id]);

    if (confirmIndex + 1 < confirmQueue.length) {
      setConfirmIndex(confirmIndex + 1);
      return;
    }
    setConfirmQueue([]);
    submitJob(nextOverrides);
  };

  const cancelConfirm = () => {
    setConfirmQueue([]);
    setOverrideIds([]);
    setSkippedIds([]);
  };

  const submitJob = async (overrides: string[]) => {
    if (!activeControllerId) return;
    const ids = [...reviewClear.map((n) => n.id), ...overrides];
    if (!ids.length) {
      setError(`Nothing to ${action}: every in-use network was skipped.`);
      setChecked(null);
      return;
    }
    setSubmitting(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/bulk-wlan/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          tenant_id: effectiveTenantId,
          network_ids: ids,
          override_ids: overrides,
          action,
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
      setLastAction(action);
      setCurrentJobId(data.job_id);
      setShowJobModal(true);
      setChecked(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleJobComplete = (result: JobResult) => {
    // Keep the modal open so the completed state stays visible.
    setLastResult(result);
  };

  const handleJobModalClose = () => {
    setShowJobModal(false);
    setRowSelection({});
    // Deleted networks are gone and deactivated ones have new counts.
    setReloadKey((k) => k + 1);
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
      columnHelper.accessor("venues", { header: "Venues", size: 60, cell: (c) => <CountCell value={c.getValue()} /> }),
      columnHelper.accessor("aps", { header: "APs", size: 60, cell: (c) => <CountCell value={c.getValue()} /> }),
      columnHelper.accessor("clients", { header: "Clients", size: 60, cell: (c) => <CountCell value={c.getValue()} /> }),
    ],
    []
  );

  const table = useReactTable({
    data: visibleNetworks,
    columns,
    state: { rowSelection, globalFilter },
    getRowId: (row) => row.id,
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // Selected networks the current search / in-use filter is hiding — easy to
  // forget about, and they are in the blast radius all the same.
  const visibleRowIds = new Set(table.getRowModel().rows.map((r) => r.id));
  const hiddenSelectedCount = selectedIds.filter((id) => !visibleRowIds.has(id)).length;
  const selectedInUseCount = networks.filter((n) => rowSelection[n.id] && inUse(n, action)).length;

  const currentConfirm = confirmQueue[confirmIndex];

  if (!activeControllerId) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <h1 className="text-2xl font-bold mb-2">Bulk WLAN Delete</h1>
        <p className="text-gray-500">Select a RuckusONE controller to get started.</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Bulk WLAN Delete</h1>
      <p className="text-gray-500 mb-6 text-sm">
        Select WiFi networks and delete them, or only deactivate them from their venues, in bulk.
        Anything still on a venue, an AP, or a client needs its own confirmation.
      </p>

      {needsEcSelection && activeControllerId && (
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            MSP-EC (tenant)
          </label>
          <MspEcPicker
            controllerId={activeControllerId}
            ecId={ecId}
            ecName={ecName}
            onChange={(id, name) => {
              setEcId(id);
              setEcName(name);
            }}
          />
        </div>
      )}

      {needsEcSelection && !ecId && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
          <p className="text-sm text-blue-800">
            Select an MSP-EC above — WiFi networks belong to a tenant, not to the MSP.
          </p>
        </div>
      )}

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
          className={`rounded-lg p-4 mb-4 text-sm ${
            lastResult.status === "COMPLETED"
              ? "bg-green-50 border border-green-200 text-green-800"
              : lastResult.status === "FAILED"
              ? "bg-red-50 border border-red-200 text-red-800"
              : "bg-yellow-50 border border-yellow-200 text-yellow-800"
          }`}
        >
          <div className="flex items-center justify-between">
            <div>
              <span className="font-medium">
                {lastResult.status === "COMPLETED"
                  ? `Bulk WLAN ${ACTION_LABELS[lastAction].noun} completed successfully.`
                  : lastResult.status === "PARTIAL"
                  ? `Bulk WLAN ${ACTION_LABELS[lastAction].noun} completed with skips or errors.`
                  : lastResult.status === "FAILED"
                  ? `Bulk WLAN ${ACTION_LABELS[lastAction].noun} failed.`
                  : `Bulk WLAN ${ACTION_LABELS[lastAction].noun} cancelled.`}
              </span>
              {lastResult.progress?.total_tasks > 0 && (
                <span className="ml-2">
                  {lastResult.progress.completed} of {lastResult.progress.total_tasks} networks processed
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
          {lastResult.errors && lastResult.errors.length > 0 && (
            <div className="mt-2 text-xs space-y-1 border-t border-current/20 pt-2">
              {lastResult.errors.map((err, i) => (
                <div key={i}>{err}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ============= STEP 1: Network Selection ============= */}
      <div className="bg-white rounded-lg shadow p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">1. Select Networks</h2>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-500">
              {selectedIds.length} of {networks.length} selected
              {hiddenSelectedCount > 0 && (
                <span className="text-amber-700"> ({hiddenSelectedCount} hidden by filter)</span>
              )}
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

        <div className="flex items-center gap-4 mb-3">
          <div className="flex rounded-lg border overflow-hidden text-sm flex-shrink-0">
            {(["delete", "deactivate"] as const).map((a) => (
              <button
                key={a}
                onClick={() => setAction(a)}
                className={`px-3 py-1.5 ${
                  action === a
                    ? a === "delete" ? "bg-red-600 text-white" : "bg-amber-500 text-white"
                    : "bg-white text-gray-600 hover:bg-gray-50"
                }`}
              >
                {a === "delete" ? "Delete" : "Deactivate only"}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-600 flex-shrink-0">
            <input type="checkbox" checked={hideInUse} onChange={(e) => setHideInUse(e.target.checked)} />
            {action === "delete" ? "Only show networks at 0 venues / APs / clients" : "Only show networks at 0 APs / clients"}
          </label>
        </div>
        <p className="text-xs text-gray-400 mb-3">
          {action === "delete"
            ? "Delete removes each network from its venues first, then deletes it."
            : "Deactivate removes each network from every venue it is on and leaves the network itself in place, so a later Delete pass finds it at 0."}
        </p>

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
                      row.getIsSelected() ? "bg-red-50" : ""
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
          <div className="mt-3 flex items-center justify-end gap-3">
            {selectedInUseCount > 0 && (
              <span className="text-xs text-red-700">
                {plural(selectedInUseCount, "selected network")} still in use — each will ask for confirmation
              </span>
            )}
            <button
              onClick={handleReview}
              disabled={checking}
              className={`px-4 py-2 text-white rounded-lg text-sm disabled:bg-gray-400 ${
                action === "delete" ? "bg-red-600 hover:bg-red-700" : "bg-amber-500 hover:bg-amber-600"
              }`}
            >
              {checking
                ? "Checking current usage..."
                : `Review ${labels.verb} of ${plural(selectedIds.length, "Network")}`}
            </button>
          </div>
        )}
      </div>

      {/* ============= Review Modal ============= */}
      {checked && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col">
            <div className="px-6 py-4 border-b">
              <h3 className="text-lg font-semibold">Review {labels.noun}</h3>
              <p className="text-xs text-gray-500 mt-1">Counts were just re-read from RuckusONE.</p>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <div className="flex flex-wrap gap-2 mb-4 text-sm">
                <span className="bg-green-100 text-green-800 px-3 py-1 rounded">
                  {reviewClear.length} clear
                </span>
                {reviewInUse.length > 0 && (
                  <span className="bg-red-100 text-red-800 px-3 py-1 rounded">
                    {reviewInUse.length} in use — confirm each
                  </span>
                )}
                {checked.missing.length > 0 && (
                  <span className="bg-gray-100 text-gray-600 px-3 py-1 rounded">
                    {checked.missing.length} no longer exist — ignored
                  </span>
                )}
              </div>

              <table className="w-full text-sm border rounded">
                <thead className="bg-gray-50">
                  <tr>
                    {["SSID", "Network Name", "Venues", "APs", "Clients", "Status"].map((h) => (
                      <th key={h} className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {[...reviewInUse, ...reviewClear].map((n) => {
                    const busy = inUse(n, action);
                    return (
                      <tr key={n.id} className={busy ? "bg-red-50" : ""}>
                        <td className="px-3 py-2">{n.ssid}</td>
                        <td className="px-3 py-2 text-gray-500">{n.name}</td>
                        <td className="px-3 py-2"><CountCell value={n.venues} /></td>
                        <td className="px-3 py-2"><CountCell value={n.aps} /></td>
                        <td className="px-3 py-2"><CountCell value={n.clients} /></td>
                        <td className="px-3 py-2 text-xs">
                          {busy
                            ? <span className="text-red-700 font-medium">In use — needs confirmation</span>
                            : <span className="text-green-700">Clear</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="px-6 py-4 border-t flex justify-end gap-3">
              <button
                onClick={() => setChecked(null)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitClick}
                disabled={submitting || checked.networks.length === 0}
                className={`px-5 py-2 text-white rounded-lg text-sm font-semibold disabled:bg-gray-400 ${
                  action === "delete" ? "bg-red-600 hover:bg-red-700" : "bg-amber-500 hover:bg-amber-600"
                }`}
              >
                {reviewInUse.length > 0
                  ? `${labels.verb} ${reviewClear.length} + review ${reviewInUse.length} in use`
                  : `${labels.verb} ${plural(reviewClear.length, "Network")}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============= Per-network In-Use Confirmation ============= */}
      {currentConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-60 z-[60] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-md w-full">
            <div className="bg-red-600 rounded-t-xl px-6 py-4 flex items-center justify-between">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                &#9888; Network In Use
              </h3>
              <span className="text-xs text-red-100">
                {confirmIndex + 1} of {confirmQueue.length}
              </span>
            </div>
            <div className="px-6 py-5 space-y-3">
              <div>
                <div className="text-base font-semibold text-gray-900">{currentConfirm.ssid}</div>
                <div className="text-xs text-gray-500">{currentConfirm.name}</div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                {([["Venues", currentConfirm.venues], ["APs", currentConfirm.aps], ["Clients", currentConfirm.clients]] as const).map(([label, value]) => (
                  <div key={label} className={`rounded-lg border p-2 ${value > 0 ? "border-red-300 bg-red-50" : "border-gray-200"}`}>
                    <div className={`text-xl font-bold ${value > 0 ? "text-red-700" : "text-gray-400"}`}>{value}</div>
                    <div className="text-xs text-gray-500">{label}</div>
                  </div>
                ))}
              </div>
              <p className="text-sm text-gray-700">
                {action === "delete" ? "Deleting" : "Deactivating"} this network first removes it from{" "}
                {plural(currentConfirm.venues, "venue")}
                {currentConfirm.clients > 0 && (
                  <>, disconnecting <strong>{plural(currentConfirm.clients, "client")}</strong></>
                )}
                {currentConfirm.aps > 0 && <> and taking the SSID off {plural(currentConfirm.aps, "AP")}</>}.
              </p>
            </div>
            <div className="px-6 py-4 border-t bg-gray-50 rounded-b-xl flex justify-between gap-3">
              <button
                onClick={cancelConfirm}
                className="px-3 py-2 text-sm text-gray-600 hover:text-gray-800 font-medium"
              >
                Cancel all
              </button>
              <div className="flex gap-2">
                <button
                  onClick={() => advanceConfirm("skip")}
                  className="px-4 py-2 text-sm bg-white border border-gray-300 rounded-lg hover:bg-gray-100 font-medium"
                >
                  Skip this one
                </button>
                <button
                  onClick={() => advanceConfirm("override")}
                  className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 font-semibold"
                >
                  {labels.verb} anyway
                </button>
              </div>
            </div>
            {(overrideIds.length > 0 || skippedIds.length > 0) && (
              <div className="px-6 pb-3 bg-gray-50 rounded-b-xl text-xs text-gray-500">
                So far: {overrideIds.length} confirmed, {skippedIds.length} skipped
              </div>
            )}
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
