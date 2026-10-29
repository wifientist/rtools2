import { useState, useEffect, useRef } from 'react';
import { ChevronDown, Loader2, ArrowRight, AlertCircle } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import { captureR1Snapshot } from './r1Snapshot';
import WlanPlacement from './WlanPlacement';
import WlanSelection from './WlanSelection';
import NetworkSettingsReview from './NetworkSettingsReview';
import DpskChoicesPanel, { type DpskWlan } from './DpskChoicesPanel';
import RadiusMapping from './RadiusMapping';
import MigrationBlueprint from './MigrationBlueprint';
import AuthConversionPanel, { type ConvertibleWlan } from './AuthConversionPanel';
import { fixSection } from './PlanDetails';
import { CONVERTIBLE_TYPES } from '@/types/szConfigMigration';
import type {
  WizardState, WizardAction, ResolverResult, TypeMapping, PlanResult,
  RadiusChoices, RadiusDecision, WlanFallback, DpskChoices, DpskPoolSpec, R1Layout, LayoutChoices,
} from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const MISSING_SNAPSHOT =
  "This migration's SZ extraction isn't available — it was interrupted (e.g. a server restart) or has " +
  'expired after 24 hours. Go back to Extraction and run it again.';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

type Section = 'wlans' | 'placement' | 'auth' | 'blueprint' | 'networks' | 'dpsk' | 'radius';

