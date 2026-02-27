import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, SearchCheck,
  CheckCircle, AlertTriangle, XCircle, Plus, Ban,
  ChevronDown, ChevronRight, Wifi, Shield, Key, Users, History, Zap,
} from 'lucide-react';
import { apiFetch, apiPost } from '@/utils/api';
import SmartZoneDomainSelector from '@/components/SmartZoneDomainSelector';
import SmartZoneZoneSelector from '@/components/SmartZoneZoneSelector';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';
import type {
  MigrationAuditReport, AuditStatus, NetworkAuditItem,
  FieldComparisonItem, APGroupActivationAudit, ResourceCoverage,
} from '@/types/migrationAudit';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// ── Status config ────────────────────────────────────────────────

const STATUS_CONFIG: Record<AuditStatus, { label: string; color: string; bg: string; icon: typeof CheckCircle }> = {
  ok:          { label: 'OK',          color: 'text-green-700',  bg: 'bg-green-50 border-green-200',  icon: CheckCircle },
  warning:     { label: 'Warning',     color: 'text-amber-700',  bg: 'bg-amber-50 border-amber-200',  icon: AlertTriangle },
  missing:     { label: 'Missing',     color: 'text-red-700',    bg: 'bg-red-50 border-red-200',      icon: XCircle },
  extra:       { label: 'Extra',       color: 'text-purple-700', bg: 'bg-purple-50 border-purple-200', icon: Plus },
  unsupported: { label: 'Unsupported', color: 'text-gray-500',   bg: 'bg-gray-50 border-gray-200',    icon: Ban },
};

// ── Session type ─────────────────────────────────────────────────

interface SessionInfo {
  id: number;
  status: string;
  sz_controller_id: number | null;
  sz_zone_id: string | null;
  sz_zone_name: string | null;
  r1_controller_id: number | null;
  r1_tenant_id: string | null;
  r1_venue_id: string | null;
  r1_venue_name: string | null;
  extraction_job_id: string | null;
  wlan_count: number | null;
  updated_at: string;
}

// ── Page Component ───────────────────────────────────────────────

type AuditMode = 'fresh' | 'sessions';

