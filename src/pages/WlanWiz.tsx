import { useState, useMemo, Fragment, type ReactNode } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Target, Loader2, RefreshCw, Search, Download, Wand2, ChevronDown, ChevronRight,
  ArrowUp, ArrowDown, X,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/wlan_wiz.py) ─────────────────────────────

interface Activation { venue_id: string; all_groups: boolean; group_ids: string[] }
interface Network {
  id: string; name: string; ssid: string; description: string; type: string; security: string;
  captive: string; vlan: number | null; vlan_pool: string | null; ap_count: number; client_count: number;
  activations: Activation[];
}
interface Venue { id: string; name: string }
interface TenantData { networks: Network[]; venues: Venue[] }

type WlanState = 'active' | 'off' | 'scheduled' | 'unknown';
interface VenueAct { network_id: string; name: string; ssid: string; all_groups: boolean; group_ids: string[]; state: WlanState }
interface Group { id: string; name: string; description: string; is_default: boolean; client_count: number }
interface Ap {
  serial: string; name: string; model: string; status: string; state: 'online' | 'offline' | 'other' | string;
  mac: string; ip: string; firmware: string; group_id: string | null; clients: number; tags: string[];
  radios: { band: string; channel: number | null; ssids: string[] }[]; ssids_on_air: string[];
}
interface VenueData {
  venue_id: string; venue_name: string; groups: Group[]; aps: Ap[]; activations: VenueAct[];
  stray_aps: number; settings_failed: number;
}

/** Everything loaded, joined once; every view reads this. */
interface Model {
  networks: Network[]; netById: Map<string, Network>; venueName: Map<string, string>;
  groups: Group[]; aps: Ap[]; acts: VenueAct[]; actByNet: Map<string, VenueAct>;
  groupById: Map<string, Group>; apsByGroup: Map<string, Ap[]>; wlansByGroup: Map<string, VenueAct[]>;
  groupsOfAct: (a: VenueAct) => string[]; onAirCount: Map<string, number>; apsHere: (a: VenueAct) => number;
}

type Tab = 'wlans' | 'groups' | 'aps' | 'matrix';
const SSID_LIMIT = 15;

const STATE_CLS: Record<WlanState, string> = {
  active: 'text-green-700 bg-green-50 border-green-200',
  off: 'text-gray-400 bg-gray-50 border-gray-200 line-through',
  scheduled: 'text-indigo-700 bg-indigo-50 border-indigo-200',
  unknown: 'text-amber-700 bg-amber-50 border-amber-200',
};

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;
const fold = (s: string | null | undefined) => (s || '').toLowerCase();
const cmp = (a: any, b: any) =>
  typeof a === 'number' && typeof b === 'number' ? a - b
    : String(a ?? '').localeCompare(String(b ?? ''), undefined, { numeric: true, sensitivity: 'base' });

/** The part of a group name a unit WLAN would carry: "1113@Site" -> "1113". */
const groupKey = (name: string) => (name || '').split('@')[0].trim().toLowerCase();

