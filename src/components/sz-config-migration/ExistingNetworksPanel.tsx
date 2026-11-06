import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type { NetworkCandidate, NetworkChoice, NetworkMatches, NetworkMatchWlan } from '@/types/szConfigMigration';

interface Props {
  matches: NetworkMatches;
  choices: Record<string, NetworkChoice>;
  onChange: (choices: Record<string, NetworkChoice>) => void;
  notMigrated: Set<string>;
  disabled?: boolean;
}

/** The choice in force for a WLAN: the operator's, else the suggestion. */
export function effectiveChoice(w: NetworkMatchWlan, choices: Record<string, NetworkChoice>): NetworkChoice {
  return choices[w.id] ?? w.suggestion;
}

const where = (c: NetworkCandidate) =>
  c.on_this_venue
    ? `on this venue${c.other_venues ? ` + ${c.other_venues} other` : ''}`
    : c.other_venues ? `shared · ${c.other_venues} other venue${c.other_venues === 1 ? '' : 's'}` : 'not activated anywhere';

const ppLabel: Record<string, string> = {
  match: 'passphrase = SZ',
  differs: 'passphrase ≠ SZ',
  unknown: "passphrase couldn't be read",
};

/** Worth a look: a shared network, a passphrase that isn't SZ's, or a second network with an SSID R1 already has. */
function needsLook(w: NetworkMatchWlan, choice: NetworkChoice): boolean {
  if (choice.action === 'create') return true;
  const c = w.candidates.find(x => x.network_id === choice.network_id);
  return !c || c.other_venues > 0 || !c.type_matches || (c.passphrase !== null && c.passphrase !== 'match');
}

