import { useState, useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export function useDualMspEcs() {
  const [activeEcData, setActiveEcData] = useState([]);
  const [secondaryEcData, setSecondaryEcData] = useState([]);
  const [loadingEcs, setLoading] = useState(true);
  const [errorEcs, setError] = useState<null | string>(null);

  useEffect(() => {
    async function fetchBoth() {
      try {
        const [activeRes, secondaryRes] = await Promise.all([
          fetch(`${API_BASE_URL}/fer1agg/ec/active`, { credentials: "include" }),
          fetch(`${API_BASE_URL}/fer1agg/ec/secondary`, { credentials: "include" })
        ]);
        const [activeJson, secondaryJson] = await Promise.all([
          activeRes.json(),
          secondaryRes.json()
        ]);
        setActiveEcData(activeJson.data || []);
        setSecondaryEcData(secondaryJson.data || []);
      } catch (err) {
        console.error("Failed to fetch EC data:", err);
        setError("Failed to load EC data.");
      } finally {
        setLoading(false);
      }
    }

    fetchBoth();
  }, []);

  return { activeEcData, secondaryEcData, loadingEcs, errorEcs };
}
