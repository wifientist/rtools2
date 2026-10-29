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

// ── Destination venue (create + zone settings) ────────────────────

export interface VenueDraft {
  name: string;
  description: string;
  address: {
    addressLine: string;
    countryCode: string;
    timezone: string | null;
    latitude: number | null;
    longitude: number | null;
  };
  notes: string[];
}

export interface VenueSettingsSection {
  key: string;
  label: string;
  path: string;
  media: string;
  rows: Array<{ label: string; value: unknown }>;
  notes: string[];
}

export interface VenueSettingsPlan {
  sections: VenueSettingsSection[];
  not_carried: string[];
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
  converted?: boolean;   // moved onto R1's own DPSK service on Review
  // Default (access) VLAN on each side. r1_vlan_source 'existing' = an R1
  // network of the same name is reused as is, with its own VLAN.
  sz_vlan?: number | null;
  r1_vlan?: number | null;
  r1_vlan_source?: 'create' | 'existing';
  vlan_notes?: string[];
}

// SZ-mapped types that authenticate against RADIUS, and so can be moved
// onto R1's own DPSK service.
export const CONVERTIBLE_TYPES = ['dpsk_external', 'aaa'];

// ── R1 placement (Review) ──────────────────────────────────────────
// Derived server-side from what each AP broadcasts in SZ; recomputed on
// every change of choice.

export interface LayoutChoices {
  all_ap_wlans: string[] | null;          // null = the suggestion
  create_groups: string[];                // group keys
  group_names: Record<string, string>;    // key -> name
  include_unbroadcast: string[];          // wlan ids: create only
  stage_unbroadcast?: string[];           // wlan ids: create + build/fill their paired AP Group, not activated
  pairing?: { mode: 'wlan_group' | 'pattern'; wlan_pattern?: string; ap_pattern?: string };
  excluded_wlans?: string[];              // wlan ids not migrated at all (test / temp SSIDs...)
}

// Every SZ WLAN, for the include / exclude list
export interface LayoutCatalogWlan {
  id: string; name: string; ssid: string; auth_type: string;
  ap_count: number;        // SZ APs broadcasting it
  excluded: boolean;
  skip_hint: boolean;      // name / SSID looks like test / temp
}

export const DEFAULT_LAYOUT_CHOICES: LayoutChoices = {
  all_ap_wlans: null, create_groups: [], group_names: {}, include_unbroadcast: [], stage_unbroadcast: [],
  pairing: { mode: 'wlan_group' },
};

export interface LayoutWlan {
  id: string;
  name: string;
  ssid: string;
  auth_type: string;
  sz_ap_count: number;
  share: number;
  suggested_all_ap: boolean;
  mode: 'all_ap_groups' | 'ap_groups';
  radio_types: string[];
  radio_notes: string[];
  gains_in_r1?: number;
  gains_sample?: string[];
  targets?: string[];
  targets_ready?: number;
  unready_note?: string;
}

export interface LayoutGroup {
  key: string;
  name: string;
  name_source: string;
  renamed: boolean;
  name_options?: Array<{ name: string; source: string }>;   // suggestion first, then AP name / unit WLAN
  ambiguous?: boolean;                                      // options disagree and nobody has picked yet
  wlan_ids: string[];
  ssids: string[];
  ap_count: number;
  aps: Array<{ name: string; serial: string | null; mac: string | null }>;
  r1_id: string | null;
  exists: boolean;
  matched_by: 'SSIDs' | 'name' | null;   // how an existing R1 group was found
  create: boolean;
  ssid_total: number;
  over_limit: boolean;
  radio_types?: Record<string, string[]>;   // wlan id -> R1 radios for THIS group's APs
  staged?: boolean;                         // per-unit group for WLANs no AP broadcasts: built, not activated
  chosen?: boolean;                         // per-unit group ticked to build (its WLANs staged)
}

