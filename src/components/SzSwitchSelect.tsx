import { useState, useEffect } from 'react';
import { Search, Check, X } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface SzSwitch {
  serialNumber: string;
  name: string;
  description?: string;
  mac?: string;
  model?: string;
  switchGroupId?: string;
  switchGroupName?: string;
  domainId?: string;
  ipAddress?: string;
  firmwareVersion?: string;
  status?: string;
}

interface SzSwitchSelectProps {
  controllerId: number;
  domainId: string;
  onClose: () => void;
  onConfirm: (switches: SzSwitch[]) => void;
}

const SzSwitchSelect = ({ controllerId, domainId, onClose, onConfirm }: SzSwitchSelectProps) => {
  const [switches, setSwitches] = useState<SzSwitch[]>([]);
  const [selectedSwitches, setSelectedSwitches] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch switches from SmartZone
  useEffect(() => {
    const fetchSwitches = async () => {
      setLoading(true);
      setError(null);

      try {
        const url = `${API_BASE_URL}/sz/${controllerId}/domains/${domainId}/switches?limit=1000`;
        const response = await fetch(url, { credentials: 'include' });

        if (!response.ok) {
          throw new Error('Failed to fetch switches from SmartZone');
        }

        const result = await response.json();
        setSwitches(result.data || []);
      } catch (err: any) {
        setError(err.message || 'Failed to load switches');
      } finally {
        setLoading(false);
      }
    };

    if (controllerId && domainId) {
      fetchSwitches();
    }
  }, [controllerId, domainId]);

  // Filter switches based on search term
  const filteredSwitches = switches.filter(sw =>
    sw.name?.toLowerCase().includes(filter.toLowerCase()) ||
    sw.serialNumber?.toLowerCase().includes(filter.toLowerCase()) ||
    sw.model?.toLowerCase().includes(filter.toLowerCase()) ||
    sw.ipAddress?.toLowerCase().includes(filter.toLowerCase()) ||
    sw.switchGroupName?.toLowerCase().includes(filter.toLowerCase())
  );

  // Toggle switch selection
  const toggleSwitchSelection = (serialNumber: string) => {
    const newSelected = new Set(selectedSwitches);
    if (newSelected.has(serialNumber)) {
      newSelected.delete(serialNumber);
    } else {
      newSelected.add(serialNumber);
    }
    setSelectedSwitches(newSelected);
  };

  const selectAllVisible = () => {
    const newSelected = new Set(selectedSwitches);
    filteredSwitches.forEach(sw => newSelected.add(sw.serialNumber));
    setSelectedSwitches(newSelected);
  };

  const deselectAllVisible = () => {
    const newSelected = new Set(selectedSwitches);
    filteredSwitches.forEach(sw => newSelected.delete(sw.serialNumber));
    setSelectedSwitches(newSelected);
  };

  const handleConfirm = () => {
    const selectedSwitchData = switches.filter(sw => selectedSwitches.has(sw.serialNumber));
    onConfirm(selectedSwitchData);
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8">
          <div className="flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            <span className="ml-3">Loading SmartZone Switches...</span>
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
          <h2 className="text-xl font-semibold">Select SmartZone Switches to Migrate</h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-full"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls */}
        <div className="p-4 border-b bg-purple-50">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-purple-900">SmartZone Switches - Select to Migrate</h3>
            <div className="text-sm text-purple-700">
              {selectedSwitches.size} of {switches.length} selected
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Filter by name, serial number, model, IP, or switch group..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            <div className="flex gap-2">
              <button
                onClick={selectAllVisible}
                className="px-3 py-2 text-sm bg-purple-600 text-white rounded hover:bg-purple-700"
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

        {/* Switch Table */}
        <div className="flex-1 overflow-auto">
          <table className="w-full">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                <th className="w-12 px-4 py-3 text-left">
                  <input
                    type="checkbox"
                    checked={filteredSwitches.length > 0 && filteredSwitches.every(sw => selectedSwitches.has(sw.serialNumber))}
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
                <th className="px-4 py-3 text-left font-medium text-gray-900">IP Address</th>
                <th className="px-4 py-3 text-left font-medium text-gray-900">Switch Group</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredSwitches.map((sw) => (
                <tr
                  key={sw.serialNumber}
                  className={`hover:bg-gray-50 ${
                    selectedSwitches.has(sw.serialNumber) ? 'bg-purple-50' : ''
                  }`}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selectedSwitches.has(sw.serialNumber)}
                      onChange={() => toggleSwitchSelection(sw.serialNumber)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-900">{sw.name || 'N/A'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900 font-mono">{sw.serialNumber}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{sw.model || 'N/A'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900 font-mono">{sw.ipAddress || 'N/A'}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{sw.switchGroupName || 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {filteredSwitches.length === 0 && (
            <div className="text-center py-8 text-gray-500">
              {filter ? 'No switches match your filter' : 'No switches found in this domain'}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t bg-gray-50 flex justify-between items-center">
          <div className="text-sm text-gray-600">
            {selectedSwitches.size} switches selected for migration to RuckusONE
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
              disabled={selectedSwitches.size === 0}
              className={`px-6 py-2 rounded-lg flex items-center ${
                selectedSwitches.size === 0
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-purple-600 text-white hover:bg-purple-700'
              }`}
            >
              <Check className="w-4 h-4 mr-2" />
              Confirm Selection ({selectedSwitches.size} Switches)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SzSwitchSelect;
