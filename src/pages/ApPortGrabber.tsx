import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, Cable,
  CheckCircle, AlertTriangle, XCircle, RefreshCw, Pencil, HelpCircle, Layers, ShieldCheck,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import JobMonitorModal from '@/components/JobMonitorModal';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/ap_port_grabber.py) ──────────────────────

type RowStatus = 'match' | 'update' | 'review' | 'not_in_r1' | 'blocked';

interface PortRow {
  sz_mac: string;
  sz_name: string;
  serial: string | null;
  model: string | null;
  sz_group: string | null;
  source: string;                 // where SZ's ports come from: AP override / AP Group 'x' / Zone
  r1: { serial: string; name: string; model: string; status: string; ap_group: string | null } | null;
  status: RowStatus;
  sz_ports: string[];
  r1_ports: string[];
  changes: string[];
  poe: { poeMode?: string; poeOut?: boolean };
  r1_poe: { poeMode?: string; poeOut?: boolean };
  notes: string[];
  cautions: string[];
  blockers: string[];
  extras: string[];
  r1_use_venue: boolean | null;
  profile_ids: string[];          // SZ profiles this AP's written ports need mapped
  uplink_ports: string[];         // never written: R1 PoE-in / trunk-only, model map, live poePort
}

interface SzProfile {
  sz_profile_id: string;
  name: string;
  type: string;
  untagId: number | null;
  vlanMembers: string;
  auth: string;
  extras: string[];
  aps: number;
  ports: number;
  suggestion: { action: 'existing' | 'create'; r1_profile_id: string | null; name: string | null; reason: string };
}

interface R1Profile { id: string; name: string; type: string; untagId: number | null; vlanMembers: string; auth: string; isDefault: boolean; in_use: number }

interface Preview {
  zone_name: string;
  venue_name: string;
  rows: PortRow[];
  model_notes: string[];
  sz_profiles: SzProfile[];
  r1_profiles: R1Profile[];
}

interface ProfileChoice { action: 'existing' | 'create'; r1_profile_id: string | null; name: string }

const STATUS: Record<RowStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  update:    { label: 'Update',     cls: 'text-indigo-700 bg-indigo-50 border-indigo-200', icon: Pencil },
  review:    { label: 'Review',     cls: 'text-amber-700 bg-amber-50 border-amber-200', icon: AlertTriangle },
  match:     { label: 'Matches',    cls: 'text-green-700 bg-green-50 border-green-200', icon: CheckCircle },
  not_in_r1: { label: 'Not in R1',  cls: 'text-gray-500 bg-gray-50 border-gray-200', icon: HelpCircle },
  blocked:   { label: 'Blocked',    cls: 'text-red-700 bg-red-50 border-red-200', icon: XCircle },
};
const ORDER: RowStatus[] = ['update', 'review', 'match', 'not_in_r1', 'blocked'];
const ACTIONABLE: RowStatus[] = ['update', 'review'];

const TYPE_LABEL: Record<string, string> = { ACCESS: 'Access', TRUNK: 'Trunk', SELECTIVE_TRUNK: 'General' };
const AUTH_LABEL: Record<string, string> = {
  DISABLED: 'no auth', OPEN: 'client visibility', SUPPLICANT: '802.1X supplicant',
  PORT_BASED_AUTHENTICATOR: '802.1X port-based', MAC_BASED_AUTHENTICATOR: '802.1X MAC-based',
};
const vlans = (p: { type: string; untagId: number | null; vlanMembers: string }) =>
  p.type === 'ACCESS' ? `${p.untagId ?? '?'}` : `untag ${p.untagId ?? '?'}, ${p.vlanMembers || '-'}`;
const sameVlans = (a: SzProfile, b: R1Profile) =>
  a.type === b.type && a.untagId === b.untagId && (a.type === 'ACCESS' || a.vlanMembers === b.vlanMembers);
const poeText = (p: { poeMode?: string; poeOut?: boolean }) =>
  [p.poeMode && `PoE ${p.poeMode}`, p.poeOut !== undefined && `PoE out ${p.poeOut ? 'on' : 'off'}`].filter(Boolean).join(', ');

// ── Page ─────────────────────────────────────────────────────────

