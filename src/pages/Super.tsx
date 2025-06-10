import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const Super = () => {
  const [isSuper, setIsSuper] = useState<boolean | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const checkAdmin = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/status`, {
          method: "GET",
          credentials: "include",
        });

        if (!response.ok) {
          navigate("/login"); // Redirect if not authenticated
          return;
        }

        const data = await response.json();
        console.log(data);
        if (data.role !== "super") {
          navigate("/"); // Redirect if not an admin
          return;
        }

        setIsSuper(true);
      } catch (error) {
        navigate("/login"); // Redirect on error
      }
    };

    checkAdmin();
  }, [navigate]);

  if (isSuper === null) {
    return <div>Loading...</div>; // Show a loading state
  }

  return (
    <div className="container mx-auto p-6">
      <h1 className="text-2xl font-bold">Super Dashboard</h1>
      <p>Welcome, Super!</p>
      {/* Add more admin features here */}
    </div>
  );
};

export default Super;
