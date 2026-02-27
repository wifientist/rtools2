import { useState, useEffect } from 'react';
import { Database, Download, Eye, MapPin, Activity, X, Copy, Check } from 'lucide-react';
import { apiFetch, apiGet } from '@/utils/api';
import type { WizardState } from '@/types/szConfigMigration';

const API_URL = import.meta.env.VITE_API_BASE_URL || '/api';

interface Props {
  state: WizardState;
}

export default function ArtifactsBar({ state }: Props) {
  const [snapshotModalOpen, setSnapshotModalOpen] = useState(false);

  const hasZone = !!state.selectedZoneName;
  const hasSzSnapshot = !!state.extractionJobId && state.extractionStatus === 'completed';
  const hasR1Venue = !!state.destVenueName;
  const hasR1Snapshot = !!state.r1SnapshotJobId;
  const hasJob = !!state.finalJobId;

  // Don't render if nothing to show
  if (!hasZone && !hasSzSnapshot && !hasR1Venue && !hasJob) return null;

  return (
    <>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {hasZone && (
          <Chip
            icon={<MapPin size={12} />}
            label={`SZ Zone: ${state.selectedZoneName}`}
            color="blue"
          />
        )}

        {hasSzSnapshot && (
          <>
            <Chip
              icon={<Database size={12} />}
              label="SZ Snapshot"
              color="blue"
              onClick={() => setSnapshotModalOpen(true)}
              actionIcon={<Eye size={11} />}
              actionLabel="Inspect"
            />
            <Chip
              icon={<Download size={12} />}
              label="Download JSON"
              color="blue"
              onClick={() => downloadSnapshot(state.extractionJobId!)}
            />
          </>
        )}

        {hasR1Venue && (
          <Chip
            icon={<MapPin size={12} />}
            label={`R1 Venue: ${state.destVenueName}`}
            color="green"
          />
        )}

        {hasR1Snapshot && (
          <Chip
            icon={<Database size={12} />}
            label="R1 Inventory"
            color="green"
          />
        )}

        {hasJob && (
          <Chip
            icon={<Activity size={12} />}
            label={`Job: ${state.finalJobId!.slice(0, 8)}...`}
            color="orange"
          />
        )}
      </div>

      {snapshotModalOpen && state.extractionJobId && (
        <SnapshotModal
          jobId={state.extractionJobId}
          onClose={() => setSnapshotModalOpen(false)}
        />
      )}
    </>
  );
}

// ── Chip ──────────────────────────────────────────────────────────

interface ChipProps {
  icon: React.ReactNode;
  label: string;
  color: 'blue' | 'green' | 'orange';
  onClick?: () => void;
  actionIcon?: React.ReactNode;
  actionLabel?: string;
}

const CHIP_COLORS = {
  blue: 'bg-blue-50 border-blue-200 text-blue-700',
  green: 'bg-green-50 border-green-200 text-green-700',
  orange: 'bg-orange-50 border-orange-200 text-orange-700',
};

function Chip({ icon, label, color, onClick, actionIcon, actionLabel }: ChipProps) {
  const base = `inline-flex items-center gap-1.5 text-xs border rounded-full px-2.5 py-1 ${CHIP_COLORS[color]}`;

  if (onClick) {
    return (
      <button onClick={onClick} className={`${base} hover:opacity-80 cursor-pointer`}>
        {icon}
        <span>{label}</span>
        {actionIcon && (
          <span className="ml-0.5 opacity-60">{actionIcon}</span>
        )}
      </button>
    );
  }

  return (
    <span className={base}>
      {icon}
      <span>{label}</span>
    </span>
  );
}

// ── Download helper ───────────────────────────────────────────────

