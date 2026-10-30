import { useState, useMemo, useEffect, useRef } from 'react';
import { useAuth } from '@/context/AuthContext';
import { apiFetch } from '@/utils/api';
import { ChevronDown, ChevronRight, Loader2, AlertCircle, CheckCircle2, Server, Wifi, Router, HardDrive, Layers, Info, Download, Link2, X, Sparkles, Zap, History } from 'lucide-react';

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
  user_set?: boolean;  // True if user manually assigned this switch group
}

// Match candidate for multi-match feature
interface MatchCandidate {
  switch_group_id: string;
  switch_group_name: string;
  switch_count: number;
  switches_online: number;
  switches_offline: number;
  score: number;
  match_type: string;  // exact, normalized, contains, word-overlap, prefix
  match_reason: string;
  same_domain: boolean;
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
  user_set_mapping?: boolean;  // True if user manually set the switch group mapping
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

// Type for zone-to-switch-group mappings (local state uses IDs)
interface ZoneSwitchGroupMapping {
  [zoneId: string]: string;  // zoneId -> switchGroupId
}

// Type for backend mapping payload (includes full switch group info)
interface SwitchGroupMappingInfo {
  id: string;
  name: string;
  switch_count: number;
  switches_online: number;
  switches_offline: number;
}

// Helper to save mappings to backend cache
// This is fire-and-forget - UI updates optimistically
// Requires switch groups list to look up full info for each mapping
async function saveMappingsToBackend(
  controllerId: number,
  mappings: ZoneSwitchGroupMapping,
  switchGroups: SwitchGroupSummary[]
) {
  try {
    // Build payload with full switch group info
    const sgMap = new Map(switchGroups.map(sg => [sg.id, sg]));
    const payload: Record<string, SwitchGroupMappingInfo | null> = {};

    for (const [zoneId, sgId] of Object.entries(mappings)) {
      const sg = sgMap.get(sgId);
      if (sg) {
        payload[zoneId] = {
          id: sg.id,
          name: sg.name,
          switch_count: sg.switch_count,
          switches_online: sg.switches_online,
          switches_offline: sg.switches_offline
        };
      }
    }

    const response = await apiFetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}/mappings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mappings: payload })
    });
    if (!response.ok) {
      console.error(`[SZAudit] Failed to save mappings: HTTP ${response.status}`);
    }
  } catch (error) {
    console.error('[SZAudit] Failed to save mappings:', error);
  }
}

// Helper to clear a specific zone's mapping in backend cache
// Sends null to clear the mapping and remove user_set flag
async function clearZoneMappingInBackend(controllerId: number, zoneId: string) {
  try {
    const payload: Record<string, null> = { [zoneId]: null };
    const response = await apiFetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}/mappings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mappings: payload })
    });
    if (!response.ok) {
      console.error(`[SZAudit] Failed to clear mapping for zone ${zoneId}: HTTP ${response.status}`);
    }
  } catch (error) {
    console.error('[SZAudit] Failed to clear mapping:', error);
  }
}

