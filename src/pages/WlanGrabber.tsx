import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, Import,
  CheckCircle, AlertTriangle, XCircle, RefreshCw, Plus, KeyRound,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/wlan_grabber.py) ─────────────────────────

type RowStatus = 'create' | 'review' | 'exists' | 'blocked';
type R1Type = 'psk' | 'open' | 'aaa' | 'dpsk' | 'unsupported';

interface GrabRow {
  sz_wlan_id: string;
  sz_name: string;
  sz_ssid: string;
  auth_type: string;
  r1_type: R1Type;
  security: string | null;
  vlan_id: number;
  auth_service_id: string | null;
  accounting_service_id: string | null;
  r1: { network_id: string; name: string; type: string | null; venue_count: number; active_in_venue: boolean | null } | null;
  suggested_dpsk_pool_id?: string;
  status: RowStatus;
  notes: string[];
}

interface RadiusProfile { id: string; name: string; type: string; primary: string | null; placeholder_secret: boolean }

interface RadiusService {
  id: string;
  name: string;
  roles: string[];
  primary: string | null;
  secondary: string | null;
  used_by: { wlan_name: string; role: string }[];
  extraction_error: boolean;
  suggestion: { action: 'create' | 'existing' | 'skip'; r1_profile_id: string | null; reason: string };
}

interface Preview {
  zone_name: string;
  venue_name: string | null;
  rows: GrabRow[];
  counts: Record<RowStatus, number>;
  radius_services: RadiusService[];
  r1_radius_profiles: RadiusProfile[];
  dpsk_pools: { id: string; name: string }[];
  warnings: string[];
}

interface RadiusChoice { action: 'create' | 'existing' | 'skip'; r1_profile_id: string | null; secret: string }

interface ApplyResult {
  sz_wlan_id: string;
  name: string;
  type: string;
  status: 'created' | 'exists' | 'failed';
  network_id?: string;
  error?: string;
  warnings: string[];
}

interface RadiusResult { sz_service_id: string; name: string; status: 'created' | 'exists' | 'failed'; error?: string }

const STATUS: Record<RowStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  create:  { label: 'Create',  cls: 'text-indigo-700 bg-indigo-50 border-indigo-200', icon: Plus },
  review:  { label: 'Review',  cls: 'text-amber-700 bg-amber-50 border-amber-200', icon: AlertTriangle },
  exists:  { label: 'In R1',   cls: 'text-green-700 bg-green-50 border-green-200', icon: CheckCircle },
  blocked: { label: 'Blocked', cls: 'text-gray-500 bg-gray-50 border-gray-200', icon: XCircle },
};

const TYPE_LABEL: Record<R1Type, string> = {
  psk: 'PSK', open: 'Open', aaa: 'Enterprise', dpsk: 'DPSK', unsupported: '—',
};

// ── Page ─────────────────────────────────────────────────────────

