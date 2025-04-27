import { createContext, useContext, useState, useEffect, ReactNode } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Define context structure
interface AuthContextType {
  isAuthenticated: boolean | null;
  userRole: string | null;
  userId: number | null;
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
        return;
      }

      const data = await response.json();

      setIsAuthenticated(true);
      setUserRole(data.role);
      setUserId(data.id);
    } catch (error) {
      console.error("Auth check error:", error);

      setIsAuthenticated(false);
      setUserRole(null);
      setUserId(null);
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
    <AuthContext.Provider value={{ isAuthenticated, userId, userRole, logout }}>
      {isAuthenticated === null ? <div className="text-center p-4">Checking session...</div> : children}
    </AuthContext.Provider>
  );
};
