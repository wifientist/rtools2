import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

const AdminRoute = ({ element }: { element: JSX.Element }) => {
  const { isAuthenticated, userRole } = useAuth();
  return isAuthenticated && userRole === "admin" ? element : <Navigate to="/" />;
};

export default AdminRoute;
