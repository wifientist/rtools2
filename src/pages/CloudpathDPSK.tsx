import { useState, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";
import NuclearCleanupModal from "@/components/NuclearCleanupModal";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface DPSKData {
  guid: string;
  name: string;
  passphrase: string;
  status: string;
  vlanid?: string;
  expirationDateTime?: string;
  useDeviceCountLimit?: boolean;
  deviceCountLimit?: number;
}

interface AuditData {
  venue_id: string;
  venue_name: string;
  total_ssids: number;
  total_dpsk_ssids: number;
  total_dpsk_pools: number;
  total_identity_groups: number;
  ssids: any[];
  identity_groups: any[];
  dpsk_pools: any[];
}

interface DPSKSsidInfo {
  id: string;
  name: string;
  ssid: string;
  dpsk_pool_ids: string[];
}

interface CloudpathPoolMetadata {
  guid?: string;
  displayName?: string;
  description?: string;
  phraseDefaultLength?: number;
  phraseRandomCharactersType?: string;
  ssidList?: string[];
  enforceDeviceCountLimit?: boolean;
  deviceCountLimit?: number;
  enforceExpirationDate?: boolean;
}

interface CloudpathExportMetadata {
  extracted_at?: string;
  cloudpath_fqdn?: string;
  extractor_version?: string;
}

function CloudpathDPSK() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  // JSON Upload - supports both legacy array and new nested format
  const [jsonData, setJsonData] = useState<DPSKData[] | Record<string, any> | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [poolMetadata, setPoolMetadata] = useState<CloudpathPoolMetadata | null>(null);
  const [exportMetadata, setExportMetadata] = useState<CloudpathExportMetadata | null>(null);

  // Venue Selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Migration Options
  const [justCopyDPSKs, setJustCopyDPSKs] = useState(true);
  const [groupByVlan, setGroupByVlan] = useState(false);
  const [includeAdaptivePolicySets, setIncludeAdaptivePolicySets] = useState(false);
  const [simulateDelay, setSimulateDelay] = useState(false);
  const [expiredDpskHandling, setExpiredDpskHandling] = useState<"no_expiration" | "renew" | "skip">("no_expiration");
  const [identityGroupName, setIdentityGroupName] = useState("");
  const [dpskServiceName, setDpskServiceName] = useState("");
  const [ssidMode, setSsidMode] = useState<"none" | "create_new" | "link_existing">("none");
  const [selectedSsidId, setSelectedSsidId] = useState<string | null>(null);

  // Parallel execution options
  const [parallelExecution, setParallelExecution] = useState(true);
  const [maxConcurrent, setMaxConcurrent] = useState(10);

  // DPSK SSID options for linking
  const [dpskSsids, setDpskSsids] = useState<DPSKSsidInfo[]>([]);
  const [loadingDpskSsids, setLoadingDpskSsids] = useState(false);

  // Job monitoring modal
  const [showJobModal, setShowJobModal] = useState(false);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");
  const [lastJobResult, setLastJobResult] = useState<JobResult | null>(null);

  // Audit functionality
  const [showAuditModal, setShowAuditModal] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [auditError, setAuditError] = useState("");

  // Identity export modal
  const [showIdentityExportModal, setShowIdentityExportModal] = useState(false);
  const [identityExportLoading, setIdentityExportLoading] = useState(false);
  const [identityExportData, setIdentityExportData] = useState<any[] | null>(null);
  const [exportingCsv, setExportingCsv] = useState(false);
  const [identityPoolFilters, setIdentityPoolFilters] = useState<Set<string>>(new Set());
  const [poolSearchText, setPoolSearchText] = useState("");
  const [identityPage, setIdentityPage] = useState(1);
  const IDENTITY_PAGE_SIZE = 100;

  // Nuclear cleanup modal
  const [showNuclearModal, setShowNuclearModal] = useState(false);

  // Determine tenant ID (for MSP, it's null until explicitly set; for EC, use r1_tenant_id)
  const activeController = controllers.find(c => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null
    : (activeController?.r1_tenant_id || null);

  // Compute unique DPSK pools from identity export data for filtering
  const uniqueIdentityPools = useMemo(() => {
    if (!identityExportData) return [];
    const poolMap = new Map<string, { id: string; name: string }>();
    for (const row of identityExportData) {
      if (row.dpsk_pool_id && !poolMap.has(row.dpsk_pool_id)) {
        poolMap.set(row.dpsk_pool_id, {
          id: row.dpsk_pool_id,
          name: row.dpsk_pool_name || row.dpsk_pool_id
        });
      }
    }
    return Array.from(poolMap.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [identityExportData]);

  // Filter identity export data by selected pools (empty set = show all)
  const filteredIdentityData = useMemo(() => {
    if (!identityExportData) return [];
    if (identityPoolFilters.size === 0) return identityExportData;
    return identityExportData.filter(row => identityPoolFilters.has(row.dpsk_pool_id));
  }, [identityExportData, identityPoolFilters]);

  // Filter pools by search text for the pool selector
  const searchFilteredPools = useMemo(() => {
    if (!poolSearchText.trim()) return uniqueIdentityPools;
    const search = poolSearchText.toLowerCase();
    return uniqueIdentityPools.filter(pool =>
      pool.name.toLowerCase().includes(search) ||
      pool.id.toLowerCase().includes(search)
    );
  }, [uniqueIdentityPools, poolSearchText]);

  // Pagination for identity table
  const totalIdentityPages = Math.ceil(filteredIdentityData.length / IDENTITY_PAGE_SIZE);
  const paginatedIdentityData = useMemo(() => {
    const start = (identityPage - 1) * IDENTITY_PAGE_SIZE;
    return filteredIdentityData.slice(start, start + IDENTITY_PAGE_SIZE);
  }, [filteredIdentityData, identityPage]);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadError("");
    setPoolMetadata(null);
    setExportMetadata(null);
    const reader = new FileReader();

    reader.onload = (event) => {
      try {
        let text = event.target?.result as string;
        console.log("File read - length:", text?.length, "first 100 chars:", text?.substring(0, 100));

        if (!text || text.length === 0) {
          setUploadError("File appears to be empty");
          setJsonData(null);
          return;
        }

        // Remove BOM if present (common in files saved from some editors)
        if (text.charCodeAt(0) === 0xFEFF) {
          text = text.slice(1);
        }
        const data = JSON.parse(text);

        // Detect format: new nested format has 'dpsks' key, legacy is just an array
        if (Array.isArray(data)) {
          // Legacy flat array format
          setJsonData(data);
          setPoolMetadata(null);
          setExportMetadata(null);
        } else if (typeof data === 'object' && data.dpsks) {
          // New nested format with pool metadata
          setJsonData(data);  // Pass the whole object to backend
          setPoolMetadata(data.pool || null);
          setExportMetadata(data.metadata || null);

          // Pre-populate names from pool displayName if not already set
          if (data.pool?.displayName) {
            if (!identityGroupName) {
              setIdentityGroupName(data.pool.displayName);
            }
            if (!dpskServiceName) {
              setDpskServiceName(data.pool.displayName);
            }
          }
        } else {
          setUploadError("Invalid format: Expected an array of DPSK objects or a Cloudpath export with 'dpsks' key");
          setJsonData(null);
          return;
        }
      } catch (err: any) {
        console.error("JSON parse error:", err);
        setUploadError(`Invalid JSON file: ${err.message || "Please check the file format."}`);
        setJsonData(null);
      }
    };

    reader.onerror = () => {
      setUploadError("Failed to read file");
      setJsonData(null);
    };

    reader.readAsText(file);
  };

  const handleVenueSelect = (selectedVenueId: string | null, venue: any) => {
    setVenueId(selectedVenueId);
    setVenueName(venue?.name || null);
    // Reset SSID selection when venue changes
    setSelectedSsidId(null);
    setDpskSsids([]);
  };

  // Fetch DPSK-enabled SSIDs for the selected venue
  const fetchDpskSsids = async () => {
    if (!venueId || !activeControllerId) return;

    setLoadingDpskSsids(true);
    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/dpsk-ssids`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to fetch DPSK SSIDs");
      }

      const data = await response.json();
      setDpskSsids(data.dpsk_ssids || []);
    } catch (err: any) {
      console.error("Error fetching DPSK SSIDs:", err);
      setDpskSsids([]);
    } finally {
      setLoadingDpskSsids(false);
    }
  };

  const handleProcess = async () => {
    if (!jsonData) {
      setError("Please upload a JSON file first");
      return;
    }

    if (!venueId) {
      setError("Please select a venue");
      return;
    }

    if (!activeControllerId) {
      setError("Please select an active controller first");
      return;
    }

    setProcessing(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/import`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          dpsk_data: jsonData,
          options: {
            just_copy_dpsks: justCopyDPSKs,
            group_by_vlan: groupByVlan,
            include_adaptive_policy_sets: includeAdaptivePolicySets,
            skip_expired_dpsks: expiredDpskHandling === "skip",
            renew_expired_dpsks: expiredDpskHandling === "renew",
            simulate_delay: simulateDelay,
            identity_group_name: identityGroupName.trim() || null,
            dpsk_service_name: dpskServiceName.trim() || null,
            ssid_mode: ssidMode,
            link_to_ssid_id: ssidMode === "link_existing" ? selectedSsidId : null,
          },
          parallel_execution: parallelExecution,
          max_concurrent: maxConcurrent,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Import failed");
      }

      const result = await response.json();
      const newJobId = result.job_id;

      // Open job monitoring modal
      setCurrentJobId(newJobId);
      setShowJobModal(true);
      setProcessing(false);

    } catch (err: any) {
      console.error("Processing error:", err);
      setError(err.message || "An error occurred");
      setProcessing(false);
    }
  };

  const handleAuditVenue = async () => {
    if (!venueId) {
      setAuditError("Please select a venue");
      return;
    }

    if (!activeControllerId) {
      setAuditError("Please select an active controller first");
      return;
    }

    setAuditLoading(true);
    setAuditError("");
    setAuditData(null);

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/audit`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Audit failed");
      }

      const data = await response.json();
      setAuditData(data);
      setShowAuditModal(true);
    } catch (err: any) {
      console.error("Audit error:", err);
      setAuditError(err.message || "An error occurred during audit");
    } finally {
      setAuditLoading(false);
    }
  };

  const handleViewIdentities = async () => {
    if (!activeControllerId) {
      setAuditError("Please select an active controller first");
      return;
    }

    setIdentityExportLoading(true);
    setAuditError("");

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/export-identities`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId || null,  // Optional - pass null if not selected
          tenant_id: effectiveTenantId,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to fetch identities");
      }

      const result = await response.json();
      setIdentityExportData(result.data);

      // Auto-select recently created pools if available from last job
      const createdPools = lastJobResult?.created_resources?.dpsk_pools;
      if (createdPools && createdPools.length > 0) {
        const poolIds = createdPools
          .map((p: any) => p.dpsk_pool_id)
          .filter((id: string) => id);
        setIdentityPoolFilters(new Set(poolIds));
      } else {
        setIdentityPoolFilters(new Set());  // Show all if no recent job
      }

      setPoolSearchText("");  // Reset pool search
      setIdentityPage(1);  // Reset to first page
      setShowIdentityExportModal(true);
    } catch (err: any) {
      console.error("Fetch identities error:", err);
      setAuditError(err.message || "An error occurred fetching identities");
    } finally {
      setIdentityExportLoading(false);
    }
  };

  const handleDownloadCsv = () => {
    if (!filteredIdentityData || filteredIdentityData.length === 0) return;

    // Build CSV content using filtered data
    const headers = ['dpsk_pool_name', 'dpsk_pool_id', 'username', 'passphrase', 'cloudpath_guid', 'identity_id', 'passphrase_id', 'identity_group_name'];
    const csvRows = [
      headers.join(','),
      ...filteredIdentityData.map(row =>
        headers.map(h => {
          const val = row[h] || '';
          // Escape quotes and wrap in quotes if contains comma
          if (val.includes(',') || val.includes('"') || val.includes('\n')) {
            return `"${val.replace(/"/g, '""')}"`;
          }
          return val;
        }).join(',')
      )
    ];
    const csvContent = csvRows.join('\n');

    // Trigger download - include venue and pool info in filename
    const venuePart = venueId ? `_${venueId}` : '_all';
    const poolSuffix = identityPoolFilters.size > 0
      ? `_${identityPoolFilters.size}pools`
      : "";
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `identities_export${venuePart}${poolSuffix}.csv`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  };

  const handleCloseJobModal = () => {
    setShowJobModal(false);
    // Optionally reset currentJobId after a delay to allow modal animation
    setTimeout(() => setCurrentJobId(null), 300);
  };

  const handleJobComplete = (result: JobResult) => {
    setLastJobResult(result);
  };

  const handleCleanup = async (jobId: string) => {
    if (!confirm('This will delete all resources created by this migration. This cannot be undone. Continue?')) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/cleanup`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: jobId,
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          nuclear: false,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Cleanup failed');
      }

      const result = await response.json();

      // Close current job modal and open cleanup job modal
      setShowJobModal(false);
      setTimeout(() => {
        setCurrentJobId(result.cleanup_job_id);
        setShowJobModal(true);
      }, 300);

    } catch (err: any) {
      console.error('Cleanup error:', err);
      alert(`Cleanup failed: ${err.message}`);
    }
  };

  const handleNuclearCleanup = () => {
    // Show the nuclear cleanup modal
    setShowNuclearModal(true);
  };

  const handleConfirmNuclearCleanup = async () => {
    // Close modal and proceed with cleanup
    setShowNuclearModal(false);

    try {
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/cleanup`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          tenant_id: effectiveTenantId,
          nuclear: true,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Nuclear cleanup failed');
      }

      const result = await response.json();

      // Open cleanup job modal
      setCurrentJobId(result.cleanup_job_id);
      setShowJobModal(true);

    } catch (err: any) {
      console.error('Nuclear cleanup error:', err);
      alert(`Nuclear cleanup failed: ${err.message}`);
    }
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Cloudpath DPSK Migration</h2>

      <p className="text-gray-600 mb-6">
        Migrate Dynamic Pre-Shared Keys (DPSKs) from Cloudpath to RuckusONE
      </p>

      {/* Step 1: Upload JSON */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 1: Upload Cloudpath Export</h3>

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Upload Cloudpath export file (.json or .txt):
          </label>
          <input
            type="file"
            accept=".json,.txt,application/json,text/plain"
            onChange={handleFileUpload}
            disabled={processing}
            className="block w-full text-sm text-gray-500
              file:mr-4 file:py-2 file:px-4
              file:rounded-md file:border-0
              file:text-sm file:font-semibold
              file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100
              disabled:opacity-50"
          />
        </div>

        {uploadError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {uploadError}
          </div>
        )}

        {jsonData && (
          <div className="p-3 bg-green-50 border border-green-200 rounded text-green-800 text-sm space-y-2">
            <div>
              ✅ Loaded {Array.isArray(jsonData) ? jsonData.length : (jsonData.dpsks?.length || 0)} DPSKs from JSON file
            </div>

            {/* Show pool metadata if available (new format) */}
            {poolMetadata && (
              <div className="mt-2 pt-2 border-t border-green-200">
                <div className="font-medium mb-1">Pool Information:</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  {poolMetadata.displayName && (
                    <>
                      <span className="text-green-700">Pool Name:</span>
                      <span>{poolMetadata.displayName}</span>
                    </>
                  )}
                  {poolMetadata.description && (
                    <>
                      <span className="text-green-700">Description:</span>
                      <span>{poolMetadata.description}</span>
                    </>
                  )}
                  {poolMetadata.phraseDefaultLength && (
                    <>
                      <span className="text-green-700">Passphrase Length:</span>
                      <span>{poolMetadata.phraseDefaultLength} characters</span>
                    </>
                  )}
                  {poolMetadata.phraseRandomCharactersType && (
                    <>
                      <span className="text-green-700">Passphrase Format:</span>
                      <span>{poolMetadata.phraseRandomCharactersType}</span>
                    </>
                  )}
                  {poolMetadata.ssidList && poolMetadata.ssidList.length > 0 && (
                    <>
                      <span className="text-green-700">Associated SSIDs:</span>
                      <span>{poolMetadata.ssidList.join(', ')}</span>
                    </>
                  )}
                </div>
              </div>
            )}

            {/* Show export metadata if available */}
            {exportMetadata && (
              <div className="text-xs text-green-600">
                Exported from {exportMetadata.cloudpath_fqdn || 'Cloudpath'}
                {exportMetadata.extracted_at && ` on ${new Date(exportMetadata.extracted_at).toLocaleString()}`}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Step 2: Venue Selection */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 2: Select Target Venue</h3>

        {activeControllerType !== "RuckusONE" ? (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800">
              Please select a RuckusONE controller as your active controller to use this tool.
            </p>
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
          <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg">
            <p className="text-sm text-green-800">
              <strong>Selected Venue:</strong> {venueName} ({venueId})
            </p>
          </div>
        )}
      </div>

      {/* Step 3: Migration Options */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 3: Migration Options</h3>

        <div className="space-y-4">
          {/* Grouping Strategy */}
          <div>
            <label className="block text-sm font-medium text-gray-900 mb-2">
              DPSK Grouping Strategy
            </label>
            <div className="space-y-2">
              <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                <input
                  type="radio"
                  checked={!groupByVlan}
                  onChange={() => setGroupByVlan(false)}
                  disabled={processing}
                  className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-900">Single Pool</span>
                  <p className="text-xs text-gray-500">Create one identity group and DPSK pool for all passphrases</p>
                </div>
              </label>

              <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                <input
                  type="radio"
                  checked={groupByVlan}
                  onChange={() => setGroupByVlan(true)}
                  disabled={processing}
                  className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-900">Group by VLAN</span>
                  <p className="text-xs text-gray-500">Create separate identity groups and pools for each VLAN ID</p>
                </div>
              </label>
            </div>
          </div>

          {/* Identity Group Name */}
          <div className="pt-4 border-t">
            <label className="block text-sm font-medium text-gray-900 mb-2">
              Identity Group Name
              {poolMetadata?.displayName && identityGroupName === poolMetadata.displayName && (
                <span className="ml-2 text-xs font-normal text-blue-600">(auto-filled from pool)</span>
              )}
            </label>
            <input
              type="text"
              value={identityGroupName}
              onChange={(e) => setIdentityGroupName(e.target.value)}
              disabled={processing}
              placeholder={groupByVlan ? "e.g., Building A (will prefix VLAN groups)" : "e.g., Cloudpath Import"}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100"
            />
            <p className="mt-1 text-xs text-gray-500">
              {groupByVlan
                ? "Optional: Prefix for identity group names (e.g., 'Building A' creates 'Building A - VLAN 10')"
                : "Optional: Custom name for the identity group (default: 'Cloudpath Import')"
              }
            </p>
          </div>

          {/* DPSK Service Name */}
          <div className="pt-4 border-t">
            <label className="block text-sm font-medium text-gray-900 mb-2">
              DPSK Service Name
              {poolMetadata?.displayName && dpskServiceName === poolMetadata.displayName && (
                <span className="ml-2 text-xs font-normal text-blue-600">(auto-filled from pool)</span>
              )}
            </label>
            <input
              type="text"
              value={dpskServiceName}
              onChange={(e) => setDpskServiceName(e.target.value)}
              disabled={processing}
              placeholder={groupByVlan ? "e.g., Building A (will suffix with VLAN)" : "e.g., MyPoolOne DPSKs"}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100"
            />
            <p className="mt-1 text-xs text-gray-500">
              {groupByVlan
                ? "Optional: Base name for DPSK services (e.g., 'Building A' creates 'Building A - VLAN 10 - DPSKs')"
                : "Optional: Custom name for the DPSK service/pool (default: uses Identity Group Name)"
              }
            </p>
            {/* Show pool context info */}
            {poolMetadata && (
              <div className="mt-2 p-2 bg-blue-50 rounded text-xs text-blue-700">
                <strong>Pool settings from Cloudpath:</strong>{' '}
                {poolMetadata.phraseDefaultLength && `${poolMetadata.phraseDefaultLength} char passphrases`}
                {poolMetadata.phraseRandomCharactersType && ` (${poolMetadata.phraseRandomCharactersType})`}
                {poolMetadata.ssidList && poolMetadata.ssidList.length > 0 && ` • SSID: ${poolMetadata.ssidList.join(', ')}`}
              </div>
            )}
          </div>

          {/* SSID Configuration */}
          <div className="pt-4 border-t">
            <label className="block text-sm font-medium text-gray-900 mb-2">
              DPSK SSID Configuration
            </label>
            <div className="space-y-2">
              <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                <input
                  type="radio"
                  checked={ssidMode === "none"}
                  onChange={() => setSsidMode("none")}
                  disabled={processing}
                  className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-900">No SSID Configuration</span>
                  <p className="text-xs text-gray-500">Just import DPSKs without linking to any SSID</p>
                </div>
              </label>

              <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer opacity-50">
                <input
                  type="radio"
                  checked={ssidMode === "create_new"}
                  onChange={() => setSsidMode("create_new")}
                  disabled={true}
                  className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-900">Create New DPSK SSID</span>
                  <p className="text-xs text-gray-500">Create a new DPSK-enabled SSID for the imported passphrases (coming soon)</p>
                </div>
              </label>

              <label className="flex items-center gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                <input
                  type="radio"
                  checked={ssidMode === "link_existing"}
                  onChange={() => {
                    setSsidMode("link_existing");
                    if (dpskSsids.length === 0) {
                      fetchDpskSsids();
                    }
                  }}
                  disabled={processing || !venueId}
                  className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-900">Link to Existing DPSK SSID</span>
                  <p className="text-xs text-gray-500">Connect imported DPSKs to an existing DPSK-enabled SSID at this venue</p>
                </div>
              </label>

              {/* SSID Dropdown when link_existing is selected */}
              {ssidMode === "link_existing" && (
                <div className="ml-7 mt-2">
                  {loadingDpskSsids ? (
                    <div className="text-sm text-gray-500">Loading DPSK SSIDs...</div>
                  ) : dpskSsids.length === 0 ? (
                    <div className="text-sm text-yellow-600 bg-yellow-50 p-3 rounded-lg border border-yellow-200">
                      No DPSK-enabled SSIDs found at this venue. You may need to create one first.
                    </div>
                  ) : (
                    <select
                      value={selectedSsidId || ""}
                      onChange={(e) => setSelectedSsidId(e.target.value || null)}
                      disabled={processing}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                    >
                      <option value="">Select a DPSK SSID...</option>
                      {dpskSsids.map((ssid) => (
                        <option key={ssid.id} value={ssid.id}>
                          {ssid.name} ({ssid.ssid})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Additional Options */}
          <div className="space-y-2 pt-4 border-t">
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={justCopyDPSKs}
                onChange={(e) => setJustCopyDPSKs(e.target.checked)}
                disabled={processing}
                className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
              />
              <div>
                <span className="text-sm font-medium text-gray-900">Just Copy DPSKs</span>
                <p className="text-xs text-gray-500">Import only the DPSK credentials without additional configuration</p>
              </div>
            </label>

            <label className="flex items-center gap-3 opacity-50">
              <input
                type="checkbox"
                checked={includeAdaptivePolicySets}
                onChange={(e) => setIncludeAdaptivePolicySets(e.target.checked)}
                disabled={true}
                className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
              />
              <div>
                <span className="text-sm font-medium text-gray-900">Include Adaptive Policy Sets</span>
                <p className="text-xs text-gray-500">Migrate Adaptive Policy Sets along with DPSKs (coming soon)</p>
              </div>
            </label>

            {/* Expired DPSK Handling */}
            <div className="pt-2">
              <label className="block text-sm font-medium text-gray-900 mb-2">
                Expired DPSK Handling
              </label>
              <div className="space-y-2 ml-4">
                <label className="flex items-center gap-3">
                  <input
                    type="radio"
                    checked={expiredDpskHandling === "no_expiration"}
                    onChange={() => setExpiredDpskHandling("no_expiration")}
                    disabled={processing}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-900">Import without expiration</span>
                    <p className="text-xs text-gray-500">Import expired DPSKs with no expiration date (never expire)</p>
                  </div>
                </label>
                <label className="flex items-center gap-3">
                  <input
                    type="radio"
                    checked={expiredDpskHandling === "renew"}
                    onChange={() => setExpiredDpskHandling("renew")}
                    disabled={processing}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-900">Renew with 1-year expiration</span>
                    <p className="text-xs text-gray-500">Import expired DPSKs with a new 1-year expiration date</p>
                  </div>
                </label>
                <label className="flex items-center gap-3">
                  <input
                    type="radio"
                    checked={expiredDpskHandling === "skip"}
                    onChange={() => setExpiredDpskHandling("skip")}
                    disabled={processing}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <div>
                    <span className="text-sm font-medium text-gray-900">Skip expired DPSKs</span>
                    <p className="text-xs text-gray-500">Do not import DPSKs that have already expired</p>
                  </div>
                </label>
              </div>
            </div>

            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={simulateDelay}
                onChange={(e) => setSimulateDelay(e.target.checked)}
                disabled={processing}
                className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
              />
              <div>
                <span className="text-sm font-medium text-gray-900">Simulate Delay (Testing/Demo)</span>
                <p className="text-xs text-gray-500">Add artificial delays to observe migration progress more clearly (300ms per passphrase)</p>
              </div>
            </label>

            {/* Parallel Execution */}
            <div className="border-t pt-4 mt-4">
              <label className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={parallelExecution}
                  onChange={(e) => setParallelExecution(e.target.checked)}
                  disabled={processing}
                  className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                />
                <div>
                  <span className="text-sm font-medium text-gray-900">Parallel Execution (Faster)</span>
                  <p className="text-xs text-gray-500">Process multiple DPSK pools concurrently for faster migration (5-8x speedup for large datasets)</p>
                </div>
              </label>

              {parallelExecution && (
                <div className="mt-3 ml-7">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Max Concurrent Batches: {maxConcurrent}
                  </label>
                  <input
                    type="range"
                    min="1"
                    max="30"
                    value={maxConcurrent}
                    onChange={(e) => setMaxConcurrent(parseInt(e.target.value))}
                    disabled={processing}
                    className="w-48 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    Passphrases are split into batches of 10. Higher values = faster. Recommended: 10-20.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Step 4: Import Button */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 4: Start Migration</h3>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}

        <button
          onClick={handleProcess}
          disabled={processing || !activeControllerId || !venueId || !jsonData}
          className={`px-6 py-3 rounded font-semibold text-lg ${
            processing || !activeControllerId || !venueId || !jsonData
              ? "bg-gray-400 cursor-not-allowed text-white"
              : "bg-blue-600 hover:bg-blue-700 text-white shadow-lg"
          }`}
        >
          {processing ? "Starting Migration..." : "Start DPSK Migration"}
        </button>

        {!jsonData && (
          <p className="text-xs text-gray-500 mt-2">
            Please upload a JSON file first
          </p>
        )}
        {!venueId && jsonData && (
          <p className="text-xs text-gray-500 mt-2">
            Please select a venue
          </p>
        )}

        {/* Last Job Result */}
        {lastJobResult && (
          <div className={`mt-4 p-4 rounded-lg border ${
            lastJobResult.status === 'COMPLETED'
              ? 'bg-green-50 border-green-200'
              : lastJobResult.status === 'FAILED'
              ? 'bg-red-50 border-red-200'
              : 'bg-yellow-50 border-yellow-200'
          }`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-2xl">
                  {lastJobResult.status === 'COMPLETED' ? '✅' :
                   lastJobResult.status === 'FAILED' ? '❌' : '⚠️'}
                </span>
                <div>
                  <p className={`font-semibold ${
                    lastJobResult.status === 'COMPLETED' ? 'text-green-800' :
                    lastJobResult.status === 'FAILED' ? 'text-red-800' : 'text-yellow-800'
                  }`}>
                    Last Migration: {lastJobResult.status}
                  </p>
                  <p className="text-sm text-gray-600">
                    {lastJobResult.progress.completed_phases}/{lastJobResult.progress.total_phases} phases completed
                    {lastJobResult.progress.failed > 0 && (
                      <span className="text-red-600 ml-2">
                        ({lastJobResult.progress.failed} tasks failed)
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    setCurrentJobId(lastJobResult.job_id);
                    setShowJobModal(true);
                  }}
                  className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 rounded border border-gray-300"
                >
                  View Details
                </button>
                <button
                  onClick={() => setLastJobResult(null)}
                  className="px-2 py-1 text-gray-400 hover:text-gray-600"
                  title="Dismiss"
                >
                  ×
                </button>
              </div>
            </div>
            {lastJobResult.errors && lastJobResult.errors.length > 0 && (
              <div className="mt-2 text-sm text-red-600">
                {lastJobResult.errors.slice(0, 2).map((err, idx) => (
                  <p key={idx}>{err}</p>
                ))}
                {lastJobResult.errors.length > 2 && (
                  <p className="text-gray-500">...and {lastJobResult.errors.length - 2} more</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Audit Section */}
      <div className="bg-white rounded-lg shadow p-6 mt-6">
        <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
          <span>🔍</span> Audit Venue DPSKs
        </h3>
        <p className="text-sm text-gray-600 mb-4">
          View all DPSKs and Adaptive Policy Sets currently configured in the selected venue.
        </p>

        {auditError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {auditError}
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={handleAuditVenue}
            disabled={auditLoading || !activeControllerId || !venueId}
            className={`px-6 py-2 rounded font-semibold ${
              auditLoading || !activeControllerId || !venueId
                ? "bg-gray-400 cursor-not-allowed text-white"
                : "bg-indigo-600 hover:bg-indigo-700 text-white"
            }`}
          >
            {auditLoading ? "Loading..." : "Audit Venue"}
          </button>

          <button
            onClick={handleViewIdentities}
            disabled={identityExportLoading || !activeControllerId}
            className={`px-6 py-2 rounded font-semibold ${
              identityExportLoading || !activeControllerId
                ? "bg-gray-400 cursor-not-allowed text-white"
                : "bg-green-600 hover:bg-green-700 text-white"
            }`}
          >
            {identityExportLoading ? "Loading..." : "View Identities"}
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-2">
          View Identities shows all identities/passphrases (optionally filtered by venue if selected)
        </p>
      </div>

      {/* Nuclear Cleanup Section */}
      <div className="bg-gradient-to-r from-red-900 to-orange-900 rounded-lg shadow-xl p-6 mt-6 border-4 border-red-600">
        <h3 className="text-2xl font-bold mb-4 flex items-center gap-3 text-white">
          <span className="text-3xl animate-pulse">☢️</span>
          <span>CLEANUP (DANGER ZONE)</span>
        </h3>
        <div className="bg-yellow-50 border-2 border-yellow-400 rounded-lg p-4 mb-4">
          <p className="text-sm font-bold text-red-900 mb-2">⚠️ EXTREME CAUTION REQUIRED</p>
          <p className="text-sm text-gray-900 mb-2">
            This will permanently delete <strong>ALL DPSK resources</strong> in the selected venue:
          </p>
          <ul className="text-sm text-gray-900 list-disc ml-5 space-y-1">
            <li>All DPSK passphrases (every single one)</li>
            <li>All DPSK pools/services</li>
            <li>All DPSK-related identity groups</li>
          </ul>
          <p className="text-sm font-bold text-red-900 mt-3">
            This is for DEV/TESTING only. Use only when you need a completely clean slate.
          </p>
        </div>

        <button
          onClick={handleNuclearCleanup}
          disabled={!activeControllerId || !venueId || !venueName}
          className={`px-6 py-3 rounded font-bold text-lg flex items-center gap-2 ${
            !activeControllerId || !venueId || !venueName
              ? "bg-gray-600 cursor-not-allowed text-gray-300"
              : "bg-red-700 hover:bg-red-800 text-white shadow-lg border-2 border-red-400"
          }`}
        >
          <span>☢️</span>
          <span>DELETE ALL DPSK RESOURCES</span>
        </button>

        {(!venueId || !venueName) && (
          <p className="text-xs text-yellow-200 mt-2">
            Select a venue first
          </p>
        )}
      </div>

      {/* Job Monitor Modal */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleCloseJobModal}
          onCleanup={handleCleanup}
          onJobComplete={handleJobComplete}
        />
      )}

      {/* Nuclear Cleanup Modal */}
      <NuclearCleanupModal
        isOpen={showNuclearModal}
        onClose={() => setShowNuclearModal(false)}
        onConfirm={handleConfirmNuclearCleanup}
        controllerId={activeControllerId || 0}
        venueId={venueId || ''}
        venueName={venueName || ''}
        tenantId={effectiveTenantId || undefined}
      />

      {/* Audit Modal */}
      {showAuditModal && auditData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-indigo-600 to-blue-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h3 className="text-2xl font-bold">DPSK Audit Results</h3>
                <p className="text-indigo-100 text-sm">
                  {auditData.venue_name} ({auditData.venue_id})
                </p>
              </div>
              <button
                onClick={() => setShowAuditModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>

            {/* Modal Body */}
            <div className="overflow-y-auto flex-1 p-6">
              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-blue-600">
                    {auditData.total_ssids || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Total SSIDs</div>
                </div>
                <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-green-600">
                    {auditData.total_dpsk_ssids || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">DPSK SSIDs</div>
                </div>
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-purple-600">
                    {auditData.total_dpsk_pools || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">DPSK Pools</div>
                </div>
                <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-orange-600">
                    {auditData.total_identity_groups || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Identity Groups</div>
                </div>
              </div>

              {/* Raw Data Display */}
              <div className="space-y-4">
                <details className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <summary className="font-semibold text-gray-700 cursor-pointer">
                    SSIDs ({auditData.ssids.length})
                  </summary>
                  <pre className="mt-3 text-xs text-gray-700 overflow-auto max-h-64 bg-white p-3 rounded border">
                    {JSON.stringify(auditData.ssids, null, 2)}
                  </pre>
                </details>

                <details className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <summary className="font-semibold text-gray-700 cursor-pointer">
                    DPSK Pools ({auditData.dpsk_pools.length})
                  </summary>
                  <pre className="mt-3 text-xs text-gray-700 overflow-auto max-h-64 bg-white p-3 rounded border">
                    {JSON.stringify(auditData.dpsk_pools, null, 2)}
                  </pre>
                </details>

                <details className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <summary className="font-semibold text-gray-700 cursor-pointer">
                    Identity Groups ({auditData.identity_groups.length})
                  </summary>
                  <pre className="mt-3 text-xs text-gray-700 overflow-auto max-h-64 bg-white p-3 rounded border">
                    {JSON.stringify(auditData.identity_groups, null, 2)}
                  </pre>
                </details>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end border-t">
              <button
                onClick={() => setShowAuditModal(false)}
                className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Identity Export Modal */}
      {showIdentityExportModal && identityExportData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-6xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-green-600 to-teal-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h3 className="text-2xl font-bold">Identities & Passphrases</h3>
                <p className="text-green-100 text-sm">
                  {filteredIdentityData.length} of {identityExportData.length} records
                  {venueId ? ` at venue ${venueId}` : ' (all venues)'}
                </p>
              </div>
              <button
                onClick={() => setShowIdentityExportModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>

            {/* Filter Bar */}
            {uniqueIdentityPools.length > 0 && (
              <div className="bg-gray-100 px-6 py-3 border-b">
                {/* Search and action row */}
                <div className="flex items-center gap-4 mb-3">
                  <label className="text-sm font-medium text-gray-700 whitespace-nowrap">Filter by DPSK Pool:</label>
                  <input
                    type="text"
                    placeholder="Search pools..."
                    value={poolSearchText}
                    onChange={(e) => setPoolSearchText(e.target.value)}
                    className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent w-48"
                  />
                  <span className="text-xs text-gray-500">
                    {searchFilteredPools.length} of {uniqueIdentityPools.length} pools
                  </span>
                  <div className="flex gap-2 ml-auto">
                    {poolSearchText && searchFilteredPools.length > 0 && (
                      <button
                        onClick={() => {
                          const newFilters = new Set(identityPoolFilters);
                          searchFilteredPools.forEach(p => newFilters.add(p.id));
                          setIdentityPoolFilters(newFilters);
                          setIdentityPage(1);
                        }}
                        className="text-sm text-green-600 hover:text-green-800 underline"
                      >
                        Select matching ({searchFilteredPools.length})
                      </button>
                    )}
                    {identityPoolFilters.size > 0 && (
                      <button
                        onClick={() => {
                          setIdentityPoolFilters(new Set());
                          setIdentityPage(1);
                        }}
                        className="text-sm text-red-600 hover:text-red-800 underline"
                      >
                        Clear all ({identityPoolFilters.size})
                      </button>
                    )}
                    {identityPoolFilters.size === 0 && uniqueIdentityPools.length > 1 && !poolSearchText && (
                      <button
                        onClick={() => {
                          setIdentityPoolFilters(new Set(uniqueIdentityPools.map(p => p.id)));
                          setIdentityPage(1);
                        }}
                        className="text-sm text-gray-500 hover:text-gray-700 underline"
                      >
                        Select all
                      </button>
                    )}
                  </div>
                </div>
                {/* Pool buttons - scrollable */}
                <div className="max-h-32 overflow-y-auto">
                  <div className="flex flex-wrap gap-2">
                    {searchFilteredPools.map(pool => {
                      const count = identityExportData.filter(r => r.dpsk_pool_id === pool.id).length;
                      const isSelected = identityPoolFilters.has(pool.id);
                      return (
                        <button
                          key={pool.id}
                          onClick={() => {
                            const newFilters = new Set(identityPoolFilters);
                            if (isSelected) {
                              newFilters.delete(pool.id);
                            } else {
                              newFilters.add(pool.id);
                            }
                            setIdentityPoolFilters(newFilters);
                            setIdentityPage(1);
                          }}
                          className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                            isSelected
                              ? 'bg-green-600 text-white'
                              : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50'
                          }`}
                        >
                          {pool.name} ({count})
                        </button>
                      );
                    })}
                    {searchFilteredPools.length === 0 && poolSearchText && (
                      <span className="text-sm text-gray-500 italic">No pools match "{poolSearchText}"</span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Modal Body */}
            <div className="overflow-auto flex-1 p-4">
              {filteredIdentityData.length === 0 ? (
                <div className="text-center text-gray-500 py-8">
                  {identityExportData.length === 0
                    ? "No identities or passphrases found at this venue."
                    : "No records match the selected filter."}
                </div>
              ) : (
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        DPSK Pool
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Username
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Passphrase
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Cloudpath GUID
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Identity ID
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Passphrase ID
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {paginatedIdentityData.map((row, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <div className="text-gray-900 font-medium">{row.dpsk_pool_name || '-'}</div>
                          <div className="text-xs text-gray-400 font-mono">{row.dpsk_pool_id || ''}</div>
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-gray-900">
                          {row.username}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap font-mono text-xs text-gray-600">
                          {row.passphrase || <span className="text-gray-400">-</span>}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap font-mono text-xs text-gray-600">
                          {row.cloudpath_guid || <span className="text-gray-400">-</span>}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap font-mono text-xs text-gray-600">
                          {row.identity_id || <span className="text-gray-400">-</span>}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap font-mono text-xs text-gray-600">
                          {row.passphrase_id || <span className="text-gray-400">-</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Modal Footer with Pagination */}
            <div className="bg-gray-50 px-6 py-4 border-t">
              {/* Pagination row */}
              {totalIdentityPages > 1 && (
                <div className="flex items-center justify-center gap-2 mb-3">
                  <button
                    onClick={() => setIdentityPage(1)}
                    disabled={identityPage === 1}
                    className={`px-2 py-1 text-sm rounded ${identityPage === 1 ? 'text-gray-400' : 'text-gray-700 hover:bg-gray-200'}`}
                  >
                    ««
                  </button>
                  <button
                    onClick={() => setIdentityPage(p => Math.max(1, p - 1))}
                    disabled={identityPage === 1}
                    className={`px-2 py-1 text-sm rounded ${identityPage === 1 ? 'text-gray-400' : 'text-gray-700 hover:bg-gray-200'}`}
                  >
                    «
                  </button>
                  <span className="text-sm text-gray-600 mx-2">
                    Page {identityPage} of {totalIdentityPages}
                  </span>
                  <button
                    onClick={() => setIdentityPage(p => Math.min(totalIdentityPages, p + 1))}
                    disabled={identityPage === totalIdentityPages}
                    className={`px-2 py-1 text-sm rounded ${identityPage === totalIdentityPages ? 'text-gray-400' : 'text-gray-700 hover:bg-gray-200'}`}
                  >
                    »
                  </button>
                  <button
                    onClick={() => setIdentityPage(totalIdentityPages)}
                    disabled={identityPage === totalIdentityPages}
                    className={`px-2 py-1 text-sm rounded ${identityPage === totalIdentityPages ? 'text-gray-400' : 'text-gray-700 hover:bg-gray-200'}`}
                  >
                    »»
                  </button>
                  <span className="text-xs text-gray-500 ml-2">
                    (showing {((identityPage - 1) * IDENTITY_PAGE_SIZE) + 1}-{Math.min(identityPage * IDENTITY_PAGE_SIZE, filteredIdentityData.length)})
                  </span>
                </div>
              )}
              {/* Actions row */}
              <div className="flex justify-between items-center">
                <div className="text-sm text-gray-500">
                  {identityPoolFilters.size > 0
                    ? `${filteredIdentityData.length} of ${identityExportData.length} records (${identityPoolFilters.size} pool${identityPoolFilters.size > 1 ? 's' : ''} selected)`
                    : `${identityExportData.length} records`}
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleDownloadCsv}
                    disabled={filteredIdentityData.length === 0}
                    className={`px-4 py-2 rounded font-semibold ${
                      filteredIdentityData.length === 0
                        ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                        : 'bg-green-600 text-white hover:bg-green-700'
                    }`}
                  >
                    {identityPoolFilters.size > 0 ? `Export All ${filteredIdentityData.length} to CSV` : `Export All ${identityExportData.length} to CSV`}
                  </button>
                  <button
                    onClick={() => setShowIdentityExportModal(false)}
                    className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default CloudpathDPSK;
