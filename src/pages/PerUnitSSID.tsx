import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// LAN Port configuration types
type PortMode = 'ignore' | 'match' | 'specific' | 'disable' | 'uplink';  // 'uplink' means port is protected

interface PortConfig {
  mode: PortMode;
  vlan?: number;
}

// Port config metadata from backend
interface PortConfigMetadata {
  model_uplink_ports: Record<string, string>;
  model_port_counts: Record<string, number>;
  configurable_models: string[];  // All models with configurable LAN ports
  port_categories: {
    lan1_uplink: string[];
    lan2_uplink: string[];
    lan3_uplink: string[];
    lan5_uplink: string[];
  };
}

interface ModelPortConfigs {
  one_port_lan1_uplink: PortConfig[];  // 1-port models where LAN1 is uplink (R650, R750, etc.)
  one_port_lan2_uplink: PortConfig[];  // 1-port models where LAN2 is uplink (R550, R560)
  two_port: PortConfig[];              // H320/H350: LAN1, LAN2 (LAN3 is uplink - outside range)
  four_port: PortConfig[];             // H510/H550/H670: LAN1-4 (LAN5 is uplink - outside range)
}

const DEFAULT_MODEL_PORT_CONFIGS: ModelPortConfigs = {
  one_port_lan1_uplink: [
    { mode: 'uplink' },  // LAN1 is uplink - protected
    { mode: 'ignore' },  // LAN2 is access port - no changes by default
  ],
  one_port_lan2_uplink: [
    { mode: 'ignore' },  // LAN1 is access port - no changes by default
    { mode: 'uplink' },  // LAN2 is uplink - protected
  ],
  two_port: [
    { mode: 'ignore' },  // LAN1 - no changes by default
    { mode: 'ignore' },  // LAN2 - no changes by default
    { mode: 'uplink' },  // LAN3 is uplink - protected (shown but disabled)
  ],
  four_port: [
    { mode: 'ignore' },  // LAN1 - no changes by default
    { mode: 'ignore' },  // LAN2 - no changes by default
    { mode: 'ignore' },  // LAN3 - no changes by default
    { mode: 'ignore' },  // LAN4 - no changes by default
    { mode: 'uplink' },  // LAN5 is uplink - protected (shown but disabled)
  ],
};

interface LanPortConfig {
  portId: string;      // e.g., "1", "2" or "LAN1", "LAN2"
  enabled: boolean;
  untagId: number | null;  // Untagged VLAN ID
  vlanMembers: string;     // Tagged VLANs
  type?: string;           // ACCESS, GENERAL, TRUNK
}

interface LanPortSettings {
  poeMode: string | null;
  poeOut: boolean;
  useVenueSettings: boolean;
  ports: LanPortConfig[];
}

interface LanPortStatus {
  port?: string;      // Port ID like "LAN1"
  id?: string;        // Alternative port ID
  phyLink?: string;   // Physical link status: "Up", "Down"
  physicalLink?: string;  // Alternative physical link field
}

interface ApDetail {
  serial: string;
  name: string;
  model: string | null;
  lan_port_statuses: LanPortStatus[];  // Live physical link status
  lan_port_settings: LanPortSettings | null;  // VLAN/enabled configuration
}

// Venue-level default LAN port settings per AP model
interface VenueModelLanPortSettings {
  model: string;
  poeMode: string | null;
  poeOut: boolean;
  lanPorts: LanPortConfig[];
}

interface SSIDInfo {
  id: string;
  name: string;
  ssid: string;
  base_vlan: number | null;
  vlan_override: number | null;
  effective_vlan: number | null;
  is_all_ap_groups: boolean;
  radio_types: string[];
}

interface AuditData {
  venue_id: string;
  venue_name: string;
  total_ap_groups: number;
  total_aps: number;
  total_ssids: number;
  venue_lan_port_settings: VenueModelLanPortSettings[];  // Venue-level defaults per model
  ap_groups: Array<{
    ap_group_id: string;
    ap_group_name: string;
    venue_id: string;
    venue_name: string;
    description: string;
    total_aps: number;
    ap_names: string[];
    ap_serials: string[];
    aps: ApDetail[];  // Full AP details with LAN port statuses
    total_ssids: number;
    ssid_names: string[];
    ssids: SSIDInfo[];
  }>;
}

// Reusable component for port configuration dropdown
interface PortConfigCellProps {
  config: PortConfig | undefined;
  onChange: (newConfig: PortConfig) => void;
}

function PortConfigCell({ config, onChange }: PortConfigCellProps) {
  const mode = config?.mode || 'ignore';
  const vlan = config?.vlan || 1;

  const getSelectClassName = () => {
    switch (mode) {
      case 'disable':
        return 'border-red-400 bg-red-50 text-red-700';
      case 'ignore':
        return 'border-gray-300 bg-gray-100 text-gray-500';
      case 'match':
        return 'border-green-400 bg-green-50 text-green-700';
      case 'specific':
        return 'border-blue-400 bg-blue-50 text-blue-700';
      default:
        return 'border-gray-300';
    }
  };

  return (
    <div className="flex flex-col items-center gap-1">
      <select
        value={mode}
        onChange={(e) => {
          const newMode = e.target.value as PortMode;
          onChange({
            mode: newMode,
            vlan: newMode === 'specific' ? vlan : undefined
          });
        }}
        className={`text-xs border rounded px-1 py-1 w-20 ${getSelectClassName()}`}
      >
        <option value="ignore">Ignore</option>
        <option value="match">Match</option>
        <option value="specific">Specific</option>
        <option value="disable">Disable</option>
      </select>
      {mode === 'specific' && (
        <input
          type="number"
          min="1"
          max="4094"
          value={vlan}
          onChange={(e) => {
            onChange({
              mode: 'specific',
              vlan: parseInt(e.target.value) || 1
            });
          }}
          className="w-14 text-xs border border-gray-300 rounded px-1 py-1"
        />
      )}
    </div>
  );
}

