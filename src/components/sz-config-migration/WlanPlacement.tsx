import { useMemo, useState } from 'react';
import { AlertTriangle, AlertCircle, Search } from 'lucide-react';
import type { R1Layout, LayoutChoices, LayoutGroup } from '@/types/szConfigMigration';

interface Props {
  layout: R1Layout;
  choices: LayoutChoices;
  onChange: (choices: LayoutChoices) => void;
  disabled?: boolean;
}

const TIER_LABELS: Record<string, string> = {
  'AP override': 'AP-level WLAN Group',
  'AP Group override': 'AP Group WLAN Group',
  'zone': 'Zone WLAN Group',
  'zone default': 'Zone "default" WLAN Group',
};

const pct = (n: number) => `${(n * 100).toFixed(n >= 0.995 || n < 0.01 ? 0 : 1)}%`;

export default function WlanPlacement({ layout, choices, onChange, disabled }: Props) {
  const [showAllWlans, setShowAllWlans] = useState(false);
  const [groupFilter, setGroupFilter] = useState('');
  const [groupView, setGroupView] = useState<'all' | 'pending' | 'create' | 'exists' | 'staged' | 'ambiguous'>('all');
  const s = layout.summary;

  const allAp = useMemo(() => new Set(
    choices.all_ap_wlans ?? layout.wlans.filter(w => w.suggested_all_ap).map(w => w.id),
  ), [choices.all_ap_wlans, layout.wlans]);

  const toggleAllAp = (id: string) => {
    const next = new Set(allAp);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange({ ...choices, all_ap_wlans: [...next] });
  };

  // Pure updates, composable: an action that changes two things must make ONE
  // onChange -- two in a row each start from the same `choices`, and the
  // second silently undoes the first.
  const withCreate = (c: LayoutChoices, keys: string[], on: boolean): LayoutChoices => {
    const next = new Set(c.create_groups);
    keys.forEach(k => (on ? next.add(k) : next.delete(k)));
    return { ...c, create_groups: [...next] };
  };
  const withMode = (c: LayoutChoices, ids: string[], mode: 'skip' | 'create' | 'stage'): LayoutChoices => {
    const create = new Set(c.include_unbroadcast);
    const stage = new Set(c.stage_unbroadcast ?? []);
    ids.forEach(id => {
      create.delete(id); stage.delete(id);
      if (mode === 'create') create.add(id);
      if (mode === 'stage') stage.add(id);
    });
    return { ...c, include_unbroadcast: [...create], stage_unbroadcast: [...stage] };
  };
  const setCreate = (keys: string[], on: boolean) => onChange(withCreate(choices, keys, on));

  // name '' = back to the suggestion
  const renameMany = (pairs: Array<[string, string]>) => {
    const names = { ...choices.group_names };
    for (const [key, name] of pairs) {
      if (name.trim()) names[key] = name.trim();
      else delete names[key];
    }
    onChange({ ...choices, group_names: names });
  };
  const rename = (key: string, name: string) => renameMany([[key, name]]);

  const toggleInclude = (id: string) => {
    const next = new Set(choices.include_unbroadcast);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange({ ...choices, include_unbroadcast: [...next] });
  };
  // skip / create / stage, per WLAN no AP broadcasts
  const setMode = (ids: string[], mode: 'skip' | 'create' | 'stage') => onChange(withMode(choices, ids, mode));
  const pairing = choices.pairing ?? { mode: 'wlan_group' as const };
  const setPairing = (p: Partial<NonNullable<LayoutChoices['pairing']>>) =>
    onChange({ ...choices, pairing: { ...pairing, ...p } });

  // Not-broadcast WLANs: sorted by name (unit numbers in order), filterable.
  const [unbroadcastFilter, setUnbroadcastFilter] = useState('');
  const unbroadcast = useMemo(() => {
    const q = unbroadcastFilter.trim().toLowerCase();
    return [...layout.unbroadcast]
      .filter(u => !q || [u.name, u.ssid, u.auth_type, u.reason].some(v => (v || '').toLowerCase().includes(q)))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [layout.unbroadcast, unbroadcastFilter]);
  const nCreate = layout.unbroadcast.filter(u => u.mode === 'create').length;
  const nStage = layout.unbroadcast.filter(u => u.mode === 'stage').length;
  const staged = layout.staged;

  // Property-wide candidates first; the long tail of single-unit WLANs on request.
  const wlanRows = showAllWlans
    ? layout.wlans
    : layout.wlans.filter(w => w.share >= 0.05 || w.suggested_all_ap || allAp.has(w.id));
  const hiddenWlans = layout.wlans.length - wlanRows.length;

  // Section 2 lists every AP Group the run builds: from the SZ broadcast, and
  // the per-unit (staged) ones for WLANs no AP broadcasts.
  const stagedGroups = layout.staged?.groups ?? [];
  const groups = useMemo(() => {
    const q = groupFilter.trim().toLowerCase();
    const source = groupView === 'staged' ? stagedGroups
      : groupView === 'all' || groupView === 'ambiguous' ? [...layout.groups, ...stagedGroups] : layout.groups;
    return source.filter(g => {
      if (groupView === 'ambiguous' && !g.ambiguous) return false;
      if (groupView === 'pending' && (g.exists || g.create)) return false;
      if (groupView === 'create' && !g.create) return false;
      if (groupView === 'exists' && !g.exists) return false;
      if (!q) return true;
      return g.name.toLowerCase().includes(q)
        || g.ssids.some(x => x.toLowerCase().includes(q))
        || g.aps.some(a => (a.name || '').toLowerCase().includes(q) || (a.serial || '').toLowerCase().includes(q));
    });
  }, [layout.groups, stagedGroups, groupFilter, groupView]);
  const nAmbiguous = [...layout.groups, ...stagedGroups].filter(g => g.ambiguous).length;
  // Bulk naming over the groups shown: '' = each group's suggestion
  const nameShownFrom = (source: '' | 'AP name' | 'unit WLAN') =>
    renameMany(groups.filter(g => g.name_options?.length).flatMap((g): Array<[string, string]> => {
      if (!source) return [[g.key, '']];
      const o = g.name_options!.find(x => x.source.split(' / ').includes(source));
      return o ? [[g.key, o.name]] : [];
    }));
  const newVisible = groups.filter(g => !g.exists && !g.staged).map(g => g.key);
  const stagedVisible = groups.filter(g => g.staged);
  const stagedChosen = stagedGroups.filter(g => g.chosen);
  // A per-unit group's tick is its WLANs' staging: ticked = build it (WLAN
  // created, not activated); unticked = create the WLAN only.
  const withStaged = (c: LayoutChoices, gs: LayoutGroup[], on: boolean) =>
    withMode(c, gs.flatMap(g => g.wlan_ids), on ? 'stage' : 'create');
  const setStaged = (gs: LayoutGroup[], on: boolean) => onChange(withStaged(choices, gs, on));
  const tickable = newVisible.length + stagedVisible.length;
  // Tick or untick the shown rows, SZ-derived and per-unit, in one update.
  const tickShown = (on: boolean) =>
    onChange(withStaged(withCreate(choices, newVisible, on), stagedVisible, on));

  // The approach (the default): every group SZ's broadcast needs, and every
  // WLAN no AP broadcasts created + staged. Anything else is the operator's
  // override, read back from the ticks as custom.
  const szToCreate = layout.groups.filter(g => !g.exists).map(g => g.key);
  const szReady = layout.groups.every(g => g.exists || g.create);
  const setAsIs = () => onChange(withMode(withCreate(choices, szToCreate, true), layout.unbroadcast.map(u => u.id), 'stage'));
  const approach: 'mirror' | 'custom' =
    szReady && layout.unbroadcast.every(u => u.mode === 'stage') ? 'mirror' : 'custom';
  const szTicked = layout.groups.filter(g => g.exists || g.create).length;

  return (
    <div className="space-y-5">
      {/* How SZ decides today */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <Stat label="SZ APs broadcasting" value={`${s.aps_broadcasting} / ${s.aps}`} />
        <Stat label="On All AP Groups" value={String(s.all_ap_wlans)} />
        <Stat label="R1 AP Groups needed" value={String(s.groups + (s.staged_groups ?? 0))}
              sub={`${s.groups} from SZ broadcast (${s.groups_existing} exist · ${s.groups_to_create} to create · ${s.groups_pending} not created)`
                + ((s.staged_groups ?? 0) ? ` · ${s.staged_groups} per-unit` : '')} />
        <Stat label="APs needing only All AP Groups" value={String(s.aps_default_group)} sub="stay in the default AP Group" />
      </div>
      <div className="text-xs text-gray-600">
        <span className="font-medium">What decides each SZ radio today</span> (closest to the AP wins):{' '}
        {Object.entries(s.sz_tiers).length === 0 ? 'no radios read' :
          Object.entries(s.sz_tiers).sort((a, b) => b[1] - a[1])
            .map(([t, n]) => `${TIER_LABELS[t] || t} on ${n} radio${n === 1 ? '' : 's'}`).join(' · ')}
      </div>

      {layout.warnings.map(w => (
        <div key={w.message}
             className={`flex items-start gap-2 text-xs rounded px-3 py-2 border ${w.severity === 'error'
               ? 'text-red-700 bg-red-50 border-red-200' : 'text-amber-700 bg-amber-50 border-amber-200'}`}>
          {w.severity === 'error' ? <AlertCircle size={13} className="mt-0.5 flex-shrink-0" />
            : <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />}
          {w.message}
        </div>
      ))}

      {/* The approach: mirror SZ, or a per-unit setup regardless */}
      <section className="border border-gray-200 rounded-lg p-3">
        <div className="flex items-center gap-2 mb-2">
          <h4 className="text-sm font-semibold text-gray-800">AP Group approach</h4>
          {approach === 'custom' && (
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">
              CUSTOM — {szTicked} of {layout.groups.length} SZ-broadcast group(s) ticked
              {stagedGroups.length > 0 && <> · {stagedChosen.length} of {stagedGroups.length} per-unit</>}
            </span>
          )}
        </div>
        <button type="button" disabled={disabled} onClick={setAsIs}
                className={`w-full text-left rounded border p-3 ${approach === 'mirror'
                  ? 'border-indigo-500 ring-1 ring-indigo-300 bg-indigo-50/50' : 'border-gray-200 hover:border-gray-300'}`}>
          <div className="text-sm font-semibold text-gray-800">
            <input type="radio" readOnly checked={approach === 'mirror'} className="mr-1.5 align-middle" />
            SZ as-is (default)
          </div>
          <div className="text-xs text-gray-600 mt-1">
            R1 AP Groups where SZ actually broadcasts different WLANs ({s.groups} here), so every AP broadcasts exactly
            what it broadcasts in SZ today.
            {layout.unbroadcast.length > 0 && <> The {layout.unbroadcast.length} WLAN(s) no AP broadcasts are created
              and staged{stagedGroups.length > 0 && <> in {stagedGroups.length} per-unit AP Group(s)</>}, not
              activated — each unit ready to switch on later.</>}
            {approach === 'custom' && <> <span className="text-indigo-700 font-medium">Click to go back to this.</span></>}
          </div>
        </button>
        <div className="text-[11px] text-gray-500 mt-2">
          Override only where needed: per group in section 2, or skip / create-only per WLAN in section 3. Any change
          shows this as Custom.
        </div>
      </section>

      {/* 1. Property-wide */}
      <section>
        <h4 className="text-sm font-semibold text-gray-800">1. All AP Groups (property-wide)</h4>
        <p className="text-xs text-gray-500 mb-2">
          Suggested when SZ broadcasts the WLAN on at least {pct(s.property_wide_share)} of APs. Confirm or change;
          everything else goes on the AP Groups below.
        </p>
        <div className="border border-gray-200 rounded overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="px-3 py-1.5 text-left w-28">All AP Groups</th>
                <th className="px-3 py-1.5 text-left">WLAN / SSID</th>
                <th className="px-3 py-1.5 text-right">SZ APs</th>
                <th className="px-3 py-1.5 text-left">R1 radios</th>
                <th className="px-3 py-1.5 text-left">Effect in R1</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {wlanRows.map(w => (
                <tr key={w.id} className={allAp.has(w.id) ? 'bg-indigo-50/40' : ''}>
                  <td className="px-3 py-1.5">
                    <label className="flex items-center gap-1.5">
                      <input type="checkbox" checked={allAp.has(w.id)} disabled={disabled}
                             onChange={() => toggleAllAp(w.id)} />
                      {w.suggested_all_ap && <span className="text-[10px] font-bold text-indigo-600">SUGGESTED</span>}
                    </label>
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="font-medium text-gray-800">{w.name}</div>
                    {w.ssid !== w.name && <div className="text-gray-400">{w.ssid}</div>}
                  </td>
                  <td className="px-3 py-1.5 text-right whitespace-nowrap">
                    {w.sz_ap_count} <span className="text-gray-400">({pct(w.share)})</span>
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    {w.radio_types.map(r => r.replace('-GHz', '')).join(' / ')}
                    {w.radio_notes.length > 0 && (
                      <span title={w.radio_notes.join('\n')} className="ml-1 text-amber-600 cursor-help">⚠</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-gray-600">
                    {w.mode === 'all_ap_groups' ? (
                      w.gains_in_r1 ? (
                        <span className="text-amber-700" title={w.gains_sample?.join(', ')}>
                          Also reaches {w.gains_in_r1} AP{w.gains_in_r1 === 1 ? '' : 's'} that don't broadcast it in SZ
                        </span>
                      ) : 'Same APs as SZ'
                    ) : (
                      <span className={w.unready_note ? 'text-amber-700' : ''}>
                        {w.targets?.length ?? 0} AP Group{(w.targets?.length ?? 0) === 1 ? '' : 's'}
                        {w.unready_note && ` — ${w.targets_ready} ready`}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hiddenWlans > 0 && (
          <button onClick={() => setShowAllWlans(true)} className="mt-1 text-xs text-indigo-600 hover:underline">
            Show {hiddenWlans} more WLAN{hiddenWlans === 1 ? '' : 's'} on under 5% of APs
          </button>
        )}
      </section>

      {/* 2. AP Groups */}
      <section>
        <h4 className="text-sm font-semibold text-gray-800">
          2. R1 AP Groups ({layout.groups.length + stagedGroups.length})
        </h4>
        <p className="text-xs text-gray-500 mb-2">
          Every AP Group Create WLANs builds. <b>From the SZ broadcast</b>: APs left with the same WLANs become one group;
          groups already in the R1 venue are reused, tick the ones to create — a group not created leaves its WLANs
          inactive for those APs. <b>Per-unit (staged)</b>: one group per unit for WLANs no AP broadcasts in SZ, built
          with its AP(s) paired by name and its WLAN not activated — ready to switch on later. AP Mapping (the last
          step) puts the APs into all of them.
        </p>

        {layout.unbroadcast.length > 0 && (
          <div className="border border-indigo-200 bg-indigo-50/50 rounded px-3 py-2 mb-2 text-xs flex flex-wrap items-center gap-3">
            <span className="font-semibold text-gray-800">Per-unit AP Groups</span>
            <span className="text-gray-600">
              {stagedGroups.length === 0
                ? <>{layout.unbroadcast.length} WLAN(s) no AP broadcasts in SZ, none pairs with an AP by name — see the
                    pairing rule in section 3.</>
                : <>{stagedChosen.length} of {stagedGroups.length} per-unit group(s) ticked to build
                    ({layout.summary.staged_aps ?? 0} AP(s)) — each for WLANs no AP broadcasts in SZ, created but not
                    activated.
                    {(layout.staged?.unpaired.length ?? 0) > 0 && <> {layout.staged.unpaired.length} staged WLAN(s) pair with no AP.</>}</>}
            </span>
            {stagedGroups.length > 0 && (
              <span className="ml-auto flex gap-3">
                <button disabled={disabled || stagedChosen.length === stagedGroups.length}
                        onClick={() => setStaged(stagedGroups, true)}
                        className="px-2.5 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:bg-gray-300 font-medium">
                  Tick all {stagedGroups.length} per-unit groups
                </button>
                {stagedChosen.length > 0 && (
                  <button disabled={disabled} onClick={() => setStaged(stagedChosen, false)}
                          className="text-gray-600 hover:underline disabled:text-gray-400">
                    Untick all (create the WLANs only)
                  </button>
                )}
              </span>
            )}
            <span className="w-full text-[11px] text-gray-500">
              Pairing and per-WLAN choices are in section 3 below.
            </span>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <div className="relative">
            <Search size={13} className="absolute left-2 top-2 text-gray-400" />
            <input value={groupFilter} onChange={e => setGroupFilter(e.target.value)}
                   placeholder="Filter by group, SSID, AP name or serial"
                   className="pl-7 pr-2 py-1 text-xs border border-gray-300 rounded w-72" />
          </div>
          <select value={groupView} onChange={e => setGroupView(e.target.value as any)}
                  className="text-xs border border-gray-300 rounded px-2 py-1">
            <option value="all">All ({layout.groups.length + stagedGroups.length})</option>
            {stagedGroups.length > 0 && <option value="staged">Per-unit ({stagedChosen.length} of {stagedGroups.length} ticked)</option>}
            <option value="pending">Not created ({s.groups_pending})</option>
            <option value="create">To create ({s.groups_to_create})</option>
            <option value="exists">Exist in R1 ({s.groups_existing})</option>
            {nAmbiguous > 0 && <option value="ambiguous">Other names possible ({nAmbiguous})</option>}
          </select>
          {groups.some(g => g.name_options?.length) && (
            <span className="flex items-center gap-2 text-xs text-gray-500"
                  title="Renames the groups shown. Each name stays editable afterwards.">
              Name {groups.length === layout.groups.length + stagedGroups.length ? 'all' : 'shown'} from:
              {([['', 'suggestion'], ['AP name', 'AP name'], ['unit WLAN', 'unit WLAN']] as const).map(([src, label]) => (
                <button key={label} disabled={disabled} onClick={() => nameShownFrom(src)}
                        className="text-indigo-600 hover:underline disabled:text-gray-400">{label}</button>
              ))}
            </span>
          )}
          {tickable > 0 && (
            <>
              <button disabled={disabled}
                      onClick={() => tickShown(true)}
                      className="text-xs text-indigo-600 hover:underline disabled:text-gray-400">
                Tick {tickable} shown to create
              </button>
              <button disabled={disabled}
                      onClick={() => tickShown(false)}
                      className="text-xs text-gray-500 hover:underline disabled:text-gray-400">
                Untick shown
              </button>
            </>
          )}
        </div>
        <div className="border border-gray-200 rounded overflow-hidden max-h-[28rem] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500 sticky top-0">
              <tr>
                <th className="px-3 py-1.5 text-left w-24">Create</th>
                <th className="px-3 py-1.5 text-left">R1 AP Group</th>
                <th className="px-3 py-1.5 text-left">SZ APs</th>
                <th className="px-3 py-1.5 text-left">Group WLANs</th>
                <th className="px-3 py-1.5 text-right">SSIDs</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {groups.map(g => (
                <GroupRow key={g.key} g={g} disabled={disabled}
                          onCreate={on => (g.staged ? setStaged([g], on) : setCreate([g.key], on))}
                          onRename={n => rename(g.key, n)} />
              ))}
              {groups.length === 0 && (
                <tr><td colSpan={5} className="px-3 py-3 text-center text-gray-400">No groups match</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-gray-400 mt-1">
          SSIDs = group WLANs + All AP Groups WLANs; R1 allows 15 per AP Group.
          Default AP Group: {layout.default_group.ap_count} AP(s), {layout.default_group.ssid_total} SSID(s).
        </p>
      </section>

      {/* 3. Not broadcast */}
      {layout.unbroadcast.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-gray-800">3. WLANs no AP broadcasts ({layout.unbroadcast.length})</h4>
          <p className="text-xs text-gray-500 mb-2">
            Create + stage by default; change it per WLAN as needed. <b>Skip</b>: not migrated. <b>Create</b>: the WLAN is created in R1, nothing else.{' '}
            <b>Create + stage</b>: the WLAN is created, its AP Group built and filled with the AP(s) it pairs with, and
            nothing is activated — turning it on later is just activating the WLAN on that group. Only APs that broadcast
            just the All AP Groups WLANs can be staged, so staging never changes what an AP broadcasts.
          </p>

          <div className="flex flex-wrap items-center gap-3 mb-2 text-xs">
            <div className="relative w-64">
              <Search size={13} className="absolute left-2 top-1.5 text-gray-400" />
              <input value={unbroadcastFilter} onChange={e => setUnbroadcastFilter(e.target.value)}
                     placeholder="Filter by WLAN, SSID, type or reason"
                     className="pl-7 pr-2 py-1 border border-gray-300 rounded w-full" />
            </div>
            <span className="text-gray-500">
              {layout.unbroadcast.length - nCreate - nStage} skipped · {nCreate} create · {nStage} create + stage
            </span>
            <span className="ml-auto flex gap-3">
              {(['skip', 'create', 'stage'] as const).map(m => (
                <button key={m} disabled={disabled} onClick={() => setMode(unbroadcast.map(u => u.id), m)}
                        className={`${m === 'skip' ? 'text-gray-500' : 'text-indigo-600'} hover:underline disabled:text-gray-400`}>
                  {m === 'skip' ? 'Skip' : m === 'create' ? 'Create' : 'Create + stage'}{' '}
                  {unbroadcast.length === layout.unbroadcast.length ? 'all' : `${unbroadcast.length} shown`}
                </button>
              ))}
            </span>
          </div>

          {nStage > 0 && (
            <div className="border border-indigo-100 bg-indigo-50/40 rounded px-3 py-2 mb-2 text-xs space-y-1.5">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-semibold text-gray-700">Pairing</span>
                <label className="flex items-center gap-1">
                  <input type="radio" disabled={disabled} checked={pairing.mode !== 'pattern'}
                         onChange={() => setPairing({ mode: 'wlan_group' })} />
                  SZ WLAN Group name = AP name
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" disabled={disabled} checked={pairing.mode === 'pattern'}
                         onChange={() => setPairing({ mode: 'pattern' })} />
                  Name pattern
                </label>
                {pairing.mode === 'pattern' && (
                  <>
                    <input value={pairing.wlan_pattern ?? ''} disabled={disabled} placeholder="WLAN, e.g. {unit}@Site"
                           onChange={e => setPairing({ wlan_pattern: e.target.value })}
                           className="border border-gray-300 rounded px-2 py-0.5 w-44 font-mono" />
                    <span className="text-gray-400">→ AP</span>
                    <input value={pairing.ap_pattern ?? ''} disabled={disabled} placeholder="{unit}"
                           onChange={e => setPairing({ ap_pattern: e.target.value })}
                           className="border border-gray-300 rounded px-2 py-0.5 w-32 font-mono" />
                  </>
                )}
              </div>
              <div className="text-gray-600">
                {stagedChosen.length} per-unit AP Group(s) to build ({layout.summary.staged_aps ?? 0} AP(s)
                {stagedChosen.some(g => g.exists) ? `, ${stagedChosen.filter(g => g.exists).length} already in R1` : ''})
                of {stagedGroups.length} the pairing finds
                {staged.unpaired.length > 0 && <> · <span className="text-amber-700">{staged.unpaired.length} paired with no AP
                  (created, no group): {staged.unpaired.slice(0, 6).join(', ')}{staged.unpaired.length > 6 ? '…' : ''}</span></>}
              </div>
              {staged.conflicts.map((c, i) => <div key={i} className="text-amber-700">• {c}</div>)}
            </div>
          )}

          <div className="border border-gray-200 rounded max-h-72 overflow-y-auto divide-y divide-gray-100">
            {unbroadcast.length === 0 && <div className="px-3 py-2 text-xs text-gray-400">No WLANs match</div>}
            {unbroadcast.map(u => (
              <div key={u.id} className="flex items-center gap-2 px-3 py-1 text-xs">
                <div className="inline-flex rounded border border-gray-300 overflow-hidden flex-shrink-0">
                  {(['skip', 'create', 'stage'] as const).map(m => (
                    <button key={m} disabled={disabled} onClick={() => setMode([u.id], m)}
                            className={`px-1.5 py-0.5 ${u.mode === m
                              ? (m === 'skip' ? 'bg-gray-600 text-white' : 'bg-indigo-600 text-white')
                              : 'bg-white text-gray-600'}`}>
                      {m === 'skip' ? 'Skip' : m === 'create' ? 'Create' : '+ Stage'}
                    </button>
                  ))}
                </div>
                <span className="font-medium">{u.name}</span>
                <span className="text-gray-400">{u.auth_type}</span>
                {/* WLAN -> AP Group: the per-unit group it pairs into (built only when staged) */}
                <span className={u.mode === 'stage' ? (u.paired_group ? 'text-indigo-700' : 'text-amber-700') : 'text-gray-400'}
                      title={u.paired_aps.length ? `AP(s): ${u.paired_aps.join(', ')}` : 'Pairs with no AP'}>
                  {u.paired_group
                    ? <>→ AP Group <b>{u.paired_group}</b>{u.paired_aps.length > 1 ? ` (${u.paired_aps.length} APs)` : ''}
                        {u.mode !== 'stage' && ' — not built'}</>
                    : <>→ (default) · All APs{u.mode === 'stage' ? ' — no unit AP pairs' : ''}</>}
                  {u.mode !== 'skip' && <span className="text-gray-400"> · not activated</span>}
                </span>
                <span className="ml-auto text-gray-400">{u.reason}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function GroupRow({ g, disabled, onCreate, onRename }: {
  g: LayoutGroup; disabled?: boolean; onCreate: (on: boolean) => void; onRename: (name: string) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <tr className={g.over_limit ? 'bg-red-50' : g.staged ? (g.chosen ? 'bg-indigo-50/30' : 'bg-gray-50/60') : g.exists ? 'bg-green-50/40' : g.create ? '' : 'bg-gray-50/60'}>
      <td className="px-3 py-1.5">
        {g.staged ? (
          <label className="flex items-center gap-1.5" title="Ticked: built with its AP(s), its WLAN created but not activated. Unticked: the WLAN only.">
            <input type="checkbox" checked={!!g.chosen} disabled={disabled} onChange={e => onCreate(e.target.checked)} />
            <span className={`text-[10px] font-bold ${g.chosen ? 'text-indigo-700' : 'text-gray-400'}`}>
              PER-UNIT · {g.chosen ? (g.exists ? 'IN R1' : 'BUILD') : 'NOT BUILT'}
            </span>
          </label>
        ) : g.exists ? (
          <span className="text-[10px] font-bold text-green-700">EXISTS</span>
        ) : (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={g.create} disabled={disabled} onChange={e => onCreate(e.target.checked)} />
            <span className={`text-[10px] font-bold ${g.create ? 'text-indigo-600' : 'text-gray-400'}`}>
              {g.create ? 'CREATE' : 'NOT CREATED'}
            </span>
          </label>
        )}
      </td>
      <td className="px-3 py-1.5">
        {g.matched_by === 'SSIDs' ? (
          <span className="font-medium">{g.name}</span>
        ) : (
          <input
            className={`w-full border rounded px-1.5 py-0.5 font-medium bg-white focus:border-indigo-400 focus:outline-none ${
              g.ambiguous ? 'border-amber-400' : 'border-gray-300'}`}
            value={draft ?? g.name}
            disabled={disabled}
            maxLength={64}
            onChange={e => setDraft(e.target.value)}
            onBlur={() => { if (draft !== null && draft !== g.name) onRename(draft); setDraft(null); }}
            onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          />
        )}
        <div className="text-[10px] text-gray-400 px-1">
          {g.matched_by === 'SSIDs' ? 'existing R1 group already carrying these SSIDs'
            : `${g.staged ? 'per-unit (staged) · ' : ''}${g.exists ? 'existing R1 group of this name · ' : ''}`
              + `named from ${g.renamed ? 'your edit' : g.name_source}`}
        </div>
        {/* Other reasonable names: one click to take one */}
        {(g.name_options?.length ?? 0) > 1 && (
          <div className="flex flex-wrap items-center gap-1 px-1 mt-0.5 text-[10px]">
            {g.ambiguous && <span className="text-amber-700 font-semibold">pick:</span>}
            {g.name_options!.filter(o => o.name !== g.name).map(o => (
              <button key={o.name} disabled={disabled} onClick={() => onRename(o.name)}
                      title={`Name it ${o.name} (from ${o.source})`}
                      className="px-1.5 py-px rounded border border-gray-300 bg-white hover:border-indigo-400 hover:text-indigo-700 disabled:text-gray-400">
                {o.name} <span className="text-gray-400">· {o.source}</span>
              </button>
            ))}
            {g.renamed && (
              <button disabled={disabled} onClick={() => onRename('')}
                      className="text-gray-500 hover:underline disabled:text-gray-400">reset</button>
            )}
          </div>
        )}
      </td>
      <td className="px-3 py-1.5" title={g.aps.map(a => `${a.name}${a.serial ? ` (${a.serial})` : ''}`).join('\n')}>
        {g.ap_count === 1 ? g.aps[0]?.name : `${g.ap_count} APs`}
        {g.ap_count > 1 && <div className="text-[10px] text-gray-400">{g.aps.slice(0, 3).map(a => a.name).join(', ')}{g.ap_count > 3 ? '…' : ''}</div>}
      </td>
      <td className="px-3 py-1.5">
        {g.ssids.join(', ')}
        {g.staged && <div className="text-[10px] text-indigo-600">created, not activated</div>}
      </td>
      <td className={`px-3 py-1.5 text-right ${g.over_limit ? 'text-red-700 font-bold' : ''}`}>{g.ssid_total}/15</td>
    </tr>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border border-gray-200 rounded p-2.5">
      <div className="text-lg font-bold text-gray-800">{value}</div>
      <div className="text-[11px] text-gray-500">{label}</div>
      {sub && <div className="text-[10px] text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}
