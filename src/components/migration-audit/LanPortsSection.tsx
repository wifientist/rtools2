import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from 'lucide-react';
import { ago } from '@/components/migration-audit/CachePanel';
import type { LanPortAuditRow, LanPortStatus, LanPortsAudit } from '@/types/migrationAudit';

const ROW_CAP = 50;

const STATUS: Record<LanPortStatus, { label: string; cls: string; title: string }> = {
  differs:    { label: 'Differs',        cls: 'bg-amber-50 text-amber-700 border-amber-200',
                title: "R1 doesn't give this AP the ports SZ does" },
  pending:    { label: 'Not yet online', cls: 'bg-blue-50 text-blue-700 border-blue-200',
                title: "R1 serves an AP's own port settings once it has connected" },
  unreadable: { label: "Can't compare",  cls: 'bg-gray-50 text-gray-600 border-gray-200',
                title: 'Something stopped the comparison; open the row for why' },
  not_in_r1:  { label: 'Not in R1',      cls: 'bg-red-50 text-red-700 border-red-200',
                title: 'No AP with this serial or MAC in the R1 venue' },
  match:      { label: 'Match',          cls: 'bg-green-50 text-green-700 border-green-200',
                title: 'R1 gives this AP what SZ does' },
};
const ORDER: LanPortStatus[] = ['differs', 'pending', 'unreadable', 'not_in_r1', 'match'];

