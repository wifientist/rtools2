import { useReducer, useCallback, useState, useMemo, useRef } from 'react';
import { useAuth } from '@/context/AuthContext';
import { apiFetch } from '@/utils/api';
import { AlertCircle, Server, ArrowRight, Target, RotateCcw, X, Clock, ChevronRight, Download } from 'lucide-react';
import WizardStepper from '@/components/sz-config-migration/WizardStepper';
import ArtifactsBar from '@/components/sz-config-migration/ArtifactsBar';
import Step1SourceSelection from '@/components/sz-config-migration/Step1SourceSelection';
import Step2Extraction from '@/components/sz-config-migration/Step2Extraction';
import Step3Destination from '@/components/sz-config-migration/Step3Destination';
import StepVenueConfig from '@/components/sz-config-migration/StepVenueConfig';
import StepApMapping from '@/components/sz-config-migration/StepApMapping';
import Step4Review from '@/components/sz-config-migration/Step4Review';
import Step5Execute from '@/components/sz-config-migration/Step5Execute';
import Step6Results from '@/components/sz-config-migration/Step6Results';
import {
  wizardReducer,
  INITIAL_STATE,
  loadWizardState,
  clearWizardState,
  type WizardAction,
  type CensusResult,
  type ExtractionProgress,
  type SnapshotSummary,
  type R1InventorySummary,
  type ResolverResult,
  type TypeMapping,
  type PlanResult,
} from '@/types/szConfigMigration';
import { useMigrationSession, type MigrationSessionSummary } from '@/hooks/useMigrationSession';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const STEP_LABELS: Record<number, string> = {
  1: 'Source Selection',
  2: 'Extraction',
  3: 'Destination',
  4: 'Venue Config',
  5: 'WLANs',
  6: 'Create WLANs',
  7: 'AP Mapping',
  8: 'Results',
};

// Job statuses the cancel endpoint accepts.
const ACTIVE_JOB_STATUSES = ['PENDING', 'RUNNING', 'VALIDATING', 'AWAITING_CONFIRMATION'];

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-600',
  extracting: 'bg-blue-100 text-blue-700',
  reviewing: 'bg-purple-100 text-purple-700',
  executing: 'bg-orange-100 text-orange-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
};

