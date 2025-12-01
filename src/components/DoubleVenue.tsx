import { useState } from "react";

interface Venue {
  id: string;
  name?: string;
}

interface DoubleVenueProps {
  sourceVenues: Venue[];
  destinationVenues: Venue[];
  onSelectionChange: (sourceVenueId: string | null, destinationVenueId: string | null) => void;
  initialSource?: string | null;
  initialDestination?: string | null;
  disabled?: boolean;
}

export default function DoubleVenue({
  sourceVenues,
  destinationVenues,
  onSelectionChange,
  initialSource = null,
  initialDestination = null,
  disabled = false
}: DoubleVenueProps) {
  const [sourceVenueId, setSourceVenueId] = useState<string | null>(initialSource);
  const [destinationVenueId, setDestinationVenueId] = useState<string | null>(initialDestination);

  const handleSourceSelect = (venueId: string) => {
    const newSourceId = venueId || null;
    setSourceVenueId(newSourceId);
    onSelectionChange(newSourceId, destinationVenueId);
  };

  const handleDestinationSelect = (venueId: string) => {
    const newDestId = venueId || null;
    setDestinationVenueId(newDestId);
    onSelectionChange(sourceVenueId, newDestId);
  };

  const isValidSelection = sourceVenueId && destinationVenueId;
  const isSameVenue = sourceVenueId === destinationVenueId;

  return (
    <div className="w-full">
      <div className="grid md:grid-cols-2 gap-6">
        {/* Source Venue */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Source Venue ({sourceVenues.length} available)
          </label>
          <select
            className="w-full border border-gray-300 rounded-md p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            value={sourceVenueId || ""}
            onChange={(e) => handleSourceSelect(e.target.value)}
            disabled={disabled}
          >
            <option value="">Select source venue...</option>
            {sourceVenues.map((venue) => (
              <option key={venue.id} value={venue.id}>
                {venue.name || venue.id}
              </option>
            ))}
          </select>
        </div>

        {/* Destination Venue */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Destination Venue ({destinationVenues.length} available)
          </label>
          <select
            className="w-full border border-gray-300 rounded-md p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            value={destinationVenueId || ""}
            onChange={(e) => handleDestinationSelect(e.target.value)}
            disabled={disabled}
          >
            <option value="">Select destination venue...</option>
            {destinationVenues.map((venue) => (
              <option key={venue.id} value={venue.id}>
                {venue.name || venue.id}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Current Selection Display */}
      {(sourceVenueId || destinationVenueId) && (
        <div className="mt-6 p-4 bg-gray-50 rounded">
          <h3 className="font-medium mb-2">Current Selection:</h3>
          <div className="text-sm space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">Source:</span>
              <span className={sourceVenueId ? "text-green-600" : "text-gray-500"}>
                {sourceVenueId
                  ? sourceVenues.find(v => v.id === sourceVenueId)?.name || sourceVenueId
                  : "Not selected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-medium">Destination:</span>
              <span className={destinationVenueId ? "text-green-600" : "text-gray-500"}>
                {destinationVenueId
                  ? destinationVenues.find(v => v.id === destinationVenueId)?.name || destinationVenueId
                  : "Not selected"}
              </span>
            </div>
          </div>

          {isValidSelection && !isSameVenue && (
            <div className="mt-2 text-sm text-blue-600 font-medium">
              ✓ Ready to compare venues
            </div>
          )}

          {isSameVenue && sourceVenueId && (
            <div className="mt-2 text-sm text-orange-600">
              ⚠️ You're comparing the same venue - changes will show as identical
            </div>
          )}
        </div>
      )}
    </div>
  );
}
