import React from "react";
import { useMspDetails } from "@/hooks/useMspDetails";
import { useMspEcs } from "@/hooks/useMspEcs"; // adjust path as needed

import { useAuth } from "@/context/AuthContext";
import EcSelector from "@/components/EcSelector"; // adjust the import path as needed

import ReactJson from 'react-json-view';


function Snapshot() {
    const { activeTenantName } = useAuth();
    const { data, loading, error } = useMspDetails();
    const { ecData, loading: loadingEcs, error: errorEcs } = useMspEcs();    

    if (loading || loadingEcs) return <p className="p-4">Loading MSP details...</p>;
    if (error || errorEcs) return <p className="p-4">Error loading MSP details: {error}</p>;

    return (
        <div className="p-4">
            <h1 className="text-2xl font-bold mb-4">MSP Details</h1>
            <h2 className="text-xl font-bold mb-4">
                {activeTenantName ? `for ${activeTenantName}` : ""}
            </h2>

            <section className="mb-4">
                <h2 className="font-semibold">End Customers</h2>
                <ReactJson src={data.ecs} />
            </section>

            <section className="mb-4">
                <h2 className="font-semibold">Labels</h2>
                <ReactJson src={data.labels} />
            </section>

            <section className="mb-4">
                <h2 className="font-semibold">Tech Partners</h2>
                <ReactJson src={data.tech_partners} />
            </section>

            <section className="mb-4">
                <h2 className="font-semibold">Entitlements</h2>
                <ReactJson src={data.entitlements} />
            </section>

            <section className="mb-4">
                <h2 className="font-semibold">MSP Entitlements</h2>
                <ReactJson src={data.msp_entitlements} />
            </section>

            <section>
                <h2 className="font-semibold">MSP Admins</h2>
                <ReactJson src={data.msp_admins} />
            </section>

            <div>
              <h2 className="text-xl font-bold mb-4">Select an End Customer:</h2>
              <EcSelector ecData={ecData} />
            </div>

        </div>
    );
}

export default Snapshot;
