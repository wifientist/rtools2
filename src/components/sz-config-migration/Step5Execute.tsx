import { useState } from 'react';
import { Play, AlertCircle, Wifi, Shield, Globe, Key, Lock, ArrowRight } from 'lucide-react';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import JobMonitorModal from '@/components/JobMonitorModal';
import type { WizardState, WizardAction } from '@/types/szConfigMigration';

const TYPE_ICONS: Record<string, React.ReactNode> = {
  psk: <Lock size={13} className="text-blue-600" />,
  open: <Globe size={13} className="text-green-600" />,
  aaa: <Shield size={13} className="text-purple-600" />,
  dpsk: <Key size={13} className="text-orange-600" />,
};

const TYPE_LABELS: Record<string, string> = {
  psk: 'PSK',
  open: 'Open',
  aaa: 'Enterprise',
  dpsk: 'DPSK',
};

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

export default function Step5Execute({ state, dispatch }: Props) {
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [showMonitorModal, setShowMonitorModal] = useState(false);
  const [executionJobId, setExecutionJobId] = useState<string | null>(state.finalJobId);

  const plan = state.planResult;
  const planJobId = state.planJobId;
  const mappings = state.typeMappings;

  const handleConfirm = (jobId: string) => {
    setShowPlanModal(false);
    setExecutionJobId(jobId);
    dispatch({ type: 'SET_EXECUTION_STARTED', jobId });
    setShowMonitorModal(true);
  };

  const handleJobComplete = () => {
    setShowMonitorModal(false);
    dispatch({ type: 'SET_STEP', step: 6 });
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

            {/* Per-WLAN table */}
            {mappings && Object.keys(mappings).length > 0 && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
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
                      const action = actionMap.get(m.wlan_name) || 'create';
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
                            {action === 'create' ? (
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

            {plan.conflicts.length > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
                <AlertCircle size={16} className="text-amber-500 mt-0.5" />
                <div className="text-xs text-amber-700">
                  {plan.conflicts.length} warning(s) — review the plan before confirming.
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
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-700">
              Migration is running. Job ID: <code className="bg-blue-100 px-1 rounded">{executionJobId}</code>
            </div>

            <div className="flex gap-3">
              <button
                onClick={() => setShowMonitorModal(true)}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm font-medium"
              >
                Open Job Monitor
              </button>
              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 6 })}
                className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium flex items-center gap-2"
              >
                View Results
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
          onClose={() => setShowMonitorModal(false)}
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
