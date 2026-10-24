import { useEffect, useState } from 'react';
import { Play, Wifi, Shield, Globe, Key, Lock, ArrowRight, CheckCircle, XCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import JobMonitorModal from '@/components/JobMonitorModal';
import PlanDetails from './PlanDetails';
import type { WizardState, WizardAction } from '@/types/szConfigMigration';

const TYPE_ICONS: Record<string, React.ReactNode> = {
  psk: <Lock size={13} className="text-blue-600" />,
  open: <Globe size={13} className="text-green-600" />,
  aaa: <Shield size={13} className="text-purple-600" />,
  dpsk: <Key size={13} className="text-orange-600" />,
  dpsk_external: <Key size={13} className="text-amber-700" />,
  wispr: <Globe size={13} className="text-sky-700" />,
};

const TYPE_LABELS: Record<string, string> = {
  psk: 'PSK',
  open: 'Open',
  aaa: 'Enterprise',
  dpsk: 'DPSK',
  dpsk_external: 'DPSK (external RADIUS)',
  wispr: 'Guest (WISPr portal)',
  skipped: 'Not migrated',
};

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Outcome {
  status: string;
  units: number;
  units_with_failures: number;
  phases: Record<string, { done: number; failed: number }>;
  failures: Array<{ phase: string; message: string; count: number; wlans: string[] }>;
  errors: string[];
  duration_seconds?: number | null;
  created_only?: string[];
  staged?: string[];
  activated?: number;
  to_activate?: number;
}

const took = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};

const PHASE_LABELS: Record<string, string> = {
  create_networks: 'Create networks', configure_dpsk: 'DPSK setup', activate_ssids: 'Activation',
  create_ap_groups: 'AP Groups',
};

