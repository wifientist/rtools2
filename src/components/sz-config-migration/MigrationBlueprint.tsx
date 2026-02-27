import { useState, useEffect, useRef } from 'react';
import {
  ChevronDown,
  ArrowRight,
  Wifi,
  Lock,
  Globe,
  Shield,
  Key,
  Radio,
  Loader2,
  Server,
  Target,
} from 'lucide-react';
import { apiGet } from '@/utils/api';
import type {
  WizardState,
  ResolverResult,
  TypeMapping,
  PlanResult,
  WLANActivation,
} from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
}

// SZ WLAN from snapshot (subset of fields we care about)
interface SZWlanInfo {
  id: string;
  name: string;
  ssid: string;
  auth_type: string;
  encryption_method: string | null;
  vlan_id: number | null;
  description: string | null;
  auth_service_id: string | null;
  accounting_service_id: string | null;
  dpsk: any | null;
  external_dpsk: any | null;
  radius_options: any | null;
  vlan: any | null;
}

const TYPE_ICONS: Record<string, React.ReactNode> = {
  psk: <Lock size={14} className="text-blue-600" />,
  open: <Globe size={14} className="text-green-600" />,
  aaa: <Shield size={14} className="text-purple-600" />,
  dpsk: <Key size={14} className="text-orange-600" />,
};

const R1_TYPE_LABELS: Record<string, string> = {
  psk: 'PSK Network',
  open: 'Open Network',
  aaa: 'Enterprise (RADIUS)',
  dpsk: 'DPSK Network',
};

const R1_TYPE_COLORS: Record<string, string> = {
  psk: 'bg-blue-50 border-blue-200',
  open: 'bg-green-50 border-green-200',
  aaa: 'bg-purple-50 border-purple-200',
  dpsk: 'bg-orange-50 border-orange-200',
};

