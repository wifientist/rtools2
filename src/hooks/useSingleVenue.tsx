import { useState, useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export function useSingleVenue(controllerId: number | null, tenantId: string | number | null) {
  const [venueData, setVenueData] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!controllerId || !tenantId) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${API_BASE_URL}/r1/${controllerId}/venues/${tenantId}`, {
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
          setVenueData(json);
        } else if (json.data && Array.isArray(json.data)) {
          setVenueData(json.data);
        } else {
          setVenueData([]);
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
  }, [controllerId, tenantId]);

  return { venueData, loading, error };
}
