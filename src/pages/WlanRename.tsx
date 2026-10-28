import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import { AlertCircle, ArrowRight, Loader2, PenLine, RefreshCw } from 'lucide-react';
import { apiPost } from '@/utils/api';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/routers/wlan_rename/router.py) ────────────────────

interface Network { id: string; name: string; ssid: string; venues: number }

interface Row extends Network {
  newName: string;
  newSsid: string | null;   // null = SSID left alone
  clash: string | null;     // why this rename can't go
}

interface ApplyResult {
  network_id: string;
  name: string;
  before?: string;
  status: 'updated' | 'unchanged' | 'failed';
  error?: string;
}

// Literal find/replace; at-end mode only touches a trailing match. Trimmed so
// removing "v2" from "Foo v2" gives "Foo", not "Foo ".
function rewrite(value: string, find: string, replace: string, atEnd: boolean): string {
  if (!find) return value;
  const out = atEnd
    ? (value.endsWith(find) ? value.slice(0, -find.length) + replace : value)
    : value.split(find).join(replace);
  return out.trim();
}

// ── Page ─────────────────────────────────────────────────────────

export default function WlanRename() {
  const {
    activeControllerId: aId, activeControllerName: aName, activeControllerType: aType,
    activeControllerSubtype: aSub,
    secondaryControllerId: sId, secondaryControllerName: sName, secondaryControllerType: sType,
    secondaryControllerSubtype: sSub,
    controllers,
  } = useAuth();

  const slots = [
    { id: aId, name: aName, type: aType, subtype: aSub },
    { id: sId, name: sName, type: sType, subtype: sSub },
  ];
  const r1Slot = slots.find(s => s.id && s.type === 'RuckusONE');
  const r1Id = r1Slot?.id ?? null;
  const isMSP = r1Slot?.subtype === 'MSP';
  const r1Controller = controllers?.find((c: any) => c.id === r1Id);

  const [ecId, setEcId] = useState<string | null>(null);
  const tenantId = isMSP ? ecId : (r1Controller?.r1_tenant_id || null);

  const [networks, setNetworks] = useState<Network[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ApplyResult[] | null>(null);
  const [confirming, setConfirming] = useState(false);

  const [find, setFind] = useState('');
  const [replace, setReplace] = useState('');
  const [atEnd, setAtEnd] = useState(true);
  const [withSsid, setWithSsid] = useState(false);
  const [deselected, setDeselected] = useState<Set<string>>(new Set());

  const ready = !!r1Id && (!isMSP || !!ecId);

  const load = async () => {
    if (!ready) return;
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const data = await apiPost<{ networks: Network[] }>(`${API_URL}/wlan-rename/networks`, {
        r1_controller_id: r1Id, tenant_id: tenantId,
      });
      setNetworks(data.networks);
      setDeselected(new Set());
    } catch (e: any) {
      setError(e.message || 'Load failed');
    } finally {
      setLoading(false);
    }
  };

  // Only networks whose name (or SSID, if ticked) would change.
  const rows: Row[] = useMemo(() => {
    if (!networks || !find) return [];
    const changed = networks
      .map(n => {
        const newName = rewrite(n.name, find, replace, atEnd);
        const ssid = withSsid ? rewrite(n.ssid, find, replace, atEnd) : n.ssid;
        return { ...n, newName, newSsid: ssid !== n.ssid ? ssid : null, clash: null as string | null };
      })
      .filter(r => r.newName !== r.name || r.newSsid !== null);

    // R1 names are unique tenant-wide, compared without case. A name being
    // renamed away doesn't count as taken.
    const leaving = new Set(changed.filter(r => r.newName !== r.name).map(r => r.id));
    const taken = new Map(networks.filter(n => !leaving.has(n.id)).map(n => [n.name.toLowerCase(), n.name]));
    const counts = new Map<string, number>();
    changed.forEach(r => counts.set(r.newName.toLowerCase(), (counts.get(r.newName.toLowerCase()) || 0) + 1));
    for (const r of changed) {
      const key = r.newName.toLowerCase();
      if (!r.newName) r.clash = 'Name would be empty';
      else if (r.newName !== r.name && taken.has(key)) r.clash = `"${taken.get(key)}" already exists`;
      else if ((counts.get(key) || 0) > 1) r.clash = 'Another rename gives the same name';
      else if (r.newSsid !== null && (!r.newSsid || r.newSsid.length > 32)) r.clash = 'SSID must be 1-32 characters';
    }
    return changed;
  }, [networks, find, replace, atEnd, withSsid]);

  const chosen = rows.filter(r => !r.clash && !deselected.has(r.id));
  const okRows = rows.filter(r => !r.clash);
  const allSelected = okRows.length > 0 && okRows.every(r => !deselected.has(r.id));

  const toggle = (id: string) => setDeselected(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const applyChanges = async () => {
    setConfirming(false);
    if (!chosen.length) return;
    setApplying(true);
    setError(null);
    try {
      const data = await apiPost<{ results: ApplyResult[] }>(`${API_URL}/wlan-rename/apply`, {
        r1_controller_id: r1Id, tenant_id: tenantId,
        changes: chosen.map(r => ({
          network_id: r.id, name: r.newName, ...(r.newSsid !== null ? { ssid: r.newSsid } : {}),
        })),
      });
      setResults(data.results);
      // R1 confirmed each write; reflect it locally so renamed rows drop out of the list.
      const done = new Set(data.results.filter(x => x.status !== 'failed').map(x => x.network_id));
      const byId = new Map(chosen.map(r => [r.id, r]));
      setNetworks(ns => ns && ns.map(n => {
        const r = byId.get(n.id);
        return r && done.has(n.id) ? { ...n, name: r.newName, ssid: r.newSsid ?? n.ssid } : n;
      }));
    } catch (e: any) {
      setError(e.message || 'Rename failed');
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <PenLine size={24} className="text-indigo-600" />
          WLAN Rename
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Find and replace in R1 network names, across the whole tenant
        </p>
      </div>

      {!r1Slot ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start gap-2">
          <AlertCircle className="text-red-500 mt-0.5" size={18} />
          <p className="text-red-800 font-semibold text-sm">Select a RuckusONE controller.</p>
        </div>
      ) : (
        <>
          <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
            {isMSP && (
              <div className="mb-3 max-w-md">
                <SingleEcSelector
                  controllerId={r1Id}
                  onEcSelect={(id) => { setEcId(id); setNetworks(null); setResults(null); }}
                  selectedEcId={ecId}
                />
              </div>
            )}
            <div className="flex items-center gap-3">
              <button
                onClick={load}
                disabled={!ready || loading || applying}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading ? 'Reading networks...' : networks ? 'Reload networks' : 'Load networks'}
              </button>
              {networks && <span className="text-sm text-gray-500">{networks.length} networks in {r1Slot.name}</span>}
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </span>
              )}
            </div>

            {networks && (
              <div className="mt-4 pt-3 border-t border-gray-100 flex flex-wrap items-end gap-4 text-sm">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-gray-500">Find</span>
                  <input value={find} onChange={e => { setFind(e.target.value); setDeselected(new Set()); }}
                    placeholder="v2" className="border border-gray-300 rounded px-2 py-1 font-mono w-48" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-gray-500">Replace with (blank removes it)</span>
                  <input value={replace} onChange={e => { setReplace(e.target.value); setDeselected(new Set()); }}
                    className="border border-gray-300 rounded px-2 py-1 font-mono w-48" />
                </label>
                <label className="flex items-center gap-1.5 pb-1">
                  <input type="checkbox" checked={atEnd} onChange={e => setAtEnd(e.target.checked)} />
                  Only at the end of the name
                </label>
                <label className="flex items-center gap-1.5 pb-1">
                  <input type="checkbox" checked={withSsid} onChange={e => setWithSsid(e.target.checked)} />
                  Also change the SSID
                </label>
              </div>
            )}
          </div>

          {results && <ResultsPanel results={results} />}

          {confirming && (
            <ConfirmModal rows={chosen} onCancel={() => setConfirming(false)} onConfirm={applyChanges} />
          )}

          {networks && find && (
            <div className="bg-white border border-gray-200 rounded-lg">
              <div className="flex items-center gap-3 p-3 border-b border-gray-100 text-sm text-gray-600">
                {rows.length} would change{rows.length - okRows.length ? `, ${rows.length - okRows.length} blocked` : ''}
                <button
                  onClick={() => setConfirming(true)}
                  disabled={!chosen.length || applying || loading}
                  className="ml-auto px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                >
                  {applying && <Loader2 size={14} className="animate-spin" />}
                  {applying ? 'Renaming...' : `Rename ${chosen.length} network${chosen.length === 1 ? '' : 's'}`}
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
                          disabled={!okRows.length}
                          onChange={() => setDeselected(allSelected ? new Set(okRows.map(r => r.id)) : new Set())}
                        />
                      </th>
                      <th className="p-2 text-left">Current name</th>
                      <th className="p-2 text-left">New name</th>
                      <th className="p-2 text-left">SSID</th>
                      <th className="p-2 text-right">Venues</th>
                      <th className="p-2 text-left">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.id} className={`border-t border-gray-100 ${r.clash ? 'text-gray-400' : ''}`}>
                        <td className="p-2">
                          <input type="checkbox" disabled={!!r.clash}
                            checked={!r.clash && !deselected.has(r.id)} onChange={() => toggle(r.id)} />
                        </td>
                        <td className="p-2">{r.name}</td>
                        <td className={`p-2 ${r.clash ? '' : 'text-indigo-700 font-medium'}`}>
                          {r.newName !== r.name ? r.newName : <span className="text-gray-400">unchanged</span>}
                        </td>
                        <td className="p-2">
                          {r.newSsid !== null
                            ? <>{r.ssid} <ArrowRight size={12} className="inline mx-1" /><span className="text-indigo-700 font-medium">{r.newSsid}</span></>
                            : <span className="text-gray-500">{r.ssid}</span>}
                        </td>
                        <td className="p-2 text-right">{r.venues}</td>
                        <td className="p-2 text-xs text-red-600">{r.clash}</td>
                      </tr>
                    ))}
                    {!rows.length && (
                      <tr><td colSpan={6} className="p-4 text-center text-gray-400">No network names match.</td></tr>
                    )}
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

