import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Define context structure
interface AuthContextType {
  isAuthenticated: boolean | null;
  userRole: string | null;
  userId: number | null;
  betaEnabled: boolean;
  activeControllerId: number | null;
  activeControllerName: string | null;
  activeControllerType: string | null;
  activeControllerSubtype: string | null;
  secondaryControllerId: number | null;
  secondaryControllerName: string | null;
  secondaryControllerType: string | null;
  secondaryControllerSubtype: string | null;
  controllers: { id: number; name: string; controller_type: string; controller_subtype: string | null; r1_tenant_id: string | null; r1_region: string | null }[];
  roleHierarchy: { [key: string]: number };
  setActiveControllerId: (id: number) => void;
  setActiveControllerName: (name: string) => void;
  setSecondaryControllerId: (id: number) => void;
  setSecondaryControllerName: (name: string) => void;
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
  const [controllers, setControllers] = useState<{ id: number; name: string; controller_type: string; controller_subtype: string | null; r1_tenant_id: string | null; r1_region: string | null }[]>([]);
  const [activeControllerId, setActiveControllerId] = useState<number | null>(null);
  const [activeControllerName, setActiveControllerName] = useState<string | null>(null);
  const [secondaryControllerId, setSecondaryControllerId] = useState<number | null>(null);
  const [secondaryControllerName, setSecondaryControllerName] = useState<string | null>(null);
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
        setControllers([]);
        setActiveControllerId(null);
        setActiveControllerName(null);
        setSecondaryControllerId(null);
        setSecondaryControllerName(null);
        setRoleHierarchy({});
        return;
      }

      const data = await response.json();

      setIsAuthenticated(true);
      setUserRole(data.role);
      setUserId(data.id);
      setBetaEnabled(data.beta_enabled || false);
      setActiveControllerId(data.active_controller_id || null);
      setSecondaryControllerId(data.secondary_controller_id || null);

      // Fetch controllers when auth succeeds
      const controllersResponse = await fetch(`${API_BASE_URL}/controllers/mine`, {
        method: "GET",
        credentials: "include",
        headers: {
          "Cache-Control": "no-store",
        },
      });
      if (controllersResponse.ok) {
        const controllersData = await controllersResponse.json();
        setControllers(controllersData);
        const activeController = controllersData.find((c: any) => c.id === data.active_controller_id);
        setActiveControllerName(activeController ? activeController.name : null);
        const secondaryController = controllersData.find((c: any) => c.id === data.secondary_controller_id);
        setSecondaryControllerName(secondaryController ? secondaryController.name : null);
      } else {
        console.error("Failed to fetch controllers");
        setControllers([]);
        setActiveControllerName(null);
        setSecondaryControllerName(null);
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
      setActiveControllerId(null);
      setActiveControllerName(null);
      setSecondaryControllerId(null);
      setSecondaryControllerName(null);
      setControllers([]);
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

      await checkAuth();

    } catch (error) {
      console.error("Logout failed", error);
    }
  };

  // Compute controller types and subtypes from controllers array
  const activeControllerType = activeControllerId
    ? controllers.find(c => c.id === activeControllerId)?.controller_type || null
    : null;

  const activeControllerSubtype = activeControllerId
    ? controllers.find(c => c.id === activeControllerId)?.controller_subtype || null
    : null;

  const secondaryControllerType = secondaryControllerId
    ? controllers.find(c => c.id === secondaryControllerId)?.controller_type || null
    : null;

  const secondaryControllerSubtype = secondaryControllerId
    ? controllers.find(c => c.id === secondaryControllerId)?.controller_subtype || null
    : null;

  return (
    <AuthContext.Provider value={{
      isAuthenticated,
      userId,
      userRole,
      betaEnabled,
      activeControllerId,
      activeControllerName,
      activeControllerType,
      activeControllerSubtype,
      secondaryControllerId,
      secondaryControllerName,
      secondaryControllerType,
      secondaryControllerSubtype,
      controllers,
      roleHierarchy,
      setActiveControllerId,
      setActiveControllerName,
      setSecondaryControllerId,
      setSecondaryControllerName,
      setBetaEnabled,
      checkAuth,
      logout
    }}>
      {isAuthenticated === null ? <div className="text-center p-4">Checking session...</div> : children}
    </AuthContext.Provider>
  );
};