export default function Step5Execute({ state, dispatch }: Props) {
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [showMonitorModal, setShowMonitorModal] = useState(false);
  const [executionJobId, setExecutionJobId] = useState<string | null>(state.finalJobId);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const loadOutcome = async (jobId: string | null = executionJobId) => {
    if (!jobId) return;
    const res = await apiFetch(`${API_URL}/sz-migration/workflow/${jobId}/outcome`);
    if (res.ok) setOutcome(await res.json());
  };
  useEffect(() => { if (state.executionStarted && executionJobId) loadOutcome().catch(() => {}); },
    [executionJobId]); // eslint-disable-line react-hooks/exhaustive-deps
  const running = !outcome || ['RUNNING', 'PENDING', 'VALIDATING', 'AWAITING_CONFIRMATION'].includes(outcome.status);
  const clean = !!outcome && outcome.status === 'COMPLETED' && outcome.units_with_failures === 0 && !outcome.failures.length;

  const plan = state.planResult;
  const planJobId = state.planJobId;
  // Names from Review's mappings; types as the plan decided them (conversions,
  // RADIUS fallbacks, skips).
  const planTypes = (plan?.type_mappings ?? {}) as Record<string, { r1_network_type?: string }>;
  const mappings = state.typeMappings && Object.fromEntries(
    Object.entries(state.typeMappings).map(([id, m]) => [id, {
      ...m, r1_network_type: planTypes[id]?.r1_network_type ?? m.r1_network_type,
    }]),
  );

  const handleConfirm = (jobId: string) => {
    setShowPlanModal(false);
    setExecutionJobId(jobId);
    dispatch({ type: 'SET_EXECUTION_STARTED', jobId });
    setShowMonitorModal(true);
  };

  // Stay here and show what happened: a PARTIAL job is not a pass.
  // The monitor stays open on the results until closed by hand. It reports
  // completion again whenever it's opened on a finished job, so closing here
  // shut it the moment it was reopened.
  const handleJobComplete = () => {
    loadOutcome().catch(() => {});
  };

  // Build per-WLAN view by merging type_mappings (has names) with actions (has create/reuse)
  const actionMap = new Map<string, string>();
  plan?.actions?.forEach(a => {
    const name = a.resource_name || a.name || '';
    if (name) actionMap.set(name, a.action);
  });

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="text-lg font-semibold mb-4">Execute Migration</h3>

        {/* Pre-execution summary */}
        {!state.executionStarted && plan && (
          <div className="space-y-4">
            {/* Summary row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <SummaryCard label="WLANs" value={plan.unit_count} color="blue" />
              <SummaryCard label="API Calls" value={plan.estimated_api_calls} color="purple" />
              <SummaryCard
                label="To Create"
                value={plan.actions.filter(a => a.action === 'create').length}
                color="indigo"
              />
              <SummaryCard
                label="To Reuse"
                value={plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists').length}
                color="green"
              />
            </div>

            <PlanDetails plan={plan} />

            {/* Per-WLAN table */}
            {mappings && Object.keys(mappings).length > 0 && (
              <div className="border border-gray-200 rounded-lg max-h-96 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left text-xs text-gray-500 uppercase tracking-wider">
                      <th className="px-4 py-2">WLAN</th>
                      <th className="px-4 py-2">SZ Auth</th>
                      <th className="px-4 py-2">R1 Type</th>
                      <th className="px-4 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {Object.entries(mappings).map(([wlanId, m]) => {
                      const action = m.r1_network_type === 'skipped' ? 'skip' : actionMap.get(m.wlan_name) || 'create';
                      return (
                        <tr key={wlanId} className="hover:bg-gray-50">
                          <td className="px-4 py-2 font-medium flex items-center gap-1.5">
                            {TYPE_ICONS[m.r1_network_type] || <Wifi size={13} />}
                            {m.wlan_name}
                          </td>
                          <td className="px-4 py-2 text-gray-500">{m.sz_auth_type}</td>
                          <td className="px-4 py-2">
                            <span className="text-xs bg-gray-100 px-1.5 py-0.5 rounded font-medium">
                              {TYPE_LABELS[m.r1_network_type] || m.r1_network_type}
                            </span>
                          </td>
                          <td className="px-4 py-2">
                            {action === 'skip' ? (
                              <span className="text-xs text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded font-medium">— Skip</span>
                            ) : action === 'create' ? (
                              <span className="text-xs text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded font-medium">+ Create</span>
                            ) : (
                              <span className="text-xs text-green-700 bg-green-50 px-1.5 py-0.5 rounded font-medium">= Reuse</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Resource breakdown from plan summary */}
            {plan.summary && (
              <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Resource Breakdown</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1 text-xs">
                  {plan.summary.networks_to_create > 0 && (
                    <div><span className="text-gray-500">Networks to create:</span> <span className="font-semibold">{plan.summary.networks_to_create}</span></div>
                  )}
                  {plan.summary.networks_to_reuse > 0 && (
                    <div><span className="text-gray-500">Networks to reuse:</span> <span className="font-semibold text-green-600">{plan.summary.networks_to_reuse}</span></div>
                  )}
                  {plan.summary.radius_groups_to_create > 0 && (
                    <div><span className="text-gray-500">RADIUS profiles:</span> <span className="font-semibold">{plan.summary.radius_groups_to_create}</span></div>
                  )}
                  {plan.summary.dpsk_pools_to_create > 0 && (
                    <div><span className="text-gray-500">DPSK pools:</span> <span className="font-semibold">{plan.summary.dpsk_pools_to_create}</span></div>
                  )}
                  {plan.summary.identity_groups_to_create > 0 && (
                    <div><span className="text-gray-500">Identity groups:</span> <span className="font-semibold">{plan.summary.identity_groups_to_create}</span></div>
                  )}
                  {plan.summary.passphrases_to_create > 0 && (
                    <div><span className="text-gray-500">Passphrases:</span> <span className="font-semibold">{plan.summary.passphrases_to_create}</span></div>
                  )}
                </div>
              </div>
            )}

            <button
              onClick={() => setShowPlanModal(true)}
              className="px-6 py-2.5 bg-orange-600 text-white rounded-lg hover:bg-orange-700 font-semibold text-sm flex items-center gap-2"
            >
              <Play size={16} />
              Review Plan & Execute
            </button>
          </div>
        )}

        {/* Post-execution: show re-open monitor option */}
        {state.executionStarted && executionJobId && (
          <div className="space-y-4">
            {running ? (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-700">
                Creating WLANs… Job ID: <code className="bg-blue-100 px-1 rounded">{executionJobId}</code>
              </div>
            ) : (
              <div className={`rounded-lg border p-4 space-y-3 ${clean ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                <div className="flex items-center gap-2 text-sm font-semibold">
                  {clean ? <CheckCircle size={16} className="text-green-600" /> : <XCircle size={16} className="text-red-600" />}
                  <span className={clean ? 'text-green-800' : 'text-red-800'}>
                    {clean ? `All ${outcome!.units} WLANs created as planned`
                        + (outcome!.to_activate !== undefined
                          ? ` — ${outcome!.activated} activated, ${outcome!.staged?.length ?? 0} staged, ${outcome!.created_only?.length ?? 0} created only`
                          : '')
                      : `Finished ${outcome!.status} — ${outcome!.units_with_failures} of ${outcome!.units} WLAN(s) hit a failure`}
                  </span>
                  {outcome!.duration_seconds != null && (
                    <span className="text-xs font-normal text-gray-500">⏱ {took(outcome!.duration_seconds)}</span>
                  )}
                  <button onClick={() => loadOutcome()} className="ml-auto text-gray-400 hover:text-gray-600" title="Refresh">
                    <RefreshCw size={14} />
                  </button>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  {Object.entries(outcome!.phases).map(([ph, c]) => (
                    <span key={ph} className={`px-2 py-0.5 rounded border ${c.failed ? 'border-red-300 bg-white text-red-700' : 'border-green-300 bg-white text-green-700'}`}>
                      {PHASE_LABELS[ph] ?? ph}: {c.done} done{c.failed ? `, ${c.failed} failed` : ''}
                    </span>
                  ))}
                </div>
                {((outcome!.created_only?.length ?? 0) + (outcome!.staged?.length ?? 0)) > 0 && (
                  <div className="text-xs text-gray-600">
                    Not activated, by plan (no SZ AP broadcasts them):
                    {(outcome!.staged?.length ?? 0) > 0 && <> staged — {outcome!.staged!.slice(0, 8).join(', ')}{outcome!.staged!.length > 8 ? '…' : ''};</>}
                    {(outcome!.created_only?.length ?? 0) > 0 && <> created only — {outcome!.created_only!.slice(0, 8).join(', ')}{outcome!.created_only!.length > 8 ? '…' : ''}</>}
                  </div>
                )}
                {outcome!.failures.length > 0 && (
                  <div className="space-y-1.5 max-h-72 overflow-y-auto">
                    {outcome!.failures.map((f, i) => (
                      <div key={i} className="bg-white border border-red-200 rounded px-3 py-2 text-xs">
                        <div className="flex gap-2 text-red-800">
                          <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
                          <span><b>{f.count}×</b> {PHASE_LABELS[f.phase] ?? f.phase}: {f.message}</span>
                        </div>
                        <div className="text-gray-500 mt-0.5 ml-5">
                          {f.wlans.slice(0, 12).join(', ')}{f.count > 12 ? ` and ${f.count - 12} more` : ''}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {!clean && (
                  <div className="text-xs text-gray-600">
                    Fix what's listed, then plan and run again — WLANs, AP Groups and pools already in R1 are found and
                    reused, not duplicated; only what's missing is created.
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={() => setShowMonitorModal(true)}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm font-medium"
              >
                Open Job Monitor
              </button>
              {!running && (
                <button
                  onClick={() => dispatch({ type: 'RERUN_EXECUTION' })}
                  className="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 text-sm font-medium flex items-center gap-2"
                  title="Back to the WLANs step to build a fresh plan from your current choices"
                >
                  <RefreshCw size={14} /> Plan and run Create WLANs again
                </button>
              )}
              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 7 })}
                className={`px-4 py-2 rounded text-sm font-medium flex items-center gap-2 ${clean
                  ? 'bg-green-600 text-white hover:bg-green-700' : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50'}`}
              >
                {clean || running ? 'Continue to AP Mapping' : 'Continue to AP Mapping anyway'}
                <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* V2 Plan Confirm Modal */}
      {planJobId && (
        <V2PlanConfirmModal
          jobId={planJobId}
          isOpen={showPlanModal}
          workflowName="sz_to_r1_migration"
          onClose={() => setShowPlanModal(false)}
          onConfirm={handleConfirm}
          apiPrefix="/sz-migration/workflow"
        />
      )}

      {/* Job Monitor Modal */}
      {executionJobId && (
        <JobMonitorModal
          jobId={executionJobId}
          isOpen={showMonitorModal}
          onClose={() => { setShowMonitorModal(false); loadOutcome().catch(() => {}); }}
          onJobComplete={handleJobComplete}
        />
      )}
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
    indigo: 'bg-indigo-50 border-indigo-200 text-indigo-700',
    green: 'bg-green-50 border-green-200 text-green-700',
  };
  return (
    <div className={`border rounded-lg p-3 text-center ${colors[color] || colors.blue}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
