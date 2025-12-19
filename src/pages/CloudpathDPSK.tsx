import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface DPSKData {
  // Placeholder - will be defined based on actual Cloudpath export format
  dpsks?: any[];
  adaptive_policy_sets?: any[];
}

interface AuditData {
  venue_id: string;
  venue_name: string;
  total_dpsks: number;
  total_policy_sets: number;
  // Additional audit fields to be defined
}

function CloudpathDPSK() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  // Step 2: JSON Upload
  const [jsonData, setJsonData] = useState<DPSKData | null>(null);
  const [uploadError, setUploadError] = useState("");

  // Step 3: Venue Selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Step 4: Options
  const [justCopyDPSKs, setJustCopyDPSKs] = useState(true);
  const [includeAdaptivePolicySets, setIncludeAdaptivePolicySets] = useState(false);
  // Additional options to be added later

  // Step 5: Processing
  const [processing, setProcessing] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  const [error, setError] = useState("");

  // Audit functionality
  const [showAuditModal, setShowAuditModal] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [auditError, setAuditError] = useState("");

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
    setResults([]);

    try {
      // Placeholder - backend endpoint to be implemented
      const response = await fetch(`${API_BASE_URL}/cloudpath-dpsk/import`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          dpsk_data: jsonData,
          options: {
            just_copy_dpsks: justCopyDPSKs,
            include_adaptive_policy_sets: includeAdaptivePolicySets,
          },
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Import failed");
      }

      const result = await response.json();
      setResults(result.results || []);
      alert("DPSK import completed successfully!");

    } catch (err: any) {
      console.error("Processing error:", err);
      setError(err.message || "An error occurred");
    } finally {
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
      // Placeholder - backend endpoint to be implemented
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

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Cloudpath DPSK Migration</h2>

      <p className="text-gray-600 mb-6">
        Migrate Dynamic Pre-Shared Keys (DPSKs) and Adaptive Policy Sets from Cloudpath to RuckusONE
      </p>

      {/* Controller Info */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
        <p className="text-sm text-gray-700">
          <strong>Active Controller:</strong> {activeControllerId || "None selected"}
        </p>
        {!activeControllerId && (
          <p className="text-sm text-red-600 mt-2">
            Please select an active controller from the Controllers page before proceeding.
          </p>
        )}
      </div>

      {/* Educational Section - How It Works */}
      <div className="bg-gradient-to-r from-blue-50 to-cyan-50 border-2 border-blue-200 rounded-lg p-6 mb-6">
        <h3 className="text-xl font-bold mb-3 flex items-center gap-2">
          <span className="text-2xl">📖</span>
          How Cloudpath DPSK Migration Works
        </h3>

        <div className="space-y-3 text-sm text-gray-700">
          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-blue-900 mb-2">Step 1: Extract Data from Cloudpath</p>
            <p>
              Use the external Cloudpath extraction script to pull all DPSKs and Adaptive Policy Sets from your Cloudpath server.
              <br/>
              <span className="text-xs text-gray-600 italic">(Script documentation and download link to be provided)</span>
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-cyan-900 mb-2">Step 2: Upload and Process</p>
            <p>
              Upload the JSON export file, select your target venue in RuckusONE, and choose migration options.
              The tool will automatically create DPSKs and optionally migrate Adaptive Policy Sets.
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-green-900 mb-2">Step 3: Verify and Audit</p>
            <p>
              Use the Audit Venue feature to review all DPSKs and policy configurations before and after migration.
            </p>
          </div>
        </div>
      </div>

      {/* Step 1: External Script Instructions */}
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6">
        <h3 className="text-lg font-semibold mb-2 flex items-center gap-2">
          <span>📋</span> Step 1: Extract Data from Cloudpath
        </h3>
        <p className="text-sm text-gray-700 mb-3">
          Run the Cloudpath extraction script against your Cloudpath server to export DPSK data to a JSON file.
        </p>
        <div className="bg-white border border-amber-300 rounded p-3 mb-3">
          <p className="text-xs font-mono text-gray-600">
            # Placeholder - Script details to be provided
            <br/>
            $ python cloudpath_export.py --server YOUR_SERVER --output dpsks.json
          </p>
        </div>
        <p className="text-xs text-gray-600 italic">
          Script documentation and usage instructions will be provided separately.
        </p>
      </div>

      {/* Step 2: Upload JSON */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 2: Upload Cloudpath Export</h3>

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Upload JSON file from Cloudpath export:
          </label>
          <input
            type="file"
            accept=".json"
            onChange={handleFileUpload}
            className="block w-full text-sm text-gray-500
              file:mr-4 file:py-2 file:px-4
              file:rounded-md file:border-0
              file:text-sm file:font-semibold
              file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100"
          />
        </div>

        {uploadError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {uploadError}
          </div>
        )}

        {jsonData && (
          <div className="p-3 bg-green-50 border border-green-200 rounded text-green-800 text-sm">
            JSON file loaded successfully
          </div>
        )}
      </div>

      {/* Step 3: Venue Selection */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 3: Select Target Venue</h3>

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

      {/* Step 4: Migration Options */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 4: Migration Options</h3>

        <div className="space-y-3">
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={justCopyDPSKs}
              onChange={(e) => setJustCopyDPSKs(e.target.checked)}
              className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
            />
            <div>
              <span className="text-sm font-medium text-gray-900">Just Copy DPSKs</span>
              <p className="text-xs text-gray-500">Import only the DPSK credentials without additional configuration</p>
            </div>
          </label>

          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={includeAdaptivePolicySets}
              onChange={(e) => setIncludeAdaptivePolicySets(e.target.checked)}
              className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
            />
            <div>
              <span className="text-sm font-medium text-gray-900">Include Adaptive Policy Sets</span>
              <p className="text-xs text-gray-500">Migrate Adaptive Policy Sets along with DPSKs</p>
            </div>
          </label>

          {/* Placeholder for additional options */}
          <div className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded text-xs text-gray-600 italic">
            Additional migration options will be added here based on requirements
          </div>
        </div>
      </div>

      {/* Step 5: Process Button */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 5: Import DPSKs</h3>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}

        <button
          onClick={handleProcess}
          disabled={processing || !activeControllerId || !venueId || !jsonData}
          className={`px-6 py-2 rounded font-semibold ${
            processing || !activeControllerId || !venueId || !jsonData
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700 text-white"
          }`}
        >
          {processing ? "Processing..." : "Import DPSKs to RuckusONE"}
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
      </div>

      {/* Results Section */}
      {results.length > 0 && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h3 className="text-xl font-semibold mb-4">Import Results</h3>
          <div className="bg-gray-50 border border-gray-200 rounded p-4">
            <pre className="text-xs text-gray-700 overflow-auto">
              {JSON.stringify(results, null, 2)}
            </pre>
          </div>
        </div>
      )}

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
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-indigo-600 hover:bg-indigo-700 text-white"
          }`}
        >
          {auditLoading ? "Loading..." : "Audit Venue"}
        </button>
      </div>

      {/* Audit Modal - Placeholder */}
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

            {/* Modal Body - Placeholder */}
            <div className="overflow-y-auto flex-1 p-6">
              <div className="grid grid-cols-2 gap-4 mb-6">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-blue-600">
                    {auditData.total_dpsks || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">DPSKs</div>
                </div>
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-purple-600">
                    {auditData.total_policy_sets || 0}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Policy Sets</div>
                </div>
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded p-4">
                <p className="text-sm text-gray-600 italic">
                  Detailed audit data will be displayed here
                </p>
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
