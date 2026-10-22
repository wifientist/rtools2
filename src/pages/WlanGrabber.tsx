import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, Import,
  CheckCircle, AlertTriangle, XCircle, RefreshCw, Plus, KeyRound, KeySquare,
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

interface ModeOption {
  r1_type: R1Type;
  security: string | null;
  blockers: string[];
  cautions: string[];
  notes: string[];
}

interface GrabRow {
  sz_wlan_id: string;
  sz_name: string;
  sz_ssid: string;
  auth_type: string;
  sz_type: R1Type;
  vlan_id: number;
  auth_service_id: string | null;
  accounting_service_id: string | null;
  modes: Partial<Record<R1Type, ModeOption>>;
  default_mode: R1Type;
  r1: { network_id: string; name: string; type: string | null; venue_count: number; active_in_venue: boolean | null } | null;
  status: RowStatus;   // of the default mode
  notes: string[];
}

interface RadiusProfile { id: string; name: string; type: string; primary: string | null; placeholder_secret: boolean }

interface RadiusService {
  id: string;
  name: string;
  roles: string[];
  primary: string | null;
  secondary: string | null;
  used_by: { wlan_id: string; wlan_name: string; role: string }[];
  extraction_error: boolean;
  suggestion: { action: 'create' | 'existing' | 'skip'; r1_profile_id: string | null; reason: string };
}

interface Preview {
  zone_name: string;
  venue_name: string | null;
  rows: GrabRow[];
  radius_services: RadiusService[];
  r1_radius_profiles: RadiusProfile[];
  dpsk_pools: { id: string; name: string }[];
  dpsk_suggestion: { pool_id: string; pool_name: string; reason: string } | null;
  warnings: string[];
}

// 'dpsk' = don't map it: create its WLANs as internal R1 DPSK (page-only; never sent).
interface RadiusChoice { action: 'dpsk' | 'create' | 'existing' | 'skip'; r1_profile_id: string | null; secret: string }

interface SharedPool {
  mode: 'existing' | 'new' | 'none';
  pool_id: string | null;
  name: string;
  passphrase_format: string;
  passphrase_length: number;
  devices_per_passphrase: number | null;   // null = unlimited
}

interface ApplyResult {
  sz_wlan_id: string;
  name: string;
  type: string;
  status: 'created' | 'exists' | 'failed';
  network_id?: string;
  error?: string;
  warnings: string[];
}

interface ItemResult { name: string; status: 'created' | 'exists' | 'failed'; error?: string }
interface RadiusResult extends ItemResult { sz_service_id: string }

interface ApplyResponse { results: ApplyResult[]; radius_results: RadiusResult[]; pool_result: ItemResult | null }

const STATUS: Record<RowStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  create:  { label: 'Create',  cls: 'text-indigo-700 bg-indigo-50 border-indigo-200', icon: Plus },
  review:  { label: 'Review',  cls: 'text-amber-700 bg-amber-50 border-amber-200', icon: AlertTriangle },
  exists:  { label: 'In R1',   cls: 'text-green-700 bg-green-50 border-green-200', icon: CheckCircle },
  blocked: { label: 'Blocked', cls: 'text-gray-500 bg-gray-50 border-gray-200', icon: XCircle },
};

const TYPE_LABEL: Record<R1Type, string> = {
  psk: 'PSK', open: 'Open', aaa: 'Enterprise', dpsk: 'DPSK', unsupported: '—',
};

const PASSPHRASE_FORMATS = [
  ['KEYBOARD_FRIENDLY', 'Keyboard friendly'],
  ['NUMBERS_ONLY', 'Numbers only'],
  ['MOST_SECURED', 'Most secured'],
  ['DICTIONARY_WORDS', 'Dictionary words'],
];

const SHARED = '';      // row pool choice: use the shared pool
const NO_POOL = 'none'; // row pool choice: no pool

