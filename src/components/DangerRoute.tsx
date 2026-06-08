import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

const DangerRoute = ({ element }: { element: ReactElement }) => {
  const { isAuthenticated, dangerEnabled } = useAuth();

  if (isAuthenticated === null) {
    return null;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!dangerEnabled) {
    return <Navigate to="/" replace />;
  }

  return element;
};

export default DangerRoute;
