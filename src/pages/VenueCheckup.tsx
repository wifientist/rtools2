import { useState, useMemo } from 'react';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Target, Loader2, RefreshCw, Stethoscope, Trash2, PenLine, ChevronDown, ChevronRight, X, Check,
} from 'lucide-react';
import { apiPost } from '@/utils/api';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Types (api/routers/venue_checkup/router.py) ──────────────────

type Entry = Record<string, any> & { model: string };

interface Fetched { ok: boolean; data?: any; error?: string; status?: number }

interface ModelSetting extends Fetched { key: string; label: string; path: string; on_groups: boolean }
interface GroupSetting extends Fetched { ap_group_id: string; ap_group_name: string; key: string; label: string }
interface VenueSetting extends Fetched { label: string; path: string }

interface Ap {
  serial: string; name: string; model: string | null; status: string | null; firmware: string | null;
  ap_group_id: string | null; ap_group_name: string | null;
}

interface Checkup {
  venue_name: string;
  venue: any;
  aps: Ap[];
  models: { model: string; ap_count: number; firmware: string[] }[];
  groups: { id: string; name: string; is_default: boolean }[];
  model_settings: ModelSetting[];
  group_settings: GroupSetting[];
  venue_settings: VenueSetting[];
  catalog: string[];
  hidden_models: (Fetched & { model: string })[];
  probed_models: string[];
}

const POE_MODES = ['Auto', '802.3af', '802.3at', '802.3bt-Class_5', '802.3bt-Class_6', '802.3bt-Class_7', '802.3bt-Class_8'];

interface Outcome {
  status: 'done' | 'still_present' | 'unconfirmed' | 'failed';
  path: string; model: string; before: Entry | null; after: Entry | null; error?: string;
}

// One entry of one list, wherever it lives.
interface Slot { setting: ModelSetting | GroupSetting; entry: Entry; groupId: string | null; scope: string }

const entriesOf = (s: Fetched): Entry[] => (s.ok && Array.isArray(s.data) ? s.data : []);

const pretty = (v: any) => JSON.stringify(v, null, 2);

// The entry without its model key, on one line when short.
const brief = (e: Entry) => {
  const { model, ...rest } = e;
  const s = JSON.stringify(rest);
  return s.length <= 140 ? s : null;
};

// ── Page ─────────────────────────────────────────────────────────

