import { useState, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import SingleVenueSelector from "@/components/SingleVenueSelector";
import JobMonitorModal from "@/components/JobMonitorModal";
import type { JobResult } from "@/components/JobMonitorModal";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// Port configuration types - same as Per-Unit SSID
type PortMode = 'ignore' | 'match' | 'specific' | 'disable' | 'uplink';

interface PortConfig {
  mode: PortMode;
  vlan?: number;
}

interface PortConfigMetadata {
  model_port_counts: Record<string, number>;
  model_uplink_ports: Record<string, string>;
  port_modes: string[];
  port_mode_descriptions: Record<string, string>;
  port_categories: {
    lan1_uplink: string[];
    lan2_uplink: string[];
    lan3_uplink: string[];
    lan5_uplink: string[];
  };
}

// CSV row - AP to VLAN mapping
interface ApVlanMapping {
  ap_identifier: string;
  vlan: number;
}

// Model port configurations - same structure as Per-Unit SSID
interface ModelPortConfigs {
  one_port_lan1_uplink: PortConfig[];  // 1-port models where LAN1 is uplink
  one_port_lan2_uplink: PortConfig[];  // 1-port models where LAN2 is uplink
  two_port: PortConfig[];              // H320/H350: LAN1, LAN2 (LAN3 is uplink)
  four_port: PortConfig[];             // H510/H550/H670: LAN1-4 (LAN5 is uplink)
}

const DEFAULT_PORT_CONFIGS: ModelPortConfigs = {
  one_port_lan1_uplink: [
    { mode: 'uplink' },   // LAN1 is uplink - protected
    { mode: 'ignore' },   // LAN2 is access port
  ],
  one_port_lan2_uplink: [
    { mode: 'ignore' },   // LAN1 is access port
    { mode: 'uplink' },   // LAN2 is uplink - protected
  ],
  two_port: [
    { mode: 'ignore' },   // LAN1
    { mode: 'ignore' },   // LAN2
    { mode: 'uplink' },   // LAN3 is uplink - protected
  ],
  four_port: [
    { mode: 'ignore' },   // LAN1
    { mode: 'ignore' },   // LAN2
    { mode: 'ignore' },   // LAN3
    { mode: 'ignore' },   // LAN4
    { mode: 'uplink' },   // LAN5 is uplink - protected
  ],
};

interface ConfigureResult {
  configured: any[];
  already_configured: any[];
  failed: any[];
  skipped: any[];
  summary: {
    total_requested: number;
    configured: number;
    already_configured: number;
    failed: number;
    skipped: number;
  };
  dry_run: boolean;
  job_id?: string;
}

// Batch configuration options
interface BatchConfig {
  max_concurrent_aps: number;
  max_concurrent_api_calls: number;
  poll_interval_seconds: number;
  max_poll_seconds: number;
}

const DEFAULT_BATCH_CONFIG: BatchConfig = {
  max_concurrent_aps: 20,
  max_concurrent_api_calls: 20,
  poll_interval_seconds: 3,
  max_poll_seconds: 120,
};


// Port config cell component - same as Per-Unit SSID
interface PortConfigCellProps {
  config: PortConfig;
  onChange: (newConfig: PortConfig) => void;
  disabled?: boolean;
}

function PortConfigCell({ config, onChange, disabled }: PortConfigCellProps) {
  const mode = config?.mode || 'ignore';
  const vlan = config?.vlan || 1;

  if (mode === 'uplink') {
    return (
      <div className="flex flex-col items-center">
        <span className="text-xs text-orange-600 font-medium px-2 py-1 bg-orange-100 rounded">
          UPLINK
        </span>
      </div>
    );
  }

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
        disabled={disabled}
        className={`text-xs border rounded px-1 py-1 w-20 ${getSelectClassName()} ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
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
          disabled={disabled}
          className={`w-16 text-xs border border-gray-300 rounded px-1 py-1 text-center ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          placeholder="VLAN"
        />
      )}
    </div>
  );
}

