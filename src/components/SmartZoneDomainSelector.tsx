import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Server, ChevronDown } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Domain {
  id: string;
  name: string;
  description?: string;
}

interface SmartZoneDomainSelectorProps {
  onDomainSelect: (domainId: string | null, domainName: string | null) => void;
  disabled?: boolean;
}

const SmartZoneDomainSelector = ({ onDomainSelect, disabled = false }: SmartZoneDomainSelectorProps) => {
  const { activeControllerId } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomainId, setSelectedDomainId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchDomains = async () => {
      if (!activeControllerId) {
        setError('No SmartZone controller selected');
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${API_BASE_URL}/sz/${activeControllerId}/domains?recursively=true`,
          { credentials: 'include' }
        );

        if (!response.ok) {
          throw new Error('Failed to fetch SmartZone domains');
        }

        const result = await response.json();
        setDomains(result.data || []);

        // Auto-select first domain if only one exists
        if (result.data?.length === 1) {
          const firstDomain = result.data[0];
          setSelectedDomainId(firstDomain.id);
          onDomainSelect(firstDomain.id, firstDomain.name);
        }
      } catch (err: any) {
        setError(err.message || 'Failed to load domains');
        setDomains([]);
      } finally {
        setLoading(false);
      }
    };

    fetchDomains();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeControllerId]); // Only re-fetch when controller changes, not when callback changes

  const handleDomainChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const domainId = e.target.value || null;
    setSelectedDomainId(domainId);

    const domain = domains.find(d => d.id === domainId);
    onDomainSelect(domainId, domain?.name || null);
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
            <h3 className="font-semibold text-red-900">Error Loading Domains</h3>
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
          <h3 className="text-lg font-semibold">SmartZone Source Domain</h3>
        </div>
        <p className="text-sm text-gray-600">
          Select the domain containing the devices you want to migrate
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Domain
          </label>
          <div className="relative">
            <select
              value={selectedDomainId || ''}
              onChange={handleDomainChange}
              disabled={disabled || domains.length === 0}
              className="w-full px-4 py-2 pr-10 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100 disabled:cursor-not-allowed appearance-none"
            >
              <option value="">Select a domain...</option>
              {domains.map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.name}
                  {domain.description ? ` - ${domain.description}` : ''}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
          </div>
        </div>

        {domains.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No domains found in this SmartZone controller
          </div>
        )}

        {selectedDomainId && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
            <div className="text-sm text-blue-900">
              <span className="font-semibold">Selected Domain:</span>{' '}
              {domains.find(d => d.id === selectedDomainId)?.name}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SmartZoneDomainSelector;