export default function VenueCheckup() {
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
  const [data, setData] = useState<Checkup | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [onlyModel, setOnlyModel] = useState<string | null>(null);
  const [probeText, setProbeText] = useState('');
  const probeModels = probeText.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);

  const reset = () => { setData(null); setOutcome(null); setError(null); setOnlyModel(null); };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiPost<Checkup>(`${API_URL}/venue-checkup/read`, { ...venueBody, probe_models: probeModels }));
    } catch (e: any) {
      setError(e?.message || 'Failed to read the venue');
    } finally {
      setLoading(false);
    }
  };

  const change = async (slot: Slot, action: 'update' | 'delete', entry?: Entry) => {
    setBusy(true);
    setOutcome(null);
    try {
      const res = await apiPost<Outcome>(`${API_URL}/venue-checkup/model-setting`, {
        ...venueBody, setting: slot.setting.key, model: slot.entry.model, action,
        ap_group_id: slot.groupId, entry,
      });
      setOutcome(res);
      await load();
    } catch (e: any) {
      setOutcome({ status: 'failed', path: slot.setting.label, model: slot.entry.model,
        before: slot.entry, after: null, error: e?.message || 'Request failed' });
    } finally {
      setBusy(false);
    }
  };

  const setLanSpecific = async (model: string, poeMode: string | null) => {
    setBusy(true);
    setOutcome(null);
    try {
      const res = await apiPost<Outcome>(`${API_URL}/venue-checkup/model-lan-specific`, {
        ...venueBody, model, poe_mode: poeMode, poe_out: false,
      });
      setOutcome(res);
      await load();
    } catch (e: any) {
      setOutcome({ status: 'failed', path: `${model} PoE record`, model, before: null, after: null,
        error: e?.message || 'Request failed' });
    } finally {
      setBusy(false);
    }
  };

  // Every entry, grouped by model: venue lists first, then AP Group lists.
  const byModel = useMemo(() => {
    const out = new Map<string, Slot[]>();
    if (!data) return out;
    for (const m of data.models) out.set(m.model, []);
    const add = (slot: Slot) => {
      if (!out.has(slot.entry.model)) out.set(slot.entry.model, []);
      out.get(slot.entry.model)!.push(slot);
    };
    for (const s of data.model_settings)
      for (const e of entriesOf(s)) add({ setting: s, entry: e, groupId: null, scope: 'Venue' });
    for (const h of data.hidden_models) if (!out.has(h.model)) out.set(h.model, []);
    for (const s of data.group_settings)
      for (const e of entriesOf(s)) add({ setting: s, entry: e, groupId: s.ap_group_id, scope: `AP Group: ${s.ap_group_name}` });
    return new Map([...out.entries()].sort(([a], [b]) => a.localeCompare(b)));
  }, [data]);

  const apCount = (model: string) => data?.models.find(m => m.model === model)?.ap_count ?? 0;
  const failedReads = data
    ? [...data.model_settings, ...data.group_settings, ...data.venue_settings].filter(s => !s.ok)
    : [];

  return (
    <div className="p-4 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <Stethoscope size={24} className="text-indigo-600" />
          Venue Checkup
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          A venue's settings as R1 holds them, with the per-AP-model ones editable
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
              <input value={probeText} onChange={e => setProbeText(e.target.value)}
                placeholder="Extra models to probe (e.g. H375, R870)"
                className="px-2 py-2 text-sm border border-gray-300 rounded w-64" />
              <button
                onClick={load}
                disabled={!ready || loading || busy}
                className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {loading ? 'Reading the venue...' : data ? 'Re-read from R1' : 'Check venue'}
              </button>
              {error && (
                <span className="text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </span>
              )}
            </div>
          </div>

          {outcome && <OutcomePanel outcome={outcome} onClose={() => setOutcome(null)} />}

          {data && (
            <>
              {failedReads.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm text-amber-800">
                  <p className="font-medium mb-1">{failedReads.length} setting read(s) failed:</p>
                  <ul className="list-disc ml-5 text-xs space-y-0.5">
                    {failedReads.map((s, i) => (
                      <li key={i}>{'ap_group_name' in s ? `${s.ap_group_name} / ` : ''}{s.label}: {s.error}</li>
                    ))}
                  </ul>
                </div>
              )}

              <HiddenModels data={data} busy={busy} onSet={setLanSpecific} />

              <div className="bg-white border border-gray-200 rounded-lg mb-4">
                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-gray-700">
                    Per-AP-model settings — {data.venue_name}
                  </h3>
                  <span className="text-xs text-gray-500">
                    {data.aps.length} APs · {data.models.length} models · click a model to filter
                  </span>
                </div>
                <div className="px-4 py-2 flex flex-wrap gap-2 border-b border-gray-100">
                  {[...byModel.keys()].map(m => (
                    <button key={m}
                      onClick={() => setOnlyModel(onlyModel === m ? null : m)}
                      className={`px-2 py-1 rounded text-xs border ${onlyModel === m
                        ? 'bg-indigo-600 text-white border-indigo-600'
                        : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100'}`}>
                      {m}{data.catalog.length > 0 && !data.catalog.includes(m) && <span className="text-amber-600"> ⚑</span>}{' '}
                      <span className="opacity-70">({apCount(m)} AP · {byModel.get(m)!.length} set)</span>
                    </button>
                  ))}
                </div>
                <div className="divide-y divide-gray-100">
                  {[...byModel.entries()].filter(([m]) => !onlyModel || m === onlyModel).map(([model, slots]) => (
                    <ModelBlock key={model} model={model} slots={slots} data={data}
                      busy={busy} onChange={change} />
                  ))}
                </div>
              </div>

              <Collapsible title={`Venue AP settings (${data.venue_settings.length})`}>
                <div className="divide-y divide-gray-100">
                  {data.venue_settings.map(s => (
                    <Collapsible key={s.path} title={s.label} sub={s.path} nested
                      badge={s.ok ? undefined : 'read failed'}>
                      <pre className="text-xs bg-gray-50 p-3 rounded overflow-x-auto">{s.ok ? pretty(s.data) : s.error}</pre>
                    </Collapsible>
                  ))}
                </div>
              </Collapsible>

              <Collapsible title="Venue object">
                <pre className="text-xs bg-gray-50 p-3 rounded overflow-x-auto">{pretty(data.venue)}</pre>
              </Collapsible>
            </>
          )}
        </>
      )}
    </div>
  );
}

// ── Pieces ───────────────────────────────────────────────────────