const modeStatus = (m: ModeOption): RowStatus =>
  m.blockers.length ? 'blocked' : m.cautions.length ? 'review' : 'create';

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
  const [modeOf, setModeOf] = useState<Record<string, R1Type>>({});        // sz_wlan_id -> R1 type
  const [rowPool, setRowPool] = useState<Record<string, string>>({});      // sz_wlan_id -> SHARED | NO_POOL | pool id
  const [shared, setShared] = useState<SharedPool | null>(null);
  const [radius, setRadius] = useState<Record<string, RadiusChoice>>({});  // SZ service id -> choice
  const [results, setResults] = useState<ApplyResponse | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [addDescription, setAddDescription] = useState(false);

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
      setModeOf(Object.fromEntries(data.rows.map(r => [r.sz_wlan_id, r.default_mode])));
      setRowPool({});
      // Usually one pool serves every DPSK network in a venue: start from the
      // venue's pool, else a new one named after the zone.
      setShared({
        mode: data.dpsk_suggestion ? 'existing' : 'new',
        pool_id: data.dpsk_suggestion?.pool_id || null,
        name: `${data.zone_name} DPSK`.slice(0, 64),
        passphrase_format: 'KEYBOARD_FRIENDLY',
        passphrase_length: 12,
        devices_per_passphrase: null,
      });
      // Preselect only the plain creates; review rows are opt-in.
      setSelected(new Set(data.rows.filter(r => r.status === 'create').map(r => r.sz_wlan_id)));
      // Enterprise WLANs default to DPSK, so their services start on 'dpsk' too.
      setRadius(Object.fromEntries(data.radius_services.map(s => [s.id, {
        action: 'dpsk', r1_profile_id: s.suggestion.r1_profile_id, secret: '',
      }])));
    } catch (e: any) {
      setError(e.message || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const modeFor = (r: GrabRow) => r.modes[modeOf[r.sz_wlan_id] || r.default_mode] || r.modes[r.default_mode]!;
  const statusOf = (r: GrabRow): RowStatus => r.status === 'exists' ? 'exists' : modeFor(r) ? modeStatus(modeFor(r)) : r.status;

  // An Enterprise row can only be created once its auth service maps to an R1 profile.
  const radiusReady = (r: GrabRow) => {
    if (modeFor(r).r1_type !== 'aaa') return true;
    const c = r.auth_service_id ? radius[r.auth_service_id] : undefined;
    if (!c) return false;
    if (c.action === 'existing') return !!c.r1_profile_id;
    if (c.action === 'create') return c.secret.length > 0;
    return false;
  };
  const sharedReady = !shared || shared.mode !== 'new' || shared.name.trim().length >= 2;
  const poolReady = (r: GrabRow) =>
    modeFor(r).r1_type !== 'dpsk' || (rowPool[r.sz_wlan_id] || SHARED) !== SHARED || sharedReady;
  const selectable = (r: GrabRow) => {
    const s = statusOf(r);
    return (s === 'create' || s === 'review') && radiusReady(r) && poolReady(r);
  };

  const chosen = useMemo(
    () => (preview?.rows || []).filter(r => selected.has(r.sz_wlan_id) && selectable(r)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [preview, selected, radius, modeOf, rowPool, shared],
  );

  const counts = useMemo(() => {
    const c: Record<RowStatus, number> = { create: 0, review: 0, exists: 0, blocked: 0 };
    (preview?.rows || []).forEach(r => { c[statusOf(r)] += 1; });
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, modeOf]);

  const dpskCapable = (preview?.rows || []).filter(r => r.status !== 'exists' && r.modes.dpsk);
  const openRows = new Map((preview?.rows || []).filter(r => r.status !== 'exists').map(r => [r.sz_wlan_id, r]));
  const visibleServices = (preview?.radius_services || []).filter(s => s.used_by.some(u => openRows.has(u.wlan_id)));

  // A service's choice decides what the WLANs authenticating through it become.
  const changeRadius = (s: RadiusService, c: RadiusChoice) => {
    setRadius(prev => ({ ...prev, [s.id]: c }));
    s.used_by.filter(u => u.role === 'authentication').forEach(u => {
      const r = openRows.get(u.wlan_id);
      const want: R1Type = c.action === 'dpsk' ? 'dpsk' : 'aaa';
      if (r && r.modes[want] && modeFor(r).r1_type !== want) setMode(r, want);
    });
  };

  const setMode = (r: GrabRow, m: R1Type) => {
    setModeOf(prev => ({ ...prev, [r.sz_wlan_id]: m }));
    // A mode that can be created plainly gets ticked; anything else is opt-in.
    const opt = r.modes[m];
    setSelected(prev => {
      const next = new Set(prev);
      if (opt && modeStatus(opt) === 'create') next.add(r.sz_wlan_id); else next.delete(r.sz_wlan_id);
      return next;
    });
  };

  const allToDpsk = () => {
    dpskCapable.forEach(r => setMode(r, 'dpsk'));
    setRadius(prev => Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, action: 'dpsk' as const }])));
  };

  const applyChanges = async () => {
    setConfirming(false);
    if (!chosen.length || !shared) return;
    setApplying(true);
    setError(null);
    // Only the services the chosen Enterprise rows use; the secret goes for 'create' only.
    const used = new Set(chosen.filter(r => modeFor(r).r1_type === 'aaa')
      .flatMap(r => [r.auth_service_id, r.accounting_service_id]).filter(Boolean) as string[]);
    const radiusBody = Object.fromEntries([...used].filter(id => radius[id]).map(id => {
      const c = radius[id];
      return [id, c.action === 'create'
        ? { action: 'create', shared_secret: c.secret }
        : c.action === 'existing' ? { action: 'existing', r1_profile_id: c.r1_profile_id } : { action: 'skip' }];   // 'dpsk' -> not mapped
    }));
    try {
      const data = await apiPost<ApplyResponse>(`${API_URL}/wlan-grabber/apply`, {
        sz_controller_id: szId, zone_id: zoneId, r1_controller_id: r1Id, tenant_id: tenantId,
        wlans: chosen.map(r => {
          const t = modeFor(r).r1_type;
          const p = rowPool[r.sz_wlan_id] || SHARED;
          return { sz_wlan_id: r.sz_wlan_id, r1_type: t, dpsk_pool: t === 'dpsk' && p !== SHARED ? p : null };
        }),
        radius: radiusBody,
        add_description: addDescription,
        dpsk_pool: {
          mode: shared.mode,
          pool_id: shared.mode === 'existing' ? shared.pool_id : null,
          name: shared.mode === 'new' ? shared.name.trim() : null,
          passphrase_format: shared.passphrase_format,
          passphrase_length: shared.passphrase_length,
          devices_per_passphrase: shared.devices_per_passphrase,
        },
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
                        Venue is optional: the DPSK pool its DPSK networks use becomes the suggested pool. Nothing is activated.
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

          {results && <ResultsPanel data={results} />}

          {confirming && shared && preview && (
            <ConfirmModal
              rows={chosen} modeFor={modeFor} rowPool={rowPool} shared={shared}
              poolNames={preview.dpsk_pools} radius={radius} services={preview.radius_services}
              onCancel={() => setConfirming(false)} onConfirm={applyChanges}
            />
          )}

          {preview && preview.warnings.length > 0 && (
            <div className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              {preview.warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}

          {preview && shared && dpskCapable.length > 0 && (
            <DpskPanel
              shared={shared} onChange={setShared} pools={preview.dpsk_pools}
              suggestion={preview.dpsk_suggestion} venueName={preview.venue_name}
              convertible={dpskCapable.filter(r => modeFor(r).r1_type !== 'dpsk').length}
              onAllToDpsk={allToDpsk} disabled={busy}
            />
          )}

          {preview && visibleServices.length > 0 && (
            <RadiusPanel
              services={visibleServices} profiles={preview.r1_radius_profiles}
              choices={radius} onChange={changeRadius}
            />
          )}

          {preview && (
            <div className="bg-white border border-gray-200 rounded-lg">
              <div className="flex flex-wrap items-center gap-3 p-3 border-b border-gray-100">
                <span className="text-sm text-gray-600 font-medium">{preview.zone_name}</span>
                {(Object.keys(STATUS) as RowStatus[]).map(s => (
                  <span key={s} className={`text-xs px-2 py-0.5 rounded border ${STATUS[s].cls}`}>
                    {STATUS[s].label}: {counts[s]}
                  </span>
                ))}
                <label className="ml-auto text-xs text-gray-600 flex items-center gap-1.5">
                  <input
                    type="checkbox" checked={addDescription} disabled={busy}
                    onChange={e => setAddDescription(e.target.checked)}
                  />
                  Description "Migrated from SZ: &lt;name&gt;"
                </label>
                <button
                  onClick={() => setConfirming(true)}
                  disabled={!chosen.length || busy}
                  className=" px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
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
                      <th className="p-2 text-left">Create as</th>
                      <th className="p-2 text-right">VLAN</th>
                      <th className="p-2 text-left">Status</th>
                      <th className="p-2 text-left">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map(r => {
                      const status = statusOf(r);
                      const st = STATUS[status];
                      const Icon = st.icon;
                      const mode = modeFor(r);
                      const modeKeys = Object.keys(r.modes) as R1Type[];
                      const open = status !== 'exists';
                      const waitingOnRadius = (status === 'create' || status === 'review') && !radiusReady(r);
                      const notes = open && mode
                        ? [...mode.blockers, ...mode.cautions, ...r.notes, ...mode.notes]
                        : r.notes;
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
                            {!open && r.r1 ? (
                              <div className="text-gray-800">{r.r1.name}</div>
                            ) : (
                              <>
                                {modeKeys.length > 1 ? (
                                  <select
                                    className="text-xs border border-gray-300 rounded px-1 py-0.5"
                                    value={mode.r1_type}
                                    disabled={busy}
                                    onChange={e => setMode(r, e.target.value as R1Type)}
                                  >
                                    {modeKeys.map(k => (
                                      <option key={k} value={k}>
                                        {TYPE_LABEL[k]}{r.modes[k]!.security ? ` (${r.modes[k]!.security})` : ''}
                                      </option>
                                    ))}
                                  </select>
                                ) : (
                                  <div className="text-gray-800">
                                    {TYPE_LABEL[mode.r1_type]}
                                    {mode.security && <span className="text-xs text-gray-500 ml-1">{mode.security}</span>}
                                  </div>
                                )}
                                {mode.r1_type === 'dpsk' && status !== 'blocked' && (
                                  <select
                                    className="mt-1 block text-xs border border-gray-300 rounded px-1 py-0.5 max-w-[14rem]"
                                    value={rowPool[r.sz_wlan_id] || SHARED}
                                    disabled={busy}
                                    onChange={e => setRowPool(prev => ({ ...prev, [r.sz_wlan_id]: e.target.value }))}
                                  >
                                    <option value={SHARED}>Shared pool</option>
                                    {preview.dpsk_pools.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                                    <option value={NO_POOL}>No pool (link later)</option>
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
                            {notes.map((n, i) => <div key={i}>{n}</div>)}
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

function DpskPanel({ shared, onChange, pools, suggestion, venueName, convertible, onAllToDpsk, disabled }: {
  shared: SharedPool;
  onChange: (s: SharedPool) => void;
  pools: { id: string; name: string }[];
  suggestion: Preview['dpsk_suggestion'];
  venueName: string | null;
  convertible: number;
  onAllToDpsk: () => void;
  disabled: boolean;
}) {
  const value = shared.mode === 'existing' ? `existing:${shared.pool_id || ''}` : shared.mode;
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <KeySquare size={14} className="text-indigo-600" /> Shared DPSK pool
        <span className="font-normal text-xs text-gray-500">— every DPSK network uses it unless its row picks another</span>
        {convertible > 0 && (
          <button
            onClick={onAllToDpsk}
            disabled={disabled}
            className="ml-auto text-xs font-medium px-2 py-1 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"
          >
            Create all {convertible} PSK/Enterprise as DPSK
          </button>
        )}
      </div>
      <div className="p-3 space-y-2 text-sm">
        <select
          className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
          value={value}
          disabled={disabled}
          onChange={e => {
            const v = e.target.value;
            if (v.startsWith('existing:')) onChange({ ...shared, mode: 'existing', pool_id: v.slice(9) });
            else onChange({ ...shared, mode: v as SharedPool['mode'], pool_id: null });
          }}
        >
          <option value="new">Create a new DPSK pool…</option>
          {pools.map(p => (
            <option key={p.id} value={`existing:${p.id}`}>
              {p.name}{suggestion?.pool_id === p.id ? ' (used in this venue)' : ''}
            </option>
          ))}
          <option value="none">No shared pool (link later)</option>
        </select>
        {suggestion ? (
          <div className="text-xs text-gray-500">Suggested: {suggestion.pool_name} — {suggestion.reason}</div>
        ) : (
          <div className="text-xs text-gray-500">
            {venueName ? `No DPSK networks in ${venueName} to suggest a pool from.` : 'Pick a venue to suggest the pool its DPSK networks use.'}
          </div>
        )}
        {shared.mode === 'new' && (
          <div className="grid sm:grid-cols-2 gap-2 pt-1">
            <label className="text-xs text-gray-600">
              Pool name (its identity group gets the same name)
              <input
                className="mt-0.5 text-sm border border-gray-300 rounded px-2 py-1 w-full"
                value={shared.name} maxLength={64} disabled={disabled}
                onChange={e => onChange({ ...shared, name: e.target.value })}
              />
            </label>
            <label className="text-xs text-gray-600">
              Passphrase format
              <select
                className="mt-0.5 text-sm border border-gray-300 rounded px-2 py-1 w-full"
                value={shared.passphrase_format} disabled={disabled}
                onChange={e => onChange({ ...shared, passphrase_format: e.target.value })}
              >
                {PASSPHRASE_FORMATS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600">
              Passphrase length
              <input
                type="number" min={8} max={64}
                className="mt-0.5 text-sm border border-gray-300 rounded px-2 py-1 w-full"
                value={shared.passphrase_length} disabled={disabled}
                onChange={e => onChange({ ...shared, passphrase_length: Math.max(8, Math.min(64, Number(e.target.value) || 12)) })}
              />
            </label>
            <label className="text-xs text-gray-600">
              Devices per passphrase (blank = unlimited)
              <input
                type="number" min={1}
                className="mt-0.5 text-sm border border-gray-300 rounded px-2 py-1 w-full"
                value={shared.devices_per_passphrase ?? ''} disabled={disabled}
                onChange={e => onChange({ ...shared, devices_per_passphrase: e.target.value ? Math.max(1, Number(e.target.value)) : null })}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}

function RadiusPanel({ services, profiles, choices, onChange }: {
  services: RadiusService[];
  profiles: RadiusProfile[];
  choices: Record<string, RadiusChoice>;
  onChange: (s: RadiusService, c: RadiusChoice) => void;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <KeyRound size={14} className="text-indigo-600" /> SZ RADIUS services (Enterprise / Cloudpath)
        <span className="font-normal text-xs text-gray-500">— convert to internal R1 DPSK, or keep RADIUS</span>
      </div>
      <div className="divide-y divide-gray-100">
        {services.map(s => {
          const c = choices[s.id] || { action: 'dpsk', r1_profile_id: null, secret: '' };
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
                {c.action !== 'dpsk' && <div className="text-xs text-gray-400 mt-0.5">{s.suggestion.reason}</div>}
              </div>
              <div className="space-y-1.5">
                <select
                  className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                  value={c.action === 'existing' ? `existing:${c.r1_profile_id || ''}` : c.action}
                  onChange={e => {
                    const v = e.target.value;
                    if (v.startsWith('existing:')) onChange(s, { ...c, action: 'existing', r1_profile_id: v.slice(9) });
                    else onChange(s, { ...c, action: v as RadiusChoice['action'], r1_profile_id: null });
                  }}
                >
                  <option value="dpsk">Convert its WLANs to internal R1 DPSK (shared pool)</option>
                  <option value="create" disabled={!s.primary}>Keep RADIUS: create a new R1 profile from SZ servers</option>
                  {usable.map(p => (
                    <option key={p.id} value={`existing:${p.id}`}>
                      Keep RADIUS: use R1 profile {p.name}{p.primary ? ` (${p.primary})` : ''}{p.placeholder_secret ? ' — placeholder secret!' : ''}
                    </option>
                  ))}
                  <option value="skip">Don't map (its Enterprise WLANs can't be created)</option>
                </select>
                {c.action === 'create' && (
                  <input
                    type="password"
                    autoComplete="new-password"
                    placeholder="Shared secret (not stored)"
                    className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                    value={c.secret}
                    onChange={e => onChange(s, { ...c, secret: e.target.value })}
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

function ConfirmModal({ rows, modeFor, rowPool, shared, poolNames, radius, services, onCancel, onConfirm }: {
  rows: GrabRow[];
  modeFor: (r: GrabRow) => ModeOption;
  rowPool: Record<string, string>;
  shared: SharedPool;
  poolNames: { id: string; name: string }[];
  radius: Record<string, RadiusChoice>;
  services: RadiusService[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const poolName = (id: string | null) => poolNames.find(p => p.id === id)?.name || id || '';
  const sharedLabel = shared.mode === 'new' ? `${shared.name.trim()} (new)`
    : shared.mode === 'existing' ? poolName(shared.pool_id) : null;
  const poolOf = (r: GrabRow) => {
    const p = rowPool[r.sz_wlan_id] || SHARED;
    return p === SHARED ? sharedLabel : p === NO_POOL ? null : poolName(p);
  };
  const dpskRows = rows.filter(r => modeFor(r).r1_type === 'dpsk');
  const usesShared = dpskRows.some(r => (rowPool[r.sz_wlan_id] || SHARED) === SHARED);
  const usedServices = new Set(rows.filter(r => modeFor(r).r1_type === 'aaa').map(r => r.auth_service_id));
  const newProfiles = services.filter(s => usedServices.has(s.id) && radius[s.id]?.action === 'create');
  const noPool = dpskRows.filter(r => !poolOf(r)).length;
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
              {rows.map(r => {
                const m = modeFor(r);
                return (
                  <tr key={r.sz_wlan_id} className="border-t border-gray-100 first:border-t-0">
                    <td className="py-1.5 text-gray-800">{r.sz_name}</td>
                    <td className="py-1.5 text-gray-500 text-xs">
                      {TYPE_LABEL[m.r1_type]}{m.security ? ` ${m.security}` : ''} · VLAN {r.vlan_id}
                      {m.r1_type === 'dpsk' && poolOf(r) && ` · pool ${poolOf(r)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {usesShared && shared.mode === 'new' && (
          <div className="mx-4 mb-2 text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded px-3 py-2">
            Also creates DPSK pool <span className="font-medium">{shared.name.trim()}</span> and its identity group
            (linked unchanged if a pool by that name already exists).
          </div>
        )}
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

function ResultsPanel({ data }: { data: ApplyResponse }) {
  const { results, radius_results: radiusResults, pool_result: pool } = data;
  const failed = results.filter(r => r.status === 'failed');
  const created = results.filter(r => r.status === 'created').length;
  const exists = results.filter(r => r.status === 'exists').length;
  const warned = results.filter(r => r.warnings?.length);
  const otherFailed = radiusResults.some(r => r.status === 'failed') || pool?.status === 'failed';
  return (
    <div className={`border rounded-lg p-3 mb-4 text-sm ${failed.length || otherFailed || warned.length ? 'bg-amber-50 border-amber-200' : 'bg-green-50 border-green-200'}`}>
      <div className="font-medium text-gray-800">
        {created} created{exists ? `, ${exists} already in R1` : ''}
        {failed.length ? `, ${failed.length} failed` : ''}
      </div>
      {pool && (
        <div className={`text-xs mt-1 ${pool.status === 'failed' ? 'text-red-700' : 'text-gray-600'}`}>
          DPSK pool {pool.name}: {pool.status}{pool.error ? ` — ${pool.error}` : ''}
        </div>
      )}
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
