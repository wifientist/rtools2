// Cross-Controller Migration Audit Types

export type AuditStatus = 'ok' | 'warning' | 'missing' | 'extra' | 'unsupported';

export interface FieldDiff {
  field: string;
  expected: any;
  actual: any;
  severity: 'info' | 'warning' | 'error';
}

export interface FieldComparisonItem {
  section: string;
  label: string;
  sz_value: any;
  r1_value: any;
  match: boolean;
  sz_only: boolean;
}

export interface NetworkAuditItem {
  sz_wlan_id: string;
  sz_wlan_name: string;
  sz_ssid: string;
  sz_auth_type: string;
  sz_vlan_id: number | null;
  sz_encryption_method: string | null;
  expected_r1_name: string;
  expected_r1_type: string;
  expected_r1_ssid: string;
  actual_r1_id: string | null;
  actual_r1_name: string | null;
  actual_r1_ssid: string | null;
  actual_r1_type: string | null;
  actual_r1_vlan: number | null;
  field_comparisons: FieldComparisonItem[];
  status: AuditStatus;
  diffs: FieldDiff[];
  notes: string;
}

export interface APGroupActivationAudit {
  sz_ap_group_name: string;
  sz_ssid_count: number;
  expected_ssids: string[];
  actual_ssids: string[];
  missing_ssids: string[];
  extra_ssids: string[];
  r1_ap_group_id: string | null;
  r1_ap_group_found: boolean;
}

export interface ResourceCoverage {
  resource_type: string;
  expected_count: number;
  actual_count: number;
  matched: string[];
  missing: string[];
}

export interface AuditSummary {
  total_sz_wlans: number;
  ok_count: number;
  warning_count: number;
  missing_count: number;
  extra_count: number;
  unsupported_count: number;
  ap_group_coverage: number;
  total_diffs: number;
  auth_external_count: number;
  auth_internal_count: number;
  auth_hybrid_count: number;
  auth_none_count: number;
  auth_error_count: number;
}

export interface MigrationAuditReport {
  sz_zone_name: string;
  r1_venue_name: string;
  sz_snapshot_job_id: string;
  r1_snapshot_job_id: string;
  audit_timestamp: string;
  summary: AuditSummary;
  networks: NetworkAuditItem[];
  extra_r1_networks: Array<{
    id: string;
    name: string;
    ssid: string;
    nwSubType: string;
    vlan: number | null;
  }>;
  ap_group_activations: APGroupActivationAudit[];
  resource_coverage: ResourceCoverage[];
  auth_paths: AuthPathAudit[];
  scorecard?: AlignmentCategory[];
  matching?: AuditMatching;
  venue_settings?: VenueSettingsAudit | null;
  broadcast?: BroadcastAudit | null;
  data_sources?: Record<string, { cached: boolean; age_seconds?: number | null; captured_at?: string; source?: string }>;
  warnings: string[];
}

// ── Auth paths (RADIUS / DPSK-backed WLANs) ──────────────────────
// Quantities only: no passphrase or shared secret is ever part of these.

export type AuthPath = 'external' | 'internal' | 'hybrid' | 'none';

export interface AuthIssue {
  severity: 'info' | 'warning' | 'error';
  message: string;
}

export interface RadiusServerCheck {
  role: 'authentication' | 'accounting';
  sz_service_name: string | null;
  sz_primary: string | null;
  sz_secondary: string | null;
  r1_profile_id: string | null;
  r1_profile_name: string | null;
  r1_primary: string | null;
  r1_secondary: string | null;
  primary_match: boolean | null;
  secondary_match: boolean | null;
  placeholder_secret: boolean | null;
}

export interface DpskPoolCheck {
  id: string;
  name: string | null;
  passphrase_count: number | null;
  identity_groups: Array<{ id: string; name: string | null; identity_count: number | null }>;
}

export interface AuthPathAudit {
  sz_wlan_id: string;
  sz_wlan_name: string;
  sz_ssid: string;
  sz_auth_type: string;
  sz_backend: 'external_radius' | 'external_dpsk' | 'sz_dpsk';
  r1_network_id: string | null;
  r1_network_name: string | null;
  r1_network_type: string | null;
  path: AuthPath;
  radius: RadiusServerCheck[];
  auth_proxy: boolean | null;
  accounting_proxy: boolean | null;
  pools: DpskPoolCheck[];
  passphrase_total: number | null;
  identity_total: number | null;
  status: AuditStatus;
  issues: AuthIssue[];
}

