import { createContext, useContext, useState, useEffect, ReactNode } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Define context structure
interface AuthContextType {
  isAuthenticated: boolean | null;
  userRole: string | null;
  userId: number | null;
  betaEnabled: boolean;
  activeTenantId: number | null;
  activeTenantName: string | null;
  secondaryTenantId: number | null;
  secondaryTenantName: string | null;
  tenants: { id: number; name: string }[];
  roleHierarchy: { [key: string]: number };
  setActiveTenantId: (id: number) => void;
  setActiveTenantName: (name: string) => void;
  setSecondaryTenantId: (id: number) => void;
  setSecondaryTenantName: (name: string) => void;
  setBetaEnabled: (enabled: boolean) => void;
  checkAuth: () => Promise<void>;
  logout: () => void;
}

// Create context
const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Custom hook to use auth context
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};

// Provider component
export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [userRole, setUserRole] = useState<string | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const [betaEnabled, setBetaEnabled] = useState<boolean>(false);
  const [tenants, setTenants] = useState<{ id: number; name: string }[]>([]);
  const [activeTenantId, setActiveTenantId] = useState<number | null>(null);
  const [activeTenantName, setActiveTenantName] = useState<string | null>(null);
  const [secondaryTenantId, setSecondaryTenantId] = useState<number | null>(null);
  const [secondaryTenantName, setSecondaryTenantName] = useState<string | null>(null);
  const [roleHierarchy, setRoleHierarchy] = useState<{ [key: string]: number }>({});


  // Helper to refresh access token using refresh token
  const refreshAccessToken = async (): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });

      if (response.ok) {
        console.log("Access token refreshed successfully");
        return true;
      } else {
        console.warn("Failed to refresh access token");
        return false;
      }
    } catch (error) {
      console.error("Error refreshing token:", error);
      return false;
    }
  };

  const checkAuth = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/status`, {
        method: "GET",
        credentials: "include",
        headers: {
          "Cache-Control": "no-store",
        },
      });

      if (!response.ok) {
        // If auth check fails, try to refresh the access token
        if (response.status === 401) {
          console.log("Access token expired, attempting refresh...");
          const refreshed = await refreshAccessToken();

          if (refreshed) {
            // Retry auth check with new access token
            return checkAuth();
          }
        }

        const errorData = await response.json();
        console.warn("Auth check failed:", errorData.error);

        setIsAuthenticated(false);
        setUserRole(null);
        setUserId(null);
        setBetaEnabled(false);
        setTenants([]); // 🔥 clear tenants on auth fail
        setActiveTenantId(null);
        setActiveTenantName(null);
        setSecondaryTenantId(null);
        setSecondaryTenantName(null);
        setRoleHierarchy({});
        return;
      }

      const data = await response.json();
      //console.log("Auth check success:", data);

      setIsAuthenticated(true);
      setUserRole(data.role);
      setUserId(data.id);
      setBetaEnabled(data.beta_enabled || false);
      setActiveTenantId(data.active_tenant_id || null);
      setSecondaryTenantId(data.secondary_tenant_id || null);
      //console.log("Updated activeTenantId to", data.active_tenant_id);
      //console.log("Updated secondaryTenantId to", data.secondary_tenant_id);
      
      // 🔥 New: Fetch tenants when auth succeeds
      const tenantsResponse = await fetch(`${API_BASE_URL}/tenants/mine`, {
        method: "GET",
        credentials: "include",
        headers: {
          "Cache-Control": "no-store",
        },
      });
      if (tenantsResponse.ok) {
        const tenantsData = await tenantsResponse.json();
        setTenants(tenantsData); // [{ id, instance_name }]
        const activeTenant = tenantsData.find(t => t.id === data.active_tenant_id);
        setActiveTenantName(activeTenant ? activeTenant.name : null);
        const secondaryTenant = tenantsData.find(ts => ts.id === data.secondary_tenant_id);
        setSecondaryTenantName(secondaryTenant ? secondaryTenant.name : null);
      } else {
        console.error("Failed to fetch tenants");
        setTenants([]);
        setActiveTenantName(null); // Clear name if tenant fetch failed
        setSecondaryTenantName(null); // Clear name if tenant fetch failed
      }

      const rolesResponse = await fetch(`${API_BASE_URL}/auth/roles`);
      if (rolesResponse.ok) {
        const rolesData = await rolesResponse.json();
        setRoleHierarchy(rolesData.hierarchy);
      } else {
        console.warn("Failed to fetch role hierarchy");
        setRoleHierarchy({});
      }

    } catch (error) {
      console.error("Auth check error:", error);

      setIsAuthenticated(false);
      setUserRole(null);
      setUserId(null);
      setBetaEnabled(false);
      setActiveTenantId(null);
      setActiveTenantName(null);
      setSecondaryTenantId(null);
      setSecondaryTenantName(null);
      setTenants([]);
      setRoleHierarchy({});

    }
  };

  useEffect(() => {
    checkAuth();
  }, []);

  // Auto-refresh access token every 11 hours (before 12-hour expiry)
  useEffect(() => {
    if (!isAuthenticated) return;

    const refreshInterval = setInterval(() => {
      console.log("Auto-refreshing access token...");
      refreshAccessToken();
    }, 11 * 60 * 60 * 1000); // 11 hours in milliseconds

    return () => clearInterval(refreshInterval);
  }, [isAuthenticated]);

  const logout = async () => {
    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });

      // setIsAuthenticated(false);
      // setUserRole(null);
      // setUserId(null);

      await checkAuth();      
      
    } catch (error) {
      console.error("Logout failed", error);
    }
  };

  return (
    <AuthContext.Provider value={{
      isAuthenticated,
      userId,
      userRole,
      betaEnabled,
      activeTenantId,
      activeTenantName,
      secondaryTenantId,
      secondaryTenantName,
      tenants,
      roleHierarchy,
      setActiveTenantId,
      setActiveTenantName,
      setSecondaryTenantId,
      setSecondaryTenantName,
      setBetaEnabled,
      checkAuth,
      logout
    }}>
      {isAuthenticated === null ? <div className="text-center p-4">Checking session...</div> : children}
    </AuthContext.Provider>
  );
};
