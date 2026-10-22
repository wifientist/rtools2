import { useState, useMemo, Fragment } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Target, ArrowRight, Loader2, Boxes, RefreshCw, Trash2, PenLine,
  ChevronDown, ChevronRight, Upload, Search, Download, MoveRight,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/apg_cleanup.py) ──────────────────────────

type GroupStatus = 'default' | 'named_default' | 'in_use' | 'awaiting_ap' | 'mapped' | 'unused';
type WlanState = 'active' | 'off' | 'scheduled' | 'unknown';

interface Wlan { id: string; name: string; ssid: string; via: 'explicit' | 'all_groups'; state: WlanState }
interface Ap { serial: string; name: string; status: string | null; model: string | null }

interface HeldAp extends Ap {
  group_id: string;
  group_name: string;
  match: 'one' | 'ambiguous' | 'none';
  suggested_group_id: string | null;
  suggested_group_name: string | null;
  suggested_group_aps: number;
  candidates: string[];
}

interface Group {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  status: GroupStatus;
  aps: Ap[];
  ap_count: number;
  online_count: number;
  client_count: number;
  wlans: Wlan[];
  explicit_wlan_count: number;
  notes: string[];
}

interface Inventory {
  venue_name: string;
  groups: Group[];
  counts: Record<GroupStatus, number>;
  summary: {
    groups: number; aps: number; networks: number; all_group_networks: number; networks_off: number;
    over_ssid_limit: number; default_aps: number; default_aps_matched: number;
  };
  default_aps: HeldAp[];
  stray_aps: Ap[];
}

interface RenameRow { id: string; old_name: string | null; new_name: string; status: string; error: string | null }
interface MoveRow {
  ap: string; serial: string | null; ap_name: string | null; from_group: string | null;
  to_group_id: string | null; to_group_name: string | null; create: boolean;
  status: 'ok' | 'already' | 'blocked' | 'moved' | 'unconfirmed' | 'failed'; error: string | null;
}
interface MoveRequestItem { ap: string; group_id?: string; group_name?: string }
interface MovePlan { moves: MoveRequestItem[]; allowCreate: boolean; rows: MoveRow[]; groups_to_create: string[] }
interface DeleteResult { id: string; name: string; status: 'deleted' | 'refused' | 'failed'; error?: string; dropped_wlans?: number }

interface Outcome {
  kind: 'delete' | 'rename' | 'move';
  before: Inventory;
  after: Inventory | null;
  results: (DeleteResult | RenameRow | MoveRow)[];
  counts: Record<string, number>;
}

const STATUS: Record<GroupStatus, { label: string; cls: string }> = {
  default: { label: 'Default', cls: 'text-gray-600 bg-gray-50 border-gray-200' },
  named_default: { label: 'Named "default"', cls: 'text-purple-700 bg-purple-50 border-purple-200' },
  in_use:  { label: 'In use',  cls: 'text-green-700 bg-green-50 border-green-200' },
  awaiting_ap: { label: 'Awaiting AP', cls: 'text-sky-700 bg-sky-50 border-sky-200' },
  mapped:  { label: 'Empty, mapped', cls: 'text-amber-700 bg-amber-50 border-amber-200' },
  unused:  { label: 'Unused',  cls: 'text-red-700 bg-red-50 border-red-200' },
};

const WLAN_STATE: Record<WlanState, string> = {
  active: 'text-green-700', off: 'text-gray-400 line-through', scheduled: 'text-indigo-600', unknown: 'text-amber-600',
};

const deletable = (g: Group) =>
  g.status === 'mapped' || g.status === 'unused' || (g.status === 'named_default' && g.ap_count === 0);
const renamable = (g: Group) => !g.is_default;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** old_name,new_name per line; a header line naming the columns is skipped. */
function parseCsv(text: string): { old: string; new: string }[] {
  return text.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
    .map(l => {
      const [a, ...rest] = l.split(',');
      return { old: (a || '').trim().replace(/^"|"$/g, ''), new: rest.join(',').trim().replace(/^"|"$/g, '') };
    })
    .filter((r, i) => !(i === 0 && /old/i.test(r.old) && /new/i.test(r.new)));
}

// ── Page ─────────────────────────────────────────────────────────

