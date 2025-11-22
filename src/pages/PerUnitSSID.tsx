import { useState } from "react";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

function PerUnitSSID() {
  const { activeTenantId, secondaryTenantId } = useAuth();
  const [csvInput, setCsvInput] = useState("");
  const [processing, setProcessing] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  const [error, setError] = useState("");

  const handleCsvInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCsvInput(e.target.value);
    setError("");
  };

  const handleProcess = async () => {
    if (!csvInput.trim()) {
      setError("Please enter unit numbers");
      return;
    }

    if (!activeTenantId) {
      setError("Please select an active tenant first");
      return;
    }

    setProcessing(true);
    setError("");
    setResults([]);

    try {
      // Parse CSV input - split by commas, newlines, or both
      const units = csvInput
        .split(/[\n,]+/)
        .map(u => u.trim())
        .filter(u => u.length > 0);

      if (units.length === 0) {
        setError("No valid unit numbers found");
        setProcessing(false);
        return;
      }

      // TODO: Backend API call will go here
      // const response = await fetch(`${API_BASE_URL}/per-unit-ssid/process`, {
      //   method: "POST",
      //   credentials: "include",
      //   headers: { "Content-Type": "application/json" },
      //   body: JSON.stringify({
      //     tenant_id: activeTenantId,
      //     units: units,
      //   }),
      // });

      // Placeholder results for now
      setResults(
        units.map((unit, idx) => ({
          unit,
          status: "pending",
          message: "Backend processing not yet implemented",
        }))
      );
    } catch (err: any) {
      setError(err.message || "An error occurred");
    } finally {
      setProcessing(false);
    }
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

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Per-Unit SSID Configuration</h2>

      {/* Tenant Info */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
        <p className="text-sm text-gray-700">
          <strong>Active Tenant:</strong> {activeTenantId || "None selected"}
        </p>
        {!activeTenantId && (
          <p className="text-sm text-red-600 mt-2">
            ⚠️ Please select an active tenant from the Tenants page before proceeding.
          </p>
        )}
      </div>

      {/* Input Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Unit Numbers Input</h3>

        {/* CSV Text Input */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Enter unit numbers (comma or newline separated):
          </label>
          <textarea
            value={csvInput}
            onChange={handleCsvInputChange}
            placeholder="101, 102, 103&#10;or&#10;101&#10;102&#10;103"
            className="w-full h-32 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
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
          disabled={processing || !activeTenantId}
          className={`px-6 py-2 rounded font-semibold ${
            processing || !activeTenantId
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700 text-white"
          }`}
        >
          {processing ? "Processing..." : "Process Units"}
        </button>
      </div>

      {/* Results Section */}
      {results.length > 0 && (
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-xl font-semibold mb-4">Processing Results</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Unit
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Message
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {results.map((result, idx) => (
                  <tr key={idx}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {result.unit}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <span
                        className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                          result.status === "success"
                            ? "bg-green-100 text-green-800"
                            : result.status === "error"
                            ? "bg-red-100 text-red-800"
                            : "bg-yellow-100 text-yellow-800"
                        }`}
                      >
                        {result.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {result.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default PerUnitSSID;
