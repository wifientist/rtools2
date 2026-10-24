import { useEffect, useState } from 'react';
import { Loader2, ArrowRight, Play, CheckCircle, XCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import JobMonitorModal from '@/components/JobMonitorModal';
import VenueSettingsPanel from './VenueSettingsPanel';
import type { WizardState, WizardAction, VenueSettingsPlan } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

interface SectionResult {
  key: string;
  label: string;
  status: string;   // apply: applied | failed; verify: verified | differs | unreadable
  error?: string;
  differences?: Array<{ field: string; wanted: unknown; actual: unknown }>;
}

interface StageReport {
  job_id: string;
  status: string;
  errors: string[];
  apply: SectionResult[];
  verify: SectionResult[];
}

/**
 * Stage: Venue Configuration. The zone's settings -- channel plans, radio,
 * AP behaviours, per-model PoE / LAN ports / LEDs -- onto the venue, as its
 * own job (plan -> confirm -> apply -> read back), before AP Groups and WLANs.
 */
export default function StepVenueConfig({ state, dispatch }: Props) {
  const [plan, setPlan] = useState<VenueSettingsPlan | null>(null);
  const [modelsRead, setModelsRead] = useState(true);
  const [planning, setPlanning] = useState(false);
  const [planJobId, setPlanJobId] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [showMonitor, setShowMonitor] = useState(false);
  const [report, setReport] = useState<StageReport | null>(null);

  useEffect(() => {
    if (!state.extractionJobId) return;
    apiFetch(`${API_URL}/sz-migration/venue-draft/${state.extractionJobId}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        setPlan(d?.settings ?? null);
        setModelsRead(d?.models_read ?? true);
      })
      .catch(() => setPlan(null));
  }, [state.extractionJobId]);

  const loadReport = async (jobId: string) => {
    const res = await apiFetch(`${API_URL}/sz-migration/venue-config/${jobId}/report`);
    if (res.ok) setReport(await res.json());
  };

  // A stage already run for this venue: show its outcome.
  useEffect(() => {
    if (state.venueConfigJobId) loadReport(state.venueConfigJobId).catch(() => {});
  }, [state.venueConfigJobId]);

  const startPlan = async () => {
    if (!state.extractionJobId || !state.r1ControllerId || !state.destVenueId) return;
    setPlanning(true);
    dispatch({ type: 'SET_ERROR', error: null });
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/venue-config/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          r1_controller_id: state.r1ControllerId,
          tenant_id: state.tenantId,
          venue_id: state.destVenueId,
          sz_snapshot_job_id: state.extractionJobId,
          venue_settings: state.venueSettings,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Planning failed: ${res.status}`);
      setPlanJobId(data.job_id);
      setShowPlan(true);
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setPlanning(false);
    }
  };

  const onConfirm = (jobId: string) => {
    setShowPlan(false);
    dispatch({ type: 'SET_VENUE_CONFIG_JOB', jobId });
    setShowMonitor(true);
  };

  const onJobComplete = () => {
    if (state.venueConfigJobId) loadReport(state.venueConfigJobId).catch(() => {});
  };

  const verify = report?.verify ?? [];
  const applyByKey = new Map((report?.apply ?? []).map(r => [r.key, r]));
  const allVerified = verify.length > 0 && verify.every(r => r.status === 'verified');
  const running = report && ['RUNNING', 'PENDING', 'AWAITING_CONFIRMATION'].includes(report.status);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-1">
          <h3 className="text-lg font-semibold">Venue Configuration</h3>
          <span className="text-sm text-gray-500">{state.selectedZoneName} → {state.destVenueName}</span>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Everything the SmartZone zone sets that has a venue-level home in R1. Applied as its own job, then read
          back from R1 and checked, before AP Groups and WLANs.
        </p>

        {!modelsRead && (
          <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-3">
            <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
            This extraction predates per-AP-model settings (PoE, LAN ports, LEDs). Run the extraction again to include them.
          </div>
        )}

        {plan ? (
          <VenueSettingsPanel plan={plan} selected={state.venueSettings}
                              onChange={sections => dispatch({ type: 'SET_VENUE_SETTINGS', sections })}
                              venueCreated={state.destVenueCreated} disabled={planning || !!running} />
        ) : (
          <div className="text-sm text-gray-500 flex items-center gap-2">
            <Loader2 size={14} className="animate-spin" /> Loading zone settings…
          </div>
        )}

        <div className="flex items-center gap-3 mt-4">
          <button
            onClick={startPlan}
            disabled={!plan || planning || !!running}
            className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:bg-gray-300 font-semibold text-sm flex items-center gap-2"
          >
            {planning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {report ? 'Plan and apply again' : 'Plan and apply to venue'}
          </button>
          {state.venueConfigJobId && (
            <button onClick={() => setShowMonitor(true)} className="text-xs text-blue-600 hover:underline">
              Open job monitor
            </button>
          )}
        </div>
      </div>

      {report && (
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-2 mb-3">
            {allVerified ? <CheckCircle size={18} className="text-green-600" />
              : running ? <Loader2 size={18} className="animate-spin text-blue-600" />
              : <XCircle size={18} className="text-red-600" />}
            <h3 className="text-base font-semibold">
              {allVerified ? 'Venue verified — R1 holds every zone setting'
                : running ? 'Applying…'
                : 'Some settings are not as the zone sets them'}
            </h3>
            <button onClick={() => state.venueConfigJobId && loadReport(state.venueConfigJobId)}
                    className="ml-auto text-gray-400 hover:text-gray-600" title="Refresh">
              <RefreshCw size={14} />
            </button>
          </div>
          {verify.length > 0 ? (
            <table className="w-full text-xs">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-3 py-1.5 text-left">Section</th>
                  <th className="px-3 py-1.5 text-left">Applied</th>
                  <th className="px-3 py-1.5 text-left">Read back</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {verify.map(v => {
                  const a = applyByKey.get(v.key);
                  return (
                    <tr key={v.key} className="align-top">
                      <td className="px-3 py-1.5 font-medium">{v.label}</td>
                      <td className="px-3 py-1.5">
                        {a?.status === 'applied' ? <span className="text-green-700">applied</span>
                          : a ? <span className="text-red-700">refused — {a.error}</span> : '—'}
                      </td>
                      <td className="px-3 py-1.5">
                        {v.status === 'verified' && <span className="text-green-700 font-semibold">verified</span>}
                        {v.status === 'unreadable' && <span className="text-red-700">unreadable — {v.error}</span>}
                        {v.status === 'differs' && (
                          <ul className="text-red-700 space-y-0.5">
                            {(v.differences ?? []).map(d => (
                              <li key={d.field}>
                                <span className="font-mono">{d.field}</span>: zone {JSON.stringify(d.wanted)}, R1 {JSON.stringify(d.actual)}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-sm text-gray-500">
              {running ? 'The job is running — results appear here when it finishes.'
                : (report.errors ?? []).join('; ') || 'No read-back results.'}
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end">
        <button
          onClick={() => dispatch({ type: 'SET_STEP', step: 5 })}
          className={`px-6 py-2.5 rounded-lg font-semibold text-sm flex items-center gap-2 ${allVerified
            ? 'bg-green-600 text-white hover:bg-green-700' : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50'}`}
        >
          {allVerified ? 'Continue to WLANs' : 'Continue without a verified venue'}
          <ArrowRight size={14} />
        </button>
      </div>

      {planJobId && (
        <V2PlanConfirmModal
          jobId={planJobId}
          isOpen={showPlan}
          workflowName="sz_venue_config"
          onClose={() => setShowPlan(false)}
          onConfirm={onConfirm}
          apiPrefix="/sz-migration/workflow"
        />
      )}
      {state.venueConfigJobId && (
        <JobMonitorModal
          jobId={state.venueConfigJobId}
          isOpen={showMonitor}
          onClose={() => { setShowMonitor(false); onJobComplete(); }}
          onJobComplete={onJobComplete}
        />
      )}
    </div>
  );
}