export interface R1Layout {
  summary: {
    aps: number;
    aps_broadcasting: number;
    aps_default_group: number;
    property_wide_share: number;
    sz_tiers: Record<string, number>;
    groups: number;
    groups_existing: number;
    groups_to_create: number;
    groups_pending: number;
    all_ap_wlans: number;
    staged_wlans?: number;
    staged_groups?: number;
    staged_aps?: number;
  };
  default_group: { ap_count: number; ssid_total: number; over_limit: boolean };
  wlans: LayoutWlan[];
  groups: LayoutGroup[];
  unbroadcast: Array<{ id: string; name: string; ssid: string; auth_type: string; reason: string; included: boolean;
                       mode: 'skip' | 'create' | 'stage'; paired_aps: string[]; paired_group: string | null }>;
  staged: { groups: LayoutGroup[]; unpaired: string[]; conflicts: string[]; mode: string };
  catalog?: LayoutCatalogWlan[];
  warnings: Array<{ message: string; severity: 'error' | 'warning' }>;
  blocked: boolean;
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
  notes?: string | string[];
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

// ── RADIUS choices (Review step) ───────────────────────────────────
// Shared secrets are deliberately NOT part of any of these: wizard state is
// saved to localStorage and to the session record. A typed secret lives only
// in the Review component's memory and is sent once, with the plan request.

export type RadiusAction = 'create' | 'existing' | 'skip';
export type WlanFallback = 'skip' | 'dpsk';

export interface RadiusService {
  id: string;
  name: string;
  roles: Array<'authentication' | 'accounting'>;
  primary: string | null;
  secondary: string | null;
  used_by: Array<{ wlan_id: string; wlan_name: string; auth_type: string; role: string }>;
  extraction_error: boolean;
  suggestion: { action: RadiusAction; r1_profile_id: string | null; reason: string };
}

export interface R1RadiusProfile {
  id: string;
  name: string;
  type: string | null;
  primary: string | null;
  secondary: string | null;
  placeholder_secret: boolean;  // still on the migration placeholder: authenticates nobody
}

export interface RadiusChoices {
  services: RadiusService[];
  r1_profiles: R1RadiusProfile[];
  error: string | null;
}

export interface RadiusDecision {
  action: RadiusAction;
  r1_profile_id: string | null;
}

// ── DPSK service choices (Review step) ─────────────────────────────
// Counts and settings only; nothing here ever holds a passphrase.

export interface DpskPoolSpec {
  mode: 'new' | 'existing';
  existing_pool_id?: string | null;
  name?: string;
  identity_group?: { mode: 'new' | 'existing'; name?: string; id?: string | null };
  passphrase_length: number;
  passphrase_format: string;
  word_count?: number;               // DICTIONARY_WORDS only (3-6); R1 requires it there
  numeric_suffix_enabled?: boolean;  // DICTIONARY_WORDS only
  devices_per_passphrase: number | null;  // null = unlimited
  expiration_days: number | null;         // null = never
}

export interface R1DpskPool {
  id: string;
  name: string;
  passphrase_length: number | null;
  passphrase_format: string | null;
  devices_per_passphrase: number | null;
  network_count: number | null;
  identity_groups: Array<{ id: string; name: string; identity_count: number | null }>;
}

export interface R1IdentityGroup {
  id: string;
  name: string;
  dpsk_pool_id: string | null;
  identity_count: number | null;
}

export interface DpskChoices {
  r1_pools: R1DpskPool[];
  r1_identity_groups: R1IdentityGroup[];
  default_pool: DpskPoolSpec;
  formats: string[];
  limits: { passphrase_length: [number, number]; devices_per_passphrase: [number, number]; word_count: [number, number] };
  error: string | null;
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
  destVenueCreated: boolean;   // created by this wizard (vs an existing venue)
  r1SnapshotJobId: string | null;
  r1InventorySummary: R1InventorySummary | null;

