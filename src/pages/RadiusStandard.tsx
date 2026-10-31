import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, ArrowRight, ChevronDown, ChevronRight, Eye, Gauge, Loader2, Pencil, Plus, RefreshCw, Save, Trash2, Trophy, X,
} from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '@/utils/api';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';
const READ_CONCURRENCY = 4;
const RANKED_SHOWN = 5;
const OPERATORS = ['ADD', 'ADD_REPLACE', 'DOES_NOT_EXIST'];

// ── Types (api/routers/radius_standard/router.py) ────────────────

interface Attribute { attributeName: string; operator: string; attributeValue: string; dataType: string }
interface Group { name: string; attributes: Attribute[] }
interface Profile { id: number; name: string; groups: Group[]; source: string | null; source_tenant_id: string | null; updated_at: string | null }
interface CatalogueEntry { name: string; vendorName: string; dataType: string | null }
interface Tenant { id: string; name: string; controllerId: number }
interface R1Group { id: string; name: string; attributeAssignments: Attribute[]; externalAssignmentsCount: number }
interface TenantRead { loading?: boolean; error?: string; groups?: R1Group[] }
interface Template { groups: Group[]; source: string; sourceTenantId: string | null }

interface ValueItem { operator: string; value: string }
interface AttributeRow { attributeName: string; expected: ValueItem[]; actual: ValueItem[]; same: boolean }
interface GroupCompare {
  name: string;
  status: 'match' | 'differs' | 'missing';
  group_id: string | null;
  in_use: number;
  similar_name: string | null;
  attributes: AttributeRow[];
}
interface Extra { name: string; in_use: number; attributes: Attribute[] }
interface Comparison { groups: GroupCompare[]; extras: Extra[] }
interface Ranked {
  id: string; name: string; score: number | null;
  agrees: string[]; differs: string[]; lacks: string[]; other_groups: string[];
}
interface GroupSummary {
  name: string; tenant_count: number; common: boolean; tied: boolean;
  variants: { attributes: Attribute[]; tenant_ids: string[]; majority: boolean }[];
}
interface Analysis {
  consistency: { tenants: Ranked[]; groups: GroupSummary[]; scored_groups: number };
  comparisons: Record<string, Comparison>;
}
interface ApplyResult { name: string; status: 'created' | 'updated' | 'unchanged' | 'failed' | 'unverified'; error?: string }