export default function MigrationBlueprint({ state }: Props) {
  const [snapshot, setSnapshot] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedWlans, setExpandedWlans] = useState<Set<string>>(new Set());
  const snapshotJobRef = useRef<string | null>(null);

  // Fetch SZ snapshot for detailed WLAN info (re-fetches when job ID changes)
  useEffect(() => {
    if (!state.extractionJobId) return;
    // Skip if we already have the snapshot for this exact job
    if (snapshotJobRef.current === state.extractionJobId && snapshot) return;

    // Clear stale snapshot from a different job
    if (snapshotJobRef.current !== state.extractionJobId) {
      setSnapshot(null);
      setError(null);
    }
    snapshotJobRef.current = state.extractionJobId;

    setLoading(true);
    apiGet<any>(`${API_URL}/sz-migration/snapshot/${state.extractionJobId}`)
      .then(d => setSnapshot(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [state.extractionJobId]);

  const resolver = state.resolverResult;
  const mappings = state.typeMappings;
  const plan = state.planResult;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-500 gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Loading snapshot data...</span>
      </div>
    );
  }

  if (error) {
    return <div className="text-red-600 text-sm py-4">Failed to load snapshot: {error}</div>;
  }

  if (!mappings || !resolver) {
    return <div className="text-gray-500 text-sm py-4">Resolve must complete before viewing the blueprint.</div>;
  }

  // Build lookup maps
  const szWlanMap = new Map<string, SZWlanInfo>();
  if (snapshot?.wlans) {
    for (const w of snapshot.wlans) {
      szWlanMap.set(w.id, w);
      // Also index by name for fallback matching
      szWlanMap.set(`name:${w.name}`, w);
    }
  }

  const refMap: Record<string, any> = snapshot?.referenced_objects || {};

  // Build per-WLAN activation map: wlan_id -> activations
  const activationsByWlan = new Map<string, WLANActivation[]>();
  for (const act of resolver.activations) {
    const list = activationsByWlan.get(act.wlan_id) || [];
    list.push(act);
    activationsByWlan.set(act.wlan_id, list);
  }

  // Build action map from plan: name -> action details
  const actionMap = new Map<string, { action: string; details?: string }>();
  if (plan?.actions) {
    for (const a of plan.actions) {
      const name = a.resource_name || a.name || '';
      if (name) actionMap.set(name, { action: a.action, details: a.details || a.notes });
    }
  }

  const toggleWlan = (id: string) => {
    setExpandedWlans(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const expandAll = () => {
    setExpandedWlans(new Set(Object.keys(mappings)));
  };

  const collapseAll = () => {
    setExpandedWlans(new Set());
  };

  const entries = Object.entries(mappings);

  return (
    <div className="space-y-2">
      {/* Controls */}
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500">{entries.length} WLANs</span>
        <div className="flex gap-2">
          <button onClick={expandAll} className="text-[11px] text-blue-600 hover:text-blue-800">
            Expand all
          </button>
          <button onClick={collapseAll} className="text-[11px] text-gray-500 hover:text-gray-700">
            Collapse all
          </button>
        </div>
      </div>

      {entries.map(([wlanId, mapping]) => {
        const isExpanded = expandedWlans.has(wlanId);
        const szWlan = szWlanMap.get(wlanId) || szWlanMap.get(`name:${mapping.wlan_name}`);
        const activations = activationsByWlan.get(wlanId) || [];
        const planAction = actionMap.get(mapping.wlan_name);

        return (
          <WlanBlueprintCard
            key={wlanId}
            wlanId={wlanId}
            mapping={mapping}
            szWlan={szWlan || null}
            activations={activations}
            planAction={planAction || null}
            refMap={refMap}
            isExpanded={isExpanded}
            onToggle={() => toggleWlan(wlanId)}
          />
        );
      })}
    </div>
  );
}

// ── Per-WLAN Blueprint Card ───────────────────────────────────────

interface CardProps {
  wlanId: string;
  mapping: TypeMapping;
  szWlan: SZWlanInfo | null;
  activations: WLANActivation[];
  planAction: { action: string; details?: string } | null;
  refMap: Record<string, any>;
  isExpanded: boolean;
  onToggle: () => void;
}

function WlanBlueprintCard({ wlanId, mapping, szWlan, activations, planAction, refMap, isExpanded, onToggle }: CardProps) {
  const action = planAction?.action || 'create';
  const borderColor = R1_TYPE_COLORS[mapping.r1_network_type] || 'bg-gray-50 border-gray-200';

  return (
    <div className={`border rounded-lg overflow-hidden ${borderColor}`}>
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-white/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <ChevronDown
            size={14}
            className={`text-gray-400 transition-transform ${isExpanded ? 'rotate-0' : '-rotate-90'}`}
          />
          <div className="flex items-center gap-2">
            {TYPE_ICONS[mapping.r1_network_type] || <Wifi size={14} />}
            <span className="font-semibold text-sm">{mapping.wlan_name}</span>
          </div>
          {szWlan && (
            <span className="text-xs text-gray-400">SSID: {szWlan.ssid}</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Auth badge */}
          <span className="text-[10px] bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded font-medium">
            {mapping.sz_auth_type}
          </span>
          <ArrowRight size={12} className="text-gray-400" />
          <span className="text-[10px] bg-white/80 text-gray-700 px-1.5 py-0.5 rounded font-medium border">
            {R1_TYPE_LABELS[mapping.r1_network_type] || mapping.r1_network_type}
          </span>
          {/* Action badge */}
          {action === 'create' ? (
            <span className="text-[10px] text-blue-700 bg-blue-100 px-1.5 py-0.5 rounded font-bold">+ Create</span>
          ) : (
            <span className="text-[10px] text-green-700 bg-green-100 px-1.5 py-0.5 rounded font-bold">= Reuse</span>
          )}
        </div>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t px-4 py-3 bg-white/60">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-4">
            {/* SZ Side */}
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <Server size={13} className="text-blue-600" />
                <span className="text-xs font-semibold text-blue-800 uppercase tracking-wider">SmartZone</span>
              </div>
              <div className="space-y-1.5 text-xs">
                <KV label="Name" value={mapping.wlan_name} />
                {szWlan && (
                  <>
                    <KV label="SSID" value={szWlan.ssid} />
                    <KV label="Auth Type" value={szWlan.auth_type} />
                    <KV label="Encryption" value={szWlan.encryption_method} />
                    <KV label="VLAN" value={szWlan.vlan_id} />
                    {szWlan.description && <KV label="Description" value={szWlan.description} />}

                    {/* Auth Service reference */}
                    {szWlan.auth_service_id && (
                      <RefKV
                        label="Auth Service"
                        refType="auth_service"
                        refId={szWlan.auth_service_id}
                        refMap={refMap}
                      />
                    )}
                    {szWlan.accounting_service_id && (
                      <RefKV
                        label="Accounting"
                        refType="accounting_service"
                        refId={szWlan.accounting_service_id}
                        refMap={refMap}
                      />
                    )}

                    {/* DPSK details */}
                    {szWlan.dpsk && (
                      <div className="bg-orange-50 border border-orange-100 rounded p-2 mt-1">
                        <span className="font-semibold text-orange-700 text-[11px]">DPSK Config</span>
                        <div className="mt-1 space-y-0.5 text-[11px]">
                          {szWlan.dpsk.type && <KV label="Type" value={szWlan.dpsk.type} />}
                          {szWlan.dpsk.length && <KV label="Key Length" value={szWlan.dpsk.length} />}
                        </div>
                      </div>
                    )}

                    {/* RADIUS options */}
                    {szWlan.radius_options && (
                      <div className="bg-purple-50 border border-purple-100 rounded p-2 mt-1">
                        <span className="font-semibold text-purple-700 text-[11px]">RADIUS Options</span>
                        <div className="mt-1 text-[11px] text-purple-800 font-mono">
                          {Object.entries(szWlan.radius_options).filter(([, v]) => v != null).slice(0, 4).map(([k, v]) => (
                            <div key={k}>{k}: {String(v)}</div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {!szWlan && (
                  <div className="text-gray-400 italic text-[11px]">
                    Snapshot not available — showing mapping data only
                  </div>
                )}
              </div>
            </div>

            {/* Arrow divider */}
            <div className="hidden md:flex flex-col items-center justify-center">
              <div className="w-px h-8 bg-gray-300" />
              <ArrowRight size={16} className="text-gray-400 my-1" />
              <div className="w-px h-8 bg-gray-300" />
            </div>
            <div className="md:hidden border-t border-dashed border-gray-300 my-1" />

            {/* R1 Side */}
            <div>
              <div className="flex items-center gap-1.5 mb-2">
                <Target size={13} className="text-green-600" />
                <span className="text-xs font-semibold text-green-800 uppercase tracking-wider">RuckusONE</span>
              </div>
              <div className="space-y-1.5 text-xs">
                <KV label="Network Type" value={R1_TYPE_LABELS[mapping.r1_network_type] || mapping.r1_network_type} />
                <div>
                  <span className="text-gray-500">Action:</span>{' '}
                  {action === 'create' ? (
                    <span className="font-semibold text-blue-700">Create new WiFi network</span>
                  ) : (
                    <span className="font-semibold text-green-700">Reuse existing network</span>
                  )}
                </div>

                {mapping.dpsk_type && (
                  <div className="bg-orange-50 border border-orange-100 rounded p-2">
                    <span className="font-semibold text-orange-700 text-[11px]">DPSK Mode:</span>{' '}
                    <span className="text-[11px]">{mapping.dpsk_type}</span>
                  </div>
                )}

                {mapping.notes && (
                  <div className="text-[11px] text-gray-600 bg-gray-50 rounded p-2">
                    {mapping.notes}
                  </div>
                )}

                {mapping.needs_user_decision && (
                  <div className="text-[11px] bg-amber-100 text-amber-700 rounded px-2 py-1 font-medium">
                    Requires user decision
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* AP Group Activations */}
          {activations.length > 0 && (
            <div className="mt-3 pt-3 border-t border-dashed">
              <div className="flex items-center gap-1.5 mb-2">
                <Radio size={13} className="text-gray-600" />
                <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
                  AP Group Activations ({activations.length})
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {activations.map((act, i) => (
                  <div
                    key={i}
                    className="bg-gray-50 border rounded px-2.5 py-1.5 text-[11px] flex items-center gap-2"
                  >
                    <span className="font-medium">{act.ap_group_name}</span>
                    <span className="text-gray-400">({act.ap_count} APs)</span>
                    <div className="flex gap-0.5">
                      {act.radios.map(r => (
                        <span
                          key={r}
                          className={`px-1 py-0.5 rounded text-[9px] font-bold ${
                            r === '2.4' ? 'bg-yellow-100 text-yellow-700' :
                            r === '5' ? 'bg-blue-100 text-blue-700' :
                            r === '6' ? 'bg-purple-100 text-purple-700' :
                            'bg-gray-100 text-gray-600'
                          }`}
                        >
                          {r}G
                        </span>
                      ))}
                    </div>
                    {act.source === 'ap_group_override' && (
                      <span className="text-[9px] text-orange-500 font-medium">override</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────

function KV({ label, value }: { label: string; value: any }) {
  if (value == null) return null;
  return (
    <div>
      <span className="text-gray-500">{label}:</span>{' '}
      <span className="font-medium">{String(value)}</span>
    </div>
  );
}

function RefKV({ label, refType, refId, refMap }: { label: string; refType: string; refId: string; refMap: Record<string, any> }) {
  const key = `${refType}:${refId}`;
  const obj = refMap[key];
  return (
    <div className="flex items-center gap-1">
      <span className="text-gray-500">{label}:</span>
      <span className="font-medium">{obj?.name || refId.slice(0, 12)}</span>
    </div>
  );
}
