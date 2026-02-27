import { createContext, useContext, useState, useEffect, useRef, type ReactNode } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface FeatureAccess {
  migration_dashboard: boolean;
}

// Define context structure
interface AuthContextType {
  isAuthenticated: boolean | null;
  userRole: string | null;
  userId: number | null;
  companyId: number | null;
  featureAccess: FeatureAccess;
  betaEnabled: boolean;
  alphaEnabled: boolean;
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
  setAlphaEnabled: (enabled: boolean) => void;
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
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [featureAccess, setFeatureAccess] = useState<FeatureAccess>({ migration_dashboard: false });
  const [betaEnabled, setBetaEnabled] = useState<boolean>(false);
  const [alphaEnabled, setAlphaEnabled] = useState<boolean>(false);
  const [controllers, setControllers] = useState<{ id: number; name: string; controller_type: string; controller_subtype: string | null; r1_tenant_id: string | null; r1_region: string | null }[]>([]);
  const [activeControllerId, setActiveControllerId] = useState<number | null>(null);
  const [activeControllerName, setActiveControllerName] = useState<string | null>(null);
  const [secondaryControllerId, setSecondaryControllerId] = useState<number | null>(null);
  const [secondaryControllerName, setSecondaryControllerName] = useState<string | null>(null);
  const [roleHierarchy, setRoleHierarchy] = useState<{ [key: string]: number }>({});

  // Ref to track in-flight refresh promises (prevents concurrent refresh attempts)
  const refreshPromiseRef = useRef<Promise<boolean> | null>(null);

  // Helper to refresh access token using refresh token
  const refreshAccessToken = async (): Promise<boolean> => {
    // If refresh already in progress, return that promise to prevent race conditions
    if (refreshPromiseRef.current) {
      return refreshPromiseRef.current;
    }

    const refreshPromise = (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });

        if (response.ok) {
          return true;
        } else {
          return false;
        }
      } catch {
        return false;
      } finally {
        refreshPromiseRef.current = null;
      }
    })();

    refreshPromiseRef.current = refreshPromise;
    return refreshPromise;
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
        // If auth check fails, try to refresh the access token (with retry)
        if (response.status === 401) {
          let refreshed = await refreshAccessToken();

          if (!refreshed) {
            // First attempt failed — wait briefly and retry once more
            // (handles transient network blips or race conditions)
            await new Promise(resolve => setTimeout(resolve, 2000));
            refreshed = await refreshAccessToken();
          }

          if (refreshed) {
            // Retry auth check with new access token
            return checkAuth();
          }
        }

        setIsAuthenticated(false);
        setUserRole(null);
        setUserId(null);
        setCompanyId(null);
        setFeatureAccess({ migration_dashboard: false });
        setBetaEnabled(false);
        setAlphaEnabled(false);
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
      setCompanyId(data.company_id ?? null);
      setFeatureAccess(data.feature_access ?? { migration_dashboard: false });
      setBetaEnabled(data.beta_enabled || false);
      setAlphaEnabled(data.alpha_enabled || false);

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
        setControllers([]);
        setActiveControllerName(null);
        setSecondaryControllerName(null);
      }

      const rolesResponse = await fetch(`${API_BASE_URL}/auth/roles`, {
        credentials: "include",
      });
      if (rolesResponse.ok) {
        const rolesData = await rolesResponse.json();
        setRoleHierarchy(rolesData.hierarchy);
      } else {
        setRoleHierarchy({});
      }

    } catch {
      setIsAuthenticated(false);
      setUserRole(null);
      setUserId(null);
      setCompanyId(null);
      setFeatureAccess({ migration_dashboard: false });
      setBetaEnabled(false);
      setAlphaEnabled(false);
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

  // Auto-refresh access token before it expires (60-min token, refresh at 25 min)
  // With visibility API support to handle backgrounded tabs
  useEffect(() => {
    if (!isAuthenticated) return;

    let intervalId: NodeJS.Timeout;
    let retryTimeoutId: NodeJS.Timeout;
    let lastRefreshTime = Date.now();
    const REFRESH_INTERVAL = 25 * 60 * 1000; // 25 minutes (35-min buffer before 60-min expiry)
    const RETRY_DELAY = 60 * 1000; // Retry failed refresh after 60 seconds

    const doRefresh = async () => {
      const success = await refreshAccessToken();
      if (success) {
        lastRefreshTime = Date.now();
      } else {
        // Refresh failed — schedule a retry so we don't silently lose the session
        retryTimeoutId = setTimeout(doRefresh, RETRY_DELAY);
      }
      return success;
    };

    const startInterval = () => {
      intervalId = setInterval(() => {
        // Only refresh if page is visible (prevents throttled background timers)
        if (document.visibilityState === 'visible') {
          doRefresh();
        }
      }, REFRESH_INTERVAL);
    };

    const handleVisibilityChange = async () => {
      if (document.visibilityState === 'visible') {
        const timeSinceRefresh = Date.now() - lastRefreshTime;

        if (timeSinceRefresh > REFRESH_INTERVAL) {
          // Access token likely expired while tab was backgrounded.
          // Refresh it first, then re-check auth status.
          const success = await doRefresh();
          if (success) {
            checkAuth();
          }
        }
      }
    };

    startInterval();
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(intervalId);
      clearTimeout(retryTimeoutId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [isAuthenticated]);

  const logout = async () => {
    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });

      await checkAuth();

    } catch {
      // Logout failed — checkAuth will handle the state
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
      companyId,
      featureAccess,
      betaEnabled,
      alphaEnabled,
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
      setAlphaEnabled,
      checkAuth,
      logout
    }}>
      {isAuthenticated === null ? <div className="text-center p-4">Checking session...</div> : children}
    </AuthContext.Provider>
  );
};
