import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle, AlertTriangle, XCircle, Clock, Hand, CircleDashed } from 'lucide-react';
import type {
  AlignmentCategory, CategoryStatus, AuditMatching, VenueSettingsAudit, SettingStatus, MatchMethod, OverviewRow,
} from '@/types/migrationAudit';

// ── Scorecard ────────────────────────────────────────────────────

const CAT_STYLE: Record<CategoryStatus, { label: string; cls: string; icon: typeof CheckCircle }> = {
  aligned:     { label: 'Aligned',     cls: 'text-green-700 bg-green-50 border-green-200',   icon: CheckCircle },
  partial:     { label: 'Partial',     cls: 'text-amber-700 bg-amber-50 border-amber-200',   icon: AlertTriangle },
  misaligned:  { label: 'Misaligned',  cls: 'text-red-700 bg-red-50 border-red-200',         icon: XCircle },
  pending:     { label: 'Pending',     cls: 'text-blue-700 bg-blue-50 border-blue-200',      icon: Clock },
  manual:      { label: 'Manual check', cls: 'text-purple-700 bg-purple-50 border-purple-200', icon: Hand },
  not_checked: { label: 'Not checked', cls: 'text-gray-500 bg-gray-50 border-gray-200',      icon: CircleDashed },
};

const fmt = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n));

