// SZ → R1 Config Migration Wizard Types

// ── Census ─────────────────────────────────────────────────────────

export interface CensusResult {
  zone_id: string;
  zone_name: string;
  wlan_count: number;
  wlan_group_count: number;
  ap_group_count: number;
  ap_count: number;
}

// ── Extraction ─────────────────────────────────────────────────────

export interface ExtractionProgress {
  phase: string;
  message: string;
  data: Record<string, any>;
}

export interface SnapshotSummary {
  zone_name: string;
  wlan_count: number;
  wlan_group_count: number;
  ap_group_count: number;
  ap_count: number;
  referenced_objects_count: number;
  warning_count: number;
}

// ── R1 Inventory ───────────────────────────────────────────────────

export interface R1InventorySummary {
  venue_name: string;
  wifi_network_count: number;
  ap_group_count: number;
  ap_count: number;
  dpsk_pool_count: number;
  identity_group_count: number;
}

// ── Resolver / Type Mapping ────────────────────────────────────────

export interface WLANActivation {
  wlan_id: string;
  wlan_name: string;
  ssid: string;
  auth_type: string;
  ap_group_id: string;
  ap_group_name: string;
  radios: string[];
  source: string;
  ap_count: number;
}

export interface APGroupSummary {
  ap_group_id: string;
  ap_group_name: string;
  ap_count: number;
  ssid_count: number;
  limit: number;
  over_limit: boolean;
  ssids: string[];
}

export interface ResolverResult {
  activations: WLANActivation[];
  ap_group_summaries: APGroupSummary[];
  warnings: string[];
  blocked: boolean;
}

export interface TypeMapping {
  wlan_name: string;
  sz_auth_type: string;
  r1_network_type: string;
  notes: string;
  needs_user_decision: boolean;
  dpsk_type: string | null;
}

// ── Plan / Validation ──────────────────────────────────────────────

export interface ConflictItem {
  unit_id?: string;
  resource_type: string;
  resource_name?: string;
  description: string;
  severity: 'error' | 'warning' | 'info';
  resolution?: string;
}

export interface ActionItem {
  resource_type: string;
  resource_name?: string;
  name?: string;
  action: string;
  details?: string;
  notes?: string;
}

export interface PlanResult {
  job_id: string;
  status: string;
  valid: boolean;
  message: string;
  summary: Record<string, any>;
  conflicts: ConflictItem[];
  unit_count: number;
  estimated_api_calls: number;
  actions: ActionItem[];
  resolver_result: ResolverResult;
  type_mappings: Record<string, TypeMapping>;
  r1_inventory_summary: Record<string, any>;
}

// ── Wizard State ───────────────────────────────────────────────────

export interface WizardState {
  currentStep: number;
  highestStepReached: number;

  // Step 1: Source
  szControllerId: number | null;
  selectedDomainId: string | null;
  selectedDomainName: string | null;
  selectedZoneId: string | null;
  selectedZoneName: string | null;
  census: CensusResult | null;

  // Step 2: Extraction
  extractionJobId: string | null;
  extractionStatus: 'idle' | 'running' | 'completed' | 'failed';
  extractionProgress: ExtractionProgress | null;
  snapshotSummary: SnapshotSummary | null;

  // Step 3: Destination
  r1ControllerId: number | null;
  tenantId: string | null;
  destVenueId: string | null;
  destVenueName: string | null;
  r1SnapshotJobId: string | null;
  r1InventorySummary: R1InventorySummary | null;

  // Step 4: Review
  resolverResult: ResolverResult | null;
  typeMappings: Record<string, TypeMapping> | null;
  planJobId: string | null;
  planResult: PlanResult | null;

  // Step 5: Execution
  executionStarted: boolean;

  // Step 6: Results
  finalJobId: string | null;

  // Session persistence (M6b)
  sessionId: number | null;

  // Global
  error: string | null;
}

export type WizardAction =
  | { type: 'SET_STEP'; step: number }
  | { type: 'SET_SOURCE'; szControllerId: number; domainId: string; domainName: string }
  | { type: 'SET_ZONE'; zoneId: string; zoneName: string }
  | { type: 'SET_CENSUS'; census: CensusResult }
  | { type: 'START_EXTRACTION'; jobId: string }
  | { type: 'UPDATE_EXTRACTION'; status: WizardState['extractionStatus']; progress?: ExtractionProgress | null; summary?: SnapshotSummary | null }
  | { type: 'SET_DESTINATION'; r1ControllerId: number; tenantId: string | null; venueId: string; venueName: string }
  | { type: 'SET_R1_SNAPSHOT'; jobId: string; summary: R1InventorySummary }
  | { type: 'SET_RESOLVER'; result: ResolverResult; mappings: Record<string, TypeMapping> }
  | { type: 'SET_PLAN'; jobId: string; result: PlanResult }
  | { type: 'SET_EXECUTION_STARTED'; jobId: string }
  | { type: 'SET_SESSION_ID'; sessionId: number }
  | { type: 'SET_ERROR'; error: string | null }
  | { type: 'RESET' };

