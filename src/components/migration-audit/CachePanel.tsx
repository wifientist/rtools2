import { useEffect, useState } from 'react';
import { Database, Trash2, RefreshCw } from 'lucide-react';
import { apiFetch } from '@/utils/api';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface CacheMeta {
  captured_at: string;
  age_seconds: number | null;
  expires_in_seconds: number | null;
  stale_reason: string | null;
  zone_name?: string;
  venue_name?: string;
  wlans?: number;
  networks?: number;
  aps?: number;
}

interface Props {
  szControllerId: number | null;
  zoneId: string | null;
  r1ControllerId: number | null;
  venueId: string | null;
  tenantId: string | null;
  refreshSz: boolean;
  refreshR1: boolean;
  onRefreshSz: (v: boolean) => void;
  onRefreshR1: (v: boolean) => void;
  // Bump to re-read the status (e.g. after an audit run wrote a fresh capture)
  reloadKey?: number;
}

export function ago(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'unknown age';
  if (seconds < 90) return 'just now';
  const m = Math.round(seconds / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.round(seconds / 3600);
  if (h < 36) return `${h} h ago`;
  const d = Math.floor(seconds / 86400);
  return `${d} day${d === 1 ? '' : 's'} ${Math.round((seconds % 86400) / 3600)} h ago`;
}

export default function CachePanel(p: Props) {
  const [status, setStatus] = useState<{ sz: CacheMeta | null; r1: CacheMeta | null; ttl_days: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = p.szControllerId && p.zoneId && p.r1ControllerId && p.venueId;

  const params = () => {
    const q = new URLSearchParams();
    if (p.szControllerId) q.set('sz_controller_id', String(p.szControllerId));
    if (p.zoneId) q.set('zone_id', p.zoneId);
    if (p.r1ControllerId) q.set('r1_controller_id', String(p.r1ControllerId));
    if (p.venueId) q.set('venue_id', p.venueId);
    if (p.tenantId) q.set('tenant_id', p.tenantId);
    return q;
  };

  const load = async () => {
    if (!ready) { setStatus(null); return; }
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/audit-cache?${params()}`);
      setStatus(res.ok ? await res.json() : null);
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => { load(); }, [p.szControllerId, p.zoneId, p.r1ControllerId, p.venueId, p.tenantId, p.reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const clear = async (side: 'sz' | 'r1') => {
    setBusy(true);
    try {
      const q = params();
      q.set('side', side);
      await apiFetch(`${API_URL}/sz-migration/audit-cache?${q}`, { method: 'DELETE' });
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (!ready) return null;

  const row = (side: 'sz' | 'r1', label: string, meta: CacheMeta | null | undefined,
               refresh: boolean, setRefresh: (v: boolean) => void) => {
    const usable = meta && !meta.stale_reason;
    return (
      <div className="flex items-center gap-3 flex-wrap text-xs py-1">
        <span className="w-12 font-medium text-gray-700">{label}</span>
        {usable ? (
          <>
            <span className="text-gray-600">
              Cached {ago(meta!.age_seconds)}
              {meta!.aps !== undefined && <span className="text-gray-400"> · {meta!.aps} APs</span>}
              {(meta!.wlans ?? meta!.networks) !== undefined && (
                <span className="text-gray-400"> · {meta!.wlans ?? meta!.networks} {side === 'sz' ? 'WLANs' : 'networks'}</span>
              )}
              {meta!.expires_in_seconds !== null && meta!.expires_in_seconds! > 0 && (
                <span className="text-gray-400"> · expires in {Math.ceil(meta!.expires_in_seconds! / 86400)} d</span>
              )}
            </span>
            <label className="flex items-center gap-1 text-gray-700">
              <input type="checkbox" checked={refresh} onChange={e => setRefresh(e.target.checked)} />
              <RefreshCw size={11} /> Refresh from {side === 'sz' ? 'SmartZone' : 'RuckusONE'}
            </label>
            <button type="button" disabled={busy} onClick={() => clear(side)}
              className="text-gray-400 hover:text-red-600 flex items-center gap-0.5" title="Remove this cached capture">
              <Trash2 size={11} /> Clear
            </button>
          </>
        ) : (
          <span className="text-gray-500">
            {meta?.stale_reason ? `${meta.stale_reason}` : 'Nothing cached'} — this run reads live and caches it
          </span>
        )}
      </div>
    );
  };

  return (
    <div className="mt-3 border border-gray-200 rounded-lg px-3 py-2 bg-gray-50">
      <div className="text-xs font-semibold text-gray-600 flex items-center gap-1.5 mb-1">
        <Database size={12} /> Capture cache <span className="font-normal text-gray-400">(kept {status?.ttl_days ?? 7} days; credentials are not stored)</span>
      </div>
      {row('sz', 'SZ', status?.sz, p.refreshSz, p.onRefreshSz)}
      {row('r1', 'R1', status?.r1, p.refreshR1, p.onRefreshR1)}
    </div>
  );
}