// Component: Filtered Switch Group Selector for a single zone
// Simplified: if selected, it's confirmed. No lock/unlock states.
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
    // Show the confirmed mapping - blue background with X to clear
    return (
      <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded px-2 py-1.5">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-blue-800 truncate">
            {mappedSg.name}
          </div>
          <div className="text-xs text-blue-600">
            {mappedSg.switch_count} switches ({mappedSg.switches_online} online)
          </div>
        </div>
        <button
          onClick={onClear}
          className="p-1 hover:bg-blue-100 rounded text-blue-600 hover:text-blue-800"
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
  zones: { zone_id: string; zone_name: string; domain_name: string; matched_switch_groups?: SwitchGroupSummary[] }[];
  switchGroups: SwitchGroupSummary[];
  mappings: ZoneSwitchGroupMapping;
  onMappingsChange: (mappings: ZoneSwitchGroupMapping) => void;
  controllerId: number;
}) {
  // Match candidates state
  const [candidates, setCandidates] = useState<Record<string, MatchCandidate[]>>({});
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [showCandidates, setShowCandidates] = useState(true);

  // Fetch candidates when modal opens
  useEffect(() => {
    if (isOpen && Object.keys(candidates).length === 0) {
      fetchCandidates();
    }
  }, [isOpen]);

  const fetchCandidates = async () => {
    setLoadingCandidates(true);
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/sz/audit/cache/${controllerId}/candidates?top_n=3&min_score=20`
      );
      if (response.ok) {
        const data = await response.json();
        const candidateMap: Record<string, MatchCandidate[]> = {};
        for (const zone of data.zones || []) {
          candidateMap[zone.zone_id] = zone.candidates || [];
        }
        setCandidates(candidateMap);
        console.log(`[SwitchGroupMapper] Loaded candidates for ${Object.keys(candidateMap).length} zones`);
      }
    } catch (error) {
      console.error('[SwitchGroupMapper] Failed to fetch candidates:', error);
    } finally {
      setLoadingCandidates(false);
    }
  };

  if (!isOpen) return null;

  const handleMapZone = (zoneId: string, switchGroupId: string) => {
    const newMappings = { ...mappings, [zoneId]: switchGroupId };
    onMappingsChange(newMappings);
    // Save to backend - all selections are user-confirmed
    saveMappingsToBackend(controllerId, { [zoneId]: switchGroupId }, switchGroups);
  };

  const handleClearZone = (zoneId: string) => {
    const newMappings = { ...mappings };
    delete newMappings[zoneId];
    onMappingsChange(newMappings);
    // Clear this zone's mapping in backend
    clearZoneMappingInBackend(controllerId, zoneId);
  };

  const clearAllMappings = async () => {
    // Build payload with null for all currently mapped zones
    const clearPayload: Record<string, null> = {};
    for (const zoneId of Object.keys(mappings)) {
      clearPayload[zoneId] = null;
    }

    onMappingsChange({});

    // Send explicit null for each zone to clear user_set flags
    if (Object.keys(clearPayload).length > 0) {
      try {
        await apiFetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}/mappings`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mappings: clearPayload })
        });
      } catch (error) {
        console.error('[SZAudit] Failed to clear all mappings:', error);
      }
    }
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
              {Object.keys(mappings).length} of {zones.length} zones confirmed
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowCandidates(!showCandidates)}
              className={`px-3 py-1 text-sm rounded flex items-center gap-1 ${
                showCandidates ? 'bg-purple-100 text-purple-700' : 'text-gray-600 hover:bg-gray-100'
              }`}
              title="Toggle match suggestions"
            >
              <Sparkles className="w-4 h-4" />
              {showCandidates ? 'Hide Suggestions' : 'Show Suggestions'}
            </button>
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
              {[...zones].sort((a, b) => a.zone_name.localeCompare(b.zone_name)).map(zone => {
                const zoneCandidates = candidates[zone.zone_id] || [];
                const hasMapping = !!mappings[zone.zone_id];
                // Filter candidates to exclude already-mapped switch groups
                const availableCandidates = zoneCandidates.filter(c => !mappedSgIds.has(c.switch_group_id));

                return (
                  <div key={zone.zone_id} className="p-2 bg-gray-50 rounded-lg">
                    <div className="flex items-start gap-3">
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
                    {/* Candidates section - show when no mapping and candidates available */}
                    {showCandidates && !hasMapping && availableCandidates.length > 0 && (
                      <div className="mt-2 ml-[172px] flex flex-wrap gap-1">
                        {availableCandidates.map((candidate) => (
                          <button
                            key={candidate.switch_group_id}
                            onClick={() => handleMapZone(zone.zone_id, candidate.switch_group_id)}
                            className="group flex items-center gap-1 px-2 py-1 text-xs bg-purple-50 hover:bg-purple-100 border border-purple-200 rounded-md transition-colors"
                            title={`${candidate.match_reason}\n${candidate.switch_count} switches (${candidate.switches_online} online)`}
                          >
                            <span className="font-medium text-purple-800 truncate max-w-32">
                              {candidate.switch_group_name}
                            </span>
                            <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${
                              candidate.score >= 80 ? 'bg-green-100 text-green-700' :
                              candidate.score >= 50 ? 'bg-yellow-100 text-yellow-700' :
                              'bg-gray-100 text-gray-600'
                            }`}>
                              {candidate.score}
                            </span>
                            {candidate.same_domain && (
                              <span title="Same domain">
                                <Zap className="w-3 h-3 text-purple-500" />
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                    {/* Loading state for candidates */}
                    {showCandidates && !hasMapping && loadingCandidates && (
                      <div className="mt-2 ml-[172px] text-xs text-gray-400 flex items-center gap-1">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Loading suggestions...
                      </div>
                    )}
                  </div>
                );
              })}
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

// Basic mode: licenses and overall counts. A null count couldn't be read.
interface LicenseUsage {
  license_type: string;
  used: number;
  total: number;
}

interface LicenseEntitlement {
  name: string;
  description: string;
  count: number;
  starts: string | null;
  expires: string | null;
  perpetual: boolean;
}

interface BasicAuditResult {
  controller_id: number;
  controller_name: string;
  host: string | null;
  total_aps: number | null;
  aps_online: number | null;
  total_zones: number | null;
  zones_with_online_aps: number | null;
  total_switches: number | null;
  switches_online: number | null;
  switch_groups: number | null;
  switch_groups_with_online: number | null;
  ap_license: LicenseUsage | null;
  switch_license: LicenseUsage | null;
  licenses: LicenseUsage[];
  entitlements: LicenseEntitlement[];
  error: string | null;
  errors: string[];
}

const EXPIRY_WARNING_DAYS = 90;

// Sums a count across controllers; unknown if any controller's is unknown
const sumKnown = (values: (number | null | undefined)[]): number | null =>
  values.some(v => v === null || v === undefined)
    ? null
    : values.reduce<number>((sum, v) => sum + (v as number), 0);

const formatCount = (value: number | null) => (value === null ? '-' : value.toLocaleString());

// Component: Licenses and overall counts per controller
function BasicAuditResults({ results }: { results: BasicAuditResult[] }) {
  const readable = results.filter(r => !r.error);
  const failed = results.filter(r => r.error);
  const sectionErrors = readable.flatMap(r => r.errors.map(e => `${r.controller_name}: ${e}`));

  const licenseStat = (key: 'ap_license' | 'switch_license') => {
    const used = sumKnown(readable.map(r => r[key]?.used));
    const total = sumKnown(readable.map(r => r[key]?.total));
    return used === null || total === null ? '-' : `${used.toLocaleString()} / ${total.toLocaleString()}`;
  };

  const ofTotal = (part: number | null, total: number | null) =>
    part === null || total === null ? '-' : `${part.toLocaleString()} / ${total.toLocaleString()}`;

  const expiryCell = (entitlement: LicenseEntitlement) => {
    if (entitlement.perpetual) return <span className="text-gray-500">Perpetual</span>;
    if (!entitlement.expires) return <span className="text-gray-400">-</span>;
    const daysLeft = (new Date(`${entitlement.expires}T00:00:00`).getTime() - Date.now()) / 86400000;
    if (daysLeft < 0) return <span className="font-medium text-red-600">{entitlement.expires} (expired)</span>;
    if (daysLeft <= EXPIRY_WARNING_DAYS) return <span className="font-medium text-amber-600">{entitlement.expires}</span>;
    return <span>{entitlement.expires}</span>;
  };

  const usageCell = (usage: LicenseUsage | null) => {
    if (!usage) return <span className="text-gray-400">-</span>;
    return (
      <span className={usage.used > usage.total ? 'font-medium text-red-600' : ''}>
        {usage.used.toLocaleString()} / {usage.total.toLocaleString()}
      </span>
    );
  };

  return (
    <>
      {(failed.length > 0 || sectionErrors.length > 0) && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-6 text-sm text-yellow-800">
          <div className="font-medium mb-1 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {failed.length > 0 ? 'Totals leave out controllers that could not be read' : 'Some data could not be read'}
          </div>
          <ul className="list-disc list-inside space-y-0.5">
            {failed.map(r => <li key={r.controller_id}>{r.controller_name}: {r.error}</li>)}
            {sectionErrors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}

      {readable.length > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4 mb-6">
            <StatCard label="APs" value={formatCount(sumKnown(readable.map(r => r.total_aps)))} icon={Wifi} />
            <StatCard label="APs Online" value={formatCount(sumKnown(readable.map(r => r.aps_online)))} icon={Wifi} />
            <StatCard
              label="Zones With Online APs"
              value={ofTotal(sumKnown(readable.map(r => r.zones_with_online_aps)), sumKnown(readable.map(r => r.total_zones)))}
              icon={Server}
            />
            <StatCard label="Switches" value={formatCount(sumKnown(readable.map(r => r.total_switches)))} icon={HardDrive} />
            <StatCard label="Switches Online" value={formatCount(sumKnown(readable.map(r => r.switches_online)))} icon={HardDrive} />
            <StatCard
              label="Switch Groups With Online Switches"
              value={ofTotal(sumKnown(readable.map(r => r.switch_groups_with_online)), sumKnown(readable.map(r => r.switch_groups)))}
              icon={Layers}
            />
            <StatCard label="AP Licenses Used" value={licenseStat('ap_license')} icon={Layers} />
            <StatCard label="Switch Licenses Used" value={licenseStat('switch_license')} icon={Layers} />
          </div>

          <div className="bg-white rounded-lg shadow overflow-x-auto mb-6">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase">
                <tr>
                  <th className="px-3 py-2">Controller</th>
                  <th className="px-3 py-2">APs</th>
                  <th className="px-3 py-2">APs Online</th>
                  <th className="px-3 py-2">Zones With Online APs</th>
                  <th className="px-3 py-2">Switches</th>
                  <th className="px-3 py-2">Switches Online</th>
                  <th className="px-3 py-2">Switch Groups With Online Switches</th>
                  <th className="px-3 py-2">AP Licenses Used</th>
                  <th className="px-3 py-2">Switch Licenses Used</th>
                </tr>
              </thead>
              <tbody>
                {readable.map(r => (
                  <tr key={r.controller_id} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium text-gray-900">{r.controller_name}</td>
                    <td className="px-3 py-2">{formatCount(r.total_aps)}</td>
                    <td className="px-3 py-2">{formatCount(r.aps_online)}</td>
                    <td className="px-3 py-2">{ofTotal(r.zones_with_online_aps, r.total_zones)}</td>
                    <td className="px-3 py-2">{formatCount(r.total_switches)}</td>
                    <td className="px-3 py-2">{formatCount(r.switches_online)}</td>
                    <td className="px-3 py-2">{ofTotal(r.switch_groups_with_online, r.switch_groups)}</td>
                    <td className="px-3 py-2">{usageCell(r.ap_license)}</td>
                    <td className="px-3 py-2">{usageCell(r.switch_license)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="bg-white rounded-lg shadow overflow-x-auto mb-6">
            <h3 className="px-3 pt-3 pb-2 text-sm font-medium text-gray-700">License Usage</h3>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase">
                <tr>
                  <th className="px-3 py-2">Controller</th>
                  <th className="px-3 py-2">License Type</th>
                  <th className="px-3 py-2">Used / Total</th>
                  <th className="px-3 py-2">Remaining</th>
                </tr>
              </thead>
              <tbody>
                {readable.flatMap(r => r.licenses.map(usage => (
                  <tr key={`${r.controller_id}-${usage.license_type}`} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium text-gray-900">{r.controller_name}</td>
                    <td className="px-3 py-2">{usage.license_type}</td>
                    <td className="px-3 py-2">{usageCell(usage)}</td>
                    <td className="px-3 py-2">{(usage.total - usage.used).toLocaleString()}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>

          <div className="bg-white rounded-lg shadow overflow-x-auto mb-6">
            <h3 className="px-3 pt-3 pb-2 text-sm font-medium text-gray-700">Licenses</h3>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase">
                <tr>
                  <th className="px-3 py-2">Controller</th>
                  <th className="px-3 py-2">License</th>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2">Count</th>
                  <th className="px-3 py-2">Starts</th>
                  <th className="px-3 py-2">Expires</th>
                </tr>
              </thead>
              <tbody>
                {readable.flatMap(r => r.entitlements.map((entitlement, i) => (
                  <tr key={`${r.controller_id}-${i}`} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium text-gray-900">{r.controller_name}</td>
                    <td className="px-3 py-2 font-mono text-xs">{entitlement.name}</td>
                    <td className="px-3 py-2 text-gray-600">{entitlement.description}</td>
                    <td className="px-3 py-2">{entitlement.count.toLocaleString()}</td>
                    <td className="px-3 py-2 text-gray-600">{entitlement.starts || '-'}</td>
                    <td className="px-3 py-2">{expiryCell(entitlement)}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

// Refresh mode options. 'basic' never reaches the job API; it has its own endpoint.
type RefreshMode = 'full' | 'incremental' | 'cached_only' | 'switches_only' | 'basic';

const REFRESH_MODE_INFO: Record<RefreshMode, { label: string; description: string; icon: string }> = {
  basic: {
    label: 'License/Capacity',
    description: 'Licenses and overall AP/switch counts only. Takes seconds, no zone data',
    icon: '📊'
  },
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
  },
  switches_only: {
    label: 'Switches Only',
    description: 'Use cached zones, refresh only switch data and re-match',
    icon: '🔌'
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
  zones_with_switch_matches: number;
  total_switch_groups_matched: number;
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
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'PARTIAL' | 'FAILED' | 'CANCELLED';
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

// A past or running audit job, from /sz/audit/history
interface AuditHistoryItem {
  job_id: string;
  controller_id: number;
  controller_name: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  total_zones?: number | null;
  total_aps?: number | null;
  errors: string[];
}

const TERMINAL_STATUSES = new Set(['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED']);

// Stored job results expire 24h after the audit finishes
const RESULT_TTL_MS = 24 * 60 * 60 * 1000;

// Backend timestamps are naive UTC ("2026-10-27T22:06:34.5")
const parseUtc = (s: string) => new Date(/(Z|[+-]\d\d:\d\d)$/.test(s) ? s : `${s}Z`);

const formatAgo = (date: Date) => {
  const mins = Math.round((Date.now() - date.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
};

const formatDuration = (ms: number) => {
  const mins = Math.round(ms / 60000);
  if (mins < 1) return '<1m';
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
};

// Main Page Component
export default function SZAudit() {
  const { controllers } = useAuth();
  const [selectedControllers, setSelectedControllers] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SZAuditResult[]>([]);
  const [basicResults, setBasicResults] = useState<BasicAuditResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedZones, setExpandedZones] = useState<Set<string>>(new Set());

  // Async job tracking
  const [activeJobs, setActiveJobs] = useState<AuditJob[]>([]);

  // Recent audit jobs, and per-job "Load result" state
  const [history, setHistory] = useState<AuditHistoryItem[]>([]);
  const [loadingJobId, setLoadingJobId] = useState<string | null>(null);
  const [historyErrors, setHistoryErrors] = useState<Record<string, string>>({});

  // Stops polling once the page is left; the job keeps running server-side
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

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

  // Extract mappings from zones when results come in
  // Only load user-confirmed mappings (user_set_mapping: true)
  // Auto-matched zones show as suggestions, not pre-populated selections
  useEffect(() => {
    if (results.length > 0) {
      const newMappings: Record<number, ZoneSwitchGroupMapping> = {};
      for (const result of results) {
        if (!result.error && result.zones) {
          const mappings: ZoneSwitchGroupMapping = {};
          let userSetCount = 0;
          for (const zone of result.zones) {
            // Only load mappings that user explicitly confirmed
            if (zone.user_set_mapping && zone.matched_switch_groups && zone.matched_switch_groups.length > 0) {
              userSetCount++;
              mappings[zone.zone_id] = zone.matched_switch_groups[0].id;
            }
          }
          console.log(`[SZAudit] Loaded ${userSetCount} user-confirmed mappings for controller ${result.controller_id}`);
          newMappings[result.controller_id] = mappings;
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
          const statusResponse = await apiFetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}`);
          if (statusResponse.ok) {
            const data = await statusResponse.json();
            newStatus[controllerId] = data;

            // If there are cached zones, fetch their details
            if (data.has_cache && data.cached_zones > 0) {
              const zonesResponse = await apiFetch(`${API_BASE_URL}/sz/audit/cache/${controllerId}/zones`);
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

  // A results entry for a controller whose audit produced no data
  const errorResult = (controllerId: number, controllerName: string, message: string): SZAuditResult => ({
    controller_id: controllerId,
    controller_name: controllerName,
    host: '',
    timestamp: new Date().toISOString(),
    error: message,
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

  const fetchResult = async (url: string): Promise<SZAuditResult | null> => {
    try {
      const response = await apiFetch(url);
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  };

  const loadHistory = async () => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/sz/audit/history?limit=10`);
      if (response.ok) {
        const data = await response.json();
        setHistory(data.audits || []);
      }
    } catch (err) {
      console.error('Failed to load audit history:', err);
    }
  };

  // Recent audits, so a run the page lost track of (reload, closed tab) can
  // be watched again or its result loaded
  useEffect(() => {
    loadHistory();
  }, []);

  // Poll jobs until each one finishes. Used by a fresh run and by watching a
  // job again after the page lost it; the job runs server-side either way.
  const watchJobs = async (jobs: AuditJob[]) => {
    setLoading(true);
    setError(null);
    setResults([]);
    setBasicResults([]);
    setExpandedZones(new Set());
    setFinalStats(null);
    setActiveJobs([...jobs]);

    try {
      const completedResults: SZAuditResult[] = [];
      const pendingJobs = jobs.filter(j => j.job_id && !TERMINAL_STATUSES.has(j.status));

      while (pendingJobs.length > 0 && mountedRef.current) {
        await new Promise(resolve => setTimeout(resolve, 2000)); // Poll every 2 seconds

        for (let i = pendingJobs.length - 1; i >= 0; i--) {
          const job = pendingJobs[i];

          try {
            // Get job status
            const statusResponse = await apiFetch(`${API_BASE_URL}/sz/audit/jobs/${job.job_id}/status`);

            if (statusResponse.status === 404) {
              completedResults.push(errorResult(job.controller_id, job.controller_name, 'Audit job no longer exists'));
              setResults([...completedResults]);
              pendingJobs.splice(i, 1);
              continue;
            }
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

            if (statusData.status === 'COMPLETED' || statusData.status === 'PARTIAL') {
              // Capture final stats before moving on
              setFinalStats({
                api_stats: statusData.api_stats,
                cache_stats: statusData.cache_stats
              });

              // Read from the cache (audit updates cache, we read from it);
              // fall back to the job's own stored result
              const result =
                await fetchResult(`${API_BASE_URL}/sz/audit/result/${job.controller_id}`) ||
                await fetchResult(`${API_BASE_URL}/sz/audit/jobs/${job.job_id}/result`);
              completedResults.push(result || errorResult(
                job.controller_id,
                job.controller_name,
                'Audit finished but its result could not be loaded. Try Load result under Recent audits.'
              ));
              setResults([...completedResults]);
              pendingJobs.splice(i, 1);
            } else if (statusData.status === 'FAILED' || statusData.status === 'CANCELLED') {
              completedResults.push(errorResult(
                job.controller_id,
                job.controller_name,
                statusData.status === 'CANCELLED'
                  ? 'Audit cancelled'
                  : (job.errors.join('; ') || 'Audit failed')
              ));
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
          completedResults.push(errorResult(
            job.controller_id,
            job.controller_name,
            job.errors.join('; ') || 'Failed to start audit'
          ));
        }
      }

      setResults(completedResults);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
      setActiveJobs([]);
      loadHistory();
    }
  };

  const runBasicAudit = async () => {
    setLoading(true);
    setError(null);
    setResults([]);
    setBasicResults([]);
    setFinalStats(null);
    setActiveJobs([]);

    const fetched = await Promise.all(Array.from(selectedControllers).map(async (controllerId): Promise<BasicAuditResult> => {
      const controller = szControllers.find(c => c.id === controllerId);
      try {
        const response = await apiFetch(`${API_BASE_URL}/sz/${controllerId}/audit/basic`);
        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          throw new Error(errData.detail || `HTTP ${response.status}`);
        }
        return await response.json();
      } catch (err) {
        return {
          controller_id: controllerId,
          controller_name: controller?.name || `Controller ${controllerId}`,
          host: null,
          total_aps: null,
          aps_online: null,
          total_zones: null,
          zones_with_online_aps: null,
          total_switches: null,
          switches_online: null,
          switch_groups: null,
          switch_groups_with_online: null,
          ap_license: null,
          switch_license: null,
          licenses: [],
          entitlements: [],
          error: err instanceof Error ? err.message : 'Could not read controller',
          errors: []
        };
      }
    }));

    setBasicResults(fetched.sort((a, b) => a.controller_name.localeCompare(b.controller_name)));
    setLoading(false);
  };

  const runAudit = async () => {
    if (selectedControllers.size === 0) return;

    if (refreshMode === 'basic') {
      await runBasicAudit();
      return;
    }

    setLoading(true);
    setError(null);
    setActiveJobs([]);

    const jobs: AuditJob[] = [];

    // Start async audit jobs for all selected controllers
    for (const controllerId of Array.from(selectedControllers)) {
      const controller = szControllers.find(c => c.id === controllerId);
      try {
        const response = await apiFetch(`${API_BASE_URL}/sz/audit/async`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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

    await watchJobs(jobs);
  };

  const watchHistoryJob = (item: AuditHistoryItem) => {
    watchJobs([{
      job_id: item.job_id,
      controller_id: item.controller_id,
      controller_name: item.controller_name,
      status: 'RUNNING',
      errors: []
    }]);
  };

  // Load a finished audit's result. The newest run for a controller reads the
  // zone cache (it carries switch mappings set since); older runs read their
  // own stored snapshot.
  const loadHistoryResult = async (item: AuditHistoryItem) => {
    setLoadingJobId(item.job_id);
    setHistoryErrors(prev => {
      const next = { ...prev };
      delete next[item.job_id];
      return next;
    });

    const newest = history.find(h => h.controller_id === item.controller_id && h.status === 'COMPLETED');
    let result: SZAuditResult | null = null;
    if (newest?.job_id === item.job_id) {
      result = await fetchResult(`${API_BASE_URL}/sz/audit/result/${item.controller_id}`);
    }
    if (!result) {
      try {
        const response = await apiFetch(`${API_BASE_URL}/sz/audit/jobs/${item.job_id}/result`);
        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          throw new Error(errData.detail || `HTTP ${response.status}`);
        }
        result = await response.json();
      } catch (err) {
        setHistoryErrors(prev => ({
          ...prev,
          [item.job_id]: err instanceof Error ? err.message : 'Could not load result'
        }));
      }
    }

    if (result) {
      const loaded = result;
      setError(null);
      setFinalStats(null);
      setBasicResults([]);
      setResults(prev => [...prev.filter(r => r.controller_id !== loaded.controller_id), loaded]);
    }
    setLoadingJobId(null);
  };

  const cancelJob = async (jobId: string) => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/sz/audit/jobs/${jobId}/cancel`, {
        method: 'POST',
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

  const exportBasicCsv = () => {
    const escape = (value: string | number | null) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const rows: (string | number | null)[][] = [
      ["Controller", "Host", "Category", "Item", "Description", "Used/Online", "Total/Count", "Starts", "Expires", "Errors"]
    ];

    for (const r of basicResults) {
      const errors = [r.error, ...r.errors].filter(Boolean).join('; ');
      if (r.error) {
        rows.push([r.controller_name, r.host, "Error", "", "", null, null, null, null, errors]);
        continue;
      }
      rows.push([r.controller_name, r.host, "Devices", "APs", "", r.aps_online, r.total_aps, null, null, errors]);
      rows.push([r.controller_name, r.host, "Devices", "Zones with online APs", "", r.zones_with_online_aps, r.total_zones, null, null, errors]);
      rows.push([r.controller_name, r.host, "Devices", "Switches", "", r.switches_online, r.total_switches, null, null, errors]);
      rows.push([r.controller_name, r.host, "Devices", "Switch groups with online switches", "", r.switch_groups_with_online, r.switch_groups, null, null, errors]);
      for (const usage of r.licenses) {
        rows.push([r.controller_name, r.host, "License usage", usage.license_type, "", usage.used, usage.total, null, null, ""]);
      }
      for (const e of r.entitlements) {
        rows.push([r.controller_name, r.host, "License", e.name, e.description, null, e.count, e.starts, e.perpetual ? "Perpetual" : e.expires, ""]);
      }
    }

    const blob = new Blob([rows.map(row => row.map(escape).join(",")).join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    a.download = `sz_audit_basic_${timestamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  const exportCsv = () => {
    if (basicResults.length > 0) {
      exportBasicCsv();
      return;
    }
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
        const wlansWpa2Psk = (wlanTypes["WPA2-PSK"] || 0) + (wlanTypes["WPA2-PSK + Portal"] || 0);
        const wlansWpa2Ent = wlanTypes["WPA2-Enterprise"] || 0;
        const wlansWpa3Sae = (wlanTypes["WPA3-SAE"] || 0) + (wlanTypes["WPA3"] || 0);
        const wlansWpa3Ent = wlanTypes["WPA3-Enterprise"] || 0;
        const wlansDpsk = (wlanTypes["DPSK"] || 0) + (wlanTypes["External DPSK"] || 0);
        const knownTypes = new Set(["Open", "Open + Portal", "WPA2-PSK", "WPA2-Enterprise", "WPA3-SAE", "WPA3", "WPA3-Enterprise", "DPSK", "External DPSK", "WPA2-PSK + Portal"]);
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
                    <option value="switches_only">{REFRESH_MODE_INFO.switches_only.icon} {REFRESH_MODE_INFO.switches_only.label}</option>
                    <option value="cached_only">{REFRESH_MODE_INFO.cached_only.icon} {REFRESH_MODE_INFO.cached_only.label}</option>
                    <option value="basic">{REFRESH_MODE_INFO.basic.icon} {REFRESH_MODE_INFO.basic.label}</option>
                  </select>
                </div>
                {/* Mode description */}
                <div className="text-xs text-gray-500">
                  {REFRESH_MODE_INFO[refreshMode].description}
                </div>
                {/* Cache status on separate line */}
                {refreshMode !== 'basic' && selectedControllers.size > 0 && Object.keys(cacheStatus).length > 0 && (
                  <div className="flex items-center gap-4 text-xs">
                    {(() => {
                      const statuses = Object.values(cacheStatus);
                      const withCache = statuses.filter(s => s.has_cache);
                      if (withCache.length === 0) {
                        return <span className="text-gray-400">No cached data</span>;
                      }
                      const totalZones = withCache.reduce((sum, s) => sum + s.cached_zones, 0);
                      const oldestAge = Math.max(...withCache.map(s => s.cache_age_minutes || 0));
                      const totalSwitchMatches = withCache.reduce((sum, s) => sum + s.total_switch_groups_matched, 0);
                      const zonesWithMatches = withCache.reduce((sum, s) => sum + s.zones_with_switch_matches, 0);
                      const ageStr = oldestAge > 0 ? ` (${oldestAge < 60 ? `${oldestAge}m` : `${Math.round(oldestAge/60)}h`} old)` : '';

                      return (
                        <>
                          <span className="text-blue-600">
                            📦 {totalZones} zones cached{ageStr}
                          </span>
                          <span className="text-emerald-600">
                            🔌 {totalSwitchMatches} switch groups matched to {zonesWithMatches} zones
                          </span>
                        </>
                      );
                    })()}
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
                  </div>
                )}
              </div>
              <button
                onClick={exportCsv}
                disabled={(results.length === 0 && basicResults.length === 0) || loading}
                className={`px-4 py-2 rounded-lg font-medium transition flex items-center gap-2 ${
                  (results.length === 0 && basicResults.length === 0) || loading
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

        {/* Recent Audits - pick a run back up after the page lost track of it */}
        {history.length > 0 && (
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
              <History className="w-4 h-4 text-gray-500" />
              Recent Audits
            </h3>
            <div className="divide-y divide-gray-100">
              {history.map(item => {
                const created = parseUtc(item.created_at);
                const completed = item.completed_at ? parseUtc(item.completed_at) : null;
                const isRunning = !TERMINAL_STATUSES.has(item.status);
                const isWatching = activeJobs.some(j => j.job_id === item.job_id);
                const resultExpired = !completed || Date.now() - completed.getTime() > RESULT_TTL_MS;
                const statusClass =
                  item.status === 'COMPLETED' ? 'bg-green-100 text-green-700' :
                  item.status === 'FAILED' ? 'bg-red-100 text-red-700' :
                  item.status === 'CANCELLED' ? 'bg-orange-100 text-orange-700' :
                  'bg-blue-100 text-blue-700';

                return (
                  <div key={item.job_id} className="py-2 flex items-center gap-3 text-sm">
                    <span className="font-medium text-gray-900 truncate w-48" title={item.controller_name}>
                      {item.controller_name}
                    </span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusClass}`}>
                      {item.status.toLowerCase()}
                    </span>
                    <span className="text-gray-500 text-xs" title={created.toLocaleString()}>
                      started {formatAgo(created)}
                      {completed && ` · took ${formatDuration(completed.getTime() - created.getTime())}`}
                    </span>
                    {item.total_zones != null && (
                      <span className="text-gray-500 text-xs">
                        {item.total_zones} zones · {item.total_aps ?? 0} APs
                      </span>
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      {historyErrors[item.job_id] && (
                        <span className="text-xs text-red-600">{historyErrors[item.job_id]}</span>
                      )}
                      {isRunning && !isWatching && (
                        <button
                          onClick={() => watchHistoryJob(item)}
                          disabled={loading}
                          className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Watch progress
                        </button>
                      )}
                      {item.status === 'COMPLETED' && (
                        resultExpired ? (
                          <span className="text-xs text-gray-400" title="Stored results last 24h; Cached only mode may still have this controller's zones">
                            result expired
                          </span>
                        ) : (
                          <button
                            onClick={() => loadHistoryResult(item)}
                            disabled={loading || loadingJobId !== null}
                            className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50 text-gray-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                          >
                            {loadingJobId === item.job_id && <Loader2 className="w-3 h-3 animate-spin" />}
                            Load result
                          </button>
                        )
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

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

        {/* Basic mode results */}
        {basicResults.length > 0 && <BasicAuditResults results={basicResults} />}

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
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium text-gray-600 flex items-center gap-1">
                    <Link2 className="w-4 h-4" />
                    Map Switch Groups:
                  </span>
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
                          {result.controller_name}
                          <span className="text-xs bg-indigo-600 text-white px-1.5 rounded-full">
                            {mappingCount}/{zoneCount}
                          </span>
                        </button>
                      );
                    })}
                  </div>
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
        {!loading && results.length === 0 && basicResults.length === 0 && !error && (
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

        // Build zone list for mapper (include matched_switch_groups for auto-matching)
        const zones = result.zones.map(z => ({
          zone_id: z.zone_id,
          zone_name: z.zone_name,
          domain_name: z.domain_name,
          matched_switch_groups: z.matched_switch_groups
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
