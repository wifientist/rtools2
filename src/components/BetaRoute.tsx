import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactNode } from "react";

const BetaRoute = ({ element }: { element: ReactNode }) => {
  const { isAuthenticated, betaEnabled } = useAuth();
  return isAuthenticated && betaEnabled ? element : <Navigate to="/" />;
};

export default BetaRoute;
