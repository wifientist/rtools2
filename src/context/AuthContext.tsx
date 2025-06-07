import { createContext, useContext, useState, useEffect, ReactNode } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Define context structure
interface AuthContextType {
  isAuthenticated: boolean | null;
  userRole: string | null;
  userId: number | null;
  activeTenantId: number | null;
  activeTenantName: string | null;
  tenants: { id: number; name: string }[];
  roleHierarchy: { [key: string]: number };
  setActiveTenantId: (id: number) => void;
  setActiveTenantName: (name: string) => void;
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
  const [tenants, setTenants] = useState<{ id: number; name: string }[]>([]);
  const [activeTenantId, setActiveTenantId] = useState<number | null>(null);
  const [activeTenantName, setActiveTenantName] = useState<string | null>(null);
  const [roleHierarchy, setRoleHierarchy] = useState<{ [key: string]: number }>({});


  const checkAuth = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/status`, {
        method: "GET",
        credentials: "include",
      });

      if (!response.ok) {
        const errorData = await response.json();
        console.warn("Auth check failed:", errorData.error);

        setIsAuthenticated(false);
        setUserRole(null);
        setUserId(null);
        setTenants([]); // 🔥 clear tenants on auth fail
        setActiveTenantId(null);
        setActiveTenantName(null);
        setRoleHierarchy({});
        return;
      }

      const data = await response.json();

      setIsAuthenticated(true);
      setUserRole(data.role);
      setUserId(data.id);
      setActiveTenantId(data.active_tenant_id || null);
      
      // 🔥 New: Fetch tenants when auth succeeds
      const tenantsResponse = await fetch(`${API_BASE_URL}/tenants/mine`, {
        method: "GET",
        credentials: "include",
      });
      if (tenantsResponse.ok) {
        const tenantsData = await tenantsResponse.json();
        setTenants(tenantsData); // [{ id, instance_name }]
        const activeTenant = tenantsData.find(t => t.id === data.active_tenant_id);
        setActiveTenantName(activeTenant ? activeTenant.name : null);
      } else {
        console.error("Failed to fetch tenants");
        setTenants([]);
        setActiveTenantName(null); // Clear name if tenant fetch failed
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
      setActiveTenantId(null);
      setActiveTenantName(null);
      setTenants([]);
      setRoleHierarchy({});

    }
  };

  useEffect(() => {
    checkAuth();
  }, []);

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
      activeTenantId,
      activeTenantName,
      tenants,
      roleHierarchy,
      setActiveTenantId,
      setActiveTenantName,
      checkAuth,
      logout
    }}>
      {isAuthenticated === null ? <div className="text-center p-4">Checking session...</div> : children}
    </AuthContext.Provider>
  );
};