function downloadCsv(filename: string, header: string[], rows: (string | number)[][]) {
  const esc = (v: string | number) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob([[header, ...rows].map(r => r.map(esc).join(',')).join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Generic sortable table ───────────────────────────────────────

interface Col<T> {
  key: string;
  label: string;
  value: (r: T) => string | number;           // sort + CSV
  render?: (r: T) => ReactNode;               // cell (defaults to value)
  className?: string;
  title?: string;
}

const ROW_CAP = 500;

function DataTable<T>({ rows, cols, rowKey, defaultSort, expand, csvName }: {
  rows: T[];
  cols: Col<T>[];
  rowKey: (r: T) => string;
  defaultSort: { key: string; desc: boolean };
  expand?: (r: T) => ReactNode;
  csvName: string;
}) {
  const [sort, setSort] = useState(defaultSort);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);

  const sorted = useMemo(() => {
    const col = cols.find(c => c.key === sort.key) || cols[0];
    const out = [...rows].sort((a, b) => cmp(col.value(a), col.value(b)));
    return sort.desc ? out.reverse() : out;
  }, [rows, cols, sort]);
  const shown = showAll ? sorted : sorted.slice(0, ROW_CAP);

  const clickHeader = (key: string) =>
    setSort(s => s.key === key ? { key, desc: !s.desc } : { key, desc: false });
  const toggle = (k: string) => setOpen(o => {
    const n = new Set(o); n.has(k) ? n.delete(k) : n.add(k); return n;
  });

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 text-xs text-gray-500">
        <span>{plural(sorted.length, 'row')}{sorted.length > shown.length && ` — showing ${shown.length}`}</span>
        <div className="flex items-center gap-3">
          {expand && open.size > 0 && (
            <button onClick={() => setOpen(new Set())} className="hover:text-gray-800">Collapse all</button>
          )}
          <button
            onClick={() => downloadCsv(csvName, cols.map(c => c.label), sorted.map(r => cols.map(c => c.value(r))))}
            className="flex items-center gap-1 hover:text-gray-800"
          >
            <Download size={12} /> CSV
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              {expand && <th className="w-6" />}
              {cols.map(c => (
                <th key={c.key} title={c.title}
                  className={`px-2 py-2 text-left font-medium whitespace-nowrap cursor-pointer select-none hover:text-gray-900 ${c.className || ''}`}
                  onClick={() => clickHeader(c.key)}>
                  <span className="inline-flex items-center gap-0.5">
                    {c.label}
                    {sort.key === c.key && (sort.desc ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map(r => {
              const k = rowKey(r);
              const isOpen = open.has(k);
              return (
                <Fragment key={k}>
                  <tr className="border-t border-gray-100 hover:bg-gray-50 align-top">
                    {expand && (
                      <td className="pl-2 py-1.5 cursor-pointer text-gray-400" onClick={() => toggle(k)}>
                        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </td>
                    )}
                    {cols.map(c => (
                      <td key={c.key} className={`px-2 py-1.5 ${c.className || ''}`}>
                        {c.render ? c.render(r) : c.value(r)}
                      </td>
                    ))}
                  </tr>
                  {expand && isOpen && (
                    <tr className="bg-gray-50/60">
                      <td />
                      <td colSpan={cols.length} className="px-2 py-2">{expand(r)}</td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {sorted.length > ROW_CAP && (
        <div className="px-3 py-2 border-t border-gray-100 text-xs">
          <button onClick={() => setShowAll(s => !s)} className="text-indigo-600 hover:underline">
            {showAll ? `Show first ${ROW_CAP}` : `Show all ${sorted.length}`}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Small pieces ─────────────────────────────────────────────────

function Chips<K extends string>({ value, onChange, options }: {
  value: K; onChange: (k: K) => void; options: { key: K; label: string; count?: number }[];
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(o => (
        <button key={o.key} onClick={() => onChange(o.key)}
          className={`px-2.5 py-1 rounded-full text-xs border ${value === o.key
            ? 'bg-indigo-600 text-white border-indigo-600'
            : 'bg-white text-gray-600 border-gray-300 hover:border-gray-400'}`}>
          {o.label}{o.count !== undefined && <span className="opacity-70"> {o.count}</span>}
        </button>
      ))}
    </div>
  );
}

function Link({ children, onClick, title }: { children: ReactNode; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} className="text-indigo-700 hover:underline text-left">{children}</button>
  );
}

function WlanChip({ name, state, viaAll, onClick }: { name: string; state: WlanState; viaAll?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick}
      title={`${state}${viaAll ? ' — via All AP Groups' : ''}`}
      className={`inline-block px-1.5 py-0.5 mr-1 mb-1 rounded border text-xs ${STATE_CLS[state]} ${viaAll ? 'border-dashed' : ''}`}>
      {name}
    </button>
  );
}

function ApState({ state, status }: { state: string; status: string }) {
  const cls = state === 'online' ? 'bg-green-500' : state === 'offline' ? 'bg-red-500' : 'bg-amber-400';
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={status}>
      <span className={`w-2 h-2 rounded-full ${cls}`} />{status || state}
    </span>
  );
}

// ── Page ─────────────────────────────────────────────────────────

export default function WlanWiz() {
  const {
    activeControllerId: aId, activeControllerName: aName, activeControllerType: aType,
    activeControllerSubtype: aSub,
    secondaryControllerId: sId, secondaryControllerName: sName, secondaryControllerType: sType,
    secondaryControllerSubtype: sSub,
    controllers,
  } = useAuth();

  const r1Slot = [
    { id: aId, name: aName, type: aType, subtype: aSub },
    { id: sId, name: sName, type: sType, subtype: sSub },
  ].find(s => s.id && s.type === 'RuckusONE');
  const r1Id = r1Slot?.id ?? null;
  const isMSP = r1Slot?.subtype === 'MSP';
  const r1Controller = controllers?.find((c: any) => c.id === r1Id);

  const [ecId, setEcId] = useState<string | null>(null);
  const [venueId, setVenueId] = useState<string | null>(null);
  const tenantId = isMSP ? ecId : (r1Controller?.r1_tenant_id || null);
  const ready = !!r1Id && (!isMSP || !!ecId);

  const [tenant, setTenant] = useState<TenantData | null>(null);
  const [venue, setVenue] = useState<VenueData | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTab] = useState<Tab>('wlans');
  const [search, setSearch] = useState('');

  const load = async (refreshTenant: boolean) => {
    if (!ready) return;
    setError(null);
    const body = { controller_id: r1Id, tenant_id: tenantId };
    try {
      const jobs: Promise<void>[] = [];
      if (refreshTenant || !tenant) {
        jobs.push(apiPost<TenantData>(`${API_URL}/wlan-wiz/networks`, body).then(setTenant));
      }
      if (venueId) {
        jobs.push(apiPost<VenueData>(`${API_URL}/wlan-wiz/venue`, { ...body, venue_id: venueId }).then(setVenue));
      } else {
        setVenue(null);
      }
      setLoading(venueId ? 'Reading WLANs and the venue...' : 'Reading WLANs...');
      await Promise.all(jobs);
    } catch (e: any) {
      setError(e.message || 'Could not read from R1');
    } finally {
      setLoading(null);
    }
  };

  const jump = (t: Tab, q: string) => { setTab(t); setSearch(q); };

  // ── Joins ──
  const m = useMemo((): Model => {
    const networks = tenant?.networks || [];
    const netById = new Map(networks.map(n => [n.id, n]));
    const venueName = new Map((tenant?.venues || []).map(v => [v.id, v.name]));
    const groups = venue?.groups || [];
    const aps = venue?.aps || [];
    const acts = venue?.activations || [];
    const actByNet = new Map(acts.map(a => [a.network_id, a]));
    const groupById = new Map(groups.map(g => [g.id, g]));

    const apsByGroup = new Map<string, Ap[]>();
    for (const ap of aps) {
      if (!ap.group_id) continue;
      if (!apsByGroup.has(ap.group_id)) apsByGroup.set(ap.group_id, []);
      apsByGroup.get(ap.group_id)!.push(ap);
    }
    const groupsOfAct = (a: VenueAct) => a.all_groups ? groups.map(g => g.id) : a.group_ids;
    const wlansByGroup = new Map<string, VenueAct[]>(groups.map(g => [g.id, []]));
    for (const a of acts) for (const gid of groupsOfAct(a)) wlansByGroup.get(gid)?.push(a);

    // On air is reported by SSID name; match either the SSID or the network name.
    const onAirCount = new Map<string, number>();
    for (const a of acts) {
      const names = new Set([fold(a.ssid), fold(a.name)].filter(Boolean));
      onAirCount.set(a.network_id, aps.filter(ap => ap.ssids_on_air.some(s => names.has(fold(s)))).length);
    }
    const apsHere = (a: VenueAct) => groupsOfAct(a).reduce((n, gid) => n + (apsByGroup.get(gid)?.length || 0), 0);

    return { networks, netById, venueName, groups, aps, acts, actByNet, groupById, apsByGroup,
             wlansByGroup, groupsOfAct, onAirCount, apsHere };
  }, [tenant, venue]);

  const q = search.trim().toLowerCase();
  const view: Tab = venue ? tab : 'wlans';

  return (
    <div className="p-4 max-w-[1600px] mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Wand2 size={24} className="text-indigo-600" />
          WLAN Wiz
          <span className="text-xs bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded font-medium">ALPHA</span>
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Every WLAN in the tenant, and a venue's AP Groups and APs, in one place — read-only. Load once, then slice.
        </p>
      </div>

      {!r1Slot ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start gap-2">
          <AlertCircle className="text-red-500 mt-0.5" size={18} />
          <div>
            <p className="text-red-800 font-semibold text-sm">Controller Setup Required</p>
            <p className="text-red-600 text-sm mt-1">Select a RuckusONE controller.</p>
          </div>
        </div>
      ) : (
        <>
          <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
              <Target size={14} className="text-green-600" /> {r1Slot.name}
              <span className="text-gray-400 text-xs font-normal">(R1{isMSP ? ' MSP' : ''})</span>
            </h3>
            <div className="space-y-3">
              {isMSP && (
                <SingleEcSelector
                  controllerId={r1Id}
                  onEcSelect={(id) => { setEcId(id); setVenueId(null); setTenant(null); setVenue(null); }}
                  selectedEcId={ecId}
                />
              )}
              {(!isMSP || ecId) && (
                <SingleVenueSelector
                  controllerId={r1Id}
                  tenantId={tenantId}
                  onVenueSelect={(id) => { setVenueId(id); setVenue(null); }}
                  selectedVenueId={venueId}
                />
              )}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3 pt-3 border-t border-gray-100">
              <button
                onClick={() => load(false)}
                disabled={!ready || !!loading}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading || (venueId ? 'Load WLANs + venue' : 'Load WLANs (pick a venue for groups and APs)')}
              </button>
              {tenant && (
                <button onClick={() => load(true)} disabled={!!loading}
                  className="px-3 py-2 border border-gray-300 rounded text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                  Re-read everything from R1
                </button>
              )}
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1"><AlertCircle size={14} /> {error}</span>
              )}
            </div>
          </div>

          {tenant && (
            <>
              <Summary m={m} venue={venue} />

              <div className="flex flex-wrap items-center gap-3 mb-3">
                <div className="flex border border-gray-300 rounded overflow-hidden text-sm">
                  {([
                    ['wlans', `WLANs (${m.networks.length})`],
                    ['groups', `AP Groups${venue ? ` (${m.groups.length})` : ''}`],
                    ['aps', `APs${venue ? ` (${m.aps.length})` : ''}`],
                    ['matrix', 'Matrix'],
                  ] as [Tab, string][]).map(([t, label]) => (
                    <button key={t} onClick={() => setTab(t)}
                      disabled={t !== 'wlans' && !venue}
                      title={t !== 'wlans' && !venue ? 'Pick a venue and load it' : undefined}
                      className={`px-3 py-1.5 border-r last:border-r-0 border-gray-300 disabled:opacity-40 ${view === t
                        ? 'bg-indigo-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'}`}>
                      {label}
                    </button>
                  ))}
                </div>
                <div className="relative flex-1 min-w-[240px] max-w-md">
                  <Search size={14} className="absolute left-2.5 top-2.5 text-gray-400" />
                  <input value={search} onChange={e => setSearch(e.target.value)}
                    placeholder="Search names, SSIDs, groups, APs, serials..."
                    className="w-full pl-8 pr-8 py-1.5 border border-gray-300 rounded text-sm" />
                  {search && (
                    <button onClick={() => setSearch('')} className="absolute right-2 top-2 text-gray-400 hover:text-gray-700">
                      <X size={14} />
                    </button>
                  )}
                </div>
              </div>

              {view === 'wlans' && <WlansView m={m} venue={venue} q={q} jump={jump} />}
              {view === 'groups' && venue && <GroupsView m={m} venue={venue} q={q} jump={jump} />}
              {view === 'aps' && venue && <ApsView m={m} venue={venue} q={q} jump={jump} />}
              {view === 'matrix' && venue && <MatrixView m={m} venue={venue} q={q} jump={jump} />}
            </>
          )}
        </>
      )}
    </div>
  );
}

type ViewProps = { m: Model; venue: VenueData | null; q: string; jump: (t: Tab, q: string) => void };

// ── Summary ──────────────────────────────────────────────────────

function Summary({ m, venue }: { m: Model; venue: VenueData | null }) {
  const never = m.networks.filter(n => n.activations.length === 0).length;
  const tiles: [string, string | number, string?][] = [
    ['WLANs in tenant', m.networks.length],
    ['Not activated anywhere', never],
  ];
  if (venue) {
    const online = m.aps.filter(a => a.state === 'online').length;
    tiles.push(
      ['Activated in venue', m.acts.length, `${m.acts.filter(a => a.all_groups).length} on All AP Groups`],
      ['AP Groups', m.groups.length,
        `${m.groups.filter(g => (m.wlansByGroup.get(g.id)?.length || 0) > SSID_LIMIT).length} over ${SSID_LIMIT} WLANs`],
      ['APs', m.aps.length, `${online} online`],
    );
  }
  return (
    <div className="mb-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {tiles.map(([label, value, sub]) => (
          <div key={label} className="bg-white border border-gray-200 rounded-lg px-3 py-2">
            <div className="text-xs text-gray-500">{label}</div>
            <div className="text-xl font-semibold text-gray-800">{value}</div>
            {sub && <div className="text-xs text-gray-400">{sub}</div>}
          </div>
        ))}
      </div>
      {venue && (venue.settings_failed > 0 || venue.stray_aps > 0) && (
        <p className="text-xs text-amber-700 mt-2">
          {venue.settings_failed > 0 && `${plural(venue.settings_failed, 'WLAN')}' on/off state could not be read (shown as unknown). `}
          {venue.stray_aps > 0 && `${plural(venue.stray_aps, 'AP')} name a group this venue does not list.`}
        </p>
      )}
    </div>
  );
}

// ── WLANs ────────────────────────────────────────────────────────

type WlanScope = 'all' | 'here' | 'elsewhere' | 'never';

function WlansView({ m, venue, q, jump }: ViewProps) {
  const [scope, setScope] = useState<WlanScope>('all');
  const here = (n: Network) => m.actByNet.get(n.id);
  const inScope = (n: Network, s: WlanScope) =>
    s === 'all' ? true
      : s === 'never' ? n.activations.length === 0
      : s === 'here' ? !!here(n)
      : n.activations.length > 0 && !here(n);

  const matches = (n: Network) => !q || fold(n.name).includes(q) || fold(n.ssid).includes(q)
    || fold(n.description).includes(q)
    || n.activations.some(a => fold(m.venueName.get(a.venue_id)).includes(q))
    || (!!here(n) && m.groupsOfAct(here(n)!).some(gid => fold(m.groupById.get(gid)?.name).includes(q)));

  const searched = m.networks.filter(matches);
  const rows = searched.filter(n => inScope(n, scope));
  const count = (s: WlanScope) => searched.filter(n => inScope(n, s)).length;

  const venueCols: Col<Network>[] = venue ? [
    {
      key: 'here', label: 'In venue', title: 'How it is activated in the loaded venue',
      value: n => { const a = here(n); return !a ? '' : a.all_groups ? 'All AP Groups' : plural(a.group_ids.length, 'group'); },
      render: n => {
        const a = here(n);
        if (!a) return <span className="text-gray-300">—</span>;
        return a.all_groups ? <span className="text-gray-700">All AP Groups</span>
          : <Link onClick={() => jump('groups', n.name)}>{plural(a.group_ids.length, 'group')}</Link>;
      },
    },
    {
      key: 'state', label: 'State', value: n => here(n)?.state || '',
      render: n => { const a = here(n); return a ? <span className={`px-1.5 py-0.5 rounded border text-xs ${STATE_CLS[a.state]}`}>{a.state}</span> : null; },
    },
    {
      key: 'aps_here', label: 'APs (venue)', title: 'APs in the groups it is activated on', className: 'text-right',
      value: n => { const a = here(n); return a ? m.apsHere(a) : -1; },
      render: n => { const a = here(n); return a ? m.apsHere(a) : ''; },
    },
    {
      key: 'on_air', label: 'On air', title: 'APs in the venue whose radios report this SSID', className: 'text-right',
      value: n => m.onAirCount.get(n.id) ?? -1,
      render: n => {
        const a = here(n);
        if (!a) return '';
        const air = m.onAirCount.get(n.id) || 0, cfg = m.apsHere(a);
        return <span className={air < cfg && a.state === 'active' ? 'text-amber-700' : ''}>{air}</span>;
      },
    },
  ] : [];

  const cols: Col<Network>[] = [
    { key: 'name', label: 'Name', value: n => n.name,
      render: n => <span title={n.description || undefined} className="font-medium text-gray-800">{n.name}</span> },
    { key: 'ssid', label: 'SSID', value: n => n.ssid },
    { key: 'type', label: 'Type', value: n => n.type, className: 'text-xs text-gray-600' },
    { key: 'security', label: 'Security', value: n => n.security, className: 'text-xs text-gray-600' },
    { key: 'vlan', label: 'VLAN', value: n => n.vlan_pool ? `pool:${n.vlan_pool}` : (n.vlan ?? '') },
    { key: 'venues', label: 'Venues', title: 'Venues it is activated in', className: 'text-right',
      value: n => n.activations.length,
      render: n => n.activations.length || <span className="text-red-500 text-xs">none</span> },
    ...venueCols,
    { key: 'ap_count', label: 'APs (tenant)', className: 'text-right', value: n => n.ap_count },
    { key: 'clients', label: 'Clients', className: 'text-right', value: n => n.client_count },
  ];

  const expand = (n: Network) => {
    const a = here(n);
    return (
      <div className="text-xs space-y-2">
        <div><span className="text-gray-500">Id:</span> <code>{n.id}</code>
          {n.description && <> · <span className="text-gray-500">Description:</span> {n.description}</>}
          {n.captive && <> · <span className="text-gray-500">Captive:</span> {n.captive}</>}
        </div>
        {n.activations.length === 0
          ? <div className="text-red-600">Not activated in any venue.</div>
          : (
            <div>
              <div className="text-gray-500 mb-1">Venues:</div>
              <ul className="space-y-0.5">
                {[...n.activations].sort((x, y) => cmp(m.venueName.get(x.venue_id), m.venueName.get(y.venue_id))).map(v => (
                  <li key={v.venue_id}>
                    <span className={v.venue_id === venue?.venue_id ? 'font-semibold' : ''}>
                      {m.venueName.get(v.venue_id) || v.venue_id}
                    </span>
                    <span className="text-gray-500"> — {v.all_groups ? 'All AP Groups' : plural(v.group_ids.length, 'AP Group')}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        {a && !a.all_groups && (
          <div>
            <div className="text-gray-500 mb-1">AP Groups in {venue?.venue_name}:</div>
            {a.group_ids.map(gid => {
              const g = m.groupById.get(gid);
              return (
                <button key={gid} onClick={() => jump('groups', g?.name || gid)}
                  className="inline-block px-1.5 py-0.5 mr-1 mb-1 rounded border border-gray-200 bg-white hover:border-indigo-400">
                  {g?.name || gid} <span className="text-gray-400">({m.apsByGroup.get(gid)?.length || 0} APs)</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      <Chips value={scope} onChange={setScope} options={[
        { key: 'all', label: 'All', count: count('all') },
        ...(venue ? [
          { key: 'here' as WlanScope, label: `In ${venue.venue_name || 'venue'}`, count: count('here') },
          { key: 'elsewhere' as WlanScope, label: 'Only in other venues', count: count('elsewhere') },
        ] : []),
        { key: 'never', label: 'Not activated anywhere', count: count('never') },
      ]} />
      <DataTable rows={rows} cols={cols} rowKey={n => n.id} expand={expand}
        defaultSort={{ key: 'venues', desc: true }} csvName="wlan-wiz-wlans.csv" />
    </div>
  );
}

// ── AP Groups ────────────────────────────────────────────────────

type GroupScope = 'all' | 'with_aps' | 'empty' | 'over';

function GroupsView({ m, venue, q, jump }: ViewProps & { venue: VenueData }) {
  const [scope, setScope] = useState<GroupScope>('all');
  const wl = (g: Group) => m.wlansByGroup.get(g.id) || [];
  const aps = (g: Group) => m.apsByGroup.get(g.id) || [];
  const inScope = (g: Group, s: GroupScope) =>
    s === 'all' ? true : s === 'with_aps' ? aps(g).length > 0 : s === 'empty' ? aps(g).length === 0 : wl(g).length > SSID_LIMIT;

  const matches = (g: Group) => !q || fold(g.name).includes(q) || fold(g.description).includes(q)
    || wl(g).some(a => fold(a.name).includes(q) || fold(a.ssid).includes(q))
    || aps(g).some(ap => fold(ap.name).includes(q) || fold(ap.serial).includes(q));
  const searched = m.groups.filter(matches);
  const rows = searched.filter(g => inScope(g, scope));
  const count = (s: GroupScope) => searched.filter(g => inScope(g, s)).length;

  const cols: Col<Group>[] = [
    { key: 'name', label: 'AP Group', value: g => g.name,
      render: g => <span className="font-medium text-gray-800" title={g.description || undefined}>
        {g.name}{g.is_default && <span className="ml-1 text-xs text-gray-400">(R1 default)</span>}</span> },
    { key: 'aps', label: 'APs', className: 'text-right', value: g => aps(g).length,
      render: g => aps(g).length ? <Link onClick={() => jump('aps', g.name)}>{aps(g).length}</Link> : <span className="text-gray-300">0</span> },
    { key: 'online', label: 'Online', className: 'text-right', value: g => aps(g).filter(a => a.state === 'online').length },
    { key: 'clients', label: 'Clients', className: 'text-right', value: g => aps(g).reduce((n, a) => n + a.clients, 0) },
    { key: 'wlan_count', label: 'WLANs', className: 'text-right', title: `R1 allows ${SSID_LIMIT} per AP Group`,
      value: g => wl(g).length,
      render: g => <span className={wl(g).length > SSID_LIMIT ? 'text-red-600 font-semibold' : ''}>{wl(g).length}</span> },
    { key: 'wlans', label: 'WLANs on this group (dashed = via All AP Groups)',
      value: g => wl(g).map(a => a.name).join('; '),
      render: g => (
        <div className="max-w-3xl">
          {[...wl(g)].sort((a, b) => Number(a.all_groups) - Number(b.all_groups) || cmp(a.name, b.name)).map(a => (
            <WlanChip key={a.network_id} name={a.name} state={a.state} viaAll={a.all_groups}
              onClick={() => jump('wlans', a.name)} />
          ))}
        </div>
      ) },
  ];

  const expand = (g: Group) => {
    const here = new Set(wl(g).map(a => a.network_id));
    const key = groupKey(g.name);
    // Unit WLANs that look like this group's but are not on it -- often deactivated ones.
    const lookalikes = key.length >= 3 && !g.is_default
      ? m.networks.filter(n => !here.has(n.id) && (fold(n.name).includes(key) || fold(n.ssid).includes(key)))
      : [];
    return (
      <div className="text-xs space-y-2">
        {g.description && <div><span className="text-gray-500">Description:</span> {g.description}</div>}
        <div>
          <div className="text-gray-500 mb-1">APs:</div>
          {aps(g).length === 0 ? <span className="text-gray-400">none</span> : aps(g).map(ap => (
            <span key={ap.serial} className="inline-flex items-center gap-1 mr-3 mb-1">
              <ApState state={ap.state} status="" /> {ap.name} <span className="text-gray-400">{ap.serial}</span>
            </span>
          ))}
        </div>
        {key.length >= 3 && !g.is_default && (
          <div>
            <div className="text-gray-500 mb-1">Other WLANs whose name or SSID contains "{key}":</div>
            {lookalikes.length === 0 ? <span className="text-gray-400">none</span> : (
              <ul className="space-y-0.5">
                {lookalikes.map(n => {
                  const a = m.actByNet.get(n.id);
                  const where = a ? (a.all_groups ? 'activated here on All AP Groups'
                    : `activated here on ${a.group_ids.map(id => m.groupById.get(id)?.name || id).join(', ')}`)
                    : n.activations.length ? `activated in ${plural(n.activations.length, 'other venue')}`
                    : 'not activated anywhere';
                  return (
                    <li key={n.id}>
                      <Link onClick={() => jump('wlans', n.name)}>{n.name}</Link>
                      <span className="text-gray-500"> (SSID {n.ssid}) — </span>
                      <span className={n.activations.length ? 'text-gray-600' : 'text-red-600'}>{where}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      <Chips value={scope} onChange={setScope} options={[
        { key: 'all', label: 'All', count: count('all') },
        { key: 'with_aps', label: 'With APs', count: count('with_aps') },
        { key: 'empty', label: 'No APs', count: count('empty') },
        { key: 'over', label: `Over ${SSID_LIMIT} WLANs`, count: count('over') },
      ]} />
      <DataTable rows={rows} cols={cols} rowKey={g => g.id} expand={expand}
        defaultSort={{ key: 'name', desc: false }} csvName={`wlan-wiz-groups-${venue.venue_name}.csv`} />
    </div>
  );
}

// ── APs ──────────────────────────────────────────────────────────

type ApScope = 'all' | 'online' | 'offline' | 'mismatch' | 'default';

function ApsView({ m, venue, q, jump }: ViewProps & { venue: VenueData }) {
  const [scope, setScope] = useState<ApScope>('all');

  const info = useMemo(() => new Map(m.aps.map(ap => {
    const g = ap.group_id ? m.groupById.get(ap.group_id) : undefined;
    const wl = ap.group_id ? m.wlansByGroup.get(ap.group_id) || [] : [];
    const air = new Set(ap.ssids_on_air.map(fold));
    const expected = wl.filter(a => a.state === 'active');
    const missing = ap.state === 'online'
      ? expected.filter(a => !air.has(fold(a.ssid)) && !air.has(fold(a.name))) : [];
    const known = new Set(wl.flatMap(a => [fold(a.ssid), fold(a.name)]));
    const extra = ap.ssids_on_air.filter(s => !known.has(fold(s)));
    return [ap.serial, { group: g, wl, missing, extra }];
  })), [m]);
  const get = (ap: Ap) => info.get(ap.serial)!;

  const inScope = (ap: Ap, s: ApScope) =>
    s === 'all' ? true : s === 'online' ? ap.state === 'online' : s === 'offline' ? ap.state !== 'online'
      : s === 'default' ? !!get(ap).group?.is_default
      : get(ap).missing.length > 0 || get(ap).extra.length > 0;
  const matches = (ap: Ap) => !q || [ap.name, ap.serial, ap.mac, ap.ip, ap.model, get(ap).group?.name, ...ap.tags]
    .some(v => fold(v).includes(q)) || get(ap).wl.some(a => fold(a.name).includes(q) || fold(a.ssid).includes(q));
  const searched = m.aps.filter(matches);
  const rows = searched.filter(ap => inScope(ap, scope));
  const count = (s: ApScope) => searched.filter(ap => inScope(ap, s)).length;

  const cols: Col<Ap>[] = [
    { key: 'name', label: 'AP', value: ap => ap.name, render: ap => <span className="font-medium text-gray-800">{ap.name}</span> },
    { key: 'serial', label: 'Serial', value: ap => ap.serial, className: 'text-xs text-gray-600' },
    { key: 'model', label: 'Model', value: ap => ap.model, className: 'text-xs' },
    { key: 'status', label: 'Status', value: ap => ap.status, render: ap => <ApState state={ap.state} status={ap.status} /> },
    { key: 'group', label: 'AP Group', value: ap => get(ap).group?.name || '',
      render: ap => get(ap).group
        ? <Link onClick={() => jump('groups', get(ap).group!.name)}>{get(ap).group!.name}</Link>
        : <span className="text-amber-600">unknown</span> },
    { key: 'ip', label: 'IP', value: ap => ap.ip, className: 'text-xs' },
    { key: 'clients', label: 'Clients', className: 'text-right', value: ap => ap.clients },
    { key: 'wlans', label: 'WLANs', className: 'text-right', title: 'WLANs its group carries', value: ap => get(ap).wl.length },
    { key: 'on_air', label: 'On air', title: 'SSIDs its radios report', value: ap => ap.ssids_on_air.join('; '),
      render: ap => <span className="text-xs text-gray-700">{ap.ssids_on_air.join(', ')}</span> },
    { key: 'diff', label: 'Config vs air', title: 'Active WLANs not on air (−) / SSIDs on air its group does not carry (+)',
      value: ap => get(ap).missing.length + get(ap).extra.length,
      render: ap => {
        const { missing, extra } = get(ap);
        if (!missing.length && !extra.length) return null;
        return (
          <span className="text-xs" title={[
            missing.length ? `Not on air: ${missing.map(a => a.name).join(', ')}` : '',
            extra.length ? `Not configured: ${extra.join(', ')}` : '',
          ].filter(Boolean).join('\n')}>
            {missing.length > 0 && <span className="text-amber-700 mr-1">−{missing.length}</span>}
            {extra.length > 0 && <span className="text-purple-700">+{extra.length}</span>}
          </span>
        );
      } },
  ];

  const expand = (ap: Ap) => {
    const { wl, missing, extra } = get(ap);
    return (
      <div className="text-xs space-y-2">
        <div className="text-gray-600">
          MAC {ap.mac || '—'} · Firmware {ap.firmware || '—'}{ap.tags.length > 0 && <> · Tags {ap.tags.join(', ')}</>}
        </div>
        <div>
          <div className="text-gray-500 mb-1">WLANs its group carries:</div>
          {wl.length === 0 ? <span className="text-gray-400">none</span>
            : wl.map(a => <WlanChip key={a.network_id} name={a.name} state={a.state} viaAll={a.all_groups}
                onClick={() => jump('wlans', a.name)} />)}
        </div>
        {ap.radios.length > 0 && (
          <div>
            <div className="text-gray-500 mb-1">Radios:</div>
            {ap.radios.map((r, i) => (
              <div key={i}>{r.band} ch {r.channel ?? '—'}: {r.ssids.join(', ') || <span className="text-gray-400">no SSIDs</span>}</div>
            ))}
          </div>
        )}
        {missing.length > 0 && <div className="text-amber-700">Active on its group but not on air: {missing.map(a => a.name).join(', ')}</div>}
        {extra.length > 0 && <div className="text-purple-700">On air but not on its group: {extra.join(', ')}</div>}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      <Chips value={scope} onChange={setScope} options={[
        { key: 'all', label: 'All', count: count('all') },
        { key: 'online', label: 'Online', count: count('online') },
        { key: 'offline', label: 'Not online', count: count('offline') },
        { key: 'default', label: 'In default group', count: count('default') },
        { key: 'mismatch', label: 'Config ≠ air', count: count('mismatch') },
      ]} />
      <DataTable rows={rows} cols={cols} rowKey={ap => ap.serial} expand={expand}
        defaultSort={{ key: 'name', desc: false }} csvName={`wlan-wiz-aps-${venue.venue_name}.csv`} />
    </div>
  );
}

// ── Matrix: AP Groups × WLANs ────────────────────────────────────

function MatrixView({ m, venue, q, jump }: ViewProps & { venue: VenueData }) {
  const [hideAll, setHideAll] = useState(true);
  const [hideEmpty, setHideEmpty] = useState(false);

  const { rows, cols } = useMemo(() => {
    const acts = m.acts.filter(a => !(hideAll && a.all_groups));
    const hit = (a: VenueAct) => fold(a.name).includes(q) || fold(a.ssid).includes(q);
    // A row shows when its name matches or it carries a matching WLAN; the
    // columns are the WLANs on the rows that show.
    const rows = m.groups.filter(g => (!hideEmpty || (m.apsByGroup.get(g.id)?.length || 0) > 0)
      && (!q || fold(g.name).includes(q) || (m.wlansByGroup.get(g.id) || []).some(hit)));
    const rowIds = new Set(rows.map(g => g.id));
    const cols = acts.filter(a => m.groupsOfAct(a).some(gid => rowIds.has(gid)))
      .sort((a, b) => m.groupsOfAct(b).length - m.groupsOfAct(a).length || cmp(a.name, b.name));
    rows.sort((a, b) => cmp(a.name, b.name));
    return { rows, cols };
  }, [m, q, hideAll, hideEmpty]);

  const hiddenAll = m.acts.filter(a => a.all_groups).length;
  const cells = rows.length * cols.length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={hideAll} onChange={e => setHideAll(e.target.checked)} />
          Hide WLANs on All AP Groups ({hiddenAll})
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={hideEmpty} onChange={e => setHideEmpty(e.target.checked)} />
          Hide groups with no APs
        </label>
        <span>{plural(rows.length, 'group')} × {plural(cols.length, 'WLAN')}</span>
        <span className="flex items-center gap-2">
          <span className="text-green-600">●</span> active
          <span className="text-indigo-500">●</span> scheduled
          <span className="text-gray-300">●</span> off
          <span className="text-green-600">○</span> via All AP Groups
        </span>
        <button className="flex items-center gap-1 hover:text-gray-900" onClick={() => downloadCsv(
          `wlan-wiz-matrix-${venue.venue_name}.csv`,
          ['AP Group', 'APs', ...cols.map(c => c.name)],
          rows.map(g => [g.name, m.apsByGroup.get(g.id)?.length || 0,
            ...cols.map(c => m.groupsOfAct(c).includes(g.id) ? (c.all_groups ? 'all' : c.state) : '')]),
        )}><Download size={12} /> CSV</button>
      </div>
      {cells > 60000 ? (
        <p className="text-sm text-amber-700">{cells.toLocaleString()} cells is too many to draw — narrow it with the search box.</p>
      ) : rows.length === 0 || cols.length === 0 ? (
        <p className="text-sm text-gray-500">Nothing to show.</p>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-auto max-h-[75vh]">
          <table className="text-xs border-collapse">
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-20 bg-gray-50 px-2 py-1 text-left align-bottom border-b border-r border-gray-200">AP Group</th>
                <th className="sticky top-0 z-10 bg-gray-50 px-1 py-1 align-bottom border-b border-r border-gray-200">APs</th>
                {cols.map(c => (
                  <th key={c.network_id} title={`${c.name} (SSID ${c.ssid}) — ${c.state}`}
                    className="sticky top-0 z-10 bg-gray-50 px-0.5 py-1 align-bottom border-b border-gray-200 font-normal">
                    <button onClick={() => jump('wlans', c.name)}
                      className={`[writing-mode:vertical-rl] rotate-180 max-h-40 truncate hover:text-indigo-700 ${c.state === 'off' ? 'text-gray-400 line-through' : 'text-gray-700'}`}>
                      {c.name}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(g => {
                const set = new Set((m.wlansByGroup.get(g.id) || []).map(a => a.network_id));
                const n = m.wlansByGroup.get(g.id)?.length || 0;
                return (
                  <tr key={g.id} className="hover:bg-indigo-50/40">
                    <td className="sticky left-0 bg-white px-2 py-0.5 border-r border-gray-200 whitespace-nowrap">
                      <Link onClick={() => jump('groups', g.name)}>{g.name}</Link>
                      {n > SSID_LIMIT && <span className="ml-1 text-red-600" title={`${n} WLANs`}>({n})</span>}
                    </td>
                    <td className="px-1 text-right text-gray-500 border-r border-gray-200">{m.apsByGroup.get(g.id)?.length || 0}</td>
                    {cols.map(c => {
                      const on = set.has(c.network_id);
                      const color = c.state === 'off' ? 'text-gray-300' : c.state === 'scheduled' ? 'text-indigo-500'
                        : c.state === 'unknown' ? 'text-amber-500' : 'text-green-600';
                      return (
                        <td key={c.network_id} className="text-center px-0.5 border-l border-gray-50">
                          {on && <span className={color}>{c.all_groups ? '○' : '●'}</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
