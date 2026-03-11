import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// Types
interface VenueAP {
  serialNumber: string;
  name: string;
  model: string;
  status: string;
  apGroupId: string;
  apGroupName: string;
}

interface SwapMapping {
  old_serial: string;
  new_serial: string;
}

interface PreviewPair {
  old_serial: string;
  new_serial: string;
  old_ap_name: string | null;
  old_ap_group_id: string | null;
  old_ap_group_name: string | null;
  old_ap_model: string | null;
  old_ap_status: string | null;
  settings_count: number;
  warnings: string[];
  errors: string[];
  valid: boolean;
}

interface SwapRecord {
  swap_id: string;
  controller_id: number;
  venue_id: string;
  old_serial: string;
  new_serial: string;
  ap_name: string | null;
  ap_group_id: string | null;
  ap_group_name: string | null;
  status: string;
  created_at: string;
  expires_at: string;
  sync_attempts: number;
  last_attempt_at: string | null;
  applied_at: string | null;
  cleanup_action: string;
  config_data?: Record<string, unknown>;
  apply_results?: Record<string, string>;
}

type Tab = "new-swap" | "pending";
type WizardStep = "venue" | "select-aps" | "map-new" | "options" | "preview" | "result";

function APPopAndSwap() {
  const {
    activeControllerId,
    activeControllerSubtype,
    controllers,
  } = useAuth();

  // Tab
  const [activeTab, setActiveTab] = useState<Tab>("new-swap");

  // Venue selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  const activeController = controllers.find((c) => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection ? null : activeController?.r1_tenant_id || null;

  // Wizard state
  const [step, setStep] = useState<WizardStep>("venue");
  const [venueAps, setVenueAps] = useState<VenueAP[]>([]);
  const [selectedOldSerials, setSelectedOldSerials] = useState<string[]>([]);
  const [mappings, setMappings] = useState<SwapMapping[]>([]);
  const [copyName, setCopyName] = useState(true);
  const [cleanupAction, setCleanupAction] = useState<"none" | "unassign" | "remove">("none");
  const [previewPairs, setPreviewPairs] = useState<PreviewPair[]>([]);
  const [applyResult, setApplyResult] = useState<any>(null);

  // Loading/error
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Pending swaps
  const [pendingSwaps, setPendingSwaps] = useState<SwapRecord[]>([]);
  const [pendingLoading, setPendingLoading] = useState(false);
  const [syncingSwapId, setSyncingSwapId] = useState<string | null>(null);
  const [expandedSwapId, setExpandedSwapId] = useState<string | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<SwapRecord | null>(null);

  // ============================================================================
  // Venue selection
  // ============================================================================
  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setStep(id ? "select-aps" : "venue");
    setSelectedOldSerials([]);
    setMappings([]);
    setPreviewPairs([]);
    setApplyResult(null);
    setVenueAps([]);
  };

  // Fetch APs when venue selected
  useEffect(() => {
    if (!venueId || !activeControllerId) return;
    (async () => {
      setLoading(true);
      try {
        const res = await apiFetch(`${API_BASE_URL}/pop-swap/${activeControllerId}/venue/${venueId}/aps`);
        if (res.ok) {
          const data = await res.json();
          const list = Array.isArray(data) ? data : data?.data || [];
          setVenueAps(list);
        }
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [venueId, activeControllerId]);

  // ============================================================================
  // Old AP selection
  // ============================================================================
  const toggleOldSerial = (serial: string) => {
    setSelectedOldSerials((prev) =>
      prev.includes(serial) ? prev.filter((s) => s !== serial) : [...prev, serial]
    );
  };

  const proceedToMapNew = () => {
    setMappings(selectedOldSerials.map((s) => ({ old_serial: s, new_serial: "" })));
    setStep("map-new");
  };

  // ============================================================================
  // New serial mapping
  // ============================================================================
  const updateNewSerial = (index: number, value: string) => {
    setMappings((prev) => prev.map((m, i) => (i === index ? { ...m, new_serial: value } : m)));
  };

  const allMapped = mappings.every((m) => m.new_serial.trim().length > 0);

  // ============================================================================
  // CSV paste support
  // ============================================================================
  const [csvMode, setCsvMode] = useState(false);
  const [csvText, setCsvText] = useState("");

  const parseCsv = () => {
    const lines = csvText.trim().split("\n").filter((l) => l.trim());
    const parsed: SwapMapping[] = [];
    for (const line of lines) {
      const parts = line.split(",").map((p) => p.trim());
      if (parts.length >= 2 && parts[0] && parts[1]) {
        parsed.push({ old_serial: parts[0], new_serial: parts[1] });
      }
    }
    if (parsed.length > 0) {
      setMappings(parsed);
      setSelectedOldSerials(parsed.map((p) => p.old_serial));
      setCsvMode(false);
      setStep("options");
    }
  };

  // ============================================================================
  // Preview
  // ============================================================================
  const runPreview = async () => {
    if (!activeControllerId || !venueId) return;
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/${activeControllerId}/venue/${venueId}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mappings,
          options: { copy_name: copyName, cleanup_action: cleanupAction },
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setPreviewPairs(data.pairs || []);
        setStep("preview");
      } else {
        const err = await res.json();
        setError(err.detail || err.error || "Preview failed");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // ============================================================================
  // Apply
  // ============================================================================
  const runApply = async () => {
    if (!activeControllerId || !venueId) return;
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/${activeControllerId}/venue/${venueId}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mappings,
          options: { copy_name: copyName, cleanup_action: cleanupAction },
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setApplyResult(data);
        setStep("result");
      } else {
        const err = await res.json();
        setError(err.detail || err.error || "Apply failed");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // ============================================================================
  // Pending swaps
  // ============================================================================
  const fetchPendingSwaps = useCallback(async () => {
    setPendingLoading(true);
    try {
      const url = activeControllerId
        ? `${API_BASE_URL}/pop-swap/swaps?controller_id=${activeControllerId}`
        : `${API_BASE_URL}/pop-swap/swaps`;
      const res = await apiFetch(url);
      if (res.ok) {
        setPendingSwaps(await res.json());
      }
    } catch (e: any) {
      console.error("Failed to fetch pending swaps:", e);
    } finally {
      setPendingLoading(false);
    }
  }, [activeControllerId]);

  useEffect(() => {
    if (activeTab === "pending") fetchPendingSwaps();
  }, [activeTab, fetchPendingSwaps]);

  const syncNow = async (swapId: string) => {
    setSyncingSwapId(swapId);
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/swaps/${swapId}/sync-now`, { method: "POST" });
      if (res.ok) {
        await fetchPendingSwaps();
      }
    } catch (e: any) {
      console.error("Sync failed:", e);
    } finally {
      setSyncingSwapId(null);
    }
  };

  const extendSwap = async (swapId: string) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/swaps/${swapId}/extend`, { method: "POST" });
      if (res.ok) {
        await fetchPendingSwaps();
      }
    } catch (e: any) {
      console.error("Extend failed:", e);
    }
  };

  const cancelSwap = async (swapId: string) => {
    if (!confirm("Cancel this swap? The stored config snapshot will be deleted.")) return;
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/swaps/${swapId}`, { method: "DELETE" });
      if (res.ok) {
        await fetchPendingSwaps();
      }
    } catch (e: any) {
      console.error("Cancel failed:", e);
    }
  };

  const viewDetail = async (swapId: string) => {
    if (expandedSwapId === swapId) {
      setExpandedSwapId(null);
      setExpandedDetail(null);
      return;
    }
    try {
      const res = await apiFetch(`${API_BASE_URL}/pop-swap/swaps/${swapId}`);
      if (res.ok) {
        setExpandedDetail(await res.json());
        setExpandedSwapId(swapId);
      }
    } catch (e) {
      console.error("Failed to fetch detail:", e);
    }
  };

  // ============================================================================
  // Helpers
  // ============================================================================
  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      pending: "bg-yellow-100 text-yellow-800",
      syncing: "bg-blue-100 text-blue-800",
      completed: "bg-green-100 text-green-800",
      failed: "bg-red-100 text-red-800",
      expired: "bg-gray-100 text-gray-500",
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] || "bg-gray-100 text-gray-600"}`}>
        {status}
      </span>
    );
  };

  const formatDate = (iso: string) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  const daysUntil = (iso: string) => {
    if (!iso) return "";
    const diff = (new Date(iso).getTime() - Date.now()) / 86400000;
    if (diff < 0) return "expired";
    if (diff < 1) return `${Math.round(diff * 24)}h`;
    return `${Math.round(diff)}d`;
  };

  const resetWizard = () => {
    setStep("venue");
    setSelectedOldSerials([]);
    setMappings([]);
    setPreviewPairs([]);
    setApplyResult(null);
    setError("");
    setVenueId(null);
    setVenueName(null);
  };

  // ============================================================================
  // Render
  // ============================================================================

  if (!activeControllerId) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Pop and Swap</h1>
        <p className="text-gray-500">Select a RuckusONE controller to get started.</p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold mb-1">Pop and Swap</h1>
      <p className="text-gray-500 text-sm mb-4">Replace access points while preserving all configuration</p>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b">
        <button
          onClick={() => setActiveTab("new-swap")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
            activeTab === "new-swap" ? "border-blue-500 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          New Swap
        </button>
        <button
          onClick={() => setActiveTab("pending")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
            activeTab === "pending" ? "border-blue-500 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          Pending Swaps
          {pendingSwaps.filter((s) => s.status === "pending" || s.status === "failed").length > 0 && (
            <span className="ml-1.5 bg-yellow-100 text-yellow-700 text-xs px-1.5 py-0.5 rounded-full">
              {pendingSwaps.filter((s) => s.status === "pending" || s.status === "failed").length}
            </span>
          )}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-4">
          {error}
          <button onClick={() => setError("")} className="float-right font-bold">&times;</button>
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB: New Swap */}
      {/* ============================================================ */}
      {activeTab === "new-swap" && (
        <div>
          {/* Step: Venue Selection */}
          {step === "venue" && (
            <SingleVenueSelector
              controllerId={activeControllerId}
              tenantId={effectiveTenantId}
              onVenueSelect={handleVenueSelect}
              selectedVenueId={venueId}
            />
          )}

          {/* Step: Select Old APs */}
          {step === "select-aps" && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h2 className="text-lg font-semibold">Select APs to Replace</h2>
                  <p className="text-sm text-gray-500">Venue: {venueName} ({venueAps.length} APs)</p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setCsvMode(true); setStep("map-new"); }}
                    className="px-3 py-1.5 text-sm border rounded hover:bg-gray-50"
                  >
                    Paste CSV Instead
                  </button>
                  <button onClick={() => setStep("venue")} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
                    Back
                  </button>
                </div>
              </div>

              {loading ? (
                <p className="text-gray-500">Loading APs...</p>
              ) : (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-left">
                      <tr>
                        <th className="px-3 py-2 w-10"></th>
                        <th className="px-3 py-2">Name</th>
                        <th className="px-3 py-2">Serial</th>
                        <th className="px-3 py-2">Model</th>
                        <th className="px-3 py-2">AP Group</th>
                        <th className="px-3 py-2">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {venueAps.map((ap) => (
                        <tr
                          key={ap.serialNumber}
                          className={`border-t cursor-pointer hover:bg-blue-50 ${
                            selectedOldSerials.includes(ap.serialNumber) ? "bg-blue-50" : ""
                          }`}
                          onClick={() => toggleOldSerial(ap.serialNumber)}
                        >
                          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={selectedOldSerials.includes(ap.serialNumber)}
                              onChange={() => toggleOldSerial(ap.serialNumber)}
                              className="rounded"
                            />
                          </td>
                          <td className="px-3 py-2 font-medium">{ap.name || ap.serialNumber}</td>
                          <td className="px-3 py-2 font-mono text-xs">{ap.serialNumber}</td>
                          <td className="px-3 py-2">{ap.model}</td>
                          <td className="px-3 py-2">{ap.apGroupName}</td>
                          <td className="px-3 py-2">
                            <span className={`text-xs ${ap.status?.toLowerCase() === "online" ? "text-green-600" : "text-gray-400"}`}>
                              {ap.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {selectedOldSerials.length > 0 && (
                <div className="mt-4 flex items-center justify-between">
                  <span className="text-sm text-gray-600">{selectedOldSerials.length} AP(s) selected</span>
                  <button
                    onClick={proceedToMapNew}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm"
                  >
                    Next: Map New APs
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Step: Map New Serials */}
          {step === "map-new" && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold">Map Replacement APs</h2>
                <button onClick={() => { setCsvMode(false); setStep("select-aps"); }} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
                  Back
                </button>
              </div>

              {csvMode ? (
                <div>
                  <p className="text-sm text-gray-500 mb-2">Paste CSV: old_serial,new_serial (one pair per line)</p>
                  <textarea
                    value={csvText}
                    onChange={(e) => setCsvText(e.target.value)}
                    rows={8}
                    className="w-full border rounded p-3 font-mono text-sm"
                    placeholder="OLD_SERIAL_1,NEW_SERIAL_1&#10;OLD_SERIAL_2,NEW_SERIAL_2"
                  />
                  <div className="mt-3 flex justify-end">
                    <button onClick={parseCsv} className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm">
                      Parse CSV
                    </button>
                  </div>
                </div>
              ) : (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-left">
                      <tr>
                        <th className="px-3 py-2">Old AP</th>
                        <th className="px-3 py-2">Old Serial</th>
                        <th className="px-3 py-2 text-center">→</th>
                        <th className="px-3 py-2">New Serial</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mappings.map((m, i) => {
                        const oldAp = venueAps.find((a) => a.serialNumber === m.old_serial);
                        return (
                          <tr key={i} className="border-t">
                            <td className="px-3 py-2">{oldAp?.name || m.old_serial}</td>
                            <td className="px-3 py-2 font-mono text-xs">{m.old_serial}</td>
                            <td className="px-3 py-2 text-center text-gray-400">→</td>
                            <td className="px-3 py-2">
                              <input
                                type="text"
                                value={m.new_serial}
                                onChange={(e) => updateNewSerial(i, e.target.value)}
                                className="border rounded px-2 py-1 w-full font-mono text-sm"
                                placeholder="Enter new serial"
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {!csvMode && allMapped && (
                <div className="mt-4 flex justify-end">
                  <button onClick={() => setStep("options")} className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm">
                    Next: Options
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Step: Options */}
          {step === "options" && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold">Swap Options</h2>
                <button onClick={() => setStep("map-new")} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
                  Back
                </button>
              </div>

              <div className="space-y-4 max-w-lg">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={copyName} onChange={(e) => setCopyName(e.target.checked)} className="rounded" />
                  <span className="text-sm">Copy AP name from old to new</span>
                </label>

                <div>
                  <p className="text-sm font-medium mb-2">Old AP cleanup (after config sync completes):</p>
                  <div className="space-y-1">
                    {(["none", "unassign", "remove"] as const).map((action) => (
                      <label key={action} className="flex items-center gap-2">
                        <input
                          type="radio"
                          name="cleanup"
                          checked={cleanupAction === action}
                          onChange={() => setCleanupAction(action)}
                        />
                        <span className="text-sm">
                          {action === "none" && "Do nothing (leave old AP as-is)"}
                          {action === "unassign" && "Unassign from AP group"}
                          {action === "remove" && "Remove from venue"}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              <div className="mt-6 flex justify-end">
                <button onClick={runPreview} disabled={loading} className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50">
                  {loading ? "Previewing..." : "Preview Swap"}
                </button>
              </div>
            </div>
          )}

          {/* Step: Preview */}
          {step === "preview" && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold">Preview</h2>
                <button onClick={() => setStep("options")} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
                  Back
                </button>
              </div>

              <div className="border rounded-lg overflow-hidden mb-4">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left">
                    <tr>
                      <th className="px-3 py-2">Old AP</th>
                      <th className="px-3 py-2">Model</th>
                      <th className="px-3 py-2">AP Group</th>
                      <th className="px-3 py-2">New Serial</th>
                      <th className="px-3 py-2">Settings</th>
                      <th className="px-3 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewPairs.map((p) => (
                      <tr key={p.old_serial} className="border-t">
                        <td className="px-3 py-2">
                          <div className="font-medium">{p.old_ap_name || p.old_serial}</div>
                          <div className="text-xs text-gray-400 font-mono">{p.old_serial}</div>
                        </td>
                        <td className="px-3 py-2">{p.old_ap_model}</td>
                        <td className="px-3 py-2">{p.old_ap_group_name}</td>
                        <td className="px-3 py-2 font-mono text-xs">{p.new_serial}</td>
                        <td className="px-3 py-2">~{p.settings_count}</td>
                        <td className="px-3 py-2">
                          {p.valid ? (
                            <span className="text-green-600 text-xs">Ready</span>
                          ) : (
                            <span className="text-red-600 text-xs">Invalid</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Warnings */}
              {previewPairs.some((p) => p.warnings.length > 0) && (
                <div className="bg-yellow-50 border border-yellow-200 rounded p-3 mb-4 text-sm">
                  <p className="font-medium text-yellow-800 mb-1">Warnings:</p>
                  <ul className="list-disc list-inside text-yellow-700">
                    {previewPairs.flatMap((p) => p.warnings.map((w, i) => (
                      <li key={`${p.old_serial}-${i}`}>{p.old_ap_name || p.old_serial}: {w}</li>
                    )))}
                  </ul>
                </div>
              )}

              {/* Errors */}
              {previewPairs.some((p) => p.errors.length > 0) && (
                <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-sm">
                  <p className="font-medium text-red-800 mb-1">Errors:</p>
                  <ul className="list-disc list-inside text-red-700">
                    {previewPairs.flatMap((p) => p.errors.map((e, i) => (
                      <li key={`${p.old_serial}-${i}`}>{p.old_serial}: {e}</li>
                    )))}
                  </ul>
                </div>
              )}

              <div className="flex justify-end gap-3">
                <button onClick={() => setStep("options")} className="px-4 py-2 border rounded text-sm hover:bg-gray-50">
                  Back
                </button>
                <button
                  onClick={runApply}
                  disabled={loading || previewPairs.every((p) => !p.valid)}
                  className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
                >
                  {loading ? "Executing..." : `Execute Swap (${previewPairs.filter((p) => p.valid).length} pairs)`}
                </button>
              </div>
            </div>
          )}

          {/* Step: Result */}
          {step === "result" && applyResult && (
            <div>
              <h2 className="text-lg font-semibold mb-3">Swap Initiated</h2>

              <div className="bg-green-50 border border-green-200 rounded p-4 mb-4">
                <p className="text-green-800 font-medium">
                  {applyResult.total_created} swap(s) created successfully.
                </p>
                <p className="text-green-700 text-sm mt-1">
                  Config snapshots captured and new APs assigned. The background poller will apply
                  settings when new APs come online (checked every 30 minutes, 7-day window).
                </p>
              </div>

              {applyResult.results && (
                <div className="border rounded-lg overflow-hidden mb-4">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-left">
                      <tr>
                        <th className="px-3 py-2">Old Serial</th>
                        <th className="px-3 py-2">New Serial</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Settings</th>
                      </tr>
                    </thead>
                    <tbody>
                      {applyResult.results.map((r: any) => (
                        <tr key={r.old_serial} className="border-t">
                          <td className="px-3 py-2 font-mono text-xs">{r.old_serial}</td>
                          <td className="px-3 py-2 font-mono text-xs">{r.new_serial}</td>
                          <td className="px-3 py-2">{statusBadge(r.status)}</td>
                          <td className="px-3 py-2 text-xs">
                            {r.settings_captured != null && `${r.settings_captured} captured`}
                            {r.message && <span className="text-red-600 ml-2">{r.message}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="flex gap-3">
                <button onClick={resetWizard} className="px-4 py-2 border rounded text-sm hover:bg-gray-50">
                  Start New Swap
                </button>
                <button
                  onClick={() => { setActiveTab("pending"); fetchPendingSwaps(); }}
                  className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm"
                >
                  View Pending Swaps
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB: Pending Swaps */}
      {/* ============================================================ */}
      {activeTab === "pending" && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Pending Swaps</h2>
            <button
              onClick={fetchPendingSwaps}
              disabled={pendingLoading}
              className="px-3 py-1.5 text-sm border rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {pendingLoading ? "Refreshing..." : "Refresh"}
            </button>
          </div>

          {pendingSwaps.length === 0 ? (
            <p className="text-gray-500 text-sm">No swap records found.</p>
          ) : (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left">
                  <tr>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Old AP</th>
                    <th className="px-3 py-2">New AP</th>
                    <th className="px-3 py-2">AP Group</th>
                    <th className="px-3 py-2">Created</th>
                    <th className="px-3 py-2">Expires</th>
                    <th className="px-3 py-2">Attempts</th>
                    <th className="px-3 py-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingSwaps.map((swap) => (
                    <>
                      <tr key={swap.swap_id} className="border-t">
                        <td className="px-3 py-2">{statusBadge(swap.status)}</td>
                        <td className="px-3 py-2">
                          <div className="font-medium">{swap.ap_name || swap.old_serial}</div>
                          <div className="text-xs text-gray-400 font-mono">{swap.old_serial}</div>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">{swap.new_serial}</td>
                        <td className="px-3 py-2">{swap.ap_group_name}</td>
                        <td className="px-3 py-2 text-xs">{formatDate(swap.created_at)}</td>
                        <td className="px-3 py-2 text-xs">
                          {swap.status === "completed" ? "—" : (
                            <span className={daysUntil(swap.expires_at) === "expired" ? "text-red-500" : ""}>
                              {formatDate(swap.expires_at)} ({daysUntil(swap.expires_at)})
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-center">{swap.sync_attempts}</td>
                        <td className="px-3 py-2">
                          <div className="flex gap-1 flex-wrap">
                            {(swap.status === "pending" || swap.status === "failed") && (
                              <>
                                <button
                                  onClick={() => syncNow(swap.swap_id)}
                                  disabled={syncingSwapId === swap.swap_id}
                                  className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                                >
                                  {syncingSwapId === swap.swap_id ? "Syncing..." : "Sync Now"}
                                </button>
                                <button
                                  onClick={() => extendSwap(swap.swap_id)}
                                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                                >
                                  Extend
                                </button>
                                <button
                                  onClick={() => cancelSwap(swap.swap_id)}
                                  className="px-2 py-1 text-xs text-red-600 border border-red-200 rounded hover:bg-red-50"
                                >
                                  Cancel
                                </button>
                              </>
                            )}
                            <button
                              onClick={() => viewDetail(swap.swap_id)}
                              className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                            >
                              {expandedSwapId === swap.swap_id ? "Hide" : "Details"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {/* Expanded detail row */}
                      {expandedSwapId === swap.swap_id && expandedDetail && (
                        <tr key={`${swap.swap_id}-detail`} className="bg-gray-50">
                          <td colSpan={8} className="px-4 py-3">
                            <div className="space-y-3">
                              {/* Apply results */}
                              {expandedDetail.apply_results && (
                                <div>
                                  <h4 className="text-xs font-semibold text-gray-600 uppercase mb-1">Apply Results</h4>
                                  <div className="grid grid-cols-2 md:grid-cols-3 gap-1">
                                    {Object.entries(expandedDetail.apply_results).map(([key, val]) => (
                                      <div key={key} className="text-xs">
                                        <span className={val === "success" ? "text-green-600" : "text-red-600"}>
                                          {val === "success" ? "✓" : "✗"}
                                        </span>{" "}
                                        {key.replace(/_/g, " ")}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                              {/* Config snapshot summary */}
                              {expandedDetail.config_data && (
                                <div>
                                  <h4 className="text-xs font-semibold text-gray-600 uppercase mb-1">Config Snapshot</h4>
                                  <p className="text-xs text-gray-500">
                                    {expandedDetail.config_data.captured_settings
                                      ? `${(expandedDetail.config_data.captured_settings as string[]).length} settings captured`
                                      : "Config data stored"}
                                    {expandedDetail.config_data.ap_model ? ` | Model: ${String(expandedDetail.config_data.ap_model)}` : null}
                                  </p>
                                </div>
                              )}
                              <div className="text-xs text-gray-400">
                                Swap ID: {swap.swap_id}
                                {swap.applied_at && ` | Applied: ${formatDate(swap.applied_at)}`}
                                {swap.last_attempt_at && ` | Last attempt: ${formatDate(swap.last_attempt_at)}`}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default APPopAndSwap;
