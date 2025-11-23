import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export default function TenantManager() {
  const { tenants, activeTenantId, secondaryTenantId, checkAuth } = useAuth();

  useEffect(() => {
    checkAuth(); // initial load
    //console.log("Running checkAuth on mount...");
  
    // const interval = setInterval(() => {
    //   checkAuth(); // check every 15 seconds
    //   console.log("Running checkAuth every 10 seconds...");
    // }, 10000);
  
    // return () => clearInterval(interval);
  }, []);

  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    tenant_id: "",
    client_id: "",
    shared_secret: "",
  });

  async function handleActiveTenantSelect(tenantId: number) {
    try {
      await fetch(`${API_BASE_URL}/tenants/set-active-tenant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ tenant_id: tenantId }),
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to switch tenant", error);
    }
  }

  async function handleSecondaryTenantSelect(tenantId: number) {
    try {
      await fetch(`${API_BASE_URL}/tenants/set-secondary-tenant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ tenant_id: tenantId }),
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to switch tenant", error);
    }
  }

  async function handleDeleteTenant(tenantId: number) {
    if (!confirm("Are you sure you want to delete this tenant?")) return;

    try {
      await fetch(`${API_BASE_URL}/tenants/${tenantId}`, {
        method: "DELETE",
        credentials: "include",
      });
      await checkAuth();
    } catch (error) {
      console.error("Failed to delete tenant", error);
    }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setFormData(prev => ({
      ...prev,
      [e.target.name]: e.target.value,
    }));
  }

  async function handleAddTenant() {
    try {
      await fetch(`${API_BASE_URL}/tenants/new`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(formData),
      });
      setShowForm(false);
      setFormData({ name: "", tenant_id: "", client_id: "", shared_secret: "" });
      await checkAuth();
    } catch (error) {
      console.error("Failed to add tenant", error);
    }
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-4">Manage Tenants</h2>

      <button
        onClick={() => setShowForm(prev => !prev)}
        className="mb-6 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
      >
        {showForm ? "Cancel" : "Add New Tenant"}
      </button>

      {showForm && (
        <div className="bg-gray-100 p-4 rounded-lg mb-6 space-y-3 shadow-inner">
          <input
            className="w-full border p-2 rounded"
            placeholder="Tenant Name"
            name="name"
            value={formData.name}
            onChange={handleInputChange}
          />
          <input
            className="w-full border p-2 rounded"
            placeholder="Tenant ID"
            name="tenant_id"
            value={formData.tenant_id}
            onChange={handleInputChange}
          />
          <input
            className="w-full border p-2 rounded"
            placeholder="Client ID"
            name="client_id"
            value={formData.client_id}
            onChange={handleInputChange}
          />
          <input
            className="w-full border p-2 rounded"
            placeholder="Shared Secret"
            name="shared_secret"
            value={formData.shared_secret}
            onChange={handleInputChange}
          />
          <button
            onClick={handleAddTenant}
            className="mt-2 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Save Tenant
          </button>
        </div>
      )}

      {tenants.length === 0 ? (
        <p className="text-gray-600">No tenants found.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {tenants.map(tenant => (
            <div
              key={tenant.id}
              className={`p-4 border rounded-lg shadow-sm transition-all ${
                tenant.id === activeTenantId
                  ? "border-blue-500 bg-blue-50"
                  : tenant.id === secondaryTenantId
                  ? "border-green-500 bg-green-50"
                  : "border-gray-200 hover:bg-gray-100"
              }`}
            >
              <div className="flex justify-between items-start mb-2">
                <div>
                  <h3 className="text-lg font-semibold">{tenant.name}</h3>
                  <p className="text-xs text-gray-600">Tenant ID: {tenant.tenant_id}</p>
                </div>
                {tenant.id === activeTenantId && (
                  <span className="text-xs text-blue-600 font-semibold">Active</span>
                )}
                {tenant.id === secondaryTenantId && (
                  <span className="text-xs text-green-600 font-semibold ml-2">Secondary</span>
)}

              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button
                  onClick={() => handleActiveTenantSelect(tenant.id)}
                  className="text-sm text-blue-600 hover:underline"
                >
                  Set Active
                </button>
                <button
                  onClick={() => handleSecondaryTenantSelect(tenant.id)}
                  className="text-sm text-green-600 hover:underline"
                >
                  Set Secondary
                </button>
                <button
                  onClick={() => handleDeleteTenant(tenant.id)}
                  className="text-sm text-red-600 hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
