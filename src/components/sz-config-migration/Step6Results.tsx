import { useState, useEffect } from 'react';
import {
  Download, CheckCircle, XCircle, AlertTriangle, Loader2, RotateCcw,
  Wifi, Shield, Key, Users, Printer, FileText, SearchCheck,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';
import MigrationReport, { buildReportFromJob } from './MigrationReport';
import type { WizardState, WizardAction } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
  dispatch: React.Dispatch<WizardAction>;
}

interface PhaseStats {
  name: string;
  status?: string;
  completed?: number;
  failed?: number;
  running?: number;
  pending?: number;
  total?: number;
}

interface CreatedResource {
  id: string;
  name: string;
  type?: string;
  reused?: boolean;
}

interface JobStatus {
  job_id: string;
  status: string;
  progress: {
    total_tasks: number;
    completed: number;
    failed: number;
    pending: number;
    percent: number;
    phase_stats: Record<string, PhaseStats>;
  };
  phases: Array<{
    id: string;
    name: string;
    status: string;
  }>;
  created_resources: Record<string, CreatedResource[]>;
  errors: string[];
}

export default function Step6Results({ state, dispatch }: Props) {
  const [result, setResult] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [showReport, setShowReport] = useState(false);

  useEffect(() => {
    if (state.finalJobId) {
      fetchResults();
    }
  }, [state.finalJobId]);

  const fetchResults = async () => {
    if (!state.finalJobId) return;
    setLoading(true);

    try {
      const res = await apiFetch(`${API_URL}/jobs/${state.finalJobId}/status`);
      if (res.ok) {
        const data = await res.json();
        setResult(data);
      }
    } catch {
      // Non-critical
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-8 flex flex-col items-center">
        <Loader2 size={32} className="text-blue-600 animate-spin mb-3" />
        <p className="text-gray-600">Loading results...</p>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <p className="text-gray-500">No results available yet.</p>
        <button
          onClick={fetchResults}
          className="mt-3 px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
        >
          Refresh
        </button>
      </div>
    );
  }

  const isCompleted = result.status === 'COMPLETED';
  const isFailed = result.status === 'FAILED';
  const isPartial = result.status === 'PARTIAL';

  // Get WLAN-specific phase stats (create_networks is the best proxy for per-WLAN progress)
  const networkPhase = result.progress.phase_stats?.create_networks;
  const wlanTotal = networkPhase?.total || state.planResult?.unit_count || 0;
  const wlanCompleted = networkPhase?.completed || 0;
  const wlanFailed = networkPhase?.failed || 0;

  // Created resources
  const createdNetworks = result.created_resources?.wifi_networks || [];
  const createdRadius = result.created_resources?.radius_profiles || [];
  const createdDpskPools = result.created_resources?.dpsk_pools || [];
  const createdIdentityGroups = result.created_resources?.identity_groups || [];

  return (
    <div className="space-y-6">
      {/* Status header */}
      <div className={`rounded-lg shadow p-6 ${
        isCompleted ? 'bg-green-50 border border-green-200' :
        isFailed ? 'bg-red-50 border border-red-200' :
        'bg-amber-50 border border-amber-200'
      }`}>
        <div className="flex items-center gap-3 mb-4">
          {isCompleted && <CheckCircle size={28} className="text-green-600" />}
          {isFailed && <XCircle size={28} className="text-red-600" />}
          {isPartial && <AlertTriangle size={28} className="text-amber-600" />}
          <div>
            <h3 className="text-xl font-bold">
              {isCompleted ? 'Migration Complete' :
               isFailed ? 'Migration Failed' :
               'Migration Partially Complete'}
            </h3>
            <p className="text-sm text-gray-500 font-mono">{result.job_id}</p>
          </div>
        </div>

        {/* WLAN progress */}
        <div className="mb-4">
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="text-gray-600">WLAN Progress</span>
            <span className="font-medium">{wlanCompleted}/{wlanTotal} completed</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
            <div
              className={`h-full transition-all ${
                wlanFailed > 0 ? 'bg-gradient-to-r from-green-500 to-red-500' : 'bg-green-500'
              }`}
              style={{ width: `${wlanTotal > 0 ? Math.round((wlanCompleted / wlanTotal) * 100) : 0}%` }}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4 text-sm">
          <div className="text-center">
            <div className="text-2xl font-bold">{wlanTotal}</div>
            <div className="text-xs text-gray-600">Total WLANs</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-green-600">{wlanCompleted}</div>
            <div className="text-xs text-gray-600">Completed</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-red-600">{wlanFailed}</div>
            <div className="text-xs text-gray-600">Failed</div>
          </div>
        </div>
      </div>

      {/* Phase breakdown */}
      <div className="bg-white rounded-lg shadow p-6">
        <h4 className="font-semibold mb-3">Workflow Phases</h4>
        <div className="space-y-2">
          {result.phases.map(phase => {
            const stats = result.progress.phase_stats?.[phase.id];
            const isPerUnit = stats && 'total' in stats;
            return (
              <div
                key={phase.id}
                className={`border rounded-lg px-4 py-2.5 flex items-center justify-between ${
                  phase.status === 'COMPLETED' ? 'border-green-200 bg-green-50' :
                  phase.status === 'FAILED' ? 'border-red-200 bg-red-50' :
                  phase.status === 'SKIPPED' ? 'border-gray-200 bg-gray-50' :
                  'border-gray-200'
                }`}
              >
                <div className="flex items-center gap-2">
                  {phase.status === 'COMPLETED' && <CheckCircle size={14} className="text-green-600" />}
                  {phase.status === 'FAILED' && <XCircle size={14} className="text-red-600" />}
                  {phase.status === 'SKIPPED' && <span className="text-xs text-gray-400">SKIP</span>}
                  {phase.status === 'PENDING' && <span className="w-3.5 h-3.5 rounded-full border-2 border-gray-300" />}
                  <span className="text-sm font-medium">{phase.name}</span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  {isPerUnit && (
                    <span className="text-gray-500 font-mono">
                      {stats.completed}/{stats.total}
                      {stats.failed ? <span className="text-red-500 ml-1">({stats.failed} failed)</span> : ''}
                    </span>
                  )}
                  <span className={`px-1.5 py-0.5 rounded font-medium ${
                    phase.status === 'COMPLETED' ? 'bg-green-100 text-green-700' :
                    phase.status === 'FAILED' ? 'bg-red-100 text-red-700' :
                    phase.status === 'SKIPPED' ? 'bg-gray-100 text-gray-500' :
                    phase.status === 'RUNNING' ? 'bg-blue-100 text-blue-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {phase.status}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Created resources */}
      {(createdNetworks.length > 0 || createdRadius.length > 0 || createdDpskPools.length > 0) && (
        <div className="bg-white rounded-lg shadow p-6">
          <h4 className="font-semibold mb-3">Created Resources</h4>
          <div className="space-y-4">
            {/* WiFi Networks */}
            {createdNetworks.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-2">
                  <Wifi size={14} />
                  WiFi Networks ({createdNetworks.length})
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {createdNetworks.map(net => (
                    <div key={net.id} className="flex items-center gap-2 text-xs bg-gray-50 rounded px-3 py-1.5">
                      {net.reused ? (
                        <span className="text-green-600 font-medium">REUSE</span>
                      ) : (
                        <span className="text-blue-600 font-medium">NEW</span>
                      )}
                      <span className="font-medium">{net.name}</span>
                      {net.type && (
                        <span className="text-gray-400">({net.type})</span>
                      )}
                      <span className="text-gray-300 font-mono text-[10px] ml-auto">{net.id.slice(0, 12)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* RADIUS profiles */}
            {createdRadius.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-2">
                  <Shield size={14} />
                  RADIUS Profiles ({createdRadius.length})
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {createdRadius.map(r => (
                    <div key={r.id} className="text-xs bg-gray-50 rounded px-3 py-1.5">
                      <span className="font-medium">{r.name}</span>
                      <span className="text-gray-300 font-mono text-[10px] ml-2">{r.id.slice(0, 12)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* DPSK Pools */}
            {createdDpskPools.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-2">
                  <Key size={14} />
                  DPSK Pools ({createdDpskPools.length})
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {createdDpskPools.map(p => (
                    <div key={p.id} className="text-xs bg-gray-50 rounded px-3 py-1.5">
                      <span className="font-medium">{p.name}</span>
                      <span className="text-gray-300 font-mono text-[10px] ml-2">{p.id.slice(0, 12)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Identity Groups */}
            {createdIdentityGroups.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-2">
                  <Users size={14} />
                  Identity Groups ({createdIdentityGroups.length})
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {createdIdentityGroups.map(ig => (
                    <div key={ig.id} className="text-xs bg-gray-50 rounded px-3 py-1.5">
                      <span className="font-medium">{ig.name}</span>
                      <span className="text-gray-300 font-mono text-[10px] ml-2">{ig.id.slice(0, 12)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Errors */}
      {result.errors.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg shadow p-6">
          <h4 className="font-semibold text-red-800 mb-2">Errors</h4>
          <div className="space-y-1">
            {result.errors.map((err, i) => (
              <div key={i} className="text-xs text-red-700 flex items-start gap-1.5">
                <XCircle size={12} className="mt-0.5 flex-shrink-0" />
                <span>{err}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={fetchResults}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium flex items-center gap-2"
        >
          <RotateCcw size={14} />
          Refresh
        </button>
        {state.extractionJobId && (
          <button
            onClick={() => window.open(`${API_URL}/sz-migration/snapshot/${state.extractionJobId}/download`, '_blank')}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium flex items-center gap-2"
          >
            <Download size={14} />
            SZ Snapshot
          </button>
        )}
        {state.sessionId && (
          <button
            onClick={() => window.open(`${API_URL}/sz-migration/sessions/${state.sessionId}/report.csv`, '_blank')}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium flex items-center gap-2"
          >
            <FileText size={14} />
            Export CSV
          </button>
        )}
        <button
          onClick={() => setShowReport(v => !v)}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium flex items-center gap-2"
        >
          <Printer size={14} />
          {showReport ? 'Hide Report' : 'Print Report'}
        </button>
        <a
          href={`/jobs/${state.finalJobId}`}
          target="_blank"
          rel="noopener noreferrer"
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm font-medium"
        >
          Full Job Monitor
        </a>
        {state.destVenueId && (state.extractionJobId || state.selectedZoneId) && (
          <a
            href={`/migration-audit?${state.extractionJobId ? `sz_snapshot=${state.extractionJobId}&` : ''}${state.selectedZoneId ? `zone_id=${state.selectedZoneId}&` : ''}venue_id=${state.destVenueId}`}
            className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 text-sm font-medium flex items-center gap-2"
          >
            <SearchCheck size={14} />
            Audit Migration
          </a>
        )}
        <button
          onClick={() => dispatch({ type: 'RESET' })}
          className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
        >
          Start New Migration
        </button>
      </div>

      {/* Printable report */}
      {showReport && result && (
        <MigrationReport
          data={buildReportFromJob(result, state)}
          onClose={() => setShowReport(false)}
        />
      )}
    </div>
  );
}
