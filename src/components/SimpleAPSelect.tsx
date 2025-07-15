import React, { useState, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Search, Check, X } from 'lucide-react';

const SimpleAPSelect = ({ sourceId, destinationId, sourceVenueData, destinationVenueData, onClose, onConfirm }) => {
  const [viewMode, setViewMode] = useState('source'); // 'source' or 'destination'
  const [sourceAPs, setSourceAPs] = useState([]);
  const [destinationAPs, setDestinationAPs] = useState([]);
  const [selectedAPs, setSelectedAPs] = useState(new Set());
  const [sourceFilter, setSourceFilter] = useState('');
  const [destinationFilter, setDestinationFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const sourceVenueMap = Object.fromEntries(
    (sourceVenueData || []).map(v => [v.id, v.name])
  );
  const destinationVenueMap = Object.fromEntries(
    (destinationVenueData || []).map(v => [v.id, v.name])
  );
  

  // Fetch AP data for both source and destination
  useEffect(() => {
    const fetchAPData = async () => {
      setLoading(true);
      setError(null);
      
      try {
        const [sourceResponse, destinationResponse] = await Promise.all([
          fetch(`/api/r1a/tenant/${sourceId}/aps`),
          fetch(`/api/r1b/tenant/${destinationId}/aps`)
        ]);

        if (!sourceResponse.ok || !destinationResponse.ok) {
          throw new Error('Failed to fetch AP data');
        }

        //console.log(sourceResponse, destinationResponse);

        const sourceData = await sourceResponse.json();
        const destinationData = await destinationResponse.json();

        console.log(sourceData, destinationData);

        //setSourceAPs(sourceData.data || []);
        //setDestinationAPs(destinationData.data || []);
        // this will add the venueName for filtering purposes
        setSourceAPs(
          (sourceData.data || []).map(ap => ({
            ...ap,
            venueName: sourceVenueMap[ap.venueId] || ap.venueId || null,
          }))
        );
        setDestinationAPs(
          (destinationData.data || []).map(ap => ({
            ...ap,
            venueName: destinationVenueMap[ap.venueId] || ap.venueId || null,
          }))
        );
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    if (sourceId && destinationId) {
      fetchAPData();
    }
  }, [sourceId, destinationId]);

  // Filter APs based on search term
  const filterAPs = (aps, filter) => {
    if (!filter) return aps;
    return aps.filter(ap => 
      ap.name?.toLowerCase().includes(filter.toLowerCase()) ||
      ap.venueName?.toLowerCase().includes(filter.toLowerCase()) ||
      ap.serialNumber?.toLowerCase().includes(filter.toLowerCase()) ||
      ap.model?.toLowerCase().includes(filter.toLowerCase())
    );
  };

  const filteredSourceAPs = filterAPs(sourceAPs, sourceFilter);
  const filteredDestinationAPs = filterAPs(destinationAPs, destinationFilter);

  // Handle AP selection
  const toggleAPSelection = (serialNumber) => {
    const newSelected = new Set(selectedAPs);
    if (newSelected.has(serialNumber)) {
      newSelected.delete(serialNumber);
    } else {
      newSelected.add(serialNumber);
    }
    setSelectedAPs(newSelected);
  };

  const selectAllVisible = () => {
    const newSelected = new Set(selectedAPs);
    filteredSourceAPs.forEach(ap => newSelected.add(ap.serialNumber));
    setSelectedAPs(newSelected);
  };

  const deselectAllVisible = () => {
    const newSelected = new Set(selectedAPs);
    filteredSourceAPs.forEach(ap => newSelected.delete(ap.serialNumber));
    setSelectedAPs(newSelected);
  };

  const handleConfirm = () => {
    const selectedAPData = sourceAPs.filter(ap => selectedAPs.has(ap.serialNumber));
    onConfirm(selectedAPData);
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8">
          <div className="flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            <span className="ml-3">Loading AP data...</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8 max-w-md">
          <div className="text-red-600 mb-4">Error: {error}</div>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600"
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg w-11/12 h-5/6 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-xl font-semibold">Select Access Points to Migrate</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-full"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Toggle Controls */}
        <div className="flex justify-center p-4 border-b bg-gray-50">
          <div className="flex rounded-lg border bg-white">
            <button
              onClick={() => setViewMode('source')}
              className={`px-6 py-2 rounded-l-lg flex items-center ${
                viewMode === 'source' 
                  ? 'bg-blue-600 text-white' 
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              <ChevronLeft className="w-4 h-4 mr-2" />
              Source ({filteredSourceAPs.length} APs)
            </button>
            <button
              onClick={() => setViewMode('destination')}
              className={`px-6 py-2 rounded-r-lg flex items-center ${
                viewMode === 'destination' 
                  ? 'bg-blue-600 text-white' 
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              Destination ({filteredDestinationAPs.length} APs)
              <ChevronRight className="w-4 h-4 ml-2" />
            </button>
          </div>
        </div>

        {/* Content Area */}
        <div className="flex-1 flex overflow-hidden">
          {/* Source Panel */}
          <div className={`transition-all duration-300 ${
            viewMode === 'source' ? 'w-full' : 'w-0 overflow-hidden'
          }`}>
            <div className="h-full flex flex-col">
              {/* Source Controls */}
              <div className="p-4 border-b bg-blue-50">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-blue-900">Source APs - Select to Migrate</h3>
                  <div className="text-sm text-blue-700">
                    {selectedAPs.size} of {sourceAPs.length} selected
                  </div>
                </div>
                
                <div className="flex items-center gap-4">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="text"
                      placeholder="Filter by name, venue, serial number, or model..."
                      value={sourceFilter}
                      onChange={(e) => setSourceFilter(e.target.value)}
                      className="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  
                  <div className="flex gap-2">
                    <button
                      onClick={selectAllVisible}
                      className="px-3 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
                    >
                      Select All
                    </button>
                    <button
                      onClick={deselectAllVisible}
                      className="px-3 py-2 text-sm bg-gray-500 text-white rounded hover:bg-gray-600"
                    >
                      Clear All
                    </button>
                  </div>
                </div>
              </div>

              {/* Source AP Table */}
              <div className="flex-1 overflow-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="w-12 px-4 py-3 text-left">
                        <input
                          type="checkbox"
                          checked={filteredSourceAPs.length > 0 && filteredSourceAPs.every(ap => selectedAPs.has(ap.serialNumber))}
                          onChange={(e) => {
                            if (e.target.checked) {
                              selectAllVisible();
                            } else {
                              deselectAllVisible();
                            }
                          }}
                          className="rounded"
                        />
                      </th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Name</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Venue</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Serial Number</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Model</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {filteredSourceAPs.map((ap) => (
                      <tr 
                        key={ap.serialNumber}
                        className={`hover:bg-gray-50 ${
                          selectedAPs.has(ap.serialNumber) ? 'bg-blue-50' : ''
                        }`}
                      >
                        <td className="px-4 py-3">
                          <input
                            type="checkbox"
                            checked={selectedAPs.has(ap.serialNumber)}
                            onChange={() => toggleAPSelection(ap.serialNumber)}
                            className="rounded"
                          />
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-900">{ap.name || 'N/A'}</td>
                        <td className="px-4 py-3 text-sm text-gray-900">{sourceVenueMap[ap.venueId] || ap.venueId || 'N/A'}</td>
                        <td className="px-4 py-3 text-sm text-gray-900 font-mono">{ap.serialNumber}</td>
                        <td className="px-4 py-3 text-sm text-gray-900">{ap.model || 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                
                {filteredSourceAPs.length === 0 && (
                  <div className="text-center py-8 text-gray-500">
                    {sourceFilter ? 'No APs match your filter' : 'No APs found'}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Destination Panel */}
          <div className={`transition-all duration-300 ${
            viewMode === 'destination' ? 'w-full' : 'w-0 overflow-hidden'
          }`}>
            <div className="h-full flex flex-col">
              {/* Destination Controls */}
              <div className="p-4 border-b bg-green-50">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-green-900">Destination APs - Reference Only</h3>
                  <div className="text-sm text-green-700">
                    {destinationAPs.length} APs
                  </div>
                </div>
                
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Filter by name, venue, serial number, or model..."
                    value={destinationFilter}
                    onChange={(e) => setDestinationFilter(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
                  />
                </div>
              </div>

              {/* Destination AP Table */}
              <div className="flex-1 overflow-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Name</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Venue</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Serial Number</th>
                      <th className="px-4 py-3 text-left font-medium text-gray-900">Model</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {filteredDestinationAPs.map((ap) => (
                      <tr key={ap.serialNumber} className="hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm text-gray-900">{ap.name || 'N/A'}</td>
                        <td className="px-4 py-3 text-sm text-gray-900">{destinationVenueMap[ap.venueId] || ap.venueId || 'N/A'}</td>
                        <td className="px-4 py-3 text-sm text-gray-900 font-mono">{ap.serialNumber}</td>
                        <td className="px-4 py-3 text-sm text-gray-900">{ap.model || 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                
                {filteredDestinationAPs.length === 0 && (
                  <div className="text-center py-8 text-gray-500">
                    {destinationFilter ? 'No APs match your filter' : 'No APs found'}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t bg-gray-50 flex justify-between items-center">
          <div className="text-sm text-gray-600">
            {selectedAPs.size} APs selected for migration
          </div>
          
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-6 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={selectedAPs.size === 0}
              className={`px-6 py-2 rounded-lg flex items-center ${
                selectedAPs.size === 0
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-blue-600 text-white hover:bg-blue-700'
              }`}
            >
              <Check className="w-4 h-4 mr-2" />
              Finalize Selection ({selectedAPs.size} APs)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SimpleAPSelect;