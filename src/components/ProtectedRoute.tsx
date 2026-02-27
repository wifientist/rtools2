import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

const ProtectedRoute = ({ element }: { element: ReactElement }) => {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  // Still checking auth - show nothing (AuthProvider shows "Checking session...")
  if (isAuthenticated === null) {
    return null;
  }

  // Not authenticated - redirect to login
  if (!isAuthenticated) {
    console.warn('[ProtectedRoute]', new Date().toISOString(),
      `Redirecting to /login — isAuthenticated=false, was on: ${location.pathname}`);
    return <Navigate to="/login" replace />;
  }

  // Authenticated - render the element
  return element;
};

export default ProtectedRoute;