function PerUnitSSID() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  const [csvInput, setCsvInput] = useState("");
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");

  // Job monitor modal state
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);
  const [lastJobResult, setLastJobResult] = useState<JobResult | null>(null);

  // Configuration options
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);
  const [apGroupPrefix, setApGroupPrefix] = useState("");
  const [apGroupPostfix, setApGroupPostfix] = useState("");
  const [nameConflictResolution, setNameConflictResolution] = useState<'keep' | 'overwrite'>('overwrite');

  // LAN port configuration options (Phase 5)
  const [configureLanPorts, setConfigureLanPorts] = useState(false);
  const [modelPortConfigs, setModelPortConfigs] = useState<ModelPortConfigs>(DEFAULT_MODEL_PORT_CONFIGS);

  // Parallel execution options
  const [parallelExecution, setParallelExecution] = useState(false);
  const [maxConcurrent, setMaxConcurrent] = useState(10);

  // Audit modal state
  const [showAuditModal, setShowAuditModal] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [auditError, setAuditError] = useState("");
  const [venueDefaultsExpanded, setVenueDefaultsExpanded] = useState(false);  // Collapsed by default

  // Venue defaults filters
  const [filterEnvironment, setFilterEnvironment] = useState<'all' | 'indoor' | 'outdoor'>('all');
  const [filterSeries, setFilterSeries] = useState<'all' | 'H' | 'R' | 'T'>('all');
  const [filterWifi, setFilterWifi] = useState<'all' | '7' | '6E' | '6' | '5'>('all');

  // Confirmation dialog for single-port models
  const [showSinglePortConfirm, setShowSinglePortConfirm] = useState(false);
  const [pendingSubmit, setPendingSubmit] = useState(false);

  // Port config metadata from backend (single source of truth)
  const [portConfigMetadata, setPortConfigMetadata] = useState<PortConfigMetadata | null>(null);
  const [metadataLoading, setMetadataLoading] = useState(false);

  // Fetch port config metadata from backend on mount
  useEffect(() => {
    const fetchPortConfigMetadata = async () => {
      setMetadataLoading(true);
      try {
        const response = await fetch(`${API_BASE_URL}/per-unit-ssid/port-config-metadata`, {
          credentials: "include",
        });
        if (response.ok) {
          const data = await response.json();
          setPortConfigMetadata(data);
        }
      } catch (err) {
        console.error("Failed to fetch port config metadata:", err);
      } finally {
        setMetadataLoading(false);
      }
    };
    fetchPortConfigMetadata();
  }, []);

  // Helper to categorize AP models
  const getModelInfo = (model: string) => {
    const m = model.toUpperCase();

    // Series (H, R, T)
    const series = m.charAt(0) as 'H' | 'R' | 'T';

    // Environment: T-series is outdoor, H/R are indoor
    const environment: 'indoor' | 'outdoor' = series === 'T' ? 'outdoor' : 'indoor';

    // WiFi generation based on model patterns
    let wifi: '7' | '6E' | '6' | '5' = '6'; // default
    if (m.includes('770') || m.includes('670') || m.includes('575') || m.includes('370')) {
      wifi = '7';
    } else if (m.includes('760') || m.includes('560')) {
      wifi = '6E';
    } else if (m.includes('850') || m.includes('750') || m.includes('650') || m.includes('550') || m.includes('350')) {
      wifi = '6';
    } else if (m.includes('710') || m.includes('610') || m.includes('510') || m.includes('320') || m.includes('720') || m.includes('310')) {
      wifi = '5';
    }

    return { series, environment, wifi };
  };

  // Helper to get all unique VLANs configured on LAN ports for a group's APs
  const getGroupLanPortVlans = (group: AuditData['ap_groups'][0]): Set<number> => {
    const vlans = new Set<number>();
    for (const ap of group.aps) {
      if (ap.lan_port_settings?.lanPorts) {
        for (const port of ap.lan_port_settings.lanPorts) {
          if (port.enabled && port.untagId) {
            vlans.add(port.untagId);
          }
        }
      }
    }
    return vlans;
  };

  // Helper to check if an SSID VLAN has a potential mismatch with LAN port config
  const checkVlanMismatch = (ssidVlan: number | null, lanPortVlans: Set<number>): 'match' | 'mismatch' | 'no-lan-config' | 'no-ssid-vlan' => {
    if (ssidVlan === null) return 'no-ssid-vlan';
    if (lanPortVlans.size === 0) return 'no-lan-config';
    if (lanPortVlans.has(ssidVlan)) return 'match';
    return 'mismatch';
  };

  // Filter venue LAN port settings
  const filteredVenueLanSettings = (auditData?.venue_lan_port_settings || []).filter(setting => {
    const info = getModelInfo(setting.model);

    if (filterEnvironment !== 'all' && info.environment !== filterEnvironment) return false;
    if (filterSeries !== 'all' && info.series !== filterSeries) return false;
    if (filterWifi !== 'all' && info.wifi !== filterWifi) return false;

    return true;
  });

  // Determine tenant ID (for MSP, it's null until explicitly set; for EC, use r1_tenant_id)
  const activeController = controllers.find(c => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null  // For MSP, we'd need EC selector - for now just use controller's default
    : (activeController?.r1_tenant_id || null);

  const handleCsvInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCsvInput(e.target.value);
    setError("");
  };

  const handleProcess = async () => {
    if (!csvInput.trim()) {
      setError("Please provide CSV data");
      return;
    }

    if (!venueId.trim()) {
      setError("Please enter a Venue ID");
      return;
    }

    if (!activeControllerId) {
      setError("Please select an active controller first");
      return;
    }

    // Confirmation for LAN port configuration
    if (configureLanPorts) {
      // Check if any ports are actually configured (not all ignore)
      const allIgnore = [
        modelPortConfigs.one_port_lan1_uplink[1]?.mode,
        modelPortConfigs.one_port_lan2_uplink[0]?.mode,
        ...modelPortConfigs.two_port.slice(0, 2).map(p => p?.mode),
        ...modelPortConfigs.four_port.slice(0, 4).map(p => p?.mode),
      ].every(m => m === 'ignore' || m === 'uplink' || !m);

      if (allIgnore) {
        setError("All LAN ports are set to 'Ignore'. Please configure at least one port to Match, Specific, or Disable.");
        return;
      }

      let confirmMsg = 'You are about to configure LAN ports on APs with configurable ports.\n\n';
      confirmMsg += 'Port configuration summary:\n';
      confirmMsg += `• 1-Port (LAN1 uplink): LAN2=${modelPortConfigs.one_port_lan1_uplink[1]?.mode || 'ignore'}\n`;
      confirmMsg += `• 1-Port (LAN2 uplink): LAN1=${modelPortConfigs.one_port_lan2_uplink[0]?.mode || 'ignore'}\n`;
      confirmMsg += `• 2-Port (H320/H350/T750): LAN1=${modelPortConfigs.two_port[0]?.mode || 'ignore'}, LAN2=${modelPortConfigs.two_port[1]?.mode || 'ignore'}\n`;
      confirmMsg += `• 4-Port (H510/H550/H670): ${modelPortConfigs.four_port.slice(0, 4).map((p, i) => `LAN${i+1}=${p?.mode || 'ignore'}`).join(', ')}\n\n`;
      confirmMsg += 'Ports set to "Ignore" will not be modified.\n';
      confirmMsg += 'Uplink ports are protected and will not be modified.\n\n';
      confirmMsg += 'Continue with LAN port configuration?';

      if (!window.confirm(confirmMsg)) {
        return;
      }
    }

    setProcessing(true);
    setError("");

    try {
      // Parse CSV
      const lines = csvInput.trim().split('\n');
      if (lines.length < 2) {
        setError("CSV must have header row and at least one data row");
        setProcessing(false);
        return;
      }

      // Parse header
      const headers = lines[0].split(',').map(h => h.trim());
      const requiredHeaders = ['unit_number', 'ssid_name', 'ssid_password', 'security_type', 'default_vlan'];
      const optionalHeaders = ['ap_serial_or_name', 'network_name'];

      // Validate required headers
      const hasAllRequired = requiredHeaders.every(h => headers.includes(h));
      if (!hasAllRequired) {
        setError(`Invalid CSV format. Required headers: ${requiredHeaders.join(', ')}. Optional: ${optionalHeaders.join(', ')}`);
        setProcessing(false);
        return;
      }

      // Parse rows and group by unit
      const unitMap = new Map<string, any>();

      for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;

        const values = line.split(',').map(v => v.trim());
        const row: any = {};
        headers.forEach((header, idx) => {
          row[header] = values[idx] || '';
        });

        const unitNumber = row.unit_number;
        if (!unitMap.has(unitNumber)) {
          unitMap.set(unitNumber, {
            unit_number: unitNumber,
            ap_identifiers: [],
            ssid_name: row.ssid_name,
            network_name: row.network_name || null,  // Optional: internal R1 name (defaults to ssid_name)
            ssid_password: row.ssid_password,
            security_type: row.security_type || 'WPA3',
            default_vlan: row.default_vlan || '1'
          });
        }

        // Add AP to this unit
        if (row.ap_serial_or_name) {
          unitMap.get(unitNumber).ap_identifiers.push(row.ap_serial_or_name);
        }
      }

      const units = Array.from(unitMap.values());

      if (units.length === 0) {
        setError("No valid units found in CSV");
        setProcessing(false);
        return;
      }

      // Call backend API - now returns job_id for async processing
      const response = await fetch(`${API_BASE_URL}/per-unit-ssid/configure`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
          units: units,
          ap_group_prefix: apGroupPrefix,
          ap_group_postfix: apGroupPostfix,
          name_conflict_resolution: nameConflictResolution,
          configure_lan_ports: configureLanPorts,
          model_port_configs: modelPortConfigs,
          parallel_execution: parallelExecution,
          max_concurrent: maxConcurrent
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Configuration failed");
      }

      const result = await response.json();

      // Show job monitor modal
      setCurrentJobId(result.job_id);
      setShowJobModal(true);

    } catch (err: any) {
      console.error("Processing error:", err);
      setError(err.message || "An error occurred");
    } finally {
      setProcessing(false);
    }
  };

  const handleCloseJobModal = () => {
    setShowJobModal(false);
    // Keep the job ID so user can reopen if needed
  };

  const handleJobComplete = (result: JobResult) => {
    setLastJobResult(result);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      setCsvInput(text);
    };
    reader.readAsText(file);
  };

  const handleVenueSelect = (selectedVenueId: string | null, venue: any) => {
    setVenueId(selectedVenueId);
    setVenueName(venue?.name || null);
  };

  // Audit progress message for UI feedback during polling
  const [auditProgress, setAuditProgress] = useState<string>("");

  const handleAuditVenue = async () => {
    if (!venueId) {
      setAuditError("Please select a venue");
      return;
    }

    if (!activeControllerId) {
      setAuditError("Please select an active controller first");
      return;
    }

    setAuditLoading(true);
    setAuditError("");
    setAuditData(null);
    setAuditProgress("Starting audit...");

    try {
      // Step 1: Start the async audit job
      console.log('🔍 Starting audit job...');
      const startResponse = await fetch(`${API_BASE_URL}/per-unit-ssid/audit/start`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          controller_id: activeControllerId,
          venue_id: venueId,
        }),
      });

      if (!startResponse.ok) {
        const error = await startResponse.json().catch(() => ({}));
        throw new Error(error.detail || `Failed to start audit: ${startResponse.status}`);
      }

      const startResult = await startResponse.json();
      const jobId = startResult.job_id;
      console.log(`🔍 Audit job started: ${jobId}`);
      setAuditProgress("Audit job started, processing...");

      // Step 2: Poll for completion
      const POLL_INTERVAL_MS = 2000; // 2 seconds
      const MAX_POLL_TIME_MS = 10 * 60 * 1000; // 10 minutes max
      const startTime = Date.now();

      while (true) {
        // Check timeout
        if (Date.now() - startTime > MAX_POLL_TIME_MS) {
          throw new Error('Audit timed out after 10 minutes. Please try again.');
        }

        // Wait before polling
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));

        // Poll status
        const statusResponse = await fetch(
          `${API_BASE_URL}/per-unit-ssid/audit/${jobId}/status`,
          { credentials: "include" }
        );

        if (!statusResponse.ok) {
          console.warn(`Status check failed: ${statusResponse.status}`);
          continue; // Keep polling
        }

        const status = await statusResponse.json();
        console.log(`🔍 Audit status: ${status.status} - ${status.progress || ''}`);
        setAuditProgress(status.progress || status.message || `Status: ${status.status}`);

        if (status.status === 'COMPLETED') {
          // Step 3: Fetch results
          console.log('🔍 Audit completed, fetching results...');
          setAuditProgress("Audit complete, loading results...");

          const resultResponse = await fetch(
            `${API_BASE_URL}/per-unit-ssid/audit/${jobId}/result`,
            { credentials: "include" }
          );

          if (!resultResponse.ok) {
            const error = await resultResponse.json().catch(() => ({}));
            throw new Error(error.detail || `Failed to fetch audit results: ${resultResponse.status}`);
          }

          const data = await resultResponse.json();
          console.log('🔍 FRONTEND: Received audit data:', data);
          console.log('🔍 FRONTEND: Total AP Groups in response:', data.ap_groups?.length);
          setAuditData(data);
          setShowAuditModal(true);
          break;
        } else if (status.status === 'FAILED') {
          throw new Error(status.message || 'Audit failed');
        }
        // Continue polling for PENDING or RUNNING
      }
    } catch (err: any) {
      console.error("Audit error:", err);
      setAuditError(err.message || "An error occurred during audit");
    } finally {
      setAuditLoading(false);
      setAuditProgress("");
    }
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h2 className="text-3xl font-bold mb-4">Per-Unit SSID Configuration</h2>

      <p className="text-gray-600 mb-6">
        Configure unique SSIDs for individual apartment units or rooms in RuckusONE
      </p>

      {/* Educational Section - How It Works */}
      <div className="bg-gradient-to-r from-purple-50 to-blue-50 border-2 border-purple-200 rounded-lg p-6 mb-6">
        <h3 className="text-xl font-bold mb-3 flex items-center gap-2">
          <span className="text-2xl">💡</span>
          How Per-Unit SSIDs Work in RuckusONE
        </h3>

        <div className="space-y-3 text-sm text-gray-700">
          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-purple-900 mb-2">🔄 SmartZone → RuckusONE Migration</p>
            <p>
              In <strong>SmartZone</strong>, you used <strong>WLAN Groups</strong> to assign different SSIDs to different APs.
              <br/>
              In <strong>RuckusONE</strong>, WLAN Groups don't exist. Instead, you use <strong>AP Groups</strong> + <strong>SSID assignments</strong>.
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-blue-900 mb-2">⚙️ Configuration Flow</p>
            <ol className="list-decimal list-inside space-y-1 ml-2">
              <li><strong>Create SSIDs</strong> for each unit in your venue (e.g., "Unit-101", "Unit-102")</li>
              <li><strong>Create AP Groups</strong> for each unit (e.g., "APGroup-101", "APGroup-102")</li>
              <li><strong>Assign APs</strong> in each physical unit to their corresponding AP Group</li>
              <li><strong>Assign SSIDs</strong> to their corresponding AP Groups (Unit-101 SSID → APGroup-101)</li>
            </ol>
            <p className="mt-2 text-xs italic text-gray-600">
              This tool automates steps 1-4 for you based on your unit list!
            </p>
          </div>

          <div className="bg-white bg-opacity-60 rounded p-3">
            <p className="font-semibold text-green-900 mb-2">🎯 Advanced: Neighboring AP Broadcast <span className="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded ml-2">Coming Soon</span></p>
            <p>
              <strong>Future Feature:</strong> Configure neighboring APs to also broadcast a unit's SSID for better coverage and seamless roaming.
              <br/>
              <span className="text-amber-700 font-medium">⚠️ Trade-off:</span> Better resiliency vs. increased interference and airtime overhead.
            </p>
          </div>
        </div>
      </div>

      {/* CSV Template Download */}
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6">
        <h3 className="text-lg font-semibold mb-2 flex items-center gap-2">
          <span>📋</span> CSV Format Required
        </h3>
        <p className="text-sm text-gray-700 mb-3">
          Upload a CSV file with the following columns:
        </p>
        <div className="text-xs space-y-1 mb-3">
          <div><strong>Required:</strong> <code className="bg-white px-1 py-0.5 rounded text-xs">unit_number, ssid_name, ssid_password, security_type, default_vlan</code></div>
          <div><strong>Optional:</strong> <code className="bg-white px-1 py-0.5 rounded text-xs">ap_serial_or_name, network_name</code></div>
        </div>
        <p className="text-xs text-gray-600 mb-3 italic">
          💡 <strong>Tip:</strong> Multiple rows with the same <code className="bg-gray-100 px-1 rounded">unit_number</code> will be grouped into the same AP Group. <code className="bg-gray-100 px-1 rounded">network_name</code> sets the internal R1 name (defaults to <code className="bg-gray-100 px-1 rounded">ssid_name</code> if omitted).
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => {
              const template = `unit_number,ap_serial_or_name,ssid_name,ssid_password,security_type,default_vlan
101,AP-101-Living,Unit-101,SecurePass101!,WPA3,10
101,AP-101-Bedroom,Unit-101,SecurePass101!,WPA3,10
102,AP-102-Living,Unit-102,SecurePass102!,WPA3,20
102,AP-102-Bedroom,Unit-102,SecurePass102!,WPA3,20
103,12AB34CD56EF,Unit-103,SecurePass103!,WPA2/WPA3,30
103,AB12CD34EF56,Unit-103,SecurePass103!,WPA2/WPA3,30`;
              const blob = new Blob([template], { type: 'text/csv' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'per-unit-ssid-with-aps.csv';
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="px-4 py-2 bg-amber-600 text-white rounded hover:bg-amber-700 text-sm font-medium"
          >
            📥 Template with APs
          </button>

          <button
            onClick={() => {
              const template = `unit_number,ssid_name,ssid_password,security_type,default_vlan
101,Unit-101,SecurePass101!,WPA3,10
102,Unit-102,SecurePass102!,WPA3,20
103,Unit-103,SecurePass103!,WPA2/WPA3,30
104,Unit-104,SecurePass104!,WPA3,40
105,Unit-105,SecurePass105!,WPA3,50`;
              const blob = new Blob([template], { type: 'text/csv' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'per-unit-ssid-no-aps.csv';
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
          >
            📝 Template without APs
          </button>
        </div>
      </div>

      {/* Venue Selection Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 1: Select Venue</h3>

        {activeControllerType !== "RuckusONE" ? (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <p className="text-sm text-yellow-800">
              Please select a RuckusONE controller as your active controller to use this tool.
            </p>
          </div>
        ) : (
          <SingleVenueSelector
            controllerId={activeControllerId}
            tenantId={effectiveTenantId}
            onVenueSelect={handleVenueSelect}
            selectedVenueId={venueId}
          />
        )}
      </div>

      {/* Input Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="text-xl font-semibold mb-4">Step 2: Configuration Input</h3>

        {/* Selected Venue Display */}
        {venueId && venueName && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg">
            <p className="text-sm text-green-800">
              <strong>Selected Venue:</strong> {venueName} ({venueId})
            </p>
          </div>
        )}

        {/* AP Group Naming (Prefix + Postfix) */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            AP Group Naming
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={apGroupPrefix}
              onChange={(e) => setApGroupPrefix(e.target.value)}
              placeholder="Prefix"
              className="w-32 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
            />
            <span className="text-gray-500 font-mono bg-gray-100 px-3 py-2 rounded border border-gray-200">
              {'{unit}'}
            </span>
            <input
              type="text"
              value={apGroupPostfix}
              onChange={(e) => setApGroupPostfix(e.target.value)}
              placeholder="Postfix"
              className="w-32 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
            />
            <span className="text-gray-400 mx-2">=</span>
            <span className="text-sm font-mono bg-blue-50 text-blue-700 px-3 py-2 rounded border border-blue-200">
              {apGroupPrefix || ''}101{apGroupPostfix || ''}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Optional prefix and/or postfix for AP Group names. Leave blank to use just the unit number.
          </p>
        </div>

        {/* Network Name Conflict Resolution */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Network Name Conflict
          </label>
          <select
            value={nameConflictResolution}
            onChange={(e) => setNameConflictResolution(e.target.value as 'keep' | 'overwrite')}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="keep">Keep existing R1 name</option>
            <option value="overwrite">Overwrite with ruckus.tools name</option>
          </select>
          <p className="text-xs text-gray-500 mt-1">
            When SSID exists but internal network name differs: keep R1's name or update to match rtools CSV
          </p>
        </div>

        {/* LAN Port Configuration (Phase 5) */}
        <div className="mb-4 p-4 bg-gray-50 border border-gray-200 rounded-lg">
          <div className="flex items-start gap-3">
            <input
              type="checkbox"
              id="configureLanPorts"
              checked={configureLanPorts}
              onChange={(e) => setConfigureLanPorts(e.target.checked)}
              className="mt-1 h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
            />
            <div className="flex-1">
              <label htmlFor="configureLanPorts" className="block text-sm font-medium text-gray-700 cursor-pointer">
                Configure AP LAN Ports
              </label>
              <p className="text-xs text-gray-500 mt-1">
                Set LAN port VLANs on APs to match each unit's <code className="bg-gray-200 px-1 rounded">default_vlan</code>. Supports H-series, R-series, and T-series with configurable ports.
              </p>

              {configureLanPorts && (
                <div className="mt-3">
                  <label className="block text-xs font-medium text-gray-600 mb-2">
                    Port Configuration Matrix
                    {metadataLoading && <span className="ml-2 text-gray-400">(loading...)</span>}
                  </label>

                  <div className="bg-white rounded border border-gray-200 overflow-hidden overflow-x-auto">
                    <table className="w-full text-sm min-w-[600px]">
                      <thead className="bg-gray-100">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium text-gray-700 w-44">Model Type</th>
                          <th className="px-2 py-2 text-center font-medium text-gray-700 w-20">LAN1</th>
                          <th className="px-2 py-2 text-center font-medium text-gray-700 w-20">LAN2</th>
                          <th className="px-2 py-2 text-center font-medium text-gray-700 w-20">LAN3</th>
                          <th className="px-2 py-2 text-center font-medium text-gray-700 w-20">LAN4</th>
                          <th className="px-2 py-2 text-center font-medium text-gray-700 w-20">LAN5</th>
                        </tr>
                      </thead>
                      <tbody>
                        {/* Row 1: 1-Port Models with LAN1 as UPLINK (R650, R750, etc.) */}
                        <tr className="bg-amber-50 border-b border-amber-200">
                          <td className="px-3 py-2 font-medium text-gray-700">
                            <div className="text-sm">1-Port (LAN1 Uplink)</div>
                            <div className="text-xs text-gray-500">
                              {portConfigMetadata?.port_categories.lan1_uplink.join(', ') || 'R650, R750, R850, T350...'}
                            </div>
                          </td>
                          {/* LAN1 = UPLINK */}
                          <td className="px-2 py-2 text-center">
                            <span className="inline-block px-2 py-1 bg-blue-100 text-blue-800 text-xs font-bold rounded border border-blue-300">
                              UPLINK
                            </span>
                          </td>
                          {/* LAN2 = Access Port */}
                          <td className="px-2 py-2 text-center">
                            <PortConfigCell
                              config={modelPortConfigs.one_port_lan1_uplink[1]}
                              onChange={(newConfig) => {
                                const updated = { ...modelPortConfigs };
                                updated.one_port_lan1_uplink = [...updated.one_port_lan1_uplink];
                                updated.one_port_lan1_uplink[1] = newConfig;
                                setModelPortConfigs(updated);
                              }}
                            />
                          </td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                        </tr>

                        {/* Row 2: 1-Port Models with LAN2 as UPLINK (R550, R560) */}
                        <tr className="bg-amber-50 border-b border-amber-200">
                          <td className="px-3 py-2 font-medium text-gray-700">
                            <div className="text-sm">1-Port (LAN2 Uplink)</div>
                            <div className="text-xs text-gray-500">
                              {portConfigMetadata?.port_categories.lan2_uplink.join(', ') || 'R550, R560'}
                            </div>
                          </td>
                          {/* LAN1 = Access Port */}
                          <td className="px-2 py-2 text-center">
                            <PortConfigCell
                              config={modelPortConfigs.one_port_lan2_uplink[0]}
                              onChange={(newConfig) => {
                                const updated = { ...modelPortConfigs };
                                updated.one_port_lan2_uplink = [...updated.one_port_lan2_uplink];
                                updated.one_port_lan2_uplink[0] = newConfig;
                                setModelPortConfigs(updated);
                              }}
                            />
                          </td>
                          {/* LAN2 = UPLINK */}
                          <td className="px-2 py-2 text-center">
                            <span className="inline-block px-2 py-1 bg-blue-100 text-blue-800 text-xs font-bold rounded border border-blue-300">
                              UPLINK
                            </span>
                          </td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                        </tr>

                        {/* Row 3: 2-Port Models (H320/H350/T750) - LAN3 is uplink */}
                        <tr className="bg-gray-50 border-b">
                          <td className="px-3 py-2 font-medium text-gray-700">
                            <div className="text-sm">2-Port Models</div>
                            <div className="text-xs text-gray-500">
                              {portConfigMetadata?.port_categories.lan3_uplink.join(', ') || 'H320, H350, T750'}
                            </div>
                          </td>
                          {/* LAN1, LAN2 = Access Ports */}
                          {[0, 1].map((portIdx) => (
                            <td key={portIdx} className="px-2 py-2 text-center">
                              <PortConfigCell
                                config={modelPortConfigs.two_port[portIdx]}
                                onChange={(newConfig) => {
                                  const updated = { ...modelPortConfigs };
                                  updated.two_port = [...updated.two_port];
                                  updated.two_port[portIdx] = newConfig;
                                  setModelPortConfigs(updated);
                                }}
                              />
                            </td>
                          ))}
                          {/* LAN3 = UPLINK */}
                          <td className="px-2 py-2 text-center">
                            <span className="inline-block px-2 py-1 bg-blue-100 text-blue-800 text-xs font-bold rounded border border-blue-300">
                              UPLINK
                            </span>
                          </td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                          <td className="px-2 py-2 text-center text-gray-300">—</td>
                        </tr>

                        {/* Row 4: 4-Port Models (H510/H550/H670) - LAN5 is uplink */}
                        <tr className="bg-white">
                          <td className="px-3 py-2 font-medium text-gray-700">
                            <div className="text-sm">4-Port Models</div>
                            <div className="text-xs text-gray-500">
                              {portConfigMetadata?.port_categories.lan5_uplink.join(', ') || 'H510, H550, H670'}
                            </div>
                          </td>
                          {/* LAN1-4 = Access Ports */}
                          {[0, 1, 2, 3].map((portIdx) => (
                            <td key={portIdx} className="px-2 py-2 text-center">
                              <PortConfigCell
                                config={modelPortConfigs.four_port[portIdx]}
                                onChange={(newConfig) => {
                                  const updated = { ...modelPortConfigs };
                                  updated.four_port = [...updated.four_port];
                                  updated.four_port[portIdx] = newConfig;
                                  setModelPortConfigs(updated);
                                }}
                              />
                            </td>
                          ))}
                          {/* LAN5 = UPLINK */}
                          <td className="px-2 py-2 text-center">
                            <span className="inline-block px-2 py-1 bg-blue-100 text-blue-800 text-xs font-bold rounded border border-blue-300">
                              UPLINK
                            </span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-2 text-xs text-gray-500 space-y-1">
                    <div>
                      <strong>Match:</strong> Uses unit's <code className="bg-gray-200 px-1 rounded">default_vlan</code> •
                      <strong className="ml-2">Specific:</strong> Custom VLAN •
                      <strong className="ml-2">Disable:</strong> Disable port via API •
                      <span className="ml-2 px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded font-bold">UPLINK</span>
                      <span className="ml-1">Protected (not configurable)</span>
                    </div>
                    <div className="text-gray-400">
                      <strong>0-Port models</strong> (e.g., ceiling APs with only uplink) have no configurable LAN ports and are skipped.
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Parallel Execution Options */}
        <div className="mb-4 p-4 bg-purple-50 border border-purple-200 rounded-lg">
          <div className="flex items-start gap-3">
            <input
              type="checkbox"
              id="parallelExecution"
              checked={parallelExecution}
              onChange={(e) => setParallelExecution(e.target.checked)}
              className="mt-1 h-4 w-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
            />
            <div className="flex-1">
              <label htmlFor="parallelExecution" className="block text-sm font-medium text-gray-700 cursor-pointer">
                Enable Parallel Execution
                <span className="ml-2 text-xs font-normal text-purple-600 bg-purple-100 px-2 py-0.5 rounded">
                  Recommended for 5+ units
                </span>
              </label>
              <p className="text-xs text-gray-500 mt-1">
                Process each unit independently in parallel. Each unit runs through all phases before moving to the next.
                Without this, all units complete Phase 1 before any start Phase 2, etc.
              </p>

              {parallelExecution && (
                <div className="mt-3 flex items-center gap-3">
                  <label htmlFor="maxConcurrent" className="text-sm text-gray-600">
                    Max concurrent units:
                  </label>
                  <input
                    type="number"
                    id="maxConcurrent"
                    min="1"
                    max="50"
                    value={maxConcurrent}
                    onChange={(e) => setMaxConcurrent(Math.max(1, Math.min(50, parseInt(e.target.value) || 10)))}
                    className="w-20 px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-purple-500"
                  />
                  <span className="text-xs text-gray-400">
                    (1-50, default 10)
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* CSV Text Input */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Or paste CSV content directly:
          </label>
          <textarea
            value={csvInput}
            onChange={handleCsvInputChange}
            placeholder="unit_number,ap_serial_or_name,ssid_name,network_name,ssid_password,security_type,default_vlan&#10;101,AP-101-Living,MyWiFi,Unit-101-Network,SecurePass101!,WPA3,10"
            className="w-full h-32 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"
          />
        </div>

        {/* File Upload */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Or upload a CSV file:
          </label>
          <input
            type="file"
            accept=".csv,.txt"
            onChange={handleFileUpload}
            className="block w-full text-sm text-gray-500
              file:mr-4 file:py-2 file:px-4
              file:rounded-md file:border-0
              file:text-sm file:font-semibold
              file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100"
          />
        </div>

        {/* Error Message */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}

        {/* Process Button */}
        <button
          onClick={handleProcess}
          disabled={processing || !activeControllerId}
          className={`px-6 py-2 rounded font-semibold ${
            processing || !activeControllerId
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700 text-white"
          }`}
        >
          {processing ? "Processing..." : "Process Units"}
        </button>

        {/* Last Job Result */}
        {lastJobResult && (
          <div className={`mt-4 p-4 rounded-lg border ${
            lastJobResult.status === 'COMPLETED'
              ? 'bg-green-50 border-green-200'
              : lastJobResult.status === 'FAILED'
              ? 'bg-red-50 border-red-200'
              : 'bg-yellow-50 border-yellow-200'
          }`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-2xl">
                  {lastJobResult.status === 'COMPLETED' ? '✅' :
                   lastJobResult.status === 'FAILED' ? '❌' : '⚠️'}
                </span>
                <div>
                  <p className={`font-semibold ${
                    lastJobResult.status === 'COMPLETED' ? 'text-green-800' :
                    lastJobResult.status === 'FAILED' ? 'text-red-800' : 'text-yellow-800'
                  }`}>
                    Last Job: {lastJobResult.status}
                  </p>
                  <p className="text-sm text-gray-600">
                    {lastJobResult.progress.completed_phases}/{lastJobResult.progress.total_phases} phases completed
                    {lastJobResult.progress.failed > 0 && (
                      <span className="text-red-600 ml-2">
                        ({lastJobResult.progress.failed} tasks failed)
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowJobModal(true)}
                  className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 rounded border border-gray-300"
                >
                  View Details
                </button>
                <button
                  onClick={() => setLastJobResult(null)}
                  className="px-2 py-1 text-gray-400 hover:text-gray-600"
                  title="Dismiss"
                >
                  ×
                </button>
              </div>
            </div>
            {lastJobResult.errors && lastJobResult.errors.length > 0 && (
              <div className="mt-2 text-sm text-red-600">
                {lastJobResult.errors.slice(0, 2).map((err, idx) => (
                  <p key={idx}>{err}</p>
                ))}
                {lastJobResult.errors.length > 2 && (
                  <p className="text-gray-500">...and {lastJobResult.errors.length - 2} more</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Audit Section */}
      <div className="bg-white rounded-lg shadow p-6 mt-6">
        <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
          <span>🔍</span> (Optional) Step 3: Venue Network Audit
        </h3>
        <p className="text-sm text-gray-600 mb-4">
          View the current network configuration for your venue before making changes.
          This shows all AP Groups, their members, and SSID activations.
        </p>

        {auditError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {auditError}
          </div>
        )}

        <div className="flex items-center gap-4">
          <button
            onClick={handleAuditVenue}
            disabled={auditLoading || !activeControllerId || !venueId}
            className={`px-6 py-2 rounded font-semibold ${
              auditLoading || !activeControllerId || !venueId
                ? "bg-gray-400 cursor-not-allowed"
                : "bg-indigo-600 hover:bg-indigo-700 text-white"
            }`}
          >
            {auditLoading ? "Auditing..." : "Audit Venue"}
          </button>
          {auditLoading && auditProgress && (
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <svg className="animate-spin h-4 w-4 text-indigo-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span>{auditProgress}</span>
            </div>
          )}
        </div>
      </div>

      {/* Job Monitor Modal */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleCloseJobModal}
          onJobComplete={handleJobComplete}
        />
      )}

      {/* Audit Modal */}
      {showAuditModal && auditData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-6xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-indigo-600 to-purple-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h3 className="text-2xl font-bold">Venue Network Audit</h3>
                <p className="text-indigo-100 text-sm">
                  {auditData.venue_name} ({auditData.venue_id})
                </p>
              </div>
              <button
                onClick={() => setShowAuditModal(false)}
                className="text-white hover:text-gray-200 text-2xl font-bold"
              >
                ×
              </button>
            </div>

            {/* Modal Body */}
            <div className="overflow-y-auto flex-1 p-6">
              {/* Summary Cards */}
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-blue-600">
                    {auditData.total_ap_groups}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">AP Groups</div>
                </div>
                <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-green-600">
                    {auditData.total_aps}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Access Points</div>
                </div>
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 text-center">
                  <div className="text-3xl font-bold text-purple-600">
                    {auditData.total_ssids}
                  </div>
                  <div className="text-sm text-gray-600 mt-1">Unique SSIDs</div>
                </div>
              </div>

              {/* Venue Default LAN Port Settings (Collapsible) */}
              {auditData.venue_lan_port_settings && auditData.venue_lan_port_settings.length > 0 && (
                <div className="mb-6 border border-amber-200 bg-amber-50 rounded-lg overflow-hidden">
                  <button
                    onClick={() => setVenueDefaultsExpanded(!venueDefaultsExpanded)}
                    className="w-full p-4 flex items-center justify-between hover:bg-amber-100 transition-colors"
                  >
                    <h4 className="text-lg font-semibold text-amber-800 flex items-center gap-2">
                      <span>{venueDefaultsExpanded ? '▼' : '▶'}</span>
                      <span>Venue Default LAN Port Settings</span>
                      <span className="text-xs font-normal text-amber-600">
                        ({auditData.venue_lan_port_settings.length} models)
                      </span>
                    </h4>
                    <span className="text-xs text-amber-600">
                      {venueDefaultsExpanded ? 'Click to collapse' : 'Click to expand'}
                    </span>
                  </button>
                  {venueDefaultsExpanded && (
                    <div className="p-4 pt-0">
                      <p className="text-xs text-amber-600 mb-3">These settings are inherited by APs unless overridden at the AP level.</p>

                      {/* Filters */}
                      <div className="flex flex-wrap gap-4 mb-4 p-3 bg-white rounded border border-amber-100">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-gray-600">Environment:</span>
                          <div className="flex gap-1">
                            {(['all', 'indoor', 'outdoor'] as const).map(env => (
                              <button key={env} onClick={() => setFilterEnvironment(env)} className={`px-2 py-1 text-xs rounded ${filterEnvironment === env ? 'bg-amber-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
                                {env === 'all' ? 'All' : env.charAt(0).toUpperCase() + env.slice(1)}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-gray-600">Series:</span>
                          <div className="flex gap-1">
                            {(['all', 'H', 'R', 'T'] as const).map(s => (
                              <button key={s} onClick={() => setFilterSeries(s)} className={`px-2 py-1 text-xs rounded ${filterSeries === s ? 'bg-amber-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
                                {s === 'all' ? 'All' : `${s}-series`}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-gray-600">WiFi:</span>
                          <div className="flex gap-1">
                            {(['all', '7', '6E', '6', '5'] as const).map(w => (
                              <button key={w} onClick={() => setFilterWifi(w)} className={`px-2 py-1 text-xs rounded ${filterWifi === w ? 'bg-amber-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
                                {w === 'all' ? 'All' : `WiFi ${w}`}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="flex items-center ml-auto">
                          <span className="text-xs text-gray-500">Showing {filteredVenueLanSettings.length} of {auditData.venue_lan_port_settings.length}</span>
                        </div>
                      </div>

                      {filteredVenueLanSettings.length === 0 ? (
                        <p className="text-sm text-gray-500 italic text-center py-4">No models match filters.</p>
                      ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {filteredVenueLanSettings.map((modelSettings, idx) => {
                          const info = getModelInfo(modelSettings.model);
                          return (
                          <div key={idx} className="bg-white border border-amber-100 rounded p-3">
                            <div className="flex items-center justify-between mb-2">
                              <span className="font-semibold text-gray-800">{modelSettings.model}</span>
                              <span className="text-[10px] text-gray-400">{info.environment} / WiFi {info.wifi}</span>
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {(modelSettings.lanPorts || []).map((port: LanPortConfig, pIdx: number) => (
                                <span
                                  key={pIdx}
                                  className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium ${
                                    port.enabled
                                      ? 'bg-amber-100 text-amber-800'
                                      : 'bg-red-100 text-red-800'
                                  }`}
                                  title={port.enabled
                                    ? `Port ${port.portId}: VLAN ${port.untagId || 'default'} (${port.type || 'ACCESS'})`
                                    : `Port ${port.portId}: Disabled`}
                                >
                                  P{port.portId}
                                  {port.enabled && port.untagId && (
                                    <span className="ml-0.5">:{port.untagId}</span>
                                  )}
                                  {!port.enabled && (
                                    <span className="ml-0.5">x</span>
                                  )}
                                </span>
                              ))}
                            </div>
                            {modelSettings.poeMode && (
                              <div className="text-xs text-gray-500 mt-1">PoE: {modelSettings.poeMode}</div>
                            )}
                          </div>
                          );
                        })}
                      </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* AP Groups List */}
              <div className="space-y-4">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-lg font-semibold text-gray-800">
                    AP Groups Configuration
                  </h4>
                  {/* VLAN Legend */}
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    <span className="flex items-center gap-1">
                      <span className="px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded font-medium">V1</span>
                      VLAN
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded font-medium">V1*</span>
                      Override
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="text-amber-600">⚠️</span>
                      VLAN mismatch
                    </span>
                  </div>
                </div>

                {auditData.ap_groups.length === 0 ? (
                  <div className="text-center py-8 text-gray-500">
                    <p>No AP Groups found in this venue.</p>
                  </div>
                ) : (
                  auditData.ap_groups.map((group) => (
                    <div
                      key={group.ap_group_id}
                      className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
                    >
                      <div className="flex justify-between items-start mb-3">
                        <div>
                          <h5 className="text-lg font-bold text-gray-800">
                            {group.ap_group_name}
                          </h5>
                          {group.description && (
                            <p className="text-sm text-gray-500 mt-1">
                              {group.description}
                            </p>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <span className="px-3 py-1 bg-blue-100 text-blue-800 text-xs font-semibold rounded-full">
                            {group.total_aps} APs
                          </span>
                          <span className="px-3 py-1 bg-purple-100 text-purple-800 text-xs font-semibold rounded-full">
                            {group.total_ssids} SSIDs
                          </span>
                        </div>
                      </div>

                      <div className="grid grid-cols-3 gap-4 mt-3">
                        {/* APs Column */}
                        <div>
                          <h6 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                            📡 Access Points
                          </h6>
                          {group.total_aps === 0 ? (
                            <p className="text-xs text-gray-500 italic">
                              No APs assigned
                            </p>
                          ) : (
                            <ul className="space-y-1">
                              {(group.aps || []).map((ap, idx) => {
                                const showSerial = ap.serial && ap.serial !== ap.name;
                                return (
                                  <li
                                    key={idx}
                                    className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded flex items-center justify-between"
                                  >
                                    <span>
                                      {ap.name}
                                      {showSerial && (
                                        <span className="text-gray-400 ml-1">
                                          ({ap.serial})
                                        </span>
                                      )}
                                    </span>
                                    {ap.model && (
                                      <span className="text-gray-400 text-[10px] ml-1">
                                        {ap.model}
                                      </span>
                                    )}
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>

                        {/* Wired Ports Column */}
                        <div>
                          <h6 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                            🔌 Wired Ports
                          </h6>
                          {group.total_aps === 0 ? (
                            <p className="text-xs text-gray-500 italic">
                              No APs
                            </p>
                          ) : (
                            <ul className="space-y-1">
                              {(group.aps || []).map((ap, idx) => {
                                const portSettings = ap.lan_port_settings;
                                const portStatuses = ap.lan_port_statuses || [];
                                const ports = portSettings?.ports || [];

                                // Helper to get physical link status for a port
                                const getPhyLink = (portId: string) => {
                                  const portNum = portId.replace('LAN', '');
                                  const status = portStatuses.find(
                                    (s: LanPortStatus) => (s.port || s.id) === portNum || (s.port || s.id) === portId
                                  );
                                  return status?.phyLink || status?.physicalLink || null;
                                };

                                if (ports.length === 0) {
                                  return (
                                    <li
                                      key={idx}
                                      className="text-xs text-gray-400 bg-gray-50 px-2 py-1 rounded italic"
                                    >
                                      No LAN ports
                                    </li>
                                  );
                                }
                                return (
                                  <li
                                    key={idx}
                                    className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded flex items-center gap-1 flex-wrap"
                                  >
                                    {ports.map((port: LanPortConfig, pIdx: number) => {
                                      const phyLink = getPhyLink(port.portId);
                                      const isUp = phyLink?.toLowerCase() === 'up';
                                      return (
                                        <span
                                          key={pIdx}
                                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                            !port.enabled
                                              ? 'bg-red-100 text-red-800'
                                              : isUp
                                                ? 'bg-green-100 text-green-800 ring-1 ring-green-400'
                                                : 'bg-gray-100 text-gray-700'
                                          }`}
                                          title={`${port.portId}: ${!port.enabled ? 'Disabled' : `VLAN ${port.untagId || 'default'}${port.vlanMembers ? ` (tagged: ${port.vlanMembers})` : ''}`}${phyLink ? ` | Link: ${phyLink}` : ''}`}
                                        >
                                          {/* Physical link indicator */}
                                          {phyLink && (
                                            <span className={`w-1.5 h-1.5 rounded-full mr-1 ${isUp ? 'bg-green-500' : 'bg-gray-400'}`} />
                                          )}
                                          {port.portId.replace('LAN', 'P')}
                                          {port.enabled && port.untagId && (
                                            <span className="ml-0.5">:{port.untagId}</span>
                                          )}
                                          {!port.enabled && (
                                            <span className="ml-0.5">x</span>
                                          )}
                                        </span>
                                      );
                                    })}
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>

                        {/* SSIDs Column */}
                        <div>
                          <h6 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1">
                            📶 SSIDs
                          </h6>
                          {group.total_ssids === 0 ? (
                            <p className="text-xs text-gray-500 italic">
                              No SSIDs activated
                            </p>
                          ) : (
                            <ul className="space-y-1">
                              {(() => {
                                const lanPortVlans = getGroupLanPortVlans(group);
                                return group.ssids.map((ssid, idx) => {
                                  const mismatchStatus = checkVlanMismatch(ssid.effective_vlan, lanPortVlans);
                                  const hasMismatch = mismatchStatus === 'mismatch';
                                  const hasOverride = ssid.vlan_override !== null;

                                  return (
                                    <li
                                      key={idx}
                                      className={`text-xs px-2 py-1.5 rounded flex items-center justify-between gap-2 ${
                                        hasMismatch
                                          ? 'bg-amber-50 border border-amber-200'
                                          : 'bg-gray-50 text-gray-600'
                                      }`}
                                      title={
                                        hasMismatch
                                          ? `VLAN ${ssid.effective_vlan} not found on any enabled LAN port (have: ${Array.from(lanPortVlans).join(', ') || 'none'})`
                                          : hasOverride
                                            ? `Base VLAN: ${ssid.base_vlan ?? 'unset'}, Override: ${ssid.vlan_override}`
                                            : undefined
                                      }
                                    >
                                      <span className="truncate flex-1">
                                        {ssid.name}
                                        {ssid.is_all_ap_groups && (
                                          <span className="ml-1 text-purple-500" title="Activated on ALL AP Groups">*</span>
                                        )}
                                      </span>
                                      <span className="flex items-center gap-1 flex-shrink-0">
                                        {ssid.effective_vlan !== null && (
                                          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                                            hasMismatch
                                              ? 'bg-amber-100 text-amber-700'
                                              : hasOverride
                                                ? 'bg-blue-100 text-blue-700'
                                                : 'bg-gray-200 text-gray-600'
                                          }`}>
                                            V{ssid.effective_vlan}
                                            {hasOverride && <span className="ml-0.5">*</span>}
                                          </span>
                                        )}
                                        {hasMismatch && (
                                          <span className="text-amber-600" title="VLAN mismatch with LAN ports">⚠️</span>
                                        )}
                                      </span>
                                    </li>
                                  );
                                });
                              })()}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Modal Footer */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end border-t">
              <button
                onClick={() => setShowAuditModal(false)}
                className="px-6 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 font-semibold"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PerUnitSSID;
