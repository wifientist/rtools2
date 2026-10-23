import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, Import,
  CheckCircle, AlertTriangle, XCircle, RefreshCw, Plus, KeyRound, KeySquare, Globe,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import JobMonitorModal from '@/components/JobMonitorModal';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/wlan_grabber.py) ─────────────────────────

type RowStatus = 'create' | 'review' | 'exists' | 'blocked';
type R1Type = 'psk' | 'open' | 'aaa' | 'dpsk' | 'wispr' | 'unsupported';

interface ModeOption {
  r1_type: R1Type;
  security: string | null;
  portal?: {
    auth_type: 'RADIUS' | 'ALWAYS_ACCEPT'; portal_url: string | null; provider: string | null; has_signing_key: boolean;
  } | null;
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
  wispr_key_sources: WisprKeySource[];
  wispr_r1: WisprR1Norms;
  warnings: string[];
}

// R1 WISPr networks sharing one integration key (the key itself never comes back).
interface WisprKeySource { network_id: string; name: string; key_group: string; count: number; examples: string[] }

// How most of the tenant's R1 WISPr networks are set up -- compared against, never copied.
interface WisprNorm<T> { value: T; count: number }
interface WisprR1Norms {
  total?: number;
  portal_url?: WisprNorm<string> | null;
  provider?: WisprNorm<string> | null;
  auth_type?: WisprNorm<string> | null;
  encrypt_mac_ip?: WisprNorm<boolean> | null;
  walled_gardens?: WisprNorm<string[]> | null;
}

// The integration key: each WLAN's SZ signature signing key unless overridden
// with one copied from an R1 network or typed (sent once, never stored).
interface WisprChoice { key_source: 'sz' | 'network' | 'typed'; key_network_id: string | null; integration_key: string }

interface RadiusChoice { action: 'dpsk' | 'create' | 'existing' | 'skip'; r1_profile_id: string | null; secret: string }

interface SharedPool {
  mode: 'existing' | 'new' | 'none';
  pool_id: string | null;
  name: string;
  passphrase_format: string;
  passphrase_length: number;
  devices_per_passphrase: number | null;   // null = unlimited
}

const STATUS: Record<RowStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  create:  { label: 'Create',  cls: 'text-indigo-700 bg-indigo-50 border-indigo-200', icon: Plus },
  review:  { label: 'Review',  cls: 'text-amber-700 bg-amber-50 border-amber-200', icon: AlertTriangle },
  exists:  { label: 'In R1',   cls: 'text-green-700 bg-green-50 border-green-200', icon: CheckCircle },
  blocked: { label: 'Blocked', cls: 'text-gray-500 bg-gray-50 border-gray-200', icon: XCircle },
};

