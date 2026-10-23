import { useMemo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import type { BroadcastAudit, ApBroadcast } from '@/types/migrationAudit';

// CSV text of the broadcast comparison, for feeding another tool. Names and
// SSIDs only -- nothing here carries a credential. Multi-value cells are
// joined with " | ".

type View = 'aps' | 'sets';
const SEP = ' | ';

const cell = (v: unknown) => {
  const s = v == null ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const toCsv = (rows: unknown[][]) => rows.map(r => r.map(cell).join(',')).join('\n');
const setKey = (ssids: string[]) => [...ssids].sort().join('\u0000');

interface SszSet { id: string; ssids: string[]; aps: ApBroadcast[] }

// Distinct SZ broadcast sets, biggest first; S1, S2, ... are stable for a
// given report so both views share them.
function buildSets(aps: ApBroadcast[]): { sets: SszSet[]; idOf: Map<string, string> } {
  const byKey = new Map<string, ApBroadcast[]>();
  for (const a of aps) {
    if (a.presence === 'r1_only' || a.sz_ssids.length === 0) continue;
    const k = setKey(a.sz_ssids);
    byKey.set(k, [...(byKey.get(k) ?? []), a]);
  }
  const ordered = [...byKey.entries()].sort((x, y) => y[1].length - x[1].length || x[0].localeCompare(y[0]));
  const idOf = new Map<string, string>();
  const sets = ordered.map(([k, members], i) => {
    const id = `S${i + 1}`;
    idOf.set(k, id);
    return { id, ssids: [...members[0].sz_ssids].sort(), aps: members };
  });
  return { sets, idOf };
}

export default function BroadcastCsv({ audit }: { audit: BroadcastAudit }) {
  const [view, setView] = useState<View>('sets');
  const [onlyDiffs, setOnlyDiffs] = useState(false);
  const [copied, setCopied] = useState(false);

  const { sets, idOf } = useMemo(() => buildSets(audit.aps), [audit.aps]);

  const text = useMemo(() => {
    if (view === 'aps') {
      const rows = audit.aps.filter(a => !onlyDiffs || a.status !== 'match');
      return toCsv([
        ['ap_name', 'serial', 'mac', 'set_id', 'sz_ap_group', 'sz_ssids', 'sz_sources',
         'r1_ap_group', 'r1_ssids', 'r1_ssids_via_all_ap_groups', 'missing_in_r1', 'extra_in_r1', 'status'],
        ...rows.map(a => [
          a.name, a.serial, a.mac, idOf.get(setKey(a.sz_ssids)) ?? '', a.sz_ap_group,
          [...a.sz_ssids].sort().join(SEP), a.sz_sources.join(SEP),
          a.r1_ap_group, [...a.r1_ssids].sort().join(SEP), (a.r1_via_all_ap_groups ?? []).join(SEP),
          a.missing_in_r1.join(SEP), a.extra_in_r1.join(SEP), a.status,
        ]),
      ]);
    }
    const exact = new Map(audit.groups.map(g => [setKey(g.ssids), g.r1_groups_matching_ssids]));
    const rows = sets
      .map(s => {
        const inR1 = s.aps.filter(a => a.presence === 'in_both');
        const groups: Record<string, number> = {};
        for (const a of inR1) groups[a.r1_ap_group ?? '?'] = (groups[a.r1_ap_group ?? '?'] ?? 0) + 1;
        const differ = s.aps.filter(a => a.status !== 'match').length;
        return { s, groups, differ, pending: s.aps.length - inR1.length };
      })
      .filter(r => !onlyDiffs || r.differ > 0);
    return toCsv([
      ['set_id', 'ssid_count', 'ssids', 'ap_count', 'aps_differing', 'aps_not_in_r1',
       'current_r1_ap_groups', 'r1_groups_with_exactly_these_ssids', 'ap_names', 'ap_serials'],
      ...rows.map(({ s, groups, differ, pending }) => [
        s.id, s.ssids.length, s.ssids.join(SEP), s.aps.length, differ, pending,
        Object.entries(groups).map(([n, c]) => `${n} (${c})`).join(SEP),
        (exact.get(setKey(s.ssids)) ?? []).join(SEP),
        s.aps.map(a => a.name ?? '').join(SEP), s.aps.map(a => a.serial ?? a.mac ?? '').join(SEP),
      ]),
    ]);
  }, [view, onlyDiffs, audit.aps, audit.groups, sets, idOf]);

  const lines = text.split('\n').length - 1;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* textarea below stays selectable */ }
  };

  return (
    <div className="px-4 py-3 border-b bg-white space-y-2">
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <span className="font-medium text-gray-700">CSV</span>
        {([['sets', `One row per SZ broadcast set (${sets.length})`], ['aps', `One row per AP (${audit.aps.length})`]] as Array<[View, string]>).map(([k, l]) => (
          <label key={k} className="flex items-center gap-1 text-gray-600">
            <input type="radio" checked={view === k} onChange={() => setView(k)} /> {l}
          </label>
        ))}
        <label className="flex items-center gap-1 text-gray-600">
          <input type="checkbox" checked={onlyDiffs} onChange={e => setOnlyDiffs(e.target.checked)} /> Only rows that differ
        </label>
        <button onClick={copy} className="ml-auto flex items-center gap-1 px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">
          {copied ? <Check size={12} className="text-green-600" /> : <Copy size={12} />} {copied ? 'Copied' : `Copy ${lines} rows`}
        </button>
      </div>
      <textarea
        readOnly value={text} onFocus={e => e.currentTarget.select()}
        className="w-full h-48 font-mono text-[11px] border border-gray-200 rounded p-2 bg-gray-50 whitespace-pre"
        spellCheck={false}
      />
      <div className="text-[11px] text-gray-400">
        A set is the exact SSIDs SZ broadcasts on an AP after zone, AP Group and per-AP overrides — each set is one R1 AP Group's worth of WLANs.
        set_id links the two views. Multi-value cells are separated by " | ".
      </div>
    </div>
  );
}