export default function MigrationAudit() {
  const {
    activeControllerId, activeControllerName, activeControllerType,
    secondaryControllerId, secondaryControllerName, secondaryControllerType,
    secondaryControllerSubtype, controllers,
  } = useAuth();

  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<AuditMode>('fresh');

  // Fresh audit inputs
  const [domainId, setDomainId] = useState<string | null>(null);
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [zoneName, setZoneName] = useState<string | null>(null);
  const [ecId, setEcId] = useState<string | null>(null);
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Session list
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  // Execution
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<MigrationAuditReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<AuditStatus | null>(null);

  const szReady = activeControllerId && activeControllerType === 'SmartZone';
  const r1Ready = secondaryControllerId && secondaryControllerType === 'RuckusONE';
  const isMSP = secondaryControllerSubtype === 'MSP';
  const secondaryController = controllers?.find((c: any) => c.id === secondaryControllerId);
  const defaultTenantId = isMSP ? null : (secondaryController?.r1_tenant_id || null);
  const effectiveTenantId = isMSP ? ecId : defaultTenantId;

  // Fresh audit ready check
  const freshReady = szReady && r1Ready && zoneId && venueId && (!isMSP || ecId);

  // ── Fetch sessions on mount ──
  useEffect(() => {
    if (szReady && r1Ready) {
      fetchSessions();
    }
  }, [activeControllerId, secondaryControllerId]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchSessions = async () => {
    setSessionsLoading(true);
    try {
      const res = await apiFetch(
        `${API_URL}/sz-migration/sessions?status=completed&sz_controller_id=${activeControllerId}&r1_controller_id=${secondaryControllerId}&limit=10`
      );
      if (res.ok) {
        const data = await res.json();
        setSessions(data);
      }
    } catch {
      // Non-critical
    } finally {
      setSessionsLoading(false);
    }
  };

  // ── URL param quick-launch ──
  useEffect(() => {
    const szSnapshot = searchParams.get('sz_snapshot');
    const zone = searchParams.get('zone_id');
    const venue = searchParams.get('venue_id');

    if (venue && (szSnapshot || (zone && szReady))) {
      // Auto-run: try snapshot first, fall back to zone
      runAudit({ szSnapshotJobId: szSnapshot || undefined, zoneId: zone || undefined, venueId: venue });
    }
  }, [szReady, r1Ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Core audit function ──
  const runAudit = async (params: {
    szSnapshotJobId?: string;
    szControllerId?: number;
    zoneId?: string;
    venueId?: string;
    tenantId?: string | null;
  }) => {
    setRunning(true);
    setError(null);
    setReport(null);
    setStatusFilter(null);

    const body: Record<string, any> = {};

    // SZ side
    if (params.szSnapshotJobId) {
      body.sz_snapshot_job_id = params.szSnapshotJobId;
    } else if (params.zoneId && activeControllerId) {
      body.sz_controller_id = params.szControllerId || activeControllerId;
      body.zone_id = params.zoneId;
    } else {
      setError('No SZ source specified');
      setRunning(false);
      return;
    }

    // R1 side — always fresh capture
    if (!params.venueId || !secondaryControllerId) {
      setError('R1 venue required');
      setRunning(false);
      return;
    }
    body.r1_controller_id = secondaryControllerId;
    body.venue_id = params.venueId;
    if (params.tenantId) {
      body.tenant_id = params.tenantId;
    } else if (effectiveTenantId) {
      body.tenant_id = effectiveTenantId;
    }

    try {
      const data = await apiPost<MigrationAuditReport>(`${API_URL}/sz-migration/audit`, body);
      setReport(data);
    } catch (e: any) {
      // If snapshot expired (404) and we have a zone_id fallback, retry with inline extraction
      if (body.sz_snapshot_job_id && params.zoneId && e.message?.includes('404')) {
        try {
          delete body.sz_snapshot_job_id;
          body.sz_controller_id = params.szControllerId || activeControllerId;
          body.zone_id = params.zoneId;
          const data = await apiPost<MigrationAuditReport>(`${API_URL}/sz-migration/audit`, body);
          setReport(data);
          return;
        } catch (e2: any) {
          setError(e2.message || 'Audit failed on retry');
          return;
        }
      }
      setError(e.message || 'Audit failed');
    } finally {
      setRunning(false);
    }
  };

  // ── Fresh audit handler ──
  const handleFreshAudit = () => {
    if (!zoneId || !venueId) return;
    runAudit({
      zoneId,
      venueId,
      tenantId: effectiveTenantId,
    });
  };

  // ── Session audit handler ──
  const handleSessionAudit = (session: SessionInfo) => {
    runAudit({
      szSnapshotJobId: session.extraction_job_id || undefined,
      szControllerId: session.sz_controller_id || undefined,
      zoneId: session.sz_zone_id || undefined,
      venueId: session.r1_venue_id || undefined,
      tenantId: session.r1_tenant_id,
    });
  };

  return (
    <div className="p-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
          <SearchCheck size={24} className="text-indigo-600" />
          Migration Audit
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Compare SZ zone config against R1 venue to verify migration completeness
        </p>
      </div>

      {/* Controller bar */}
      {(!szReady || !r1Ready) ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <div className="flex items-start gap-2">
            <AlertCircle className="text-red-500 mt-0.5" size={18} />
            <div>
              <p className="text-red-800 font-semibold text-sm">Controller Setup Required</p>
              <p className="text-red-600 text-sm mt-1">
                Set <strong>Primary</strong> to SmartZone and <strong>Secondary</strong> to RuckusONE.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-4 bg-gray-50 border border-gray-200 rounded-lg p-3">
            <div className="flex items-center gap-2 text-sm">
              <Server size={16} className="text-blue-600" />
              <span className="font-medium">{activeControllerName}</span>
              <span className="text-gray-400 text-xs">(SZ)</span>
            </div>
            <ArrowRight size={16} className="text-gray-400" />
            <div className="flex items-center gap-2 text-sm">
              <Target size={16} className="text-green-600" />
              <span className="font-medium">{secondaryControllerName}</span>
              <span className="text-gray-400 text-xs">(R1{isMSP ? ' MSP' : ''})</span>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 mb-4">
            <button
              onClick={() => setMode('fresh')}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-t-lg text-sm font-medium border border-b-0 transition ${
                mode === 'fresh'
                  ? 'bg-white text-indigo-700 border-gray-200'
                  : 'bg-gray-100 text-gray-500 border-transparent hover:text-gray-700'
              }`}
            >
              <Zap size={14} /> Fresh Audit
            </button>
            <button
              onClick={() => setMode('sessions')}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-t-lg text-sm font-medium border border-b-0 transition ${
                mode === 'sessions'
                  ? 'bg-white text-indigo-700 border-gray-200'
                  : 'bg-gray-100 text-gray-500 border-transparent hover:text-gray-700'
              }`}
            >
              <History size={14} /> From Session
              {sessions.length > 0 && (
                <span className="bg-gray-200 text-gray-600 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                  {sessions.length}
                </span>
              )}
            </button>
          </div>

          {/* Fresh Audit Tab */}
          {mode === 'fresh' && (
            <div className="bg-white border border-gray-200 rounded-lg rounded-tl-none p-4 mb-6">
              <div className="space-y-5">
                {/* SZ Source */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                    <Server size={14} className="text-blue-600" /> SZ Source
                  </h3>
                  <div className="space-y-3">
                    <SmartZoneDomainSelector
                      onDomainSelect={(id) => {
                        setDomainId(id);
                        setZoneId(null);
                        setZoneName(null);
                      }}
                      disabled={running}
                    />
                    {domainId && (
                      <SmartZoneZoneSelector
                        domainId={domainId}
                        onZoneSelect={(id, name) => {
                          setZoneId(id);
                          setZoneName(name);
                        }}
                        disabled={running}
                      />
                    )}
                    {zoneName && (
                      <div className="text-xs text-gray-500 bg-gray-50 rounded px-3 py-2">
                        Zone: <span className="font-medium text-gray-700">{zoneName}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* R1 Destination */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                    <Target size={14} className="text-green-600" /> R1 Destination
                  </h3>
                  <div className="space-y-3">
                    {isMSP && (
                      <SingleEcSelector
                        controllerId={secondaryControllerId}
                        onEcSelect={(id) => {
                          setEcId(id);
                          setVenueId(null);
                          setVenueName(null);
                        }}
                        selectedEcId={ecId}
                      />
                    )}
                    {(!isMSP || ecId) && (
                      <SingleVenueSelector
                        controllerId={secondaryControllerId}
                        tenantId={effectiveTenantId}
                        onVenueSelect={(id, venue) => {
                          setVenueId(id);
                          setVenueName(venue?.name || venue?.venueName || id);
                        }}
                        selectedVenueId={venueId}
                      />
                    )}
                    {venueName && (
                      <div className="text-xs text-gray-500 bg-gray-50 rounded px-3 py-2">
                        Venue: <span className="font-medium text-gray-700">{venueName}</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="mt-4 flex items-center gap-3 pt-3 border-t border-gray-100">
                <button
                  onClick={handleFreshAudit}
                  disabled={!freshReady || running}
                  className="px-5 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium flex items-center gap-2"
                >
                  {running ? <Loader2 size={14} className="animate-spin" /> : <SearchCheck size={14} />}
                  {running ? 'Running Audit...' : 'Run Audit'}
                </button>
                {error && (
                  <span className="text-sm text-red-600 flex items-center gap-1">
                    <AlertCircle size={14} /> {error}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* From Session Tab */}
          {mode === 'sessions' && (
            <div className="bg-white border border-gray-200 rounded-lg rounded-tl-none p-4 mb-6">
              {sessionsLoading ? (
                <div className="flex items-center justify-center py-8 gap-2 text-gray-400">
                  <Loader2 size={16} className="animate-spin" /> Loading sessions...
                </div>
              ) : sessions.length === 0 ? (
                <div className="text-center py-8 text-gray-400 text-sm">
                  No completed migration sessions for this controller pair.
                </div>
              ) : (
                <div className="overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-500 text-xs border-b">
                        <th className="py-2 px-3">SZ Zone</th>
                        <th className="py-2 px-3">R1 Venue</th>
                        <th className="py-2 px-3">WLANs</th>
                        <th className="py-2 px-3">Date</th>
                        <th className="py-2 px-3"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {sessions.map(s => (
                        <tr key={s.id} className="hover:bg-gray-50">
                          <td className="py-2.5 px-3 font-medium">{s.sz_zone_name || '—'}</td>
                          <td className="py-2.5 px-3">{s.r1_venue_name || '—'}</td>
                          <td className="py-2.5 px-3 text-gray-500">{s.wlan_count ?? '—'}</td>
                          <td className="py-2.5 px-3 text-gray-400 text-xs">
                            {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : '—'}
                          </td>
                          <td className="py-2.5 px-3">
                            <button
                              onClick={() => handleSessionAudit(s)}
                              disabled={running}
                              className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-1"
                            >
                              {running ? <Loader2 size={10} className="animate-spin" /> : <SearchCheck size={10} />}
                              Audit
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {error && (
                <div className="mt-3 text-sm text-red-600 flex items-center gap-1">
                  <AlertCircle size={14} /> {error}
                </div>
              )}
            </div>
          )}

          {/* Loading overlay */}
          {running && !report && (
            <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-6 mb-6 flex items-center justify-center gap-3">
              <Loader2 size={20} className="animate-spin text-indigo-600" />
              <div>
                <p className="text-sm font-medium text-indigo-800">Running audit...</p>
                <p className="text-xs text-indigo-600">Extracting SZ zone data + capturing R1 inventory. This may take 15-30 seconds.</p>
              </div>
            </div>
          )}
        </>
      )}

      {/* Results */}
      {report && (
        <AuditResults
          report={report}
          statusFilter={statusFilter}
          onFilterChange={setStatusFilter}
        />
      )}
    </div>
  );
}

// ── Audit Results ────────────────────────────────────────────────

function AuditResults({
  report,
  statusFilter,
  onFilterChange,
}: {
  report: MigrationAuditReport;
  statusFilter: AuditStatus | null;
  onFilterChange: (s: AuditStatus | null) => void;
}) {
  const s = report.summary;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-gray-800">
              {report.sz_zone_name} → {report.r1_venue_name}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Audited {new Date(report.audit_timestamp).toLocaleString()}
            </p>
          </div>
          <div className="text-right text-sm text-gray-500">
            {s.total_sz_wlans} SZ WLANs · {s.total_diffs} differences
          </div>
        </div>
      </div>

      {/* Summary cards (clickable filters) */}
      <div className="grid grid-cols-5 gap-3">
        <SummaryCard
          label="OK" count={s.ok_count} status="ok"
          active={statusFilter === 'ok'} onClick={() => onFilterChange(statusFilter === 'ok' ? null : 'ok')}
        />
        <SummaryCard
          label="Warning" count={s.warning_count} status="warning"
          active={statusFilter === 'warning'} onClick={() => onFilterChange(statusFilter === 'warning' ? null : 'warning')}
        />
        <SummaryCard
          label="Missing" count={s.missing_count} status="missing"
          active={statusFilter === 'missing'} onClick={() => onFilterChange(statusFilter === 'missing' ? null : 'missing')}
        />
        <SummaryCard
          label="Extra in R1" count={s.extra_count} status="extra"
          active={statusFilter === 'extra'} onClick={() => onFilterChange(statusFilter === 'extra' ? null : 'extra')}
        />
        <SummaryCard
          label="N/A" count={s.unsupported_count} status="unsupported"
          active={statusFilter === 'unsupported'} onClick={() => onFilterChange(statusFilter === 'unsupported' ? null : 'unsupported')}
        />
      </div>

      {/* Network audit table */}
      <NetworkAuditTable
        networks={report.networks}
        extraNetworks={report.extra_r1_networks}
        statusFilter={statusFilter}
      />

      {/* AP Group coverage */}
      {report.ap_group_activations.length > 0 && (
        <APGroupCoverage
          activations={report.ap_group_activations}
          coveragePercent={s.ap_group_coverage}
        />
      )}

      {/* Resource coverage */}
      {report.resource_coverage.length > 0 && (
        <ResourceCoverageSection coverage={report.resource_coverage} />
      )}

      {/* Warnings */}
      {report.warnings.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-amber-800 mb-2">Warnings ({report.warnings.length})</h3>
          <div className="space-y-1">
            {report.warnings.map((w, i) => (
              <div key={i} className="text-xs text-amber-700 flex items-start gap-1.5">
                <AlertTriangle size={11} className="mt-0.5 flex-shrink-0" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Summary Card ─────────────────────────────────────────────────

function SummaryCard({
  label, count, status, active, onClick,
}: {
  label: string; count: number; status: AuditStatus; active: boolean;
  onClick: () => void;
}) {
  const cfg = STATUS_CONFIG[status];
  return (
    <button
      onClick={onClick}
      className={`border rounded-lg p-3 text-center transition-all ${cfg.bg} ${
        active ? 'ring-2 ring-offset-1 ring-indigo-400' : ''
      } hover:shadow-sm`}
    >
      <div className={`text-2xl font-bold ${cfg.color}`}>{count}</div>
      <div className="text-xs text-gray-600">{label}</div>
    </button>
  );
}

// ── Network Audit Table ──────────────────────────────────────────

function NetworkAuditTable({
  networks,
  extraNetworks,
  statusFilter,
}: {
  networks: NetworkAuditItem[];
  extraNetworks: MigrationAuditReport['extra_r1_networks'];
  statusFilter: AuditStatus | null;
}) {
  const filtered = statusFilter === 'extra'
    ? [] // Extra networks shown separately
    : statusFilter
      ? networks.filter(n => n.status === statusFilter)
      : networks;

  const showExtra = !statusFilter || statusFilter === 'extra';

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">
          Network Comparison
          {statusFilter && statusFilter !== 'extra' && (
            <span className="text-gray-400 font-normal ml-2">
              (filtered: {STATUS_CONFIG[statusFilter].label})
            </span>
          )}
        </h3>
      </div>

      {/* SZ WLAN rows */}
      {filtered.length > 0 && (
        <div className="divide-y divide-gray-100">
          {filtered.map(item => (
            <NetworkRow key={item.sz_wlan_id} item={item} />
          ))}
        </div>
      )}

      {filtered.length === 0 && statusFilter !== 'extra' && (
        <div className="px-4 py-6 text-center text-sm text-gray-400">
          {statusFilter ? `No networks with status "${statusFilter}"` : 'No networks to display'}
        </div>
      )}

      {/* Extra R1 networks */}
      {showExtra && extraNetworks.length > 0 && (
        <>
          <div className="px-4 py-2 bg-purple-50 border-t border-purple-100">
            <span className="text-xs font-semibold text-purple-700">
              Extra in R1 ({extraNetworks.length}) — not in SZ zone
            </span>
          </div>
          <div className="divide-y divide-gray-100">
            {extraNetworks.map(net => (
              <div key={net.id} className="px-4 py-2.5 flex items-center gap-3 text-sm">
                <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700">
                  EXTRA
                </span>
                <span className="font-medium">{net.name}</span>
                <span className="text-gray-400">{net.ssid}</span>
                <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-xs">{net.nwSubType}</span>
                <span className="text-gray-300 font-mono text-[10px] ml-auto">{net.id?.slice(0, 16)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Section ordering for field comparisons ───────────────────────

const SECTION_ORDER = [
  'Basic Settings',
  'Security & Encryption',
  'VLAN & Network',
  'Authentication & RADIUS',
  'Client Management',
  'Radio & Spectrum',
  'Rate Limiting',
  'DHCP & IP',
  'Roaming & RSSI',
  'Advanced Features',
  'SZ-Only Settings',
];

// ── Network Row (expandable with deep field accordion) ───────────

function NetworkRow({ item }: { item: NetworkAuditItem }) {
  const [expanded, setExpanded] = useState(false);
  const [mismatchOnly, setMismatchOnly] = useState(false);
  const cfg = STATUS_CONFIG[item.status];
  const Icon = cfg.icon;

  const hasFieldComparisons = item.field_comparisons && item.field_comparisons.length > 0;
  const mismatchCount = hasFieldComparisons
    ? item.field_comparisons.filter(fc => !fc.match && !fc.sz_only).length
    : 0;

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center gap-3 text-sm hover:bg-gray-50 text-left"
      >
        {expanded ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
        <Icon size={14} className={cfg.color} />
        <span className="font-medium w-44 truncate">{item.sz_wlan_name}</span>
        <span className="text-gray-400 w-32 truncate">{item.sz_ssid}</span>
        <span className="text-gray-500 w-28 text-xs">{item.sz_auth_type}</span>
        <span className="text-gray-500 w-16 text-xs">{item.expected_r1_type}</span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${cfg.bg} ${cfg.color}`}>
          {cfg.label.toUpperCase()}
        </span>
        {mismatchCount > 0 && (
          <span className="text-xs text-amber-600 ml-1">{mismatchCount} mismatch{mismatchCount !== 1 ? 'es' : ''}</span>
        )}
        {item.notes && (
          <span className="text-xs text-gray-400 ml-auto truncate max-w-xs">{item.notes}</span>
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-3 ml-8">
          {/* Deep field comparison accordion */}
          {hasFieldComparisons ? (
            <div>
              {/* Toggle for mismatches only */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-500">
                  {item.field_comparisons.length} fields compared
                  {mismatchCount > 0 && <span className="text-amber-600 ml-1">({mismatchCount} mismatches)</span>}
                </span>
                <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={mismatchOnly}
                    onChange={() => setMismatchOnly(!mismatchOnly)}
                    className="rounded border-gray-300 text-indigo-600 w-3.5 h-3.5"
                  />
                  Mismatches only
                </label>
              </div>

              <FieldComparisonAccordion
                comparisons={item.field_comparisons}
                mismatchOnly={mismatchOnly}
              />
            </div>
          ) : (
            /* Fallback: basic comparison table (for missing/unsupported items) */
            <table className="w-full text-xs border border-gray-100 rounded">
              <thead>
                <tr className="text-left text-gray-500 bg-gray-50 border-b">
                  <th className="py-1.5 px-3 w-40">Field</th>
                  <th className="py-1.5 px-3">SZ / Expected</th>
                  <th className="py-1.5 px-3">R1 Actual</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-gray-50">
                  <td className="py-1.5 px-3 font-medium text-gray-600">Name</td>
                  <td className="py-1.5 px-3">{item.expected_r1_name}</td>
                  <td className="py-1.5 px-3">{item.actual_r1_name || '—'}</td>
                </tr>
                <tr className="border-b border-gray-50">
                  <td className="py-1.5 px-3 font-medium text-gray-600">SSID</td>
                  <td className="py-1.5 px-3">{item.expected_r1_ssid}</td>
                  <td className="py-1.5 px-3">{item.actual_r1_ssid || '—'}</td>
                </tr>
                <tr className="border-b border-gray-50">
                  <td className="py-1.5 px-3 font-medium text-gray-600">Type</td>
                  <td className="py-1.5 px-3">{item.expected_r1_type}</td>
                  <td className="py-1.5 px-3">{item.actual_r1_type || '—'}</td>
                </tr>
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ── Field Comparison Accordion ───────────────────────────────────

function FieldComparisonAccordion({
  comparisons,
  mismatchOnly,
}: {
  comparisons: FieldComparisonItem[];
  mismatchOnly: boolean;
}) {
  // Group comparisons by section
  const grouped = new Map<string, FieldComparisonItem[]>();
  for (const fc of comparisons) {
    const existing = grouped.get(fc.section) || [];
    existing.push(fc);
    grouped.set(fc.section, existing);
  }

  // Sort sections by defined order
  const sortedSections = Array.from(grouped.keys()).sort((a, b) => {
    const ai = SECTION_ORDER.indexOf(a);
    const bi = SECTION_ORDER.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });

  return (
    <div className="space-y-1">
      {sortedSections.map(section => {
        const fields = grouped.get(section) || [];
        const sectionMismatches = fields.filter(f => !f.match && !f.sz_only).length;

        // In mismatch-only mode, skip sections with no mismatches
        const visibleFields = mismatchOnly
          ? fields.filter(f => !f.match && !f.sz_only)
          : fields;

        if (visibleFields.length === 0) return null;

        return (
          <FieldSection
            key={section}
            section={section}
            fields={visibleFields}
            mismatchCount={sectionMismatches}
            defaultOpen={sectionMismatches > 0}
          />
        );
      })}
    </div>
  );
}

// ── Field Section (collapsible) ──────────────────────────────────

function FieldSection({
  section,
  fields,
  mismatchCount,
  defaultOpen,
}: {
  section: string;
  fields: FieldComparisonItem[];
  mismatchCount: number;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-gray-100 rounded overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className={`w-full px-3 py-1.5 flex items-center gap-2 text-xs text-left ${
          mismatchCount > 0 ? 'bg-amber-50' : 'bg-gray-50'
        } hover:bg-gray-100 transition`}
      >
        {open ? <ChevronDown size={12} className="text-gray-400" /> : <ChevronRight size={12} className="text-gray-400" />}
        <span className="font-semibold text-gray-700">{section}</span>
        <span className="text-gray-400">({fields.length})</span>
        {mismatchCount > 0 && (
          <span className="ml-auto px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-bold">
            {mismatchCount} mismatch{mismatchCount !== 1 ? 'es' : ''}
          </span>
        )}
      </button>

      {open && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-400 border-b border-gray-100">
              <th className="py-1 px-3 w-48 font-medium">Field</th>
              <th className="py-1 px-3 font-medium">SZ Value</th>
              <th className="py-1 px-3 font-medium">R1 Value</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((fc, i) => {
              const szDisplay = formatFieldValue(fc.sz_value);
              const r1Display = fc.sz_only ? '—' : formatFieldValue(fc.r1_value);
              const rowClass = fc.sz_only
                ? 'text-gray-400 italic'
                : !fc.match
                  ? 'bg-amber-50'
                  : '';

              return (
                <tr key={i} className={`border-b border-gray-50 ${rowClass}`}>
                  <td className="py-1 px-3 font-medium text-gray-600">
                    {fc.label}
                    {fc.sz_only && <span className="ml-1 text-[9px] text-gray-400">(SZ only)</span>}
                  </td>
                  <td className="py-1 px-3 font-mono text-[11px]">{szDisplay}</td>
                  <td className={`py-1 px-3 font-mono text-[11px] ${!fc.match && !fc.sz_only ? 'text-amber-800 font-semibold' : ''}`}>
                    {r1Display}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function formatFieldValue(value: any): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

// ── AP Group Coverage ────────────────────────────────────────────

function APGroupCoverage({
  activations,
  coveragePercent,
}: {
  activations: APGroupActivationAudit[];
  coveragePercent: number;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">AP Group SSID Coverage</h3>
        <span className={`text-xs font-bold ${coveragePercent === 100 ? 'text-green-600' : 'text-amber-600'}`}>
          {coveragePercent}% covered
        </span>
      </div>
      <div className="divide-y divide-gray-100">
        {activations.map(apg => {
          const allCovered = apg.missing_ssids.length === 0;
          return (
            <div key={apg.sz_ap_group_name} className="px-4 py-2.5 flex items-center gap-3 text-sm">
              {allCovered
                ? <CheckCircle size={14} className="text-green-600" />
                : <AlertTriangle size={14} className="text-amber-600" />
              }
              <span className="font-medium w-40 truncate">{apg.sz_ap_group_name}</span>
              {!apg.r1_ap_group_found ? (
                <span className="text-xs text-red-600">AP Group not found in R1</span>
              ) : (
                <>
                  <span className="text-xs text-gray-500">
                    {apg.actual_ssids.length}/{apg.expected_ssids.length} SSIDs
                  </span>
                  {apg.missing_ssids.length > 0 && (
                    <span className="text-xs text-red-600">
                      Missing: {apg.missing_ssids.join(', ')}
                    </span>
                  )}
                  {apg.extra_ssids.length > 0 && (
                    <span className="text-xs text-purple-600">
                      Extra: {apg.extra_ssids.join(', ')}
                    </span>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Resource Coverage ────────────────────────────────────────────

const RESOURCE_ICONS: Record<string, typeof Wifi> = {
  dpsk_pools: Key,
  identity_groups: Users,
  radius_profiles: Shield,
};

const RESOURCE_LABELS: Record<string, string> = {
  dpsk_pools: 'DPSK Pools',
  identity_groups: 'Identity Groups',
  radius_profiles: 'RADIUS Profiles',
};

function ResourceCoverageSection({ coverage }: { coverage: ResourceCoverage[] }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">Supporting Resources</h3>
      </div>
      <div className="divide-y divide-gray-100">
        {coverage.map(rc => {
          const Icon = RESOURCE_ICONS[rc.resource_type] || Wifi;
          const allMatched = rc.missing.length === 0;
          return (
            <div key={rc.resource_type} className="px-4 py-2.5 flex items-center gap-3 text-sm">
              <Icon size={14} className={allMatched ? 'text-green-600' : 'text-amber-600'} />
              <span className="font-medium w-36">
                {RESOURCE_LABELS[rc.resource_type] || rc.resource_type}
              </span>
              <span className="text-xs text-gray-500">
                {rc.matched.length}/{rc.expected_count} matched
              </span>
              {rc.missing.length > 0 && (
                <span className="text-xs text-red-600">
                  Missing: {rc.missing.join(', ')}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
