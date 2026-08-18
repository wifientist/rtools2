import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export interface LicenseBlock {
    id: string | number | null;
    sku: string | null;
    sku_tier?: string | null;
    license_type?: string | null;
    device_type?: string | null;
    quantity: number;
    effective_date: string | null;
    expiration_date: string | null;
    term_days: number | null;
    term_years: number | null;
    status: string | null;
    is_trial: boolean;
    days_remaining: number | null;
    expired: boolean;
    created_by?: string | null;
    revoked_date?: string | null;
}

export interface Cliff {
    date: string;
    days_out: number;
    quantity_lost: number;
    capacity_after: number;
    skus: string[];
}

export interface TimelineSegment {
    start: string;
    end: string;
    capacity: number;
    days: number;
}

/** Pool capacity and committed-to-ECs on one shared set of breakpoints. */
export interface CombinedSegment {
    start: string;
    end: string;
    capacity: number;
    committed: number;
    headroom: number;
}

export interface QuarterBucket {
    quarter: string;
    total: number;
    by_ec: { name: string; quantity: number }[];
}

export interface ChurnPoint {
    date: string;
    quantity: number;
}

/** Fields shared by the MSP pool and each EC's assignment position. */
interface Position {
    timeline: TimelineSegment[];
    cliffs: Cliff[];
    capacity_today?: number;
    effective_expiration?: string | null;
    days_to_effective_expiration?: number | null;
    capacity_after_first_cliff?: number | null;
    last_expiration?: string | null;
    days_to_last_expiration?: number | null;
    tail_days?: number;
    cliff_count?: number;
}

export interface Pool extends Position {
    blocks: LicenseBlock[];
    purchased: number;
    block_count: number;
    trial_quantity: number;
    courtesy?: number;
}

export interface EcPosition extends Position {
    tenant_id: string;
    name: string;
    error?: string | null;
    quantity: number;
    assignment_count: number;
    historical_count: number;
    license_types: string[];
    assignments: LicenseBlock[];
    history: LicenseBlock[];
    churn: ChurnPoint[];
}

export interface Compliance {
    license_type?: string | null;
    tenant_name?: string | null;
    total_paid?: number;
    used?: number;
    available?: number;
    expiring_soon?: number;
    next_expiration_date?: string | null;
    device_breakdown?: { device_type: string; installed: number; used: number }[];
    error?: string;
}

export interface MspLicensing {
    as_of: string;
    pool: Pool;
    compliance: Compliance;
    ecs: EcPosition[];
    assigned_total: number;
    combined_timeline: CombinedSegment[];
    quarters: QuarterBucket[];
    /** Set when the pool outlives every assignment — licenses nobody holds. */
    idle_tail: { from: string; until: string; quantity: number } | null;
    warnings: string[];
}

export function useMspLicensing() {
    const { activeControllerId } = useAuth();
    const [data, setData] = useState<MspLicensing | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const [reloadKey, setReloadKey] = useState(0);

    const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

    useEffect(() => {
        if (!activeControllerId) {
            setError("No active controller selected");
            setLoading(false);
            return;
        }

        const controller = new AbortController();
        setLoading(true);
        setError(null);

        fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/msp/licensing`, {
            method: "GET",
            credentials: "include",
            signal: controller.signal,
        })
            .then(async (res) => {
                const json = await res.json().catch(() => null);
                if (!res.ok) {
                    throw new Error(json?.detail || `HTTP error ${res.status}`);
                }
                return json;
            })
            .then((json) => {
                setData(json.data);
                setLoading(false);
            })
            .catch((err) => {
                if (err.name !== "AbortError") {
                    setError(err.message);
                    setLoading(false);
                }
            });

        return () => controller.abort();
    }, [activeControllerId, reloadKey]);

    return { data, loading, error, refresh };
}
