import { useMemo, useState } from 'react';
import type { BroadcastAudit, ApBroadcast } from '@/types/migrationAudit';

type Tab = 'aps' | 'ssids' | 'groups';
const RENDER_CAP = 300;

const AP_STATUS: Record<ApBroadcast['status'], { label: string; cls: string }> = {
  match:   { label: 'Same SSIDs',        cls: 'text-green-700' },
  differs: { label: 'Differs',           cls: 'text-red-700 font-medium' },
  unknown: { label: 'Same (SZ AP overrides unread)', cls: 'text-amber-700' },
  pending: { label: 'Not in R1 yet',     cls: 'text-blue-700' },
  r1_only: { label: 'R1 only',           cls: 'text-purple-700' },
};

// One SSID per line -- easier to scan than a comma list.
function List({ items, cls = '' }: { items: string[]; cls?: string }) {
  if (!items.length) return <span className="text-gray-300">—</span>;
  return <div className={cls}>{items.map((s, i) => <div key={i}>{s}</div>)}</div>;
}

export default function BroadcastSection({ audit }: { audit: BroadcastAudit }) {
  const [tab, setTab] = useState<Tab>('aps');
  const [query, setQuery] = useState('');
  const [onlyIssues, setOnlyIssues] = useState(true);
  const q = query.trim().toLowerCase();
  const c = audit.counts;

  const aps = useMemo(() => audit.aps.filter(a => {
    if (onlyIssues && (a.status === 'match')) return false;
    if (!q) return true;
    return [a.name, a.serial, a.mac, a.sz_ap_group, a.r1_ap_group, ...a.sz_ssids, ...a.r1_ssids]
      .some(v => (v || '').toLowerCase().includes(q));
  }), [audit.aps, onlyIssues, q]);

  const ssids = useMemo(() => audit.ssids.filter(s =>
    (!onlyIssues || s.status !== 'match') && (!q || s.ssid.toLowerCase().includes(q))
  ), [audit.ssids, onlyIssues, q]);

  const groups = useMemo(() => audit.groups.filter(g =>
    (!onlyIssues || g.status !== 'match') &&
    (!q || g.ssids.some(s => s.toLowerCase().includes(q)) || Object.keys(g.r1_ap_groups).some(n => n.toLowerCase().includes(q)))
  ), [audit.groups, onlyIssues, q]);

  const tabs: Array<[Tab, string]> = [
    ['aps', `APs (${c.aps_differs ?? 0} differ of ${c.aps_compared ?? 0})`],
    ['ssids', `SSIDs (${audit.ssids.length})`],
    ['groups', `Broadcast groups (${audit.groups.length})`],
  ];
  const shown = tab === 'aps' ? aps.length : tab === 'ssids' ? ssids.length : groups.length;

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 space-y-2">
        <div className="flex items-center gap-1 flex-wrap">
          <h3 className="text-sm font-semibold text-gray-700 mr-3">What each AP broadcasts — SZ vs R1</h3>
          {tabs.map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={`text-xs px-2.5 py-1 rounded ${tab === k ? 'bg-white border border-gray-300 font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 flex-wrap text-xs">
          <input
            value={query} onChange={e => setQuery(e.target.value)}
            placeholder="Search AP, serial, group or SSID"
            className="border border-gray-300 rounded px-2 py-1 w-64 max-w-full bg-white"
          />
          <label className="flex items-center gap-1 text-gray-600">
            <input type="checkbox" checked={onlyIssues} onChange={e => setOnlyIssues(e.target.checked)} />
            Only differences
          </label>
          <span className="text-gray-400">
            {shown > RENDER_CAP ? `Showing ${RENDER_CAP} of ${shown} — narrow with search` : `${shown} shown`}
          </span>
        </div>
        {audit.notes.map((n, i) => <div key={i} className="text-[11px] text-gray-500">• {n}</div>)}
      </div>

      <div className="overflow-x-auto max-h-[32rem] overflow-y-auto">
        {tab === 'aps' && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white"><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">AP</th>
              <th className="px-2 py-2 font-medium">SZ SSIDs <span className="font-normal text-gray-400">· from which WLAN Group</span></th>
              <th className="px-2 py-2 font-medium">R1 SSIDs <span className="font-normal text-gray-400">· AP Group</span></th>
              <th className="px-2 py-2 font-medium">Missing in R1</th>
              <th className="px-2 py-2 font-medium">Extra in R1</th>
              <th className="px-4 py-2 font-medium">Result</th>
            </tr></thead>
            <tbody>
              {aps.slice(0, RENDER_CAP).map((a, i) => {
                const s = AP_STATUS[a.status];
                return (
                  <tr key={i} className="border-b border-gray-100 align-top">
                    <td className="px-4 py-1.5">
                      <div className="font-medium">{a.name ?? '—'}</div>
                      <div className="text-gray-400 font-mono">{a.serial ?? a.mac}</div>
                    </td>
                    <td className="px-2 py-1.5">
                      <List items={a.sz_ssids} />
                      {a.sz_sources.length > 0 && (
                        <div className="text-[11px] text-gray-400 mt-0.5">{a.sz_sources.map((s, i) => <div key={i}>{s}</div>)}</div>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <List items={a.r1_ssids.map(s => (a.r1_via_all_ap_groups ?? []).includes(s) ? `${s}  (All AP Groups)` : s)} />
                      <div className="text-[11px] text-gray-400 mt-0.5">AP Group: {a.r1_ap_group ?? (a.status === 'pending' ? 'not in R1 yet' : '—')}</div>
                    </td>
                    <td className="px-2 py-1.5"><List items={a.missing_in_r1} cls="text-red-600" /></td>
                    <td className="px-2 py-1.5"><List items={a.extra_in_r1} cls="text-purple-700" /></td>
                    <td className={`px-4 py-1.5 ${s.cls}`}>{s.label}</td>
                  </tr>
                );
              })}
              {aps.length === 0 && <tr><td colSpan={6} className="px-4 py-3 text-gray-500 italic">Nothing to show.</td></tr>}
            </tbody>
          </table>
        )}

        {tab === 'ssids' && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white"><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">SSID</th>
              <th className="px-2 py-2 font-medium text-right">SZ APs</th>
              <th className="px-2 py-2 font-medium text-right">R1 APs</th>
              <th className="px-2 py-2 font-medium text-right" title="R1's own apCount on the network">R1 reports</th>
              <th className="px-2 py-2 font-medium">Missing on</th>
              <th className="px-4 py-2 font-medium">Extra on</th>
            </tr></thead>
            <tbody>
              {ssids.slice(0, RENDER_CAP).map(s => (
                <tr key={s.ssid} className="border-b border-gray-100 align-top">
                  <td className={`px-4 py-1.5 font-medium ${s.status === 'missing' ? 'text-red-600' : s.status === 'extra' ? 'text-purple-700' : ''}`}>
                    {s.ssid}
                    {s.status === 'missing' && <span className="font-normal text-[11px]"> — on no R1 AP</span>}
                    {s.status === 'extra' && <span className="font-normal text-[11px]"> — not broadcast on SZ</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{s.sz_ap_count}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums ${s.r1_ap_count !== s.sz_ap_count ? 'text-amber-700' : ''}`}>{s.r1_ap_count}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-gray-400">{s.r1_reported_ap_count ?? '—'}</td>
                  <td className="px-2 py-1.5 text-red-600">
                    {s.missing_in_r1 ? `${s.missing_in_r1}: ${s.sample_missing.join(', ')}${s.missing_in_r1 > s.sample_missing.length ? '…' : ''}` : ''}
                  </td>
                  <td className="px-4 py-1.5 text-purple-700">
                    {s.extra_in_r1 ? `${s.extra_in_r1}: ${s.sample_extra.join(', ')}${s.extra_in_r1 > s.sample_extra.length ? '…' : ''}` : ''}
                  </td>
                </tr>
              ))}
              {ssids.length === 0 && <tr><td colSpan={6} className="px-4 py-3 text-gray-500 italic">Nothing to show.</td></tr>}
            </tbody>
          </table>
        )}

        {tab === 'groups' && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white"><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">SZ broadcast set (SSIDs)</th>
              <th className="px-2 py-2 font-medium text-right">APs</th>
              <th className="px-2 py-2 font-medium">In R1 AP Group(s)</th>
              <th className="px-2 py-2 font-medium">R1 groups carrying exactly this set</th>
              <th className="px-4 py-2 font-medium">Result</th>
            </tr></thead>
            <tbody>
              {groups.slice(0, RENDER_CAP).map((g, i) => (
                <tr key={i} className="border-b border-gray-100 align-top">
                  <td className="px-4 py-1.5">
                    <List items={g.ssids} />
                    <div className="text-[11px] text-gray-400">e.g. {g.sample_aps.join(', ')}</div>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{g.sz_ap_count}</td>
                  <td className="px-2 py-1.5">
                    {Object.entries(g.r1_ap_groups).map(([n, k]) => `${n} (${k})`).join(', ') || '—'}
                    {g.r1_not_in_venue > 0 && <div className="text-blue-700">{g.r1_not_in_venue} not in R1 yet</div>}
                  </td>
                  <td className="px-2 py-1.5 text-gray-600">{g.r1_groups_matching_ssids.join(', ') || <span className="text-gray-300">none</span>}</td>
                  <td className={`px-4 py-1.5 ${g.status === 'match' ? 'text-green-700' : g.status === 'split' ? 'text-amber-700' : g.status === 'pending' ? 'text-blue-700' : 'text-red-700 font-medium'}`}>
                    {g.status === 'match' ? 'Same SSIDs' : g.status === 'split' ? 'Same SSIDs, several R1 groups'
                      : g.status === 'pending' ? 'Not in R1 yet' : 'Differs'}
                  </td>
                </tr>
              ))}
              {groups.length === 0 && <tr><td colSpan={5} className="px-4 py-3 text-gray-500 italic">Nothing to show.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