export function Scorecard({ categories }: { categories: AlignmentCategory[] }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">Alignment scorecard — SZ → R1</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">Category</th>
              <th className="px-2 py-2 font-medium text-right">SZ</th>
              <th className="px-2 py-2 font-medium text-right">R1</th>
              <th className="px-2 py-2 font-medium text-right">Aligned</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {categories.map(c => <ScorecardRow key={c.key} c={c} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ScorecardRow({ c }: { c: AlignmentCategory }) {
  const [open, setOpen] = useState(false);
  const s = CAT_STYLE[c.status] ?? CAT_STYLE.not_checked;
  const Icon = s.icon;
  const expandable = c.details.length > 0 || c.breakdown.length > 0;
  return (
    <>
      <tr
        className={`border-b border-gray-100 ${expandable ? 'cursor-pointer hover:bg-gray-50' : ''}`}
        onClick={() => expandable && setOpen(!open)}
      >
        <td className="px-4 py-2 font-medium text-gray-800">
          <span className="inline-flex items-center gap-1.5">
            {expandable
              ? (open ? <ChevronDown size={13} className="text-gray-400" /> : <ChevronRight size={13} className="text-gray-400" />)
              : <span className="w-[13px]" />}
            {c.label}
          </span>
        </td>
        <td className="px-2 py-2 text-right tabular-nums">{fmt(c.sz_count)}</td>
        <td className="px-2 py-2 text-right tabular-nums">{fmt(c.r1_count)}</td>
        <td className="px-2 py-2 text-right tabular-nums">
          {c.aligned_count === null ? '—' : `${c.aligned_count}${c.sz_count ? ` / ${c.sz_count}` : ''}`}
        </td>
        <td className="px-4 py-2">
          <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border ${s.cls}`}>
            <Icon size={11} /> {s.label}
          </span>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-gray-100 bg-gray-50/50">
          <td colSpan={5} className="px-10 py-2 space-y-2">
            {c.details.map((d, i) => <div key={i} className="text-xs text-gray-600">• {d}</div>)}
            {c.breakdown.length > 0 && <Breakdown rows={c.breakdown} />}
          </td>
        </tr>
      )}
    </>
  );
}

function cell(v: any): string {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return String(v);
}

function Breakdown({ rows }: { rows: Array<Record<string, any>> }) {
  const cols = Array.from(new Set(rows.flatMap(r => Object.keys(r))));
  return (
    <div className="overflow-x-auto max-h-64 overflow-y-auto">
      <table className="text-xs">
        <thead>
          <tr className="text-gray-400 text-left">
            {cols.map(c => <th key={c} className="pr-4 py-0.5 font-medium">{c.replace(/_/g, ' ')}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="text-gray-700">
              {cols.map(c => <td key={c} className="pr-4 py-0.5 align-top">{cell(r[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Zone ↔ Venue settings ───────────────────────────────────────

const SET_STYLE: Record<SettingStatus, { label: string; cls: string }> = {
  match:       { label: 'Match',        cls: 'text-green-700' },
  differs:     { label: 'Differs',      cls: 'text-red-700 font-medium' },
  unmappable:  { label: 'No R1 value',  cls: 'text-amber-700' },
  sz_only:     { label: 'SZ only',      cls: 'text-gray-500' },
  unavailable: { label: 'Not read',     cls: 'text-gray-400' },
  review:      { label: 'Review',       cls: 'text-purple-700' },
};

export function VenueSettingsSection({ audit }: { audit: VenueSettingsAudit }) {
  const [onlyIssues, setOnlyIssues] = useState(false);
  const rows = onlyIssues ? audit.rows.filter(r => r.status !== 'match') : audit.rows;
  const sections = Array.from(new Set(rows.map(r => r.section)));
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-700">Zone ↔ Venue settings</h3>
        <label className="text-xs text-gray-600 flex items-center gap-1.5">
          <input type="checkbox" checked={onlyIssues} onChange={e => setOnlyIssues(e.target.checked)} />
          Only differences
        </label>
      </div>
      {audit.errors.length > 0 && (
        <div className="px-4 py-2 text-xs text-amber-700 border-b">Could not read from R1: {audit.errors.join('; ')}</div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">Setting</th>
              <th className="px-2 py-2 font-medium">SZ zone</th>
              <th className="px-2 py-2 font-medium">R1 venue</th>
              <th className="px-4 py-2 font-medium">Result</th>
            </tr>
          </thead>
          <tbody>
            {sections.map(sec => (
              <SectionRows key={sec} section={sec} rows={rows.filter(r => r.section === sec)} />
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-3 text-gray-500 italic">No differences.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SectionRows({ section, rows }: { section: string; rows: VenueSettingsAudit['rows'] }) {
  return (
    <>
      <tr className="bg-gray-50/70">
        <td colSpan={4} className="px-4 py-1 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{section}</td>
      </tr>
      {rows.map((r, i) => {
        const s = SET_STYLE[r.status] ?? SET_STYLE.review;
        return (
          <tr key={i} className="border-b border-gray-100 align-top">
            <td className="px-4 py-1.5 text-gray-700">{r.label}</td>
            <td className="px-2 py-1.5 font-mono">{cell(r.sz_value)}</td>
            <td className="px-2 py-1.5 font-mono">{cell(r.r1_value)}</td>
            <td className={`px-4 py-1.5 ${s.cls}`}>
              {s.label}
              {r.note && <div className="text-[11px] text-gray-400 font-normal">{r.note}</div>}
            </td>
          </tr>
        );
      })}
    </>
  );
}

// ── Pairings ─────────────────────────────────────────────────────

const METHOD_LABEL: Record<MatchMethod, string> = {
  name: 'name', name_case: 'name (case differs)', ssid: 'SSID', default: 'default group',
  serial: 'serial', mac: 'MAC', none: '—',
};

type PairTab = 'wlans' | 'wlan_groups' | 'ap_groups' | 'aps';
const PAIR_RENDER_CAP = 300;

function Lines({ items }: { items?: string[] | null }) {
  if (!items || items.length === 0) return <span className="text-gray-300">—</span>;
  return <>{items.map((s, i) => <div key={i}>{s}</div>)}</>;
}

export function MatchingSection({ matching }: { matching: AuditMatching }) {
  const [tab, setTab] = useState<PairTab>('wlans');
  const [apFilter, setApFilter] = useState<'all' | 'issues'>('issues');
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const has = (...vals: Array<string | null | undefined>) => !q || vals.some(v => (v || '').toLowerCase().includes(q));
  const wlans = matching.wlans.filter(w => has(w.sz_name, w.sz_ssid, w.r1_name, w.r1_ssid,
    ...(w.sz_assigned ?? []), ...(w.r1_activated ?? []), String(w.sz_vlan ?? ''), String(w.r1_vlan ?? '')));
  const wlanGroups = (matching.wlan_groups ?? []).filter(g => has(g.name, ...g.ssids, ...Object.keys(g.ap_groups),
    ...g.r1_groups_carrying_all));
  const tabs: Array<[PairTab, string]> = [
    ['wlans', `WLANs (${matching.wlans.length})`],
    ['wlan_groups', `WLAN Groups (${(matching.wlan_groups ?? []).length})`],
    ['ap_groups', `AP Groups (${matching.ap_groups.length})`],
    ['aps', `APs (${matching.aps.length})`],
  ];
  const shown = tab === 'wlans' ? wlans.length : tab === 'wlan_groups' ? wlanGroups.length : null;
  const aps = apFilter === 'all'
    ? matching.aps
    : matching.aps.filter(a => a.presence !== 'in_both' || a.group_aligned === false || a.name_alignment === 'differs');

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center gap-1 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-700 mr-3">What pairs with what</h3>
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`text-xs px-2.5 py-1 rounded ${tab === k ? 'bg-white border border-gray-300 font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
            {label}
          </button>
        ))}
        {(tab === 'wlans' || tab === 'wlan_groups') && (
          <span className="ml-auto flex items-center gap-2 text-xs">
            <input value={query} onChange={e => setQuery(e.target.value)}
              placeholder="Search name, SSID, group, VLAN"
              className="border border-gray-300 rounded px-2 py-1 w-56 max-w-full bg-white" />
            <span className="text-gray-400">
              {shown !== null && shown > PAIR_RENDER_CAP ? `${PAIR_RENDER_CAP} of ${shown}` : `${shown}`}
            </span>
          </span>
        )}
      </div>

      <div className="overflow-x-auto max-h-[32rem] overflow-y-auto">
        {tab === 'wlan_groups' && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white"><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">SZ WLAN Group</th>
              <th className="px-2 py-2 font-medium">SSIDs</th>
              <th className="px-2 py-2 font-medium">Applied on SZ</th>
              <th className="px-4 py-2 font-medium">R1 AP Groups carrying all its SSIDs</th>
            </tr></thead>
            <tbody>
              {wlanGroups.slice(0, PAIR_RENDER_CAP).map(g => {
                const applied: string[] = [];
                if (g.zone_bands.length) applied.push(`Zone-wide: ${g.zone_bands.join('/')} GHz`);
                Object.entries(g.ap_groups).forEach(([n, b]) => applied.push(`AP Group '${n}': ${b.join('/')} GHz`));
                if (g.ap_override_total) {
                  applied.push(`AP override on ${g.ap_override_total} AP${g.ap_override_total === 1 ? '' : 's'} (` +
                    Object.entries(g.ap_override_counts).map(([b, n]) => `${b} GHz: ${n}`).join(', ') + ')');
                }
                return (
                  <tr key={g.id} className="border-b border-gray-100 align-top">
                    <td className="px-4 py-1.5 font-medium">{g.name}</td>
                    <td className="px-2 py-1.5"><Lines items={g.ssids} /></td>
                    <td className={`px-2 py-1.5 ${applied.length ? 'text-gray-600' : 'text-gray-400 italic'}`}>
                      {applied.length ? <Lines items={applied} /> : 'Not applied anywhere'}
                      {g.ap_override_sample.length > 0 && (
                        <div className="text-[11px] text-gray-400">e.g. {g.ap_override_sample.slice(0, 4).join(', ')}</div>
                      )}
                    </td>
                    <td className={`px-4 py-1.5 ${g.r1_groups_carrying_all.length ? 'text-gray-700' : 'text-gray-400'}`}>
                      {g.r1_groups_carrying_all.length ? <Lines items={g.r1_groups_carrying_all} /> : 'none'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {tab === 'wlans' && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white"><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">SZ WLAN (SSID)</th>
              <th className="px-2 py-2 font-medium text-right">SZ VLAN</th>
              <th className="px-2 py-2 font-medium">SZ assigned via</th>
              <th className="px-2 py-2 font-medium">R1 network (SSID)</th>
              <th className="px-2 py-2 font-medium text-right">R1 VLAN</th>
              <th className="px-2 py-2 font-medium">R1 activated on</th>
              <th className="px-2 py-2 font-medium text-right" title="APs broadcasting it: SZ / R1">APs SZ / R1</th>
              <th className="px-4 py-2 font-medium">Matched by · notes</th>
            </tr></thead>
            <tbody>
              {wlans.slice(0, PAIR_RENDER_CAP).map(w => {
                const vlanOff = w.r1_network_id && w.sz_vlan != null && w.r1_vlan != null && w.sz_vlan !== w.r1_vlan;
                const apOff = w.r1_network_id && w.sz_ap_count != null && w.r1_ap_count != null && w.sz_ap_count !== w.r1_ap_count;
                return (
                  <tr key={w.sz_wlan_id} className="border-b border-gray-100 align-top">
                    <td className="px-4 py-1.5">{w.sz_name}<div className="text-gray-400">{w.sz_ssid}</div></td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${vlanOff ? 'text-red-700 font-semibold' : ''}`}>{w.sz_vlan ?? '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600"><Lines items={w.sz_assigned} /></td>
                    <td className={`px-2 py-1.5 ${!w.r1_network_id ? 'text-red-600' : !w.activated_in_venue ? 'text-amber-700' : ''}`}>
                      {w.r1_network_id ? <>{w.r1_name}<div className="text-gray-400">{w.r1_ssid}</div></> : 'not found'}
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${vlanOff ? 'text-red-700 font-semibold' : ''}`}>
                      {w.r1_network_id ? (w.r1_vlan ?? '—') : '—'}
                    </td>
                    <td className={`px-2 py-1.5 ${w.r1_all_ap_groups ? 'text-gray-700 font-medium' : 'text-gray-600'}`}>
                      <Lines items={w.r1_activated} />
                    </td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${apOff ? 'text-amber-700' : 'text-gray-500'}`}>
                      {w.sz_ap_count ?? '—'} / {w.r1_network_id ? (w.r1_ap_count ?? '—') : '—'}
                    </td>
                    <td className="px-4 py-1.5 text-gray-500">
                      <span className={w.method === 'ssid' || w.method === 'name_case' ? 'text-amber-700' : ''}>{METHOD_LABEL[w.method]}</span>
                      {w.notes.length > 0 && <div>{w.notes.join('; ')}</div>}
                    </td>
                  </tr>
                );
              })}
              {matching.r1_only_networks.map(n => (
                <tr key={n.id} className="border-b border-gray-100 text-purple-700">
                  <td className="px-4 py-1.5 text-gray-400">—</td>
                  <td className="px-2 py-1.5">{n.name} <span className="text-gray-400">({n.ssid})</span></td>
                  <td className="px-2 py-1.5">R1 only</td>
                  <td className="px-4 py-1.5 text-gray-500">In the venue, no SZ WLAN</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'ap_groups' && (
          <table className="w-full text-xs">
            <thead><tr className="text-gray-500 text-left border-b">
              <th className="px-4 py-2 font-medium">SZ AP Group</th>
              <th className="px-2 py-2 font-medium text-right">SZ APs</th>
              <th className="px-2 py-2 font-medium">R1 AP Group</th>
              <th className="px-2 py-2 font-medium text-right">R1 APs</th>
              <th className="px-2 py-2 font-medium">Matched by</th>
              <th className="px-4 py-2 font-medium">Notes</th>
            </tr></thead>
            <tbody>
              {matching.ap_groups.map(g => (
                <tr key={g.sz_ap_group_id} className="border-b border-gray-100 align-top">
                  <td className="px-4 py-1.5">{g.sz_name}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{g.sz_ap_count}</td>
                  <td className={`px-2 py-1.5 ${!g.r1_ap_group_id && g.sz_ap_count > 0 ? 'text-red-600' : ''}`}>
                    {g.r1_name ?? (g.sz_ap_count > 0 ? 'not found' : '—')}
                  </td>
                  <td className={`px-2 py-1.5 text-right tabular-nums ${g.r1_ap_group_id && g.r1_ap_count !== g.sz_ap_count ? 'text-amber-700' : ''}`}>
                    {g.r1_ap_group_id ? g.r1_ap_count : '—'}
                  </td>
                  <td className={`px-2 py-1.5 ${g.method === 'name_case' ? 'text-amber-700' : 'text-gray-500'}`}>{METHOD_LABEL[g.method]}</td>
                  <td className="px-4 py-1.5 text-gray-500">{g.notes.join('; ')}</td>
                </tr>
              ))}
              {matching.r1_only_ap_groups.map(g => (
                <tr key={g.id} className="border-b border-gray-100 text-purple-700">
                  <td className="px-4 py-1.5 text-gray-400">—</td>
                  <td className="px-2 py-1.5 text-right">—</td>
                  <td className="px-2 py-1.5">{g.name}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{g.ap_count}</td>
                  <td className="px-2 py-1.5">R1 only</td>
                  <td className="px-4 py-1.5" />
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'aps' && (
          <>
            <div className="px-4 py-2 text-xs flex items-center gap-3 border-b">
              <label className="flex items-center gap-1"><input type="radio" checked={apFilter === 'issues'} onChange={() => setApFilter('issues')} /> Only exceptions</label>
              <label className="flex items-center gap-1"><input type="radio" checked={apFilter === 'all'} onChange={() => setApFilter('all')} /> All APs</label>
            </div>
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 text-left border-b">
                <th className="px-4 py-2 font-medium">AP (SZ name / R1 name)</th>
                <th className="px-2 py-2 font-medium">Serial / MAC</th>
                <th className="px-2 py-2 font-medium">SZ group</th>
                <th className="px-2 py-2 font-medium">R1 group (expected)</th>
                <th className="px-4 py-2 font-medium">Where</th>
              </tr></thead>
              <tbody>
                {aps.map((a, i) => (
                  <tr key={i} className="border-b border-gray-100 align-top">
                    <td className={`px-4 py-1.5 ${a.name_alignment === 'differs' ? 'text-amber-700' : ''}`}>
                      {a.sz_name && a.r1_name && a.sz_name !== a.r1_name ? `${a.sz_name} / ${a.r1_name}` : a.name ?? '—'}
                    </td>
                    <td className="px-2 py-1.5 font-mono text-gray-500">{a.serial ?? '—'}<br />{a.mac ?? ''}</td>
                    <td className="px-2 py-1.5">{a.sz_ap_group ?? '—'}</td>
                    <td className={`px-2 py-1.5 ${a.group_aligned === false ? 'text-red-600' : ''}`}>
                      {a.r1_ap_group ?? '—'}
                      {a.expected_r1_ap_group && a.group_aligned === false && (
                        <span className="text-gray-500"> (expected {a.expected_r1_ap_group})</span>
                      )}
                    </td>
                    <td className={`px-4 py-1.5 ${a.presence === 'sz_only' ? 'text-blue-700' : a.presence === 'r1_only' ? 'text-purple-700' : 'text-gray-500'}`}>
                      {a.presence === 'in_both' ? `both (by ${METHOD_LABEL[a.method]})` : a.presence === 'sz_only' ? 'SZ only — not in R1 venue yet' : 'R1 only'}
                    </td>
                  </tr>
                ))}
                {aps.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-3 text-gray-500 italic">No exceptions.</td></tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

// ── Top summary: SZ left, R1 right ──────────────────────────────

export function OverviewTable({ rows, szName, r1Name }: { rows: OverviewRow[]; szName: string; r1Name: string }) {
  const mark: Record<OverviewRow['status'], { sym: string; cls: string; title: string }> = {
    match:   { sym: '=', cls: 'text-green-700', title: 'Same on both sides' },
    differs: { sym: '≠', cls: 'text-amber-700 font-bold', title: 'Different — worth a look' },
    info:    { sym: '~', cls: 'text-gray-400', title: 'Not one-to-one by design' },
  };
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-gray-500 border-b bg-gray-50">
            <th className="px-4 py-2 text-left font-medium">Summary</th>
            <th className="px-4 py-2 text-right font-medium">SmartZone<div className="font-normal text-gray-400 truncate">{szName}</div></th>
            <th className="px-2 py-2 w-8" />
            <th className="px-4 py-2 text-left font-medium">RuckusONE<div className="font-normal text-gray-400 truncate">{r1Name}</div></th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const m = mark[r.status] ?? mark.info;
            return (
              <tr key={r.label} className="border-b border-gray-100 align-top">
                <td className="px-4 py-2.5 font-medium text-gray-800">
                  {r.label}
                  {r.note && <div className="text-[11px] font-normal text-gray-500">{r.note}</div>}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <div className="text-lg font-semibold tabular-nums">{fmt(r.sz_value)}</div>
                  <div className="text-[11px] text-gray-400">{r.sz_note}</div>
                </td>
                <td className={`px-2 py-2.5 text-center text-lg ${m.cls}`} title={m.title}>{m.sym}</td>
                <td className="px-4 py-2.5">
                  <div className="text-lg font-semibold tabular-nums">{fmt(r.r1_value)}</div>
                  <div className="text-[11px] text-gray-400">{r.r1_note}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
