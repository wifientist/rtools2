import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import UserManager from "@/components/UserManager";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const Users = () => {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const checkAdmin = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/status`, {
          method: "GET",
          credentials: "include",
        });

        if (!response.ok) {
          navigate("/login");
          return;
        }

        const data = await response.json();
        if (data.role !== "admin" && data.role !== "super") {
          navigate("/");
          return;
        }

        setIsAdmin(true);
      } catch (error) {
        navigate("/login");
      }
    };

    checkAdmin();
  }, [navigate]);

  if (isAdmin === null) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        <span className="ml-3">Loading...</span>
      </div>
    );
  }

  return <UserManager />;
};

export default Users;