// Models outside R1's catalog that still hold a record here. R1 refuses
// writes to them (WIFI-20067) unless it has capabilities for the model on
// the venue firmware; the buttons try anyway and show R1's answer.
function HiddenModels({ data, busy, onSet }: {
  data: Checkup; busy: boolean; onSet: (model: string, poeMode: string | null) => void;
}) {
  const [mode, setMode] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <div className={`border rounded-lg mb-4 ${data.hidden_models.length ? 'bg-amber-50 border-amber-200' : 'bg-white border-gray-200'}`}>
      <div className="px-4 py-3 text-sm">
        <p className="font-semibold text-gray-800">
          Hidden models: {data.hidden_models.length ? `${data.hidden_models.length} with a stored record` : 'none found'}
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          Not in R1's catalog for this venue ({data.catalog.length} models), so no list or UI page shows them.
          Probed by name: {data.probed_models.join(', ')}
        </p>
      </div>
      {data.hidden_models.map(h => (
        <div key={h.model} className="px-4 py-2 border-t border-amber-200 flex flex-wrap items-center gap-2 text-sm">
          <span className="font-semibold">{h.model}</span>
          <code className="text-xs bg-white px-2 py-1 rounded">{h.ok ? JSON.stringify(h.data) : `read failed: ${h.error}`}</code>
          <div className="ml-auto flex items-center gap-2">
            <select value={mode[h.model] ?? 'Auto'} onChange={e => setMode({ ...mode, [h.model]: e.target.value })}
              className="text-xs border border-gray-300 rounded px-1 py-1">
              {POE_MODES.map(m => <option key={m}>{m}</option>)}
            </select>
            <button disabled={busy} onClick={() => onSet(h.model, mode[h.model] ?? 'Auto')}
              className="px-2 py-1 text-xs rounded border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1">
              <PenLine size={12} /> Set PoE mode
            </button>
            {confirm === h.model ? (
              <>
                <span className="text-xs text-red-700">Clear {h.model}'s record?</span>
                <button disabled={busy} onClick={() => { setConfirm(null); onSet(h.model, null); }}
                  className="px-2 py-1 text-xs rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
                  {busy ? <Loader2 size={12} className="animate-spin" /> : 'Clear'}
                </button>
                <button onClick={() => setConfirm(null)} className="px-2 py-1 text-xs rounded border border-gray-200 bg-white">Cancel</button>
              </>
            ) : (
              <button disabled={busy} onClick={() => setConfirm(h.model)}
                className="px-2 py-1 text-xs rounded border border-red-200 text-red-600 bg-white hover:bg-red-50 disabled:opacity-50 flex items-center gap-1">
                <Trash2 size={12} /> Clear
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ModelBlock({ model, slots, data, busy, onChange }: {
  model: string; slots: Slot[]; data: Checkup; busy: boolean;
  onChange: (slot: Slot, action: 'update' | 'delete', entry?: Entry) => void;
}) {
  const [showAps, setShowAps] = useState(false);
  const info = data.models.find(m => m.model === model);
  const aps = data.aps.filter(a => (a.model || '?') === model);

  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-3 mb-2">
        <span className="font-semibold text-gray-800">{model}</span>
        {info ? (
          <button onClick={() => setShowAps(!showAps)} className="text-xs text-indigo-600 hover:underline">
            {info.ap_count} AP{info.ap_count === 1 ? '' : 's'}{info.firmware.length ? ` · fw ${info.firmware.join(', ')}` : ''}
          </button>
        ) : (
          <span className="text-xs text-amber-600">no AP of this model in the venue</span>
        )}
      </div>
      {showAps && (
        <table className="text-xs mb-3 ml-2">
          <tbody>
            {aps.map(a => (
              <tr key={a.serial} className="text-gray-600">
                <td className="pr-4">{a.name}</td><td className="pr-4 font-mono">{a.serial}</td>
                <td className="pr-4">{a.status}</td><td className="pr-4">{a.firmware}</td><td>{a.ap_group_name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {slots.length === 0 ? (
        <p className="text-xs text-gray-400 ml-2">No per-model entries — this model runs on defaults.</p>
      ) : (
        <div className="space-y-2">
          {slots.map((slot, i) => (
            <EntryRow key={`${slot.setting.key}-${slot.groupId}-${i}`} slot={slot} busy={busy} onChange={onChange} />
          ))}
        </div>
      )}
    </div>
  );
}

function EntryRow({ slot, busy, onChange }: {
  slot: Slot; busy: boolean;
  onChange: (slot: Slot, action: 'update' | 'delete', entry?: Entry) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [open, setOpen] = useState(false);
  const short = brief(slot.entry);

  const startEdit = () => { setText(pretty(slot.entry)); setParseError(null); setEditing(true); setOpen(true); };
  const save = () => {
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Must be a JSON object');
      if (parsed.model && parsed.model !== slot.entry.model) throw new Error('The model cannot change here');
      setEditing(false);
      onChange(slot, 'update', parsed);
    } catch (e: any) {
      setParseError(e.message);
    }
  };

  return (
    <div className="border border-gray-200 rounded p-2 ml-2">
      <div className="flex items-center gap-2 text-sm">
        <button onClick={() => setOpen(!open)} className="text-gray-400">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <span className="font-medium text-gray-700">{slot.setting.label}</span>
        <span className={`text-xs px-1.5 py-0.5 rounded ${slot.groupId ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600'}`}>
          {slot.scope}
        </span>
        {!open && short && <code className="text-xs text-gray-500 truncate">{short}</code>}
        <div className="ml-auto flex items-center gap-1">
          {!editing && !confirmDelete && (
            <>
              <button disabled={busy} onClick={startEdit}
                className="px-2 py-1 text-xs rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1">
                <PenLine size={12} /> Edit
              </button>
              <button disabled={busy} onClick={() => setConfirmDelete(true)}
                className="px-2 py-1 text-xs rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50 flex items-center gap-1">
                <Trash2 size={12} /> Delete
              </button>
            </>
          )}
          {confirmDelete && (
            <>
              <span className="text-xs text-red-700">Remove {slot.entry.model} from {slot.setting.label} ({slot.scope})?</span>
              <button disabled={busy} onClick={() => { setConfirmDelete(false); onChange(slot, 'delete'); }}
                className="px-2 py-1 text-xs rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
                {busy ? <Loader2 size={12} className="animate-spin" /> : 'Delete'}
              </button>
              <button onClick={() => setConfirmDelete(false)} className="px-2 py-1 text-xs rounded border border-gray-200">Cancel</button>
            </>
          )}
        </div>
      </div>
      {open && !editing && (
        <pre className="text-xs bg-gray-50 p-2 mt-2 rounded overflow-x-auto">{pretty(slot.entry)}</pre>
      )}
      {editing && (
        <div className="mt-2">
          <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false}
            rows={Math.min(30, text.split('\n').length + 1)}
            className="w-full font-mono text-xs border border-gray-300 rounded p-2" />
          {parseError && <p className="text-xs text-red-600 mt-1">{parseError}</p>}
          <p className="text-xs text-gray-500 mt-1">
            Saves the whole {slot.setting.label} list back to R1 with only this model's entry changed, then re-reads it.
          </p>
          <div className="flex gap-2 mt-2">
            <button disabled={busy} onClick={save}
              className="px-3 py-1 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-1">
              <Check size={12} /> Save to R1
            </button>
            <button onClick={() => setEditing(false)} className="px-3 py-1 text-xs rounded border border-gray-200">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function OutcomePanel({ outcome, onClose }: { outcome: Outcome; onClose: () => void }) {
  const tone = {
    done: 'bg-green-50 border-green-200 text-green-800',
    still_present: 'bg-amber-50 border-amber-200 text-amber-800',
    unconfirmed: 'bg-amber-50 border-amber-200 text-amber-800',
    failed: 'bg-red-50 border-red-200 text-red-800',
  }[outcome.status];
  const headline = {
    done: 'Done — R1 confirms the change.',
    still_present: 'R1 accepted the removal, but the record is still there.',
    unconfirmed: 'Written, but the read-back does not show it.',
    failed: 'Failed.',
  }[outcome.status];
  return (
    <div className={`border rounded-lg p-3 mb-4 text-sm ${tone}`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium">{outcome.model} · {outcome.path}</p>
          <p>{headline}{outcome.error ? ` ${outcome.error}` : ''}</p>
        </div>
        <button onClick={onClose}><X size={16} /></button>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><p className="text-xs font-medium">Before</p><pre className="text-xs bg-white/60 p-2 rounded overflow-x-auto">{pretty(outcome.before)}</pre></div>
        <div><p className="text-xs font-medium">After</p><pre className="text-xs bg-white/60 p-2 rounded overflow-x-auto">{pretty(outcome.after)}</pre></div>
      </div>
    </div>
  );
}

function Collapsible({ title, sub, badge, nested, children }: {
  title: string; sub?: string; badge?: string; nested?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={nested ? 'px-4 py-2' : 'bg-white border border-gray-200 rounded-lg mb-4'}>
      <button onClick={() => setOpen(!open)}
        className={`w-full flex items-center gap-2 text-left ${nested ? 'text-sm' : 'px-4 py-3 text-sm font-semibold text-gray-700'}`}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {title}
        {sub && <span className="text-xs text-gray-400 font-mono">{sub}</span>}
        {badge && <span className="text-xs text-red-600">{badge}</span>}
      </button>
      {open && <div className={nested ? 'mt-2' : 'px-4 pb-4'}>{children}</div>}
    </div>
  );
}