const TYPE_LABEL: Record<R1Type, string> = {
  psk: 'PSK', open: 'Open', aaa: 'Enterprise', dpsk: 'DPSK', wispr: 'WISPr guest', unsupported: '—',
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
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [modeOf, setModeOf] = useState<Record<string, R1Type>>({});        // sz_wlan_id -> R1 type
  const [rowPool, setRowPool] = useState<Record<string, string>>({});      // sz_wlan_id -> SHARED | NO_POOL | pool id
  const [shared, setShared] = useState<SharedPool | null>(null);
  const [radius, setRadius] = useState<Record<string, RadiusChoice>>({});  // SZ service id -> choice
  const [planJobId, setPlanJobId] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [showJob, setShowJob] = useState(false);
  const [addDescription, setAddDescription] = useState(false);
  const [wispr, setWispr] = useState<WisprChoice>({ key_source: 'sz', key_network_id: null, integration_key: '' });

  const ready = !!szId && !!r1Id && !!zoneId && (!isMSP || !!ecId);

  const clearPreview = () => { setPreview(null); setSelected(new Set()); };

  const loadPreview = async () => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiPost<Preview>(`${API_URL}/wlan-grabber/preview`, {
        sz_controller_id: szId, zone_id: zoneId,
        r1_controller_id: r1Id, tenant_id: tenantId, venue_id: venueId,
      });
      setPreview(data);
      setModeOf(Object.fromEntries(data.rows.map(r => [r.sz_wlan_id, r.default_mode])));
      // SZ by default; the R1 key most of the tenant uses is only preselected for the override.
      setWispr({ key_source: 'sz', key_network_id: data.wispr_key_sources[0]?.network_id || null, integration_key: '' });
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
      // A service whose WLANs could become DPSK starts on 'dpsk' (Enterprise defaults to DPSK);
      // one only WISPr guest networks use starts on its suggestion (same server in R1 = reuse).
      const dpskable = new Set(data.rows.filter(r => r.modes.dpsk).map(r => r.sz_wlan_id));
      setRadius(Object.fromEntries(data.radius_services.map(s => [s.id, {
        action: s.used_by.some(u => dpskable.has(u.wlan_id)) ? 'dpsk' : s.suggestion.action,
        r1_profile_id: s.suggestion.r1_profile_id, secret: '',
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
  const needsRadius = (r: GrabRow) => {
    const m = modeFor(r);
    return m.r1_type === 'aaa' || (m.r1_type === 'wispr' && m.portal?.auth_type === 'RADIUS');
  };
  const radiusReady = (r: GrabRow) => {
    if (!needsRadius(r)) return true;
    const c = r.auth_service_id ? radius[r.auth_service_id] : undefined;
    if (!c) return false;
    if (c.action === 'existing') return !!c.r1_profile_id;
    if (c.action === 'create') return c.secret.length > 0;
    return false;
  };
  const sharedReady = !shared || shared.mode !== 'new' || shared.name.trim().length >= 2;
  const keyReady = wispr.key_source === 'sz'
    || (wispr.key_source === 'network' && !!wispr.key_network_id)
    || (wispr.key_source === 'typed' && /^[a-zA-Z0-9]{16}$/.test(wispr.integration_key));
  const wisprReady = (r: GrabRow) => modeFor(r).r1_type !== 'wispr' || keyReady;
  const poolReady = (r: GrabRow) =>
    modeFor(r).r1_type !== 'dpsk' || (rowPool[r.sz_wlan_id] || SHARED) !== SHARED || sharedReady;
  const selectable = (r: GrabRow) => {
    const s = statusOf(r);
    return (s === 'create' || s === 'review') && radiusReady(r) && poolReady(r) && wisprReady(r);
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
  const wisprCapable = (preview?.rows || []).filter(r => r.status !== 'exists' && r.modes.wispr);
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

  const sharedLabel = !shared ? '' : shared.mode === 'new' ? `${shared.name.trim() || 'new pool'} (new)`
    : shared.mode === 'existing' ? (preview?.dpsk_pools.find(p => p.id === shared.pool_id)?.name || 'existing pool')
    : 'none';

  // A pool picked on one row becomes the shared pool, and every row goes back to using it.
  const poolForAll = (poolId: string) => {
    setShared(prev => prev && { ...prev, mode: 'existing', pool_id: poolId });
    setRowPool({});
  };

  const allToDpsk = () => {
    dpskCapable.forEach(r => setMode(r, 'dpsk'));
    setRadius(prev => Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, action: 'dpsk' as const }])));
  };

  // Plan a workflow job; the plan modal confirms it and the job monitor follows it.
  const createPlan = async () => {
    if (!chosen.length || !shared) return;
    setPlanning(true);
    setError(null);
    // Only the services the chosen Enterprise rows use; the secret goes for 'create' only.
    const used = new Set(chosen.filter(needsRadius)
      .flatMap(r => [r.auth_service_id, r.accounting_service_id]).filter(Boolean) as string[]);
    const radiusBody = Object.fromEntries([...used].filter(id => radius[id]).map(id => {
      const c = radius[id];
      return [id, c.action === 'create'
        ? { action: 'create', shared_secret: c.secret }
        : c.action === 'existing' ? { action: 'existing', r1_profile_id: c.r1_profile_id } : { action: 'skip' }];   // 'dpsk' -> not mapped
    }));
    try {
      const data = await apiPost<{ job_id: string }>(`${API_URL}/wlan-grabber/v2/plan`, {
        sz_controller_id: szId, venue_id: venueId, zone_id: zoneId, r1_controller_id: r1Id, tenant_id: tenantId,
        wlans: chosen.map(r => {
          const t = modeFor(r).r1_type;
          const p = rowPool[r.sz_wlan_id] || SHARED;
          return { sz_wlan_id: r.sz_wlan_id, r1_type: t, dpsk_pool: t === 'dpsk' && p !== SHARED ? p : null };
        }),
        radius: radiusBody,
        add_description: addDescription,
        wispr: chosen.some(r => modeFor(r).r1_type === 'wispr') ? {
          key_source: wispr.key_source,
          key_network_id: wispr.key_source === 'network' ? wispr.key_network_id : null,
          integration_key: wispr.key_source === 'typed' ? wispr.integration_key : null,
        } : null,
        dpsk_pool: {
          mode: shared.mode,
          pool_id: shared.mode === 'existing' ? shared.pool_id : null,
          pool_name: shared.mode === 'existing' ? sharedLabel : null,
          name: shared.mode === 'new' ? shared.name.trim() : null,
          passphrase_format: shared.passphrase_format,
          passphrase_length: shared.passphrase_length,
          devices_per_passphrase: shared.devices_per_passphrase,
        },
      });
      // Secrets are single-use: forget them once sent.
      setRadius(prev => Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, secret: '' }])));
      setWispr(w => ({ ...w, integration_key: '' }));
      setPlanJobId(data.job_id);
      setShowPlan(true);
    } catch (e: any) {
      setError(e.message || 'Plan failed');
    } finally {
      setPlanning(false);
    }
  };

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const selectableRows = (preview?.rows || []).filter(selectable);
  const allSelected = selectableRows.length > 0 && selectableRows.every(r => selected.has(r.sz_wlan_id));
  const busy = loading || planning;

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

          {planJobId && (
            <V2PlanConfirmModal
              jobId={planJobId}
              isOpen={showPlan}
              workflowName="wlan_grabber"
              apiPrefix="/wlan-grabber/v2"
              onClose={() => setShowPlan(false)}
              onConfirm={(id: string) => { setShowPlan(false); setJobId(id); setShowJob(true); }}
            />
          )}

          {jobId && (
            <JobMonitorModal
              jobId={jobId}
              isOpen={showJob}
              onClose={() => setShowJob(false)}
              // Re-read both sides: what was created now shows as In R1.
              onJobComplete={() => { setSelected(new Set()); loadPreview(); }}
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

          {preview && wisprCapable.length > 0 && (
            <WisprPanel
              sources={preview.wispr_key_sources} norms={preview.wispr_r1} choice={wispr} onChange={setWispr}
              disabled={busy} rows={wisprCapable.filter(r => modeFor(r).r1_type === 'wispr')}
            />
          )}

          {preview && visibleServices.length > 0 && (
            <RadiusPanel
              services={visibleServices} profiles={preview.r1_radius_profiles}
              choices={radius} onChange={changeRadius}
              dpskable={new Set(preview.rows.filter(r => r.modes.dpsk).map(r => r.sz_wlan_id))}
              onCheckSecret={async (profileId, secret) => {
                const res = await apiPost<{ matches: boolean | null }>(`${API_URL}/wlan-grabber/radius/check-secret`, {
                  r1_controller_id: r1Id, tenant_id: tenantId, r1_profile_id: profileId, shared_secret: secret,
                });
                return res.matches;
              }}
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
                  onClick={() => createPlan()}
                  disabled={!chosen.length || busy}
                  className=" px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                >
                  {planning && <Loader2 size={14} className="animate-spin" />}
                  {planning ? 'Planning...' : `Plan ${chosen.length} network${chosen.length === 1 ? '' : 's'} in R1…`}
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
                                    <option value={SHARED}>Shared pool: {sharedLabel}</option>
                                    {preview.dpsk_pools.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                                    <option value={NO_POOL}>No pool (link later)</option>
                                  </select>
                                )}
                                {mode.r1_type === 'dpsk' && ![SHARED, NO_POOL].includes(rowPool[r.sz_wlan_id] || SHARED) && (
                                  <button
                                    onClick={() => poolForAll(rowPool[r.sz_wlan_id])}
                                    disabled={busy}
                                    className="mt-1 block text-xs text-indigo-700 hover:underline disabled:opacity-50"
                                  >
                                    Apply to all DPSK networks
                                  </button>
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

function WisprPanel({ sources, norms, choice, onChange, rows, disabled }: {
  sources: WisprKeySource[];
  norms: WisprR1Norms;
  choice: WisprChoice;
  onChange: (c: WisprChoice) => void;
  rows: GrabRow[];
  disabled: boolean;
}) {
  const signed = rows.filter(r => r.modes.wispr?.portal?.has_signing_key).length;
  const total = norms?.total || 0;
  const override = choice.key_source !== 'sz';
  const typedOk = /^[a-zA-Z0-9]{16}$/.test(choice.integration_key);
  const pct = (n?: { count: number } | null) => (n && total ? `${n.count} of ${total}` : '');
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <Globe size={14} className="text-indigo-600" /> WISPr guest portal
        <span className="font-normal text-xs text-gray-500">
          — {rows.length} WISPr guest network{rows.length === 1 ? '' : 's'}, built from SmartZone
        </span>
      </div>
      <div className="p-3 space-y-3 text-sm">
        <div className="text-xs text-gray-600">
          From each WLAN&apos;s SZ hotspot portal: portal &amp; redirect URL, provider (the SZ portal name), walled
          garden, session timeout and grace period, bypass CNA, MAC bypass, RADIUS (auth and proxy), VLAN and
          WLAN settings — and the <span className="font-medium">signature signing key</span>, which is R1&apos;s
          integration key.
        </div>
        <div className={`text-xs rounded px-2 py-1.5 border ${override ? 'text-gray-400 border-gray-100' : 'text-gray-700 bg-gray-50 border-gray-200'}`}>
          Integration key from SZ: {signed} of {rows.length} WLAN{rows.length === 1 ? '' : 's'} have a signing key
          — copied exactly, MAC &amp; IP encrypted.
          {rows.length - signed > 0 && ` ${rows.length - signed} without one get no key and MAC & IP are not encrypted, as on SZ.`}
        </div>

        {total > 0 && (
          <div className="text-xs text-gray-600 border border-gray-100 rounded p-2">
            <div className="font-medium text-gray-700 mb-0.5">Compared with your {total} R1 WISPr networks (not copied)</div>
            <div>MAC &amp; IP encrypted with a key: {norms.encrypt_mac_ip?.value ? 'yes' : 'no'} on {pct(norms.encrypt_mac_ip)}</div>
            {norms.portal_url && <div className="truncate">Portal URL: {norms.portal_url.value} on {pct(norms.portal_url)}</div>}
            {norms.provider && <div>Provider: {norms.provider.value} on {pct(norms.provider)}</div>}
            {norms.auth_type && <div>Auth: {norms.auth_type.value} on {pct(norms.auth_type)}</div>}
            <div className="text-gray-500 mt-0.5">Each row notes where its SZ values differ.</div>
          </div>
        )}

        <div className="space-y-1.5 text-xs text-gray-700">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={override} disabled={disabled}
              onChange={e => onChange({ ...choice, key_source: e.target.checked ? (sources.length ? 'network' : 'typed') : 'sz' })} />
            Override SZ&apos;s integration key (MAC &amp; IP then encrypted)
          </label>
          {override && (
            <div className="pl-6 space-y-1.5">
              {sources.length > 0 && (
                <label className="flex flex-wrap items-center gap-2">
                  <input type="radio" checked={choice.key_source === 'network'} disabled={disabled}
                    onChange={() => onChange({ ...choice, key_source: 'network' })} />
                  Copy exactly from R1 network
                  <select
                    className="text-xs border border-gray-300 rounded px-1 py-0.5 max-w-[22rem]"
                    value={choice.key_network_id || ''}
                    disabled={disabled || choice.key_source !== 'network'}
                    onChange={e => onChange({ ...choice, key_network_id: e.target.value || null })}
                  >
                    {sources.map(x => (
                      <option key={x.network_id} value={x.network_id}>
                        {x.name} ({x.key_group}, used by {x.count} network{x.count === 1 ? '' : 's'})
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="flex flex-wrap items-center gap-2">
                <input type="radio" checked={choice.key_source === 'typed'} disabled={disabled}
                  onChange={() => onChange({ ...choice, key_source: 'typed' })} />
                Type it
                <input
                  type="password" autoComplete="off" maxLength={16} placeholder="16 letters or digits (not stored)"
                  className={`text-xs border rounded px-1 py-0.5 w-56 ${choice.key_source === 'typed' && choice.integration_key && !typedOk ? 'border-red-400' : 'border-gray-300'}`}
                  value={choice.integration_key}
                  disabled={disabled || choice.key_source !== 'typed'}
                  onChange={e => onChange({ ...choice, integration_key: e.target.value.trim() })}
                />
              </label>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Optional: type the SZ shared secret to confirm the reused R1 profile carries the same one.
function SecretCheck({ profileId, onCheck }: {
  profileId: string;
  onCheck: (profileId: string, secret: string) => Promise<boolean | null>;
}) {
  const [secret, setSecret] = useState('');
  const [verdict, setVerdict] = useState<boolean | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setVerdict(await onCheck(profileId, secret)); } catch { setVerdict(null); }
    finally { setBusy(false); setSecret(''); }
  };
  return (
    <div className="flex items-center gap-2">
      <input
        type="password" autoComplete="off" placeholder="SZ shared secret, to check it matches (optional)"
        className="text-xs border border-gray-300 rounded px-2 py-1 flex-1"
        value={secret} onChange={e => { setSecret(e.target.value); setVerdict(undefined); }}
      />
      <button onClick={run} disabled={!secret || busy}
        className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
        {busy ? 'Checking…' : 'Check'}
      </button>
      {verdict === true && <span className="text-xs text-green-700">matches</span>}
      {verdict === false && <span className="text-xs text-red-700">differs</span>}
      {verdict === null && <span className="text-xs text-gray-500">could not read the profile</span>}
    </div>
  );
}

function RadiusPanel({ services, profiles, choices, onChange, dpskable, onCheckSecret }: {
  services: RadiusService[];
  profiles: RadiusProfile[];
  choices: Record<string, RadiusChoice>;
  onChange: (s: RadiusService, c: RadiusChoice) => void;
  dpskable: Set<string>;
  onCheckSecret: (profileId: string, secret: string) => Promise<boolean | null>;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <KeyRound size={14} className="text-indigo-600" /> SZ RADIUS services
        <span className="font-normal text-xs text-gray-500">
          — matched to R1 profiles by server address; SZ never returns the shared secret
        </span>
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
                  {s.used_by.some(u => dpskable.has(u.wlan_id)) && (
                    <option value="dpsk">Convert its WLANs to internal R1 DPSK (shared pool)</option>
                  )}
                  <option value="create" disabled={!s.primary}>Keep RADIUS: create a new R1 profile from SZ servers</option>
                  {usable.map(p => (
                    <option key={p.id} value={`existing:${p.id}`}>
                      Keep RADIUS: use R1 profile {p.name}{p.primary ? ` (${p.primary})` : ''}{p.placeholder_secret ? ' — placeholder secret!' : ''}
                    </option>
                  ))}
                  <option value="skip">Don't map (its RADIUS-auth WLANs can't be created)</option>
                </select>
                {c.action === 'existing' && c.r1_profile_id && (
                  <SecretCheck profileId={c.r1_profile_id} onCheck={onCheckSecret} />
                )}
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
