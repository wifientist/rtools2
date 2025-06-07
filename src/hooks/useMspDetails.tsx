import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext"; // Update path as needed

export function useMspDetails() {
    const { activeTenantId } = useAuth();
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!activeTenantId) {
            setError("No active tenant selected");
            setLoading(false);
            return;
        }

        const controller = new AbortController();
        setLoading(true);

        //fetch(`/api/fer1agg/msp/fulldetails/${activeTenantId}`, {
        fetch(`/api/fer1agg/msp/fulldetails`, {
                method: "GET",
            credentials: "include",
            signal: controller.signal
        })
            .then(res => {
                if (!res.ok) {
                    throw new Error(`HTTP error ${res.status}`);
                }
                return res.json();
            })
            .then(json => {
                setData(json.data);
                setLoading(false);
            })
            .catch(err => {
                if (err.name !== "AbortError") {
                    setError(err.message);
                    setLoading(false);
                }
            });

        return () => controller.abort();
    }, [activeTenantId]);

    return { data, loading, error };
}
