import React, { useState, useEffect } from "react";

interface Venue {
  id: string | number;
  name: string;
  address?: string;
}

interface VenueSelectPanelProps {
  sourceVenues: Venue[];
  destinationVenues: Venue[];
  defaultDestinationAddress?: string;
  onSourceChange: (selectedIds: (string | number)[]) => void;
  onDestinationChange: (venue: Venue | string | number) => void;
}

export default function VenueSelectPanel({
  sourceVenues,
  destinationVenues,
  defaultDestinationAddress = "",
  onSourceChange,
  onDestinationChange,
}: VenueSelectPanelProps) {
  const [selectedSourceVenueIds, setSelectedSourceVenueIds] = useState<(string | number)[]>([]);
  const [selectedDestinationId, setSelectedDestinationId] = useState<string | number | null>(null);
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [newVenueName, setNewVenueName] = useState("");
  const [newVenueAddress, setNewVenueAddress] = useState(defaultDestinationAddress);

  const [isCreatingNewVenue, setIsCreatingNewVenue] = useState(false);
  //const [newVenueName, setNewVenueName] = useState('');
  //const [newVenueAddress, setNewVenueAddress] = useState(defaultAddressFromEC);


  // Handle source venue checkbox toggle
  const handleSourceToggle = (id: string | number) => {
    const updated = selectedSourceVenueIds.includes(id)
      ? selectedSourceVenueIds.filter(vId => vId !== id)
      : [...selectedSourceVenueIds, id];

    setSelectedSourceVenueIds(updated);
    onSourceChange(updated);
  };

  // Handle destination selection change
  const handleDestinationChange = (id: string | number) => {
    setSelectedDestinationId(id);
    setIsCreatingNew(false);
    onDestinationChange(id);
  };

  // Handle create new venue confirmation
  const handleCreateNewConfirm = () => {
    if (!newVenueName.trim()) return alert("Venue name is required.");
    const newVenue: Venue = {
      id: "new", // Temporary placeholder
      name: newVenueName.trim(),
      address: newVenueAddress.trim() || undefined,
    };
    onDestinationChange(newVenue);
  };

  // Reset address when switching to create-new
  useEffect(() => {
    if (isCreatingNew) {
      setSelectedDestinationId(null);
      setNewVenueAddress(defaultDestinationAddress);
    }
  }, [isCreatingNew, defaultDestinationAddress]);

  return (
    <div className="grid md:grid-cols-2 gap-8 mb-8">
      {/* Source Venue Selection */}
      <div>
        <h3 className="text-lg font-semibold mb-2">Source Venues</h3>
        {sourceVenues.length === 0 ? (
          <p className="text-sm text-gray-500">No venues available for source EC.</p>
        ) : (
          <div className="space-y-2">
            {sourceVenues.map(venue => (
              <label key={venue.id} className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  checked={selectedSourceVenueIds.includes(venue.id)}
                  onChange={() => handleSourceToggle(venue.id)}
                  className="form-checkbox"
                />
                <span className="text-sm">{venue.name}</span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Destination Venue Selection */}
      <div>
        <h3 className="text-lg font-semibold mb-2">Destination Venue</h3>
        {destinationVenues.length === 0 ? (
          <p className="text-sm text-gray-500">No venues available for destination EC.</p>
        ) : (
          <div className="space-y-2">
            {destinationVenues.map(venue => (
              <label key={venue.id} className="flex items-center space-x-2">
                <input
                  type="radio"
                  name="destinationVenue"
                  value={venue.id}
                  checked={selectedDestinationId === venue.id}
                  onChange={() => handleDestinationChange(venue.id)}
                  className="form-radio"
                />
                <span className="text-sm">{venue.name}</span>
              </label>
            ))}
          </div>
        )}

        {/* Divider */}
        <div className="my-4 border-t border-gray-300" />

        {/* Create New Venue */}
        <label className="flex items-center space-x-2">
          <input
            type="radio"
            name="destinationVenue"
            checked={isCreatingNew}
            onChange={() => setIsCreatingNew(true)}
            className="form-radio"
          />
          <span className="text-sm">+ Create New Venue</span>
        </label>

        {isCreatingNew && (
          <div className="mt-4 space-y-3">
            <div>
              <label className="block text-sm font-medium">Venue Name</label>
              <input
                type="text"
                value={newVenueName}
                onChange={e => setNewVenueName(e.target.value)}
                className="form-input w-full mt-1"
                placeholder="Enter venue name"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">Address</label>
              <input
                type="text"
                value={newVenueAddress}
                onChange={e => setNewVenueAddress(e.target.value)}
                className="form-input w-full mt-1"
                placeholder="Enter address (optional)"
              />
            </div>
            <button
              onClick={handleCreateNewConfirm}
              className="btn mt-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded"
            >
              Use This New Venue
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
