import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import {
  AlertCircle, Server, Target, ArrowRight, Loader2, SearchCheck,
  CheckCircle, AlertTriangle, XCircle, Plus, Ban,
  ChevronDown, ChevronRight, Wifi, Shield, Key, Users, Search,
} from 'lucide-react';
import { apiGet, apiPost } from '@/utils/api';
import SzZonePicker from '@/components/migration-audit/SzZonePicker';
import SingleVenueSelector from '@/components/SingleVenueSelector';
import SingleEcSelector from '@/components/SingleEcSelector';
import { Scorecard, VenueSettingsSection, MatchingSection, OverviewTable } from '@/components/migration-audit/AlignmentSections';
import PortalSection from '@/components/migration-audit/PortalSection';
import BroadcastSection from '@/components/migration-audit/BroadcastSection';
import GapsSection from '@/components/migration-audit/GapsSection';
import CachePanel, { ago } from '@/components/migration-audit/CachePanel';
import type {
  MigrationAuditReport, AuditStatus, NetworkAuditItem,
  FieldComparisonItem, APGroupActivationAudit, ResourceCoverage,
  AuthPathAudit, AuthPath, RadiusServerCheck,
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

// ── Page Component ───────────────────────────────────────────────

export default function MigrationAudit() {
  const {
    activeControllerId: authActiveId, activeControllerName: authActiveName,
    activeControllerType: authActiveType, activeControllerSubtype: authActiveSubtype,
    secondaryControllerId: authSecondaryId, secondaryControllerName: authSecondaryName,
    secondaryControllerType: authSecondaryType, secondaryControllerSubtype: authSecondarySubtype,
    controllers,
  } = useAuth();

  // The audit only needs one SZ and one R1 controller among the two selected,
  // in either slot -- other tools want R1 as the active controller.
  const slots = [
    { id: authActiveId, name: authActiveName, type: authActiveType, subtype: authActiveSubtype },
    { id: authSecondaryId, name: authSecondaryName, type: authSecondaryType, subtype: authSecondarySubtype },
  ];
  const szSlot = slots.find(s => s.id && s.type === 'SmartZone');
  const r1Slot = slots.find(s => s.id && s.type === 'RuckusONE');
  const activeControllerId = szSlot?.id ?? null;          // SZ controller (either slot)
  const activeControllerName = szSlot?.name ?? null;
  const secondaryControllerId = r1Slot?.id ?? null;       // R1 controller (either slot)
  const secondaryControllerName = r1Slot?.name ?? null;
  const secondaryControllerSubtype = r1Slot?.subtype ?? null;

  const [searchParams] = useSearchParams();
  // One search box; the zone and venue pickers both follow it
  const [siteSearch, setSiteSearch] = useState('');
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [zoneName, setZoneName] = useState<string | null>(null);
  const [ecId, setEcId] = useState<string | null>(null);
  const [venueId, setVenueId] = useState<string | null>(null);
  const [venueName, setVenueName] = useState<string | null>(null);

  // Capture cache: refresh one or both sides instead of re-using the cache
  const [refreshSz, setRefreshSz] = useState(false);
  const [refreshR1, setRefreshR1] = useState(false);
  const [cacheReload, setCacheReload] = useState(0);

  // Execution
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<MigrationAuditReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<AuditStatus | null>(null);
  const [progressId, setProgressId] = useState<string | null>(null);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // While the audit request is open: tick the clock, read its status line every 2s
  useEffect(() => {
    if (!running || !progressId) return;
    let stopped = false;
    let seconds = 0;
    setElapsed(0);
    setProgressMessage(null);
    const timer = setInterval(async () => {
      seconds += 1;
      setElapsed(seconds);
      if (seconds % 2) return;
      try {
        const { message } = await apiGet<{ message: string | null }>(
          `${API_URL}/sz-migration/audit/progress/${progressId}`
        );
        if (!stopped && message) setProgressMessage(message);
      } catch {
        // A missed status read is harmless; the next tick tries again
      }
    }, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [running, progressId]);

  const szReady = !!szSlot;
  const r1Ready = !!r1Slot;
  const isMSP = secondaryControllerSubtype === 'MSP';
  const secondaryController = controllers?.find((c: any) => c.id === secondaryControllerId);
  const defaultTenantId = isMSP ? null : (secondaryController?.r1_tenant_id || null);
  const effectiveTenantId = isMSP ? ecId : defaultTenantId;

  // Fresh audit ready check
  const freshReady = szReady && r1Ready && zoneId && venueId && (!isMSP || ecId);

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
    // Capture cache: re-use each side's capture unless asked to refresh it
    body.use_cache = true;
    body.sz_refresh = refreshSz;
    body.r1_refresh = refreshR1;
    if (params.tenantId) {
      body.tenant_id = params.tenantId;
    } else if (effectiveTenantId) {
      body.tenant_id = effectiveTenantId;
    }
    body.progress_id = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    setProgressId(body.progress_id);

    try {
      const data = await apiPost<MigrationAuditReport>(`${API_URL}/sz-migration/audit`, body);
      setReport(data);
      // A refresh happened (or nothing was cached): show the new capture's age, untick refresh
      setRefreshSz(false);
      setRefreshR1(false);
      setCacheReload(k => k + 1);
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
                Select one SmartZone and one RuckusONE controller, as primary and secondary in either order.
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

          <div className="relative mb-3">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={siteSearch}
              onChange={e => setSiteSearch(e.target.value)}
              placeholder="Find a site — type part of the zone / venue name (e.g. kessler)"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              autoFocus
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
            <div className="space-y-5">
              {/* SZ Source */}
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-1.5">
                  <Server size={14} className="text-blue-600" /> SZ Source
                </h3>
                <div className="space-y-3">
                  <SzZonePicker
                    controllerId={activeControllerId}
                    filter={siteSearch}
                    selectedZoneId={zoneId}
                    onZoneSelect={(id, name) => {
                      setZoneId(id);
                      setZoneName(name);
                    }}
                    disabled={running}
                  />
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
                      filter={siteSearch}
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
                      filter={siteSearch}
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

            <CachePanel
              szControllerId={activeControllerId}
              zoneId={zoneId}
              r1ControllerId={secondaryControllerId}
              venueId={venueId}
              tenantId={effectiveTenantId}
              refreshSz={refreshSz}
              refreshR1={refreshR1}
              onRefreshSz={setRefreshSz}
              onRefreshR1={setRefreshR1}
              reloadKey={cacheReload}
            />

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

          {/* Loading overlay */}
          {running && !report && (
            <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-6 mb-6 flex items-center justify-center gap-3">
              <Loader2 size={20} className="animate-spin text-indigo-600 shrink-0" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-indigo-800">
                  Running audit... <span className="font-normal tabular-nums">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</span>
                </p>
                <p className="text-sm text-indigo-700 break-words">{progressMessage || 'Starting...'}</p>
                <p className="text-xs text-indigo-600">Cached captures load in seconds; capturing a very large site live can take several minutes.</p>
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
          r1ControllerId={secondaryControllerId}
          tenantId={effectiveTenantId}
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
  r1ControllerId,
  tenantId,
}: {
  report: MigrationAuditReport;
  statusFilter: AuditStatus | null;
  onFilterChange: (s: AuditStatus | null) => void;
  r1ControllerId: number | null;
  tenantId: string | null | undefined;
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
              {(['sz', 'r1'] as const).map(side => {
                const d = report.data_sources?.[side];
                if (!d) return null;
                return (
                  <span key={side} className={d.cached ? 'text-amber-700' : ''}>
                    {' · '}{side.toUpperCase()}: {d.cached ? `cached data from ${ago(d.age_seconds)}` : d.source ?? 'read live'}
                  </span>
                );
              })}
            </p>
          </div>
          <div className="text-right text-sm text-gray-500">
            {s.total_sz_wlans} SZ WLANs · {s.total_diffs} differences
          </div>
        </div>
      </div>

      {/* An empty zone makes every R1 network read "no SZ WLAN": say so first */}
      {s.total_sz_wlans === 0 && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg px-4 py-3 text-sm text-amber-900">
          <div className="font-semibold">SZ zone “{report.sz_zone_name}” has no WLANs — is this the right zone?</div>
          <div className="text-xs mt-1">
            Nothing on the SZ side can pair, so
            {report.extra_r1_networks.length > 0 ? ` all ${report.extra_r1_networks.length} R1 networks` : ' every R1 network'} show
            as “no SZ WLAN” below. Controllers often hold an empty twin of a site's zone (a placeholder, or the
            zone the APs left) — pick the zone again; the picker shows each zone's WLAN and AP counts.
          </div>
        </div>
      )}

      {/* Top summary: the headline counts, SZ beside R1 */}
      {report.overview && report.overview.length > 0 && (
        <OverviewTable rows={report.overview} szName={report.sz_zone_name} r1Name={report.r1_venue_name} />
      )}

      {/* Right under the summary: APs and WLANs SZ has that R1 doesn't yet */}
      {report.matching && <GapsSection matching={report.matching} />}

      {/* Scorecard: how aligned is this site, category by category */}
      {report.scorecard && report.scorecard.length > 0 && <Scorecard categories={report.scorecard} />}

      {/* Captive portal WLANs, every setting */}
      <PortalSection portals={report.portals ?? []} unpaired={report.unpaired_r1_portals ?? []}
                     r1ControllerId={r1ControllerId} tenantId={tenantId ?? null} />

      {/* Zone ↔ Venue settings */}
      {report.venue_settings && <VenueSettingsSection audit={report.venue_settings} />}

      {/* Pairings the rest of the report rests on */}
      {report.matching && <MatchingSection matching={report.matching} />}

      {/* Which SSIDs each AP broadcasts, through each controller's hierarchy */}
      {report.broadcast && <BroadcastSection audit={report.broadcast} />}

      <h3 className="text-sm font-semibold text-gray-700 pt-2">Per-WLAN detail</h3>

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

      {/* Authentication paths (RADIUS / DPSK-backed WLANs) */}
      {(report.auth_paths ?? []).length > 0 && (
        <AuthPathSection items={report.auth_paths} summary={s} />
      )}

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
  const [pskIssuesOnly, setPskIssuesOnly] = useState(false);
  const pskCompared = networks.filter(n => n.passphrase_check).length;
  const pskIssues = networks.filter(n => PASSPHRASE_ISSUES.has(n.passphrase_check ?? '')).length;
  const byStatus = statusFilter === 'extra'
    ? [] // Extra networks shown separately
    : statusFilter
      ? networks.filter(n => n.status === statusFilter)
      : networks;
  const filtered = pskIssuesOnly ? byStatus.filter(n => PASSPHRASE_ISSUES.has(n.passphrase_check ?? '')) : byStatus;

  const showExtra = !statusFilter || statusFilter === 'extra';

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center gap-4 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-700">
          Network Comparison
          {statusFilter && statusFilter !== 'extra' && (
            <span className="text-gray-400 font-normal ml-2">
              (filtered: {STATUS_CONFIG[statusFilter].label})
            </span>
          )}
        </h3>
        {pskCompared > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input type="checkbox" checked={pskIssuesOnly} onChange={e => setPskIssuesOnly(e.target.checked)} />
            PSK passphrase issues only ({pskIssues} of {pskCompared} PSK)
          </label>
        )}
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

// PSK only: the server compares keyed fingerprints and sends a verdict, never
// a value. DPSK networks have no single passphrase and never get one.
const PASSPHRASE_BADGE: Record<NonNullable<NetworkAuditItem['passphrase_check']>, { label: string; cls: string }> = {
  match:         { label: 'PSK match',      cls: 'bg-green-50 text-green-700 border-green-200' },
  differs:       { label: 'PSK differs',    cls: 'bg-red-50 text-red-700 border-red-200' },
  missing_in_r1: { label: 'PSK not in R1',  cls: 'bg-red-50 text-red-700 border-red-200' },
  missing_in_sz: { label: 'PSK not on SZ',  cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  unknown:       { label: 'PSK unknown',    cls: 'bg-gray-50 text-gray-500 border-gray-200' },
};
const PASSPHRASE_ISSUES = new Set(['differs', 'missing_in_r1', 'missing_in_sz']);

function NetworkRow({ item }: { item: NetworkAuditItem }) {
  const [expanded, setExpanded] = useState(false);
  const [mismatchOnly, setMismatchOnly] = useState(false);
  const cfg = STATUS_CONFIG[item.status];
  const Icon = cfg.icon;

  const hasFieldComparisons = item.field_comparisons && item.field_comparisons.length > 0;
  const mismatchCount = hasFieldComparisons
    ? item.field_comparisons.filter(fc => fieldStatus(fc) === 'differs').length
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
        {/* Default VLAN on the WLAN itself -- worth seeing without expanding */}
        <span
          className={`w-28 text-xs tabular-nums ${
            item.actual_r1_id && item.sz_vlan_id != null && item.actual_r1_vlan != null &&
            Number(item.sz_vlan_id) !== Number(item.actual_r1_vlan) ? 'text-red-700 font-semibold' : 'text-gray-500'
          }`}
          title="Default VLAN: SZ → R1"
        >
          VLAN {item.sz_vlan_id ?? '—'} → {item.actual_r1_id ? (item.actual_r1_vlan ?? '—') : '—'}
        </span>
        <span className="w-24">
          {item.passphrase_check && (
            <span
              className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${PASSPHRASE_BADGE[item.passphrase_check].cls}`}
              title={item.passphrase_note || 'SZ and R1 passphrases compared on the server; values are never sent'}
            >
              {PASSPHRASE_BADGE[item.passphrase_check].label}
            </span>
          )}
        </span>
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
        const sectionMismatches = fields.filter(f => fieldStatus(f) === 'differs').length;

        // In mismatch-only mode, show real differences plus rows needing a look
        const visibleFields = mismatchOnly
          ? fields.filter(f => ['differs', 'review'].includes(fieldStatus(f)))
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
              <th className="py-1 px-3 font-medium w-40">Result</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((fc, i) => {
              const status = fieldStatus(fc);
              const st = FIELD_STATUS[status];
              // Both sides in one vocabulary; raw values on hover
              const szDisplay = fc.sz_display ?? formatFieldValue(fc.sz_value);
              const r1Display = fc.sz_only ? '—' : (fc.r1_display ?? formatFieldValue(fc.r1_value));
              const rowClass = status === 'sz_only' ? 'text-gray-400 italic'
                : status === 'differs' ? 'bg-amber-50' : '';

              return (
                <tr key={i} className={`border-b border-gray-50 ${rowClass}`}>
                  <td className="py-1 px-3 font-medium text-gray-600">
                    {fc.label}
                    {fc.sz_only && <span className="ml-1 text-[9px] text-gray-400">(SZ only)</span>}
                  </td>
                  <td className="py-1 px-3 font-mono text-[11px]" title={`SZ reports: ${formatFieldValue(fc.sz_value)}`}>
                    {szDisplay ?? '—'}
                  </td>
                  <td className={`py-1 px-3 font-mono text-[11px] ${status === 'differs' ? 'text-amber-800 font-semibold' : ''}`}
                    title={fc.sz_only ? '' : `R1 reports: ${formatFieldValue(fc.r1_value)}`}>
                    {r1Display ?? '—'}
                  </td>
                  <td className={`py-1 px-3 text-[11px] ${st.cls}`}>
                    {st.label}
                    {fc.note && <div className="text-[10px] text-gray-400">{fc.note}</div>}
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

type FieldStatus = 'match' | 'differs' | 'default' | 'review' | 'sz_only';

const FIELD_STATUS: Record<FieldStatus, { label: string; cls: string }> = {
  match:   { label: 'Same',               cls: 'text-green-700' },
  differs: { label: 'Differs',            cls: 'text-red-700 font-semibold' },
  default: { label: 'Default both sides', cls: 'text-gray-400' },
  review:  { label: 'Review',             cls: 'text-purple-700' },
  sz_only: { label: 'No R1 equivalent',   cls: 'text-gray-400' },
};

// Older reports (before per-field status) only carry match / sz_only.
function fieldStatus(fc: FieldComparisonItem): FieldStatus {
  if (fc.status) return fc.status;
  if (fc.sz_only) return 'sz_only';
  return fc.match ? 'match' : 'differs';
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
        <h3 className="text-sm font-semibold text-gray-700">
          AP Group SSID Coverage <span className="font-normal text-gray-400 text-xs">(SZ AP Group level — ignores per-AP overrides; see "What each AP broadcasts")</span>
        </h3>
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

// ── Authentication Paths ─────────────────────────────────────────
// External RADIUS (kept on Cloudpath etc.) vs R1-internal DPSK. Counts only:
// the backend never sends a passphrase or shared secret, and this view has
// no field that could show one.

const PATH_CONFIG: Record<AuthPath, { label: string; cls: string }> = {
  external: { label: 'External RADIUS', cls: 'bg-blue-100 text-blue-700' },
  internal: { label: 'R1 DPSK',         cls: 'bg-orange-100 text-orange-700' },
  hybrid:   { label: 'Hybrid',          cls: 'bg-purple-100 text-purple-700' },
  none:     { label: 'Nothing linked',  cls: 'bg-red-100 text-red-700' },
};

const BACKEND_LABELS: Record<AuthPathAudit['sz_backend'], string> = {
  external_radius: 'SZ → external RADIUS',
  external_dpsk: 'SZ → external DPSK',
  sz_dpsk: 'SZ internal DPSK',
};

const ISSUE_STYLE: Record<string, string> = {
  error: 'text-red-700',
  warning: 'text-amber-700',
  info: 'text-gray-500',
};

function AuthPathSection({
  items,
  summary,
}: {
  items: AuthPathAudit[];
  summary: MigrationAuditReport['summary'];
}) {
  const counts: Array<[AuthPath, number]> = [
    ['external', summary.auth_external_count],
    ['internal', summary.auth_internal_count],
    ['hybrid', summary.auth_hybrid_count],
    ['none', summary.auth_none_count],
  ];
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-gray-700">Authentication Paths</h3>
        <div className="flex items-center gap-1.5 flex-wrap">
          {counts.filter(([, n]) => n > 0).map(([p, n]) => (
            <span key={p} className={`text-xs px-2 py-0.5 rounded font-medium ${PATH_CONFIG[p].cls}`}>
              {PATH_CONFIG[p].label}: {n}
            </span>
          ))}
          {summary.auth_error_count > 0 && (
            <span className="text-xs px-2 py-0.5 rounded font-medium bg-red-100 text-red-700">
              {summary.auth_error_count} error{summary.auth_error_count === 1 ? '' : 's'}
            </span>
          )}
        </div>
      </div>
      <div className="divide-y divide-gray-100">
        {items.map(item => <AuthPathRow key={item.sz_wlan_id} item={item} />)}
      </div>
    </div>
  );
}

function AuthPathRow({ item }: { item: AuthPathAudit }) {
  const hasProblems = item.issues.some(i => i.severity !== 'info');
  const [open, setOpen] = useState(hasProblems);
  const cfg = STATUS_CONFIG[item.status];
  const StatusIcon = cfg.icon;
  const auth = item.radius.find(r => r.role === 'authentication');

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-2.5 flex items-center gap-3 text-sm text-left hover:bg-gray-50"
      >
        {open ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
        <StatusIcon size={14} className={cfg.color} />
        <span className="font-medium w-40 truncate" title={item.sz_wlan_name}>{item.sz_wlan_name}</span>
        <span className="text-xs text-gray-500 w-36 truncate">{BACKEND_LABELS[item.sz_backend]}</span>
        <ArrowRight size={12} className="text-gray-300" />
        <span className={`text-xs px-2 py-0.5 rounded font-medium ${PATH_CONFIG[item.path].cls}`}>
          {PATH_CONFIG[item.path].label}
        </span>
        <span className="text-xs text-gray-500 ml-auto flex items-center gap-3">
          {(item.path === 'internal' || item.path === 'hybrid') && (
            <>
              <span className="flex items-center gap-1" title="Passphrases in the linked DPSK pool(s)">
                <Key size={11} /> {item.passphrase_total ?? '?'}
              </span>
              <span className="flex items-center gap-1" title="Identities in the pool's identity group(s)">
                <Users size={11} /> {item.identity_total ?? '?'}
              </span>
            </>
          )}
          {(item.path === 'external' || item.path === 'hybrid') && auth && (
            <span className="flex items-center gap-1" title="Authentication server">
              <Shield size={11} /> {auth.r1_primary ?? '—'}
            </span>
          )}
        </span>
      </button>

      {open && (
        <div className="px-10 pb-3 space-y-3">
          {item.radius.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 text-left">
                  <th className="font-medium py-1 w-28">Role</th>
                  <th className="font-medium py-1">SZ</th>
                  <th className="font-medium py-1">R1 profile</th>
                  <th className="font-medium py-1 w-32">Shared secret</th>
                </tr>
              </thead>
              <tbody>
                {item.radius.map(r => <RadiusRow key={r.role} r={r} />)}
              </tbody>
            </table>
          )}

          {item.pools.length > 0 && (
            <div className="space-y-1.5">
              {item.pools.map(p => (
                <div key={p.id} className="text-xs border border-gray-100 rounded p-2">
                  <div className="flex items-center gap-2">
                    <Key size={11} className="text-orange-600" />
                    <span className="font-medium">{p.name || p.id}</span>
                    <span className="text-gray-500 ml-auto">
                      {p.passphrase_count ?? '?'} passphrases
                    </span>
                  </div>
                  {p.identity_groups.length === 0 ? (
                    <div className="text-red-600 mt-1 ml-5">No identity group</div>
                  ) : p.identity_groups.map(g => (
                    <div key={g.id} className="flex items-center gap-2 mt-1 ml-5 text-gray-600">
                      <Users size={11} />
                      <span>{g.name || g.id}</span>
                      <span className="text-gray-500 ml-auto">{g.identity_count ?? '?'} identities</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}

          {item.issues.length > 0 && (
            <div className="space-y-1">
              {item.issues.map((i, idx) => (
                <div key={idx} className={`text-xs flex items-start gap-1.5 ${ISSUE_STYLE[i.severity]}`}>
                  {i.severity === 'error' ? <XCircle size={11} className="mt-0.5 flex-shrink-0" />
                    : i.severity === 'warning' ? <AlertTriangle size={11} className="mt-0.5 flex-shrink-0" />
                    : <AlertCircle size={11} className="mt-0.5 flex-shrink-0" />}
                  <span>{i.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ServerCell({ primary, secondary, match }: { primary: string | null; secondary: string | null; match?: boolean | null }) {
  return (
    <div className={match === false ? 'text-red-600' : 'text-gray-700'}>
      <div>{primary ?? '—'}</div>
      {secondary && <div className="text-gray-400">{secondary}</div>}
    </div>
  );
}

function RadiusRow({ r }: { r: RadiusServerCheck }) {
  const mismatch = r.primary_match === false || r.secondary_match === false;
  return (
    <tr className="border-t border-gray-100 align-top">
      <td className="py-1.5 capitalize text-gray-600">{r.role}</td>
      <td className="py-1.5">
        {r.sz_service_name && <div className="text-gray-400">{r.sz_service_name}</div>}
        <ServerCell primary={r.sz_primary} secondary={r.sz_secondary} />
      </td>
      <td className="py-1.5">
        {r.r1_profile_name && <div className="text-gray-400">{r.r1_profile_name}</div>}
        <ServerCell primary={r.r1_primary} secondary={r.r1_secondary} match={mismatch ? false : null} />
      </td>
      <td className="py-1.5">
        {r.placeholder_secret === true ? (
          <span className="text-red-600 font-medium">Placeholder</span>
        ) : r.placeholder_secret === false ? (
          <span className="text-green-600">Set</span>
        ) : (
          <span className="text-gray-400">Not checked</span>
        )}
      </td>
    </tr>
  );
}
