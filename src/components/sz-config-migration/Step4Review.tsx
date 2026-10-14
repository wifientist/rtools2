import { useState, useEffect, useRef } from 'react';
import { ChevronDown, Loader2, ArrowRight, AlertCircle } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import ActivationMatrix from './ActivationMatrix';
import NetworkSettingsReview from './NetworkSettingsReview';
import DpskDecisions from './DpskDecisions';
import RadiusMapping from './RadiusMapping';
import MigrationBlueprint from './MigrationBlueprint';
import type {
  WizardState, WizardAction, ResolverResult, TypeMapping, PlanResult,
  RadiusChoices, RadiusDecision, WlanFallback,
} from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

type Section = 'activation' | 'blueprint' | 'networks' | 'dpsk' | 'radius' | 'summary';

export default function Step4Review({ state, dispatch }: Props) {
  const [openSections, setOpenSections] = useState<Set<Section>>(new Set(['activation', 'summary']));
  const [resolving, setResolving] = useState(false);
  const [planning, setPlanning] = useState(false);

  // Run resolve on mount if not already done
  useEffect(() => {
    if (!state.resolverResult && state.extractionJobId && !resolving) {
      runResolve();
    }
  }, []);

  // RADIUS services this zone uses, and the R1 profiles they could map to.
  const [radiusChoices, setRadiusChoices] = useState<RadiusChoices | null>(null);
  const [choicesLoading, setChoicesLoading] = useState(false);
  // Typed shared secrets: component memory only. Never dispatched (wizard
  // state is persisted), never rendered back; sent once with the plan request.
  const secrets = useRef<Record<string, string>>({});
  const [secretSet, setSecretSet] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (state.resolverResult && !radiusChoices && !choicesLoading) {
      loadRadiusChoices();
    }
  }, [state.resolverResult]);

  const needsRadiusChoice = (radiusChoices?.services.length ?? 0) > 0;

  // Effective decisions: what was chosen, else the suggestion.
  const decisions: Record<string, RadiusDecision> = {};
  for (const svc of radiusChoices?.services ?? []) {
    decisions[svc.id] = state.radiusDecisions?.[svc.id] ?? {
      action: svc.suggestion.action, r1_profile_id: svc.suggestion.r1_profile_id,
    };
  }
  const decisionsComplete = (radiusChoices?.services ?? []).every(svc => {
    const d = decisions[svc.id];
    if (d.action === 'existing') return !!d.r1_profile_id;
    if (d.action === 'skip') {
      return svc.used_by.filter(u => u.role === 'authentication')
        .every(u => !!state.wlanFallbacks[u.wlan_id]);
    }
    return true;
  });

  // Zones with no RADIUS have nothing to decide: plan straight away, as before.
  useEffect(() => {
    if (radiusChoices && !needsRadiusChoice && !state.planResult && !planning) {
      runPlan();
    }
  }, [radiusChoices]);

  const loadRadiusChoices = async () => {
    if (!state.extractionJobId || !state.r1ControllerId) return;
    setChoicesLoading(true);
    try {
      const params = new URLSearchParams({ r1_controller_id: String(state.r1ControllerId) });
      if (state.tenantId) params.set('tenant_id', state.tenantId);
      const res = await apiFetch(
        `${API_URL}/sz-migration/radius-choices/${state.extractionJobId}?${params}`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Could not load RADIUS choices: ${res.status}`);
      }
      const data: RadiusChoices = await res.json();
      setRadiusChoices(data);
      if (data.services.length > 0) {
        setOpenSections(prev => new Set(prev).add('radius'));
      }
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setChoicesLoading(false);
    }
  };

  const updateChoices = (
    nextDecisions: Record<string, RadiusDecision>,
    nextFallbacks: Record<string, WlanFallback>,
  ) => {
    dispatch({ type: 'SET_RADIUS_CHOICES', decisions: nextDecisions, fallbacks: nextFallbacks });
    if (state.planResult) dispatch({ type: 'CLEAR_PLAN' });
  };

  const handleSecret = (serviceId: string, value: string) => {
    if (value) secrets.current[serviceId] = value;
    else delete secrets.current[serviceId];
    setSecretSet(prev => ({ ...prev, [serviceId]: !!value }));
    if (state.planResult) dispatch({ type: 'CLEAR_PLAN' });
  };

  const runResolve = async () => {
    if (!state.extractionJobId) return;
    setResolving(true);
    dispatch({ type: 'SET_ERROR', error: null });

    try {
      const res = await apiFetch(`${API_URL}/sz-migration/resolve/${state.extractionJobId}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Resolve failed: ${res.status}`);
      }
      const data = await res.json();
      dispatch({
        type: 'SET_RESOLVER',
        result: data.resolver as ResolverResult,
        mappings: data.type_mappings as Record<string, TypeMapping>,
      });
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setResolving(false);
    }
  };

  const runPlan = async () => {
    if (!state.extractionJobId || !state.r1ControllerId || !state.destVenueId) return;
    setPlanning(true);

    try {
      // Create the plan
      const res = await apiFetch(`${API_URL}/sz-migration/workflow/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sz_controller_id: state.szControllerId,
          r1_controller_id: state.r1ControllerId,
          tenant_id: state.tenantId,
          venue_id: state.destVenueId,
          sz_snapshot_job_id: state.extractionJobId,
          ...(needsRadiusChoice && {
            radius_decisions: Object.fromEntries(
              Object.entries(decisions).map(([sid, d]) => [sid, {
                action: d.action,
                r1_profile_id: d.action === 'existing' ? d.r1_profile_id : null,
                ...(d.action === 'create' && secrets.current[sid]
                  ? { shared_secret: secrets.current[sid] } : {}),
              }]),
            ),
            wlan_fallbacks: state.wlanFallbacks,
          }),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Plan failed: ${res.status}`);
      }
      const planData = await res.json();
      const jobId = planData.job_id;

      // Poll for validation to complete
      let result: PlanResult | null = null;
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const pollRes = await apiFetch(`${API_URL}/sz-migration/workflow/${jobId}/plan`);
        if (!pollRes.ok) continue;
        const pollData: PlanResult = await pollRes.json();
        if (pollData.status !== 'VALIDATING') {
          result = pollData;
          break;
        }
      }

      if (result) {
        dispatch({ type: 'SET_PLAN', jobId, result });
      } else {
        throw new Error('Validation timed out after 2 minutes');
      }
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setPlanning(false);
    }
  };

  const toggleSection = (section: Section) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };

  const loading = resolving || planning;
  const plan = state.planResult;
  const canProceed = plan?.valid && !state.resolverResult?.blocked;

  return (
    <div className="space-y-4">
      {/* Loading state */}
      {loading && (
        <div className="bg-white rounded-lg shadow p-8 flex flex-col items-center">
          <Loader2 size={32} className="text-blue-600 animate-spin mb-3" />
          <p className="text-gray-600 font-medium">
            {resolving ? 'Resolving WLAN activations...' : 'Running validation...'}
          </p>
          <p className="text-gray-400 text-sm mt-1">
            {resolving
              ? 'Mapping WLAN Groups to AP Groups and checking 15-SSID limits'
              : 'Checking existing R1 resources and building execution plan'
            }
          </p>
        </div>
      )}

      {/* Accordion panels */}
      {!loading && state.resolverResult && (
        <>
          <AccordionPanel
            title="Activation Matrix"
            subtitle={`${state.resolverResult.ap_group_summaries.length} AP Groups, ${state.resolverResult.activations.length} activations`}
            section="activation"
            isOpen={openSections.has('activation')}
            onToggle={toggleSection}
            badge={state.resolverResult.blocked ? 'BLOCKED' : undefined}
            badgeColor={state.resolverResult.blocked ? 'red' : undefined}
          >
            <ActivationMatrix resolverResult={state.resolverResult} />
          </AccordionPanel>

          {state.typeMappings && (
            <>
              <AccordionPanel
                title="Migration Blueprint"
                subtitle={`${Object.keys(state.typeMappings).length} WLANs — SZ → R1 mapping`}
                section="blueprint"
                isOpen={openSections.has('blueprint')}
                onToggle={toggleSection}
              >
                <MigrationBlueprint state={state} />
              </AccordionPanel>

              <AccordionPanel
                title="Network Settings"
                subtitle={`${Object.keys(state.typeMappings).length} WLANs`}
                section="networks"
                isOpen={openSections.has('networks')}
                onToggle={toggleSection}
              >
                <NetworkSettingsReview typeMappings={state.typeMappings} />
              </AccordionPanel>

              <AccordionPanel
                title="DPSK Configuration"
                subtitle={`${Object.values(state.typeMappings).filter(m => m.r1_network_type === 'dpsk').length} DPSK WLANs`}
                section="dpsk"
                isOpen={openSections.has('dpsk')}
                onToggle={toggleSection}
              >
                <DpskDecisions typeMappings={state.typeMappings} />
              </AccordionPanel>

              <AccordionPanel
                title="RADIUS Servers"
                subtitle={
                  radiusChoices
                    ? `${radiusChoices.services.length} used by this zone`
                    : choicesLoading ? 'Loading…' : ''
                }
                section="radius"
                isOpen={openSections.has('radius')}
                onToggle={toggleSection}
                badge={needsRadiusChoice && !plan ? 'CHOOSE' : undefined}
                badgeColor={needsRadiusChoice && !plan ? 'amber' : undefined}
              >
                {radiusChoices ? (
                  <div className="space-y-3">
                    <RadiusMapping
                      choices={radiusChoices}
                      decisions={decisions}
                      fallbacks={state.wlanFallbacks}
                      secretSet={secretSet}
                      disabled={planning}
                      onDecision={(sid, d) => updateChoices({ ...decisions, [sid]: d }, state.wlanFallbacks)}
                      onFallback={(wid, fb) => updateChoices(decisions, { ...state.wlanFallbacks, [wid]: fb })}
                      onSecret={handleSecret}
                    />
                    {needsRadiusChoice && !plan && (
                      <div className="flex justify-end">
                        <button
                          onClick={runPlan}
                          disabled={!decisionsComplete || planning}
                          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 text-sm font-semibold flex items-center gap-2"
                        >
                          {planning && <Loader2 size={14} className="animate-spin" />}
                          Build plan with these choices
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-gray-500 flex items-center gap-2">
                    {choicesLoading && <Loader2 size={14} className="animate-spin" />}
                    {choicesLoading ? 'Loading RADIUS servers…' : 'RADIUS choices unavailable'}
                  </div>
                )}
              </AccordionPanel>
            </>
          )}

          {/* Plan Summary */}
          {plan && (
            <AccordionPanel
              title="Migration Summary"
              subtitle={plan.valid ? 'Ready to execute' : 'Validation issues'}
              section="summary"
              isOpen={openSections.has('summary')}
              onToggle={toggleSection}
              badge={plan.valid ? 'VALID' : 'INVALID'}
              badgeColor={plan.valid ? 'green' : 'red'}
            >
              <div className="space-y-4">
                {/* Summary stats */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard label="Units (WLANs)" value={plan.unit_count} color="blue" />
                  <StatCard label="API Calls Est." value={plan.estimated_api_calls} color="purple" />
                  <StatCard
                    label="To Create"
                    value={plan.actions.filter(a => a.action === 'create').length}
                    color="blue"
                  />
                  <StatCard
                    label="To Reuse"
                    value={plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists').length}
                    color="green"
                  />
                </div>

                {/* Actions list */}
                {plan.actions.length > 0 && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {/* Create */}
                    {plan.actions.filter(a => a.action === 'create').length > 0 && (
                      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                        <h4 className="text-xs font-semibold text-blue-800 mb-1">
                          Will Create ({plan.actions.filter(a => a.action === 'create').length})
                        </h4>
                        <ul className="space-y-1 max-h-32 overflow-y-auto">
                          {plan.actions.filter(a => a.action === 'create').map((a, i) => (
                            <li key={i} className="text-xs text-blue-700">
                              + {a.resource_name || a.name} <span className="text-blue-400">({a.resource_type})</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Reuse */}
                    {plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists').length > 0 && (
                      <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                        <h4 className="text-xs font-semibold text-green-800 mb-1">
                          Will Reuse ({plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists').length})
                        </h4>
                        <ul className="space-y-1 max-h-32 overflow-y-auto">
                          {plan.actions.filter(a => a.action === 'reuse' || a.action === 'exists').map((a, i) => (
                            <li key={i} className="text-xs text-green-700">
                              = {a.resource_name || a.name} <span className="text-green-400">({a.resource_type})</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Conflicts */}
                {plan.conflicts.length > 0 && (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                    <h4 className="text-xs font-semibold text-amber-800 mb-1">
                      Conflicts ({plan.conflicts.length})
                    </h4>
                    {plan.conflicts.map((c, i) => (
                      <div key={i} className="text-xs text-amber-700 flex items-start gap-1">
                        <AlertCircle size={12} className="mt-0.5 flex-shrink-0" />
                        <span>{c.description}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </AccordionPanel>
          )}

          {/* Continue button */}
          {canProceed && (
            <div className="flex justify-end">
              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 5 })}
                className="px-6 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 font-semibold text-sm flex items-center gap-2"
              >
                Continue to Execute
                <ArrowRight size={14} />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Accordion Panel ────────────────────────────────────────────────

interface AccordionPanelProps {
  title: string;
  subtitle: string;
  section: Section;
  isOpen: boolean;
  onToggle: (section: Section) => void;
  badge?: string;
  badgeColor?: string;
  children: React.ReactNode;
}

function AccordionPanel({ title, subtitle, section, isOpen, onToggle, badge, badgeColor, children }: AccordionPanelProps) {
  const badgeColors: Record<string, string> = {
    red: 'bg-red-100 text-red-700',
    green: 'bg-green-100 text-green-700',
    blue: 'bg-blue-100 text-blue-700',
    amber: 'bg-amber-100 text-amber-700',
  };

  return (
    <div className="bg-white rounded-lg shadow overflow-hidden">
      <button
        onClick={() => onToggle(section)}
        className="w-full px-5 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <ChevronDown
            size={16}
            className={`text-gray-400 transition-transform ${isOpen ? 'rotate-0' : '-rotate-90'}`}
          />
          <div className="text-left">
            <span className="font-semibold text-sm">{title}</span>
            <span className="text-xs text-gray-500 ml-2">{subtitle}</span>
          </div>
        </div>
        {badge && (
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${badgeColors[badgeColor || 'blue'] || badgeColors.blue}`}>
            {badge}
          </span>
        )}
      </button>
      {isOpen && (
        <div className="px-5 pb-4 border-t border-gray-100 pt-3">
          {children}
        </div>
      )}
    </div>
  );
}

// ── Stat Card ──────────────────────────────────────────────────────

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
    green: 'bg-green-50 border-green-200 text-green-700',
  };
  return (
    <div className={`border rounded-lg p-3 text-center ${colorMap[color] || colorMap.blue}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
