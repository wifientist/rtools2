import { useState } from "react";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export default function TenantSecondaruy() {
  const { tenants, secondaryTenantId, secondaryTenantName, checkAuth } = useAuth(); // ✅ Grab everything you need from context
  // const [showForm, setShowForm] = useState(false);
  // const [formData, setFormData] = useState({
  //   name: "",
  //   tenant_id: "",
  //   client_id: "",
  //   shared_secret: "",
  // });

  async function handleSecondaryTenantSelect(tenantId: number) {
    try {
      await fetch(`${API_BASE_URL}/tenants/set-secondary-tenant`, {
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
  

  // function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
  //   setFormData(prev => ({
  //     ...prev,
  //     [e.target.name]: e.target.value,
  //   }));
  // }

  // async function handleAddTenant() {
  //   try {
  //     await fetch("/api/tenants/new", {
  //       method: "POST",
  //       headers: { "Content-Type": "application/json" },
  //       credentials: "include",
  //       body: JSON.stringify(formData),
  //     });
  //     setShowForm(false);
  //     setFormData({ name: "", tenant_id: "", client_id: "", shared_secret: "" });

  //     // ⚡ After adding, re-check auth to refresh tenants list in context
  //     await checkAuth(); // <- assuming you import `checkAuth` from useAuth
  //   } catch (error) {
  //     console.error("Failed to add tenant", error);
  //   }
  // }

  return (
    <div className="p-6">
      <h2 className="text-xl font-bold mb-6">Choose Secondary Tenant</h2>

      {tenants.length === 0 ? (
        <p className="mb-6 text-gray-600">No tenants found.</p>
      ) : (
        <>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
          {tenants.map((tenant) => (
            <div
              key={tenant.id}
              className={`p-4 rounded-lg border shadow-sm transition cursor-pointer ${
                tenant.id === secondaryTenantId
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
              onClick={() => handleSecondaryTenantSelect(tenant.id)}
            >
              <h3 className="text-lg font-semibold mb-1">{tenant.name}</h3>
            </div>
          ))}
        </div>
        </>
      )}
      
    </div> 
  );
}
