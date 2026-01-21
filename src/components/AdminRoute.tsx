import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

const AdminRoute = ({ element }: { element: ReactElement }) => {
  const { isAuthenticated, userRole } = useAuth();

  // Still checking auth - show nothing
  if (isAuthenticated === null) {
    return null;
  }

  // Not authenticated - redirect to login
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  // Authenticated but not admin/super - redirect to home
  if (userRole !== "admin" && userRole !== "super") {
    return <Navigate to="/" replace />;
  }

  return element;
};

export default AdminRoute;
