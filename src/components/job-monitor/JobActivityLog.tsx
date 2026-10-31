import { useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '@/utils/api';
import { matchesSnippet } from '@/utils/textMatch';
import { resourceLabel } from './format';

const API_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const POLL_MS = 3000;
const PAGE_ROWS = 200;

interface ActivityRow {
  position: number;
  time: string;
  action: string;
  type: string;
  name: string;
  id?: string | null;
  phase_id: string;
  unit_id?: string | null;
  detail?: string;
}

const ACTION_STYLES: Record<string, string> = {
  created: 'bg-green-100 text-green-800',
  updated: 'bg-blue-100 text-blue-800',
  reused: 'bg-gray-100 text-gray-700',
  skipped: 'bg-gray-100 text-gray-500',
  failed: 'bg-red-100 text-red-800',
};

interface JobActivityLogProps {
  jobId: string;
  live: boolean;
  phaseNames: Record<string, string>;
  rawMessages: string[];
}

const JobActivityLog = ({ jobId, live, phaseNames, rawMessages }: JobActivityLogProps) => {
  const [tab, setTab] = useState<'objects' | 'raw'>('objects');
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [dropped, setDropped] = useState(0);
  const [action, setAction] = useState('');
  const [type, setType] = useState('');
  const [filter, setFilter] = useState('');
  const [shown, setShown] = useState(PAGE_ROWS);
  // Set while reading further down: rows after this stay out of the table, so
  // what's being read doesn't slide away as new ones arrive.
  const [heldAt, setHeldAt] = useState<number | null>(null);
  const positionRef = useRef(0);

  useEffect(() => {
    setRows([]);
    setDropped(0);
    setHeldAt(null);
    positionRef.current = 0;
  }, [jobId]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const read = async () => {
      try {
        // A page short of the limit means we've caught up.
        for (;;) {
          const res = await apiFetch(`${API_URL}/jobs/${jobId}/activity?position=${positionRef.current}`);
          if (!res.ok || stopped) break;
          const data = await res.json();
          if (stopped) return;
          const added: ActivityRow[] = data.rows.map((row: Omit<ActivityRow, 'position'>, idx: number) => ({
            ...row, position: data.first_position + idx,
          }));
          positionRef.current = data.next_position;
          setDropped(data.dropped);
          if (added.length) setRows((prev) => [...prev, ...added]);
          if (added.length < 2000) break;
        }
      } catch {
        // The next read picks up from the same position.
      }
      if (live && !stopped) timer = setTimeout(read, POLL_MS);
    };

    read();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, live]);

  const types = useMemo(() => [...new Set(rows.map((r) => r.type))].sort(), [rows]);
  const actions = useMemo(() => [...new Set(rows.map((r) => r.action))].sort(), [rows]);

  const matching = useMemo(() => {
    const q = filter.trim();
    return rows
      .filter((r) => (!action || r.action === action) && (!type || r.type === type)
        && (!q || matchesSnippet(`${r.name} ${r.id ?? ''} ${r.detail ?? ''}`, q)))
      .reverse();
  }, [rows, action, type, filter]);

  const visible = heldAt === null ? matching : matching.filter((r) => r.position <= heldAt);
  const waitingRows = matching.length - visible.length;
  const newest = matching[0]?.position ?? 0;

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const scrolled = e.currentTarget.scrollTop > 0;
    if (scrolled && heldAt === null) setHeldAt(newest);
    if (!scrolled && heldAt !== null) setHeldAt(null);
  };

  return (
    <div className="mt-6 bg-white rounded-lg shadow">
      <div className="p-4 border-b border-gray-200 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-bold text-gray-900 mr-2">Activity</h2>
        <div className="flex rounded border border-gray-200 overflow-hidden text-sm">
          <button
            onClick={() => setTab('objects')}
            className={`px-3 py-1 ${tab === 'objects' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            Objects ({rows.length.toLocaleString()})
          </button>
          <button
            onClick={() => setTab('raw')}
            className={`px-3 py-1 ${tab === 'raw' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            Raw messages
          </button>
        </div>
        {tab === 'objects' && (
          <>
            <select value={action} onChange={(e) => setAction(e.target.value)} className="border rounded px-2 py-1 text-sm">
              <option value="">All actions</option>
              {actions.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select value={type} onChange={(e) => setType(e.target.value)} className="border rounded px-2 py-1 text-sm">
              <option value="">All types</option>
              {types.map((t) => <option key={t} value={t}>{resourceLabel(t)}</option>)}
            </select>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by name, id or detail…"
              className="border rounded px-3 py-1 text-sm w-full sm:w-64"
            />
            <span className="ml-auto text-xs text-gray-500">
              {matching.length === rows.length
                ? `${rows.length.toLocaleString()} rows`
                : `${matching.length.toLocaleString()} of ${rows.length.toLocaleString()} rows`}
            </span>
          </>
        )}
      </div>

      {tab === 'raw' ? (
        <div className="p-4 max-h-[420px] overflow-y-auto space-y-1">
          {rawMessages.length === 0 ? (
            <p className="text-sm text-gray-500">No messages since this page opened.</p>
          ) : rawMessages.map((message, idx) => (
            <div key={idx} className="text-xs font-mono text-gray-700 py-0.5">{message}</div>
          ))}
        </div>
      ) : (
        <>
          {dropped > 0 && (
            <p className="px-4 py-2 text-xs text-amber-800 bg-amber-50 border-b border-amber-200">
              Showing the latest rows; the oldest {dropped.toLocaleString()} were dropped. The totals above still count them.
            </p>
          )}
          {waitingRows > 0 && (
            <p className="px-4 py-1 text-xs text-blue-800 bg-blue-50 border-b border-blue-200">
              {waitingRows.toLocaleString()} new row{waitingRows === 1 ? '' : 's'} — scroll to the top to see {waitingRows === 1 ? 'it' : 'them'}.
            </p>
          )}
          <div onScroll={onScroll} className="max-h-[420px] overflow-auto">
            {rows.length === 0 ? (
              <p className="p-4 text-sm text-gray-500">No objects created or changed yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-gray-50 text-left text-xs text-gray-500">
                  <tr>
                    <th className="px-4 py-2 font-medium">Time</th>
                    <th className="px-4 py-2 font-medium">Action</th>
                    <th className="px-4 py-2 font-medium">Type</th>
                    <th className="px-4 py-2 font-medium">Name</th>
                    <th className="px-4 py-2 font-medium">Phase</th>
                    <th className="px-4 py-2 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {visible.slice(0, shown).map((row) => (
                    <tr key={row.position} className="align-top">
                      <td className="px-4 py-1.5 text-xs text-gray-500 whitespace-nowrap">{new Date(row.time).toLocaleTimeString()}</td>
                      <td className="px-4 py-1.5">
                        <span className={`text-xs px-2 py-0.5 rounded ${ACTION_STYLES[row.action] ?? 'bg-gray-100 text-gray-700'}`}>{row.action}</span>
                      </td>
                      <td className="px-4 py-1.5 text-xs text-gray-600 whitespace-nowrap">{resourceLabel(row.type)}</td>
                      <td className="px-4 py-1.5 break-all" title={row.id ?? undefined}>{row.name}</td>
                      <td className="px-4 py-1.5 text-xs text-gray-600 whitespace-nowrap">{phaseNames[row.phase_id] ?? row.phase_id}</td>
                      <td className="px-4 py-1.5 text-xs text-gray-600 break-words">{row.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {visible.length > shown && (
              <button
                onClick={() => setShown(shown + PAGE_ROWS)}
                className="w-full py-2 text-sm text-blue-700 hover:bg-blue-50"
              >
                Show more ({(visible.length - shown).toLocaleString()} left)
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default JobActivityLog;
