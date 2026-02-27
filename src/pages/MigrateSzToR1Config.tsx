import { useReducer, useCallback, useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import { AlertCircle, Server, ArrowRight, Target, RotateCcw, X, Clock, ChevronRight, Download } from 'lucide-react';
import WizardStepper from '@/components/sz-config-migration/WizardStepper';
import ArtifactsBar from '@/components/sz-config-migration/ArtifactsBar';
import Step1SourceSelection from '@/components/sz-config-migration/Step1SourceSelection';
import Step2Extraction from '@/components/sz-config-migration/Step2Extraction';
import Step3Destination from '@/components/sz-config-migration/Step3Destination';
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
  4: 'Review',
  5: 'Execute',
  6: 'Results',
};

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
    activeControllerId,
    activeControllerName,
    activeControllerType,
    secondaryControllerId,
    secondaryControllerName,
    secondaryControllerType,
    secondaryControllerSubtype,
    controllers,
  } = useAuth();

  // Check for saved state before initializing reducer
  const savedState = useMemo(() => loadWizardState(), []);
  const isResume = savedState !== null && savedState.currentStep > 1;

  const [state, dispatch] = useReducer(wizardReducer, INITIAL_STATE, () => savedState || INITIAL_STATE);
  const [resumeBannerDismissed, setResumeBannerDismissed] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // Controller validation
  const szReady = activeControllerId && activeControllerType === 'SmartZone';
  const r1Ready = secondaryControllerId && secondaryControllerType === 'RuckusONE';
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

  const handleStartFresh = useCallback(() => {
    dispatch({ type: 'RESET' });
    setResumeBannerDismissed(true);
  }, []);

  const handleResumeSession = useCallback((session: MigrationSessionSummary) => {
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
                Set your <strong>Primary controller</strong> to a SmartZone and{' '}
                <strong>Secondary controller</strong> to a RuckusONE on the Controllers page.
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
      {szReady && r1Ready && isResume && !resumeBannerDismissed && state.currentStep > 1 && (
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
              onClick={handleStartFresh}
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
          {state.currentStep === 1 && (
            <Step1SourceSelection
              szControllerId={activeControllerId!}
              state={state}
              dispatch={dispatch}
            />
          )}

          {state.currentStep === 2 && (
            <Step2Extraction
              state={state}
              dispatch={dispatch}
            />
          )}

          {state.currentStep === 3 && (
            <Step3Destination
              r1ControllerId={secondaryControllerId!}
              isMSP={isMSP}
              defaultTenantId={defaultTenantId}
              state={state}
              dispatch={dispatch}
            />
          )}

          {state.currentStep === 4 && (
            <Step4Review
              state={state}
              dispatch={dispatch}
            />
          )}

          {state.currentStep === 5 && (
            <Step5Execute
              state={state}
              dispatch={dispatch}
            />
          )}

          {state.currentStep === 6 && (
            <Step6Results
              state={state}
              dispatch={dispatch}
            />
          )}
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
