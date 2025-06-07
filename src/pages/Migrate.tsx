import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

//console.log('Environment variables:', import.meta.env);

const Migrate = () => {
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/status`); // Update with your API URL
        const data = await response.json();
        setStatus(data.status);
      } catch (error) {
        console.error("API status check failed:", error);
        setStatus("error");
      }
    };

    checkStatus();
  }, []);

  return (
    <div className="text-center">
      <h2 className="text-2xl font-bold">AP Migration Tool</h2>
      
    </div>
  );
};

export default Migrate;
