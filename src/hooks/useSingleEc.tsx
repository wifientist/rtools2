import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export function useSingleEc(controllerId: number | null) {
  const [ecData, setEcData] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!controllerId) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${API_BASE_URL}/r1/${controllerId}/msp/mspEcs`, {
      method: "GET",
      credentials: "include",
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP error ${res.status}`);
        }
        return res.json();
      })
      .then((json) => {
        // Handle both array and object responses
        if (Array.isArray(json)) {
          setEcData(json);
        } else if (json.data && Array.isArray(json.data)) {
          setEcData(json.data);
        } else {
          setEcData([]);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [controllerId]);

  return { ecData, loading, error };
}