function ConfirmModal({ rows, onCancel, onConfirm }: {
  rows: Row[]; onCancel: () => void; onConfirm: () => void;
}) {
  const ssids = rows.filter(r => r.newSsid !== null).length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">
            Rename {rows.length} R1 network{rows.length === 1 ? '' : 's'}
          </h2>
        </div>
        <div className="p-4 max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-t border-gray-100 first:border-t-0">
                  <td className="py-1.5 text-gray-500">{r.name}</td>
                  <td className="py-1.5"><ArrowRight size={12} className="inline mx-1 text-gray-400" /></td>
                  <td className="py-1.5 text-indigo-700 font-medium">{r.newName}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {ssids > 0 && (
          <div className="mx-4 mb-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
            {ssids} SSID{ssids === 1 ? '' : 's'} change too — clients will see a new network name over the air.
          </div>
        )}
        <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
          <button onClick={onCancel} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={onConfirm} className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 font-medium">
            Rename {rows.length}
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
        {updated} renamed{unchanged ? `, ${unchanged} already named that` : ''}
        {failed.length ? `, ${failed.length} failed` : ''}
      </div>
      {failed.map(r => (
        <div key={r.network_id} className="text-red-700 text-xs mt-1">
          {r.before || r.network_id} → {r.name}: {r.error}
        </div>
      ))}
    </div>
  );
}
