import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import WorkflowGraph from "@/components/WorkflowGraph";
import { ChevronDown, ChevronRight, AlertTriangle, Loader } from "lucide-react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ==================== Types ====================

interface ResourceItem {
  id: string;
  name?: string;
  username?: string;
  pool_id?: string;
  group_id?: string;
}

interface ResourceInventory {
  passphrases: ResourceItem[];
  dpsk_pools: ResourceItem[];
  identities: ResourceItem[];
  identity_groups: ResourceItem[];
  wifi_networks: ResourceItem[];
  ap_groups: ResourceItem[];
}

interface PlanResult {
  job_id: string;
  status: string;
  inventory: ResourceInventory | null;
  total_resources: number;
}

// ==================== Constants ====================

const CATEGORIES = [
  { key: "passphrases", label: "DPSK Passphrases", icon: "🔑" },
  { key: "dpsk_pools", label: "DPSK Pools (Services)", icon: "📦" },
  { key: "identities", label: "Identities", icon: "👤" },
  { key: "identity_groups", label: "Identity Groups", icon: "👥" },
  { key: "wifi_networks", label: "WiFi Networks", icon: "📡" },
  { key: "ap_groups", label: "AP Groups", icon: "📶" },
] as const;

type CategoryKey = (typeof CATEGORIES)[number]["key"];

const POLL_INTERVAL = 2000;
const MAX_POLL_TIME = 5 * 60 * 1000;

// ==================== Component ====================

