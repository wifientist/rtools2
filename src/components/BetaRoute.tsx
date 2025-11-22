import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

const BetaRoute = ({ element }: { element: JSX.Element }) => {
  const { isAuthenticated, betaEnabled } = useAuth();
  return isAuthenticated && betaEnabled ? element : <Navigate to="/" />;
};

export default BetaRoute;
