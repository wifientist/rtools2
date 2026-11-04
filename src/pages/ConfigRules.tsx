import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Check, CircleSlash, CornerLeftUp, HelpCircle, ListChecks, Loader2, Pencil, Play, Plus, RefreshCw, Save,
  Trash2, X,
} from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '@/utils/api';
import SingleEcSelector from '@/components/SingleEcSelector';
import VenuePicker, { type VenueRow } from '@/components/VenuePicker';
import JobMonitorModal from '@/components/JobMonitorModal';
import { useSingleVenue } from '@/hooks/useSingleVenue';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/services/config_rules.py) ─────────────────────────

type FieldType = 'bool' | 'enum' | 'int' | 'channels';
type Level = 'venue' | 'ap_group' | 'wlan';
type State = 'pass' | 'fail' | 'unavailable' | 'inherited' | 'na';

interface CatalogField {
  id: string; label: string; group: string; level: Level; type: FieldType; ops: string[];
  options?: string[]; presets?: Record<string, string[]>; min?: number; max?: number;
}

interface Rule { id: string; name: string; field: string; op: string; value: any; enabled: boolean }

interface Ruleset { id: number; name: string; rules: Rule[]; updated_at: string | null }

interface Item { object: string; state: State; actual: any; reason: string }
interface Cell { state: State; items: Item[] }
type Counts = Record<State, number>;

interface ReportRule extends Rule { field_label: string; group: string; level: Level; op_label: string; counts: Counts }
interface ReportVenue { id: string; name: string; results: Record<string, Cell>; counts: Counts }

interface Report {
  job_id: string; status: string; ruleset_name: string | null; venues_requested: number;
  rules: ReportRule[]; venues: ReportVenue[]; totals: Counts;
}

const LEVEL_LABELS: Record<Level, string> = { venue: 'Venue', ap_group: 'AP Group', wlan: 'WLAN' };

const STATE_STYLE: Record<State, { label: string; cell: string; icon: ReactNode }> = {
  pass: { label: 'Passed', cell: 'bg-green-100 text-green-700', icon: <Check size={14} /> },
  fail: { label: 'Failed', cell: 'bg-red-100 text-red-700', icon: <X size={14} /> },
  unavailable: { label: 'Could not check', cell: 'bg-amber-100 text-amber-700', icon: <HelpCircle size={14} /> },
  inherited: { label: 'Inherited', cell: 'bg-blue-50 text-blue-600', icon: <CornerLeftUp size={14} /> },
  na: { label: 'Not applicable', cell: 'bg-gray-100 text-gray-400', icon: <CircleSlash size={14} /> },
};
const STATES: State[] = ['fail', 'unavailable', 'pass', 'inherited', 'na'];

const FINISHED = ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'];

