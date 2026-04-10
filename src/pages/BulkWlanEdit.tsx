import { useState, useEffect, useMemo } from "react";
import { useAuth } from "@/context/AuthContext";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
} from "@tanstack/react-table";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// ============================================================================
// Types
// ============================================================================

interface Network {
  id: string;
  name: string;
  ssid: string;
  securityProtocol: string;
  vlan: number;
  type: string;
  venues: number;
  aps: number;
  clients: number;
}

interface Settings {
  // WLAN-level
  vlanId: number | null;
  enabled: boolean | null;
  managementFrameProtection: string | null;
  macAddressAuthentication: boolean | null;
  // Radio
  rfBandUsage: string | null;
  bssMinimumPhyRate: string | null;
  managementFrameMinimumPhyRate: string | null;
  phyTypeConstraint: string | null;
  // Client isolation
  clientIsolation: boolean | null;
  clientIsolationPacketsType: string | null;
  clientIsolationAutoVrrp: boolean | null;
  // Wi-Fi standards
  wifi6Enabled: boolean | null;
  wifi7Enabled: boolean | null;
  enableBandBalancing: boolean | null;
  // Client management
  maxClientsOnWlanPerRadio: number | null;
  clientInactivityTimeout: number | null;
  clientLoadBalancingEnable: boolean | null;
  enableTransientClientManagement: boolean | null;
  // Roaming & RSSI
  enableJoinRSSIThreshold: boolean | null;
  joinRSSIThreshold: number | null;
  rssiAssociationRejectionThreshold: number | null;
  enableFastRoaming: boolean | null;
  mobilityDomainId: number | null;
  joinExpireTime: number | null;
  joinWaitThreshold: number | null;
  joinWaitTime: number | null;
  // Rate limiting
  userDownlinkRateLimiting: number | null;
  userUplinkRateLimiting: number | null;
  totalDownlinkRateLimiting: number | null;
  totalUplinkRateLimiting: number | null;
  enableMulticastDownlinkRateLimiting: boolean | null;
  multicastDownlinkRateLimiting: number | null;
  enableMulticastUplinkRateLimiting: boolean | null;
  multicastUplinkRateLimiting: number | null;
  // QoS & Application
  qosMirroringEnabled: boolean | null;
  qosMirroringScope: string | null;
  applicationVisibilityEnabled: boolean | null;
  bssPriority: string | null;
  // Security
  enableGtkRekey: boolean | null;
  enableAntiSpoofing: boolean | null;
  enableApHostNameAdvertisement: boolean | null;
  hideSsid: boolean | null;
  // Access control
  accessControlEnable: boolean | null;
  applicationPolicyEnable: boolean | null;
  l2AclEnable: boolean | null;
  l3AclEnable: boolean | null;
  // DHCP
  dhcpOption82Enabled: boolean | null;
  dhcpOption82MacFormat: string | null;
  dhcpOption82SubOption1Enabled: boolean | null;
  dhcpOption82SubOption1Format: string | null;
  dhcpOption82SubOption2Enabled: boolean | null;
  dhcpOption82SubOption2Format: string | null;
  dhcpOption82SubOption150Enabled: boolean | null;
  dhcpOption82SubOption151Enabled: boolean | null;
  dhcpOption82SubOption151Format: string | null;
  dhcpOption82SubOption151Input: string | null;
  forceMobileDeviceDhcp: boolean | null;
  // Frame & broadcast limits
  enableArpRequestRateLimit: boolean | null;
  arpRequestRateLimit: number | null;
  enableDhcpRequestRateLimit: boolean | null;
  dhcpRequestRateLimit: number | null;
  broadcastProbeResponseDelay: number | null;
  directedThreshold: number | null;
  // Advanced features
  enableNeighborReport: boolean | null;
  enableOptimizedConnectivityExperience: boolean | null;
  enableAirtimeDecongestion: boolean | null;
  enableAdditionalRegulatoryDomains: boolean | null;
  proxyARP: boolean | null;
  multicastFilterEnabled: boolean | null;
  enableSyslog: boolean | null;
  multiLinkOperationEnabled: boolean | null;
  wifiCallingEnabled: boolean | null;
}

type Changes = {
  [K in keyof Settings]?: NonNullable<Settings[K]>;
};

interface NetworkSettings {
  name: string;
  ssid: string;
  settings: Settings;
}

interface FieldDiff {
  field: string;
  old_value: any;
  new_value: any;
}

interface NetworkDiff {
  network_id: string;
  name: string;
  ssid: string;
  changes: FieldDiff[];
}

// ============================================================================
// Default settings — empty for safer bulk ops (all "No Change")
// ============================================================================

const DEFAULT_CHANGES: Changes = {};

const RECOMMENDED_CHANGES: Changes = {
  clientIsolation: true,
  clientIsolationPacketsType: "UNICAST_MULTICAST",
  applicationVisibilityEnabled: true,
  bssMinimumPhyRate: "12",
  phyTypeConstraint: "OFDM",
  enableJoinRSSIThreshold: true,
  joinRSSIThreshold: -75,
  qosMirroringEnabled: true,
  qosMirroringScope: "ALL_CLIENTS",
  enableApHostNameAdvertisement: true,
};

// ============================================================================
// Setting display config
// ============================================================================

const FIELD_LABELS: Record<string, string> = {
  // Basic WLAN
  vlanId: "VLAN ID",
  enabled: "Network Enabled",
  // Security & Authentication
  managementFrameProtection: "Management Frame Protection (.11w)",
  macAddressAuthentication: "MAC Address Authentication",
  enableGtkRekey: "GTK Rekey",
  enableAntiSpoofing: "Anti-Spoofing",
  hideSsid: "Hide SSID",
  enableApHostNameAdvertisement: "AP Hostname in Beacon",
  // Access Control
  accessControlEnable: "Access Control",
  applicationPolicyEnable: "Application Policy",
  l2AclEnable: "Layer 2 ACL",
  l3AclEnable: "Layer 3 ACL",
  // Radio
  rfBandUsage: "RF Band",
  bssMinimumPhyRate: "BSS Min Rate",
  managementFrameMinimumPhyRate: "Mgmt Frame Min Rate",
  phyTypeConstraint: "OFDM Only",
  wifi6Enabled: "Wi-Fi 6 (802.11ax)",
  wifi7Enabled: "Wi-Fi 7 (802.11be)",
  enableBandBalancing: "Band Balancing",
  // Client Management
  clientIsolation: "Client Isolation",
  clientIsolationPacketsType: "Isolation Packets Type",
  clientIsolationAutoVrrp: "Isolation Auto VRRP",
  maxClientsOnWlanPerRadio: "Max Clients per Radio",
  clientInactivityTimeout: "Client Inactivity Timeout",
  clientLoadBalancingEnable: "Client Load Balancing",
  enableTransientClientManagement: "Transient Client Management",
  // Roaming & RSSI
  enableJoinRSSIThreshold: "Minimum Join RSSI Threshold",
  joinRSSIThreshold: "Join RSSI Threshold Value (dBm)",
  rssiAssociationRejectionThreshold: "Association Rejection RSSI (dBm)",
  enableFastRoaming: "Fast Roaming (802.11r)",
  mobilityDomainId: "Mobility Domain ID",
  joinExpireTime: "Client Join Expire Time",
  joinWaitThreshold: "Client Join Wait Threshold",
  joinWaitTime: "Client Join Wait Time",
  // Rate Limiting
  userDownlinkRateLimiting: "User Downlink Limit",
  userUplinkRateLimiting: "User Uplink Limit",
  totalDownlinkRateLimiting: "Total Downlink Limit",
  totalUplinkRateLimiting: "Total Uplink Limit",
  enableMulticastDownlinkRateLimiting: "Multicast Downlink Limiting",
  multicastDownlinkRateLimiting: "Multicast Downlink Limit",
  enableMulticastUplinkRateLimiting: "Multicast Uplink Limiting",
  multicastUplinkRateLimiting: "Multicast Uplink Limit",
  // QoS & Application
  qosMirroringEnabled: "QoS Mirroring",
  qosMirroringScope: "QoS Mirroring Scope",
  applicationVisibilityEnabled: "Application Visibility",
  bssPriority: "BSS Priority",
  // DHCP
  forceMobileDeviceDhcp: "Force Mobile Device DHCP",
  dhcpOption82Enabled: "DHCP Option 82",
  dhcpOption82MacFormat: "Option 82 MAC Format",
  dhcpOption82SubOption1Enabled: "Sub-Option 1",
  dhcpOption82SubOption1Format: "Sub-Option 1 Format",
  dhcpOption82SubOption2Enabled: "Sub-Option 2",
  dhcpOption82SubOption2Format: "Sub-Option 2 Format",
  dhcpOption82SubOption150Enabled: "Sub-Option 150",
  dhcpOption82SubOption151Enabled: "Sub-Option 151",
  dhcpOption82SubOption151Format: "Sub-Option 151 Format",
  dhcpOption82SubOption151Input: "Sub-Option 151 Input",
  // Frame & Broadcast
  enableArpRequestRateLimit: "ARP Request Rate Limit",
  arpRequestRateLimit: "ARP Rate Limit Value",
  enableDhcpRequestRateLimit: "DHCP Request Rate Limit",
  dhcpRequestRateLimit: "DHCP Rate Limit Value",
  broadcastProbeResponseDelay: "Broadcast Probe Response Delay",
  directedThreshold: "Directed Threshold",
  // Advanced
  enableNeighborReport: "Neighbor Report (802.11k)",
  enableOptimizedConnectivityExperience: "Optimized Connectivity (OCE)",
  enableAirtimeDecongestion: "Airtime Decongestion",
  enableAdditionalRegulatoryDomains: "Additional Regulatory Domains",
  proxyARP: "Proxy ARP",
  multicastFilterEnabled: "Multicast Filter",
  enableSyslog: "Syslog",
  multiLinkOperationEnabled: "Multi-Link Operation (MLO)",
  wifiCallingEnabled: "Wi-Fi Calling",
};

