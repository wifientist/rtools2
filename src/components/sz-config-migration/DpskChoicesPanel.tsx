import { Key, Users, Plus, Trash2, AlertTriangle, Info } from 'lucide-react';
import type { DpskChoices, DpskPoolSpec } from '@/types/szConfigMigration';

export interface DpskWlan {
  id: string;
  name: string;
  source: 'sz_dpsk' | 'converted';
}

interface Props {
  choices: DpskChoices;
  wlans: DpskWlan[];
  pools: Record<string, DpskPoolSpec>;
  assignments: Record<string, string>;
  onChange: (pools: Record<string, DpskPoolSpec>, assignments: Record<string, string>) => void;
  disabled?: boolean;
}

const FORMAT_LABELS: Record<string, string> = {
  KEYBOARD_FRIENDLY: 'Keyboard friendly (a-z, A-Z, 0-9)',
  MOST_SECURED: 'Most secured (incl. symbols)',
  NUMBERS_ONLY: 'Numbers only',
  DICTIONARY_WORDS: 'Dictionary words',
};

const input = 'border border-gray-300 rounded px-2 py-1 text-xs bg-white';

function poolLabel(key: string, spec: DpskPoolSpec, choices: DpskChoices): string {
  if (spec.mode === 'existing') {
    const p = choices.r1_pools.find(x => x.id === spec.existing_pool_id);
    return p ? `${p.name} (existing)` : 'Existing pool — not chosen';
  }
  return spec.name ? `${spec.name} (new)` : `New pool ${key}`;
}