export default function WlanGrabber() {
  const {
    activeControllerId: aId, activeControllerName: aName, activeControllerType: aType,
    activeControllerSubtype: aSub,
    secondaryControllerId: sId, secondaryControllerName: sName, secondaryControllerType: sType,
    secondaryControllerSubtype: sSub,
    controllers,
  } = useAuth();

  // One R1 and one SZ controller among the two selected, in either slot.
  const slots = [
    { id: aId, name: aName, type: aType, subtype: aSub },
    { id: sId, name: sName, type: sType, subtype: sSub },
  ];
  const r1Slot = slots.find(s => s.id && s.type === 'RuckusONE');
  const szSlot = slots.find(s => s.id && s.type === 'SmartZone');
  const r1Id = r1Slot?.id ?? null;
  const szId = szSlot?.id ?? null;
  const isMSP = r1Slot?.subtype === 'MSP';
  const r1Controller = controllers?.find((c: any) => c.id === r1Id);

  const [domainId, setDomainId] = useState<string | null>(null);
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [ecId, setEcId] = useState<string | null>(null);
  const [venueId, setVenueId] = useState<string | null>(null);
  const tenantId = isMSP ? ecId : (r1Controller?.r1_tenant_id || null);

  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pools, setPools] = useState<Record<string, string>>({});          // sz_wlan_id -> pool id ('' = none)
  const [radius, setRadius] = useState<Record<string, RadiusChoice>>({});  // SZ service id -> choice
  const [results, setResults] = useState<{ results: ApplyResult[]; radius_results: RadiusResult[] } | null>(null);
  const [confirming, setConfirming] = useState(false);

  const ready = !!szId && !!r1Id && !!zoneId && (!isMSP || !!ecId);

  const clearPreview = () => { setPreview(null); setSelected(new Set()); setResults(null); };

  const loadPreview = async () => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const data = await apiPost<Preview>(`${API_URL}/wlan-grabber/preview`, {
        sz_controller_id: szId, zone_id: zoneId,
        r1_controller_id: r1Id, tenant_id: tenantId, venue_id: venueId,
      });
      setPreview(data);
      // Preselect only the plain creates; review rows are opt-in.
      setSelected(new Set(data.rows.filter(r => r.status === 'create').map(r => r.sz_wlan_id)));
      setPools(Object.fromEntries(data.rows.filter(r => r.r1_type === 'dpsk')
        .map(r => [r.sz_wlan_id, r.suggested_dpsk_pool_id || ''])));
      setRadius(Object.fromEntries(data.radius_services.map(s => [s.id, {
        action: s.suggestion.action, r1_profile_id: s.suggestion.r1_profile_id, secret: '',
      }])));
    } catch (e: any) {
      setError(e.message || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  // An Enterprise row can only be created once its auth service maps to an R1 profile.
  const radiusReady = (r: GrabRow) => {
    if (r.r1_type !== 'aaa') return true;
    const c = r.auth_service_id ? radius[r.auth_service_id] : undefined;
    if (!c) return false;
    if (c.action === 'existing') return !!c.r1_profile_id;
    if (c.action === 'create') return c.secret.length > 0;
    return false;
  };
  const selectable = (r: GrabRow) => (r.status === 'create' || r.status === 'review') && radiusReady(r);

  const chosen = useMemo(
    () => (preview?.rows || []).filter(r => selected.has(r.sz_wlan_id) && selectable(r)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [preview, selected, radius],
  );

  const applyChanges = async () => {
    setConfirming(false);
    if (!chosen.length) return;
    setApplying(true);
    setError(null);
    // Only the services the chosen Enterprise rows use; the secret goes for 'create' only.
    const used = new Set(chosen.filter(r => r.r1_type === 'aaa')
      .flatMap(r => [r.auth_service_id, r.accounting_service_id]).filter(Boolean) as string[]);
    const radiusBody = Object.fromEntries([...used].filter(id => radius[id]).map(id => {
      const c = radius[id];
      return [id, c.action === 'create'
        ? { action: 'create', shared_secret: c.secret }
        : c.action === 'existing' ? { action: 'existing', r1_profile_id: c.r1_profile_id } : { action: 'skip' }];
    }));
    try {
      const data = await apiPost<{ results: ApplyResult[]; radius_results: RadiusResult[] }>(
        `${API_URL}/wlan-grabber/apply`, {
          sz_controller_id: szId, zone_id: zoneId, r1_controller_id: r1Id, tenant_id: tenantId,
          wlans: chosen.map(r => ({ sz_wlan_id: r.sz_wlan_id, dpsk_pool_id: pools[r.sz_wlan_id] || null })),
          radius: radiusBody,
        });
      setResults(data);
      // Secrets are single-use: forget them once sent.
      setRadius(prev => Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, secret: '' }])));
      const done = new Map(data.results.filter(x => x.status !== 'failed').map(x => [x.sz_wlan_id, x]));
      setPreview(p => p && {
        ...p,
        rows: p.rows.map(r => {
          const d = done.get(r.sz_wlan_id);
          return d ? {
            ...r, status: 'exists' as RowStatus,
            r1: { network_id: d.network_id || '', name: d.name, type: d.type, venue_count: 0, active_in_venue: null },
            notes: ['Created — not activated yet; use WLAN Activator', ...d.warnings],
          } : r;
        }),
      });
      setSelected(new Set());
    } catch (e: any) {
      setError(e.message || 'Create failed');
    } finally {
      setApplying(false);
    }
  };

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const selectableRows = (preview?.rows || []).filter(selectable);
  const allSelected = selectableRows.length > 0 && selectableRows.every(r => selected.has(r.sz_wlan_id));
  const busy = loading || applying;

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Import size={24} className="text-indigo-600" />
          WLAN Grabber
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Create in R1 the SZ WLANs it doesn't have yet. Networks are created unactivated — activate them with WLAN Activator.
        </p>
      </div>

      {(!szSlot || !r1Slot) ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start gap-2">
          <AlertCircle className="text-red-500 mt-0.5" size={18} />
          <div>
            <p className="text-red-800 font-semibold text-sm">Controller Setup Required</p>
            <p className="text-red-600 text-sm mt-1">
              Select one RuckusONE controller (primary) and one SmartZone controller (secondary).
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-4 bg-gray-50 border border-gray-200 rounded-lg p-3 text-sm">
            <Server size={16} className="text-blue-600" />
            <span className="font-medium">{szSlot.name}</span>
            <span className="text-gray-400 text-xs">(SZ)</span>
            <ArrowRight size={16} className="text-gray-400" />
            <Target size={16} className="text-green-600" />
            <span className="font-medium">{r1Slot.name}</span>
            <span className="text-gray-400 text-xs">(R1{isMSP ? ' MSP' : ''})</span>
          </div>

          <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
            <div className="space-y-6">
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                  <Server size={14} className="text-blue-600" /> SZ Zone
                </h3>
                <div className="space-y-3">
                  <SmartZoneDomainSelector
                    controllerId={szId}
                    onDomainSelect={(id) => { setDomainId(id); setZoneId(null); clearPreview(); }}
                    disabled={busy}
                  />
                  {domainId && (
                    <SmartZoneZoneSelector
                      controllerId={szId}
                      domainId={domainId}
                      onZoneSelect={(id) => { setZoneId(id); clearPreview(); }}
                      disabled={busy}
                    />
                  )}
                </div>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                  <Target size={14} className="text-green-600" /> R1 Tenant
                </h3>
                <div className="space-y-3">
                  {isMSP && (
                    <SingleEcSelector
                      controllerId={r1Id}
                      onEcSelect={(id) => { setEcId(id); setVenueId(null); clearPreview(); }}
                      selectedEcId={ecId}
                    />
                  )}
                  {(!isMSP || ecId) && (
                    <div>
                      <SingleVenueSelector
                        controllerId={r1Id}
                        tenantId={tenantId}
                        onVenueSelect={(id) => { setVenueId(id); clearPreview(); }}
                        selectedVenueId={venueId}
                      />
                      <p className="text-xs text-gray-500 mt-1">
                        Venue is optional: its DPSK networks suggest which DPSK pool new DPSK networks use. Nothing is activated.
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-3 pt-3 border-t border-gray-100">
              <button
                onClick={() => loadPreview()}
                disabled={!ready || busy}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading ? 'Reading both sides...' : preview ? 'Re-compare' : 'Find missing WLANs'}
              </button>
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </span>
              )}
            </div>
          </div>

          {results && <ResultsPanel results={results.results} radiusResults={results.radius_results} />}

          {confirming && (
            <ConfirmModal
              rows={chosen} pools={pools} poolNames={preview?.dpsk_pools || []} radius={radius}
              services={preview?.radius_services || []}
              onCancel={() => setConfirming(false)} onConfirm={applyChanges}
            />
          )}

          {preview && preview.warnings.length > 0 && (
            <div className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              {preview.warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}

          {preview && preview.radius_services.length > 0 && (
            <RadiusPanel
              services={preview.radius_services} profiles={preview.r1_radius_profiles}
              choices={radius} onChange={(id, c) => setRadius(prev => ({ ...prev, [id]: c }))}
            />
          )}

          {preview && (
            <div className="bg-white border border-gray-200 rounded-lg">
              <div className="flex flex-wrap items-center gap-3 p-3 border-b border-gray-100">
                <span className="text-sm text-gray-600">
                  <span className="font-medium">{preview.zone_name}</span>
                  {preview.venue_name && <> · DPSK context from <span className="font-medium">{preview.venue_name}</span></>}
                </span>
                {(Object.keys(STATUS) as RowStatus[]).map(s => (
                  <span key={s} className={`text-xs px-2 py-0.5 rounded border ${STATUS[s].cls}`}>
                    {STATUS[s].label}: {preview.counts[s] ?? 0}
                  </span>
                ))}
                <button
                  onClick={() => setConfirming(true)}
                  disabled={!chosen.length || busy}
                  className="ml-auto px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                >
                  {applying && <Loader2 size={14} className="animate-spin" />}
                  {applying ? 'Creating...' : `Create ${chosen.length} network${chosen.length === 1 ? '' : 's'} in R1`}
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                    <tr>
                      <th className="p-2 w-8">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          disabled={!selectableRows.length}
                          onChange={() => setSelected(allSelected
                            ? new Set()
                            : new Set(selectableRows.map(r => r.sz_wlan_id)))}
                        />
                      </th>
                      <th className="p-2 text-left">SZ WLAN</th>
                      <th className="p-2 text-left">Becomes</th>
                      <th className="p-2 text-right">VLAN</th>
                      <th className="p-2 text-left">Status</th>
                      <th className="p-2 text-left">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map(r => {
                      const st = STATUS[r.status];
                      const Icon = st.icon;
                      const waitingOnRadius = (r.status === 'create' || r.status === 'review') && !radiusReady(r);
                      return (
                        <tr key={r.sz_wlan_id} className="border-t border-gray-100 align-top">
                          <td className="p-2">
                            <input
                              type="checkbox"
                              disabled={!selectable(r)}
                              checked={selectable(r) && selected.has(r.sz_wlan_id)}
                              onChange={() => toggle(r.sz_wlan_id)}
                            />
                          </td>
                          <td className="p-2">
                            <div className="font-medium text-gray-800">{r.sz_name}</div>
                            {r.sz_ssid !== r.sz_name && <div className="text-xs text-gray-500">SSID {r.sz_ssid}</div>}
                            <div className="text-xs text-gray-400">{r.auth_type}</div>
                          </td>
                          <td className="p-2">
                            {r.status === 'exists' && r.r1 ? (
                              <div className="text-gray-800">{r.r1.name}</div>
                            ) : (
                              <>
                                <div className="text-gray-800">
                                  {TYPE_LABEL[r.r1_type]}
                                  {r.security && <span className="text-xs text-gray-500 ml-1">{r.security}</span>}
                                </div>
                                {r.r1_type === 'dpsk' && r.status !== 'blocked' && (
                                  <select
                                    className="mt-1 text-xs border border-gray-300 rounded px-1 py-0.5 max-w-[14rem]"
                                    value={pools[r.sz_wlan_id] ?? ''}
                                    disabled={busy}
                                    onChange={e => setPools(prev => ({ ...prev, [r.sz_wlan_id]: e.target.value }))}
                                  >
                                    <option value="">No DPSK pool (link later)</option>
                                    {preview.dpsk_pools.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                                  </select>
                                )}
                              </>
                            )}
                          </td>
                          <td className="p-2 text-right font-mono">{r.vlan_id}</td>
                          <td className="p-2">
                            <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border ${st.cls}`}>
                              <Icon size={12} /> {st.label}
                            </span>
                          </td>
                          <td className="p-2 text-xs text-gray-600">
                            {waitingOnRadius && (
                              <div className="text-amber-700">Choose a RADIUS profile for its auth service above</div>
                            )}
                            {r.notes.map((n, i) => <div key={i}>{n}</div>)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RadiusPanel({ services, profiles, choices, onChange }: {
  services: RadiusService[];
  profiles: RadiusProfile[];
  choices: Record<string, RadiusChoice>;
  onChange: (id: string, c: RadiusChoice) => void;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <KeyRound size={14} className="text-indigo-600" /> RADIUS services used by Enterprise WLANs
      </div>
      <div className="divide-y divide-gray-100">
        {services.map(s => {
          const c = choices[s.id] || { action: 'skip', r1_profile_id: null, secret: '' };
          const wantType = s.roles.length === 1 && s.roles[0] === 'accounting' ? 'ACCOUNTING' : 'AUTHENTICATION';
          const usable = profiles.filter(p => !p.type || p.type === wantType);
          return (
            <div key={s.id} className="p-3 grid md:grid-cols-2 gap-3 text-sm">
              <div>
                <div className="font-medium text-gray-800">{s.name}</div>
                <div className="text-xs text-gray-500">
                  {s.primary || 'no server address'}{s.secondary ? `, ${s.secondary}` : ''} · {s.roles.join(' + ')}
                </div>
                <div className="text-xs text-gray-500">
                  Used by {s.used_by.map(u => u.wlan_name).join(', ')}
                </div>
                <div className="text-xs text-gray-400 mt-0.5">{s.suggestion.reason}</div>
              </div>
              <div className="space-y-1.5">
                <select
                  className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                  value={c.action === 'existing' ? `existing:${c.r1_profile_id || ''}` : c.action}
                  onChange={e => {
                    const v = e.target.value;
                    if (v.startsWith('existing:')) onChange(s.id, { ...c, action: 'existing', r1_profile_id: v.slice(9) });
                    else onChange(s.id, { ...c, action: v as RadiusChoice['action'], r1_profile_id: null });
                  }}
                >
                  <option value="create" disabled={!s.primary}>Create a new R1 profile from SZ servers</option>
                  {usable.map(p => (
                    <option key={p.id} value={`existing:${p.id}`}>
                      Use R1 profile {p.name}{p.primary ? ` (${p.primary})` : ''}{p.placeholder_secret ? ' — placeholder secret!' : ''}
                    </option>
                  ))}
                  <option value="skip">Don't map (its WLANs can't be created)</option>
                </select>
                {c.action === 'create' && (
                  <input
                    type="password"
                    autoComplete="new-password"
                    placeholder="Shared secret (not stored)"
                    className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                    value={c.secret}
                    onChange={e => onChange(s.id, { ...c, secret: e.target.value })}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConfirmModal({ rows, pools, poolNames, radius, services, onCancel, onConfirm }: {
  rows: GrabRow[];
  pools: Record<string, string>;
  poolNames: { id: string; name: string }[];
  radius: Record<string, RadiusChoice>;
  services: RadiusService[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const poolName = (id: string) => poolNames.find(p => p.id === id)?.name || id;
  const usedServices = new Set(rows.filter(r => r.r1_type === 'aaa').map(r => r.auth_service_id));
  const newProfiles = services.filter(s => usedServices.has(s.id) && radius[s.id]?.action === 'create');
  const noPool = rows.filter(r => r.r1_type === 'dpsk' && !pools[r.sz_wlan_id]).length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">
            Create {rows.length} R1 network{rows.length === 1 ? '' : 's'}
          </h2>
          <p className="text-xs text-gray-500 mt-1">Tenant-wide, not activated in any venue.</p>
        </div>
        <div className="p-4 max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody>
              {rows.map(r => (
                <tr key={r.sz_wlan_id} className="border-t border-gray-100 first:border-t-0">
                  <td className="py-1.5 text-gray-800">{r.sz_name}</td>
                  <td className="py-1.5 text-gray-500 text-xs">
                    {TYPE_LABEL[r.r1_type]}{r.security ? ` ${r.security}` : ''} · VLAN {r.vlan_id}
                    {r.r1_type === 'dpsk' && pools[r.sz_wlan_id] && ` · pool ${poolName(pools[r.sz_wlan_id])}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {newProfiles.length > 0 && (
          <div className="mx-4 mb-2 text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded px-3 py-2">
            Also creates RADIUS profile{newProfiles.length === 1 ? '' : 's'}: {newProfiles.map(s => s.name).join(', ')}
          </div>
        )}
        {noPool > 0 && (
          <div className="mx-4 mb-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
            {noPool} DPSK network{noPool === 1 ? ' has' : 's have'} no pool; R1 won't activate {noPool === 1 ? 'it' : 'them'} until one is linked.
          </div>
        )}
        <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
          <button onClick={onCancel} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={onConfirm} className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 font-medium">
            Create {rows.length}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResultsPanel({ results, radiusResults }: { results: ApplyResult[]; radiusResults: RadiusResult[] }) {
  const failed = results.filter(r => r.status === 'failed');
  const created = results.filter(r => r.status === 'created').length;
  const exists = results.filter(r => r.status === 'exists').length;
  const warned = results.filter(r => r.warnings?.length);
  const radiusFailed = radiusResults.filter(r => r.status === 'failed');
  return (
    <div className={`border rounded-lg p-3 mb-4 text-sm ${failed.length || radiusFailed.length || warned.length ? 'bg-amber-50 border-amber-200' : 'bg-green-50 border-green-200'}`}>
      <div className="font-medium text-gray-800">
        {created} created{exists ? `, ${exists} already in R1` : ''}
        {failed.length ? `, ${failed.length} failed` : ''}
      </div>
      {radiusResults.map(r => (
        <div key={r.sz_service_id} className={`text-xs mt-1 ${r.status === 'failed' ? 'text-red-700' : 'text-gray-600'}`}>
          RADIUS {r.name}: {r.status}{r.error ? ` — ${r.error}` : ''}
        </div>
      ))}
      {failed.map(r => (
        <div key={r.sz_wlan_id} className="text-red-700 text-xs mt-1">{r.name}: {r.error}</div>
      ))}
      {warned.map(r => r.warnings.map((w, i) => (
        <div key={`${r.sz_wlan_id}-${i}`} className="text-amber-700 text-xs mt-1">{r.name}: {w}</div>
      )))}
    </div>
  );
}