export default function ApgCleanup() {
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
  const ready = !!r1Id && !!venueId && (!isMSP || !!ecId);
  const venueBody = { controller_id: r1Id, venue_id: venueId, tenant_id: tenantId };

  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inv, setInv] = useState<Inventory | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<GroupStatus | 'all'>('all');
  const [search, setSearch] = useState('');
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Rename
  const [renameOpen, setRenameOpen] = useState(false);
  const [mode, setMode] = useState<'pattern' | 'csv'>('pattern');
  const [find, setFind] = useState('');
  const [replace, setReplace] = useState('');
  const [useRegex, setUseRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [checked, setChecked] = useState<RenameRow[] | null>(null);
  const [confirmRename, setConfirmRename] = useState(false);

  // Move
  const [movePlan, setMovePlan] = useState<MovePlan | null>(null);
  const [moveCsvOpen, setMoveCsvOpen] = useState(false);
  const [moveCsv, setMoveCsv] = useState('');
  const [allowCreate, setAllowCreate] = useState(true);

  const reset = () => {
    setInv(null); setSelected(new Set()); setExpanded(new Set()); setOutcome(null); setChecked(null);
  };

  const readVenue = () => apiPost<Inventory>(`${API_URL}/apg-cleanup/inventory`, venueBody);

  const load = async () => {
    if (!ready) return;
    setLoading(true); setError(null); setOutcome(null); setChecked(null);
    try {
      const data = await readVenue();
      setInv(data);
      // Preselect the obvious cleanup candidates only.
      setSelected(new Set(data.groups.filter(g => g.status === 'unused'
        || (g.status === 'named_default' && g.ap_count === 0)).map(g => g.id)));
    } catch (e: any) {
      setError(e.message || 'Could not read the venue');
    } finally {
      setLoading(false);
    }
  };

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (inv?.groups || []).filter(g =>
      (filter === 'all' || g.status === filter) && (!q || g.name.toLowerCase().includes(q)));
  }, [inv, filter, search]);

  const selGroups = (inv?.groups || []).filter(g => selected.has(g.id));
  const toDelete = selGroups.filter(deletable);

  // ── Rename proposals (computed here; the server re-checks them against R1) ──
  const proposals = useMemo(() => {
    if (!inv) return { rows: [] as { id: string; old: string; new: string }[], unmatched: [] as string[], error: null as string | null };
    if (mode === 'pattern') {
      if (!find) return { rows: [], unmatched: [], error: null };
      let re: RegExp;
      try {
        re = new RegExp(useRegex ? find : escapeRe(find), caseSensitive ? 'g' : 'gi');
      } catch (e: any) {
        return { rows: [], unmatched: [], error: `Bad pattern: ${e.message}` };
      }
      // Selected groups if any are selected, else every group the pattern touches.
      const pool = (selGroups.length ? selGroups : inv.groups).filter(renamable);
      const rows = pool
        .map(g => ({ id: g.id, old: g.name, new: g.name.replace(re, replace) }))
        .filter(r => r.new !== r.old);
      return { rows, unmatched: [], error: null };
    }
    const byName = new Map(inv.groups.map(g => [g.name, g]));
    const byFolded = new Map(inv.groups.map(g => [g.name.toLowerCase(), g]));
    const rows: { id: string; old: string; new: string }[] = [];
    const unmatched: string[] = [];
    for (const line of parseCsv(csvText)) {
      const g = byName.get(line.old) || byFolded.get(line.old.toLowerCase());
      if (!g || !line.new) unmatched.push(line.old || '(blank)');
      else rows.push({ id: g.id, old: g.name, new: line.new });
    }
    return { rows, unmatched, error: null };
  }, [inv, mode, find, replace, useRegex, caseSensitive, csvText, selGroups]);

  const checkRenames = async () => {
    setBusy(true); setError(null);
    try {
      const data = await apiPost<{ rows: RenameRow[] }>(`${API_URL}/apg-cleanup/rename`, {
        ...venueBody, dry_run: true,
        renames: proposals.rows.map(r => ({ id: r.id, new_name: r.new })),
      });
      setChecked(data.rows);
    } catch (e: any) {
      setError(e.message || 'Rename check failed');
    } finally {
      setBusy(false);
    }
  };

  const okRenames = (checked || []).filter(r => r.status === 'ok');

  /** Check moves against R1 (dry run), then confirm in a modal. */
  const startMove = async (moves: MoveRequestItem[], create: boolean) => {
    if (!moves.length) return;
    setBusy(true); setError(null);
    try {
      const data = await apiPost<{ rows: MoveRow[]; groups_to_create: string[] }>(`${API_URL}/apg-cleanup/move`, {
        ...venueBody, dry_run: true, allow_create: create, moves,
      });
      setMovePlan({ moves, allowCreate: create, rows: data.rows, groups_to_create: data.groups_to_create });
    } catch (e: any) {
      setError(e.message || 'Move check failed');
    } finally {
      setBusy(false);
    }
  };

  const moveCsvRows = useMemo(() => parseCsv(moveCsv)
    .filter((r, i) => !(i === 0 && /ap_identifier|unit/i.test(r.old)))
    .filter(r => r.old && r.new), [moveCsv]);

  /** Write, then poll R1 again and keep both sides for the before/after summary. */
  const runWrite = async (kind: 'delete' | 'rename' | 'move') => {
    if (!inv) return;
    const plan = movePlan;
    setConfirmDelete(false); setConfirmRename(false); setMovePlan(null);
    setBusy(true); setError(null);
    const before = inv;
    try {
      const data = kind === 'delete'
        ? await apiPost<{ results: DeleteResult[]; counts: Record<string, number> }>(
            `${API_URL}/apg-cleanup/delete`, { ...venueBody, group_ids: toDelete.map(g => g.id) })
        : kind === 'move'
        ? await apiPost<{ results: MoveRow[]; counts: Record<string, number> }>(
            `${API_URL}/apg-cleanup/move`, {
              ...venueBody, allow_create: plan?.allowCreate ?? false,
              // Rows come back in request order; send only the ones that checked out.
              moves: (plan?.moves || []).filter((_, i) => plan?.rows[i]?.status === 'ok'),
            })
        : await apiPost<{ results: RenameRow[]; counts: Record<string, number> }>(
            `${API_URL}/apg-cleanup/rename`,
            { ...venueBody, renames: okRenames.map(r => ({ id: r.id, new_name: r.new_name })) });
      let after: Inventory | null = null;
      try {
        after = await readVenue();
        setInv(after);
      } catch (e: any) {
        setError(`Changes sent, but re-reading the venue failed: ${e.message}`);
      }
      setOutcome({ kind, before, after, results: data.results, counts: data.counts });
      setSelected(new Set());
      setChecked(null);
      if (kind === 'rename') { setCsvText(''); setFind(''); setReplace(''); }
      if (kind === 'move') setMoveCsv('');
    } catch (e: any) {
      setError(e.message || `${kind} failed`);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    setter(next);
  };

  const visSelectable = visible.filter(renamable);
  const allVisSelected = visSelectable.length > 0 && visSelectable.every(g => selected.has(g.id));

  const loadCsvFile = (f: File | undefined) => {
    if (!f) return;
    f.text().then(t => { setCsvText(t); setChecked(null); });
  };

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Boxes size={24} className="text-indigo-600" />
          APG Cleanup
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          A venue's AP Groups: their APs, the WLANs mapped to them, and renaming or deleting the ones you no longer need
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
                  onEcSelect={(id) => { setEcId(id); setVenueId(null); reset(); }}
                  selectedEcId={ecId}
                />
              )}
              {(!isMSP || ecId) && (
                <SingleVenueSelector
                  controllerId={r1Id}
                  tenantId={tenantId}
                  onVenueSelect={(id) => { setVenueId(id); reset(); }}
                  selectedVenueId={venueId}
                />
              )}
            </div>
            <div className="mt-4 flex items-center gap-3 pt-3 border-t border-gray-100">
              <button
                onClick={load}
                disabled={!ready || loading || busy}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading ? 'Reading the venue...' : inv ? 'Re-read from R1' : 'Load AP Groups'}
              </button>
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </span>
              )}
            </div>
          </div>

          {outcome && <OutcomePanel outcome={outcome} onClose={() => setOutcome(null)} />}

          {inv && (
            <>
              <SummaryTiles inv={inv} />

              {inv.default_aps.length > 0 && (
                <HeldApsPanel key={inv.default_aps.map(a => a.serial).join()} inv={inv} busy={busy} onMove={moves => startMove(moves, true)} />
              )}

              {/* ── Move APs by CSV (what AP Regroup did) ── */}
              <div className="bg-white border border-gray-200 rounded-lg mb-4">
                <button onClick={() => setMoveCsvOpen(o => !o)}
                  className="w-full flex items-center gap-2 p-3 text-sm font-semibold text-gray-700">
                  {moveCsvOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  <MoveRight size={14} className="text-indigo-600" /> Move APs by CSV
                  <span className="text-xs font-normal text-gray-400">ap_identifier,ap_group_name — the AP Regroup format</span>
                </button>
                {moveCsvOpen && (
                  <div className="p-3 pt-0 space-y-2">
                    <textarea value={moveCsv} onChange={e => setMoveCsv(e.target.value)} rows={5}
                      placeholder={'ap_identifier,ap_group_name\nR350-ABC123,Building-1\n1222 NEW,1222@Site'}
                      className="w-full border border-gray-300 rounded px-2 py-1 font-mono text-sm" />
                    <div className="flex flex-wrap items-center gap-3 text-sm">
                      <label className="inline-flex items-center gap-1 text-indigo-700 cursor-pointer">
                        <Upload size={14} /> Load a .csv file
                        <input type="file" accept=".csv,text/csv,text/plain" className="hidden"
                          onChange={e => e.target.files?.[0]?.text().then(setMoveCsv)} />
                      </label>
                      <label className="flex items-center gap-1 text-gray-600">
                        <input type="checkbox" checked={allowCreate} onChange={e => setAllowCreate(e.target.checked)} />
                        Create AP Groups that do not exist
                      </label>
                      <span className="text-xs text-gray-500">AP by serial or exact name.</span>
                      <button
                        onClick={() => startMove(moveCsvRows.map(r => ({ ap: r.old, group_name: r.new })), allowCreate)}
                        disabled={!moveCsvRows.length || busy}
                        className="ml-auto px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 font-medium"
                      >
                        Check {plural(moveCsvRows.length, 'move')}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* ── Rename panel ── */}
              <div className="bg-white border border-gray-200 rounded-lg mb-4">
                <button
                  onClick={() => setRenameOpen(o => !o)}
                  className="w-full flex items-center gap-2 p-3 text-sm font-semibold text-gray-700"
                >
                  {renameOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  <PenLine size={14} className="text-indigo-600" /> Rename AP Groups
                  <span className="text-xs font-normal text-gray-400">by pattern or CSV</span>
                </button>
                {renameOpen && (
                  <div className="p-3 pt-0 space-y-3">
                    <div className="flex gap-1 text-sm">
                      {(['pattern', 'csv'] as const).map(m => (
                        <button key={m} onClick={() => { setMode(m); setChecked(null); }}
                          className={`px-3 py-1 rounded border ${mode === m ? 'bg-indigo-50 border-indigo-300 text-indigo-700' : 'border-gray-200 text-gray-600'}`}>
                          {m === 'pattern' ? 'Find & replace' : 'CSV list'}
                        </button>
                      ))}
                    </div>

                    {mode === 'pattern' ? (
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                          <input value={find} onChange={e => { setFind(e.target.value); setChecked(null); }}
                            placeholder={useRegex ? 'Regex, e.g. ^Bldg-(\\d+)' : 'Find'}
                            className="border border-gray-300 rounded px-2 py-1 font-mono w-64" />
                          <ArrowRight size={14} className="text-gray-400" />
                          <input value={replace} onChange={e => { setReplace(e.target.value); setChecked(null); }}
                            placeholder={useRegex ? 'Replace, e.g. Building-$1' : 'Replace with'}
                            className="border border-gray-300 rounded px-2 py-1 font-mono w-64" />
                          <label className="flex items-center gap-1 text-gray-600">
                            <input type="checkbox" checked={useRegex} onChange={e => { setUseRegex(e.target.checked); setChecked(null); }} /> Regex
                          </label>
                          <label className="flex items-center gap-1 text-gray-600">
                            <input type="checkbox" checked={caseSensitive} onChange={e => { setCaseSensitive(e.target.checked); setChecked(null); }} /> Match case
                          </label>
                        </div>
                        <p className="text-xs text-gray-500">
                          Applies to the {selGroups.length ? plural(selGroups.filter(renamable).length, 'selected group') : 'all groups'}.
                          {useRegex && ' Use $1, $2 for capture groups; ^ and $ anchor the whole name.'}
                        </p>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <textarea value={csvText} onChange={e => { setCsvText(e.target.value); setChecked(null); }}
                          rows={5} placeholder={'old_name,new_name\nBldg-1,Building-01\nBldg-2,Building-02'}
                          className="w-full border border-gray-300 rounded px-2 py-1 font-mono text-sm" />
                        <label className="inline-flex items-center gap-1 text-sm text-indigo-700 cursor-pointer">
                          <Upload size={14} /> Load a .csv file
                          <input type="file" accept=".csv,text/csv,text/plain" className="hidden"
                            onChange={e => loadCsvFile(e.target.files?.[0])} />
                        </label>
                        {proposals.unmatched.length > 0 && (
                          <p className="text-xs text-amber-700">
                            Not found in this venue (or no new name): {proposals.unmatched.join(', ')}
                          </p>
                        )}
                      </div>
                    )}

                    {proposals.error && <p className="text-xs text-red-600">{proposals.error}</p>}

                    <RenameTable proposals={proposals.rows} checked={checked} />

                    <div className="flex items-center gap-2">
                      <button
                        onClick={checkRenames}
                        disabled={!proposals.rows.length || busy}
                        className="px-4 py-1.5 text-sm rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 flex items-center gap-2"
                      >
                        {busy && !checked ? <Loader2 size={14} className="animate-spin" /> : null}
                        Check {plural(proposals.rows.length, 'rename')} against R1
                      </button>
                      <button
                        onClick={() => setConfirmRename(true)}
                        disabled={!okRenames.length || busy}
                        className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 font-medium"
                      >
                        Rename {plural(okRenames.length, 'group')}
                      </button>
                      {checked && checked.length > okRenames.length && (
                        <span className="text-xs text-gray-500">
                          {checked.length - okRenames.length} not applied (see table)
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* ── Groups table ── */}
              <div className="bg-white border border-gray-200 rounded-lg">
                <div className="flex flex-wrap items-center gap-2 p-3 border-b border-gray-100">
                  <span className="text-sm font-medium text-gray-700 mr-1">{inv.venue_name}</span>
                  {(['all', ...Object.keys(STATUS)] as (GroupStatus | 'all')[]).map(s => (
                    <button key={s} onClick={() => setFilter(s)}
                      className={`text-xs px-2 py-0.5 rounded border ${s === 'all' ? 'border-gray-200 text-gray-600' : STATUS[s].cls} ${filter === s ? 'ring-2 ring-indigo-300' : ''}`}>
                      {s === 'all' ? `All: ${inv.groups.length}` : `${STATUS[s].label}: ${inv.counts[s] ?? 0}`}
                    </button>
                  ))}
                  <div className="relative">
                    <Search size={12} className="absolute left-2 top-1.5 text-gray-400" />
                    <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter by name"
                      className="border border-gray-200 rounded pl-6 pr-2 py-0.5 text-xs w-40" />
                  </div>
                  <button
                    onClick={() => setConfirmDelete(true)}
                    disabled={!toDelete.length || busy || loading}
                    className="ml-auto px-4 py-1.5 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                  >
                    {busy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    Delete {plural(toDelete.length, 'group')}
                  </button>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                      <tr>
                        <th className="p-2 w-8">
                          <input type="checkbox" checked={allVisSelected} disabled={!visSelectable.length}
                            onChange={() => {
                              const next = new Set(selected);
                              visSelectable.forEach(g => allVisSelected ? next.delete(g.id) : next.add(g.id));
                              setSelected(next);
                            }} />
                        </th>
                        <th className="p-2 w-6" />
                        <th className="p-2 text-left">AP Group</th>
                        <th className="p-2 text-left">Status</th>
                        <th className="p-2 text-right">APs</th>
                        <th className="p-2 text-right">Clients</th>
                        <th className="p-2 text-left">WLANs</th>
                        <th className="p-2 text-left">Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map(g => {
                        const open = expanded.has(g.id);
                        const explicit = g.wlans.filter(w => w.via === 'explicit');
                        const allg = g.wlans.length - explicit.length;
                        return (
                          <Fragment key={g.id}>
                            <tr className="border-t border-gray-100 align-top">
                              <td className="p-2">
                                <input type="checkbox" disabled={!renamable(g)}
                                  checked={selected.has(g.id)} onChange={() => toggle(selected, setSelected, g.id)} />
                              </td>
                              <td className="p-2">
                                <button onClick={() => toggle(expanded, setExpanded, g.id)} className="text-gray-400 hover:text-gray-700">
                                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                </button>
                              </td>
                              <td className="p-2">
                                <div className="font-medium text-gray-800">{g.name}</div>
                                {g.description && <div className="text-xs text-gray-500">{g.description}</div>}
                              </td>
                              <td className="p-2">
                                <span className={`text-xs px-2 py-0.5 rounded border whitespace-nowrap ${STATUS[g.status].cls}`}>
                                  {STATUS[g.status].label}
                                </span>
                              </td>
                              <td className="p-2 text-right font-mono whitespace-nowrap">
                                {g.ap_count}
                                {g.ap_count > 0 && <span className="text-xs text-gray-400"> ({g.online_count} up)</span>}
                              </td>
                              <td className="p-2 text-right font-mono">{g.client_count}</td>
                              <td className="p-2 text-xs">
                                {explicit.length === 0 && allg === 0 && <span className="text-gray-400">none</span>}
                                {explicit.map(w => (
                                  <span key={w.id} className={`mr-2 ${WLAN_STATE[w.state]}`} title={`${w.ssid} — ${w.state}`}>{w.name}</span>
                                ))}
                                {allg > 0 && <span className="text-gray-400">+{allg} venue-wide</span>}
                              </td>
                              <td className="p-2 text-xs text-gray-600">
                                {g.notes.map((n, i) => <div key={i}>{n}</div>)}
                              </td>
                            </tr>
                            {open && <GroupDetail g={g} />}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {inv.stray_aps.length > 0 && (
                  <div className="p-3 border-t border-gray-100 text-xs text-amber-700">
                    {plural(inv.stray_aps.length, 'AP')} point at an AP Group R1 did not list:{' '}
                    {inv.stray_aps.map(a => a.name).join(', ')}
                  </div>
                )}
              </div>
            </>
          )}

          {confirmDelete && (
            <Modal
              title={`Delete ${plural(toDelete.length, 'AP Group')}`}
              confirmLabel={`Delete ${toDelete.length}`}
              danger
              onCancel={() => setConfirmDelete(false)}
              onConfirm={() => runWrite('delete')}
            >
              <table className="w-full text-sm">
                <tbody>
                  {toDelete.map(g => (
                    <tr key={g.id} className="border-t border-gray-100 first:border-t-0">
                      <td className="py-1.5 text-gray-800">{g.name}</td>
                      <td className="py-1.5 text-right text-xs text-gray-500">
                        {g.explicit_wlan_count ? `drops ${plural(g.explicit_wlan_count, 'WLAN mapping')}` : 'no mappings'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {selGroups.length > toDelete.length && (
                <p className="mt-2 text-xs text-gray-500">
                  {selGroups.length - toDelete.length} selected group(s) still hold APs (or are the default) and are left alone.
                </p>
              )}
              {toDelete.some(g => g.notes.some(n => n.startsWith('Only AP Group'))) && (
                <p className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                  Some of these are the only AP Group a network is activated on in this venue. See the notes column.
                </p>
              )}
              <p className="mt-2 text-xs text-gray-500">R1 is re-read first; any group that has gained APs since is refused.</p>
            </Modal>
          )}

          {movePlan && (
            <MoveModal plan={movePlan} onCancel={() => setMovePlan(null)} onConfirm={() => runWrite('move')} />
          )}

          {confirmRename && (
            <Modal
              title={`Rename ${plural(okRenames.length, 'AP Group')}`}
              confirmLabel={`Rename ${okRenames.length}`}
              onCancel={() => setConfirmRename(false)}
              onConfirm={() => runWrite('rename')}
            >
              <table className="w-full text-sm">
                <tbody>
                  {okRenames.map(r => (
                    <tr key={r.id} className="border-t border-gray-100 first:border-t-0">
                      <td className="py-1.5 text-gray-500">{r.old_name}</td>
                      <td className="py-1.5 px-2"><ArrowRight size={12} className="text-gray-400" /></td>
                      <td className="py-1.5 text-indigo-700 font-medium">{r.new_name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-gray-500">
                Each group keeps its APs; the tool reads every group back to confirm the name took and the membership did not move.
              </p>
            </Modal>
          )}
        </>
      )}
    </div>
  );
}

// ── Pieces ───────────────────────────────────────────────────────

function SummaryTiles({ inv }: { inv: Inventory }) {
  const s = inv.summary;
  const tiles: [string, number, string?][] = [
    ['AP Groups', s.groups],
    ['APs', s.aps],
    ['WLANs in venue', s.networks],
    ['Venue-wide WLANs', s.all_group_networks],
    ['WLANs switched off', s.networks_off, s.networks_off ? 'text-gray-500' : undefined],
    ['Unused groups', inv.counts.unused ?? 0, inv.counts.unused ? 'text-red-700' : undefined],
    ['Empty but mapped', inv.counts.mapped ?? 0, inv.counts.mapped ? 'text-amber-700' : undefined],
    ['Over 15 SSIDs', s.over_ssid_limit, s.over_ssid_limit ? 'text-red-700' : undefined],
    ['APs in default group', s.default_aps, s.default_aps ? 'text-sky-700' : undefined],
    ['Awaiting their AP', inv.counts.awaiting_ap ?? 0, inv.counts.awaiting_ap ? 'text-sky-700' : undefined],
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 gap-2 mb-4">
      {tiles.map(([label, n, cls]) => (
        <div key={label} className="bg-white border border-gray-200 rounded-lg p-2">
          <div className={`text-xl font-semibold ${cls || 'text-gray-800'}`}>{n}</div>
          <div className="text-xs text-gray-500">{label}</div>
        </div>
      ))}
    </div>
  );
}

/** The "@site" tail most of the venue's group names share, for proposing new group names. */
function siteSuffix(groups: Group[]): string {
  const tally = new Map<string, number>();
  groups.forEach(g => {
    const at = g.name.indexOf('@');
    if (at > 0) tally.set(g.name.slice(at), (tally.get(g.name.slice(at)) || 0) + 1);
  });
  const [best] = [...tally.entries()].sort((a, b) => b[1] - a[1]);
  return best && best[1] >= groups.length / 2 ? best[0] : '';
}

const proposeName = (apName: string, suffix: string) =>
  (apName.replace(/[`*]|\$\(/g, '').trim() + suffix).slice(0, 64);

/**
 * APs left in the default group (or a group named "default"), each with a
 * target: the group its name points at, or -- for no match -- a new group
 * the operator can opt into creating.
 */
function HeldApsPanel({ inv, busy, onMove }: {
  inv: Inventory; busy: boolean; onMove: (moves: MoveRequestItem[]) => void;
}) {
  const aps = inv.default_aps;
  const suffix = useMemo(() => siteSuffix(inv.groups), [inv]);
  const existing = useMemo(() => new Set(inv.groups.map(g => g.name.toLowerCase())), [inv]);
  const [open, setOpen] = useState(true);
  // Per AP: its target group name. Matched ones go to their group; the rest
  // get a proposed new group, unticked -- creating one is opt-in.
  const [target, setTarget] = useState<Record<string, string>>(() => Object.fromEntries(aps.map(a => [
    a.serial, a.match === 'one' ? a.suggested_group_name || '' : a.match === 'none' ? proposeName(a.name, suffix) : '',
  ])));
  const [ticked, setTicked] = useState<Set<string>>(
    () => new Set(aps.filter(a => a.match === 'one' && a.suggested_group_aps === 0).map(a => a.serial)));

  const isNew = (name: string) => !!name.trim() && !existing.has(name.trim().toLowerCase());
  const chosen = aps.filter(a => ticked.has(a.serial) && (target[a.serial] || '').trim());
  const creating = new Set(chosen.map(a => target[a.serial].trim()).filter(isNew).map(n => n.toLowerCase())).size;
  const allTicked = aps.length > 0 && aps.every(a => ticked.has(a.serial));
  const tick = (serial: string) => setTicked(prev => {
    const next = new Set(prev);
    next.has(serial) ? next.delete(serial) : next.add(serial);
    return next;
  });

  const downloadCsv = () => {
    const q = (v: string) => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    const lines = ['ap_identifier,ap_group_name', ...chosen.map(a => `${q(a.serial)},${q(target[a.serial].trim())}`)];
    const url = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `ap-moves-${(inv.venue_name || 'venue').replace(/[^\w.-]+/g, '_')}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="bg-white border border-sky-200 rounded-lg mb-4">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-2 p-3 text-sm font-semibold text-gray-700">
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        APs not placed in a group
        <span className="text-xs font-normal text-gray-500">
          {aps.length} in the default group{aps.some(a => a.group_name !== '(default)') ? ' or a group named "default"' : ''} — {aps.filter(a => a.match === 'one').length} named for a group
        </span>
      </button>
      {open && (
        <div className="p-3 pt-0 space-y-2">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <button onClick={() => onMove(chosen.map(a => ({ ap: a.serial, group_name: target[a.serial].trim() })))}
              disabled={!chosen.length || busy}
              className="px-4 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 font-medium flex items-center gap-2">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <MoveRight size={14} />}
              Move {plural(chosen.length, 'AP')}{creating ? ` (creates ${plural(creating, 'group')})` : ''}
            </button>
            <button onClick={downloadCsv} disabled={!chosen.length}
              className="text-xs text-indigo-700 hover:underline disabled:opacity-50 flex items-center gap-1">
              <Download size={12} /> export as CSV
            </button>
            <span className="text-xs text-gray-500">
              Pre-ticked: APs named for a group that has no AP yet. Type an existing group's name, or a new one to create it.
            </span>
          </div>
          <datalist id="apg-group-names">
            {inv.groups.filter(g => !g.is_default && g.status !== 'named_default').map(g => <option key={g.id} value={g.name} />)}
          </datalist>
          <div className="max-h-96 overflow-y-auto border border-gray-100 rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase sticky top-0">
                <tr>
                  <th className="p-2 w-8">
                    <input type="checkbox" checked={allTicked}
                      onChange={() => setTicked(allTicked ? new Set() : new Set(aps.map(a => a.serial)))} />
                  </th>
                  <th className="p-2 text-left">AP</th>
                  <th className="p-2 text-left">Serial</th>
                  <th className="p-2 text-left">Now in</th>
                  <th className="p-2 w-6" />
                  <th className="p-2 text-left">Move to</th>
                </tr>
              </thead>
              <tbody>
                {aps.map(a => {
                  const t = target[a.serial] || '';
                  return (
                    <tr key={a.serial} className="border-t border-gray-100 align-top">
                      <td className="p-2"><input type="checkbox" checked={ticked.has(a.serial)} onChange={() => tick(a.serial)} /></td>
                      <td className="p-2 text-gray-800">{a.name}</td>
                      <td className="p-2 font-mono text-xs text-gray-500">{a.serial}</td>
                      <td className="p-2 text-xs text-gray-500">{a.group_name}</td>
                      <td className="p-2"><ArrowRight size={12} className="text-gray-400 mt-1" /></td>
                      <td className="p-2 text-xs">
                        <input list="apg-group-names" value={t}
                          onChange={e => setTarget(prev => ({ ...prev, [a.serial]: e.target.value }))}
                          placeholder={a.match === 'ambiguous' ? `pick: ${a.candidates.join(' / ')}` : 'AP Group name'}
                          className="border border-gray-200 rounded px-2 py-0.5 font-mono w-72" />
                        <div className="mt-0.5">
                          {isNew(t) ? <span className="text-purple-700">new group — created on move</span>
                            : a.match === 'one' && t === a.suggested_group_name
                              ? (a.suggested_group_aps > 0
                                  ? <span className="text-amber-700">matched by name — already has {plural(a.suggested_group_aps, 'AP')}</span>
                                  : <span className="text-gray-500">matched by name</span>)
                            : a.match === 'ambiguous' && !t ? <span className="text-amber-700">ambiguous: {a.candidates.join(', ')}</span>
                            : a.match === 'none' && !t ? <span className="text-gray-400">no group by that name</span>
                            : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function MoveModal({ plan, onCancel, onConfirm }: { plan: MovePlan; onCancel: () => void; onConfirm: () => void }) {
  const ok = plan.rows.filter(r => r.status === 'ok');
  const others = plan.rows.filter(r => r.status !== 'ok');
  return (
    <Modal title={`Move ${plural(ok.length, 'AP')}`} confirmLabel={`Move ${ok.length}`}
      onCancel={onCancel} onConfirm={ok.length ? onConfirm : onCancel}>
      {plan.groups_to_create.length > 0 && (
        <div className="mb-3 text-xs text-purple-800 bg-purple-50 border border-purple-200 rounded px-3 py-2">
          Creates {plural(plan.groups_to_create.length, 'AP Group')}: {plan.groups_to_create.join(', ')}.
          They start with no WLANs activated.
        </div>
      )}
      <table className="w-full text-sm">
        <tbody>
          {ok.map((r, i) => (
            <tr key={i} className="border-t border-gray-100 first:border-t-0">
              <td className="py-1.5 text-gray-800">{r.ap_name}</td>
              <td className="py-1.5 text-xs text-gray-500">{r.from_group}</td>
              <td className="py-1.5 px-2"><ArrowRight size={12} className="text-gray-400" /></td>
              <td className="py-1.5 text-indigo-700 font-medium">
                {r.to_group_name}{r.create && <span className="ml-1 text-xs text-purple-700">(new)</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {others.length > 0 && (
        <div className="mt-3 text-xs">
          <div className="text-gray-500 mb-1">Not moved:</div>
          {others.map((r, i) => (
            <div key={i} className={r.status === 'already' ? 'text-gray-500' : 'text-red-700'}>
              {r.ap_name || r.ap} → {r.to_group_name || '?'}: {r.status === 'already' ? 'already there' : r.error}
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-xs text-gray-500">
        Each AP is read back from R1 afterwards; one R1 has taken but not yet shown is reported as unconfirmed, not failed.
      </p>
    </Modal>
  );
}

function GroupDetail({ g }: { g: Group }) {
  return (
    <tr className="bg-gray-50/60">
      <td />
      <td />
      <td colSpan={6} className="p-3">
        <div className="grid md:grid-cols-2 gap-4 text-xs">
          <div>
            <div className="font-semibold text-gray-600 mb-1">APs ({g.ap_count})</div>
            {g.aps.length === 0 ? <div className="text-gray-400">none</div> : (
              <div className="max-h-48 overflow-y-auto">
                {g.aps.map(a => (
                  <div key={a.serial} className="flex gap-2">
                    <span className="text-gray-800">{a.name}</span>
                    <span className="text-gray-400 font-mono">{a.serial}</span>
                    <span className="text-gray-400">{a.model}</span>
                    <span className={String(a.status || '').startsWith('2') ? 'text-green-700' : 'text-gray-400'}>
                      {(a.status || '').replace(/^\d_\d+_/, '')}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="font-semibold text-gray-600 mb-1">WLANs ({g.wlans.length})</div>
            {g.wlans.length === 0 ? <div className="text-gray-400">none</div> : (
              <table>
                <tbody>
                  {g.wlans.map(w => (
                    <tr key={w.id}>
                      <td className={`pr-3 ${WLAN_STATE[w.state]}`}>{w.name}</td>
                      <td className="pr-3 text-gray-400">{w.ssid !== w.name ? w.ssid : ''}</td>
                      <td className="pr-3 text-gray-500">{w.via === 'explicit' ? 'this group' : 'all groups'}</td>
                      <td className="text-gray-500">{w.state}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </td>
    </tr>
  );
}

function RenameTable({ proposals, checked }: {
  proposals: { id: string; old: string; new: string }[];
  checked: RenameRow[] | null;
}) {
  if (!proposals.length) return null;
  const byId = new Map((checked || []).map(r => [r.id, r]));
  return (
    <div className="max-h-72 overflow-y-auto border border-gray-100 rounded">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-500 text-xs uppercase sticky top-0">
          <tr>
            <th className="p-2 text-left">Before</th>
            <th className="p-2 w-6" />
            <th className="p-2 text-left">After</th>
            <th className="p-2 text-left">Check</th>
          </tr>
        </thead>
        <tbody>
          {proposals.map(p => {
            const c = byId.get(p.id);
            return (
              <tr key={p.id} className="border-t border-gray-100">
                <td className="p-2 font-mono text-gray-500">{p.old}</td>
                <td className="p-2"><ArrowRight size={12} className="text-gray-400" /></td>
                <td className="p-2 font-mono text-indigo-700">{p.new}</td>
                <td className="p-2 text-xs">
                  {!c ? <span className="text-gray-400">not checked</span>
                    : c.status === 'ok' ? <span className="text-green-700">ok</span>
                    : c.status === 'unchanged' ? <span className="text-gray-500">unchanged</span>
                    : <span className="text-red-700">{c.error}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OutcomePanel({ outcome, onClose }: { outcome: Outcome; onClose: () => void }) {
  const { before, after, results, kind } = outcome;
  const problems = results.filter(r => !['deleted', 'renamed', 'moved', 'already'].includes(r.status));
  const done = results.length - problems.length;
  const rows: [string, number, number | null][] = [
    ['AP Groups', before.summary.groups, after?.summary.groups ?? null],
    ['In use', before.counts.in_use ?? 0, after ? after.counts.in_use ?? 0 : null],
    ['Empty but mapped', before.counts.mapped ?? 0, after ? after.counts.mapped ?? 0 : null],
    ['Unused', before.counts.unused ?? 0, after ? after.counts.unused ?? 0 : null],
    ['Named "default"', before.counts.named_default ?? 0, after ? after.counts.named_default ?? 0 : null],
    ['Awaiting AP', before.counts.awaiting_ap ?? 0, after ? after.counts.awaiting_ap ?? 0 : null],
    ['APs', before.summary.aps, after?.summary.aps ?? null],
  ];
  return (
    <div className={`border rounded-lg p-4 mb-4 text-sm ${problems.length ? 'bg-amber-50 border-amber-200' : 'bg-green-50 border-green-200'}`}>
      <div className="flex items-start justify-between">
        <div className="font-medium text-gray-800">
          {done} {kind === 'delete' ? 'deleted' : kind === 'rename' ? 'renamed' : 'moved (or already there)'}
          {problems.length ? `, ${problems.length} not` : ''}
          {after ? ' — re-read from R1:' : ' — could not re-read R1'}
        </div>
        <button onClick={onClose} className="text-xs text-gray-500 hover:text-gray-800">dismiss</button>
      </div>
      <table className="mt-2 text-xs">
        <thead className="text-gray-500">
          <tr><th className="text-left pr-6 font-normal" /><th className="text-right pr-4 font-normal">Before</th><th className="text-right font-normal">After</th></tr>
        </thead>
        <tbody>
          {rows.map(([label, b, a]) => (
            <tr key={label}>
              <td className="pr-6 text-gray-600">{label}</td>
              <td className="text-right pr-4 font-mono">{b}</td>
              <td className={`text-right font-mono ${a !== null && a !== b ? 'font-semibold text-indigo-700' : ''}`}>{a ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {kind === 'rename' && done > 0 && (
        <div className="mt-2 text-xs text-gray-700">
          {results.filter(r => r.status === 'renamed').map(r => {
            const rr = r as RenameRow;
            return <div key={rr.id}>{rr.old_name} <ArrowRight size={10} className="inline mx-1" /> {rr.new_name}</div>;
          })}
        </div>
      )}
      {kind === 'move' && done > 0 && (
        <div className="mt-2 text-xs text-gray-700 max-h-40 overflow-y-auto">
          {(results as MoveRow[]).filter(r => r.status === 'moved').map((r, i) => (
            <div key={i}>{r.ap_name}: {r.from_group} <ArrowRight size={10} className="inline mx-1" /> {r.to_group_name}</div>
          ))}
        </div>
      )}
      {problems.map((r, i) => (
        <div key={i} className={`text-xs mt-1 ${r.status === 'unconfirmed' ? 'text-amber-700' : 'text-red-700'}`}>
          {'ap_name' in r ? `${r.ap_name || r.ap} → ${r.to_group_name}`
            : 'name' in r ? r.name : `${(r as RenameRow).old_name} → ${(r as RenameRow).new_name}`}: {r.status.replace(/_/g, ' ')}
          {r.error ? ` — ${r.error}` : ''}
        </div>
      ))}
    </div>
  );
}

function Modal({ title, confirmLabel, danger, onCancel, onConfirm, children }: {
  title: string; confirmLabel: string; danger?: boolean;
  onCancel: () => void; onConfirm: () => void; children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">{title}</h2>
        </div>
        <div className="p-4 max-h-96 overflow-y-auto">{children}</div>
        <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
          <button onClick={onCancel} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
          <button onClick={onConfirm}
            className={`px-4 py-1.5 text-sm rounded text-white font-medium ${danger ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'}`}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
