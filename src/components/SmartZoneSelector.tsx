import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Server, ChevronDown } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Zone {
  id: string;
  name: string;
  description?: string;
}

interface SmartZoneSelectorProps {
  onZoneSelect: (zoneId: string | null, zoneName: string | null) => void;
  disabled?: boolean;
}

const SmartZoneSelector = ({ onZoneSelect, disabled = false }: SmartZoneSelectorProps) => {
  const { activeControllerId } = useAuth();
  const [zones, setZones] = useState<Zone[]>([]);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchZones = async () => {
      if (!activeControllerId) {
        setError('No SmartZone controller selected');
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${API_BASE_URL}/sz/${activeControllerId}/zones`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          throw new Error('Failed to fetch SmartZone zones');
        }

        const result = await response.json();
        setZones(result.data || []);

        // Auto-select first zone if only one exists
        if (result.data?.length === 1) {
          const firstZone = result.data[0];
          setSelectedZoneId(firstZone.id);
          onZoneSelect(firstZone.id, firstZone.name);
        }
      } catch (err: any) {
        setError(err.message || 'Failed to load zones');
        setZones([]);
      } finally {
        setLoading(false);
      }
    };

    fetchZones();
  }, [activeControllerId, onZoneSelect]);

  const handleZoneChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const zoneId = e.target.value || null;
    setSelectedZoneId(zoneId);

    const zone = zones.find(z => z.id === zoneId);
    onZoneSelect(zoneId, zone?.name || null);
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-3">
          <Server className="w-5 h-5 text-gray-400" />
          <div className="animate-pulse bg-gray-200 h-6 w-48 rounded"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <div className="flex items-start gap-3">
          <Server className="w-5 h-5 text-red-500 mt-0.5" />
          <div>
            <h3 className="font-semibold text-red-900">Error Loading Zones</h3>
            <p className="text-sm text-red-700 mt-1">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="mb-4">
        <div className="flex items-center gap-3 mb-2">
          <Server className="w-5 h-5 text-gray-600" />
          <h3 className="text-lg font-semibold">SmartZone Source</h3>
        </div>
        <p className="text-sm text-gray-600">
          Select the zone/domain containing the APs you want to migrate
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Zone / Domain
          </label>
          <div className="relative">
            <select
              value={selectedZoneId || ''}
              onChange={handleZoneChange}
              disabled={disabled || zones.length === 0}
              className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100 disabled:cursor-not-allowed appearance-none"
            >
              <option value="">Select a zone...</option>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                  {zone.description ? ` - ${zone.description}` : ''}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
          </div>
        </div>

        {zones.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No zones found in this SmartZone controller
          </div>
        )}

        {selectedZoneId && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
            <div className="text-sm text-blue-900">
              <span className="font-semibold">Selected Zone:</span>{' '}
              {zones.find(z => z.id === selectedZoneId)?.name}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SmartZoneSelector;
