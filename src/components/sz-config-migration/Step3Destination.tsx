import { useEffect, useState } from 'react';
import { Loader2, ArrowRight, Wifi, Radio, MonitorSpeaker, Key, Users, CheckCircle } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import SingleEcSelector from '@/components/SingleEcSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import CreateVenuePanel from './CreateVenuePanel';
import type { WizardState, WizardAction, VenueDraft } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  r1ControllerId: number;
  isMSP: boolean;
  defaultTenantId: string | null;
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

export default function Step3Destination({ r1ControllerId, isMSP, defaultTenantId, state, dispatch }: Props) {
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [selectedEc, setSelectedEc] = useState<any>(null);
  const [venueMode, setVenueMode] = useState<'existing' | 'create'>(state.destVenueCreated ? 'create' : 'existing');
  const [draft, setDraft] = useState<VenueDraft | null>(null);

  // Zone-derived venue pre-fill, from the extraction (the zone settings are
  // chosen on Review and applied by the migration job)
  useEffect(() => {
    if (!state.extractionJobId) return;
    apiFetch(`${API_URL}/sz-migration/venue-draft/${state.extractionJobId}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (d) setDraft(d.draft);
      })
      .catch(() => {});
  }, [state.extractionJobId]);

  const handleEcSelect = (ecId: string | null, ec: any) => {
    setSelectedEc(ec);
    if (ecId) {
      dispatch({
        type: 'SET_DESTINATION',
        r1ControllerId,
        tenantId: ecId,
        venueId: '',
        venueName: '',
      });
    }
  };

  const handleVenueSelect = (venueId: string | null, venue: any) => {
    if (venueId && venue) {
      dispatch({
        type: 'SET_DESTINATION',
        r1ControllerId,
        tenantId: effectiveTenantId,
        venueId,
        venueName: venue.name || venue.venueName || venueId,
      });
    }
  };

  const handleVenueCreated = (venue: { id: string; name: string }) => {
    dispatch({
      type: 'SET_DESTINATION',
      r1ControllerId,
      tenantId: effectiveTenantId,
      venueId: venue.id,
      venueName: venue.name,
      created: true,
    });
  };

  const captureR1Snapshot = async () => {
    if (!state.destVenueId) return;
    setSnapshotLoading(true);
    dispatch({ type: 'SET_ERROR', error: null });

    try {
      const res = await apiFetch(`${API_URL}/sz-migration/r1-snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_id: r1ControllerId,
          tenant_id: state.tenantId,
          venue_id: state.destVenueId,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `R1 snapshot failed: ${res.status}`);
      }
      const data = await res.json();
      const jobId = data.job_id;

      // Fetch the full inventory to get summary counts
      const invRes = await apiFetch(`${API_URL}/sz-migration/r1-snapshot/${jobId}`);
      if (!invRes.ok) throw new Error('Failed to fetch R1 inventory');
      const inv = await invRes.json();

      dispatch({
        type: 'SET_R1_SNAPSHOT',
        jobId,
        summary: {
          venue_name: inv.venue_name || state.destVenueName || '',
          wifi_network_count: inv.wifi_networks?.length || 0,
          ap_group_count: inv.ap_groups?.length || 0,
          ap_count: inv.aps?.length || 0,
          dpsk_pool_count: inv.dpsk_pools?.length || 0,
          identity_group_count: inv.identity_groups?.length || 0,
        },
      });
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setSnapshotLoading(false);
    }
  };

  // For MSP: use tenant selected via EC selector
  // For EC: use r1_tenant_id from the controller record
  const effectiveTenantId = isMSP ? state.tenantId : defaultTenantId;

  return (
    <div className="space-y-6">
      {/* EC Selector (MSP only) */}
      {isMSP && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-sm">1</div>
            <h3 className="text-lg font-semibold">Select Tenant (EC)</h3>
          </div>
          <SingleEcSelector
            controllerId={r1ControllerId}
            onEcSelect={handleEcSelect}
            selectedEcId={state.tenantId}
          />
        </div>
      )}

      {/* Venue Selector */}
      {(!isMSP || state.tenantId) && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-sm">
              {isMSP ? '2' : '1'}
            </div>
            <h3 className="text-lg font-semibold">Destination Venue</h3>
            <div className="ml-auto flex rounded border border-gray-300 overflow-hidden text-xs">
              {(['existing', 'create'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => setVenueMode(m)}
                  className={`px-3 py-1.5 ${venueMode === m ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                >
                  {m === 'existing' ? 'Existing venue' : 'Create new venue'}
                </button>
              ))}
            </div>
          </div>
          {venueMode === 'existing' && (
            <SingleVenueSelector
              controllerId={r1ControllerId}
              tenantId={effectiveTenantId}
              onVenueSelect={handleVenueSelect}
              selectedVenueId={state.destVenueId}
            />
          )}
          {venueMode === 'create' && state.destVenueCreated && state.destVenueId && (
            <div className="flex items-center gap-2 text-sm text-green-800 bg-green-50 border border-green-200 rounded px-3 py-2">
              <CheckCircle size={16} />
              Created <strong>{state.destVenueName}</strong> in R1.
            </div>
          )}
          {venueMode === 'create' && !(state.destVenueCreated && state.destVenueId) && (
            <CreateVenuePanel
              r1ControllerId={r1ControllerId}
              tenantId={effectiveTenantId}
              draft={draft}
              onCreated={handleVenueCreated}
            />
          )}
        </div>
      )}

      {/* R1 Snapshot */}
      {state.destVenueId && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-sm">
              {isMSP ? '3' : '2'}
            </div>
            <h3 className="text-lg font-semibold">R1 Venue Snapshot</h3>
            <span className="text-sm text-gray-500">({state.destVenueName})</span>
          </div>

          {!state.r1SnapshotJobId && (
            <button
              onClick={captureR1Snapshot}
              disabled={snapshotLoading}
              className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:bg-gray-400 font-medium text-sm flex items-center gap-2"
            >
              {snapshotLoading && <Loader2 size={16} className="animate-spin" />}
              {snapshotLoading ? 'Capturing...' : 'Capture R1 State'}
            </button>
          )}

          {state.r1InventorySummary && (
            <div className="space-y-4">
              <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
                <h4 className="font-semibold text-indigo-800 mb-2">Existing R1 Resources</h4>
                <div className="grid grid-cols-3 md:grid-cols-5 gap-3 text-sm">
                  <InventoryCard icon={<Wifi size={16} />} label="WiFi Networks" value={state.r1InventorySummary.wifi_network_count} />
                  <InventoryCard icon={<Radio size={16} />} label="AP Groups" value={state.r1InventorySummary.ap_group_count} />
                  <InventoryCard icon={<MonitorSpeaker size={16} />} label="APs" value={state.r1InventorySummary.ap_count} />
                  <InventoryCard icon={<Key size={16} />} label="DPSK Pools" value={state.r1InventorySummary.dpsk_pool_count} />
                  <InventoryCard icon={<Users size={16} />} label="Identity Groups" value={state.r1InventorySummary.identity_group_count} />
                </div>
              </div>

              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 4 })}
                className="px-6 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 font-semibold text-sm flex items-center gap-2"
              >
                Continue to Venue Configuration
                <ArrowRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InventoryCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="text-center text-indigo-700">
      <div className="flex justify-center mb-1">{icon}</div>
      <div className="text-xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
