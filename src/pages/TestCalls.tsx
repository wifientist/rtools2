import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext"; 

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

function TestCalls() {
  const { activeTenantId, secondaryTenantId } = useAuth();

  const [endpoint, setEndpoint] = useState(`/r1/${activeTenantId}/tenant/self`);
  const [params, setParams] = useState("");
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleCallApi = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const url = `${API_BASE_URL}${endpoint}?${params}`;
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResponse(data);
    } catch (err) {
      setError(err.toString());
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-3xl mx-auto bg-white shadow rounded-lg">
      <h2 className="text-2xl font-bold mb-4">API Test Tool</h2>

      <div className="mb-4">
        <label className="block font-medium">Endpoint</label>
        <input
          type="text"
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          className="input w-full"
        />
      </div>

      <div className="mb-4">
        <label className="block font-medium">Query Parameters</label>
        <input
          type="text"
          value={params}
          onChange={(e) => setParams(e.target.value)}
          className="input w-full"
        />
      </div>

      <button
        onClick={handleCallApi}
        className="button is-primary border mb-4"
        disabled={loading}
      >
        {loading ? "Loading..." : "Call API"}
      </button>

      {error && (
        <div className="has-text-danger mb-4">
          <strong>Error:</strong> {error}
        </div>
      )}

      {response && (
        <div className="mt-4">
          <h3 className="font-semibold mb-2">Response</h3>
          <pre className="text-sm overflow-auto bg-light p-3 rounded">
            {JSON.stringify(response, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export default TestCalls;
