import { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { useSingleEc } from '@/hooks/useSingleEc';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface ContextSelectorProps {
  context: {
    scopeType: 'client' | 'ap' | 'ssid';
    scopeId: string | null;
    scopeName: string | null;
    timeWindow: '15min' | '1hour' | '24hours';
  };
  onContextChange: (context: any) => void;
}

function ContextSelector({ context, onContextChange }: ContextSelectorProps) {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();
  const { ecData } = useSingleEc(activeControllerId);

  // Detect if controller is MSP or EC
  const isMSP = activeControllerSubtype === 'MSP';

  // For EC controllers, get the tenant_id from the controller data
  const activeController = controllers.find(c => c.id === activeControllerId);
  const ecTenantId = activeController?.r1_tenant_id || null;

  // Hierarchical selections: For MSP: Tenant → Venue → Target; For EC: Venue → Target
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null);
  const [selectedVenueId, setSelectedVenueId] = useState<string | null>(null);

  const [venues, setVenues] = useState<any[]>([]);
  const [clients, setClients] = useState<any[]>([]);
  const [aps, setAps] = useState<any[]>([]);
  const [ssids, setSsids] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [venuesLoading, setVenuesLoading] = useState(false);

  // For EC controllers, automatically set tenant_id when component mounts
  useEffect(() => {
    if (!isMSP && ecTenantId) {
      setSelectedTenantId(ecTenantId);
    }
  }, [isMSP, ecTenantId]);

  // Fetch venues when tenant is selected
  useEffect(() => {
    if (!activeControllerId || !selectedTenantId) {
      setVenues([]);
      setSelectedVenueId(null);
      return;
    }

    const fetchVenues = async () => {
      setVenuesLoading(true);
      try {
        // Use the existing r1 venues endpoint with controller_id
        const endpoint = `${API_BASE_URL}/r1/${activeControllerId}/venues/${selectedTenantId}`;
        const response = await fetch(endpoint, { credentials: 'include' });

        if (response.ok) {
          const data = await response.json();
          // API returns an array of venues directly
          const venueList = Array.isArray(data) ? data : [];
          setVenues(venueList);
        } else {
          console.error('Failed to fetch venues:', response.status);
          setVenues([]);
        }
      } catch (error) {
        console.error('Error fetching venues:', error);
        setVenues([]);
      } finally {
        setVenuesLoading(false);
      }
    };

    fetchVenues();
  }, [activeControllerId, selectedTenantId]);

  // Fetch APs/SSIDs/Clients when venue is selected
  useEffect(() => {
    if (!activeControllerId || !selectedTenantId || !selectedVenueId) {
      setClients([]);
      setAps([]);
      setSsids([]);
      return;
    }

    const fetchOptions = async () => {
      setLoading(true);
      try {
        const endpoint = `${API_BASE_URL}/fer1agg/${activeControllerId}/speed-context?tenant_id=${selectedTenantId}&venue_id=${selectedVenueId}`;
        const response = await fetch(endpoint, { credentials: 'include' });

        if (response.ok) {
          const data = await response.json();
          setClients(data.clients || []);
          setAps(data.aps || []);
          setSsids(data.ssids || []);
        } else {
          console.error('Failed to fetch context options:', response.status);
          setClients([]);
          setAps([]);
          setSsids([]);
        }
      } catch (error) {
        console.error('Error fetching context options:', error);
        setClients([]);
        setAps([]);
        setSsids([]);
      } finally {
        setLoading(false);
      }
    };

    fetchOptions();
  }, [activeControllerId, selectedTenantId, selectedVenueId]);

  const handleScopeTypeChange = (newType: 'client' | 'ap' | 'ssid') => {
    onContextChange({
      ...context,
      scopeType: newType,
      scopeId: null,
      scopeName: null,
    });
  };

  const handleScopeIdChange = (id: string) => {
    let name = null;
    if (context.scopeType === 'client') {
      const client = clients.find(c => c.id === id);
      name = client?.name || client?.mac;
    } else if (context.scopeType === 'ap') {
      const ap = aps.find(a => a.id === id);
      name = ap?.name || ap?.mac;
    } else if (context.scopeType === 'ssid') {
      const ssid = ssids.find(s => s.id === id);
      name = ssid?.name;
    }

    onContextChange({
      ...context,
      scopeId: id,
      scopeName: name,
    });
  };

  const handleTimeWindowChange = (window: '15min' | '1hour' | '24hours') => {
    onContextChange({
      ...context,
      timeWindow: window,
    });
  };

  const getOptions = () => {
    switch (context.scopeType) {
      case 'client':
        return clients;
      case 'ap':
        return aps;
      case 'ssid':
        return ssids;
      default:
        return [];
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-6 mb-6">
      <h3 className="text-lg font-semibold text-gray-900 mb-4">Select What to Analyze</h3>

      {/* Hierarchical Selection: MSP: Tenant → Venue → Target; EC: Venue → Target */}
      <div className="mb-6 space-y-4">
        {/* EC/Tenant Selector (Only for MSP controllers) */}
        {isMSP && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              1. Select End Customer (Tenant):
            </label>
            <select
              value={selectedTenantId || ''}
              onChange={(e) => setSelectedTenantId(e.target.value || null)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option value="">-- Select Tenant --</option>
              {ecData?.map((ec: any) => (
                <option key={ec.id} value={ec.id}>
                  {ec.name} ({ec.tenantType || 'EC'})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Venue Selector (shown when tenant is selected) */}
        {selectedTenantId && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {isMSP ? '2. Select Venue:' : '1. Select Venue:'}
            </label>
            <select
              value={selectedVenueId || ''}
              onChange={(e) => setSelectedVenueId(e.target.value || null)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              disabled={venuesLoading}
            >
              <option value="">-- Select Venue --</option>
              {venues.map((venue: any) => (
                <option key={venue.id} value={venue.id}>
                  {venue.name}
                </option>
              ))}
            </select>
            {venuesLoading && <p className="text-sm text-gray-500 mt-1">Loading venues...</p>}
          </div>
        )}
      </div>

      {/* Target Selection (shown only when venue is selected) */}
      {selectedTenantId && selectedVenueId && (
        <>
          <div className="border-t pt-4 mb-4">
            <h4 className="text-md font-semibold text-gray-800 mb-2">
              {isMSP ? '3. Choose Analysis Target:' : '2. Choose Analysis Target:'}
            </h4>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Scope Type Selector */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Analyze by:
              </label>
              <div className="flex gap-2">
                <button
                  onClick={() => handleScopeTypeChange('client')}
                  className={`flex-1 px-4 py-2 rounded-lg font-medium transition ${
                    context.scopeType === 'client'
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  📱 Client
                </button>
                <button
                  onClick={() => handleScopeTypeChange('ap')}
                  className={`flex-1 px-4 py-2 rounded-lg font-medium transition ${
                    context.scopeType === 'ap'
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  🛜 AP
                </button>
                <button
                  onClick={() => handleScopeTypeChange('ssid')}
                  className={`flex-1 px-4 py-2 rounded-lg font-medium transition ${
                    context.scopeType === 'ssid'
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  📡 SSID
                </button>
              </div>
            </div>

            {/* Specific Item Selector */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Select {context.scopeType}:
              </label>
              <select
                value={context.scopeId || ''}
                onChange={(e) => handleScopeIdChange(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={loading}
              >
                <option value="">-- Select --</option>
                {getOptions().map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name || option.mac}
                  </option>
                ))}
              </select>
            </div>

            {/* Time Window Selector */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Time window:
              </label>
              <select
                value={context.timeWindow}
                onChange={(e) => handleTimeWindowChange(e.target.value as any)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value="15min">Last 15 minutes</option>
                <option value="1hour">Last hour</option>
                <option value="24hours">Last 24 hours</option>
              </select>
            </div>
          </div>
        </>
      )}

      {/* Show message when no venue selected */}
      {!selectedVenueId && (
        <div className="text-center text-gray-500 py-8">
          {isMSP
            ? 'Please select a tenant and venue to begin analysis'
            : 'Please select a venue to begin analysis'
          }
        </div>
      )}
    </div>
  );
}

export default ContextSelector;