// Bandwidth attributes are bits per second. Customers land on both 500 * 10^6
// and 500 * 2^20, so a round binary value is labelled as what it is.
function speed(name: string, value: string): string | null {
  if (!name.includes('Bandwidth')) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n % 1048576 === 0) return n >= 1073741824 ? `${n / 1073741824} Gibps` : `${n / 1048576} Mibps`;
  if (n >= 1e9) return `${+(n / 1e9).toFixed(2)} Gbps`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(2)} Mbps`;
  return `${+(n / 1e3).toFixed(2)} Kbps`;
}

// One grid cell: the speeds at a glance, anything else by name.
function cellLine(a: Attribute): string {
  const shown = speed(a.attributeName, a.attributeValue) || a.attributeValue;
  if (a.attributeName === 'WISPr-Bandwidth-Max-Down') return `↓ ${shown}`;
  if (a.attributeName === 'WISPr-Bandwidth-Max-Up') return `↑ ${shown}`;
  return `${a.attributeName.replace(/^WISPr-/, '')}: ${shown}`;
}

const allAttributes = (attrs: Attribute[]) =>
  attrs.map(a => `${a.attributeName} ${a.operator} ${a.attributeValue}`).join('\n');

const blankAttribute = (): Attribute => ({
  attributeName: 'WISPr-Bandwidth-Max-Down', operator: 'ADD', attributeValue: '', dataType: 'INTEGER',
});

const toTemplateGroups = (groups: R1Group[]): Group[] =>
  groups
    .filter(g => g.attributeAssignments?.length)
    .map(g => ({ name: g.name, attributes: g.attributeAssignments.map(a => ({ ...a, attributeValue: String(a.attributeValue) })) }))
    .sort((a, b) => a.name.localeCompare(b.name));

function ValueList({ items, attr }: { items: ValueItem[]; attr: string }) {
  if (!items.length) return <span className="text-gray-400 italic">not set</span>;
  return (
    <>
      {items.map((v, i) => (
        <div key={i} className="font-mono">
          {v.value}
          {speed(attr, v.value) && <span className="ml-2 font-sans text-gray-500">{speed(attr, v.value)}</span>}
          {v.operator !== 'ADD' && <span className="ml-2 font-sans text-xs text-gray-400">{v.operator}</span>}
        </div>
      ))}
    </>
  );
}

// ── Page ─────────────────────────────────────────────────────────

type Tab = 'compare' | 'template';
const CURRENT_KEY = 'radiusStandard.currentProfile';

function rememberedProfile(): number | null {
  try { return Number(localStorage.getItem(CURRENT_KEY)) || null; } catch { return null; }
}
function rememberProfile(id: number | null) {
  try {
    if (id) localStorage.setItem(CURRENT_KEY, String(id)); else localStorage.removeItem(CURRENT_KEY);
  } catch { /* only a convenience */ }
}

function Blocked({ children }: { children: ReactNode }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 flex items-start gap-2">
      <AlertCircle className="text-red-500 mt-0.5 shrink-0" size={18} />
      <div className="text-red-800 font-semibold text-sm">{children}</div>
    </div>
  );
}

export default function RadiusStandard() {
  const { activeControllerId: aId, secondaryControllerId: sId, controllers } = useAuth();

  const r1 = (id: number | null) => controllers?.find(c => c.id === id && c.controller_type === 'RuckusONE');
  const active = r1(aId);
  const secondary = r1(sId);
  const isMsp = (c?: { controller_subtype: string | null }) => c?.controller_subtype === 'MSP';
  // Two of a kind selected: the active one is the one used.
  const msp = isMsp(active) ? active : isMsp(secondary) ? secondary : undefined;
  const target = active && !isMsp(active) ? active : secondary && !isMsp(secondary) ? secondary : undefined;
  const twoMsps = isMsp(active) && isMsp(secondary);
  const twoTargets = !!active && !!secondary && !isMsp(active) && !isMsp(secondary);
  const ecControllers = (controllers || []).filter(c => c.controller_type === 'RuckusONE' && !isMsp(c) && c.r1_tenant_id);

  const [error, setError] = useState<string | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const known = useMemo(() => new Map(catalogue.map(a => [a.name, a])), [catalogue]);

  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [current, setCurrent] = useState<Profile | null>(null);
  const [tab, setTab] = useState<Tab>('template');
  const [saveName, setSaveName] = useState('');
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState<{ id: string; name: string } | null>(null);
  const [draft, setDraft] = useState<Group[] | null>(null);   // non-null while editing

  const template: Template | null = useMemo(() => current && ({
    groups: current.groups,
    source: current.source ? `${current.name}, taken from ${current.source}` : `${current.name}, built by hand`,
    sourceTenantId: current.source_tenant_id,
  }), [current]);
  const templateNames = template?.groups.map(g => g.name) || [];

  // MSP overview (Choose template tab)
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [reads, setReads] = useState<Record<string, TenantRead>>({});
  const [reading, setReading] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const analysisSeq = useRef(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showAllRanked, setShowAllRanked] = useState(false);

  // One tenant against the template (Compare tab)
  const [targetRead, setTargetRead] = useState<TenantRead>({});
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const targetSeq = useRef(0);

  // A chosen template opens on the comparison; otherwise choosing one is the job.
  useEffect(() => {
    apiGet<{ profiles: Profile[] }>(`${API_URL}/radius-standards/profiles`)
      .then(d => {
        setProfiles(d.profiles);
        const remembered = d.profiles.find(p => p.id === rememberedProfile());
        const chosen = remembered || (d.profiles.length === 1 ? d.profiles[0] : null);
        setCurrent(chosen);
        setSaveName(chosen?.name || '');
        setTab(chosen ? 'compare' : 'template');
      })
      .catch(e => setError(e.message));
  }, []);

  useEffect(() => {
    const via = msp ?? target;
    if (!via) return;
    apiPost<{ attributes: CatalogueEntry[] }>(`${API_URL}/radius-standards/attributes`, {
      r1_controller_id: via.id, tenant_id: msp ? null : via.r1_tenant_id,
    })
      .then(d => setCatalogue(d.attributes))
      .catch(e => setError(`Could not read R1's attribute list: ${e.message}`));
  }, [msp?.id, target?.id]);

  useEffect(() => {
    setTenants([]);
    setReads({});
    setAnalysis(null);
    if (!msp) return;
    apiPost<{ tenants: { id: string; name: string }[] }>(`${API_URL}/radius-standards/tenants`, { r1_controller_id: msp.id })
      .then(d => setTenants(d.tenants.map(t => ({ ...t, controllerId: msp.id }))))
      .catch(e => setError(e.message));
  }, [msp?.id]);

  // ── MSP overview ──

  const readTenant = async (t: Tenant) => {
    setReads(r => ({ ...r, [t.id]: { ...r[t.id], loading: true, error: undefined } }));
    try {
      const data = await apiPost<{ groups: R1Group[] }>(`${API_URL}/radius-standards/read`, {
        r1_controller_id: t.controllerId, tenant_id: t.id,
      });
      setReads(r => ({ ...r, [t.id]: { groups: data.groups } }));
    } catch (e: any) {
      setReads(r => ({ ...r, [t.id]: { error: e.message } }));
    }
  };

  const readAll = async () => {
    setReading(true);
    setReads({});
    setAnalysis(null);
    const queue = [...tenants];
    await Promise.all(Array.from({ length: READ_CONCURRENCY }, async () => {
      for (let t = queue.shift(); t; t = queue.shift()) await readTenant(t);
    }));
    setReading(false);
  };

  // Ranking and comparisons come from the server's one implementation.
  useEffect(() => {
    if (reading) return;
    const read = tenants.filter(t => reads[t.id]?.groups);
    if (!read.length) return;
    const seq = ++analysisSeq.current;
    apiPost<Analysis>(`${API_URL}/radius-standards/analyze`, {
      tenants: read.map(t => ({ id: t.id, name: t.name, groups: reads[t.id].groups })),
      template: template?.groups ?? null,
    })
      .then(d => { if (seq === analysisSeq.current) setAnalysis(d); })
      .catch(e => { if (seq === analysisSeq.current) setError(e.message); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reading, reads, template]);

  // ── Compare ──

  const compareGroups = async (groups: R1Group[], seq: number) => {
    if (!template || !target) return;
    const d = await apiPost<Analysis>(`${API_URL}/radius-standards/analyze`, {
      tenants: [{ id: target.r1_tenant_id, name: target.name, groups }],
      template: template.groups,
    });
    if (seq === targetSeq.current) setComparison(d.comparisons[target.r1_tenant_id!]);
  };

  const loadTarget = async () => {
    if (!template || !target) return;
    const seq = ++targetSeq.current;
    setTargetRead({ loading: true });
    setComparison(null);
    try {
      const data = await apiPost<{ groups: R1Group[] }>(`${API_URL}/radius-standards/read`, {
        r1_controller_id: target.id, tenant_id: target.r1_tenant_id,
      });
      if (seq !== targetSeq.current) return;
      setTargetRead({ groups: data.groups });
      await compareGroups(data.groups, seq);
    } catch (e: any) {
      if (seq === targetSeq.current) setTargetRead({ error: e.message });
    }
  };

  useEffect(() => {
    if (tab === 'compare') loadTarget();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, target?.id, template]);

  // ── Template ──

  const choose = (p: Profile | null) => {
    setCurrent(p);
    rememberProfile(p?.id ?? null);
    setSaveName(p?.name || '');
    setDraft(null);
  };

  const store = async (name: string, groups: Group[], source: string | null, sourceTenantId: string | null): Promise<void> => {
    const existing = profiles?.find(p => p.name === name);
    const body = { name, groups, source, source_tenant_id: sourceTenantId };
    const saved = existing
      ? await apiPut<Profile>(`${API_URL}/radius-standards/profiles/${existing.id}`, body)
      : await apiPost<Profile>(`${API_URL}/radius-standards/profiles`, body);
    setProfiles(ps => [...(ps || []).filter(p => p.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)));
    choose(saved);
  };

  const pickTenant = async (name: string) => {
    const t = picking;
    const groups = t && reads[t.id]?.groups;
    if (!t || !groups) return;
    await store(name, toTemplateGroups(groups), t.name, t.id);
    setPicking(null);
    setTab('compare');
  };

  const draftProblem = useMemo(() => {
    if (!draft) return null;
    const names = draft.map(g => g.name.trim());
    if (!draft.length) return 'A template needs at least one group';
    if (names.some(n => !n)) return 'Every group needs a name';
    if (new Set(names).size !== names.length) return 'Two groups share a name';
    if (draft.some(g => !g.attributes.length)) return 'Every group needs at least one attribute';
    if (draft.some(g => g.attributes.some(a => !a.attributeValue.trim()))) return 'Every attribute needs a value';
    if (draft.some(g => g.attributes.some(a => a.dataType === 'INTEGER' && !/^\d+$/.test(a.attributeValue.trim())))) return 'An integer attribute has a non-numeric value';
    if (catalogue.length && draft.some(g => g.attributes.some(a => !known.has(a.attributeName)))) return "An attribute isn't one R1 knows (R1 would accept it and return nothing)";
    return null;
  }, [draft, catalogue, known]);

  const finishEdit = async () => {
    if (!draft || draftProblem) return;
    const source = current?.source ? current.source.replace(/ \(edited\)$/, '') : null;
    setSaving(true);
    setError(null);
    try {
      await store(saveName.trim(), draft.map(g => ({ ...g, name: g.name.trim() })), source ? `${source} (edited)` : null, null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const editGroup = (i: number, patch: Partial<Group>) => setDraft(d => d && d.map((g, x) => (x === i ? { ...g, ...patch } : g)));
  const editAttribute = (gi: number, ai: number, patch: Partial<Attribute>) =>
    setDraft(d => d && d.map((g, x) => (x === gi
      ? { ...g, attributes: g.attributes.map((a, y) => (y === ai ? { ...a, ...patch } : a)) }
      : g)));

  const deleteCurrent = async () => {
    if (!current || !window.confirm(`Delete the template "${current.name}"? Nothing changes in R1.`)) return;
    try {
      await apiDelete(`${API_URL}/radius-standards/profiles/${current.id}`);
      setProfiles(ps => (ps || []).filter(x => x.id !== current.id));
      choose(null);
      setTab('template');
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── Grid shape ──

  const summaries = analysis?.consistency.groups || [];
  const columns = [
    ...templateNames,
    ...summaries.map(g => g.name).filter(n => !templateNames.includes(n)),
  ];
  const majorityOf = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const g of summaries) {
      if (g.common && !g.tied) map.set(g.name, new Set(g.variants.find(v => v.majority)?.tenant_ids || []));
    }
    return map;
  }, [summaries]);

  const readCount = tenants.filter(t => reads[t.id]?.groups || reads[t.id]?.error).length;
  const ranked = analysis?.consistency.tenants || [];
  const scored = analysis?.consistency.scored_groups || 0;
  const matching = tenants.filter(t => {
    const c = analysis?.comparisons[t.id];
    return c && c.groups.every(g => g.status === 'match');
  }).length;

  const tabClass = (t: Tab) => `px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
    tab === t ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-700 disabled:text-gray-300'}`;

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2"><Gauge size={24} /> RADIUS Standard</h1>
        <p className="text-gray-600 mt-1 text-sm">
          Keep a customer's RADIUS attribute groups the same across their tenants
        </p>
      </div>

      <div className="flex border-b border-gray-200 mb-6">
        <button className={tabClass('compare')} disabled={!current} onClick={() => setTab('compare')}
          title={current ? undefined : 'Choose a template first'}>
          Compare to template
        </button>
        <button className={tabClass('template')} onClick={() => setTab('template')}>
          {current ? `Template: ${current.name}` : 'Choose template'}
        </button>
      </div>

      {error && <div className="mb-4 text-sm text-red-600 flex items-center gap-1"><AlertCircle size={14} /> {error}</div>}

      {tab === 'compare' && template && (
        <>
          {!target ? (
            <Blocked>
              Select an MSP-EC controller in the toolbar to compare. An MSP can't be the target: writes only go through a tenant's own controller.
            </Blocked>
          ) : (
            <>
              <div className="bg-white border border-gray-200 rounded-lg p-3 mb-4 flex flex-wrap items-center gap-3 text-sm">
                <span className="text-gray-600">Target tenant: <strong className="text-gray-800">{target.name}</strong></span>
                <button
                  onClick={loadTarget}
                  disabled={targetRead.loading}
                  className="px-3 py-1 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1.5 text-gray-700"
                >
                  {targetRead.loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Re-read
                </button>
                {twoTargets && <span className="text-xs text-gray-500">Two tenant controllers selected: using the active one.</span>}
              </div>

              {targetRead.error && <Blocked>{target.name} could not be read: {targetRead.error}</Blocked>}
              {targetRead.loading && <p className="text-sm text-gray-500 flex items-center gap-2 mb-4"><Loader2 size={14} className="animate-spin" /> Reading {target.name}…</p>}
              {comparison && !targetRead.loading && (
                <div className="bg-white border border-gray-200 rounded-lg p-4">
                  <PushPanel
                    key={`${target.id}-${current?.updated_at}`}
                    tenant={{ id: target.r1_tenant_id!, name: target.name, controllerId: target.id }}
                    isSource={template.sourceTenantId === target.r1_tenant_id}
                    template={template}
                    comparison={comparison}
                    writer={target}
                    onPushed={groups => {
                      if (!groups) { loadTarget(); return; }
                      setTargetRead({ groups });
                      compareGroups(groups, targetSeq.current).catch(e => setError(e.message));
                    }}
                  />
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'template' && (
        <>
          {/* ── The template ── */}
          <div className="bg-white border-2 border-indigo-200 rounded-lg p-4 mb-6">
            <div className="flex flex-wrap items-center gap-3 mb-3 text-sm">
              <label className="flex items-center gap-2 text-gray-600">
                Customer
                <select
                  value={current?.id ?? ''}
                  onChange={e => choose((profiles || []).find(p => p.id === Number(e.target.value)) || null)}
                  className="border border-gray-300 rounded px-2 py-1 font-medium text-gray-800"
                >
                  <option value="">New customer…</option>
                  {(profiles || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              {!current && (
                <input
                  value={saveName}
                  onChange={e => setSaveName(e.target.value)}
                  placeholder="Customer name"
                  className="border border-gray-300 rounded px-2 py-1 w-52"
                />
              )}
              {current && (
                <span className="text-gray-600">
                  Taken from <strong>{current.source || 'values entered by hand'}</strong>
                  {current.updated_at && <span className="text-gray-400"> · {new Date(current.updated_at + 'Z').toLocaleDateString()}</span>}
                </span>
              )}
              {!draft && (
                <button
                  onClick={() => setDraft(current?.groups ?? [])}
                  className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50 flex items-center gap-1 text-gray-700"
                >
                  <Pencil size={12} /> {current ? 'Edit values' : 'Build by hand'}
                </button>
              )}
              {current && !draft && (
                <button onClick={deleteCurrent} className="ml-auto text-gray-400 hover:text-red-600" title="Delete this template">
                  <Trash2 size={14} />
                </button>
              )}
            </div>

            <datalist id="radius-attribute-names">
              {catalogue.map(a => <option key={a.name} value={a.name}>{a.vendorName}</option>)}
            </datalist>

            {draft ? (
              <div>
                {draft.map((g, gi) => (
                  <div key={gi} className="border border-gray-200 rounded mb-3">
                    <div className="flex items-center gap-2 p-2 bg-gray-50 border-b border-gray-200">
                      <input
                        value={g.name}
                        onChange={e => editGroup(gi, { name: e.target.value })}
                        placeholder="Group name (exact, case-sensitive)"
                        className="border border-gray-300 rounded px-2 py-1 text-sm font-medium w-72"
                      />
                      <button
                        onClick={() => editGroup(gi, { attributes: [...g.attributes, blankAttribute()] })}
                        className="text-xs text-indigo-700 hover:underline flex items-center gap-1"
                      >
                        <Plus size={12} /> Attribute
                      </button>
                      <button onClick={() => setDraft(draft.filter((_, x) => x !== gi))} className="ml-auto text-gray-400 hover:text-red-600" title="Remove group from the template">
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <table className="w-full text-sm">
                      <tbody>
                        {g.attributes.map((a, ai) => {
                          const unknown = catalogue.length > 0 && !known.has(a.attributeName);
                          return (
                            <tr key={ai} className="border-t border-gray-100 first:border-t-0">
                              <td className="p-1.5 w-96">
                                <input
                                  list="radius-attribute-names"
                                  value={a.attributeName}
                                  onChange={e => editAttribute(gi, ai, {
                                    attributeName: e.target.value,
                                    dataType: known.get(e.target.value)?.dataType || a.dataType,
                                  })}
                                  className={`border rounded px-2 py-1 w-full font-mono text-xs ${unknown ? 'border-red-400' : 'border-gray-300'}`}
                                />
                              </td>
                              <td className="p-1.5 w-36">
                                <select
                                  value={a.operator}
                                  onChange={e => editAttribute(gi, ai, { operator: e.target.value })}
                                  className="border border-gray-300 rounded px-1 py-1 text-xs w-full"
                                >
                                  {OPERATORS.map(o => <option key={o}>{o}</option>)}
                                </select>
                              </td>
                              <td className="p-1.5 w-52">
                                <input
                                  value={a.attributeValue}
                                  onChange={e => editAttribute(gi, ai, { attributeValue: e.target.value })}
                                  className="border border-gray-300 rounded px-2 py-1 w-full font-mono text-xs"
                                />
                              </td>
                              <td className="p-1.5 text-xs text-gray-500">{speed(a.attributeName, a.attributeValue) || a.dataType}</td>
                              <td className="p-1.5 w-8 text-right">
                                <button
                                  onClick={() => editGroup(gi, { attributes: g.attributes.filter((_, x) => x !== ai) })}
                                  className="text-gray-400 hover:text-red-600"
                                  title="Remove attribute"
                                >
                                  <X size={14} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ))}
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <button
                    onClick={() => setDraft([...draft, {
                      name: '',
                      attributes: [blankAttribute(), { ...blankAttribute(), attributeName: 'WISPr-Bandwidth-Max-Up' }],
                    }])}
                    className="px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50 flex items-center gap-1"
                  >
                    <Plus size={14} /> Add group
                  </button>
                  <button
                    onClick={finishEdit}
                    disabled={!!draftProblem || saving || !saveName.trim()}
                    className="px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 font-medium flex items-center gap-2"
                  >
                    {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Save template
                  </button>
                  <button onClick={() => setDraft(null)} className="px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
                  {draftProblem && <span className="text-amber-700">{draftProblem}</span>}
                </div>
              </div>
            ) : current ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                      <th className="p-2">Group</th><th className="p-2">Attribute</th><th className="p-2">Value</th><th className="p-2">Operator</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.groups.map(g => g.attributes.map((a, i) => (
                      <tr key={`${g.name}-${a.attributeName}-${i}`} className={i === 0 ? 'border-t border-gray-200' : ''}>
                        <td className="p-2 font-medium text-gray-800">{i === 0 ? g.name : ''}</td>
                        <td className="p-2 font-mono text-xs">{a.attributeName}</td>
                        <td className="p-2">
                          <span className="font-mono">{a.attributeValue}</span>
                          {speed(a.attributeName, a.attributeValue) && <span className="ml-2 text-gray-500">{speed(a.attributeName, a.attributeValue)}</span>}
                        </td>
                        <td className="p-2 text-xs text-gray-500">{a.operator}</td>
                      </tr>
                    )))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-gray-500">No template yet. Read the tenants below and use one of them, or pick a saved customer above.</p>
            )}
          </div>

          {/* ── Read through the MSP ── */}
          {!msp ? (
            <Blocked>Select an MSP controller to read every tenant. Choosing a template needs one; only an MSP can see them all.</Blocked>
          ) : (
            <>
              <div className="bg-white border border-gray-200 rounded-lg p-3 mb-6 flex flex-wrap items-center gap-3 text-sm">
                <button
                  onClick={readAll}
                  disabled={reading || !tenants.length}
                  className="px-4 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 font-medium flex items-center gap-2"
                >
                  {reading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                  {reading ? `Reading ${readCount}/${tenants.length}` : `Read all ${tenants.length} tenant${tenants.length === 1 ? '' : 's'}`}
                </button>
                <span className="text-gray-600 flex items-center gap-1.5">
                  <Eye size={14} /> Read-only through <strong>{msp.name}</strong>
                </span>
                {twoMsps && <span className="text-xs text-gray-500">Two MSPs selected: using the active one.</span>}
              </div>

              {analysis && (
                <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
                  <h2 className="font-semibold text-gray-800 flex items-center gap-2 mb-1"><Trophy size={16} className="text-amber-500" /> Most consistent tenants</h2>
                  {scored === 0 ? (
                    <p className="text-sm text-gray-500">No group is shared by enough tenants with a clear majority value to rank them. Pick a template from the grid below.</p>
                  ) : (
                    <>
                      <p className="text-xs text-gray-500 mb-3">
                        Scored on the {scored} group{scored === 1 ? '' : 's'} at least half the tenants share and mostly agree on. A tenant scores for holding the most common values.
                      </p>
                      <div className="space-y-1">
                        {ranked.slice(0, showAllRanked ? undefined : RANKED_SHOWN).map(r => (
                          <div key={r.id} className={`flex flex-wrap items-center gap-3 p-2 rounded text-sm ${template?.sourceTenantId === r.id ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}>
                            <span className="w-48 font-medium text-gray-800 truncate" title={r.name}>{r.name}</span>
                            <span className="w-24 h-2 rounded bg-gray-100 overflow-hidden">
                              <span className="block h-full bg-green-500" style={{ width: `${(r.score || 0) * 100}%` }} />
                            </span>
                            <span className="w-14 text-gray-600 tabular-nums">{r.agrees.length}/{scored}</span>
                            <span className="flex-1 min-w-48 text-xs text-gray-500">
                              {r.differs.length > 0 && <span className="text-amber-700">differs: {r.differs.join(', ')} </span>}
                              {r.lacks.length > 0 && <span className="text-red-700">lacks: {r.lacks.join(', ')} </span>}
                              {r.other_groups.length > 0 && <span title={r.other_groups.join(', ')}>+{r.other_groups.length} other</span>}
                              {!r.differs.length && !r.lacks.length && !r.other_groups.length && 'Agrees on everything'}
                            </span>
                            {template?.sourceTenantId === r.id ? (
                              <span className="text-xs font-medium text-indigo-700 px-2">Template</span>
                            ) : (
                              <button
                                onClick={() => setPicking(r)}
                                className="px-3 py-1 text-xs rounded border border-indigo-300 text-indigo-700 hover:bg-indigo-50 font-medium"
                              >
                                Use as template
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                      {ranked.length > RANKED_SHOWN && (
                        <button onClick={() => setShowAllRanked(v => !v)} className="mt-2 text-xs text-indigo-700 hover:underline">
                          {showAllRanked ? 'Show fewer' : `Show all ${ranked.length}`}
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}

              {Object.keys(reads).length > 0 && (
                <div className="bg-white border border-gray-200 rounded-lg">
                  <div className="flex flex-wrap items-center gap-4 p-3 border-b border-gray-100 text-sm">
                    <h2 className="font-semibold text-gray-800">Every tenant</h2>
                    {current && analysis ? (
                      <span className="text-gray-600">{matching} of {tenants.length} match the template</span>
                    ) : (
                      <span className="text-xs text-gray-500">Amber = differs from what most tenants have.</span>
                    )}
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-left text-gray-500 border-b border-gray-200">
                          <th className="p-2 sticky left-0 bg-white z-10 min-w-48">Tenant</th>
                          {columns.map(n => (
                            <th key={n} className={`p-2 whitespace-nowrap ${templateNames.includes(n) ? 'text-indigo-700' : ''}`}>{n}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {tenants.map(t => {
                          const open = expanded === t.id;
                          return (
                            <TenantRows
                              key={t.id}
                              tenant={t}
                              read={reads[t.id]}
                              open={open}
                              isSource={template?.sourceTenantId === t.id}
                              columns={columns}
                              majorityOf={majorityOf}
                              comparison={analysis?.comparisons[t.id]}
                              hasTemplate={!!template}
                              onToggle={() => setExpanded(open ? null : t.id)}
                              onPick={() => setPicking(t)}
                              readOnly={!!current && !ecControllers.some(c => c.r1_tenant_id === t.id)}
                            />
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      {picking && (
        <PickDialog
          tenantName={picking.name}
          groups={toTemplateGroups(reads[picking.id]?.groups || [])}
          customers={(profiles || []).map(p => p.name)}
          initial={current?.name ?? ''}
          onCancel={() => setPicking(null)}
          onSave={pickTenant}
        />
      )}
    </div>
  );
}

// Which customer this tenant's values become the template for. A failed save
// is shown here, not at the top of a page scrolled well past it.
function PickDialog({ tenantName, groups, customers, initial, onCancel, onSave }: {
  tenantName: string;
  groups: Group[];
  customers: string[];
  initial: string;
  onCancel: () => void;
  onSave: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const trimmed = name.trim();
  const replacing = customers.includes(trimmed);

  const save = async () => {
    setSaving(true);
    setProblem(null);
    try {
      await onSave(trimmed);
    } catch (e: any) {
      setProblem(e.message);
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-800">Use {tenantName} as the template</h2>
          <p className="text-sm text-gray-500 mt-1">
            Saves its {groups.length} group{groups.length === 1 ? '' : 's'} here. Nothing changes in R1.
          </p>
        </div>
        <div className="p-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">Customer</span>
            <input
              autoFocus
              list="radius-customers"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Customer name"
              className="border border-gray-300 rounded px-2 py-1.5"
            />
            <datalist id="radius-customers">
              {customers.map(c => <option key={c} value={c} />)}
            </datalist>
          </label>
          {replacing && <p className="mt-2 text-xs text-amber-700">Replaces the saved "{trimmed}" template.</p>}
          {!groups.length && <p className="mt-2 text-xs text-red-700">{tenantName} has no groups to use.</p>}
          {problem && <p className="mt-2 text-sm text-red-600 flex items-center gap-1"><AlertCircle size={14} /> {problem}</p>}
        </div>
        <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
          <button onClick={onCancel} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
          <button
            onClick={save}
            disabled={!trimmed || !groups.length || saving}
            className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 font-medium flex items-center gap-2"
          >
            {saving && <Loader2 size={14} className="animate-spin" />} {replacing ? 'Replace template' : 'Save template'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Tenant row in the MSP overview. Read-only: writes live on the Compare tab. ──

function TenantRows({
  tenant, read, open, isSource, columns, majorityOf, comparison, hasTemplate, onToggle, onPick, readOnly,
}: {
  tenant: Tenant;
  read?: TenantRead;
  open: boolean;
  isSource: boolean;
  columns: string[];
  majorityOf: Map<string, Set<string>>;
  comparison?: Comparison;
  hasTemplate: boolean;
  onToggle: () => void;
  onPick: () => void;
  readOnly: boolean;
}) {
  const groups = read?.groups;

  const cell = (name: string) => {
    if (!groups) return <td key={name} className="p-2 text-gray-300">·</td>;
    const found = groups.find(g => g.name === name);
    const status = comparison?.groups.find(g => g.name === name)?.status;
    let tone = 'text-gray-700';
    if (status === 'match') tone = 'bg-green-50 text-green-800';
    else if (status === 'differs') tone = 'bg-amber-100 text-amber-900 font-medium';
    else if (status === 'missing') tone = 'bg-red-50 text-red-700';
    else if (!hasTemplate && found && majorityOf.has(name) && !majorityOf.get(name)!.has(tenant.id)) tone = 'bg-amber-100 text-amber-900 font-medium';
    else if (hasTemplate && found) tone = 'text-gray-400';
    if (!found) {
      return <td key={name} className={`p-2 whitespace-nowrap ${tone}`}>{status === 'missing' ? 'missing' : <span className="text-gray-300">—</span>}</td>;
    }
    const attrs = found.attributeAssignments || [];
    return (
      <td key={name} className={`p-2 whitespace-nowrap align-top ${tone}`} title={allAttributes(attrs)}>
        {attrs.map((a, i) => <div key={i}>{cellLine(a)}</div>)}
      </td>
    );
  };

  return (
    <>
      <tr className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer" onClick={() => groups && onToggle()}>
        <td className="p-2 sticky left-0 bg-white z-10 font-medium text-gray-800 text-sm align-top">
          <span className="flex items-center gap-1">
            {groups ? (open ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />) : read?.loading ? <Loader2 size={14} className="animate-spin text-gray-400" /> : null}
            {tenant.name}
            {isSource && <span className="ml-1 text-xs px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-800 font-normal">template</span>}
          </span>
          {read?.error && <div className="text-xs font-normal text-red-600 max-w-64 whitespace-normal">{read.error}</div>}
        </td>
        {columns.map(cell)}
      </tr>
      {open && groups && (
        <tr className="border-b border-gray-200 bg-gray-50">
          <td colSpan={columns.length + 1} className="p-3 sticky left-0">
            <div className="flex flex-wrap items-center gap-2">
              {!isSource && (
                <button
                  onClick={onPick}
                  className="px-3 py-1.5 text-xs rounded border border-indigo-300 bg-white text-indigo-700 hover:bg-indigo-50 font-medium"
                >
                  Use {tenant.name} as the template
                </button>
              )}
              {readOnly && !isSource && (
                <span className="text-xs text-amber-700">You have no controller for {tenant.name}, so it can only be read.</span>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// The write, spelled out: from where, to where, through which key.
function Direction({ template, tenant, writerName }: { template: Template; tenant: Tenant; writerName: string }) {
  return (
    <div className="flex flex-wrap items-stretch gap-2 text-sm">
      <div className="flex-1 min-w-48 rounded border border-gray-300 bg-white p-3">
        <div className="text-xs uppercase tracking-wide text-gray-500">From — not changed</div>
        <div className="font-semibold text-gray-800">Template</div>
        <div className="text-xs text-gray-600">{template.source}</div>
      </div>
      <div className="flex flex-col items-center justify-center px-2 text-indigo-700">
        <ArrowRight size={24} />
        <span className="text-xs font-semibold">WRITES TO</span>
      </div>
      <div className="flex-1 min-w-48 rounded border-2 border-indigo-500 bg-indigo-50 p-3">
        <div className="text-xs uppercase tracking-wide text-indigo-700">To — this tenant changes</div>
        <div className="font-semibold text-indigo-900">{tenant.name}</div>
        <div className="text-xs text-indigo-800">through {writerName}</div>
      </div>
    </div>
  );
}

function PushPanel({ tenant, isSource, template, comparison, writer, onPushed }: {
  tenant: Tenant;
  isSource: boolean;
  template: Template;
  comparison: Comparison;
  writer: { id: number; name: string };
  onPushed: (groups: R1Group[] | null) => void;
}) {
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [results, setResults] = useState<ApplyResult[] | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const changes = comparison.groups.filter(g => g.status !== 'match');
  const same = comparison.groups.filter(g => g.status === 'match');
  const present = comparison.groups.filter(g => g.status !== 'missing');
  const chosen = changes.filter(g => !picked || picked.has(g.name));

  const toggle = (name: string) => {
    const next = new Set(picked ?? changes.map(g => g.name));
    next.has(name) ? next.delete(name) : next.add(name);
    setPicked(next);
  };

  const push = async () => {
    setConfirming(false);
    setApplying(true);
    setApplyError(null);
    try {
      const data = await apiPost<{ results: ApplyResult[]; groups: R1Group[] | null }>(`${API_URL}/radius-standards/apply`, {
        r1_controller_id: writer.id, tenant_id: tenant.id, template: template.groups, group_names: chosen.map(g => g.name),
      });
      setResults(data.results);
      setPicked(null);
      onPushed(data.groups);
    } catch (e: any) {
      setApplyError(e.message);
    } finally {
      setApplying(false);
    }
  };

  const changeList = (groups: GroupCompare[], selectable: boolean) => (
    <table className="w-full text-sm bg-white border border-gray-200 rounded">
      <thead>
        <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
          {selectable && <th className="p-2 w-6" />}
          <th className="p-2">Group</th>
          <th className="p-2">Attribute</th>
          <th className="p-2">Now on {tenant.name}</th>
          <th className="p-2 w-6" />
          <th className="p-2">After push (template)</th>
        </tr>
      </thead>
      <tbody>
        {groups.map(g => g.attributes.filter(r => !r.same).map((r, i) => (
          <tr key={`${g.name}-${r.attributeName}`} className="border-t border-gray-100">
            {selectable && (
              <td className="p-2 align-top">
                {i === 0 && <input type="checkbox" checked={!picked || picked.has(g.name)} onChange={() => toggle(g.name)} />}
              </td>
            )}
            <td className="p-2 align-top font-medium">
              {i === 0 && (
                <>
                  {g.name}
                  <span className={`ml-2 text-xs px-1.5 py-0.5 rounded font-normal ${g.status === 'missing' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}`}>
                    {g.status === 'missing' ? 'will be created' : 'will be updated'}
                  </span>
                  {g.similar_name && <div className="text-xs font-normal text-amber-700">Tenant has "{g.similar_name}" — names are case-sensitive, so this adds a second group</div>}
                  {g.in_use > 0 && <div className="text-xs font-normal text-gray-500">In use: {g.in_use} link{g.in_use === 1 ? '' : 's'}</div>}
                </>
              )}
            </td>
            <td className="p-2 align-top font-mono text-xs">{r.attributeName}</td>
            <td className="p-2 align-top text-gray-700"><ValueList items={r.actual} attr={r.attributeName} /></td>
            <td className="p-2 align-top text-indigo-600"><ArrowRight size={14} /></td>
            <td className="p-2 align-top text-indigo-800 font-medium">
              {r.expected.length ? <ValueList items={r.expected} attr={r.attributeName} /> : <span className="italic">removed</span>}
            </td>
          </tr>
        )))}
      </tbody>
    </table>
  );

  return (
    <div className="space-y-3">
      <Direction template={template} tenant={tenant} writerName={writer.name} />

      {isSource ? (
        <p className="text-sm text-indigo-800">This tenant is where the template came from, so there is nothing to push here.</p>
      ) : changes.length === 0 ? (
        <p className="text-sm text-green-700 font-medium">{tenant.name} already matches the template on all {same.length} group{same.length === 1 ? '' : 's'}.</p>
      ) : (
        <>
          <h3 className="text-sm font-semibold text-gray-800">
            {changes.length} group{changes.length === 1 ? '' : 's'} would change on {tenant.name}
          </h3>
          {changeList(changes, true)}
        </>
      )}

      {/* Everything the tenant has now, including what the template doesn't mention. */}
      <div>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">
          Everything on {tenant.name} now ({present.length + comparison.extras.length} group{present.length + comparison.extras.length === 1 ? '' : 's'})
        </h3>
        {comparison.extras.length > 0 && (
          <p className="text-xs text-gray-500 mb-2">
            {comparison.extras.length} {comparison.extras.length === 1 ? "isn't" : "aren't"} in the template. Writing never changes or deletes {comparison.extras.length === 1 ? 'it' : 'them'}.
          </p>
        )}
        <table className="w-full text-sm bg-white border border-gray-200 rounded">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
              <th className="p-2">Group</th>
              <th className="p-2">Compared to template</th>
              <th className="p-2">Attribute</th>
              <th className="p-2">Value</th>
            </tr>
          </thead>
          <tbody>
            {comparison.extras.map(x => x.attributes.map((a, i) => (
              <tr key={`extra-${x.name}-${i}`} className="border-t border-gray-100 bg-blue-50/50">
                <td className="p-2 align-top font-medium">{i === 0 && x.name}</td>
                <td className="p-2 align-top text-xs">
                  {i === 0 && (
                    <>
                      <span className="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800">only on this tenant, left alone</span>
                      {x.in_use > 0 && <div className="mt-1 text-gray-500">In use: {x.in_use} link{x.in_use === 1 ? '' : 's'}</div>}
                    </>
                  )}
                </td>
                <td className="p-2 align-top font-mono text-xs">{a.attributeName}</td>
                <td className="p-2 align-top"><ValueList items={[{ operator: a.operator, value: a.attributeValue }]} attr={a.attributeName} /></td>
              </tr>
            )))}
            {present.map(g => g.attributes.filter(r => r.actual.length).map((r, i) => (
              <tr key={`${g.name}-${r.attributeName}`} className="border-t border-gray-100">
                <td className="p-2 align-top font-medium">{i === 0 && g.name}</td>
                <td className="p-2 align-top text-xs">
                  {i === 0 && (
                    <span className={`px-1.5 py-0.5 rounded ${g.status === 'match' ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'}`}>
                      {g.status === 'match' ? 'matches' : 'differs'}
                    </span>
                  )}
                </td>
                <td className="p-2 align-top font-mono text-xs">{r.attributeName}</td>
                <td className="p-2 align-top"><ValueList items={r.actual} attr={r.attributeName} /></td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>

      {applyError && <div className="text-sm text-red-600 flex items-center gap-1"><AlertCircle size={14} /> {applyError}</div>}
      {results && (
        <div className="text-sm bg-white border border-gray-200 rounded p-2">
          {results.map(r => (
            <div key={r.name} className={r.status === 'failed' || r.status === 'unverified' ? 'text-red-700' : 'text-green-700'}>
              {r.name}: {r.status}{r.error ? ` — ${r.error}` : ''}
            </div>
          ))}
        </div>
      )}

      {!isSource && changes.length > 0 && (
        <button
          onClick={() => setConfirming(true)}
          disabled={!chosen.length || applying}
          className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
        >
          {applying ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
          Write {chosen.length} group{chosen.length === 1 ? '' : 's'} to {tenant.name}
        </button>
      )}

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setConfirming(false)}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl" onClick={e => e.stopPropagation()}>
            <div className="p-4 border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-800 mb-3">Write to {tenant.name}?</h2>
              <Direction template={template} tenant={tenant} writerName={writer.name} />
            </div>
            <div className="p-4 max-h-80 overflow-y-auto">{changeList(chosen, false)}</div>
            {comparison.extras.length > 0 && (
              <p className="mx-4 mb-2 text-xs text-gray-500">Not touched: {comparison.extras.map(e => e.name).join(', ')}</p>
            )}
            {chosen.some(g => g.in_use > 0) && (
              <div className="mx-4 mb-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                In use: {chosen.filter(g => g.in_use > 0).map(g => g.name).join(', ')}. Every policy pointing at these groups gets the new values.
              </div>
            )}
            <div className="flex justify-end gap-2 p-4 border-t border-gray-100">
              <button onClick={() => setConfirming(false)} className="px-4 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50">Cancel</button>
              <button onClick={push} className="px-4 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 font-medium">
                Write {chosen.length} to {tenant.name}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
