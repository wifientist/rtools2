import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext"; // Update path as needed

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export function useMspDetails() {
    const { activeControllerId } = useAuth();
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!activeControllerId) {
            setError("No active controller selected");
            setLoading(false);
            return;
        }

        const controller = new AbortController();
        setLoading(true);

        fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/msp/fulldetails`, {
        //fetch(`${API_BASE_URL}/fer1agga/msp/fulldetails`, {
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
    }, [activeControllerId]);

    return { data, loading, error };
}