export default function ApPortGrabber() {
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
  const [choices, setChoices] = useState<Record<string, ProfileChoice>>({});
  const [filter, setFilter] = useState<RowStatus | 'all'>('all');
  const [planJobId, setPlanJobId] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [showJob, setShowJob] = useState(false);

  const ready = !!szId && !!r1Id && !!zoneId && !!venueId && (!isMSP || !!ecId);
  const clearPreview = () => { setPreview(null); setSelected(new Set()); };

  const loadPreview = async () => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiPost<Preview>(`${API_URL}/ap-port-grabber/preview`, {
        sz_controller_id: szId, zone_id: zoneId, r1_controller_id: r1Id, tenant_id: tenantId, venue_id: venueId,
      });
      setPreview(data);
      setChoices(Object.fromEntries(data.sz_profiles.map(p => [p.sz_profile_id, {
        action: p.suggestion.action, r1_profile_id: p.suggestion.r1_profile_id, name: p.suggestion.name || '',
      }])));
      // Plain updates are ticked; reviews are opt-in.
      setSelected(new Set(data.rows.filter(r => r.status === 'update').map(r => r.sz_mac)));
      setFilter('all');
    } catch (e: any) {
      setError(e.message || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const choiceReady = (id: string) => {
    const c = choices[id];
    if (!c) return false;
    return c.action === 'existing' ? !!c.r1_profile_id : c.name.trim().length >= 2 && c.name.trim().length <= 32;
  };
  const rowReady = (r: PortRow) => ACTIONABLE.includes(r.status) && r.profile_ids.every(choiceReady);

  const chosen = useMemo(
    () => (preview?.rows || []).filter(r => selected.has(r.sz_mac) && rowReady(r)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [preview, selected, choices],
  );
  const waitingOnProfile = (preview?.rows || []).filter(r => selected.has(r.sz_mac) && ACTIONABLE.includes(r.status) && !rowReady(r)).length;

  const counts = useMemo(() => {
    const c = Object.fromEntries(ORDER.map(s => [s, 0])) as Record<RowStatus, number>;
    (preview?.rows || []).forEach(r => { c[r.status] += 1; });
    return c;
  }, [preview]);

  // Only the profiles a ticked AP needs are shown for mapping.
  const neededProfiles = useMemo(() => {
    const ids = new Set((preview?.rows || []).filter(r => selected.has(r.sz_mac)).flatMap(r => r.profile_ids));
    return (preview?.sz_profiles || []).filter(p => ids.has(p.sz_profile_id));
  }, [preview, selected]);

  const createPlan = async () => {
    if (!chosen.length) return;
    setPlanning(true);
    setError(null);
    const used = new Set(chosen.flatMap(r => r.profile_ids));
    try {
      const data = await apiPost<{ job_id: string }>(`${API_URL}/ap-port-grabber/v2/plan`, {
        sz_controller_id: szId, zone_id: zoneId, r1_controller_id: r1Id, tenant_id: tenantId, venue_id: venueId,
        aps: chosen.map(r => r.sz_mac),
        profiles: Object.fromEntries([...used].map(id => {
          const c = choices[id];
          return [id, c.action === 'existing'
            ? { action: 'existing', r1_profile_id: c.r1_profile_id }
            : { action: 'create', name: c.name.trim() }];
        })),
      });
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

  const visible = (preview?.rows || []).filter(r => filter === 'all' || r.status === filter);
  const selectableVisible = visible.filter(r => ACTIONABLE.includes(r.status));
  const allSelected = selectableVisible.length > 0 && selectableVisible.every(r => selected.has(r.sz_mac));
  const busy = loading || planning;

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Cable size={24} className="text-indigo-600" />
          AP Port Grabber
          <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-blue-100 text-blue-800">beta</span>
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Give each R1 AP the LAN ports and PoE its SZ twin has — AP and AP Group overrides included, which R1 can
          only hold per AP. Venue-wide per-model ports are the migration's job.
        </p>
        <p className="text-sm mt-2 flex items-start gap-1.5 text-green-800 bg-green-50 border border-green-200 rounded px-3 py-2">
          <ShieldCheck size={16} className="mt-0.5 shrink-0" />
          <span>
            The AP's uplink port is never written. It is every port R1 flags as PoE-in or trunk-only, the model's
            known uplink, and the port the AP is connected on right now. The job re-checks it live before and after
            each AP; if it ever changes, that AP goes straight back to venue settings and the whole job halts.
          </span>
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
                  <Target size={14} className="text-green-600" /> R1 Venue
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
                    <SingleVenueSelector
                      controllerId={r1Id}
                      tenantId={tenantId}
                      onVenueSelect={(id) => { setVenueId(id); clearPreview(); }}
                      selectedVenueId={venueId}
                    />
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
                {loading ? 'Reading every AP on both sides...' : preview ? 'Re-compare' : 'Compare AP ports'}
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
              workflowName="ap_port_grabber"
              apiPrefix="/ap-port-grabber/v2"
              onClose={() => setShowPlan(false)}
              onConfirm={(id: string) => { setShowPlan(false); setJobId(id); setShowJob(true); }}
            />
          )}

          {jobId && (
            <JobMonitorModal
              jobId={jobId}
              isOpen={showJob}
              onClose={() => setShowJob(false)}
              // Re-read both sides: what was written now shows as Matches.
              onJobComplete={() => { setSelected(new Set()); loadPreview(); }}
            />
          )}

          {preview && preview.model_notes.length > 0 && (
            <div className="mb-4 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              {preview.model_notes.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}

          {preview && neededProfiles.length > 0 && (
            <ProfilePanel
              profiles={neededProfiles} r1Profiles={preview.r1_profiles}
              choices={choices} onChange={(id, c) => setChoices(prev => ({ ...prev, [id]: c }))}
            />
          )}

          {preview && (
            <div className="bg-white border border-gray-200 rounded-lg">
              <div className="p-3 border-b border-gray-100 flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold text-gray-700">
                  {preview.zone_name} → {preview.venue_name}
                </span>
                <span className="text-gray-400">·</span>
                <button onClick={() => setFilter('all')}
                  className={`text-xs px-2 py-0.5 rounded border ${filter === 'all' ? 'bg-gray-800 text-white border-gray-800' : 'border-gray-300 text-gray-600'}`}>
                  All {preview.rows.length}
                </button>
                {ORDER.filter(s => counts[s] > 0).map(s => {
                  const st = STATUS[s];
                  return (
                    <button key={s} onClick={() => setFilter(s)}
                      className={`text-xs px-2 py-0.5 rounded border ${st.cls} ${filter === s ? 'ring-2 ring-offset-1 ring-gray-400' : ''}`}>
                      {st.label} {counts[s]}
                    </button>
                  );
                })}
                <div className="ml-auto flex items-center gap-3">
                  {waitingOnProfile > 0 && (
                    <span className="text-xs text-amber-700">{waitingOnProfile} ticked AP(s) wait on a profile choice</span>
                  )}
                  <button
                    onClick={createPlan}
                    disabled={!chosen.length || busy}
                    className="px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                  >
                    {planning ? <Loader2 size={14} className="animate-spin" /> : <Pencil size={14} />}
                    Plan {chosen.length} AP{chosen.length === 1 ? '' : 's'}
                  </button>
                </div>
              </div>

              {preview.rows.length === 0 ? (
                <div className="p-6 text-sm text-gray-500 text-center">No APs in this zone.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                      <tr>
                        <th className="p-2 w-8">
                          <input type="checkbox" checked={allSelected} disabled={!selectableVisible.length}
                            onChange={() => setSelected(prev => {
                              const next = new Set(prev);
                              selectableVisible.forEach(r => allSelected ? next.delete(r.sz_mac) : next.add(r.sz_mac));
                              return next;
                            })} />
                        </th>
                        <th className="p-2 text-left">SZ AP</th>
                        <th className="p-2 text-left">SZ ports</th>
                        <th className="p-2 text-left">R1 now</th>
                        <th className="p-2 text-left">Change</th>
                        <th className="p-2 text-left">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {visible.map(r => <Row key={r.sz_mac} r={r} checked={selected.has(r.sz_mac)}
                        ready={rowReady(r)} onToggle={() => toggle(r.sz_mac)} />)}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Rows ─────────────────────────────────────────────────────────

function Row({ r, checked, ready, onToggle }: { r: PortRow; checked: boolean; ready: boolean; onToggle: () => void }) {
  const st = STATUS[r.status];
  const Icon = st.icon;
  const actionable = ACTIONABLE.includes(r.status);
  const szPoe = poeText(r.poe);
  const r1Poe = poeText(r.r1_poe);
  return (
    <tr className={`align-top ${actionable && checked ? 'bg-indigo-50/30' : ''}`}>
      <td className="p-2">
        <input type="checkbox" checked={checked} disabled={!actionable} onChange={onToggle} />
      </td>
      <td className="p-2">
        <div className="font-medium text-gray-800">{r.sz_name}</div>
        <div className="text-xs text-gray-500">{r.model} · {r.serial || r.sz_mac}</div>
        <div className="text-xs text-gray-500">From: {r.source}</div>
      </td>
      <td className="p-2 text-xs text-gray-700">
        {r.sz_ports.map((p, i) => <div key={i}>{p}</div>)}
        {szPoe && <div className="text-gray-500">{szPoe}</div>}
      </td>
      <td className="p-2 text-xs text-gray-700">
        {r.r1 ? (
          <>
            <div className="text-gray-500">{r.r1.name}{r.r1_use_venue === false ? ' · own settings' : r.r1_use_venue ? ' · venue settings' : ''}</div>
            {r.r1_ports.map((p, i) => <div key={i}>{p}</div>)}
            {r1Poe && <div className="text-gray-500">{r1Poe}</div>}
          </>
        ) : <span className="text-gray-400">—</span>}
      </td>
      <td className="p-2 text-xs">
        {r.changes.map((c, i) => <div key={i} className="text-indigo-700">{c}</div>)}
        {r.uplink_ports?.length > 0 && (
          <div className="text-green-700 flex items-center gap-1 mt-0.5">
            <ShieldCheck size={12} /> Uplink LAN{r.uplink_ports.join(', LAN')} never touched
          </div>
        )}
        {actionable && checked && !ready && <div className="text-amber-700">Waiting on a profile choice above</div>}
      </td>
      <td className="p-2">
        <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${st.cls}`}>
          <Icon size={12} /> {st.label}
        </span>
        <div className="mt-1 space-y-0.5 text-xs max-w-xs">
          {r.blockers.map((n, i) => <div key={`b${i}`} className="text-red-700">{n}</div>)}
          {r.cautions.map((n, i) => <div key={`c${i}`} className="text-amber-700">{n}</div>)}
          {r.extras.map((n, i) => <div key={`e${i}`} className="text-gray-500">Not carried: {n}</div>)}
          {r.notes.map((n, i) => <div key={`n${i}`} className="text-gray-500">{n}</div>)}
        </div>
      </td>
    </tr>
  );
}

// ── Profile mapping ──────────────────────────────────────────────

function ProfilePanel({ profiles, r1Profiles, choices, onChange }: {
  profiles: SzProfile[];
  r1Profiles: R1Profile[];
  choices: Record<string, ProfileChoice>;
  onChange: (id: string, c: ProfileChoice) => void;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg mb-4">
      <div className="p-3 border-b border-gray-100 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
        <Layers size={14} className="text-indigo-600" /> SZ Ethernet Port Profiles → R1
        <span className="font-normal text-xs text-gray-500">
          — matched by name: reused if R1 has it, created if not; each port then gets SZ's exact VLANs as its own override
        </span>
      </div>
      <div className="divide-y divide-gray-100">
        {profiles.map(p => {
          const c = choices[p.sz_profile_id] || { action: 'create', r1_profile_id: null, name: '' };
          const usable = r1Profiles.filter(x => x.type === p.type)
            .sort((a, b) => Number(b.auth === p.auth) - Number(a.auth === p.auth)
              || Number(sameVlans(p, b)) - Number(sameVlans(p, a)) || a.name.localeCompare(b.name));
          return (
            <div key={p.sz_profile_id} className="p-3 grid md:grid-cols-2 gap-3 text-sm">
              <div>
                <div className="font-medium text-gray-800">{p.name}</div>
                <div className="text-xs text-gray-500">
                  {TYPE_LABEL[p.type] || p.type} · {AUTH_LABEL[p.auth] || p.auth} · {vlans(p)} · {p.ports} port(s) on {p.aps} AP(s)
                </div>
                {p.extras.length > 0 && (
                  <div className="text-xs text-amber-700">Not carried: {p.extras.join(', ')}</div>
                )}
                <div className="text-xs text-gray-400 mt-0.5">{p.suggestion.reason}</div>
              </div>
              <div className="space-y-1.5">
                <select
                  className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                  value={c.action === 'existing' ? `existing:${c.r1_profile_id || ''}` : 'create'}
                  onChange={e => {
                    const v = e.target.value;
                    if (v.startsWith('existing:')) onChange(p.sz_profile_id, { ...c, action: 'existing', r1_profile_id: v.slice(9) });
                    else onChange(p.sz_profile_id, { ...c, action: 'create', r1_profile_id: null,
                      name: c.name || p.suggestion.name || p.name.slice(0, 32) });
                  }}
                >
                  {c.action === 'existing' && !c.r1_profile_id && <option value="existing:">Choose an R1 profile…</option>}
                  {(p.auth === 'DISABLED' || p.auth === 'OPEN') && <option value="create">Create a new R1 profile</option>}
                  {usable.map(x => (
                    <option key={x.id} value={`existing:${x.id}`}>
                      Use {x.name}{x.isDefault ? ' (R1 default)' : ''} — {AUTH_LABEL[x.auth] || x.auth}, {vlans(x)}
                      {x.auth !== p.auth ? ' · DIFFERENT AUTH' : sameVlans(p, x) ? ' · same VLANs' : ' · VLANs set per port'}
                    </option>
                  ))}
                </select>
                {c.action === 'create' && (
                  <input
                    className="text-sm border border-gray-300 rounded px-2 py-1 w-full"
                    placeholder="R1 profile name (2-32 characters)"
                    maxLength={32}
                    value={c.name}
                    onChange={e => onChange(p.sz_profile_id, { ...c, name: e.target.value })}
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
