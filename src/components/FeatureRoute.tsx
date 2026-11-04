import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import type { ReactElement } from "react";

/** Guards a page by a server-computed feature_access flag (see constants/access.py). */
const FeatureRoute = ({ element, feature }: { element: ReactElement; feature: "migration_dashboard" | "migration_tracker" }) => {
  const { isAuthenticated, featureAccess } = useAuth();

  if (isAuthenticated === null) {
    return null;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!featureAccess[feature]) {
    return <Navigate to="/" replace />;
  }

  return element;
};

export default FeatureRoute;