// Dropdown option sets
const BSS_MIN_RATE_OPTIONS = [
  { value: "default", label: "Default" },
  { value: "1", label: "1 Mbps" },
  { value: "2", label: "2 Mbps" },
  { value: "5.5", label: "5.5 Mbps" },
  { value: "12", label: "12 Mbps" },
  { value: "24", label: "24 Mbps" },
];

const MGMT_FRAME_MIN_RATE_OPTIONS = [
  { value: "1", label: "1 Mbps" },
  { value: "2", label: "2 Mbps" },
  { value: "5.5", label: "5.5 Mbps" },
  { value: "6", label: "6 Mbps" },
  { value: "9", label: "9 Mbps" },
  { value: "11", label: "11 Mbps" },
  { value: "12", label: "12 Mbps" },
  { value: "18", label: "18 Mbps" },
  { value: "24", label: "24 Mbps" },
];

const MFP_OPTIONS = [
  { value: "Disabled", label: "Disabled" },
  { value: "Optional", label: "Optional" },
  { value: "Required", label: "Required" },
];

const RF_BAND_OPTIONS = [
  { value: "2.4GHZ", label: "2.4 GHz" },
  { value: "5.0GHZ", label: "5.0 GHz" },
  { value: "BOTH", label: "Both" },
];

const BSS_PRIORITY_OPTIONS = [
  { value: "HIGH", label: "High" },
  { value: "LOW", label: "Low" },
];

const ISOLATION_PACKETS_OPTIONS = [
  { value: "UNICAST", label: "Unicast" },
  { value: "MULTICAST", label: "Multicast" },
  { value: "UNICAST_MULTICAST", label: "Unicast + Multicast" },
];

const QOS_SCOPE_OPTIONS = [
  { value: "MSCS_REQUESTS_ONLY", label: "MSCS Requests Only" },
  { value: "ALL_CLIENTS", label: "All Clients" },
];

const DHCP82_MAC_FORMAT_OPTIONS = [
  { value: "COLON", label: "Colon (AA:BB:CC)" },
  { value: "HYPHEN", label: "Hyphen (AA-BB-CC)" },
  { value: "NODELIMITER", label: "No Delimiter (AABBCC)" },
];

const DHCP82_SUBOPT1_FORMAT_OPTIONS = [
  { value: "SUBOPT1_AP_INFO_LOCATION", label: "AP Info Location" },
  { value: "SUBOPT1_AP_INFO", label: "AP Info" },
  { value: "SUBOPT1_AP_MAC_ESSID_PRIVACYTYPE", label: "AP MAC + ESSID + Privacy" },
  { value: "SUBOPT1_AP_MAC_hex", label: "AP MAC (hex)" },
  { value: "SUBOPT1_AP_MAC_hex_ESSID", label: "AP MAC (hex) + ESSID" },
  { value: "SUBOPT1_ESSID", label: "ESSID" },
  { value: "SUBOPT1_AP_MAC", label: "AP MAC" },
  { value: "SUBOPT1_AP_MAC_ESSID", label: "AP MAC + ESSID" },
  { value: "SUBOPT1_AP_Name_ESSID", label: "AP Name + ESSID" },
  { value: "SUBOPT1_CUSTOMIZED", label: "Customized" },
];

const DHCP82_SUBOPT2_FORMAT_OPTIONS = [
  { value: "SUBOPT2_CLIENT_MAC", label: "Client MAC" },
  { value: "SUBOPT2_CLIENT_MAC_hex", label: "Client MAC (hex)" },
  { value: "SUBOPT2_CLIENT_MAC_hex_ESSID", label: "Client MAC (hex) + ESSID" },
  { value: "SUBOPT2_AP_MAC", label: "AP MAC" },
  { value: "SUBOPT2_AP_MAC_hex", label: "AP MAC (hex)" },
  { value: "SUBOPT2_AP_MAC_hex_ESSID", label: "AP MAC (hex) + ESSID" },
  { value: "SUBOPT2_AP_MAC_ESSID", label: "AP MAC + ESSID" },
  { value: "SUBOPT2_AP_Name", label: "AP Name" },
];

const DHCP82_SUBOPT151_FORMAT_OPTIONS = [
  { value: "SUBOPT151_AREA_NAME", label: "Area Name" },
  { value: "SUBOPT151_ESSID", label: "ESSID" },
];

// Map of enum-type fields to their option sets for formatValue
const ENUM_OPTIONS: Record<string, { value: string; label: string }[]> = {
  bssMinimumPhyRate: BSS_MIN_RATE_OPTIONS,
  managementFrameMinimumPhyRate: MGMT_FRAME_MIN_RATE_OPTIONS,
  managementFrameProtection: MFP_OPTIONS,
  rfBandUsage: RF_BAND_OPTIONS,
  bssPriority: BSS_PRIORITY_OPTIONS,
  clientIsolationPacketsType: ISOLATION_PACKETS_OPTIONS,
  qosMirroringScope: QOS_SCOPE_OPTIONS,
  dhcpOption82MacFormat: DHCP82_MAC_FORMAT_OPTIONS,
  dhcpOption82SubOption1Format: DHCP82_SUBOPT1_FORMAT_OPTIONS,
  dhcpOption82SubOption2Format: DHCP82_SUBOPT2_FORMAT_OPTIONS,
  dhcpOption82SubOption151Format: DHCP82_SUBOPT151_FORMAT_OPTIONS,
};

// Fields that display as "Enabled"/"Disabled" mapped from string values
const BOOL_STRING_FIELDS: Record<string, { trueVal: string }> = {
  phyTypeConstraint: { trueVal: "OFDM" },
};

// Number fields with units
const NUMBER_UNITS: Record<string, string> = {
  joinRSSIThreshold: "dBm",
  rssiAssociationRejectionThreshold: "dBm",
  clientInactivityTimeout: "sec",
  joinExpireTime: "sec",
  joinWaitTime: "sec",
  userDownlinkRateLimiting: "Mbps",
  userUplinkRateLimiting: "Mbps",
  totalDownlinkRateLimiting: "Mbps",
  totalUplinkRateLimiting: "Mbps",
  multicastDownlinkRateLimiting: "Mbps",
  multicastUplinkRateLimiting: "Mbps",
  broadcastProbeResponseDelay: "ms",
  arpRequestRateLimit: "pps",
  dhcpRequestRateLimit: "pps",
};

