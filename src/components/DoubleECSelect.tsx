import React, { useState } from "react";
import SimpleEcSelect from "@/components/SimpleECSelect";

export default function DoubleEcSelect({ 
  sourceEcData,
  destinationEcData, 
  onSelectionChange,
  initialSource = null,
  initialDestination = null,
  showActions = true,
  disabled = false 
}) {
  const [sourceEcId, setSourceEcId] = useState(initialSource);
  const [destinationEcId, setDestinationEcId] = useState(initialDestination);

  const handleSourceSelect = (ecId) => {
    setSourceEcId(ecId);
    // If destination is the same as new source, clear it
    if (ecId === destinationEcId) {
      setDestinationEcId(null);
      onSelectionChange?.(ecId, null);
    } else {
      onSelectionChange?.(ecId, destinationEcId);
    }
  };

  const handleDestinationSelect = (ecId) => {
    setDestinationEcId(ecId);
    // If source is the same as new destination, clear it
    if (ecId === sourceEcId) {
      setSourceEcId(null);
      onSelectionChange?.(null, ecId);
    } else {
      onSelectionChange?.(sourceEcId, ecId);
    }
  };

  const resetSelection = () => {
    setSourceEcId(null);
    setDestinationEcId(null);
    onSelectionChange?.(null, null);
  };

  const isValidSelection = sourceEcId && destinationEcId && sourceEcId !== destinationEcId;

  return (
    <div className="w-full">
      <div className="grid md:grid-cols-2 gap-6">
        <SimpleEcSelect
          ecData={sourceEcData}
          selectedEcId={sourceEcId}
          onSelect={handleSourceSelect}
          label="Source EC"
          placeholder="Search for source EC..."
          disabled={disabled}
        />
        
        <SimpleEcSelect
          ecData={destinationEcData}
          selectedEcId={destinationEcId}
          onSelect={handleDestinationSelect}
          label="Destination EC"
          placeholder="Search for destination EC..."
          disabled={disabled}
        />
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
                {sourceEcId ? sourceEcData.find(ec => ec.id === sourceEcId)?.name : "Not selected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-medium">Destination:</span>
              <span className={destinationEcId ? "text-green-600" : "text-gray-500"}>
                {destinationEcId ? destinationEcData.find(ec => ec.id === destinationEcId)?.name : "Not selected"}
              </span>
            </div>
          </div>
          
          {isValidSelection && (
            <div className="mt-2 text-sm text-blue-600 font-medium">
              ✓ Ready for next step
            </div>
          )}
          
          {sourceEcId && destinationEcId && sourceEcId === destinationEcId && (
            <div className="mt-2 text-sm text-red-600">
              ⚠️ Source and destination cannot be the same
            </div>
          )}
        </div>
      )}
    </div>
  );
}