export default function ExistingNetworksPanel({ matches, choices, onChange, notMigrated, disabled }: Props) {
  const [filter, setFilter] = useState('');
  const [onlyLook, setOnlyLook] = useState(true);

  const wlans = useMemo(() => matches.wlans.filter(w => !notMigrated.has(w.id)), [matches, notMigrated]);
  const lookCount = wlans.filter(w => needsLook(w, effectiveChoice(w, choices))).length;
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return wlans.filter(w =>
      (!onlyLook || needsLook(w, effectiveChoice(w, choices)))
      && (!q || `${w.name} ${w.ssid} ${w.candidates.map(c => c.name).join(' ')}`.toLowerCase().includes(q)));
  }, [wlans, filter, onlyLook, choices]);

  const set = (id: string, choice: NetworkChoice | null) => {
    const next = { ...choices };
    if (choice) next[id] = choice; else delete next[id];
    onChange(next);
  };

  if (wlans.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No R1 network has the SSID of any WLAN being migrated: every one is created new.
      </div>
    );
  }

  const reuse = wlans.filter(w => effectiveChoice(w, choices).action === 'reuse').length;
  const overridden = wlans.filter(w => choices[w.id]).length;
  // Reused PSK networks whose passphrase isn't SZ's: set SZ's or keep R1's, in bulk.
  const ppRows = wlans.flatMap(w => {
    const ch = effectiveChoice(w, choices);
    const c = ch.action === 'reuse' ? w.candidates.find(x => x.network_id === ch.network_id) : undefined;
    return c && w.r1_type === 'psk' && (c.type === 'psk' || c.type === null) && c.passphrase !== 'match'
      ? [{ w, ch, c }] : [];
  });
  const ppSz = ppRows.filter(r => r.ch.passphrase === 'sz').length;
  const setAllPp = (pp: 'sz' | 'keep') => {
    const next = { ...choices };
    for (const { w, ch, c } of ppRows) {
      if (pp === 'sz' && (!w.sz_has_passphrase || c.other_venues > 0)) continue;   // never other venues' in bulk
      next[w.id] = { action: 'reuse', network_id: c.network_id, passphrase: pp };
      if (ch.passphrase === pp && !choices[w.id]) delete next[w.id];               // already the suggestion
    }
    onChange(next);
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-600">
        These WLANs' SSIDs already exist as R1 networks. R1 networks are tenant-wide, so a WLAN reuses the existing
        network instead of getting a second one with the same SSID. The suggestion prefers a network already on
        this venue, then one not activated anywhere, then a shared one on other venues with the same type and
        passphrase. When a reused PSK network's passphrase isn't SZ's, choose whether to set SZ's on it or keep
        R1's. Passphrases are only compared, never shown.
      </p>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="text-gray-700">
          <b>{reuse}</b> reuse · <b>{wlans.length - reuse}</b> create new
          {overridden > 0 && <> · {overridden} changed from the suggestion</>}
        </span>
        <label className="flex items-center gap-1 text-gray-600">
          <input type="checkbox" checked={onlyLook} onChange={e => setOnlyLook(e.target.checked)} />
          Only the {lookCount} worth a look (shared, passphrase not SZ's, or a second network)
        </label>
        {ppRows.length > 0 && (
          <span className="flex items-center gap-1 text-amber-800">
            {ppRows.length} reused PSK network{ppRows.length === 1 ? '' : 's'} with a passphrase other than SZ's
            ({ppSz} set to SZ's):
            <button type="button" disabled={disabled} onClick={() => setAllPp('sz')}
                    title="Only networks no other venue uses"
                    className="px-2 py-0.5 border border-amber-300 rounded hover:bg-amber-50 disabled:opacity-50">
              Set SZ's on all
            </button>
            <button type="button" disabled={disabled} onClick={() => setAllPp('keep')}
                    className="px-2 py-0.5 border border-amber-300 rounded hover:bg-amber-50 disabled:opacity-50">
              Keep R1's on all
            </button>
          </span>
        )}
        {overridden > 0 && (
          <button type="button" disabled={disabled} onClick={() => onChange({})}
                  className="px-2 py-0.5 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
            Reset all to suggested
          </button>
        )}
        <div className="relative w-64 ml-auto">
          <Search size={13} className="absolute left-2 top-2 text-gray-400" />
          <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter by WLAN, SSID or R1 name"
                 className="pl-7 pr-2 py-1 text-xs border border-gray-300 rounded w-full" />
        </div>
      </div>

      <div className="border border-gray-200 rounded divide-y divide-gray-100 max-h-[32rem] overflow-y-auto">
        {shown.length === 0 && (
          <div className="p-3 text-xs text-gray-500 italic">
            {onlyLook ? 'Nothing needs a look — every WLAN reuses its own network on this venue with SZ\'s passphrase.' : 'No match.'}
          </div>
        )}
        {shown.map(w => {
          const choice = effectiveChoice(w, choices);
          const chosen = choice.action === 'reuse' ? w.candidates.find(c => c.network_id === choice.network_id) : undefined;
          const psk = w.r1_type === 'psk' && chosen && (chosen.type === 'psk' || chosen.type === null);
          const ppOff = psk && chosen!.passphrase !== 'match';
          const value = choice.action === 'create' ? 'create' : choice.network_id;
          return (
            <div key={w.id} className="px-3 py-2 text-xs flex flex-wrap items-center gap-x-3 gap-y-1">
              <div className="w-56 min-w-0">
                <div className="font-medium text-gray-800 truncate" title={w.name}>{w.name}</div>
                <div className="text-gray-500 truncate">SSID {w.ssid} · {w.r1_type?.toUpperCase()}</div>
              </div>
              <select
                value={value}
                disabled={disabled}
                onChange={e => {
                  const v = e.target.value;
                  if (v === 'create') return set(w.id, { action: 'create' });
                  const c = w.candidates.find(x => x.network_id === v)!;
                  const pskC = w.r1_type === 'psk' && (c.type === 'psk' || c.type === null) && c.passphrase !== 'match';
                  // Ours: SZ's passphrase by default; someone else's shared network: keep theirs unless told.
                  set(w.id, { action: 'reuse', network_id: v,
                              passphrase: pskC ? (c.on_this_venue || c.other_venues === 0) && w.sz_has_passphrase ? 'sz' : 'keep' : null });
                }}
                className="border border-gray-300 rounded px-1.5 py-1 max-w-[28rem]"
              >
                {w.candidates.map(c => (
                  <option key={c.network_id} value={c.network_id}>
                    Reuse '{c.name}' — {where(c)}
                    {!c.type_matches ? ` · R1 type ${c.type?.toUpperCase()}` : ''}
                    {c.passphrase ? ` · ${ppLabel[c.passphrase]}` : ''}
                  </option>
                ))}
                <option value="create">Create a new network (same SSID, second R1 network)</option>
              </select>
              {ppOff && (
                <select
                  value={choice.passphrase === 'sz' ? 'sz' : 'keep'}
                  disabled={disabled}
                  onChange={e => set(w.id, { ...choice, passphrase: e.target.value as 'sz' | 'keep' })}
                  className={`border rounded px-1.5 py-1 ${choice.passphrase === 'sz' ? 'border-gray-300' : 'border-amber-400 bg-amber-50'}`}
                >
                  {w.sz_has_passphrase && (
                    <option value="sz">
                      Set SZ's passphrase on it{chosen!.other_venues ? ` (changes it on ${chosen!.other_venues} other venue(s) too)` : ''}
                    </option>
                  )}
                  <option value="keep">Keep R1's passphrase (clients need R1's, not SZ's)</option>
                </select>
              )}
              <span className="text-gray-500">
                {choices[w.id]
                  ? <button type="button" disabled={disabled} onClick={() => set(w.id, null)}
                            className="underline hover:text-gray-700">back to suggested</button>
                  : <span title={w.suggestion.reason}>suggested: {w.suggestion.reason}</span>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
