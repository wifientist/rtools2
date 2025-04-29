import { useState } from "react";
import { useAuth } from "@/context/AuthContext"; // assuming you have tenants stored here

export default function TenantPage() {
  const { activeTenantId, activeTenantName } = useAuth(); // or however you're tracking it
  const [connectionStatus, setConnectionStatus] = useState<"idle" | "success" | "error">("idle");
  const [loading, setLoading] = useState(false);

  async function handleLoginToExternalApi() {
    setLoading(true);
    try {
      const response = await fetch(`/api/r1/mspEntitlements`, {
        method: "GET",
        credentials: "include", // if you need cookies/session
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          tenant_id: activeTenantId,
        }),
      });

      if (response.ok) {
        setConnectionStatus("success");
      } else {
        setConnectionStatus("error");
      }
    } catch (error) {
      console.error("Connection failed:", error);
      setConnectionStatus("error");
    }
    setLoading(false);
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Tenant Page</h1>

      <div className="mb-6">
        <p><strong>ID:</strong> {activeTenantId}</p>
        <p><strong>Name:</strong> {activeTenantName}</p>
      </div>

      <div className="mb-6">
        <button
          onClick={handleLoginToExternalApi}
          disabled={loading}
          className="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded"
        >
          {loading ? "Connecting..." : "get MSP Entitlements"}
        </button>
      </div>

      {connectionStatus === "success" && (
        <p className="text-green-600 font-semibold">Connected successfully!</p>
      )}
      {connectionStatus === "error" && (
        <p className="text-red-600 font-semibold">Failed to connect.</p>
      )}
    </div>
  );
}
