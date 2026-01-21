import { useState, useEffect } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface DPSKPool {
  id: string;
  name: string;
  description?: string;
  identityGroupId?: string;
  identityGroupName?: string;
  maxDevicesPerUser?: number;
  passphraseCount?: number;
}

interface PaginatedResponse {
  data?: DPSKPool[];
  total?: number;
  page?: number;
  pageSize?: number;
}

export function useDpskPools(controllerId: number | null, tenantId: string | null) {
  const [pools, setPools] = useState<DPSKPool[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!controllerId) {
      setLoading(false);
      setPools([]);
      return;
    }

    const abortController = new AbortController();
    setLoading(true);
    setError(null);

    const fetchAllPages = async () => {
      const PAGE_SIZE = 500;
      let allPools: DPSKPool[] = [];
      let currentPage = 1;
      let hasMore = true;

      try {
        while (hasMore) {
          const params = new URLSearchParams();
          if (tenantId) {
            params.append("tenant_id", tenantId);
          }
          params.append("page", String(currentPage));
          params.append("limit", String(PAGE_SIZE));

          const response = await fetch(
            `${API_BASE_URL}/r1/${controllerId}/dpsk/pools?${params.toString()}`,
            {
              method: "GET",
              credentials: "include",
              signal: abortController.signal,
            }
          );

          if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
          }

          const json: PaginatedResponse | DPSKPool[] = await response.json();

          // Handle both array and paginated object responses
          let pagePools: DPSKPool[] = [];
          let total = 0;

          if (Array.isArray(json)) {
            // Direct array response (no pagination info)
            pagePools = json;
            hasMore = false; // Can't paginate without info
          } else if (json.data && Array.isArray(json.data)) {
            // Paginated response with data array
            pagePools = json.data;
            total = json.total || 0;

            // Check if there are more pages
            const fetchedSoFar = allPools.length + pagePools.length;
            hasMore = fetchedSoFar < total;
          } else {
            // Unknown format
            hasMore = false;
          }

          allPools = [...allPools, ...pagePools];
          currentPage++;

          // Safety limit to prevent infinite loops
          if (currentPage > 100) {
            console.warn("useDpskPools: Hit page limit, stopping pagination");
            hasMore = false;
          }
        }

        setPools(allPools);
        setLoading(false);
      } catch (err: any) {
        if (err.name !== "AbortError") {
          setError(err.message);
          setLoading(false);
        }
      }
    };

    fetchAllPages();

    return () => abortController.abort();
  }, [controllerId, tenantId]);

  return { pools, loading, error };
}
