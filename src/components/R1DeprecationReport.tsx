import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/utils/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

interface Finding {
  severity: "critical" | "high" | "medium" | "info";
  method: string;
  path: string;
  reasons: string[];
  replacement?: string | null;
  removal_date?: string | null;
  days_left?: number | null;
  tools: string[];
  locations: string[];
  calls_30d: number;
}

interface LiveItem {
  severity: string;
  method: string;
  path: string;
  why: string;
  replacement?: string | null;
  last_seen?: string;
}

interface Report {
  generated_at: string;
  spec_file: string;
  spec_age_days: number;
  spec_stale: boolean;
  calls_checked: number;
  ok: number;
  counts: Record<string, number>;
  findings: Finding[];
  live: LiveItem[];
}

const TONE: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-amber-100 text-amber-800",
  medium: "bg-gray-100 text-gray-700",
  info: "bg-gray-50 text-gray-500",
};

/** The full deprecation audit: every flagged call, why, and where we make it. */
const R1DeprecationReport = () => {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async (run: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_BASE_URL}/r1-deprecations/${run ? "run" : "report"}`, {
        method: run ? "POST" : "GET",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setReport(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the deprecation report");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  return (
    <section className="mb-8">
      <h2 className="text-lg font-semibold">Deprecation watch</h2>
      <p className="text-xs text-gray-500 mb-2">
        The R1 calls in our code and the ones the tools made in the last 30 days, checked against the newest
        R1 spec in api/specs and the deprecation headers R1 puts on live responses. Runs daily at 13:00 UTC
        and sends no requests to R1.
      </p>
      <div className="flex items-center gap-3 mb-3 text-sm">
        <button
          onClick={() => load(true)}
          disabled={busy}
          className="px-3 py-1.5 bg-blue-600 text-white rounded-md disabled:opacity-50"
        >
          {busy ? "Checking..." : "Run check now"}
        </button>
        {report && (
          <span className="text-gray-600">
            {report.calls_checked} calls, {report.ok} clean · critical {report.counts.critical ?? 0} · high{" "}
            {report.counts.high ?? 0} · medium {report.counts.medium ?? 0} · {report.spec_file} (
            {report.spec_age_days} days old{report.spec_stale ? ": upload a newer one" : ""}) · checked{" "}
            {new Date(report.generated_at).toLocaleString()}
          </span>
        )}
      </div>
      {error && <p className="text-sm text-red-600 mb-2">{error}</p>}

      {report && report.live.length > 0 && (
        <div className="mb-3">
          <h3 className="text-sm font-semibold mb-1">Flagged by R1 on live responses</h3>
          <ul className="text-xs space-y-0.5">
            {report.live.map((l) => (
              <li key={`${l.method} ${l.path}`}>
                <span className={`px-1.5 rounded ${TONE[l.severity] ?? TONE.medium}`}>{l.severity}</span>{" "}
                <span className="font-mono">{l.method} {l.path}</span>: {l.why}
                {l.replacement && <span className="font-mono"> → {l.replacement}</span>}
                {l.last_seen && <span className="text-gray-500"> · last {new Date(l.last_seen).toLocaleString()}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report && report.findings.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="text-left border-b">
                <th className="py-1 pr-3">Severity</th>
                <th className="py-1 pr-3">Call</th>
                <th className="py-1 pr-3">Why</th>
                <th className="py-1 pr-3">Use instead</th>
                <th className="py-1 pr-3 text-right">Calls (30d)</th>
                <th className="py-1">Used by</th>
              </tr>
            </thead>
            <tbody>
              {report.findings.map((f) => {
                const key = `${f.method} ${f.path}`;
                return (
                  <tr
                    key={key}
                    onClick={() => setOpen(open === key ? null : key)}
                    className="border-b border-gray-100 align-top cursor-pointer hover:bg-gray-50"
                  >
                    <td className="py-1 pr-3">
                      <span className={`px-1.5 rounded ${TONE[f.severity]}`}>{f.severity}</span>
                    </td>
                    <td className="py-1 pr-3 font-mono whitespace-nowrap">{key}</td>
                    <td className="py-1 pr-3">
                      {f.reasons.join("; ")}
                      {open === key && f.locations.length > 0 && (
                        <div className="mt-1 font-mono text-gray-500">{f.locations.join(" · ")}</div>
                      )}
                    </td>
                    <td className="py-1 pr-3 font-mono">{f.replacement ?? ""}</td>
                    <td className="py-1 pr-3 text-right">{f.calls_30d.toLocaleString()}</td>
                    <td className="py-1 text-gray-600">{f.tools.join(", ") || "runtime path"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export default R1DeprecationReport;
