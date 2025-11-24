import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

const AdminRoute = ({ element }: { element: JSX.Element }) => {
  const { isAuthenticated, userRole } = useAuth();
  return isAuthenticated && (userRole === "admin" || userRole === "super") ? element : <Navigate to="/" />;
};

export default AdminRoute;
