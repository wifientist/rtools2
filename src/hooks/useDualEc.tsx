import { useState, useEffect } from "react";

export function useDualEc() {
  const [activeEcData, setActiveEcData] = useState([]);
  const [secondaryEcData, setSecondaryEcData] = useState([]);
  const [activeRaw, setActiveRaw] = useState(null); // optional: access full object
  const [secondaryRaw, setSecondaryRaw] = useState(null);
  const [loadingEcs, setLoading] = useState(true);
  const [errorEcs, setError] = useState<null | string>(null);

  useEffect(() => {
    async function fetchCombined() {
      try {
        const res = await fetch("/api/fer1agga/ec/dual", { credentials: "include" });
        const json = await res.json();

        const active = json.data?.active || {};
        const secondary = json.data?.secondary || {};

        setActiveRaw(active);
        setSecondaryRaw(secondary);
        setActiveEcData(active.ecs || []);
        setSecondaryEcData(secondary.ecs || []);
      } catch (err) {
        console.error("Failed to fetch EC data:", err);
        setError("Failed to load EC data.");
      } finally {
        setLoading(false);
      }
    }

    fetchCombined();
  }, []);

  return {
    activeEcData,
    secondaryEcData,
    activeRaw,
    secondaryRaw,
    loadingEcs,
    errorEcs,
  };
}