export default function MigrateSzToR1Config() {
  const {
    activeControllerId: authActiveId, activeControllerName: authActiveName,
    activeControllerType: authActiveType, activeControllerSubtype: authActiveSubtype,
    secondaryControllerId: authSecondaryId, secondaryControllerName: authSecondaryName,
    secondaryControllerType: authSecondaryType, secondaryControllerSubtype: authSecondarySubtype,
    controllers,
  } = useAuth();

  // One SmartZone (source) and one RuckusONE (destination) among the two
  // selected controllers, in either slot -- as on Migration Audit.
  const slots = [
    { id: authActiveId, name: authActiveName, type: authActiveType, subtype: authActiveSubtype },
    { id: authSecondaryId, name: authSecondaryName, type: authSecondaryType, subtype: authSecondarySubtype },
  ];
  const szSlot = slots.find(s => s.id && s.type === 'SmartZone');
  const r1Slot = slots.find(s => s.id && s.type === 'RuckusONE');
  const activeControllerId = szSlot?.id ?? null;          // SZ controller (either slot)
  const activeControllerName = szSlot?.name ?? null;
  const secondaryControllerId = r1Slot?.id ?? null;       // R1 controller (either slot)
  const secondaryControllerName = r1Slot?.name ?? null;
  const secondaryControllerSubtype = r1Slot?.subtype ?? null;

  // Check for saved state before initializing reducer
  const savedState = useMemo(() => loadWizardState(), []);
  const isResume = savedState !== null && savedState.currentStep > 1;

  const [state, dispatch] = useReducer(wizardReducer, INITIAL_STATE, () => savedState || INITIAL_STATE);

  // Steps get a dispatch tied to the current run: a plan or read that finishes
  // after the run was dropped must not land in (or navigate) the new one.
  const currentRun = useRef(state.runId);
  currentRun.current = state.runId;
  const stepDispatch = useMemo<React.Dispatch<WizardAction>>(() => {
    const run = state.runId;
    return action => { if (run === currentRun.current) dispatch(action); };
  }, [state.runId]);

  // Remounts the steps on a deliberate restart, clearing their selectors.
  const [restarts, setRestarts] = useState(0);
  const [startOver, setStartOver] = useState<{ activeJobs: string[]; busy: boolean; error: string | null } | null>(null);
  const [resumeBannerDismissed, setResumeBannerDismissed] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // Controller validation
  const szReady = !!szSlot;
  const r1Ready = !!r1Slot;
  const isMSP = secondaryControllerSubtype === 'MSP';

  // For EC controllers: use the controller's r1_tenant_id from the database
  // For MSP controllers: user selects tenant via EC selector in Step 3
  const secondaryController = controllers?.find((c: any) => c.id === secondaryControllerId);
  const defaultTenantId = isMSP ? null : (secondaryController?.r1_tenant_id || null);

  // Session persistence hook
  const { sessions, sessionsLoading, loadSession } = useMigrationSession(
    state,
    dispatch,
    activeControllerId ?? null,
  );

  const maxStep = state.highestStepReached ?? state.currentStep;
  const handleStepClick = useCallback((step: number) => {
    if (step !== state.currentStep && step <= maxStep) {
      dispatch({ type: 'SET_STEP', step });
    }
  }, [state.currentStep, maxStep]);

  // Stages that write to R1 and keep going without this page.
  const writeJobIds = [state.venueConfigJobId, state.finalJobId, state.apMappingJobId]
    .filter((id): id is string => !!id);

  const openStartOver = async () => {
    setStartOver({ activeJobs: [], busy: true, error: null });
    const checks = await Promise.all(writeJobIds.map(async id => {
      try {
        const res = await apiFetch(`${API_URL}/jobs/${id}/status`);
        if (res.status === 404) return { id, active: false, failed: false };
        if (!res.ok) return { id, active: false, failed: true };
        const job = await res.json();
        return { id, active: ACTIVE_JOB_STATUSES.includes(job.status), failed: false };
      } catch {
        return { id, active: false, failed: true };
      }
    }));
    setStartOver({
      activeJobs: checks.filter(c => c.active).map(c => c.id),
      busy: false,
      error: checks.some(c => c.failed) ? 'Could not check whether a job from this run is still running.' : null,
    });
  };

  const confirmStartOver = async () => {
    if (!startOver) return;
    setStartOver({ ...startOver, busy: true, error: null });
    for (const id of startOver.activeJobs) {
      try {
        const res = await apiFetch(`${API_URL}/jobs/${id}/cancel`, { method: 'POST' });
        if (!res.ok) throw new Error(`${res.status}`);
      } catch (e: any) {
        // Still writing to R1: don't lose sight of it.
        setStartOver({ ...startOver, busy: false, error: `Could not cancel the running job (${e.message}), so nothing was reset.` });
        return;
      }
    }
    dispatch({ type: 'RESET' });
    setRestarts(n => n + 1);
    setResumeBannerDismissed(true);
    setShowHistory(false);
    setStartOver(null);
  };

  const hasProgress = state.currentStep > 1 || !!state.selectedDomainId || !!state.sessionId;

  const handleResumeSession = useCallback((session: MigrationSessionSummary) => {
    setRestarts(n => n + 1);
    loadSession(session.id);
    setShowHistory(false);
    setResumeBannerDismissed(true);
  }, [loadSession]);

  return (
    <div className="p-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">
            SZ → R1 Config Migration
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Migrate WiFi network configurations from SmartZone to RuckusONE
          </p>
        </div>
        <div className="flex items-center gap-2">
          {szReady && r1Ready && hasProgress && (
            <button
              onClick={openStartOver}
              className="flex items-center gap-1.5 text-xs text-red-600 hover:text-red-800 border border-red-200 rounded-lg px-3 py-1.5"
            >
              <RotateCcw size={13} />
              Start over
            </button>
          )}
          {szReady && r1Ready && sessions.length > 0 && (
            <button
              onClick={() => setShowHistory(!showHistory)}
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg px-3 py-1.5"
            >
              <Clock size={13} />
              History ({sessions.length})
            </button>
          )}
        </div>
      </div>

      {/* Start over confirmation */}
      {startOver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5">
            <h3 className="text-lg font-semibold text-gray-800">Start this migration over?</h3>
            <p className="text-sm text-gray-600 mt-2">
              The wizard goes back to Step 1 with no zone, venue or plan selected.
              {state.selectedZoneName && <> You are leaving <strong>{state.selectedZoneName}</strong>{state.destVenueName && <> → <strong>{state.destVenueName}</strong></>}.</>}
            </p>
            <p className="text-sm text-gray-600 mt-2">
              Nothing already created in R1 is removed. This run stays in History.
            </p>
            {startOver.activeJobs.length > 0 && (
              <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mt-3">
                A job from this run is still writing to R1. Starting over cancels it; it stops at its next safe point and keeps what it already created.
              </p>
            )}
            {startOver.error && (
              <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 mt-3">{startOver.error}</p>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setStartOver(null)}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Go back
              </button>
              <button
                onClick={confirmStartOver}
                disabled={startOver.busy}
                className="px-3 py-1.5 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-gray-400"
              >
                {startOver.activeJobs.length > 0 ? 'Cancel job and start over' : 'Start over'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Session history panel */}
      {showHistory && sessions.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg mb-6 overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
              Recent Migrations
            </span>
            <button
              onClick={() => setShowHistory(false)}
              className="text-gray-400 hover:text-gray-600"
            >
              <X size={14} />
            </button>
          </div>
          <div className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
            {sessions.map(s => (
              <button
                key={s.id}
                onClick={() => handleResumeSession(s)}
                className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-50 text-left group"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${STATUS_COLORS[s.status] || STATUS_COLORS.draft}`}>
                    {s.status}
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">
                      {s.sz_zone_name || 'No zone selected'}
                      {s.r1_venue_name && (
                        <span className="text-gray-400 font-normal"> → {s.r1_venue_name}</span>
                      )}
                    </div>
                    <div className="text-[11px] text-gray-400">
                      Step {s.current_step}: {STEP_LABELS[s.current_step] || '?'}
                      {s.wlan_count != null && <> · {s.wlan_count} WLANs</>}
                      {' · '}
                      {formatRelativeTime(s.updated_at)}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  {['completed', 'failed'].includes(s.status) && s.summary_json?.execution && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        window.open(`${API_URL}/sz-migration/sessions/${s.id}/report.csv`, '_blank');
                      }}
                      className="p-1 text-gray-400 hover:text-blue-600 rounded hover:bg-blue-50"
                      title="Download CSV report"
                    >
                      <Download size={13} />
                    </button>
                  )}
                  <ChevronRight size={14} className="text-gray-300 group-hover:text-gray-500" />
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Controller validation */}
      {(!szReady || !r1Ready) && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <div className="flex items-start gap-2">
            <AlertCircle className="text-red-500 mt-0.5" size={18} />
            <div>
              <p className="text-red-800 font-semibold text-sm">Controller Setup Required</p>
              <p className="text-red-600 text-sm mt-1">
                Select one <strong>SmartZone</strong> (source) and one <strong>RuckusONE</strong> (destination)
                as your primary and secondary controllers, in either order.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Controller indicator */}
      {szReady && r1Ready && (
        <div className="flex items-center gap-3 mb-6 bg-gray-50 border border-gray-200 rounded-lg p-3">
          <div className="flex items-center gap-2 text-sm">
            <Server size={16} className="text-blue-600" />
            <span className="font-medium">{activeControllerName}</span>
            <span className="text-gray-400 text-xs">(SZ)</span>
          </div>
          <ArrowRight size={16} className="text-gray-400" />
          <div className="flex items-center gap-2 text-sm">
            <Target size={16} className="text-green-600" />
            <span className="font-medium">{secondaryControllerName}</span>
            <span className="text-gray-400 text-xs">(R1{isMSP ? ' MSP' : ''})</span>
          </div>
        </div>
      )}

      {/* Resume banner */}
      {szReady && r1Ready && isResume && !resumeBannerDismissed && state.currentStep > 1 && state.runId === savedState?.runId && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4 flex items-start justify-between">
          <div className="flex items-start gap-2">
            <RotateCcw size={16} className="text-blue-600 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-blue-800">
                Resuming previous migration
              </p>
              <p className="text-xs text-blue-600 mt-0.5">
                Step {state.currentStep}: {STEP_LABELS[state.currentStep]}
                {state.selectedZoneName && <> — Zone: {state.selectedZoneName}</>}
                {state.destVenueName && <> → Venue: {state.destVenueName}</>}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 ml-4 flex-shrink-0">
            <button
              onClick={openStartOver}
              className="text-xs text-blue-600 hover:text-blue-800 underline"
            >
              Start fresh
            </button>
            <button
              onClick={() => setResumeBannerDismissed(true)}
              className="text-blue-400 hover:text-blue-600"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Artifacts bar — always visible once we have data */}
      {szReady && r1Ready && (
        <ArtifactsBar state={state} />
      )}

      {/* Wizard stepper */}
      {szReady && r1Ready && (
        <>
          <WizardStepper
            currentStep={state.currentStep}
            highestStepReached={maxStep}
            onStepClick={handleStepClick}
          />

          {/* Error banner */}
          {state.error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-start gap-2">
              <AlertCircle className="text-red-500 mt-0.5 flex-shrink-0" size={16} />
              <div className="text-sm text-red-700">{state.error}</div>
              <button
                onClick={() => dispatch({ type: 'SET_ERROR', error: null })}
                className="ml-auto text-red-400 hover:text-red-600 text-sm"
              >
                dismiss
              </button>
            </div>
          )}

          {/* Step content */}
          <div key={restarts}>
          {state.currentStep === 1 && (
            <Step1SourceSelection
              szControllerId={activeControllerId!}
              r1ControllerName={secondaryControllerName}
              state={state}
              dispatch={stepDispatch}
            />
          )}

          {state.currentStep === 2 && (
            <Step2Extraction
              state={state}
              dispatch={stepDispatch}
            />
          )}

          {state.currentStep === 3 && (
            <Step3Destination
              r1ControllerId={secondaryControllerId!}
              isMSP={isMSP}
              defaultTenantId={defaultTenantId}
              state={state}
              dispatch={stepDispatch}
            />
          )}

          {state.currentStep === 4 && (
            <StepVenueConfig state={state} dispatch={stepDispatch} />
          )}

          {state.currentStep === 5 && (
            <Step4Review
              state={state}
              dispatch={stepDispatch}
            />
          )}

          {state.currentStep === 6 && (
            <Step5Execute
              state={state}
              dispatch={stepDispatch}
            />
          )}

          {state.currentStep === 7 && (
            <StepApMapping state={state} dispatch={stepDispatch} />
          )}

          {state.currentStep === 8 && (
            <Step6Results
              state={state}
              dispatch={stepDispatch}
            />
          )}
          </div>
        </>
      )}
    </div>
  );
}

function formatRelativeTime(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    if (diffDays < 30) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  } catch {
    return isoString;
  }
}