async function downloadSnapshot(jobId: string) {
  try {
    const res = await apiFetch(`${API_URL}/sz-migration/snapshot/${jobId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sz-snapshot-${jobId.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error('Download failed:', e);
  }
}

// ── Snapshot Modal ────────────────────────────────────────────────

function SnapshotModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<'summary' | 'wlans' | 'ap_groups' | 'raw'>('summary');

  useEffect(() => {
    apiGet<any>(`${API_URL}/sz-migration/snapshot/${jobId}`)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [jobId]);

  const handleCopy = () => {
    if (!data) return;
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-5xl max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="px-5 py-3 border-b flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database size={16} className="text-blue-600" />
            <h3 className="font-semibold text-sm">SZ Snapshot Inspector</h3>
            <span className="text-xs text-gray-400 font-mono">{jobId.slice(0, 8)}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
            >
              {copied ? <Check size={12} className="text-green-600" /> : <Copy size={12} />}
              {copied ? 'Copied' : 'Copy JSON'}
            </button>
            <button
              onClick={() => downloadSnapshot(jobId)}
              className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
            >
              <Download size={12} />
              Download
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 ml-2">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b px-5">
          {(['summary', 'wlans', 'ap_groups', 'raw'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                activeTab === tab
                  ? 'border-blue-600 text-blue-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab === 'summary' ? 'Summary' : tab === 'wlans' ? 'WLANs' : tab === 'ap_groups' ? 'AP Groups' : 'Raw JSON'}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading && <p className="text-gray-500 text-sm text-center py-8">Loading snapshot...</p>}
          {error && <p className="text-red-600 text-sm text-center py-8">{error}</p>}

          {data && activeTab === 'summary' && <SummaryTab data={data} />}
          {data && activeTab === 'wlans' && <WlansTab data={data} />}
          {data && activeTab === 'ap_groups' && <ApGroupsTab data={data} />}
          {data && activeTab === 'raw' && (
            <pre className="text-xs font-mono bg-gray-50 border rounded-lg p-4 overflow-x-auto whitespace-pre-wrap max-h-[60vh]">
              {JSON.stringify(data, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Snapshot Tabs ─────────────────────────────────────────────────

function SummaryTab({ data }: { data: any }) {
  const meta = data.extraction_metadata || {};
  const counts = meta.counts || {};
  const zone = data.zone || {};

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MiniCard label="WLANs" value={counts.wlans ?? data.wlans?.length ?? 0} />
        <MiniCard label="WLAN Groups" value={counts.wlan_groups ?? data.wlan_groups?.length ?? 0} />
        <MiniCard label="AP Groups" value={counts.ap_groups ?? data.ap_groups?.length ?? 0} />
        <MiniCard label="APs" value={counts.aps ?? data.aps?.length ?? 0} />
      </div>

      <div className="bg-gray-50 border rounded-lg p-3 text-xs space-y-1">
        <div><span className="text-gray-500">Zone:</span> <span className="font-medium">{zone.name || '—'}</span></div>
        <div><span className="text-gray-500">Zone ID:</span> <span className="font-mono text-gray-600">{zone.id || '—'}</span></div>
        {zone.country_code && <div><span className="text-gray-500">Country:</span> {zone.country_code}</div>}
        {meta.extracted_at && <div><span className="text-gray-500">Extracted:</span> {new Date(meta.extracted_at).toLocaleString()}</div>}
        {meta.duration_seconds && <div><span className="text-gray-500">Duration:</span> {meta.duration_seconds.toFixed(1)}s</div>}
        <div><span className="text-gray-500">Referenced Objects:</span> {counts.referenced_objects ?? Object.keys(data.referenced_objects || {}).length}</div>
        {counts.warnings > 0 && <div className="text-amber-600"><span className="text-gray-500">Warnings:</span> {counts.warnings}</div>}
      </div>

      {data.warnings?.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-amber-800 mb-1">Extraction Warnings</h4>
          {data.warnings.map((w: any, i: number) => (
            <div key={i} className="text-xs text-amber-700">
              [{w.phase}] {w.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WlansTab({ data }: { data: any }) {
  const wlans = data.wlans || [];
  const refs = data.referenced_objects || {};

  return (
    <div className="space-y-2">
      {wlans.length === 0 && <p className="text-gray-500 text-sm py-4 text-center">No WLANs in snapshot</p>}
      {wlans.map((wlan: any) => (
        <WlanCard key={wlan.id} wlan={wlan} refs={refs} />
      ))}
    </div>
  );
}

function WlanCard({ wlan, refs }: { wlan: any; refs: Record<string, any> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-50 text-left"
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-sm">{wlan.name}</span>
          <span className="text-xs text-gray-400">SSID: {wlan.ssid}</span>
          <span className="text-xs bg-gray-100 px-1.5 py-0.5 rounded font-medium">{wlan.auth_type}</span>
        </div>
        <span className="text-gray-400 text-xs">{expanded ? '−' : '+'}</span>
      </button>
      {expanded && (
        <div className="px-4 pb-3 border-t text-xs space-y-2 pt-2">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1">
            <KV label="ID" value={wlan.id} mono />
            <KV label="SSID" value={wlan.ssid} />
            <KV label="Auth Type" value={wlan.auth_type} />
            <KV label="Encryption" value={wlan.encryption_method} />
            <KV label="VLAN" value={wlan.vlan_id} />
            {wlan.description && <KV label="Description" value={wlan.description} />}
          </div>

          {/* References */}
          {wlan.auth_service_id && (
            <RefRow label="Auth Service" refType="auth_service" refId={wlan.auth_service_id} refs={refs} />
          )}
          {wlan.accounting_service_id && (
            <RefRow label="Accounting" refType="accounting_service" refId={wlan.accounting_service_id} refs={refs} />
          )}
          {wlan.device_policy_id && (
            <RefRow label="Device Policy" refType="device_policy" refId={wlan.device_policy_id} refs={refs} />
          )}

          {/* DPSK config */}
          {wlan.dpsk && (
            <div className="bg-orange-50 border border-orange-100 rounded p-2">
              <span className="font-semibold text-orange-700">DPSK Config:</span>
              <pre className="mt-1 text-[10px] font-mono text-orange-800 whitespace-pre-wrap">{JSON.stringify(wlan.dpsk, null, 2)}</pre>
            </div>
          )}

          {/* Raw toggle */}
          <details className="mt-1">
            <summary className="text-gray-400 cursor-pointer hover:text-gray-600 text-[11px]">
              View raw WLAN JSON
            </summary>
            <pre className="mt-1 text-[10px] font-mono bg-gray-50 border rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-48">
              {JSON.stringify(wlan.raw || wlan, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}

function ApGroupsTab({ data }: { data: any }) {
  const groups = data.ap_groups || [];
  const wlanGroups = data.wlan_groups || [];

  // Build a lookup: wlan_group_id -> wlan_group
  const wgMap = new Map<string, any>();
  wlanGroups.forEach((wg: any) => wgMap.set(wg.id, wg));

  return (
    <div className="space-y-2">
      {groups.length === 0 && <p className="text-gray-500 text-sm py-4 text-center">No AP Groups in snapshot</p>}
      {groups.map((ag: any) => (
        <ApGroupCard key={ag.id} group={ag} wgMap={wgMap} />
      ))}
    </div>
  );
}

function ApGroupCard({ group, wgMap }: { group: any; wgMap: Map<string, any> }) {
  const [expanded, setExpanded] = useState(false);

  // Collect WLAN group assignments for each radio
  const radioAssignments = group.radio_config || {};

  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-50 text-left"
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-sm">{group.name}</span>
          <span className="text-xs text-gray-400">{group.ap_count} APs</span>
        </div>
        <span className="text-gray-400 text-xs">{expanded ? '−' : '+'}</span>
      </button>
      {expanded && (
        <div className="px-4 pb-3 border-t text-xs space-y-2 pt-2">
          <KV label="ID" value={group.id} mono />
          {group.description && <KV label="Description" value={group.description} />}

          <div className="mt-2">
            <span className="font-semibold text-gray-600">Radio → WLAN Group Assignments:</span>
            <div className="mt-1 space-y-1">
              {Object.entries(radioAssignments).map(([radio, wgId]) => {
                const wg = wgMap.get(wgId as string);
                return (
                  <div key={radio} className="flex items-center gap-2">
                    <span className="text-gray-500 w-24">{radio}:</span>
                    {wg ? (
                      <span>{wg.name} <span className="text-gray-400">({(wg.members || []).length} WLANs)</span></span>
                    ) : wgId ? (
                      <span className="font-mono text-gray-400">{wgId as string}</span>
                    ) : (
                      <span className="text-gray-400 italic">zone default</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────

function MiniCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-50 border rounded-lg p-3 text-center">
      <div className="text-xl font-bold text-gray-800">{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: any; mono?: boolean }) {
  if (value == null) return null;
  return (
    <div>
      <span className="text-gray-500">{label}:</span>{' '}
      <span className={mono ? 'font-mono text-gray-600' : 'font-medium'}>{String(value)}</span>
    </div>
  );
}

function RefRow({ label, refType, refId, refs }: { label: string; refType: string; refId: string; refs: Record<string, any> }) {
  const key = `${refType}:${refId}`;
  const obj = refs[key];
  return (
    <div className="bg-gray-50 border rounded p-2 flex items-center gap-2">
      <span className="text-gray-500">{label}:</span>
      <span className="font-medium">{obj?.name || refId}</span>
      {obj?.name && <span className="text-gray-400 font-mono text-[10px]">{refId.slice(0, 8)}</span>}
    </div>
  );
}