export const INITIAL_STATE: WizardState = {
  currentStep: 1,
  highestStepReached: 1,
  szControllerId: null,
  selectedDomainId: null,
  selectedDomainName: null,
  selectedZoneId: null,
  selectedZoneName: null,
  census: null,
  extractionJobId: null,
  extractionStatus: 'idle',
  extractionProgress: null,
  snapshotSummary: null,
  r1ControllerId: null,
  tenantId: null,
  destVenueId: null,
  destVenueName: null,
  r1SnapshotJobId: null,
  r1InventorySummary: null,
  resolverResult: null,
  typeMappings: null,
  planJobId: null,
  planResult: null,
  executionStarted: false,
  finalJobId: null,
  sessionId: null,
  error: null,
};

const STORAGE_KEY = 'sz_config_migration_wizard';

export function saveWizardState(state: WizardState) {
  try {
    // Don't persist transient fields
    const { extractionProgress, error, ...persistable } = state;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ...persistable,
      _savedAt: Date.now(),
    }));
  } catch {
    // localStorage full or unavailable
  }
}

export function loadWizardState(): WizardState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    // Restore with defaults for transient fields
    return {
      ...INITIAL_STATE,
      ...saved,
      extractionProgress: null,
      error: null,
      // If extraction was running when page reloaded, mark as completed
      // (SSE reconnect will poll for actual status)
      extractionStatus: saved.extractionStatus === 'running' ? 'completed' : saved.extractionStatus,
      // Backwards compat: old saved state won't have this field
      highestStepReached: saved.highestStepReached || saved.currentStep || 1,
    };
  } catch {
    return null;
  }
}

export function clearWizardState() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  const next = wizardReducerInner(state, action);
  if (action.type === 'RESET') {
    clearWizardState();
  } else {
    saveWizardState(next);
  }
  return next;
}

function wizardReducerInner(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'SET_STEP':
      return {
        ...state,
        currentStep: action.step,
        highestStepReached: Math.max(state.highestStepReached ?? state.currentStep, action.step),
        error: null,
      };

    case 'SET_SOURCE':
      return {
        ...state,
        szControllerId: action.szControllerId,
        selectedDomainId: action.domainId,
        selectedDomainName: action.domainName,
        selectedZoneId: null,
        selectedZoneName: null,
        census: null,
      };

    case 'SET_ZONE':
      return {
        ...state,
        selectedZoneId: action.zoneId,
        selectedZoneName: action.zoneName,
        census: null,
      };

    case 'SET_CENSUS':
      return { ...state, census: action.census };

    case 'START_EXTRACTION':
      return {
        ...state,
        extractionJobId: action.jobId,
        extractionStatus: 'running',
        extractionProgress: null,
        snapshotSummary: null,
        currentStep: 2,
        highestStepReached: Math.max(state.highestStepReached ?? state.currentStep, 2),
      };

    case 'UPDATE_EXTRACTION':
      return {
        ...state,
        extractionStatus: action.status,
        extractionProgress: action.progress ?? state.extractionProgress,
        snapshotSummary: action.summary ?? state.snapshotSummary,
      };

    case 'SET_DESTINATION':
      return {
        ...state,
        r1ControllerId: action.r1ControllerId,
        tenantId: action.tenantId,
        destVenueId: action.venueId,
        destVenueName: action.venueName,
      };

    case 'SET_R1_SNAPSHOT':
      return {
        ...state,
        r1SnapshotJobId: action.jobId,
        r1InventorySummary: action.summary,
      };

    case 'SET_RESOLVER':
      return {
        ...state,
        resolverResult: action.result,
        typeMappings: action.mappings,
      };

    case 'SET_PLAN':
      return {
        ...state,
        planJobId: action.jobId,
        planResult: action.result,
      };

    case 'SET_EXECUTION_STARTED':
      return {
        ...state,
        executionStarted: true,
        finalJobId: action.jobId,
        currentStep: 5,
        highestStepReached: Math.max(state.highestStepReached ?? state.currentStep, 5),
      };

    case 'SET_SESSION_ID':
      return { ...state, sessionId: action.sessionId };

    case 'SET_ERROR':
      return { ...state, error: action.error };

    case 'RESET':
      return { ...INITIAL_STATE };

    default:
      return state;
  }
}
