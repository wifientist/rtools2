import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Network, ChevronDown } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface SwitchGroup {
  id: string;
  label?: string;  // SmartZone uses 'label' instead of 'name'
  name?: string;   // Fallback for compatibility
  description?: string;
}

interface SmartZoneSwitchGroupSelectorProps {
  domainId: string;
  onSwitchGroupSelect: (switchGroupId: string | null, switchGroupName: string | null) => void;
  disabled?: boolean;
}

const SmartZoneSwitchGroupSelector = ({ domainId, onSwitchGroupSelect, disabled = false }: SmartZoneSwitchGroupSelectorProps) => {
  const { activeControllerId } = useAuth();
  const [switchGroups, setSwitchGroups] = useState<SwitchGroup[]>([]);
  const [selectedSwitchGroupId, setSelectedSwitchGroupId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSwitchGroups = async () => {
      if (!activeControllerId || !domainId) {
        setError('No SmartZone controller or domain selected');
        return;
      }

      setLoading(true);
      setError(null);
      setSelectedSwitchGroupId(null); // Reset selection when domain changes

      try {
        const response = await fetch(
          `${API_BASE_URL}/sz/${activeControllerId}/domains/${domainId}/switchgroups`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          const errorData = await response.json().catch(() => null);
          const errorMsg = errorData?.detail || `HTTP ${response.status}: Failed to fetch switch groups`;
          throw new Error(errorMsg);
        }

        const result = await response.json();
        setSwitchGroups(result.data || []);

        // Auto-select first switch group if only one exists
        if (result.data?.length === 1) {
          const firstSwitchGroup = result.data[0];
          setSelectedSwitchGroupId(firstSwitchGroup.id);
          const name = firstSwitchGroup.label || firstSwitchGroup.name;
          onSwitchGroupSelect(firstSwitchGroup.id, name);
        }
      } catch (err: any) {
        console.error('Switch group fetch error:', err);
        setError(err.message || 'Failed to load switch groups');
        setSwitchGroups([]);
      } finally {
        setLoading(false);
      }
    };

    fetchSwitchGroups();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeControllerId, domainId]);

  const handleSwitchGroupChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const switchGroupId = e.target.value || null;
    setSelectedSwitchGroupId(switchGroupId);

    const switchGroup = switchGroups.find(sg => sg.id === switchGroupId);
    const name = switchGroup?.label || switchGroup?.name || null;
    onSwitchGroupSelect(switchGroupId, name);
  };

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
            <h3 className="font-semibold text-red-900">Error Loading Switch Groups</h3>
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
          <h3 className="text-lg font-semibold">Switch Group (for Switches)</h3>
        </div>
        <p className="text-sm text-gray-600">
          Select the switch group containing the switches you want to migrate
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Switch Group
          </label>
          <div className="relative">
            <select
              value={selectedSwitchGroupId || ''}
              onChange={handleSwitchGroupChange}
              disabled={disabled || switchGroups.length === 0}
              className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100 disabled:cursor-not-allowed appearance-none"
            >
              <option value="">Select a switch group...</option>
              {switchGroups.map((switchGroup) => (
                <option key={switchGroup.id} value={switchGroup.id}>
                  {switchGroup.label || switchGroup.name}
                  {switchGroup.description ? ` - ${switchGroup.description}` : ''}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
          </div>
        </div>

        {switchGroups.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No switch groups found in this domain
          </div>
        )}

        {selectedSwitchGroupId && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
            <div className="text-sm text-blue-900">
              <span className="font-semibold">Selected Switch Group:</span>{' '}
              {switchGroups.find(sg => sg.id === selectedSwitchGroupId)?.label ||
               switchGroups.find(sg => sg.id === selectedSwitchGroupId)?.name}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SmartZoneSwitchGroupSelector;