// ── Checkpoint 1: scorecard, pairings, zone/venue settings ───────

export type CategoryStatus = 'aligned' | 'partial' | 'misaligned' | 'pending' | 'manual' | 'not_checked';

export interface AlignmentCategory {
  key: string;
  label: string;
  sz_count: number | null;
  r1_count: number | null;
  aligned_count: number | null;
  status: CategoryStatus;
  details: string[];
  breakdown: Array<Record<string, any>>;
}

export type MatchMethod = 'name' | 'name_case' | 'ssid' | 'default' | 'serial' | 'mac' | 'none';

export interface WlanMatch {
  sz_wlan_id: string;
  sz_name: string;
  sz_ssid: string;
  r1_network_id: string | null;
  r1_name: string | null;
  r1_ssid: string | null;
  method: MatchMethod;
  activated_in_venue: boolean;
  notes: string[];
}

export interface ApGroupMatch {
  sz_ap_group_id: string;
  sz_name: string;
  sz_ap_count: number;
  r1_ap_group_id: string | null;
  r1_name: string | null;
  r1_ap_count: number;
  method: MatchMethod;
  notes: string[];
}

export interface ApMatch {
  name: string | null;
  serial: string | null;
  mac: string | null;
  model: string | null;
  sz_ap_group: string | null;
  r1_ap_group: string | null;
  expected_r1_ap_group: string | null;
  method: MatchMethod;
  presence: 'in_both' | 'sz_only' | 'r1_only';
  group_aligned: boolean | null;
  r1_status: string | null;
  sz_name: string | null;
  r1_name: string | null;
  name_alignment: 'exact' | 'case' | 'differs' | null;
}

export interface AuditMatching {
  wlans: WlanMatch[];
  r1_only_networks: Array<{ id: string; name: string; ssid: string; nwSubType: string }>;
  ap_groups: ApGroupMatch[];
  r1_only_ap_groups: Array<{ id: string; name: string; ap_count: number }>;
  aps: ApMatch[];
  counts: Record<string, number>;
}

export type SettingStatus = 'match' | 'differs' | 'sz_only' | 'unmappable' | 'unavailable' | 'review';

export interface SettingComparison {
  section: string;
  label: string;
  sz_value: any;
  r1_value: any;
  status: SettingStatus;
  note: string;
}

export interface VenueSettingsAudit {
  rows: SettingComparison[];
  errors: string[];
}

// ── Checkpoint 2: per-AP SSID broadcast ──────────────────────────

export interface ApBroadcast {
  name: string | null;
  serial: string | null;
  mac: string | null;
  sz_ap_group: string | null;
  r1_ap_group: string | null;
  presence: 'in_both' | 'sz_only' | 'r1_only';
  sz_ssids: string[];
  r1_ssids: string[];
  missing_in_r1: string[];
  extra_in_r1: string[];
  status: 'match' | 'differs' | 'pending' | 'r1_only' | 'unknown';
  sz_sources: string[];
}

export interface SsidCoverage {
  ssid: string;
  sz_ap_count: number;
  r1_ap_count: number;
  r1_reported_ap_count: number | null;
  missing_in_r1: number;
  extra_in_r1: number;
  sample_missing: string[];
  sample_extra: string[];
  status: 'match' | 'differs' | 'missing' | 'extra';
}

export interface BroadcastGroup {
  ssids: string[];
  sz_ap_count: number;
  sz_ap_groups: Record<string, number>;
  r1_ap_groups: Record<string, number>;
  r1_not_in_venue: number;
  r1_groups_matching_ssids: string[];
  status: 'match' | 'split' | 'differs' | 'pending';
  sample_aps: string[];
}

export interface BroadcastAudit {
  aps: ApBroadcast[];
  ssids: SsidCoverage[];
  groups: BroadcastGroup[];
  counts: Record<string, number>;
  notes: string[];
}
