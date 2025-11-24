import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const { checkAuth } = useAuth();

const handleSelectTenant = async (tenantId: number) => {
  const response = await fetch(`${API_BASE_URL}/auth/set-active-tenant`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ tenant_id: tenantId }),
  });

  if (response.ok) {
    // Refresh local auth state after changing tenant
    await checkAuth();
  } else {
    console.error("Failed to switch tenants");
  }
};
