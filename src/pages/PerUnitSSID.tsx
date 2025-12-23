import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface AuditData {
  venue_id: string;
  venue_name: string;
  total_ap_groups: number;
  total_aps: number;
  total_ssids: number;
  ap_groups: Array<{
    ap_group_id: string;
    ap_group_name: string;
    venue_id: string;
    venue_name: string;
    description: string;
    total_aps: number;
    ap_names: string[];
    ap_serials: string[];
    total_ssids: number;
    ssid_names: string[];
    ssids: any[];
  }>;
}

function PerUnitSSID() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  const [csvInput, setCsvInput] = useState("");
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");

  // Job monitor modal state
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastJobResult, setLastJobResult] = useState<JobResult | null>(null);

  // Configuration options
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);
  const [apGroupPrefix, setApGroupPrefix] = useState("APG-");

  // Audit modal state
  const [showAuditModal, setShowAuditModal] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [auditError, setAuditError] = useState("");

  // Determine tenant ID (for MSP, it's null until explicitly set; for EC, use r1_tenant_id)
  const activeController = controllers.find(c => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null  // For MSP, we'd need EC selector - for now just use controller's default
    : (activeController?.r1_tenant_id || null);

  const handleCsvInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCsvInput(e.target.value);
    setError("");
  };

  const handleProcess = async () => {
    if (!csvInput.trim()) {
      setError("Please provide CSV data");
      return;
    }

    if (!venueId.trim()) {
      setError("Please enter a Venue ID");
      return;
    }

    if (!activeControllerId) {
      setError("Please select an active controller first");
      return;
    }

    setProcessing(true);
    setError("");

    try {
      // Parse CSV
      const lines = csvInput.trim().split('\n');
      if (lines.length < 2) {
        setError("CSV must have header row and at least one data row");
        setProcessing(false);
        return;
      }

      // Parse header
      const headers = lines[0].split(',').map(h => h.trim());
      const requiredHeaders = ['unit_number', 'ssid_name', 'ssid_password', 'security_type', 'default_vlan'];
      const optionalHeaders = ['ap_serial_or_name'];

      // Validate required headers
      const hasAllRequired = requiredHeaders.every(h => headers.includes(h));
      if (!hasAllRequired) {
        setError(`Invalid CSV format. Required headers: ${requiredHeaders.join(', ')}. Optional: ${optionalHeaders.join(', ')}`);
        setProcessing(false);
        return;
      }

      // Parse rows and group by unit
      const unitMap = new Map<string, any>();

      for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;

        const values = line.split(',').map(v => v.trim());
        const row: any = {};
        headers.forEach((header, idx) => {
          row[header] = values[idx] || '';
        });

        const unitNumber = row.unit_number;
        if (!unitMap.has(unitNumber)) {
          unitMap.set(unitNumber, {
            unit_number: unitNumber,
            ap_identifiers: [],
            ssid_name: row.ssid_name,
            ssid_password: row.ssid_password,
            security_type: row.security_type || 'WPA3',
            default_vlan: row.default_vlan || '1'
          });
        }

        // Add AP to this unit
        if (row.ap_serial_or_name) {
          unitMap.get(unitNumber).ap_identifiers.push(row.ap_serial_or_name);
        }
      }

      const units = Array.from(unitMap.values());

      if (units.length === 0) {
        setError("No valid units found in CSV");
        setProcessing(false);
        return;
      }

      // Call backend API - now returns job_id for async processing
      const response = await fetch(`${API_BASE_URL}/per-unit-ssid/configure`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          units: units,
          ap_group_prefix: apGroupPrefix
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Configuration failed");
      }

      const result = await response.json();

      // Show job monitor modal
      setCurrentJobId(result.job_id);
      setShowJobModal(true);

    } catch (err: any) {
      console.error("Processing error:", err);
      setError(err.message || "An error occurred");
    } finally {
      setProcessing(false);
    }
  };

  const handleCloseJobModal = () => {
    setShowJobModal(false);
    // Keep the job ID so user can reopen if needed
  };

  const handleJobComplete = (result: JobResult) => {
    setLastJobResult(result);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      setCsvInput(text);
    };
    reader.readAsText(file);
  };

  const handleVenueSelect = (selectedVenueId: string | null, venue: any) => {
    setVenueId(selectedVenueId);
    setVenueName(venue?.name || null);
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
      const response = await fetch(`${API_BASE_URL}/per-unit-ssid/audit`, {
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
      console.log('🔍 FRONTEND: Received audit data:', data);
      console.log('🔍 FRONTEND: Total AP Groups in response:', data.ap_groups?.length);
      console.log('🔍 FRONTEND: AP Group names:', data.ap_groups?.map((g: any) => g.ap_group_name));
      setAuditData(data);
      setShowAuditModal(true);
    } catch (err: any) {
      console.error("Audit error:", err);
      setAuditError(err.message || "An error occurred during audit");
    } finally {
      setAuditLoading(false);
    }
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Per-Unit SSID Configuration</h2>

      <p className="text-gray-600 mb-6">
        Configure unique SSIDs for individual apartment units or rooms in RuckusONE
      </p>

      {/* Controller Info */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
        <p className="text-sm text-gray-700">
          <strong>Active Controller:</strong> {activeControllerId || "None selected"}
        </p>
        {!activeControllerId && (
          <p className="text-sm text-red-600 mt-2">
            ⚠️ Please select an active controller from the Controllers page before proceeding.
          </p>
        )}
      </div>

      {/* Educational Section - How It Works */}
      <div className="bg-gradient-to-r from-purple-50 to-blue-50 border-2 border-purple-200 rounded-lg p-6 mb-6">
        <h3 className="text-xl font-bold mb-3 flex items-center gap-2">
          <span className="text-2xl">💡</span>
          How Per-Unit SSIDs Work in RuckusONE
        </h3>

        <div className="space-y-3 text-sm text-gray-700">
          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-purple-900 mb-2">🔄 SmartZone → RuckusONE Migration</p>
            <p>
              In <strong>SmartZone</strong>, you used <strong>WLAN Groups</strong> to assign different SSIDs to different APs.
              <br/>
              In <strong>RuckusONE</strong>, WLAN Groups don't exist. Instead, you use <strong>AP Groups</strong> + <strong>SSID assignments</strong>.
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-blue-900 mb-2">⚙️ Configuration Flow</p>
            <ol className="list-decimal list-inside space-y-1 ml-2">
              <li><strong>Create SSIDs</strong> for each unit in your venue (e.g., "Unit-101", "Unit-102")</li>
              <li><strong>Create AP Groups</strong> for each unit (e.g., "APGroup-101", "APGroup-102")</li>
              <li><strong>Assign APs</strong> in each physical unit to their corresponding AP Group</li>
              <li><strong>Assign SSIDs</strong> to their corresponding AP Groups (Unit-101 SSID → APGroup-101)</li>
            </ol>
            <p className="mt-2 text-xs italic text-gray-600">
              This tool automates steps 1-4 for you based on your unit list!
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-green-900 mb-2">🎯 Advanced: Neighboring AP Broadcast <span className="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded ml-2">Coming Soon</span></p>
            <p>
              <strong>Future Feature:</strong> Configure neighboring APs to also broadcast a unit's SSID for better coverage and seamless roaming.
              <br/>
              <span className="text-amber-700 font-medium">⚠️ Trade-off:</span> Better resiliency vs. increased interference and airtime overhead.
            </p>
          </div>
        </div>
      </div>

      {/* CSV Template Download */}
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6">
        <h3 className="text-lg font-semibold mb-2 flex items-center gap-2">
          <span>📋</span> CSV Format Required
        </h3>
        <p className="text-sm text-gray-700 mb-3">
          Upload a CSV file with the following columns:
        </p>
        <div className="text-xs space-y-1 mb-3">
          <div><strong>Required:</strong> <code className="bg-white px-1 py-0.5 rounded text-xs">unit_number, ssid_name, ssid_password, security_type, default_vlan</code></div>
          <div><strong>Optional:</strong> <code className="bg-white px-1 py-0.5 rounded text-xs">ap_serial_or_name</code> <span className="text-gray-500">(can be omitted to only create SSIDs and AP Groups)</span></div>
        </div>
        <p className="text-xs text-gray-600 mb-3 italic">
          💡 <strong>Tip:</strong> Multiple rows with the same <code className="bg-gray-100 px-1 rounded">unit_number</code> will be grouped into the same AP Group. You can omit <code className="bg-gray-100 px-1 rounded">ap_serial_or_name</code> entirely if you just want to pre-create SSIDs and AP Groups.
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => {
              const template = `unit_number,ap_serial_or_name,ssid_name,ssid_password,security_type,default_vlan
101,AP-101-Living,Unit-101,SecurePass101!,WPA3,10
101,AP-101-Bedroom,Unit-101,SecurePass101!,WPA3,10
102,AP-102-Living,Unit-102,SecurePass102!,WPA3,20
102,AP-102-Bedroom,Unit-102,SecurePass102!,WPA3,20
103,12AB34CD56EF,Unit-103,SecurePass103!,WPA2/WPA3,30
103,AB12CD34EF56,Unit-103,SecurePass103!,WPA2/WPA3,30`;
              const blob = new Blob([template], { type: 'text/csv' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'per-unit-ssid-with-aps.csv';
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="px-4 py-2 bg-amber-600 text-white rounded hover:bg-amber-700 text-sm font-medium"
          >
            📥 Template with APs
          </button>

          <button
            onClick={() => {
              const template = `unit_number,ssid_name,ssid_password,security_type,default_vlan
101,Unit-101,SecurePass101!,WPA3,10
102,Unit-102,SecurePass102!,WPA3,20
103,Unit-103,SecurePass103!,WPA2/WPA3,30
104,Unit-104,SecurePass104!,WPA3,40
105,Unit-105,SecurePass105!,WPA3,50`;
              const blob = new Blob([template], { type: 'text/csv' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'per-unit-ssid-no-aps.csv';
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
          >
            📝 Template without APs
          </button>
        </div>
      </div>

      {/* Venue Selection Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 1: Select Venue</h3>

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
      </div>

      {/* Input Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 2: Configuration Input</h3>

        {/* Selected Venue Display */}
        {venueId && venueName && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg">
            <p className="text-sm text-green-800">
              <strong>Selected Venue:</strong> {venueName} ({venueId})
            </p>
          </div>
        )}

        {/* AP Group Prefix Input */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            AP Group Prefix
          </label>
          <input
            type="text"
            value={apGroupPrefix}
            onChange={(e) => setApGroupPrefix(e.target.value)}
            placeholder="APG-"
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            Prefix for AP Group names (e.g., "APG-" creates "APG-101", "APG-102")
          </p>
        </div>

        {/* CSV Text Input */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Or paste CSV content directly:
          </label>
          <textarea
            value={csvInput}
            onChange={handleCsvInputChange}
            placeholder="unit_number,ap_serial_or_name,ssid_name,ssid_password,security_type,default_vlan&#10;101,AP-101-Living,Unit-101,SecurePass101!,WPA3,10"
            className="w-full h-32 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"
          />
        </div>

        {/* File Upload */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Or upload a CSV file:
          </label>
          <input
            type="file"
            accept=".csv,.txt"
            onChange={handleFileUpload}
            className="block w-full text-sm text-gray-500
              file:mr-4 file:py-2 file:px-4
              file:rounded-md file:border-0
              file:text-sm file:font-semibold
              file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100"
          />
        </div>

        {/* Error Message */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}

        {/* Process Button */}
        <button
          onClick={handleProcess}
          disabled={processing || !activeControllerId}
          className={`px-6 py-2 rounded font-semibold ${
            processing || !activeControllerId
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700 text-white"
          }`}
        >
          {processing ? "Processing..." : "Process Units"}
        </button>

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
                    Last Job: {lastJobResult.status}
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
          <span>🔍</span> (Optional) Step 3: Venue Network Audit
        </h3>
        <p className="text-sm text-gray-600 mb-4">
          View the current network configuration for your venue before making changes.
          This shows all AP Groups, their members, and SSID activations.
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
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-indigo-600 hover:bg-indigo-700 text-white"
          }`}
        >
          {auditLoading ? "Loading..." : "Audit Venue"}
        </button>
      </div>

      {/* Job Monitor Modal */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleCloseJobModal}
          onJobComplete={handleJobComplete}
        />
      )}

      {/* Audit Modal */}
      {showAuditModal && auditData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-6xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-indigo-600 to-purple-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h3 className="text-2xl font-bold">Venue Network Audit</h3>
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
              {/* Summary Cards */}
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-blue-600">
                    {auditData.total_ap_groups}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">AP Groups</div>
                </div>
                <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-green-600">
                    {auditData.total_aps}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Access Points</div>
                </div>
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-purple-600">
                    {auditData.total_ssids}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Unique SSIDs</div>
                </div>
              </div>

              {/* AP Groups List */}
              <div className="space-y-4">
                <h4 className="text-lg font-semibold text-gray-800 mb-3">
                  AP Groups Configuration
                </h4>

                {auditData.ap_groups.length === 0 ? (
                  <div className="text-center py-8 text-gray-500">
                    <p>No AP Groups found in this venue.</p>
                  </div>
                ) : (
                  auditData.ap_groups.map((group) => (
                    <div
                      key={group.ap_group_id}
                      className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
                    >
                      <div className="flex justify-between items-start mb-3">
                        <div>
                          <h5 className="text-lg font-bold text-gray-800">
                            {group.ap_group_name}
                          </h5>
                          {group.description && (
                            <p className="text-sm text-gray-500 mt-1">
                              {group.description}
                            </p>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <span className="px-3 py-1 bg-blue-100 text-blue-800 text-xs font-semibold rounded-full">
                            {group.total_aps} APs
                          </span>
                          <span className="px-3 py-1 bg-purple-100 text-purple-800 text-xs font-semibold rounded-full">
                            {group.total_ssids} SSIDs
                          </span>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-4 mt-3">
                        {/* APs Column */}
                        <div>
                          <h6 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                            📡 Access Points
                          </h6>
                          {group.total_aps === 0 ? (
                            <p className="text-xs text-gray-500 italic">
                              No APs assigned
                            </p>
                          ) : (
                            <ul className="space-y-1">
                              {group.ap_names.map((apName, idx) => {
                                const serial = group.ap_serials[idx];
                                const showSerial = serial && serial !== apName;
                                return (
                                  <li
                                    key={idx}
                                    className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded"
                                  >
                                    {apName}
                                    {showSerial && (
                                      <span className="text-gray-400 ml-1">
                                        ({serial})
                                      </span>
                                    )}
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>

                        {/* SSIDs Column */}
                        <div>
                          <h6 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                            📶 SSIDs
                          </h6>
                          {group.total_ssids === 0 ? (
                            <p className="text-xs text-gray-500 italic">
                              No SSIDs activated
                            </p>
                          ) : (
                            <ul className="space-y-1">
                              {group.ssid_names.map((ssidName, idx) => (
                                <li
                                  key={idx}
                                  className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded"
                                >
                                  {ssidName}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
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

export default PerUnitSSID;
