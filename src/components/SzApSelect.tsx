import { useState, useEffect } from 'react';
import { Search, Check, X } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface SzAP {
  mac: string;
  name: string;
  description?: string;
  serial: string;
  model?: string;
  zoneName?: string;
  location?: string;
  latitude?: number;
  longitude?: number;
}

interface SzApSelectProps {
  controllerId: number;
  zoneId: string;
  onClose: () => void;
  onConfirm: (aps: SzAP[]) => void;
}

const SzApSelect = ({ controllerId, zoneId, onClose, onConfirm }: SzApSelectProps) => {
  const [aps, setAps] = useState<SzAP[]>([]);
  const [selectedAPs, setSelectedAPs] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch APs from SmartZone
  useEffect(() => {
    const fetchAPs = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${API_BASE_URL}/sz/${controllerId}/zones/${zoneId}/aps?limit=1000`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          throw new Error('Failed to fetch APs from SmartZone');
        }

        const result = await response.json();
        setAps(result.data || []);
      } catch (err: any) {
        setError(err.message || 'Failed to load APs');
      } finally {
        setLoading(false);
      }
    };

    if (controllerId && zoneId) {
      fetchAPs();
    }
  }, [controllerId, zoneId]);

  // Filter APs based on search term
  const filteredAPs = aps.filter(ap =>
    ap.name?.toLowerCase().includes(filter.toLowerCase()) ||
    ap.serial?.toLowerCase().includes(filter.toLowerCase()) ||
    ap.model?.toLowerCase().includes(filter.toLowerCase()) ||
    ap.location?.toLowerCase().includes(filter.toLowerCase())
  );

  // Toggle AP selection
  const toggleAPSelection = (serial: string) => {
    const newSelected = new Set(selectedAPs);
    if (newSelected.has(serial)) {
      newSelected.delete(serial);
    } else {
      newSelected.add(serial);
    }
    setSelectedAPs(newSelected);
  };

  const selectAllVisible = () => {
    const newSelected = new Set(selectedAPs);
    filteredAPs.forEach(ap => newSelected.add(ap.serial));
    setSelectedAPs(newSelected);
  };

  const deselectAllVisible = () => {
    const newSelected = new Set(selectedAPs);
    filteredAPs.forEach(ap => newSelected.delete(ap.serial));
    setSelectedAPs(newSelected);
  };

  const handleConfirm = () => {
    const selectedAPData = aps.filter(ap => selectedAPs.has(ap.serial));
    onConfirm(selectedAPData);
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8">
          <div className="flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            <span className="ml-3">Loading SmartZone APs...</span>
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
          <h2 className="text-xl font-semibold">Select SmartZone APs to Migrate</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-full"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls */}
        <div className="p-4 border-b bg-blue-50">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-blue-900">SmartZone APs - Select to Migrate</h3>
            <div className="text-sm text-blue-700">
              {selectedAPs.size} of {aps.length} selected
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Filter by name, serial number, model, or location..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
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

        {/* AP Table */}
        <div className="flex-1 overflow-auto">
          <table className="w-full">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                <th className="w-12 px-4 py-3 text-left">
                  <input
                    type="checkbox"
                    checked={filteredAPs.length > 0 && filteredAPs.every(ap => selectedAPs.has(ap.serial))}
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
                <th className="px-4 py-3 text-left font-medium text-gray-900">Serial Number</th>
                <th className="px-4 py-3 text-left font-medium text-gray-900">Model</th>
                <th className="px-4 py-3 text-left font-medium text-gray-900">Location</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredAPs.map((ap) => (
                <tr
                  key={ap.serial}
                  className={`hover:bg-gray-50 ${
                    selectedAPs.has(ap.serial) ? 'bg-blue-50' : ''
                  }`}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selectedAPs.has(ap.serial)}
                      onChange={() => toggleAPSelection(ap.serial)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-900">{ap.name || 'N/A'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900 font-mono">{ap.serial}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{ap.model || 'N/A'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{ap.location || 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {filteredAPs.length === 0 && (
            <div className="text-center py-8 text-gray-500">
              {filter ? 'No APs match your filter' : 'No APs found in this zone'}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t bg-gray-50 flex justify-between items-center">
          <div className="text-sm text-gray-600">
            {selectedAPs.size} APs selected for migration to RuckusONE
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
              Confirm Selection ({selectedAPs.size} APs)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SzApSelect;
