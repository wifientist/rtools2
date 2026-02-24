import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

const AlphaRoute = ({ element }: { element: ReactElement }) => {
  const { isAuthenticated, alphaEnabled } = useAuth();

  if (isAuthenticated === null) {
    return null;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!alphaEnabled) {
    return <Navigate to="/" replace />;
  }

  return element;
};

export default AlphaRoute;
