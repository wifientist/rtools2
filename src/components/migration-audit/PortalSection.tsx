import { useState } from 'react';
import { apiPost } from '@/utils/api';
import type { PortalAudit, PortalCheck } from '@/types/migrationAudit';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';
// Must equal audit_portal.KEY_ROW
const KEY_ROW = 'Signing key → integration key';

type Unpaired = { id: string; name: string | null; ssid: string | null; guest_type: string | null };
type KeyResult = { network_id: string; status: 'match' | 'differs' | 'not_set' | 'not_read'; note: string };
const KEY_RESULT: Record<KeyResult['status'], string> = {
  match: 'text-green-700', differs: 'text-red-700 font-semibold', not_set: 'text-red-700', not_read: 'text-amber-700',
};
const KEY_RESULT_LABEL: Record<KeyResult['status'], string> = {
  match: '✓ key check: match', differs: '✗ key check: differs', not_set: '✗ key check: R1 has no key', not_read: '? key check: not read',
};

const STATUS: Record<PortalAudit['status'], { label: string; cls: string }> = {
  match:   { label: 'All settings match', cls: 'bg-green-50 text-green-700 border-green-200' },
  differs: { label: 'Differs',            cls: 'bg-red-50 text-red-700 border-red-200' },
  unknown: { label: 'Partly unreadable',  cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  missing: { label: 'No R1 network',      cls: 'bg-red-50 text-red-700 border-red-200' },
};
const CHECK: Record<PortalCheck['status'], string> = {
  match: 'text-green-700', differs: 'text-red-700 font-semibold', unknown: 'text-amber-700', info: 'text-gray-500',
};
const CHECK_LABEL: Record<PortalCheck['status'], string> = {
  match: '✓ match', differs: '✗ differs', unknown: '? unknown', info: 'info',
};

// Captive portal WLANs, setting by setting. Every setting either side's
// portal holds is here: compared where R1 has a counterpart, listed per side
// where it doesn't. Keys show only as set / not set (plus a check result).
export default function PortalSection({ portals, unpaired, r1ControllerId, tenantId }: {
  portals: PortalAudit[]; unpaired: Unpaired[]; r1ControllerId: number | null; tenantId: string | null;
}) {
  const [keyResults, setKeyResults] = useState<Record<string, KeyResult>>({});
  if (!portals.length && !unpaired.length) return null;
  const resultFor = (p: PortalAudit) => (p.r1_network_id ? keyResults[p.r1_network_id] : undefined);
  const nDiffer = portals.filter(p => effectiveStatus(p, resultFor(p)) !== 'match').length;
  const keyNetworks = portals.filter(p => p.r1_network_id && p.checks.some(c => c.label === KEY_ROW))
    .map(p => p.r1_network_id as string);
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-700">Captive portal WLANs ({portals.length})</h3>
        <span className={`text-xs ${nDiffer ? 'text-red-700' : 'text-green-700'}`}>
          {nDiffer ? `${nDiffer} of ${portals.length} need a look` : 'Every compared setting matches'}
        </span>
      </div>
      {keyNetworks.length > 0 && r1ControllerId && (
        <KeyCheck networkIds={keyNetworks} r1ControllerId={r1ControllerId} tenantId={tenantId} onResults={setKeyResults} />
      )}
      {portals.map(p => <PortalCard key={p.sz_wlan_id} p={p} keyResult={resultFor(p)} />)}
      {unpaired.length > 0 && (
        <div className="px-4 py-2 text-xs text-amber-800 border-t bg-amber-50/40">
          R1 guest network(s) in this venue no SZ portal WLAN pairs with:{' '}
          {unpaired.map(u => `${u.name}${u.guest_type ? ` (${u.guest_type})` : ''}`).join(', ')}
        </div>
      )}
    </div>
  );
}

// A passed key check settles the one row SZ's API can't answer
function effectiveStatus(p: PortalAudit, kr?: KeyResult): PortalAudit['status'] {
  if (!kr) return p.status;
  if (kr.status === 'differs' || kr.status === 'not_set') return 'differs';
  if (kr.status !== 'match' || p.status !== 'unknown') return p.status;
  const rest = p.checks.filter(c => c.label !== KEY_ROW).map(c => c.status);
  if (rest.includes('differs')) return 'differs';
  return rest.includes('unknown') || p.notes.some(n => n.includes('refresh the SZ side')) ? 'unknown' : 'match';
}

