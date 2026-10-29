import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type { LayoutCatalogWlan } from '@/types/szConfigMigration';

interface Props {
  catalog: LayoutCatalogWlan[];
  excluded: string[];
  onChange: (excluded: string[]) => void;
  disabled?: boolean;
}

/**
 * Which SZ WLANs are migrated at all. An excluded WLAN is treated as if SZ
 * never had it: no R1 network, no activation, and it drops out of every AP's
 * broadcast -- so out of the AP Groups below too. Everything is included by
 * default; names that look like test / temp SSIDs are flagged, not dropped.
 */
export default function WlanSelection({ catalog, excluded, onChange, disabled }: Props) {
  const [filter, setFilter] = useState('');
  const out = new Set(excluded);
  const set = (ids: string[], exclude: boolean) => {
    const next = new Set(out);
    ids.forEach(id => (exclude ? next.add(id) : next.delete(id)));
    onChange(catalog.map(w => w.id).filter(id => next.has(id)));
  };

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? catalog.filter(w => `${w.name} ${w.ssid} ${w.auth_type}`.toLowerCase().includes(q)) : catalog;
  }, [catalog, filter]);
  const hinted = catalog.filter(w => w.skip_hint && !out.has(w.id));

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-600">
        Untick a WLAN to leave it out of the migration entirely: no network is created in R1, nothing is activated,
        and it no longer counts toward any AP's broadcast — so it won't shape the AP Groups either. Everything is
        included by default.
      </p>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <div className="relative w-72">
          <Search size={13} className="absolute left-2 top-1.5 text-gray-400" />
          <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter by name, SSID or security"
                 className="pl-7 pr-2 py-1 border border-gray-300 rounded w-full" />
        </div>
        {hinted.length > 0 && (
          <button disabled={disabled} onClick={() => set(hinted.map(w => w.id), true)}
                  className="text-amber-700 font-medium hover:underline disabled:text-gray-400">
            Exclude {hinted.length} test / temp WLAN{hinted.length === 1 ? '' : 's'}
          </button>
        )}
        <button disabled={disabled} onClick={() => set(shown.map(w => w.id), true)}
                className="text-gray-600 hover:underline disabled:text-gray-400">
          Exclude {shown.length === catalog.length ? 'all' : `${shown.length} shown`}
        </button>
        <button disabled={disabled} onClick={() => set(shown.map(w => w.id), false)}
                className="text-indigo-600 hover:underline disabled:text-gray-400">
          Include {shown.length === catalog.length ? 'all' : `${shown.length} shown`}
        </button>
        <span className="ml-auto text-gray-500">
          {catalog.length - out.size} of {catalog.length} migrated{out.size ? ` · ${out.size} excluded` : ''}
        </span>
      </div>

      <div className="border border-gray-200 rounded max-h-80 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-500 text-left sticky top-0">
            <tr>
              <th className="px-3 py-1.5 w-8"></th>
              <th className="px-3 py-1.5 font-medium">WLAN</th>
              <th className="px-3 py-1.5 font-medium">SSID</th>
              <th className="px-3 py-1.5 font-medium">Security (SZ)</th>
              <th className="px-3 py-1.5 font-medium text-right">APs broadcasting</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {shown.map(w => {
              const isOut = out.has(w.id);
              return (
                <tr key={w.id} className={isOut ? 'bg-gray-50 text-gray-400' : ''}>
                  <td className="px-3 py-1">
                    <input type="checkbox" checked={!isOut} disabled={disabled} onChange={e => set([w.id], !e.target.checked)} />
                  </td>
                  <td className={`px-3 py-1 font-medium ${isOut ? 'line-through' : 'text-gray-800'}`}>
                    {w.name}
                    {w.skip_hint && (
                      <span className="ml-1.5 text-[10px] px-1 rounded bg-amber-100 text-amber-800 no-underline">test / temp?</span>
                    )}
                  </td>
                  <td className="px-3 py-1">{w.ssid !== w.name ? w.ssid : <span className="text-gray-300">same</span>}</td>
                  <td className="px-3 py-1">{w.auth_type}</td>
                  <td className="px-3 py-1 text-right tabular-nums">{w.ap_count || <span className="text-gray-300">none</span>}</td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-2 text-center text-gray-400">No WLANs match</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
