import { Printer, CheckCircle, XCircle, AlertTriangle, Server, Target, ArrowRight } from 'lucide-react';
import type { WizardState } from '@/types/szConfigMigration';
import type { MigrationSessionSummary } from '@/hooks/useMigrationSession';

// ── Report Data Interface ─────────────────────────────────────────

interface MigrationReportData {
  szZoneName: string;
  r1VenueName: string;
  status: string;
  startedAt: string | null;
  completedAt: string | null;
  totalWlans: number;
  completedWlans: number;
  failedWlans: number;
  phases: Array<{
    id: string;
    name: string;
    status: string;
    completed: number;
    failed: number;
    total: number;
  }>;
  createdResources: Record<string, Array<{
    id: string;
    name: string;
    type?: string;
    reused?: boolean;
  }>>;
  units: Array<{
    wlan_name: string;
    ssid: string;
    r1_network_type: string;
    status: string;
    reused: boolean;
    network_name: string | null;
    network_id: string | null;
    activated: boolean;
    error: string | null;
  }>;
  errors: string[];
}

// ── Data Adapters ─────────────────────────────────────────────────

interface JobStatus {
  job_id: string;
  status: string;
  progress: {
    total_tasks: number;
    completed: number;
    failed: number;
    pending: number;
    percent: number;
    phase_stats: Record<string, any>;
  };
  phases: Array<{ id: string; name: string; status: string }>;
  created_resources: Record<string, Array<{ id: string; name: string; type?: string; reused?: boolean }>>;
  errors: string[];
}

export function buildReportFromJob(job: JobStatus, state: WizardState): MigrationReportData {
  const networkPhase = job.progress.phase_stats?.create_networks;
  return {
    szZoneName: state.selectedZoneName || '',
    r1VenueName: state.destVenueName || '',
    status: job.status,
    startedAt: null,
    completedAt: new Date().toISOString(),
    totalWlans: networkPhase?.total || state.planResult?.unit_count || 0,
    completedWlans: networkPhase?.completed || 0,
    failedWlans: networkPhase?.failed || 0,
    phases: job.phases.map(p => {
      const stats = job.progress.phase_stats?.[p.id] || {};
      return {
        id: p.id,
        name: p.name,
        status: p.status,
        completed: stats.completed || 0,
        failed: stats.failed || 0,
        total: stats.total || 0,
      };
    }),
    createdResources: job.created_resources || {},
    units: [], // Per-unit detail not available from live job status
    errors: job.errors || [],
  };
}

export function buildReportFromSession(session: MigrationSessionSummary): MigrationReportData | null {
  const execution = session.summary_json?.execution;
  if (!execution) return null;

  const progress = execution.progress || {};
  return {
    szZoneName: session.sz_zone_name || '',
    r1VenueName: session.r1_venue_name || '',
    status: execution.status || session.status,
    startedAt: execution.started_at || session.created_at,
    completedAt: execution.completed_at || session.updated_at,
    totalWlans: session.summary_json?.unit_count || session.wlan_count || 0,
    completedWlans: progress.completed || 0,
    failedWlans: progress.failed || 0,
    phases: execution.phases || [],
    createdResources: execution.created_resources || {},
    units: execution.units || [],
    errors: execution.errors || [],
  };
}

// ── Report Component ──────────────────────────────────────────────

interface Props {
  data: MigrationReportData;
  onClose: () => void;
}

const STATUS_STYLES: Record<string, string> = {
  COMPLETED: 'bg-green-100 text-green-800',
  FAILED: 'bg-red-100 text-red-800',
  PARTIAL: 'bg-amber-100 text-amber-800',
};

const PHASE_STATUS_STYLES: Record<string, string> = {
  COMPLETED: 'text-green-700 bg-green-50',
  FAILED: 'text-red-700 bg-red-50',
  RUNNING: 'text-blue-700 bg-blue-50',
  SKIPPED: 'text-gray-500 bg-gray-50',
  PENDING: 'text-gray-500 bg-gray-50',
};

