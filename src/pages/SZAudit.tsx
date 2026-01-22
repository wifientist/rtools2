import { useState, useMemo, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { ChevronDown, ChevronRight, Loader2, AlertCircle, CheckCircle2, Server, Wifi, Router, HardDrive, Layers, Info, Download, Link2, X } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Types matching the backend schemas
interface ModelDistribution {
  model: string;
  count: number;
}

interface FirmwareDistribution {
  version: string;
  count: number;
}

interface ApStatusBreakdown {
  online: number;
  offline: number;
  flagged: number;
  total: number;
}

interface WlanSummary {
  id: string;
  name: string;
  ssid: string;
  auth_type: string;
  encryption: string;
  vlan: number | null;
}

interface ApGroupSummary {
  id: string;
  name: string;
  ap_count: number;
}

interface WlanGroupSummary {
  id: string;
  name: string;
  wlan_count: number;
}

interface SwitchGroupSummary {
  id: string;
  name: string;
  switch_count: number;
  switches_online: number;
  switches_offline: number;
  firmware_versions?: FirmwareDistribution[];
}

interface ZoneAudit {
  zone_id: string;
  zone_name: string;
  domain_id: string;
  domain_name: string;
  external_ips: string[];
  ap_status: ApStatusBreakdown;
  ap_model_distribution: ModelDistribution[];
  ap_groups: ApGroupSummary[];
  ap_firmware_distribution: FirmwareDistribution[];
  wlan_count: number;
  wlan_groups: WlanGroupSummary[];
  wlans: WlanSummary[];
  wlan_type_breakdown: Record<string, number>;
  matched_switch_groups: SwitchGroupSummary[];  // Switch groups matched by name to this zone
}

interface DomainAudit {
  domain_id: string;
  domain_name: string;
  parent_domain_id: string | null;
  parent_domain_name: string | null;
  zone_count: number;
  total_aps: number;
  total_wlans: number;
  switch_groups: SwitchGroupSummary[];
  total_switches: number;
  switch_firmware_distribution: FirmwareDistribution[];
  children: DomainAudit[];
}

interface SZAuditResult {
  controller_id: number;
  controller_name: string;
  host: string;
  timestamp: string;
  cluster_ip: string | null;
  controller_firmware: string | null;
  domains: DomainAudit[];
  zones: ZoneAudit[];
  total_domains: number;
  total_zones: number;
  total_aps: number;
  total_wlans: number;
  total_switches: number;
  ap_model_summary: ModelDistribution[];
  ap_firmware_summary: FirmwareDistribution[];
  switch_firmware_summary: FirmwareDistribution[];
  wlan_type_summary: Record<string, number>;
  error: string | null;
  partial_errors: string[];
}

// Extended zone type with controller info for the flat table
interface ZoneRowData extends ZoneAudit {
  controller_name: string;
  controller_id: number;
  controller_firmware: string | null;
  // Switch info from domain level (fallback if no matched switch groups)
  domain_switches: number;
  domain_switch_groups: SwitchGroupSummary[];
  // Best switch groups to show: matched first, then domain-level
  effective_switch_groups: SwitchGroupSummary[];
}

// Component: Stat Card
function StatCard({ label, value, icon: Icon }: { label: string; value: number | string; icon: React.ElementType }) {
  return (
    <div className="bg-white rounded-lg shadow p-4 flex flex-col items-center">
      <Icon className="w-6 h-6 text-gray-500 mb-1" />
      <div className="text-2xl font-bold text-gray-900">{typeof value === 'number' ? value.toLocaleString() : value}</div>
      <div className="text-xs text-gray-500 uppercase tracking-wider">{label}</div>
    </div>
  );
}

// Component: Distribution Badges (compact)
function DistributionBadges({ items, colorClass = "bg-blue-100 text-blue-800", maxShow = 3 }: { items: { label: string; count: number }[]; colorClass?: string; maxShow?: number }) {
  if (items.length === 0) return <span className="text-gray-400 text-xs">-</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.slice(0, maxShow).map((item, i) => (
        <span key={i} className={`px-1.5 py-0.5 rounded text-xs ${colorClass}`}>
          {item.label} ({item.count})
        </span>
      ))}
      {items.length > maxShow && (
        <span className="px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-600">
          +{items.length - maxShow}
        </span>
      )}
    </div>
  );
}

// Component: AP Status Cell
function ApStatusCell({ status }: { status: ApStatusBreakdown }) {
  if (status.total === 0) return <span className="text-gray-400 text-sm">0</span>;
  return (
    <div className="text-sm">
      <span className="font-medium">{status.total}</span>
      <span className="text-xs ml-1">
        (<span className="text-green-600">{status.online}</span>
        {status.offline > 0 && <span className="text-red-600">/{status.offline}</span>})
      </span>
    </div>
  );
}

// Component: WLAN Types Cell
function WlanTypesCell({ breakdown }: { breakdown: Record<string, number> }) {
  const types = Object.entries(breakdown);
  if (types.length === 0) return <span className="text-gray-400 text-xs">-</span>;

  return (
    <div className="flex flex-wrap gap-1">
      {types.slice(0, 3).map(([type, count], i) => (
        <span key={i} className="px-1.5 py-0.5 rounded text-xs bg-purple-100 text-purple-800">
          {type} ({count})
        </span>
      ))}
      {types.length > 3 && (
        <span className="px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-600">+{types.length - 3}</span>
      )}
    </div>
  );
}

// Type for zone-to-switch-group mappings (stored per controller in localStorage)
interface ZoneSwitchGroupMapping {
  [zoneId: string]: string;  // zoneId -> switchGroupId
}

// Helper to load/save mappings from localStorage
function loadMappings(controllerId: number): ZoneSwitchGroupMapping {
  try {
    const saved = localStorage.getItem(`sz_audit_sg_mapping_${controllerId}`);
    return saved ? JSON.parse(saved) : {};
  } catch {
    return {};
  }
}

function saveMappings(controllerId: number, mappings: ZoneSwitchGroupMapping) {
  localStorage.setItem(`sz_audit_sg_mapping_${controllerId}`, JSON.stringify(mappings));
}