  // Step 4: Review
  resolverResult: ResolverResult | null;
  typeMappings: Record<string, TypeMapping> | null;
  radiusDecisions: Record<string, RadiusDecision> | null;  // null = not chosen yet
  wlanFallbacks: Record<string, WlanFallback>;
  dpskPools: Record<string, DpskPoolSpec> | null;   // null = not edited yet (default applies)
  dpskAssignments: Record<string, string>;          // wlan id -> pool key
  layoutChoices: LayoutChoices;
  authConversions: Record<string, 'dpsk'>;   // wlan id -> converted to R1 DPSK
  // Extraction job whose WLAN-step defaults (SZ as-is placement, create + stage,
  // convert to R1 DPSK) were filled in -- once per extraction, never over choices made.
  layoutDefaultsFor?: string | null;
  authDefaultsFor?: string | null;
  venueSettings: string[] | null;            // zone setting sections to apply; null = all
  venueConfigJobId: string | null;           // Venue Configuration stage job
  apMappingJobId: string | null;             // AP Mapping stage job
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
  | { type: 'USE_EXTRACTION'; jobId: string; summary: SnapshotSummary }
  | { type: 'UPDATE_EXTRACTION'; status: WizardState['extractionStatus']; progress?: ExtractionProgress | null; summary?: SnapshotSummary | null }
  | { type: 'SET_DESTINATION'; r1ControllerId: number; tenantId: string | null; venueId: string; venueName: string; created?: boolean }
  | { type: 'SET_R1_SNAPSHOT'; jobId: string; summary: R1InventorySummary }
  | { type: 'SET_RESOLVER'; result: ResolverResult; mappings: Record<string, TypeMapping> }
  | { type: 'SET_PLAN'; jobId: string; result: PlanResult }
  | { type: 'SET_RADIUS_CHOICES'; decisions: Record<string, RadiusDecision>; fallbacks: Record<string, WlanFallback> }
  | { type: 'CLEAR_PLAN' }
  | { type: 'SET_LAYOUT_CHOICES'; choices: LayoutChoices }
  | { type: 'SET_AUTH_CONVERSIONS'; conversions: Record<string, 'dpsk'> }
  | { type: 'APPLY_WLAN_DEFAULTS'; jobId: string; choices?: LayoutChoices; conversions?: Record<string, 'dpsk'> }
  | { type: 'SET_VENUE_SETTINGS'; sections: string[] | null }
  | { type: 'SET_VENUE_CONFIG_JOB'; jobId: string }
  | { type: 'SET_AP_MAPPING_JOB'; jobId: string }
  | { type: 'SET_DPSK_CHOICES'; pools: Record<string, DpskPoolSpec>; assignments: Record<string, string> }
  | { type: 'SET_EXECUTION_STARTED'; jobId: string }
  | { type: 'RERUN_EXECUTION' }
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
  destVenueCreated: false,
  r1SnapshotJobId: null,
  r1InventorySummary: null,
  resolverResult: null,
  typeMappings: null,
  radiusDecisions: null,
  wlanFallbacks: {},
  dpskPools: null,
  dpskAssignments: {},
  layoutChoices: DEFAULT_LAYOUT_CHOICES,
  authConversions: {},
  venueSettings: null,
  venueConfigJobId: null,
  apMappingJobId: null,
  planJobId: null,
  planResult: null,
  executionStarted: false,
  finalJobId: null,
  sessionId: null,
  error: null,
};

const STORAGE_KEY = 'sz_config_migration_wizard';

// The server's snapshot summary (field names vary by version) as the wizard keeps it.
export function toSnapshotSummary(s: Record<string, any>): SnapshotSummary {
  return {
    zone_name: s.zone_name || '',
    wlan_count: s.wlan_count || s.wlans || 0,
    wlan_group_count: s.wlan_group_count || s.wlan_groups || 0,
    ap_group_count: s.ap_group_count || s.ap_groups || 0,
    ap_count: s.ap_count || s.aps || 0,
    referenced_objects_count: s.referenced_objects_count || s.referenced_objects || 0,
    warning_count: s.warning_count || s.warnings || 0,
  } as SnapshotSummary;
}

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
      // Kept as saved: Step 2 asks the server for the real status. (A
      // "running" extraction used to be marked completed here, which let an
      // extraction killed by a server restart pass as finished.)
      extractionStatus: saved.extractionStatus,
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

