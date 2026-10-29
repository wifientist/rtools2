import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, ArrowRight, Play, CheckCircle, XCircle, RefreshCw, AlertTriangle, Search } from 'lucide-react';
import { apiFetch } from '@/utils/api';
import V2PlanConfirmModal from '@/components/V2PlanConfirmModal';
import JobMonitorModal from '@/components/JobMonitorModal';
import WlanPlacement from './WlanPlacement';
import type { WizardState, WizardAction, R1Layout, LayoutChoices } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

interface Counts { [k: string]: number }

interface StageReport {
  job_id: string;
  status: string;
  errors: string[];
  before: Counts;
  map: { counts?: Counts; problems?: Array<{ serial: string; ap_name: string; to_group_name: string; status: string; error: string }> };
  activate: Array<{ name: string; status: string; error: string | null }>;
  verify: {
    counts?: Counts;
    broadcast?: Counts;
    differing?: Array<{ name: string; serial: string; missing_in_r1: string[]; extra_in_r1: string[]; sz_ap_group: string; r1_ap_group: string }>;
    differing_total?: number;
    not_mapped?: Array<{ name: string; serial: string; group_name: string; current_group: string | null; action: string }>;
    not_active?: Array<{ name: string; status: string; error: string | null; targets: Array<{ group_name: string; status: string }> }>;
    kept?: Array<{ name: string; serial: string; current_group: string; extra_ssids: string[] }>;
    no_network?: string[];
    staged?: Array<{ name: string; ssids: string[]; ap_count: number }>;
    fallback_default?: Array<{ name: string; serial: string; wanted_group: string | null }>;
    by_name?: Array<{ name: string; serial: string; group_name: string; sz_group_name: string | null }>;
  };
}

interface PreviewAp {
  name: string | null; serial: string | null; mac: string | null; model: string | null; sz_ap_group: string | null;
  group_name: string; group_id: string | null; staged: boolean; broadcast: string[];
  current_group: string | null; in_r1: boolean; silent: boolean;
  action: 'already' | 'move' | 'not_in_r1' | 'group_missing' | 'keep' | 'unmapped' | 'blocked' | 'add' | 'not_selected';
  note?: string; extra_ssids?: string[]; error: string | null;
  name_clash?: boolean; r1_name?: string | null;
  // Place by name: retargeted (sz_group_name = where SZ's broadcast put it), or,
  // when off, the group its name points at instead
  by_name?: boolean; sz_group_name?: string | null; name_group?: string | null;
  // Manual: the operator's AP Group, checked against R1 (exists / planned = this plan builds it / missing)
  manual?: boolean; manual_status?: 'exists' | 'planned' | 'missing' | null; auto_group?: string | null;
}
interface Preview {
  venue_name: string; r1_ap_count: number; counts: Counts; aps: PreviewAp[];
  r1_groups: string[]; manual_missing: string[];
  warnings: Array<{ message: string; severity: string }>;
  staged: { unpaired: string[]; conflicts: string[] };
  activations: Array<{ name: string; status: string; error: string | null }>;
}

// What the table's filter chips group by.
type View = 'all' | 'move' | 'new' | 'already' | 'waiting' | 'nogroup' | 'keep' | 'other' | 'byname';
const VIEW_OF = (a: PreviewAp['action']): View =>
  a === 'move' ? 'move' : a === 'not_in_r1' ? 'new' : a === 'already' ? 'already'
    : a === 'group_missing' ? 'waiting' : a === 'unmapped' ? 'nogroup' : a === 'keep' ? 'keep' : 'other';
const VIEWS: Array<[View, string]> = [
  ['all', 'All'], ['move', 'To move'], ['new', 'Not in R1 yet'], ['already', 'Already in place'],
  ['waiting', 'Waiting on an AP Group'], ['nogroup', 'No AP Group built'], ['keep', 'Kept as R1 has them'],
  ['other', 'No serial'], ['byname', 'Name points elsewhere'],
];
const inView = (a: PreviewAp, vw: View) =>
  vw === 'all' || (vw === 'byname' ? !!(a.by_name || a.name_group) : VIEW_OF(a.action) === vw);
const ROW_CAP = 800;

const Tile = ({ label, value, ok }: { label: string; value: string; ok: boolean | null }) => (
  <div className={`rounded border px-3 py-2 ${ok === null ? 'border-gray-200' : ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'}`}>
    <div className="text-[11px] text-gray-500">{label}</div>
    <div className={`text-lg font-semibold tabular-nums ${ok === null ? 'text-gray-800' : ok ? 'text-green-700' : 'text-red-700'}`}>{value}</div>
  </div>
);

const List = ({ items }: { items: string[] }) =>
  items.length ? <>{items.map((s, i) => <div key={i}>{s}</div>)}</> : <span className="text-gray-300">—</span>;

