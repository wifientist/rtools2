import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface TimingRow {
  kind: string;
  label: string;
  status: string;
  count: number;
  total_seconds: number;
  average_seconds: number;
  median_under_seconds: number;
  p90_under_seconds: number;
  max_seconds: number;
}

interface HourRow {
  hour_utc: string;
  count: number;
  average_seconds: number;
  median_under_seconds: number;
  p90_under_seconds: number;
  max_seconds: number;
}

const SECTIONS = [
  { kind: "async_seen", title: "Seconds until we saw it done", note: "From R1's 202 until a poll of ours first read it as finished: the wait a job sits through, including up to a few seconds between polls. Only calls something polled." },
  { kind: "async", title: "Seconds until done in R1", note: "From R1's 202 to the activity's end time, which R1 gives in whole seconds. Only calls something polled." },
  { kind: "async_work", title: "R1's own working time", note: "The activity's start time to its end time. A gap from the table above is time spent waiting in R1, but only past a few seconds: R1's start can land before its 202 reaches us." },
  { kind: "ready", title: "Seconds until ready", note: "Calls with no activity to read: from the 202 until a read shows them finished." },
  { kind: "call", title: "Every call", note: "Send to response, one row per route and result." },
  { kind: "paced", title: "Waiting on our own rate limit", note: "Calls held back to stay under R1's limit, before they were sent." },
];

const RANGES = [
  { hours: 1, label: "Last hour" },
  { hours: 24, label: "Last 24 hours" },
  { hours: 168, label: "Last 7 days" },
  { hours: 720, label: "Last 30 days" },
];

const rowKey = (row: TimingRow) => `${row.kind}|${row.label}|${row.status}`;

const R1CallTiming = () => {
  const [hours, setHours] = useState(24);
  const [rows, setRows] = useState<TimingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [hourRows, setHourRows] = useState<HourRow[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`${API_BASE_URL}/r1-call-timing/summary?hours=${hours}`);
      if (!response.ok) throw new Error(`Could not load timing (${response.status})`);
      const data = await response.json();
      setRows(data.rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load timing");
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleHours = async (row: TimingRow) => {
    const key = rowKey(row);
    if (openKey === key) {
      setOpenKey(null);
      return;
    }
    setOpenKey(key);
    setHourRows([]);
    try {
      const query = new URLSearchParams({ kind: row.kind, label: row.label, status: row.status, hours: "720" });
      const response = await apiFetch(`${API_BASE_URL}/r1-call-timing/hourly?${query}`);
      if (!response.ok) throw new Error(`Could not load hours (${response.status})`);
      setHourRows((await response.json()).rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load hours");
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-4">
      <h1 className="text-2xl font-bold mb-1">R1 Call Timing</h1>
      <p className="text-sm text-gray-600 mb-4">
        In seconds, kept for 30 days and saved every 30 seconds. Select a row to see it per UTC hour.
      </p>
      <div className="flex items-center gap-3 mb-4">
        <select
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          {RANGES.map((r) => (
            <option key={r.hours} value={r.hours}>{r.label}</option>
          ))}
        </select>
        <button onClick={load} disabled={loading} className="px-3 py-2 bg-blue-600 text-white rounded-md text-sm disabled:opacity-50">
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>
      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {SECTIONS.map((section) => {
        const sectionRows = rows.filter((r) => r.kind === section.kind);
        return (
          <section key={section.kind} className="mb-8">
            <h2 className="text-lg font-semibold">{section.title}</h2>
            <p className="text-xs text-gray-500 mb-2">{section.note}</p>
            {sectionRows.length === 0 ? (
              <p className="text-sm text-gray-500">Nothing recorded in this range.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left border-b">
                      <th className="py-1 pr-3">Call</th>
                      <th className="py-1 pr-3">Result</th>
                      <th className="py-1 pr-3 text-right">Count</th>
                      <th className="py-1 pr-3 text-right">Average</th>
                      <th className="py-1 pr-3 text-right">Half under</th>
                      <th className="py-1 pr-3 text-right">90% under</th>
                      <th className="py-1 pr-3 text-right">Max</th>
                      <th className="py-1 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sectionRows.map((row) => (
                      <RowWithHours
                        key={rowKey(row)}
                        row={row}
                        open={openKey === rowKey(row)}
                        hourRows={hourRows}
                        onToggle={() => toggleHours(row)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
};

const RowWithHours = ({
  row,
  open,
  hourRows,
  onToggle,
}: {
  row: TimingRow;
  open: boolean;
  hourRows: HourRow[];
  onToggle: () => void;
}) => (
  <>
    <tr className="border-b hover:bg-gray-50">
      <td className="py-1 pr-3 font-mono text-xs break-all">
        <button onClick={onToggle} aria-expanded={open} className="text-left text-blue-700 hover:underline">
          {row.label}
        </button>
      </td>
      <td className="py-1 pr-3">{row.status}</td>
      <td className="py-1 pr-3 text-right">{row.count}</td>
      <td className="py-1 pr-3 text-right">{row.average_seconds}</td>
      <td className="py-1 pr-3 text-right">{row.median_under_seconds}</td>
      <td className="py-1 pr-3 text-right">{row.p90_under_seconds}</td>
      <td className="py-1 pr-3 text-right">{row.max_seconds}</td>
      <td className="py-1 text-right">{row.total_seconds}</td>
    </tr>
    {open && (
      <tr className="border-b bg-gray-50">
        <td colSpan={8} className="p-2">
          {hourRows.length === 0 ? (
            <p className="text-xs text-gray-500">No hours to show.</p>
          ) : (
            <table className="text-xs">
              <thead>
                <tr className="text-left">
                  <th className="pr-4">Hour (UTC)</th>
                  <th className="pr-4 text-right">Count</th>
                  <th className="pr-4 text-right">Average</th>
                  <th className="pr-4 text-right">Half under</th>
                  <th className="pr-4 text-right">90% under</th>
                  <th className="text-right">Max</th>
                </tr>
              </thead>
              <tbody>
                {hourRows.map((hour) => (
                  <tr key={hour.hour_utc}>
                    <td className="pr-4 font-mono">{hour.hour_utc}</td>
                    <td className="pr-4 text-right">{hour.count}</td>
                    <td className="pr-4 text-right">{hour.average_seconds}</td>
                    <td className="pr-4 text-right">{hour.median_under_seconds}</td>
                    <td className="pr-4 text-right">{hour.p90_under_seconds}</td>
                    <td className="text-right">{hour.max_seconds}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </td>
      </tr>
    )}
  </>
);

export default R1CallTiming;
