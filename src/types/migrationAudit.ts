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
  warnings: string[];
}