// The user pastes the key (from the SZ UI or the portal provider); the
// server compares it with live R1 and answers match / differs only. The
// field is cleared on submit and the key is kept nowhere.
function KeyCheck({ networkIds, r1ControllerId, tenantId, onResults }: {
  networkIds: string[]; r1ControllerId: number; tenantId: string | null;
  onResults: (r: Record<string, KeyResult>) => void;
}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const entered = key;
    setKey('');
    if (!entered.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const data = await apiPost<{ results: KeyResult[]; format_note: string }>(
        `${API_URL}/sz-migration/audit/portal-key-check`,
        { r1_controller_id: r1ControllerId, tenant_id: tenantId, network_ids: networkIds, key: entered });
      onResults(Object.fromEntries(data.results.map(r => [r.network_id, r])));
      const ok = data.results.filter(r => r.status === 'match').length;
      setMsg({ text: `${ok} of ${data.results.length} R1 portal network(s) hold this key`
                     + (data.format_note ? ` — ${data.format_note}` : ''), error: ok < data.results.length });
    } catch (err: any) {
      setMsg({ text: err?.message || 'Key check failed', error: true });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="px-4 py-2 border-b bg-indigo-50/40 flex items-center gap-2 flex-wrap text-xs">
      <span className="font-medium text-gray-700">Check a portal key</span>
      <span className="text-gray-500">SZ's API won't return its signing key — paste it from the SZ UI or the portal provider:</span>
      <input type="password" autoComplete="off" spellCheck={false} value={key} onChange={e => setKey(e.target.value)}
             placeholder="portal / integration key" className="border border-gray-300 rounded px-2 py-1 font-mono w-56" />
      <button type="submit" disabled={busy || !key.trim()}
              className="px-2.5 py-1 rounded bg-indigo-600 text-white disabled:opacity-40">
        {busy ? 'Checking…' : 'Check against R1'}
      </button>
      {msg && <span className={msg.error ? 'text-red-700' : 'text-green-700'}>{msg.text}</span>}
      <span className="basis-full text-[10px] text-gray-400">
        Compared on the server with what live R1 holds; never stored, logged or shown.
      </span>
    </form>
  );
}

function PortalCard({ p, keyResult }: { p: PortalAudit; keyResult?: KeyResult }) {
  const [open, setOpen] = useState(p.status !== 'match');
  const [showAll, setShowAll] = useState(false);
  const st = STATUS[effectiveStatus(p, keyResult)];
  return (
    <div className="border-b last:border-b-0">
      <button type="button" onClick={() => setOpen(o => !o)}
              className="w-full px-4 py-2 flex items-center gap-3 text-left hover:bg-gray-50">
        <span className="text-gray-400 text-xs w-3">{open ? '▾' : '▸'}</span>
        <span className="font-medium text-sm text-gray-800">{p.sz_wlan_name}</span>
        <span className="text-xs text-gray-500">
          {p.sz_type}{p.sz_portal_name ? ` · portal "${p.sz_portal_name}"` : ''} → {p.r1_name ?? '—'}
          {p.r1_guest_type ? ` (${p.r1_guest_type})` : ''}
        </span>
        <span className={`ml-auto px-1.5 py-0.5 rounded border text-[10px] font-semibold ${st.cls}`}>{st.label}</span>
      </button>
      {open && (
        <div className="px-4 pb-3">
          {p.notes.map((n, i) => <div key={i} className="text-xs text-amber-700 mb-1">• {n}</div>)}
          {p.checks.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 text-left border-b">
                  <th className="py-1.5 pr-2 font-medium w-56">Setting</th>
                  <th className="py-1.5 pr-2 font-medium">SZ</th>
                  <th className="py-1.5 pr-2 font-medium">R1</th>
                  <th className="py-1.5 font-medium w-20">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {p.checks.map(c => (
                  <tr key={c.label} className={c.status === 'differs' ? 'bg-red-50/40' : ''}>
                    <td className="py-1 pr-2 text-gray-700">{c.label}</td>
                    <td className="py-1 pr-2 font-mono break-all">{c.sz}</td>
                    <td className="py-1 pr-2 font-mono break-all">{c.r1}</td>
                    <td className={`py-1 whitespace-nowrap ${CHECK[c.status]}`} title={c.note}>
                      {CHECK_LABEL[c.status]}
                      {c.note && <div className="text-[10px] text-gray-400 font-normal whitespace-normal">{c.note}</div>}
                      {c.label === KEY_ROW && keyResult && (
                        <div className={`mt-0.5 ${KEY_RESULT[keyResult.status]}`}>
                          {KEY_RESULT_LABEL[keyResult.status]}
                          {keyResult.note && <div className="text-[10px] font-normal whitespace-normal">{keyResult.note}</div>}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(p.sz_only.length > 0 || p.r1_only.length > 0) && (
            <div className="mt-2">
              <button type="button" onClick={() => setShowAll(s => !s)} className="text-xs text-indigo-600 hover:underline">
                {showAll ? 'Hide' : 'Show'} settings with no counterpart ({p.sz_only.length} SZ · {p.r1_only.length} R1)
              </button>
              {showAll && (
                <div className="grid md:grid-cols-2 gap-4 mt-1.5">
                  <OneSide title="SZ portal only" rows={p.sz_only.map(c => [c.label, c.sz])} />
                  <OneSide title="R1 guest portal only" rows={p.r1_only.map(c => [c.label, c.r1])} />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OneSide({ title, rows }: { title: string; rows: Array<[string, string]> }) {
  return (
    <div>
      <div className="text-[11px] font-semibold text-gray-600 mb-0.5">{title}</div>
      {rows.length === 0 ? <div className="text-xs text-gray-400">—</div> : (
        <table className="w-full text-xs">
          <tbody className="divide-y divide-gray-100">
            {rows.map(([k, v]) => (
              <tr key={k}>
                <td className="py-0.5 pr-2 text-gray-500 font-mono">{k}</td>
                <td className="py-0.5 font-mono break-all whitespace-pre-line">{v.length > 300 ? `${v.slice(0, 300)}…` : v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