function formatValue(field: string, value: any): string {
  if (value === null || value === undefined) return "N/A";
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  // Bool-like string fields
  if (BOOL_STRING_FIELDS[field]) {
    return value === BOOL_STRING_FIELDS[field].trueVal ? "Enabled" : "Disabled";
  }
  // Enum fields
  if (ENUM_OPTIONS[field]) {
    const opt = ENUM_OPTIONS[field].find((o) => o.value === value);
    return opt ? opt.label : String(value);
  }
  // Number with unit
  if (NUMBER_UNITS[field]) return `${value} ${NUMBER_UNITS[field]}`;
  return String(value);
}

// ============================================================================
// Component
// ============================================================================

const columnHelper = createColumnHelper<Network>();

export default function BulkWlanEdit() {
  const { activeControllerId, activeControllerSubtype, controllers } = useAuth();
  const activeController = controllers.find((c: any) => c.id === activeControllerId);
  const effectiveTenantId =
    activeControllerSubtype === "MSP" ? null : activeController?.r1_tenant_id || null;

  // Network list
  const [networks, setNetworks] = useState<Network[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Selection
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [globalFilter, setGlobalFilter] = useState("");

  // Settings
  const [fetchedSettings, setFetchedSettings] = useState<Record<string, NetworkSettings>>({});
  const [settingsLoading, setSettingsLoading] = useState(false);

  // Changes — all "No Change" by default for safer bulk operations
  const [changes, setChanges] = useState<Changes>({});

  // Preview
  const [previewData, setPreviewData] = useState<{
    diffs: NetworkDiff[];
    change_count: number;
    unchanged_count: number;
    already_set_count: number;
    already_set_fields: string[];
    errors: string[];
  } | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // Job
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastResult, setLastResult] = useState<JobResult | null>(null);
  const [showDangerConfirm, setShowDangerConfirm] = useState(false);

  // ---- Derived ----
  const selectedIds = useMemo(
    () => Object.entries(rowSelection).filter(([, v]) => v).map(([k]) => networks[parseInt(k)]?.id).filter(Boolean),
    [rowSelection, networks]
  );

  const hasChanges = useMemo(
    () => Object.values(changes).some((v) => v !== undefined),
    [changes]
  );

  // ---- Fetch networks ----
  useEffect(() => {
    if (!activeControllerId) return;
    setLoading(true);
    setError("");
    setNetworks([]);
    setRowSelection({});
    setFetchedSettings({});
    setChanges({ ...DEFAULT_CHANGES });

    const url = effectiveTenantId
      ? `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks?tenant_id=${effectiveTenantId}`
      : `${API_BASE_URL}/bulk-wlan/${activeControllerId}/networks`;

    apiFetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setNetworks(data.networks || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [activeControllerId, effectiveTenantId]);

  // ---- Fetch settings for selected networks (batched) ----
  const FETCH_BATCH_SIZE = 50;
  const [fetchProgress, setFetchProgress] = useState({ fetched: 0, total: 0 });

  const fetchSettings = async () => {
    if (!selectedIds.length || !activeControllerId) return;
    setSettingsLoading(true);
    setFetchProgress({ fetched: 0, total: selectedIds.length });

    const allNetworks: Record<string, NetworkSettings> = {};
    let fetched = 0;

    try {
      // Chunk selectedIds into batches of FETCH_BATCH_SIZE
      for (let i = 0; i < selectedIds.length; i += FETCH_BATCH_SIZE) {
        const batch = selectedIds.slice(i, i + FETCH_BATCH_SIZE);

        const res = await apiFetch(`${API_BASE_URL}/bulk-wlan/fetch-settings`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            controller_id: activeControllerId,
            tenant_id: effectiveTenantId,
            network_ids: batch,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const detail = Array.isArray(err.detail)
            ? err.detail.map((d: any) => `${d.loc?.join(".")}: ${d.msg}`).join("; ")
            : err.detail || `HTTP ${res.status}`;
          throw new Error(detail);
        }
        const data = await res.json();
        Object.assign(allNetworks, data.networks || {});
        fetched += batch.length;
        setFetchProgress({ fetched, total: selectedIds.length });
      }

      setFetchedSettings(allNetworks);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSettingsLoading(false);
    }
  };

  // ---- Current value summary ----
  const settingsSummary = useMemo(() => {
    const entries = Object.values(fetchedSettings);
    if (!entries.length) return {};

    const summary: Record<string, Record<string, number>> = {};
    for (const entry of entries) {
      for (const [field, value] of Object.entries(entry.settings)) {
        if (!summary[field]) summary[field] = {};
        const key = formatValue(field, value);
        summary[field][key] = (summary[field][key] || 0) + 1;
      }
    }
    return summary;
  }, [fetchedSettings]);

  // ---- Preview (computed client-side from already-fetched settings) ----
  const handlePreview = () => {
    if (!selectedIds.length || !hasChanges) return;

    const diffs: NetworkDiff[] = [];
    let unchangedCount = 0;
    let alreadySetCount = 0;
    // Track fields where ALL selected networks already have the desired value
    const alreadySetFields = new Set<string>();
    const changedFields = new Set<string>();

    const activeChanges = Object.entries(changes).filter(([, v]) => v !== undefined);

    for (const nid of selectedIds) {
      const entry = fetchedSettings[nid];
      if (!entry) continue;

      const fieldDiffs: FieldDiff[] = [];
      for (const [field, newValue] of activeChanges) {
        const oldValue = entry.settings[field as keyof Settings];
        if (oldValue !== newValue) {
          fieldDiffs.push({ field, old_value: oldValue, new_value: newValue });
          changedFields.add(field);
        }
      }

      if (fieldDiffs.length > 0) {
        diffs.push({ network_id: nid, name: entry.name, ssid: entry.ssid, changes: fieldDiffs });
      } else {
        // This network already has ALL the desired values
        alreadySetCount++;
        unchangedCount++;
      }
    }

    // Fields that were never flagged as changed across any network
    for (const [field] of activeChanges) {
      if (!changedFields.has(field)) {
        alreadySetFields.add(field);
      }
    }

    setPreviewData({
      diffs,
      change_count: diffs.length,
      unchanged_count: unchangedCount,
      already_set_count: alreadySetCount,
      already_set_fields: Array.from(alreadySetFields),
      errors: [],
    });
    setShowPreview(true);
  };

  // ---- Apply ----
  const hasDangerousChanges = changes.vlanId !== undefined || changes.enabled !== undefined;

  const handleApplyClick = () => {
    if (hasDangerousChanges) {
      setShowDangerConfirm(true);
    } else {
      handleApply();
    }
  };

  const handleApply = async () => {
    setShowDangerConfirm(false);
    if (!selectedIds.length || !hasChanges || !activeControllerId) return;
    try {
      const res = await apiFetch(`${API_BASE_URL}/bulk-wlan/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          tenant_id: effectiveTenantId,
          network_ids: selectedIds,
          changes,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail = Array.isArray(err.detail)
          ? err.detail.map((d: any) => `${d.loc?.join(".")}: ${d.msg}`).join("; ")
          : err.detail || `HTTP ${res.status}`;
        throw new Error(detail);
      }
      const data = await res.json();
      setCurrentJobId(data.job_id);
      setShowJobModal(true);
      setShowPreview(false);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleJobComplete = (result: JobResult) => {
    // Don't close the modal — let the user see the completed state in JobMonitorView.
    // State reset happens when the user manually closes the modal.
    setLastResult(result);
  };

  const handleJobModalClose = () => {
    setShowJobModal(false);
    setChanges({ ...DEFAULT_CHANGES });
    setFetchedSettings({});
    setRowSelection({});
  };

  // ---- Table ----
  const columns = useMemo(
    () => [
      columnHelper.display({
        id: "select",
        header: ({ table }) => (
          <input
            type="checkbox"
            checked={table.getIsAllPageRowsSelected()}
            onChange={table.getToggleAllPageRowsSelectedHandler()}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
          />
        ),
        size: 40,
      }),
      columnHelper.accessor("ssid", { header: "SSID", size: 200 }),
      columnHelper.accessor("name", { header: "Network Name", size: 220 }),
      columnHelper.accessor("securityProtocol", { header: "Security", size: 120 }),
      columnHelper.accessor("type", { header: "Type", size: 80 }),
      columnHelper.accessor("vlan", { header: "VLAN", size: 60 }),
      columnHelper.accessor("venues", { header: "Venues", size: 60 }),
      columnHelper.accessor("aps", { header: "APs", size: 60 }),
      columnHelper.accessor("clients", { header: "Clients", size: 60 }),
    ],
    []
  );

  const table = useReactTable({
    data: networks,
    columns,
    state: { rowSelection, globalFilter },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // Section collapse state
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const toggleSection = (title: string) =>
    setCollapsedSections((s) => ({ ...s, [title]: !s[title] }));

  // ---- Render helpers ----

  // Danger banner for VLAN / Enabled changes in preview modal
  const DangerBanner = ({ className }: { className?: string }) => (
    <div className={`bg-red-50 border-2 border-red-300 rounded-lg p-4 ${className ?? ""}`}>
      <div className="flex items-start gap-2">
        <span className="text-red-600 text-lg leading-none">&#9888;</span>
        <div>
          <div className="text-sm font-semibold text-red-800">
            Critical Change{changes.vlanId !== undefined && changes.enabled !== undefined ? "s" : ""} Detected
          </div>
          <div className="text-xs text-red-700 mt-1 space-y-1">
            {changes.vlanId !== undefined && (
              <p><strong>VLAN ID change</strong> — Changing the VLAN can immediately disconnect all clients. Ensure the target VLAN is configured on your switches.</p>
            )}
            {changes.enabled !== undefined && (
              <p><strong>Enabled state change</strong> — {changes.enabled === false
                ? "Disabling networks will immediately disconnect all connected clients."
                : "Enabling networks will start broadcasting SSIDs on all affected APs."}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  // Reusable collapsible section wrapper
  const Section = ({ title, warning, children }: { title: string; warning?: string; children: React.ReactNode }) => {
    const isCollapsed = collapsedSections[title] ?? false;
    return (
      <div className="mt-10 first:mt-0">
        <button
          type="button"
          onClick={() => toggleSection(title)}
          className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 border border-gray-200 rounded-md group cursor-pointer hover:bg-gray-100 transition-colors mb-3"
        >
          <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
            {title}
          </h3>
          <span className="text-gray-400 group-hover:text-gray-600 text-xs transition-colors">
            {isCollapsed ? "+" : "\u2212"}
          </span>
        </button>
        {!isCollapsed && (
          <>
            {warning && (
              <div className="bg-amber-50 border border-amber-200 rounded p-2 mb-2 text-xs text-amber-800">
                {warning}
              </div>
            )}
            <div className="space-y-0">{children}</div>
          </>
        )}
      </div>
    );
  };

  // Boolean toggle (No Change / Enable / Disable)
  const TriStateToggle = ({
    label,
    value,
    onChange,
    summaryKey,
    indent,
  }: {
    label: string;
    value: boolean | undefined;
    onChange: (v: boolean | undefined) => void;
    summaryKey: string;
    indent?: boolean;
  }) => (
    <div className={`flex items-center justify-between py-2 border-b border-gray-100 ${indent ? "pl-6" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <div className="flex gap-1 flex-shrink-0">
        {(["no_change", "enable", "disable"] as const).map((opt) => {
          const isActive =
            opt === "no_change"
              ? value === undefined
              : opt === "enable"
              ? value === true
              : value === false;
          return (
            <button
              key={opt}
              onClick={() =>
                onChange(opt === "no_change" ? undefined : opt === "enable" ? true : false)
              }
              className={`px-3 py-1 text-xs rounded ${
                isActive
                  ? opt === "no_change"
                    ? "bg-gray-200 text-gray-700"
                    : opt === "enable"
                    ? "bg-green-600 text-white"
                    : "bg-red-600 text-white"
                  : "bg-gray-100 text-gray-400 hover:bg-gray-200"
              }`}
            >
              {opt === "no_change" ? "No Change" : opt === "enable" ? "Enable" : "Disable"}
            </button>
          );
        })}
      </div>
    </div>
  );

  // Enum dropdown (No Change + options)
  const EnumDropdown = ({
    label,
    value,
    onChange,
    options,
    summaryKey,
    indent,
  }: {
    label: string;
    value: string | undefined;
    onChange: (v: string | undefined) => void;
    options: { value: string; label: string }[];
    summaryKey: string;
    indent?: boolean;
  }) => (
    <div className={`flex items-center justify-between py-2 border-b border-gray-100 ${indent ? "pl-6" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <select
        value={value ?? "__no_change__"}
        onChange={(e) => onChange(e.target.value === "__no_change__" ? undefined : e.target.value)}
        className="px-3 py-1 text-sm border rounded flex-shrink-0"
      >
        <option value="__no_change__">No Change</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );

  // Number input (with "No Change" placeholder, min/max, optional unit)
  const NumberInput = ({
    label,
    value,
    onChange,
    min,
    max,
    unit,
    summaryKey,
    indent,
  }: {
    label: string;
    value: number | undefined;
    onChange: (v: number | undefined) => void;
    min: number;
    max: number;
    unit?: string;
    summaryKey: string;
    indent?: boolean;
  }) => (
    <div className={`flex items-center justify-between py-2 border-b border-gray-100 ${indent ? "pl-6" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        <input
          type="number"
          min={min}
          max={max}
          placeholder="No Change"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value ? parseInt(e.target.value) : undefined)}
          className="w-28 px-2 py-1 text-sm border rounded"
        />
        {unit && <span className="text-xs text-gray-500">{unit}</span>}
      </div>
    </div>
  );

  // Text input (with "No Change" placeholder)
  const TextInput = ({
    label,
    value,
    onChange,
    summaryKey,
    indent,
  }: {
    label: string;
    value: string | undefined;
    onChange: (v: string | undefined) => void;
    summaryKey: string;
    indent?: boolean;
  }) => (
    <div className={`flex items-center justify-between py-2 border-b border-gray-100 ${indent ? "pl-6" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <input
        type="text"
        placeholder="No Change"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="w-48 px-2 py-1 text-sm border rounded flex-shrink-0"
      />
    </div>
  );

  // RSSI slider with toggle
  const RssiSlider = ({
    label,
    value,
    onChange,
    summaryKey,
    indent,
  }: {
    label: string;
    value: number | undefined;
    onChange: (v: number | undefined) => void;
    summaryKey: string;
    indent?: boolean;
  }) => (
    <div className={`flex items-center justify-between py-2 border-b border-gray-100 ${indent ? "pl-6" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        {settingsSummary[summaryKey] && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">
            Current:{" "}
            {Object.entries(settingsSummary[summaryKey])
              .map(([k, c]) => `${c}x ${k}`)
              .join(", ")}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          onClick={() => onChange(undefined)}
          className={`px-2 py-1 text-xs rounded ${
            value === undefined ? "bg-gray-200 text-gray-700" : "bg-gray-100 text-gray-400 hover:bg-gray-200"
          }`}
        >
          No Change
        </button>
        <input
          type="range"
          min={-90}
          max={-60}
          value={value ?? -75}
          onChange={(e) => onChange(parseInt(e.target.value))}
          className="w-24"
        />
        <span className="text-xs text-gray-600 w-14 text-right">
          {value !== undefined ? `${value} dBm` : "—"}
        </span>
      </div>
    </div>
  );

  // Helper to update changes and clear dependent fields
  const setField = (field: keyof Changes, value: any) => {
    setChanges((c) => ({ ...c, [field]: value }));
  };

  // Helper for parent toggles that clear children when moved away from true
  const setParentToggle = (
    field: keyof Changes,
    value: boolean | undefined,
    children: (keyof Changes)[],
  ) => {
    setChanges((c) => {
      const next = { ...c, [field]: value };
      if (value !== true) {
        for (const child of children) {
          next[child] = undefined as any;
        }
      }
      return next;
    });
  };

  if (!activeControllerId) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <h1 className="text-2xl font-bold mb-2">Bulk WLAN Edit</h1>
        <p className="text-gray-500">Select a RuckusONE controller to get started.</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Bulk WLAN Edit</h1>
      <p className="text-gray-500 mb-6 text-sm">
        Select WiFi networks and modify advanced settings in bulk.
      </p>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-red-800 text-sm">
          {error}
          <button className="ml-2 text-red-600 underline text-xs" onClick={() => setError("")}>
            dismiss
          </button>
        </div>
      )}

      {/* ============= Last Job Result Banner ============= */}
      {lastResult && !showJobModal && (
        <div
          className={`rounded-lg p-4 mb-4 text-sm ${
            lastResult.status === "COMPLETED"
              ? "bg-green-50 border border-green-200 text-green-800"
              : lastResult.status === "PARTIAL"
              ? "bg-yellow-50 border border-yellow-200 text-yellow-800"
              : lastResult.status === "FAILED"
              ? "bg-red-50 border border-red-200 text-red-800"
              : "bg-yellow-50 border border-yellow-200 text-yellow-800"
          }`}
        >
          <div className="flex items-center justify-between">
            <div>
              <span className="font-medium">
                {lastResult.status === "COMPLETED"
                  ? "Bulk WLAN update completed successfully."
                  : lastResult.status === "PARTIAL"
                  ? "Bulk WLAN update completed with errors."
                  : lastResult.status === "FAILED"
                  ? "Bulk WLAN update failed."
                  : "Bulk WLAN update cancelled."}
              </span>
              {lastResult.progress?.total_tasks > 0 && (
                <span className="ml-2">
                  {lastResult.progress.completed} of {lastResult.progress.total_tasks} networks processed
                  {lastResult.progress.failed > 0 && `, ${lastResult.progress.failed} failed`}
                </span>
              )}
            </div>
            <button
              onClick={() => setLastResult(null)}
              className="text-xs underline opacity-70 hover:opacity-100"
            >
              dismiss
            </button>
          </div>
          {lastResult.errors && lastResult.errors.length > 0 && (
            <div className="mt-2 text-xs space-y-1 border-t border-current/20 pt-2">
              {lastResult.errors.map((err, i) => (
                <div key={i}>{err}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ============= STEP 1: Network Selection ============= */}
      <div className="bg-white rounded-lg shadow p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">1. Select Networks</h2>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-500">
              {selectedIds.length} of {networks.length} selected
            </span>
            {selectedIds.length > 0 && (
              <button
                onClick={() => setRowSelection({})}
                className="text-xs text-gray-500 hover:text-gray-700 underline"
              >
                Deselect All
              </button>
            )}
          </div>
        </div>

        <input
          type="text"
          placeholder="Search networks by name, SSID, type..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg mb-3 text-sm"
        />

        {loading ? (
          <div className="text-center py-8 text-gray-400">Loading networks...</div>
        ) : networks.length === 0 ? (
          <div className="text-center py-8 text-gray-400">No WiFi networks found.</div>
        ) : (
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto border rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                        style={{ width: header.getSize() }}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-gray-100">
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={`hover:bg-gray-50 cursor-pointer ${
                      row.getIsSelected() ? "bg-blue-50" : ""
                    }`}
                    onClick={row.getToggleSelectedHandler()}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-2 text-gray-700">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {selectedIds.length > 0 && (
          <div className="mt-3 flex justify-end">
            <button
              onClick={fetchSettings}
              disabled={settingsLoading}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:bg-gray-400"
            >
              {settingsLoading
                ? `Fetching Settings... ${fetchProgress.fetched}/${fetchProgress.total}`
                : `Fetch Settings for ${selectedIds.length} Network${selectedIds.length > 1 ? "s" : ""}`}
            </button>
          </div>
        )}
      </div>

      {/* ============= STEP 2: Settings Editor ============= */}
      {Object.keys(fetchedSettings).length > 0 && (
        <div className="bg-white rounded-lg shadow p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">2. Edit Settings</h2>
            <button
              onClick={() => setChanges((c) => ({ ...c, ...RECOMMENDED_CHANGES }))}
              className="px-3 py-1.5 text-xs bg-amber-50 border border-amber-300 text-amber-800 rounded-lg hover:bg-amber-100 transition-colors"
            >
              Ruckus Recommends
            </button>
          </div>
          <p className="text-xs text-gray-400 mb-4">
            Only settings you change will be applied. "No Change" leaves the current value untouched.
          </p>

          <div className="space-y-0">

            {/* ========== Section 1: Basic WLAN ========== */}
            <Section
              title="Basic WLAN"
              warning={
                changes.vlanId !== undefined || changes.enabled !== undefined
                  ? "Changing VLAN ID or enabled state affects live traffic. Proceed with caution."
                  : undefined
              }
            >
              <TriStateToggle
                label="Network Enabled"
                value={changes.enabled}
                onChange={(v) => setField("enabled", v)}
                summaryKey="enabled"
              />
              <NumberInput
                label="VLAN ID"
                value={changes.vlanId}
                onChange={(v) => setField("vlanId", v)}
                min={1} max={4094}
                summaryKey="vlanId"
              />
            </Section>

            {/* ========== Section 2: Security & Authentication ========== */}
            <Section title="Security & Authentication">
              <EnumDropdown
                label="Management Frame Protection (.11w)"
                value={changes.managementFrameProtection}
                onChange={(v) => setField("managementFrameProtection", v)}
                options={MFP_OPTIONS}
                summaryKey="managementFrameProtection"
              />
              <TriStateToggle
                label="MAC Address Authentication"
                value={changes.macAddressAuthentication}
                onChange={(v) => setField("macAddressAuthentication", v)}
                summaryKey="macAddressAuthentication"
              />
              <TriStateToggle
                label="GTK Rekey"
                value={changes.enableGtkRekey}
                onChange={(v) => setField("enableGtkRekey", v)}
                summaryKey="enableGtkRekey"
              />
              <TriStateToggle
                label="Anti-Spoofing"
                value={changes.enableAntiSpoofing}
                onChange={(v) => setField("enableAntiSpoofing", v)}
                summaryKey="enableAntiSpoofing"
              />
              <TriStateToggle
                label="Hide SSID"
                value={changes.hideSsid}
                onChange={(v) => setField("hideSsid", v)}
                summaryKey="hideSsid"
              />
              <TriStateToggle
                label="AP Hostname in Beacon"
                value={changes.enableApHostNameAdvertisement}
                onChange={(v) => setField("enableApHostNameAdvertisement", v)}
                summaryKey="enableApHostNameAdvertisement"
              />
            </Section>

            {/* ========== Section 3: Access Control ========== */}
            <Section title="Access Control">
              <TriStateToggle
                label="Access Control Policy"
                value={changes.accessControlEnable}
                onChange={(v) => setField("accessControlEnable", v)}
                summaryKey="accessControlEnable"
              />
              <TriStateToggle
                label="Application Policy"
                value={changes.applicationPolicyEnable}
                onChange={(v) => setField("applicationPolicyEnable", v)}
                summaryKey="applicationPolicyEnable"
              />
              <TriStateToggle
                label="Layer 2 ACL"
                value={changes.l2AclEnable}
                onChange={(v) => setField("l2AclEnable", v)}
                summaryKey="l2AclEnable"
              />
              <TriStateToggle
                label="Layer 3 ACL"
                value={changes.l3AclEnable}
                onChange={(v) => setField("l3AclEnable", v)}
                summaryKey="l3AclEnable"
              />
            </Section>

            {/* ========== Section 4: Radio Settings ========== */}
            <Section title="Radio Settings">
              <EnumDropdown
                label="RF Band"
                value={changes.rfBandUsage}
                onChange={(v) => setField("rfBandUsage", v)}
                options={RF_BAND_OPTIONS}
                summaryKey="rfBandUsage"
              />
              <EnumDropdown
                label="BSS Min Rate"
                value={changes.bssMinimumPhyRate}
                onChange={(v) => setField("bssMinimumPhyRate", v)}
                options={BSS_MIN_RATE_OPTIONS}
                summaryKey="bssMinimumPhyRate"
              />
              <EnumDropdown
                label="Mgmt Frame Min Rate"
                value={changes.managementFrameMinimumPhyRate}
                onChange={(v) => setField("managementFrameMinimumPhyRate", v)}
                options={MGMT_FRAME_MIN_RATE_OPTIONS}
                summaryKey="managementFrameMinimumPhyRate"
              />
              <TriStateToggle
                label="OFDM Only"
                value={
                  changes.phyTypeConstraint === undefined
                    ? undefined
                    : changes.phyTypeConstraint === "OFDM"
                }
                onChange={(v) =>
                  setField("phyTypeConstraint", v === undefined ? undefined : v ? "OFDM" : "NONE")
                }
                summaryKey="phyTypeConstraint"
              />
              <TriStateToggle
                label="Wi-Fi 6 (802.11ax)"
                value={changes.wifi6Enabled}
                onChange={(v) => setField("wifi6Enabled", v)}
                summaryKey="wifi6Enabled"
              />
              <TriStateToggle
                label="Wi-Fi 7 (802.11be)"
                value={changes.wifi7Enabled}
                onChange={(v) => setField("wifi7Enabled", v)}
                summaryKey="wifi7Enabled"
              />
              <TriStateToggle
                label="Band Balancing"
                value={changes.enableBandBalancing}
                onChange={(v) => setField("enableBandBalancing", v)}
                summaryKey="enableBandBalancing"
              />
            </Section>

            {/* ========== Section 5: Client Management ========== */}
            <Section title="Client Management">
              <TriStateToggle
                label="Client Isolation"
                value={changes.clientIsolation}
                onChange={(v) =>
                  setParentToggle("clientIsolation", v, [
                    "clientIsolationPacketsType",
                    "clientIsolationAutoVrrp",
                  ])
                }
                summaryKey="clientIsolation"
              />
              {changes.clientIsolation === true && (
                <>
                  <EnumDropdown
                    label="Isolation Packets Type"
                    value={changes.clientIsolationPacketsType}
                    onChange={(v) => setField("clientIsolationPacketsType", v)}
                    options={ISOLATION_PACKETS_OPTIONS}
                    summaryKey="clientIsolationPacketsType"
                    indent
                  />
                  <TriStateToggle
                    label="Isolation Auto VRRP"
                    value={changes.clientIsolationAutoVrrp}
                    onChange={(v) => setField("clientIsolationAutoVrrp", v)}
                    summaryKey="clientIsolationAutoVrrp"
                    indent
                  />
                </>
              )}
              <NumberInput
                label="Max Clients per Radio"
                value={changes.maxClientsOnWlanPerRadio}
                onChange={(v) => setField("maxClientsOnWlanPerRadio", v)}
                min={1} max={512}
                summaryKey="maxClientsOnWlanPerRadio"
              />
              <NumberInput
                label="Client Inactivity Timeout"
                value={changes.clientInactivityTimeout}
                onChange={(v) => setField("clientInactivityTimeout", v)}
                min={60} max={86400} unit="sec"
                summaryKey="clientInactivityTimeout"
              />
              <TriStateToggle
                label="Client Load Balancing"
                value={changes.clientLoadBalancingEnable}
                onChange={(v) => setField("clientLoadBalancingEnable", v)}
                summaryKey="clientLoadBalancingEnable"
              />
              <TriStateToggle
                label="Transient Client Management"
                value={changes.enableTransientClientManagement}
                onChange={(v) => setField("enableTransientClientManagement", v)}
                summaryKey="enableTransientClientManagement"
              />
            </Section>

            {/* ========== Section 6: Roaming & RSSI ========== */}
            <Section title="Roaming & RSSI">
              <TriStateToggle
                label="Minimum Join RSSI Threshold"
                value={changes.enableJoinRSSIThreshold}
                onChange={(v) => setParentToggle("enableJoinRSSIThreshold", v, ["joinRSSIThreshold"])}
                summaryKey="enableJoinRSSIThreshold"
              />
              {changes.enableJoinRSSIThreshold === true && (
                <RssiSlider
                  label="Join RSSI Threshold Value"
                  value={changes.joinRSSIThreshold}
                  onChange={(v) => setField("joinRSSIThreshold", v)}
                  summaryKey="joinRSSIThreshold"
                  indent
                />
              )}
              <RssiSlider
                label="Association Rejection RSSI Threshold"
                value={changes.rssiAssociationRejectionThreshold}
                onChange={(v) => setField("rssiAssociationRejectionThreshold", v)}
                summaryKey="rssiAssociationRejectionThreshold"
              />
              <TriStateToggle
                label="Fast Roaming (802.11r)"
                value={changes.enableFastRoaming}
                onChange={(v) => setParentToggle("enableFastRoaming", v, ["mobilityDomainId"])}
                summaryKey="enableFastRoaming"
              />
              {changes.enableFastRoaming === true && (
                <NumberInput
                  label="Mobility Domain ID"
                  value={changes.mobilityDomainId}
                  onChange={(v) => setField("mobilityDomainId", v)}
                  min={1} max={65535}
                  summaryKey="mobilityDomainId"
                  indent
                />
              )}
              <NumberInput
                label="Client Join Expire Time"
                value={changes.joinExpireTime}
                onChange={(v) => setField("joinExpireTime", v)}
                min={1} max={300} unit="sec"
                summaryKey="joinExpireTime"
              />
              <NumberInput
                label="Client Join Wait Threshold"
                value={changes.joinWaitThreshold}
                onChange={(v) => setField("joinWaitThreshold", v)}
                min={1} max={50}
                summaryKey="joinWaitThreshold"
              />
              <NumberInput
                label="Client Join Wait Time"
                value={changes.joinWaitTime}
                onChange={(v) => setField("joinWaitTime", v)}
                min={1} max={60} unit="sec"
                summaryKey="joinWaitTime"
              />
            </Section>

            {/* ========== Section 7: Rate Limiting ========== */}
            <Section title="Rate Limiting">
              <NumberInput
                label="User Downlink Limit"
                value={changes.userDownlinkRateLimiting}
                onChange={(v) => setField("userDownlinkRateLimiting", v)}
                min={0} max={200} unit="Mbps"
                summaryKey="userDownlinkRateLimiting"
              />
              <NumberInput
                label="User Uplink Limit"
                value={changes.userUplinkRateLimiting}
                onChange={(v) => setField("userUplinkRateLimiting", v)}
                min={0} max={200} unit="Mbps"
                summaryKey="userUplinkRateLimiting"
              />
              <NumberInput
                label="Total Downlink Limit"
                value={changes.totalDownlinkRateLimiting}
                onChange={(v) => setField("totalDownlinkRateLimiting", v)}
                min={0} max={500} unit="Mbps"
                summaryKey="totalDownlinkRateLimiting"
              />
              <NumberInput
                label="Total Uplink Limit"
                value={changes.totalUplinkRateLimiting}
                onChange={(v) => setField("totalUplinkRateLimiting", v)}
                min={0} max={500} unit="Mbps"
                summaryKey="totalUplinkRateLimiting"
              />
              <TriStateToggle
                label="Multicast Downlink Limiting"
                value={changes.enableMulticastDownlinkRateLimiting}
                onChange={(v) =>
                  setParentToggle("enableMulticastDownlinkRateLimiting", v, [
                    "multicastDownlinkRateLimiting",
                  ])
                }
                summaryKey="enableMulticastDownlinkRateLimiting"
              />
              {changes.enableMulticastDownlinkRateLimiting === true && (
                <NumberInput
                  label="Multicast Downlink Limit"
                  value={changes.multicastDownlinkRateLimiting}
                  onChange={(v) => setField("multicastDownlinkRateLimiting", v)}
                  min={1} max={12} unit="Mbps"
                  summaryKey="multicastDownlinkRateLimiting"
                  indent
                />
              )}
              <TriStateToggle
                label="Multicast Uplink Limiting"
                value={changes.enableMulticastUplinkRateLimiting}
                onChange={(v) =>
                  setParentToggle("enableMulticastUplinkRateLimiting", v, [
                    "multicastUplinkRateLimiting",
                  ])
                }
                summaryKey="enableMulticastUplinkRateLimiting"
              />
              {changes.enableMulticastUplinkRateLimiting === true && (
                <NumberInput
                  label="Multicast Uplink Limit"
                  value={changes.multicastUplinkRateLimiting}
                  onChange={(v) => setField("multicastUplinkRateLimiting", v)}
                  min={1} max={100} unit="Mbps"
                  summaryKey="multicastUplinkRateLimiting"
                  indent
                />
              )}
            </Section>

            {/* ========== Section 8: QoS & Application ========== */}
            <Section title="QoS & Application">
              <TriStateToggle
                label="QoS Mirroring"
                value={changes.qosMirroringEnabled}
                onChange={(v) => setParentToggle("qosMirroringEnabled", v, ["qosMirroringScope"])}
                summaryKey="qosMirroringEnabled"
              />
              {changes.qosMirroringEnabled === true && (
                <EnumDropdown
                  label="QoS Mirroring Scope"
                  value={changes.qosMirroringScope}
                  onChange={(v) => setField("qosMirroringScope", v)}
                  options={QOS_SCOPE_OPTIONS}
                  summaryKey="qosMirroringScope"
                  indent
                />
              )}
              <TriStateToggle
                label="Application Visibility"
                value={changes.applicationVisibilityEnabled}
                onChange={(v) => setField("applicationVisibilityEnabled", v)}
                summaryKey="applicationVisibilityEnabled"
              />
              <EnumDropdown
                label="BSS Priority"
                value={changes.bssPriority}
                onChange={(v) => setField("bssPriority", v)}
                options={BSS_PRIORITY_OPTIONS}
                summaryKey="bssPriority"
              />
            </Section>

            {/* ========== Section 9: DHCP ========== */}
            <Section title="DHCP">
              <TriStateToggle
                label="Force Mobile Device DHCP"
                value={changes.forceMobileDeviceDhcp}
                onChange={(v) => setField("forceMobileDeviceDhcp", v)}
                summaryKey="forceMobileDeviceDhcp"
              />
              <TriStateToggle
                label="DHCP Option 82"
                value={changes.dhcpOption82Enabled}
                onChange={(v) =>
                  setParentToggle("dhcpOption82Enabled", v, [
                    "dhcpOption82MacFormat",
                    "dhcpOption82SubOption1Enabled",
                    "dhcpOption82SubOption1Format",
                    "dhcpOption82SubOption2Enabled",
                    "dhcpOption82SubOption2Format",
                    "dhcpOption82SubOption150Enabled",
                    "dhcpOption82SubOption151Enabled",
                    "dhcpOption82SubOption151Format",
                    "dhcpOption82SubOption151Input",
                  ])
                }
                summaryKey="dhcpOption82Enabled"
              />
              {changes.dhcpOption82Enabled === true && (
                <>
                  <EnumDropdown
                    label="MAC Format"
                    value={changes.dhcpOption82MacFormat}
                    onChange={(v) => setField("dhcpOption82MacFormat", v)}
                    options={DHCP82_MAC_FORMAT_OPTIONS}
                    summaryKey="dhcpOption82MacFormat"
                    indent
                  />
                  <TriStateToggle
                    label="Sub-Option 1"
                    value={changes.dhcpOption82SubOption1Enabled}
                    onChange={(v) =>
                      setParentToggle("dhcpOption82SubOption1Enabled", v, [
                        "dhcpOption82SubOption1Format",
                      ])
                    }
                    summaryKey="dhcpOption82SubOption1Enabled"
                    indent
                  />
                  {changes.dhcpOption82SubOption1Enabled === true && (
                    <EnumDropdown
                      label="Sub-Option 1 Format"
                      value={changes.dhcpOption82SubOption1Format}
                      onChange={(v) => setField("dhcpOption82SubOption1Format", v)}
                      options={DHCP82_SUBOPT1_FORMAT_OPTIONS}
                      summaryKey="dhcpOption82SubOption1Format"
                      indent
                    />
                  )}
                  <TriStateToggle
                    label="Sub-Option 2"
                    value={changes.dhcpOption82SubOption2Enabled}
                    onChange={(v) =>
                      setParentToggle("dhcpOption82SubOption2Enabled", v, [
                        "dhcpOption82SubOption2Format",
                      ])
                    }
                    summaryKey="dhcpOption82SubOption2Enabled"
                    indent
                  />
                  {changes.dhcpOption82SubOption2Enabled === true && (
                    <EnumDropdown
                      label="Sub-Option 2 Format"
                      value={changes.dhcpOption82SubOption2Format}
                      onChange={(v) => setField("dhcpOption82SubOption2Format", v)}
                      options={DHCP82_SUBOPT2_FORMAT_OPTIONS}
                      summaryKey="dhcpOption82SubOption2Format"
                      indent
                    />
                  )}
                  <TriStateToggle
                    label="Sub-Option 150"
                    value={changes.dhcpOption82SubOption150Enabled}
                    onChange={(v) => setField("dhcpOption82SubOption150Enabled", v)}
                    summaryKey="dhcpOption82SubOption150Enabled"
                    indent
                  />
                  <TriStateToggle
                    label="Sub-Option 151"
                    value={changes.dhcpOption82SubOption151Enabled}
                    onChange={(v) =>
                      setParentToggle("dhcpOption82SubOption151Enabled", v, [
                        "dhcpOption82SubOption151Format",
                        "dhcpOption82SubOption151Input",
                      ])
                    }
                    summaryKey="dhcpOption82SubOption151Enabled"
                    indent
                  />
                  {changes.dhcpOption82SubOption151Enabled === true && (
                    <>
                      <EnumDropdown
                        label="Sub-Option 151 Format"
                        value={changes.dhcpOption82SubOption151Format}
                        onChange={(v) => setField("dhcpOption82SubOption151Format", v)}
                        options={DHCP82_SUBOPT151_FORMAT_OPTIONS}
                        summaryKey="dhcpOption82SubOption151Format"
                        indent
                      />
                      <TextInput
                        label="Sub-Option 151 Input"
                        value={changes.dhcpOption82SubOption151Input}
                        onChange={(v) => setField("dhcpOption82SubOption151Input", v)}
                        summaryKey="dhcpOption82SubOption151Input"
                        indent
                      />
                    </>
                  )}
                </>
              )}
            </Section>

            {/* ========== Section 10: Frame & Broadcast Limits ========== */}
            <Section title="Frame & Broadcast Limits">
              <TriStateToggle
                label="ARP Request Rate Limit"
                value={changes.enableArpRequestRateLimit}
                onChange={(v) =>
                  setParentToggle("enableArpRequestRateLimit", v, ["arpRequestRateLimit"])
                }
                summaryKey="enableArpRequestRateLimit"
              />
              {changes.enableArpRequestRateLimit === true && (
                <NumberInput
                  label="ARP Rate Limit Value"
                  value={changes.arpRequestRateLimit}
                  onChange={(v) => setField("arpRequestRateLimit", v)}
                  min={15} max={100} unit="pps"
                  summaryKey="arpRequestRateLimit"
                  indent
                />
              )}
              <TriStateToggle
                label="DHCP Request Rate Limit"
                value={changes.enableDhcpRequestRateLimit}
                onChange={(v) =>
                  setParentToggle("enableDhcpRequestRateLimit", v, ["dhcpRequestRateLimit"])
                }
                summaryKey="enableDhcpRequestRateLimit"
              />
              {changes.enableDhcpRequestRateLimit === true && (
                <NumberInput
                  label="DHCP Rate Limit Value"
                  value={changes.dhcpRequestRateLimit}
                  onChange={(v) => setField("dhcpRequestRateLimit", v)}
                  min={15} max={100} unit="pps"
                  summaryKey="dhcpRequestRateLimit"
                  indent
                />
              )}
              <NumberInput
                label="Broadcast Probe Response Delay"
                value={changes.broadcastProbeResponseDelay}
                onChange={(v) => setField("broadcastProbeResponseDelay", v)}
                min={8} max={120} unit="ms"
                summaryKey="broadcastProbeResponseDelay"
              />
              <NumberInput
                label="Directed Threshold"
                value={changes.directedThreshold}
                onChange={(v) => setField("directedThreshold", v)}
                min={0} max={5}
                summaryKey="directedThreshold"
              />
            </Section>

            {/* ========== Section 11: Advanced Features ========== */}
            <Section title="Advanced Features">
              <TriStateToggle
                label="Neighbor Report (802.11k)"
                value={changes.enableNeighborReport}
                onChange={(v) => setField("enableNeighborReport", v)}
                summaryKey="enableNeighborReport"
              />
              <TriStateToggle
                label="Optimized Connectivity (OCE)"
                value={changes.enableOptimizedConnectivityExperience}
                onChange={(v) => setField("enableOptimizedConnectivityExperience", v)}
                summaryKey="enableOptimizedConnectivityExperience"
              />
              <TriStateToggle
                label="Airtime Decongestion"
                value={changes.enableAirtimeDecongestion}
                onChange={(v) => setField("enableAirtimeDecongestion", v)}
                summaryKey="enableAirtimeDecongestion"
              />
              <TriStateToggle
                label="Additional Regulatory Domains"
                value={changes.enableAdditionalRegulatoryDomains}
                onChange={(v) => setField("enableAdditionalRegulatoryDomains", v)}
                summaryKey="enableAdditionalRegulatoryDomains"
              />
              <TriStateToggle
                label="Proxy ARP"
                value={changes.proxyARP}
                onChange={(v) => setField("proxyARP", v)}
                summaryKey="proxyARP"
              />
              <TriStateToggle
                label="Multicast Filter"
                value={changes.multicastFilterEnabled}
                onChange={(v) => setField("multicastFilterEnabled", v)}
                summaryKey="multicastFilterEnabled"
              />
              <TriStateToggle
                label="Syslog"
                value={changes.enableSyslog}
                onChange={(v) => setField("enableSyslog", v)}
                summaryKey="enableSyslog"
              />
              <TriStateToggle
                label="Multi-Link Operation (MLO)"
                value={changes.multiLinkOperationEnabled}
                onChange={(v) => setField("multiLinkOperationEnabled", v)}
                summaryKey="multiLinkOperationEnabled"
              />
              <TriStateToggle
                label="Wi-Fi Calling"
                value={changes.wifiCallingEnabled}
                onChange={(v) => setField("wifiCallingEnabled", v)}
                summaryKey="wifiCallingEnabled"
              />
            </Section>

          </div>

          {/* Preview button */}
          <div className="mt-4 flex justify-end gap-3">
            <button
              onClick={() => setChanges({})}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
            >
              Reset All
            </button>
            <button
              onClick={handlePreview}
              disabled={!hasChanges}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:bg-gray-400"
            >
              Preview Changes
            </button>
          </div>
        </div>
      )}

      {/* ============= STEP 3: Preview Modal ============= */}
      {showPreview && previewData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col">
            <div className="px-6 py-4 border-b flex items-center justify-between">
              <h3 className="text-lg font-semibold">Preview Changes</h3>
              <button
                onClick={() => setShowPreview(false)}
                className="text-gray-400 hover:text-gray-600 text-xl"
              >
                &times;
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <div className="flex flex-wrap gap-3 mb-4 text-sm">
                {previewData.change_count > 0 && (
                  <span className="bg-green-100 text-green-800 px-3 py-1 rounded">
                    {previewData.change_count} network{previewData.change_count !== 1 ? "s" : ""} will change
                  </span>
                )}
                {previewData.already_set_count > 0 && (
                  <span className="bg-blue-100 text-blue-700 px-3 py-1 rounded">
                    {previewData.already_set_count} already at desired values
                  </span>
                )}
                {previewData.unchanged_count - previewData.already_set_count > 0 && (
                  <span className="bg-gray-100 text-gray-600 px-3 py-1 rounded">
                    {previewData.unchanged_count - previewData.already_set_count} unchanged
                  </span>
                )}
              </div>

              {hasDangerousChanges && (
                <DangerBanner className="mb-4" />
              )}

              {previewData.errors.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-sm text-red-700">
                  {previewData.errors.map((e, i) => (
                    <div key={i}>{e}</div>
                  ))}
                </div>
              )}

              {previewData.already_set_count > 0 && previewData.already_set_fields.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4">
                  <div className="text-sm font-medium text-blue-800 mb-1">
                    {previewData.already_set_count === selectedIds.length
                      ? "All selected networks already have these settings:"
                      : `${previewData.already_set_count} network${previewData.already_set_count !== 1 ? "s" : ""} already ${previewData.already_set_count !== 1 ? "have" : "has"} these settings:`}
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-1.5">
                    {previewData.already_set_fields.map((f) => (
                      <span key={f} className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">
                        {FIELD_LABELS[f] || f}: {formatValue(f, changes[f as keyof Changes])}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {previewData.diffs.length === 0 && previewData.already_set_count === 0 ? (
                <p className="text-gray-500 text-center py-8">No changes to apply.</p>
              ) : previewData.diffs.length === 0 ? (
                <p className="text-gray-500 text-center py-4 text-sm">No additional changes needed — all values already match.</p>
              ) : (
                <div className="space-y-2">
                  {previewData.diffs.map((diff) => (
                    <div key={diff.network_id} className="border rounded-lg p-3">
                      <div className="font-medium text-sm">
                        {diff.ssid}{" "}
                        <span className="text-gray-400 font-normal">({diff.name})</span>
                      </div>
                      <div className="mt-2 space-y-1">
                        {diff.changes.map((c, i) => (
                          <div key={i} className="flex items-center text-xs gap-2">
                            <span className="text-gray-500 w-40">
                              {FIELD_LABELS[c.field] || c.field}
                            </span>
                            <span className="text-red-600 line-through">
                              {formatValue(c.field, c.old_value)}
                            </span>
                            <span className="text-gray-400">&rarr;</span>
                            <span className="text-green-700 font-medium">
                              {formatValue(c.field, c.new_value)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {hasDangerousChanges && previewData.diffs.length > 0 && (
                <DangerBanner className="mt-4" />
              )}
            </div>

            <div className="px-6 py-4 border-t flex justify-end gap-3">
              <button
                onClick={() => setShowPreview(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleApplyClick}
                disabled={previewData.change_count === 0}
                className={`px-5 py-2 text-white rounded-lg text-sm disabled:bg-gray-400 ${
                  hasDangerousChanges
                    ? "bg-red-600 hover:bg-red-700 font-semibold"
                    : "bg-green-600 hover:bg-green-700"
                }`}
              >
                {hasDangerousChanges ? "⚠ " : ""}
                Apply Changes to {previewData.change_count} Network
                {previewData.change_count !== 1 ? "s" : ""}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============= Dangerous Change Confirmation ============= */}
      {showDangerConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-60 z-[60] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-md w-full">
            <div className="bg-red-600 rounded-t-xl px-6 py-4">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                &#9888; Critical Change Warning
              </h3>
            </div>
            <div className="px-6 py-5 space-y-3">
              <p className="text-sm text-gray-800 font-medium">
                You are about to bulk-modify <span className="font-bold text-red-700">
                  {changes.vlanId !== undefined && changes.enabled !== undefined
                    ? "VLAN ID and Enabled state"
                    : changes.vlanId !== undefined
                    ? "VLAN ID"
                    : "Enabled state"}
                </span> across <span className="font-bold">{previewData?.change_count ?? 0} network{(previewData?.change_count ?? 0) !== 1 ? "s" : ""}</span>.
              </p>
              <DangerBanner />
              <p className="text-xs text-gray-500 mt-2">This action cannot be easily undone. Proceed with caution.</p>
            </div>
            <div className="px-6 py-4 border-t bg-gray-50 rounded-b-xl flex justify-end gap-3">
              <button
                onClick={() => setShowDangerConfirm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 font-medium"
              >
                Go Back
              </button>
              <button
                onClick={handleApply}
                className="px-5 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 font-semibold"
              >
                Yes, Apply Dangerous Changes
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Job Monitor */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleJobModalClose}
          onJobComplete={handleJobComplete}
        />
      )}
    </div>
  );
}
