import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';

export interface ConvertibleWlan {
  id: string;
  name: string;
  ssid: string;
  szAuthType: string;
  r1Type: string;            // dpsk_external | aaa (as SZ maps it)
  serviceName: string | null;
}

interface Props {
  wlans: ConvertibleWlan[];
  conversions: Record<string, 'dpsk'>;
  onChange: (conversions: Record<string, 'dpsk'>) => void;
  disabled?: boolean;
}

const KINDS: Array<{ r1Type: string; title: string; keep: string; convert: string }> = [
  {
    r1Type: 'dpsk_external',
    title: 'SZ external DPSK (WPA2 Personal, keys checked by RADIUS)',
    keep: 'R1 DPSK network that keeps asking the same RADIUS server (e.g. Cloudpath) for keys',
    convert: "R1's own DPSK service and identity groups; the RADIUS server is no longer used",
  },
  {
    r1Type: 'aaa',
    title: 'SZ Enterprise (802.1X)',
    keep: 'R1 Enterprise (AAA) network using the same RADIUS server',
    convert: "R1 DPSK network: clients join with a passphrase instead of 802.1X credentials",
  },
];

export default function AuthConversionPanel({ wlans, conversions, onChange, disabled }: Props) {
  const [filter, setFilter] = useState('');

  const set = (ids: string[], convert: boolean) => {
    const next = { ...conversions };
    ids.forEach(id => (convert ? (next[id] = 'dpsk') : delete next[id]));
    onChange(next);
  };

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? wlans.filter(w => `${w.name} ${w.ssid} ${w.serviceName ?? ''}`.toLowerCase().includes(q)) : wlans;
  }, [wlans, filter]);

  if (wlans.length === 0) {
    return (
      <div className="text-sm text-gray-500 italic">
        No WLAN authenticates against a RADIUS server, so there is nothing to convert.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-600">
        Each WLAN below authenticates against a RADIUS server in SZ. By default every one moves onto R1's own DPSK
        service and identity management; untick a WLAN to keep it on its external RADIUS server in R1 instead.
        Converted WLANs get a DPSK service in the next sections; a RADIUS
        server only converted WLANs used is not created. No SZ or Cloudpath passphrase or identity is copied.
      </p>

      {wlans.length > 10 && (
        <div className="relative w-72">
          <Search size={13} className="absolute left-2 top-2 text-gray-400" />
          <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter by WLAN, SSID or RADIUS server"
                 className="pl-7 pr-2 py-1 text-xs border border-gray-300 rounded w-full" />
        </div>
      )}

      {KINDS.map(kind => {
        const all = wlans.filter(w => w.r1Type === kind.r1Type);
        if (all.length === 0) return null;
        const rows = shown.filter(w => w.r1Type === kind.r1Type);
        const nConverted = all.filter(w => conversions[w.id]).length;
        return (
          <section key={kind.r1Type}>
            <div className="flex flex-wrap items-baseline gap-2 mb-1">
              <h4 className="text-sm font-semibold text-gray-800">{kind.title}</h4>
              <span className="text-xs text-gray-500">
                {all.length} WLAN{all.length === 1 ? '' : 's'} · {nConverted} converted · {all.length - nConverted} kept external
              </span>
              <span className="ml-auto flex gap-3 text-xs">
                <button disabled={disabled} onClick={() => set(rows.map(w => w.id), true)}
                        className="text-indigo-600 hover:underline disabled:text-gray-400">
                  Convert {rows.length === all.length ? 'all' : `${rows.length} shown`} to R1 DPSK
                </button>
                <button disabled={disabled} onClick={() => set(rows.map(w => w.id), false)}
                        className="text-gray-500 hover:underline disabled:text-gray-400">
                  Keep {rows.length === all.length ? 'all' : `${rows.length} shown`} external
                </button>
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] text-gray-500 mb-2">
              <div><span className="font-semibold text-gray-600">Keep external:</span> {kind.keep}</div>
              <div><span className="font-semibold text-indigo-600">Convert:</span> {kind.convert}</div>
            </div>
            <div className="border border-gray-200 rounded max-h-72 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-500 sticky top-0">
                  <tr>
                    <th className="px-3 py-1.5 text-left">WLAN</th>
                    <th className="px-3 py-1.5 text-left">RADIUS server (SZ)</th>
                    <th className="px-3 py-1.5 text-left w-56">In R1</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map(w => (
                    <tr key={w.id} className={conversions[w.id] ? 'bg-indigo-50/40' : ''}>
                      <td className="px-3 py-1">
                        <span className="font-medium text-gray-800">{w.name}</span>
                        {w.ssid !== w.name && <span className="text-gray-400 ml-1">({w.ssid})</span>}
                      </td>
                      <td className="px-3 py-1 text-gray-600">{w.serviceName ?? '—'}</td>
                      <td className="px-3 py-1">
                        <div className="inline-flex rounded border border-gray-300 overflow-hidden">
                          <button disabled={disabled} onClick={() => set([w.id], false)}
                                  className={`px-2 py-0.5 ${!conversions[w.id] ? 'bg-gray-700 text-white' : 'bg-white text-gray-600'}`}>
                            Keep external
                          </button>
                          <button disabled={disabled} onClick={() => set([w.id], true)}
                                  className={`px-2 py-0.5 ${conversions[w.id] ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600'}`}>
                            R1 DPSK
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={3} className="px-3 py-2 text-center text-gray-400">No WLANs match</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}
