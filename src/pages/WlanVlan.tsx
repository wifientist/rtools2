import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, Layers,
  CheckCircle, AlertTriangle, XCircle, RefreshCw,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/wlan_vlan.py) ────────────────────────────

type RowStatus = 'in_sync' | 'change' | 'review' | 'blocked';

interface VlanRow {
  sz_wlan_id: string;
  sz_name: string;
  sz_ssid: string;
  sz: { vlan: number | null; vlan_pool: string | null };
  r1_network_id: string | null;
  r1_name: string | null;
  r1_ssid: string | null;
  match_method: string;
  r1: { vlan: number | null; other_venues: number } | null;
  target_vlan: number | null;
  status: RowStatus;
  notes: string[];
}

interface Preview {
  zone_name: string;
  venue_name: string;
  rows: VlanRow[];
  r1_only: { id: string; name: string; ssid: string }[];
  counts: Record<RowStatus, number>;
}

interface ApplyResult {
  network_id: string;
  name?: string;
  vlan_id: number;
  before?: number | null;
  status: 'updated' | 'unchanged' | 'failed';
  error?: string;
}

const STATUS: Record<RowStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  in_sync: { label: 'In sync', cls: 'text-green-700 bg-green-50 border-green-200', icon: CheckCircle },
  change:  { label: 'Change',  cls: 'text-indigo-700 bg-indigo-50 border-indigo-200', icon: ArrowRight },
  review:  { label: 'Review',  cls: 'text-amber-700 bg-amber-50 border-amber-200', icon: AlertTriangle },
  blocked: { label: 'Blocked', cls: 'text-gray-500 bg-gray-50 border-gray-200', icon: XCircle },
};

const selectable = (r: VlanRow) => r.status === 'change' || r.status === 'review';

// ── Page ─────────────────────────────────────────────────────────

