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

interface Outcome {
  status: string;
  units: number;
  units_with_failures: number;
  activated: number;
  to_activate: number;
  staged: string[];
  created_only: string[];
  duration_seconds?: number | null;
  failures: Array<{ phase: string; message: string; count: number; wlans: string[] }>;
  networks?: { created: number; reused: number };
  ap_groups?: { created: number; existing: number; failed: string[] };
  resources?: Record<string, number>;
}

const took = (sec?: number | null) => {
  if (sec == null) return '';
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), x = sec % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${x}s` : `${x}s`;
};

// A results section: one stage of the wizard, in order.
function StageCard({ n, title, status, children }: {
  n: number; title: string; status: 'ok' | 'warn' | 'fail' | 'skip'; children: React.ReactNode;
}) {
  const tone = { ok: 'border-green-200', warn: 'border-amber-300', fail: 'border-red-300', skip: 'border-gray-200' }[status];
  const icon = { ok: <CheckCircle size={16} className="text-green-600" />, warn: <AlertTriangle size={16} className="text-amber-600" />,
    fail: <XCircle size={16} className="text-red-600" />, skip: <span className="text-[10px] font-bold text-gray-400">SKIP</span> }[status];
  return (
    <div className={`bg-white rounded-lg shadow border-l-4 ${tone} p-5`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="w-6 h-6 rounded-full bg-gray-100 text-gray-600 text-xs font-bold flex items-center justify-center">{n}</span>
        <h4 className="font-semibold">{title}</h4>
        <span className="ml-auto">{icon}</span>
      </div>
      {children}
    </div>
  );
}

function Tile({ value, label, tone = 'text-gray-800' }: { value: React.ReactNode; label: string; tone?: string }) {
  return (
    <div className="text-center bg-gray-50 rounded p-2">
      <div className={`text-2xl font-bold ${tone}`}>{value}</div>
      <div className="text-xs text-gray-600">{label}</div>
    </div>
  );
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
  // What became of each WLAN: activated / staged / created only -- so the
  // count matches what R1 shows activated in the venue.
  const [breakdown, setBreakdown] = useState<Outcome | null>(null);
  // The other stages' own read-backs, for their sections.
  const [venueReport, setVenueReport] = useState<any | null>(null);
  const [apReport, setApReport] = useState<any | null>(null);
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
    apiFetch(`${API_URL}/sz-migration/workflow/${state.finalJobId}/outcome`)
      .then(r => (r.ok ? r.json() : null)).then(d => d && setBreakdown(d)).catch(() => {});
    if (state.venueConfigJobId) {
      apiFetch(`${API_URL}/sz-migration/venue-config/${state.venueConfigJobId}/report`)
        .then(r => (r.ok ? r.json() : null)).then(d => d && setVenueReport(d)).catch(() => {});
    }
    if (state.apMappingJobId) {
      apiFetch(`${API_URL}/sz-migration/ap-mapping/${state.apMappingJobId}/report`)
        .then(r => (r.ok ? r.json() : null)).then(d => d && setApReport(d)).catch(() => {});
    }

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

        <div className="text-sm text-gray-600">
          {state.selectedZoneName} → {state.destVenueName}
          {breakdown?.duration_seconds != null && <> · Create WLANs took {took(breakdown.duration_seconds)}</>}
        </div>
      </div>

      {/* 1. Venue Configuration */}
      {(() => {
        const verify: any[] = venueReport?.verify ?? [];
        const ok = verify.filter(v => v.status === 'verified').length;
        const off = verify.filter(v => v.status !== 'verified');
        return (
          <StageCard n={1} title="Venue Configuration"
                     status={!state.venueConfigJobId ? 'skip' : !verify.length ? 'warn' : off.length ? 'warn' : 'ok'}>
            {!state.venueConfigJobId ? (
              <div className="text-sm text-gray-500">Not run — the venue's settings were left as R1 had them.</div>
            ) : !verify.length ? (
              <div className="text-sm text-gray-500">No read-back yet{venueReport?.status ? ` (job ${venueReport.status})` : ''}.</div>
            ) : (
              <>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm mb-2">
                  <Tile value={<>{ok}<span className="text-sm text-gray-400"> / {verify.length}</span></>} label="Sections verified"
                        tone={off.length ? 'text-amber-600' : 'text-green-600'} />
                  <Tile value={off.length} label="Not as the zone sets them" tone={off.length ? 'text-amber-600' : 'text-gray-400'} />
                </div>
                {off.length > 0 && (
                  <ul className="text-xs text-amber-800 space-y-0.5">
                    {off.map(v => (
                      <li key={v.key}><b>{v.label}:</b> {v.status === 'differs'
                        ? (v.differences ?? []).slice(0, 4).map((d: any) => d.field).join(', ') + ((v.differences?.length ?? 0) > 4 ? '…' : '')
                        : v.error}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </StageCard>
        );
      })()}

      {/* 2. WLANs */}
      <StageCard n={2} title="WLANs"
                 status={!breakdown ? (wlanFailed ? 'fail' : 'ok') : breakdown.units_with_failures ? 'warn'
                   : breakdown.activated === breakdown.to_activate ? 'ok' : 'warn'}>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
          <Tile value={wlanTotal} label="WLANs" />
          {breakdown ? (
            <>
              <Tile value={<>{breakdown.activated}<span className="text-sm text-gray-400"> / {breakdown.to_activate}</span></>}
                    label="Activated" tone={breakdown.activated === breakdown.to_activate ? 'text-green-600' : 'text-amber-600'} />
              <Tile value={breakdown.staged.length} label="Staged (AP Group built)" tone="text-indigo-600" />
              <Tile value={breakdown.created_only.length} label="Created only" />
              <Tile value={breakdown.units_with_failures} label="With a failure"
                    tone={breakdown.units_with_failures ? 'text-red-600' : 'text-gray-400'} />
            </>
          ) : (
            <>
              <Tile value={wlanCompleted} label="Completed" tone="text-green-600" />
              <Tile value={wlanFailed} label="Failed" tone="text-red-600" />
            </>
          )}
        </div>
        {breakdown && (
          <div className="mt-3 text-xs text-gray-600 space-y-1">
            <div>
              {breakdown.networks && <>Networks: <b>{breakdown.networks.created}</b> created, <b>{breakdown.networks.reused}</b> already in R1 (reused). </>}
              {breakdown.resources && Object.values(breakdown.resources).some(Boolean) && (
                <>Services created: {[
                  breakdown.resources.dpsk_pools ? `${breakdown.resources.dpsk_pools} DPSK pool(s)` : '',
                  breakdown.resources.identity_groups ? `${breakdown.resources.identity_groups} identity group(s)` : '',
                  breakdown.resources.radius_profiles ? `${breakdown.resources.radius_profiles} RADIUS profile(s)` : '',
                ].filter(Boolean).join(', ')}.</>
              )}
            </div>
            <div className="text-gray-500">Activated is what the venue should show as activated in R1.</div>
            {(breakdown.staged.length > 0 || breakdown.created_only.length > 0) && (
              <details>
                <summary className="cursor-pointer select-none">Which WLANs aren't activated, and why</summary>
                {breakdown.staged.length > 0 && <div className="mt-1"><b>Staged:</b> {breakdown.staged.join(', ')}</div>}
                {breakdown.created_only.length > 0 && <div className="mt-1"><b>Created only:</b> {breakdown.created_only.join(', ')}</div>}
                <div className="mt-1 text-gray-500">Neither is broadcast by any AP in SZ, so neither is activated in R1.</div>
              </details>
            )}
            {breakdown.failures.filter(f => f.phase !== 'create_ap_groups').map((f, i) => (
              <div key={i} className="text-red-700">
                <b>{f.count}×</b> {f.message} <span className="text-gray-500">({f.wlans.slice(0, 6).join(', ')}{f.count > 6 ? '…' : ''})</span>
              </div>
            ))}
          </div>
        )}
      </StageCard>

      {/* 3. AP Groups */}
      {breakdown?.ap_groups && (
        <StageCard n={3} title="AP Groups" status={breakdown.ap_groups.failed.length ? 'warn' : 'ok'}>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
            <Tile value={breakdown.ap_groups.created} label="Created" tone="text-green-600" />
            <Tile value={breakdown.ap_groups.existing} label="Already in R1" />
            <Tile value={breakdown.ap_groups.failed.length} label="Refused by R1"
                  tone={breakdown.ap_groups.failed.length ? 'text-red-600' : 'text-gray-400'} />
          </div>
          {breakdown.ap_groups.failed.length > 0 && (
            <div className="mt-2 text-xs text-red-700">Not created: {breakdown.ap_groups.failed.join(', ')} — re-run Create WLANs to retry.</div>
          )}
        </StageCard>
      )}

      {/* 4. AP Mapping */}
      {(() => {
        const v = apReport?.verify ?? {};
        const c = v.counts ?? {};
        const placed = (c.aps ?? 0) - (c.aps_unmapped ?? 0) - (c.aps_blocked ?? 0) - (c.aps_keep ?? 0)
          - (c.aps_not_in_r1 ?? 0) - (c.aps_group_missing ?? 0);
        const differ = v.differing_total ?? 0;
        return (
          <StageCard n={4} title="AP Mapping"
                     status={!state.apMappingJobId ? 'skip' : !v.counts ? 'warn' : differ || (c.aps_already ?? 0) < placed ? 'warn' : 'ok'}>
            {!state.apMappingJobId ? (
              <div className="text-sm text-gray-500">Skipped — configuration only; no APs were brought into R1.</div>
            ) : !v.counts ? (
              <div className="text-sm text-gray-500">No read-back yet{apReport?.status ? ` (job ${apReport.status})` : ''}.</div>
            ) : (
              <>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
                  <Tile value={c.aps ?? 0} label="SZ APs" />
                  <Tile value={<>{c.aps_already ?? 0}<span className="text-sm text-gray-400"> / {placed}</span></>}
                        label="In their AP Group" tone={(c.aps_already ?? 0) >= placed ? 'text-green-600' : 'text-amber-600'} />
                  <Tile value={(v.fallback_default ?? []).length} label="In default group (no AP Group)" tone="text-amber-600" />
                  <Tile value={c.aps_keep ?? 0} label="Kept as R1 has them" />
                  <Tile value={differ} label="Broadcast ≠ SZ" tone={differ ? 'text-red-600' : 'text-gray-400'} />
                </div>
                <div className="mt-2 text-xs text-gray-600">
                  {(c.aps_not_in_r1 ?? 0) > 0 && <>{c.aps_not_in_r1} SZ AP(s) not brought into R1. </>}
                  {(apReport?.map?.counts?.added ?? 0) > 0 && <>{apReport.map.counts.added} added this run. </>}
                  {(apReport?.map?.counts?.moved ?? 0) > 0 && <>{apReport.map.counts.moved} moved this run. </>}
                </div>
              </>
            )}
          </StageCard>
        );
      })()}

      {/* Details: the raw phases and created resources, for digging in */}
      <details className="bg-white rounded-lg shadow p-4">
        <summary className="font-semibold cursor-pointer select-none text-sm">Create WLANs job details — phases and created resources</summary>
        <div className="mt-4 space-y-6">
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

        </div>
      </details>

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
        {state.destVenueId && (
          // Opens the audit fresh: the URL hand-off didn't fully load it, and
          // picking the zone + venue there is quick.
          <a
            href="/migration-audit"
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
