import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Bell, UserCircle } from "lucide-react";

const Toolbar = () => {
  const { isAuthenticated, activeTenantName, logout } = useAuth();

  return (
    <header className="h-16 bg-gray-700 text-gray-100 flex items-center justify-between px-6 shadow-sm">
      <div className="h-16 flex items-center justify-center font-bold text-lg">
        RUCKUS.Tools
      </div>
      <div className="text-sm">
        Active Tenant: <span className="font-semibold">{activeTenantName || "None"}</span>
      </div>
      <div className="flex items-center space-x-4">
        <Bell className="w-5 h-5 text-gray-100 cursor-pointer" />
        
        {isAuthenticated ? 
          <>
          <Link to="/profile" className="hover:underline"><UserCircle className="w-6 h-6 text-gray-100" /></Link>
          <button onClick={logout} className="text-sm bg-red-500 text-white px-3 py-1 rounded">
            Logout
          </button>
          </>
          : (
          <>
            <a href="/login" className="text-sm text-blue-500 hover:underline">Login</a>
            <a href="/signup" className="text-sm text-blue-500 hover:underline">Signup</a>
          </>
        )}
      </div>
    </header>
  );
};

export default Toolbar;
