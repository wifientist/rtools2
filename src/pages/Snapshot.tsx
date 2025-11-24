import { useMspDetails } from "@/hooks/useMspDetails";
//import { useDualMspEcs } from "@/hooks/useDualMspEcs"; // adjust path as needed

import { useAuth } from "@/context/AuthContext";
//import EcSelector from "@/components/EcSelector"; // adjust the import path as needed
import { AlertCircle } from "lucide-react";
import ReactJson from 'react-json-view';


function Snapshot() {
    const { activeControllerName, activeControllerType } = useAuth();
    const { data, loading, error } = useMspDetails();
    //const { ecData, loading: loadingEcs, error: errorEcs } = useMspEcs();
    //const { activeEcData, secondaryEcData, loadingEcs, errorEcs } = useDualMspEcs();

    if (loading || loading) return <p className="p-4">Loading MSP details...</p>;

    if (error || error) {
        return (
            <div className="p-4">
                <div className="bg-red-50 border-2 border-red-200 rounded-lg p-6">
                    <div className="flex items-start gap-3">
                        <AlertCircle className="w-6 h-6 text-red-600 mt-0.5 flex-shrink-0" />
                        <div className="flex-1">
                            <h3 className="text-lg font-semibold text-red-900 mb-2">
                                Error Loading MSP Details
                            </h3>
                            <p className="text-red-800 mb-3">{error}</p>
                            {activeControllerType && activeControllerType !== "RuckusONE" && (
                                <div className="bg-white rounded-lg p-4 mt-3">
                                    <p className="text-sm text-gray-700">
                                        <strong>Note:</strong> MSP snapshots require a RuckusONE controller.
                                        Your active controller "{activeControllerName}" is a <strong>{activeControllerType}</strong> controller.
                                    </p>
                                    <p className="text-sm text-gray-600 mt-2">
                                        Please go to the <a href="/controllers" className="underline font-semibold text-blue-600">Controllers</a> page
                                        to select a RuckusONE controller.
                                    </p>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="p-4">
            <h1 className="text-2xl font-bold mb-4">MSP Details {activeControllerName ? `for ${activeControllerName}` : ""}</h1>
                
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

        </div>
    );
}

export default Snapshot;
