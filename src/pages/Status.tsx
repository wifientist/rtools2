import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

//console.log('Environment variables:', import.meta.env);

const Status = () => {
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
      <h2 className="text-2xl font-bold">Ruckus Tools API Status</h2>
      <p className="mt-4 text-sm">
        API Status: <span className="font-bold">{status || "checking..."}</span>
      </p>
    </div>
  );
};

export default Status;