export default function StepApMapping({ state, dispatch }: Props) {
  const [layout, setLayout] = useState<R1Layout | null>(null);
  const [layoutLoading, setLayoutLoading] = useState(false);
  const layoutSeq = useRef(0);
  const [planning, setPlanning] = useState(false);
  const [planJobId, setPlanJobId] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [showMonitor, setShowMonitor] = useState(false);
  const [report, setReport] = useState<StageReport | null>(null);
  // The AP table: a read-only preview of every SZ AP against the venue now.
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Picked serials. Moves start picked; APs not in R1 only when picked --
  // adding a serial claims it for this R1 tenant.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [view, setView] = useState<View>('all');
  // AP names must be unique within the venue; SZ doesn't care ("Hallway" x2).
  const [namePolicy, setNamePolicy] = useState<'serial' | 'keep'>('serial');
  // serial -> the R1 name typed for a duplicate-named AP (default: Name_serial)
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [query, setQuery] = useState('');
  // SZ had APs in the wrong AP Groups: send each to the R1 group its name points at.
  const [placeByName, setPlaceByName] = useState(false);
  // Manual AP Group per AP (serial -> name): typed (draft), and the set last
  // validated against R1 -- the preview and the job use only the validated set.
  const [draftOverrides, setDraftOverrides] = useState<Record<string, string>>({});
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState<string | null>(null);
  const cleanOverrides = (o: Record<string, string>) =>
    Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
  const overridesDirty = JSON.stringify(cleanOverrides(draftOverrides)) !== JSON.stringify(overrides);

  // Placement: the same choices Review uses.
  useEffect(() => {
    if (!state.extractionJobId) return;
    const seq = ++layoutSeq.current;
    (async () => {
      setLayoutLoading(true);
      try {
        const res = await apiFetch(`${API_URL}/sz-migration/layout/${state.extractionJobId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ r1_snapshot_job_id: state.r1SnapshotJobId, choices: state.layoutChoices }),
        });
        if (!res.ok) throw new Error(`Placement failed: ${res.status}`);
        const data: R1Layout = await res.json();
        if (seq === layoutSeq.current) setLayout(data);
      } catch (e: any) {
        if (seq === layoutSeq.current) dispatch({ type: 'SET_ERROR', error: e.message });
      } finally {
        if (seq === layoutSeq.current) setLayoutLoading(false);
      }
    })();
  }, [state.extractionJobId, state.r1SnapshotJobId, state.layoutChoices]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadPreview = async () => {
    if (!state.extractionJobId || !state.r1ControllerId || !state.destVenueId) return;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/ap-mapping/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          r1_controller_id: state.r1ControllerId, tenant_id: state.tenantId, venue_id: state.destVenueId,
          sz_snapshot_job_id: state.extractionJobId, layout_choices: state.layoutChoices,
          ap_name_policy: namePolicy, place_by_name: placeByName, ap_group_overrides: overrides,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Preview failed: ${res.status}`);
      setPreview(data);
      setPicked(new Set((data as Preview).aps.filter(a => a.action === 'move' && a.serial).map(a => a.serial!)));
    } catch (e: any) {
      setPreviewError(e.message);
    } finally {
      setPreviewLoading(false);
    }
  };

  useEffect(() => { loadPreview(); },
    [state.extractionJobId, state.destVenueId, state.layoutChoices, namePolicy, placeByName, overrides]); // eslint-disable-line react-hooks/exhaustive-deps

  const aps = preview?.aps ?? [];
  // An AP whose AP Group wasn't built can still come over: picked, it goes to
  // the default group (and broadcasts the All AP Groups WLANs until then).
  const selectable = (a: PreviewAp) => !!a.serial
    && (a.action === 'move' || a.action === 'not_in_r1'
        || (a.action === 'unmapped' && !(a.in_r1 && a.current_group === '(default)')));
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return aps.filter(a => inView(a, view) && (!q || [
      a.name, a.serial, a.mac, a.model, a.sz_ap_group, a.group_name, a.current_group, a.sz_group_name, a.name_group,
      a.serial ? draftOverrides[a.serial] : null,
      ...a.broadcast,
    ].some(v => (v || '').toLowerCase().includes(q))));
  }, [aps, view, query]);
  const countOf = (vw: View) => aps.filter(a => inView(a, vw)).length;
  const isPicked = (a: PreviewAp) => !!a.serial && picked.has(a.serial);
  // The R1 name an AP will get: typed (or Name_serial) for a duplicate when renaming.
  const r1NameOf = (a: PreviewAp) => a.name_clash && namePolicy === 'serial' && a.serial
    ? (renames[a.serial] ?? `${a.name}_${a.serial}`) : (a.name ?? '');
  const badRename = namePolicy === 'serial' && (() => {
    const counts = new Map<string, number>();
    for (const a of aps) { const n = r1NameOf(a).trim().toLowerCase(); if (n) counts.set(n, (counts.get(n) ?? 0) + 1); }
    return aps.some(a => a.name_clash && (!r1NameOf(a).trim() || (counts.get(r1NameOf(a).trim().toLowerCase()) ?? 0) > 1));
  })();
  const toMove = aps.filter(a => isPicked(a) && (a.action === 'move' || (a.action === 'unmapped' && a.in_r1))).length;
  const toAdd = aps.filter(a => isPicked(a) && (a.action === 'not_in_r1' || (a.action === 'unmapped' && !a.in_r1))).length;
  const toDefault = aps.filter(a => isPicked(a) && a.action === 'unmapped').length;
  const noGroupShown = shown.filter(a => a.action === 'unmapped' && selectable(a));
  const newShown = shown.filter(a => a.action === 'not_in_r1');
  const setPick = (serials: string[], on: boolean) => setPicked(prev => {
    const next = new Set(prev);
    serials.forEach(x => (on ? next.add(x) : next.delete(x)));
    return next;
  });
  const pc = preview?.counts ?? {};
  const configGaps = (pc.aps_group_missing ?? 0) > 0 || (preview?.activations ?? []).length > 0;

  const loadReport = async (jobId: string) => {
    const res = await apiFetch(`${API_URL}/sz-migration/ap-mapping/${jobId}/report`);
    if (res.ok) setReport(await res.json());
  };

  useEffect(() => {
    if (state.apMappingJobId) loadReport(state.apMappingJobId).catch(() => {});
  }, [state.apMappingJobId]);

  const startPlan = async () => {
    if (!state.extractionJobId || !state.r1ControllerId || !state.destVenueId) return;
    setPlanning(true);
    dispatch({ type: 'SET_ERROR', error: null });
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/ap-mapping/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          r1_controller_id: state.r1ControllerId,
          tenant_id: state.tenantId,
          venue_id: state.destVenueId,
          sz_snapshot_job_id: state.extractionJobId,
          layout_choices: state.layoutChoices,
          selected_serials: [...picked],
          ap_name_policy: namePolicy,
          place_by_name: placeByName,
          ap_group_overrides: overrides,
          ap_renames: namePolicy === 'serial'
            ? Object.fromEntries(aps.filter(a => a.name_clash && a.serial)
                .map(a => [a.serial!, (renames[a.serial!] ?? `${a.name}_${a.serial}`).trim()]))
            : {},
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Planning failed: ${res.status}`);
      setPlanJobId(data.job_id);
      setShowPlan(true);
    } catch (e: any) {
      dispatch({ type: 'SET_ERROR', error: e.message });
    } finally {
      setPlanning(false);
    }
  };

  const createMissing = async (names: string[]) => {
    if (!state.r1ControllerId || !state.destVenueId) return;
    setCreating(true);
    setCreateResult(null);
    try {
      const res = await apiFetch(`${API_URL}/sz-migration/ap-mapping/create-groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ r1_controller_id: state.r1ControllerId, tenant_id: state.tenantId,
                               venue_id: state.destVenueId, names }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Create failed: ${res.status}`);
      const errs = (data.errors ?? []) as Array<{ name: string; error: string }>;
      setCreateResult(`Created ${data.created.length}${data.existing.length ? `, ${data.existing.length} already there` : ''}`
        + (errs.length ? ` — failed: ${errs.map(e => `${e.name} (${e.error})`).join('; ')}` : ''));
      await loadPreview();
    } catch (e: any) {
      setCreateResult(e.message);
    } finally {
      setCreating(false);
    }
  };

  const onConfirm = (jobId: string) => {
    setShowPlan(false);
    dispatch({ type: 'SET_AP_MAPPING_JOB', jobId });
    setShowMonitor(true);
  };

  const onJobComplete = () => {
    if (state.apMappingJobId) loadReport(state.apMappingJobId).catch(() => {});
    loadPreview();
  };

  const v = report?.verify ?? {};
  const c = v.counts ?? {};
  const differ = v.differing_total ?? 0;
  const mappable = (c.aps ?? 0) - (c.aps_unmapped ?? 0) - (c.aps_blocked ?? 0) - (c.aps_keep ?? 0)
    - (c.aps_not_in_r1 ?? 0) - (c.aps_group_missing ?? 0);
  const activatable = (c.wlans ?? 0) - (c.wlans_no_network ?? 0);
  const verified = !!v.counts && differ === 0 && (v.not_mapped ?? []).length === 0 && (v.not_active ?? []).length === 0
    && (c.aps_group_missing ?? 0) === 0;
  const running = report && ['RUNNING', 'PENDING', 'AWAITING_CONFIRMATION'].includes(report.status);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-1">
          <h3 className="text-lg font-semibold">AP Mapping</h3>
          <span className="text-sm text-gray-500">{state.selectedZoneName} → {state.destVenueName}</span>
          <button onClick={loadPreview} disabled={previewLoading} title="Read the venue again"
                  className="ml-auto text-gray-400 hover:text-gray-600 disabled:opacity-40">
            <RefreshCw size={14} className={previewLoading ? 'animate-spin' : ''} />
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          The last step, and optional. Create WLANs has built all the configuration — networks, every AP Group (staged
          ones too) and activations. Here the APs themselves come over: each SZ AP below, the R1 AP Group it belongs in,
          and what mapping does with it. Pick the APs to map — or skip this step and keep the configuration only.
        </p>

        {layout && (
          <details className={`mb-3 ${layoutLoading ? 'opacity-60' : ''}`}>
            <summary className="text-xs text-gray-600 cursor-pointer select-none">
              Placement from the WLANs step: {layout.summary.groups + (layout.summary.staged_groups ?? 0)} AP Group(s),
              {' '}{layout.summary.all_ap_wlans} WLAN(s) on All AP Groups — read-only here; change it on the WLANs step
              and re-run Create WLANs
            </summary>
            <div className="mt-2">
              <WlanPlacement layout={layout} choices={state.layoutChoices} onChange={(_: LayoutChoices) => {}} disabled />
            </div>
          </details>
        )}

        {configGaps && (
          <div className="text-xs text-red-800 bg-red-50 border border-red-200 rounded px-3 py-2 mb-3 space-y-0.5">
            {(pc.aps_group_missing ?? 0) > 0 && (
              <div className="flex gap-1.5"><AlertTriangle size={13} className="mt-0.5" />
                {pc.aps_group_missing} AP(s) belong in AP Groups not in R1 yet — re-run Create WLANs (it builds every
                AP Group), then come back.</div>
            )}
            {(preview?.activations ?? []).length > 0 && (
              <div className="flex gap-1.5"><AlertTriangle size={13} className="mt-0.5" />
                {preview!.activations.length} WLAN(s) not active where the plan puts them
                ({preview!.activations.slice(0, 4).map(a => a.name).join(', ')}{preview!.activations.length > 4 ? '…' : ''})
                — re-run Create WLANs; AP Mapping doesn't activate.</div>
            )}
            <button onClick={() => dispatch({ type: 'RERUN_EXECUTION' })}
                    className="mt-1 px-3 py-1 bg-orange-600 text-white rounded hover:bg-orange-700 font-medium">
              Plan and run Create WLANs again
            </button>
          </div>
        )}
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-base font-semibold">APs from SmartZone ({aps.length})</h3>
          {preview && <span className="text-xs text-gray-500">{preview.venue_name} has {preview.r1_ap_count} AP(s) in R1 now</span>}
        </div>

        {previewLoading && !preview && (
          <div className="text-sm text-gray-500 flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Reading the venue…</div>
        )}
        {previewError && <div className="text-sm text-red-700">{previewError}</div>}

        {preview && (
          <>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {VIEWS.map(([k, label]) => {
                const n = countOf(k);
                if (k !== 'all' && n === 0) return null;
                return (
                  <button key={k} onClick={() => setView(k)}
                          className={`text-xs px-2.5 py-1 rounded-full border ${view === k
                            ? 'bg-gray-800 text-white border-gray-800' : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'}`}>
                    {label} <span className="tabular-nums">{n}</span>
                  </button>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center gap-3 mb-2 text-xs">
              <div className="relative w-72">
                <Search size={13} className="absolute left-2 top-1.5 text-gray-400" />
                <input value={query} onChange={e => setQuery(e.target.value)}
                       placeholder="Search AP, serial, model, group or SSID"
                       className="pl-7 pr-2 py-1 border border-gray-300 rounded w-full" />
              </div>
              <button onClick={() => setPick(shown.filter(selectable).map(a => a.serial!), true)}
                      className="text-indigo-600 hover:underline">Pick all shown</button>
              <button onClick={() => setPick(shown.filter(selectable).map(a => a.serial!), false)}
                      className="text-gray-500 hover:underline">Clear shown</button>
              {newShown.length > 0 && (
                <button onClick={() => setPick(newShown.map(a => a.serial!), true)}
                        className="text-amber-700 hover:underline font-medium">
                  Add all {newShown.length} not in R1{newShown.length !== countOf('new') ? ' shown' : ''}
                </button>
              )}
              {noGroupShown.length > 0 && (
                <button onClick={() => setPick(noGroupShown.map(a => a.serial!), true)}
                        className="text-amber-700 hover:underline">
                  Pick all {noGroupShown.length} without an AP Group (→ default group)
                </button>
              )}
              <span className="ml-auto text-gray-500">
                {toMove} to move · {toAdd} to add{toDefault ? ` · ${toDefault} into the default group` : ''}
              </span>
            </div>

            {(Object.keys(cleanOverrides(draftOverrides)).length > 0 || Object.keys(overrides).length > 0) && (
              <div className="text-xs border border-indigo-200 bg-indigo-50/50 rounded px-3 py-2 mb-2 space-y-1.5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-semibold text-gray-700">
                    {Object.keys(cleanOverrides(draftOverrides)).length} AP(s) placed by hand
                  </span>
                  <button onClick={() => setOverrides(cleanOverrides(draftOverrides))}
                          disabled={previewLoading || !overridesDirty}
                          className="px-2.5 py-1 rounded bg-indigo-600 text-white font-medium disabled:bg-gray-300">
                    {previewLoading ? 'Checking R1…' : 'Validate manual choices'}
                  </button>
                  <button onClick={() => { setDraftOverrides({}); setOverrides({}); }}
                          className="text-gray-500 hover:underline">Clear all</button>
                  {overridesDirty
                    ? <span className="text-amber-700">Changed since the last check — validate before mapping</span>
                    : <span className="text-green-700">Checked against R1</span>}
                </div>
                {!overridesDirty && preview.manual_missing.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 text-red-800">
                    <AlertTriangle size={13} />
                    {preview.manual_missing.length} AP Group(s) not in R1: {preview.manual_missing.slice(0, 8).join(', ')}
                    {preview.manual_missing.length > 8 ? '…' : ''}
                    <button onClick={() => createMissing(preview.manual_missing)} disabled={creating}
                            className="px-2.5 py-1 rounded bg-orange-600 text-white font-medium disabled:bg-gray-300">
                      {creating ? 'Creating…' : `Create ${preview.manual_missing.length} AP Group(s) in R1`}
                    </button>
                    <span className="text-gray-500">(empty groups, nothing activated on them) — or fix the names</span>
                  </div>
                )}
                {createResult && <div className="text-gray-700">{createResult}</div>}
              </div>
            )}

            {(preview.counts.aps_name_clash ?? 0) > 0 && (() => {
              const dup = aps.filter(a => a.name_clash && a.serial);
              const nameOf = (a: PreviewAp) => renames[a.serial!] ?? `${a.name}_${a.serial}`;
              // Names an edited box would still collide with: the other APs here
              const taken = new Map<string, number>();
              for (const a of aps) {
                const n = (a.name_clash && namePolicy === 'serial' ? nameOf(a) : a.name || '').trim().toLowerCase();
                if (n) taken.set(n, (taken.get(n) ?? 0) + 1);
              }
              return (
                <div className="text-xs bg-amber-50 border border-amber-300 rounded px-3 py-2 mb-2 space-y-2">
                  <div className="flex gap-1.5 text-amber-900">
                    <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
                    <span>
                      {dup.length} AP(s) share a name with another AP being loaded or already in this venue. R1 needs
                      AP names unique within a venue.
                    </span>
                  </div>
                  <label className="flex items-center gap-1.5 pl-5 font-medium">
                    <input type="radio" checked={namePolicy === 'serial'} onChange={() => setNamePolicy('serial')} />
                    Rename
                  </label>
                  {namePolicy === 'serial' && (
                    <div className="pl-10 space-y-1 max-h-48 overflow-y-auto">
                      {dup.map(a => {
                        const v = nameOf(a);
                        const bad = !v.trim() ? 'empty' : (taken.get(v.trim().toLowerCase()) ?? 0) > 1 ? 'still a duplicate' : '';
                        return (
                          <div key={a.serial} className="flex items-center gap-2">
                            <span className="w-40 truncate text-gray-700" title={a.serial ?? ''}>{a.name} <span className="text-gray-400 font-mono">{a.serial}</span></span>
                            <span className="text-gray-400">→</span>
                            <input className={`border rounded px-1.5 py-0.5 font-mono w-64 ${bad ? 'border-red-400' : 'border-gray-300'}`}
                                   value={v} maxLength={64}
                                   onChange={e => setRenames(r => ({ ...r, [a.serial!]: e.target.value }))} />
                            {bad && <span className="text-red-700">{bad}</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <label className="flex items-center gap-1.5 pl-5">
                    <input type="radio" checked={namePolicy === 'keep'} onChange={() => setNamePolicy('keep')} />
                    Keep the names — R1 will refuse adding the duplicates
                  </label>
                </div>
              );
            })()}

            <div className="text-xs border border-gray-200 rounded px-3 py-2 mb-2 space-y-1">
              <div className="font-semibold text-gray-700">Which R1 AP Group each AP goes into</div>
              <label className="flex items-start gap-2 cursor-pointer">
                <input type="radio" className="mt-0.5" checked={!placeByName} disabled={previewLoading}
                       onChange={() => setPlaceByName(false)} />
                <span><span className="font-medium">As SmartZone had it</span> (default) — each AP gets the group
                  matching what it broadcast in SZ.</span>
              </label>
              <label className="flex items-start gap-2 cursor-pointer">
                <input type="radio" className="mt-0.5" checked={placeByName} disabled={previewLoading}
                       onChange={() => setPlaceByName(true)} />
                <span><span className="font-medium">By AP name</span> — the group named after the AP (or carrying its
                  "unit@…" SSID). An AP sitting in another unit's group with no group of its own goes to the default
                  group (common WLANs only). Use when SZ had APs in the wrong AP Groups.</span>
              </label>
              <div className={(pc.aps_name_differs ?? 0) > 0 ? 'text-amber-800 pl-5' : 'text-gray-500 pl-5'}>
                {(pc.aps_name_differs ?? 0) > 0
                  ? <>{pc.aps_name_differs} AP(s) land in a different group by name than by SZ —
                      {' '}<button className="underline" onClick={() => setView('byname')}>show them</button>.</>
                  : 'Both give the same result for every AP here.'}
              </div>
            </div>

            {toAdd > 0 && (
              <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-2 flex gap-1.5">
                <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
                {toAdd} AP(s) will be added to {preview.venue_name} by serial. A serial belongs to one R1 tenant at a
                time — don't add real APs to a sandbox you'll need them out of later.
              </div>
            )}

            <datalist id="r1-ap-groups">
              <option value="(default)" />
              {preview.r1_groups.map(n => <option key={n} value={n} />)}
            </datalist>
            <div className="border border-gray-200 rounded max-h-[34rem] overflow-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-500 text-left sticky top-0">
                  <tr>
                    <th className="px-2 py-1.5 w-6"></th>
                    <th className="px-2 py-1.5 font-medium">AP</th>
                    <th className="px-2 py-1.5 font-medium">SZ AP Group</th>
                    <th className="px-2 py-1.5 font-medium">→ R1 AP Group: as SZ had it</th>
                    <th className="px-2 py-1.5 font-medium">by AP name</th>
                    <th className="px-2 py-1.5 font-medium">manual</th>
                    <th className="px-2 py-1.5 font-medium">In R1 now</th>
                    <th className="px-2 py-1.5 font-medium">Mapping</th>
                    <th className="px-2 py-1.5 font-medium">Will broadcast</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {shown.slice(0, ROW_CAP).map(a => {
                    const on = !!a.serial && picked.has(a.serial);
                    const outcome =
                      a.action === 'move' ? (on ? ['Move', 'text-blue-700'] : ['Leave where it is', 'text-gray-400'])
                      : a.action === 'not_in_r1' ? (on ? ['Add to R1', 'text-amber-700 font-semibold'] : ['Not added', 'text-gray-400'])
                      : a.action === 'already' ? ['In place', 'text-green-700']
                      : a.action === 'group_missing' ? ['Waiting on AP Group', 'text-red-700']
                      : a.action === 'keep' ? ['Kept as R1 has it', 'text-purple-700']
                      : a.action === 'unmapped'
                        ? (on ? [a.in_r1 ? 'Move → default group' : 'Add to R1 → default group', 'text-amber-700 font-semibold']
                              : a.in_r1 && a.current_group === '(default)' ? ['In default group (no AP Group built)', 'text-gray-500']
                              : ['No AP Group built', 'text-gray-500'])
                      : ['No serial', 'text-gray-500'];
                    return (
                      <tr key={a.serial ?? a.mac ?? a.name ?? ''} className={`align-top ${on ? 'bg-indigo-50/40' : ''}`}>
                        <td className="px-2 py-1">
                          {selectable(a) && (
                            <input type="checkbox" checked={on} onChange={e => setPick([a.serial!], e.target.checked)} />
                          )}
                        </td>
                        <td className="px-2 py-1">
                          <div className="font-medium text-gray-800">
                            {a.name ?? '—'}
                            {a.name_clash && <span className="ml-1 text-[10px] px-1 rounded bg-amber-100 text-amber-800">duplicate name</span>}
                          </div>
                          {r1NameOf(a) && r1NameOf(a) !== a.name && (
                            <div className="text-[11px] text-amber-700">→ in R1: <span className="font-mono">{r1NameOf(a)}</span></div>
                          )}
                          <div className="font-mono text-gray-400">{a.serial ?? a.mac}{a.model ? ` · ${a.model}` : ''}</div>
                        </td>
                        <td className="px-2 py-1 text-gray-600">{a.sz_ap_group ?? '—'}</td>
                        {(() => {
                          // Both targets; the one in use is bold, the other greyed
                          const bySz = a.by_name || a.manual ? (a.sz_group_name ?? '—') : a.group_name;
                          const byName = a.by_name ? a.group_name : a.name_group;
                          const draft = a.serial ? (draftOverrides[a.serial] ?? '') : '';
                          const pending = !!a.serial && draft.trim() !== (overrides[a.serial] ?? '');
                          return (<>
                            <td className={`px-2 py-1 ${a.manual || (placeByName && byName) ? 'text-gray-400 line-through' : 'font-medium'}`}>
                              {bySz}
                              {a.staged && !a.by_name && <span className="ml-1 text-[10px] px-1 rounded bg-indigo-100 text-indigo-700 no-underline">staged</span>}
                            </td>
                            <td className="px-2 py-1">
                              {byName
                                ? <span className={a.manual ? 'text-gray-400 line-through'
                                    : placeByName ? 'font-medium text-indigo-700' : 'text-amber-700'}>{byName}</span>
                                : <span className="text-gray-300">same</span>}
                            </td>
                            <td className="px-2 py-1 whitespace-nowrap">
                              {a.serial && (
                                <input list="r1-ap-groups" value={draft} placeholder="AP Group…" maxLength={64}
                                       onChange={e => setDraftOverrides(o => ({ ...o, [a.serial!]: e.target.value }))}
                                       className={`border rounded px-1.5 py-0.5 w-32 ${draft.trim() ? 'border-indigo-400 font-medium' : 'border-gray-200'}`} />
                              )}
                              {a.manual && !pending && (
                                a.manual_status === 'missing'
                                  ? <span className="ml-1 text-[10px] px-1 rounded bg-red-100 text-red-700">not in R1</span>
                                  : a.manual_status === 'planned'
                                    ? <span className="ml-1 text-[10px] px-1 rounded bg-blue-100 text-blue-700">to be built</span>
                                    : <span className="ml-1 text-[10px] px-1 rounded bg-green-100 text-green-700">in R1 ✓</span>
                              )}
                              {pending && <span className="ml-1 text-[10px] text-amber-700">not checked</span>}
                            </td>
                          </>);
                        })()}
                        <td className="px-2 py-1 text-gray-600">{a.in_r1 ? (a.current_group ?? '—') : <span className="text-gray-400">not in R1</span>}</td>
                        <td className={`px-2 py-1 ${outcome[1]}`} title={a.note ?? a.error ?? ''}>{outcome[0]}</td>
                        <td className="px-2 py-1 text-gray-600" title={a.broadcast.join('\n')}>
                          {a.broadcast.length === 0 ? <span className="text-gray-300">nothing</span>
                            : <>{a.broadcast.slice(0, 2).join(', ')}{a.broadcast.length > 2 && <span className="text-gray-400"> +{a.broadcast.length - 2}</span>}</>}
                        </td>
                      </tr>
                    );
                  })}
                  {shown.length === 0 && <tr><td colSpan={9} className="px-3 py-3 text-center text-gray-400">No APs match</td></tr>}
                </tbody>
              </table>
            </div>
            {shown.length > ROW_CAP && <div className="text-xs text-gray-400 mt-1">Showing {ROW_CAP} of {shown.length} — narrow with search or a filter</div>}
          </>
        )}
      </div>

      {report && (
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <div className="flex items-center gap-2">
            {verified ? <CheckCircle size={18} className="text-green-600" />
              : running ? <Loader2 size={18} className="animate-spin text-blue-600" />
              : <XCircle size={18} className="text-red-600" />}
            <h3 className="text-base font-semibold">
              {verified ? 'Every AP broadcasts in R1 what it broadcasts in SZ'
                : running ? 'Mapping…'
                : v.counts ? 'Some APs are not where SZ puts them yet' : 'No read-back yet'}
            </h3>
            <button onClick={() => state.apMappingJobId && loadReport(state.apMappingJobId)}
                    className="ml-auto text-gray-400 hover:text-gray-600" title="Refresh">
              <RefreshCw size={14} />
            </button>
          </div>

          {v.counts ? (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <Tile label="APs in their AP Group" value={`${c.aps_already ?? 0} / ${mappable}`}
                      ok={(c.aps_already ?? 0) >= mappable} />
                <Tile label="WLANs active as planned" value={`${c.wlans_already ?? 0} / ${activatable}`}
                      ok={(c.wlans_already ?? 0) >= activatable} />
                <Tile label="APs broadcasting differently from SZ" value={String(differ)} ok={differ === 0} />
                <Tile label="Kept as R1 has them" value={String(c.aps_keep ?? 0)} ok={null} />
                {(c.aps_group_missing ?? 0) > 0 && (
                  <Tile label="APs waiting on an AP Group (re-run Create WLANs)" value={String(c.aps_group_missing)} ok={false} />
                )}
                {(c.aps_not_in_r1 ?? 0) > 0 && (
                  <Tile label="SZ APs not in R1 (not added)" value={String(c.aps_not_in_r1)} ok={null} />
                )}
              </div>

              {((c.aps_unmapped ?? 0) > 0 || (v.no_network ?? []).length > 0) && (
                <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2 space-y-0.5">
                  {(c.aps_unmapped ?? 0) > 0 && (
                    <div className="flex gap-1.5"><AlertTriangle size={13} className="mt-0.5" />
                      {c.aps_unmapped} AP(s) belong in AP Groups not in R1 and not ticked to create — tick them above and run again.</div>
                  )}
                  {(v.no_network ?? []).length > 0 && (
                    <div className="flex gap-1.5"><AlertTriangle size={13} className="mt-0.5" />
                      No R1 network yet for: {(v.no_network ?? []).join(', ')} — create them (Execute), then run again.</div>
                  )}
                </div>
              )}

              {(report.map.problems ?? []).length > 0 && (
                <Section title={`AP mapping problems (${report.map.problems!.length})`}>
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {report.map.problems!.map(p => (
                      <tr key={p.serial}><td className="px-3 py-1 font-medium">{p.ap_name}</td>
                        <td className="px-3 py-1 font-mono text-gray-500">{p.serial}</td>
                        <td className="px-3 py-1">→ {p.to_group_name}</td>
                        <td className="px-3 py-1 text-red-700">{p.status}: {p.error}</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}

              {(v.not_active ?? []).length > 0 && (
                <Section title={`WLANs not active as planned (${v.not_active!.length})`}>
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {v.not_active!.map(a => (
                      <tr key={a.name} className="align-top"><td className="px-3 py-1 font-medium">{a.name}</td>
                        <td className="px-3 py-1 text-red-700">{a.error || a.targets.map(t => `${t.group_name} (${t.status})`).join(', ')}</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}

              {differ > 0 && (
                <Section title={`APs broadcasting differently from SZ (${differ}${differ > (v.differing ?? []).length ? `, first ${(v.differing ?? []).length}` : ''})`}>
                  <table className="w-full text-xs">
                    <thead className="text-gray-500 text-left"><tr>
                      <th className="px-3 py-1 font-medium">AP</th><th className="px-3 py-1 font-medium">R1 AP Group</th>
                      <th className="px-3 py-1 font-medium">Missing in R1</th><th className="px-3 py-1 font-medium">Extra in R1</th>
                    </tr></thead>
                    <tbody className="divide-y divide-gray-100">
                      {(v.differing ?? []).map(d => (
                        <tr key={d.serial} className="align-top">
                          <td className="px-3 py-1"><div className="font-medium">{d.name}</div><div className="font-mono text-gray-400">{d.serial}</div></td>
                          <td className="px-3 py-1">{d.r1_ap_group ?? '—'}</td>
                          <td className="px-3 py-1 text-red-600"><List items={d.missing_in_r1} /></td>
                          <td className="px-3 py-1 text-purple-700"><List items={d.extra_in_r1} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Section>
              )}

              {(v.staged ?? []).length > 0 && (
                <Section title={`Staged — ready, not activated (${v.staged!.length})`}
                         note="WLANs no AP broadcasts in SZ: created, with their AP Group built and filled. Activate the WLAN on its group to turn it on.">
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {v.staged!.map(g => (
                      <tr key={g.name}><td className="px-3 py-1 font-medium">{g.name}</td>
                        <td className="px-3 py-1">{g.ssids.join(', ')}</td>
                        <td className="px-3 py-1 text-gray-500">{g.ap_count} AP(s)</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}

              {(v.fallback_default ?? []).length > 0 && (
                <Section title={`In the default group — their AP Group isn't built (${v.fallback_default!.length})`}
                         note="Brought over by choice without their own AP Group; they broadcast only the All AP Groups WLANs. Tick their groups on the WLANs step and re-run Create WLANs, then map them again, to give them their SZ broadcast.">
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {v.fallback_default!.map(k => (
                      <tr key={k.serial}><td className="px-3 py-1 font-medium">{k.name}</td>
                        <td className="px-3 py-1 font-mono text-gray-400">{k.serial}</td>
                        <td className="px-3 py-1 text-gray-600">wants AP Group {k.wanted_group}</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}

              {(v.by_name ?? []).length > 0 && (
                <Section title={`Placed by name (${v.by_name!.length})`}
                         note="Mapped to the AP Group their name points at, not the one their SZ broadcast did. They broadcast that group's WLANs, so they aren't compared with SZ.">
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {v.by_name!.map(k => (
                      <tr key={k.serial}><td className="px-3 py-1 font-medium">{k.name}</td>
                        <td className="px-3 py-1">→ {k.group_name}</td>
                        <td className="px-3 py-1 text-gray-500">SZ broadcast said {k.sz_group_name ?? '—'}</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}

              {(v.kept ?? []).length > 0 && (
                <Section title={`Kept as R1 has them (${v.kept!.length})`}
                         note="SZ broadcasts only the All AP Groups WLANs on these APs, but R1 has them in groups with SSIDs of their own — usually a fix made in R1. Left where they are; move them in R1 if that's wrong.">
                  <table className="w-full text-xs"><tbody className="divide-y divide-gray-100">
                    {v.kept!.map(k => (
                      <tr key={k.serial}><td className="px-3 py-1 font-medium">{k.name}</td>
                        <td className="px-3 py-1">{k.current_group}</td>
                        <td className="px-3 py-1 text-purple-700">+ {k.extra_ssids.join(', ')}</td></tr>
                    ))}
                  </tbody></table>
                </Section>
              )}
            </>
          ) : (
            <div className="text-sm text-gray-500">
              {running ? 'The job is running — results appear here when it finishes.'
                : (report.errors ?? []).join('; ') || 'No read-back results.'}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 justify-end">
        {state.apMappingJobId && (
          <button onClick={() => setShowMonitor(true)} className="text-xs text-blue-600 hover:underline mr-auto">
            Open job monitor
          </button>
        )}
        <button
          onClick={startPlan}
          disabled={!preview || planning || !!running || toMove + toAdd === 0 || badRename || overridesDirty}
          title={overridesDirty ? 'Validate manual choices first'
            : badRename ? 'A rename box is empty or still a duplicate' : undefined}
          className="px-5 py-2.5 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:bg-gray-300 font-semibold text-sm flex items-center gap-2"
        >
          {planning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          Plan and map {toMove + toAdd} AP(s)
        </button>
        <button
          onClick={() => dispatch({ type: 'SET_STEP', step: 8 })}
          className={`px-5 py-2.5 rounded-lg font-semibold text-sm flex items-center gap-2 ${verified
            ? 'bg-green-600 text-white hover:bg-green-700' : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50'}`}
        >
          {verified ? 'Continue to Results' : report ? 'Continue to Results' : 'Skip AP Mapping, keep config only, go to Results'}
          <ArrowRight size={14} />
        </button>
      </div>

      {planJobId && (
        <V2PlanConfirmModal
          jobId={planJobId}
          isOpen={showPlan}
          workflowName="sz_ap_mapping"
          onClose={() => setShowPlan(false)}
          onConfirm={onConfirm}
          apiPrefix="/sz-migration/workflow"
        />
      )}
      {state.apMappingJobId && (
        <JobMonitorModal
          jobId={state.apMappingJobId}
          isOpen={showMonitor}
          onClose={() => { setShowMonitor(false); onJobComplete(); }}
          onJobComplete={onJobComplete}
        />
      )}
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-semibold text-gray-700 mb-1">{title}</div>
      {note && <div className="text-[11px] text-gray-500 mb-1">{note}</div>}
      <div className="border border-gray-200 rounded max-h-72 overflow-y-auto">{children}</div>
    </div>
  );
}
