import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

const BetaRoute = ({ element }: { element: ReactElement }) => {
  const { isAuthenticated, betaEnabled } = useAuth();

  // Still checking auth - show nothing
  if (isAuthenticated === null) {
    return null;
  }

  // Not authenticated - redirect to login
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  // Authenticated but no beta access - redirect to home
  if (!betaEnabled) {
    return <Navigate to="/" replace />;
  }

  return element;
};

export default BetaRoute;
