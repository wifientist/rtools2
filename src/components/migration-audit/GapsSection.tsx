import { useState } from 'react';
import type { AuditMatching, ApMatch, WlanMatch } from '@/types/migrationAudit';

const ROW_CAP = 15;

const REASON_CLS: Record<NonNullable<ApMatch['missing_code']>, string> = {
  not_in_r1: 'bg-red-50 text-red-700 border-red-200',
  other_venue: 'bg-amber-50 text-amber-700 border-amber-200',
  no_serial: 'bg-gray-50 text-gray-600 border-gray-200',
  not_searched: 'bg-gray-50 text-gray-600 border-gray-200',
};
const REASON_LABEL: Record<NonNullable<ApMatch['missing_code']>, string> = {
  not_in_r1: 'serial not in R1',
  other_venue: 'in another venue',
  no_serial: 'no serial in SZ',
  not_searched: 'tenant not searched',
};

// What SZ has that R1 doesn't yet: APs missing from the venue (and why),
// WLANs never created, and WLANs created but not activated here.
export default function GapsSection({ matching }: { matching: AuditMatching }) {
  const aps = matching.aps.filter(a => a.presence === 'sz_only');
  const notCreated = matching.wlans.filter(w => !w.r1_network_id);
  const notActivated = matching.wlans.filter(w => w.r1_network_id && !w.activated_in_venue);
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">In SZ, not yet in R1</h3>
      </div>
      <ApGaps aps={aps} />
      <WlanGaps title="WLANs not created in R1" empty="Every SZ WLAN has an R1 network" rows={notCreated} />
      <WlanGaps title="WLANs created in R1 but not activated in this venue" empty="Every R1 network found is activated here"
                rows={notActivated} showR1 />
    </div>
  );
}

function Heading({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center gap-2 mb-1.5">
      <span className="text-xs font-semibold text-gray-700">{title}</span>
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold tabular-nums ${
        count ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'}`}>{count}</span>
    </div>
  );
}

function MoreToggle({ total, all, onToggle }: { total: number; all: boolean; onToggle: () => void }) {
  if (total <= ROW_CAP) return null;
  return (
    <button type="button" onClick={onToggle} className="mt-1 text-xs text-indigo-600 hover:underline">
      {all ? 'Show fewer' : `Show all ${total}`}
    </button>
  );
}

function ApGaps({ aps }: { aps: ApMatch[] }) {
  const [all, setAll] = useState(false);
  const byReason = aps.reduce<Record<string, number>>((acc, a) => {
    const k = a.missing_code ?? 'not_searched';
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});
  const shown = all ? aps : aps.slice(0, ROW_CAP);
  return (
    <div className="px-4 py-3 border-b">
      <Heading title="APs in SZ but not in this R1 venue" count={aps.length} />
      {aps.length === 0 ? <div className="text-xs text-green-700">Every SZ AP is in the venue</div> : (
        <>
          <div className="flex gap-1.5 flex-wrap mb-2">
            {Object.entries(byReason).map(([k, n]) => (
              <span key={k} className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${REASON_CLS[k as keyof typeof REASON_CLS]}`}>
                {n} {REASON_LABEL[k as keyof typeof REASON_LABEL]}
              </span>
            ))}
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 text-left border-b">
                <th className="py-1 pr-2 font-medium">AP</th>
                <th className="py-1 pr-2 font-medium">Serial</th>
                <th className="py-1 pr-2 font-medium">Model</th>
                <th className="py-1 pr-2 font-medium">SZ AP Group</th>
                <th className="py-1 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {shown.map((a, i) => (
                <tr key={a.serial ?? a.mac ?? i}>
                  <td className="py-1 pr-2 text-gray-800">{a.name ?? '—'}</td>
                  <td className="py-1 pr-2 font-mono">{a.serial ?? '—'}</td>
                  <td className="py-1 pr-2 text-gray-600">{a.model ?? '—'}</td>
                  <td className="py-1 pr-2 text-gray-600">{a.sz_ap_group ?? '—'}</td>
                  <td className={`py-1 ${a.missing_code === 'not_in_r1' ? 'text-red-700' : a.missing_code === 'other_venue' ? 'text-amber-700' : 'text-gray-500'}`}>
                    {a.missing_reason ?? 'Not in this venue'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <MoreToggle total={aps.length} all={all} onToggle={() => setAll(x => !x)} />
        </>
      )}
    </div>
  );
}

function WlanGaps({ title, empty, rows, showR1 }: { title: string; empty: string; rows: WlanMatch[]; showR1?: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, ROW_CAP);
  return (
    <div className="px-4 py-3 border-b last:border-b-0">
      <Heading title={title} count={rows.length} />
      {rows.length === 0 ? <div className="text-xs text-green-700">{empty}</div> : (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 text-left border-b">
                <th className="py-1 pr-2 font-medium">SZ WLAN</th>
                <th className="py-1 pr-2 font-medium">SSID</th>
                {showR1 && <th className="py-1 pr-2 font-medium">R1 network</th>}
                <th className="py-1 pr-2 font-medium text-right">SZ APs</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {shown.map(w => (
                <tr key={w.sz_wlan_id}>
                  <td className="py-1 pr-2 text-gray-800">{w.sz_name}</td>
                  <td className="py-1 pr-2 font-mono">{w.sz_ssid}</td>
                  {showR1 && <td className="py-1 pr-2 text-gray-600">{w.r1_name ?? '—'}</td>}
                  <td className="py-1 pr-2 text-right tabular-nums text-gray-600">{w.sz_ap_count ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <MoreToggle total={rows.length} all={all} onToggle={() => setAll(x => !x)} />
        </>
      )}
    </div>
  );
}
