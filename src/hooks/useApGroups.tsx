import { useState, useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export function useApGroups(controllerId: number | null, tenantId: string | number | null) {
  const [apGroups, setApGroups] = useState<any[]>([]);
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

    fetch(`${API_BASE_URL}/r1/${controllerId}/venues/${tenantId}/apgroups`, {
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
        //console.log("🎯 useApGroups - Raw Response:", json);
        //console.log("🎯 useApGroups - Response Type:", typeof json, Array.isArray(json) ? "(array)" : "(object)");

        let groups = [];

        // Handle both array and object responses
        if (Array.isArray(json)) {
          groups = json;
        } else if (json.data && Array.isArray(json.data)) {
          groups = json.data;
        }

        // Filter and process groups - only keep groups with names
        // Default groups (isDefault: true) often don't have explicit names
        const processedGroups = groups
          .filter(group => group.name && group.name.trim() !== '')
          .map(group => ({
            ...group,
            name: group.name.trim()
          }));

        //console.log("🎯 useApGroups - Original count:", groups.length);
        //console.log("🎯 useApGroups - After filtering (with names):", processedGroups.length);
        //console.log("🎯 useApGroups - Processed groups:", processedGroups.map(g => ({ id: g.id, name: g.name, isDefault: g.isDefault })));

        setApGroups(processedGroups);
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

  return { apGroups, loading, error };
}
