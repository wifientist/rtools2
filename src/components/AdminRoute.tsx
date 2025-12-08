import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactNode } from "react";

const AdminRoute = ({ element }: { element: ReactNode }) => {
  const { isAuthenticated, userRole } = useAuth();
  return isAuthenticated && (userRole === "admin" || userRole === "super") ? element : <Navigate to="/" />;
};

export default AdminRoute;