    case 'USE_EXTRACTION':
      // An earlier, completed extraction of this zone: everything downstream
      // starts over exactly as for a fresh one, minus the SZ read.
      return {
        ...wizardReducerInner(state, { type: 'START_EXTRACTION', jobId: action.jobId }),
        extractionStatus: 'completed',
        snapshotSummary: action.summary,
        currentStep: 3,
        highestStepReached: Math.max(state.highestStepReached ?? state.currentStep, 3),
      };

    case 'START_EXTRACTION':
      return {
        ...state,
        extractionJobId: action.jobId,
        extractionStatus: 'running',
        extractionProgress: null,
        snapshotSummary: null,
        radiusDecisions: null,
        wlanFallbacks: {},
        dpskPools: null,
        dpskAssignments: {},
        layoutChoices: DEFAULT_LAYOUT_CHOICES,
        authConversions: {},
        venueSettings: null,
        venueConfigJobId: null,
        apMappingJobId: null,
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
        destVenueCreated: action.venueId === state.destVenueId ? state.destVenueCreated : !!action.created,
        // A snapshot describes one venue: a different venue needs its own.
        r1SnapshotJobId: action.venueId === state.destVenueId ? state.r1SnapshotJobId : null,
        venueConfigJobId: action.venueId === state.destVenueId ? state.venueConfigJobId : null,
        apMappingJobId: action.venueId === state.destVenueId ? state.apMappingJobId : null,
        r1InventorySummary: action.venueId === state.destVenueId ? state.r1InventorySummary : null,
        // R1 profile ids belong to the tenant: a new destination re-asks.
        radiusDecisions: action.tenantId === state.tenantId ? state.radiusDecisions : null,
        dpskPools: action.tenantId === state.tenantId ? state.dpskPools : null,
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

    case 'SET_RADIUS_CHOICES':
      return {
        ...state,
        radiusDecisions: action.decisions,
        wlanFallbacks: action.fallbacks,
      };

    case 'SET_DPSK_CHOICES':
      return { ...state, dpskPools: action.pools, dpskAssignments: action.assignments };

    case 'SET_LAYOUT_CHOICES':
      // A different placement is a different plan.
      return { ...state, layoutChoices: action.choices, planJobId: null, planResult: null };

    case 'SET_VENUE_SETTINGS':
      return { ...state, venueSettings: action.sections };

    case 'SET_VENUE_CONFIG_JOB':
      return { ...state, venueConfigJobId: action.jobId };

    case 'SET_AP_MAPPING_JOB':
      return { ...state, apMappingJobId: action.jobId };

    case 'SET_AUTH_CONVERSIONS':
      return { ...state, authConversions: action.conversions, planJobId: null, planResult: null };

    case 'APPLY_WLAN_DEFAULTS': {
      // Marks the defaults done for this extraction; fills in only what is given.
      // Choices the operator already made come back unchanged and keep their plan.
      const changed = (action.choices && action.choices !== state.layoutChoices)
        || (action.conversions && action.conversions !== state.authConversions);
      return {
        ...state,
        ...(action.choices ? { layoutChoices: action.choices, layoutDefaultsFor: action.jobId } : {}),
        ...(action.conversions ? { authConversions: action.conversions, authDefaultsFor: action.jobId } : {}),
        ...(changed ? { planJobId: null, planResult: null } : {}),
      };
    }

    case 'CLEAR_PLAN':
      // Choices changed after a plan was built: that plan no longer describes
      // what would run, so it is dropped and has to be rebuilt.
      return { ...state, planJobId: null, planResult: null };

    case 'SET_EXECUTION_STARTED':
      return {
        ...state,
        executionStarted: true,
        finalJobId: action.jobId,
        currentStep: 6,
        highestStepReached: Math.max(state.highestStepReached ?? state.currentStep, 6),
      };

    case 'RERUN_EXECUTION':
      // Plan and run Create WLANs again: the finished job's plan is spent, so
      // back to the WLANs step to build a new one from the current choices.
      // Re-runs reuse what already exists in R1 (found by name).
      return {
        ...state,
        executionStarted: false,
        finalJobId: null,
        planJobId: null,
        planResult: null,
        currentStep: 5,
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
