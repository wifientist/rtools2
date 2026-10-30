import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Network } from 'lucide-react';
import { matchesSnippet } from '@/utils/textMatch';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Zone {
  id: string;
  name: string;
  description?: string;
}

interface SmartZoneZoneSelectorProps {
  domainId: string;
  onZoneSelect: (zoneId: string | null, zoneName: string | null) => void;
  disabled?: boolean;
  // Which SZ controller to read. Defaults to the active one; pages that let
  // SZ be either the active or the secondary controller pass it explicitly.
  controllerId?: number | null;
}

const SmartZoneZoneSelector = ({ domainId, onZoneSelect, disabled = false, controllerId }: SmartZoneZoneSelectorProps) => {
  const { activeControllerId: authControllerId } = useAuth();
  const activeControllerId = controllerId ?? authControllerId;
  const [zones, setZones] = useState<Zone[]>([]);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchZones = async () => {
      if (!activeControllerId || !domainId) {
        setError('No SmartZone controller or domain selected');
        return;
      }

      setLoading(true);
      setError(null);
      setSelectedZoneId(null); // Reset selection when domain changes
      setFilter('');

      try {
        const response = await fetch(
          `${API_BASE_URL}/sz/${activeControllerId}/zones?domain_id=${domainId}`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          throw new Error('Failed to fetch zones');
        }

        const result = await response.json();
        const sorted: Zone[] = [...(result.data || [])].sort((a: Zone, b: Zone) =>
          a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
        setZones(sorted);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeControllerId, domainId]);

  // One zone at a time: ticking a zone unticks the last, unticking clears.
  const pick = (zone: Zone | null) => {
    setSelectedZoneId(zone?.id ?? null);
    onZoneSelect(zone?.id ?? null, zone?.name ?? null);
  };

  const q = filter.trim();
  const shown = q
    ? zones.filter(z => matchesSnippet(`${z.name} ${z.description ?? ''}`, q))
    : zones;

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-3">
          <Network className="w-5 h-5 text-gray-400" />
          <div className="animate-pulse bg-gray-200 h-6 w-48 rounded"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <div className="flex items-start gap-3">
          <Network className="w-5 h-5 text-red-500 mt-0.5" />
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
          <Network className="w-5 h-5 text-gray-600" />
          <h3 className="text-lg font-semibold">Wireless Zone (for APs)</h3>
        </div>
        <p className="text-sm text-gray-600">
          Select the wireless zone containing the APs you want to migrate
        </p>
      </div>

      <div className="space-y-4">
        {zones.length > 0 && (
          <div>
            <div className="flex flex-wrap items-center gap-3 mb-2">
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter zones by name or description…"
                disabled={disabled}
                className="border rounded px-3 py-1.5 text-sm w-full sm:w-80"
              />
              <span className="ml-auto text-xs text-gray-500">
                {shown.length === zones.length
                  ? `${zones.length} zones`
                  : `${shown.length} of ${zones.length} zones`}
              </span>
            </div>
            <div className="max-h-72 overflow-y-auto border rounded divide-y">
              {shown.map((zone) => {
                const on = zone.id === selectedZoneId;
                return (
                  <label
                    key={zone.id}
                    className={`flex items-center gap-3 px-3 py-1.5 text-sm ${
                      disabled ? 'opacity-60' : 'cursor-pointer'
                    } ${on ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                  >
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={disabled}
                      onChange={() => pick(on ? null : zone)}
                    />
                    <span className="flex-1">{zone.name}</span>
                    {zone.description && (
                      <span className="text-xs text-gray-500 truncate max-w-[50%]">{zone.description}</span>
                    )}
                  </label>
                );
              })}
              {!shown.length && (
                <p className="px-3 py-2 text-sm text-gray-500">
                  No zone matches “{filter}”.
                </p>
              )}
            </div>
          </div>
        )}

        {zones.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No zones found in this domain
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

export default SmartZoneZoneSelector;