const RESOURCE_LABELS: Record<string, string> = {
  wifi_networks: 'WiFi Networks',
  radius_profiles: 'RADIUS Profiles',
  dpsk_pools: 'DPSK Pools',
  identity_groups: 'Identity Groups',
};

export default function MigrationReport({ data, onClose }: Props) {
  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="mt-6">
      {/* Action buttons — hidden in print */}
      <div className="no-print flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-gray-800">Migration Report</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handlePrint}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm font-medium flex items-center gap-2"
          >
            <Printer size={14} />
            Print / Save as PDF
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
          >
            Close
          </button>
        </div>
      </div>

      {/* Report content — this is what prints */}
      <div className="bg-white rounded-lg shadow print:shadow-none print:rounded-none">
        {/* Header */}
        <div className="px-6 py-5 border-b">
          <h1 className="text-xl font-bold text-gray-900 mb-3">
            SZ → R1 Config Migration Report
          </h1>
          <div className="flex items-center gap-3 text-sm">
            <div className="flex items-center gap-1.5">
              <Server size={14} className="text-blue-600" />
              <span className="font-medium">{data.szZoneName || '—'}</span>
            </div>
            <ArrowRight size={14} className="text-gray-400" />
            <div className="flex items-center gap-1.5">
              <Target size={14} className="text-green-600" />
              <span className="font-medium">{data.r1VenueName || '—'}</span>
            </div>
            <span className={`ml-auto px-2 py-0.5 rounded text-xs font-bold ${STATUS_STYLES[data.status] || 'bg-gray-100 text-gray-700'}`}>
              {data.status}
            </span>
          </div>
          <div className="mt-2 text-xs text-gray-500 space-x-4">
            {data.startedAt && <span>Started: {new Date(data.startedAt).toLocaleString()}</span>}
            {data.completedAt && <span>Completed: {new Date(data.completedAt).toLocaleString()}</span>}
          </div>
        </div>

        {/* Summary cards */}
        <div className="px-6 py-4 border-b">
          <div className="grid grid-cols-3 gap-4">
            <SummaryCard label="Total WLANs" value={data.totalWlans} color="blue" />
            <SummaryCard label="Completed" value={data.completedWlans} color="green" />
            <SummaryCard label="Failed" value={data.failedWlans} color="red" />
          </div>
        </div>

        {/* Created Resources */}
        {Object.keys(data.createdResources).length > 0 && (
          <div className="px-6 py-4 border-b">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Created Resources</h2>
            <div className="space-y-3">
              {Object.entries(data.createdResources).map(([type, resources]) => {
                if (!resources || resources.length === 0) return null;
                const created = resources.filter(r => !r.reused);
                const reused = resources.filter(r => r.reused);
                return (
                  <div key={type}>
                    <div className="text-xs font-medium text-gray-600 mb-1">
                      {RESOURCE_LABELS[type] || type} ({resources.length})
                      {created.length > 0 && <span className="text-blue-600 ml-1">{created.length} new</span>}
                      {reused.length > 0 && <span className="text-green-600 ml-1">{reused.length} reused</span>}
                    </div>
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-left text-gray-500 border-b">
                          <th className="py-1 pr-4">Name</th>
                          <th className="py-1 pr-4">ID</th>
                          <th className="py-1">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resources.map((r, i) => (
                          <tr key={i} className="border-b border-gray-50">
                            <td className="py-1 pr-4 font-medium">{r.name}</td>
                            <td className="py-1 pr-4 font-mono text-gray-400 text-[10px]">{r.id?.slice(0, 16)}</td>
                            <td className="py-1">
                              {r.reused ? (
                                <span className="text-green-600 font-medium">Reused</span>
                              ) : (
                                <span className="text-blue-600 font-medium">Created</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* WLAN Detail Table */}
        {data.units.length > 0 && (
          <div className="px-6 py-4 border-b">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">
              WLAN Details ({data.units.length})
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500 border-b">
                    <th className="py-1.5 pr-3">WLAN Name</th>
                    <th className="py-1.5 pr-3">SSID</th>
                    <th className="py-1.5 pr-3">Type</th>
                    <th className="py-1.5 pr-3">Status</th>
                    <th className="py-1.5 pr-3">Action</th>
                    <th className="py-1.5 pr-3">R1 Network</th>
                    <th className="py-1.5 pr-3">Activated</th>
                    <th className="py-1.5">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {data.units.map((unit, i) => (
                    <tr key={i} className="border-b border-gray-50">
                      <td className="py-1.5 pr-3 font-medium">{unit.wlan_name}</td>
                      <td className="py-1.5 pr-3 text-gray-600">{unit.ssid}</td>
                      <td className="py-1.5 pr-3">
                        <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 font-medium">
                          {unit.r1_network_type}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3">
                        {unit.status === 'COMPLETED' ? (
                          <span className="flex items-center gap-1 text-green-600">
                            <CheckCircle size={11} /> OK
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-red-600">
                            <XCircle size={11} /> Failed
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3">
                        {unit.reused ? (
                          <span className="text-green-600">Reused</span>
                        ) : (
                          <span className="text-blue-600">Created</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-gray-500 text-[10px]">
                        {unit.network_name || '—'}
                      </td>
                      <td className="py-1.5 pr-3">
                        {unit.activated ? 'Yes' : 'No'}
                      </td>
                      <td className="py-1.5 text-red-600 max-w-xs truncate">
                        {unit.error || ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Phase Breakdown */}
        {data.phases.length > 0 && (
          <div className="px-6 py-4 border-b">
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Workflow Phases</h2>
            <div className="space-y-1">
              {data.phases.map(phase => (
                <div
                  key={phase.id}
                  className="flex items-center justify-between text-xs px-3 py-1.5 rounded border border-gray-100"
                >
                  <div className="flex items-center gap-2">
                    {phase.status === 'COMPLETED' && <CheckCircle size={12} className="text-green-600" />}
                    {phase.status === 'FAILED' && <XCircle size={12} className="text-red-600" />}
                    {phase.status === 'SKIPPED' && <span className="w-3 h-3 rounded-full border-2 border-gray-300" />}
                    {!['COMPLETED', 'FAILED', 'SKIPPED'].includes(phase.status) && (
                      <span className="w-3 h-3 rounded-full border-2 border-gray-300" />
                    )}
                    <span className="font-medium">{phase.name}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {phase.total > 0 && (
                      <span className="text-gray-500 font-mono">
                        {phase.completed}/{phase.total}
                        {phase.failed > 0 && <span className="text-red-500 ml-1">({phase.failed} failed)</span>}
                      </span>
                    )}
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${PHASE_STATUS_STYLES[phase.status] || PHASE_STATUS_STYLES.PENDING}`}>
                      {phase.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Errors */}
        {data.errors.length > 0 && (
          <div className="px-6 py-4 border-b">
            <h2 className="text-sm font-semibold text-red-700 mb-2">Errors ({data.errors.length})</h2>
            <div className="space-y-1">
              {data.errors.map((err, i) => (
                <div key={i} className="text-xs text-red-700 flex items-start gap-1.5">
                  <AlertTriangle size={11} className="mt-0.5 flex-shrink-0" />
                  <span>{err}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-6 py-3 text-[10px] text-gray-400">
          Generated {new Date().toLocaleString()} by rtools2 Migration Engine
        </div>
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    green: 'bg-green-50 border-green-200 text-green-700',
    red: 'bg-red-50 border-red-200 text-red-700',
  };
  return (
    <div className={`border rounded-lg p-3 text-center ${colors[color] || colors.blue}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
