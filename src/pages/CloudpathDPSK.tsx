import { useState } from "react";
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

function CloudpathDPSK() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  // JSON Upload
  const [jsonData, setJsonData] = useState<DPSKData[] | null>(null);
  const [uploadError, setUploadError] = useState("");

  // Venue Selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Migration Options
  const [justCopyDPSKs, setJustCopyDPSKs] = useState(true);
  const [groupByVlan, setGroupByVlan] = useState(false);
  const [includeAdaptivePolicySets, setIncludeAdaptivePolicySets] = useState(false);
  const [simulateDelay, setSimulateDelay] = useState(false);
  const [expiredDpskHandling, setExpiredDpskHandling] = useState<"renew" | "skip">("renew");

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

  // Nuclear cleanup modal
  const [showNuclearModal, setShowNuclearModal] = useState(false);

  // Determine tenant ID (for MSP, it's null until explicitly set; for EC, use r1_tenant_id)
  const activeController = controllers.find(c => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null
    : (activeController?.r1_tenant_id || null);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadError("");
    const reader = new FileReader();

    reader.onload = (event) => {
      try {
        const text = event.target?.result as string;
        const data = JSON.parse(text);

        if (!Array.isArray(data)) {
          setUploadError("Invalid format: Expected an array of DPSK objects");
          setJsonData(null);
          return;
        }

        setJsonData(data);
      } catch (err) {
        setUploadError("Invalid JSON file. Please check the file format.");
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
          },
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
            Upload JSON file from Cloudpath export:
          </label>
          <input
            type="file"
            accept=".json"
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
          <div className="p-3 bg-green-50 border border-green-200 rounded text-green-800 text-sm">
            ✅ Loaded {jsonData.length} DPSKs from JSON file
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
                  onClick={() => setShowJobModal(true)}
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
    </div>
  );
}

export default CloudpathDPSK;
