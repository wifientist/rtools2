import React, { useState } from "react";
import { useDualEc } from "@/hooks/useDualEc";
import SimpleEcSelect from "@/components/SimpleECSelect";

export default function DoubleEc({
  onSelectionChange,
  initialSource = null,
  initialDestination = null,
  showActions = true,
  disabled = false,
  allowSameTenant = false
}) {
  const { activeEcData, secondaryEcData, activeRaw, secondaryRaw, loadingEcs, errorEcs } = useDualEc();
  const [sourceEcId, setSourceEcId] = useState(initialSource);
  const [destinationEcId, setDestinationEcId] = useState(initialDestination);

  //console.log(activeEcData, secondaryEcData, activeRaw, secondaryRaw);

  const activeList = [
    {
      id: String(activeRaw?.self?.id),
      name: activeRaw?.self?.name,
      tenantType: "SELF",
      customerCount: 0,
    },
    ...activeEcData,
  ];
  
  const secondaryList = [
    {
      id: String(secondaryRaw?.self?.id),
      name: secondaryRaw?.self?.name,
      tenantType: "SELF",
      customerCount: 0,
    },
    ...secondaryEcData,
  ];
  

  const handleSourceSelect = (ecId) => {
    setSourceEcId(ecId);
    console.log("Source selected:", ecId);
    const sourceObj = activeList.find(ec => String(ec.id) === String(ecId));
    const destinationObj = secondaryList.find(ec => String(ec.id) === String(destinationEcId));

    // If allowSameTenant is true, don't null out the source when it matches destination
    const effectiveSourceId = (allowSameTenant || ecId !== destinationEcId) ? ecId : null;

    onSelectionChange?.(
      effectiveSourceId,
      destinationEcId,
      sourceObj,
      destinationObj
    );
  };

  const handleDestinationSelect = (ecId) => {
    setDestinationEcId(ecId);
    console.log("Destination selected:", ecId);
    const sourceObj = activeList.find(ec => String(ec.id) === String(sourceEcId));
    const destinationObj = secondaryList.find(ec => String(ec.id) === String(ecId));

    // If allowSameTenant is true, don't null out the destination when it matches source
    const effectiveDestId = (allowSameTenant || ecId !== sourceEcId) ? ecId : null;

    onSelectionChange?.(
      sourceEcId,
      effectiveDestId,
      sourceObj,
      destinationObj
    );
  };
  

  const resetSelection = () => {
    setSourceEcId(null);
    setDestinationEcId(null);
    onSelectionChange?.(null, null);
  };


  const isValidSelection = sourceEcId && destinationEcId && (allowSameTenant || sourceEcId !== destinationEcId);

  if (loadingEcs) return <div>Loading ECs...</div>;
  if (errorEcs) return <div className="text-red-600">Error loading ECs: {errorEcs}</div>;

  return (
    <div className="w-full">
      <div className="grid md:grid-cols-2 gap-6">
        {/* Source Column */}
        <div>
          <h3 className="text-sm font-semibold text-gray-700 mb-1">
            Source: 
          </h3>
          <p>{activeRaw?.self?.name || "N/A"}</p>
            <SimpleEcSelect
              ecData={activeList}
              selectedEcId={sourceEcId}
              onSelect={handleSourceSelect}
              label=""
              placeholder="Search for source EC..."
              disabled={disabled}
            />
        </div>

        {/* Destination Column */}
        <div>
          <h3 className="text-sm font-semibold text-gray-700 mb-1">
            Destination: 
          </h3>
          <p>{secondaryRaw?.self?.name || "N/A"}</p>
            <SimpleEcSelect
              ecData={secondaryList}              
              selectedEcId={destinationEcId}
              onSelect={handleDestinationSelect}
              label=""
              placeholder="Search for destination EC..."
              disabled={disabled}
            />
        </div>
      </div>

      {showActions && (
        <div className="flex gap-4 mt-6">
          <button
            onClick={resetSelection}
            className="btn btn-secondary"
            disabled={disabled || (!sourceEcId && !destinationEcId)}
          >
            Reset Selection
          </button>
        </div>
      )}

      {(sourceEcId || destinationEcId) && (
        <div className="mt-6 p-4 bg-gray-50 rounded">
          <h3 className="font-medium mb-2">Current Selection:</h3>
          <div className="text-sm space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">Source:</span>
              <span className={sourceEcId ? "text-green-600" : "text-gray-500"}>
              {sourceEcId ? activeList.find(ec => String(ec.id) === String(sourceEcId))?.name : "Not selected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-medium">Destination:</span>
              <span className={destinationEcId ? "text-green-600" : "text-gray-500"}>
                {destinationEcId ? secondaryList.find(ec => String(ec.id) === destinationEcId)?.name : "Not selected"}
              </span>
            </div>
          </div>

          {isValidSelection && (
            <div className="mt-2 text-sm text-blue-600 font-medium">
              ✓ Ready for next step
            </div>
          )}

          {!allowSameTenant && sourceEcId && destinationEcId && sourceEcId === destinationEcId && (
            <div className="mt-2 text-sm text-red-600">
              ⚠️ Source and destination cannot be the same
            </div>
          )}
        </div>
      )}
    </div>
  );
}