export default function WlanVlan() {
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
  const [results, setResults] = useState<ApplyResult[] | null>(null);
  const [confirming, setConfirming] = useState(false);

  const ready = !!szId && !!r1Id && !!zoneId && !!venueId && (!isMSP || !!ecId);

  const clearPreview = () => { setPreview(null); setSelected(new Set()); setResults(null); };

  const loadPreview = async () => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const data = await apiPost<Preview>(`${API_URL}/wlan-vlan/preview`, {
        sz_controller_id: szId, zone_id: zoneId,
        r1_controller_id: r1Id, venue_id: venueId, tenant_id: tenantId,
      });
      setPreview(data);
      // Preselect only the plain changes; review rows are opt-in.
      setSelected(new Set(data.rows.filter(r => r.status === 'change').map(r => r.sz_wlan_id)));
    } catch (e: any) {
      setError(e.message || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const chosen = useMemo(
    () => (preview?.rows || []).filter(r => selected.has(r.sz_wlan_id) && selectable(r)),
    [preview, selected],
  );

  const applyChanges = async () => {
    setConfirming(false);
    if (!chosen.length) return;
    setApplying(true);
    setError(null);
    try {
      const data = await apiPost<{ results: ApplyResult[] }>(`${API_URL}/wlan-vlan/apply`, {
        r1_controller_id: r1Id, tenant_id: tenantId,
        changes: chosen.map(r => ({ network_id: r.r1_network_id, vlan_id: r.target_vlan })),
      });
      setResults(data.results);
      // R1 confirmed each write (async task completed); reflect it without re-reading both sides.
      const done = new Map(data.results.filter(x => x.status !== 'failed').map(x => [x.network_id, x.vlan_id]));
      setPreview(p => p && {
        ...p,
        rows: p.rows.map(r => r.r1_network_id && done.has(r.r1_network_id)
          ? { ...r, status: 'in_sync' as RowStatus, r1: r.r1 && { ...r.r1, vlan: done.get(r.r1_network_id)! } }
          : r),
      });
      setSelected(new Set());
    } catch (e: any) {
      setError(e.message || 'Apply failed');
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

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Layers size={24} className="text-indigo-600" />
          WLAN VLAN
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Copy each SZ WLAN's default VLAN onto the matching R1 network
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
            <div className="grid md:grid-cols-2 gap-6">
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                  <Server size={14} className="text-blue-600" /> SZ Zone
                </h3>
                <div className="space-y-3">
                  <SmartZoneDomainSelector
                    controllerId={szId}
                    onDomainSelect={(id) => { setDomainId(id); setZoneId(null); clearPreview(); }}
                    disabled={loading || applying}
                  />
                  {domainId && (
                    <SmartZoneZoneSelector
                      controllerId={szId}
                      domainId={domainId}
                      onZoneSelect={(id) => { setZoneId(id); clearPreview(); }}
                      disabled={loading || applying}
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
                disabled={!ready || loading || applying}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading ? 'Reading both sides...' : preview ? 'Re-compare' : 'Compare VLANs'}
              </button>
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </span>
              )}
            </div>
          </div>

          {results && <ResultsPanel results={results} />}

          {confirming && (
            <ConfirmModal rows={chosen} onCancel={() => setConfirming(false)} onConfirm={applyChanges} />
          )}

          {preview && (
            <div className="bg-white border border-gray-200 rounded-lg">
              <div className="flex flex-wrap items-center gap-3 p-3 border-b border-gray-100">
                <span className="text-sm text-gray-600">
                  <span className="font-medium">{preview.zone_name}</span> → <span className="font-medium">{preview.venue_name}</span>
                </span>
                {(Object.keys(STATUS) as RowStatus[]).map(s => (
                  <span key={s} className={`text-xs px-2 py-0.5 rounded border ${STATUS[s].cls}`}>
                    {STATUS[s].label}: {preview.counts[s] ?? 0}
                  </span>
                ))}
                <button
                  onClick={() => setConfirming(true)}
                  disabled={!chosen.length || applying || loading}
                  className="ml-auto px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                >
                  {applying && <Loader2 size={14} className="animate-spin" />}
                  {applying ? 'Setting VLANs...' : `Set ${chosen.length} VLAN${chosen.length === 1 ? '' : 's'} on R1`}
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
                      <th className="p-2 text-right">SZ VLAN</th>
                      <th className="p-2 text-left">R1 network</th>
                      <th className="p-2 text-right">R1 VLAN</th>
                      <th className="p-2 text-left">Status</th>
                      <th className="p-2 text-left">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map(r => {
                      const st = STATUS[r.status];
                      const Icon = st.icon;
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
                          </td>
                          <td className="p-2 text-right font-mono">{r.sz.vlan ?? '—'}</td>
                          <td className="p-2">
                            {r.r1_network_id ? (
                              <>
                                <div className="text-gray-800">{r.r1_name}</div>
                                {r.match_method !== 'name' && (
                                  <div className="text-xs text-gray-500">matched by {r.match_method.replace('_', ' ')}</div>
                                )}
                              </>
                            ) : <span className="text-gray-400">—</span>}
                          </td>
                          <td className={`p-2 text-right font-mono ${r.status === 'in_sync' ? 'text-green-700' : ''}`}>
                            {r.r1?.vlan ?? '—'}
                          </td>
                          <td className="p-2">
                            <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border ${st.cls}`}>
                              <Icon size={12} /> {st.label}
                            </span>
                          </td>
                          <td className="p-2 text-xs text-gray-600">
                            {r.notes.map((n, i) => <div key={i}>{n}</div>)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {preview.r1_only.length > 0 && (
                <div className="p-3 border-t border-gray-100 text-xs text-gray-500">
                  R1 networks in this venue with no SZ WLAN (left alone):{' '}
                  {preview.r1_only.map(n => n.name).join(', ')}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ConfirmModal({ rows, onCancel, onConfirm }: {
  rows: VlanRow[]; onCancel: () => void; onConfirm: () => void;
}) {
  const shared = rows.filter(r => r.r1?.other_venues).length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">
            Set VLAN on {rows.length} R1 network{rows.length === 1 ? '' : 's'}
          </h2>
        </div>
        <div className="p-4 max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody>
              {rows.map(r => (
                <tr key={r.sz_wlan_id} className="border-t border-gray-100 first:border-t-0">
                  <td className="py-1.5 text-gray-800">{r.r1_name}</td>
                  <td className="py-1.5 text-right font-mono text-gray-500 whitespace-nowrap">
                    {r.r1?.vlan ?? '—'} <ArrowRight size={12} className="inline mx-1" />
                    <span className="text-indigo-700 font-semibold">{r.target_vlan}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {shared > 0 && (
          <div className="mx-4 mb-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
            {shared} of these are also active in other venues; the VLAN is tenant-wide, so they change there too.
          </div>
        )}
        <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
          <button onClick={onCancel} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={onConfirm} className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 font-medium">
            Set {rows.length} VLAN{rows.length === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResultsPanel({ results }: { results: ApplyResult[] }) {
  const failed = results.filter(r => r.status === 'failed');
  const updated = results.filter(r => r.status === 'updated').length;
  const unchanged = results.filter(r => r.status === 'unchanged').length;
  return (
    <div className={`border rounded-lg p-3 mb-4 text-sm ${failed.length ? 'bg-amber-50 border-amber-200' : 'bg-green-50 border-green-200'}`}>
      <div className="font-medium text-gray-800">
        {updated} updated{unchanged ? `, ${unchanged} already set` : ''}
        {failed.length ? `, ${failed.length} failed` : ''}
      </div>
      {failed.map(r => (
        <div key={r.network_id} className="text-red-700 text-xs mt-1">
          {r.name || r.network_id} → VLAN {r.vlan_id}: {r.error}
        </div>
      ))}
    </div>
  );
}