const newId = () => `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

const showValue = (v: any): string => {
  if (Array.isArray(v)) return v.join(', ');
  if (v === true) return 'on';
  if (v === false) return 'off';
  if (v && typeof v === 'object') {
    if (v.min != null && v.max != null) return `${v.min} to ${v.max}`;
    return v.min != null ? `at least ${v.min}` : `at most ${v.max}`;
  }
  return v == null ? '' : String(v);
};

const emptyValue = (field: CatalogField, op: string): any => {
  if (op === 'range') return { min: null, max: null };
  if (op === 'eq') return field.type === 'bool' ? true : field.type === 'int' ? null : '';
  return [];
};

// ── Value editor ─────────────────────────────────────────────────

function Chips({ options, value, onChange }: { options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(o => {
        const on = value.includes(o);
        return (
          <button key={o} type="button" onClick={() => onChange(on ? value.filter(x => x !== o) : [...value, o])}
            className={`px-2 py-0.5 text-xs rounded border ${on
              ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}`}>
            {o}
          </button>
        );
      })}
    </div>
  );
}

function ValueEditor({ field, op, value, onChange }: {
  field: CatalogField; op: string; value: any; onChange: (v: any) => void;
}) {
  const num = (s: string) => (s === '' ? null : Number(s));
  const inputClass = 'px-2 py-1 text-sm border border-gray-300 rounded w-28';
  if (op === 'range') {
    return (
      <div className="flex items-center gap-2 text-sm">
        <input type="number" className={inputClass} placeholder={`min ${field.min ?? ''}`} value={value?.min ?? ''}
          onChange={e => onChange({ ...value, min: num(e.target.value) })} />
        to
        <input type="number" className={inputClass} placeholder={`max ${field.max ?? ''}`} value={value?.max ?? ''}
          onChange={e => onChange({ ...value, max: num(e.target.value) })} />
      </div>
    );
  }
  if (field.type === 'bool') {
    return (
      <select className="px-2 py-1 text-sm border border-gray-300 rounded" value={value ? 'on' : 'off'}
        onChange={e => onChange(e.target.value === 'on')}>
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
    );
  }
  if (field.type === 'int') {
    return <input type="number" className={inputClass} value={value ?? ''} onChange={e => onChange(num(e.target.value))} />;
  }
  if (op === 'eq') {
    return (
      <select className="px-2 py-1 text-sm border border-gray-300 rounded" value={value ?? ''}
        onChange={e => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {(field.options || []).map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  return (
    <div className="space-y-1.5">
      {field.presets && Object.keys(field.presets).length > 0 && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-gray-500">
          Set to:
          {Object.entries(field.presets).map(([name, list]) => (
            <button key={name} type="button" onClick={() => onChange(list)}
              className="px-2 py-0.5 rounded border border-gray-300 text-gray-700 hover:bg-gray-50">{name}</button>
          ))}
          <button type="button" onClick={() => onChange([])} className="px-2 py-0.5 text-gray-500 hover:underline">Clear</button>
        </div>
      )}
      <Chips options={field.options || []} value={Array.isArray(value) ? value : []} onChange={onChange} />
    </div>
  );
}

// ── Rule form ────────────────────────────────────────────────────

function RuleForm({ initial, fields, opLabels, onSave, onCancel }: {
  initial: Rule | null; fields: CatalogField[]; opLabels: Record<string, string>;
  onSave: (r: Rule) => void; onCancel: () => void;
}) {
  const start = initial ? fields.find(f => f.id === initial.field) : null;
  const [level, setLevel] = useState<Level>(start?.level || 'venue');
  const [fieldId, setFieldId] = useState(initial?.field || '');
  const [op, setOp] = useState(initial?.op || '');
  const [value, setValue] = useState<any>(initial?.value);
  const [name, setName] = useState(initial?.name || '');

  const field = fields.find(f => f.id === fieldId) || null;
  const groups = useMemo(() => {
    const out: Record<string, CatalogField[]> = {};
    fields.filter(f => f.level === level).forEach(f => { (out[f.group] ||= []).push(f); });
    return out;
  }, [fields, level]);

  const chooseField = (id: string) => {
    const f = fields.find(x => x.id === id);
    setFieldId(id);
    if (f) {
      setOp(f.ops[0]);
      setValue(emptyValue(f, f.ops[0]));
    }
  };
  const chooseOp = (next: string) => {
    setOp(next);
    if (field) setValue(emptyValue(field, next));
  };

  const complete = !!field && !!op && (
    op === 'range' ? value?.min != null || value?.max != null
      : Array.isArray(value) ? value.length > 0
        : value !== '' && value != null);
  const autoName = field && complete ? `${field.label} ${opLabels[op]} ${showValue(value)}` : '';

  return (
    <div className="border border-blue-200 bg-blue-50/40 rounded-lg p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select className="px-2 py-1 text-sm border border-gray-300 rounded" value={level}
          onChange={e => { setLevel(e.target.value as Level); setFieldId(''); setOp(''); }}>
          {(Object.keys(LEVEL_LABELS) as Level[]).map(l => <option key={l} value={l}>{LEVEL_LABELS[l]}</option>)}
        </select>
        <select className="px-2 py-1 text-sm border border-gray-300 rounded" value={fieldId}
          onChange={e => chooseField(e.target.value)}>
          <option value="">Choose a setting…</option>
          {Object.entries(groups).map(([group, list]) => (
            <optgroup key={group} label={group}>
              {list.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
            </optgroup>
          ))}
        </select>
        {field && (
          <select className="px-2 py-1 text-sm border border-gray-300 rounded" value={op}
            onChange={e => chooseOp(e.target.value)}>
            {field.ops.map(o => <option key={o} value={o}>{opLabels[o]}</option>)}
          </select>
        )}
      </div>
      {field && <ValueEditor field={field} op={op} value={value} onChange={setValue} />}
      <div className="flex flex-wrap items-center gap-2">
        <input value={name} onChange={e => setName(e.target.value)} placeholder={autoName || 'Rule name'}
          className="px-2 py-1 text-sm border border-gray-300 rounded flex-1 min-w-[16rem]" />
        <button type="button" disabled={!complete}
          onClick={() => onSave({
            id: initial?.id || newId(), name: name.trim() || autoName, field: fieldId, op, value,
            enabled: initial?.enabled ?? true,
          })}
          className="px-3 py-1 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
          {initial ? 'Update rule' : 'Add rule'}
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1 text-sm rounded border border-gray-300 hover:bg-gray-50">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Report ───────────────────────────────────────────────────────

function ReportView({ report, onRecheck, canRecheck }: {
  report: Report; onRecheck: () => void; canRecheck: boolean;
}) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [open, setOpen] = useState<{ rule: ReportRule; venue: ReportVenue } | null>(null);
  const rules = onlyProblems ? report.rules.filter(r => r.counts.fail || r.counts.unavailable) : report.rules;

  const expected = (r: ReportRule) => `${r.op_label} ${showValue(r.value)}`;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-gray-700">
          Results{report.ruleset_name ? `: ${report.ruleset_name}` : ''}
          <span className="font-normal text-gray-500"> · {report.venues.length} of {report.venues_requested} venues</span>
        </h3>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-gray-600">
          <input type="checkbox" checked={onlyProblems} onChange={e => setOnlyProblems(e.target.checked)} />
          Only rules with problems
        </label>
        <button onClick={onRecheck} disabled={!canRecheck}
          title="Re-evaluate this run with the ruleset as it is now. Does not read R1."
          className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40">
          <RefreshCw size={12} /> Recheck with current rules
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        {STATES.map(s => (
          <span key={s} className={`inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs ${STATE_STYLE[s].cell}`}>
            {STATE_STYLE[s].icon} {STATE_STYLE[s].label}: {report.totals[s]}
          </span>
        ))}
      </div>

      {rules.length === 0 ? (
        <p className="text-sm text-gray-500">{onlyProblems ? 'No problems found.' : 'No rules to show.'}</p>
      ) : (
        <div className="overflow-auto max-h-[70vh] border border-gray-200 rounded">
          <table className="text-xs border-separate border-spacing-0">
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-20 bg-gray-50 p-2 text-left font-semibold text-gray-600 border-b border-r border-gray-200 min-w-[18rem] max-w-[26rem]">
                  Rule
                </th>
                <th className="sticky top-0 z-10 bg-gray-50 p-2 font-semibold text-gray-600 border-b border-r border-gray-200 whitespace-nowrap">
                  Failed
                </th>
                {report.venues.map(v => (
                  <th key={v.id} className="sticky top-0 z-10 bg-gray-50 p-2 font-medium text-gray-600 border-b border-gray-200 whitespace-nowrap max-w-[10rem] truncate" title={v.name}>
                    {v.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rules.map(r => (
                <tr key={r.id}>
                  <td className="sticky left-0 z-10 bg-white p-2 border-b border-r border-gray-100">
                    <div className="font-medium text-gray-800">{r.name}</div>
                    <div className="text-gray-400">{LEVEL_LABELS[r.level]} · {expected(r)}</div>
                  </td>
                  <td className="p-2 text-center border-b border-r border-gray-100 font-semibold text-gray-700">
                    {r.counts.fail}
                  </td>
                  {report.venues.map(v => {
                    const cell = v.results[r.id];
                    const style = STATE_STYLE[cell?.state || 'na'];
                    return (
                      <td key={v.id} className="p-1 text-center border-b border-gray-100">
                        <button onClick={() => setOpen({ rule: r, venue: v })} title={style.label}
                          className={`inline-flex items-center justify-center w-8 h-6 rounded ${style.cell} hover:ring-2 hover:ring-gray-300`}>
                          {style.icon}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && (
        <div className="border border-gray-200 rounded p-3">
          <div className="flex items-start gap-2 mb-2">
            <div className="text-sm">
              <span className="font-semibold text-gray-800">{open.venue.name}</span>
              <span className="text-gray-500"> · {open.rule.name}</span>
              <div className="text-xs text-gray-500">Rule: {open.rule.field_label} {expected(open.rule)}</div>
            </div>
            <button onClick={() => setOpen(null)} className="ml-auto text-gray-400 hover:text-gray-700" aria-label="Close"><X size={16} /></button>
          </div>
          {(open.venue.results[open.rule.id]?.items || []).length === 0 ? (
            <p className="text-xs text-gray-500">Nothing at this venue to check.</p>
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {(open.venue.results[open.rule.id]?.items || []).map((item, i) => (
                  <tr key={i} className="border-t border-gray-100 align-top">
                    <td className="py-1 pr-3 text-gray-800 whitespace-nowrap">{item.object}</td>
                    <td className="py-1 pr-3 whitespace-nowrap">{STATE_STYLE[item.state].label}</td>
                    <td className="py-1 text-gray-600 break-words">{item.reason || showValue(item.actual)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────

export default function ConfigRules() {
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
  const tenantId = isMSP ? ecId : (r1Controller?.r1_tenant_id || null);
  const { venueData, loading: venuesLoading } = useSingleVenue(r1Id, tenantId);
  const venues: VenueRow[] = useMemo(
    () => venueData.map((v: any) => ({ venueId: v.id, venueName: v.name || v.id })), [venueData]);
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState('');

  const [fields, setFields] = useState<CatalogField[]>([]);
  const [opLabels, setOpLabels] = useState<Record<string, string>>({});
  const [starter, setStarter] = useState<Rule[]>([]);
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [tab, setTab] = useState<'run' | 'manage'>('run');
  const [runRulesetId, setRunRulesetId] = useState<number | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ name: string; rules: Rule[] } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState<Rule | 'new' | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const jobId = searchParams.get('job');
  const [monitorOpen, setMonitorOpen] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const running = !!report && !FINISHED.includes(report.status);
  const runRuleset = rulesets.find(r => r.id === runRulesetId) || rulesets[0] || null;
  const current = rulesets.find(r => r.id === currentId) || null;
  const fieldById = useMemo(() => Object.fromEntries(fields.map(f => [f.id, f])), [fields]);

  const load = (r: Ruleset | null) => {
    setCurrentId(r?.id ?? null);
    setDraft(r ? { name: r.name, rules: r.rules } : null);
    setDirty(false);
    setEditing(null);
  };

  useEffect(() => {
    (async () => {
      try {
        const [c, rs] = await Promise.all([
          apiGet<any>(`${API_URL}/config-rules/catalog`),
          apiGet<{ rulesets: Ruleset[] }>(`${API_URL}/config-rules/rulesets`),
        ]);
        setFields(c.fields);
        setOpLabels(c.op_labels);
        setStarter(c.starter_rules);
        setRulesets(rs.rulesets);
        load(rs.rulesets[0] || null);
        if (!rs.rulesets.length) setTab('manage');
      } catch (e: any) {
        setError(e?.message || 'Could not load Config Rules');
      }
    })();
  }, []);

  const loadReport = useCallback(async (id: string, rulesetId?: number) => {
    try {
      const qs = rulesetId ? `?ruleset_id=${rulesetId}` : '';
      setReport(await apiGet<Report>(`${API_URL}/config-rules/runs/${id}/report${qs}`));
    } catch (e: any) {
      setError(e?.message || 'Could not load the results');
    }
  }, []);

  useEffect(() => { if (jobId) loadReport(jobId); }, [jobId, loadReport]);

  // Results appear when the run ends, with or without the job monitor open.
  useEffect(() => {
    if (!jobId || !running) return;
    const timer = setTimeout(() => loadReport(jobId), 5000);
    return () => clearTimeout(timer);
  }, [jobId, running, report, loadReport]);

  const setRules = (rules: Rule[]) => {
    setDraft(d => (d ? { ...d, rules } : d));
    setDirty(true);
  };

  const guarded = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e?.message || 'That did not work');
    } finally {
      setBusy(false);
    }
  };

  const save = () => guarded(async () => {
    if (!draft || !current) return;
    const saved = await apiPut<Ruleset>(`${API_URL}/config-rules/rulesets/${current.id}`, {
      name: draft.name, rules: draft.rules, expected_updated_at: current.updated_at,
    });
    setRulesets(list => list.map(r => (r.id === saved.id ? saved : r)));
    setDirty(false);
  });

  const createRuleset = (name: string, rules: Rule[]) => guarded(async () => {
    const created = await apiPost<Ruleset>(`${API_URL}/config-rules/rulesets`, { name, rules });
    setRulesets(list => [...list, created].sort((a, b) => a.name.localeCompare(b.name)));
    load(created);
  });

  const removeRuleset = () => guarded(async () => {
    if (!current || !window.confirm(`Delete "${current.name}" for everyone in your company?`)) return;
    await apiDelete(`${API_URL}/config-rules/rulesets/${current.id}`);
    const rest = rulesets.filter(r => r.id !== current.id);
    setRulesets(rest);
    load(rest[0] || null);
  });

  const reload = () => guarded(async () => {
    const rs = await apiGet<{ rulesets: Ruleset[] }>(`${API_URL}/config-rules/rulesets`);
    setRulesets(rs.rulesets);
    load(rs.rulesets.find(r => r.id === currentId) || rs.rulesets[0] || null);
  });

  const run = () => guarded(async () => {
    if (!runRuleset) return;
    setReport(null);
    setNotice(null);
    const res = await apiPost<{ job_id: string; already_running?: boolean }>(`${API_URL}/config-rules/run`, {
      controller_id: r1Id, tenant_id: tenantId, ruleset_id: runRuleset.id, venue_ids: selected,
    });
    if (res.already_running) setNotice('You already have a check running on this tenant, so this is that one.');
    setSearchParams({ job: res.job_id });
    setMonitorOpen(true);
  });

  const grouped = useMemo(() => {
    const out: Record<string, Rule[]> = {};
    (draft?.rules || []).forEach(r => {
      const label = fieldById[r.field]?.group || 'Unknown setting';
      (out[label] ||= []).push(r);
    });
    return out;
  }, [draft, fieldById]);

  const canRun = !!runRuleset && selected.length > 0 && !!r1Id && (!isMSP || !!ecId) && !busy && !running;
  const tabClass = (t: string) => `px-3 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t
    ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`;

  return (
    <div className="p-4 max-w-7xl mx-auto space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-800 flex items-center gap-2">
          <ListChecks size={22} /> Config Rules
        </h1>
        <p className="text-sm text-gray-500">
          Check venues, AP Groups and WLANs against your company's rules. This reads R1 and changes nothing.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
          {error.includes('Reload it') && (
            <button onClick={reload} className="ml-auto underline whitespace-nowrap">Reload ruleset</button>
          )}
        </div>
      )}

      <div className="flex border-b border-gray-200" role="tablist">
        <button role="tab" aria-selected={tab === 'run'} className={tabClass('run')} onClick={() => setTab('run')}>Run a check</button>
        <button role="tab" aria-selected={tab === 'manage'} className={tabClass('manage')} onClick={() => setTab('manage')}>
          Manage rulesets{dirty ? ' •' : ''}
        </button>
      </div>

      {tab === 'manage' && (
        <div className="grid md:grid-cols-[16rem_1fr] gap-4 items-start">
          <div className="bg-white border border-gray-200 rounded-lg p-3 space-y-2">
            <ul className="space-y-1">
              {rulesets.map(r => (
                <li key={r.id}>
                  <button onClick={() => {
                    if (r.id === currentId || (dirty && !window.confirm('Discard unsaved changes?'))) return;
                    load(r);
                  }}
                    className={`w-full text-left px-2 py-1.5 rounded text-sm ${r.id === currentId
                      ? 'bg-blue-50 text-blue-800 font-medium' : 'text-gray-700 hover:bg-gray-50'}`}>
                    {r.name}
                    <span className="block text-xs font-normal text-gray-500">{r.rules.length} rule{r.rules.length === 1 ? '' : 's'}</span>
                  </button>
                </li>
              ))}
            </ul>
            {rulesets.length === 0 && <p className="text-sm text-gray-500 px-2">No rulesets yet.</p>}
            <button disabled={busy}
              onClick={() => {
                if (dirty && !window.confirm('Discard unsaved changes?')) return;
                const n = window.prompt('Name for the new ruleset');
                if (n?.trim()) createRuleset(n.trim(), []);
              }}
              className="w-full flex items-center justify-center gap-1 px-2 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40">
              <Plus size={14} /> New ruleset
            </button>
            {rulesets.length === 0 && starter.length > 0 && (
              <button disabled={busy} onClick={() => createRuleset('Starter rules', starter.map(r => ({ ...r, id: newId() })))}
                className="w-full px-2 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-40">
                Create from starter rules
              </button>
            )}
            <p className="text-xs text-gray-500 px-1">Shared with everyone in your company, and anyone in it can edit them.</p>
          </div>

          {draft && current ? (
            <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <input value={draft.name} aria-label="Ruleset name"
                  onChange={e => { setDraft({ ...draft, name: e.target.value }); setDirty(true); }}
                  className="px-2 py-1.5 text-sm border border-gray-300 rounded w-64" />
                <button onClick={save} disabled={!dirty || busy}
                  className="flex items-center gap-1 px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
                  <Save size={14} /> Save
                </button>
                {dirty && <span className="text-xs text-amber-700">Unsaved changes</span>}
                <button onClick={removeRuleset} disabled={busy} aria-label="Delete ruleset"
                  className="ml-auto p-1.5 text-gray-400 hover:text-red-600"><Trash2 size={16} /></button>
              </div>
              {draft.rules.length === 0 && !editing && <p className="text-sm text-gray-500">No rules yet.</p>}
              {Object.entries(grouped).map(([group, rules]) => (
                <div key={group}>
                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">{group}</h4>
                  <ul className="border border-gray-200 rounded divide-y divide-gray-100">
                    {rules.map(r => {
                      const f = fieldById[r.field];
                      return (
                        <li key={r.id} className="flex items-center gap-2 px-2 py-1.5 text-sm">
                          <input type="checkbox" checked={r.enabled} aria-label={`Enable ${r.name}`}
                            onChange={e => setRules(draft.rules.map(x => (x.id === r.id ? { ...x, enabled: e.target.checked } : x)))} />
                          <span className={r.enabled ? 'text-gray-800' : 'text-gray-400'}>{r.name}</span>
                          <span className="text-xs text-gray-400 truncate">
                            {LEVEL_LABELS[f?.level || 'venue']} · {opLabels[r.op]} {showValue(r.value)}
                          </span>
                          <button onClick={() => setEditing(r)} aria-label={`Edit ${r.name}`} className="ml-auto text-gray-400 hover:text-gray-700"><Pencil size={14} /></button>
                          <button onClick={() => setRules(draft.rules.filter(x => x.id !== r.id))} aria-label={`Remove ${r.name}`}
                            className="text-gray-400 hover:text-red-600"><Trash2 size={14} /></button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
              {editing ? (
                <RuleForm key={editing === 'new' ? 'new' : editing.id} initial={editing === 'new' ? null : editing}
                  fields={fields} opLabels={opLabels} onCancel={() => setEditing(null)}
                  onSave={rule => {
                    setRules(editing === 'new' ? [...draft.rules, rule] : draft.rules.map(x => (x.id === rule.id ? rule : x)));
                    setEditing(null);
                  }} />
              ) : (
                <button onClick={() => setEditing('new')}
                  className="flex items-center gap-1 px-2 py-1 text-sm rounded border border-dashed border-gray-300 text-gray-600 hover:bg-gray-50">
                  <Plus size={14} /> Add a rule
                </button>
              )}
            </div>
          ) : (
            <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm text-gray-500">
              Create a ruleset to start adding rules.
            </div>
          )}
        </div>
      )}

      {tab === 'run' && (
        <>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <label htmlFor="run-ruleset" className="block text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">Ruleset</label>
            {rulesets.length === 0 ? (
              <p className="text-sm text-gray-500">
                No rulesets yet. <button onClick={() => setTab('manage')} className="text-blue-600 hover:underline">Create one</button>.
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <select id="run-ruleset" className="px-2 py-1.5 text-sm border border-gray-300 rounded min-w-[16rem]"
                  value={runRuleset?.id ?? ''} onChange={e => setRunRulesetId(Number(e.target.value))}>
                  {rulesets.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
                <span className="text-xs text-gray-500">
                  {runRuleset?.rules.filter(r => r.enabled).length ?? 0} enabled rules
                </span>
                {dirty && current?.id === runRuleset?.id && (
                  <span className="text-xs text-amber-700">Runs the saved version; your edits are not saved yet.</span>
                )}
              </div>
            )}
          </div>

          {!r1Id ? (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
              Select a RuckusONE controller from the toolbar to run these rules.
            </div>
          ) : (
            <div className="space-y-3">
              {isMSP && (
                <div className="bg-white border border-gray-200 rounded-lg p-4">
                  <SingleEcSelector controllerId={r1Id} selectedEcId={ecId}
                    onEcSelect={(id) => { setEcId(id); setSelected([]); }} />
                </div>
              )}
              {(!isMSP || ecId) && (
                <VenuePicker venues={venues} loading={venuesLoading} selected={selected} setSelected={setSelected}
                  filter={filter} setFilter={setFilter} emptyMessage="No venues in this tenant."
                  renderMeta={() => null}
                  renderSummary={(chosen) => `${chosen.length} of ${venues.length} selected`} />
              )}
              <div className="flex flex-wrap items-center gap-3">
                <button onClick={run} disabled={!canRun}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
                  {busy || running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  {running ? 'Check running…' : `Check ${selected.length} venue${selected.length === 1 ? '' : 's'}`}
                </button>
                {jobId && (
                  <button onClick={() => setMonitorOpen(true)} className="text-xs text-blue-600 hover:underline">Show job</button>
                )}
                {notice && <span className="text-xs text-amber-700">{notice}</span>}
              </div>
            </div>
          )}

          {running && (
            <div className="flex items-center gap-2 p-3 bg-white border border-gray-200 rounded-lg text-sm text-gray-600">
              <Loader2 size={14} className="animate-spin" />
              Reading R1. Results appear here when it finishes, and you can close the job window meanwhile.
            </div>
          )}

          {report && !running && (
            <ReportView report={report} canRecheck={!!runRuleset && !!jobId}
              onRecheck={() => { if (jobId && runRuleset) loadReport(jobId, runRuleset.id); }} />
          )}
        </>
      )}

      {jobId && (
        <JobMonitorModal key={jobId} jobId={jobId} isOpen={monitorOpen}
          onClose={() => { setMonitorOpen(false); loadReport(jobId); }}
          onJobComplete={() => loadReport(jobId)} />
      )}
    </div>
  );
}
