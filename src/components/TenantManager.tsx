import { useState } from "react";
import { useAuth } from "@/context/AuthContext"; // ✅

export default function TenantManager() {
  const { tenants, activeTenantId, checkAuth } = useAuth(); // ✅ Grab everything you need from context
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    tenant_id: "",
    client_id: "",
    shared_secret: "",
  });

  async function handleTenantSelect(tenantId: number) {
    try {
      await fetch("/api/tenants/set-active-tenant", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({ tenant_id: tenantId }),
      });
  
      await checkAuth(); // ✅ re-check auth to reload fresh tenant state (important after new session cookie)
    } catch (error) {
      console.error("Failed to switch tenant", error);
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
      await fetch("/api/tenants/new", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(formData),
      });
      setShowForm(false);
      setFormData({ name: "", client_id: "", shared_secret: "" });

      // ⚡ After adding, re-check auth to refresh tenants list in context
      await checkAuth(); // <- assuming you import `checkAuth` from useAuth
    } catch (error) {
      console.error("Failed to add tenant", error);
    }
  }

  return (
    <div className="p-6">
      <h2 className="text-2xl font-bold mb-6">Manage R1 Tenants</h2>

      {tenants.length === 0 ? (
        <p className="mb-6 text-gray-600">No tenants found.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
          {tenants.map((tenant) => (
            <div
              key={tenant.id}
              className={`p-4 rounded-lg border shadow-sm transition cursor-pointer ${
                tenant.id === activeTenantId
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
              onClick={() => handleTenantSelect(tenant.id)}
            >
              <h3 className="text-lg font-semibold mb-1">{tenant.name}</h3>
            </div>
          ))}
        </div>
      )}

      {!showForm ? (
        <button
          onClick={() => setShowForm(true)}
          className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
        >
          Add a R1 Tenant
        </button>
      ) : (
        <div className="bg-gray-100 p-6 rounded-lg shadow-sm">
          <h3 className="text-xl font-semibold mb-4">Add New R1 Tenant</h3>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-1" htmlFor="name">
              Tenant Name
            </label>
            <input
              type="text"
              id="name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-1" htmlFor="tenant_id">
              Tenant ID
            </label>
            <input
              type="text"
              id="tenant_id"
              name="tenant_id"
              value={formData.tenant_id}
              onChange={handleInputChange}
              className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-1" htmlFor="client_id">
              Client ID
            </label>
            <input
              type="text"
              id="client_id"
              name="client_id"
              value={formData.client_id}
              onChange={handleInputChange}
              className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <div className="mb-4">
            <label className="block text-sm font-medium mb-1" htmlFor="shared_secret">
              Shared Secret
            </label>
            <input
              type="text"
              id="shared_secret"
              name="shared_secret"
              value={formData.shared_secret}
              onChange={handleInputChange}
              className="w-full p-2 border rounded-md focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <div className="mb-6">
            <p className="text-sm text-gray-500 italic">Instructions TBD</p>
          </div>

          <div className="flex gap-3">
            <button
              onClick={handleAddTenant}
              className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition"
            >
              Submit
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="px-6 py-2 bg-gray-400 text-white rounded-lg hover:bg-gray-500 transition"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