export default function Step4Review({ state, dispatch }: Props) {
  const [openSections, setOpenSections] = useState<Set<Section>>(new Set(['wlans', 'placement', 'auth']));
  const [resolving, setResolving] = useState(false);
  const [planning, setPlanning] = useState(false);

  // The venue as R1 has it NOW: re-read on entering this step -- Create WLANs
  // changes R1, and placement from the Destination-time snapshot showed groups
  // that exist as still "to create".
  const [r1Reading, setR1Reading] = useState(false);
  const [r1ReadAt, setR1ReadAt] = useState<Date | null>(null);
  const refreshR1 = async () => {
    setR1Reading(true);
    try {
      await captureR1Snapshot(state, dispatch);
      setR1ReadAt(new Date());
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: `Could not re-read the venue: ${e.message}` });
    } finally {
      setR1Reading(false);
    }
  };
  useEffect(() => { refreshR1(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // R1 placement for the current choices, recomputed server-side on change.
  const [layout, setLayout] = useState<R1Layout | null>(null);
  const [layoutLoading, setLayoutLoading] = useState(false);
  const layoutSeq = useRef(0);
  useEffect(() => {
    if (!state.extractionJobId) return;
    const seq = ++layoutSeq.current;
    const t = setTimeout(async () => {
      setLayoutLoading(true);
      try {
        const res = await apiFetch(`${API_URL}/sz-migration/layout/${state.extractionJobId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ r1_snapshot_job_id: state.r1SnapshotJobId, choices: state.layoutChoices }),
        });
        if (res.status === 404) throw new Error(MISSING_SNAPSHOT);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Placement failed: ${res.status}`);
        }
        const data: R1Layout = await res.json();
        if (seq === layoutSeq.current) setLayout(data);
      } catch (e: any) {
        if (seq === layoutSeq.current) dispatch({ type: 'SET_ERROR', error: e.message });
      } finally {
        if (seq === layoutSeq.current) setLayoutLoading(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [state.extractionJobId, state.r1SnapshotJobId, state.layoutChoices]);

  const updateLayoutChoices = (choices: LayoutChoices) => dispatch({ type: 'SET_LAYOUT_CHOICES', choices });


  // Type mappings are refreshed on every visit: they are cheap, and saved
  // ones may predate a classification fix (external DPSK once read as Enterprise).
  useEffect(() => {
    if (state.extractionJobId && !resolving) {
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

  // ── External auth → R1 DPSK ─────────────────────────────────────
  // WLANs that authenticate against RADIUS may move onto R1's own DPSK
  // service. Only WLANs that are migrated at all are offered.
  const excludedWlans = state.layoutChoices.excluded_wlans ?? [];
  const notMigrated = new Set([...(layout?.unbroadcast ?? []).filter(u => !u.included).map(u => u.id), ...excludedWlans]);
  const serviceByWlan = new Map<string, string>();
  for (const svc of radiusChoices?.services ?? []) {
    for (const u of svc.used_by) if (u.role === 'authentication') serviceByWlan.set(u.wlan_id, svc.name);
  }
  const convertible: ConvertibleWlan[] = Object.entries(state.typeMappings ?? {})
    .filter(([id, m]) => CONVERTIBLE_TYPES.includes(m.r1_network_type) && !notMigrated.has(id))
    .map(([id, m]) => ({
      id, name: m.wlan_name ?? id, ssid: m.wlan_name ?? id, szAuthType: m.sz_auth_type,
      r1Type: m.r1_network_type, serviceName: serviceByWlan.get(id) ?? null,
    }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  const converted = new Set(convertible.filter(w => state.authConversions[w.id]).map(w => w.id));
  const updateConversions = (c: Record<string, 'dpsk'>) => dispatch({ type: 'SET_AUTH_CONVERSIONS', conversions: c });

  // RADIUS services still needed: used by a migrated WLAN that authenticates
  // against RADIUS in R1 (not converted; not e.g. a portal WLAN made plain PSK).
  // RADIUS users: Enterprise / external DPSK not converted, and WISPr guest
  // networks (an SZ hotspot's RADIUS auth; Always Accept ones reference no service).
  const usesRadius = (id: string) => !converted.has(id) && !notMigrated.has(id)
    && [...CONVERTIBLE_TYPES, 'wispr'].includes(state.typeMappings?.[id]?.r1_network_type ?? '');
  const radiusServices = (radiusChoices?.services ?? []).filter(svc => svc.used_by.some(u => usesRadius(u.wlan_id)));
  const neededRadiusChoices: RadiusChoices | null = radiusChoices && { ...radiusChoices, services: radiusServices };
  const needsRadiusChoice = radiusServices.length > 0;

  // Mappings as they will be built: converted WLANs are R1 DPSK.
  const effectiveMappings: Record<string, TypeMapping> | null = state.typeMappings && Object.fromEntries(
    Object.entries(state.typeMappings).map(([id, m]) => [id, converted.has(id)
      ? { ...m, r1_network_type: 'dpsk', converted: true,
          notes: `Converted to R1 DPSK — SZ ${m.sz_auth_type} used RADIUS; R1's DPSK service replaces it` }
      : m]),
  );
  // ...less the WLANs excluded from the migration
  const migratedMappings: Record<string, TypeMapping> | null = effectiveMappings && Object.fromEntries(
    Object.entries(effectiveMappings).filter(([id]) => !excludedWlans.includes(id)));

  // Effective decisions: what was chosen, else the suggestion.
  const decisions: Record<string, RadiusDecision> = {};
  for (const svc of radiusServices) {
    decisions[svc.id] = state.radiusDecisions?.[svc.id] ?? {
      action: svc.suggestion.action, r1_profile_id: svc.suggestion.r1_profile_id,
    };
  }
  const decisionsComplete = radiusServices.every(svc => {
    const d = decisions[svc.id];
    if (d.action === 'existing') return !!d.r1_profile_id;
    if (d.action === 'skip') {
      return svc.used_by.filter(u => u.role === 'authentication' && usesRadius(u.wlan_id))
        .every(u => !!state.wlanFallbacks[u.wlan_id]);
    }
    return true;
  });

  // ── DPSK services ──────────────────────────────────────────────
  // Which WLANs end up DPSK: native SZ DPSK, plus RADIUS WLANs converted
  // above -- minus any whose RADIUS service is dropped and the WLAN skipped.
  const radiusSkippedWlans = new Set(
    radiusServices
      .filter(svc => decisions[svc.id]?.action === 'skip')
      .flatMap(svc => svc.used_by.filter(u => u.role === 'authentication' && usesRadius(u.wlan_id)).map(u => u.wlan_id)),
  );
  const dpskWlans: DpskWlan[] = Object.entries(state.typeMappings ?? {}).flatMap(([id, m]): DpskWlan[] => {
    const name = m.wlan_name ?? id;
    if (notMigrated.has(id)) return [];
    if (converted.has(id)) return [{ id, name, source: 'converted' as const }];
    if (radiusSkippedWlans.has(id)) {
      return state.wlanFallbacks[id] === 'dpsk' ? [{ id, name, source: 'converted' as const }] : [];
    }
    return m.r1_network_type === 'dpsk' ? [{ id, name, source: 'sz_dpsk' as const }] : [];
  });
  const needsDpskChoice = dpskWlans.length > 0;

  const [dpskChoices, setDpskChoices] = useState<DpskChoices | null>(null);
  const [dpskLoading, setDpskLoading] = useState(false);
  useEffect(() => {
    if (needsDpskChoice && !dpskChoices && !dpskLoading) loadDpskChoices();
  }, [needsDpskChoice]);

  // Effective choices: what was edited, else one shared new pool for all.
  const dpskPools: Record<string, DpskPoolSpec> =
    state.dpskPools ?? (dpskChoices ? { shared: dpskChoices.default_pool } : {});
  const firstPoolKey = Object.keys(dpskPools)[0] ?? '';
  const dpskAssignments: Record<string, string> = Object.fromEntries(
    dpskWlans.map(w => [w.id, state.dpskAssignments[w.id] ?? firstPoolKey]),
  );
  const dpskComplete = !needsDpskChoice || (!!dpskChoices && dpskWlans.every(w => {
    const spec = dpskPools[dpskAssignments[w.id]];
    if (!spec) return false;
    if (spec.mode === 'existing') return !!spec.existing_pool_id;
    const ig = spec.identity_group;
    return !!spec.name?.trim() && !!ig && (ig.mode === 'new' ? !!ig.name?.trim() : !!ig.id);
  }));

  // Placement always needs a look, so the plan is never built unasked.
  const placementReady = !!layout && !layout.blocked && !layoutLoading;

  const loadDpskChoices = async () => {
    if (!state.extractionJobId || !state.r1ControllerId) return;
    setDpskLoading(true);
    try {
      const params = new URLSearchParams({ r1_controller_id: String(state.r1ControllerId) });
      if (state.tenantId) params.set('tenant_id', state.tenantId);
      const res = await apiFetch(`${API_URL}/sz-migration/dpsk-choices/${state.extractionJobId}?${params}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Could not load DPSK choices: ${res.status}`);
      }
      setDpskChoices(await res.json());
      setOpenSections(prev => new Set(prev).add('dpsk'));
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setDpskLoading(false);
    }
  };

  const updateDpsk = (pools: Record<string, DpskPoolSpec>, assignments: Record<string, string>) => {
    dispatch({ type: 'SET_DPSK_CHOICES', pools, assignments });
    if (state.planResult) dispatch({ type: 'CLEAR_PLAN' });
  };

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
      const qs = state.r1SnapshotJobId ? `?r1_snapshot_job_id=${encodeURIComponent(state.r1SnapshotJobId)}` : '';
      const res = await apiFetch(`${API_URL}/sz-migration/resolve/${state.extractionJobId}${qs}`, {
        method: 'POST',
      });
      if (res.status === 404) throw new Error(MISSING_SNAPSHOT);
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
          layout_choices: state.layoutChoices,
          auth_conversions: Object.fromEntries([...converted].map(id => [id, 'dpsk'])),
          // Always sent (possibly empty): a service only converted WLANs used
          // gets no decision, and so is not created.
          ...({
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
          ...(needsDpskChoice && {
            // Only the services some WLAN actually uses.
            dpsk_pools: Object.fromEntries(
              Object.entries(dpskPools).filter(([k]) => Object.values(dpskAssignments).includes(k)),
            ),
            dpsk_assignments: dpskAssignments,
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
        // Valid: Execute shows the plan and confirms it. Not valid: the
        // errors are at the top of this page, where the choices are fixed.
        if (result.valid) dispatch({ type: 'SET_STEP', step: 6 });
        else window.scrollTo({ top: 0, behavior: 'smooth' });
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

  // Only the first type mapping blanks the page; a refresh or a plan build
  // keeps the panels (and your scroll position) where they are.
  const loading = resolving && !state.resolverResult;
  const plan = state.planResult;
  const canProceed = plan?.valid;
  const planErrors = plan && !plan.valid ? plan.conflicts.filter(c => c.severity === 'error') : [];

  return (
    <div className="space-y-4">
      {/* Loading state */}
      {loading && (
        <div className="bg-white rounded-lg shadow p-8 flex flex-col items-center">
          <Loader2 size={32} className="text-blue-600 animate-spin mb-3" />
          <p className="text-gray-600 font-medium">
            Mapping WLAN security types...
          </p>
          <p className="text-gray-400 text-sm mt-1">Mapping SZ security types to R1 network types</p>
        </div>
      )}

      {/* A plan that didn't validate: what to fix, and where */}
      {planErrors.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-800 mb-2">
            <AlertCircle size={16} />
            The plan has {planErrors.length} problem{planErrors.length === 1 ? '' : 's'} — fix them below, then build it again
          </div>
          <ul className="space-y-1 max-h-48 overflow-y-auto">
            {planErrors.map((c, i) => (
              <li key={i} className="text-xs text-red-700 flex gap-2">
                {fixSection(c.resource_type) && (
                  <span className="flex-shrink-0 font-semibold">[{fixSection(c.resource_type)}]</span>
                )}
                <span>{c.description}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Accordion panels */}
      {!loading && state.resolverResult && (
        <>
          <AccordionPanel
            title="WLANs to migrate"
            subtitle={layout?.catalog
              ? `${layout.catalog.length - excludedWlans.length} of ${layout.catalog.length} migrated`
                + (excludedWlans.length ? ` · ${excludedWlans.length} excluded` : '')
              : 'Loading…'}
            section="wlans"
            isOpen={openSections.has('wlans')}
            onToggle={toggleSection}
            badge={(layout?.catalog ?? []).some(w => w.skip_hint && !excludedWlans.includes(w.id)) ? 'TEST / TEMP?' : undefined}
            badgeColor="amber"
          >
            {layout?.catalog ? (
              <div className={layoutLoading ? 'opacity-60' : ''}>
                <WlanSelection catalog={layout.catalog} excluded={excludedWlans} disabled={planning}
                               onChange={ids => updateLayoutChoices({ ...state.layoutChoices, excluded_wlans: ids })} />
              </div>
            ) : (
              <div className="text-sm text-gray-500 flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Loading WLANs…
              </div>
            )}
          </AccordionPanel>

          <AccordionPanel
            title="WLAN Placement"
            subtitle={layout
              ? `${layout.summary.all_ap_wlans} on All AP Groups · ${layout.summary.groups} AP Groups (${layout.summary.groups_existing} in R1, ${layout.summary.groups_to_create} to create, ${layout.summary.groups_pending} not created)`
                + (r1Reading ? ' · reading R1…' : r1ReadAt ? ` · R1 read ${r1ReadAt.toLocaleTimeString()}` : '')
              : layoutLoading || r1Reading ? 'Working out placement…' : ''}
            section="placement"
            isOpen={openSections.has('placement')}
            onToggle={toggleSection}
            badge={layout?.blocked ? 'BLOCKED' : !plan ? 'CONFIRM' : undefined}
            badgeColor={layout?.blocked ? 'red' : !plan ? 'amber' : undefined}
          >
            {layout ? (
              <div className={layoutLoading || r1Reading ? 'opacity-60 pointer-events-none' : ''}>
                <div className="flex items-center justify-end gap-2 mb-2 text-xs text-gray-500">
                  <span>
                    {r1Reading ? 'Reading the venue from R1…'
                      : r1ReadAt ? `Matched against the venue as R1 had it at ${r1ReadAt.toLocaleTimeString()}`
                      : 'Matched against the R1 snapshot from the Destination step'}
                  </span>
                  <button type="button" onClick={refreshR1} disabled={r1Reading || planning}
                          className="px-2 py-0.5 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
                    Refresh R1 state
                  </button>
                </div>
                <WlanPlacement layout={layout} choices={state.layoutChoices}
                               onChange={updateLayoutChoices} disabled={planning} />
              </div>
            ) : (
              <div className="text-sm text-gray-500 flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Working out placement…
              </div>
            )}
          </AccordionPanel>

          {state.typeMappings && (
            <>
              <AccordionPanel
                title="Authentication"
                subtitle={convertible.length
                  ? `${convertible.length} RADIUS-backed WLAN${convertible.length === 1 ? '' : 's'} · ${converted.size} converted to R1 DPSK`
                  : 'No RADIUS-backed WLANs'}
                section="auth"
                isOpen={openSections.has('auth')}
                onToggle={toggleSection}
              >
                <AuthConversionPanel wlans={convertible} conversions={state.authConversions}
                                     onChange={updateConversions} disabled={planning} />
              </AccordionPanel>

              <AccordionPanel
                title="RADIUS Servers"
                subtitle={
                  radiusChoices
                    ? `${radiusServices.length} needed${radiusChoices.services.length > radiusServices.length
                        ? ` (${radiusChoices.services.length - radiusServices.length} not needed in R1)` : ''}`
                    : choicesLoading ? 'Loading…' : ''
                }
                section="radius"
                isOpen={openSections.has('radius')}
                onToggle={toggleSection}
                badge={needsRadiusChoice && !plan ? 'CHOOSE' : undefined}
                badgeColor={needsRadiusChoice && !plan ? 'amber' : undefined}
              >
                {neededRadiusChoices && !needsRadiusChoice ? (
                  <div className="text-sm text-gray-500 italic">
                    No RADIUS server is needed: no migrated WLAN keeps external authentication.
                  </div>
                ) : neededRadiusChoices ? (
                  <div className="space-y-3">
                    <RadiusMapping
                      choices={neededRadiusChoices}
                      decisions={decisions}
                      fallbacks={state.wlanFallbacks}
                      secretSet={secretSet}
                      disabled={planning}
                      onDecision={(sid, d) => updateChoices({ ...decisions, [sid]: d }, state.wlanFallbacks)}
                      onFallback={(wids, fb) => updateChoices(decisions, {
                        ...state.wlanFallbacks,
                        ...Object.fromEntries(wids.map(w => [w, fb])),
                      })}
                      onSecret={handleSecret}
                    />
                  </div>
                ) : (
                  <div className="text-sm text-gray-500 flex items-center gap-2">
                    {choicesLoading && <Loader2 size={14} className="animate-spin" />}
                    {choicesLoading ? 'Loading RADIUS servers…' : 'RADIUS choices unavailable'}
                  </div>
                )}
              </AccordionPanel>

              <AccordionPanel
                title="DPSK Services"
                subtitle={`${dpskWlans.length} WLAN${dpskWlans.length === 1 ? '' : 's'} will be DPSK`}
                section="dpsk"
                isOpen={openSections.has('dpsk')}
                onToggle={toggleSection}
                badge={needsDpskChoice && !plan ? 'CHOOSE' : undefined}
                badgeColor={needsDpskChoice && !plan ? 'amber' : undefined}
              >
                {!needsDpskChoice ? (
                  <div className="text-sm text-gray-500 italic">
                    No WLAN will be a DPSK network in R1. This section can be skipped.
                  </div>
                ) : dpskChoices ? (
                  <DpskChoicesPanel
                    choices={dpskChoices}
                    wlans={dpskWlans}
                    pools={dpskPools}
                    assignments={dpskAssignments}
                    onChange={updateDpsk}
                    disabled={planning}
                  />
                ) : (
                  <div className="text-sm text-gray-500 flex items-center gap-2">
                    {dpskLoading && <Loader2 size={14} className="animate-spin" />}
                    {dpskLoading ? 'Loading DPSK pools…' : 'DPSK choices unavailable'}
                  </div>
                )}
              </AccordionPanel>

              <AccordionPanel
                title="Migration Blueprint"
                subtitle={`${Object.keys(state.typeMappings).length} WLANs — SZ → R1 mapping`}
                section="blueprint"
                isOpen={openSections.has('blueprint')}
                onToggle={toggleSection}
              >
                <MigrationBlueprint state={{ ...state, typeMappings: migratedMappings }} layout={layout} />
              </AccordionPanel>

              <AccordionPanel
                title="Network Settings"
                subtitle={`${Object.keys(state.typeMappings).length} WLANs`}
                section="networks"
                isOpen={openSections.has('networks')}
                onToggle={toggleSection}
              >
                <NetworkSettingsReview typeMappings={migratedMappings ?? {}} />
              </AccordionPanel>

              {!canProceed && (
                <div className="flex justify-end items-center gap-3">
                  {(!decisionsComplete || !dpskComplete) && (
                    <span className="text-xs text-amber-700">
                      Finish the choices marked CHOOSE above
                    </span>
                  )}
                  {layout?.blocked && (
                    <span className="text-xs text-red-700">Placement is over R1's 15-SSID limit — adjust it above</span>
                  )}
                  <button
                    onClick={runPlan}
                    disabled={!decisionsComplete || !dpskComplete || !placementReady || planning}
                    className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 text-sm font-semibold flex items-center gap-2"
                  >
                    {planning && <Loader2 size={14} className="animate-spin" />}
                    {planning ? 'Building and validating plan…' : plan ? 'Build plan again' : 'Build plan with these choices'}
                  </button>
                </div>
              )}
            </>
          )}

          {/* Continue button */}
          {canProceed && (
            <div className="flex justify-end">
              <button
                onClick={() => dispatch({ type: 'SET_STEP', step: 6 })}
                className="px-6 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 font-semibold text-sm flex items-center gap-2"
              >
                Continue to Execute
                <ArrowRight size={14} />
              </button>
            </div>
          )}

          {/* Networks already built in R1 (an earlier run, or by hand): AP
              Mapping pairs each WLAN with the network R1 has, using the
              placement chosen above, so Create WLANs can be skipped. */}
          <div className="flex justify-end items-center gap-2 text-xs text-gray-500">
            {!placementReady && (
              <span className="text-amber-700">
                {layoutLoading || r1Reading ? 'Waiting for WLAN Placement to finish loading…'
                  : !layout ? "WLAN Placement didn't load — see the error at the top of the page"
                  : `WLAN Placement is blocked: ${layout.warnings.filter(w => w.severity === 'error')
                      .map(w => w.message).join('; ') || 'see the panel above'}`}
              </span>
            )}
            WLANs already in R1 for this venue?
            <button
              onClick={() => dispatch({ type: 'SET_STEP', step: 7 })}
              disabled={!placementReady}
              title={placementReady ? 'Uses the placement above; Create WLANs is not run'
                : 'Placement must load (and be under the 15-SSID limit) first'}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 disabled:text-gray-300 flex items-center gap-1"
            >
              Skip to AP Mapping <ArrowRight size={12} />
            </button>
          </div>
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