export default function DangerZone() {
  const {
    activeControllerId,
    activeControllerSubtype,
    controllers,
  } = useAuth();

  // Venue selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Options
  const [namePattern, setNamePattern] = useState("");
  const [allNetworks, setAllNetworks] = useState(true);  // Default to all networks

  // Scan state
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [planResult, setPlanResult] = useState<PlanResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // Inventory display
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [selectedCategories, setSelectedCategories] = useState<Set<CategoryKey>>(
    new Set(CATEGORIES.map((c) => c.key))
  );

  // Execution
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [monitorJobId, setMonitorJobId] = useState<string | null>(null);

  // Graph
  const [showGraph, setShowGraph] = useState(false);

  // Polling ref
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  // Controller/tenant logic
  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  const effectiveTenantId =
    activeControllerSubtype === "MSP"
      ? null
      : activeController?.r1_tenant_id || null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  // Reset state when venue changes
  useEffect(() => {
    setPlanResult(null);
    setJobId(null);
    setScanError(null);
    setConfirmError(null);
    setExpandedCategories(new Set());
    setSelectedCategories(new Set(CATEGORIES.map((c) => c.key)));
  }, [venueId]);

  // ==================== Handlers ====================

  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
  };

  const handleScan = async () => {
    if (!activeControllerId) return;

    setScanning(true);
    setScanError(null);
    setPlanResult(null);
    setConfirmError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/cleanup/v2/plan`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId || null,
          tenant_id: effectiveTenantId,
          nuclear_mode: true,
          name_pattern: namePattern || null,
          all_networks: allNetworks,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.detail || `Scan failed: ${response.status}`);
      }

      const data = await response.json();
      setJobId(data.job_id);

      // Start polling
      pollPlan(data.job_id);
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Failed to start scan");
      setScanning(false);
    }
  };

  const pollPlan = useCallback((id: string) => {
    const startTime = Date.now();

    const doPoll = async () => {
      if (!mountedRef.current) return;

      if (Date.now() - startTime > MAX_POLL_TIME) {
        setScanError("Scan timed out after 5 minutes");
        setScanning(false);
        return;
      }

      try {
        const response = await fetch(`${API_BASE_URL}/cleanup/v2/${id}/plan`, {
          credentials: "include",
        });

        if (!response.ok) throw new Error(`Poll failed: ${response.status}`);

        const data: PlanResult = await response.json();
        if (!mountedRef.current) return;

        setPlanResult(data);

        if (data.status === "VALIDATING") {
          pollRef.current = setTimeout(doPoll, POLL_INTERVAL);
        } else {
          setScanning(false);
        }
      } catch (err) {
        if (!mountedRef.current) return;
        setScanError(err instanceof Error ? err.message : "Poll failed");
        setScanning(false);
      }
    };

    doPoll();
  }, []);

  const handleConfirm = async () => {
    if (!jobId) return;

    setConfirming(true);
    setConfirmError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/cleanup/v2/${jobId}/confirm`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_categories: Array.from(selectedCategories),
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.detail || `Confirm failed: ${response.status}`);
      }

      setMonitorJobId(jobId);
      setShowJobModal(true);
    } catch (err) {
      setConfirmError(err instanceof Error ? err.message : "Confirm failed");
    } finally {
      setConfirming(false);
    }
  };

  const handleCloseJobModal = () => {
    setShowJobModal(false);
    // Reset for a fresh scan
    setPlanResult(null);
    setJobId(null);
  };

  const toggleCategory = (key: string) => {
    const next = new Set(expandedCategories);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setExpandedCategories(next);
  };

  const toggleCategorySelection = (key: CategoryKey) => {
    const next = new Set(selectedCategories);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelectedCategories(next);
  };

  const getItemDisplay = (item: ResourceItem, category: string): string => {
    if (category === "passphrases" || category === "identities") {
      return item.username || item.name || item.id;
    }
    return item.name || item.id;
  };

  // ==================== Derived ====================

  const inventory = planResult?.inventory;
  const totalResources = planResult?.total_resources || 0;
  const isReady = planResult?.status === "AWAITING_CONFIRMATION" && inventory;
  const isEmpty = isReady && totalResources === 0;

  const selectedResourceCount = inventory
    ? CATEGORIES.reduce((sum, { key }) => {
        if (!selectedCategories.has(key)) return sum;
        return sum + ((inventory[key as CategoryKey] || []) as ResourceItem[]).length;
      }, 0)
    : 0;

  // ==================== Render ====================

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <span className="text-3xl">☢️</span>
          <h1 className="text-3xl font-bold text-gray-900">Danger Zone</h1>
        </div>
        <p className="text-gray-500 text-sm ml-12">
          Scan and delete DPSK resources from a venue. Uses the V2 workflow engine
          with plan/confirm flow — nothing is deleted until you confirm.
        </p>
      </div>

      {/* Venue Selection (Optional) */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 mb-6">
        <h2 className="text-lg font-semibold text-gray-800 mb-2">Select Venue (Optional)</h2>
        <p className="text-gray-500 text-sm mb-4">
          Select a venue to filter AP groups. Leave empty to scan tenant-wide resources only.
        </p>

        {!activeControllerId ? (
          <div className="text-gray-500 text-sm p-4 bg-gray-50 rounded">
            Select a controller from the top menu first.
          </div>
        ) : (
          <SingleVenueSelector
            controllerId={activeControllerId}
            tenantId={effectiveTenantId}
            onVenueSelect={handleVenueSelect}
            selectedVenueId={venueId}
          />
        )}

        {venueId && venueName && (
          <div className="mt-3 p-3 bg-blue-50 border border-blue-200 rounded text-sm text-blue-800">
            <strong>Selected:</strong> {venueName} <span className="text-blue-500 text-xs">({venueId})</span>
          </div>
        )}
      </div>

      {/* Options + Scan */}
      {activeControllerId && (
        <div className="bg-gray-900 rounded-lg border-2 border-red-600 shadow-lg p-6 mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <AlertTriangle className="text-red-400" size={20} />
            Resource Scan
          </h2>

          {/* Name pattern filter */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Name Pattern Filter (optional regex)
            </label>
            <input
              type="text"
              value={namePattern}
              onChange={(e) => setNamePattern(e.target.value)}
              placeholder="e.g. ^Unit-.*-DPSK$"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white text-sm placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
            />
            <p className="text-xs text-gray-500 mt-1">
              Only resources whose names match this regex will be included.
              Leave blank to scan all resources.
            </p>
          </div>

          {/* All networks toggle */}
          <div className="mb-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={allNetworks}
                onChange={(e) => setAllNetworks(e.target.checked)}
                className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-red-500 focus:ring-red-500"
              />
              <span className="text-sm font-medium text-gray-300">
                Include all tenant WiFi networks
              </span>
            </label>
            <p className="text-xs text-gray-500 mt-1 ml-6">
              {allNetworks
                ? "All tenant-level WiFi networks will be scanned (recommended)."
                : "Only networks activated on the selected venue will be included."}
            </p>
          </div>

          {/* Scan button */}
          <button
            onClick={handleScan}
            disabled={scanning || !activeControllerId}
            className={`w-full py-3 rounded-lg font-semibold text-sm transition-all ${
              scanning || !activeControllerId
                ? "bg-gray-700 text-gray-400 cursor-not-allowed"
                : "bg-gradient-to-r from-red-600 to-orange-600 hover:from-red-700 hover:to-orange-700 text-white shadow-lg hover:shadow-red-500/30"
            }`}
          >
            {scanning ? (
              <span className="flex items-center justify-center gap-2">
                <Loader className="animate-spin" size={16} />
                Scanning venue for resources...
              </span>
            ) : (
              "☢️ Scan for Resources"
            )}
          </button>

          {scanError && (
            <div className="mt-4 p-3 bg-red-900/50 border border-red-600 rounded text-red-300 text-sm">
              {scanError}
            </div>
          )}
        </div>
      )}

      {/* Inventory Results */}
      {isReady && !isEmpty && inventory && (
        <div className="bg-gray-900 rounded-lg border-2 border-orange-600 shadow-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <span className="text-2xl">📋</span>
              Resource Inventory
            </h2>
            <span className="px-3 py-1 bg-red-600 text-white text-sm font-bold rounded-full">
              {selectedResourceCount === totalResources
                ? `${totalResources} total`
                : `${selectedResourceCount} / ${totalResources} selected`}
            </span>
          </div>

          {/* Categories */}
          <div className="space-y-2">
            {CATEGORIES.map(({ key, label, icon }) => {
              const items = (inventory[key as CategoryKey] || []) as ResourceItem[];
              const isExpanded = expandedCategories.has(key);
              const isSelected = selectedCategories.has(key);

              return (
                <div
                  key={key}
                  className={`border rounded-lg overflow-hidden ${
                    !isSelected
                      ? "border-gray-700 bg-gray-800/30 opacity-50"
                      : items.length > 0
                      ? "border-red-700 bg-red-900/20"
                      : "border-gray-700 bg-gray-800/30"
                  }`}
                >
                  <div className="flex items-center p-3 gap-3">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleCategorySelection(key)}
                      disabled={items.length === 0}
                      className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-red-500 focus:ring-red-500 flex-shrink-0 cursor-pointer disabled:opacity-30 disabled:cursor-default"
                    />
                    <button
                      onClick={() => toggleCategory(key)}
                      className="flex items-center gap-3 text-left hover:bg-white/5 transition-colors flex-1 -my-3 py-3"
                    >
                      {isExpanded ? (
                        <ChevronDown className="text-orange-500 flex-shrink-0" size={18} />
                      ) : (
                        <ChevronRight className="text-gray-500 flex-shrink-0" size={18} />
                      )}
                      <span className="text-xl">{icon}</span>
                      <span className={`font-medium flex-1 ${isSelected ? "text-white" : "text-gray-500 line-through"}`}>{label}</span>
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                          items.length > 0 && isSelected
                            ? "bg-red-600 text-white"
                            : "bg-gray-700 text-gray-400"
                        }`}
                      >
                        {items.length}
                      </span>
                    </button>
                  </div>

                  {isExpanded && items.length > 0 && (
                    <div className="border-t border-gray-700 bg-gray-900/50 p-3 max-h-56 overflow-y-auto">
                      <ul className="space-y-1">
                        {items.map((item, idx) => (
                          <li
                            key={item.id || idx}
                            className="text-gray-300 text-xs pl-3 py-1 border-l-2 border-gray-700 hover:border-orange-500 hover:text-white transition-colors font-mono"
                          >
                            {getItemDisplay(item, key)}
                            {key === "passphrases" && item.pool_id && (
                              <span className="text-gray-600 ml-2">
                                pool:{item.pool_id.substring(0, 8)}...
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {isExpanded && items.length === 0 && (
                    <div className="border-t border-gray-700 bg-gray-900/50 p-3">
                      <p className="text-gray-600 text-xs text-center">None found</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Workflow Graph Toggle */}
          <div className="mt-4 border border-gray-700 rounded-lg overflow-hidden">
            <button
              onClick={() => setShowGraph(!showGraph)}
              className="w-full px-4 py-2 flex items-center justify-between bg-gray-800 hover:bg-gray-700 transition-colors"
            >
              <span className="text-sm font-medium text-gray-300">
                {showGraph ? "▼" : "▶"} Cleanup Workflow Graph
              </span>
              <span className="text-xs text-gray-500">venue_cleanup</span>
            </button>
            {showGraph && (
              <WorkflowGraph workflowName="venue_cleanup" height={280} compact />
            )}
          </div>

          {/* Warning + Confirm */}
          <div className="mt-6 p-4 bg-red-900/30 border-2 border-red-600 rounded-lg">
            <div className="flex items-start gap-3">
              <AlertTriangle className="text-red-400 flex-shrink-0 mt-0.5" size={22} />
              <div className="text-red-200 text-sm">
                <p className="font-semibold mb-1">This action cannot be undone.</p>
                <p>
                  Confirming will permanently delete{" "}
                  <span className="font-bold text-red-100">{selectedResourceCount} resources</span>
                  {selectedResourceCount < totalResources && (
                    <span className="text-red-300"> (of {totalResources} found)</span>
                  )}
                  {venueName ? ` from ${venueName}` : ""}.
                  Uncheck categories above to exclude them.
                </p>
              </div>
            </div>
          </div>

          {confirmError && (
            <div className="mt-3 p-3 bg-red-900/50 border border-red-600 rounded text-red-300 text-sm">
              {confirmError}
            </div>
          )}

          <div className="mt-4 flex gap-3 justify-end">
            <button
              onClick={() => {
                setPlanResult(null);
                setJobId(null);
              }}
              className="px-5 py-2.5 bg-gray-700 hover:bg-gray-600 text-white rounded-lg font-medium text-sm transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={confirming || selectedResourceCount === 0}
              className={`px-6 py-2.5 rounded-lg font-semibold text-sm transition-all flex items-center gap-2 ${
                confirming || selectedResourceCount === 0
                  ? "bg-gray-600 text-gray-400 cursor-not-allowed"
                  : "bg-gradient-to-r from-red-600 to-orange-600 hover:from-red-700 hover:to-orange-700 text-white shadow-lg hover:shadow-red-500/50"
              }`}
            >
              <span>☢️</span>
              {confirming
                ? "Confirming..."
                : `Delete ${selectedResourceCount} Resources`}
            </button>
          </div>
        </div>
      )}

      {/* Empty inventory */}
      {isEmpty && (
        <div className="bg-gray-50 rounded-lg border border-gray-200 p-8 text-center mb-6">
          <div className="text-4xl mb-3">✅</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-1">No resources found</h3>
          <p className="text-gray-500 text-sm">
            {namePattern
              ? `No resources matching "${namePattern}" in this venue.`
              : "This venue has no DPSK resources to clean up."}
          </p>
        </div>
      )}

      {/* Job Monitor Modal */}
      {monitorJobId && (
        <JobMonitorModal
          jobId={monitorJobId}
          isOpen={showJobModal}
          onClose={handleCloseJobModal}
        />
      )}
    </div>
  );
}