// Per-AP LAN ports: SZ's effective ports (AP override > AP Group > zone) vs
// the same AP in R1. Read-only; AP Port Grabber writes the fixes.
export default function LanPortsSection({ audit, onCheck }: { audit: LanPortsAudit; onCheck?: () => Promise<void> }) {
  const counts = audit.counts ?? {};
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const src = audit.sz_sources ?? {};
  const flagged = (src.ap ?? 0) + (src.group ?? 0);

  const check = async () => {
    if (!onCheck) return;
    setChecking(true);
    setCheckError(null);
    setElapsed(0);
    const timer = setInterval(() => setElapsed(e => e + 1), 1000);
    try {
      await onCheck();
      setFilter(null);
    } catch (e: any) {
      setCheckError(e.message || 'LAN port check failed');
    } finally {
      clearInterval(timer);
      setChecking(false);
    }
  };
  const checkButton = onCheck && (audit.available || audit.deferred) && (
    <button onClick={check} disabled={checking}
            className={`text-xs px-2.5 py-1 rounded border flex items-center gap-1.5 disabled:opacity-60 ${
              audit.deferred ? 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700'
                             : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}`}>
      {checking ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
      {checking ? `Reading R1 ports… ${elapsed}s` : audit.deferred ? 'Check R1 ports' : 'Re-check R1 ports'}
    </button>
  );
  const [filter, setFilter] = useState<LanPortStatus | null>(
    (counts.differs ?? 0) > 0 ? 'differs' : null);
  const [showAll, setShowAll] = useState(false);

  const rows = filter ? audit.rows.filter(r => r.status === filter) : audit.rows;
  const shown = showAll ? rows : rows.slice(0, ROW_CAP);

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold text-gray-700">AP LAN ports</h3>
          <div className="text-[11px] text-gray-500">
            Each SZ AP's effective ports (AP override, else AP Group, else zone) vs the same AP in R1.
            R1 holds per-AP ports only once the AP is online.
          </div>
        </div>
        <div className="flex items-center gap-3">
          {audit.available && audit.r1_read?.age_seconds != null && (
            <span className="text-[11px] text-gray-400">R1 ports read {ago(audit.r1_read.age_seconds)}</span>
          )}
          {audit.available && (counts.differs ?? 0) > 0 && (
            <Link to="/ap-port-grabber" className="text-xs text-blue-600 hover:underline">
              Fix with AP Port Grabber →
            </Link>
          )}
          {checkButton}
        </div>
      </div>
      {checkError && <div className="px-4 py-2 text-xs text-red-700 border-b">{checkError}</div>}

      {!audit.available ? (
        <div className="px-4 py-3 text-xs text-gray-600 space-y-1">
          {audit.deferred ? (
            <>
              <div>
                {flagged > 0
                  ? <><span className="font-semibold text-amber-700">{flagged} AP(s)</span> get port settings beyond the zone in SZ
                      ({src.ap ?? 0} their own, {src.group ?? 0} via AP Group); {src.zone ?? 0} follow the zone.</>
                  : <>No SZ AP has its own or AP Group port settings; all {src.zone ?? 0} follow the zone.</>}
                {(src.unknown ?? 0) > 0 && <> {src.unknown} couldn't be read in SZ.</>}
              </div>
              <div className="text-gray-500">
                R1 not checked yet: it takes one R1 read per AP, so it runs separately from the audit.
              </div>
            </>
          ) : audit.reason}
        </div>
      ) : (
        <>
          <div className="px-4 py-2 border-b flex flex-wrap gap-1.5">
            {ORDER.filter(s => counts[s]).map(s => (
              <button key={s} title={STATUS[s].title}
                      onClick={() => { setFilter(filter === s ? null : s); setShowAll(false); }}
                      className={`text-xs px-2 py-0.5 rounded border ${STATUS[s].cls} ${
                        filter === s ? 'ring-2 ring-offset-1 ring-blue-300' : ''}`}>
                {STATUS[s].label} {counts[s]}
              </button>
            ))}
            {filter && (
              <button onClick={() => setFilter(null)} className="text-xs text-gray-500 hover:underline ml-1">
                show all {audit.rows.length}
              </button>
            )}
          </div>
          {audit.model_notes.length > 0 && (
            <div className="px-4 py-2 border-b text-xs text-amber-700 space-y-0.5">
              {audit.model_notes.map((n, i) => <div key={i}>{n}</div>)}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 text-left border-b">
                  <th className="w-6" />
                  <th className="px-2 py-2 font-medium">AP</th>
                  <th className="px-2 py-2 font-medium">SZ source</th>
                  <th className="px-2 py-2 font-medium">Difference</th>
                  <th className="px-4 py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(r => <LanRow key={r.sz_mac ?? r.sz_name ?? ''} row={r} />)}
                {rows.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-3 text-gray-500 italic">No APs.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {rows.length > ROW_CAP && (
            <button onClick={() => setShowAll(!showAll)}
                    className="w-full px-4 py-2 text-xs text-blue-600 hover:bg-gray-50 border-t">
              {showAll ? 'Show fewer' : `Show all ${rows.length}`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function LanRow({ row }: { row: LanPortAuditRow }) {
  const [open, setOpen] = useState(false);
  const s = STATUS[row.status] ?? STATUS.unreadable;
  const summary = row.status === 'differs' ? row.changes.join('; ')
    : row.status === 'match' ? ''
    : row.blockers[0] ?? row.notes[0] ?? '';
  return (
    <>
      <tr className="border-b border-gray-100 align-top cursor-pointer hover:bg-gray-50" onClick={() => setOpen(!open)}>
        <td className="pl-3 py-1.5 text-gray-400">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
        <td className="px-2 py-1.5">
          <div className="text-gray-800">{row.sz_name}</div>
          <div className="text-[11px] text-gray-400 font-mono">{row.model} · {row.serial || row.sz_mac}</div>
        </td>
        <td className="px-2 py-1.5 text-gray-600">{row.source}</td>
        <td className="px-2 py-1.5 font-mono text-gray-700 max-w-xl">{summary}</td>
        <td className="px-4 py-1.5">
          <span title={s.title} className={`px-1.5 py-0.5 rounded border whitespace-nowrap ${s.cls}`}>{s.label}</span>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-gray-100 bg-gray-50/60">
          <td />
          <td colSpan={4} className="px-2 py-2 space-y-1.5">
            <div className="grid grid-cols-2 gap-4">
              <PortList title={`SZ${row.sz_group ? ` (AP Group ${row.sz_group})` : ''}`} ports={row.sz_ports} poe={row.poe} />
              <PortList title={`R1${row.r1_name ? ` — ${row.r1_name}` : ''}${
                row.r1_use_venue === true ? ' (venue settings)' : row.r1_use_venue === false ? ' (own settings)' : ''}`}
                        ports={row.r1_ports} poe={row.r1_poe} />
            </div>
            {row.uplink_ports.length > 0 && (
              <div className="text-[11px] text-gray-500">
                Uplink: {row.uplink_ports.map(p => `LAN${p}`).join(', ')}
              </div>
            )}
            {[...row.blockers, ...row.notes].map((n, i) => (
              <div key={`n${i}`} className="text-[11px] text-amber-700">{n}</div>
            ))}
            {row.extras.map((n, i) => (
              <div key={`x${i}`} className="text-[11px] text-gray-500">Not carried to R1: {n}</div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}

function PortList({ title, ports, poe }: { title: string; ports: string[]; poe: Record<string, any> }) {
  const poeText = Object.entries(poe ?? {}).map(([k, v]) => `${k} ${String(v)}`).join(', ');
  return (
    <div>
      <div className="text-[11px] font-semibold text-gray-500 mb-0.5">{title}</div>
      {ports.length === 0
        ? <div className="text-[11px] text-gray-400 italic">—</div>
        : ports.map((p, i) => <div key={i} className="font-mono text-[11px] text-gray-700">{p}</div>)}
      {poeText && <div className="text-[11px] text-gray-500 mt-0.5">PoE: {poeText}</div>}
    </div>
  );
}
