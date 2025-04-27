import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext"; // Use auth context

const Navbar = () => {
  const { isAuthenticated, userRole, logout } = useAuth(); // Get auth state

  return (
    <nav className="bg-blue-600 text-white p-4">
      <div className="container mx-auto flex justify-between items-center">
        <h1 className="text-xl font-bold">Ruckus Tools</h1>
        <div className="space-x-4">
          <Link to="/" className="hover:underline">Home</Link>
          {isAuthenticated ? 
          <>
            <Link to="/profile" className="hover:underline">Profile</Link>
            <Link to="/proposals" className="hover:underline">Proposals</Link>
            {userRole === "admin" && <Link to="/admin" className="hover:underline">Admin</Link>}
            <button onClick={logout} className="bg-red-500 px-3 py-1 rounded">
                Logout
              </button>
          </> 
            : 
          null}
          {isAuthenticated ? 
          null 
            : 
          ( <>
            <Link to="/login" className="hover:underline">Login</Link>
            <Link to="/signup" className="hover:underline">Signup</Link>
          </> )}
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