// Component: Filtered Switch Group Selector for a single zone
function SwitchGroupSelector({
  currentMapping,
  availableSwitchGroups,
  allSwitchGroups,
  onSelect,
  onClear
}: {
  currentMapping: string | null;
  availableSwitchGroups: SwitchGroupSummary[];
  allSwitchGroups: SwitchGroupSummary[];
  onSelect: (sgId: string) => void;
  onClear: () => void;
}) {
  const [filter, setFilter] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  const mappedSg = currentMapping ? allSwitchGroups.find(sg => sg.id === currentMapping) : null;

  // Filter available switch groups by search term
  const filteredGroups = availableSwitchGroups.filter(sg =>
    sg.name.toLowerCase().includes(filter.toLowerCase())
  );

  if (mappedSg) {
    // Show the mapped switch group with X to clear
    return (
      <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded px-2 py-1.5">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-green-800 truncate">{mappedSg.name}</div>
          <div className="text-xs text-green-600">
            {mappedSg.switch_count} switches ({mappedSg.switches_online} online)
          </div>
        </div>
        <button
          onClick={onClear}
          className="p-1 hover:bg-green-100 rounded text-green-600 hover:text-green-800"
          title="Remove mapping"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="relative">
      <input
        type="text"
        value={filter}
        onChange={(e) => {
          setFilter(e.target.value);
          setIsOpen(true);
        }}
        onFocus={() => setIsOpen(true)}
        placeholder="Type to search..."
        className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      />
      {isOpen && (
        <>
          {/* Backdrop to close dropdown */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => {
              setIsOpen(false);
              setFilter('');
            }}
          />
          {/* Dropdown */}
          <div className="absolute z-20 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-auto">
            {filteredGroups.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-500 italic">
                {availableSwitchGroups.length === 0 ? 'No switch groups available' : 'No matches'}
              </div>
            ) : (
              filteredGroups.map(sg => (
                <button
                  key={sg.id}
                  onClick={() => {
                    onSelect(sg.id);
                    setIsOpen(false);
                    setFilter('');
                  }}
                  className="w-full text-left px-3 py-2 hover:bg-blue-50 border-b border-gray-100 last:border-0"
                >
                  <div className="text-sm font-medium text-gray-900">{sg.name}</div>
                  <div className="text-xs text-gray-500">
                    {sg.switch_count} switches • {sg.switches_online} online, {sg.switches_offline} offline
                  </div>
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Component: Switch Group Mapper Modal
function SwitchGroupMapper({
  isOpen,
  onClose,
  zones,
  switchGroups,
  mappings,
  onMappingsChange,
  controllerId
}: {
  isOpen: boolean;
  onClose: () => void;
  zones: { zone_id: string; zone_name: string; domain_name: string }[];
  switchGroups: SwitchGroupSummary[];
  mappings: ZoneSwitchGroupMapping;
  onMappingsChange: (mappings: ZoneSwitchGroupMapping) => void;
  controllerId: number;
}) {
  if (!isOpen) return null;

  const handleMapZone = (zoneId: string, switchGroupId: string) => {
    const newMappings = { ...mappings, [zoneId]: switchGroupId };
    onMappingsChange(newMappings);
    saveMappings(controllerId, newMappings);
  };

  const handleClearZone = (zoneId: string) => {
    const newMappings = { ...mappings };
    delete newMappings[zoneId];
    onMappingsChange(newMappings);
    saveMappings(controllerId, newMappings);
  };

  const clearAllMappings = () => {
    onMappingsChange({});
    saveMappings(controllerId, {});
  };

  // Get which switch groups are already mapped
  const mappedSgIds = new Set(Object.values(mappings));

  // Available switch groups (not yet mapped)
  const availableSwitchGroups = switchGroups.filter(sg => !mappedSgIds.has(sg.id));

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-6xl w-full max-h-[80vh] overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Map Switch Groups to Zones</h3>
            <p className="text-sm text-gray-500">
              {Object.keys(mappings).length} of {zones.length} zones mapped
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={clearAllMappings}
              className="px-3 py-1 text-sm text-red-600 hover:bg-red-50 rounded"
            >
              Clear All
            </button>
            <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded">
              <X className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        <div className="flex max-h-[60vh]">
          {/* Left: Zones */}
          <div className="flex-1 overflow-auto p-4 border-r border-gray-200">
            <h4 className="text-xs font-medium text-gray-500 uppercase mb-3">Zones</h4>
            <div className="space-y-2">
              {zones.map(zone => (
                <div key={zone.zone_id} className="flex items-start gap-3 p-2 bg-gray-50 rounded-lg">
                  <div className="flex-shrink-0 w-40">
                    <div className="text-sm font-medium text-gray-900 truncate" title={zone.zone_name}>{zone.zone_name}</div>
                    <div className="text-xs text-gray-500 truncate">{zone.domain_name}</div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <SwitchGroupSelector
                      currentMapping={mappings[zone.zone_id] || null}
                      availableSwitchGroups={availableSwitchGroups}
                      allSwitchGroups={switchGroups}
                      onSelect={(sgId) => handleMapZone(zone.zone_id, sgId)}
                      onClear={() => handleClearZone(zone.zone_id)}
                    />
                  </div>
                </div>
              ))}
            </div>

            {zones.length === 0 && (
              <div className="text-center py-8 text-gray-500">No zones found</div>
            )}
          </div>

          {/* Right: Available Switch Groups (Bullpen) */}
          <div className="w-72 flex-shrink-0 overflow-auto p-4 bg-gray-50">
            <h4 className="text-xs font-medium text-gray-500 uppercase mb-3">
              Available ({availableSwitchGroups.length})
            </h4>
            {availableSwitchGroups.length === 0 ? (
              <div className="text-center py-8 text-gray-400 text-sm">
                All switch groups mapped
              </div>
            ) : (
              <div className="space-y-2">
                {availableSwitchGroups.map(sg => (
                  <div
                    key={sg.id}
                    className="p-2 bg-white border border-gray-200 rounded-lg"
                  >
                    <div className="text-sm font-medium text-gray-900">{sg.name}</div>
                    <div className="text-xs text-gray-500">
                      {sg.switch_count} switches • {sg.switches_online} online
                      {sg.switches_offline > 0 && (
                        <span className="text-red-500"> • {sg.switches_offline} offline</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {switchGroups.length === 0 && (
              <div className="text-center py-8 text-gray-500 text-sm">No switch groups</div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-end px-6 py-4 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

// Component: Expandable Zone Row
function ZoneRow({ zone, expanded, onToggle }: { zone: ZoneRowData; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr
        className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
        onClick={onToggle}
      >
        {/* Controller */}
        <td className="px-3 py-2 text-sm">
          <div className="font-medium text-gray-900">{zone.controller_name}</div>
          {zone.controller_firmware && (
            <div className="text-xs text-gray-500">v{zone.controller_firmware}</div>
          )}
        </td>

        {/* Domain */}
        <td className="px-3 py-2 text-sm text-gray-700">
          {zone.domain_name}
        </td>

        {/* Zone */}
        <td className="px-3 py-2">
          <div className="flex items-center gap-1">
            {expanded ? (
              <ChevronDown className="w-4 h-4 text-gray-400" />
            ) : (
              <ChevronRight className="w-4 h-4 text-gray-400" />
            )}
            <span className="font-medium text-gray-900 text-sm">{zone.zone_name}</span>
          </div>
        </td>

        {/* APs */}
        <td className="px-3 py-2">
          <ApStatusCell status={zone.ap_status} />
        </td>

        {/* AP Models */}
        <td className="px-3 py-2">
          <DistributionBadges
            items={zone.ap_model_distribution.map(m => ({ label: m.model, count: m.count }))}
            colorClass="bg-green-100 text-green-800"
            maxShow={2}
          />
        </td>

        {/* AP Firmware */}
        <td className="px-3 py-2">
          <DistributionBadges
            items={zone.ap_firmware_distribution.map(f => ({ label: f.version, count: f.count }))}
            colorClass="bg-orange-100 text-orange-800"
            maxShow={2}
          />
        </td>

        {/* External IPs */}
        <td className="px-3 py-2 text-sm text-center">
          {zone.external_ips && zone.external_ips.length > 0 ? (
            <span className="px-1.5 py-0.5 rounded text-xs bg-cyan-100 text-cyan-800">
              {zone.external_ips.length}
            </span>
          ) : (
            <span className="text-gray-400 text-xs">-</span>
          )}
        </td>

        {/* WLANs */}
        <td className="px-3 py-2 text-sm">
          <span className="font-medium">{zone.wlan_count}</span>
          <span className="text-gray-500 text-xs ml-1">
            ({zone.wlan_groups.length} grp)
          </span>
        </td>

        {/* WLAN Types */}
        <td className="px-3 py-2">
          <WlanTypesCell breakdown={zone.wlan_type_breakdown} />
        </td>

        {/* Switches - show mapped switch groups only */}
        <td className="px-3 py-2 text-sm">
          {zone.effective_switch_groups.length > 0 ? (
            <div>
              <span className="font-medium">
                {zone.effective_switch_groups.reduce((sum, sg) => sum + sg.switch_count, 0)}
              </span>
              <span className="text-gray-500 text-xs ml-1">
                ({zone.effective_switch_groups.length} grp)
              </span>
            </div>
          ) : (
            <span className="text-gray-400">-</span>
          )}
        </td>
      </tr>

      {/* Expanded Details Row */}
      {expanded && (
        <tr className="bg-gray-50 border-b border-gray-200">
          <td colSpan={10} className="px-6 py-4">
            <div className="grid grid-cols-4 gap-6 text-sm">
              {/* AP Details */}
              <div>
                <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-1">
                  <Wifi className="w-4 h-4" /> AP Details
                </h4>
                <div className="space-y-2">
                  <div>
                    <span className="text-gray-500">Groups:</span>
                    <span className="ml-2">
                      {zone.ap_groups.length > 0
                        ? zone.ap_groups.map(g => g.name).join(', ')
                        : '-'
                      }
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Firmware:</span>
                    <div className="mt-1">
                      <DistributionBadges
                        items={zone.ap_firmware_distribution.map(f => ({ label: f.version, count: f.count }))}
                        colorClass="bg-orange-100 text-orange-800"
                        maxShow={4}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* WLAN Details */}
              <div>
                <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-1">
                  <Router className="w-4 h-4" /> WLAN Details
                </h4>
                {zone.wlans.length > 0 ? (
                  <div className="space-y-1 max-h-32 overflow-y-auto">
                    {zone.wlans.map(wlan => (
                      <div key={wlan.id} className="flex items-center gap-2 text-xs">
                        <span className="font-medium">{wlan.name}</span>
                        <span className="text-gray-500">({wlan.auth_type})</span>
                        {wlan.vlan && <span className="text-gray-400">VLAN {wlan.vlan}</span>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-gray-400">No WLANs</span>
                )}
              </div>

              {/* Switch Groups - manually mapped */}
              <div>
                <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-1">
                  <HardDrive className="w-4 h-4" /> Switch Group
                  {zone.effective_switch_groups.length > 0 && (
                    <span className="text-xs font-normal text-blue-600 ml-1">(mapped)</span>
                  )}
                </h4>
                {zone.effective_switch_groups.length > 0 ? (
                  <div className="space-y-1 max-h-32 overflow-y-auto">
                    {zone.effective_switch_groups.map(sg => (
                      <div key={sg.id} className="flex items-center gap-2 text-xs">
                        <span className="font-medium">{sg.name}</span>
                        <span className="text-gray-500">
                          ({sg.switch_count} switch{sg.switch_count !== 1 ? 'es' : ''})
                        </span>
                        {sg.switch_count > 0 && (
                          <span className="text-xs">
                            <span className="text-green-600">{sg.switches_online} online</span>
                            {sg.switches_offline > 0 && (
                              <span className="text-red-600 ml-1">/ {sg.switches_offline} offline</span>
                            )}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                ) : zone.domain_switch_groups.length > 0 ? (
                  <div className="text-xs text-gray-500">
                    <span className="italic">Not mapped.</span>
                    <span className="ml-1">Use "Map Switch Groups" button above.</span>
                  </div>
                ) : (
                  <span className="text-gray-400 text-xs">No switch groups in domain</span>
                )}
              </div>

              {/* Zone Info */}
              <div>
                <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-1">
                  <Info className="w-4 h-4" /> Zone Info
                </h4>
                <div className="space-y-1 text-xs">
                  {zone.external_ips && zone.external_ips.length > 0 && (
                    <div>
                      <span className="text-gray-500">External IP{zone.external_ips.length > 1 ? 's' : ''}:</span>
                      <span className="ml-2 font-mono">
                        {zone.external_ips.length <= 3
                          ? zone.external_ips.join(', ')
                          : `${zone.external_ips.slice(0, 2).join(', ')} +${zone.external_ips.length - 2} more`
                        }
                      </span>
                    </div>
                  )}
                  <div>
                    <span className="text-gray-500">Zone ID:</span>
                    <span className="ml-2 font-mono text-gray-600">{zone.zone_id.slice(0, 8)}...</span>
                  </div>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// Refresh mode options
type RefreshMode = 'full' | 'incremental' | 'cached_only';

const REFRESH_MODE_INFO: Record<RefreshMode, { label: string; description: string; icon: string }> = {
  full: {
    label: 'Full Refresh',
    description: 'Fetch all zones from SmartZone API, updating cache',
    icon: '🔄'
  },
  incremental: {
    label: 'Incremental',
    description: 'Use cached zones if available, only fetch new/changed zones',
    icon: '⚡'
  },
  cached_only: {
    label: 'Cached Only',
    description: 'Return cached data instantly, no API calls (requires previous audit)',
    icon: '💾'
  }
};

// Cache status from backend
interface CacheStatus {
  controller_id: number;
  has_cache: boolean;
  cached_zones: number;
  last_audit_time: string | null;
  cache_age_minutes: number | null;
  zone_count: number | null;
}

// Cached zone info for selection UI
interface CachedZoneInfo {
  zone_id: string;
  zone_name: string;
  domain_name: string;
  cached_at: string | null;
  ap_count: number;
  wlan_count: number;
}

// Final audit stats to display after completion
interface AuditFinalStats {
  api_stats?: {
    total_calls: number;
    errors: number;
    elapsed_seconds: number;
    avg_calls_per_second: number;
  };
  cache_stats?: {
    refresh_mode: string;
    zones_from_cache: number;
    zones_refreshed: number;
    cache_hit_rate: number;
  };
}

// Types for async job tracking
interface AuditJob {
  job_id: string;
  controller_id: number;
  controller_name: string;
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
  current_phase?: string;
  current_activity?: string;  // Detailed progress message
  progress?: {
    phases_total: number;
    phases_completed: number;
    percent: number;
  };
  errors: string[];
  refresh_mode?: RefreshMode;
  cache_stats?: {
    refresh_mode: string;
    zones_from_cache: number;
    zones_refreshed: number;
    cache_hit_rate: number;
  };
  api_stats?: {
    total_calls: number;
    errors: number;
    elapsed_seconds: number;
    avg_calls_per_second: number;
    calls_last_minute: number;
    current_rate_per_second: number;
  };
}

// Main Page Component
export default function SZAudit() {
  const { controllers } = useAuth();
  const [selectedControllers, setSelectedControllers] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SZAuditResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedZones, setExpandedZones] = useState<Set<string>>(new Set());

  // Async job tracking
  const [activeJobs, setActiveJobs] = useState<AuditJob[]>([]);

  // Refresh mode
  const [refreshMode, setRefreshMode] = useState<RefreshMode>('full');

  // Cache status for selected controllers
  const [cacheStatus, setCacheStatus] = useState<Record<number, CacheStatus>>({});

  // Cached zones for selection (when in incremental mode)
  const [cachedZones, setCachedZones] = useState<Record<number, CachedZoneInfo[]>>({});
  const [forceRefreshZones, setForceRefreshZones] = useState<Set<string>>(new Set());
  const [showZoneSelector, setShowZoneSelector] = useState(false);

  // Final stats from completed audit (to show above results)
  const [finalStats, setFinalStats] = useState<AuditFinalStats | null>(null);

  // Switch group mapping state
  const [mapperOpen, setMapperOpen] = useState(false);
  const [mapperControllerId, setMapperControllerId] = useState<number | null>(null);
  const [sgMappings, setSgMappings] = useState<Record<number, ZoneSwitchGroupMapping>>({});

  // Filter to only SmartZone controllers
  const szControllers = useMemo(() =>
    controllers.filter(c => c.controller_type === 'SmartZone'),
    [controllers]
  );

  // Load saved mappings when results come in
  useEffect(() => {
    if (results.length > 0) {
      const newMappings: Record<number, ZoneSwitchGroupMapping> = {};
      for (const result of results) {
        if (!result.error) {
          newMappings[result.controller_id] = loadMappings(result.controller_id);
        }
      }
      setSgMappings(newMappings);
    }
  }, [results]);

  // Fetch cache status and cached zones when controllers are selected
  useEffect(() => {
    const fetchCacheData = async () => {
      const newStatus: Record<number, CacheStatus> = {};
      const newCachedZones: Record<number, CachedZoneInfo[]> = {};

      for (const controllerId of selectedControllers) {
        try {
          // Fetch cache status
          const statusResponse = await fetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}`, {
            credentials: 'include'
          });
          if (statusResponse.ok) {
            const data = await statusResponse.json();
            newStatus[controllerId] = data;

            // If there are cached zones, fetch their details
            if (data.has_cache && data.cached_zones > 0) {
              const zonesResponse = await fetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}/zones`, {
                credentials: 'include'
              });
              if (zonesResponse.ok) {
                newCachedZones[controllerId] = await zonesResponse.json();
              }
            }
          }
        } catch (err) {
          console.error(`Failed to fetch cache data for controller ${controllerId}:`, err);
        }
      }

      setCacheStatus(newStatus);
      setCachedZones(newCachedZones);
    };

    if (selectedControllers.size > 0) {
      fetchCacheData();
    } else {
      setCacheStatus({});
      setCachedZones({});
    }
    // Clear force refresh selection when controllers change
    setForceRefreshZones(new Set());
    setShowZoneSelector(false);
  }, [selectedControllers]);

  // Clear force refresh zones when mode changes away from incremental
  useEffect(() => {
    if (refreshMode !== 'incremental') {
      setForceRefreshZones(new Set());
      setShowZoneSelector(false);
    }
  }, [refreshMode]);

  const toggleController = (id: number) => {
    setSelectedControllers(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () => {
    setSelectedControllers(new Set(szControllers.map(c => c.id)));
  };

  const clearSelection = () => {
    setSelectedControllers(new Set());
  };

  const toggleZoneExpand = (zoneId: string) => {
    setExpandedZones(prev => {
      const next = new Set(prev);
      if (next.has(zoneId)) {
        next.delete(zoneId);
      } else {
        next.add(zoneId);
      }
      return next;
    });
  };

  const runAudit = async () => {
    if (selectedControllers.size === 0) return;

    setLoading(true);
    setError(null);
    setResults([]);
    setExpandedZones(new Set());
    setActiveJobs([]);
    setFinalStats(null);

    const controllerIds = Array.from(selectedControllers);
    const jobs: AuditJob[] = [];

    try {
      // Start async audit jobs for all selected controllers
      for (const controllerId of controllerIds) {
        const controller = szControllers.find(c => c.id === controllerId);
        try {
          const response = await fetch(`${API_BASE_URL}/sz/audit/async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              controller_id: controllerId,
              refresh_mode: refreshMode,
              force_refresh_zones: Array.from(forceRefreshZones)
            })
          });

          if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || errData.error || `HTTP ${response.status}`);
          }

          const data = await response.json();
          jobs.push({
            job_id: data.job_id,
            controller_id: controllerId,
            controller_name: controller?.name || `Controller ${controllerId}`,
            status: 'RUNNING',
            progress: { phases_total: 4, phases_completed: 0, percent: 0 },
            errors: []
          });
        } catch (err) {
          // If we can't even start the job, track it as failed
          jobs.push({
            job_id: '',
            controller_id: controllerId,
            controller_name: controller?.name || `Controller ${controllerId}`,
            status: 'FAILED',
            errors: [err instanceof Error ? err.message : 'Failed to start audit']
          });
        }
      }

      setActiveJobs([...jobs]);

      // Poll for completion
      const completedResults: SZAuditResult[] = [];
      const pendingJobs = jobs.filter(j => j.status === 'RUNNING');

      while (pendingJobs.length > 0) {
        await new Promise(resolve => setTimeout(resolve, 2000)); // Poll every 2 seconds

        for (let i = pendingJobs.length - 1; i >= 0; i--) {
          const job = pendingJobs[i];
          if (!job.job_id) continue;

          try {
            // Get job status
            const statusResponse = await fetch(`${API_BASE_URL}/sz/audit/jobs/${job.job_id}/status`, {
              credentials: 'include'
            });

            if (!statusResponse.ok) {
              throw new Error(`Status check failed: HTTP ${statusResponse.status}`);
            }

            const statusData = await statusResponse.json();
            job.status = statusData.status;
            job.current_phase = statusData.current_phase;
            job.current_activity = statusData.current_activity;
            job.progress = statusData.progress;
            job.errors = statusData.errors || [];
            job.refresh_mode = statusData.refresh_mode;
            job.cache_stats = statusData.cache_stats;
            job.api_stats = statusData.api_stats;

            // Update UI with latest status
            setActiveJobs(prev => prev.map(j => j.job_id === job.job_id ? { ...job } : j));

            if (statusData.status === 'COMPLETED') {
              // Capture final stats before moving on
              setFinalStats({
                api_stats: statusData.api_stats,
                cache_stats: statusData.cache_stats
              });

              // Fetch the result
              const resultResponse = await fetch(`${API_BASE_URL}/sz/audit/jobs/${job.job_id}/result`, {
                credentials: 'include'
              });

              if (resultResponse.ok) {
                const result = await resultResponse.json();
                completedResults.push(result);
                setResults([...completedResults]);
              }

              pendingJobs.splice(i, 1);
            } else if (statusData.status === 'FAILED' || statusData.status === 'CANCELLED') {
              // Create a failed/cancelled result entry
              completedResults.push({
                controller_id: job.controller_id,
                controller_name: job.controller_name,
                host: '',
                timestamp: new Date().toISOString(),
                error: statusData.status === 'CANCELLED'
                  ? 'Audit cancelled'
                  : (job.errors.join('; ') || 'Audit failed'),
                domains: [],
                zones: [],
                total_domains: 0,
                total_zones: 0,
                total_aps: 0,
                total_wlans: 0,
                total_switches: 0,
                ap_model_summary: [],
                ap_firmware_summary: [],
                switch_firmware_summary: [],
                wlan_type_summary: {},
                cluster_ip: null,
                controller_firmware: null,
                partial_errors: []
              });
              setResults([...completedResults]);
              pendingJobs.splice(i, 1);
            }
          } catch (err) {
            console.error(`Failed to check status for job ${job.job_id}:`, err);
          }
        }
      }

      // Handle any jobs that failed to start
      for (const job of jobs) {
        if (job.status === 'FAILED' && !job.job_id) {
          completedResults.push({
            controller_id: job.controller_id,
            controller_name: job.controller_name,
            host: '',
            timestamp: new Date().toISOString(),
            error: job.errors.join('; ') || 'Failed to start audit',
            domains: [],
            zones: [],
            total_domains: 0,
            total_zones: 0,
            total_aps: 0,
            total_wlans: 0,
            total_switches: 0,
            ap_model_summary: [],
            ap_firmware_summary: [],
            switch_firmware_summary: [],
            wlan_type_summary: {},
            cluster_ip: null,
            controller_firmware: null,
            partial_errors: []
          });
        }
      }

      setResults(completedResults);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
      setActiveJobs([]);
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      const response = await fetch(`${API_BASE_URL}/sz/audit/jobs/${jobId}/cancel`, {
        method: 'POST',
        credentials: 'include'
      });

      if (response.ok) {
        // Update the job status to show cancelling
        setActiveJobs(prev => prev.map(j =>
          j.job_id === jobId
            ? { ...j, current_activity: 'Cancelling...' }
            : j
        ));
      }
    } catch (err) {
      console.error(`Failed to cancel job ${jobId}:`, err);
    }
  };

  const exportCsv = () => {
    if (results.length === 0) return;

    // Build CSV content from already-fetched results
    const headers = [
      "Controller Name",
      "Controller Host",
      "Controller Firmware",
      "Domain Name",
      "Zone Name",
      "Zone ID",
      "APs Total",
      "APs Online",
      "APs Offline",
      "APs Flagged",
      "AP Groups",
      "AP Model 1",
      "AP Model 1 Count",
      "AP Model 2",
      "AP Model 2 Count",
      "AP Model 3",
      "AP Model 3 Count",
      "AP Models Other",
      "AP Firmware 1",
      "AP Firmware 1 Count",
      "AP Firmware 2",
      "AP Firmware 2 Count",
      "AP Firmware 3",
      "AP Firmware 3 Count",
      "AP Firmware Other",
      "External IP Count",
      "External IPs",
      "WLAN Count",
      "WLAN Group Count",
      "WLANs Open",
      "WLANs WPA2-PSK",
      "WLANs WPA2-Enterprise",
      "WLANs WPA3-SAE",
      "WLANs WPA3-Enterprise",
      "WLANs DPSK",
      "WLANs Other",
      "Domain Switch Count",
      "Domain Switch Groups",
      "Mapped Switch Group",
      "Mapped Switch Group ID",
      "Mapped Switches Total",
      "Mapped Switches Online",
      "Mapped Switches Offline",
      "Mapped Switch Firmware 1",
      "Mapped Switch Firmware 1 Count",
      "Mapped Switch Firmware 2",
      "Mapped Switch Firmware 2 Count",
      "Mapped Switch Firmware Other",
    ];

    const rows: string[][] = [headers];

    for (const result of results) {
      if (result.error) {
        rows.push([
          result.controller_name,
          result.host,
          `ERROR: ${result.error}`,
          ...Array(headers.length - 3).fill("")
        ]);
        continue;
      }

      // Build domain -> switch count mapping
      const domainSwitchCounts: Record<string, number> = {};
      const domainSwitchGroups: Record<string, number> = {};
      // Build switch group lookup by ID
      const allSwitchGroupsMap: Record<string, SwitchGroupSummary> = {};
      const collectSwitchGroups = (domains: DomainAudit[]) => {
        for (const d of domains) {
          domainSwitchCounts[d.domain_id] = d.total_switches;
          domainSwitchGroups[d.domain_id] = d.switch_groups.length;
          for (const sg of d.switch_groups) {
            allSwitchGroupsMap[sg.id] = sg;
          }
          if (d.children) collectSwitchGroups(d.children);
        }
      };
      collectSwitchGroups(result.domains);

      // Get manual mappings for this controller
      const controllerMappings = sgMappings[result.controller_id] || {};

      for (const zone of result.zones) {
        // Get mapped switch group for this zone
        const mappedSgId = controllerMappings[zone.zone_id] || "";
        const mappedSg = mappedSgId ? allSwitchGroupsMap[mappedSgId] : null;
        const sgFirmware = mappedSg?.firmware_versions || [];

        // AP models - get top 3
        const apModels = [...zone.ap_model_distribution].sort((a, b) => b.count - a.count);
        const model1 = apModels[0]?.model || "";
        const model1Count = apModels[0]?.count || "";
        const model2 = apModels[1]?.model || "";
        const model2Count = apModels[1]?.count || "";
        const model3 = apModels[2]?.model || "";
        const model3Count = apModels[2]?.count || "";
        const modelsOther = apModels.slice(3).reduce((sum, m) => sum + m.count, 0) || "";

        // AP firmware - get top 3
        const apFirmware = [...zone.ap_firmware_distribution].sort((a, b) => b.count - a.count);
        const fw1 = apFirmware[0]?.version || "";
        const fw1Count = apFirmware[0]?.count || "";
        const fw2 = apFirmware[1]?.version || "";
        const fw2Count = apFirmware[1]?.count || "";
        const fw3 = apFirmware[2]?.version || "";
        const fw3Count = apFirmware[2]?.count || "";
        const fwOther = apFirmware.slice(3).reduce((sum, f) => sum + f.count, 0) || "";

        // WLAN type counts
        const wlanTypes = zone.wlan_type_breakdown;
        const wlansOpen = (wlanTypes["Open"] || 0) + (wlanTypes["Open + Portal"] || 0);
        const wlansWpa2Psk = wlanTypes["WPA2-PSK"] || 0;
        const wlansWpa2Ent = wlanTypes["WPA2-Enterprise"] || 0;
        const wlansWpa3Sae = (wlanTypes["WPA3-SAE"] || 0) + (wlanTypes["WPA3"] || 0);
        const wlansWpa3Ent = wlanTypes["WPA3-Enterprise"] || 0;
        const wlansDpsk = wlanTypes["DPSK"] || 0;
        const knownTypes = new Set(["Open", "Open + Portal", "WPA2-PSK", "WPA2-Enterprise", "WPA3-SAE", "WPA3", "WPA3-Enterprise", "DPSK"]);
        const wlansOther = Object.entries(wlanTypes)
          .filter(([k]) => !knownTypes.has(k))
          .reduce((sum, [, v]) => sum + v, 0);

        rows.push([
          result.controller_name,
          result.host,
          result.controller_firmware || "",
          zone.domain_name,
          zone.zone_name,
          zone.zone_id,
          String(zone.ap_status.total),
          String(zone.ap_status.online),
          String(zone.ap_status.offline),
          String(zone.ap_status.flagged),
          String(zone.ap_groups.length),
          String(model1), String(model1Count),
          String(model2), String(model2Count),
          String(model3), String(model3Count),
          String(modelsOther),
          String(fw1), String(fw1Count),
          String(fw2), String(fw2Count),
          String(fw3), String(fw3Count),
          String(fwOther),
          String(zone.external_ips?.length || 0),
          zone.external_ips?.join("; ") || "",
          String(zone.wlan_count),
          String(zone.wlan_groups.length),
          String(wlansOpen || ""),
          String(wlansWpa2Psk || ""),
          String(wlansWpa2Ent || ""),
          String(wlansWpa3Sae || ""),
          String(wlansWpa3Ent || ""),
          String(wlansDpsk || ""),
          String(wlansOther || ""),
          String(domainSwitchCounts[zone.domain_id] || ""),
          String(domainSwitchGroups[zone.domain_id] || ""),
          // Mapped switch group
          mappedSg?.name || "",
          mappedSgId,
          String(mappedSg?.switch_count || ""),
          String(mappedSg?.switches_online || ""),
          String(mappedSg?.switches_offline || ""),
          // Mapped switch firmware
          sgFirmware[0]?.version || "",
          String(sgFirmware[0]?.count || ""),
          sgFirmware[1]?.version || "",
          String(sgFirmware[1]?.count || ""),
          String(sgFirmware.slice(2).reduce((sum, f) => sum + f.count, 0) || ""),
        ]);
      }
    }

    // Convert to CSV string
    const csvContent = rows.map(row =>
      row.map(cell => {
        // Escape quotes and wrap in quotes if contains comma, quote, or newline
        const escaped = String(cell).replace(/"/g, '""');
        return /[,"\n]/.test(escaped) ? `"${escaped}"` : escaped;
      }).join(",")
    ).join("\n");

    // Download
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    a.download = `sz_audit_${timestamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  // Flatten zones across all controllers for table view
  const allZoneRows: ZoneRowData[] = useMemo(() => {
    const rows: ZoneRowData[] = [];

    for (const result of results) {
      if (result.error) continue;

      // Build domain -> switch info lookup
      const domainSwitchInfo: Record<string, { total: number; groups: SwitchGroupSummary[] }> = {};
      const buildDomainLookup = (domains: DomainAudit[]) => {
        for (const d of domains) {
          domainSwitchInfo[d.domain_id] = {
            total: d.total_switches,
            groups: d.switch_groups
          };
          if (d.children) buildDomainLookup(d.children);
        }
      };
      buildDomainLookup(result.domains);

      // Get manual mappings for this controller
      const controllerMappings = sgMappings[result.controller_id] || {};

      // Build flat list of all switch groups for this controller
      const allSwitchGroups: SwitchGroupSummary[] = [];
      const collectSwitchGroups = (domains: DomainAudit[]) => {
        for (const d of domains) {
          allSwitchGroups.push(...d.switch_groups);
          if (d.children) collectSwitchGroups(d.children);
        }
      };
      collectSwitchGroups(result.domains);

      for (const zone of result.zones) {
        const switchInfo = domainSwitchInfo[zone.domain_id] || { total: 0, groups: [] };

        // Use manual mapping if exists, otherwise empty
        const mappedSgId = controllerMappings[zone.zone_id];
        const mappedSg = mappedSgId ? allSwitchGroups.find(sg => sg.id === mappedSgId) : null;
        const effectiveGroups = mappedSg ? [mappedSg] : [];

        rows.push({
          ...zone,
          controller_name: result.controller_name,
          controller_id: result.controller_id,
          controller_firmware: result.controller_firmware,
          domain_switches: switchInfo.total,
          domain_switch_groups: switchInfo.groups,
          effective_switch_groups: effectiveGroups
        });
      }
    }

    // Sort by controller name, then domain, then zone
    rows.sort((a, b) => {
      const cmp1 = a.controller_name.localeCompare(b.controller_name);
      if (cmp1 !== 0) return cmp1;
      const cmp2 = a.domain_name.localeCompare(b.domain_name);
      if (cmp2 !== 0) return cmp2;
      return a.zone_name.localeCompare(b.zone_name);
    });

    return rows;
  }, [results, sgMappings]);

  // Aggregate stats across all results
  const aggregateStats = useMemo(() => {
    const successfulResults = results.filter(r => !r.error);

    // Count switch groups across all domains
    const countSwitchGroups = (domains: DomainAudit[]): number => {
      let count = 0;
      for (const d of domains) {
        count += d.switch_groups.length;
        if (d.children) count += countSwitchGroups(d.children);
      }
      return count;
    };

    return {
      totalControllers: successfulResults.length,
      totalDomains: successfulResults.reduce((sum, r) => sum + r.total_domains, 0),
      totalZones: successfulResults.reduce((sum, r) => sum + r.total_zones, 0),
      totalAps: successfulResults.reduce((sum, r) => sum + r.total_aps, 0),
      totalWlans: successfulResults.reduce((sum, r) => sum + r.total_wlans, 0),
      totalSwitches: successfulResults.reduce((sum, r) => sum + r.total_switches, 0),
      totalSwitchGroups: successfulResults.reduce((sum, r) => sum + countSwitchGroups(r.domains), 0),
      wlanTypes: successfulResults.reduce((acc, r) => {
        Object.entries(r.wlan_type_summary).forEach(([type, count]) => {
          acc[type] = (acc[type] || 0) + count;
        });
        return acc;
      }, {} as Record<string, number>),
      apModels: successfulResults.reduce((acc, r) => {
        r.ap_model_summary.forEach(m => {
          acc[m.model] = (acc[m.model] || 0) + m.count;
        });
        return acc;
      }, {} as Record<string, number>),
      apFirmware: successfulResults.reduce((acc, r) => {
        r.ap_firmware_summary.forEach(f => {
          acc[f.version] = (acc[f.version] || 0) + f.count;
        });
        return acc;
      }, {} as Record<string, number>),
    };
  }, [results]);

  // Collect partial errors
  const allPartialErrors = useMemo(() => {
    return results.flatMap(r => r.partial_errors || []);
  }, [results]);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-full mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900">SZ Audit</h1>
          <p className="text-gray-600 mt-1">Comprehensive SmartZone controller audit - zones, APs, WLANs, switches, and firmware</p>
        </div>

        {/* Controller Selection */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900">Select Controllers</h2>
            <div className="flex gap-2">
              <button
                onClick={selectAll}
                className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded transition"
              >
                Select All
              </button>
              <button
                onClick={clearSelection}
                className="px-3 py-1 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded transition"
              >
                Clear
              </button>
            </div>
          </div>

          {szControllers.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <Server className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No SmartZone controllers configured.</p>
              <p className="text-sm">Add a SmartZone controller in the Controllers page to use this feature.</p>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {szControllers.map((controller) => (
                <label
                  key={controller.id}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition ${
                    selectedControllers.has(controller.id)
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedControllers.has(controller.id)}
                    onChange={() => toggleController(controller.id)}
                    className="w-4 h-4 text-blue-600 rounded"
                  />
                  <Server className="w-4 h-4 text-gray-500" />
                  <span className="font-medium text-gray-900 text-sm">{controller.name}</span>
                </label>
              ))}
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <span className="text-sm text-gray-500">
              {selectedControllers.size} controller{selectedControllers.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-3">
              {/* Refresh Mode Selector with Cache Info */}
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-600">Mode:</label>
                  <select
                    value={refreshMode}
                    onChange={(e) => setRefreshMode(e.target.value as RefreshMode)}
                    disabled={loading}
                    className="px-3 py-2 text-sm border border-gray-300 rounded-lg bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-50"
                  >
                    <option value="full">{REFRESH_MODE_INFO.full.icon} {REFRESH_MODE_INFO.full.label}</option>
                    <option value="incremental">{REFRESH_MODE_INFO.incremental.icon} {REFRESH_MODE_INFO.incremental.label}</option>
                    <option value="cached_only">{REFRESH_MODE_INFO.cached_only.icon} {REFRESH_MODE_INFO.cached_only.label}</option>
                  </select>
                </div>
                {/* Mode description and cache status */}
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-gray-500">{REFRESH_MODE_INFO[refreshMode].description}</span>
                  {selectedControllers.size > 0 && Object.keys(cacheStatus).length > 0 && (
                    <>
                      <span className="text-blue-600">
                        {(() => {
                          const statuses = Object.values(cacheStatus);
                          const withCache = statuses.filter(s => s.has_cache);
                          if (withCache.length === 0) return '• No cached data';
                          const totalZones = withCache.reduce((sum, s) => sum + s.cached_zones, 0);
                          const oldestAge = Math.max(...withCache.map(s => s.cache_age_minutes || 0));
                          return `• ${totalZones} zones cached${oldestAge > 0 ? ` (${oldestAge < 60 ? `${oldestAge}m` : `${Math.round(oldestAge/60)}h`} old)` : ''}`;
                        })()}
                      </span>
                      {/* Zone selector toggle - only show for incremental mode with cached zones */}
                      {refreshMode === 'incremental' && Object.values(cachedZones).flat().length > 0 && (
                        <button
                          onClick={() => setShowZoneSelector(!showZoneSelector)}
                          className="text-blue-600 hover:text-blue-800 underline"
                        >
                          {showZoneSelector ? 'Hide' : 'Select'} zones to refresh
                          {forceRefreshZones.size > 0 && ` (${forceRefreshZones.size})`}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
              <button
                onClick={exportCsv}
                disabled={results.length === 0 || loading}
                className={`px-4 py-2 rounded-lg font-medium transition flex items-center gap-2 ${
                  results.length === 0 || loading
                    ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                    : 'bg-green-600 hover:bg-green-700 text-white'
                }`}
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
              <button
                onClick={runAudit}
                disabled={selectedControllers.size === 0 || loading}
                className={`px-6 py-2 rounded-lg font-medium transition ${
                  selectedControllers.size === 0 || loading
                    ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                    : 'bg-blue-600 hover:bg-blue-700 text-white'
                }`}
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Running Audit...
                  </span>
                ) : (
                  'Run Audit'
                )}
              </button>
            </div>
          </div>

          {/* Zone Selector Panel - for incremental mode */}
          {showZoneSelector && refreshMode === 'incremental' && Object.values(cachedZones).flat().length > 0 && (
            <div className="mt-4 p-4 bg-gray-50 border border-gray-200 rounded-lg">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-medium text-gray-700">
                  Select zones to force-refresh (others will use cache)
                </h4>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      // Select all
                      const allZoneIds = Object.values(cachedZones).flat().map(z => z.zone_id);
                      setForceRefreshZones(new Set(allZoneIds));
                    }}
                    className="text-xs text-blue-600 hover:text-blue-800"
                  >
                    Select All
                  </button>
                  <span className="text-gray-300">|</span>
                  <button
                    onClick={() => setForceRefreshZones(new Set())}
                    className="text-xs text-blue-600 hover:text-blue-800"
                  >
                    Clear
                  </button>
                </div>
              </div>
              <div className="max-h-64 overflow-y-auto space-y-1">
                {Object.entries(cachedZones).map(([controllerId, zones]) => {
                  const controller = szControllers.find(c => c.id === parseInt(controllerId));
                  return (
                    <div key={controllerId}>
                      {Object.keys(cachedZones).length > 1 && (
                        <div className="text-xs font-medium text-gray-500 mt-2 mb-1">
                          {controller?.name || `Controller ${controllerId}`}
                        </div>
                      )}
                      {zones.map(zone => (
                        <label
                          key={zone.zone_id}
                          className={`flex items-center gap-2 p-2 rounded cursor-pointer hover:bg-gray-100 ${
                            forceRefreshZones.has(zone.zone_id) ? 'bg-blue-50' : ''
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={forceRefreshZones.has(zone.zone_id)}
                            onChange={(e) => {
                              const newSet = new Set(forceRefreshZones);
                              if (e.target.checked) {
                                newSet.add(zone.zone_id);
                              } else {
                                newSet.delete(zone.zone_id);
                              }
                              setForceRefreshZones(newSet);
                            }}
                            className="w-4 h-4 text-blue-600 rounded"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-gray-900 truncate">
                              {zone.zone_name}
                            </div>
                            <div className="text-xs text-gray-500">
                              {zone.domain_name} • {zone.ap_count} APs • {zone.wlan_count} WLANs
                              {zone.cached_at && (
                                <span className="ml-1">
                                  • cached {(() => {
                                    const mins = Math.round((Date.now() - new Date(zone.cached_at).getTime()) / 60000);
                                    return mins < 60 ? `${mins}m ago` : `${Math.round(mins/60)}h ago`;
                                  })()}
                                </span>
                              )}
                            </div>
                          </div>
                        </label>
                      ))}
                    </div>
                  );
                })}
              </div>
              {forceRefreshZones.size > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-200 text-sm text-gray-600">
                  {forceRefreshZones.size} zone{forceRefreshZones.size !== 1 ? 's' : ''} will be refreshed from API
                </div>
              )}
            </div>
          )}
        </div>

        {/* Active Jobs Progress */}
        {loading && activeJobs.length > 0 && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
            <h3 className="text-sm font-medium text-blue-800 mb-3 flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />
              Audit in Progress
            </h3>
            <div className="space-y-3">
              {activeJobs.map(job => (
                <div key={job.job_id || job.controller_id} className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-blue-900 truncate">
                        {job.controller_name}
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-blue-600">
                          {job.status === 'COMPLETED' && <CheckCircle2 className="w-4 h-4 text-green-600 inline" />}
                          {job.status === 'FAILED' && <AlertCircle className="w-4 h-4 text-red-600 inline" />}
                          {job.status === 'RUNNING' && job.current_phase && (
                            <span className="capitalize">{job.current_phase.replace(/_/g, ' ')}</span>
                          )}
                        </span>
                        {job.status === 'RUNNING' && job.job_id && (
                          <button
                            onClick={() => cancelJob(job.job_id)}
                            className="p-1 rounded hover:bg-red-100 text-red-500 hover:text-red-700 transition"
                            title="Cancel audit"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </div>
                    {/* Activity detail */}
                    {job.status === 'RUNNING' && job.current_activity && (
                      <div className="text-xs text-blue-700 mb-1 truncate" title={job.current_activity}>
                        {job.current_activity}
                      </div>
                    )}
                    <div className="w-full bg-blue-200 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full transition-all duration-300 ${
                          job.status === 'COMPLETED' ? 'bg-green-500' :
                          job.status === 'FAILED' ? 'bg-red-500' :
                          job.status === 'CANCELLED' ? 'bg-orange-500' :
                          'bg-blue-600'
                        }`}
                        style={{ width: `${job.progress?.percent || 0}%` }}
                      />
                    </div>
                    {/* Cache & API Stats */}
                    {job.status === 'RUNNING' && (job.cache_stats || job.api_stats) && (
                      <div className="mt-1 flex items-center gap-4 text-xs">
                        {job.cache_stats && job.cache_stats.zones_from_cache + job.cache_stats.zones_refreshed > 0 && (
                          <span className="text-blue-600">
                            Cache: {job.cache_stats.zones_from_cache} cached / {job.cache_stats.zones_refreshed} refreshed
                            {job.cache_stats.cache_hit_rate > 0 && (
                              <span className="text-blue-400 ml-1">({job.cache_stats.cache_hit_rate}% hit)</span>
                            )}
                          </span>
                        )}
                        {job.api_stats && (
                          <span className="text-gray-500">
                            API: {job.api_stats.total_calls} calls
                            {job.api_stats.elapsed_seconds > 0 && (
                              <span className="text-gray-400 ml-1">
                                ({job.api_stats.avg_calls_per_second}/s avg, {job.api_stats.current_rate_per_second}/s now)
                              </span>
                            )}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <div className="flex items-center gap-2 text-red-800">
              <AlertCircle className="w-5 h-5" />
              <span className="font-medium">Audit failed:</span>
              <span>{error}</span>
            </div>
          </div>
        )}

        {/* Results */}
        {results.length > 0 && (
          <>
            {/* Final Audit Stats */}
            {finalStats && (finalStats.api_stats || finalStats.cache_stats) && (
              <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium text-gray-700">Audit Performance</h3>
                  <span className="text-xs text-gray-400">
                    {finalStats.cache_stats?.refresh_mode && (
                      <span className="capitalize">{finalStats.cache_stats.refresh_mode} mode</span>
                    )}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-6">
                  {finalStats.api_stats && (
                    <div className="flex items-center gap-4">
                      <div className="text-center">
                        <div className="text-2xl font-bold text-gray-900">
                          {finalStats.api_stats.total_calls.toLocaleString()}
                        </div>
                        <div className="text-xs text-gray-500">API Calls</div>
                      </div>
                      <div className="text-center">
                        <div className="text-2xl font-bold text-gray-900">
                          {finalStats.api_stats.elapsed_seconds}s
                        </div>
                        <div className="text-xs text-gray-500">Duration</div>
                      </div>
                      <div className="text-center">
                        <div className="text-2xl font-bold text-gray-900">
                          {finalStats.api_stats.avg_calls_per_second}/s
                        </div>
                        <div className="text-xs text-gray-500">Avg Rate</div>
                      </div>
                      {finalStats.api_stats.errors > 0 && (
                        <div className="text-center">
                          <div className="text-2xl font-bold text-red-600">
                            {finalStats.api_stats.errors}
                          </div>
                          <div className="text-xs text-red-500">Errors</div>
                        </div>
                      )}
                    </div>
                  )}
                  {finalStats.cache_stats && (finalStats.cache_stats.zones_from_cache > 0 || finalStats.cache_stats.zones_refreshed > 0) && (
                    <div className="flex items-center gap-4 border-l border-gray-200 pl-6">
                      <div className="text-center">
                        <div className="text-2xl font-bold text-blue-600">
                          {finalStats.cache_stats.zones_from_cache}
                        </div>
                        <div className="text-xs text-gray-500">From Cache</div>
                      </div>
                      <div className="text-center">
                        <div className="text-2xl font-bold text-green-600">
                          {finalStats.cache_stats.zones_refreshed}
                        </div>
                        <div className="text-xs text-gray-500">Refreshed</div>
                      </div>
                      {finalStats.cache_stats.cache_hit_rate > 0 && (
                        <div className="text-center">
                          <div className="text-2xl font-bold text-purple-600">
                            {finalStats.cache_stats.cache_hit_rate}%
                          </div>
                          <div className="text-xs text-gray-500">Cache Hit</div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Global Summary */}
            <div className="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg shadow p-6 mb-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">Global Summary</h2>

              <div className="grid grid-cols-7 gap-4 mb-6">
                <StatCard label="Controllers" value={aggregateStats.totalControllers} icon={Server} />
                <StatCard label="Domains" value={aggregateStats.totalDomains} icon={Layers} />
                <StatCard label="Zones" value={aggregateStats.totalZones} icon={Server} />
                <StatCard label="APs" value={aggregateStats.totalAps} icon={Wifi} />
                <StatCard label="WLANs" value={aggregateStats.totalWlans} icon={Router} />
                <StatCard label="Switch Groups" value={aggregateStats.totalSwitchGroups} icon={Layers} />
                <StatCard label="Switches" value={aggregateStats.totalSwitches} icon={HardDrive} />
              </div>

              <div className="grid grid-cols-3 gap-6">
                <div>
                  <span className="text-sm font-medium text-gray-700 uppercase block mb-2">AP Models:</span>
                  <DistributionBadges
                    items={Object.entries(aggregateStats.apModels)
                      .sort((a, b) => b[1] - a[1])
                      .map(([model, count]) => ({ label: model, count }))}
                    colorClass="bg-green-100 text-green-800"
                    maxShow={6}
                  />
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-700 uppercase block mb-2">WLAN Types:</span>
                  <DistributionBadges
                    items={Object.entries(aggregateStats.wlanTypes)
                      .sort((a, b) => b[1] - a[1])
                      .map(([type, count]) => ({ label: type, count }))}
                    colorClass="bg-purple-100 text-purple-800"
                    maxShow={6}
                  />
                </div>

                <div>
                  <span className="text-sm font-medium text-gray-700 uppercase block mb-2">AP Firmware:</span>
                  <DistributionBadges
                    items={Object.entries(aggregateStats.apFirmware)
                      .sort((a, b) => b[1] - a[1])
                      .map(([version, count]) => ({ label: version, count }))}
                    colorClass="bg-orange-100 text-orange-800"
                    maxShow={6}
                  />
                </div>
              </div>
            </div>

            {/* Partial Errors */}
            {allPartialErrors.length > 0 && (
              <div className="mb-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-yellow-800 text-sm font-medium mb-2">
                  <AlertCircle className="w-4 h-4" />
                  <span>Some data could not be fetched ({allPartialErrors.length} warnings):</span>
                </div>
                <ul className="text-xs text-yellow-700 list-disc list-inside max-h-24 overflow-y-auto">
                  {allPartialErrors.slice(0, 10).map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                  {allPartialErrors.length > 10 && (
                    <li>...and {allPartialErrors.length - 10} more</li>
                  )}
                </ul>
              </div>
            )}

            {/* Zones Table */}
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    Zones ({allZoneRows.length})
                  </h2>
                  <p className="text-sm text-gray-500">Click a row to expand details</p>
                </div>
                {/* Map Switch Groups buttons - one per controller */}
                <div className="flex gap-2">
                  {results.filter(r => !r.error).map(result => {
                    const mappingCount = Object.keys(sgMappings[result.controller_id] || {}).length;
                    const zoneCount = result.zones.length;
                    return (
                      <button
                        key={result.controller_id}
                        onClick={() => {
                          setMapperControllerId(result.controller_id);
                          setMapperOpen(true);
                        }}
                        className="px-3 py-1.5 text-sm bg-indigo-100 hover:bg-indigo-200 text-indigo-700 rounded flex items-center gap-1.5"
                        title={`Map switch groups for ${result.controller_name}`}
                      >
                        <Link2 className="w-4 h-4" />
                        {results.length > 1 ? result.controller_name.slice(0, 10) : 'Map Switch Groups'}
                        {mappingCount > 0 && (
                          <span className="text-xs bg-indigo-600 text-white px-1.5 rounded-full">
                            {mappingCount}/{zoneCount}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50">
                    <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      <th className="px-3 py-3">SmartZone</th>
                      <th className="px-3 py-3">Domain</th>
                      <th className="px-3 py-3">Zone</th>
                      <th className="px-3 py-3">APs</th>
                      <th className="px-3 py-3">AP Models</th>
                      <th className="px-3 py-3">AP Firmware</th>
                      <th className="px-3 py-3">External IPs</th>
                      <th className="px-3 py-3">WLANs</th>
                      <th className="px-3 py-3">WLAN Types</th>
                      <th className="px-3 py-3">Switches</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {allZoneRows.map((zone) => (
                      <ZoneRow
                        key={`${zone.controller_id}-${zone.zone_id}`}
                        zone={zone}
                        expanded={expandedZones.has(zone.zone_id)}
                        onToggle={() => toggleZoneExpand(zone.zone_id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              {allZoneRows.length === 0 && (
                <div className="text-center py-12 text-gray-500">
                  <p>No zones found in selected controllers</p>
                </div>
              )}
            </div>
          </>
        )}

        {/* Empty State */}
        {!loading && results.length === 0 && !error && (
          <div className="text-center py-12 text-gray-500">
            <CheckCircle2 className="w-16 h-16 mx-auto mb-4 opacity-30" />
            <p className="text-lg">Select controllers and run an audit to see results</p>
          </div>
        )}
      </div>

      {/* Switch Group Mapper Modal */}
      {mapperControllerId !== null && (() => {
        const result = results.find(r => r.controller_id === mapperControllerId);
        if (!result || result.error) return null;

        // Collect all switch groups from all domains
        const allSwitchGroups: SwitchGroupSummary[] = [];
        const collectSwitchGroups = (domains: DomainAudit[]) => {
          for (const d of domains) {
            allSwitchGroups.push(...d.switch_groups);
            if (d.children) collectSwitchGroups(d.children);
          }
        };
        collectSwitchGroups(result.domains);

        // Build zone list for mapper
        const zones = result.zones.map(z => ({
          zone_id: z.zone_id,
          zone_name: z.zone_name,
          domain_name: z.domain_name
        }));

        return (
          <SwitchGroupMapper
            isOpen={mapperOpen}
            onClose={() => {
              setMapperOpen(false);
              setMapperControllerId(null);
            }}
            zones={zones}
            switchGroups={allSwitchGroups}
            mappings={sgMappings[mapperControllerId] || {}}
            onMappingsChange={(newMappings) => {
              setSgMappings(prev => ({
                ...prev,
                [mapperControllerId]: newMappings
              }));
            }}
            controllerId={mapperControllerId}
          />
        );
      })()}
    </div>
  );
}