function APPortConfig() {
  const {
    activeControllerId,
    activeControllerType,
    activeControllerSubtype,
    controllers
  } = useAuth();

  // Venue selection
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Get effective tenant ID
  const activeController = controllers.find(c => c.id === activeControllerId);
  const needsEcSelection = activeControllerSubtype === "MSP";
  const effectiveTenantId = needsEcSelection
    ? null
    : (activeController?.r1_tenant_id || null);

  // Port config metadata from API
  const [portConfigMetadata, setPortConfigMetadata] = useState<PortConfigMetadata | null>(null);

  // Step 1: CSV input for AP → VLAN mappings
  const [csvInput, setCsvInput] = useState<string>("");
  const [apVlanMappings, setApVlanMappings] = useState<ApVlanMapping[]>([]);
  const [csvError, setCsvError] = useState<string>("");

  // Step 2: Model-based port configurations
  const [modelPortConfigs, setModelPortConfigs] = useState<ModelPortConfigs>(DEFAULT_PORT_CONFIGS);

  // Results
  const [configuring, setConfiguring] = useState(false);
  const [result, setResult] = useState<ConfigureResult | null>(null);
  const [error, setError] = useState("");

  // Batch config state (always use batch mode with workflow jobs)
  const [showBatchOptions, setShowBatchOptions] = useState(false);
  const [batchConfig, setBatchConfig] = useState<BatchConfig>(DEFAULT_BATCH_CONFIG);

  // Job monitor modal state (uses workflow job framework)
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [showJobModal, setShowJobModal] = useState(false);

  // Handle venue selection
  const handleVenueSelect = (id: string | null, venue: any) => {
    setVenueId(id);
    setVenueName(venue?.name || null);
    setResult(null);
  };

  // Fetch port metadata on mount
  useEffect(() => {
    const fetchMetadata = async () => {
      if (!activeControllerId) return;
      try {
        const response = await fetch(`${API_BASE_URL}/ap-port-config/${activeControllerId}/metadata`, {
          credentials: "include",
        });
        if (response.ok) {
          const data = await response.json();
          setPortConfigMetadata(data);
        }
      } catch (err) {
        console.error("Failed to fetch port metadata:", err);
      }
    };
    fetchMetadata();
  }, [activeControllerId]);

  // Parse CSV input
  const parseCSV = () => {
    setCsvError("");
    const lines = csvInput.trim().split('\n');
    const mappings: ApVlanMapping[] = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      // Skip header row if present
      if (i === 0 && (line.toLowerCase().includes('ap_name') || line.toLowerCase().includes('serial') || line.toLowerCase().includes('vlan'))) {
        continue;
      }

      const parts = line.split(',').map(p => p.trim());
      if (parts.length < 2) {
        setCsvError(`Line ${i + 1}: Expected 2 columns (ap_identifier, vlan)`);
        return;
      }

      const ap_identifier = parts[0];
      const vlan = parseInt(parts[1]);

      if (!ap_identifier) {
        setCsvError(`Line ${i + 1}: Missing AP identifier`);
        return;
      }

      if (isNaN(vlan) || vlan < 1 || vlan > 4094) {
        setCsvError(`Line ${i + 1}: Invalid VLAN "${parts[1]}" (must be 1-4094)`);
        return;
      }

      mappings.push({ ap_identifier, vlan });
    }

    if (mappings.length === 0) {
      setCsvError("No valid AP mappings found in CSV");
      return;
    }

    setApVlanMappings(mappings);
  };

  // Clear CSV
  const clearCSV = () => {
    setCsvInput("");
    setApVlanMappings([]);
    setCsvError("");
  };

  // Get port config for a model category
  const getPortConfigsForModel = (model: string): PortConfig[] | null => {
    const uplinkPort = portConfigMetadata?.model_uplink_ports[model];

    if (uplinkPort === 'LAN1') {
      return modelPortConfigs.one_port_lan1_uplink;
    } else if (uplinkPort === 'LAN2') {
      return modelPortConfigs.one_port_lan2_uplink;
    } else if (uplinkPort === 'LAN3') {
      return modelPortConfigs.two_port;
    } else if (uplinkPort === 'LAN5') {
      return modelPortConfigs.four_port;
    }
    return null; // No configurable ports
  };

  // Build configuration payload
  const buildConfigPayload = async () => {
    if (apVlanMappings.length === 0) {
      throw new Error("Please import AP→VLAN mappings first");
    }

    // Fetch APs to get their models
    const url = effectiveTenantId
      ? `${API_BASE_URL}/ap-port-config/${activeControllerId}/venue/${venueId}/aps?tenant_id=${effectiveTenantId}`
      : `${API_BASE_URL}/ap-port-config/${activeControllerId}/venue/${venueId}/aps`;

    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
      throw new Error(`Failed to fetch APs: ${response.statusText}`);
    }

    const data = await response.json();
    const venueAps = data.aps || [];

    // Create lookup by name and serial
    const apLookup = new Map<string, any>();
    for (const ap of venueAps) {
      if (ap.name) apLookup.set(ap.name.toLowerCase(), ap);
      if (ap.serial) apLookup.set(ap.serial.toLowerCase(), ap);
    }

    // Build individual AP configs
    const apConfigs: any[] = [];
    const notFound: string[] = [];

    for (const mapping of apVlanMappings) {
      const ap = apLookup.get(mapping.ap_identifier.toLowerCase());

      if (!ap) {
        notFound.push(mapping.ap_identifier);
        continue;
      }

      if (!ap.has_configurable_ports) {
        continue; // Skip APs without configurable ports
      }

      const portConfigs = getPortConfigsForModel(ap.model);
      if (!portConfigs) continue;

      // Build port config for this AP
      const apPortConfig: Record<string, any> = {
        ap_identifier: ap.name || ap.serial,
      };

      portConfigs.forEach((config, idx) => {
        const portKey = `lan${idx + 1}`;

        if (config.mode === 'ignore' || config.mode === 'uplink') {
          // Don't include - will be ignored
          return;
        }

        if (config.mode === 'match') {
          // Use VLAN from CSV mapping
          apPortConfig[portKey] = { mode: 'specific', vlan: mapping.vlan };
        } else if (config.mode === 'specific') {
          // Use specific VLAN from config
          apPortConfig[portKey] = { mode: 'specific', vlan: config.vlan };
        } else if (config.mode === 'disable') {
          apPortConfig[portKey] = { mode: 'disable' };
        }
      });

      // Only add if there are port configs
      if (Object.keys(apPortConfig).length > 1) {
        apConfigs.push(apPortConfig);
      }
    }

    if (notFound.length > 0) {
      console.warn(`APs not found in venue: ${notFound.join(', ')}`);
    }

    if (apConfigs.length === 0) {
      throw new Error("No APs with port configurations to apply. Check that APs exist in the venue and have configurable ports.");
    }

    return { ap_configs: apConfigs, not_found: notFound };
  };

  // Configure ports - always uses workflow job framework for consistency
  const handleConfigure = async (dryRun: boolean = false) => {
    if (!venueId || !activeControllerId) {
      setError("Please select a venue first");
      return;
    }
    if (needsEcSelection && !effectiveTenantId) {
      setError("MSP controllers require an EC/Tenant selection");
      return;
    }

    setConfiguring(true);
    setError("");
    setResult(null);

    try {
      const { ap_configs, not_found } = await buildConfigPayload();

      if (dryRun) {
        // Dry run uses sequential endpoint (no job needed)
        const response = await fetch(`${API_BASE_URL}/ap-port-config/${activeControllerId}/configure`, {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            venue_id: venueId,
            ...(effectiveTenantId && { tenant_id: effectiveTenantId }),
            ap_configs,
            dry_run: true
          }),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `Configuration failed: ${response.statusText}`);
        }

        const data = await response.json();
        if (not_found.length > 0) {
          data.not_found = not_found;
        }
        setResult(data);
        setConfiguring(false);

      } else {
        // Apply uses batch endpoint with workflow job framework
        const response = await fetch(`${API_BASE_URL}/ap-port-config/${activeControllerId}/configure-batch`, {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            venue_id: venueId,
            ...(effectiveTenantId && { tenant_id: effectiveTenantId }),
            ap_configs,
            batch_config: batchConfig,
            dry_run: false
          }),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `Job failed to start: ${response.statusText}`);
        }

        const data = await response.json();

        // Show job monitor modal
        setCurrentJobId(data.job_id);
        setShowJobModal(true);
        setConfiguring(false);

        // Log not_found for debugging
        if (not_found.length > 0) {
          console.log('APs not found in venue:', not_found);
        }
      }

    } catch (err: any) {
      setError(err.message || "Configuration failed");
      setConfiguring(false);
    }
  };

  // Handle job modal close
  const handleCloseJobModal = () => {
    setShowJobModal(false);
  };

  // Handle job completion from modal
  const handleJobComplete = (jobResult: JobResult) => {
    // Convert job result to ConfigureResult format if needed
    if (jobResult.summary) {
      setResult({
        configured: [],
        already_configured: [],
        failed: [],
        skipped: [],
        summary: {
          total_requested: jobResult.summary.total_aps || 0,
          configured: jobResult.summary.configured || 0,
          already_configured: jobResult.summary.already_correct || 0,
          failed: jobResult.summary.failed || 0,
          skipped: jobResult.summary.skipped || 0,
        },
        dry_run: false,
        job_id: jobResult.job_id,
      });
    }
  };

  // Check if any ports are configured (not all ignore/uplink)
  const hasPortConfig = () => {
    const allConfigs = [
      ...modelPortConfigs.one_port_lan1_uplink,
      ...modelPortConfigs.one_port_lan2_uplink,
      ...modelPortConfigs.two_port,
      ...modelPortConfigs.four_port,
    ];
    return allConfigs.some(c => c.mode === 'match' || c.mode === 'specific' || c.mode === 'disable');
  };

  const canApply = venueId && apVlanMappings.length > 0 && hasPortConfig();

  return (
    <div className="container mx-auto py-6 px-4">
      <h1 className="text-2xl font-bold mb-6">AP Port Configuration</h1>
      <p className="text-gray-600 mb-6">
        Configure LAN port VLANs on APs based on per-AP VLAN mappings. This standalone tool uses
        the same port configuration logic as Per-Unit SSID.
      </p>

      {/* Venue Selection */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4">1. Select Venue</h2>

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

        {venueId && venueName && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
            <span className="text-sm font-medium text-green-800">
              Selected: {venueName}
            </span>
          </div>
        )}
      </div>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
          {error}
        </div>
      )}

      {venueId && (
        <>
          {/* Step 1: CSV Input */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="text-lg font-semibold mb-4">2. Import AP → VLAN Mappings</h2>

            <p className="text-sm text-gray-600 mb-3">
              Paste CSV with two columns: <code className="bg-gray-100 px-1 rounded">ap_name_or_serial, vlan</code>
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <textarea
                  value={csvInput}
                  onChange={(e) => setCsvInput(e.target.value)}
                  placeholder="ap_name_or_serial,vlan&#10;Unit-101,101&#10;Unit-102,102&#10;Unit-103,103"
                  className="w-full h-48 px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                {csvError && (
                  <p className="text-sm text-red-600 mt-1">{csvError}</p>
                )}
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={parseCSV}
                    disabled={!csvInput.trim()}
                    className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 text-sm"
                  >
                    Parse CSV
                  </button>
                  <button
                    onClick={clearCSV}
                    className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm"
                  >
                    Clear
                  </button>
                </div>
              </div>

              <div>
                {apVlanMappings.length > 0 ? (
                  <div className="border border-green-200 bg-green-50 rounded-lg p-3">
                    <div className="text-sm font-medium text-green-800 mb-2">
                      {apVlanMappings.length} AP mapping(s) loaded
                    </div>
                    <div className="max-h-40 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-left text-gray-600">
                            <th className="pb-1">AP</th>
                            <th className="pb-1">VLAN</th>
                          </tr>
                        </thead>
                        <tbody>
                          {apVlanMappings.slice(0, 10).map((m, i) => (
                            <tr key={i} className="border-t border-green-200">
                              <td className="py-1 font-mono">{m.ap_identifier}</td>
                              <td className="py-1">{m.vlan}</td>
                            </tr>
                          ))}
                          {apVlanMappings.length > 10 && (
                            <tr className="border-t border-green-200 text-gray-500">
                              <td colSpan={2} className="py-1">
                                ... and {apVlanMappings.length - 10} more
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : (
                  <div className="border border-gray-200 bg-gray-50 rounded-lg p-3 text-sm text-gray-500">
                    No mappings loaded yet. Paste CSV and click "Parse CSV".
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Step 2: Model Port Configuration */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="text-lg font-semibold mb-4">3. Configure Ports by Model</h2>

            <p className="text-sm text-gray-600 mb-3">
              Configure what each port should do per model category:
            </p>
            <ul className="text-xs text-gray-500 mb-4 list-disc list-inside">
              <li><strong>Ignore</strong> - Don't change this port</li>
              <li><strong>Match</strong> - Use the VLAN from the CSV mapping for each AP</li>
              <li><strong>Specific</strong> - Use a specific VLAN (same for all APs)</li>
              <li><strong>Disable</strong> - Disable the port</li>
            </ul>

            <div className="overflow-x-auto">
              <table className="w-full text-sm border border-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium text-gray-700">Model Category</th>
                    <th className="px-2 py-2 text-center font-medium text-gray-700 w-24">LAN1</th>
                    <th className="px-2 py-2 text-center font-medium text-gray-700 w-24">LAN2</th>
                    <th className="px-2 py-2 text-center font-medium text-gray-700 w-24">LAN3</th>
                    <th className="px-2 py-2 text-center font-medium text-gray-700 w-24">LAN4</th>
                    <th className="px-2 py-2 text-center font-medium text-gray-700 w-24">LAN5</th>
                  </tr>
                </thead>
                <tbody>
                  {/* 1-Port Models (LAN1 Uplink) */}
                  <tr className="border-t">
                    <td className="px-3 py-3">
                      <div className="text-sm font-medium">1-Port (LAN1 Uplink)</div>
                      <div className="text-xs text-gray-500">
                        {portConfigMetadata?.port_categories?.lan1_uplink?.join(', ') || 'R650, R750, R850, T350...'}
                      </div>
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell config={modelPortConfigs.one_port_lan1_uplink[0]} onChange={() => {}} disabled />
                    </td>
                    <td className="px-2 py-3 text-center">
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
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                  </tr>

                  {/* 1-Port Models (LAN2 Uplink) */}
                  <tr className="border-t">
                    <td className="px-3 py-3">
                      <div className="text-sm font-medium">1-Port (LAN2 Uplink)</div>
                      <div className="text-xs text-gray-500">
                        {portConfigMetadata?.port_categories?.lan2_uplink?.join(', ') || 'R550, R560'}
                      </div>
                    </td>
                    <td className="px-2 py-3 text-center">
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
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell config={modelPortConfigs.one_port_lan2_uplink[1]} onChange={() => {}} disabled />
                    </td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                  </tr>

                  {/* 2-Port Models */}
                  <tr className="border-t">
                    <td className="px-3 py-3">
                      <div className="text-sm font-medium">2-Port</div>
                      <div className="text-xs text-gray-500">
                        {portConfigMetadata?.port_categories?.lan3_uplink?.join(', ') || 'H320, H350, T750'}
                      </div>
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.two_port[0]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.two_port = [...updated.two_port];
                          updated.two_port[0] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.two_port[1]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.two_port = [...updated.two_port];
                          updated.two_port[1] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell config={modelPortConfigs.two_port[2]} onChange={() => {}} disabled />
                    </td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                    <td className="px-2 py-3 text-center text-gray-300">-</td>
                  </tr>

                  {/* 4-Port Models */}
                  <tr className="border-t">
                    <td className="px-3 py-3">
                      <div className="text-sm font-medium">4-Port</div>
                      <div className="text-xs text-gray-500">
                        {portConfigMetadata?.port_categories?.lan5_uplink?.join(', ') || 'H510, H550, H670'}
                      </div>
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.four_port[0]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.four_port = [...updated.four_port];
                          updated.four_port[0] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.four_port[1]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.four_port = [...updated.four_port];
                          updated.four_port[1] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.four_port[2]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.four_port = [...updated.four_port];
                          updated.four_port[2] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell
                        config={modelPortConfigs.four_port[3]}
                        onChange={(newConfig) => {
                          const updated = { ...modelPortConfigs };
                          updated.four_port = [...updated.four_port];
                          updated.four_port[3] = newConfig;
                          setModelPortConfigs(updated);
                        }}
                      />
                    </td>
                    <td className="px-2 py-3 text-center">
                      <PortConfigCell config={modelPortConfigs.four_port[4]} onChange={() => {}} disabled />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p className="text-xs text-gray-500 mt-3">
              <strong>Note:</strong> 0-Port models (ceiling APs with only uplink) have no configurable ports and are skipped.
            </p>
          </div>

          {/* Step 3: Apply */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h2 className="text-lg font-semibold mb-4">4. Apply Configuration</h2>

            {/* Advanced Options (collapsible) */}
            <div className="mb-4">
              <button
                type="button"
                onClick={() => setShowBatchOptions(!showBatchOptions)}
                className="text-sm text-gray-600 hover:text-gray-800 flex items-center gap-1"
              >
                {showBatchOptions ? '▼' : '▶'} Advanced Options
              </button>

              {showBatchOptions && (
                <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded-lg grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Max Concurrent APs
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      value={batchConfig.max_concurrent_aps}
                      onChange={(e) => setBatchConfig({
                        ...batchConfig,
                        max_concurrent_aps: parseInt(e.target.value) || 20
                      })}
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Max API Calls/sec
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="50"
                      value={batchConfig.max_concurrent_api_calls}
                      onChange={(e) => setBatchConfig({
                        ...batchConfig,
                        max_concurrent_api_calls: parseInt(e.target.value) || 20
                      })}
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Poll Interval (sec)
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="10"
                      value={batchConfig.poll_interval_seconds}
                      onChange={(e) => setBatchConfig({
                        ...batchConfig,
                        poll_interval_seconds: parseInt(e.target.value) || 3
                      })}
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Max Poll Time (sec)
                    </label>
                    <input
                      type="number"
                      min="30"
                      max="600"
                      value={batchConfig.max_poll_seconds}
                      onChange={(e) => setBatchConfig({
                        ...batchConfig,
                        max_poll_seconds: parseInt(e.target.value) || 120
                      })}
                      className="w-full px-2 py-1 text-sm border border-gray-300 rounded"
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="flex gap-4">
              <button
                onClick={() => handleConfigure(true)}
                disabled={configuring || !canApply}
                className="px-4 py-2 bg-yellow-500 text-white rounded hover:bg-yellow-600 disabled:opacity-50"
              >
                {configuring ? "Processing..." : "Preview Changes (Dry Run)"}
              </button>
              <button
                onClick={() => handleConfigure(false)}
                disabled={configuring || !canApply}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {configuring ? "Starting..." : `Apply Configuration (${apVlanMappings.length} APs)`}
              </button>
            </div>

            {!canApply && (
              <p className="text-sm text-gray-500 mt-3">
                {!apVlanMappings.length
                  ? "Import AP→VLAN mappings first (Step 2)"
                  : !hasPortConfig()
                  ? "Configure at least one port to Match, Specific, or Disable (Step 3)"
                  : ""}
              </p>
            )}
          </div>

          {/* Results */}
          {result && (
            <div className={`rounded-lg shadow p-4 mb-6 ${result.dry_run ? 'bg-yellow-50 border border-yellow-200' : 'bg-green-50 border border-green-200'}`}>
              <h2 className="text-lg font-semibold mb-4">
                {result.dry_run ? "Preview Results (Dry Run)" : "Configuration Results"}
              </h2>

              <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
                <div className="text-center">
                  <div className="text-2xl font-bold text-green-600">{result.summary.configured}</div>
                  <div className="text-sm text-gray-600">Configured</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-blue-600">{result.summary.already_configured}</div>
                  <div className="text-sm text-gray-600">Already Correct</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-red-600">{result.summary.failed}</div>
                  <div className="text-sm text-gray-600">Failed</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-gray-600">{result.summary.skipped}</div>
                  <div className="text-sm text-gray-600">Skipped</div>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold">{result.summary.total_requested}</div>
                  <div className="text-sm text-gray-600">Total</div>
                </div>
              </div>

              {(result as any).not_found?.length > 0 && (
                <div className="mt-4">
                  <h3 className="font-medium text-orange-700 mb-2">APs Not Found in Venue ({(result as any).not_found.length}):</h3>
                  <p className="text-sm text-orange-600">
                    {(result as any).not_found.slice(0, 10).join(', ')}
                    {(result as any).not_found.length > 10 && ` ... and ${(result as any).not_found.length - 10} more`}
                  </p>
                </div>
              )}

              {result.configured.length > 0 && (
                <div className="mt-4">
                  <h3 className="font-medium text-green-700 mb-2">
                    {result.dry_run ? "APs to Configure:" : "Configured APs:"} ({result.configured.length})
                  </h3>
                  <div className="max-h-48 overflow-y-auto">
                    <table className="w-full text-sm border border-green-200">
                      <thead className="bg-green-100 sticky top-0">
                        <tr>
                          <th className="px-2 py-1 text-left">AP</th>
                          <th className="px-2 py-1 text-left">Model</th>
                          <th className="px-2 py-1 text-left">Port Changes</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.configured.map((ap: any, i: number) => (
                          <tr key={i} className="border-t border-green-200">
                            <td className="px-2 py-1 font-mono text-xs">{ap.ap_identifier}</td>
                            <td className="px-2 py-1 text-xs">{ap.ap_model || '-'}</td>
                            <td className="px-2 py-1 text-xs">
                              {ap.ports_configured?.map((p: any, j: number) => (
                                <span key={j} className="inline-block mr-2 px-1 bg-green-200 rounded">
                                  {p.port_id}: {p.action === 'disable' ? 'disable' : `VLAN ${p.vlan}`}
                                </span>
                              )) || '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {result.already_configured.length > 0 && (
                <div className="mt-4">
                  <h3 className="font-medium text-blue-700 mb-2">Already Correct ({result.already_configured.length}):</h3>
                  <div className="text-sm text-blue-600 max-h-32 overflow-y-auto">
                    {result.already_configured.map((ap: any, i: number) => (
                      <div key={i} className="inline-block mr-2 mb-1 px-2 py-1 bg-blue-100 rounded text-xs">
                        {ap.ap_identifier}
                        {ap.ports_already_correct?.length > 0 && (
                          <span className="ml-1 text-blue-500">
                            ({ap.ports_already_correct.map((p: any) => p.port_id).join(', ')})
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.failed.length > 0 && (
                <div className="mt-4">
                  <h3 className="font-medium text-red-700 mb-2">Failed APs:</h3>
                  <ul className="text-sm text-red-600 list-disc list-inside">
                    {result.failed.map((ap: any, i: number) => (
                      <li key={i}>{ap.ap_identifier}: {ap.errors?.join(', ') || ap.skipped_reason || 'Unknown error'}</li>
                    ))}
                  </ul>
                </div>
              )}

              {result.skipped.length > 0 && (
                <div className="mt-4">
                  <h3 className="font-medium text-gray-700 mb-2">Skipped APs:</h3>
                  <ul className="text-sm text-gray-600 list-disc list-inside max-h-32 overflow-y-auto">
                    {result.skipped.map((ap: any, i: number) => (
                      <li key={i}>{ap.ap_identifier}: {ap.skipped_reason || 'No configurable ports'}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Job Monitor Modal */}
      {currentJobId && (
        <JobMonitorModal
          jobId={currentJobId}
          isOpen={showJobModal}
          onClose={handleCloseJobModal}
          onJobComplete={handleJobComplete}
        />
      )}
    </div>
  );
}

export default APPortConfig;
