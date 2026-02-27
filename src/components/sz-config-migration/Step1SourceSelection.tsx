import { useState } from 'react';
import { Wifi, Users, Radio, MonitorSpeaker, Loader2 } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import type { WizardState, WizardAction, CensusResult } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  szControllerId: number;
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

export default function Step1SourceSelection({ szControllerId, state, dispatch }: Props) {
  const [censusLoading, setCensusLoading] = useState(false);
  const [extractLoading, setExtractLoading] = useState(false);

  const handleDomainSelect = (domainId: string | null, domainName: string | null) => {
    if (domainId && domainName) {
      dispatch({ type: 'SET_SOURCE', szControllerId, domainId, domainName });
    }
  };

  const handleZoneSelect = (zoneId: string | null, zoneName: string | null) => {
    if (zoneId && zoneName) {
      dispatch({ type: 'SET_ZONE', zoneId, zoneName });
    }
  };

  const runCensus = async () => {
    if (!state.selectedZoneId) return;
    setCensusLoading(true);
    dispatch({ type: 'SET_ERROR', error: null });

    try {
      const res = await apiFetch(`${API_URL}/sz-migration/census`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_id: szControllerId,
          zone_id: state.selectedZoneId,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Census failed: ${res.status}`);
      }
      const data: CensusResult = await res.json();
      dispatch({ type: 'SET_CENSUS', census: data });
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setCensusLoading(false);
    }
  };

  const startExtraction = async () => {
    if (!state.selectedZoneId) return;
    setExtractLoading(true);
    dispatch({ type: 'SET_ERROR', error: null });

    try {
      const res = await apiFetch(`${API_URL}/sz-migration/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_id: szControllerId,
          zone_id: state.selectedZoneId,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Extract failed: ${res.status}`);
      }
      const data = await res.json();
      dispatch({ type: 'START_EXTRACTION', jobId: data.job_id });
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setExtractLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Step 1: Select Domain */}
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-sm">1</div>
          <h3 className="text-lg font-semibold">Select SmartZone Domain</h3>
        </div>
        <SmartZoneDomainSelector onDomainSelect={handleDomainSelect} />
      </div>

      {/* Step 2: Select Zone */}
      {state.selectedDomainId && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-sm">2</div>
            <h3 className="text-lg font-semibold">Select Zone</h3>
          </div>
          <SmartZoneZoneSelector
            domainId={state.selectedDomainId}
            onZoneSelect={handleZoneSelect}
          />
        </div>
      )}

      {/* Census + Extract */}
      {state.selectedZoneId && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-sm">3</div>
            <h3 className="text-lg font-semibold">Zone Census</h3>
            <span className="text-sm text-gray-500">({state.selectedZoneName})</span>
          </div>

          {!state.census && (
            <button
              onClick={runCensus}
              disabled={censusLoading}
              className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-400 font-medium text-sm flex items-center gap-2"
            >
              {censusLoading && <Loader2 size={16} className="animate-spin" />}
              {censusLoading ? 'Running Census...' : 'Run Quick Census'}
            </button>
          )}

          {state.census && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <CensusCard icon={<Wifi size={18} />} label="WLANs" value={state.census.wlan_count} color="blue" />
                <CensusCard icon={<Users size={18} />} label="WLAN Groups" value={state.census.wlan_group_count} color="indigo" />
                <CensusCard icon={<Radio size={18} />} label="AP Groups" value={state.census.ap_group_count} color="purple" />
                <CensusCard icon={<MonitorSpeaker size={18} />} label="APs" value={state.census.ap_count} color="green" />
              </div>

              <button
                onClick={startExtraction}
                disabled={extractLoading}
                className="px-6 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 font-semibold text-sm flex items-center gap-2"
              >
                {extractLoading && <Loader2 size={16} className="animate-spin" />}
                {extractLoading ? 'Starting...' : 'Begin Deep Extraction'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CensusCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    indigo: 'bg-indigo-50 border-indigo-200 text-indigo-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
    green: 'bg-green-50 border-green-200 text-green-700',
  };
  return (
    <div className={`border rounded-lg p-3 text-center ${colorMap[color] || colorMap.blue}`}>
      <div className="flex justify-center mb-1">{icon}</div>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
