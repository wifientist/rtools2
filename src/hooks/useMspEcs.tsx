import { useState, useEffect } from "react";

export function useMspEcs() {
  const [ecData, setEcData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<null | string>(null);

  useEffect(() => {
    async function fetchData() {
      try {
        const response = await fetch("/api/r1/msp/mspEcs", {
          credentials: "include"
        });
        const result = await response.json();
        setEcData(result.data || []);  // Defensive fallback
      } catch (error) {
        console.error("Failed to fetch EC data:", error);
        setError("Failed to load EC data.");
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, []);

  return { ecData, loading, error };
}
