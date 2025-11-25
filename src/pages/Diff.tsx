import React, { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
//import { useDualMspEcs } from "@/hooks/useDualMspEcs";
//import { useDualEc } from "@/hooks/useDualEc";
//import DoubleECSelect from "@/components/DoubleECSelect";
import DoubleEc from "@/components/DoubleEc";
import ECComparisonTable from "@/components/ECComparisonTable";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

function Diff() {
  const { activeControllerId, secondaryControllerId } = useAuth();
  //const { activeEcData, secondaryEcData, loadingEcs, errorEcs } = useDualMspEcs();
  const [selectedSource, setSelectedSource] = useState(null);
  const [selectedDestination, setSelectedDestination] = useState(null);
  const [sourceDetails, setSourceDetails] = useState(null);
  const [destinationDetails, setDestinationDetails] = useState(null);

  const handleSelectionChange = (sourceId, destinationId) => {
    setSelectedSource(sourceId);
    setSelectedDestination(destinationId);
    setSourceDetails(null);
    setDestinationDetails(null);
  };

  useEffect(() => {
    const fetchDetails = async () => {
      if (selectedSource && selectedDestination && selectedSource !== selectedDestination) {
        try {
          const [srcRes, destRes] = await Promise.all([
            fetch(`${API_BASE_URL}/fer1agg/${activeControllerId}/tenant/fulldetails?tenant_id=${selectedSource}`),
            fetch(`${API_BASE_URL}/fer1agg/${secondaryControllerId}/tenant/fulldetails?tenant_id=${selectedDestination}`),
          ]);
          const [srcData, destData] = await Promise.all([
            srcRes.json(),
            destRes.json(),
          ]);
          console.log('Source data sections:', Object.keys(srcData?.data || {}));
          console.log('Destination data sections:', Object.keys(destData?.data || {}));
          console.log('Source WLANs:', srcData?.data?.wlans);
          console.log('Destination WLANs:', destData?.data?.wlans);
          setSourceDetails(srcData);
          setDestinationDetails(destData);
        } catch (error) {
          console.error("Error fetching EC details:", error);
        }
      }
    };

    fetchDetails();
  }, [selectedSource, selectedDestination]);

  //if (loadingEcs) return <p className="p-4">Loading End Customers...</p>;
  //if (errorEcs) return <p className="p-4 text-red-500">Failed to load ECs: {errorEcs}</p>;

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Compare End Customers</h2>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <DoubleEc
          onSelectionChange={handleSelectionChange}
          initialSource={selectedSource}
          initialDestination={selectedDestination}
          showActions={false}
          disabled={false}
        />
      </div>

      {selectedSource && selectedDestination && selectedSource === selectedDestination && (
        <p className="text-red-500 mb-4">Source and destination cannot be the same EC.</p>
      )}

      {sourceDetails && destinationDetails && (
        <ECComparisonTable source={sourceDetails} destination={destinationDetails} />
      )}

      {sourceDetails && destinationDetails && (
        <div className="columns mt-6">
                <p>Raw ecData:</p>

          <div className="column has-background-light p-4 rounded">
            <h3 className="font-semibold text-lg mb-2">Source EC Details</h3>
            <pre className="text-xs text-left overflow-auto">
              {JSON.stringify(sourceDetails, null, 2)}
            </pre>
          </div>
          <div className="column has-background-light p-4 rounded">
            <h3 className="font-semibold text-lg mb-2">Destination EC Details</h3>
            <pre className="text-xs text-left overflow-auto">
              {JSON.stringify(destinationDetails, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default Diff;