export default function DpskChoicesPanel({ choices, wlans, pools, assignments, onChange, disabled }: Props) {
  if (wlans.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No WLAN will be a DPSK network in R1. This section can be skipped.
      </div>
    );
  }

  const [minLen, maxLen] = choices.limits.passphrase_length;
  const [, maxDev] = choices.limits.devices_per_passphrase;
  const keys = Object.keys(pools);
  // Groups already chosen by some new pool here: one group can serve one pool.
  const claimed = new Set(
    Object.values(pools)
      .filter(p => p.mode === 'new' && p.identity_group?.mode === 'existing')
      .map(p => p.identity_group?.id),
  );

  const setPool = (key: string, patch: Partial<DpskPoolSpec>) =>
    onChange({ ...pools, [key]: { ...pools[key], ...patch } }, assignments);

  const addPool = () => {
    let n = keys.length + 1;
    while (pools[`pool-${n}`]) n++;
    const d = choices.default_pool;
    onChange({
      ...pools,
      [`pool-${n}`]: {
        ...d,
        name: `${d.name}-${n}`,
        identity_group: { mode: 'new', name: `${d.identity_group?.name ?? 'identities'}-${n}` },
      },
    }, assignments);
  };

  const removePool = (key: string) => {
    const { [key]: _gone, ...rest } = pools;
    const fallback = Object.keys(rest)[0];
    const next = Object.fromEntries(
      Object.entries(assignments).map(([w, k]) => [w, k === key ? fallback : k]),
    );
    onChange(rest, next);
  };

  return (
    <div className="space-y-4">
      <div className="text-xs text-gray-600 flex items-start gap-1.5">
        <Info size={12} className="mt-0.5 flex-shrink-0 text-blue-500" />
        <span>
          Every DPSK network needs a DPSK service (pool) with an identity group. WLANs that serve
          the same people should share one. <strong>A new pool starts with no passphrases</strong> —
          import them afterwards (e.g. with the Cloudpath import) before anyone can join.
        </span>
      </div>
      {choices.error && (
        <div className="text-xs text-amber-700 flex items-start gap-1.5">
          <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" /> {choices.error}
        </div>
      )}

      {/* WLAN → pool */}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <div className="px-3 py-2 bg-gray-50 text-xs font-semibold text-gray-700">Which DPSK service each WLAN uses</div>
        <div className="divide-y divide-gray-100">
          {wlans.map(w => (
            <div key={w.id} className="px-3 py-2 flex items-center gap-3 text-xs flex-wrap">
              <span className="font-medium w-36 truncate" title={w.name}>{w.name}</span>
              <span className="text-gray-400 w-40">
                {w.source === 'converted' ? 'converted from RADIUS' : 'SZ DPSK'}
              </span>
              <select
                className={`${input} flex-1 min-w-[12rem] max-w-sm`}
                value={assignments[w.id] ?? ''}
                disabled={disabled}
                onChange={e => onChange(pools, { ...assignments, [w.id]: e.target.value })}
              >
                <option value="">Choose…</option>
                {keys.map(k => <option key={k} value={k}>{poolLabel(k, pools[k], choices)}</option>)}
              </select>
            </div>
          ))}
        </div>
      </div>

      {/* Pools */}
      {keys.map(key => {
        const spec = pools[key];
        const users = wlans.filter(w => assignments[w.id] === key).map(w => w.name);
        const existing = choices.r1_pools.find(p => p.id === spec.existing_pool_id);
        const ig = spec.identity_group ?? { mode: 'new' as const, name: '' };
        return (
          <div key={key} className="border border-orange-200 bg-orange-50 rounded-lg p-3 space-y-2.5">
            <div className="flex items-center gap-2">
              <Key size={14} className="text-orange-600" />
              <span className="font-semibold text-sm flex-1">{poolLabel(key, spec, choices)}</span>
              <span className="text-xs text-gray-500">
                {users.length ? `Used by ${users.join(', ')}` : 'Not used by any WLAN'}
              </span>
              {keys.length > 1 && (
                <button
                  type="button" disabled={disabled} onClick={() => removePool(key)}
                  className="text-gray-400 hover:text-red-600" title="Remove this DPSK service"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>

            <div className="flex items-center gap-4 text-xs pl-6">
              {(['new', 'existing'] as const).map(mode => (
                <label key={mode} className={`flex items-center gap-1.5 ${mode === 'existing' && !choices.r1_pools.length ? 'text-gray-400' : ''}`}>
                  <input
                    type="radio" name={`pool-mode-${key}`} checked={spec.mode === mode}
                    disabled={disabled || (mode === 'existing' && !choices.r1_pools.length)}
                    onChange={() => setPool(key, { mode })}
                  />
                  {mode === 'new' ? 'Create new pool' : 'Use existing R1 pool'}
                </label>
              ))}
            </div>

            {spec.mode === 'existing' ? (
              <div className="pl-6 space-y-1.5">
                <select
                  className={`${input} w-full max-w-md`}
                  value={spec.existing_pool_id ?? ''}
                  disabled={disabled}
                  onChange={e => setPool(key, { existing_pool_id: e.target.value || null })}
                >
                  <option value="">Choose an R1 pool…</option>
                  {choices.r1_pools.map(p => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {p.identity_groups.reduce((n, g) => n + (g.identity_count ?? 0), 0)} identities
                      {p.network_count ? `, on ${p.network_count} network(s)` : ''}
                    </option>
                  ))}
                </select>
                {existing && (
                  <div className="text-[11px] text-gray-600 space-y-0.5">
                    <div>
                      {existing.passphrase_length ?? '?'}-char {existing.passphrase_format ?? ''},{' '}
                      {existing.devices_per_passphrase ?? 'unlimited'} device(s)/passphrase
                    </div>
                    {existing.identity_groups.length === 0
                      ? <div className="text-red-600">No identity group — nobody can authenticate</div>
                      : existing.identity_groups.map(g => (
                        <div key={g.id} className="flex items-center gap-1"><Users size={10} /> {g.name} · {g.identity_count ?? '?'} identities</div>
                      ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="pl-6 grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-xs">
                <label className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Pool name</span>
                  <input className={input} value={spec.name ?? ''} disabled={disabled}
                    onChange={e => setPool(key, { name: e.target.value })} />
                </label>

                <div className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Identity group</span>
                  <div className="flex items-center gap-2">
                    <select
                      className={input}
                      value={ig.mode}
                      disabled={disabled}
                      onChange={e => setPool(key, {
                        identity_group: e.target.value === 'new'
                          ? { mode: 'new', name: `${spec.name || 'migrated'}-identities` }
                          : { mode: 'existing', id: null },
                      })}
                    >
                      <option value="new">New</option>
                      <option value="existing">Existing</option>
                    </select>
                    {ig.mode === 'new' ? (
                      <input className={`${input} flex-1`} value={ig.name ?? ''} disabled={disabled}
                        onChange={e => setPool(key, { identity_group: { mode: 'new', name: e.target.value } })} />
                    ) : (
                      <select
                        className={`${input} flex-1`}
                        value={ig.id ?? ''}
                        disabled={disabled}
                        onChange={e => setPool(key, { identity_group: { mode: 'existing', id: e.target.value || null } })}
                      >
                        <option value="">Choose a group…</option>
                        {choices.r1_identity_groups.map(g => {
                          const bound = !!g.dpsk_pool_id;
                          const taken = claimed.has(g.id) && g.id !== ig.id;
                          return (
                            <option key={g.id} value={g.id} disabled={bound || taken}>
                              {g.name} · {g.identity_count ?? 0} identities
                              {bound ? ' — already serves a pool' : taken ? ' — chosen for another pool' : ''}
                            </option>
                          );
                        })}
                      </select>
                    )}
                  </div>
                </div>

                <label className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Passphrase format</span>
                  <select className={input} value={spec.passphrase_format} disabled={disabled}
                    onChange={e => setPool(key, { passphrase_format: e.target.value })}>
                    {choices.formats.map(f => <option key={f} value={f}>{FORMAT_LABELS[f] ?? f}</option>)}
                  </select>
                </label>

                <label className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Passphrase length ({minLen}–{maxLen})</span>
                  <input type="number" min={minLen} max={maxLen} className={`${input} w-24`}
                    value={spec.passphrase_length} disabled={disabled}
                    onChange={e => setPool(key, { passphrase_length: parseInt(e.target.value, 10) || minLen })} />
                </label>

                <div className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Devices per passphrase</span>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1">
                      <input type="checkbox" checked={spec.devices_per_passphrase === null} disabled={disabled}
                        onChange={e => setPool(key, { devices_per_passphrase: e.target.checked ? null : 1 })} />
                      Unlimited
                    </label>
                    {spec.devices_per_passphrase !== null && (
                      <input type="number" min={1} max={maxDev} className={`${input} w-20`}
                        value={spec.devices_per_passphrase} disabled={disabled}
                        onChange={e => setPool(key, { devices_per_passphrase: parseInt(e.target.value, 10) || 1 })} />
                    )}
                  </div>
                </div>

                <div className="flex flex-col gap-0.5">
                  <span className="text-gray-600">Expiration</span>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1">
                      <input type="checkbox" checked={spec.expiration_days === null} disabled={disabled}
                        onChange={e => setPool(key, { expiration_days: e.target.checked ? null : 365 })} />
                      Never
                    </label>
                    {spec.expiration_days !== null && (
                      <>
                        <input type="number" min={1} className={`${input} w-20`}
                          value={spec.expiration_days} disabled={disabled}
                          onChange={e => setPool(key, { expiration_days: parseInt(e.target.value, 10) || 1 })} />
                        <span className="text-gray-500">days after creation</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}

      <button
        type="button" onClick={addPool} disabled={disabled}
        className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
      >
        <Plus size={12} /> Add another DPSK service
      </button>
    </div>
  );
}
